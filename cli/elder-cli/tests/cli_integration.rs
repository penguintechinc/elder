//! End-to-end CLI integration tests backed by a local mock HTTP server.

use std::collections::HashMap;
use std::io::Cursor;
use std::sync::{Arc, Mutex};

use elder_cli::cli::{build_cli, FORBIDDEN_SECRET_FLAGS};
use elder_cli::credentials::{CredentialStore, MemoryCredentialStore, SecretKind};
use elder_cli::{run_with_args, RunContext};
use serde_json::{json, Value};
use tempfile::{NamedTempFile, TempDir};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

#[derive(Debug, Clone)]
struct RecordedRequest {
    method: String,
    target: String,
    headers: HashMap<String, String>,
    body: String,
}

#[derive(Debug, Clone)]
struct MockResponse {
    status: u16,
    body: String,
}

impl MockResponse {
    fn json(status: u16, val: Value) -> Self {
        Self {
            status,
            body: val.to_string(),
        }
    }
}

struct MockServer {
    base_url: String,
    recorded: Arc<Mutex<Vec<RecordedRequest>>>,
    _handle: tokio::task::JoinHandle<()>,
}

impl MockServer {
    async fn start<F>(handler: F) -> Self
    where
        F: Fn(&RecordedRequest) -> MockResponse + Send + Sync + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let recorded = Arc::new(Mutex::new(Vec::new()));
        let recorded_bg = Arc::clone(&recorded);
        let handler = Arc::new(handler);

        let handle = tokio::spawn(async move {
            loop {
                let Ok((mut stream, _)) = listener.accept().await else {
                    break;
                };
                let recorded_req = Arc::clone(&recorded_bg);
                let handler_fn = Arc::clone(&handler);
                tokio::spawn(async move {
                    let mut buf = Vec::new();
                    let mut chunk = [0u8; 4096];
                    let mut header_end = None;
                    let mut content_length = 0usize;

                    loop {
                        let Ok(n) = stream.read(&mut chunk).await else {
                            return;
                        };
                        if n == 0 {
                            break;
                        }
                        buf.extend_from_slice(&chunk[..n]);
                        if header_end.is_none() {
                            if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                                header_end = Some(pos + 4);
                                let header_str = String::from_utf8_lossy(&buf[..pos]);
                                for line in header_str.lines().skip(1) {
                                    if let Some((k, v)) = line.split_once(':') {
                                        if k.eq_ignore_ascii_case("content-length") {
                                            content_length = v.trim().parse().unwrap_or(0);
                                        }
                                    }
                                }
                            }
                        }
                        if let Some(end) = header_end {
                            if buf.len() >= end + content_length {
                                break;
                            }
                        }
                    }

                    let Some(end) = header_end else {
                        return;
                    };
                    let header_str = String::from_utf8_lossy(&buf[..end - 4]);
                    let mut lines = header_str.lines();
                    let req_line = lines.next().unwrap_or_default();
                    let mut parts = req_line.split_whitespace();
                    let method = parts.next().unwrap_or_default().to_string();
                    let target = parts.next().unwrap_or_default().to_string();

                    let mut headers = HashMap::new();
                    for line in lines {
                        if let Some((k, v)) = line.split_once(':') {
                            headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
                        }
                    }

                    let body_bytes = &buf[end..buf.len().min(end + content_length)];
                    let body = String::from_utf8_lossy(body_bytes).to_string();

                    let req = RecordedRequest {
                        method,
                        target,
                        headers,
                        body,
                    };
                    let resp = handler_fn(&req);
                    recorded_req.lock().unwrap().push(req);

                    let reason = match resp.status {
                        200 => "OK",
                        201 => "Created",
                        202 => "Accepted",
                        204 => "No Content",
                        401 => "Unauthorized",
                        403 => "Forbidden",
                        404 => "Not Found",
                        500 => "Internal Server Error",
                        _ => "Status",
                    };
                    let raw = format!(
                        "HTTP/1.1 {} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                        resp.status,
                        reason,
                        resp.body.len(),
                        resp.body
                    );
                    let _ = stream.write_all(raw.as_bytes()).await;
                    let _ = stream.shutdown().await;
                });
            }
        });

        Self {
            base_url: format!("http://{addr}"),
            recorded,
            _handle: handle,
        }
    }

    fn requests(&self) -> Vec<RecordedRequest> {
        self.recorded.lock().unwrap().clone()
    }
}

