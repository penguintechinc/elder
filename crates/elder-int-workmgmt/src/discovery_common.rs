//! Shared endpoint/security discovery structures, PII/secret scrubbing, and Elder `/start` + `/complete` runner.

use std::collections::BTreeMap;

use chrono::Utc;
use elder_svc_common::ElderApiClient;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::error::{Result, WorkMgmtError};
use crate::sync_common::{get_bool_from_map, get_i64_from_map, get_str_from_map};
use crate::RunOutcome;

const SENSITIVE_EXACT_KEYS: &[&str] = &[
    "author_email",
    "author_name",
    "author_id",
    "device_mapping",
    "end_users",
    "encryption_key",
    "recovery_key",
    "filevault_key",
    "bitlocker_recovery_key",
    "unlock_pin",
    "bootstrap_token",
    "escrow_key",
    "password",
    "secret",
    "token",
    "api_token",
    "api_key",
    "private_key",
    "secret_key",
    "profile_contents",
    "mobileconfig",
    "email",
    "user_email",
    "first_name",
    "last_name",
    "firstname",
    "lastname",
    "phone",
    "passcode",
];

const SENSITIVE_SUBSTRINGS: &[&str] = &[
    "password",
    "secret",
    "token",
    "encryption_key",
    "recovery_key",
    "unlock_pin",
    "private_key",
    "filevault_key",
    "bitlocker_recovery",
    "escrow_key",
    "mobileconfig",
    "profile_contents",
];

/// Parsed discovery job input merging top-level fields and `config_json`.
#[derive(Debug, Clone)]
pub struct DiscoveryJobContext {
    pub job_id: Option<i64>,
    pub provider: String,
    pub organization_id: Option<i64>,
    pub merged: Map<String, Value>,
}

/// Relationship hint attached to a discovered resource (matches `BaseDiscoveryProvider.format_resource`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiscoveryRelationship {
    pub target_external_id: String,
    pub target_kind: String,
    pub edge_type: String,
}

/// Standardized Elder discovery resource record.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiscoveryResource {
    pub resource_id: String,
    pub resource_type: String,
    pub name: String,
    pub provider: String,
    pub region: Option<String>,
    pub tags: BTreeMap<String, String>,
    pub metadata: Value,
    pub external_id: String,
    pub relationships: Vec<DiscoveryRelationship>,
    pub discovered_at: String,
}

/// Standardized Elder discovery dependency edge record.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiscoveryDependency {
    pub source_external_id: String,
    pub target_external_id: String,
    pub dependency_type: String,
    pub description: Option<String>,
    pub metadata: Value,
}

/// Aggregated discovery result produced by a provider connector.
#[derive(Debug, Clone, Default)]
pub struct DiscoveryOutput {
    pub resources: Vec<DiscoveryResource>,
    pub dependencies: Vec<DiscoveryDependency>,
    pub partial_errors: Vec<String>,
    pub summary_extra: Map<String, Value>,
}

#[derive(Debug, Serialize)]
struct CompleteDiscoveryRequest {
    history_id: i64,
    status: String,
    organization_id: Option<i64>,
    resources: Vec<DiscoveryResource>,
    dependencies: Vec<DiscoveryDependency>,
    error_message: Option<String>,
    summary: Value,
}

impl DiscoveryJobContext {
    /// Parse discovery job context from JSON.
    pub fn from_value(job: &Value) -> Result<Self> {
        let obj = job
            .as_object()
            .ok_or_else(|| WorkMgmtError::Config("discovery job must be a JSON object".into()))?;

        let mut merged = obj.clone();
        for nested_key in ["config", "config_json"] {
            if let Some(Value::Object(inner)) = obj.get(nested_key) {
                for (k, v) in inner {
                    merged.insert(k.clone(), v.clone());
                }
            }
        }

        let job_id = get_i64_from_map(&merged, &["id", "job_id"]);
        let provider = get_str_from_map(&merged, &["provider", "provider_type"])
            .unwrap_or_default()
            .to_ascii_lowercase();
        let organization_id = get_i64_from_map(&merged, &["organization_id", "_organization_id"]);

        Ok(Self {
            job_id,
            provider,
            organization_id,
            merged,
        })
    }

    pub fn get_str(&self, keys: &[&str]) -> Option<String> {
        get_str_from_map(&self.merged, keys)
    }

    pub fn get_i64(&self, keys: &[&str]) -> Option<i64> {
        get_i64_from_map(&self.merged, keys)
    }

    pub fn get_bool(&self, keys: &[&str]) -> Option<bool> {
        get_bool_from_map(&self.merged, keys)
    }
}

/// Return `true` when a key represents PII or secret material that must never be stored.
pub fn is_sensitive_key(key: &str) -> bool {
    let lower = key.to_ascii_lowercase();
    if SENSITIVE_EXACT_KEYS.contains(&lower.as_str()) {
        return true;
    }
    SENSITIVE_SUBSTRINGS.iter().any(|sub| lower.contains(sub))
}

/// Recursively strip PII and secret keys from arbitrary JSON values.
pub fn sanitize_json_value(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut clean = Map::new();
            for (k, v) in map {
                if !is_sensitive_key(k) {
                    clean.insert(k.clone(), sanitize_json_value(v));
                }
            }
            Value::Object(clean)
        }
        Value::Array(items) => Value::Array(items.iter().map(sanitize_json_value).collect()),
        other => other.clone(),
    }
}

/// Map a CVSS score to an Elder severity label (`critical`, `high`, `medium`, `low`, `unknown`).
pub fn cvss_to_severity(score: Option<f64>) -> &'static str {
    match score {
        None => "unknown",
        Some(s) if s >= 9.0 => "critical",
        Some(s) if s >= 7.0 => "high",
        Some(s) if s >= 4.0 => "medium",
        Some(_) => "low",
    }
}

