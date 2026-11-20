//! Shared work-management sync models, timestamp parsing, reconciliation, and Elder `/apply` callback.

use std::collections::{HashMap, HashSet};
use std::time::Duration;

use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use elder_svc_common::ElderApiClient;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::error::{Result, WorkMgmtError};
use crate::RunOutcome;

/// Supported work-management sync directions.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SyncDirection {
    Pull,
    Push,
    Bidirectional,
}

/// Strategy used when both Elder and the external platform modified an item.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConflictStrategy {
    Manual,
    LastModifiedWins,
    ElderWins,
    ExternalWins,
}

/// Parsed work-management sync job context from `config`.
#[derive(Debug, Clone)]
pub struct SyncJobContext {
    pub config_id: Option<i64>,
    pub platform: String,
    pub correlation_id: String,
    pub two_way_create: bool,
    pub direction: SyncDirection,
    pub conflict_strategy: ConflictStrategy,
    pub since: Option<DateTime<Utc>>,
    pub retry_base_delay: Duration,
    pub local_items: Vec<LocalItem>,
    pub mappings: Vec<SyncMappingRecord>,
    pub merged: Map<String, Value>,
}

/// An Elder local issue record provided in `config["local_items"]`.
#[derive(Debug, Clone, Serialize)]
pub struct LocalItem {
    pub elder_id: i64,
    pub title: String,
    pub description: String,
    pub status: String,
    pub priority: String,
    pub issue_type: String,
    pub labels: Vec<String>,
    pub assignees: Vec<String>,
    pub due_date: Option<String>,
    pub updated_at: Option<DateTime<Utc>>,
    pub raw_updated_at: Option<String>,
    pub deleted: bool,
    pub list_id: Option<String>,
    pub lock_version: Option<i64>,
    pub milestone_id: Option<i64>,
    pub weight: Option<i64>,
}

/// An existing Elder <-> external mapping provided in `config["mappings"]`.
#[derive(Debug, Clone)]
pub struct SyncMappingRecord {
    pub elder_type: String,
    pub elder_id: i64,
    pub external_id: String,
    pub last_synced_at: Option<DateTime<Utc>>,
    pub elder_updated_at: Option<DateTime<Utc>>,
    pub external_updated_at: Option<DateTime<Utc>>,
}

/// A remote issue pulled and normalized for Elder `/apply`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PulledItem {
    pub elder_type: String,
    pub elder_id: Option<i64>,
    pub external_id: String,
    pub external_key: Option<String>,
    pub operation: String,
    pub title: String,
    pub description: String,
    pub status: String,
    pub priority: String,
    pub issue_type: String,
    pub labels: Vec<String>,
    pub assignees: Vec<String>,
    pub due_date: Option<String>,
    pub closed_at: Option<String>,
    pub external_updated_at: Option<String>,
    pub updated_at: Option<String>,
    pub external_url: Option<String>,
    pub metadata: Value,
    #[serde(skip)]
    pub external_updated_dt: Option<DateTime<Utc>>,
    #[serde(skip)]
    pub deleted: bool,
}

/// A mapping created or updated when pushing an Elder item to an external platform.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PushedMapping {
    pub elder_type: String,
    pub elder_id: i64,
    pub external_platform: String,
    pub external_id: String,
    pub sync_config_id: i64,
    pub operation: String,
    pub elder_updated_at: Option<String>,
    pub external_updated_at: Option<String>,
    pub last_synced_at: String,
}

/// Conflict detected between Elder and remote issue updates.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncConflict {
    pub conflict_type: String,
    pub elder_type: String,
    pub elder_id: i64,
    pub external_id: String,
    pub resolution_strategy: String,
    pub resolved: bool,
    pub elder_modified: Option<String>,
    pub external_modified: Option<String>,
    pub last_synced_at: Option<String>,
    pub mismatched_fields: Vec<String>,
    pub elder_data: Value,
    pub external_data: Value,
}

