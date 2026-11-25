//! Typed client and models for Elder Runs serverless functions (`/api/v1/runs`).
//!
//! Exposes CRUD, invocation, and execution-inspection operations alongside
//! S3-compatible artifact storage metadata (`storage_backend`, `storage_key`,
//! `code_sha256`).

use std::collections::BTreeMap;
use std::fmt;

use percent_encoding::{utf8_percent_encode, AsciiSet, NON_ALPHANUMERIC};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::catalog::HttpMethod;
use crate::client::ElderClient;
use crate::error::{ElderError, Result};

/// RFC 3986 unreserved characters (`ALPHA / DIGIT / "-" / "." / "_" / "~"`).
const PATH_SEGMENT_ENCODE_SET: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'-')
    .remove(b'.')
    .remove(b'_')
    .remove(b'~');

/// Paginated response envelope returned by Elder Runs list endpoints.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunPage<T> {
    /// Page items.
    pub items: Vec<T>,
    /// Total matching items across all pages.
    pub total: u64,
    /// Current 1-based page number.
    pub page: u32,
    /// Page size.
    pub per_page: u32,
    /// Total number of pages.
    pub pages: u32,
}

/// Serverless function definition returned by `/api/v1/runs`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunFunction {
    /// Numeric function identifier.
    pub id: i64,
    /// Owning tenant identifier.
    #[serde(default)]
    pub tenant_id: Option<i64>,
    /// Universal Village ID (when minted).
    #[serde(default)]
    pub village_id: Option<String>,
    /// Human-readable function name.
    pub name: String,
    /// Optional function description.
    #[serde(default)]
    pub description: Option<String>,
    /// Execution runtime (`nodejs`, `rust`, `python`, `golang`, `ruby`).
    pub runtime: String,
    /// Function source code.
    #[serde(default)]
    pub code: String,
    /// Optional entrypoint symbol or command.
    #[serde(default)]
    pub entrypoint: Option<String>,
    /// Execution timeout in seconds.
    #[serde(default)]
    pub timeout_seconds: Option<u32>,
    /// Memory limit in MiB.
    #[serde(default)]
    pub memory_mb: Option<u32>,
    /// Environment variables injected at execution time.
    #[serde(default)]
    pub env_vars: BTreeMap<String, Value>,
    /// Optional cron expression for scheduled invocations.
    #[serde(default)]
    pub schedule_cron: Option<String>,
    /// Kubernetes namespace where executions run.
    #[serde(default)]
    pub execution_namespace: Option<String>,
    /// Optional target worker cluster selector.
    #[serde(default)]
    pub worker_cluster: Option<String>,
    /// Whether the function is enabled for invocation.
    #[serde(default)]
    pub is_enabled: bool,
    /// Creator identity ID.
    #[serde(default)]
    pub created_by_identity_id: Option<i64>,
    /// Creation timestamp (ISO-8601 UTC).
    #[serde(default)]
    pub created_at: Option<String>,
    /// Last modification timestamp (ISO-8601 UTC).
    #[serde(default)]
    pub updated_at: Option<String>,
    /// Artifact storage backend (for example `"db"` or `"s3"`).
    #[serde(default)]
    pub storage_backend: Option<String>,
    /// S3-compatible object key storing the function artifact, when applicable.
    #[serde(default)]
    pub storage_key: Option<String>,
    /// SHA-256 hex digest of the stored function source/artifact.
    #[serde(default)]
    pub code_sha256: Option<String>,
    /// Latest immutable version number (present on create/get/update).
    #[serde(default)]
    pub latest_version: Option<u32>,
    /// Recent execution records (present on `GET /api/v1/runs/{func_id}`).
    #[serde(default)]
    pub recent_executions: Option<Vec<RunExecution>>,
}

