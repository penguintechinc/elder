//! Mock-HTTP unit and integration tests for all 7 connectors in `elder-int-workmgmt`.

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use elder_int_workmgmt::{jira, run_endpoint_discovery, run_sync_config};
use elder_svc_common::{ElderApiClient, ServiceTokenProvider};
use jsonwebtoken::{decode, Algorithm, DecodingKey, Validation};
use rsa::pkcs1::EncodeRsaPrivateKey;
use rsa::pkcs8::{EncodePrivateKey, EncodePublicKey, LineEnding};
use rsa::RsaPrivateKey;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::Mutex;

#[derive(Debug, Clone)]
struct RecordedRequest {
    method: String,
    path: String,
    query: String,
    headers: HashMap<String, String>,
    body: String,
}

#[derive(Debug, Clone)]
struct MockResponse {
    status: u16,
    headers: Vec<(String, String)>,
    body: String,
}

impl MockResponse {
    fn json(status: u16, body: Value) -> Self {
        Self {
            status,
            headers: vec![("Content-Type".to_string(), "application/json".to_string())],
            body: body.to_string(),
        }
    }

    fn with_header(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.headers.push((name.into(), value.into()));
        self
    }

    fn empty(status: u16) -> Self {
        Self {
            status,
            headers: Vec::new(),
            body: String::new(),
        }
    }
}

struct MockServer {
    base_url: String,
    recorded: Arc<Mutex<Vec<RecordedRequest>>>,
}

impl MockServer {
    async fn start<F>(handler: F) -> Self
    where
        F: Fn(RecordedRequest, usize) -> MockResponse + Send + Sync + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind loopback mock server");
        let addr = listener.local_addr().expect("local addr");
        let recorded = Arc::new(Mutex::new(Vec::new()));
        let recorded_clone = Arc::clone(&recorded);
        let handler = Arc::new(handler);
        let counter = Arc::new(AtomicUsize::new(0));

        tokio::spawn(async move {
            loop {
                let Ok((mut socket, _)) = listener.accept().await else {
                    break;
                };
                let rec = Arc::clone(&recorded_clone);
                let h = Arc::clone(&handler);
                let cnt = Arc::clone(&counter);

                tokio::spawn(async move {
                    if let Some(req) = read_http_request(&mut socket).await {
                        let idx = cnt.fetch_add(1, Ordering::SeqCst);
                        rec.lock().await.push(req.clone());
                        let resp = h(req, idx);
                        let mut raw = format!(
                            "HTTP/1.1 {} MOCK\r\nContent-Length: {}\r\nConnection: close\r\n",
                            resp.status,
                            resp.body.len()
                        );
                        for (k, v) in &resp.headers {
                            raw.push_str(&format!("{k}: {v}\r\n"));
                        }
                        raw.push_str("\r\n");
                        let _ = socket.write_all(raw.as_bytes()).await;
                        if !resp.body.is_empty() {
                            let _ = socket.write_all(resp.body.as_bytes()).await;
                        }
                        let _ = socket.shutdown().await;
                    }
                });
            }
        });

        Self {
            base_url: format!("http://{addr}"),
            recorded,
        }
    }

    async fn requests(&self) -> Vec<RecordedRequest> {
        self.recorded.lock().await.clone()
    }
}

async fn read_http_request(socket: &mut tokio::net::TcpStream) -> Option<RecordedRequest> {
    let mut buf = Vec::with_capacity(4096);
    let mut chunk = [0u8; 4096];

    let header_end = loop {
        let n = socket.read(&mut chunk).await.ok()?;
        if n == 0 {
            return None;
        }
        buf.extend_from_slice(&chunk[..n]);
        if let Some(pos) = find_double_crlf(&buf) {
            break pos;
        }
    };

    let head_str = String::from_utf8_lossy(&buf[..header_end]);
    let mut lines = head_str.split("\r\n");
    let request_line = lines.next()?;
    let mut req_parts = request_line.split_whitespace();
    let method = req_parts.next()?.to_string();
    let raw_target = req_parts.next()?.to_string();
    let (path, query) = match raw_target.split_once('?') {
        Some((p, q)) => (p.to_string(), q.to_string()),
        None => (raw_target, String::new()),
    };

    let mut headers = HashMap::new();
    let mut content_length: usize = 0;
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            let key = k.trim().to_ascii_lowercase();
            let val = v.trim().to_string();
            if key == "content-length" {
                content_length = val.parse::<usize>().unwrap_or(0);
            }
            headers.insert(key, val);
        }
    }

    let body_start = header_end + 4;
    while buf.len() < body_start + content_length {
        let n = socket.read(&mut chunk).await.ok()?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
    }

    let body_end = (body_start + content_length).min(buf.len());
    let body = String::from_utf8_lossy(&buf[body_start..body_end]).to_string();

    Some(RecordedRequest {
        method,
        path,
        query,
        headers,
        body,
    })
}

fn find_double_crlf(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|w| w == b"\r\n\r\n")
}

struct EphemeralRsaMaterial {
    pkcs8_pem: String,
    pkcs1_pem: String,
    public_pem: String,
}

fn ephemeral_rsa() -> &'static EphemeralRsaMaterial {
    static KEY: OnceLock<EphemeralRsaMaterial> = OnceLock::new();
    KEY.get_or_init(|| {
        let mut rng = rand::thread_rng();
        let priv_key = RsaPrivateKey::new(&mut rng, 2048).expect("generate ephemeral RSA key");
        let pkcs8_pem = priv_key
            .to_pkcs8_pem(LineEnding::LF)
            .expect("encode pkcs8")
            .to_string();
        let pkcs1_pem = priv_key
            .to_pkcs1_pem(LineEnding::LF)
            .expect("encode pkcs1")
            .to_string();
        let public_pem = priv_key
            .to_public_key()
            .to_public_key_pem(LineEnding::LF)
            .expect("encode public pem");
        EphemeralRsaMaterial {
            pkcs8_pem,
            pkcs1_pem,
            public_pem,
        }
    })
}

fn test_elder_client(base_url: &str) -> ElderApiClient {
    let auth = ServiceTokenProvider::from_env_lookup(
        "svc-integrations",
        vec!["integrations:write".into()],
        |k| match k {
            "ELDER_API_TOKEN" => Some("elder-mock-token".into()),
            _ => None,
        },
    )
    .expect("auth");
    ElderApiClient::new(base_url, auth)
        .expect("elder client")
        .with_retry_config(1, Duration::from_millis(5))
}

#[derive(Debug, Deserialize, Serialize)]
struct GhAppClaims {
    iss: String,
    iat: i64,
    exp: i64,
}