/// Payload posted to `POST /api/v1/integrations/internal/sync/{config_id}/apply`.
#[derive(Debug, Clone, Serialize)]
pub struct SyncApplyPayload {
    pub correlation_id: String,
    pub sync_type: String,
    pub success: bool,
    pub error_message: Option<String>,
    pub pulled_items: Vec<PulledItem>,
    pub pushed_mappings: Vec<PushedMapping>,
    pub conflicts: Vec<SyncConflict>,
    pub items_synced: usize,
    pub items_failed: usize,
    pub metadata: Value,
}

/// Outcome of a platform sync run before posting to `/apply`.
#[derive(Debug, Clone)]
pub struct PlatformSyncOutcome {
    pub pulled_items: Vec<PulledItem>,
    pub pushed_mappings: Vec<PushedMapping>,
    pub conflicts: Vec<SyncConflict>,
    pub items_failed: usize,
    pub errors: Vec<String>,
    pub metadata: Value,
}

/// Plan computed by `reconcile_issues`.
#[derive(Debug)]
pub struct ReconcilePlan {
    pub pulled_items: Vec<PulledItem>,
    pub conflicts: Vec<SyncConflict>,
    pub to_create: Vec<LocalItem>,
    pub to_update: Vec<(LocalItem, SyncMappingRecord)>,
}

impl SyncJobContext {
    /// Parse a sync configuration JSON blob supporting both flat and `config_json`-nested shapes.
    pub fn from_value(config: &Value) -> Result<Self> {
        let obj = config
            .as_object()
            .ok_or_else(|| WorkMgmtError::Config("sync config must be a JSON object".into()))?;

        let mut merged = obj.clone();
        for nested_key in ["config", "config_json"] {
            if let Some(Value::Object(inner)) = obj.get(nested_key) {
                for (k, v) in inner {
                    merged.insert(k.clone(), v.clone());
                }
            }
        }

        let config_id = get_i64_from_map(&merged, &["id", "config_id", "sync_config_id"]);
        let platform = get_str_from_map(&merged, &["platform"])
            .unwrap_or_default()
            .to_ascii_lowercase();

        let correlation_id = get_str_from_map(&merged, &["correlation_id"]).unwrap_or_else(|| {
            format!(
                "sync-{}-{}",
                config_id.unwrap_or(0),
                Utc::now().timestamp_millis()
            )
        });

        let two_way_create = get_bool_from_map(&merged, &["two_way_create"]).unwrap_or(false);

        let direction = match get_str_from_map(&merged, &["direction"])
            .map(|s| s.to_ascii_lowercase())
            .as_deref()
        {
            Some("pull") | Some("external_to_elder") => SyncDirection::Pull,
            Some("push") | Some("elder_to_external") => SyncDirection::Push,
            Some("bidirectional") | None => SyncDirection::Bidirectional,
            Some(other) => {
                return Err(WorkMgmtError::Config(format!(
                    "unsupported sync direction {other:?}; expected pull, push, or bidirectional"
                )));
            }
        };

        let conflict_strategy = match get_str_from_map(&merged, &["conflict_strategy"])
            .map(|s| s.to_ascii_lowercase())
            .as_deref()
        {
            Some("last_modified_wins") => ConflictStrategy::LastModifiedWins,
            Some("elder_wins") => ConflictStrategy::ElderWins,
            Some("external_wins") => ConflictStrategy::ExternalWins,
            Some("manual") | None => ConflictStrategy::Manual,
            Some(other) => {
                return Err(WorkMgmtError::Config(format!(
                    "unsupported conflict_strategy {other:?}"
                )));
            }
        };

        let since = merged.get("since").and_then(parse_flexible_datetime);
        let retry_ms = get_i64_from_map(&merged, &["retry_delay_ms"])
            .map(|v| u64::try_from(v.max(0)).unwrap_or(150))
            .unwrap_or(150);

        let local_items = merged
            .get("local_items")
            .and_then(Value::as_array)
            .map(|arr| arr.iter().filter_map(parse_local_item).collect())
            .unwrap_or_default();

        let mappings = merged
            .get("mappings")
            .and_then(Value::as_array)
            .map(|arr| arr.iter().filter_map(parse_mapping_record).collect())
            .unwrap_or_default();

        Ok(Self {
            config_id,
            platform,
            correlation_id,
            two_way_create,
            direction,
            conflict_strategy,
            since,
            retry_base_delay: Duration::from_millis(retry_ms),
            local_items,
            mappings,
            merged,
        })
    }

