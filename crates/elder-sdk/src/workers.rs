//! Typed client and models for the Remote Workers Fleet API (`/api/v1/admin/workers`).
//!
//! Supports listing, inspecting, onboarding, updating, rotating credentials for,
//! offboarding, and recording heartbeats from remote `runworker` and `scanner`
//! worker instances.

use std::collections::BTreeMap;
use std::fmt;

use percent_encoding::{utf8_percent_encode, AsciiSet, NON_ALPHANUMERIC};
use serde::de::{self, Deserializer};
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

fn deserialize_id_string<'de, D>(deserializer: D) -> std::result::Result<String, D::Error>
where
    D: Deserializer<'de>,
{
    match Value::deserialize(deserializer)? {
        Value::String(s) => Ok(s),
        Value::Number(n) => Ok(n.to_string()),
        other => Err(de::Error::custom(format!(
            "expected string or integer worker id, got {other}"
        ))),
    }
}

fn deserialize_opt_id_string<'de, D>(
    deserializer: D,
) -> std::result::Result<Option<String>, D::Error>
where
    D: Deserializer<'de>,
{
    match Option::<Value>::deserialize(deserializer)? {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s)),
        Some(Value::Number(n)) => Ok(Some(n.to_string())),
        Some(other) => Err(de::Error::custom(format!(
            "expected string, integer, or null worker id, got {other}"
        ))),
    }
}

fn encode_worker_id(worker_id: &str) -> Result<String> {
    let trimmed = worker_id.trim();
    if trimmed.is_empty() {
        return Err(ElderError::Validation("worker_id must not be empty".into()));
    }
    Ok(utf8_percent_encode(trimmed, PATH_SEGMENT_ENCODE_SET).to_string())
}

/// Registered remote worker record returned by `/api/v1/admin/workers`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RemoteWorker {
    /// Worker identifier (numeric ID or UUID serialized as string).
    #[serde(deserialize_with = "deserialize_id_string")]
    pub id: String,
    /// Optional public UUID when distinct from `id`.
    #[serde(default)]
    pub public_id: Option<String>,
    /// Owning tenant identifier.
    #[serde(default)]
    pub tenant_id: Option<i64>,
    /// Human-readable worker name.
    pub name: String,
    /// Worker type (`runworker` or `scanner`).
    pub worker_type: String,
    /// Optional target cluster identifier.
    #[serde(default)]
    pub cluster: Option<String>,
    /// Computed fleet status (`online`, `stale`, `offline`, `revoked`).
    #[serde(default)]
    pub status: String,
    /// Whether the worker is enabled for work assignment.
    #[serde(default)]
    pub is_enabled: bool,
    /// Masked authentication token prefix (never the full secret).
    #[serde(default)]
    pub token_prefix: Option<String>,
    /// Last reported worker hostname or pod name.
    #[serde(default)]
    pub hostname: Option<String>,
    /// Last reported worker binary or image version.
    #[serde(default)]
    pub version: Option<String>,
    /// Last heartbeat timestamp (ISO-8601 UTC).
    #[serde(default)]
    pub last_heartbeat_at: Option<String>,
    /// Poll interval in seconds.
    #[serde(default)]
    pub poll_interval_seconds: Option<u32>,
    /// Progress reporting interval in seconds.
    #[serde(default)]
    pub progress_interval_seconds: Option<u32>,
    /// Maximum concurrent jobs or scans.
    #[serde(default)]
    pub max_concurrent: Option<u32>,
    /// Optional worker configuration map.
    #[serde(default)]
    pub config: BTreeMap<String, Value>,
    /// Optional worker capabilities payload.
    #[serde(default)]
    pub capabilities: Option<Value>,
    /// Creation timestamp (ISO-8601 UTC).
    #[serde(default)]
    pub created_at: Option<String>,
    /// Last modification timestamp (ISO-8601 UTC).
    #[serde(default)]
    pub updated_at: Option<String>,
}