#[tokio::test]
async fn github_app_jwt_link_pagination_and_pr_filtering() {
    let rsa = ephemeral_rsa();
    let pub_pem = rsa.public_pem.clone();
    let base_holder = Arc::new(OnceLock::<String>::new());
    let base_for_handler = Arc::clone(&base_holder);

    let gh_mock = MockServer::start(move |req, _| {
        if req.method == "POST" && req.path == "/app/installations/77/access_tokens" {
            let auth = req.headers.get("authorization").cloned().unwrap_or_default();
            let jwt_str = auth.strip_prefix("Bearer ").expect("bearer jwt");
            let mut val = Validation::new(Algorithm::RS256);
            val.validate_exp = false;
            val.required_spec_claims.clear();
            let decoded = decode::<GhAppClaims>(
                jwt_str,
                &DecodingKey::from_rsa_pem(pub_pem.as_bytes()).expect("pub pem"),
                &val,
            )
            .expect("valid RS256 app JWT");
            assert_eq!(decoded.claims.iss, "99123");
            assert_eq!(decoded.claims.exp - decoded.claims.iat, 600);
            return MockResponse::json(
                201,
                json!({
                    "token": "ghs_install_tok_1",
                    "expires_at": "2099-01-01T00:00:00Z"
                }),
            );
        }

        if req.method == "GET" && req.path == "/repos/penguintech/elder/issues" {
            assert_eq!(
                req.headers.get("authorization").map(String::as_str),
                Some("Bearer ghs_install_tok_1")
            );
            if req.query.contains("page=2") {
                return MockResponse::json(
                    200,
                    json!([
                        {
                            "number": 12,
                            "title": "Second page bug",
                            "body": "Page 2 body",
                            "state": "closed",
                            "labels": [{"name": "bug"}, {"name": "priority:high"}],
                            "updated_at": "2026-11-10T10:00:00Z",
                            "closed_at": "2026-11-10T10:00:00Z",
                            "html_url": "https://github.com/penguintech/elder/issues/12"
                        }
                    ]),
                );
            }
            let base = base_for_handler.get().expect("base set");
            return MockResponse::json(
                200,
                json!([
                    {
                        "number": 10,
                        "title": "First page issue",
                        "body": "Body 1",
                        "state": "open",
                        "labels": [{"name": "security"}, {"name": "critical"}],
                        "updated_at": "2026-11-09T10:00:00Z",
                        "html_url": "https://github.com/penguintech/elder/issues/10"
                    },
                    {
                        "number": 11,
                        "title": "Should filter out PR",
                        "state": "open",
                        "pull_request": {"url": "https://api.github.com/pulls/11"},
                        "updated_at": "2026-11-09T11:00:00Z"
                    }
                ]),
            )
            .with_header(
                "Link",
                format!(
                    "<{base}/repos/penguintech/elder/issues?state=all&per_page=100&page=2>; rel=\"next\", <{base}/repos/penguintech/elder/issues?page=2>; rel=\"last\""
                ),
            );
        }

        MockResponse::json(404, json!({"message": "not found"}))
    })
    .await;
    let _ = base_holder.set(gh_mock.base_url.clone());

    let elder_mock =
        MockServer::start(|_req, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 10,
        "platform": "github",
        "config_json": {
            "owner": "penguintech",
            "repo_name": "elder",
            "app_id": 99123,
            "installation_id": 77,
            "private_key": rsa.pkcs8_pem,
            "api_base_url": gh_mock.base_url,
            "retry_delay_ms": 1
        }
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 2);
    assert_eq!(outcome.items_failed, 0);
    assert!(outcome.error.is_none());

    let elder_reqs = elder_mock.requests().await;
    assert_eq!(elder_reqs.len(), 1);
    assert_eq!(
        elder_reqs[0].path,
        "/api/v1/integrations/internal/sync/10/apply"
    );
    let apply_body: Value = serde_json::from_str(&elder_reqs[0].body).expect("apply json");
    assert_eq!(apply_body["success"], json!(true));
    assert_eq!(apply_body["pulled_items"].as_array().expect("arr").len(), 2);
    assert_eq!(apply_body["pulled_items"][0]["external_id"], json!("10"));
    assert_eq!(apply_body["pulled_items"][0]["priority"], json!("CRITICAL"));
    assert_eq!(
        apply_body["pulled_items"][0]["issue_type"],
        json!("SECURITY")
    );
    assert_eq!(apply_body["pulled_items"][1]["external_id"], json!("12"));
    assert_eq!(apply_body["pulled_items"][1]["status"], json!("CLOSED"));
}

