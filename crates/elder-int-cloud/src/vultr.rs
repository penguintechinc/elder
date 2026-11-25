//! Vultr v2 API cloud discovery (`instances`, `vpcs`, `blocks`, `bare-metals`, `kubernetes/clusters`).

use reqwest::Client;
use serde_json::{json, Map, Value};

use crate::common::{
    build_http_client, join_url, normalize_http_base_url, opt_bool, opt_str, req_str,
    safe_endpoint_label, truncate_body, CloudError, CloudResult, DiscoveryCollection,
    NormalizedResource, TlsOptions,
};

const DEFAULT_VULTR_BASE: &str = "https://api.vultr.com";

#[derive(Clone)]
struct VultrConfig {
    api_key: String,
    api_base_url: String,
    tls: TlsOptions,
}

impl std::fmt::Debug for VultrConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("VultrConfig")
            .field("api_base_url", &self.api_base_url)
            .field("has_api_key", &(!self.api_key.is_empty()))
            .finish()
    }
}

impl VultrConfig {
    fn from_map(cfg: &Map<String, Value>) -> CloudResult<Self> {
        let api_key = req_str(cfg, &["api_key", "token", "api_token"], "api_key")?;
        let raw_base = opt_str(cfg, &["api_base_url", "base_url"])
            .unwrap_or_else(|| DEFAULT_VULTR_BASE.to_string());
        let normalized = normalize_http_base_url(&raw_base, "api_base_url")?;
        let api_base_url = normalized
            .strip_suffix("/v2")
            .unwrap_or(&normalized)
            .to_string();

        let tls = TlsOptions {
            verify_ssl: opt_bool(cfg, &["verify_ssl"], true),
            ca_cert_pem: opt_str(cfg, &["ca_cert_pem", "ca_cert"]),
            identity_pem: None,
        };

        Ok(Self {
            api_key,
            api_base_url,
            tls,
        })
    }
}

async fn vultr_get_paged(
    http: &Client,
    cfg: &VultrConfig,
    path: &str,
    array_keys: &[&str],
) -> CloudResult<Vec<Value>> {
    let mut items = Vec::new();
    let mut cursor: Option<String> = None;

    loop {
        let url_str = join_url(&cfg.api_base_url, path);
        let mut parsed = url::Url::parse(&url_str)
            .map_err(|e| CloudError::Config(format!("invalid Vultr URL: {e}")))?;
        if let Some(cur) = cursor.as_deref() {
            parsed.query_pairs_mut().append_pair("cursor", cur);
        }

        let resp = http
            .get(parsed.clone())
            .bearer_auth(&cfg.api_key)
            .send()
            .await?;
        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(CloudError::Http {
                status: status.as_u16(),
                endpoint: safe_endpoint_label(parsed.as_str()),
                body: truncate_body(&body),
            });
        }

        let data: Value = resp
            .json()
            .await
            .map_err(|e| CloudError::Parse(format!("invalid Vultr JSON for {path}: {e}")))?;

        for key in array_keys {
            if let Some(arr) = data.get(*key).and_then(Value::as_array) {
                items.extend(arr.iter().cloned());
                break;
            }
        }

        cursor = data
            .get("meta")
            .and_then(|m| m.get("links"))
            .and_then(|l| l.get("next"))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned);
        if cursor.is_none() {
            break;
        }
    }

    Ok(items)
}