/// Execution record returned by Elder Runs invocation and execution endpoints.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunExecution {
    /// Numeric execution row ID.
    #[serde(default)]
    pub id: Option<i64>,
    /// Owning tenant identifier.
    #[serde(default)]
    pub tenant_id: Option<i64>,
    /// Unique UUID string identifying this execution.
    pub execution_id: String,
    /// Target function numeric ID.
    pub function_id: i64,
    /// Execution status (`pending`, `running`, `completed`, `failed`, `timed_out`).
    pub status: String,
    /// Trigger source (`manual`, `schedule`, `stream`).
    #[serde(default)]
    pub trigger_type: Option<String>,
    /// Identity ID that triggered the execution, if any.
    #[serde(default)]
    pub triggered_by_identity_id: Option<i64>,
    /// Worker instance that claimed/ran this execution.
    #[serde(default)]
    pub worker_id: Option<String>,
    /// JSON input payload passed to the function.
    #[serde(default)]
    pub input_data: Option<Value>,
    /// JSON output payload returned by the function.
    #[serde(default)]
    pub output_data: Option<Value>,
    /// Captured standard output.
    #[serde(default)]
    pub stdout_log: Option<String>,
    /// Captured standard error.
    #[serde(default)]
    pub stderr_log: Option<String>,
    /// Process exit code.
    #[serde(default)]
    pub exit_code: Option<i32>,
    /// Error summary when execution failed or timed out.
    #[serde(default)]
    pub error_message: Option<String>,
    /// Execution start timestamp (ISO-8601 UTC).
    #[serde(default)]
    pub started_at: Option<String>,
    /// Execution completion timestamp (ISO-8601 UTC).
    #[serde(default)]
    pub completed_at: Option<String>,
    /// Wall-clock duration in milliseconds.
    #[serde(default)]
    pub duration_ms: Option<i64>,
    /// Queue creation timestamp (ISO-8601 UTC).
    #[serde(default)]
    pub created_at: Option<String>,
}

/// Query filters for `GET /api/v1/runs`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ListRunFunctionsParams {
    /// 1-based page number.
    pub page: Option<u32>,
    /// Items per page.
    pub per_page: Option<u32>,
    /// Filter by runtime (`nodejs`, `rust`, `python`, `golang`, `ruby`).
    pub runtime: Option<String>,
    /// Substring search over function name and description.
    pub search: Option<String>,
    /// Filter by target worker cluster.
    pub worker_cluster: Option<String>,
    /// Filter by enabled state.
    pub is_enabled: Option<bool>,
}

impl ListRunFunctionsParams {
    /// Serialize populated filters into query-string key/value pairs.
    pub fn to_query_pairs(&self) -> Vec<(String, String)> {
        let mut pairs = Vec::new();
        if let Some(page) = self.page {
            pairs.push(("page".into(), page.to_string()));
        }
        if let Some(per_page) = self.per_page {
            pairs.push(("per_page".into(), per_page.to_string()));
        }
        if let Some(ref runtime) = self.runtime {
            if !runtime.trim().is_empty() {
                pairs.push(("runtime".into(), runtime.trim().to_string()));
            }
        }
        if let Some(ref search) = self.search {
            if !search.trim().is_empty() {
                pairs.push(("search".into(), search.trim().to_string()));
            }
        }
        if let Some(ref cluster) = self.worker_cluster {
            if !cluster.trim().is_empty() {
                pairs.push(("worker_cluster".into(), cluster.trim().to_string()));
            }
        }
        if let Some(enabled) = self.is_enabled {
            pairs.push(("is_enabled".into(), enabled.to_string()));
        }
        pairs
    }
}

/// Request payload for `POST /api/v1/runs`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CreateRunFunctionRequest {
    /// Function name (required).
    pub name: String,
    /// Execution runtime (`nodejs`, `rust`, `python`, `golang`, `ruby`).
    pub runtime: String,
    /// Function source code (required).
    pub code: String,
    /// Optional function description.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    /// Optional entrypoint symbol or command.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub entrypoint: Option<String>,
    /// Execution timeout in seconds (`1..=3600`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeout_seconds: Option<u32>,
    /// Memory limit in MiB (`32..=16384`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_mb: Option<u32>,
    /// Environment variables injected at runtime.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub env_vars: Option<BTreeMap<String, Value>>,
    /// Optional cron schedule expression.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub schedule_cron: Option<String>,
    /// Optional Kubernetes execution namespace.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub execution_namespace: Option<String>,
    /// Optional target worker cluster.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub worker_cluster: Option<String>,
    /// Whether the function starts enabled (defaults to `true` server-side).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub is_enabled: Option<bool>,
}

