//! iBoss cloud security endpoint & policy discovery connector.

use std::collections::{BTreeMap, HashSet};
use std::time::Duration;

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, AUTHORIZATION, CONTENT_TYPE};
use reqwest::StatusCode;
use serde_json::{json, Value};

use crate::discovery_common::{
    format_resource, DiscoveryDependency, DiscoveryJobContext, DiscoveryOutput,
    DiscoveryRelationship, DiscoveryResource,
};
use crate::error::{Result, WorkMgmtError};
use crate::http_client::{
    build_http_client, get_with_fallbacks, normalize_base_url, send_with_retry, truncate_body,
    FallbackGetClient,
};
use crate::sync_common::value_to_id_string;

/// Authenticate against iBoss and discover devices, groups, policies, categories, connectors, and apps.
pub async fn discover(ctx: &DiscoveryJobContext) -> Result<DiscoveryOutput> {
    let raw_url = ctx
        .get_str(&["url", "base_url", "iboss_url", "api_url"])
        .ok_or_else(|| {
            WorkMgmtError::Config("iBoss discovery requires 'url' or 'base_url'".into())
        })?;
    let base_url = normalize_base_url(&raw_url, true)?;
    let verify_ssl = ctx.get_bool(&["verify_ssl"]).unwrap_or(true);
    let retry_delay = Duration::from_millis(
        ctx.get_i64(&["retry_delay_ms"])
            .map(|v| u64::try_from(v.max(0)).unwrap_or(150))
            .unwrap_or(150),
    );
    let http = build_http_client(verify_ssl)?;
    let headers = authenticate_iboss(&http, &base_url, ctx, retry_delay).await?;
    let getter = FallbackGetClient {
        http: &http,
        platform: "iboss",
        base_url: &base_url,
        headers: &headers,
        base_delay: retry_delay,
    };

    let mut output = DiscoveryOutput::default();
    let mut known_categories: HashSet<String> = HashSet::new();

    // 1. Devices
    let (dev_status, dev_items, dev_err) =
        fetch_iboss_collection(&getter, "/api/v1/devices", &[], &["devices", "items"]).await?;
    if matches!(dev_status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN) {
        return Err(WorkMgmtError::ApiHttp {
            platform: "iboss",
            status: dev_status.as_u16(),
            body: dev_err.unwrap_or_else(|| "iBoss authentication rejected".into()),
        });
    }
    if let Some(err) = dev_err {
        output.partial_errors.push(err);
    }
    for dev in &dev_items {
        if let Some((res, deps)) = normalize_device(dev) {
            output.resources.push(res);
            output.dependencies.extend(deps);
        }
    }

    // 2. Groups
    let (grp_status, grp_items, grp_err) =
        fetch_iboss_collection(&getter, "/api/v1/groups", &[], &["groups", "items"]).await?;
    if matches!(grp_status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN) {
        return Err(WorkMgmtError::ApiHttp {
            platform: "iboss",
            status: grp_status.as_u16(),
            body: grp_err.unwrap_or_else(|| "iBoss authentication rejected".into()),
        });
    }
    if let Some(err) = grp_err {
        output.partial_errors.push(err);
    }
    for grp in &grp_items {
        if let Some((res, deps)) = normalize_group(grp) {
            output.resources.push(res);
            output.dependencies.extend(deps);
        }
    }

    // 3. Policies (and referenced categories)
    let (pol_status, pol_items, pol_err) =
        fetch_iboss_collection(&getter, "/api/v1/policies", &[], &["policies", "items"]).await?;
    if matches!(pol_status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN) {
        return Err(WorkMgmtError::ApiHttp {
            platform: "iboss",
            status: pol_status.as_u16(),
            body: pol_err.unwrap_or_else(|| "iBoss authentication rejected".into()),
        });
    }
    if let Some(err) = pol_err {
        output.partial_errors.push(err);
    }
    let mut policy_categories: Vec<String> = Vec::new();
    for pol in &pol_items {
        if let Some((res, deps, cats)) = normalize_policy(pol) {
            output.resources.push(res);
            output.dependencies.extend(deps);
            policy_categories.extend(cats);
        }
    }

    // 4. Categories (with /web-categories fallback, plus synthesize categories referenced by policies)
    let (_, cat_items, cat_err) = fetch_iboss_collection(
        &getter,
        "/api/v1/categories",
        &["/api/v1/web-categories"],
        &["categories", "items"],
    )
    .await?;
    if let Some(err) = cat_err {
        output.partial_errors.push(err);
    }
    for cat in &cat_items {
        if let Some(res) = normalize_category(cat) {
            known_categories.insert(res.external_id.clone());
            output.resources.push(res);
        }
    }
    for cat_name in policy_categories {
        let slug = slugify_category(&cat_name);
        let ext_id = format!("iboss:category:{slug}");
        if known_categories.insert(ext_id.clone()) {
            let mut tags = BTreeMap::new();
            tags.insert("provider".to_string(), "iboss".to_string());
            tags.insert("category".to_string(), cat_name.clone());
            output.resources.push(
                format_resource(
                    "iboss",
                    format!("category-{slug}"),
                    "iboss_category",
                    &cat_name,
                    ext_id,
                    json!({ "category_name": cat_name, "source": "policy_reference" }),
                )
                .with_tags(tags),
            );
        }
    }

    // 5. Cloud connectors (graceful on 404)
    let (_, conn_items, conn_err) =
        fetch_iboss_collection(&getter, "/api/v1/connectors", &[], &["connectors", "items"])
            .await?;
    if let Some(err) = conn_err {
        output.partial_errors.push(err);
    }
    for conn in &conn_items {
        if let Some(res) = normalize_connector(conn) {
            output.resources.push(res);
        }
    }

    // 6. Applications (graceful on 404)
    let (_, app_items, app_err) = fetch_iboss_collection(
        &getter,
        "/api/v1/applications",
        &[],
        &["applications", "items"],
    )
    .await?;
    if let Some(err) = app_err {
        output.partial_errors.push(err);
    }
    for app in &app_items {
        if let Some(res) = normalize_application(app) {
            output.resources.push(res);
        }
    }

    output
        .summary_extra
        .insert("devices_count".to_string(), json!(dev_items.len()));
    output
        .summary_extra
        .insert("groups_count".to_string(), json!(grp_items.len()));
    output
        .summary_extra
        .insert("policies_count".to_string(), json!(pol_items.len()));
    output.summary_extra.insert(
        "categories_count".to_string(),
        json!(known_categories.len()),
    );

    Ok(output)
}

