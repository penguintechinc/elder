//! OpenProject v3 work-management sync connector (`Basic base64("apikey:" + token)`).

use std::collections::HashMap;

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, AUTHORIZATION, CONTENT_TYPE};
use reqwest::StatusCode;
use serde_json::{json, Map, Value};

use crate::error::{Result, WorkMgmtError};
use crate::http_client::{
    build_http_client, encode_path_segment, ensure_path_suffix, normalize_base_url,
    send_with_retry, truncate_body,
};
use crate::sync_common::{
    make_pushed_mapping, parse_datetime_str, reconcile_issues, value_to_id_string, LocalItem,
    PlatformSyncOutcome, PulledItem, PushedMapping, SyncDirection, SyncJobContext,
};

const MAX_PAGES: usize = 500;

/// Run batch two-way work-package synchronization against OpenProject REST API v3.
pub async fn sync(ctx: &SyncJobContext) -> Result<PlatformSyncOutcome> {
    let api_key = ctx
        .get_str(&["api_key", "api_token", "token"])
        .ok_or_else(|| {
            WorkMgmtError::Auth("OpenProject sync requires 'api_key' or 'api_token'".into())
        })?;
    let raw_base = ctx
        .get_str(&["base_url", "api_base_url", "url"])
        .ok_or_else(|| WorkMgmtError::Config("OpenProject sync requires 'base_url'".into()))?;
    let normalized = normalize_base_url(&raw_base, true)?;
    let api_base = ensure_path_suffix(&normalized, "/api/v3");
    let project_id = ctx.get_str(&["project_id"]);
    let verify_ssl = ctx.get_bool(&["verify_ssl"]).unwrap_or(true);
    let http = build_http_client(verify_ssl)?;

    let basic_val = format!("Basic {}", STANDARD.encode(format!("apikey:{api_key}")));
    let mut headers = HeaderMap::new();
    headers.insert(
        AUTHORIZATION,
        HeaderValue::from_str(&basic_val)
            .map_err(|e| WorkMgmtError::Auth(format!("invalid OpenProject Basic header: {e}")))?,
    );
    headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
    headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));

    let (pulled_raw, lock_versions) = if ctx.direction != SyncDirection::Push {
        pull_work_packages(&http, &headers, &api_base, project_id.as_deref(), ctx).await?
    } else {
        (Vec::new(), HashMap::new())
    };

    let plan = reconcile_issues(ctx, pulled_raw);
    let mut pushed_mappings: Vec<PushedMapping> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    let mut items_failed: usize = 0;

    for item in &plan.to_create {
        match push_create_wp(&http, &headers, &api_base, project_id.as_deref(), ctx, item).await {
            Ok(m) => pushed_mappings.push(m),
            Err(err) => {
                items_failed += 1;
                errors.push(format!("create elder issue {}: {err}", item.elder_id));
            }
        }
    }

    for (item, mapping) in &plan.to_update {
        let lock_ver = item
            .lock_version
            .or_else(|| lock_versions.get(&mapping.external_id).copied())
            .unwrap_or(0);
        match push_update_wp(
            &http,
            &headers,
            &api_base,
            ctx,
            item,
            &mapping.external_id,
            lock_ver,
        )
        .await
        {
            Ok(m) => pushed_mappings.push(m),
            Err(err) => {
                items_failed += 1;
                errors.push(format!(
                    "update OpenProject work package #{}: {err}",
                    mapping.external_id
                ));
            }
        }
    }

    Ok(PlatformSyncOutcome {
        pulled_items: plan.pulled_items,
        pushed_mappings,
        conflicts: plan.conflicts,
        items_failed,
        errors,
        metadata: json!({
            "project_id": project_id,
            "api_base_url": api_base,
        }),
    })
}

