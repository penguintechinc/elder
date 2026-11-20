//! Pure-Rust identity and directory connectors for `svc-integrations`.
//!
//! Provides connectors for Google Workspace (Admin Directory API via RS256
//! service-account domain-wide delegation), Okta (`SSWS` token + RFC 8288
//! `Link` pagination), Authentik (`Bearer` token + v3 pagination), and
//! LDAP/LDAPS (`ldap3` backed strictly by `rustls`, with zero OpenSSL).

pub mod authentik;
pub mod google_workspace;
pub mod ldap;
pub mod okta;

use std::time::Duration;

use elder_svc_common::ElderApiClient;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use thiserror::Error;
use tracing::Instrument;

pub use google_workspace::run_google_workspace;

pub(crate) const DEFAULT_HTTP_CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
pub(crate) const DEFAULT_HTTP_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
pub(crate) const MAX_PAGINATION_PAGES: usize = 1000;

/// Normalized execution outcome returned by every identity connector entrypoint.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunOutcome {
    /// Number of directory items/memberships synchronized successfully.
    pub items_synced: usize,
    /// Number of items or sub-operations that failed during the run.
    pub items_failed: usize,
    /// Terminal status (`"completed"`, `"partial"`, or `"failed"`).
    pub status: String,
    /// Sanitized error summary when `status` is `"partial"` or `"failed"`.
    pub error: Option<String>,
}

impl RunOutcome {
    /// Construct a fully succeeded outcome.
    pub fn completed(items_synced: usize) -> Self {
        Self {
            items_synced,
            items_failed: 0,
            status: "completed".to_string(),
            error: None,
        }
    }

    /// Construct a partial-success outcome.
    pub fn partial(items_synced: usize, items_failed: usize, error: impl Into<String>) -> Self {
        Self {
            items_synced,
            items_failed,
            status: "partial".to_string(),
            error: Some(error.into()),
        }
    }

    /// Construct a failed outcome.
    pub fn failed(error: impl Into<String>) -> Self {
        Self {
            items_synced: 0,
            items_failed: 1,
            status: "failed".to_string(),
            error: Some(error.into()),
        }
    }

    /// Return `true` when the run did not end in `"failed"`.
    pub fn is_success(&self) -> bool {
        self.status != "failed"
    }
}

/// Errors produced by identity connector configuration, authentication, or transport.
#[derive(Debug, Error)]
pub enum IdentityError {
    /// Invalid or missing provider configuration.
    #[error("invalid identity configuration: {0}")]
    Config(String),

    /// Authentication, JWT signing, or LDAP bind failure.
    #[error("identity authentication error: {0}")]
    Auth(String),

    /// Upstream HTTP or LDAP protocol failure.
    #[error("upstream directory error: {0}")]
    Upstream(String),

    /// Error communicating with Elder API.
    #[error("Elder API error: {0}")]
    ElderApi(#[from] elder_svc_common::CommonError),
}

/// Sanitized directory user record emitted by Okta, Authentik, and LDAP connectors.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NormalizedUser {
    /// Provider-unique user identifier (`id`, `pk`, `objectGUID`, `entryUUID`, or DN).
    pub external_id: String,
    /// Duplicate key expected by Elder `/iam/{id}/apply`.
    pub id: String,
    /// Technical username/token (never populated with raw email or PII in discovery).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub username: Option<String>,
    /// Identity classification (`"human"`, `"service_account"`, `"integration"`).
    pub identity_type: String,
    /// Whether the upstream directory account is active/enabled.
    pub is_active: bool,
}

impl NormalizedUser {
    /// Return a PII-free label suitable for `/discovery/{id}/complete` resource names.
    pub fn discovery_name(&self, provider: &str) -> String {
        match &self.username {
            Some(name) if !name.is_empty() && !name.contains('@') => name.clone(),
            _ => format!("{provider}:{}", self.external_id),
        }
    }
}