/// Paginated list response returned by `GET /api/v1/admin/workers`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RemoteWorkerListResponse {
    /// Page of registered remote workers.
    #[serde(default)]
    pub items: Vec<RemoteWorker>,
    /// Total matching workers across all pages.
    #[serde(default)]
    pub total: u64,
    /// Optional 1-based page number.
    #[serde(default)]
    pub page: Option<u32>,
    /// Optional items per page.
    #[serde(default)]
    pub per_page: Option<u32>,
    /// Optional total number of pages.
    #[serde(default)]
    pub pages: Option<u32>,
}

/// Query filters for `GET /api/v1/admin/workers`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ListRemoteWorkersParams {
    /// Filter by worker type (`runworker`, `scanner`).
    pub worker_type: Option<String>,
    /// Filter by target cluster.
    pub cluster: Option<String>,
    /// Filter by computed fleet status (`online`, `stale`, `offline`, `revoked`).
    pub status: Option<String>,
    /// Optional 1-based page number.
    pub page: Option<u32>,
    /// Optional items per page.
    pub per_page: Option<u32>,
}

impl ListRemoteWorkersParams {
    /// Serialize populated filters into query-string key/value pairs.
    pub fn to_query_pairs(&self) -> Vec<(String, String)> {
        let mut pairs = Vec::new();
        if let Some(ref worker_type) = self.worker_type {
            if !worker_type.trim().is_empty() {
                pairs.push(("worker_type".into(), worker_type.trim().to_string()));
            }
        }
        if let Some(ref cluster) = self.cluster {
            if !cluster.trim().is_empty() {
                pairs.push(("cluster".into(), cluster.trim().to_string()));
            }
        }
        if let Some(ref status) = self.status {
            if !status.trim().is_empty() {
                pairs.push(("status".into(), status.trim().to_string()));
            }
        }
        if let Some(page) = self.page {
            pairs.push(("page".into(), page.to_string()));
        }
        if let Some(per_page) = self.per_page {
            pairs.push(("per_page".into(), per_page.to_string()));
        }
        pairs
    }
}

/// Request payload for `POST /api/v1/admin/workers`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OnboardRemoteWorkerRequest {
    /// Human-readable worker name (required).
    pub name: String,
    /// Worker type (`runworker` or `scanner`, required).
    pub worker_type: String,
    /// Optional target cluster identifier.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cluster: Option<String>,
    /// Optional poll interval in seconds.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub poll_interval_seconds: Option<u32>,
    /// Optional progress reporting interval in seconds.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub progress_interval_seconds: Option<u32>,
    /// Optional maximum concurrent jobs or scans.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_concurrent: Option<u32>,
    /// Optional worker configuration map.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub config: Option<BTreeMap<String, Value>>,
    /// Optional worker capabilities payload.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub capabilities: Option<Value>,
}

impl OnboardRemoteWorkerRequest {
    /// Construct a minimal [`OnboardRemoteWorkerRequest`] with required fields.
    pub fn new(name: impl Into<String>, worker_type: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            worker_type: worker_type.into(),
            cluster: None,
            poll_interval_seconds: None,
            progress_interval_seconds: None,
            max_concurrent: None,
            config: None,
            capabilities: None,
        }
    }
}

/// Response returned by `POST /api/v1/admin/workers` containing the newly
/// onboarded worker record and its one-time authentication token.
#[derive(Clone, PartialEq, Serialize, Deserialize)]
pub struct OnboardRemoteWorkerResponse {
    /// Newly created remote worker record.
    pub worker: RemoteWorker,
    /// One-time plaintext worker authentication token.
    pub token: String,
    /// Masked token prefix (when returned at top level).
    #[serde(default)]
    pub token_prefix: Option<String>,
    /// Optional Helm values snippet for deploying the worker.
    #[serde(default)]
    pub helm_values: Option<String>,
    /// Optional container environment variable map for deploying the worker.
    #[serde(default)]
    pub env_vars: Option<BTreeMap<String, String>>,
}

