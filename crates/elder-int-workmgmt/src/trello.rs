//! Trello v1 work-management sync connector (board lists + cards pull, card create/update).

use std::collections::HashMap;
use std::time::Duration;

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

const DEFAULT_TRELLO_BASE: &str = "https://api.trello.com";

struct TrelloClient<'a> {
    http: &'a reqwest::Client,
    headers: &'a HeaderMap,
    api_base: &'a str,
    auth_params: &'a [(String, String)],
    base_delay: Duration,
}

#[derive(Debug, Default)]
struct BoardLists {
    names_by_id: HashMap<String, String>,
    first_open_id: Option<String>,
    in_progress_id: Option<String>,
    done_id: Option<String>,
}

/// Run batch two-way synchronization against Trello v1 REST API.
pub async fn sync(ctx: &SyncJobContext) -> Result<PlatformSyncOutcome> {
    let api_key = ctx
        .get_str(&["api_key", "key"])
        .ok_or_else(|| WorkMgmtError::Auth("Trello sync requires 'api_key' or 'key'".into()))?;
    let api_token = ctx
        .get_str(&["api_token", "token"])
        .ok_or_else(|| WorkMgmtError::Auth("Trello sync requires 'api_token' or 'token'".into()))?;
    let board_id = ctx
        .get_str(&["board_id"])
        .ok_or_else(|| WorkMgmtError::Config("Trello sync requires 'board_id'".into()))?;

    let raw_base = ctx
        .get_str(&["api_base_url", "base_url"])
        .unwrap_or_else(|| DEFAULT_TRELLO_BASE.to_string());
    let normalized = normalize_base_url(&raw_base, true)?;
    let api_base = ensure_path_suffix(&normalized, "/1");
    let verify_ssl = ctx.get_bool(&["verify_ssl"]).unwrap_or(true);
    let http = build_http_client(verify_ssl)?;

    let mut headers = HeaderMap::new();
    headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
    headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));

    let auth_params = [
        ("key".to_string(), api_key),
        ("token".to_string(), api_token),
    ];

    let client = TrelloClient {
        http: &http,
        headers: &headers,
        api_base: &api_base,
        auth_params: &auth_params,
        base_delay: ctx.retry_base_delay,
    };

    let lists = fetch_board_lists(&client, &board_id).await?;

    let pulled_raw = if ctx.direction != SyncDirection::Push {
        fetch_board_cards(&client, &board_id, &lists).await?
    } else {
        Vec::new()
    };

    let plan = reconcile_issues(ctx, pulled_raw);
    let default_list_id = ctx.get_str(&["default_list_id", "list_id"]);
    let mut pushed_mappings: Vec<PushedMapping> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    let mut items_failed: usize = 0;

    for item in &plan.to_create {
        match push_create_card(&client, &lists, default_list_id.as_deref(), ctx, item).await {
            Ok(m) => pushed_mappings.push(m),
            Err(err) => {
                items_failed += 1;
                errors.push(format!("create elder issue {}: {err}", item.elder_id));
            }
        }
    }

    for (item, mapping) in &plan.to_update {
        match push_update_card(&client, &lists, ctx, item, &mapping.external_id).await {
            Ok(m) => pushed_mappings.push(m),
            Err(err) => {
                items_failed += 1;
                errors.push(format!("update Trello card {}: {err}", mapping.external_id));
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
            "board_id": board_id,
            "lists_count": lists.names_by_id.len(),
            "api_base_url": api_base,
        }),
    })
}

