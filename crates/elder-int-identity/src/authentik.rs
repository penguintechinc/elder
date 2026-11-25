//! Authentik v3 identity connector (`Bearer` token auth and paginated users/groups).
//!
//! PRIVACY INVARIANT: Never log `api_token` or user emails/PII in `tracing::*`
//! statements or error messages.

use std::collections::HashSet;

use serde_json::Value;
use url::Url;

use crate::{
    build_rustls_http_client, first_non_empty, non_empty_str, validate_http_url,
    DirectorySyncBatch, IdentityError, NormalizedGroup, NormalizedMembership, NormalizedUser,
    MAX_PAGINATION_PAGES,
};

#[derive(Debug, Clone)]
struct AuthentikConfig {
    base_url: Url,
    api_token: String,
    verify_ssl: bool,
    sync_users: bool,
    sync_groups: bool,
}

impl AuthentikConfig {
    fn from_value(config: &Value) -> Result<Self, IdentityError> {
        let raw_base = first_non_empty(config, &["api_base_url", "base_url", "url"])
            .or_else(|| {
                first_non_empty(config, &["domain", "authentik_domain"]).map(|dom| {
                    if dom.starts_with("http://") || dom.starts_with("https://") {
                        dom
                    } else {
                        format!("https://{dom}")
                    }
                })
            })
            .ok_or_else(|| {
                IdentityError::Config("Authentik config missing domain or base_url".into())
            })?;

        let trimmed = raw_base.trim().trim_end_matches('/');
        let normalized_root = trimmed.strip_suffix("/api/v3").unwrap_or(trimmed);
        let base_url = validate_http_url(normalized_root, "Authentik base_url")?;

        let api_token = first_non_empty(config, &["api_token", "token", "authentik_api_token"])
            .ok_or_else(|| IdentityError::Config("Authentik config missing api_token".into()))?;

        let verify_ssl = config
            .get("verify_ssl")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        let sync_users = config
            .get("sync_users")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        let sync_groups = config
            .get("sync_groups")
            .and_then(Value::as_bool)
            .unwrap_or(true);

        Ok(Self {
            base_url,
            api_token,
            verify_ssl,
            sync_users,
            sync_groups,
        })
    }
}

/// Fetch normalized users, groups, and group memberships from Authentik `/api/v3`.
pub async fn fetch_directory(config: &Value) -> Result<DirectorySyncBatch, IdentityError> {
    let cfg = AuthentikConfig::from_value(config)?;
    let http = build_rustls_http_client(cfg.verify_ssl, "elder-svc-integrations/0.1")?;

    let mut batch = DirectorySyncBatch::default();

    if cfg.sync_users {
        let raw_users =
            paginate_authentik(&http, &cfg.base_url, "/api/v3/core/users/", &cfg.api_token).await?;
        for raw in &raw_users {
            if let Some(user) = normalize_authentik_user(raw) {
                batch.users.push(user);
            }
        }
    }

    if cfg.sync_groups {
        let raw_groups =
            paginate_authentik(&http, &cfg.base_url, "/api/v3/core/groups/", &cfg.api_token)
                .await?;
        let mut seen_memberships: HashSet<(String, String)> = HashSet::new();

        for raw_grp in &raw_groups {
            let Some(group) = normalize_authentik_group(raw_grp) else {
                continue;
            };
            for uid in extract_group_member_ids(raw_grp) {
                if seen_memberships.insert((uid.clone(), group.external_id.clone())) {
                    batch.memberships.push(NormalizedMembership {
                        user_external_id: uid.clone(),
                        group_external_id: group.external_id.clone(),
                        provider_member_id: uid,
                    });
                }
            }
            batch.groups.push(group);
        }
    }

    Ok(batch)
}

