//! Okta directory connector (`SSWS` token auth and RFC 8288 `Link` pagination).
//!
//! PRIVACY INVARIANT: Never log `api_token` or user emails/PII in `tracing::*`
//! statements or error messages.

use std::collections::HashSet;

use reqwest::header::HeaderMap;
use serde_json::Value;
use url::Url;

use crate::{
    build_rustls_http_client, first_non_empty, non_empty_str, validate_http_url,
    DirectorySyncBatch, IdentityError, NormalizedGroup, NormalizedMembership, NormalizedUser,
    MAX_PAGINATION_PAGES,
};

#[derive(Debug, Clone)]
struct OktaConfig {
    base_url: Url,
    api_token: String,
    sync_users: bool,
    sync_groups: bool,
    user_filter: Option<String>,
}

impl OktaConfig {
    fn from_value(config: &Value) -> Result<Self, IdentityError> {
        let base_raw = first_non_empty(config, &["api_base_url", "base_url", "url", "okta_url"])
            .or_else(|| {
                first_non_empty(config, &["domain", "okta_domain"]).map(|dom| {
                    if dom.starts_with("http://") || dom.starts_with("https://") {
                        dom
                    } else {
                        format!("https://{dom}")
                    }
                })
            })
            .ok_or_else(|| {
                IdentityError::Config("Okta config missing domain or base_url".into())
            })?;

        let base_url = validate_http_url(&base_raw, "Okta base_url")?;
        let api_token = first_non_empty(config, &["api_token", "token", "okta_api_token"])
            .ok_or_else(|| IdentityError::Config("Okta config missing api_token".into()))?;

        let sync_users = config
            .get("sync_users")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        let sync_groups = config
            .get("sync_groups")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        let user_filter = non_empty_str(config.get("user_filter"));

        Ok(Self {
            base_url,
            api_token,
            sync_users,
            sync_groups,
            user_filter,
        })
    }
}

/// Fetch normalized users, groups, and group memberships from Okta.
pub async fn fetch_directory(config: &Value) -> Result<DirectorySyncBatch, IdentityError> {
    let cfg = OktaConfig::from_value(config)?;
    let http = build_rustls_http_client(true, "elder-svc-integrations/0.1")?;
    let auth_header = format!("SSWS {}", cfg.api_token);

    let mut batch = DirectorySyncBatch::default();

    if cfg.sync_users {
        let mut users_url = build_endpoint_url(&cfg.base_url, &["api", "v1", "users"])?;
        if let Some(filter) = &cfg.user_filter {
            users_url.query_pairs_mut().append_pair("filter", filter);
        }
        let raw_users = paginate_okta(&http, &cfg.base_url, users_url, &auth_header).await?;
        for raw in &raw_users {
            if let Some(user) = normalize_okta_user(raw) {
                batch.users.push(user);
            }
        }
    }

    if cfg.sync_groups {
        let groups_url = build_endpoint_url(&cfg.base_url, &["api", "v1", "groups"])?;
        let raw_groups = paginate_okta(&http, &cfg.base_url, groups_url, &auth_header).await?;

        let mut member_failures = 0_usize;
        for raw_grp in &raw_groups {
            let group_type = non_empty_str(raw_grp.get("type")).unwrap_or_default();
            if !group_type.is_empty() && group_type != "OKTA_GROUP" {
                continue;
            }
            let Some(group) = normalize_okta_group(raw_grp) else {
                continue;
            };
            let members_url = build_endpoint_url(
                &cfg.base_url,
                &["api", "v1", "groups", &group.external_id, "users"],
            )?;
            match paginate_okta(&http, &cfg.base_url, members_url, &auth_header).await {
                Ok(raw_members) => {
                    for mem in &raw_members {
                        if let Some(uid) = non_empty_str(mem.get("id")) {
                            batch.memberships.push(NormalizedMembership {
                                user_external_id: uid.clone(),
                                group_external_id: group.external_id.clone(),
                                provider_member_id: uid,
                            });
                        }
                    }
                }
                Err(err) => {
                    member_failures += 1;
                    tracing::warn!(error = %err, "Failed fetching Okta group members");
                }
            }
            batch.groups.push(group);
        }
        if member_failures > 0 {
            batch.items_failed = member_failures;
            batch.warning = Some(format!(
                "failed fetching members for {member_failures} Okta group(s)"
            ));
        }
    }

    Ok(batch)
}

