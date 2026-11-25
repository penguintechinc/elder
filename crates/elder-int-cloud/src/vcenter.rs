//! VMware vCenter REST API infrastructure discovery (`vm`, `host`, `cluster`, `network`, `datastore`).

use reqwest::Client;
use serde_json::{json, Map, Value};

use crate::common::{
    build_http_client, join_url, normalize_http_base_url, opt_bool, opt_str, req_str,
    safe_endpoint_label, truncate_body, CloudError, CloudResult, DiscoveryCollection,
    NormalizedResource, TlsOptions,
};

#[derive(Clone)]
struct VCenterConfig {
    base_url: String,
    username: String,
    password: String,
    tls: TlsOptions,
}

impl std::fmt::Debug for VCenterConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("VCenterConfig")
            .field("base_url", &self.base_url)
            .field("has_password", &(!self.password.is_empty()))
            .field("tls", &self.tls)
            .finish()
    }
}

impl VCenterConfig {
    fn from_map(cfg: &Map<String, Value>) -> CloudResult<Self> {
        let raw_url = opt_str(cfg, &["url", "vcenter_url", "api_base_url", "endpoint"])
            .or_else(|| {
                opt_str(cfg, &["host", "vcenter_host"]).map(|h| {
                    if h.starts_with("http://") || h.starts_with("https://") {
                        h
                    } else {
                        format!("https://{h}")
                    }
                })
            })
            .ok_or_else(|| CloudError::Config("missing vCenter url / host".into()))?;
        let base_url = normalize_http_base_url(&raw_url, "vcenter url")?;
        let username = req_str(cfg, &["username", "vcenter_username"], "vcenter username")?;
        let password = req_str(cfg, &["password", "vcenter_password"], "vcenter password")?;

        let tls = TlsOptions {
            verify_ssl: opt_bool(cfg, &["verify_ssl", "vcenter_verify_ssl"], true),
            ca_cert_pem: opt_str(cfg, &["ca_cert_pem", "ca_cert"]),
            identity_pem: None,
        };

        Ok(Self {
            base_url,
            username,
            password,
            tls,
        })
    }
}

async fn create_session(http: &Client, cfg: &VCenterConfig) -> CloudResult<String> {
    let url = join_url(&cfg.base_url, "/api/session");
    let resp = http
        .post(&url)
        .basic_auth(&cfg.username, Some(&cfg.password))
        .send()
        .await?;
    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(CloudError::Auth(format!(
            "vCenter session login HTTP {}: {}",
            status.as_u16(),
            truncate_body(&body)
        )));
    }

    let val: Value = resp
        .json()
        .await
        .map_err(|e| CloudError::Parse(format!("invalid vCenter session JSON: {e}")))?;

    let session_id = val
        .as_str()
        .or_else(|| val.get("value").and_then(Value::as_str))
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToOwned::to_owned)
        .ok_or_else(|| CloudError::Auth("vCenter session response missing session ID".into()))?;

    Ok(session_id)
}

async fn vcenter_get_list(
    http: &Client,
    cfg: &VCenterConfig,
    session_id: &str,
    path: &str,
) -> CloudResult<Vec<Value>> {
    let url = join_url(&cfg.base_url, path);
    let resp = http
        .get(&url)
        .header("vmware-api-session-id", session_id)
        .send()
        .await?;
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
        .map_err(|e| CloudError::Parse(format!("invalid vCenter JSON for {path}: {e}")))?;

    let arr = data
        .as_array()
        .or_else(|| data.get("value").and_then(Value::as_array))
        .cloned()
        .unwrap_or_default();
    Ok(arr)
}

