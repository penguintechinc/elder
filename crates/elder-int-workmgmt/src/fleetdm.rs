//! FleetDM v4.95.0 / v5 endpoint & security discovery connector.
//!
//! Discovers managed hosts, software versions (with `/software/titles` & `/software` fallbacks),
//! OS versions, vulnerabilities, policies (with `/global/policies` fallback), labels + memberships,
//! MDM configuration profiles (safe metadata only), and fleets/teams without persisting PII or secrets.

use std::collections::{BTreeMap, HashSet};
use std::time::Duration;

use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, AUTHORIZATION, CONTENT_TYPE};
use reqwest::StatusCode;
use serde_json::{json, Map, Value};

use crate::discovery_common::{
    cvss_to_severity, format_resource, sanitize_json_value, DiscoveryDependency,
    DiscoveryJobContext, DiscoveryOutput, DiscoveryRelationship, DiscoveryResource,
};
use crate::error::{Result, WorkMgmtError};
use crate::http_client::{
    build_http_client, get_with_fallbacks, normalize_base_url, truncate_body, FallbackGetClient,
};
use crate::sync_common::value_to_id_string;

const MAX_PAGES: usize = 500;

struct FleetClient<'a> {
    getter: FallbackGetClient<'a>,
    per_page: usize,
}

struct PaginatedOutcome {
    status: StatusCode,
    items: Vec<Value>,
    error: Option<String>,
}

struct NormalizedHost {
    resource: DiscoveryResource,
    dependencies: Vec<DiscoveryDependency>,
    fleet_link: Option<(String, String)>,
}