#[tokio::test]
async fn github_app_pkcs1_and_401_token_refresh() {
    let rsa = ephemeral_rsa();
    let mint_count = Arc::new(AtomicUsize::new(0));
    let mint_clone = Arc::clone(&mint_count);

    let gh_mock = MockServer::start(move |req, _| {
        if req.method == "POST" && req.path == "/app/installations/88/access_tokens" {
            let n = mint_clone.fetch_add(1, Ordering::SeqCst) + 1;
            return MockResponse::json(
                201,
                json!({
                    "token": format!("ghs_tok_{n}"),
                    "expires_at": "2099-01-01T00:00:00Z"
                }),
            );
        }
        if req.method == "GET" && req.path == "/repos/acme/app/issues" {
            if req.headers.get("authorization").map(String::as_str) == Some("Bearer ghs_tok_1") {
                return MockResponse::json(401, json!({"message": "Bad credentials"}));
            }
            return MockResponse::json(
                200,
                json!([{"number": 1, "title": "Recovered", "state": "open", "labels": []}]),
            );
        }
        MockResponse::json(404, json!({}))
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 11,
        "platform": "github",
        "owner": "acme",
        "repo_name": "app",
        "app_id": "555",
        "installation_id": 88,
        "private_key": rsa.pkcs1_pem,
        "api_base_url": gh_mock.base_url,
        "retry_delay_ms": 1
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 1);
    assert_eq!(mint_count.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn github_pat_push_create_and_mapped_update() {
    let gh_mock = MockServer::start(|req, _| {
        assert_eq!(
            req.headers.get("authorization").map(String::as_str),
            Some("Bearer ghp_test_pat")
        );
        match (req.method.as_str(), req.path.as_str()) {
            ("GET", "/repos/org/repo/issues") => MockResponse::json(
                200,
                json!([{
                    "number": 5,
                    "title": "Old remote title",
                    "body": "Old body",
                    "state": "open",
                    "labels": [],
                    "updated_at": "2026-11-01T00:00:00Z"
                }]),
            ),
            ("POST", "/repos/org/repo/issues") => MockResponse::json(
                201,
                json!({"number": 99, "state": "open", "updated_at": "2026-11-20T12:00:00Z"}),
            ),
            ("PATCH", "/repos/org/repo/issues/5") => MockResponse::json(
                200,
                json!({"number": 5, "state": "closed", "updated_at": "2026-11-20T12:01:00Z"}),
            ),
            _ => MockResponse::json(404, json!({})),
        }
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 12,
        "platform": "github",
        "two_way_create": true,
        "config_json": {
            "owner": "org",
            "repo_name": "repo",
            "access_token": "ghp_test_pat",
            "api_base_url": gh_mock.base_url,
            "retry_delay_ms": 1
        },
        "mappings": [
            {
                "elder_type": "issue",
                "elder_id": 200,
                "external_id": "5",
                "last_synced_at": "2026-11-05T00:00:00Z"
            }
        ],
        "local_items": [
            {
                "id": 200,
                "title": "Updated locally",
                "description": "Fixed in Elder",
                "status": "CLOSED",
                "updated_at": "2026-11-15T00:00:00Z"
            },
            {
                "id": 201,
                "title": "Brand new local issue",
                "description": "Create on GitHub",
                "status": "OPEN",
                "labels": ["bug"],
                "updated_at": "2026-11-16T00:00:00Z"
            }
        ]
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 2);

    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    let pushed = apply_body["pushed_mappings"].as_array().expect("pushed");
    assert_eq!(pushed.len(), 2);
    assert!(apply_body["pulled_items"]
        .as_array()
        .expect("pulled")
        .is_empty());
}

#[tokio::test]
async fn github_conflict_detection_both_modified_since_last_synced_at() {
    let gh_mock = MockServer::start(|req, _| {
        if req.method == "GET" && req.path == "/repos/org/repo/issues" {
            return MockResponse::json(
                200,
                json!([{
                    "number": 7,
                    "title": "Remote edited title",
                    "body": "Remote body",
                    "state": "open",
                    "labels": [],
                    "updated_at": "2026-11-12T10:00:00Z"
                }]),
            );
        }
        MockResponse::json(500, json!({"error": "should not push on manual conflict"}))
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 13,
        "platform": "github",
        "owner": "org",
        "repo_name": "repo",
        "token": "ghp_tok",
        "api_base_url": gh_mock.base_url,
        "two_way_create": false,
        "mappings": [
            {
                "elder_id": 300,
                "external_id": "7",
                "last_synced_at": "2026-11-10T00:00:00Z"
            }
        ],
        "local_items": [
            {
                "id": 300,
                "title": "Elder edited title",
                "description": "Elder body",
                "status": "OPEN",
                "updated_at": "2026-11-11T10:00:00Z"
            },
            {
                "id": 301,
                "title": "Should NOT be created because two_way_create is false",
                "status": "OPEN"
            }
        ]
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 0);

    let gh_reqs = gh_mock.requests().await;
    assert_eq!(gh_reqs.len(), 1); // Only GET, no PATCH or POST!

    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    let conflicts = apply_body["conflicts"].as_array().expect("conflicts");
    assert_eq!(conflicts.len(), 1);
    assert_eq!(conflicts[0]["conflict_type"], json!("both_modified"));
    assert_eq!(conflicts[0]["resolved"], json!(false));
    assert_eq!(conflicts[0]["elder_id"], json!(300));
    assert_eq!(conflicts[0]["external_id"], json!("7"));
}

#[tokio::test]
async fn github_conflict_last_modified_wins_pushes_when_elder_newer() {
    let gh_mock = MockServer::start(|req, _| match (req.method.as_str(), req.path.as_str()) {
        ("GET", "/repos/org/repo/issues") => MockResponse::json(
            200,
            json!([{
                "number": 8,
                "title": "Older remote edit",
                "body": "Remote",
                "state": "open",
                "labels": [],
                "updated_at": "2026-11-11T10:00:00Z"
            }]),
        ),
        ("PATCH", "/repos/org/repo/issues/8") => MockResponse::json(
            200,
            json!({"number": 8, "updated_at": "2026-11-13T10:00:00Z"}),
        ),
        _ => MockResponse::json(404, json!({})),
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 14,
        "platform": "github",
        "owner": "org",
        "repo_name": "repo",
        "token": "ghp_tok",
        "api_base_url": gh_mock.base_url,
        "conflict_strategy": "last_modified_wins",
        "mappings": [{
            "elder_id": 400,
            "external_id": "8",
            "last_synced_at": "2026-11-10T00:00:00Z"
        }],
        "local_items": [{
            "id": 400,
            "title": "Newer Elder edit",
            "description": "Elder wins",
            "status": "OPEN",
            "updated_at": "2026-11-12T10:00:00Z"
        }]
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.items_synced, 1);
    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    assert_eq!(apply_body["conflicts"][0]["resolved"], json!(true));
    assert_eq!(
        apply_body["conflicts"][0]["resolution_strategy"],
        json!("last_modified_wins")
    );
    assert_eq!(apply_body["pushed_mappings"][0]["external_id"], json!("8"));
}

#[tokio::test]
async fn jira_search_jql_adf_and_push_create_update() {
    let jira_mock = MockServer::start(|req, _| {
        let expected_auth = format!(
            "Basic {}",
            STANDARD.encode("secops@penguintech.io:jira_tok_123")
        );
        assert_eq!(
            req.headers.get("authorization").map(String::as_str),
            Some(expected_auth.as_str())
        );

        match (req.method.as_str(), req.path.as_str()) {
            ("POST", "/rest/api/3/search/jql") => {
                let body: Value = serde_json::from_str(&req.body).unwrap_or_default();
                if body.get("nextPageToken").and_then(Value::as_str) == Some("tok_page_2") {
                    MockResponse::json(
                        200,
                        json!({
                            "isLast": true,
                            "issues": [{
                                "id": "1002",
                                "key": "ELD-2",
                                "fields": {
                                    "summary": "Page two ticket",
                                    "description": {
                                        "type": "doc",
                                        "version": 1,
                                        "content": [
                                            {"type": "paragraph", "content": [{"type": "text", "text": "Details line"}]}
                                        ]
                                    },
                                    "status": {"name": "Done", "statusCategory": {"key": "done"}},
                                    "priority": {"name": "Highest"},
                                    "issuetype": {"name": "Bug"},
                                    "labels": ["sec"],
                                    "updated": "2026-11-02T09:00:00.000+0000"
                                }
                            }]
                        }),
                    )
                } else {
                    MockResponse::json(
                        200,
                        json!({
                            "isLast": false,
                            "nextPageToken": "tok_page_2",
                            "issues": [{
                                "id": "1001",
                                "key": "ELD-1",
                                "fields": {
                                    "summary": "Page one ticket",
                                    "description": {
                                        "type": "doc",
                                        "version": 1,
                                        "content": [
                                            {"type": "paragraph", "content": [
                                                {"type": "text", "text": "Hello "},
                                                {"type": "mention", "attrs": {"text": "@alice"}},
                                                {"type": "hardBreak"},
                                                {"type": "inlineCard", "attrs": {"url": "https://elder.example"}}
                                            ]}
                                        ]
                                    },
                                    "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                                    "priority": {"name": "High"},
                                    "issuetype": {"name": "Story"},
                                    "labels": [],
                                    "updated": "2026-11-01T09:00:00.000+0000"
                                }
                            }]
                        }),
                    )
                }
            }
            ("POST", "/rest/api/3/issue") => {
                let body: Value = serde_json::from_str(&req.body).unwrap_or_default();
                assert_eq!(body["fields"]["description"]["type"], json!("doc"));
                assert_eq!(body["fields"]["description"]["version"], json!(1));
                assert_eq!(
                    body["fields"]["description"]["content"][0]["content"][0]["text"],
                    json!("Line one")
                );
                MockResponse::json(201, json!({"id": "1003", "key": "ELD-3"}))
            }
            ("PUT", "/rest/api/3/issue/ELD-2") => MockResponse::empty(204),
            ("GET", "/rest/api/3/issue/ELD-2/transitions") => MockResponse::json(
                200,
                json!({"transitions": [{"id": "31", "name": "Done", "to": {"statusCategory": {"key": "done"}}}]}),
            ),
            ("POST", "/rest/api/3/issue/ELD-2/transitions") => MockResponse::empty(204),
            _ => MockResponse::json(404, json!({})),
        }
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 20,
        "platform": "jira",
        "two_way_create": true,
        "jira_url": jira_mock.base_url,
        "email": "secops@penguintech.io",
        "api_token": "jira_tok_123",
        "project_key": "ELD",
        "mappings": [{
            "elder_id": 500,
            "external_id": "ELD-2",
            "last_synced_at": "2026-11-05T00:00:00Z"
        }],
        "local_items": [
            {
                "id": 500,
                "title": "Close ELD-2",
                "description": "Patched",
                "status": "CLOSED",
                "priority": "CRITICAL",
                "updated_at": "2026-11-15T00:00:00Z"
            },
            {
                "id": 501,
                "title": "New Jira issue",
                "description": "Line one\nLine two",
                "status": "OPEN",
                "issue_type": "BUG"
            }
        ]
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 3); // 1 pulled (ELD-1) + 2 pushed (ELD-2 update + ELD-3 create)

    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    assert_eq!(apply_body["pulled_items"][0]["external_id"], json!("ELD-1"));
    assert_eq!(
        apply_body["pulled_items"][0]["status"],
        json!("IN_PROGRESS")
    );
    assert_eq!(
        apply_body["pulled_items"][0]["description"],
        json!("Hello @alice\nhttps://elder.example")
    );
}

#[tokio::test]
async fn jira_404_fallback_to_legacy_search_and_bearer_auth() {
    let jira_mock = MockServer::start(|req, _| {
        assert_eq!(
            req.headers.get("authorization").map(String::as_str),
            Some("Bearer jira_bearer_tok")
        );
        match (req.method.as_str(), req.path.as_str()) {
            ("POST", "/rest/api/3/search/jql") => {
                MockResponse::json(410, json!({"errorMessages": ["Gone"]}))
            }
            ("GET", "/rest/api/3/search") => MockResponse::json(
                200,
                json!({
                    "total": 1,
                    "issues": [{
                        "key": "LEG-9",
                        "fields": {
                            "summary": "Legacy search hit",
                            "description": "Plain string",
                            "status": {"name": "Open", "statusCategory": {"key": "new"}},
                            "priority": {"name": "Medium"},
                            "issuetype": {"name": "Task"}
                        }
                    }]
                }),
            ),
            _ => MockResponse::json(404, json!({})),
        }
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 21,
        "platform": "jira",
        "jira_url": jira_mock.base_url,
        "api_token": "jira_bearer_tok",
        "project_key": "LEG"
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 1);
    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    assert_eq!(apply_body["pulled_items"][0]["external_id"], json!("LEG-9"));
}

#[test]
fn jira_adf_roundtrip_helpers() {
    let adf = jira::text_to_adf("Line A\n\nLine B");
    assert_eq!(adf["type"], json!("doc"));
    assert_eq!(adf["version"], json!(1));
    assert_eq!(adf["content"].as_array().expect("content").len(), 3);
    assert_eq!(jira::adf_to_text(Some(&adf)), "Line A\n\nLine B");
    assert_eq!(jira::adf_to_text(Some(&jira::text_to_adf(""))), "");

    let table_adf = json!({
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "table", "content": [
                {"type": "tableRow", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Col1"}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": "Col2"}]}
                ]}
            ]}
        ]
    });
    assert_eq!(jira::adf_to_text(Some(&table_adf)), "Col1 | Col2");
}

#[tokio::test]
async fn gitlab_v4_private_token_pagination_and_push() {
    let gl_mock = MockServer::start(|req, _| {
        assert_eq!(
            req.headers.get("private-token").map(String::as_str),
            Some("glpat-secret")
        );
        match (req.method.as_str(), req.path.as_str()) {
            ("GET", "/api/v4/projects/group%2Frepo/issues") => {
                if req.query.contains("page=2") {
                    MockResponse::json(
                        200,
                        json!([{
                            "iid": 2,
                            "title": "GL page 2",
                            "description": "Desc 2",
                            "state": "closed",
                            "labels": ["type::bug", "priority::high"],
                            "updated_at": "2026-11-02T00:00:00Z"
                        }]),
                    )
                } else {
                    MockResponse::json(
                        200,
                        json!([{
                            "iid": 1,
                            "title": "GL page 1",
                            "description": "Desc 1",
                            "state": "opened",
                            "labels": ["workflow::in-progress"],
                            "updated_at": "2026-11-01T00:00:00Z"
                        }]),
                    )
                    .with_header("x-next-page", "2")
                }
            }
            ("POST", "/api/v4/projects/group%2Frepo/issues") => {
                MockResponse::json(201, json!({"iid": 3, "updated_at": "2026-11-20T00:00:00Z"}))
            }
            ("PUT", "/api/v4/projects/group%2Frepo/issues/2") => {
                MockResponse::json(200, json!({"iid": 2, "updated_at": "2026-11-20T01:00:00Z"}))
            }
            _ => MockResponse::json(404, json!({})),
        }
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 30,
        "platform": "gitlab",
        "two_way_create": true,
        "api_token": "glpat-secret",
        "project_id": "group/repo",
        "base_url": gl_mock.base_url,
        "mappings": [{"elder_id": 600, "external_id": "2", "last_synced_at": "2026-11-05T00:00:00Z"}],
        "local_items": [
            {"id": 600, "title": "Reopen GL #2", "status": "OPEN", "updated_at": "2026-11-15T00:00:00Z"},
            {"id": 601, "title": "Create GL #3", "status": "OPEN"}
        ]
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 3);
    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    assert_eq!(
        apply_body["pulled_items"][0]["status"],
        json!("IN_PROGRESS")
    );
    assert_eq!(
        apply_body["pushed_mappings"]
            .as_array()
            .expect("pushed")
            .len(),
        2
    );
}

#[tokio::test]
async fn trello_v1_lists_cards_pull_and_push() {
    let tr_mock = MockServer::start(|req, _| {
        assert!(req.query.contains("key=tr_key") && req.query.contains("token=tr_tok"));
        match (req.method.as_str(), req.path.as_str()) {
            ("GET", "/1/boards/board42/lists") => MockResponse::json(
                200,
                json!([
                    {"id": "list_todo", "name": "To Do", "closed": false},
                    {"id": "list_doing", "name": "In Progress", "closed": false},
                    {"id": "list_done", "name": "Done", "closed": false}
                ]),
            ),
            ("GET", "/1/boards/board42/cards") => MockResponse::json(
                200,
                json!([
                    {
                        "id": "card_1",
                        "name": "Trello task",
                        "desc": "Work on it",
                        "idList": "list_doing",
                        "closed": false,
                        "labels": [{"name": "high"}],
                        "dateLastActivity": "2026-11-01T12:00:00Z"
                    }
                ]),
            ),
            ("POST", "/1/cards") => MockResponse::json(
                200,
                json!({"id": "card_2", "dateLastActivity": "2026-11-20T12:00:00Z"}),
            ),
            ("PUT", "/1/cards/card_1") => MockResponse::json(
                200,
                json!({"id": "card_1", "dateLastActivity": "2026-11-20T12:05:00Z"}),
            ),
            _ => MockResponse::json(404, json!({})),
        }
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 40,
        "platform": "trello",
        "two_way_create": true,
        "api_key": "tr_key",
        "api_token": "tr_tok",
        "board_id": "board42",
        "base_url": tr_mock.base_url,
        "mappings": [{"elder_id": 700, "external_id": "card_1", "last_synced_at": "2026-11-05T00:00:00Z"}],
        "local_items": [
            {"id": 700, "title": "Close card 1", "status": "CLOSED", "updated_at": "2026-11-10T00:00:00Z"},
            {"id": 701, "title": "New card 2", "status": "OPEN"}
        ]
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 2); // 2 pushed (card_1 suppressed from pull because pushed!)

    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    assert_eq!(
        apply_body["pushed_mappings"]
            .as_array()
            .expect("pushed")
            .len(),
        2
    );
}

#[tokio::test]
async fn openproject_v3_basic_apikey_hal_pull_and_patch() {
    let op_mock = MockServer::start(|req, _| {
        let expected_auth = format!("Basic {}", STANDARD.encode("apikey:op_secret_token"));
        assert_eq!(
            req.headers.get("authorization").map(String::as_str),
            Some(expected_auth.as_str())
        );
        match (req.method.as_str(), req.path.as_str()) {
            ("GET", "/api/v3/projects/proj9/work_packages") => MockResponse::json(
                200,
                json!({
                    "total": 2,
                    "_embedded": {
                        "elements": [
                            {
                                "id": 101,
                                "subject": "WP One",
                                "lockVersion": 3,
                                "description": {"raw": "Markdown desc"},
                                "updatedAt": "2026-11-01T08:00:00Z",
                                "_links": {
                                    "status": {"title": "In progress"},
                                    "priority": {"title": "Immediate"},
                                    "type": {"title": "Bug"}
                                }
                            },
                            {
                                "id": 102,
                                "subject": "WP Two",
                                "lockVersion": 7,
                                "description": {"raw": "Old desc"},
                                "updatedAt": "2026-11-01T09:00:00Z",
                                "_links": {
                                    "status": {"title": "New"},
                                    "priority": {"title": "Normal"},
                                    "type": {"title": "Task"}
                                }
                            }
                        ]
                    }
                }),
            ),
            ("POST", "/api/v3/projects/proj9/work_packages") => {
                MockResponse::json(201, json!({"id": 103, "updatedAt": "2026-11-20T09:00:00Z"}))
            }
            ("PATCH", "/api/v3/work_packages/102") => {
                let body: Value = serde_json::from_str(&req.body).unwrap_or_default();
                assert_eq!(body["lockVersion"], json!(7));
                MockResponse::json(200, json!({"id": 102, "updatedAt": "2026-11-20T09:01:00Z"}))
            }
            _ => MockResponse::json(404, json!({})),
        }
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 50,
        "platform": "openproject",
        "two_way_create": true,
        "api_key": "op_secret_token",
        "base_url": op_mock.base_url,
        "project_id": "proj9",
        "mappings": [{"elder_id": 800, "external_id": "102", "last_synced_at": "2026-11-05T00:00:00Z"}],
        "local_items": [
            {"id": 800, "title": "Updated WP 102", "description": "New raw", "status": "OPEN", "updated_at": "2026-11-12T00:00:00Z"},
            {"id": 801, "title": "Created WP 103", "description": "Create raw", "status": "OPEN"}
        ]
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 3); // 101 pulled + 102 updated + 103 created

    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    assert_eq!(apply_body["pulled_items"][0]["priority"], json!("CRITICAL"));
    assert_eq!(apply_body["pulled_items"][0]["issue_type"], json!("BUG"));
    assert_eq!(
        apply_body["pulled_items"][0]["status"],
        json!("IN_PROGRESS")
    );
}

#[tokio::test]
async fn sync_error_still_posts_failure_to_apply() {
    let gl_mock =
        MockServer::start(|_, _| MockResponse::json(401, json!({"message": "Unauthorized"}))).await;
    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 60,
        "platform": "gitlab",
        "api_token": "bad_tok",
        "project_id": "1",
        "base_url": gl_mock.base_url,
        "retry_delay_ms": 1
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "failed");
    assert_eq!(outcome.items_failed, 1);
    assert!(outcome.error.is_some());

    let elder_reqs = elder_mock.requests().await;
    assert_eq!(elder_reqs.len(), 1);
    assert_eq!(
        elder_reqs[0].path,
        "/api/v1/integrations/internal/sync/60/apply"
    );
    let apply_body: Value = serde_json::from_str(&elder_reqs[0].body).expect("apply json");
    assert_eq!(apply_body["success"], json!(false));
    assert!(apply_body["error_message"]
        .as_str()
        .is_some_and(|s| s.contains("401")));
}

#[tokio::test]
async fn sync_unsupported_platform_posts_failure_to_apply() {
    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 61,
        "platform": "asana"
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "failed");
    assert_eq!(elder_mock.requests().await.len(), 1);
    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    assert_eq!(apply_body["success"], json!(false));
}

#[tokio::test]
async fn fleetdm_full_v5_discovery_and_elder_callbacks() {
    let fleet_mock = MockServer::start(|req, _| {
        assert_eq!(
            req.headers.get("authorization").map(String::as_str),
            Some("Bearer fleet_tok_abc")
        );
        match req.path.as_str() {
            "/api/v1/fleet/hosts" => MockResponse::json(
                200,
                json!({
                    "hosts": [{
                        "id": 10,
                        "uuid": "host-uuid-10",
                        "hostname": "laptop-10.local",
                        "platform": "darwin",
                        "os_version": "macOS 15.1",
                        "os_version_id": 4,
                        "fleet_id": 2,
                        "status": "online",
                        "mdm": {"enrollment_status": "On (automatic)", "connected_to_fleet": true}
                    }]
                }),
            ),
            "/api/v1/fleet/software/versions" => MockResponse::json(
                200,
                json!({"software": [{"id": 50, "name": "curl", "version": "8.9.0", "source": "homebrew_packages"}]}),
            ),
            "/api/v1/fleet/os_versions" => MockResponse::json(
                200,
                json!({"os_versions": [{"os_version_id": 4, "name": "macOS 15.1", "platform": "darwin", "hosts_count": 1}]}),
            ),
            "/api/v1/fleet/vulnerabilities" => MockResponse::json(
                200,
                json!({
                    "vulnerabilities": [{
                        "cve": "CVE-2026-0001",
                        "cvss_score": 9.8,
                        "software": [{"id": 50}]
                    }]
                }),
            ),
            "/api/v1/fleet/policies" => MockResponse::json(
                200,
                json!({"policies": [{"id": 7, "name": "Disk encryption", "platform": "darwin", "critical": true}]}),
            ),
            "/api/v1/fleet/labels" => MockResponse::json(
                200,
                json!({"labels": [{"id": 3, "name": "Corp Macs", "label_type": "manual"}]}),
            ),
            "/api/v1/fleet/labels/3/hosts" => MockResponse::json(
                200,
                json!({"hosts": [{"id": 10}]}),
            ),
            "/api/v1/fleet/mdm/profiles" => MockResponse::json(
                200,
                json!({"profiles": [{"profile_uuid": "prof-1", "name": "FileVault", "platform": "darwin"}]}),
            ),
            "/api/v1/fleet/fleets" => MockResponse::json(
                200,
                json!({"fleets": [{"id": 2, "name": "Engineering"}]}),
            ),
            _ => MockResponse::json(404, json!({})),
        }
    })
    .await;

    let elder_mock = MockServer::start(|req, _| {
        if req.method == "POST" && req.path == "/api/v1/integrations/internal/discovery/90/start" {
            return MockResponse::json(200, json!({"history_id": 9001}));
        }
        if req.method == "POST" && req.path == "/api/v1/integrations/internal/discovery/90/complete"
        {
            return MockResponse::json(200, json!({"ok": true}));
        }
        MockResponse::json(404, json!({}))
    })
    .await;

    let elder = test_elder_client(&elder_mock.base_url);
    let job = json!({
        "id": 90,
        "provider": "fleetdm",
        "organization_id": 15,
        "config_json": {
            "fleetdm_url": fleet_mock.base_url,
            "api_token": "fleet_tok_abc",
            "per_page": 50
        }
    });

    let outcome = run_endpoint_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 8); // host + sw + os + vuln + policy + label + mdm + fleet
    assert_eq!(outcome.items_failed, 0);

    let elder_reqs = elder_mock.requests().await;
    assert_eq!(elder_reqs.len(), 2);
    assert_eq!(
        elder_reqs[0].path,
        "/api/v1/integrations/internal/discovery/90/start"
    );
    assert_eq!(
        elder_reqs[1].path,
        "/api/v1/integrations/internal/discovery/90/complete"
    );

    let complete: Value = serde_json::from_str(&elder_reqs[1].body).expect("complete json");
    assert_eq!(complete["history_id"], json!(9001));
    assert_eq!(complete["status"], json!("completed"));
    assert_eq!(complete["organization_id"], json!(15));
    assert_eq!(complete["resources"].as_array().expect("res").len(), 8);
    // dependencies: host->os (runs_on), vuln->sw (affects), label->host (manages), host->fleet (in_fleet)
    assert_eq!(complete["dependencies"].as_array().expect("deps").len(), 4);
}

#[tokio::test]
async fn fleetdm_legacy_404_fallbacks_software_policies_mdm_teams() {
    let fleet_mock = MockServer::start(|req, _| match req.path.as_str() {
        "/api/v1/fleet/hosts" => MockResponse::json(
            200,
            json!({"hosts": [{"id": 1, "hostname": "h1", "team_id": 5}]}),
        ),
        "/api/v1/fleet/software/versions" => MockResponse::json(404, json!({})),
        "/api/v1/fleet/software/titles" => MockResponse::json(410, json!({})),
        "/api/v1/fleet/software" => MockResponse::json(
            200,
            json!({"software": [{"id": 9, "name": "git", "version": "2.45"}]}),
        ),
        "/api/v1/fleet/os_versions" => MockResponse::json(200, json!({"os_versions": []})),
        "/api/v1/fleet/vulnerabilities" => MockResponse::json(200, json!({"vulnerabilities": []})),
        "/api/v1/fleet/policies" => MockResponse::json(404, json!({})),
        "/api/v1/fleet/global/policies" => MockResponse::json(
            200,
            json!({"policies": [{"id": 22, "name": "Legacy Policy"}]}),
        ),
        "/api/v1/fleet/labels" => MockResponse::json(200, json!({"labels": []})),
        "/api/v1/fleet/mdm/profiles" => MockResponse::json(404, json!({})),
        "/api/v1/fleet/configuration_profiles" => MockResponse::json(
            200,
            json!({"profiles": [{"uuid": "legacy-prof", "name": "Legacy MDM"}]}),
        ),
        "/api/v1/fleet/fleets" => MockResponse::json(404, json!({})),
        "/api/v1/fleet/teams" => {
            MockResponse::json(200, json!({"teams": [{"id": 5, "name": "Legacy Team"}]}))
        }
        _ => MockResponse::json(404, json!({})),
    })
    .await;

    let elder_mock = MockServer::start(|req, _| {
        if req.path.ends_with("/start") {
            MockResponse::json(200, json!({"history_id": 910}))
        } else {
            MockResponse::json(200, json!({"ok": true}))
        }
    })
    .await;

    let elder = test_elder_client(&elder_mock.base_url);
    let job = json!({
        "id": 91,
        "provider": "fleetdm",
        "url": fleet_mock.base_url,
        "token": "tok_fallback"
    });

    let outcome = run_endpoint_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 5); // host + sw + policy + mdm + team
    let complete: Value =
        serde_json::from_str(&elder_mock.requests().await[1].body).expect("complete");
    assert_eq!(complete["dependencies"].as_array().expect("deps").len(), 1);
    assert_eq!(
        complete["dependencies"][0]["target_external_id"],
        json!("fleetdm:fleet:5")
    );
}

#[tokio::test]
async fn fleetdm_429_retry_after_5xx_backoff_and_free_tier_402_skip() {
    let hosts_attempts = Arc::new(AtomicUsize::new(0));
    let policies_attempts = Arc::new(AtomicUsize::new(0));
    let ha = Arc::clone(&hosts_attempts);
    let pa = Arc::clone(&policies_attempts);

    let fleet_mock = MockServer::start(move |req, _| match req.path.as_str() {
        "/api/v1/fleet/hosts" => {
            let n = ha.fetch_add(1, Ordering::SeqCst);
            if n == 0 {
                MockResponse::json(429, json!({"message": "rate limited"}))
                    .with_header("Retry-After", "0")
            } else {
                MockResponse::json(200, json!({"hosts": [{"id": 1, "hostname": "retry-host"}]}))
            }
        }
        "/api/v1/fleet/policies" => {
            let n = pa.fetch_add(1, Ordering::SeqCst);
            if n == 0 {
                MockResponse::json(503, json!({"error": "upstream unavailable"}))
            } else {
                MockResponse::json(
                    200,
                    json!({"policies": [{"id": 1, "name": "Recovered Policy"}]}),
                )
            }
        }
        "/api/v1/fleet/mdm/profiles" | "/api/v1/fleet/configuration_profiles" => {
            MockResponse::json(402, json!({"message": "Fleet Premium required"}))
        }
        "/api/v1/fleet/software/versions" => MockResponse::json(200, json!({"software": []})),
        "/api/v1/fleet/os_versions" => MockResponse::json(200, json!({"os_versions": []})),
        "/api/v1/fleet/vulnerabilities" => MockResponse::json(200, json!({"vulnerabilities": []})),
        "/api/v1/fleet/labels" => MockResponse::json(200, json!({"labels": []})),
        _ => MockResponse::json(404, json!({})),
    })
    .await;

    let elder_mock = MockServer::start(|req, _| {
        if req.path.ends_with("/start") {
            MockResponse::json(200, json!({"history_id": 920}))
        } else {
            MockResponse::json(200, json!({"ok": true}))
        }
    })
    .await;

    let elder = test_elder_client(&elder_mock.base_url);
    let job = json!({
        "id": 92,
        "provider": "fleetdm",
        "url": fleet_mock.base_url,
        "api_token": "tok",
        "retry_delay_ms": 5
    });

    let outcome = run_endpoint_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 2);
    assert_eq!(hosts_attempts.load(Ordering::SeqCst), 2);
    assert_eq!(policies_attempts.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn fleetdm_strips_all_pii_and_secrets() {
    let fleet_mock = MockServer::start(|req, _| match req.path.as_str() {
        "/api/v1/fleet/hosts" => MockResponse::json(
            200,
            json!({
                "hosts": [{
                    "id": 44,
                    "hostname": "workstation-44",
                    "platform": "darwin",
                    "device_mapping": [{"email": "alice@example.com"}],
                    "end_users": [{"idp_username": "alice"}],
                    "recovery_key": "SUPER-SECRET-RECOVERY-KEY",
                    "filevault_key": "FV-SECRET",
                    "mdm": {
                        "enrollment_status": "On",
                        "connected_to_fleet": true,
                        "encryption_key": "MDM-ENC-SECRET",
                        "macos_settings": {"recovery_key": "INNER-SECRET", "disk_encryption": "verified"}
                    }
                }]
            }),
        ),
        "/api/v1/fleet/policies" => MockResponse::json(
            200,
            json!({
                "policies": [{
                    "id": 2,
                    "name": "Install agent",
                    "install_software": {"name": "pkg", "api_token": "POL-SECRET"}
                }]
            }),
        ),
        "/api/v1/fleet/mdm/profiles" => MockResponse::json(
            200,
            json!({
                "profiles": [{
                    "profile_uuid": "p-44",
                    "name": "WiFi",
                    "platform": "darwin",
                    "mobileconfig": "<plist>SECRET-CERT</plist>",
                    "profile_contents": "SECRET-RAW"
                }]
            }),
        ),
        "/api/v1/fleet/software/versions" => MockResponse::json(200, json!({"software": []})),
        "/api/v1/fleet/os_versions" => MockResponse::json(200, json!({"os_versions": []})),
        "/api/v1/fleet/vulnerabilities" => MockResponse::json(200, json!({"vulnerabilities": []})),
        "/api/v1/fleet/labels" => MockResponse::json(200, json!({"labels": []})),
        _ => MockResponse::json(404, json!({})),
    })
    .await;

    let elder_mock = MockServer::start(|req, _| {
        if req.path.ends_with("/start") {
            MockResponse::json(200, json!({"history_id": 930}))
        } else {
            MockResponse::json(200, json!({"ok": true}))
        }
    })
    .await;
    let elder = test_elder_client(&elder_mock.base_url);

    let job = json!({
        "id": 93,
        "provider": "fleetdm",
        "url": fleet_mock.base_url,
        "api_token": "tok"
    });
    let outcome = run_endpoint_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");

    let raw_complete = elder_mock.requests().await[1].body.clone();
    for forbidden in [
        "alice@example.com",
        "SUPER-SECRET-RECOVERY-KEY",
        "FV-SECRET",
        "MDM-ENC-SECRET",
        "INNER-SECRET",
        "POL-SECRET",
        "SECRET-CERT",
        "SECRET-RAW",
        "device_mapping",
        "end_users",
    ] {
        assert!(
            !raw_complete.contains(forbidden),
            "sensitive token/PII {forbidden:?} leaked into /complete payload!"
        );
    }
}

#[tokio::test]
async fn fleetdm_multi_page_pagination_via_meta_has_next_results() {
    let fleet_mock = MockServer::start(|req, _| match req.path.as_str() {
        "/api/v1/fleet/hosts" => {
            if req.query.contains("page=0") {
                MockResponse::json(
                    200,
                    json!({
                        "hosts": [{"id": 1, "hostname": "h1"}],
                        "meta": {"has_next_results": true}
                    }),
                )
            } else {
                MockResponse::json(
                    200,
                    json!({
                        "hosts": [{"id": 2, "hostname": "h2"}],
                        "meta": {"has_next_results": false}
                    }),
                )
            }
        }
        "/api/v1/fleet/software/versions" => MockResponse::json(200, json!({"software": []})),
        "/api/v1/fleet/os_versions" => MockResponse::json(200, json!({"os_versions": []})),
        "/api/v1/fleet/vulnerabilities" => MockResponse::json(200, json!({"vulnerabilities": []})),
        "/api/v1/fleet/policies" => MockResponse::json(200, json!({"policies": []})),
        "/api/v1/fleet/labels" => MockResponse::json(200, json!({"labels": []})),
        "/api/v1/fleet/mdm/profiles" => MockResponse::json(200, json!({"profiles": []})),
        _ => MockResponse::json(404, json!({})),
    })
    .await;

    let elder_mock = MockServer::start(|req, _| {
        if req.path.ends_with("/start") {
            MockResponse::json(200, json!({"history_id": 940}))
        } else {
            MockResponse::json(200, json!({"ok": true}))
        }
    })
    .await;
    let elder = test_elder_client(&elder_mock.base_url);

    let job = json!({
        "id": 94,
        "provider": "fleetdm",
        "url": fleet_mock.base_url,
        "api_token": "tok",
        "per_page": 1
    });
    let outcome = run_endpoint_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 2);
}

#[tokio::test]
async fn iboss_token_auth_resources_and_dependencies() {
    let iboss_mock = MockServer::start(|req, _| {
        assert_eq!(
            req.headers.get("authorization").map(String::as_str),
            Some("Bearer iboss_api_tok")
        );
        assert_eq!(
            req.headers.get("x-tenant-id").map(String::as_str),
            Some("tenant-xyz")
        );
        match req.path.as_str() {
            "/api/v1/devices" => MockResponse::json(
                200,
                json!({"devices": [{"id": "d1", "hostname": "edge-gw-1", "platform": "linux", "groupId": "g1", "policyId": "p1"}]}),
            ),
            "/api/v1/groups" => MockResponse::json(
                200,
                json!({"groups": [{"id": "g1", "name": "Corporate", "memberCount": 25, "policies": ["p1"]}]}),
            ),
            "/api/v1/policies" => MockResponse::json(
                200,
                json!({"policies": [{"id": "p1", "name": "Malware Block", "enabled": true, "categories": ["Malware", "Phishing"]}]}),
            ),
            "/api/v1/categories" => MockResponse::json(
                200,
                json!({"categories": [{"id": "c1", "name": "Malware", "action": "block"}]}),
            ),
            "/api/v1/connectors" => MockResponse::json(
                200,
                json!({"connectors": [{"id": "cn1", "name": "US-East Gateway", "status": "online"}]}),
            ),
            "/api/v1/applications" => MockResponse::json(
                200,
                json!({"applications": [{"id": "a1", "name": "Slack", "category": "collaboration", "usersCount": 40}]}),
            ),
            _ => MockResponse::json(404, json!({})),
        }
    })
    .await;

    let elder_mock = MockServer::start(|req, _| {
        if req.path.ends_with("/start") {
            MockResponse::json(200, json!({"history_id": 950}))
        } else {
            MockResponse::json(200, json!({"ok": true}))
        }
    })
    .await;
    let elder = test_elder_client(&elder_mock.base_url);

    let job = json!({
        "id": 95,
        "provider": "iboss",
        "organization_id": 88,
        "url": iboss_mock.base_url,
        "api_token": "iboss_api_tok",
        "tenant_id": "tenant-xyz"
    });

    let outcome = run_endpoint_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    // device + group + policy + Malware cat + Phishing synthesized cat + connector + app = 7
    assert_eq!(outcome.items_synced, 7);

    let complete: Value =
        serde_json::from_str(&elder_mock.requests().await[1].body).expect("complete");
    // device->group, device->policy, group->policy, policy->Malware, policy->Phishing = 5
    assert_eq!(complete["dependencies"].as_array().expect("deps").len(), 5);
}

#[tokio::test]
async fn iboss_username_password_login_and_basic_fallback() {
    // 1. Login endpoint succeeds with JWT
    let iboss_login_mock = MockServer::start(|req, _| match req.path.as_str() {
        "/api/v1/auth/login" => MockResponse::json(200, json!({"token": "session_jwt_99"})),
        "/api/v1/devices" => {
            assert_eq!(
                req.headers.get("authorization").map(String::as_str),
                Some("Bearer session_jwt_99")
            );
            MockResponse::json(200, json!({"devices": [{"id": "d9", "hostname": "gw9"}]}))
        }
        _ => MockResponse::json(404, json!({})),
    })
    .await;

    let elder_mock = MockServer::start(|req, _| {
        if req.path.ends_with("/start") {
            MockResponse::json(200, json!({"history_id": 960}))
        } else {
            MockResponse::json(200, json!({"ok": true}))
        }
    })
    .await;
    let elder = test_elder_client(&elder_mock.base_url);

    let job = json!({
        "id": 96,
        "provider": "iboss",
        "url": iboss_login_mock.base_url,
        "username": "admin@corp",
        "password": "pw"
    });
    let outcome = run_endpoint_discovery(&elder, &job).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 1);

    // 2. Login endpoints return 404 -> falls back to Basic auth directly
    let iboss_basic_mock = MockServer::start(|req, _| match req.path.as_str() {
        "/api/v1/auth/login" | "/ibossauth/web/tokens" => MockResponse::json(404, json!({})),
        "/api/v1/devices" => {
            let expected = format!("Basic {}", STANDARD.encode("admin@corp:pw"));
            assert_eq!(
                req.headers.get("authorization").map(String::as_str),
                Some(expected.as_str())
            );
            MockResponse::json(200, json!({"devices": [{"id": "d10", "hostname": "gw10"}]}))
        }
        _ => MockResponse::json(404, json!({})),
    })
    .await;

    let outcome2 = run_endpoint_discovery(
        &elder,
        &json!({
            "id": 96,
            "provider": "iboss",
            "url": iboss_basic_mock.base_url,
            "username": "admin@corp",
            "password": "pw"
        }),
    )
    .await;
    assert_eq!(outcome2.status, "completed");
    assert_eq!(outcome2.items_synced, 1);
}

