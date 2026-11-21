//! GitLab v4 work-management sync connector (`PRIVATE-TOKEN`, paginated issues, scoped labels).

use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, CONTENT_TYPE};
use reqwest::StatusCode;
use serde_json::{json, Map, Value};

use crate::error::{Result, WorkMgmtError};
use crate::http_client::{
    build_http_client, encode_path_segment, ensure_path_suffix, normalize_base_url,
    send_with_retry, truncate_body,
};
use crate::sync_common::{
    extract_label_names, labels_to_issue_type, labels_to_priority, make_pushed_mapping,
    parse_datetime_str, reconcile_issues, value_to_id_string, LocalItem, PlatformSyncOutcome,
    PulledItem, PushedMapping, SyncDirection, SyncJobContext,
};

const DEFAULT_GITLAB_BASE: &str = "https://gitlab.com";
const MAX_PAGES: usize = 500;

/// Run batch two-way issue synchronization against GitLab REST API v4.
pub async fn sync(ctx: &SyncJobContext) -> Result<PlatformSyncOutcome> {
    let api_token = ctx
        .get_str(&["api_token", "private_token", "token"])
        .ok_or_else(|| {
            WorkMgmtError::Auth("GitLab sync requires 'api_token' or 'private_token'".into())
        })?;
    let project_id = ctx
        .get_str(&["project_id"])
        .ok_or_else(|| WorkMgmtError::Config("GitLab sync requires 'project_id'".into()))?;
    let raw_base = ctx
        .get_str(&["api_base_url", "base_url", "gitlab_url"])
        .unwrap_or_else(|| DEFAULT_GITLAB_BASE.to_string());
    let normalized = normalize_base_url(&raw_base, true)?;
    let api_base = ensure_path_suffix(&normalized, "/api/v4");
    let verify_ssl = ctx.get_bool(&["verify_ssl"]).unwrap_or(true);
    let http = build_http_client(verify_ssl)?;

    let mut headers = HeaderMap::new();
    headers.insert(
        "PRIVATE-TOKEN",
        HeaderValue::from_str(&api_token)
            .map_err(|e| WorkMgmtError::Auth(format!("invalid GitLab PRIVATE-TOKEN: {e}")))?,
    );
    headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
    headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));

    let project_prefix = format!("{api_base}/projects/{}", encode_path_segment(&project_id));

    let pulled_raw = if ctx.direction != SyncDirection::Push {
        pull_issues(&http, &headers, &project_prefix, ctx).await?
    } else {
        Vec::new()
    };

    let plan = reconcile_issues(ctx, pulled_raw);
    let mut pushed_mappings: Vec<PushedMapping> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    let mut items_failed: usize = 0;

    for item in &plan.to_create {
        match push_create_issue(&http, &headers, &project_prefix, ctx, item).await {
            Ok(m) => pushed_mappings.push(m),
            Err(err) => {
                items_failed += 1;
                errors.push(format!("create elder issue {}: {err}", item.elder_id));
            }
        }
    }

    for (item, mapping) in &plan.to_update {
        match push_update_issue(
            &http,
            &headers,
            &project_prefix,
            ctx,
            item,
            &mapping.external_id,
        )
        .await
        {
            Ok(m) => pushed_mappings.push(m),
            Err(err) => {
                items_failed += 1;
                errors.push(format!(
                    "update GitLab issue #{}: {err}",
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

async fn pull_issues(
    http: &reqwest::Client,
    headers: &HeaderMap,
    project_prefix: &str,
    ctx: &SyncJobContext,
) -> Result<Vec<PulledItem>> {
    let issues_url = format!("{project_prefix}/issues");
    let mut page: usize = 1;
    let mut pulled = Vec::new();

    for _ in 0..MAX_PAGES {
        let mut query = vec![
            ("per_page".to_string(), "100".to_string()),
            ("page".to_string(), page.to_string()),
        ];
        if let Some(since) = ctx.since {
            query.push(("updated_after".to_string(), since.to_rfc3339()));
        }

        let (status, resp_headers, json_body, raw_body) = send_with_retry(
            "gitlab",
            || http.get(&issues_url).headers(headers.clone()).query(&query),
            3,
            ctx.retry_base_delay,
        )
        .await?;

        if status != StatusCode::OK {
            return Err(WorkMgmtError::ApiHttp {
                platform: "gitlab",
                status: status.as_u16(),
                body: truncate_body(&raw_body),
            });
        }

        let items = json_body
            .as_ref()
            .and_then(Value::as_array)
            .ok_or_else(|| WorkMgmtError::ApiHttp {
                platform: "gitlab",
                status: status.as_u16(),
                body: "expected JSON array from GitLab /issues".into(),
            })?;

        for issue in items {
            if let Some(norm) = normalize_gitlab_issue(issue) {
                pulled.push(norm);
            }
        }

        if let Some(header_val) = resp_headers
            .get("x-next-page")
            .and_then(|v| v.to_str().ok())
        {
            let trimmed = header_val.trim();
            if trimmed.is_empty() {
                break;
            }
            match trimmed.parse::<usize>() {
                Ok(np) if np > page => page = np,
                _ => break,
            }
        } else if items.len() < 100 {
            break;
        } else {
            page += 1;
        }
    }

    Ok(pulled)
}

fn normalize_gitlab_issue(issue: &Value) -> Option<PulledItem> {
    let ext_id = issue
        .get("iid")
        .and_then(value_to_id_string)
        .or_else(|| issue.get("id").and_then(value_to_id_string))?;
    let title = issue
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let description = issue
        .get("description")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let labels = extract_label_names(issue.get("labels"));

    let state = issue
        .get("state")
        .and_then(Value::as_str)
        .unwrap_or("opened");
    let status = if state.eq_ignore_ascii_case("closed") {
        "CLOSED".to_string()
    } else if labels.iter().any(|l| {
        let lower = l.to_ascii_lowercase();
        lower.contains("in-progress") || lower.contains("in_progress") || lower.contains("doing")
    }) {
        "IN_PROGRESS".to_string()
    } else {
        "OPEN".to_string()
    };

    let priority = labels_to_priority(&labels);
    let issue_type = labels_to_issue_type(&labels);

    let mut assignees = Vec::new();
    if let Some(arr) = issue.get("assignees").and_then(Value::as_array) {
        for user in arr {
            if let Some(username) = user.get("username").and_then(Value::as_str) {
                assignees.push(username.to_string());
            }
        }
    }

    let due_date = issue
        .get("due_date")
        .and_then(Value::as_str)
        .map(str::to_string);
    let closed_at = issue
        .get("closed_at")
        .and_then(Value::as_str)
        .map(str::to_string);
    let external_updated_at = issue
        .get("updated_at")
        .and_then(Value::as_str)
        .map(str::to_string);
    let external_updated_dt = external_updated_at.as_deref().and_then(parse_datetime_str);
    let external_url = issue
        .get("web_url")
        .and_then(Value::as_str)
        .map(str::to_string);

    Some(PulledItem {
        elder_type: "issue".to_string(),
        elder_id: None,
        external_id: ext_id.clone(),
        external_key: Some(format!("#{ext_id}")),
        operation: "create".to_string(),
        title,
        description,
        status,
        priority,
        issue_type,
        labels,
        assignees,
        due_date,
        closed_at,
        external_updated_at: external_updated_at.clone(),
        updated_at: external_updated_at,
        external_url,
        metadata: json!({
            "gitlab_id": issue.get("id"),
            "weight": issue.get("weight"),
        }),
        external_updated_dt,
        deleted: false,
    })
}

fn build_gitlab_issue_payload(item: &LocalItem, include_state_event: bool) -> Value {
    let mut map = Map::new();
    map.insert("title".to_string(), json!(item.title));
    map.insert("description".to_string(), json!(item.description));
    if !item.labels.is_empty() {
        map.insert("labels".to_string(), json!(item.labels.join(",")));
    }
    if let Some(due) = &item.due_date {
        map.insert("due_date".to_string(), json!(due));
    }
    if let Some(weight) = item.weight {
        map.insert("weight".to_string(), json!(weight));
    }
    if include_state_event {
        let event = if matches!(item.status.as_str(), "CLOSED" | "RESOLVED") {
            "close"
        } else {
            "reopen"
        };
        map.insert("state_event".to_string(), json!(event));
    }
    Value::Object(map)
}

async fn push_create_issue(
    http: &reqwest::Client,
    headers: &HeaderMap,
    project_prefix: &str,
    ctx: &SyncJobContext,
    item: &LocalItem,
) -> Result<PushedMapping> {
    let url = format!("{project_prefix}/issues");
    let payload = build_gitlab_issue_payload(item, false);
    let (status, _, json_body, raw_body) = send_with_retry(
        "gitlab",
        || http.post(&url).headers(headers.clone()).json(&payload),
        3,
        ctx.retry_base_delay,
    )
    .await?;

    if !matches!(status, StatusCode::OK | StatusCode::CREATED) {
        return Err(WorkMgmtError::ApiHttp {
            platform: "gitlab",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let created = json_body.ok_or_else(|| WorkMgmtError::ApiHttp {
        platform: "gitlab",
        status: status.as_u16(),
        body: "GitLab create issue returned empty body".into(),
    })?;

    let ext_id = created
        .get("iid")
        .and_then(value_to_id_string)
        .or_else(|| created.get("id").and_then(value_to_id_string))
        .ok_or_else(|| WorkMgmtError::ApiHttp {
            platform: "gitlab",
            status: status.as_u16(),
            body: "GitLab create issue response missing 'iid'".into(),
        })?;

    let mut ext_updated = created
        .get("updated_at")
        .and_then(Value::as_str)
        .map(str::to_string);

    if matches!(item.status.as_str(), "CLOSED" | "RESOLVED") {
        let put_url = format!("{project_prefix}/issues/{}", encode_path_segment(&ext_id));
        let close_body = json!({ "state_event": "close" });
        let (p_status, _, p_json, _) = send_with_retry(
            "gitlab",
            || {
                http.put(&put_url)
                    .headers(headers.clone())
                    .json(&close_body)
            },
            3,
            ctx.retry_base_delay,
        )
        .await?;
        if p_status.is_success() {
            if let Some(upd) = p_json
                .as_ref()
                .and_then(|v| v.get("updated_at"))
                .and_then(Value::as_str)
            {
                ext_updated = Some(upd.to_string());
            }
        }
    }

    Ok(make_pushed_mapping(
        "gitlab",
        ctx.sync_config_id_or_zero(),
        item,
        ext_id,
        "create",
        ext_updated,
    ))
}

async fn push_update_issue(
    http: &reqwest::Client,
    headers: &HeaderMap,
    project_prefix: &str,
    ctx: &SyncJobContext,
    item: &LocalItem,
    external_id: &str,
) -> Result<PushedMapping> {
    let url = format!(
        "{project_prefix}/issues/{}",
        encode_path_segment(external_id)
    );
    let payload = build_gitlab_issue_payload(item, true);
    let (status, _, json_body, raw_body) = send_with_retry(
        "gitlab",
        || http.put(&url).headers(headers.clone()).json(&payload),
        3,
        ctx.retry_base_delay,
    )
    .await?;

    if status != StatusCode::OK {
        return Err(WorkMgmtError::ApiHttp {
            platform: "gitlab",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let ext_updated = json_body
        .as_ref()
        .and_then(|v| v.get("updated_at"))
        .and_then(Value::as_str)
        .map(str::to_string);

    Ok(make_pushed_mapping(
        "gitlab",
        ctx.sync_config_id_or_zero(),
        item,
        external_id.to_string(),
        "update",
        ext_updated,
    ))
}