/// Normalized directory group record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NormalizedGroup {
    /// Provider-unique group identifier.
    pub external_id: String,
    /// Duplicate key expected by Elder `/iam/{id}/apply`.
    pub id: String,
    /// Group display name.
    pub name: String,
    /// Optional group description.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    /// Whether the group is active.
    pub is_active: bool,
}

/// Normalized group membership link.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NormalizedMembership {
    /// External user ID matching [`NormalizedUser::external_id`].
    pub user_external_id: String,
    /// External group ID matching [`NormalizedGroup::external_id`].
    pub group_external_id: String,
    /// Provider-specific membership identifier.
    pub provider_member_id: String,
}

/// Aggregated normalized output from an Okta, Authentik, or LDAP sync run.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DirectorySyncBatch {
    /// Synchronized users.
    pub users: Vec<NormalizedUser>,
    /// Synchronized groups.
    pub groups: Vec<NormalizedGroup>,
    /// Synchronized user-to-group memberships.
    pub memberships: Vec<NormalizedMembership>,
    /// Non-fatal item failures encountered (e.g. single group member lookup failure).
    pub items_failed: usize,
    /// Optional summary of non-fatal item failures.
    pub warning: Option<String>,
}

impl DirectorySyncBatch {
    /// Total normalized objects/edges produced.
    pub fn total_items(&self) -> usize {
        self.users.len() + self.groups.len() + self.memberships.len()
    }
}

/// Run an IAM provider sync (`okta`, `authentik`, `ldap`) and apply results via
/// `POST /api/v1/integrations/internal/iam/{provider_id}/apply`.
pub async fn run_identity_provider(elder: &ElderApiClient, provider: &Value) -> RunOutcome {
    let Ok(provider_id) = extract_positive_id(provider, "id") else {
        return RunOutcome::failed("IAM provider payload missing positive 'id'");
    };
    let Some(provider_type) =
        non_empty_str(provider.get("provider_type")).map(|s| s.to_ascii_lowercase())
    else {
        return RunOutcome::failed("IAM provider payload missing 'provider_type'");
    };

    let span = tracing::info_span!(
        "elder_int_identity.iam_provider",
        provider_id = provider_id,
        provider_type = %provider_type
    );

    async move {
        let config = extract_config_object(provider);
        let batch_res = dispatch_directory_fetch(&provider_type, &config).await;

        let (payload, outcome) = match batch_res {
            Ok(batch) => {
                let synced = batch.total_items();
                let outcome = if batch.items_failed > 0 {
                    RunOutcome::partial(
                        synced,
                        batch.items_failed,
                        batch
                            .warning
                            .clone()
                            .unwrap_or_else(|| "partial identity sync failures".to_string()),
                    )
                } else {
                    RunOutcome::completed(synced)
                };
                let body = json!({
                    "success": true,
                    "error_message": outcome.error,
                    "users": batch.users,
                    "groups": batch.groups,
                    "roles": [],
                    "memberships": batch.memberships,
                });
                (body, outcome)
            }
            Err(err) => {
                let msg = err.to_string();
                let body = json!({
                    "success": false,
                    "error_message": msg,
                    "users": [],
                    "groups": [],
                    "roles": [],
                    "memberships": [],
                });
                (body, RunOutcome::failed(msg))
            }
        };

        let apply_path = format!("/api/v1/integrations/internal/iam/{provider_id}/apply");
        if let Err(err) = elder.post_json::<_, Value>(&apply_path, &payload).await {
            tracing::error!(
                provider_id = provider_id,
                provider_type = %provider_type,
                error = %err,
                "Failed submitting IAM apply payload to Elder API"
            );
            return RunOutcome::failed(format!("failed submitting IAM apply results: {err}"));
        }

        tracing::info!(
            provider_id = provider_id,
            provider_type = %provider_type,
            status = %outcome.status,
            items_synced = outcome.items_synced,
            items_failed = outcome.items_failed,
            "Completed IAM provider sync"
        );
        outcome
    }
    .instrument(span)
    .await
}