    /// Look up the first non-empty string across a list of candidate keys.
    pub fn get_str(&self, keys: &[&str]) -> Option<String> {
        get_str_from_map(&self.merged, keys)
    }

    /// Look up an `i64` across a list of candidate keys.
    pub fn get_i64(&self, keys: &[&str]) -> Option<i64> {
        get_i64_from_map(&self.merged, keys)
    }

    /// Look up a `bool` across a list of candidate keys.
    pub fn get_bool(&self, keys: &[&str]) -> Option<bool> {
        get_bool_from_map(&self.merged, keys)
    }

    /// Return `config_id` or 0 when absent.
    pub fn sync_config_id_or_zero(&self) -> i64 {
        self.config_id.unwrap_or(0)
    }
}

/// Reconcile pulled remote issues against `local_items` and `mappings`.
pub fn reconcile_issues(ctx: &SyncJobContext, pulled_raw: Vec<PulledItem>) -> ReconcilePlan {
    let mut by_elder_id: HashMap<i64, &SyncMappingRecord> = HashMap::new();
    let mut by_external_id: HashMap<&str, &SyncMappingRecord> = HashMap::new();
    for m in &ctx.mappings {
        if m.elder_type.is_empty() || m.elder_type.eq_ignore_ascii_case("issue") {
            by_elder_id.insert(m.elder_id, m);
            by_external_id.insert(m.external_id.as_str(), m);
        }
    }

    let mut pulled_by_ext: HashMap<String, PulledItem> = HashMap::with_capacity(pulled_raw.len());
    let mut pulled_order: Vec<String> = Vec::with_capacity(pulled_raw.len());
    for mut item in pulled_raw {
        if let Some(m) = by_external_id.get(item.external_id.as_str()) {
            item.elder_id = Some(m.elder_id);
            item.operation = "update".to_string();
        } else {
            item.operation = "create".to_string();
        }
        let key = item.external_id.clone();
        if !pulled_by_ext.contains_key(&key) {
            pulled_order.push(key.clone());
        }
        pulled_by_ext.insert(key, item);
    }

    let mut conflicts: Vec<SyncConflict> = Vec::new();
    let mut to_create: Vec<LocalItem> = Vec::new();
    let mut to_update: Vec<(LocalItem, SyncMappingRecord)> = Vec::new();
    let mut suppressed_pulls: HashSet<String> = HashSet::new();

    if ctx.direction != SyncDirection::Pull {
        for local in &ctx.local_items {
            if let Some(mapping) = by_elder_id.get(&local.elder_id) {
                let remote_opt = pulled_by_ext.get(&mapping.external_id);
                let local_modified = is_local_modified(local, mapping, ctx.direction);
                let remote_modified =
                    remote_opt.is_some_and(|remote| is_remote_modified(remote, mapping));

                if local.deleted && remote_opt.is_some_and(|r| !r.deleted) {
                    suppressed_pulls.insert(mapping.external_id.clone());
                    let remote_val = remote_opt
                        .map(|r| serde_json::to_value(r).unwrap_or(Value::Null))
                        .unwrap_or(Value::Null);
                    conflicts.push(SyncConflict {
                        conflict_type: "deleted_local".to_string(),
                        elder_type: "issue".to_string(),
                        elder_id: local.elder_id,
                        external_id: mapping.external_id.clone(),
                        resolution_strategy: "manual".to_string(),
                        resolved: false,
                        elder_modified: local.raw_updated_at.clone(),
                        external_modified: remote_opt.and_then(|r| r.external_updated_at.clone()),
                        last_synced_at: mapping.last_synced_at.map(|d| d.to_rfc3339()),
                        mismatched_fields: vec!["deleted".to_string()],
                        elder_data: serde_json::to_value(local).unwrap_or(Value::Null),
                        external_data: remote_val,
                    });
                    continue;
                }

                if local_modified && remote_modified {
                    if let Some(remote) = remote_opt {
                        let mismatched = find_mismatched_fields(local, remote);
                        let (strategy_name, resolved, winner_elder) = match ctx.conflict_strategy {
                            ConflictStrategy::Manual => ("manual", false, false),
                            ConflictStrategy::ElderWins => ("elder_wins", true, true),
                            ConflictStrategy::ExternalWins => ("external_wins", true, false),
                            ConflictStrategy::LastModifiedWins => {
                                let elder_newer =
                                    match (local.updated_at, remote.external_updated_dt) {
                                        (Some(l), Some(r)) => l >= r,
                                        _ => true,
                                    };
                                ("last_modified_wins", true, elder_newer)
                            }
                        };

                        conflicts.push(SyncConflict {
                            conflict_type: "both_modified".to_string(),
                            elder_type: "issue".to_string(),
                            elder_id: local.elder_id,
                            external_id: mapping.external_id.clone(),
                            resolution_strategy: strategy_name.to_string(),
                            resolved,
                            elder_modified: local.raw_updated_at.clone(),
                            external_modified: remote.external_updated_at.clone(),
                            last_synced_at: mapping.last_synced_at.map(|d| d.to_rfc3339()),
                            mismatched_fields: mismatched,
                            elder_data: serde_json::to_value(local).unwrap_or(Value::Null),
                            external_data: serde_json::to_value(remote).unwrap_or(Value::Null),
                        });

                        if resolved && winner_elder {
                            suppressed_pulls.insert(mapping.external_id.clone());
                            to_update.push((local.clone(), (*mapping).clone()));
                        } else if !resolved {
                            // Unresolved manual conflict: do not overwrite either side.
                            suppressed_pulls.insert(mapping.external_id.clone());
                        }
                        continue;
                    }
                }

                if local_modified && !remote_modified {
                    suppressed_pulls.insert(mapping.external_id.clone());
                    to_update.push((local.clone(), (*mapping).clone()));
                }
            } else if ctx.two_way_create || ctx.direction == SyncDirection::Push {
                to_create.push(local.clone());
            }
        }
    }

    let pulled_items = pulled_order
        .into_iter()
        .filter(|ext_id| !suppressed_pulls.contains(ext_id))
        .filter_map(|ext_id| pulled_by_ext.remove(&ext_id))
        .collect();

    ReconcilePlan {
        pulled_items,
        conflicts,
        to_create,
        to_update,
    }
}