#[tokio::test]
async fn iboss_auth_failure_reports_failed_to_complete() {
    let iboss_mock = MockServer::start(|req, _| match req.path.as_str() {
        "/api/v1/auth/login" => MockResponse::json(401, json!({"error": "invalid credentials"})),
        _ => MockResponse::json(404, json!({})),
    })
    .await;

    let elder_mock = MockServer::start(|req, _| {
        if req.path.ends_with("/start") {
            MockResponse::json(200, json!({"history_id": 970}))
        } else {
            MockResponse::json(200, json!({"ok": true}))
        }
    })
    .await;
    let elder = test_elder_client(&elder_mock.base_url);

    let outcome = run_endpoint_discovery(
        &elder,
        &json!({
            "id": 97,
            "provider": "iboss",
            "url": iboss_mock.base_url,
            "username": "bad",
            "password": "bad"
        }),
    )
    .await;
    assert_eq!(outcome.status, "failed");
    assert!(outcome.error.is_some());

    let complete: Value =
        serde_json::from_str(&elder_mock.requests().await[1].body).expect("complete");
    assert_eq!(complete["status"], json!("failed"));
    assert_eq!(complete["history_id"], json!(970));
}

#[tokio::test]
async fn discovery_partial_failure_reports_partial_status() {
    let fleet_mock = MockServer::start(|req, _| match req.path.as_str() {
        "/api/v1/fleet/hosts" => {
            MockResponse::json(200, json!({"hosts": [{"id": 1, "hostname": "ok-host"}]}))
        }
        "/api/v1/fleet/vulnerabilities" => {
            MockResponse::json(500, json!({"error": "vuln DB down"}))
        }
        "/api/v1/fleet/software/versions" => MockResponse::json(200, json!({"software": []})),
        "/api/v1/fleet/os_versions" => MockResponse::json(200, json!({"os_versions": []})),
        "/api/v1/fleet/policies" => MockResponse::json(200, json!({"policies": []})),
        "/api/v1/fleet/labels" => MockResponse::json(200, json!({"labels": []})),
        "/api/v1/fleet/mdm/profiles" => MockResponse::json(200, json!({"profiles": []})),
        _ => MockResponse::json(404, json!({})),
    })
    .await;

    let elder_mock = MockServer::start(|req, _| {
        if req.path.ends_with("/start") {
            MockResponse::json(200, json!({"history_id": 980}))
        } else {
            MockResponse::json(200, json!({"ok": true}))
        }
    })
    .await;
    let elder = test_elder_client(&elder_mock.base_url);

    let outcome = run_endpoint_discovery(
        &elder,
        &json!({
            "id": 98,
            "provider": "fleetdm",
            "url": fleet_mock.base_url,
            "api_token": "tok",
            "retry_delay_ms": 1
        }),
    )
    .await;
    assert_eq!(outcome.status, "partial");
    assert_eq!(outcome.items_synced, 1);
    assert_eq!(outcome.items_failed, 1);
    assert!(outcome.error.as_deref().is_some_and(|e| e.contains("500")));

    let complete: Value =
        serde_json::from_str(&elder_mock.requests().await[1].body).expect("complete");
    assert_eq!(complete["status"], json!("partial"));
}