/// Run FleetDM discovery and return normalized Elder `resources` and `dependencies`.
pub async fn discover(ctx: &DiscoveryJobContext) -> Result<DiscoveryOutput> {
    let raw_url = ctx
        .get_str(&["url", "fleetdm_url", "base_url"])
        .ok_or_else(|| WorkMgmtError::Config("FleetDM discovery requires 'url'".into()))?;
    let base_url = normalize_base_url(&raw_url, true)?;
    let api_token = ctx
        .get_str(&["api_token", "token", "fleetdm_api_token"])
        .ok_or_else(|| {
            WorkMgmtError::Auth("FleetDM discovery requires 'api_token' or 'token'".into())
        })?;

    let per_page = ctx.get_i64(&["per_page"]).unwrap_or(100).clamp(1, 500) as usize;
    let sync_mdm = ctx.get_bool(&["sync_mdm"]).unwrap_or(true);
    let sync_labels = ctx.get_bool(&["sync_labels"]).unwrap_or(true);
    let verify_ssl = ctx.get_bool(&["verify_ssl"]).unwrap_or(true);
    let retry_delay = Duration::from_millis(
        ctx.get_i64(&["retry_delay_ms"])
            .map(|v| u64::try_from(v.max(0)).unwrap_or(150))
            .unwrap_or(150),
    );

    let http = build_http_client(verify_ssl)?;
    let headers = fleet_headers(&api_token)?;
    let client = FleetClient {
        getter: FallbackGetClient {
            http: &http,
            platform: "fleetdm",
            base_url: &base_url,
            headers: &headers,
            base_delay: retry_delay,
        },
        per_page,
    };

    let mut output = DiscoveryOutput::default();
    let mut host_fleet_links: Vec<(String, String)> = Vec::new();

    // 1. Hosts (core endpoint: 401/403 aborts loudly)
    let hosts_res = client
        .paginate(
            "/api/v1/fleet/hosts",
            &[],
            &["hosts"],
            &[("include_device_status".to_string(), "true".to_string())],
        )
        .await;

    if matches!(
        hosts_res.status,
        StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN
    ) {
        return Err(WorkMgmtError::ApiHttp {
            platform: "fleetdm",
            status: hosts_res.status.as_u16(),
            body: hosts_res
                .error
                .unwrap_or_else(|| "FleetDM authentication rejected".into()),
        });
    }
    if let Some(err) = hosts_res.error {
        output.partial_errors.push(err);
    }

    for host in &hosts_res.items {
        if let Some(norm) = normalize_host(host) {
            output.resources.push(norm.resource);
            output.dependencies.extend(norm.dependencies);
            if let Some(link) = norm.fleet_link {
                host_fleet_links.push(link);
            }
        }
    }

    // 2. Software versions (with /software/titles & /software fallbacks)
    let sw_res = client
        .paginate(
            "/api/v1/fleet/software/versions",
            &["/api/v1/fleet/software/titles", "/api/v1/fleet/software"],
            &["software", "software_titles", "titles"],
            &[(
                "without_vulnerability_details".to_string(),
                "true".to_string(),
            )],
        )
        .await;
    if let Some(err) = sw_res.error {
        output.partial_errors.push(err);
    }
    for sw in &sw_res.items {
        if let Some(res) = normalize_software(sw) {
            output.resources.push(res);
        }
    }

    // 3. OS versions
    let os_res = client
        .paginate("/api/v1/fleet/os_versions", &[], &["os_versions"], &[])
        .await;
    if let Some(err) = os_res.error {
        output.partial_errors.push(err);
    }
    for os_item in &os_res.items {
        if let Some(res) = normalize_os_version(os_item) {
            output.resources.push(res);
        }
    }

    // 4. Vulnerabilities
    let vuln_res = client
        .paginate(
            "/api/v1/fleet/vulnerabilities",
            &[],
            &["vulnerabilities"],
            &[],
        )
        .await;
    if let Some(err) = vuln_res.error {
        output.partial_errors.push(err);
    }
    for vuln in &vuln_res.items {
        if let Some((res, deps)) = normalize_vulnerability(vuln) {
            output.resources.push(res);
            output.dependencies.extend(deps);
        }
    }

    // 5. Policies (Fleet 5 unified + Fleet 4.x /global/policies fallback)
    let pol_res = client
        .paginate(
            "/api/v1/fleet/policies",
            &["/api/v1/fleet/global/policies"],
            &["policies"],
            &[],
        )
        .await;
    if let Some(err) = pol_res.error {
        output.partial_errors.push(err);
    }
    for policy in &pol_res.items {
        if let Some(res) = normalize_policy(policy) {
            output.resources.push(res);
        }
    }

    // 6. Labels (+ host memberships for custom/manual labels)
    if sync_labels {
        let lbl_res = client
            .paginate("/api/v1/fleet/labels", &[], &["labels"], &[])
            .await;
        if let Some(err) = lbl_res.error {
            output.partial_errors.push(err);
        }
        for label in &lbl_res.items {
            let Some(lid) = label.get("id").and_then(value_to_id_string) else {
                continue;
            };
            let label_type = label
                .get("label_type")
                .and_then(Value::as_str)
                .unwrap_or("custom");
            let label_hosts = if label_type != "builtin" {
                let path = format!("/api/v1/fleet/labels/{lid}/hosts");
                client.paginate(&path, &[], &["hosts"], &[]).await.items
            } else {
                Vec::new()
            };
            let (res, deps) = normalize_label(label, &lid, &label_hosts);
            output.resources.push(res);
            output.dependencies.extend(deps);
        }
    }

    // 7. MDM configuration profiles (graceful on Free/402/403/404; safe metadata only)
    if sync_mdm {
        let mdm_res = client
            .paginate(
                "/api/v1/fleet/mdm/profiles",
                &["/api/v1/fleet/configuration_profiles"],
                &["profiles", "mdm_profiles", "configuration_profiles"],
                &[],
            )
            .await;
        if let Some(err) = mdm_res.error {
            output.partial_errors.push(err);
        }
        for prof in &mdm_res.items {
            if let Some(res) = normalize_mdm_profile(prof) {
                output.resources.push(res);
            }
        }
    }

    // 8. Fleets (Fleet 5) / Teams (Fleet 4.x) — graceful on 402/403/404
    let fleets_res = client
        .paginate(
            "/api/v1/fleet/fleets",
            &["/api/v1/fleet/teams"],
            &["fleets", "teams"],
            &[],
        )
        .await;
    let mut known_fleets: HashSet<String> = HashSet::new();
    for fleet in &fleets_res.items {
        if let Some(res) = normalize_fleet(fleet) {
            known_fleets.insert(res.external_id.clone());
            output.resources.push(res);
        }
    }
    for (host_ext, fleet_ext) in host_fleet_links {
        if known_fleets.contains(&fleet_ext) {
            output.dependencies.push(DiscoveryDependency {
                source_external_id: host_ext,
                target_external_id: fleet_ext,
                dependency_type: "in_fleet".to_string(),
                description: Some("Host enrolled in Fleet/Team".to_string()),
                metadata: json!({}),
            });
        }
    }

    output
        .summary_extra
        .insert("hosts_count".to_string(), json!(hosts_res.items.len()));
    output
        .summary_extra
        .insert("software_count".to_string(), json!(sw_res.items.len()));
    output
        .summary_extra
        .insert("os_versions_count".to_string(), json!(os_res.items.len()));
    output.summary_extra.insert(
        "vulnerabilities_count".to_string(),
        json!(vuln_res.items.len()),
    );
    output
        .summary_extra
        .insert("policies_count".to_string(), json!(pol_res.items.len()));

    Ok(output)
}