impl CreateRunFunctionRequest {
    /// Construct a minimal [`CreateRunFunctionRequest`] with the required fields.
    pub fn new(
        name: impl Into<String>,
        runtime: impl Into<String>,
        code: impl Into<String>,
    ) -> Self {
        Self {
            name: name.into(),
            runtime: runtime.into(),
            code: code.into(),
            description: None,
            entrypoint: None,
            timeout_seconds: None,
            memory_mb: None,
            env_vars: None,
            schedule_cron: None,
            execution_namespace: None,
            worker_cluster: None,
            is_enabled: None,
        }
    }
}

/// Request payload for `PUT /api/v1/runs/{func_id}`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct UpdateRunFunctionRequest {
    /// Updated function name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// Updated description.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    /// Updated runtime.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime: Option<String>,
    /// Updated source code (creates a new version when changed).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
    /// Updated entrypoint.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub entrypoint: Option<String>,
    /// Updated timeout in seconds (`1..=3600`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeout_seconds: Option<u32>,
    /// Updated memory limit in MiB (`32..=16384`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_mb: Option<u32>,
    /// Replacement environment variable map.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub env_vars: Option<BTreeMap<String, Value>>,
    /// Updated cron schedule.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub schedule_cron: Option<String>,
    /// Updated execution namespace.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub execution_namespace: Option<String>,
    /// Updated target worker cluster.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub worker_cluster: Option<String>,
    /// Updated enabled state.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub is_enabled: Option<bool>,
}

/// Request payload for `POST /api/v1/runs/{func_id}/invoke`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct InvokeRunRequest {
    /// Optional JSON input payload passed to the function.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_data: Option<Value>,
    /// Optional trigger type (`manual`, `schedule`, `stream`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trigger_type: Option<String>,
}

/// Response returned by `POST /api/v1/runs/{func_id}/invoke` (`202 Accepted`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InvokeRunResponse {
    /// Unique UUID of the queued execution.
    pub execution_id: String,
    /// Initial execution record (`status = "pending"`).
    pub execution: RunExecution,
}

/// Query filters for `GET /api/v1/runs/executions`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ListExecutionsParams {
    /// 1-based page number.
    pub page: Option<u32>,
    /// Items per page.
    pub per_page: Option<u32>,
    /// Filter by owning function numeric ID.
    pub function_id: Option<i64>,
    /// Filter by status (`pending`, `running`, `completed`, `failed`, `timed_out`).
    pub status: Option<String>,
    /// Filter by trigger type (`manual`, `schedule`, `stream`).
    pub trigger_type: Option<String>,
}

impl ListExecutionsParams {
    /// Serialize populated filters into query-string key/value pairs.
    pub fn to_query_pairs(&self) -> Vec<(String, String)> {
        let mut pairs = Vec::new();
        if let Some(page) = self.page {
            pairs.push(("page".into(), page.to_string()));
        }
        if let Some(per_page) = self.per_page {
            pairs.push(("per_page".into(), per_page.to_string()));
        }
        if let Some(function_id) = self.function_id {
            pairs.push(("function_id".into(), function_id.to_string()));
        }
        if let Some(ref status) = self.status {
            if !status.trim().is_empty() {
                pairs.push(("status".into(), status.trim().to_string()));
            }
        }
        if let Some(ref trigger_type) = self.trigger_type {
            if !trigger_type.trim().is_empty() {
                pairs.push(("trigger_type".into(), trigger_type.trim().to_string()));
            }
        }
        pairs
    }
}

/// Typed client for Elder Runs serverless functions (`/api/v1/runs`).
#[derive(Clone)]
pub struct RunsClient<'a> {
    client: &'a ElderClient,
}

