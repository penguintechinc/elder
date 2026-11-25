//! Jira Cloud REST v3 work-management sync connector (JQL search + legacy fallback, ADF conversion).

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, AUTHORIZATION, CONTENT_TYPE};
use reqwest::StatusCode;
use serde_json::{json, Map, Value};

use crate::error::{Result, WorkMgmtError};
use crate::http_client::{
    build_http_client, encode_path_segment, normalize_base_url, send_with_retry, strip_path_suffix,
    truncate_body,
};
use crate::sync_common::{
    extract_label_names, make_pushed_mapping, parse_datetime_str, reconcile_issues, LocalItem,
    PlatformSyncOutcome, PulledItem, PushedMapping, SyncDirection, SyncJobContext,
};

const MAX_PAGES: usize = 500;

/// Convert plain text into an Atlassian Document Format (ADF) v1 document.
pub fn text_to_adf(text: &str) -> Value {
    if text.is_empty() {
        return json!({
            "type": "doc",
            "version": 1,
            "content": []
        });
    }

    let paragraphs: Vec<Value> = text
        .split('\n')
        .map(|line| {
            if line.is_empty() {
                json!({ "type": "paragraph", "content": [] })
            } else {
                json!({
                    "type": "paragraph",
                    "content": [{ "type": "text", "text": line }]
                })
            }
        })
        .collect();

    json!({
        "type": "doc",
        "version": 1,
        "content": paragraphs
    })
}

/// Extract plain text cleanly from an Atlassian Document Format (ADF) payload.
pub fn adf_to_text(adf: Option<&Value>) -> String {
    let Some(val) = adf else {
        return String::new();
    };
    match val {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Object(map) => {
            if map
                .get("content")
                .and_then(Value::as_array)
                .is_none_or(Vec::is_empty)
            {
                return String::new();
            }
            extract_adf_node(val).trim_matches('\n').to_string()
        }
        other => other.to_string(),
    }
}

fn extract_adf_node(node: &Value) -> String {
    match node {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Object(map) => {
            let node_type = map.get("type").and_then(Value::as_str).unwrap_or("");
            match node_type {
                "text" => map
                    .get("text")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                "hardBreak" => "\n".to_string(),
                "mention" | "emoji" => map
                    .get("attrs")
                    .and_then(|a| a.get("text"))
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                "inlineCard" => map
                    .get("attrs")
                    .and_then(|a| a.get("url"))
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                _ => {
                    let children = map
                        .get("content")
                        .and_then(Value::as_array)
                        .map(Vec::as_slice)
                        .unwrap_or(&[]);
                    let child_texts: Vec<String> = children.iter().map(extract_adf_node).collect();
                    match node_type {
                        "doc" | "bulletList" | "orderedList" | "table" | "blockquote" => {
                            child_texts.join("\n")
                        }
                        "tableRow" => child_texts
                            .into_iter()
                            .filter(|s| !s.is_empty())
                            .collect::<Vec<_>>()
                            .join(" | "),
                        _ => child_texts.concat(),
                    }
                }
            }
        }
        _ => String::new(),
    }
}

fn jira_headers(ctx: &SyncJobContext) -> Result<HeaderMap> {
    let email = ctx.get_str(&["email", "username"]);
    let api_token = ctx.get_str(&["api_token", "bearer_token", "access_token", "token"]);
    let auth_mode = ctx.get_str(&["auth_mode"]).map(|s| s.to_ascii_lowercase());

    let auth_header = match auth_mode.as_deref() {
        Some("bearer") => {
            let token = api_token.ok_or_else(|| {
                WorkMgmtError::Auth("Jira Bearer auth requires 'api_token' or 'token'".into())
            })?;
            format!("Bearer {token}")
        }
        Some("basic") => {
            let user_email = email
                .ok_or_else(|| WorkMgmtError::Auth("Jira Basic auth requires 'email'".into()))?;
            let token = api_token.ok_or_else(|| {
                WorkMgmtError::Auth("Jira Basic auth requires 'api_token'".into())
            })?;
            format!("Basic {}", STANDARD.encode(format!("{user_email}:{token}")))
        }
        None => match (email, api_token) {
            (Some(user_email), Some(token)) => {
                format!("Basic {}", STANDARD.encode(format!("{user_email}:{token}")))
            }
            (None, Some(token)) => format!("Bearer {token}"),
            _ => {
                return Err(WorkMgmtError::Auth(
                    "Jira auth requires ('email' + 'api_token') or Bearer 'token'".into(),
                ));
            }
        },
        Some(other) => {
            return Err(WorkMgmtError::Config(format!(
                "unsupported Jira auth_mode {other:?}"
            )));
        }
    };

    let mut headers = HeaderMap::new();
    headers.insert(
        AUTHORIZATION,
        HeaderValue::from_str(&auth_header)
            .map_err(|e| WorkMgmtError::Auth(format!("invalid Jira Authorization header: {e}")))?,
    );
    headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
    headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
    Ok(headers)
}