impl FleetClient<'_> {
    async fn paginate(
        &self,
        primary_path: &str,
        fallback_paths: &[&str],
        result_keys: &[&str],
        extra_params: &[(String, String)],
    ) -> PaginatedOutcome {
        let mut all_items: Vec<Value> = Vec::new();
        let mut last_status = StatusCode::OK;

        for page in 0..MAX_PAGES {
            let mut query = Vec::with_capacity(extra_params.len() + 2);
            query.extend_from_slice(extra_params);
            query.push(("page".to_string(), page.to_string()));
            query.push(("per_page".to_string(), self.per_page.to_string()));

            let (status, json_body, raw_body) = match get_with_fallbacks(
                &self.getter,
                primary_path,
                fallback_paths,
                &query,
            )
            .await
            {
                Ok(tuple) => tuple,
                Err(err) => {
                    return PaginatedOutcome {
                        status: StatusCode::BAD_GATEWAY,
                        items: all_items,
                        error: Some(format!("{primary_path}: {err}")),
                    };
                }
            };

            last_status = status;
            if status != StatusCode::OK {
                if matches!(
                    status,
                    StatusCode::PAYMENT_REQUIRED
                        | StatusCode::FORBIDDEN
                        | StatusCode::NOT_FOUND
                        | StatusCode::GONE
                        | StatusCode::NO_CONTENT
                ) {
                    tracing::debug!(
                        path = primary_path,
                        status = status.as_u16(),
                        "FleetDM endpoint skipped cleanly"
                    );
                    return PaginatedOutcome {
                        status,
                        items: all_items,
                        error: None,
                    };
                }
                return PaginatedOutcome {
                    status,
                    items: all_items,
                    error: Some(format!(
                        "{primary_path} HTTP {}: {}",
                        status.as_u16(),
                        truncate_body(&raw_body)
                    )),
                };
            }

            let Some(data) = json_body else {
                break;
            };

            let items = extract_array_by_keys(&data, result_keys);
            if items.is_empty() {
                break;
            }
            let page_len = items.len();
            all_items.extend(items);

            let has_next = data
                .get("meta")
                .and_then(|m| m.get("has_next_results"))
                .and_then(Value::as_bool);
            if let Some(flag) = has_next {
                if !flag {
                    break;
                }
            } else if page_len < self.per_page {
                break;
            }
        }

        PaginatedOutcome {
            status: last_status,
            items: all_items,
            error: None,
        }
    }
}

fn fleet_headers(api_token: &str) -> Result<HeaderMap> {
    let mut headers = HeaderMap::new();
    headers.insert(
        AUTHORIZATION,
        HeaderValue::from_str(&format!("Bearer {api_token}"))
            .map_err(|e| WorkMgmtError::Auth(format!("invalid FleetDM bearer token: {e}")))?,
    );
    headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
    headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
    Ok(headers)
}

fn extract_array_by_keys(data: &Value, keys: &[&str]) -> Vec<Value> {
    if let Some(arr) = data.as_array() {
        return arr.clone();
    }
    if let Some(obj) = data.as_object() {
        for key in keys {
            if let Some(Value::Array(arr)) = obj.get(*key) {
                return arr.clone();
            }
        }
    }
    Vec::new()
}

/// Extract the PII/secret-free subset of a host's `mdm` metadata object.
pub fn extract_safe_mdm(raw_mdm: Option<&Value>) -> Option<Value> {
    let obj = raw_mdm?.as_object()?;
    let mut subset = Map::new();
    for key in [
        "enrollment_status",
        "connected_to_fleet",
        "name",
        "macos_settings",
        "os_settings",
        "device_status",
        "pending_action",
    ] {
        if let Some(val) = obj.get(key) {
            subset.insert(key.to_string(), val.clone());
        }
    }
    Some(sanitize_json_value(&Value::Object(subset)))
}

