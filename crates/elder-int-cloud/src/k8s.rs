//! Kubernetes cluster discovery and RBAC IAM sync via pure-rustls REST calls.

use std::collections::{BTreeMap, BTreeSet};

use reqwest::Client;
use serde_json::{json, Map, Value};

use crate::common::{
    build_http_client, join_url, normalize_http_base_url, opt_bool, opt_str, parse_json_tags,
    safe_endpoint_label, truncate_body, CloudError, CloudResult, DiscoveryCollection,
    IamCollection, NormalizedResource, TlsOptions,
};

const IN_CLUSTER_TOKEN_PATH: &str = "/var/run/secrets/kubernetes.io/serviceaccount/token";
const IN_CLUSTER_CA_PATH: &str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt";

#[derive(Clone)]
struct K8sConfig {
    api_server: String,
    token: String,
    tls: TlsOptions,
}

impl std::fmt::Debug for K8sConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("K8sConfig")
            .field("api_server", &self.api_server)
            .field("tls", &self.tls)
            .finish()
    }
}

impl K8sConfig {
    fn from_map(cfg: &Map<String, Value>) -> CloudResult<Self> {
        let in_cluster = opt_bool(cfg, &["in_cluster"], false);

        let raw_endpoint = opt_str(cfg, &["api_server", "endpoint", "api_url", "url"])
            .or_else(|| {
                if in_cluster {
                    let host = std::env::var("KUBERNETES_SERVICE_HOST").ok()?;
                    let port = std::env::var("KUBERNETES_SERVICE_PORT")
                        .ok()
                        .unwrap_or_else(|| "443".to_string());
                    Some(format!("https://{host}:{port}"))
                } else {
                    None
                }
            })
            .ok_or_else(|| CloudError::Config("missing Kubernetes api_server / endpoint".into()))?;
        let api_server = normalize_http_base_url(&raw_endpoint, "api_server")?;

        let token = if let Some(tok) = opt_str(cfg, &["token", "bearer_token"]) {
            tok
        } else {
            let token_path =
                opt_str(cfg, &["token_file"]).unwrap_or_else(|| IN_CLUSTER_TOKEN_PATH.to_string());
            let raw = std::fs::read_to_string(&token_path).map_err(|e| {
                CloudError::Config(format!(
                    "missing Kubernetes token and failed reading {token_path}: {e}"
                ))
            })?;
            let trimmed = raw.trim().to_string();
            if trimmed.is_empty() {
                return Err(CloudError::Config(format!(
                    "Kubernetes token file {token_path} is empty"
                )));
            }
            trimmed
        };

        let ca_cert_pem = opt_str(cfg, &["ca_cert_pem", "ca_cert"]).or_else(|| {
            if in_cluster {
                std::fs::read_to_string(IN_CLUSTER_CA_PATH).ok()
            } else {
                None
            }
        });

        let tls = TlsOptions {
            verify_ssl: opt_bool(cfg, &["verify_ssl"], true),
            ca_cert_pem,
            identity_pem: None,
        };

        Ok(Self {
            api_server,
            token,
            tls,
        })
    }
}

async fn k8s_list_items(http: &Client, cfg: &K8sConfig, path: &str) -> CloudResult<Vec<Value>> {
    let mut items = Vec::new();
    let mut continue_tok: Option<String> = None;

    loop {
        let base = join_url(&cfg.api_server, path);
        let mut parsed = url::Url::parse(&base)
            .map_err(|e| CloudError::Config(format!("invalid Kubernetes URL: {e}")))?;
        if let Some(cont) = continue_tok.as_deref() {
            parsed.query_pairs_mut().append_pair("continue", cont);
        }

        let resp = http
            .get(parsed.clone())
            .bearer_auth(&cfg.token)
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
            .map_err(|e| CloudError::Parse(format!("invalid Kubernetes JSON for {path}: {e}")))?;
        if let Some(arr) = data.get("items").and_then(Value::as_array) {
            items.extend(arr.iter().cloned());
        }

        continue_tok = data
            .get("metadata")
            .and_then(|m| m.get("continue"))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned);
        if continue_tok.is_none() {
            break;
        }
    }

    Ok(items)
}