/// Run batch two-way issue synchronization against Jira Cloud REST API v3.
pub async fn sync(ctx: &SyncJobContext) -> Result<PlatformSyncOutcome> {
    let raw_url = ctx
        .get_str(&["jira_url", "api_base_url", "base_url", "url"])
        .ok_or_else(|| {
            WorkMgmtError::Config("Jira sync requires 'jira_url' or 'base_url'".into())
        })?;
    let normalized = normalize_base_url(&raw_url, true)?;
    let root_url = strip_path_suffix(&normalized, "/rest/api/3");
    let api_base = format!("{root_url}/rest/api/3");

    let project_key = ctx.get_str(&["project_key"]);
    let custom_jql = ctx.get_str(&["jql"]);
    if project_key.is_none() && custom_jql.is_none() {
        return Err(WorkMgmtError::Config(
            "Jira sync requires 'project_key' (or 'jql')".into(),
        ));
    }

    let headers = jira_headers(ctx)?;
    let verify_ssl = ctx.get_bool(&["verify_ssl"]).unwrap_or(true);
    let http = build_http_client(verify_ssl)?;

    let pulled_raw = if ctx.direction != SyncDirection::Push {
        pull_issues(
            &http,
            &headers,
            &api_base,
            project_key.as_deref(),
            custom_jql.as_deref(),
            ctx,
        )
        .await?
    } else {
        Vec::new()
    };

    let plan = reconcile_issues(ctx, pulled_raw);
    let mut pushed_mappings: Vec<PushedMapping> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    let mut items_failed: usize = 0;

    for item in &plan.to_create {
        let Some(proj_key) = project_key.as_deref() else {
            items_failed += 1;
            errors.push(format!(
                "create elder issue {}: Jira 'project_key' is required",
                item.elder_id
            ));
            continue;
        };
        match push_create_issue(&http, &headers, &api_base, proj_key, ctx, item).await {
            Ok(mapping) => pushed_mappings.push(mapping),
            Err(err) => {
                items_failed += 1;
                errors.push(format!("create elder issue {}: {err}", item.elder_id));
            }
        }
    }

    for (item, mapping) in &plan.to_update {
        match push_update_issue(&http, &headers, &api_base, ctx, item, &mapping.external_id).await {
            Ok(upd) => pushed_mappings.push(upd),
            Err(err) => {
                items_failed += 1;
                errors.push(format!("update Jira issue {}: {err}", mapping.external_id));
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
            "project_key": project_key,
            "api_base_url": api_base,
        }),
    })
}

async fn pull_issues(
    http: &reqwest::Client,
    headers: &HeaderMap,
    api_base: &str,
    project_key: Option<&str>,
    custom_jql: Option<&str>,
    ctx: &SyncJobContext,
) -> Result<Vec<PulledItem>> {
    let mut jql = match custom_jql {
        Some(q) => q.to_string(),
        None => {
            let key = project_key.unwrap_or_default();
            let mut base_q = format!("project = \"{key}\"");
            if let Some(since) = ctx.since {
                base_q.push_str(&format!(
                    " AND updated >= \"{}\"",
                    since.format("%Y-%m-%d %H:%M")
                ));
            }
            base_q.push_str(" ORDER BY updated DESC");
            base_q
        }
    };
    jql = jql.trim().to_string();

    let fields = [
        "summary",
        "description",
        "status",
        "priority",
        "issuetype",
        "labels",
        "fixVersions",
        "assignee",
        "updated",
        "duedate",
    ];

    let search_jql_url = format!("{api_base}/search/jql");
    let mut next_page_token: Option<String> = None;
    let mut pulled: Vec<PulledItem> = Vec::new();

    for page in 0..MAX_PAGES {
        let mut body = Map::new();
        body.insert("jql".to_string(), json!(jql));
        body.insert("maxResults".to_string(), json!(100));
        body.insert("fields".to_string(), json!(fields));
        if let Some(token) = &next_page_token {
            body.insert("nextPageToken".to_string(), json!(token));
        }
        let body_val = Value::Object(body);

        let (status, _, json_body, raw_body) = send_with_retry(
            "jira",
            || {
                http.post(&search_jql_url)
                    .headers(headers.clone())
                    .json(&body_val)
            },
            3,
            ctx.retry_base_delay,
        )
        .await?;

        if page == 0 && matches!(status, StatusCode::NOT_FOUND | StatusCode::GONE) {
            return pull_issues_legacy_get(http, headers, api_base, &jql, &fields, ctx).await;
        }

        if status != StatusCode::OK {
            return Err(WorkMgmtError::ApiHttp {
                platform: "jira",
                status: status.as_u16(),
                body: truncate_body(&raw_body),
            });
        }

        let data = json_body.unwrap_or_else(|| json!({}));
        let issues = data
            .get("issues")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or(&[]);

        for issue in issues {
            if let Some(norm) = normalize_jira_issue(issue) {
                pulled.push(norm);
            }
        }

        let next_tok = data
            .get("nextPageToken")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
        let is_last = data.get("isLast").and_then(Value::as_bool);

        if is_last == Some(true) || next_tok.is_none() || issues.is_empty() {
            break;
        }
        next_page_token = next_tok;
    }

    Ok(pulled)
}