fn normalize_host(host: &Value) -> Option<NormalizedHost> {
    let host_id = host.get("id").and_then(value_to_id_string)?;
    let ext_id = format!("fleetdm:host:{host_id}");
    let platform = host
        .get("platform")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();
    let hostname = host
        .get("hostname")
        .or_else(|| host.get("computer_name"))
        .or_else(|| host.get("display_name"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Host-{host_id}"));

    let fleet_id = host
        .get("fleet_id")
        .and_then(value_to_id_string)
        .or_else(|| host.get("team_id").and_then(value_to_id_string));
    let mdm_safe = extract_safe_mdm(host.get("mdm"));

    let mut relationships = Vec::new();
    let mut dependencies = Vec::new();
    if let Some(os_id) = host.get("os_version_id").and_then(value_to_id_string) {
        let os_ext = format!("fleetdm:os:{os_id}");
        relationships.push(DiscoveryRelationship {
            target_external_id: os_ext.clone(),
            target_kind: "entity".to_string(),
            edge_type: "runs_on".to_string(),
        });
        dependencies.push(DiscoveryDependency {
            source_external_id: ext_id.clone(),
            target_external_id: os_ext,
            dependency_type: "runs_on".to_string(),
            description: Some("Host runs operating system version".to_string()),
            metadata: json!({}),
        });
    }

    let fleet_link = fleet_id
        .as_ref()
        .map(|fid| (ext_id.clone(), format!("fleetdm:fleet:{fid}")));

    let metadata = json!({
        "fleetdm_host_id": host.get("id"),
        "uuid": host.get("uuid"),
        "hostname": host.get("hostname"),
        "computer_name": host.get("computer_name"),
        "platform": platform,
        "os_version": host.get("os_version"),
        "osquery_version": host.get("osquery_version"),
        "hardware_model": host.get("hardware_model"),
        "hardware_serial": host.get("hardware_serial"),
        "hardware_vendor": host.get("hardware_vendor"),
        "memory": host.get("memory"),
        "disk_encryption_enabled": host.get("disk_encryption_enabled"),
        "primary_ip": host.get("primary_ip"),
        "primary_mac": host.get("primary_mac"),
        "public_ip": host.get("public_ip"),
        "status": host.get("status"),
        "seen_time": host.get("seen_time"),
        "fleet_id": fleet_id,
        "mdm": mdm_safe,
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "fleetdm".to_string());
    tags.insert("platform".to_string(), platform);

    let resource = format_resource(
        "fleetdm",
        host_id,
        "fleetdm_host",
        &hostname,
        ext_id,
        metadata,
    )
    .with_tags(tags)
    .with_relationships(relationships);

    Some(NormalizedHost {
        resource,
        dependencies,
        fleet_link,
    })
}

fn normalize_software(sw: &Value) -> Option<DiscoveryResource> {
    let sw_id = sw.get("id").and_then(value_to_id_string)?;
    let sw_name = sw
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Software-{sw_id}"));
    let sw_ver = sw
        .get("version")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let display = if sw_ver.is_empty() {
        sw_name.clone()
    } else {
        format!("{sw_name} {sw_ver}")
    };
    let source = sw
        .get("source")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();

    let metadata = json!({
        "fleetdm_software_id": sw.get("id"),
        "software_name": sw_name,
        "version": sw.get("version"),
        "source": sw.get("source"),
        "bundle_identifier": sw.get("bundle_identifier"),
        "hosts_count": sw.get("hosts_count").unwrap_or(&json!(0)),
        "generated_cpe": sw.get("generated_cpe"),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "fleetdm".to_string());
    tags.insert("source".to_string(), source);

    Some(
        format_resource(
            "fleetdm",
            format!("software-{sw_id}"),
            "fleetdm_software",
            &display,
            format!("fleetdm:software:{sw_id}"),
            metadata,
        )
        .with_tags(tags),
    )
}

fn normalize_os_version(os_item: &Value) -> Option<DiscoveryResource> {
    let os_id = os_item
        .get("os_version_id")
        .and_then(value_to_id_string)
        .or_else(|| os_item.get("id").and_then(value_to_id_string))?;
    let name_only = os_item
        .get("name_only")
        .and_then(Value::as_str)
        .unwrap_or("OS");
    let version = os_item.get("version").and_then(Value::as_str).unwrap_or("");
    let display = os_item
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| {
            let joined = format!("{name_only} {version}").trim().to_string();
            if joined.is_empty() {
                format!("OS-{os_id}")
            } else {
                joined
            }
        });
    let platform = os_item
        .get("platform")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();

    let metadata = json!({
        "os_version_id": os_item.get("os_version_id").or_else(|| os_item.get("id")),
        "name_only": os_item.get("name_only"),
        "version": os_item.get("version"),
        "platform": platform,
        "hosts_count": os_item.get("hosts_count").unwrap_or(&json!(0)),
        "generated_cpes": os_item.get("generated_cpes").unwrap_or(&json!([])),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "fleetdm".to_string());
    tags.insert("platform".to_string(), platform);

    Some(
        format_resource(
            "fleetdm",
            format!("os-{os_id}"),
            "fleetdm_os_version",
            &display,
            format!("fleetdm:os:{os_id}"),
            metadata,
        )
        .with_tags(tags),
    )
}

fn normalize_vulnerability(vuln: &Value) -> Option<(DiscoveryResource, Vec<DiscoveryDependency>)> {
    let cve = vuln
        .get("cve")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())?
        .to_string();
    let cvss = vuln.get("cvss_score").and_then(Value::as_f64);
    let severity = cvss_to_severity(cvss);
    let vuln_ext = format!("fleetdm:vuln:{cve}");

    let mut relationships = Vec::new();
    let mut dependencies = Vec::new();
    if let Some(sw_arr) = vuln.get("software").and_then(Value::as_array) {
        for sw in sw_arr {
            if let Some(sw_id) = sw.get("id").and_then(value_to_id_string) {
                let target_ext = format!("fleetdm:software:{sw_id}");
                relationships.push(DiscoveryRelationship {
                    target_external_id: target_ext.clone(),
                    target_kind: "entity".to_string(),
                    edge_type: "affects".to_string(),
                });
                dependencies.push(DiscoveryDependency {
                    source_external_id: vuln_ext.clone(),
                    target_external_id: target_ext,
                    dependency_type: "affects".to_string(),
                    description: Some(format!("Vulnerability {cve} affects software")),
                    metadata: json!({}),
                });
            }
        }
    }
    if let Some(os_arr) = vuln.get("os_versions").and_then(Value::as_array) {
        for os_entry in os_arr {
            let os_id = os_entry
                .get("os_version_id")
                .and_then(value_to_id_string)
                .or_else(|| os_entry.get("id").and_then(value_to_id_string));
            if let Some(oid) = os_id {
                let target_ext = format!("fleetdm:os:{oid}");
                relationships.push(DiscoveryRelationship {
                    target_external_id: target_ext.clone(),
                    target_kind: "entity".to_string(),
                    edge_type: "affects".to_string(),
                });
                dependencies.push(DiscoveryDependency {
                    source_external_id: vuln_ext.clone(),
                    target_external_id: target_ext,
                    dependency_type: "affects".to_string(),
                    description: Some(format!("Vulnerability {cve} affects OS version")),
                    metadata: json!({}),
                });
            }
        }
    }

    let metadata = json!({
        "cve": cve,
        "cvss_score": vuln.get("cvss_score"),
        "severity": severity,
        "epss_probability": vuln.get("epss_probability"),
        "cisa_known_exploit": vuln.get("cisa_known_exploit"),
        "cve_published": vuln.get("cve_published").or_else(|| vuln.get("published")),
        "hosts_count": vuln.get("hosts_count").unwrap_or(&json!(0)),
        "details_link": vuln.get("details_link"),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "fleetdm".to_string());
    tags.insert("severity".to_string(), severity.to_string());

    let res = format_resource(
        "fleetdm",
        cve.clone(),
        "fleetdm_vulnerability",
        &format!("CVE: {cve}"),
        vuln_ext,
        metadata,
    )
    .with_tags(tags)
    .with_relationships(relationships);
    Some((res, dependencies))
}

fn normalize_policy(policy: &Value) -> Option<DiscoveryResource> {
    let pid = policy.get("id").and_then(value_to_id_string)?;
    let platform = policy
        .get("platform")
        .and_then(Value::as_str)
        .unwrap_or("all")
        .to_string();
    let name = policy
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Policy-{pid}"));

    let metadata = json!({
        "fleetdm_policy_id": policy.get("id"),
        "platform": policy.get("platform"),
        "critical": policy.get("critical").and_then(Value::as_bool).unwrap_or(false),
        "passing_host_count": policy.get("passing_host_count").unwrap_or(&json!(0)),
        "failing_host_count": policy.get("failing_host_count").unwrap_or(&json!(0)),
        "query": policy.get("query"),
        "resolution": policy.get("resolution"),
        "install_software": policy.get("install_software"),
        "run_script": policy.get("run_script"),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "fleetdm".to_string());
    tags.insert("platform".to_string(), platform);

    Some(
        format_resource(
            "fleetdm",
            format!("policy-{pid}"),
            "fleetdm_policy",
            &name,
            format!("fleetdm:policy:{pid}"),
            metadata,
        )
        .with_tags(tags),
    )
}

fn normalize_label(
    label: &Value,
    lid: &str,
    label_hosts: &[Value],
) -> (DiscoveryResource, Vec<DiscoveryDependency>) {
    let label_type = label
        .get("label_type")
        .and_then(Value::as_str)
        .unwrap_or("custom")
        .to_string();
    let label_ext = format!("fleetdm:label:{lid}");
    let name = label
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Label-{lid}"));

    let mut relationships = Vec::new();
    let mut dependencies = Vec::new();
    for lhost in label_hosts {
        if let Some(hid) = lhost.get("id").and_then(value_to_id_string) {
            let host_ext = format!("fleetdm:host:{hid}");
            relationships.push(DiscoveryRelationship {
                target_external_id: host_ext.clone(),
                target_kind: "entity".to_string(),
                edge_type: "manages".to_string(),
            });
            dependencies.push(DiscoveryDependency {
                source_external_id: label_ext.clone(),
                target_external_id: host_ext,
                dependency_type: "manages".to_string(),
                description: Some(format!("FleetDM label {lid} groups host")),
                metadata: json!({}),
            });
        }
    }

    let metadata = json!({
        "fleetdm_label_id": label.get("id"),
        "label_type": label_type,
        "label_membership_type": label.get("label_membership_type"),
        "platform": label.get("platform"),
        "host_count": label.get("host_count"),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "fleetdm".to_string());
    tags.insert("label_type".to_string(), label_type);

    let res = format_resource(
        "fleetdm",
        format!("label-{lid}"),
        "fleetdm_label",
        &name,
        label_ext,
        metadata,
    )
    .with_tags(tags)
    .with_relationships(relationships);
    (res, dependencies)
}

fn normalize_mdm_profile(prof: &Value) -> Option<DiscoveryResource> {
    let puuid = prof
        .get("profile_uuid")
        .and_then(value_to_id_string)
        .or_else(|| prof.get("uuid").and_then(value_to_id_string))
        .or_else(|| prof.get("id").and_then(value_to_id_string))?;
    let platform = prof
        .get("platform")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();
    let name = prof
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| puuid.clone());

    let metadata = json!({
        "profile_uuid": puuid,
        "platform": prof.get("platform"),
        "identifier": prof.get("identifier"),
        "checksum": prof.get("checksum"),
        "self_service": prof.get("self_service"),
        "hidden": prof.get("hidden"),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "fleetdm".to_string());
    tags.insert("platform".to_string(), platform);

    Some(
        format_resource(
            "fleetdm",
            format!("profile-{puuid}"),
            "fleetdm_mdm_profile",
            &name,
            format!("fleetdm:profile:{puuid}"),
            metadata,
        )
        .with_tags(tags),
    )
}

fn normalize_fleet(fleet: &Value) -> Option<DiscoveryResource> {
    let fid = fleet.get("id").and_then(value_to_id_string)?;
    let name = fleet
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Fleet-{fid}"));

    let metadata = json!({
        "fleetdm_fleet_id": fleet.get("id"),
        "name": name,
        "description": fleet.get("description"),
        "host_count": fleet.get("host_count"),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "fleetdm".to_string());

    Some(
        format_resource(
            "fleetdm",
            format!("fleet-{fid}"),
            "fleetdm_fleet",
            &name,
            format!("fleetdm:fleet:{fid}"),
            metadata,
        )
        .with_tags(tags),
    )
}