/// Run an identity discovery job (`okta`, `authentik`, `ldap`) via
/// `POST /api/v1/integrations/internal/discovery/{job_id}/start` and `/complete`.
pub async fn run_identity_discovery(elder: &ElderApiClient, job: &Value) -> RunOutcome {
    let Ok(job_id) = extract_positive_id(job, "id") else {
        return RunOutcome::failed("discovery job payload missing positive 'id'");
    };
    let Some(provider_type) = non_empty_str(job.get("provider")).map(|s| s.to_ascii_lowercase())
    else {
        return RunOutcome::failed("discovery job payload missing 'provider'");
    };
    let organization_id = job.get("organization_id").and_then(Value::as_i64);

    let span = tracing::info_span!(
        "elder_int_identity.discovery_job",
        job_id = job_id,
        provider = %provider_type
    );

    async move {
        let start_path = format!("/api/v1/integrations/internal/discovery/{job_id}/start");
        let start_resp = match elder.post_empty::<Value>(&start_path).await {
            Ok(resp) => resp,
            Err(err) => {
                tracing::error!(
                    job_id = job_id,
                    provider = %provider_type,
                    error = %err,
                    "Failed marking identity discovery job started"
                );
                return RunOutcome::failed(format!("failed starting discovery job: {err}"));
            }
        };
        let history_id = start_resp.get("history_id").and_then(Value::as_i64);

        let config = extract_config_object(job);
        let batch_res = dispatch_directory_fetch(&provider_type, &config).await;

        let (complete_body, outcome) = match batch_res {
            Ok(batch) => {
                let resources = build_discovery_resources(&provider_type, &batch);
                let dependencies = build_discovery_dependencies(&provider_type, &batch);
                let entities_discovered = resources.len();
                let synced = batch.total_items();
                let outcome = if batch.items_failed > 0 {
                    RunOutcome::partial(
                        synced,
                        batch.items_failed,
                        batch
                            .warning
                            .clone()
                            .unwrap_or_else(|| "partial identity discovery failures".to_string()),
                    )
                } else {
                    RunOutcome::completed(synced)
                };
                let body = json!({
                    "history_id": history_id,
                    "organization_id": organization_id,
                    "status": outcome.status,
                    "resources": resources,
                    "dependencies": dependencies,
                    "summary": {
                        "entities_discovered": entities_discovered,
                        "entities_created": 0,
                        "entities_updated": 0,
                        "memberships_discovered": batch.memberships.len(),
                    },
                    "error_message": outcome.error,
                });
                (body, outcome)
            }
            Err(err) => {
                let msg = err.to_string();
                let body = json!({
                    "history_id": history_id,
                    "organization_id": organization_id,
                    "status": "failed",
                    "resources": [],
                    "dependencies": [],
                    "summary": {
                        "entities_discovered": 0,
                        "entities_created": 0,
                        "entities_updated": 0,
                    },
                    "error_message": msg,
                });
                (body, RunOutcome::failed(msg))
            }
        };

        let complete_path = format!("/api/v1/integrations/internal/discovery/{job_id}/complete");
        if let Err(err) = elder
            .post_json::<_, Value>(&complete_path, &complete_body)
            .await
        {
            tracing::error!(
                job_id = job_id,
                provider = %provider_type,
                error = %err,
                "Failed submitting identity discovery job completion"
            );
            return RunOutcome::failed(format!("failed completing discovery job: {err}"));
        }

        tracing::info!(
            job_id = job_id,
            provider = %provider_type,
            status = %outcome.status,
            items_synced = outcome.items_synced,
            items_failed = outcome.items_failed,
            "Completed identity discovery job"
        );
        outcome
    }
    .instrument(span)
    .await
}

async fn dispatch_directory_fetch(
    provider_type: &str,
    config: &Value,
) -> Result<DirectorySyncBatch, IdentityError> {
    match provider_type {
        "okta" => okta::fetch_directory(config).await,
        "authentik" => authentik::fetch_directory(config).await,
        "ldap" => ldap::fetch_directory(config).await,
        other => Err(IdentityError::Config(format!(
            "unsupported identity provider type {other:?}"
        ))),
    }
}