fn is_local_modified(
    local: &LocalItem,
    mapping: &SyncMappingRecord,
    direction: SyncDirection,
) -> bool {
    if direction == SyncDirection::Push {
        return true;
    }
    if let Some(sync_ts) = mapping.last_synced_at {
        return local.updated_at.is_some_and(|u| u > sync_ts);
    }
    if let (Some(u), Some(prev)) = (local.updated_at, mapping.elder_updated_at) {
        return u > prev;
    }
    true
}

fn is_remote_modified(remote: &PulledItem, mapping: &SyncMappingRecord) -> bool {
    if let Some(sync_ts) = mapping.last_synced_at {
        return remote.external_updated_dt.is_some_and(|u| u > sync_ts);
    }
    if let (Some(u), Some(prev)) = (remote.external_updated_dt, mapping.external_updated_at) {
        return u > prev;
    }
    false
}

fn find_mismatched_fields(local: &LocalItem, remote: &PulledItem) -> Vec<String> {
    let mut fields = Vec::new();
    if local.title != remote.title {
        fields.push("title".to_string());
    }
    if local.description != remote.description {
        fields.push("description".to_string());
    }
    if !local.status.eq_ignore_ascii_case(&remote.status) {
        fields.push("status".to_string());
    }
    if !local.priority.eq_ignore_ascii_case(&remote.priority) {
        fields.push("priority".to_string());
    }
    let local_labels: HashSet<&str> = local.labels.iter().map(String::as_str).collect();
    let remote_labels: HashSet<&str> = remote.labels.iter().map(String::as_str).collect();
    if local_labels != remote_labels {
        fields.push("labels".to_string());
    }
    fields
}