async fn paginate_authentik(
    http: &reqwest::Client,
    base_url: &Url,
    endpoint_path: &str,
    api_token: &str,
) -> Result<Vec<Value>, IdentityError> {
    let mut initial_url = base_url
        .join(endpoint_path)
        .map_err(|e| IdentityError::Config(format!("invalid Authentik endpoint: {e}")))?;
    initial_url
        .query_pairs_mut()
        .append_pair("page_size", "100");

    let mut results = Vec::new();
    let mut current_url: Option<Url> = Some(initial_url.clone());
    let mut visited: HashSet<String> = HashSet::new();

    for _ in 0..MAX_PAGINATION_PAGES {
        let Some(url) = current_url.take() else {
            break;
        };
        if !visited.insert(url.as_str().to_string()) {
            return Err(IdentityError::Upstream(
                "Authentik pagination loop detected duplicate page URL".into(),
            ));
        }

        let resp = http
            .get(url.as_str())
            .bearer_auth(api_token)
            .header(reqwest::header::ACCEPT, "application/json")
            .send()
            .await
            .map_err(|e| IdentityError::Upstream(format!("Authentik API transport error: {e}")))?;

        let status = resp.status();
        if status.as_u16() == 401 || status.as_u16() == 403 {
            return Err(IdentityError::Auth(format!(
                "Authentik API authentication rejected with HTTP {}",
                status.as_u16()
            )));
        }
        if !status.is_success() {
            return Err(IdentityError::Upstream(format!(
                "Authentik API returned HTTP {}",
                status.as_u16()
            )));
        }

        let body: Value = resp.json().await.map_err(|e| {
            IdentityError::Upstream(format!("invalid Authentik JSON response: {e}"))
        })?;

        match &body {
            Value::Array(arr) => {
                results.extend(arr.iter().cloned());
                break;
            }
            Value::Object(map) => {
                if let Some(arr) = map.get("results").and_then(Value::as_array) {
                    results.extend(arr.iter().cloned());
                } else {
                    results.push(body.clone());
                    break;
                }

                current_url = resolve_next_page(base_url, endpoint_path, &body)?;
            }
            _ => break,
        }
    }

    Ok(results)
}

fn resolve_next_page(
    base_url: &Url,
    endpoint_path: &str,
    body: &Value,
) -> Result<Option<Url>, IdentityError> {
    let next_val = body
        .get("pagination")
        .and_then(|p| p.get("next"))
        .or_else(|| body.get("next"));

    let Some(val) = next_val else {
        return Ok(None);
    };

    match val {
        Value::Null => Ok(None),
        Value::Number(num) => {
            let Some(page) = num.as_u64().filter(|n| *n > 0) else {
                return Ok(None);
            };
            let mut next_url = base_url
                .join(endpoint_path)
                .map_err(|e| IdentityError::Upstream(format!("invalid next URL: {e}")))?;
            next_url
                .query_pairs_mut()
                .append_pair("page", &page.to_string())
                .append_pair("page_size", "100");
            Ok(Some(next_url))
        }
        Value::String(raw) => {
            let trimmed = raw.trim();
            if trimmed.is_empty() {
                return Ok(None);
            }
            let resolved = if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
                Url::parse(trimmed)
            } else if trimmed.starts_with('?') {
                let mut u = base_url.join(endpoint_path).map_err(|e| {
                    IdentityError::Upstream(format!("invalid Authentik endpoint: {e}"))
                })?;
                u.set_query(Some(trimmed.trim_start_matches('?')));
                Ok(u)
            } else {
                base_url.join(trimmed)
            }
            .map_err(|e| IdentityError::Upstream(format!("invalid Authentik next URL: {e}")))?;

            if resolved.scheme() != base_url.scheme()
                || resolved.host_str() != base_url.host_str()
                || resolved.port_or_known_default() != base_url.port_or_known_default()
            {
                return Err(IdentityError::Upstream(
                    "Authentik pagination next URL crossed origin boundary".into(),
                ));
            }
            Ok(Some(resolved))
        }
        _ => Ok(None),
    }
}

fn value_as_id_string(v: Option<&Value>) -> Option<String> {
    match v {
        Some(Value::String(s)) => {
            let t = s.trim();
            (!t.is_empty()).then(|| t.to_string())
        }
        Some(Value::Number(n)) => Some(n.to_string()),
        _ => None,
    }
}

fn normalize_authentik_user(raw: &Value) -> Option<NormalizedUser> {
    let ext_id = value_as_id_string(
        raw.get("pk")
            .or_else(|| raw.get("uuid"))
            .or_else(|| raw.get("id")),
    )?;
    let is_service_account = raw
        .get("is_service_account")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        || non_empty_str(raw.get("type")).is_some_and(|t| t == "service_account");
    let identity_type = if is_service_account {
        "service_account"
    } else {
        "human"
    };
    let is_active = raw
        .get("is_active")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let username = non_empty_str(raw.get("username")).filter(|u| !u.contains('@'));

    Some(NormalizedUser {
        external_id: ext_id.clone(),
        id: ext_id,
        username,
        identity_type: identity_type.to_string(),
        is_active,
    })
}

fn normalize_authentik_group(raw: &Value) -> Option<NormalizedGroup> {
    let ext_id = value_as_id_string(
        raw.get("pk")
            .or_else(|| raw.get("num_pk"))
            .or_else(|| raw.get("id")),
    )?;
    let name = non_empty_str(raw.get("name")).unwrap_or_else(|| ext_id.clone());
    let description = non_empty_str(raw.get("description"));

    Some(NormalizedGroup {
        external_id: ext_id.clone(),
        id: ext_id,
        name,
        description,
        is_active: true,
    })
}