/// Truncate a string cleanly to at most `max_chars` Unicode characters.
pub fn truncate_chars(s: &str, max_chars: usize) -> String {
    if s.chars().count() <= max_chars {
        return s.to_string();
    }
    s.chars().take(max_chars).collect()
}

/// Build a standardized `DiscoveryResource` (sanitizing metadata automatically).
pub fn format_resource(
    provider: &str,
    resource_id: impl Into<String>,
    resource_type: impl Into<String>,
    name: &str,
    external_id: impl Into<String>,
    metadata: Value,
) -> DiscoveryResource {
    DiscoveryResource {
        resource_id: resource_id.into(),
        resource_type: resource_type.into(),
        name: truncate_chars(name, 255),
        provider: provider.to_string(),
        region: None,
        tags: BTreeMap::new(),
        metadata: sanitize_json_value(&metadata),
        external_id: truncate_chars(&external_id.into(), 255),
        relationships: Vec::new(),
        discovered_at: Utc::now().to_rfc3339(),
    }
}

impl DiscoveryResource {
    /// Attach normalized provider tags.
    pub fn with_tags(mut self, tags: BTreeMap<String, String>) -> Self {
        self.tags = tags;
        self
    }

    /// Attach relationship edges.
    pub fn with_relationships(mut self, relationships: Vec<DiscoveryRelationship>) -> Self {
        self.relationships = relationships;
        self
    }
}

/// Execute `/start`, invoke `discover_fn`, and post `/complete` back to Elder API.
pub async fn run_discovery_lifecycle<F, Fut>(
    elder: &ElderApiClient,
    ctx: &DiscoveryJobContext,
    discover_fn: F,
) -> RunOutcome
where
    F: FnOnce() -> Fut,
    Fut: std::future::Future<Output = Result<DiscoveryOutput>>,
{
    let Some(job_id) = ctx.job_id else {
        let err = "discovery job missing numeric 'id'".to_string();
        tracing::error!(provider = %ctx.provider, error = %err);
        return RunOutcome {
            items_synced: 0,
            items_failed: 1,
            status: "failed".to_string(),
            error: Some(err),
        };
    };

    let start_path = format!("/api/v1/integrations/internal/discovery/{job_id}/start");
    let start_resp = match elder.post_empty::<Value>(&start_path).await {
        Ok(resp) => resp,
        Err(err) => {
            let msg = format!("failed calling discovery /start: {err}");
            tracing::error!(job_id, provider = %ctx.provider, error = %msg);
            return RunOutcome {
                items_synced: 0,
                items_failed: 1,
                status: "failed".to_string(),
                error: Some(msg),
            };
        }
    };

    let Some(history_id) = extract_history_id(&start_resp) else {
        let msg = "Elder discovery /start response missing 'history_id'".to_string();
        tracing::error!(job_id, provider = %ctx.provider, error = %msg);
        return RunOutcome {
            items_synced: 0,
            items_failed: 1,
            status: "failed".to_string(),
            error: Some(msg),
        };
    };

    let discover_res = discover_fn().await;
    let (status_str, resources, dependencies, error_message, mut summary_extra, items_failed) =
        match discover_res {
            Ok(output) => {
                let err_count = output.partial_errors.len();
                let err_msg = if output.partial_errors.is_empty() {
                    None
                } else {
                    Some(output.partial_errors.join("; "))
                };
                let status_str = if err_count == 0 {
                    "completed"
                } else if !output.resources.is_empty() {
                    "partial"
                } else {
                    "failed"
                };
                (
                    status_str.to_string(),
                    output.resources,
                    output.dependencies,
                    err_msg,
                    output.summary_extra,
                    err_count,
                )
            }
            Err(err) => (
                "failed".to_string(),
                Vec::new(),
                Vec::new(),
                Some(err.to_string()),
                Map::new(),
                1,
            ),
        };

    let mut by_type: BTreeMap<String, usize> = BTreeMap::new();
    for res in &resources {
        *by_type.entry(res.resource_type.clone()).or_insert(0) += 1;
    }
    let items_synced = resources.len();

    summary_extra.insert("provider".to_string(), json!(ctx.provider));
    summary_extra.insert("resources_count".to_string(), json!(resources.len()));
    summary_extra.insert("dependencies_count".to_string(), json!(dependencies.len()));
    summary_extra.insert("by_type".to_string(), json!(by_type));

    let complete_req = CompleteDiscoveryRequest {
        history_id,
        status: status_str.clone(),
        organization_id: ctx.organization_id,
        resources,
        dependencies,
        error_message: error_message.clone(),
        summary: Value::Object(summary_extra),
    };

    let complete_path = format!("/api/v1/integrations/internal/discovery/{job_id}/complete");
    if let Err(cb_err) = elder
        .post_json::<_, Value>(&complete_path, &complete_req)
        .await
    {
        tracing::error!(
            job_id,
            history_id,
            provider = %ctx.provider,
            error = %cb_err,
            "Failed submitting discovery /complete callback"
        );
        let combined = match error_message {
            Some(prev) => format!("{prev}; Elder /complete failed: {cb_err}"),
            None => format!("Elder /complete failed: {cb_err}"),
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
        error: error_message,
    }
}

fn extract_history_id(val: &Value) -> Option<i64> {
    val.get("history_id")
        .and_then(Value::as_i64)
        .or_else(|| {
            val.get("data")
                .and_then(|d| d.get("history_id"))
                .and_then(Value::as_i64)
        })
        .or_else(|| val.get("id").and_then(Value::as_i64))
}