impl fmt::Debug for OnboardRemoteWorkerResponse {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("OnboardRemoteWorkerResponse")
            .field("worker", &self.worker)
            .field("token", &"[REDACTED]")
            .field("token_prefix", &self.token_prefix)
            .field("has_helm_values", &self.helm_values.is_some())
            .field("has_env_vars", &self.env_vars.is_some())
            .finish()
    }
}

/// Request payload for `PUT /api/v1/admin/workers/{worker_id}`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct UpdateRemoteWorkerRequest {
    /// Updated worker name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// Updated cluster identifier.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cluster: Option<String>,
    /// Enable or disable the worker.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub is_enabled: Option<bool>,
    /// Updated poll interval in seconds.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub poll_interval_seconds: Option<u32>,
    /// Updated progress reporting interval in seconds.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub progress_interval_seconds: Option<u32>,
    /// Updated maximum concurrent jobs or scans.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_concurrent: Option<u32>,
    /// Updated worker configuration map.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub config: Option<BTreeMap<String, Value>>,
    /// Updated worker capabilities payload.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub capabilities: Option<Value>,
}

/// Response returned by `POST /api/v1/admin/workers/{worker_id}/rotate-token`.
#[derive(Clone, PartialEq, Serialize, Deserialize)]
pub struct RotateTokenResponse {
    /// Updated remote worker record, when returned by the server.
    #[serde(default)]
    pub worker: Option<RemoteWorker>,
    /// Worker identifier, when returned as a top-level field.
    #[serde(default, deserialize_with = "deserialize_opt_id_string")]
    pub worker_id: Option<String>,
    /// Newly issued one-time plaintext worker authentication token.
    pub token: String,
    /// New masked token prefix.
    #[serde(default)]
    pub token_prefix: Option<String>,
}

impl fmt::Debug for RotateTokenResponse {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RotateTokenResponse")
            .field("worker", &self.worker)
            .field("worker_id", &self.worker_id)
            .field("token", &"[REDACTED]")
            .field("token_prefix", &self.token_prefix)
            .finish()
    }
}

/// Request payload for `POST /api/v1/admin/workers/heartbeat`.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct WorkerHeartbeatRequest {
    /// Worker type (`runworker` or `scanner`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub worker_type: Option<String>,
    /// Optional worker identifier.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub worker_id: Option<String>,
    /// Worker hostname or pod name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hostname: Option<String>,
    /// Running worker binary or image version.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
    /// Optional target cluster identifier.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cluster: Option<String>,
    /// Optional operational status hint.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    /// Optional count of active jobs or scans.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub active_jobs: Option<u32>,
    /// Optional worker capabilities payload.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub capabilities: Option<Value>,
    /// Optional free-form telemetry metadata.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<BTreeMap<String, Value>>,
}

/// Typed client for the Remote Workers Fleet API (`/api/v1/admin/workers`).
#[derive(Clone)]
pub struct WorkersClient<'a> {
    client: &'a ElderClient,
}

impl<'a> fmt::Debug for WorkersClient<'a> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WorkersClient")
            .field("base_url", &self.client.base_url())
            .finish()
    }
}

impl<'a> WorkersClient<'a> {
    /// Construct a [`WorkersClient`] bound to `client`.
    pub(crate) fn new(client: &'a ElderClient) -> Self {
        Self { client }
    }

    /// List registered remote workers via `GET /api/v1/admin/workers`.
    pub async fn list(&self, params: &ListRemoteWorkersParams) -> Result<RemoteWorkerListResponse> {
        let query = params.to_query_pairs();
        let query_ref = if query.is_empty() {
            None
        } else {
            Some(query.as_slice())
        };
        self.client
            .request_typed(HttpMethod::Get, "/api/v1/admin/workers", query_ref, None)
            .await
    }

    /// Fetch a single remote worker via `GET /api/v1/admin/workers/{worker_id}`.
    pub async fn get(&self, worker_id: &str) -> Result<RemoteWorker> {
        let encoded = encode_worker_id(worker_id)?;
        let path = format!("/api/v1/admin/workers/{encoded}");
        self.client
            .request_typed(HttpMethod::Get, &path, None, None)
            .await
    }