impl<'a> fmt::Debug for RunsClient<'a> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RunsClient")
            .field("base_url", &self.client.base_url())
            .finish()
    }
}

impl<'a> RunsClient<'a> {
    /// Construct a [`RunsClient`] bound to `client`.
    pub(crate) fn new(client: &'a ElderClient) -> Self {
        Self { client }
    }

    /// List serverless functions via `GET /api/v1/runs`.
    pub async fn list(&self, params: &ListRunFunctionsParams) -> Result<RunPage<RunFunction>> {
        let query = params.to_query_pairs();
        let query_ref = if query.is_empty() {
            None
        } else {
            Some(query.as_slice())
        };
        self.client
            .request_typed(HttpMethod::Get, "/api/v1/runs", query_ref, None)
            .await
    }

    /// Create a serverless function via `POST /api/v1/runs`.
    pub async fn create(&self, req: &CreateRunFunctionRequest) -> Result<RunFunction> {
        if req.name.trim().is_empty() {
            return Err(ElderError::Validation("name must not be empty".into()));
        }
        if req.runtime.trim().is_empty() {
            return Err(ElderError::Validation("runtime must not be empty".into()));
        }
        if req.code.trim().is_empty() {
            return Err(ElderError::Validation("code must not be empty".into()));
        }
        let body = serde_json::to_value(req)?;
        self.client
            .request_typed(HttpMethod::Post, "/api/v1/runs", None, Some(&body))
            .await
    }

    /// Get a single serverless function (including `latest_version`,
    /// `recent_executions`, and artifact storage metadata) via `GET /api/v1/runs/{func_id}`.
    pub async fn get(&self, func_id: i64) -> Result<RunFunction> {
        let path = format!("/api/v1/runs/{func_id}");
        self.client
            .request_typed(HttpMethod::Get, &path, None, None)
            .await
    }

    /// Update a serverless function via `PUT /api/v1/runs/{func_id}`.
    pub async fn update(
        &self,
        func_id: i64,
        req: &UpdateRunFunctionRequest,
    ) -> Result<RunFunction> {
        let path = format!("/api/v1/runs/{func_id}");
        let body = serde_json::to_value(req)?;
        self.client
            .request_typed(HttpMethod::Put, &path, None, Some(&body))
            .await
    }

    /// Delete a serverless function via `DELETE /api/v1/runs/{func_id}`.
    pub async fn delete(&self, func_id: i64) -> Result<()> {
        let path = format!("/api/v1/runs/{func_id}");
        self.client
            .request(HttpMethod::Delete, &path, None, None)
            .await?;
        Ok(())
    }

    /// Queue a function invocation via `POST /api/v1/runs/{func_id}/invoke`.
    pub async fn invoke(&self, func_id: i64, req: &InvokeRunRequest) -> Result<InvokeRunResponse> {
        let path = format!("/api/v1/runs/{func_id}/invoke");
        let body = serde_json::to_value(req)?;
        self.client
            .request_typed(HttpMethod::Post, &path, None, Some(&body))
            .await
    }

    /// List executions for the current tenant via `GET /api/v1/runs/executions`.
    pub async fn list_executions(
        &self,
        params: &ListExecutionsParams,
    ) -> Result<RunPage<RunExecution>> {
        let query = params.to_query_pairs();
        let query_ref = if query.is_empty() {
            None
        } else {
            Some(query.as_slice())
        };
        self.client
            .request_typed(HttpMethod::Get, "/api/v1/runs/executions", query_ref, None)
            .await
    }

    /// List executions filtered to a specific function via
    /// `GET /api/v1/runs/executions?function_id={func_id}`.
    pub async fn list_function_executions(
        &self,
        func_id: i64,
        params: &ListExecutionsParams,
    ) -> Result<RunPage<RunExecution>> {
        let mut scoped = params.clone();
        scoped.function_id = Some(func_id);
        self.list_executions(&scoped).await
    }