async fn pull_issues_legacy_get(
    http: &reqwest::Client,
    headers: &HeaderMap,
    api_base: &str,
    jql: &str,
    fields: &[&str],
    ctx: &SyncJobContext,
) -> Result<Vec<PulledItem>> {
    let legacy_url = format!("{api_base}/search");
    let fields_csv = fields.join(",");
    let mut start_at: usize = 0;
    let mut pulled = Vec::new();

    for _page in 0..MAX_PAGES {
        let query = [
            ("jql".to_string(), jql.to_string()),
            ("startAt".to_string(), start_at.to_string()),
            ("maxResults".to_string(), "100".to_string()),
            ("fields".to_string(), fields_csv.clone()),
        ];

        let (status, _, json_body, raw_body) = send_with_retry(
            "jira",
            || http.get(&legacy_url).headers(headers.clone()).query(&query),
            3,
            ctx.retry_base_delay,
        )
        .await?;

        if status != StatusCode::OK {
            return Err(WorkMgmtError::ApiHttp {
                platform: "jira",
                status: status.as_u16(),
                body: truncate_body(&raw_body),
            });
        }

        let data = json_body.unwrap_or_else(|| json!({}));
        let issues = data
            .get("issues")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or(&[]);

        for issue in issues {
            if let Some(norm) = normalize_jira_issue(issue) {
                pulled.push(norm);
            }
        }

        if issues.is_empty() {
            break;
        }
        start_at += issues.len();
        let total = data
            .get("total")
            .and_then(Value::as_u64)
            .map(|t| t as usize);
        if total.is_some_and(|t| start_at >= t) || issues.len() < 100 {
            break;
        }
    }

    Ok(pulled)
}

fn normalize_jira_issue(issue: &Value) -> Option<PulledItem> {
    let key = issue
        .get("key")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())?
        .to_string();
    let fields = issue.get("fields").unwrap_or(&Value::Null);

    let title = fields
        .get("summary")
        .and_then(Value::as_str)
        .unwrap_or(key.as_str())
        .to_string();
    let description = adf_to_text(fields.get("description"));
    let status = map_jira_status(fields.get("status"));
    let priority = map_jira_priority(fields.get("priority"));
    let issue_type = map_jira_issue_type(fields.get("issuetype"));
    let labels = extract_label_names(fields.get("labels"));

    let mut assignees = Vec::new();
    if let Some(assignee) = fields.get("assignee").and_then(Value::as_object) {
        if let Some(name) = assignee
            .get("displayName")
            .or_else(|| assignee.get("accountId"))
            .and_then(Value::as_str)
        {
            assignees.push(name.to_string());
        }
    }

    let due_date = fields
        .get("duedate")
        .and_then(Value::as_str)
        .map(str::to_string);
    let external_updated_at = fields
        .get("updated")
        .and_then(Value::as_str)
        .map(str::to_string);
    let external_updated_dt = external_updated_at.as_deref().and_then(parse_datetime_str);
    let external_url = issue
        .get("self")
        .and_then(Value::as_str)
        .map(str::to_string);

    Some(PulledItem {
        elder_type: "issue".to_string(),
        elder_id: None,
        external_id: key.clone(),
        external_key: Some(key),
        operation: "create".to_string(),
        title,
        description,
        status: status.to_string(),
        priority: priority.to_string(),
        issue_type: issue_type.to_string(),
        labels,
        assignees,
        due_date,
        closed_at: None,
        external_updated_at: external_updated_at.clone(),
        updated_at: external_updated_at,
        external_url,
        metadata: json!({
            "jira_id": issue.get("id"),
            "status_raw": fields.get("status").and_then(|s| s.get("name")),
        }),
        external_updated_dt,
        deleted: false,
    })
}