async fn authenticate_iboss(
    http: &reqwest::Client,
    base_url: &str,
    ctx: &DiscoveryJobContext,
    base_delay: Duration,
) -> Result<HeaderMap> {
    let mut headers = HeaderMap::new();
    headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
    headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
    if let Some(tenant_id) = ctx.get_str(&["tenant_id", "iboss_tenant_id"]) {
        if let Ok(hv) = HeaderValue::from_str(&tenant_id) {
            headers.insert("X-Tenant-ID", hv);
        }
    }

    if let Some(api_token) = ctx.get_str(&["api_token", "api_key", "token"]) {
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(&format!("Bearer {api_token}"))
                .map_err(|e| WorkMgmtError::Auth(format!("invalid iBoss token header: {e}")))?,
        );
        return Ok(headers);
    }

    let username = ctx.get_str(&["username", "user"]).ok_or_else(|| {
        WorkMgmtError::Auth(
            "iBoss discovery requires 'api_token' or ('username' + 'password')".into(),
        )
    })?;
    let password = ctx
        .get_str(&["password"])
        .ok_or_else(|| WorkMgmtError::Auth("iBoss username auth requires 'password'".into()))?;

    let login_paths: Vec<String> = match ctx.get_str(&["auth_endpoint"]) {
        Some(custom) => vec![custom],
        None => vec![
            "/api/v1/auth/login".to_string(),
            "/ibossauth/web/tokens".to_string(),
        ],
    };

    let basic_header = format!(
        "Basic {}",
        STANDARD.encode(format!("{username}:{password}"))
    );
    let login_payload = json!({ "username": username, "password": password });

    for path in &login_paths {
        let full_url = if path.starts_with('/') {
            format!("{base_url}{path}")
        } else {
            format!("{base_url}/{path}")
        };
        let mut req_headers = headers.clone();
        if path.contains("ibossauth") {
            if let Ok(hv) = HeaderValue::from_str(&basic_header) {
                req_headers.insert(AUTHORIZATION, hv);
            }
        }

        let (status, _, json_body, raw_body) = send_with_retry(
            "iboss",
            || {
                http.post(&full_url)
                    .headers(req_headers.clone())
                    .json(&login_payload)
            },
            2,
            base_delay,
        )
        .await?;

        if matches!(status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN) {
            return Err(WorkMgmtError::Auth(format!(
                "iBoss login failed with HTTP {}: {}",
                status.as_u16(),
                truncate_body(&raw_body)
            )));
        }

        if matches!(status, StatusCode::NOT_FOUND | StatusCode::GONE) {
            continue;
        }

        if status.is_success() {
            let token = json_body
                .as_ref()
                .and_then(|v| {
                    v.get("token")
                        .or_else(|| v.get("access_token"))
                        .or_else(|| v.get("jwt"))
                        .or_else(|| v.get("session_token"))
                })
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty());

            if let Some(tok) = token {
                headers.insert(
                    AUTHORIZATION,
                    HeaderValue::from_str(&format!("Bearer {tok}")).map_err(|e| {
                        WorkMgmtError::Auth(format!("invalid iBoss session token: {e}"))
                    })?,
                );
                return Ok(headers);
            }

            return Err(WorkMgmtError::Auth(
                "iBoss login succeeded but response missing 'token'".into(),
            ));
        }

        return Err(WorkMgmtError::ApiHttp {
            platform: "iboss",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    // If no login endpoint exists (404/410), fall back to HTTP Basic Auth directly.
    headers.insert(
        AUTHORIZATION,
        HeaderValue::from_str(&basic_header)
            .map_err(|e| WorkMgmtError::Auth(format!("invalid iBoss Basic header: {e}")))?,
    );
    Ok(headers)
}

async fn fetch_iboss_collection(
    getter: &FallbackGetClient<'_>,
    primary_path: &str,
    fallback_paths: &[&str],
    result_keys: &[&str],
) -> Result<(StatusCode, Vec<Value>, Option<String>)> {
    let (status, json_body, raw_body) =
        match get_with_fallbacks(getter, primary_path, fallback_paths, &[]).await {
            Ok(tuple) => tuple,
            Err(err) => {
                return Ok((
                    StatusCode::BAD_GATEWAY,
                    Vec::new(),
                    Some(format!("{primary_path}: {err}")),
                ));
            }
        };

    if matches!(
        status,
        StatusCode::NOT_FOUND | StatusCode::GONE | StatusCode::NO_CONTENT
    ) {
        return Ok((status, Vec::new(), None));
    }

    if status != StatusCode::OK {
        return Ok((
            status,
            Vec::new(),
            Some(format!(
                "{primary_path} HTTP {}: {}",
                status.as_u16(),
                truncate_body(&raw_body)
            )),
        ));
    }

    let items = json_body
        .as_ref()
        .map(|data| {
            if let Some(arr) = data.as_array() {
                return arr.clone();
            }
            if let Some(obj) = data.as_object() {
                for key in result_keys {
                    if let Some(Value::Array(arr)) = obj.get(*key) {
                        return arr.clone();
                    }
                }
            }
            Vec::new()
        })
        .unwrap_or_default();

    Ok((status, items, None))
}

fn normalize_device(dev: &Value) -> Option<(DiscoveryResource, Vec<DiscoveryDependency>)> {
    let did = dev
        .get("id")
        .or_else(|| dev.get("deviceId"))
        .and_then(value_to_id_string)?;
    let ext_id = format!("iboss:device:{did}");
    let name = dev
        .get("hostname")
        .or_else(|| dev.get("name"))
        .or_else(|| dev.get("deviceName"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Device-{did}"));

    let mut relationships = Vec::new();
    let mut dependencies = Vec::new();

    if let Some(gid) = dev
        .get("groupId")
        .or_else(|| dev.get("group_id"))
        .and_then(value_to_id_string)
    {
        let group_ext = format!("iboss:group:{gid}");
        relationships.push(DiscoveryRelationship {
            target_external_id: group_ext.clone(),
            target_kind: "entity".to_string(),
            edge_type: "member_of".to_string(),
        });
        dependencies.push(DiscoveryDependency {
            source_external_id: ext_id.clone(),
            target_external_id: group_ext,
            dependency_type: "member_of".to_string(),
            description: Some("Device belongs to iBoss group".to_string()),
            metadata: json!({}),
        });
    }

    if let Some(pid) = dev
        .get("policyId")
        .or_else(|| dev.get("policy_id"))
        .and_then(value_to_id_string)
    {
        let pol_ext = format!("iboss:policy:{pid}");
        relationships.push(DiscoveryRelationship {
            target_external_id: pol_ext.clone(),
            target_kind: "entity".to_string(),
            edge_type: "protected_by".to_string(),
        });
        dependencies.push(DiscoveryDependency {
            source_external_id: ext_id.clone(),
            target_external_id: pol_ext,
            dependency_type: "protected_by".to_string(),
            description: Some("Device governed by iBoss policy".to_string()),
            metadata: json!({}),
        });
    }

    let platform = dev
        .get("platform")
        .or_else(|| dev.get("os"))
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();

    let metadata = json!({
        "iboss_device_id": did,
        "hostname": dev.get("hostname").or_else(|| dev.get("deviceName")),
        "platform": dev.get("platform").or_else(|| dev.get("os")),
        "os_version": dev.get("osVersion").or_else(|| dev.get("os_version")),
        "status": dev.get("status"),
        "ip_address": dev.get("ipAddress").or_else(|| dev.get("ip_address")),
        "last_seen": dev.get("lastSeen").or_else(|| dev.get("last_seen")),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "iboss".to_string());
    tags.insert("platform".to_string(), platform);

    let res = format_resource(
        "iboss",
        format!("device-{did}"),
        "iboss_device",
        &name,
        ext_id,
        metadata,
    )
    .with_tags(tags)
    .with_relationships(relationships);
    Some((res, dependencies))
}

fn normalize_group(grp: &Value) -> Option<(DiscoveryResource, Vec<DiscoveryDependency>)> {
    let gid = grp.get("id").and_then(value_to_id_string)?;
    let ext_id = format!("iboss:group:{gid}");
    let gname = grp
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Group-{gid}"));

    let mut relationships = Vec::new();
    let mut dependencies = Vec::new();
    if let Some(policies) = grp.get("policies").and_then(Value::as_array) {
        for p in policies {
            if let Some(pid) =
                value_to_id_string(p).or_else(|| p.get("id").and_then(value_to_id_string))
            {
                let pol_ext = format!("iboss:policy:{pid}");
                relationships.push(DiscoveryRelationship {
                    target_external_id: pol_ext.clone(),
                    target_kind: "entity".to_string(),
                    edge_type: "applies_policy".to_string(),
                });
                dependencies.push(DiscoveryDependency {
                    source_external_id: ext_id.clone(),
                    target_external_id: pol_ext,
                    dependency_type: "applies_policy".to_string(),
                    description: Some("iBoss group enforces security policy".to_string()),
                    metadata: json!({}),
                });
            }
        }
    }

    let metadata = json!({
        "iboss_group_id": gid,
        "group_name": gname,
        "description": grp.get("description"),
        "member_count": grp.get("memberCount").or_else(|| grp.get("member_count")).unwrap_or(&json!(0)),
        "policies": grp.get("policies").unwrap_or(&json!([])),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "iboss".to_string());
    tags.insert("identity_type".to_string(), "group".to_string());

    let res = format_resource(
        "iboss",
        format!("group-{gid}"),
        "iboss_group",
        &format!("Group: {gname}"),
        ext_id,
        metadata,
    )
    .with_tags(tags)
    .with_relationships(relationships);
    Some((res, dependencies))
}

fn normalize_policy(
    pol: &Value,
) -> Option<(DiscoveryResource, Vec<DiscoveryDependency>, Vec<String>)> {
    let pid = pol.get("id").and_then(value_to_id_string)?;
    let ext_id = format!("iboss:policy:{pid}");
    let pname = pol
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Policy-{pid}"));
    let policy_type = pol
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("web-filtering")
        .to_string();

    let mut relationships = Vec::new();
    let mut dependencies = Vec::new();
    let mut cat_names = Vec::new();
    if let Some(cats) = pol.get("categories").and_then(Value::as_array) {
        for c in cats {
            let cname = c
                .as_str()
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .or_else(|| {
                    c.get("name")
                        .and_then(Value::as_str)
                        .map(str::trim)
                        .filter(|s| !s.is_empty())
                        .map(str::to_string)
                });
            if let Some(name) = cname {
                let slug = slugify_category(&name);
                let cat_ext = format!("iboss:category:{slug}");
                relationships.push(DiscoveryRelationship {
                    target_external_id: cat_ext.clone(),
                    target_kind: "entity".to_string(),
                    edge_type: "filters_category".to_string(),
                });
                dependencies.push(DiscoveryDependency {
                    source_external_id: ext_id.clone(),
                    target_external_id: cat_ext,
                    dependency_type: "filters_category".to_string(),
                    description: Some(format!("Policy filters category {name}")),
                    metadata: json!({}),
                });
                cat_names.push(name);
            }
        }
    }

    let metadata = json!({
        "iboss_policy_id": pid,
        "policy_name": pname,
        "policy_type": policy_type,
        "enabled": pol.get("enabled").and_then(Value::as_bool).unwrap_or(false),
        "priority": pol.get("priority"),
        "categories": pol.get("categories").unwrap_or(&json!([])),
        "actions": pol.get("actions").unwrap_or(&json!({})),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "iboss".to_string());
    tags.insert("policy_type".to_string(), policy_type);

    let res = format_resource(
        "iboss",
        format!("policy-{pid}"),
        "iboss_policy",
        &format!("Policy: {pname}"),
        ext_id,
        metadata,
    )
    .with_tags(tags)
    .with_relationships(relationships);
    Some((res, dependencies, cat_names))
}

fn normalize_category(cat: &Value) -> Option<DiscoveryResource> {
    let name = cat
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)?;
    let cid = cat
        .get("id")
        .and_then(value_to_id_string)
        .unwrap_or_else(|| slugify_category(&name));
    let slug = slugify_category(&name);

    let metadata = json!({
        "iboss_category_id": cid,
        "category_name": name,
        "action": cat.get("action"),
        "risk_level": cat.get("riskLevel").or_else(|| cat.get("risk_level")),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "iboss".to_string());
    tags.insert("category".to_string(), name.clone());

    Some(
        format_resource(
            "iboss",
            format!("category-{cid}"),
            "iboss_category",
            &name,
            format!("iboss:category:{slug}"),
            metadata,
        )
        .with_tags(tags),
    )
}

fn normalize_connector(conn: &Value) -> Option<DiscoveryResource> {
    let cid = conn.get("id").and_then(value_to_id_string)?;
    let name = conn
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Connector-{cid}"));

    let metadata = json!({
        "iboss_connector_id": cid,
        "connector_type": conn.get("type"),
        "location": conn.get("location"),
        "ip_address": conn.get("ipAddress").or_else(|| conn.get("ip_address")),
        "status": conn.get("status"),
        "version": conn.get("version"),
        "last_seen": conn.get("lastSeen"),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "iboss".to_string());
    tags.insert("resource_type".to_string(), "gateway".to_string());

    Some(
        format_resource(
            "iboss",
            format!("connector-{cid}"),
            "iboss_connector",
            &name,
            format!("iboss:connector:{cid}"),
            metadata,
        )
        .with_tags(tags),
    )
}

fn normalize_application(app: &Value) -> Option<DiscoveryResource> {
    let aid = app.get("id").and_then(value_to_id_string)?;
    let name = app
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("App-{aid}"));
    let category = app
        .get("category")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();

    let metadata = json!({
        "iboss_app_id": aid,
        "app_name": name,
        "category": category,
        "subcategory": app.get("subcategory"),
        "risk_score": app.get("riskScore").or_else(|| app.get("risk_score")),
        "users_count": app.get("usersCount").unwrap_or(&json!(0)),
        "sessions_count": app.get("sessionsCount").unwrap_or(&json!(0)),
        "bytes_transferred": app.get("bytesTransferred").unwrap_or(&json!(0)),
        "blocked": app.get("blocked").and_then(Value::as_bool).unwrap_or(false),
        "vendor": app.get("vendor"),
    });

    let mut tags = BTreeMap::new();
    tags.insert("provider".to_string(), "iboss".to_string());
    tags.insert("category".to_string(), category);

    Some(
        format_resource(
            "iboss",
            format!("app-{aid}"),
            "iboss_application",
            &format!("App: {name}"),
            format!("iboss:app:{aid}"),
            metadata,
        )
        .with_tags(tags),
    )
}

fn slugify_category(name: &str) -> String {
    name.trim()
        .to_ascii_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect()
}