fn meta_uid_and_name(item: &Value) -> Option<(String, String)> {
    let meta = item.get("metadata")?;
    let name = meta.get("name").and_then(Value::as_str)?.to_string();
    let uid = meta
        .get("uid")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| name.clone());
    Some((uid, name))
}

fn meta_namespace(item: &Value) -> Option<String> {
    item.get("metadata")
        .and_then(|m| m.get("namespace"))
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .map(ToOwned::to_owned)
}

fn meta_labels(item: &Value) -> BTreeMap<String, String> {
    parse_json_tags(item.get("metadata").and_then(|m| m.get("labels")))
}

/// Parse Kubernetes CPU strings (`"500m"` -> `0.5`, `"2"` -> `2.0`).
pub fn parse_k8s_cpu(raw: &str) -> f64 {
    let s = raw.trim();
    if s.is_empty() {
        return 0.0;
    }
    if let Some(milli) = s.strip_suffix('m') {
        return milli.parse::<f64>().unwrap_or(0.0) / 1000.0;
    }
    s.parse::<f64>().unwrap_or(0.0)
}

/// Parse Kubernetes memory strings (`"512Mi"` -> bytes).
pub fn parse_k8s_memory(raw: &str) -> u64 {
    let s = raw.trim();
    if s.is_empty() {
        return 0;
    }
    let multipliers: &[(&str, f64)] = &[
        ("Ki", 1024.0),
        ("Mi", 1024.0 * 1024.0),
        ("Gi", 1024.0 * 1024.0 * 1024.0),
        ("Ti", 1024.0 * 1024.0 * 1024.0 * 1024.0),
        ("k", 1000.0),
        ("M", 1000.0 * 1000.0),
        ("G", 1000.0 * 1000.0 * 1000.0),
    ];
    for (suffix, mult) in multipliers {
        if let Some(num) = s.strip_suffix(suffix) {
            return (num.parse::<f64>().unwrap_or(0.0) * mult) as u64;
        }
    }
    s.parse::<u64>().unwrap_or(0)
}

/// Run Kubernetes cluster discovery (nodes, namespaces, deployments, pods, services, ingresses).
pub async fn discover(cfg_map: &Map<String, Value>) -> CloudResult<DiscoveryCollection> {
    let cfg = K8sConfig::from_map(cfg_map)?;
    let http = build_http_client(&cfg.tls)?;
    let mut out = DiscoveryCollection::default();

    let mut node_lookup: BTreeMap<String, String> = BTreeMap::new();
    let mut ns_lookup: BTreeMap<String, String> = BTreeMap::new();
    let mut svc_lookup: BTreeMap<(String, String), String> = BTreeMap::new();

    match fetch_nodes(&http, &cfg, &mut node_lookup).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("K8s nodes: {err}")),
    }

    match fetch_namespaces(&http, &cfg, &mut ns_lookup).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("K8s namespaces: {err}")),
    }

    match fetch_deployments(&http, &cfg, &ns_lookup, &mut out).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("K8s deployments: {err}")),
    }

    match fetch_pods(&http, &cfg, &ns_lookup, &node_lookup, &mut out).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("K8s pods: {err}")),
    }

    match fetch_services(&http, &cfg, &ns_lookup, &mut svc_lookup, &mut out).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("K8s services: {err}")),
    }

    match fetch_ingresses(&http, &cfg, &ns_lookup, &svc_lookup, &mut out).await {
        Ok(items) => {
            out.mark_step_ok();
            out.resources.extend(items);
        }
        Err(err) => out.mark_step_err(format!("K8s ingresses: {err}")),
    }

    Ok(out)
}