async fn pull_work_packages(
    http: &reqwest::Client,
    headers: &HeaderMap,
    api_base: &str,
    project_id: Option<&str>,
    ctx: &SyncJobContext,
) -> Result<(Vec<PulledItem>, HashMap<String, i64>)> {
    let list_url = match project_id {
        Some(pid) => format!(
            "{api_base}/projects/{}/work_packages",
            encode_path_segment(pid)
        ),
        None => format!("{api_base}/work_packages"),
    };

    let page_size: usize = 100;
    let mut pulled = Vec::new();
    let mut lock_versions = HashMap::new();

    for offset in 1..=MAX_PAGES {
        let query = [
            ("offset".to_string(), offset.to_string()),
            ("pageSize".to_string(), page_size.to_string()),
        ];
        let (status, _, json_body, raw_body) = send_with_retry(
            "openproject",
            || http.get(&list_url).headers(headers.clone()).query(&query),
            3,
            ctx.retry_base_delay,
        )
        .await?;

        if status != StatusCode::OK {
            return Err(WorkMgmtError::ApiHttp {
                platform: "openproject",
                status: status.as_u16(),
                body: truncate_body(&raw_body),
            });
        }

        let body = json_body.unwrap_or_else(|| json!({}));
        let elements: &[Value] = body
            .get("_embedded")
            .and_then(|e| e.get("elements"))
            .and_then(Value::as_array)
            .or_else(|| body.get("elements").and_then(Value::as_array))
            .or_else(|| body.as_array())
            .map(Vec::as_slice)
            .unwrap_or(&[]);

        for wp in elements {
            if let Some((norm, lock_ver)) = normalize_work_package(wp) {
                if let Some(lv) = lock_ver {
                    lock_versions.insert(norm.external_id.clone(), lv);
                }
                pulled.push(norm);
            }
        }

        let has_next_link = body
            .get("_links")
            .and_then(|l| l.get("next"))
            .and_then(|n| n.get("href"))
            .and_then(Value::as_str)
            .is_some();
        let total = body
            .get("total")
            .and_then(Value::as_u64)
            .map(|t| t as usize);

        let should_continue = if elements.is_empty() {
            false
        } else if has_next_link {
            true
        } else if let Some(t) = total {
            pulled.len() < t
        } else {
            elements.len() >= page_size
        };
        if !should_continue {
            break;
        }
    }

    Ok((pulled, lock_versions))
}

