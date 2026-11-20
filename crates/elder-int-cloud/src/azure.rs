//! Azure ARM cloud discovery using AAD OAuth2 client_credentials flow.

use std::collections::BTreeMap;

use reqwest::Client;
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::common::{
    build_http_client, join_url, normalize_http_base_url, opt_bool, opt_str, parse_json_tags,
    req_str, safe_endpoint_label, truncate_body, CloudError, CloudResult, DiscoveryCollection,
    NormalizedResource, TlsOptions,
};

const DEFAULT_LOGIN_BASE: &str = "https://login.microsoftonline.com";
const DEFAULT_ARM_BASE: &str = "https://management.azure.com";
const DEFAULT_AZURE_SCOPE: &str = "https://management.azure.com/.default";

#[derive(Debug, Deserialize)]
struct AadTokenResponse {
    access_token: String,
}

#[derive(Clone)]
struct AzureConfig {
    subscription_id: String,
    tenant_id: String,
    client_id: String,
    client_secret: String,
    login_base: String,
    arm_base: String,
    scope: String,
    tls: TlsOptions,
}

impl std::fmt::Debug for AzureConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AzureConfig")
            .field("subscription_id", &self.subscription_id)
            .field("tenant_id", &self.tenant_id)
            .field("login_base", &self.login_base)
            .field("arm_base", &self.arm_base)
            .field("has_client_secret", &(!self.client_secret.is_empty()))
            .finish()
    }
}

impl AzureConfig {
    fn from_map(cfg: &Map<String, Value>) -> CloudResult<Self> {
        let subscription_id = req_str(cfg, &["subscription_id"], "subscription_id")?;
        let tenant_id = req_str(cfg, &["tenant_id"], "tenant_id")?;
        let client_id = req_str(cfg, &["client_id"], "client_id")?;
        let client_secret = req_str(cfg, &["client_secret"], "client_secret")?;

        let login_raw = opt_str(cfg, &["login_base", "login_base_url"])
            .unwrap_or_else(|| DEFAULT_LOGIN_BASE.to_string());
        let login_base = normalize_http_base_url(&login_raw, "login_base")?;

        let arm_raw = opt_str(cfg, &["arm_base_url", "api_base_url", "base_url"])
            .unwrap_or_else(|| DEFAULT_ARM_BASE.to_string());
        let arm_base = normalize_http_base_url(&arm_raw, "arm_base_url")?;

        let scope = opt_str(cfg, &["scope"]).unwrap_or_else(|| DEFAULT_AZURE_SCOPE.to_string());
        let tls = TlsOptions {
            verify_ssl: opt_bool(cfg, &["verify_ssl"], true),
            ca_cert_pem: opt_str(cfg, &["ca_cert_pem", "ca_cert"]),
            identity_pem: None,
        };

        Ok(Self {
            subscription_id,
            tenant_id,
            client_id,
            client_secret,
            login_base,
            arm_base,
            scope,
            tls,
        })
    }
}

async fn fetch_aad_token(http: &Client, cfg: &AzureConfig) -> CloudResult<String> {
    let token_url = join_url(
        &cfg.login_base,
        &format!("/{}/oauth2/v2.0/token", cfg.tenant_id),
    );
    let form = [
        ("grant_type", "client_credentials"),
        ("client_id", cfg.client_id.as_str()),
        ("client_secret", cfg.client_secret.as_str()),
        ("scope", cfg.scope.as_str()),
    ];

    let resp = http.post(&token_url).form(&form).send().await?;
    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(CloudError::Auth(format!(
            "Azure AAD token HTTP {}: {}",
            status.as_u16(),
            truncate_body(&body)
        )));
    }

    let parsed: AadTokenResponse = resp
        .json()
        .await
        .map_err(|e| CloudError::Parse(format!("invalid Azure AAD token JSON: {e}")))?;
    if parsed.access_token.trim().is_empty() {
        return Err(CloudError::Auth(
            "Azure AAD response missing access_token".into(),
        ));
    }
    Ok(parsed.access_token)
}

async fn arm_get_paged(
    http: &Client,
    arm_base: &str,
    initial_path: &str,
    token: &str,
) -> CloudResult<Vec<Value>> {
    let mut items = Vec::new();
    let mut next_url = Some(join_url(arm_base, initial_path));

    while let Some(url) = next_url {
        let resp = http.get(&url).bearer_auth(token).send().await?;
        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(CloudError::Http {
                status: status.as_u16(),
                endpoint: safe_endpoint_label(&url),
                body: truncate_body(&body),
            });
        }

        let data: Value = resp
            .json()
            .await
            .map_err(|e| CloudError::Parse(format!("invalid Azure ARM JSON: {e}")))?;

        if let Some(arr) = data.get("value").and_then(Value::as_array) {
            items.extend(arr.iter().cloned());
        }

        next_url = data
            .get("nextLink")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|link| {
                if link.starts_with("http://") || link.starts_with("https://") {
                    link.to_string()
                } else {
                    join_url(arm_base, link)
                }
            });
    }

    Ok(items)
}

