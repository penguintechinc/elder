//! GCP cloud discovery and IAM sync using Service Account RS256 JWT OAuth2.

use std::collections::{BTreeMap, BTreeSet};

use chrono::Utc;
use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::common::{
    build_http_client, join_url, normalize_http_base_url, opt_bool, opt_str, parse_json_tags,
    safe_endpoint_label, truncate_body, CloudError, CloudResult, DiscoveryCollection,
    IamCollection, NormalizedResource, TlsOptions,
};

const DEFAULT_TOKEN_URI: &str = "https://oauth2.googleapis.com/token";
const DEFAULT_SCOPE: &str = "https://www.googleapis.com/auth/cloud-platform.read-only";
const DEFAULT_COMPUTE_BASE: &str = "https://compute.googleapis.com";
const DEFAULT_STORAGE_BASE: &str = "https://storage.googleapis.com";
const DEFAULT_SQL_BASE: &str = "https://sqladmin.googleapis.com";
const DEFAULT_CRM_BASE: &str = "https://cloudresourcemanager.googleapis.com";
const DEFAULT_IAM_BASE: &str = "https://iam.googleapis.com";

#[derive(Debug, Serialize)]
struct ServiceAccountJwtClaims<'a> {
    iss: &'a str,
    sub: &'a str,
    scope: &'a str,
    aud: &'a str,
    iat: i64,
    exp: i64,
}

#[derive(Debug, Deserialize)]
struct OAuthTokenResponse {
    access_token: String,
}

#[derive(Clone)]
struct GcpConfig {
    project_id: String,
    client_email: String,
    private_key_pem: String,
    private_key_id: Option<String>,
    token_uri: String,
    compute_base: String,
    storage_base: String,
    sql_base: String,
    crm_base: String,
    iam_base: String,
    tls: TlsOptions,
}

impl std::fmt::Debug for GcpConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("GcpConfig")
            .field("project_id", &self.project_id)
            .field("token_uri", &self.token_uri)
            .field("has_private_key", &(!self.private_key_pem.is_empty()))
            .finish()
    }
}

impl GcpConfig {
    fn from_map(cfg: &Map<String, Value>) -> CloudResult<Self> {
        let mut merged = cfg.clone();
        if let Some(creds_val) = cfg.get("credentials_json") {
            if let Some(obj) = creds_val.as_object() {
                for (k, v) in obj {
                    merged.entry(k.clone()).or_insert_with(|| v.clone());
                }
            } else if let Some(raw) = creds_val.as_str() {
                let parsed: Value = serde_json::from_str(raw).map_err(|e| {
                    CloudError::Config(format!("invalid GCP credentials_json: {e}"))
                })?;
                if let Some(obj) = parsed.as_object() {
                    for (k, v) in obj {
                        merged.entry(k.clone()).or_insert_with(|| v.clone());
                    }
                }
            }
        }

        let project_id = opt_str(&merged, &["project_id"])
            .ok_or_else(|| CloudError::Config("missing GCP project_id".into()))?;
        let client_email = opt_str(&merged, &["client_email"])
            .ok_or_else(|| CloudError::Config("missing GCP service account client_email".into()))?;
        let raw_pem = opt_str(&merged, &["private_key"])
            .ok_or_else(|| CloudError::Config("missing GCP service account private_key".into()))?;
        let private_key_pem = normalize_pem(&raw_pem);
        let private_key_id = opt_str(&merged, &["private_key_id"]);

        let token_uri_raw =
            opt_str(&merged, &["token_uri"]).unwrap_or_else(|| DEFAULT_TOKEN_URI.to_string());
        let token_uri = normalize_http_base_url(&token_uri_raw, "token_uri")?;

        let shared_api_base = opt_str(&merged, &["api_base_url", "base_url"]);
        let compute_base = resolve_gcp_base(
            &merged,
            &["compute_base_url"],
            shared_api_base.as_deref(),
            DEFAULT_COMPUTE_BASE,
            "compute_base_url",
        )?;
        let storage_base = resolve_gcp_base(
            &merged,
            &["storage_base_url"],
            shared_api_base.as_deref(),
            DEFAULT_STORAGE_BASE,
            "storage_base_url",
        )?;
        let sql_base = resolve_gcp_base(
            &merged,
            &["sql_base_url"],
            shared_api_base.as_deref(),
            DEFAULT_SQL_BASE,
            "sql_base_url",
        )?;
        let crm_base = resolve_gcp_base(
            &merged,
            &["crm_base_url", "crm_base"],
            shared_api_base.as_deref(),
            DEFAULT_CRM_BASE,
            "crm_base_url",
        )?;
        let iam_base = resolve_gcp_base(
            &merged,
            &["iam_base_url", "iam_base"],
            shared_api_base.as_deref(),
            DEFAULT_IAM_BASE,
            "iam_base_url",
        )?;

        let tls = TlsOptions {
            verify_ssl: opt_bool(&merged, &["verify_ssl"], true),
            ca_cert_pem: opt_str(&merged, &["ca_cert_pem", "ca_cert"]),
            identity_pem: None,
        };

        Ok(Self {
            project_id,
            client_email,
            private_key_pem,
            private_key_id,
            token_uri,
            compute_base,
            storage_base,
            sql_base,
            crm_base,
            iam_base,
            tls,
        })
    }
}