fn map_jira_status(status_obj: Option<&Value>) -> &'static str {
    let Some(obj) = status_obj.and_then(Value::as_object) else {
        return "OPEN";
    };
    let cat_key = obj
        .get("statusCategory")
        .and_then(|sc| sc.get("key"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_lowercase();
    match cat_key.as_str() {
        "done" => return "CLOSED",
        "indeterminate" => return "IN_PROGRESS",
        "new" => return "OPEN",
        _ => {}
    }

    let name = obj
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_ascii_lowercase();
    if ["done", "closed", "resolved", "complete"]
        .iter()
        .any(|tok| name.contains(tok))
    {
        return "CLOSED";
    }
    if ["progress", "review", "doing"]
        .iter()
        .any(|tok| name.contains(tok))
    {
        return "IN_PROGRESS";
    }
    "OPEN"
}

fn map_jira_priority(priority_obj: Option<&Value>) -> &'static str {
    let Some(name) = priority_obj
        .and_then(|p| p.get("name"))
        .and_then(Value::as_str)
        .map(str::to_ascii_lowercase)
    else {
        return "MEDIUM";
    };

    match name.as_str() {
        "highest" | "blocker" | "critical" => "CRITICAL",
        "high" => "HIGH",
        "low" | "lowest" | "minor" | "trivial" => "LOW",
        _ => "MEDIUM",
    }
}

fn map_jira_issue_type(issuetype_obj: Option<&Value>) -> &'static str {
    let Some(name) = issuetype_obj
        .and_then(|it| it.get("name"))
        .and_then(Value::as_str)
        .map(str::to_ascii_lowercase)
    else {
        return "OTHER";
    };

    if name.contains("bug") || name.contains("defect") {
        "BUG"
    } else if ["story", "feature", "epic", "improvement"]
        .iter()
        .any(|t| name.contains(t))
    {
        "FEATURE"
    } else if name.contains("security") || name.contains("vuln") {
        "SECURITY"
    } else if ["incident", "problem", "operations", "task"]
        .iter()
        .any(|t| name.contains(t))
    {
        "OPERATIONS"
    } else if name.contains("support") || name.contains("service") {
        "SUPPORT"
    } else if name.contains("config") {
        "CONFIG"
    } else if name.contains("architecture") {
        "ARCHITECTURE"
    } else if name.contains("process") {
        "PROCESS"
    } else if name.contains("approval") {
        "APPROVAL"
    } else if name.contains("code") {
        "CODE"
    } else {
        "OTHER"
    }
}

fn map_elder_priority_to_jira(priority: &str) -> &'static str {
    match priority.to_ascii_uppercase().as_str() {
        "CRITICAL" | "URGENT" => "Highest",
        "HIGH" => "High",
        "LOW" => "Low",
        _ => "Medium",
    }
}

fn map_elder_type_to_jira(issue_type: &str) -> &'static str {
    match issue_type.to_ascii_uppercase().as_str() {
        "BUG" | "SECURITY" => "Bug",
        "FEATURE" => "Story",
        _ => "Task",
    }
}

fn build_jira_fields(item: &LocalItem, project_key: Option<&str>) -> Value {
    let mut fields = Map::new();
    fields.insert("summary".to_string(), json!(item.title));
    fields.insert("description".to_string(), text_to_adf(&item.description));
    fields.insert(
        "priority".to_string(),
        json!({ "name": map_elder_priority_to_jira(&item.priority) }),
    );
    if !item.labels.is_empty() {
        fields.insert("labels".to_string(), json!(item.labels));
    }
    if let Some(proj) = project_key {
        fields.insert("project".to_string(), json!({ "key": proj }));
        fields.insert(
            "issuetype".to_string(),
            json!({ "name": map_elder_type_to_jira(&item.issue_type) }),
        );
    }
    Value::Object(fields)
}