fn extract_resource_group_name(arm_id: &str) -> Option<String> {
    let mut parts = arm_id.split('/');
    while let Some(seg) = parts.next() {
        if seg.eq_ignore_ascii_case("resourceGroups") {
            return parts.next().map(|s| s.to_ascii_lowercase());
        }
    }
    None
}

fn link_resource_group(
    res: &mut NormalizedResource,
    arm_id: &str,
    source_kind: &str,
    rg_lookup: &BTreeMap<String, String>,
    out: &mut DiscoveryCollection,
) {
    if let Some(rg_name_lower) = extract_resource_group_name(arm_id) {
        if let Some(rg_id) = rg_lookup.get(&rg_name_lower) {
            res.add_relationship(rg_id, "entity", "in_resource_group");
            out.push_dependency(
                &res.external_id,
                source_kind,
                rg_id,
                "entity",
                "in_resource_group",
            );
        }
    }
}

/// Run Azure ARM discovery (`resourcegroups`, `virtualMachines`, `virtualNetworks`, `storageAccounts`, `servers`).
pub async fn discover(cfg_map: &Map<String, Value>) -> CloudResult<DiscoveryCollection> {
    let cfg = AzureConfig::from_map(cfg_map)?;
    let http = build_http_client(&cfg.tls)?;
    let token = fetch_aad_token(&http, &cfg).await?;

    let mut out = DiscoveryCollection::default();
    let mut rg_lookup: BTreeMap<String, String> = BTreeMap::new();

    match fetch_resource_groups(&http, &cfg, &token, &mut rg_lookup).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("Azure resourcegroups: {err}")),
    }

    match fetch_virtual_machines(&http, &cfg, &token, &rg_lookup, &mut out).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("Azure virtualMachines: {err}")),
    }

    match fetch_virtual_networks(&http, &cfg, &token, &rg_lookup, &mut out).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("Azure virtualNetworks: {err}")),
    }

    match fetch_storage_accounts(&http, &cfg, &token, &rg_lookup, &mut out).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("Azure storageAccounts: {err}")),
    }

    match fetch_sql_servers(&http, &cfg, &token, &rg_lookup, &mut out).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("Azure SQL servers: {err}")),
    }

    Ok(out)
}

async fn fetch_resource_groups(
    http: &Client,
    cfg: &AzureConfig,
    token: &str,
    rg_lookup: &mut BTreeMap<String, String>,
) -> CloudResult<Vec<NormalizedResource>> {
    let path = format!(
        "/subscriptions/{}/resourcegroups?api-version=2021-04-01",
        cfg.subscription_id
    );
    let items = arm_get_paged(http, &cfg.arm_base, &path, token).await?;
    let mut resources = Vec::new();

    for rg in items {
        let Some(name) = rg.get("name").and_then(Value::as_str) else {
            continue;
        };
        let id = rg.get("id").and_then(Value::as_str).unwrap_or(name);
        let location = rg
            .get("location")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned);
        let provisioning_state = rg
            .get("properties")
            .and_then(|p| p.get("provisioningState"))
            .and_then(Value::as_str);

        rg_lookup.insert(name.to_ascii_lowercase(), id.to_string());

        let mut res =
            NormalizedResource::new(id, id, "azure_resource_group", "datacenter", name, "azure");
        res.region = location.clone();
        res.tags = parse_json_tags(rg.get("tags"));
        res.metadata = json!({
            "location": location,
            "provisioning_state": provisioning_state,
            "subscription_id": cfg.subscription_id,
        });
        resources.push(res);
    }

    Ok(resources)
}

async fn fetch_virtual_machines(
    http: &Client,
    cfg: &AzureConfig,
    token: &str,
    rg_lookup: &BTreeMap<String, String>,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let path = format!(
        "/subscriptions/{}/providers/Microsoft.Compute/virtualMachines?api-version=2024-03-01",
        cfg.subscription_id
    );
    let items = arm_get_paged(http, &cfg.arm_base, &path, token).await?;
    let mut resources = Vec::new();

    for vm in items {
        let Some(name) = vm.get("name").and_then(Value::as_str) else {
            continue;
        };
        let id = vm.get("id").and_then(Value::as_str).unwrap_or(name);
        let location = vm
            .get("location")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned);
        let props = vm.get("properties");
        let vm_size = props
            .and_then(|p| p.get("hardwareProfile"))
            .and_then(|h| h.get("vmSize"))
            .and_then(Value::as_str);
        let os_type = props
            .and_then(|p| p.get("storageProfile"))
            .and_then(|s| s.get("osDisk"))
            .and_then(|d| d.get("osType"))
            .and_then(Value::as_str);
        let provisioning_state = props
            .and_then(|p| p.get("provisioningState"))
            .and_then(Value::as_str);

        let mut res = NormalizedResource::new(id, id, "azure_vm", "compute", name, "azure");
        res.region = location.clone();
        res.tags = parse_json_tags(vm.get("tags"));
        res.metadata = json!({
            "vm_size": vm_size,
            "os_type": os_type,
            "location": location,
            "provisioning_state": provisioning_state,
            "subscription_id": cfg.subscription_id,
        });
        link_resource_group(&mut res, id, "entity", rg_lookup, out);
        resources.push(res);
    }

    Ok(resources)
}