fn resolve_gcp_base(
    cfg: &Map<String, Value>,
    keys: &[&str],
    shared_api_base: Option<&str>,
    default_url: &str,
    label: &str,
) -> CloudResult<String> {
    let raw = opt_str(cfg, keys)
        .or_else(|| shared_api_base.map(ToOwned::to_owned))
        .unwrap_or_else(|| default_url.to_string());
    normalize_http_base_url(&raw, label)
}

fn normalize_pem(raw: &str) -> String {
    if raw.contains("\\n") && !raw.contains('\n') {
        raw.replace("\\n", "\n")
    } else {
        raw.to_string()
    }
}

/// Mint an RS256 JWT assertion for a GCP service account.
pub fn build_service_account_jwt(
    client_email: &str,
    private_key_pem: &str,
    private_key_id: Option<&str>,
    token_uri: &str,
    scope: &str,
) -> CloudResult<String> {
    let now = Utc::now().timestamp();
    let claims = ServiceAccountJwtClaims {
        iss: client_email,
        sub: client_email,
        scope,
        aud: token_uri,
        iat: now,
        exp: now + 3600,
    };
    let mut header = Header::new(Algorithm::RS256);
    header.kid = private_key_id.map(ToOwned::to_owned);
    let key = EncodingKey::from_rsa_pem(private_key_pem.as_bytes())
        .map_err(|e| CloudError::Auth(format!("invalid GCP RSA private key PEM: {e}")))?;
    encode(&header, &claims, &key)
        .map_err(|e| CloudError::Auth(format!("failed signing GCP service account JWT: {e}")))
}

async fn exchange_oauth_token(http: &Client, cfg: &GcpConfig) -> CloudResult<String> {
    let assertion = build_service_account_jwt(
        &cfg.client_email,
        &cfg.private_key_pem,
        cfg.private_key_id.as_deref(),
        &cfg.token_uri,
        DEFAULT_SCOPE,
    )?;

    let params = [
        ("grant_type", "urn:ietf:params:oauth:grant-type:jwt-bearer"),
        ("assertion", assertion.as_str()),
    ];
    let resp = http.post(&cfg.token_uri).form(&params).send().await?;
    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(CloudError::Auth(format!(
            "GCP OAuth2 token exchange HTTP {}: {}",
            status.as_u16(),
            truncate_body(&body)
        )));
    }
    let parsed: OAuthTokenResponse = resp
        .json()
        .await
        .map_err(|e| CloudError::Parse(format!("invalid GCP OAuth2 token JSON: {e}")))?;
    if parsed.access_token.trim().is_empty() {
        return Err(CloudError::Auth(
            "GCP OAuth2 response missing access_token".into(),
        ));
    }
    Ok(parsed.access_token)
}