/// Run vCenter REST discovery (`vm`, `host`, `cluster`, `network`, `datastore`).
pub async fn discover(cfg_map: &Map<String, Value>) -> CloudResult<DiscoveryCollection> {
    let cfg = VCenterConfig::from_map(cfg_map)?;
    let http = build_http_client(&cfg.tls)?;
    let session_id = create_session(&http, &cfg).await?;

    let mut out = DiscoveryCollection::default();

    match vcenter_get_list(&http, &cfg, &session_id, "/api/vcenter/vm").await {
        Ok(items) => {
            out.mark_step_ok();
            for vm in items {
                let Some(id) = vm.get("vm").and_then(Value::as_str) else {
                    continue;
                };
                let name = vm
                    .get("name")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(id);
                let mut res =
                    NormalizedResource::new(id, id, "vcenter_vm", "compute", name, "vcenter");
                res.metadata = json!({
                    "power_state": vm.get("power_state").and_then(Value::as_str),
                    "cpu_count": vm.get("cpu_count").and_then(Value::as_i64),
                    "memory_size_MiB": vm.get("memory_size_MiB").and_then(Value::as_i64),
                    "host": vm.get("host").and_then(Value::as_str),
                    "cluster": vm.get("cluster").and_then(Value::as_str),
                    "vcenter_url": cfg.base_url,
                });
                if let Some(host_id) = vm.get("host").and_then(Value::as_str) {
                    res.add_relationship(host_id, "entity", "runs_on");
                    out.push_dependency(id, "entity", host_id, "entity", "runs_on");
                }
                if let Some(net_id) = vm.get("network").and_then(Value::as_str) {
                    res.add_relationship(net_id, "networking_resource", "in_network");
                    out.push_dependency(id, "entity", net_id, "networking_resource", "in_network");
                }
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("vCenter vm: {err}")),
    }

    match vcenter_get_list(&http, &cfg, &session_id, "/api/vcenter/host").await {
        Ok(items) => {
            out.mark_step_ok();
            for host in items {
                let Some(id) = host.get("host").and_then(Value::as_str) else {
                    continue;
                };
                let name = host
                    .get("name")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(id);
                let mut res =
                    NormalizedResource::new(id, id, "vcenter_host", "compute", name, "vcenter");
                res.metadata = json!({
                    "connection_state": host.get("connection_state").and_then(Value::as_str),
                    "power_state": host.get("power_state").and_then(Value::as_str),
                    "cluster": host.get("cluster").and_then(Value::as_str),
                    "vcenter_url": cfg.base_url,
                });
                if let Some(cluster_id) = host.get("cluster").and_then(Value::as_str) {
                    res.add_relationship(cluster_id, "entity", "runs_in");
                    out.push_dependency(id, "entity", cluster_id, "entity", "runs_in");
                }
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("vCenter host: {err}")),
    }

    match vcenter_get_list(&http, &cfg, &session_id, "/api/vcenter/cluster").await {
        Ok(items) => {
            out.mark_step_ok();
            for cluster in items {
                let Some(id) = cluster.get("cluster").and_then(Value::as_str) else {
                    continue;
                };
                let name = cluster
                    .get("name")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(id);
                let mut res =
                    NormalizedResource::new(id, id, "vcenter_cluster", "compute", name, "vcenter");
                res.metadata = json!({
                    "ha_enabled": cluster.get("ha_enabled").and_then(Value::as_bool),
                    "drs_enabled": cluster.get("drs_enabled").and_then(Value::as_bool),
                    "vcenter_url": cfg.base_url,
                });
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("vCenter cluster: {err}")),
    }

    match vcenter_get_list(&http, &cfg, &session_id, "/api/vcenter/network").await {
        Ok(items) => {
            out.mark_step_ok();
            for net in items {
                let Some(id) = net.get("network").and_then(Value::as_str) else {
                    continue;
                };
                let name = net
                    .get("name")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(id);
                let mut res =
                    NormalizedResource::new(id, id, "vcenter_network", "network", name, "vcenter");
                res.metadata = json!({
                    "network_type": net.get("type").and_then(Value::as_str),
                    "vcenter_url": cfg.base_url,
                });
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("vCenter network: {err}")),
    }

    match vcenter_get_list(&http, &cfg, &session_id, "/api/vcenter/datastore").await {
        Ok(items) => {
            out.mark_step_ok();
            for ds in items {
                let Some(id) = ds.get("datastore").and_then(Value::as_str) else {
                    continue;
                };
                let name = ds
                    .get("name")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(id);
                let mut res = NormalizedResource::new(
                    id,
                    id,
                    "vcenter_datastore",
                    "storage",
                    name,
                    "vcenter",
                );
                res.metadata = json!({
                    "datastore_type": ds.get("type").and_then(Value::as_str),
                    "free_space": ds.get("free_space").and_then(Value::as_u64),
                    "capacity": ds.get("capacity").and_then(Value::as_u64),
                    "vcenter_url": cfg.base_url,
                });
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("vCenter datastore: {err}")),
    }

    let logout_url = join_url(&cfg.base_url, "/api/session");
    let _ = http
        .delete(&logout_url)
        .header("vmware-api-session-id", &session_id)
        .send()
        .await;

    Ok(out)
}