#[tokio::test]
async fn discovery_unsupported_provider_and_start_failure() {
    let elder_mock = MockServer::start(|req, _| {
        if req.path == "/api/v1/integrations/internal/discovery/99/start" {
            MockResponse::json(200, json!({"history_id": 990}))
        } else if req.path == "/api/v1/integrations/internal/discovery/100/start" {
            MockResponse::json(500, json!({"error": "db down"}))
        } else {
            MockResponse::json(200, json!({"ok": true}))
        }
    })
    .await;
    let elder = test_elder_client(&elder_mock.base_url);

    let unsupported =
        run_endpoint_discovery(&elder, &json!({"id": 99, "provider": "unknown_edr"})).await;
    assert_eq!(unsupported.status, "failed");
    let complete: Value =
        serde_json::from_str(&elder_mock.requests().await[1].body).expect("complete");
    assert_eq!(complete["status"], json!("failed"));

    let start_err = run_endpoint_discovery(
        &elder,
        &json!({"id": 100, "provider": "fleetdm", "url": "https://f.local", "api_token": "t"}),
    )
    .await;
    assert_eq!(start_err.status, "failed");
    assert!(start_err
        .error
        .as_deref()
        .is_some_and(|s| s.contains("/start")));
}

#[tokio::test]
async fn github_sync_push_only_direction_skips_pull() {
    let gh_mock = MockServer::start(|req, _| match (req.method.as_str(), req.path.as_str()) {
        ("POST", "/repos/org/repo/issues") => MockResponse::json(
            201,
            json!({"number": 401, "state": "open", "updated_at": "2026-11-20T00:00:00Z"}),
        ),
        _ => MockResponse::json(500, json!({"unexpected": req.path})),
    })
    .await;
    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 70,
        "platform": "github",
        "direction": "push",
        "owner": "org",
        "repo_name": "repo",
        "api_token": "ghp_tok",
        "api_base_url": gh_mock.base_url,
        "local_items": [{
            "id": 901,
            "title": "Push-only issue",
            "status": "OPEN"
        }]
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 1);
    assert_eq!(gh_mock.requests().await.len(), 1);
    assert_eq!(gh_mock.requests().await[0].method, "POST");
}