fn build_endpoint_url(base_url: &Url, segments: &[&str]) -> Result<Url, IdentityError> {
    let mut url = base_url.clone();
    {
        let mut path = url
            .path_segments_mut()
            .map_err(|_| IdentityError::Config("Okta base_url cannot be a base".into()))?;
        path.pop_if_empty();
        for seg in segments {
            path.push(seg);
        }
    }
    Ok(url)
}

async fn paginate_okta(
    http: &reqwest::Client,
    base_url: &Url,
    initial_url: Url,
    auth_header: &str,
) -> Result<Vec<Value>, IdentityError> {
    let mut results = Vec::new();
    let mut current_url: Option<Url> = Some(initial_url);
    let mut visited: HashSet<String> = HashSet::new();

    for _ in 0..MAX_PAGINATION_PAGES {
        let Some(url) = current_url.take() else {
            break;
        };
        if !visited.insert(url.as_str().to_string()) {
            return Err(IdentityError::Upstream(
                "Okta pagination loop detected duplicate Link URL".into(),
            ));
        }

        let resp = http
            .get(url)
            .header(reqwest::header::AUTHORIZATION, auth_header)
            .header(reqwest::header::ACCEPT, "application/json")
            .send()
            .await
            .map_err(|e| IdentityError::Upstream(format!("Okta API transport error: {e}")))?;

        let status = resp.status();
        if status.as_u16() == 401 || status.as_u16() == 403 {
            return Err(IdentityError::Auth(format!(
                "Okta API authentication rejected with HTTP {}",
                status.as_u16()
            )));
        }
        if !status.is_success() {
            return Err(IdentityError::Upstream(format!(
                "Okta API returned HTTP {}",
                status.as_u16()
            )));
        }

        let next_url = parse_next_link_from_headers(resp.headers(), base_url)?;
        let body: Value = resp
            .json()
            .await
            .map_err(|e| IdentityError::Upstream(format!("invalid Okta JSON response: {e}")))?;

        match body {
            Value::Array(items) => results.extend(items),
            other => results.push(other),
        }

        current_url = next_url;
    }

    Ok(results)
}

/// Parse RFC 8288 `Link` headers for `rel="next"`, validating same-origin safety.
pub fn parse_next_link_from_headers(
    headers: &HeaderMap,
    base_url: &Url,
) -> Result<Option<Url>, IdentityError> {
    for raw_val in headers.get_all(reqwest::header::LINK) {
        let Ok(header_str) = raw_val.to_str() else {
            continue;
        };
        if let Some(next_raw) = parse_next_link_header(header_str) {
            let resolved = base_url
                .join(&next_raw)
                .map_err(|e| IdentityError::Upstream(format!("invalid Okta Link URL: {e}")))?;
            if resolved.scheme() != base_url.scheme()
                || resolved.host_str() != base_url.host_str()
                || resolved.port_or_known_default() != base_url.port_or_known_default()
            {
                return Err(IdentityError::Upstream(
                    "Okta Link rel=\"next\" URL crossed origin boundary".into(),
                ));
            }
            return Ok(Some(resolved));
        }
    }
    Ok(None)
}

/// Extract the URL from an RFC 8288 `Link` header entry carrying `rel="next"`.
pub fn parse_next_link_header(link_header: &str) -> Option<String> {
    for entry in link_header.split(',') {
        let mut parts = entry.split(';');
        let Some(url_part) = parts.next().map(str::trim) else {
            continue;
        };
        if !(url_part.starts_with('<') && url_part.ends_with('>')) {
            continue;
        }
        let has_next_rel = parts.any(|param| {
            let trimmed = param.trim();
            let Some((k, v)) = trimmed.split_once('=') else {
                return false;
            };
            if !k.trim().eq_ignore_ascii_case("rel") {
                return false;
            }
            let rel_vals = v.trim().trim_matches('"').trim_matches('\'');
            rel_vals
                .split_whitespace()
                .any(|token| token.eq_ignore_ascii_case("next"))
        });
        if has_next_rel {
            let inner = url_part[1..url_part.len() - 1].trim();
            if !inner.is_empty() {
                return Some(inner.to_string());
            }
        }
    }
    None
}