    /// Fetch a single execution record via `GET /api/v1/runs/executions/{execution_id}`.
    pub async fn get_execution(&self, execution_id: &str) -> Result<RunExecution> {
        let trimmed = execution_id.trim();
        if trimmed.is_empty() {
            return Err(ElderError::Validation(
                "execution_id must not be empty".into(),
            ));
        }
        let encoded = utf8_percent_encode(trimmed, PATH_SEGMENT_ENCODE_SET).to_string();
        let path = format!("/api/v1/runs/executions/{encoded}");
        self.client
            .request_typed(HttpMethod::Get, &path, None, None)
            .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{ClientConfig, RetryConfig};
    use serde_json::json;
    use std::sync::Arc;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;
    use tokio::sync::Mutex;

    async fn spawn_mock_server(
        responses: Vec<(u16, String)>,
    ) -> (String, Arc<Mutex<Vec<(String, String, String)>>>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        let recorded = Arc::new(Mutex::new(Vec::new()));
        let rec_bg = Arc::clone(&recorded);

        tokio::spawn(async move {
            for (status, body) in responses {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let mut buf = vec![0u8; 16384];
                let n = sock.read(&mut buf).await.unwrap_or(0);
                let raw = String::from_utf8_lossy(&buf[..n]);
                let split = raw.find("\r\n\r\n").unwrap_or(raw.len());
                let first_line = raw[..split].lines().next().unwrap_or("");
                let mut parts = first_line.split_whitespace();
                let method = parts.next().unwrap_or("").to_string();
                let target = parts.next().unwrap_or("").to_string();
                let req_body = if split + 4 <= raw.len() {
                    raw[split + 4..].to_string()
                } else {
                    String::new()
                };
                rec_bg.lock().await.push((method, target, req_body));

                let wire = if status == 204 {
                    "HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                        .to_string()
                } else {
                    format!(
                        "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                        body.len()
                    )
                };
                let _ = sock.write_all(wire.as_bytes()).await;
                let _ = sock.shutdown().await;
            }
        });

        (format!("http://{addr}"), recorded)
    }

    fn sample_function_json(id: i64, with_details: bool) -> Value {
        let mut obj = json!({
            "id": id,
            "tenant_id": 7,
            "village_id": format!("RUN-{id}"),
            "name": "resize-avatar",
            "description": "Resizes uploaded avatars",
            "runtime": "python",
            "code": "def handler(event): return {'ok': True}",
            "entrypoint": "handler",
            "timeout_seconds": 60,
            "memory_mb": 256,
            "env_vars": {"BUCKET": "avatars"},
            "schedule_cron": null,
            "execution_namespace": "elder-runs",
            "worker_cluster": "dal2-alpha",
            "is_enabled": true,
            "created_by_identity_id": 11,
            "created_at": "2026-11-20T12:00:00+00:00",
            "updated_at": "2026-11-20T12:05:00+00:00",
            "storage_backend": "s3",
            "storage_key": format!("runs/7/{id}/v2/code.py"),
            "code_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        });
        if with_details {
            obj["latest_version"] = json!(2);
            obj["recent_executions"] = json!([sample_execution_json(id, "exec-001", "completed")]);
        }
        obj
    }

    fn sample_execution_json(func_id: i64, exec_id: &str, status: &str) -> Value {
        json!({
            "id": 99,
            "tenant_id": 7,
            "execution_id": exec_id,
            "function_id": func_id,
            "status": status,
            "trigger_type": "manual",
            "triggered_by_identity_id": 11,
            "worker_id": "svc-runworker-1",
            "input_data": {"width": 128},
            "output_data": {"ok": true},
            "stdout_log": "resized\n",
            "stderr_log": "",
            "exit_code": 0,
            "error_message": null,
            "started_at": "2026-11-20T12:01:00+00:00",
            "completed_at": "2026-11-20T12:01:01+00:00",
            "duration_ms": 420,
            "created_at": "2026-11-20T12:00:59+00:00"
        })
    }

    #[tokio::test]
    async fn runs_crud_invoke_and_execution_methods_round_trip_with_s3_metadata() {
        let list_body = json!({
            "items": [sample_function_json(42, false)],
            "total": 1,
            "page": 1,
            "per_page": 20,
            "pages": 1
        })
        .to_string();
        let create_body = sample_function_json(42, false).to_string();
        let get_body = sample_function_json(42, true).to_string();
        let update_body = sample_function_json(42, false).to_string();
        let invoke_body = json!({
            "execution_id": "exec-999",
            "execution": sample_execution_json(42, "exec-999", "pending")
        })
        .to_string();
        let execs_body = json!({
            "items": [sample_execution_json(42, "exec-999", "completed")],
            "total": 1,
            "page": 1,
            "per_page": 25,
            "pages": 1
        })
        .to_string();
        let exec_body = sample_execution_json(42, "exec-999", "completed").to_string();

        let (url, recorded) = spawn_mock_server(vec![
            (200, list_body),
            (201, create_body),
            (200, get_body),
            (200, update_body),
            (204, String::new()),
            (202, invoke_body),
            (200, execs_body),
            (200, exec_body),
        ])
        .await;

        let client = ElderClient::new(
            ClientConfig::new(url)
                .unwrap()
                .with_bearer("tok-runs")
                .with_retry(RetryConfig::disabled()),
        )
        .unwrap();

        let listed = client
            .runs()
            .list(&ListRunFunctionsParams {
                page: Some(1),
                per_page: Some(20),
                runtime: Some("python".into()),
                search: Some("avatar".into()),
                worker_cluster: Some("dal2-alpha".into()),
                is_enabled: Some(true),
            })
            .await
            .expect("list runs");
        assert_eq!(listed.total, 1);
        assert_eq!(listed.items[0].storage_backend.as_deref(), Some("s3"));
        assert_eq!(
            listed.items[0].storage_key.as_deref(),
            Some("runs/7/42/v2/code.py")
        );
        assert_eq!(
            listed.items[0].code_sha256.as_deref(),
            Some("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        );

        let mut env_vars = BTreeMap::new();
        env_vars.insert("BUCKET".into(), json!("avatars"));
        let mut create_req = CreateRunFunctionRequest::new(
            "resize-avatar",
            "python",
            "def handler(event): return {'ok': True}",
        );
        create_req.description = Some("Resizes uploaded avatars".into());
        create_req.entrypoint = Some("handler".into());
        create_req.timeout_seconds = Some(60);
        create_req.memory_mb = Some(256);
        create_req.env_vars = Some(env_vars);
        create_req.worker_cluster = Some("dal2-alpha".into());
        create_req.is_enabled = Some(true);

        let created = client.runs().create(&create_req).await.expect("create run");
        assert_eq!(created.id, 42);
        assert_eq!(created.storage_backend.as_deref(), Some("s3"));

        let fetched = client.runs().get(42).await.expect("get run");
        assert_eq!(fetched.latest_version, Some(2));
        assert_eq!(fetched.recent_executions.as_ref().map(Vec::len), Some(1));
        assert_eq!(fetched.storage_key.as_deref(), Some("runs/7/42/v2/code.py"));

        let updated = client
            .runs()
            .update(
                42,
                &UpdateRunFunctionRequest {
                    timeout_seconds: Some(120),
                    is_enabled: Some(false),
                    ..Default::default()
                },
            )
            .await
            .expect("update run");
        assert_eq!(updated.id, 42);

        client.runs().delete(42).await.expect("delete run");

        let invoked = client
            .runs()
            .invoke(
                42,
                &InvokeRunRequest {
                    input_data: Some(json!({"width": 128})),
                    trigger_type: Some("manual".into()),
                },
            )
            .await
            .expect("invoke run");
        assert_eq!(invoked.execution_id, "exec-999");
        assert_eq!(invoked.execution.status, "pending");

        let execs = client
            .runs()
            .list_function_executions(
                42,
                &ListExecutionsParams {
                    page: Some(1),
                    per_page: Some(25),
                    status: Some("completed".into()),
                    ..Default::default()
                },
            )
            .await
            .expect("list executions");
        assert_eq!(execs.items.len(), 1);
        assert_eq!(execs.items[0].execution_id, "exec-999");

        let exec = client
            .runs()
            .get_execution("exec-999")
            .await
            .expect("get execution");
        assert_eq!(exec.duration_ms, Some(420));
        assert_eq!(exec.exit_code, Some(0));

        let reqs = recorded.lock().await;
        assert_eq!(reqs.len(), 8);
        assert_eq!(reqs[0].0, "GET");
        assert_eq!(
            reqs[0].1,
            "/api/v1/runs?page=1&per_page=20&runtime=python&search=avatar&worker_cluster=dal2-alpha&is_enabled=true"
        );
        assert_eq!(reqs[1].0, "POST");
        assert_eq!(reqs[1].1, "/api/v1/runs");
        let create_body_json: Value = serde_json::from_str(&reqs[1].2).unwrap();
        assert_eq!(create_body_json["name"], "resize-avatar");
        assert_eq!(create_body_json["runtime"], "python");
        assert_eq!(create_body_json["env_vars"]["BUCKET"], "avatars");
        assert_eq!(reqs[2].0, "GET");
        assert_eq!(reqs[2].1, "/api/v1/runs/42");
        assert_eq!(reqs[3].0, "PUT");
        assert_eq!(reqs[3].1, "/api/v1/runs/42");
        let update_body_json: Value = serde_json::from_str(&reqs[3].2).unwrap();
        assert_eq!(update_body_json["timeout_seconds"], 120);
        assert_eq!(update_body_json["is_enabled"], false);
        assert!(update_body_json.get("name").is_none());
        assert_eq!(reqs[4].0, "DELETE");
        assert_eq!(reqs[4].1, "/api/v1/runs/42");
        assert_eq!(reqs[5].0, "POST");
        assert_eq!(reqs[5].1, "/api/v1/runs/42/invoke");
        let invoke_body_json: Value = serde_json::from_str(&reqs[5].2).unwrap();
        assert_eq!(invoke_body_json["input_data"]["width"], 128);
        assert_eq!(invoke_body_json["trigger_type"], "manual");
        assert_eq!(reqs[6].0, "GET");
        assert_eq!(
            reqs[6].1,
            "/api/v1/runs/executions?page=1&per_page=25&function_id=42&status=completed"
        );
        assert_eq!(reqs[7].0, "GET");
        assert_eq!(reqs[7].1, "/api/v1/runs/executions/exec-999");
    }

    #[tokio::test]
    async fn runs_validates_required_inputs_client_side() {
        let client = ElderClient::new(ClientConfig::new("http://127.0.0.1:5000").unwrap()).unwrap();

        let err = client
            .runs()
            .create(&CreateRunFunctionRequest::new("", "python", "print(1)"))
            .await
            .unwrap_err();
        assert!(matches!(err, ElderError::Validation(_)));

        let err = client
            .runs()
            .create(&CreateRunFunctionRequest::new("fn", "  ", "print(1)"))
            .await
            .unwrap_err();
        assert!(matches!(err, ElderError::Validation(_)));

        let err = client
            .runs()
            .create(&CreateRunFunctionRequest::new("fn", "python", ""))
            .await
            .unwrap_err();
        assert!(matches!(err, ElderError::Validation(_)));

        let err = client.runs().get_execution("   ").await.unwrap_err();
        assert!(matches!(err, ElderError::Validation(_)));
    }

    #[test]
    fn deserializes_function_without_optional_s3_fields() {
        let raw = json!({
            "id": 1,
            "name": "hello",
            "runtime": "nodejs",
            "code": "console.log('hi')",
            "is_enabled": true
        });
        let func: RunFunction = serde_json::from_value(raw).expect("deserialize");
        assert_eq!(func.id, 1);
        assert_eq!(func.storage_backend, None);
        assert_eq!(func.storage_key, None);
        assert_eq!(func.code_sha256, None);
        assert_eq!(func.latest_version, None);
        assert_eq!(func.recent_executions, None);
    }
}