fn extract_group_member_ids(raw_grp: &Value) -> Vec<String> {
    let mut ids = Vec::new();
    if let Some(arr) = raw_grp.get("users_obj").and_then(Value::as_array) {
        for user_obj in arr {
            if let Some(id) = value_as_id_string(user_obj.get("pk").or_else(|| user_obj.get("id")))
            {
                ids.push(id);
            }
        }
    }
    if let Some(arr) = raw_grp.get("users").and_then(Value::as_array) {
        for item in arr {
            if let Some(id) = value_as_id_string(Some(item)) {
                ids.push(id);
            }
        }
    }
    ids
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{run_identity_discovery, run_identity_provider};
    use elder_svc_common::{ElderApiClient, ServiceTokenProvider};
    use serde_json::json;
    use std::sync::{Arc, Mutex};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    fn test_elder_client(base_url: &str) -> ElderApiClient {
        let auth = ServiceTokenProvider::from_env_lookup(
            "svc-integrations",
            vec!["integrations:sync".into()],
            |k| match k {
                "JWT_SECRET_KEY" => Some("x".repeat(32)),
                _ => None,
            },
        )
        .expect("auth");
        ElderApiClient::new(base_url, auth).expect("client")
    }

    async fn read_req(sock: &mut tokio::net::TcpStream) -> (String, String, String, String) {
        let mut buf = Vec::new();
        let mut chunk = [0u8; 4096];
        let hend = loop {
            let n = sock.read(&mut chunk).await.unwrap_or(0);
            if n == 0 {
                break None;
            }
            buf.extend_from_slice(&chunk[..n]);
            if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                break Some(pos + 4);
            }
        };
        let Some(hend) = hend else {
            return (String::new(), String::new(), String::new(), String::new());
        };
        let hdrs = String::from_utf8_lossy(&buf[..hend]).to_string();
        let clen = hdrs
            .lines()
            .find_map(|l| {
                let (k, v) = l.split_once(':')?;
                k.eq_ignore_ascii_case("content-length")
                    .then(|| v.trim().parse::<usize>().ok())
                    .flatten()
            })
            .unwrap_or(0);
        while buf.len() < hend + clen {
            let n = sock.read(&mut chunk).await.unwrap_or(0);
            if n == 0 {
                break;
            }
            buf.extend_from_slice(&chunk[..n]);
        }
        let first = hdrs.lines().next().unwrap_or("");
        let mut parts = first.split_whitespace();
        let method = parts.next().unwrap_or("").to_string();
        let path = parts.next().unwrap_or("").to_string();
        let auth = hdrs
            .lines()
            .find_map(|l| {
                let (k, v) = l.split_once(':')?;
                k.eq_ignore_ascii_case("authorization")
                    .then(|| v.trim().to_string())
            })
            .unwrap_or_default();
        let body = String::from_utf8_lossy(&buf[hend..hend + clen]).to_string();
        (method, path, auth, body)
    }

    #[tokio::test]
    async fn authentik_paginates_users_and_groups_and_applies_to_iam() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base_url = format!("http://{addr}");

        let recorded_apply: Arc<Mutex<Option<Value>>> = Arc::new(Mutex::new(None));
        let recorded_auths: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let apply_ref = Arc::clone(&recorded_apply);
        let auths_ref = Arc::clone(&recorded_auths);

        let server = tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let a_ref = Arc::clone(&apply_ref);
                let au_ref = Arc::clone(&auths_ref);
                tokio::spawn(async move {
                    let (method, path, auth, body) = read_req(&mut sock).await;
                    if path.starts_with("/api/v3/") {
                        au_ref.lock().unwrap().push(auth);
                    }
                    let (status, resp_body) = match path.as_str() {
                        "/api/v3/core/users/?page_size=100" => (
                            200,
                            json!({
                                "pagination": {"next": 2},
                                "results": [
                                    {"pk": 10, "username": "alice", "is_active": true, "is_service_account": false}
                                ]
                            })
                            .to_string(),
                        ),
                        "/api/v3/core/users/?page=2&page_size=100" => (
                            200,
                            json!({
                                "pagination": {"next": 0},
                                "results": [
                                    {"pk": 11, "username": "ci-bot", "is_active": true, "is_service_account": true}
                                ]
                            })
                            .to_string(),
                        ),
                        "/api/v3/core/groups/?page_size=100" => (
                            200,
                            json!({
                                "pagination": {"next": "/api/v3/core/groups/?page=2&page_size=100"},
                                "results": [
                                    {"pk": "grp-1", "name": "DevOps", "users": [10], "users_obj": [{"pk": 10}]}
                                ]
                            })
                            .to_string(),
                        ),
                        "/api/v3/core/groups/?page=2&page_size=100" => (
                            200,
                            json!({
                                "pagination": {"next": null},
                                "results": [
                                    {"pk": "grp-2", "name": "Bots", "users": [11]}
                                ]
                            })
                            .to_string(),
                        ),
                        "/api/v1/integrations/internal/iam/21/apply" if method == "POST" => {
                            *a_ref.lock().unwrap() = Some(serde_json::from_str(&body).unwrap());
                            (200, json!({"provider_id": 21, "success": true}).to_string())
                        }
                        _ => (404, json!({"path": path}).to_string()),
                    };

                    let resp = format!(
                        "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{resp_body}",
                        resp_body.len()
                    );
                    let _ = sock.write_all(resp.as_bytes()).await;
                    let _ = sock.shutdown().await;
                });
            }
        });

        let provider = json!({
            "id": 21,
            "provider_type": "authentik",
            "config": {
                "base_url": format!("{base_url}/api/v3"),
                "api_token": "ak-bearer-secret"
            }
        });
        let elder = test_elder_client(&base_url);
        let outcome = run_identity_provider(&elder, &provider).await;
        server.abort();

        assert_eq!(outcome.status, "completed");
        assert_eq!(outcome.items_synced, 6); // 2 users + 2 groups + 2 deduplicated memberships

        let auths = recorded_auths.lock().unwrap().clone();
        assert_eq!(auths.len(), 4);
        assert!(auths.iter().all(|a| a == "Bearer ak-bearer-secret"));

        let apply = recorded_apply.lock().unwrap().clone().unwrap();
        assert_eq!(apply["success"], true);
        assert_eq!(apply["users"][1]["identity_type"], "service_account");
        assert_eq!(apply["memberships"].as_array().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn authentik_handles_auth_error_and_submits_failure() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base_url = format!("http://{addr}");

        let recorded_complete: Arc<Mutex<Option<Value>>> = Arc::new(Mutex::new(None));
        let comp_ref = Arc::clone(&recorded_complete);

        let server = tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let c_ref = Arc::clone(&comp_ref);
                tokio::spawn(async move {
                    let (method, path, _auth, body) = read_req(&mut sock).await;
                    let (status, resp_body) = match (method.as_str(), path.as_str()) {
                        ("POST", "/api/v1/integrations/internal/discovery/88/start") => {
                            (200, json!({"history_id": 12, "job_id": 88}).to_string())
                        }
                        ("GET", p) if p.starts_with("/api/v3/") => {
                            (401, json!({"detail": "Invalid token"}).to_string())
                        }
                        ("POST", "/api/v1/integrations/internal/discovery/88/complete") => {
                            *c_ref.lock().unwrap() = Some(serde_json::from_str(&body).unwrap());
                            (200, json!({"job_id": 88, "status": "failed"}).to_string())
                        }
                        _ => (404, json!({}).to_string()),
                    };
                    let resp = format!(
                        "HTTP/1.1 {status} TEST\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{resp_body}",
                        resp_body.len()
                    );
                    let _ = sock.write_all(resp.as_bytes()).await;
                    let _ = sock.shutdown().await;
                });
            }
        });

        let job = json!({
            "id": 88,
            "provider": "authentik",
            "organization_id": 1,
            "config": {
                "base_url": base_url,
                "api_token": "bad-token"
            }
        });
        let elder = test_elder_client(&base_url);
        let outcome = run_identity_discovery(&elder, &job).await;
        server.abort();

        assert_eq!(outcome.status, "failed");
        let comp = recorded_complete.lock().unwrap().clone().unwrap();
        assert_eq!(comp["status"], "failed");
        assert_eq!(comp["history_id"], 12);
    }

    #[test]
    fn authentik_rejects_cross_origin_pagination_and_missing_fields() {
        assert!(matches!(
            AuthentikConfig::from_value(&json!({"domain": "auth.example.com"})),
            Err(IdentityError::Config(_))
        ));
        let base = Url::parse("https://auth.example.com").unwrap();
        let err = resolve_next_page(
            &base,
            "/api/v3/core/users/",
            &json!({"pagination": {"next": "https://attacker.example.com/p2"}}),
        );
        assert!(err.is_err());
    }
}