    /// Onboard a new remote worker via `POST /api/v1/admin/workers`.
    pub async fn onboard(
        &self,
        req: &OnboardRemoteWorkerRequest,
    ) -> Result<OnboardRemoteWorkerResponse> {
        if req.name.trim().is_empty() {
            return Err(ElderError::Validation("name must not be empty".into()));
        }
        if req.worker_type.trim().is_empty() {
            return Err(ElderError::Validation(
                "worker_type must not be empty".into(),
            ));
        }
        let body = serde_json::to_value(req)?;
        self.client
            .request_typed(HttpMethod::Post, "/api/v1/admin/workers", None, Some(&body))
            .await
    }

    /// Update an existing remote worker via `PUT /api/v1/admin/workers/{worker_id}`.
    pub async fn update(
        &self,
        worker_id: &str,
        req: &UpdateRemoteWorkerRequest,
    ) -> Result<RemoteWorker> {
        let encoded = encode_worker_id(worker_id)?;
        let path = format!("/api/v1/admin/workers/{encoded}");
        let body = serde_json::to_value(req)?;
        self.client
            .request_typed(HttpMethod::Put, &path, None, Some(&body))
            .await
    }

    /// Rotate a remote worker's authentication token via
    /// `POST /api/v1/admin/workers/{worker_id}/rotate-token`.
    pub async fn rotate_token(&self, worker_id: &str) -> Result<RotateTokenResponse> {
        let encoded = encode_worker_id(worker_id)?;
        let path = format!("/api/v1/admin/workers/{encoded}/rotate-token");
        self.client
            .request_typed(HttpMethod::Post, &path, None, None)
            .await
    }

    /// Offboard and revoke a remote worker via `DELETE /api/v1/admin/workers/{worker_id}`.
    pub async fn offboard(&self, worker_id: &str) -> Result<()> {
        let encoded = encode_worker_id(worker_id)?;
        let path = format!("/api/v1/admin/workers/{encoded}");
        self.client
            .request(HttpMethod::Delete, &path, None, None)
            .await?;
        Ok(())
    }