/// Build a `PushedMapping` entry after creating or updating an external issue.
pub fn make_pushed_mapping(
    platform: &str,
    sync_config_id: i64,
    local: &LocalItem,
    external_id: String,
    operation: &str,
    external_updated_at: Option<String>,
) -> PushedMapping {
    PushedMapping {
        elder_type: "issue".to_string(),
        elder_id: local.elder_id,
        external_platform: platform.to_string(),
        external_id,
        sync_config_id,
        operation: operation.to_string(),
        elder_updated_at: local.raw_updated_at.clone(),
        external_updated_at,
        last_synced_at: Utc::now().to_rfc3339(),
    }
}

/// Post sync outcome (success or failure) to Elder's `/apply` internal callback and return `RunOutcome`.
pub async fn finalize_sync_run(
    elder: &ElderApiClient,
    ctx: &SyncJobContext,
    outcome_res: Result<PlatformSyncOutcome>,
) -> RunOutcome {
    let (apply_payload, status_str, items_synced, items_failed, err_msg) = match outcome_res {
        Ok(data) => {
            let items_synced = data.pulled_items.len() + data.pushed_mappings.len();
            let items_failed = data.items_failed;
            let error_message = if data.errors.is_empty() {
                None
            } else {
                Some(data.errors.join("; "))
            };
            let success = items_failed == 0 && error_message.is_none();
            let status_str = if success {
                "completed".to_string()
            } else if items_synced > 0 {
                "partial".to_string()
            } else {
                "failed".to_string()
            };
            let mut meta = match data.metadata {
                Value::Object(m) => m,
                _ => Map::new(),
            };
            meta.insert("platform".to_string(), json!(ctx.platform));
            meta.insert("conflicts_count".to_string(), json!(data.conflicts.len()));

            let payload = SyncApplyPayload {
                correlation_id: ctx.correlation_id.clone(),
                sync_type: "batch".to_string(),
                success,
                error_message: error_message.clone(),
                pulled_items: data.pulled_items,
                pushed_mappings: data.pushed_mappings,
                conflicts: data.conflicts,
                items_synced,
                items_failed,
                metadata: Value::Object(meta),
            };
            (
                payload,
                status_str,
                items_synced,
                items_failed,
                error_message,
            )
        }
        Err(err) => {
            let msg = err.to_string();
            let payload = SyncApplyPayload {
                correlation_id: ctx.correlation_id.clone(),
                sync_type: "batch".to_string(),
                success: false,
                error_message: Some(msg.clone()),
                pulled_items: Vec::new(),
                pushed_mappings: Vec::new(),
                conflicts: Vec::new(),
                items_synced: 0,
                items_failed: 1,
                metadata: json!({ "platform": ctx.platform }),
            };
            (payload, "failed".to_string(), 0, 1, Some(msg))
        }
    };

    let Some(config_id) = ctx.config_id else {
        let missing_msg = err_msg.unwrap_or_else(|| "missing sync config id".to_string());
        tracing::error!(platform = %ctx.platform, error = %missing_msg, "Cannot post /apply without config id");
        return RunOutcome {
            items_synced,
            items_failed: items_failed.max(1),
            status: "failed".to_string(),
            error: Some(missing_msg),
        };
    };

    let apply_path = format!("/api/v1/integrations/internal/sync/{config_id}/apply");
    if let Err(cb_err) = elder
        .post_json::<_, Value>(&apply_path, &apply_payload)
        .await
    {
        tracing::error!(
            config_id,
            platform = %ctx.platform,
            error = %cb_err,
            "Failed posting sync /apply callback to Elder API"
        );
        let combined = match err_msg {
            Some(prev) => format!("{prev}; Elder /apply failed: {cb_err}"),
            None => format!("Elder /apply failed: {cb_err}"),
        };
        return RunOutcome {
            items_synced,
            items_failed: items_failed.max(1),
            status: "failed".to_string(),
            error: Some(combined),
        };
    }

    RunOutcome {
        items_synced,
        items_failed,
        status: status_str,
        error: err_msg,
    }
}