async fn fetch_virtual_networks(
    http: &Client,
    cfg: &AzureConfig,
    token: &str,
    rg_lookup: &BTreeMap<String, String>,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let path = format!(
        "/subscriptions/{}/providers/Microsoft.Network/virtualNetworks?api-version=2023-11-01",
        cfg.subscription_id
    );
    let items = arm_get_paged(http, &cfg.arm_base, &path, token).await?;
    let mut resources = Vec::new();

    for vnet in items {
        let Some(name) = vnet.get("name").and_then(Value::as_str) else {
            continue;
        };
        let id = vnet.get("id").and_then(Value::as_str).unwrap_or(name);
        let location = vnet
            .get("location")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned);
        let props = vnet.get("properties");
        let address_space = props
            .and_then(|p| p.get("addressSpace"))
            .and_then(|a| a.get("addressPrefixes"))
            .cloned()
            .unwrap_or_else(|| json!([]));
        let subnets_count = props
            .and_then(|p| p.get("subnets"))
            .and_then(Value::as_array)
            .map_or(0, Vec::len);
        let provisioning_state = props
            .and_then(|p| p.get("provisioningState"))
            .and_then(Value::as_str);

        let mut res = NormalizedResource::new(id, id, "azure_vnet", "network", name, "azure");
        res.region = location.clone();
        res.tags = parse_json_tags(vnet.get("tags"));
        res.metadata = json!({
            "address_space": address_space,
            "location": location,
            "provisioning_state": provisioning_state,
            "subnets_count": subnets_count,
            "subscription_id": cfg.subscription_id,
        });
        link_resource_group(&mut res, id, "networking_resource", rg_lookup, out);
        resources.push(res);
    }

    Ok(resources)
}

async fn fetch_storage_accounts(
    http: &Client,
    cfg: &AzureConfig,
    token: &str,
    rg_lookup: &BTreeMap<String, String>,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let path = format!(
        "/subscriptions/{}/providers/Microsoft.Storage/storageAccounts?api-version=2023-05-01",
        cfg.subscription_id
    );
    let items = arm_get_paged(http, &cfg.arm_base, &path, token).await?;
    let mut resources = Vec::new();

    for acct in items {
        let Some(name) = acct.get("name").and_then(Value::as_str) else {
            continue;
        };
        let id = acct.get("id").and_then(Value::as_str).unwrap_or(name);
        let location = acct
            .get("location")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned);
        let kind = acct.get("kind").and_then(Value::as_str);
        let sku_name = acct
            .get("sku")
            .and_then(|s| s.get("name"))
            .and_then(Value::as_str);
        let provisioning_state = acct
            .get("properties")
            .and_then(|p| p.get("provisioningState"))
            .and_then(Value::as_str);

        let mut res =
            NormalizedResource::new(id, id, "azure_storage_account", "storage", name, "azure");
        res.region = location.clone();
        res.tags = parse_json_tags(acct.get("tags"));
        res.metadata = json!({
            "kind": kind,
            "sku_name": sku_name,
            "location": location,
            "provisioning_state": provisioning_state,
            "subscription_id": cfg.subscription_id,
        });
        link_resource_group(&mut res, id, "data_store", rg_lookup, out);
        resources.push(res);
    }

    Ok(resources)
}

async fn fetch_sql_servers(
    http: &Client,
    cfg: &AzureConfig,
    token: &str,
    rg_lookup: &BTreeMap<String, String>,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let path = format!(
        "/subscriptions/{}/providers/Microsoft.Sql/servers?api-version=2023-08-01-preview",
        cfg.subscription_id
    );
    let items = arm_get_paged(http, &cfg.arm_base, &path, token).await?;
    let mut resources = Vec::new();

    for srv in items {
        let Some(name) = srv.get("name").and_then(Value::as_str) else {
            continue;
        };
        let id = srv.get("id").and_then(Value::as_str).unwrap_or(name);
        let location = srv
            .get("location")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned);
        let props = srv.get("properties");
        let fqdn = props
            .and_then(|p| p.get("fullyQualifiedDomainName"))
            .and_then(Value::as_str);
        let version = props.and_then(|p| p.get("version")).and_then(Value::as_str);
        let state = props.and_then(|p| p.get("state")).and_then(Value::as_str);

        let mut res =
            NormalizedResource::new(id, id, "azure_sql_server", "database", name, "azure");
        res.region = location.clone();
        res.tags = parse_json_tags(srv.get("tags"));
        res.metadata = json!({
            "fully_qualified_domain_name": fqdn,
            "version": version,
            "state": state,
            "location": location,
            "subscription_id": cfg.subscription_id,
        });
        link_resource_group(&mut res, id, "data_store", rg_lookup, out);
        resources.push(res);
    }

    Ok(resources)
}