async fn gcp_get_json(http: &Client, url: &str, token: &str) -> CloudResult<Value> {
    let resp = http.get(url).bearer_auth(token).send().await?;
    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(CloudError::Http {
            status: status.as_u16(),
            endpoint: safe_endpoint_label(url),
            body: truncate_body(&body),
        });
    }
    resp.json::<Value>()
        .await
        .map_err(|e| CloudError::Parse(format!("invalid GCP JSON response: {e}")))
}

async fn gcp_post_json(http: &Client, url: &str, token: &str, body: &Value) -> CloudResult<Value> {
    let resp = http.post(url).bearer_auth(token).json(body).send().await?;
    let status = resp.status();
    if !status.is_success() {
        let err_body = resp.text().await.unwrap_or_default();
        return Err(CloudError::Http {
            status: status.as_u16(),
            endpoint: safe_endpoint_label(url),
            body: truncate_body(&err_body),
        });
    }
    resp.json::<Value>()
        .await
        .map_err(|e| CloudError::Parse(format!("invalid GCP JSON response: {e}")))
}

fn trailing_segment(raw: &str) -> &str {
    raw.rsplit('/').next().unwrap_or(raw)
}

/// Run GCP cloud discovery (Compute instances & networks, GCS buckets, Cloud SQL instances).
pub async fn discover(cfg_map: &Map<String, Value>) -> CloudResult<DiscoveryCollection> {
    let cfg = GcpConfig::from_map(cfg_map)?;
    let http = build_http_client(&cfg.tls)?;
    let token = exchange_oauth_token(&http, &cfg).await?;

    let mut out = DiscoveryCollection::default();
    let mut network_index: BTreeMap<String, String> = BTreeMap::new();

    match fetch_networks(&http, &cfg, &token, &mut network_index).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("GCP Compute Networks: {err}")),
    }

    match fetch_compute_instances(&http, &cfg, &token, &network_index, &mut out).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("GCP Compute Instances: {err}")),
    }

    match fetch_gcs_buckets(&http, &cfg, &token).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("GCP Storage Buckets: {err}")),
    }

    match fetch_sql_instances(&http, &cfg, &token, &network_index, &mut out).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("GCP Cloud SQL Instances: {err}")),
    }

    Ok(out)
}

async fn fetch_networks(
    http: &Client,
    cfg: &GcpConfig,
    token: &str,
    network_index: &mut BTreeMap<String, String>,
) -> CloudResult<Vec<NormalizedResource>> {
    let mut resources = Vec::new();
    let mut page_token: Option<String> = None;

    loop {
        let base = join_url(
            &cfg.compute_base,
            &format!("/compute/v1/projects/{}/global/networks", cfg.project_id),
        );
        let mut parsed = url::Url::parse(&base)
            .map_err(|e| CloudError::Config(format!("invalid compute URL: {e}")))?;
        if let Some(pt) = page_token.as_deref() {
            parsed.query_pairs_mut().append_pair("pageToken", pt);
        }

        let data = gcp_get_json(http, parsed.as_str(), token).await?;
        if let Some(items) = data.get("items").and_then(Value::as_array) {
            for net in items {
                let Some(name) = net.get("name").and_then(Value::as_str) else {
                    continue;
                };
                let id_str = net
                    .get("id")
                    .and_then(|v| {
                        v.as_str()
                            .map(ToOwned::to_owned)
                            .or_else(|| v.as_u64().map(|n| n.to_string()))
                    })
                    .unwrap_or_else(|| name.to_string());
                if let Some(self_link) = net.get("selfLink").and_then(Value::as_str) {
                    network_index.insert(self_link.to_string(), id_str.clone());
                }
                network_index.insert(name.to_string(), id_str.clone());

                let auto_create = net.get("autoCreateSubnetworks").and_then(Value::as_bool);
                let routing_mode = net
                    .get("routingConfig")
                    .and_then(|r| r.get("routingMode"))
                    .and_then(Value::as_str);

                let mut res =
                    NormalizedResource::new(&id_str, &id_str, "vpc", "network", name, "gcp");
                res.region = Some("global".to_string());
                res.metadata = json!({
                    "network_id": id_str,
                    "network_name": name,
                    "auto_create_subnets": auto_create,
                    "routing_mode": routing_mode,
                    "project_id": cfg.project_id,
                    "self_link": net.get("selfLink").and_then(Value::as_str),
                });
                resources.push(res);
            }
        }

        page_token = data
            .get("nextPageToken")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned);
        if page_token.is_none() {
            break;
        }
    }

    Ok(resources)
}