async fn fetch_nodes(
    http: &Client,
    cfg: &K8sConfig,
    node_lookup: &mut BTreeMap<String, String>,
) -> CloudResult<Vec<NormalizedResource>> {
    let items = k8s_list_items(http, cfg, "/api/v1/nodes").await?;
    let mut resources = Vec::new();

    for node in items {
        let Some((uid, name)) = meta_uid_and_name(&node) else {
            continue;
        };
        node_lookup.insert(name.clone(), uid.clone());

        let status = node.get("status");
        let node_info = status.and_then(|s| s.get("nodeInfo"));
        let capacity = status.and_then(|s| s.get("capacity"));
        let taints = node
            .get("spec")
            .and_then(|s| s.get("taints"))
            .cloned()
            .unwrap_or_else(|| json!([]));
        let conditions: Vec<&str> = status
            .and_then(|s| s.get("conditions"))
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(|c| c.get("type").and_then(Value::as_str))
                    .collect()
            })
            .unwrap_or_default();

        let mut res =
            NormalizedResource::new(&uid, &uid, "k8s_node", "compute", &name, "kubernetes");
        res.tags = meta_labels(&node);
        res.metadata = json!({
            "capacity_cpu": capacity.and_then(|c| c.get("cpu")).and_then(Value::as_str),
            "capacity_memory": capacity.and_then(|c| c.get("memory")).and_then(Value::as_str),
            "kubelet_version": node_info.and_then(|i| i.get("kubeletVersion")).and_then(Value::as_str),
            "os_image": node_info.and_then(|i| i.get("osImage")).and_then(Value::as_str),
            "architecture": node_info.and_then(|i| i.get("architecture")).and_then(Value::as_str),
            "kernel_version": node_info.and_then(|i| i.get("kernelVersion")).and_then(Value::as_str),
            "container_runtime": node_info.and_then(|i| i.get("containerRuntimeVersion")).and_then(Value::as_str),
            "taints": taints,
            "conditions": conditions,
        });
        resources.push(res);
    }

    Ok(resources)
}

async fn fetch_namespaces(
    http: &Client,
    cfg: &K8sConfig,
    ns_lookup: &mut BTreeMap<String, String>,
) -> CloudResult<Vec<NormalizedResource>> {
    let items = k8s_list_items(http, cfg, "/api/v1/namespaces").await?;
    let mut resources = Vec::new();

    for ns in items {
        let Some((uid, name)) = meta_uid_and_name(&ns) else {
            continue;
        };
        ns_lookup.insert(name.clone(), uid.clone());
        let phase = ns
            .get("status")
            .and_then(|s| s.get("phase"))
            .and_then(Value::as_str);

        let mut res =
            NormalizedResource::new(&uid, &uid, "k8s_namespace", "network", &name, "kubernetes");
        res.tags = meta_labels(&ns);
        res.metadata = json!({
            "namespace": name,
            "phase": phase,
        });
        resources.push(res);
    }

    Ok(resources)
}

async fn fetch_deployments(
    http: &Client,
    cfg: &K8sConfig,
    ns_lookup: &BTreeMap<String, String>,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let items = k8s_list_items(http, cfg, "/apis/apps/v1/deployments").await?;
    let mut resources = Vec::new();

    for dep in items {
        let Some((uid, name)) = meta_uid_and_name(&dep) else {
            continue;
        };
        let namespace = meta_namespace(&dep);
        let spec = dep.get("spec");
        let status = dep.get("status");
        let selector = spec
            .and_then(|s| s.get("selector"))
            .and_then(|sel| sel.get("matchLabels"))
            .cloned()
            .unwrap_or_else(|| json!({}));
        let images: Vec<&str> = spec
            .and_then(|s| s.get("template"))
            .and_then(|t| t.get("spec"))
            .and_then(|ps| ps.get("containers"))
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(|c| c.get("image").and_then(Value::as_str))
                    .collect()
            })
            .unwrap_or_default();

        let mut res =
            NormalizedResource::new(&uid, &uid, "k8s_deployment", "compute", &name, "kubernetes");
        res.tags = meta_labels(&dep);
        res.metadata = json!({
            "namespace": namespace,
            "replicas": spec.and_then(|s| s.get("replicas")).and_then(Value::as_i64),
            "available_replicas": status.and_then(|s| s.get("availableReplicas")).and_then(Value::as_i64),
            "ready_replicas": status.and_then(|s| s.get("readyReplicas")).and_then(Value::as_i64),
            "images": images,
            "selector": selector,
        });

        if let Some(ns_name) = &namespace {
            let ns_target = ns_lookup
                .get(ns_name)
                .cloned()
                .unwrap_or_else(|| ns_name.clone());
            res.add_relationship(&ns_target, "networking_resource", "runs_in");
            out.push_dependency(&uid, "entity", &ns_target, "networking_resource", "runs_in");
        }

        resources.push(res);
    }

    Ok(resources)
}