fn attach_vpc_links(res: &mut NormalizedResource, item: &Value, out: &mut DiscoveryCollection) {
    if let Some(vpc_ids) = item.get("vpc_ids").and_then(Value::as_array) {
        for v in vpc_ids {
            if let Some(vpc_id) = v.as_str().filter(|s| !s.is_empty()) {
                res.add_relationship(vpc_id, "networking_resource", "in_network");
                out.push_dependency(
                    &res.external_id,
                    "entity",
                    vpc_id,
                    "networking_resource",
                    "in_network",
                );
            }
        }
    }
    if let Some(vpc_id) = item
        .get("vpc_id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
    {
        res.add_relationship(vpc_id, "networking_resource", "in_network");
        out.push_dependency(
            &res.external_id,
            "entity",
            vpc_id,
            "networking_resource",
            "in_network",
        );
    }
}

/// Run Vultr v2 cloud discovery.
pub async fn discover(cfg_map: &Map<String, Value>) -> CloudResult<DiscoveryCollection> {
    let cfg = VultrConfig::from_map(cfg_map)?;
    let http = build_http_client(&cfg.tls)?;
    let mut out = DiscoveryCollection::default();

    match vultr_get_paged(&http, &cfg, "/v2/instances", &["instances"]).await {
        Ok(items) => {
            out.mark_step_ok();
            for inst in items {
                let Some(id) = inst.get("id").and_then(Value::as_str) else {
                    continue;
                };
                let name = inst
                    .get("label")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(id);
                let mut res =
                    NormalizedResource::new(id, id, "vultr_instance", "compute", name, "vultr");
                res.region = inst
                    .get("region")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                res.metadata = json!({
                    "status": inst.get("status"),
                    "plan": inst.get("plan"),
                    "main_ip": inst.get("main_ip"),
                    "internal_ip": inst.get("internal_ip"),
                    "os": inst.get("os"),
                    "vcpu_count": inst.get("vcpu_count"),
                    "ram": inst.get("ram"),
                    "disk": inst.get("disk"),
                });
                attach_vpc_links(&mut res, &inst, &mut out);
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("Vultr instances: {err}")),
    }

    match vultr_get_paged(&http, &cfg, "/v2/vpcs", &["vpcs"]).await {
        Ok(items) => {
            out.mark_step_ok();
            for vpc in items {
                let Some(id) = vpc.get("id").and_then(Value::as_str) else {
                    continue;
                };
                let name = vpc
                    .get("description")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(id);
                let mut res =
                    NormalizedResource::new(id, id, "vultr_vpc", "network", name, "vultr");
                res.region = vpc
                    .get("region")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                res.metadata = json!({
                    "v4_subnet": vpc.get("v4_subnet"),
                    "v4_subnet_mask": vpc.get("v4_subnet_mask"),
                    "ip_block": vpc.get("ip_block"),
                });
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("Vultr vpcs: {err}")),
    }

    match vultr_get_paged(&http, &cfg, "/v2/blocks", &["blocks"]).await {
        Ok(items) => {
            out.mark_step_ok();
            for blk in items {
                let Some(id) = blk.get("id").and_then(Value::as_str) else {
                    continue;
                };
                let name = blk
                    .get("label")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(id);
                let mut res = NormalizedResource::new(
                    id,
                    id,
                    "vultr_block_storage",
                    "storage",
                    name,
                    "vultr",
                );
                res.region = blk
                    .get("region")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                res.metadata = json!({
                    "size_gb": blk.get("size_gb"),
                    "status": blk.get("status"),
                    "block_type": blk.get("block_type"),
                    "attached_to_instance": blk.get("attached_to_instance"),
                });
                if let Some(inst_id) = blk
                    .get("attached_to_instance")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                {
                    res.add_relationship(inst_id, "entity", "attached_to");
                    out.push_dependency(id, "data_store", inst_id, "entity", "attached_to");
                }
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("Vultr blocks: {err}")),
    }

    match vultr_get_paged(&http, &cfg, "/v2/bare-metals", &["bare_metals"]).await {
        Ok(items) => {
            out.mark_step_ok();
            for bm in items {
                let Some(id) = bm.get("id").and_then(Value::as_str) else {
                    continue;
                };
                let name = bm
                    .get("label")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(id);
                let mut res =
                    NormalizedResource::new(id, id, "vultr_bare_metal", "compute", name, "vultr");
                res.region = bm
                    .get("region")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                res.metadata = json!({
                    "status": bm.get("status"),
                    "plan": bm.get("plan"),
                    "main_ip": bm.get("main_ip"),
                    "os": bm.get("os"),
                    "cpu_count": bm.get("cpu_count"),
                    "ram": bm.get("ram"),
                });
                attach_vpc_links(&mut res, &bm, &mut out);
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("Vultr bare-metals: {err}")),
    }

    match vultr_get_paged(
        &http,
        &cfg,
        "/v2/kubernetes/clusters",
        &["vke_clusters", "clusters"],
    )
    .await
    {
        Ok(items) => {
            out.mark_step_ok();
            for cluster in items {
                let Some(id) = cluster.get("id").and_then(Value::as_str) else {
                    continue;
                };
                let name = cluster
                    .get("label")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(id);
                let mut res = NormalizedResource::new(
                    id,
                    id,
                    "vultr_kubernetes_cluster",
                    "compute",
                    name,
                    "vultr",
                );
                res.region = cluster
                    .get("region")
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned);
                res.metadata = json!({
                    "version": cluster.get("version"),
                    "status": cluster.get("status"),
                    "endpoint": cluster.get("endpoint"),
                    "node_pools": cluster.get("node_pools"),
                });
                out.resources.push(res);
            }
        }
        Err(err) => out.mark_step_err(format!("Vultr kubernetes/clusters: {err}")),
    }

    Ok(out)
}