async fn fetch_compute_instances(
    http: &Client,
    cfg: &GcpConfig,
    token: &str,
    network_index: &BTreeMap<String, String>,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let mut resources = Vec::new();
    let mut page_token: Option<String> = None;

    loop {
        let base = join_url(
            &cfg.compute_base,
            &format!(
                "/compute/v1/projects/{}/aggregated/instances",
                cfg.project_id
            ),
        );
        let mut parsed = url::Url::parse(&base)
            .map_err(|e| CloudError::Config(format!("invalid compute URL: {e}")))?;
        if let Some(pt) = page_token.as_deref() {
            parsed.query_pairs_mut().append_pair("pageToken", pt);
        }

        let data = gcp_get_json(http, parsed.as_str(), token).await?;
        if let Some(zones_map) = data.get("items").and_then(Value::as_object) {
            for (_zone_key, scoped) in zones_map {
                let Some(instances) = scoped.get("instances").and_then(Value::as_array) else {
                    continue;
                };
                for inst in instances {
                    let Some(name) = inst.get("name").and_then(Value::as_str) else {
                        continue;
                    };
                    let id_str = inst
                        .get("id")
                        .and_then(|v| {
                            v.as_str()
                                .map(ToOwned::to_owned)
                                .or_else(|| v.as_u64().map(|n| n.to_string()))
                        })
                        .unwrap_or_else(|| name.to_string());
                    let zone = inst
                        .get("zone")
                        .and_then(Value::as_str)
                        .map(trailing_segment)
                        .map(ToOwned::to_owned);
                    let machine_type = inst
                        .get("machineType")
                        .and_then(Value::as_str)
                        .map(trailing_segment);
                    let status = inst.get("status").and_then(Value::as_str);

                    let first_nic = inst
                        .get("networkInterfaces")
                        .and_then(Value::as_array)
                        .and_then(|arr| arr.first());
                    let internal_ip = first_nic
                        .and_then(|n| n.get("networkIP"))
                        .and_then(Value::as_str);
                    let external_ip = first_nic
                        .and_then(|n| n.get("accessConfigs"))
                        .and_then(Value::as_array)
                        .and_then(|arr| arr.first())
                        .and_then(|ac| ac.get("natIP"))
                        .and_then(Value::as_str);

                    let mut res = NormalizedResource::new(
                        &id_str,
                        &id_str,
                        "gce_instance",
                        "compute",
                        name,
                        "gcp",
                    );
                    res.region = zone.clone();
                    res.tags = parse_json_tags(inst.get("labels"));
                    res.metadata = json!({
                        "instance_id": id_str,
                        "machine_type": machine_type,
                        "status": status,
                        "zone": zone,
                        "internal_ip": internal_ip,
                        "external_ip": external_ip,
                        "project_id": cfg.project_id,
                    });

                    if let Some(nics) = inst.get("networkInterfaces").and_then(Value::as_array) {
                        for nic in nics {
                            if let Some(net_ref) = nic.get("network").and_then(Value::as_str) {
                                let target_id = resolve_network_id(net_ref, network_index);
                                res.add_relationship(
                                    &target_id,
                                    "networking_resource",
                                    "in_network",
                                );
                                out.push_dependency(
                                    &id_str,
                                    "entity",
                                    &target_id,
                                    "networking_resource",
                                    "in_network",
                                );
                            }
                            if let Some(sub_ref) = nic.get("subnetwork").and_then(Value::as_str) {
                                let sub_id = trailing_segment(sub_ref);
                                res.add_relationship(sub_id, "networking_resource", "in_subnet");
                                out.push_dependency(
                                    &id_str,
                                    "entity",
                                    sub_id,
                                    "networking_resource",
                                    "in_subnet",
                                );
                            }
                        }
                    }

                    resources.push(res);
                }
            }
        }

        page_token = data
            .get("nextPageToken")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned);
        if page_token.is_none() {
            break;
        }
    }

    Ok(resources)
}

