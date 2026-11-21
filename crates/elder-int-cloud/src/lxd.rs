//! LXD v1 REST infrastructure discovery (`instances`, `projects`, `networks`, `storage-pools`).

use reqwest::header::{HeaderName, HeaderValue};
use reqwest::Client;
use serde_json::{json, Map, Value};

use crate::common::{
    build_http_client, join_url, normalize_http_base_url, opt_bool, opt_str, req_str,
    safe_endpoint_label, truncate_body, CloudError, CloudResult, DiscoveryCollection,
    NormalizedResource, TlsOptions,
};

#[derive(Clone)]
struct LxdConfig {
    base_url: String,
    bearer_token: Option<String>,
    custom_header: Option<(String, String)>,
    tls: TlsOptions,
}

impl std::fmt::Debug for LxdConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LxdConfig")
            .field("base_url", &self.base_url)
            .field("has_bearer_token", &self.bearer_token.is_some())
            .field("has_custom_header", &self.custom_header.is_some())
            .field("tls", &self.tls)
            .finish()
    }
}

impl LxdConfig {
    fn from_map(cfg: &Map<String, Value>) -> CloudResult<Self> {
        let raw_url = req_str(
            cfg,
            &["url", "lxd_url", "api_base_url", "endpoint"],
            "lxd url",
        )?;
        let normalized = normalize_http_base_url(&raw_url, "lxd url")?;
        let base_url = normalized
            .strip_suffix("/1.0")
            .unwrap_or(&normalized)
            .to_string();

        let bearer_token = opt_str(
            cfg,
            &["trust_token", "lxd_trust_token", "token", "api_token"],
        );
        let custom_header = match (
            opt_str(cfg, &["auth_header_name", "header_name"]),
            opt_str(cfg, &["auth_header_value", "header_value"]),
        ) {
            (Some(k), Some(v)) => Some((k, v)),
            _ => None,
        };

        let identity_pem = if let Some(combined) = opt_str(cfg, &["identity_pem"]) {
            Some(combined)
        } else {
            let cert = opt_str(cfg, &["client_cert_pem", "lxd_cert", "cert_pem"]);
            let key = opt_str(cfg, &["client_key_pem", "lxd_key", "key_pem"]);
            match (cert, key) {
                (Some(c), Some(k)) => Some(format!("{c}\n{k}")),
                (Some(_), None) | (None, Some(_)) => {
                    return Err(CloudError::Config(
                        "both client_cert_pem and client_key_pem must be provided together".into(),
                    ))
                }
                (None, None) => None,
            }
        };

        let tls = TlsOptions {
            verify_ssl: opt_bool(cfg, &["verify_ssl", "lxd_verify_cert"], true),
            ca_cert_pem: opt_str(cfg, &["ca_cert_pem", "ca_cert"]),
            identity_pem,
        };

        Ok(Self {
            base_url,
            bearer_token,
            custom_header,
            tls,
        })
    }
}

async fn lxd_get_metadata_list(
    http: &Client,
    cfg: &LxdConfig,
    path: &str,
) -> CloudResult<Vec<Value>> {
    let url = join_url(&cfg.base_url, path);
    let mut req = http.get(&url);
    if let Some(tok) = &cfg.bearer_token {
        req = req.bearer_auth(tok);
    }
    if let Some((hdr_name, hdr_val)) = &cfg.custom_header {
        let name = HeaderName::from_bytes(hdr_name.as_bytes()).map_err(|e| {
            CloudError::Config(format!("invalid LXD auth_header_name {hdr_name:?}: {e}"))
        })?;
        let val = HeaderValue::from_str(hdr_val)
            .map_err(|e| CloudError::Config(format!("invalid LXD auth_header_value: {e}")))?;
        req = req.header(name, val);
    }

    let resp = req.send().await?;
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
        .map_err(|e| CloudError::Parse(format!("invalid LXD JSON for {path}: {e}")))?;

    let payload = data.get("metadata").unwrap_or(&data);
    Ok(payload.as_array().cloned().unwrap_or_default())
}