async fn fetch_board_lists(client: &TrelloClient<'_>, board_id: &str) -> Result<BoardLists> {
    let url = format!(
        "{}/boards/{}/lists",
        client.api_base,
        encode_path_segment(board_id)
    );
    let (status, _, json_body, raw_body) = send_with_retry(
        "trello",
        || {
            client
                .http
                .get(&url)
                .headers(client.headers.clone())
                .query(client.auth_params)
        },
        3,
        client.base_delay,
    )
    .await?;

    if status != StatusCode::OK {
        return Err(WorkMgmtError::ApiHttp {
            platform: "trello",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let mut lists = BoardLists::default();
    if let Some(arr) = json_body.as_ref().and_then(Value::as_array) {
        for entry in arr {
            let Some(id) = entry.get("id").and_then(value_to_id_string) else {
                continue;
            };
            let name = entry
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            let closed = entry
                .get("closed")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let lower = name.to_ascii_lowercase();
            if !closed && lists.first_open_id.is_none() {
                lists.first_open_id = Some(id.clone());
            }
            if lists.in_progress_id.is_none()
                && (lower.contains("progress") || lower.contains("doing"))
            {
                lists.in_progress_id = Some(id.clone());
            }
            if lists.done_id.is_none()
                && (lower.contains("done")
                    || lower.contains("closed")
                    || lower.contains("complete"))
            {
                lists.done_id = Some(id.clone());
            }
            lists.names_by_id.insert(id, name);
        }
    }

    Ok(lists)
}

async fn fetch_board_cards(
    client: &TrelloClient<'_>,
    board_id: &str,
    lists: &BoardLists,
) -> Result<Vec<PulledItem>> {
    let url = format!(
        "{}/boards/{}/cards",
        client.api_base,
        encode_path_segment(board_id)
    );
    let (status, _, json_body, raw_body) = send_with_retry(
        "trello",
        || {
            client
                .http
                .get(&url)
                .headers(client.headers.clone())
                .query(client.auth_params)
        },
        3,
        client.base_delay,
    )
    .await?;

    if status != StatusCode::OK {
        return Err(WorkMgmtError::ApiHttp {
            platform: "trello",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let cards = json_body
        .as_ref()
        .and_then(Value::as_array)
        .ok_or_else(|| WorkMgmtError::ApiHttp {
            platform: "trello",
            status: status.as_u16(),
            body: "expected JSON array from Trello /boards/{id}/cards".into(),
        })?;

    Ok(cards
        .iter()
        .filter_map(|c| normalize_trello_card(c, lists))
        .collect())
}

fn normalize_trello_card(card: &Value, lists: &BoardLists) -> Option<PulledItem> {
    let ext_id = card.get("id").and_then(value_to_id_string)?;
    let title = card
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let description = card
        .get("desc")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let closed = card.get("closed").and_then(Value::as_bool).unwrap_or(false);
    let list_id = card.get("idList").and_then(value_to_id_string);
    let list_name = list_id
        .as_deref()
        .and_then(|id| lists.names_by_id.get(id))
        .map(|s| s.to_ascii_lowercase())
        .unwrap_or_default();

    let status = if closed
        || list_name.contains("done")
        || list_name.contains("closed")
        || list_name.contains("complete")
    {
        "CLOSED".to_string()
    } else if list_name.contains("progress")
        || list_name.contains("doing")
        || list_name.contains("review")
    {
        "IN_PROGRESS".to_string()
    } else {
        "OPEN".to_string()
    };

    let labels = extract_label_names(card.get("labels"));
    let priority = labels_to_priority(&labels);
    let issue_type = labels_to_issue_type(&labels);
    let due_date = card.get("due").and_then(Value::as_str).map(str::to_string);
    let external_updated_at = card
        .get("dateLastActivity")
        .and_then(Value::as_str)
        .map(str::to_string);
    let external_updated_dt = external_updated_at.as_deref().and_then(parse_datetime_str);
    let external_url = card
        .get("shortUrl")
        .or_else(|| card.get("url"))
        .and_then(Value::as_str)
        .map(str::to_string);

    Some(PulledItem {
        elder_type: "issue".to_string(),
        elder_id: None,
        external_id: ext_id.clone(),
        external_key: Some(ext_id),
        operation: "create".to_string(),
        title,
        description,
        status,
        priority,
        issue_type,
        labels,
        assignees: Vec::new(),
        due_date,
        closed_at: None,
        external_updated_at: external_updated_at.clone(),
        updated_at: external_updated_at,
        external_url,
        metadata: json!({
            "list_id": list_id,
            "closed": closed,
        }),
        external_updated_dt,
        deleted: false,
    })
}

fn resolve_target_list_id(
    item: &LocalItem,
    lists: &BoardLists,
    default_list_id: Option<&str>,
) -> Option<String> {
    if let Some(lid) = &item.list_id {
        return Some(lid.clone());
    }
    match item.status.as_str() {
        "IN_PROGRESS" if lists.in_progress_id.is_some() => lists.in_progress_id.clone(),
        "CLOSED" | "RESOLVED" if lists.done_id.is_some() => lists.done_id.clone(),
        _ => default_list_id
            .map(str::to_string)
            .or_else(|| lists.first_open_id.clone()),
    }
}

async fn push_create_card(
    client: &TrelloClient<'_>,
    lists: &BoardLists,
    default_list_id: Option<&str>,
    ctx: &SyncJobContext,
    item: &LocalItem,
) -> Result<PushedMapping> {
    let list_id = resolve_target_list_id(item, lists, default_list_id).ok_or_else(|| {
        WorkMgmtError::Config("Trello card creation requires a board list ('list_id')".into())
    })?;

    let mut body = Map::new();
    body.insert("idList".to_string(), json!(list_id));
    body.insert("name".to_string(), json!(item.title));
    body.insert("desc".to_string(), json!(item.description));
    body.insert(
        "closed".to_string(),
        json!(matches!(item.status.as_str(), "CLOSED" | "RESOLVED")),
    );
    if let Some(due) = &item.due_date {
        body.insert("due".to_string(), json!(due));
    }
    let body_val = Value::Object(body);

    let url = format!("{}/cards", client.api_base);
    let (status, _, json_body, raw_body) = send_with_retry(
        "trello",
        || {
            client
                .http
                .post(&url)
                .headers(client.headers.clone())
                .query(client.auth_params)
                .json(&body_val)
        },
        3,
        client.base_delay,
    )
    .await?;

    if !matches!(status, StatusCode::OK | StatusCode::CREATED) {
        return Err(WorkMgmtError::ApiHttp {
            platform: "trello",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let created = json_body.ok_or_else(|| WorkMgmtError::ApiHttp {
        platform: "trello",
        status: status.as_u16(),
        body: "Trello create card returned empty body".into(),
    })?;

    let card_id = created
        .get("id")
        .and_then(value_to_id_string)
        .ok_or_else(|| WorkMgmtError::ApiHttp {
            platform: "trello",
            status: status.as_u16(),
            body: "Trello create card response missing 'id'".into(),
        })?;
    let ext_updated = created
        .get("dateLastActivity")
        .and_then(Value::as_str)
        .map(str::to_string);

    Ok(make_pushed_mapping(
        "trello",
        ctx.sync_config_id_or_zero(),
        item,
        card_id,
        "create",
        ext_updated,
    ))
}

async fn push_update_card(
    client: &TrelloClient<'_>,
    lists: &BoardLists,
    ctx: &SyncJobContext,
    item: &LocalItem,
    card_id: &str,
) -> Result<PushedMapping> {
    let mut body = Map::new();
    body.insert("name".to_string(), json!(item.title));
    body.insert("desc".to_string(), json!(item.description));
    body.insert(
        "closed".to_string(),
        json!(matches!(item.status.as_str(), "CLOSED" | "RESOLVED")),
    );
    if let Some(lid) = resolve_target_list_id(item, lists, None) {
        body.insert("idList".to_string(), json!(lid));
    }
    if let Some(due) = &item.due_date {
        body.insert("due".to_string(), json!(due));
    }
    let body_val = Value::Object(body);

    let url = format!("{}/cards/{}", client.api_base, encode_path_segment(card_id));
    let (status, _, json_body, raw_body) = send_with_retry(
        "trello",
        || {
            client
                .http
                .put(&url)
                .headers(client.headers.clone())
                .query(client.auth_params)
                .json(&body_val)
        },
        3,
        client.base_delay,
    )
    .await?;

    if status != StatusCode::OK {
        return Err(WorkMgmtError::ApiHttp {
            platform: "trello",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let ext_updated = json_body
        .as_ref()
        .and_then(|v| v.get("dateLastActivity"))
        .and_then(Value::as_str)
        .map(str::to_string);

    Ok(make_pushed_mapping(
        "trello",
        ctx.sync_config_id_or_zero(),
        item,
        card_id.to_string(),
        "update",
        ext_updated,
    ))
}