fn resolve_network_id(net_ref: &str, network_index: &BTreeMap<String, String>) -> String {
    if let Some(id) = network_index.get(net_ref) {
        return id.clone();
    }
    let name = trailing_segment(net_ref);
    if let Some(id) = network_index.get(name) {
        return id.clone();
    }
    name.to_string()
}

async fn fetch_gcs_buckets(
    http: &Client,
    cfg: &GcpConfig,
    token: &str,
) -> CloudResult<Vec<NormalizedResource>> {
    let mut resources = Vec::new();
    let mut page_token: Option<String> = None;

    loop {
        let base = join_url(&cfg.storage_base, "/storage/v1/b");
        let mut parsed = url::Url::parse(&base)
            .map_err(|e| CloudError::Config(format!("invalid storage URL: {e}")))?;
        {
            let mut pairs = parsed.query_pairs_mut();
            pairs.append_pair("project", &cfg.project_id);
            if let Some(pt) = page_token.as_deref() {
                pairs.append_pair("pageToken", pt);
            }
        }

        let data = gcp_get_json(http, parsed.as_str(), token).await?;
        if let Some(items) = data.get("items").and_then(Value::as_array) {
            for bucket in items {
                let Some(name) = bucket.get("name").and_then(Value::as_str) else {
                    continue;
                };
                let location = bucket
                    .get("location")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                let storage_class = bucket.get("storageClass").and_then(Value::as_str);
                let time_created = bucket.get("timeCreated").and_then(Value::as_str);

                let mut res =
                    NormalizedResource::new(name, name, "gcs_bucket", "storage", name, "gcp");
                res.region = location.clone();
                res.tags = parse_json_tags(bucket.get("labels"));
                res.metadata = json!({
                    "location": location,
                    "storage_class": storage_class,
                    "created": time_created,
                    "project_id": cfg.project_id,
                });
                resources.push(res);
            }
        }

        page_token = data
            .get("nextPageToken")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned);
        if page_token.is_none() {
            break;
        }
    }

    Ok(resources)
}

async fn fetch_sql_instances(
    http: &Client,
    cfg: &GcpConfig,
    token: &str,
    network_index: &BTreeMap<String, String>,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let mut resources = Vec::new();
    let mut page_token: Option<String> = None;

    loop {
        let base = join_url(
            &cfg.sql_base,
            &format!("/sql/v1beta4/projects/{}/instances", cfg.project_id),
        );
        let mut parsed = url::Url::parse(&base)
            .map_err(|e| CloudError::Config(format!("invalid SQL URL: {e}")))?;
        if let Some(pt) = page_token.as_deref() {
            parsed.query_pairs_mut().append_pair("pageToken", pt);
        }

        let data = gcp_get_json(http, parsed.as_str(), token).await?;
        if let Some(items) = data.get("items").and_then(Value::as_array) {
            for db in items {
                let Some(name) = db.get("name").and_then(Value::as_str) else {
                    continue;
                };
                let conn_name = db
                    .get("connectionName")
                    .and_then(Value::as_str)
                    .unwrap_or(name);
                let region = db
                    .get("region")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                let settings = db.get("settings");
                let tier = settings.and_then(|s| s.get("tier")).and_then(Value::as_str);
                let private_net = settings
                    .and_then(|s| s.get("ipConfiguration"))
                    .and_then(|ip| ip.get("privateNetwork"))
                    .and_then(Value::as_str);

                let mut res = NormalizedResource::new(
                    name,
                    conn_name,
                    "cloud_sql_instance",
                    "database",
                    name,
                    "gcp",
                );
                res.region = region;
                res.tags = parse_json_tags(settings.and_then(|s| s.get("userLabels")));
                res.metadata = json!({
                    "database_version": db.get("databaseVersion").and_then(Value::as_str),
                    "state": db.get("state").and_then(Value::as_str),
                    "tier": tier,
                    "connection_name": conn_name,
                    "backend_type": db.get("backendType").and_then(Value::as_str),
                    "project_id": cfg.project_id,
                });

                if let Some(net_ref) = private_net {
                    let net_id = resolve_network_id(net_ref, network_index);
                    res.add_relationship(&net_id, "networking_resource", "in_network");
                    out.push_dependency(
                        conn_name,
                        "data_store",
                        &net_id,
                        "networking_resource",
                        "in_network",
                    );
                }

                resources.push(res);
            }
        }

        page_token = data
            .get("nextPageToken")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned);
        if page_token.is_none() {
            break;
        }
    }

    Ok(resources)
}

