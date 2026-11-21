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