/// Extract a non-empty string value from a JSON map using candidate keys.
pub fn get_str_from_map(map: &Map<String, Value>, keys: &[&str]) -> Option<String> {
    for key in keys {
        if let Some(val) = map.get(*key) {
            match val {
                Value::String(s) => {
                    let trimmed = s.trim();
                    if !trimmed.is_empty() {
                        return Some(trimmed.to_string());
                    }
                }
                Value::Number(n) => {
                    return Some(n.to_string());
                }
                _ => {}
            }
        }
    }
    None
}

/// Extract an `i64` from a JSON map using candidate keys (accepts numeric strings).
pub fn get_i64_from_map(map: &Map<String, Value>, keys: &[&str]) -> Option<i64> {
    for key in keys {
        if let Some(val) = map.get(*key) {
            match val {
                Value::Number(n) => {
                    if let Some(i) = n.as_i64() {
                        return Some(i);
                    }
                }
                Value::String(s) => {
                    if let Ok(i) = s.trim().parse::<i64>() {
                        return Some(i);
                    }
                }
                _ => {}
            }
        }
    }
    None
}

/// Extract a `bool` from a JSON map using candidate keys.
pub fn get_bool_from_map(map: &Map<String, Value>, keys: &[&str]) -> Option<bool> {
    for key in keys {
        if let Some(val) = map.get(*key) {
            match val {
                Value::Bool(b) => return Some(*b),
                Value::String(s) => match s.trim().to_ascii_lowercase().as_str() {
                    "true" | "1" | "yes" => return Some(true),
                    "false" | "0" | "no" => return Some(false),
                    _ => {}
                },
                Value::Number(n) => {
                    if let Some(i) = n.as_i64() {
                        return Some(i != 0);
                    }
                }
                _ => {}
            }
        }
    }
    None
}

/// Parse an ISO8601/RFC3339, Jira `+0000`, date-only `YYYY-MM-DD`, or unix epoch timestamp.
pub fn parse_flexible_datetime(val: &Value) -> Option<DateTime<Utc>> {
    match val {
        Value::Number(n) => n
            .as_i64()
            .and_then(|secs| DateTime::from_timestamp(secs, 0))
            .or_else(|| {
                n.as_f64()
                    .and_then(|secs| DateTime::from_timestamp(secs as i64, 0))
            }),
        Value::String(raw) => parse_datetime_str(raw),
        _ => None,
    }
}

/// Parse an ISO8601/RFC3339/Jira/date string into `DateTime<Utc>`.
pub fn parse_datetime_str(raw: &str) -> Option<DateTime<Utc>> {
    let mut s = raw.trim().to_string();
    if s.is_empty() {
        return None;
    }

    // Normalize Jira offsets like 2026-11-20T10:15:00.000+0000 -> +00:00
    if s.len() >= 5 {
        let bytes = s.as_bytes();
        let sign_idx = bytes.len() - 5;
        if (bytes[sign_idx] == b'+' || bytes[sign_idx] == b'-')
            && bytes[sign_idx + 1..].iter().all(u8::is_ascii_digit)
        {
            s.insert(bytes.len() - 2, ':');
        }
    }

    if let Ok(dt) = DateTime::parse_from_rfc3339(&s) {
        return Some(dt.with_timezone(&Utc));
    }

    for fmt in [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S",
    ] {
        if let Ok(naive) = NaiveDateTime::parse_from_str(&s, fmt) {
            return Some(naive.and_utc());
        }
    }

    if s.len() >= 10 {
        if let Ok(date) = NaiveDate::parse_from_str(&s[..10], "%Y-%m-%d") {
            return date.and_hms_opt(0, 0, 0).map(|naive| naive.and_utc());
        }
    }

    None
}