fn normalize_work_package(wp: &Value) -> Option<(PulledItem, Option<i64>)> {
    let ext_id = wp.get("id").and_then(value_to_id_string)?;
    let title = wp
        .get("subject")
        .or_else(|| wp.get("title"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();

    let description = match wp.get("description") {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Object(obj)) => obj
            .get("raw")
            .or_else(|| obj.get("html"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        _ => String::new(),
    };

    let links = wp.get("_links");
    let status_title = links
        .and_then(|l| l.get("status"))
        .and_then(|s| s.get("title"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_lowercase();
    let status = if ["closed", "done", "rejected", "resolved"]
        .iter()
        .any(|k| status_title.contains(k))
    {
        "CLOSED".to_string()
    } else if status_title.contains("progress") || status_title.contains("developing") {
        "IN_PROGRESS".to_string()
    } else {
        "OPEN".to_string()
    };

    let priority_title = links
        .and_then(|l| l.get("priority"))
        .and_then(|p| p.get("title"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_lowercase();
    let priority = if priority_title.contains("immediate") || priority_title.contains("critical") {
        "CRITICAL".to_string()
    } else if priority_title.contains("high") {
        "HIGH".to_string()
    } else if priority_title.contains("low") {
        "LOW".to_string()
    } else {
        "MEDIUM".to_string()
    };

    let type_title = links
        .and_then(|l| l.get("type"))
        .and_then(|t| t.get("title"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_lowercase();
    let issue_type = if type_title.contains("bug") {
        "BUG".to_string()
    } else if type_title.contains("feature") || type_title.contains("epic") {
        "FEATURE".to_string()
    } else if type_title.contains("task") {
        "OPERATIONS".to_string()
    } else {
        "OTHER".to_string()
    };

    let mut assignees = Vec::new();
    if let Some(assignee) = links
        .and_then(|l| l.get("assignee"))
        .and_then(|a| a.get("title"))
        .and_then(Value::as_str)
    {
        assignees.push(assignee.to_string());
    }

    let due_date = wp
        .get("dueDate")
        .and_then(Value::as_str)
        .map(str::to_string);
    let external_updated_at = wp
        .get("updatedAt")
        .and_then(Value::as_str)
        .map(str::to_string);
    let external_updated_dt = external_updated_at.as_deref().and_then(parse_datetime_str);
    let external_url = links
        .and_then(|l| l.get("self"))
        .and_then(|s| s.get("href"))
        .and_then(Value::as_str)
        .map(str::to_string);
    let lock_version = wp.get("lockVersion").and_then(Value::as_i64);

    let item = PulledItem {
        elder_type: "issue".to_string(),
        elder_id: None,
        external_id: ext_id.clone(),
        external_key: Some(format!("WP-{ext_id}")),
        operation: "create".to_string(),
        title,
        description,
        status,
        priority,
        issue_type,
        labels: Vec::new(),
        assignees,
        due_date,
        closed_at: None,
        external_updated_at: external_updated_at.clone(),
        updated_at: external_updated_at,
        external_url,
        metadata: json!({
            "lock_version": lock_version,
        }),
        external_updated_dt,
        deleted: false,
    };
    Some((item, lock_version))
}

async fn push_create_wp(
    http: &reqwest::Client,
    headers: &HeaderMap,
    api_base: &str,
    project_id: Option<&str>,
    ctx: &SyncJobContext,
    item: &LocalItem,
) -> Result<PushedMapping> {
    let url = match project_id {
        Some(pid) => format!(
            "{api_base}/projects/{}/work_packages",
            encode_path_segment(pid)
        ),
        None => format!("{api_base}/work_packages"),
    };

    let mut body = Map::new();
    body.insert("subject".to_string(), json!(item.title));
    body.insert(
        "description".to_string(),
        json!({ "format": "markdown", "raw": item.description }),
    );
    if let Some(due) = &item.due_date {
        body.insert("dueDate".to_string(), json!(due));
    }
    let body_val = Value::Object(body);

    let (status, _, json_body, raw_body) = send_with_retry(
        "openproject",
        || http.post(&url).headers(headers.clone()).json(&body_val),
        3,
        ctx.retry_base_delay,
    )
    .await?;

    if !matches!(status, StatusCode::OK | StatusCode::CREATED) {
        return Err(WorkMgmtError::ApiHttp {
            platform: "openproject",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let created = json_body.ok_or_else(|| WorkMgmtError::ApiHttp {
        platform: "openproject",
        status: status.as_u16(),
        body: "OpenProject create work_package returned empty body".into(),
    })?;

    let ext_id = created
        .get("id")
        .and_then(value_to_id_string)
        .ok_or_else(|| WorkMgmtError::ApiHttp {
            platform: "openproject",
            status: status.as_u16(),
            body: "OpenProject create work_package missing 'id'".into(),
        })?;
    let ext_updated = created
        .get("updatedAt")
        .and_then(Value::as_str)
        .map(str::to_string);

    Ok(make_pushed_mapping(
        "openproject",
        ctx.sync_config_id_or_zero(),
        item,
        ext_id,
        "create",
        ext_updated,
    ))
}

async fn push_update_wp(
    http: &reqwest::Client,
    headers: &HeaderMap,
    api_base: &str,
    ctx: &SyncJobContext,
    item: &LocalItem,
    external_id: &str,
    lock_version: i64,
) -> Result<PushedMapping> {
    let url = format!(
        "{api_base}/work_packages/{}",
        encode_path_segment(external_id)
    );
    let mut body = Map::new();
    body.insert("lockVersion".to_string(), json!(lock_version));
    body.insert("subject".to_string(), json!(item.title));
    body.insert(
        "description".to_string(),
        json!({ "format": "markdown", "raw": item.description }),
    );
    if let Some(due) = &item.due_date {
        body.insert("dueDate".to_string(), json!(due));
    }
    let body_val = Value::Object(body);

    let (status, _, json_body, raw_body) = send_with_retry(
        "openproject",
        || http.patch(&url).headers(headers.clone()).json(&body_val),
        3,
        ctx.retry_base_delay,
    )
    .await?;

    if status != StatusCode::OK {
        return Err(WorkMgmtError::ApiHttp {
            platform: "openproject",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let ext_updated = json_body
        .as_ref()
        .and_then(|v| v.get("updatedAt"))
        .and_then(Value::as_str)
        .map(str::to_string);

    Ok(make_pushed_mapping(
        "openproject",
        ctx.sync_config_id_or_zero(),
        item,
        external_id.to_string(),
        "update",
        ext_updated,
    ))
}