/// Run LXD v1 REST discovery (`instances`, `projects`, `networks`, `storage-pools`).
pub async fn discover(cfg_map: &Map<String, Value>) -> CloudResult<DiscoveryCollection> {
    let cfg = LxdConfig::from_map(cfg_map)?;
    let http = build_http_client(&cfg.tls)?;
    let mut out = DiscoveryCollection::default();

    match lxd_get_metadata_list(&http, &cfg, "/1.0/instances?recursion=1").await {
        Ok(items) => {
            out.mark_step_ok();
            for inst in items {
                let Some(name) = inst.get("name").and_then(Value::as_str) else {
                    continue;
                };
                let project = inst
                    .get("project")
                    .and_then(Value::as_str)
                    .unwrap_or("default");
                let ext_id = if project == "default" {
                    name.to_string()
                } else {
                    format!("{project}/{name}")
                };
                let inst_type = inst
                    .get("type")
                    .and_then(Value::as_str)
                    .unwrap_or("container");
                let resource_type = if inst_type == "virtual-machine" {
                    "lxd_vm"
                } else {
                    "lxd_container"
                };

                let config = inst.get("config");
                let devices = inst.get("devices").and_then(Value::as_object);
                let mut ipv4_addresses: Vec<String> = Vec::new();
                if let Some(net_map) = inst
                    .get("state")
                    .and_then(|s| s.get("network"))
                    .and_then(Value::as_object)
                {
                    for iface in net_map.values() {
                        if let Some(addrs) = iface.get("addresses").and_then(Value::as_array) {
                            for addr in addrs {
                                if addr.get("family").and_then(Value::as_str) == Some("inet") {
                                    if let Some(ip) = addr.get("address").and_then(Value::as_str) {
                                        ipv4_addresses.push(ip.to_string());
                                    }
                                }
                            }
                        }
                    }
                }

                let mut res = NormalizedResource::new(
                    &ext_id,
                    &ext_id,
                    resource_type,
                    "compute",
                    name,
                    "lxd",
                );
                res.metadata = json!({
                    "instance_name": name,
                    "project": project,
                    "instance_type": inst_type,
                    "status": inst.get("status").and_then(Value::as_str),
                    "architecture": inst.get("architecture").and_then(Value::as_str),
                    "os": config.and_then(|c| c.get("image.os")).and_then(Value::as_str),
                    "os_release": config.and_then(|c| c.get("image.release")).and_then(Value::as_str),
                    "ipv4_addresses": ipv4_addresses,
                    "profiles": inst.get("profiles").cloned().unwrap_or_else(|| json!([])),
                    "lxd_url": cfg.base_url,
                });

                if let Some(devs) = devices {
                    for dev in devs.values() {
                        let dev_type = dev.get("type").and_then(Value::as_str);
                        if dev_type == Some("nic") {
                            if let Some(net_name) = dev.get("network").and_then(Value::as_str) {
                                res.add_relationship(net_name, "networking_resource", "in_network");
                                out.push_dependency(
                                    &ext_id,
                                    "entity",
                                    net_name,
                                    "networking_resource",
                                    "in_network",
                                );
                            }
                        } else if dev_type == Some("disk") {
                            if let Some(pool_name) = dev.get("pool").and_then(Value::as_str) {
                                res.add_relationship(pool_name, "data_store", "attached_to");
                                out.push_dependency(
                                    &ext_id,
                                    "entity",
                                    pool_name,
                                    "data_store",
                                    "attached_to",
                                );
                            }
                        }
                    }
                }

                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("LXD instances: {err}")),
    }

    match lxd_get_metadata_list(&http, &cfg, "/1.0/projects?recursion=1").await {
        Ok(items) => {
            out.mark_step_ok();
            for proj in items {
                let Some(name) = proj.get("name").and_then(Value::as_str) else {
                    continue;
                };
                let mut res =
                    NormalizedResource::new(name, name, "lxd_project", "datacenter", name, "lxd");
                res.metadata = json!({
                    "description": proj.get("description").and_then(Value::as_str),
                    "config": proj.get("config").cloned().unwrap_or_else(|| json!({})),
                    "used_by": proj.get("used_by").cloned().unwrap_or_else(|| json!([])),
                });
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("LXD projects: {err}")),
    }

    match lxd_get_metadata_list(&http, &cfg, "/1.0/networks?recursion=1").await {
        Ok(items) => {
            out.mark_step_ok();
            for net in items {
                let Some(name) = net.get("name").and_then(Value::as_str) else {
                    continue;
                };
                let net_cfg = net.get("config");
                let mut res =
                    NormalizedResource::new(name, name, "lxd_network", "network", name, "lxd");
                res.metadata = json!({
                    "network_type": net.get("type").and_then(Value::as_str),
                    "status": net.get("status").and_then(Value::as_str),
                    "managed": net.get("managed").and_then(Value::as_bool),
                    "ipv4_address": net_cfg.and_then(|c| c.get("ipv4.address")).and_then(Value::as_str),
                    "ipv6_address": net_cfg.and_then(|c| c.get("ipv6.address")).and_then(Value::as_str),
                    "lxd_url": cfg.base_url,
                });
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("LXD networks: {err}")),
    }

    match lxd_get_metadata_list(&http, &cfg, "/1.0/storage-pools?recursion=1").await {
        Ok(items) => {
            out.mark_step_ok();
            for pool in items {
                let Some(name) = pool.get("name").and_then(Value::as_str) else {
                    continue;
                };
                let mut res =
                    NormalizedResource::new(name, name, "lxd_storage_pool", "storage", name, "lxd");
                res.metadata = json!({
                    "driver": pool.get("driver").and_then(Value::as_str),
                    "status": pool.get("status").and_then(Value::as_str),
                    "description": pool.get("description").and_then(Value::as_str),
                    "config": pool.get("config").cloned().unwrap_or_else(|| json!({})),
                    "lxd_url": cfg.base_url,
                });
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("LXD storage-pools: {err}")),
    }

    Ok(out)
}