async fn push_create_issue(
    http: &reqwest::Client,
    headers: &HeaderMap,
    api_base: &str,
    project_key: &str,
    ctx: &SyncJobContext,
    item: &LocalItem,
) -> Result<PushedMapping> {
    let url = format!("{api_base}/issue");
    let body = json!({ "fields": build_jira_fields(item, Some(project_key)) });
    let (status, _, json_body, raw_body) = send_with_retry(
        "jira",
        || http.post(&url).headers(headers.clone()).json(&body),
        3,
        ctx.retry_base_delay,
    )
    .await?;

    if !matches!(status, StatusCode::OK | StatusCode::CREATED) {
        return Err(WorkMgmtError::ApiHttp {
            platform: "jira",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let created = json_body.ok_or_else(|| WorkMgmtError::ApiHttp {
        platform: "jira",
        status: status.as_u16(),
        body: "Jira create issue returned empty body".into(),
    })?;

    let issue_key = created
        .get("key")
        .or_else(|| created.get("id"))
        .and_then(Value::as_str)
        .ok_or_else(|| WorkMgmtError::ApiHttp {
            platform: "jira",
            status: status.as_u16(),
            body: "Jira create issue response missing 'key'".into(),
        })?
        .to_string();

    if matches!(item.status.as_str(), "IN_PROGRESS" | "CLOSED" | "RESOLVED") {
        let _ = transition_jira_issue(
            http,
            headers,
            api_base,
            &issue_key,
            &item.status,
            ctx.retry_base_delay,
        )
        .await;
    }

    Ok(make_pushed_mapping(
        "jira",
        ctx.sync_config_id_or_zero(),
        item,
        issue_key,
        "create",
        None,
    ))
}

async fn push_update_issue(
    http: &reqwest::Client,
    headers: &HeaderMap,
    api_base: &str,
    ctx: &SyncJobContext,
    item: &LocalItem,
    issue_key: &str,
) -> Result<PushedMapping> {
    let url = format!("{api_base}/issue/{}", encode_path_segment(issue_key));
    let body = json!({ "fields": build_jira_fields(item, None) });
    let (status, _, _, raw_body) = send_with_retry(
        "jira",
        || http.put(&url).headers(headers.clone()).json(&body),
        3,
        ctx.retry_base_delay,
    )
    .await?;

    if !matches!(status, StatusCode::OK | StatusCode::NO_CONTENT) {
        return Err(WorkMgmtError::ApiHttp {
            platform: "jira",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let _ = transition_jira_issue(
        http,
        headers,
        api_base,
        issue_key,
        &item.status,
        ctx.retry_base_delay,
    )
    .await;

    Ok(make_pushed_mapping(
        "jira",
        ctx.sync_config_id_or_zero(),
        item,
        issue_key.to_string(),
        "update",
        None,
    ))
}

async fn transition_jira_issue(
    http: &reqwest::Client,
    headers: &HeaderMap,
    api_base: &str,
    issue_key: &str,
    elder_status: &str,
    base_delay: std::time::Duration,
) -> Result<()> {
    let target_cat = match elder_status.to_ascii_uppercase().as_str() {
        "CLOSED" | "RESOLVED" => "done",
        "IN_PROGRESS" => "indeterminate",
        _ => "new",
    };

    let url = format!(
        "{api_base}/issue/{}/transitions",
        encode_path_segment(issue_key)
    );
    let (status, _, json_body, _) = send_with_retry(
        "jira",
        || http.get(&url).headers(headers.clone()),
        2,
        base_delay,
    )
    .await?;
    if status != StatusCode::OK {
        return Ok(());
    }

    let transitions = json_body
        .as_ref()
        .and_then(|v| v.get("transitions"))
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[]);

    let mut chosen_id: Option<String> = None;
    for t in transitions {
        let to_cat = t
            .get("to")
            .and_then(|to| to.get("statusCategory"))
            .and_then(|sc| sc.get("key"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_ascii_lowercase();
        if to_cat == target_cat {
            chosen_id = t
                .get("id")
                .and_then(Value::as_str)
                .map(str::to_string)
                .or_else(|| t.get("id").and_then(Value::as_i64).map(|i| i.to_string()));
            break;
        }
    }

    if chosen_id.is_none() {
        let fallback_names: &[&str] = match target_cat {
            "done" => &["done", "closed", "resolved", "complete", "completed"],
            "indeterminate" => &["in progress", "in review", "active", "started"],
            _ => &["to do", "open", "backlog", "new", "reopen"],
        };
        for t in transitions {
            let to_name = t
                .get("to")
                .and_then(|to| to.get("name"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_ascii_lowercase();
            let t_name = t
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_ascii_lowercase();
            if fallback_names.contains(&to_name.as_str())
                || fallback_names.contains(&t_name.as_str())
            {
                chosen_id = t
                    .get("id")
                    .and_then(Value::as_str)
                    .map(str::to_string)
                    .or_else(|| t.get("id").and_then(Value::as_i64).map(|i| i.to_string()));
                break;
            }
        }
    }

    if let Some(tid) = chosen_id {
        let payload = json!({ "transition": { "id": tid } });
        let _ = send_with_retry(
            "jira",
            || http.post(&url).headers(headers.clone()).json(&payload),
            2,
            base_delay,
        )
        .await;
    }

    Ok(())
}