    /// Send a worker liveness heartbeat via `POST /api/v1/admin/workers/heartbeat`.
    pub async fn heartbeat(&self, req: &WorkerHeartbeatRequest) -> Result<RemoteWorker> {
        let body = serde_json::to_value(req)?;
        self.client
            .request_typed(
                HttpMethod::Post,
                "/api/v1/admin/workers/heartbeat",
                None,
                Some(&body),
            )
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

    fn sample_worker_json(id: &str, worker_type: &str, status: &str) -> Value {
        json!({
            "id": id,
            "tenant_id": 7,
            "name": "dal2-runworker-01",
            "worker_type": worker_type,
            "cluster": "dal2-alpha",
            "status": status,
            "is_enabled": true,
            "token_prefix": "ewrk_9a8b",
            "hostname": "runworker-pod-01",
            "version": "4.0.12",
            "last_heartbeat_at": "2026-11-23T22:00:00+00:00",
            "poll_interval_seconds": 3,
            "progress_interval_seconds": 15,
            "max_concurrent": 8,
            "created_at": "2026-11-20T12:00:00+00:00",
            "updated_at": "2026-11-23T22:00:00+00:00"
        })
    }

    #[tokio::test]
    async fn workers_fleet_lifecycle_round_trips_all_endpoints() {
        let list_body = json!({
            "items": [sample_worker_json("wrk-01", "runworker", "online")],
            "total": 1,
            "page": 1,
            "per_page": 25,
            "pages": 1
        })
        .to_string();
        let get_body = sample_worker_json("wrk-01", "runworker", "online").to_string();
        let onboard_body = json!({
            "worker": sample_worker_json("wrk-02", "scanner", "offline"),
            "token": "ewrk_SUPER_SECRET_ONBOARD_TOKEN_1234",
            "token_prefix": "ewrk_9a8b"
        })
        .to_string();
        let update_body = json!({
            "id": 42,
            "name": "dal2-runworker-updated",
            "worker_type": "runworker",
            "cluster": "dal2-beta",
            "status": "online",
            "is_enabled": false,
            "token_prefix": "ewrk_9a8b",
            "poll_interval_seconds": 10
        })
        .to_string();
        let rotate_body = json!({
            "worker": sample_worker_json("wrk-01", "runworker", "online"),
            "worker_id": "wrk-01",
            "token": "ewrk_SUPER_SECRET_ROTATED_TOKEN_5678",
            "token_prefix": "ewrk_1c2d"
        })
        .to_string();
        let heartbeat_body = sample_worker_json("wrk-01", "runworker", "online").to_string();

        let (url, recorded) = spawn_mock_server(vec![
            (200, list_body),
            (200, get_body),
            (201, onboard_body),
            (200, update_body),
            (200, rotate_body),
            (204, String::new()),
            (200, heartbeat_body),
        ])
        .await;

        let client = ElderClient::new(
            ClientConfig::new(url)
                .unwrap()
                .with_bearer("tok-admin")
                .with_retry(RetryConfig::disabled()),
        )
        .unwrap();

        let listed = client
            .workers()
            .list(&ListRemoteWorkersParams {
                worker_type: Some("runworker".into()),
                cluster: Some("dal2-alpha".into()),
                status: Some("online".into()),
                page: Some(1),
                per_page: Some(25),
            })
            .await
            .expect("list workers");
        assert_eq!(listed.total, 1);
        assert_eq!(listed.items[0].id, "wrk-01");
        assert_eq!(listed.items[0].worker_type, "runworker");
        assert_eq!(listed.items[0].token_prefix.as_deref(), Some("ewrk_9a8b"));

        let fetched = client.workers().get("wrk-01").await.expect("get worker");
        assert_eq!(fetched.hostname.as_deref(), Some("runworker-pod-01"));
        assert_eq!(fetched.version.as_deref(), Some("4.0.12"));

        let mut onboard_req = OnboardRemoteWorkerRequest::new("edge-scanner-01", "scanner");
        onboard_req.cluster = Some("edge-west".into());
        onboard_req.poll_interval_seconds = Some(5);
        onboard_req.progress_interval_seconds = Some(20);
        onboard_req.max_concurrent = Some(4);
        let onboarded = client
            .workers()
            .onboard(&onboard_req)
            .await
            .expect("onboard worker");
        assert_eq!(onboarded.worker.id, "wrk-02");
        assert_eq!(onboarded.token, "ewrk_SUPER_SECRET_ONBOARD_TOKEN_1234");
        let debug_onboard = format!("{onboarded:?}");
        assert!(!debug_onboard.contains("SUPER_SECRET_ONBOARD_TOKEN"));
        assert!(debug_onboard.contains("[REDACTED]"));

        let updated = client
            .workers()
            .update(
                "42",
                &UpdateRemoteWorkerRequest {
                    name: Some("dal2-runworker-updated".into()),
                    cluster: Some("dal2-beta".into()),
                    is_enabled: Some(false),
                    poll_interval_seconds: Some(10),
                    ..Default::default()
                },
            )
            .await
            .expect("update worker");
        assert_eq!(updated.id, "42");
        assert!(!updated.is_enabled);
        assert_eq!(updated.poll_interval_seconds, Some(10));

        let rotated = client
            .workers()
            .rotate_token("wrk-01")
            .await
            .expect("rotate token");
        assert_eq!(rotated.token, "ewrk_SUPER_SECRET_ROTATED_TOKEN_5678");
        assert_eq!(rotated.token_prefix.as_deref(), Some("ewrk_1c2d"));
        let debug_rotated = format!("{rotated:?}");
        assert!(!debug_rotated.contains("SUPER_SECRET_ROTATED_TOKEN"));
        assert!(debug_rotated.contains("[REDACTED]"));

        client
            .workers()
            .offboard("wrk-01")
            .await
            .expect("offboard worker");

        let hb = client
            .workers()
            .heartbeat(&WorkerHeartbeatRequest {
                worker_type: Some("runworker".into()),
                worker_id: Some("wrk-01".into()),
                hostname: Some("runworker-pod-01".into()),
                version: Some("4.0.12".into()),
                cluster: Some("dal2-alpha".into()),
                active_jobs: Some(2),
                ..Default::default()
            })
            .await
            .expect("heartbeat");
        assert_eq!(hb.status, "online");

        let reqs = recorded.lock().await;
        assert_eq!(reqs.len(), 7);
        assert_eq!(reqs[0].0, "GET");
        assert_eq!(
            reqs[0].1,
            "/api/v1/admin/workers?worker_type=runworker&cluster=dal2-alpha&status=online&page=1&per_page=25"
        );
        assert_eq!(reqs[1].0, "GET");
        assert_eq!(reqs[1].1, "/api/v1/admin/workers/wrk-01");
        assert_eq!(reqs[2].0, "POST");
        assert_eq!(reqs[2].1, "/api/v1/admin/workers");
        let onboard_payload: Value = serde_json::from_str(&reqs[2].2).unwrap();
        assert_eq!(onboard_payload["name"], "edge-scanner-01");
        assert_eq!(onboard_payload["worker_type"], "scanner");
        assert_eq!(onboard_payload["cluster"], "edge-west");
        assert_eq!(onboard_payload["poll_interval_seconds"], 5);
        assert_eq!(onboard_payload["progress_interval_seconds"], 20);
        assert_eq!(onboard_payload["max_concurrent"], 4);

        assert_eq!(reqs[3].0, "PUT");
        assert_eq!(reqs[3].1, "/api/v1/admin/workers/42");
        let update_payload: Value = serde_json::from_str(&reqs[3].2).unwrap();
        assert_eq!(update_payload["name"], "dal2-runworker-updated");
        assert_eq!(update_payload["is_enabled"], false);
        assert_eq!(update_payload["poll_interval_seconds"], 10);

        assert_eq!(reqs[4].0, "POST");
        assert_eq!(reqs[4].1, "/api/v1/admin/workers/wrk-01/rotate-token");
        assert_eq!(reqs[5].0, "DELETE");
        assert_eq!(reqs[5].1, "/api/v1/admin/workers/wrk-01");
        assert_eq!(reqs[6].0, "POST");
        assert_eq!(reqs[6].1, "/api/v1/admin/workers/heartbeat");
        let hb_payload: Value = serde_json::from_str(&reqs[6].2).unwrap();
        assert_eq!(hb_payload["worker_type"], "runworker");
        assert_eq!(hb_payload["hostname"], "runworker-pod-01");
        assert_eq!(hb_payload["active_jobs"], 2);
    }

    #[tokio::test]
    async fn workers_client_validates_required_inputs() {
        let client = ElderClient::new(ClientConfig::new("https://elder.example.com").unwrap())
            .expect("client");

        assert!(matches!(
            client.workers().get("   ").await,
            Err(ElderError::Validation(_))
        ));
        assert!(matches!(
            client
                .workers()
                .onboard(&OnboardRemoteWorkerRequest::new("", "runworker"))
                .await,
            Err(ElderError::Validation(_))
        ));
        assert!(matches!(
            client
                .workers()
                .onboard(&OnboardRemoteWorkerRequest::new("w1", "   "))
                .await,
            Err(ElderError::Validation(_))
        ));
        assert!(matches!(
            client
                .workers()
                .update("", &UpdateRemoteWorkerRequest::default())
                .await,
            Err(ElderError::Validation(_))
        ));
        assert!(matches!(
            client.workers().rotate_token("").await,
            Err(ElderError::Validation(_))
        ));
        assert!(matches!(
            client.workers().offboard(" ").await,
            Err(ElderError::Validation(_))
        ));
    }
}