async fn fetch_pods(
    http: &Client,
    cfg: &K8sConfig,
    ns_lookup: &BTreeMap<String, String>,
    node_lookup: &BTreeMap<String, String>,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let items = k8s_list_items(http, cfg, "/api/v1/pods").await?;
    let mut resources = Vec::new();

    for pod in items {
        let Some((uid, name)) = meta_uid_and_name(&pod) else {
            continue;
        };
        let namespace = meta_namespace(&pod);
        let spec = pod.get("spec");
        let status = pod.get("status");
        let node_name = spec
            .and_then(|s| s.get("nodeName"))
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned);

        let containers_arr = spec
            .and_then(|s| s.get("containers"))
            .and_then(Value::as_array);
        let mut containers_meta = Vec::new();
        let mut req_cpu = 0.0_f64;
        let mut req_mem = 0_u64;
        let mut lim_cpu = 0.0_f64;
        let mut lim_mem = 0_u64;

        if let Some(arr) = containers_arr {
            for c in arr {
                containers_meta.push(json!({
                    "name": c.get("name").and_then(Value::as_str),
                    "image": c.get("image").and_then(Value::as_str),
                }));
                if let Some(requests) = c.get("resources").and_then(|r| r.get("requests")) {
                    if let Some(cpu) = requests.get("cpu").and_then(Value::as_str) {
                        req_cpu += parse_k8s_cpu(cpu);
                    }
                    if let Some(mem) = requests.get("memory").and_then(Value::as_str) {
                        req_mem += parse_k8s_memory(mem);
                    }
                }
                if let Some(limits) = c.get("resources").and_then(|r| r.get("limits")) {
                    if let Some(cpu) = limits.get("cpu").and_then(Value::as_str) {
                        lim_cpu += parse_k8s_cpu(cpu);
                    }
                    if let Some(mem) = limits.get("memory").and_then(Value::as_str) {
                        lim_mem += parse_k8s_memory(mem);
                    }
                }
            }
        }

        let mut res =
            NormalizedResource::new(&uid, &uid, "k8s_pod", "compute", &name, "kubernetes");
        res.tags = meta_labels(&pod);
        res.metadata = json!({
            "namespace": namespace,
            "node_name": node_name,
            "phase": status.and_then(|s| s.get("phase")).and_then(Value::as_str),
            "pod_ip": status.and_then(|s| s.get("podIP")).and_then(Value::as_str),
            "containers_count": containers_meta.len(),
            "containers": containers_meta,
            "resource_requests": { "cpu": req_cpu, "memory": req_mem },
            "resource_limits": { "cpu": lim_cpu, "memory": lim_mem },
        });

        if let Some(ns_name) = &namespace {
            let ns_target = ns_lookup
                .get(ns_name)
                .cloned()
                .unwrap_or_else(|| ns_name.clone());
            res.add_relationship(&ns_target, "networking_resource", "runs_in");
            out.push_dependency(&uid, "entity", &ns_target, "networking_resource", "runs_in");
        }
        if let Some(nn) = &node_name {
            let node_target = node_lookup.get(nn).cloned().unwrap_or_else(|| nn.clone());
            res.add_relationship(&node_target, "entity", "runs_on");
            out.push_dependency(&uid, "entity", &node_target, "entity", "runs_on");
        }

        resources.push(res);
    }

    Ok(resources)
}