async fn run_cli(
    args: &[&str],
    config_path: std::path::PathBuf,
    store: &dyn CredentialStore,
    stdin_bytes: &[u8],
    env_vars: &[(&str, &str)],
) -> (elder_cli::error::Result<()>, String, String) {
    let mut stdin = Cursor::new(stdin_bytes.to_vec());
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let env_map: HashMap<String, String> = env_vars
        .iter()
        .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
        .collect();
    let env_lookup = |k: &str| env_map.get(k).cloned();

    let mut ctx = RunContext {
        config_path,
        store,
        stdin: &mut stdin,
        stdout: &mut stdout,
        stderr: &mut stderr,
        stdout_is_tty: false,
        stdin_is_tty: false,
        env_lookup: &env_lookup,
    };

    let full_args: Vec<String> = std::iter::once("elder".to_string())
        .chain(args.iter().map(|s| (*s).to_string()))
        .collect();

    let res = run_with_args(full_args, &mut ctx).await;
    (
        res,
        String::from_utf8(stdout).unwrap(),
        String::from_utf8(stderr).unwrap(),
    )
}

#[tokio::test]
async fn dynamic_catalog_dispatch_path_query_body_file_field_and_pagination() {
    let server = MockServer::start(|req| match (req.method.as_str(), req.target.as_str()) {
        ("GET", t) if t.starts_with("/api/v1/issues?") && t.contains("page=1") => {
            MockResponse::json(
                200,
                json!({
                    "items": [{"id": "1", "title": "First"}],
                    "page": 1,
                    "per_page": 1,
                    "pages": 2,
                    "total": 2
                }),
            )
        }
        ("GET", t) if t.starts_with("/api/v1/issues?") && t.contains("page=2") => {
            MockResponse::json(
                200,
                json!({
                    "items": [{"id": "2", "title": "Second"}],
                    "page": 2,
                    "per_page": 1,
                    "pages": 2,
                    "total": 2
                }),
            )
        }
        ("GET", "/api/v1/issues/42?include=comments") => {
            MockResponse::json(200, json!({"id": "42", "title": "Detailed issue"}))
        }
        ("POST", "/api/v1/issues") => {
            let parsed: Value = serde_json::from_str(&req.body).unwrap_or(Value::Null);
            MockResponse::json(201, json!({"created": true, "echo": parsed}))
        }
        ("POST", "/api/v1/hooks/wh_123/test") => {
            MockResponse::json(200, json!({"hook_tested": true}))
        }
        _ => MockResponse::json(404, json!({"error": "not found"})),
    })
    .await;

    let tmp = TempDir::new().unwrap();
    let cfg_path = tmp.path().join("config.toml");
    let store = MemoryCredentialStore::new();

    // 1. Path param + query param
    let (res, out, _) = run_cli(
        &[
            "--url",
            &server.base_url,
            "issues",
            "get-issue",
            "--id",
            "42",
            "-q",
            "include=comments",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &[("ELDER_API_TOKEN", "tok_test_env_9876")],
    )
    .await;
    res.unwrap();
    let val: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(val["title"], "Detailed issue");

    // 2. --body @file + --field override
    let body_file = NamedTempFile::new().unwrap();
    std::fs::write(body_file.path(), r#"{"title":"From file","open":false}"#).unwrap();
    let body_arg = format!("@{}", body_file.path().display());

    let (res, out, _) = run_cli(
        &[
            "--url",
            &server.base_url,
            "issues",
            "create-issue",
            "--body",
            &body_arg,
            "-f",
            "open=true",
            "-f",
            "priority=2",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    let val: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(val["echo"]["title"], "From file");
    assert_eq!(val["echo"]["open"], true);
    assert_eq!(val["echo"]["priority"], 2);

    // 3. --all-pages pagination
    let (res, out, _) = run_cli(
        &[
            "--url",
            &server.base_url,
            "issues",
            "list-issues",
            "--all-pages",
            "--per-page",
            "1",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    let items: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(items.as_array().unwrap().len(), 2);

    // 4. Sanitized path param --path-token (never --token)
    let (res, out, _) = run_cli(
        &[
            "--url",
            &server.base_url,
            "streams",
            "test-webhook",
            "--path-token",
            "wh_123",
        ],
        cfg_path,
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    let val: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(val["hook_tested"], true);

    // Confirm Authorization header was sent on first request
    let reqs = server.requests();
    assert_eq!(
        reqs[0].headers.get("authorization").map(String::as_str),
        Some("Bearer tok_test_env_9876")
    );
}

#[tokio::test]
async fn ergonomic_id_search_ai_research_api_and_version_commands() {
    let server = MockServer::start(|req| match (req.method.as_str(), req.target.as_str()) {
        ("GET", "/api/v1/id/ENT-42") => {
            MockResponse::json(200, json!({"village_id": "ENT-42", "kind": "entity"}))
        }
        ("GET", "/api/v1/search?q=postgres") => MockResponse::json(
            200,
            json!({"results": [{"id": "1", "name": "postgres-prod"}]}),
        ),
        ("POST", "/api/v1/search/ai-research") => {
            let body: Value = serde_json::from_str(&req.body).unwrap_or(Value::Null);
            MockResponse::json(
                200,
                json!({"answer": "Found 3 clusters", "model": body["model"]}),
            )
        }
        ("POST", "/api/v1/custom/echo") => {
            let body: Value = serde_json::from_str(&req.body).unwrap_or(Value::Null);
            MockResponse::json(200, json!({"echo": body}))
        }
        ("GET", "/api/v1/status") => {
            MockResponse::json(200, json!({"status": "healthy", "version": "4.0.1"}))
        }
        _ => MockResponse::json(404, json!({"error": "not found"})),
    })
    .await;

    let tmp = TempDir::new().unwrap();
    let cfg_path = tmp.path().join("config.toml");
    let store = MemoryCredentialStore::new();

    // id
    let (res, out, _) = run_cli(
        &["--url", &server.base_url, "id", "ENT-42"],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    assert!(out.contains("ENT-42"));

    // search (yaml output)
    let (res, out, _) = run_cli(
        &[
            "--url",
            &server.base_url,
            "-o",
            "yaml",
            "search",
            "postgres",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    assert!(out.contains("name: postgres-prod"));

    // ai-research
    let (res, out, _) = run_cli(
        &[
            "--url",
            &server.base_url,
            "ai-research",
            "list prod clusters",
            "--model",
            "gemini-2.5-pro",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    let val: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(val["model"], "gemini-2.5-pro");

    // api escape hatch with stdin body
    let (res, out, _) = run_cli(
        &[
            "--url",
            &server.base_url,
            "api",
            "POST",
            "/api/v1/custom/echo",
            "--body",
            "-",
        ],
        cfg_path.clone(),
        &store,
        br#"{"ping":"pong"}"#,
        &[],
    )
    .await;
    res.unwrap();
    let val: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(val["echo"]["ping"], "pong");

    // version --check-updates
    let (res, out, _) = run_cli(
        &["--url", &server.base_url, "version", "--check-updates"],
        cfg_path,
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    let val: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(val["server_version"], "4.0.1");
    assert_eq!(val["update_available"], true);
}

#[tokio::test]
async fn auth_login_whoami_refresh_logout_masks_secrets_and_uses_keyring() {
    let server = MockServer::start(|req| match (req.method.as_str(), req.target.as_str()) {
        ("POST", "/api/v1/auth/login") => MockResponse::json(
            200,
            json!({
                "access_token": "eyJhbGciOiJSUzI1NiJ9.SUPER_SECRET_ACCESS_TOKEN.sig9999",
                "refresh_token": "ref_SUPER_SECRET_REFRESH_TOKEN_8888",
                "token_type": "Bearer",
                "expires_in": 3600,
                "user": {"username": "alice"}
            }),
        ),
        ("GET", "/api/v1/auth/me") => {
            let auth = req
                .headers
                .get("authorization")
                .cloned()
                .unwrap_or_default();
            if auth.starts_with("Bearer eyJ") {
                MockResponse::json(200, json!({"sub": "usr-1", "username": "alice"}))
            } else {
                MockResponse::json(401, json!({"error": "unauthorized"}))
            }
        }
        ("POST", "/api/v1/auth/refresh") => MockResponse::json(
            200,
            json!({
                "access_token": "eyJhbGciOiJSUzI1NiJ9.NEW_ROTATED_ACCESS_TOKEN.sig7777",
                "refresh_token": "ref_NEW_ROTATED_REFRESH_6666",
                "expires_in": 3600
            }),
        ),
        ("POST", "/api/v1/auth/logout") => MockResponse::json(200, json!({"ok": true})),
        _ => MockResponse::json(404, json!({"error": "not found"})),
    })
    .await;

    let tmp = TempDir::new().unwrap();
    let cfg_path = tmp.path().join("config.toml");
    let store = MemoryCredentialStore::new();

    // 1. Login via --password-stdin (never --password flag!)
    let (res, out, err) = run_cli(
        &[
            "--url",
            &server.base_url,
            "auth",
            "login",
            "--username",
            "alice",
            "--password-stdin",
        ],
        cfg_path.clone(),
        &store,
        b"super-secret-password\n",
        &[],
    )
    .await;
    res.unwrap();
    assert!(!out.contains("SUPER_SECRET_ACCESS_TOKEN"));
    assert!(!out.contains("SUPER_SECRET_REFRESH_TOKEN"));
    assert!(!err.contains("SUPER_SECRET_ACCESS_TOKEN"));
    assert!(out.contains("eyJh****9999"));
    assert!(store
        .get_secret("default", SecretKind::AccessToken)
        .unwrap()
        .is_some());

    // 2. Whoami uses keyring token and masks preview
    let (res, out, _) = run_cli(
        &["--url", &server.base_url, "auth", "whoami"],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    assert!(!out.contains("SUPER_SECRET_ACCESS_TOKEN"));
    let val: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(val["auth_source"], "keyring:access_token");
    assert_eq!(val["identity"]["username"], "alice");

    // 3. Refresh rotates stored token without leaking raw value
    let (res, out, _) = run_cli(
        &["--url", &server.base_url, "auth", "refresh"],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    assert!(!out.contains("NEW_ROTATED_ACCESS_TOKEN"));
    assert!(out.contains("eyJh****7777"));

    // 4. Logout revokes and clears keyring
    let (res, out, _) = run_cli(
        &["--url", &server.base_url, "auth", "logout"],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    let val: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(val["logged_out"], true);
    assert_eq!(
        store
            .get_secret("default", SecretKind::AccessToken)
            .unwrap(),
        None
    );

    // 5. After logout, whoami returns exit code 3 (401 auth error)
    let (res, _, _) = run_cli(
        &["--url", &server.base_url, "auth", "whoami"],
        cfg_path,
        &store,
        b"",
        &[],
    )
    .await;
    let err = res.unwrap_err();
    assert_eq!(err.exit_code_u8(), 3);
}

#[tokio::test]
async fn offline_services_describe_configure_and_completions() {
    let tmp = TempDir::new().unwrap();
    let cfg_path = tmp.path().join("config.toml");
    let store = MemoryCredentialStore::new();

    // services (table)
    let (res, out, _) = run_cli(
        &["-o", "table", "services"],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    assert!(out.contains("issues"));
    assert!(out.contains("entities"));

    // describe service & operation
    let (res, out, _) = run_cli(
        &["describe", "issues", "get-issue"],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    let val: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(val["method"], "GET");
    assert_eq!(val["path"], "/api/v1/issues/{id}");

    // configure non-interactive stores ONLY non-secret profile metadata
    let (res, out, _) = run_cli(
        &[
            "--profile",
            "staging",
            "--url",
            "https://staging.elder.example.com",
            "configure",
            "--default-output",
            "json",
            "--non-interactive",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &[],
    )
    .await;
    res.unwrap();
    assert!(out.contains("staging.elder.example.com"));
    let disk = std::fs::read_to_string(&cfg_path).unwrap();
    assert!(disk.contains("staging.elder.example.com"));
    assert!(!disk.contains("token"));
    assert!(!disk.contains("password"));

    // completions
    let (res, out, _) = run_cli(&["completions", "bash"], cfg_path, &store, b"", &[]).await;
    res.unwrap();
    assert!(out.contains("_elder()"));
}

#[tokio::test]
async fn rejects_forbidden_secret_flags_at_cli_parser() {
    let tmp = TempDir::new().unwrap();
    let cfg_path = tmp.path().join("config.toml");
    let store = MemoryCredentialStore::new();

    for flag in FORBIDDEN_SECRET_FLAGS {
        let arg = format!("--{flag}");
        let (res, _, _) = run_cli(
            &["auth", "login", &arg, "secret"],
            cfg_path.clone(),
            &store,
            b"",
            &[],
        )
        .await;
        let err = res.unwrap_err();
        assert_eq!(
            err.exit_code_u8(),
            2,
            "expected usage exit code 2 for forbidden flag {arg}"
        );
    }

    // Confirm parser tree itself has zero forbidden flags anywhere
    fn walk(cmd: &clap::Command) {
        for arg in cmd.get_arguments() {
            if let Some(long) = arg.get_long() {
                assert!(
                    !FORBIDDEN_SECRET_FLAGS.contains(&long),
                    "forbidden flag --{long} on {}",
                    cmd.get_name()
                );
            }
        }
        for sub in cmd.get_subcommands() {
            walk(sub);
        }
    }
    walk(&build_cli());
}

#[tokio::test]
async fn runs_subcommands_cover_crud_invoke_executions_and_s3_metadata() {
    let sample_fn = |id: i64, with_details: bool| {
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
            "created_at": "2026-11-20T12:00:00+00:00",
            "updated_at": "2026-11-20T12:05:00+00:00",
            "storage_backend": "s3",
            "storage_key": format!("runs/7/{id}/v1/code.py"),
            "code_sha256": "abc123def456"
        });
        if with_details {
            obj["latest_version"] = json!(1);
            obj["recent_executions"] = json!([{
                "id": 101,
                "execution_id": "exec-uuid-1",
                "function_id": id,
                "status": "completed",
                "trigger_type": "manual",
                "exit_code": 0,
                "duration_ms": 215
            }]);
        }
        obj
    };

    let sample_exec = |func_id: i64, exec_id: &str, status: &str| {
        json!({
            "id": 101,
            "tenant_id": 7,
            "execution_id": exec_id,
            "function_id": func_id,
            "status": status,
            "trigger_type": "manual",
            "worker_id": "svc-runworker-1",
            "input_data": {"width": 128},
            "output_data": {"ok": true},
            "stdout_log": "done\n",
            "stderr_log": "",
            "exit_code": 0,
            "duration_ms": 215,
            "created_at": "2026-11-20T12:01:00+00:00"
        })
    };

    let server = MockServer::start(
        move |req| match (req.method.as_str(), req.target.as_str()) {
            ("GET", t) if t.starts_with("/api/v1/runs/executions/") => {
                MockResponse::json(200, sample_exec(42, "exec-uuid-1", "completed"))
            }
            ("GET", t) if t.starts_with("/api/v1/runs/executions") => MockResponse::json(
                200,
                json!({
                    "items": [sample_exec(42, "exec-uuid-1", "completed")],
                    "total": 1,
                    "page": 1,
                    "per_page": 20,
                    "pages": 1
                }),
            ),
            ("GET", "/api/v1/runs/42") => MockResponse::json(200, sample_fn(42, true)),
            ("GET", t) if t.starts_with("/api/v1/runs") => MockResponse::json(
                200,
                json!({
                    "items": [sample_fn(42, false)],
                    "total": 1,
                    "page": 1,
                    "per_page": 20,
                    "pages": 1
                }),
            ),
            ("POST", "/api/v1/runs") => MockResponse::json(201, sample_fn(42, false)),
            ("PUT", "/api/v1/runs/42") => MockResponse::json(200, sample_fn(42, false)),
            ("DELETE", "/api/v1/runs/42") => MockResponse {
                status: 204,
                body: String::new(),
            },
            ("POST", "/api/v1/runs/42/invoke") => MockResponse::json(
                202,
                json!({
                    "execution_id": "exec-uuid-1",
                    "execution": sample_exec(42, "exec-uuid-1", "pending")
                }),
            ),
            _ => MockResponse::json(404, json!({"error": "not found"})),
        },
    )
    .await;

    let tmp = TempDir::new().unwrap();
    let cfg_path = tmp.path().join("config.toml");
    let store = MemoryCredentialStore::new();
    let env = [
        ("ELDER_API_URL", server.base_url.as_str()),
        ("ELDER_TOKEN", "runs-jwt-token"),
    ];

    // 1. list (table output includes storage_backend, storage_key, code_sha256)
    let (res, out, _) = run_cli(
        &[
            "-o",
            "table",
            "runs",
            "list",
            "--runtime",
            "python",
            "--search",
            "avatar",
            "--is-enabled",
            "true",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    assert!(out.contains("s3"));
    assert!(out.contains("runs/"));
    assert!(out.contains("abc1"));

    // 2. get (JSON + table both surface storage metadata)
    let (res, out_json, _) = run_cli(
        &["-o", "json", "runs", "get", "42"],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let fetched: Value = serde_json::from_str(&out_json).unwrap();
    assert_eq!(fetched["storage_backend"], "s3");
    assert_eq!(fetched["storage_key"], "runs/7/42/v1/code.py");
    assert_eq!(fetched["code_sha256"], "abc123def456");
    assert_eq!(fetched["latest_version"], 1);

    let (res, out_table, _) = run_cli(
        &["-o", "table", "runs", "get", "42"],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    assert!(out_table.contains("storage_backend"));
    assert!(out_table.contains("runs/7/42/v1/code.py"));
    assert!(out_table.contains("abc123def456"));

    // 3. create with --code-file and --env
    let mut code_file = NamedTempFile::new().unwrap();
    std::io::Write::write_all(&mut code_file, b"def handler(e): return {'ok': True}\n").unwrap();
    let code_path = code_file.path().to_string_lossy().to_string();

    let (res, out, _) = run_cli(
        &[
            "-o",
            "json",
            "runs",
            "create",
            "--name",
            "resize-avatar",
            "--runtime",
            "python",
            "--code-file",
            &code_path,
            "--entrypoint",
            "handler",
            "--timeout-seconds",
            "90",
            "--memory-mb",
            "512",
            "--env",
            "BUCKET=avatars",
            "--worker-cluster",
            "dal2-alpha",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let created: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(created["id"], 42);

    // 4. create with inline --code
    let (res, _, _) = run_cli(
        &[
            "-o",
            "json",
            "runs",
            "create",
            "--name",
            "inline-fn",
            "--runtime",
            "nodejs",
            "--code",
            "console.log('hi')",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();

    // 5. update
    let (res, _, _) = run_cli(
        &[
            "-o",
            "json",
            "runs",
            "update",
            "42",
            "--timeout-seconds",
            "120",
            "--is-enabled",
            "false",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();

    // 6. invoke with --input JSON
    let (res, out, _) = run_cli(
        &[
            "-o",
            "json",
            "runs",
            "invoke",
            "42",
            "--input",
            r#"{"width":128}"#,
            "--trigger-type",
            "manual",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let invoked: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(invoked["execution_id"], "exec-uuid-1");

    // 7. executions (with positional func_id and --status)
    let (res, out, _) = run_cli(
        &[
            "-o",
            "json",
            "runs",
            "executions",
            "42",
            "--status",
            "completed",
            "--per-page",
            "10",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let execs: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(execs["items"][0]["execution_id"], "exec-uuid-1");

    // 8. execution
    let (res, out, _) = run_cli(
        &["-o", "json", "runs", "execution", "exec-uuid-1"],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let exec: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(exec["duration_ms"], 215);

    // 9. delete
    let (res, out, _) = run_cli(
        &["-o", "json", "runs", "delete", "42"],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let deleted: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(deleted["deleted"], true);
    assert_eq!(deleted["id"], 42);

    // 10. invalid --input JSON fails with usage exit code 2
    let (res, _, _) = run_cli(
        &["runs", "invoke", "42", "--input", "{not-json"],
        cfg_path,
        &store,
        b"",
        &env,
    )
    .await;
    assert_eq!(res.unwrap_err().exit_code_u8(), 2);

    // Verify wire requests recorded by mock server.
    let reqs = server.requests();
    assert_eq!(reqs.len(), 10);
    assert_eq!(reqs[0].method, "GET");
    assert_eq!(
        reqs[0].target,
        "/api/v1/runs?runtime=python&search=avatar&is_enabled=true"
    );
    assert_eq!(reqs[3].method, "POST");
    assert_eq!(reqs[3].target, "/api/v1/runs");
    let create_payload: Value = serde_json::from_str(&reqs[3].body).unwrap();
    assert_eq!(
        create_payload["code"],
        "def handler(e): return {'ok': True}\n"
    );
    assert_eq!(create_payload["env_vars"]["BUCKET"], "avatars");
    assert_eq!(reqs[5].method, "PUT");
    assert_eq!(reqs[5].target, "/api/v1/runs/42");
    let update_payload: Value = serde_json::from_str(&reqs[5].body).unwrap();
    assert_eq!(update_payload["timeout_seconds"], 120);
    assert_eq!(update_payload["is_enabled"], false);
    assert_eq!(reqs[6].method, "POST");
    assert_eq!(reqs[6].target, "/api/v1/runs/42/invoke");
    let invoke_payload: Value = serde_json::from_str(&reqs[6].body).unwrap();
    assert_eq!(invoke_payload["input_data"]["width"], 128);
    assert_eq!(invoke_payload["trigger_type"], "manual");
    assert_eq!(reqs[7].method, "GET");
    assert_eq!(
        reqs[7].target,
        "/api/v1/runs/executions?per_page=10&function_id=42&status=completed"
    );
    assert_eq!(reqs[8].method, "GET");
    assert_eq!(reqs[8].target, "/api/v1/runs/executions/exec-uuid-1");
    assert_eq!(reqs[9].method, "DELETE");
    assert_eq!(reqs[9].target, "/api/v1/runs/42");
}

#[tokio::test]
async fn workers_subcommands_cover_fleet_lifecycle_and_table_columns() {
    let sample_worker = |id: &str, worker_type: &str, status: &str| {
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
    };

    let server = MockServer::start(
        move |req| match (req.method.as_str(), req.target.as_str()) {
            ("GET", t) if t.starts_with("/api/v1/admin/workers?") => MockResponse::json(
                200,
                json!({
                    "items": [sample_worker("wrk-01", "runworker", "online")],
                    "total": 1,
                    "page": 1,
                    "per_page": 20,
                    "pages": 1
                }),
            ),
            ("GET", "/api/v1/admin/workers/wrk-01") => {
                MockResponse::json(200, sample_worker("wrk-01", "runworker", "online"))
            }
            ("POST", "/api/v1/admin/workers") => MockResponse::json(
                201,
                json!({
                    "worker": sample_worker("wrk-02", "scanner", "offline"),
                    "token": "test-onboard-tok-1",
                    "token_prefix": "ewrk_9a8b"
                }),
            ),
            ("PUT", "/api/v1/admin/workers/wrk-01") => {
                let mut w = sample_worker("wrk-01", "runworker", "online");
                w["name"] = json!("dal2-runworker-renamed");
                w["cluster"] = json!("dal2-beta");
                w["is_enabled"] = json!(false);
                w["poll_interval_seconds"] = json!(10);
                MockResponse::json(200, w)
            }
            ("POST", "/api/v1/admin/workers/wrk-01/rotate-token") => MockResponse::json(
                200,
                json!({
                    "worker": sample_worker("wrk-01", "runworker", "online"),
                    "worker_id": "wrk-01",
                    "token": "test-rotated-tok-2",
                    "token_prefix": "ewrk_1c2d"
                }),
            ),
            ("DELETE", "/api/v1/admin/workers/wrk-01") => MockResponse {
                status: 204,
                body: String::new(),
            },
            _ => MockResponse::json(404, json!({"error": "not found"})),
        },
    )
    .await;

    let tmp = TempDir::new().unwrap();
    let cfg_path = tmp.path().join("config.toml");
    let store = MemoryCredentialStore::new();
    let env = [
        ("ELDER_API_URL", server.base_url.as_str()),
        ("ELDER_TOKEN", "workers-admin-jwt"),
    ];

    // 1. list (table output prioritizes fleet columns)
    let (res, out_table, _) = run_cli(
        &[
            "-o",
            "table",
            "workers",
            "list",
            "--type",
            "runworker",
            "--cluster",
            "dal2-alpha",
            "--status",
            "online",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    assert!(out_table.contains("wrk-01"));
    assert!(out_table.contains("online"));
    assert!(out_table.contains("dal2"));
    assert!(out_table.contains("4.0"));

    // 2. get
    let (res, out_get, _) = run_cli(
        &["-o", "json", "workers", "get", "wrk-01"],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let fetched: Value = serde_json::from_str(&out_get).unwrap();
    assert_eq!(fetched["id"], "wrk-01");
    assert_eq!(fetched["token_prefix"], "ewrk_9a8b");

    // 3. onboard
    let (res, out_onboard, _) = run_cli(
        &[
            "-o",
            "json",
            "workers",
            "onboard",
            "--name",
            "edge-scanner-01",
            "--type",
            "scanner",
            "--cluster",
            "edge-west",
            "--poll-interval",
            "5",
            "--progress-interval",
            "20",
            "--max-concurrent",
            "4",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let onboarded: Value = serde_json::from_str(&out_onboard).unwrap();
    assert_eq!(onboarded["worker"]["id"], "wrk-02");
    assert_eq!(onboarded["token"], "test-onboard-tok-1");

    // 4. update
    let (res, out_update, _) = run_cli(
        &[
            "-o",
            "json",
            "workers",
            "update",
            "wrk-01",
            "--name",
            "dal2-runworker-renamed",
            "--cluster",
            "dal2-beta",
            "--enabled",
            "false",
            "--poll-interval",
            "10",
        ],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let updated: Value = serde_json::from_str(&out_update).unwrap();
    assert_eq!(updated["name"], "dal2-runworker-renamed");
    assert_eq!(updated["is_enabled"], false);
    assert_eq!(updated["poll_interval_seconds"], 10);

    // 5. rotate-token
    let (res, out_rotate, _) = run_cli(
        &["-o", "json", "workers", "rotate-token", "wrk-01"],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let rotated: Value = serde_json::from_str(&out_rotate).unwrap();
    assert_eq!(rotated["token"], "test-rotated-tok-2");
    assert_eq!(rotated["token_prefix"], "ewrk_1c2d");

    // 6. offboard
    let (res, out_offboard, _) = run_cli(
        &["-o", "json", "workers", "offboard", "wrk-01"],
        cfg_path.clone(),
        &store,
        b"",
        &env,
    )
    .await;
    res.unwrap();
    let offboarded: Value = serde_json::from_str(&out_offboard).unwrap();
    assert_eq!(offboarded["offboarded"], true);
    assert_eq!(offboarded["id"], "wrk-01");

    // 7. invalid --type fails with usage exit code 2
    let (res, _, _) = run_cli(
        &["workers", "list", "--type", "invalid-type"],
        cfg_path,
        &store,
        b"",
        &env,
    )
    .await;
    assert_eq!(res.unwrap_err().exit_code_u8(), 2);

    // Verify recorded wire requests
    let reqs = server.requests();
    assert_eq!(reqs.len(), 6);
    assert_eq!(reqs[0].method, "GET");
    assert_eq!(
        reqs[0].target,
        "/api/v1/admin/workers?worker_type=runworker&cluster=dal2-alpha&status=online"
    );
    assert_eq!(reqs[1].method, "GET");
    assert_eq!(reqs[1].target, "/api/v1/admin/workers/wrk-01");
    assert_eq!(reqs[2].method, "POST");
    assert_eq!(reqs[2].target, "/api/v1/admin/workers");
    let onboard_payload: Value = serde_json::from_str(&reqs[2].body).unwrap();
    assert_eq!(onboard_payload["name"], "edge-scanner-01");
    assert_eq!(onboard_payload["worker_type"], "scanner");
    assert_eq!(onboard_payload["cluster"], "edge-west");
    assert_eq!(onboard_payload["poll_interval_seconds"], 5);
    assert_eq!(onboard_payload["progress_interval_seconds"], 20);
    assert_eq!(onboard_payload["max_concurrent"], 4);

    assert_eq!(reqs[3].method, "PUT");
    assert_eq!(reqs[3].target, "/api/v1/admin/workers/wrk-01");
    let update_payload: Value = serde_json::from_str(&reqs[3].body).unwrap();
    assert_eq!(update_payload["name"], "dal2-runworker-renamed");
    assert_eq!(update_payload["cluster"], "dal2-beta");
    assert_eq!(update_payload["is_enabled"], false);
    assert_eq!(update_payload["poll_interval_seconds"], 10);

    assert_eq!(reqs[4].method, "POST");
    assert_eq!(reqs[4].target, "/api/v1/admin/workers/wrk-01/rotate-token");
    assert_eq!(reqs[5].method, "DELETE");
    assert_eq!(reqs[5].target, "/api/v1/admin/workers/wrk-01");
}