#[tokio::test]
async fn github_app_escaped_newline_pem_and_partial_push_failure() {
    let rsa = ephemeral_rsa();
    let escaped_pem = rsa.pkcs8_pem.replace('\n', "\\n");

    let gh_mock = MockServer::start(|req, _| match (req.method.as_str(), req.path.as_str()) {
        ("POST", "/app/installations/12/access_tokens") => {
            MockResponse::json(201, json!({"token": "ghs_escaped_ok"}))
        }
        ("GET", "/repos/org/repo/issues") => MockResponse::json(
            200,
            json!([{"number": 1, "title": "Pulled 1", "state": "open", "labels": []}]),
        ),
        ("POST", "/repos/org/repo/issues") => {
            MockResponse::json(422, json!({"message": "Validation Failed"}))
        }
        _ => MockResponse::json(404, json!({})),
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let config = json!({
        "id": 71,
        "platform": "github",
        "two_way_create": true,
        "owner": "org",
        "repo_name": "repo",
        "app_id": "321",
        "installation_id": 12,
        "private_key": escaped_pem,
        "api_base_url": gh_mock.base_url,
        "local_items": [{"id": 902, "title": "Will fail 422", "status": "OPEN"}]
    });

    let outcome = run_sync_config(&elder, &config).await;
    assert_eq!(outcome.status, "partial");
    assert_eq!(outcome.items_synced, 1);
    assert_eq!(outcome.items_failed, 1);
    assert!(outcome.error.as_deref().is_some_and(|s| s.contains("422")));

    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    assert_eq!(apply_body["success"], json!(false));
    assert_eq!(apply_body["items_synced"], json!(1));
    assert_eq!(apply_body["items_failed"], json!(1));
}

#[tokio::test]
async fn openproject_global_work_packages_and_links_next_pagination() {
    let op_mock = MockServer::start(|req, _| {
        assert_eq!(req.path, "/api/v3/work_packages");
        if req.query.contains("offset=2") {
            MockResponse::json(
                200,
                json!({
                    "total": 2,
                    "_embedded": {
                        "elements": [{"id": 2, "subject": "Global WP 2", "updatedAt": "2026-11-02T00:00:00Z"}]
                    }
                }),
            )
        } else {
            MockResponse::json(
                200,
                json!({
                    "total": 2,
                    "_links": {"next": {"href": "/api/v3/work_packages?offset=2"}},
                    "_embedded": {
                        "elements": [{"id": 1, "subject": "Global WP 1", "updatedAt": "2026-11-01T00:00:00Z"}]
                    }
                }),
            )
        }
    })
    .await;

    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let outcome = run_sync_config(
        &elder,
        &json!({
            "id": 72,
            "platform": "openproject",
            "api_key": "k",
            "base_url": op_mock.base_url
        }),
    )
    .await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 2);
}

#[tokio::test]
async fn trello_pull_only_ignores_local_items_and_maps_in_progress() {
    let tr_mock = MockServer::start(|req, _| match (req.method.as_str(), req.path.as_str()) {
        ("GET", "/1/boards/b1/lists") => {
            MockResponse::json(200, json!([{"id": "l1", "name": "Doing", "closed": false}]))
        }
        ("GET", "/1/boards/b1/cards") => MockResponse::json(
            200,
            json!([{"id": "c1", "name": "Card A", "desc": "D", "idList": "l1", "closed": false}]),
        ),
        _ => MockResponse::json(500, json!({"unexpected": req.path})),
    })
    .await;
    let elder_mock = MockServer::start(|_, _| MockResponse::json(200, json!({"ok": true}))).await;
    let elder = test_elder_client(&elder_mock.base_url);

    let outcome = run_sync_config(
        &elder,
        &json!({
            "id": 73,
            "platform": "trello",
            "direction": "pull",
            "two_way_create": true,
            "api_key": "k",
            "api_token": "t",
            "board_id": "b1",
            "base_url": tr_mock.base_url,
            "local_items": [{"id": 1, "title": "Should not push in pull mode"}]
        }),
    )
    .await;
    assert_eq!(outcome.status, "completed");
    assert_eq!(outcome.items_synced, 1);

    let apply_body: Value =
        serde_json::from_str(&elder_mock.requests().await[0].body).expect("apply");
    assert_eq!(
        apply_body["pulled_items"][0]["status"],
        json!("IN_PROGRESS")
    );
    assert!(apply_body["pushed_mappings"]
        .as_array()
        .expect("p")
        .is_empty());
}

#[tokio::test]
async fn fleetdm_401_unauthorized_fails_and_posts_failed_to_complete() {
    let fleet_mock =
        MockServer::start(|_, _| MockResponse::json(401, json!({"message": "Invalid token"})))
            .await;
    let elder_mock = MockServer::start(|req, _| {
        if req.path.ends_with("/start") {
            MockResponse::json(200, json!({"history_id": 999}))
        } else {
            MockResponse::json(200, json!({"ok": true}))
        }
    })
    .await;
    let elder = test_elder_client(&elder_mock.base_url);

    let outcome = run_endpoint_discovery(
        &elder,
        &json!({
            "id": 101,
            "provider": "fleetdm",
            "url": fleet_mock.base_url,
            "api_token": "bad_tok"
        }),
    )
    .await;
    assert_eq!(outcome.status, "failed");
    assert!(outcome.error.as_deref().is_some_and(|e| e.contains("401")));
    let complete: Value =
        serde_json::from_str(&elder_mock.requests().await[1].body).expect("complete");
    assert_eq!(complete["status"], json!("failed"));
    assert_eq!(complete["history_id"], json!(999));
}