/// Sync GCP IAM policy bindings (CRM `getIamPolicy`) and service accounts.
pub async fn sync_iam(cfg_map: &Map<String, Value>) -> CloudResult<IamCollection> {
    let cfg = GcpConfig::from_map(cfg_map)?;
    let http = build_http_client(&cfg.tls)?;
    let token = exchange_oauth_token(&http, &cfg).await?;

    let mut out = IamCollection::default();

    match fetch_service_accounts(&http, &cfg, &token).await {
        Ok(accounts) => {
            out.mark_step_ok();
            out.users.extend(accounts);
        }
        Err(err) => out.mark_step_err(format!("GCP IAM ServiceAccounts: {err}")),
    }

    match fetch_project_iam_policy(&http, &cfg, &token).await {
        Ok((groups, roles, memberships)) => {
            out.mark_step_ok();
            out.groups.extend(groups);
            out.roles.extend(roles);
            out.memberships.extend(memberships);
        }
        Err(err) => out.mark_step_err(format!("GCP CRM getIamPolicy: {err}")),
    }

    Ok(out)
}

async fn fetch_service_accounts(
    http: &Client,
    cfg: &GcpConfig,
    token: &str,
) -> CloudResult<Vec<Value>> {
    let mut accounts = Vec::new();
    let mut page_token: Option<String> = None;

    loop {
        let base = join_url(
            &cfg.iam_base,
            &format!("/v1/projects/{}/serviceAccounts", cfg.project_id),
        );
        let mut parsed = url::Url::parse(&base)
            .map_err(|e| CloudError::Config(format!("invalid IAM URL: {e}")))?;
        if let Some(pt) = page_token.as_deref() {
            parsed.query_pairs_mut().append_pair("pageToken", pt);
        }

        let data = gcp_get_json(http, parsed.as_str(), token).await?;
        let list = data
            .get("accounts")
            .or_else(|| data.get("serviceAccounts"))
            .and_then(Value::as_array);

        if let Some(items) = list {
            for sa in items {
                let email = sa.get("email").and_then(Value::as_str).unwrap_or("");
                let name = sa.get("name").and_then(Value::as_str).unwrap_or(email);
                let unique_id = sa.get("uniqueId").and_then(Value::as_str).unwrap_or(name);
                if email.is_empty() && name.is_empty() {
                    continue;
                }
                accounts.push(json!({
                    "id": unique_id,
                    "external_id": name,
                    "username": email,
                    "email": email,
                    "display_name": sa.get("displayName").and_then(Value::as_str),
                    "disabled": sa.get("disabled").and_then(Value::as_bool).unwrap_or(false),
                    "project_id": cfg.project_id,
                    "kind": "service_account",
                    "provider": "gcp_iam",
                }));
            }
        }

        page_token = data
            .get("nextPageToken")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned);
        if page_token.is_none() {
            break;
        }
    }

    Ok(accounts)
}