fn build_discovery_resources(provider: &str, batch: &DirectorySyncBatch) -> Vec<Value> {
    let mut resources = Vec::with_capacity(batch.users.len() + batch.groups.len());
    for user in &batch.users {
        resources.push(json!({
            "resource_type": "iam_user",
            "category": "iam",
            "provider": provider,
            "resource_id": user.external_id,
            "external_id": user.external_id,
            "name": user.discovery_name(provider),
            "metadata": {
                "arn": user.external_id,
                "identity_type": user.identity_type,
                "is_active": user.is_active,
                "auth_provider": provider,
            }
        }));
    }
    for group in &batch.groups {
        resources.push(json!({
            "resource_type": "iam_group",
            "category": "iam",
            "provider": provider,
            "resource_id": group.external_id,
            "external_id": group.external_id,
            "name": group.name,
            "metadata": {
                "description": group.description,
                "is_active": group.is_active,
                "provider": provider,
            }
        }));
    }
    resources
}

fn build_discovery_dependencies(provider: &str, batch: &DirectorySyncBatch) -> Vec<Value> {
    batch
        .memberships
        .iter()
        .map(|mem| {
            json!({
                "source_external_id": mem.user_external_id,
                "target_external_id": mem.group_external_id,
                "edge_type": "member_of",
                "target_kind": "entity",
                "metadata": {
                    "provider": provider,
                }
            })
        })
        .collect()
}

pub(crate) fn extract_positive_id(value: &Value, key: &str) -> Result<i64, IdentityError> {
    let id = value
        .get(key)
        .and_then(Value::as_i64)
        .ok_or_else(|| IdentityError::Config(format!("missing integer '{key}'")))?;
    if id <= 0 {
        return Err(IdentityError::Config(format!(
            "'{key}' must be positive, got {id}"
        )));
    }
    Ok(id)
}

pub(crate) fn extract_config_object(container: &Value) -> Value {
    let raw = container
        .get("config")
        .or_else(|| container.get("config_json"));
    match raw {
        Some(Value::Object(_)) => raw.cloned().unwrap_or_else(|| json!({})),
        Some(Value::String(s)) => serde_json::from_str::<Value>(s)
            .ok()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({})),
        _ => json!({}),
    }
}

pub(crate) fn parse_json_value_or_string(raw: Option<&Value>) -> Option<Value> {
    match raw {
        Some(Value::Object(_)) => raw.cloned(),
        Some(Value::String(s)) => serde_json::from_str::<Value>(s.trim()).ok(),
        _ => None,
    }
}

pub(crate) fn non_empty_str(val: Option<&Value>) -> Option<String> {
    val.and_then(Value::as_str).and_then(|s| {
        let trimmed = s.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

pub(crate) fn first_non_empty(obj: &Value, keys: &[&str]) -> Option<String> {
    for key in keys {
        if let Some(s) = non_empty_str(obj.get(*key)) {
            return Some(s);
        }
    }
    None
}

pub(crate) fn validate_http_url(raw: &str, field: &str) -> Result<url::Url, IdentityError> {
    let trimmed = raw.trim().trim_end_matches('/');
    if !trimmed.starts_with("http://") && !trimmed.starts_with("https://") {
        return Err(IdentityError::Config(format!(
            "{field} must start with http:// or https://"
        )));
    }
    let parsed = url::Url::parse(trimmed)
        .map_err(|e| IdentityError::Config(format!("invalid {field}: {e}")))?;
    if parsed.host_str().is_none() {
        return Err(IdentityError::Config(format!("{field} missing host")));
    }
    Ok(parsed)
}

pub(crate) fn build_rustls_http_client(
    verify_ssl: bool,
    user_agent: &str,
) -> Result<reqwest::Client, IdentityError> {
    reqwest::Client::builder()
        .connect_timeout(DEFAULT_HTTP_CONNECT_TIMEOUT)
        .timeout(DEFAULT_HTTP_REQUEST_TIMEOUT)
        .danger_accept_invalid_certs(!verify_ssl)
        .user_agent(user_agent)
        .build()
        .map_err(|e| IdentityError::Upstream(format!("failed building TLS HTTP client: {e}")))
}