/// Convert JSON string or number to `Option<String>`.
pub fn value_to_id_string(val: &Value) -> Option<String> {
    match val {
        Value::String(s) => {
            let t = s.trim();
            if t.is_empty() {
                None
            } else {
                Some(t.to_string())
            }
        }
        Value::Number(n) => Some(n.to_string()),
        _ => None,
    }
}

/// Extract label names from an array of strings or `{"name": ...}` objects.
pub fn extract_label_names(val: Option<&Value>) -> Vec<String> {
    let Some(Value::Array(items)) = val else {
        return Vec::new();
    };
    items
        .iter()
        .filter_map(|item| match item {
            Value::String(s) => {
                let t = s.trim();
                if t.is_empty() {
                    None
                } else {
                    Some(t.to_string())
                }
            }
            Value::Object(obj) => obj
                .get("name")
                .or_else(|| obj.get("title"))
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(str::to_string),
            _ => None,
        })
        .collect()
}

/// Infer Elder uppercase priority (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`) from labels.
pub fn labels_to_priority(labels: &[String]) -> String {
    let mut tokens: HashSet<String> = HashSet::new();
    for label in labels {
        let lower = label.trim().to_ascii_lowercase();
        if lower.is_empty() {
            continue;
        }
        tokens.insert(lower.clone());
        for prefix in [
            "priority:",
            "priority/",
            "priority::",
            "sev:",
            "severity:",
            "severity::",
        ] {
            if let Some(rest) = lower.strip_prefix(prefix) {
                tokens.insert(rest.trim().to_string());
            }
        }
    }

    if ["critical", "urgent", "p0", "sev0"]
        .iter()
        .any(|k| tokens.contains(*k))
    {
        return "CRITICAL".to_string();
    }
    if ["high", "important", "p1", "sev1"]
        .iter()
        .any(|k| tokens.contains(*k))
    {
        return "HIGH".to_string();
    }
    if ["low", "minor", "p3", "p4"]
        .iter()
        .any(|k| tokens.contains(*k))
    {
        return "LOW".to_string();
    }
    "MEDIUM".to_string()
}

/// Infer Elder uppercase issue type (`SECURITY`, `BUG`, `FEATURE`, `OPERATIONS`, `CONFIG`, `SUPPORT`, `OTHER`) from labels.
pub fn labels_to_issue_type(labels: &[String]) -> String {
    let mut tokens: HashSet<String> = HashSet::new();
    for label in labels {
        let lower = label.trim().to_ascii_lowercase();
        if lower.is_empty() {
            continue;
        }
        tokens.insert(lower.clone());
        for prefix in ["type:", "type/", "type::", "kind:", "kind::"] {
            if let Some(rest) = lower.strip_prefix(prefix) {
                tokens.insert(rest.trim().to_string());
            }
        }
    }

    if ["security", "cve", "vulnerability"]
        .iter()
        .any(|k| tokens.contains(*k))
    {
        return "SECURITY".to_string();
    }
    if ["bug", "defect"].iter().any(|k| tokens.contains(*k)) {
        return "BUG".to_string();
    }
    if ["enhancement", "feature", "feature-request"]
        .iter()
        .any(|k| tokens.contains(*k))
    {
        return "FEATURE".to_string();
    }
    if ["operations", "ops", "incident"]
        .iter()
        .any(|k| tokens.contains(*k))
    {
        return "OPERATIONS".to_string();
    }
    if ["config", "configuration"]
        .iter()
        .any(|k| tokens.contains(*k))
    {
        return "CONFIG".to_string();
    }
    if ["support", "question", "helpdesk"]
        .iter()
        .any(|k| tokens.contains(*k))
    {
        return "SUPPORT".to_string();
    }
    "OTHER".to_string()
}