async fn fetch_project_iam_policy(
    http: &Client,
    cfg: &GcpConfig,
    token: &str,
) -> CloudResult<(Vec<Value>, Vec<Value>, Vec<Value>)> {
    let url = join_url(
        &cfg.crm_base,
        &format!("/v1/projects/{}:getIamPolicy", cfg.project_id),
    );
    let data = gcp_post_json(
        http,
        &url,
        token,
        &json!({ "options": { "requestedPolicyVersion": 3 } }),
    )
    .await?;

    let mut seen_roles: BTreeSet<String> = BTreeSet::new();
    let mut seen_groups: BTreeSet<String> = BTreeSet::new();
    let mut groups = Vec::new();
    let mut roles = Vec::new();
    let mut memberships = Vec::new();

    if let Some(bindings) = data.get("bindings").and_then(Value::as_array) {
        for binding in bindings {
            let Some(role_name) = binding.get("role").and_then(Value::as_str) else {
                continue;
            };
            if seen_roles.insert(role_name.to_string()) {
                roles.push(json!({
                    "id": role_name,
                    "external_id": role_name,
                    "name": role_name,
                    "provider": "gcp_iam",
                }));
            }

            let condition = binding.get("condition").cloned();
            if let Some(members) = binding.get("members").and_then(Value::as_array) {
                for member_val in members {
                    let Some(member_str) = member_val.as_str() else {
                        continue;
                    };
                    let (member_type, member_id) = match member_str.split_once(':') {
                        Some((kind, id)) => (kind, id),
                        None => ("principal", member_str),
                    };

                    if member_type == "group" && seen_groups.insert(member_id.to_string()) {
                        groups.push(json!({
                            "id": member_id,
                            "external_id": member_str,
                            "name": member_id,
                            "provider": "gcp_iam",
                        }));
                    }

                    memberships.push(json!({
                        "member": member_str,
                        "member_type": member_type,
                        "member_id": member_id,
                        "role": role_name,
                        "condition": condition,
                        "kind": "iam_binding",
                        "provider": "gcp_iam",
                    }));
                }
            }
        }
    }

    Ok((groups, roles, memberships))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_and_verifies_ephemeral_rs256_jwt_assertion() {
        use jsonwebtoken::{decode, DecodingKey, Validation};
        use rand::rngs::OsRng;
        use rsa::pkcs8::{EncodePrivateKey, EncodePublicKey, LineEnding};
        use rsa::RsaPrivateKey;

        #[derive(Debug, Deserialize)]
        struct DecodedClaims {
            iss: String,
            sub: String,
            scope: String,
            aud: String,
            iat: i64,
            exp: i64,
        }

        let priv_key = RsaPrivateKey::new(&mut OsRng, 2048).expect("ephemeral key");
        let priv_pem = priv_key
            .to_pkcs8_pem(LineEnding::LF)
            .expect("pkcs8 pem")
            .to_string();
        let pub_pem = priv_key
            .to_public_key()
            .to_public_key_pem(LineEnding::LF)
            .expect("spki pem");

        let jwt = build_service_account_jwt(
            "sa-test@elder-proj.iam.gserviceaccount.com",
            &priv_pem,
            Some("key-1"),
            "https://oauth2.googleapis.com/token",
            DEFAULT_SCOPE,
        )
        .expect("jwt should sign");

        let mut validation = Validation::new(Algorithm::RS256);
        validation.set_audience(&["https://oauth2.googleapis.com/token"]);
        let decoded = decode::<DecodedClaims>(
            &jwt,
            &DecodingKey::from_rsa_pem(pub_pem.as_bytes()).expect("decoding key"),
            &validation,
        )
        .expect("rs256 jwt must verify");

        assert_eq!(
            decoded.claims.iss,
            "sa-test@elder-proj.iam.gserviceaccount.com"
        );
        assert_eq!(
            decoded.claims.sub,
            "sa-test@elder-proj.iam.gserviceaccount.com"
        );
        assert_eq!(decoded.claims.scope, DEFAULT_SCOPE);
        assert_eq!(decoded.claims.aud, "https://oauth2.googleapis.com/token");
        assert_eq!(decoded.claims.exp - decoded.claims.iat, 3600);
    }
}