fn normalize_okta_user(raw: &Value) -> Option<NormalizedUser> {
    let ext_id = non_empty_str(raw.get("id"))?;
    let status = non_empty_str(raw.get("status")).unwrap_or_else(|| "ACTIVE".to_string());
    let is_active = !matches!(
        status.to_ascii_uppercase().as_str(),
        "DEPROVISIONED" | "SUSPENDED"
    );
    let login = raw
        .get("profile")
        .and_then(|p| non_empty_str(p.get("login")))
        .filter(|l| !l.contains('@'));

    Some(NormalizedUser {
        external_id: ext_id.clone(),
        id: ext_id,
        username: login,
        identity_type: "human".to_string(),
        is_active,
    })
}

fn normalize_okta_group(raw: &Value) -> Option<NormalizedGroup> {
    let ext_id = non_empty_str(raw.get("id"))?;
    let profile = raw.get("profile");
    let name = profile
        .and_then(|p| non_empty_str(p.get("name")))
        .or_else(|| non_empty_str(raw.get("name")))
        .unwrap_or_else(|| ext_id.clone());
    let description = profile
        .and_then(|p| non_empty_str(p.get("description")))
        .or_else(|| non_empty_str(raw.get("description")));

    Some(NormalizedGroup {
        external_id: ext_id.clone(),
        id: ext_id,
        name,
        description,
        is_active: true,
    })
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

    #[test]
    fn parses_link_next_headers_and_rejects_cross_origin() {
        let base = Url::parse("https://dev-123.okta.com").unwrap();
        assert_eq!(
            parse_next_link_header(
                r#"<https://dev-123.okta.com/api/v1/users?after=1>; rel="self", <https://dev-123.okta.com/api/v1/users?after=2>; rel="next""#
            ),
            Some("https://dev-123.okta.com/api/v1/users?after=2".to_string())
        );
        assert_eq!(
            parse_next_link_header(r#"<https://dev-123.okta.com/api/v1/users>; rel="self""#),
            None
        );

        let mut hdrs = HeaderMap::new();
        hdrs.insert(
            reqwest::header::LINK,
            r#"<https://evil.example.com/steal>; rel="next""#.parse().unwrap(),
        );
        assert!(parse_next_link_from_headers(&hdrs, &base).is_err());
    }

    #[tokio::test]
    async fn okta_paginates_users_groups_members_and_applies_to_iam() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base_url = format!("http://{addr}");
        let base_c = base_url.clone();

        let recorded_apply: Arc<Mutex<Option<Value>>> = Arc::new(Mutex::new(None));
        let recorded_auths: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let apply_ref = Arc::clone(&recorded_apply);
        let auths_ref = Arc::clone(&recorded_auths);

        let server = tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let b_url = base_c.clone();
                let a_ref = Arc::clone(&apply_ref);
                let au_ref = Arc::clone(&auths_ref);
                tokio::spawn(async move {
                    let (method, path, auth, body) = read_req(&mut sock).await;
                    if path.starts_with("/api/v1/users") || path.starts_with("/api/v1/groups") {
                        au_ref.lock().unwrap().push(auth);
                    }
                    let (status, link_hdr, resp_body) = match path.as_str() {
                        "/api/v1/users" => (
                            200,
                            Some(format!("<{b_url}/api/v1/users?after=u2>; rel=\"next\"")),
                            json!([
                                {"id": "00u1", "status": "ACTIVE", "profile": {"login": "alice@example.com"}},
                            ])
                            .to_string(),
                        ),
                        "/api/v1/users?after=u2" => (
                            200,
                            None,
                            json!([
                                {"id": "00u2", "status": "SUSPENDED", "profile": {"login": "svc-bot"}}
                            ])
                            .to_string(),
                        ),
                        "/api/v1/groups" => (
                            200,
                            None,
                            json!([
                                {"id": "00g1", "type": "OKTA_GROUP", "profile": {"name": "Platform", "description": "Core"}},
                                {"id": "00g2", "type": "APP_GROUP", "profile": {"name": "Ignored"}}
                            ])
                            .to_string(),
                        ),
                        "/api/v1/groups/00g1/users" => (
                            200,
                            None,
                            json!([
                                {"id": "00u1"},
                                {"id": "00u2"}
                            ])
                            .to_string(),
                        ),
                        "/api/v1/integrations/internal/iam/12/apply" if method == "POST" => {
                            *a_ref.lock().unwrap() = Some(serde_json::from_str(&body).unwrap());
                            (200, None, json!({"provider_id": 12, "success": true}).to_string())
                        }
                        _ => (404, None, json!({"path": path}).to_string()),
                    };

                    let link_line = link_hdr
                        .map(|h| format!("Link: {h}\r\n"))
                        .unwrap_or_default();
                    let resp = format!(
                        "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\n{link_line}Content-Length: {}\r\nConnection: close\r\n\r\n{resp_body}",
                        resp_body.len()
                    );
                    let _ = sock.write_all(resp.as_bytes()).await;
                    let _ = sock.shutdown().await;
                });
            }
        });

        let provider = json!({
            "id": 12,
            "provider_type": "okta",
            "config": {
                "base_url": base_url,
                "api_token": "okta-ssws-secret"
            }
        });
        let elder = test_elder_client(&base_url);
        let outcome = run_identity_provider(&elder, &provider).await;
        server.abort();

        assert_eq!(outcome.status, "completed");
        assert_eq!(outcome.items_synced, 5); // 2 users + 1 OKTA_GROUP + 2 memberships

        let auths = recorded_auths.lock().unwrap().clone();
        assert!(!auths.is_empty());
        assert!(auths.iter().all(|a| a == "SSWS okta-ssws-secret"));

        let apply = recorded_apply.lock().unwrap().clone().unwrap();
        assert_eq!(apply["success"], true);
        assert_eq!(apply["users"].as_array().unwrap().len(), 2);
        assert_eq!(apply["groups"].as_array().unwrap().len(), 1);
        assert_eq!(apply["memberships"].as_array().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn okta_discovery_job_calls_start_and_complete() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base_url = format!("http://{addr}");

        let completed: Arc<Mutex<Option<Value>>> = Arc::new(Mutex::new(None));
        let comp_ref = Arc::clone(&completed);

        let server = tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let c_ref = Arc::clone(&comp_ref);
                tokio::spawn(async move {
                    let (method, path, _auth, body) = read_req(&mut sock).await;
                    let (status, resp_body) = match (method.as_str(), path.as_str()) {
                        ("POST", "/api/v1/integrations/internal/discovery/55/start") => {
                            (200, json!({"history_id": 808, "job_id": 55}).to_string())
                        }
                        ("GET", "/api/v1/users") => {
                            (200, json!([{"id": "00u1", "status": "ACTIVE"}]).to_string())
                        }
                        ("GET", "/api/v1/groups") => (
                            200,
                            json!([{"id": "00g1", "type": "OKTA_GROUP", "profile": {"name": "Admins"}}])
                                .to_string(),
                        ),
                        ("GET", "/api/v1/groups/00g1/users") => {
                            (200, json!([{"id": "00u1"}]).to_string())
                        }
                        ("POST", "/api/v1/integrations/internal/discovery/55/complete") => {
                            *c_ref.lock().unwrap() = Some(serde_json::from_str(&body).unwrap());
                            (200, json!({"job_id": 55, "status": "completed"}).to_string())
                        }
                        _ => (404, json!({}).to_string()),
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

        let job = json!({
            "id": 55,
            "provider": "okta",
            "organization_id": 3,
            "config": {
                "base_url": base_url,
                "api_token": "tok"
            }
        });
        let elder = test_elder_client(&base_url);
        let outcome = run_identity_discovery(&elder, &job).await;
        server.abort();

        assert_eq!(outcome.status, "completed");
        let comp = completed.lock().unwrap().clone().unwrap();
        assert_eq!(comp["history_id"], 808);
        assert_eq!(comp["organization_id"], 3);
        assert_eq!(comp["status"], "completed");
        assert_eq!(comp["resources"].as_array().unwrap().len(), 2);
        assert_eq!(comp["dependencies"].as_array().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn okta_config_validation_rejects_missing_fields() {
        assert!(matches!(
            OktaConfig::from_value(&json!({"domain": "dev.okta.com"})),
            Err(IdentityError::Config(_))
        ));
        assert!(matches!(
            OktaConfig::from_value(&json!({"api_token": "tok"})),
            Err(IdentityError::Config(_))
        ));
    }
}