async fn fetch_services(
    http: &Client,
    cfg: &K8sConfig,
    ns_lookup: &BTreeMap<String, String>,
    svc_lookup: &mut BTreeMap<(String, String), String>,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let items = k8s_list_items(http, cfg, "/api/v1/services").await?;
    let mut resources = Vec::new();

    for svc in items {
        let Some((uid, name)) = meta_uid_and_name(&svc) else {
            continue;
        };
        let namespace = meta_namespace(&svc);
        if let Some(ns) = &namespace {
            svc_lookup.insert((ns.clone(), name.clone()), uid.clone());
        }

        let spec = svc.get("spec");
        let ports: Vec<Value> = spec
            .and_then(|s| s.get("ports"))
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .map(|p| {
                        json!({
                            "port": p.get("port").and_then(Value::as_i64),
                            "protocol": p.get("protocol").and_then(Value::as_str),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();

        let mut res =
            NormalizedResource::new(&uid, &uid, "k8s_service", "network", &name, "kubernetes");
        res.tags = meta_labels(&svc);
        res.metadata = json!({
            "namespace": namespace,
            "type": spec.and_then(|s| s.get("type")).and_then(Value::as_str),
            "cluster_ip": spec.and_then(|s| s.get("clusterIP")).and_then(Value::as_str),
            "ports": ports,
        });

        if let Some(ns_name) = &namespace {
            let ns_target = ns_lookup
                .get(ns_name)
                .cloned()
                .unwrap_or_else(|| ns_name.clone());
            res.add_relationship(&ns_target, "networking_resource", "runs_in");
            out.push_dependency(
                &uid,
                "service",
                &ns_target,
                "networking_resource",
                "runs_in",
            );
        }

        resources.push(res);
    }

    Ok(resources)
}

async fn fetch_ingresses(
    http: &Client,
    cfg: &K8sConfig,
    ns_lookup: &BTreeMap<String, String>,
    svc_lookup: &BTreeMap<(String, String), String>,
    out: &mut DiscoveryCollection,
) -> CloudResult<Vec<NormalizedResource>> {
    let items = k8s_list_items(http, cfg, "/apis/networking.k8s.io/v1/ingresses").await?;
    let mut resources = Vec::new();

    for ing in items {
        let Some((uid, name)) = meta_uid_and_name(&ing) else {
            continue;
        };
        let namespace = meta_namespace(&ing);
        let spec = ing.get("spec");

        let mut tls_hosts: Vec<String> = Vec::new();
        if let Some(tls_arr) = spec.and_then(|s| s.get("tls")).and_then(Value::as_array) {
            for tls in tls_arr {
                if let Some(hosts) = tls.get("hosts").and_then(Value::as_array) {
                    for h in hosts {
                        if let Some(hs) = h.as_str() {
                            tls_hosts.push(hs.to_string());
                        }
                    }
                }
            }
        }

        let mut paths = Vec::new();
        let mut backend_services: Vec<String> = Vec::new();
        if let Some(rules) = spec.and_then(|s| s.get("rules")).and_then(Value::as_array) {
            for rule in rules {
                let host = rule.get("host").and_then(Value::as_str).unwrap_or("*");
                if let Some(http_paths) = rule
                    .get("http")
                    .and_then(|h| h.get("paths"))
                    .and_then(Value::as_array)
                {
                    for p in http_paths {
                        let svc = p.get("backend").and_then(|b| b.get("service"));
                        let svc_name = svc.and_then(|s| s.get("name")).and_then(Value::as_str);
                        let svc_port = svc.and_then(|s| s.get("port")).and_then(|pt| {
                            pt.get("number")
                                .cloned()
                                .or_else(|| pt.get("name").cloned())
                        });
                        paths.push(json!({
                            "host": host,
                            "path": p.get("path").and_then(Value::as_str).unwrap_or("/"),
                            "path_type": p.get("pathType").and_then(Value::as_str),
                            "service": svc_name,
                            "port": svc_port,
                        }));
                        if let Some(sn) = svc_name {
                            if !backend_services.iter().any(|b| b == sn) {
                                backend_services.push(sn.to_string());
                            }
                        }
                    }
                }
            }
        }

        let mut res =
            NormalizedResource::new(&uid, &uid, "k8s_ingress", "network", &name, "kubernetes");
        res.tags = meta_labels(&ing);
        res.metadata = json!({
            "namespace": namespace,
            "ingress_class": spec.and_then(|s| s.get("ingressClassName")).and_then(Value::as_str),
            "paths": paths,
            "tls_enabled": !tls_hosts.is_empty(),
            "tls_hosts": tls_hosts,
            "backend_services": backend_services,
        });

        if let Some(ns_name) = &namespace {
            let ns_target = ns_lookup
                .get(ns_name)
                .cloned()
                .unwrap_or_else(|| ns_name.clone());
            res.add_relationship(&ns_target, "networking_resource", "runs_in");
            out.push_dependency(
                &uid,
                "networking_resource",
                &ns_target,
                "networking_resource",
                "runs_in",
            );

            for svc_name in &backend_services {
                let svc_target = svc_lookup
                    .get(&(ns_name.clone(), svc_name.clone()))
                    .cloned()
                    .unwrap_or_else(|| svc_name.clone());
                res.add_relationship(&svc_target, "service", "routes_to");
                out.push_dependency(
                    &uid,
                    "networking_resource",
                    &svc_target,
                    "service",
                    "routes_to",
                );
            }
        }

        resources.push(res);
    }

    Ok(resources)
}

/// Sync Kubernetes RBAC (ServiceAccounts, ClusterRoles, ClusterRoleBindings, Roles, RoleBindings).
pub async fn sync_iam(cfg_map: &Map<String, Value>) -> CloudResult<IamCollection> {
    let cfg = K8sConfig::from_map(cfg_map)?;
    let http = build_http_client(&cfg.tls)?;
    let mut out = IamCollection::default();
    let mut seen_groups: BTreeSet<String> = BTreeSet::new();

    match k8s_list_items(&http, &cfg, "/api/v1/serviceaccounts").await {
        Ok(items) => {
            out.mark_step_ok();
            for sa in items {
                let Some((uid, name)) = meta_uid_and_name(&sa) else {
                    continue;
                };
                let namespace = meta_namespace(&sa).unwrap_or_else(|| "default".to_string());
                out.users.push(json!({
                    "id": uid,
                    "external_id": uid,
                    "username": format!("{namespace}/{name}"),
                    "name": name,
                    "namespace": namespace,
                    "labels": meta_labels(&sa),
                    "kind": "service_account",
                    "provider": "kubernetes",
                }));
            }
        }
        Err(err) => out.mark_step_err(format!("K8s serviceaccounts: {err}")),
    }

    match k8s_list_items(
        &http,
        &cfg,
        "/apis/rbac.authorization.k8s.io/v1/clusterroles",
    )
    .await
    {
        Ok(items) => {
            out.mark_step_ok();
            for role in items {
                let Some((uid, name)) = meta_uid_and_name(&role) else {
                    continue;
                };
                out.roles.push(json!({
                    "id": uid,
                    "external_id": uid,
                    "name": name,
                    "scope": "cluster",
                    "kind": "ClusterRole",
                    "rules": role.get("rules").cloned().unwrap_or_else(|| json!([])),
                    "labels": meta_labels(&role),
                    "provider": "kubernetes",
                }));
            }
        }
        Err(err) => out.mark_step_err(format!("K8s clusterroles: {err}")),
    }

    match k8s_list_items(&http, &cfg, "/apis/rbac.authorization.k8s.io/v1/roles").await {
        Ok(items) => {
            out.mark_step_ok();
            for role in items {
                let Some((uid, name)) = meta_uid_and_name(&role) else {
                    continue;
                };
                let namespace = meta_namespace(&role);
                out.roles.push(json!({
                    "id": uid,
                    "external_id": uid,
                    "name": name,
                    "namespace": namespace,
                    "scope": "namespace",
                    "kind": "Role",
                    "rules": role.get("rules").cloned().unwrap_or_else(|| json!([])),
                    "labels": meta_labels(&role),
                    "provider": "kubernetes",
                }));
            }
        }
        Err(err) => out.mark_step_err(format!("K8s roles: {err}")),
    }

    match k8s_list_items(
        &http,
        &cfg,
        "/apis/rbac.authorization.k8s.io/v1/clusterrolebindings",
    )
    .await
    {
        Ok(items) => {
            out.mark_step_ok();
            collect_bindings(
                &items,
                "cluster",
                "ClusterRoleBinding",
                &mut seen_groups,
                &mut out,
            );
        }
        Err(err) => out.mark_step_err(format!("K8s clusterrolebindings: {err}")),
    }

    match k8s_list_items(
        &http,
        &cfg,
        "/apis/rbac.authorization.k8s.io/v1/rolebindings",
    )
    .await
    {
        Ok(items) => {
            out.mark_step_ok();
            collect_bindings(
                &items,
                "namespace",
                "RoleBinding",
                &mut seen_groups,
                &mut out,
            );
        }
        Err(err) => out.mark_step_err(format!("K8s rolebindings: {err}")),
    }

    Ok(out)
}

fn collect_bindings(
    items: &[Value],
    scope: &str,
    kind: &str,
    seen_groups: &mut BTreeSet<String>,
    out: &mut IamCollection,
) {
    for binding in items {
        let Some((uid, name)) = meta_uid_and_name(binding) else {
            continue;
        };
        let namespace = meta_namespace(binding);
        let role_ref = binding.get("roleRef");
        let role_kind = role_ref.and_then(|r| r.get("kind")).and_then(Value::as_str);
        let role_name = role_ref.and_then(|r| r.get("name")).and_then(Value::as_str);

        if let Some(subjects) = binding.get("subjects").and_then(Value::as_array) {
            for subj in subjects {
                let subj_kind = subj.get("kind").and_then(Value::as_str).unwrap_or("");
                let Some(subj_name) = subj.get("name").and_then(Value::as_str) else {
                    continue;
                };
                let subj_ns = subj.get("namespace").and_then(Value::as_str);

                if subj_kind.eq_ignore_ascii_case("Group")
                    && seen_groups.insert(subj_name.to_string())
                {
                    out.groups.push(json!({
                        "id": subj_name,
                        "external_id": format!("k8s-group:{subj_name}"),
                        "name": subj_name,
                        "provider": "kubernetes",
                    }));
                }

                out.memberships.push(json!({
                    "binding_uid": uid,
                    "binding_name": name,
                    "namespace": namespace,
                    "subject_kind": subj_kind,
                    "subject_name": subj_name,
                    "subject_namespace": subj_ns,
                    "role_kind": role_kind,
                    "role_name": role_name,
                    "scope": scope,
                    "kind": kind,
                    "provider": "kubernetes",
                }));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_k8s_cpu_and_memory_units() {
        assert!((parse_k8s_cpu("500m") - 0.5).abs() < 1e-6);
        assert!((parse_k8s_cpu("2") - 2.0).abs() < 1e-6);
        assert_eq!(parse_k8s_cpu(""), 0.0);

        assert_eq!(parse_k8s_memory("512Mi"), 512 * 1024 * 1024);
        assert_eq!(parse_k8s_memory("2Gi"), 2 * 1024 * 1024 * 1024);
        assert_eq!(parse_k8s_memory("1024Ki"), 1024 * 1024);
        assert_eq!(parse_k8s_memory("100M"), 100_000_000);
        assert_eq!(parse_k8s_memory("1048576"), 1_048_576);
        assert_eq!(parse_k8s_memory(""), 0);
    }
}