fn parse_local_item(val: &Value) -> Option<LocalItem> {
    let obj = val.as_object()?;
    if let Some(rt) = get_str_from_map(obj, &["elder_type", "resource_type"]) {
        if !rt.eq_ignore_ascii_case("issue") {
            return None;
        }
    }
    let elder_id = get_i64_from_map(obj, &["id", "elder_id"])?;
    let title = get_str_from_map(obj, &["title", "summary", "subject", "name"]).unwrap_or_default();
    let description = get_str_from_map(obj, &["description", "body", "desc"]).unwrap_or_default();
    let status = get_str_from_map(obj, &["status", "state"])
        .map(|s| s.to_ascii_uppercase())
        .unwrap_or_else(|| "OPEN".to_string());
    let priority = get_str_from_map(obj, &["priority"])
        .map(|s| s.to_ascii_uppercase())
        .unwrap_or_else(|| "MEDIUM".to_string());
    let issue_type = get_str_from_map(obj, &["issue_type", "type"])
        .map(|s| s.to_ascii_uppercase())
        .unwrap_or_else(|| "OTHER".to_string());
    let labels = extract_label_names(obj.get("labels"));

    let mut assignees: Vec<String> = Vec::new();
    if let Some(Value::Array(arr)) = obj.get("assignees") {
        for entry in arr {
            match entry {
                Value::String(s) if !s.trim().is_empty() => assignees.push(s.trim().to_string()),
                Value::Object(m) => {
                    if let Some(login) = get_str_from_map(m, &["login", "username", "name", "id"]) {
                        assignees.push(login);
                    }
                }
                _ => {}
            }
        }
    } else if let Some(single) = get_str_from_map(obj, &["assignee"]) {
        assignees.push(single);
    }

    let raw_updated_at = get_str_from_map(obj, &["updated_at", "elder_updated_at"]);
    let updated_at = obj
        .get("updated_at")
        .or_else(|| obj.get("elder_updated_at"))
        .and_then(parse_flexible_datetime);

    Some(LocalItem {
        elder_id,
        title,
        description,
        status,
        priority,
        issue_type,
        labels,
        assignees,
        due_date: get_str_from_map(obj, &["due_date", "duedate", "due"]),
        updated_at,
        raw_updated_at,
        deleted: get_bool_from_map(obj, &["deleted"]).unwrap_or(false),
        list_id: get_str_from_map(obj, &["list_id", "idList"]),
        lock_version: get_i64_from_map(obj, &["lock_version", "lockVersion"]),
        milestone_id: get_i64_from_map(obj, &["milestone_id"]),
        weight: get_i64_from_map(obj, &["weight"]),
    })
}

fn parse_mapping_record(val: &Value) -> Option<SyncMappingRecord> {
    let obj = val.as_object()?;
    let elder_type = get_str_from_map(obj, &["elder_type", "resource_type"])
        .unwrap_or_else(|| "issue".to_string());
    let elder_id = get_i64_from_map(obj, &["elder_id", "local_id"])?;
    let external_id = get_str_from_map(obj, &["external_id", "remote_id"])?;
    let last_synced_at = obj.get("last_synced_at").and_then(parse_flexible_datetime);
    let elder_updated_at = obj
        .get("elder_updated_at")
        .and_then(parse_flexible_datetime);
    let external_updated_at = obj
        .get("external_updated_at")
        .and_then(parse_flexible_datetime);

    Some(SyncMappingRecord {
        elder_type,
        elder_id,
        external_id,
        last_synced_at,
        elder_updated_at,
        external_updated_at,
    })
}
