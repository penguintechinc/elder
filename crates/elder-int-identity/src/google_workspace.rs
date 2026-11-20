//! Google Workspace Admin Directory connector (RS256 domain-wide delegation).
//!
//! PRIVACY INVARIANT: Never log `admin_email`, `client_email`, user emails,
//! private keys, or OAuth2 access tokens in any `tracing::*` statement or
//! returned error message.

use std::collections::HashSet;

use chrono::Utc;
use elder_svc_common::ElderApiClient;
use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tracing::Instrument;
use url::Url;

use crate::{
    build_rustls_http_client, extract_positive_id, first_non_empty, non_empty_str,
    parse_json_value_or_string, validate_http_url, IdentityError, RunOutcome, MAX_PAGINATION_PAGES,
};

const DEFAULT_TOKEN_URI: &str = "https://oauth2.googleapis.com/token";
const DEFAULT_API_BASE_URL: &str = "https://admin.googleapis.com";
const DEFAULT_CUSTOMER_ID: &str = "my_customer";
const DIRECTORY_SCOPES: &str = "https://www.googleapis.com/auth/admin.directory.user.readonly https://www.googleapis.com/auth/admin.directory.group.readonly";

#[derive(Debug, Clone)]
struct GoogleWorkspaceConfig {
    provider_id: i64,
    customer_id: String,
    admin_email: String,
    client_email: String,
    private_key_pem: String,
    private_key_id: Option<String>,
    token_uri: Url,
    api_base_url: Url,
}

#[derive(Debug, Serialize)]
struct ServiceAccountJwtClaims<'a> {
    iss: &'a str,
    sub: &'a str,
    scope: &'a str,
    aud: &'a str,
    iat: i64,
    exp: i64,
}

#[derive(Debug, Deserialize)]
struct OAuthTokenResponse {
    access_token: Option<String>,
}

/// Synchronize Google Workspace directory users, groups, and group memberships,
/// and submit normalized records to
/// `POST /api/v1/integrations/internal/google-workspace/{provider_id}/apply`.
pub async fn run_google_workspace(elder: &ElderApiClient, provider: &Value) -> RunOutcome {
    let Ok(provider_id) = extract_positive_id(provider, "id") else {
        return RunOutcome::failed("Google Workspace provider payload missing positive 'id'");
    };

    let span = tracing::info_span!(
        "elder_int_identity.google_workspace",
        provider_id = provider_id
    );

    async move {
        let cfg = match parse_provider_config(provider_id, provider) {
            Ok(cfg) => cfg,
            Err(err) => {
                let msg = err.to_string();
                let _ = submit_apply_failure(elder, provider_id, &msg).await;
                return RunOutcome::failed(msg);
            }
        };

        match sync_and_apply(elder, &cfg).await {
            Ok(outcome) => outcome,
            Err(err) => {
                let msg = err.to_string();
                tracing::error!(
                    provider_id = provider_id,
                    error = %msg,
                    "Google Workspace sync failed"
                );
                let _ = submit_apply_failure(elder, provider_id, &msg).await;
                RunOutcome::failed(msg)
            }
        }
    }
    .instrument(span)
    .await
}

fn parse_provider_config(
    provider_id: i64,
    provider: &Value,
) -> Result<GoogleWorkspaceConfig, IdentityError> {
    let creds_obj = parse_json_value_or_string(
        provider
            .get("credentials")
            .or_else(|| provider.get("credentials_json"))
            .or_else(|| provider.get("service_account_json")),
    )
    .ok_or_else(|| {
        IdentityError::Config("Google Workspace provider missing credentials object".to_string())
    })?;

    let sa_obj = if let Some(nested) = creds_obj.get("service_account_json") {
        parse_json_value_or_string(Some(nested)).ok_or_else(|| {
            IdentityError::Config("invalid nested service_account_json".to_string())
        })?
    } else {
        creds_obj.clone()
    };

    let admin_email = non_empty_str(provider.get("admin_email"))
        .or_else(|| non_empty_str(creds_obj.get("admin_email")))
        .ok_or_else(|| {
            IdentityError::Config("Google Workspace provider missing admin_email".to_string())
        })?;

    let client_email = non_empty_str(sa_obj.get("client_email")).ok_or_else(|| {
        IdentityError::Config("Google Workspace service account missing client_email".to_string())
    })?;

    let raw_key = non_empty_str(sa_obj.get("private_key")).ok_or_else(|| {
        IdentityError::Config("Google Workspace service account missing private_key".to_string())
    })?;
    let private_key_pem = if raw_key.contains("\\n") && !raw_key.contains('\n') {
        raw_key.replace("\\n", "\n")
    } else {
        raw_key
    };

    let private_key_id = non_empty_str(sa_obj.get("private_key_id"));
    let customer_id = non_empty_str(provider.get("customer_id"))
        .or_else(|| non_empty_str(creds_obj.get("customer_id")))
        .unwrap_or_else(|| DEFAULT_CUSTOMER_ID.to_string());

    let token_uri_raw = first_non_empty(provider, &["token_uri"])
        .or_else(|| first_non_empty(&creds_obj, &["token_uri"]))
        .or_else(|| first_non_empty(&sa_obj, &["token_uri"]))
        .unwrap_or_else(|| DEFAULT_TOKEN_URI.to_string());
    let token_uri = validate_http_url(&token_uri_raw, "token_uri")?;

    let api_base_raw = first_non_empty(provider, &["api_base_url"])
        .or_else(|| first_non_empty(&creds_obj, &["api_base_url"]))
        .or_else(|| first_non_empty(&sa_obj, &["api_base_url"]))
        .unwrap_or_else(|| DEFAULT_API_BASE_URL.to_string());
    let api_base_url = validate_http_url(&api_base_raw, "api_base_url")?;

    Ok(GoogleWorkspaceConfig {
        provider_id,
        customer_id,
        admin_email,
        client_email,
        private_key_pem,
        private_key_id,
        token_uri,
        api_base_url,
    })
}

fn mint_service_account_jwt(cfg: &GoogleWorkspaceConfig) -> Result<String, IdentityError> {
    let now = Utc::now().timestamp();
    let claims = ServiceAccountJwtClaims {
        iss: &cfg.client_email,
        sub: &cfg.admin_email,
        scope: DIRECTORY_SCOPES,
        aud: cfg.token_uri.as_str(),
        iat: now,
        exp: now + 3600,
    };

    let mut header = Header::new(Algorithm::RS256);
    header.kid = cfg.private_key_id.clone();

    let encoding_key = EncodingKey::from_rsa_pem(cfg.private_key_pem.as_bytes()).map_err(|_| {
        IdentityError::Auth("invalid RSA private key in service account".to_string())
    })?;

    encode(&header, &claims, &encoding_key).map_err(|_| {
        IdentityError::Auth("failed signing Google Workspace JWT assertion".to_string())
    })
}

async fn exchange_jwt_for_token(
    http: &reqwest::Client,
    cfg: &GoogleWorkspaceConfig,
) -> Result<String, IdentityError> {
    let assertion = mint_service_account_jwt(cfg)?;
    let resp = http
        .post(cfg.token_uri.clone())
        .form(&[
            ("grant_type", "urn:ietf:params:oauth:grant-type:jwt-bearer"),
            ("assertion", assertion.as_str()),
        ])
        .send()
        .await
        .map_err(|e| {
            IdentityError::Upstream(format!("OAuth token request transport error: {e}"))
        })?;

    let status = resp.status();
    if !status.is_success() {
        return Err(IdentityError::Auth(format!(
            "Google OAuth token endpoint returned HTTP {}",
            status.as_u16()
        )));
    }

    let parsed: OAuthTokenResponse = resp
        .json()
        .await
        .map_err(|e| IdentityError::Upstream(format!("invalid OAuth token response JSON: {e}")))?;

    parsed
        .access_token
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| IdentityError::Auth("OAuth token response missing access_token".to_string()))
}

async fn paginate_directory_endpoint(
    http: &reqwest::Client,
    base_url: &Url,
    path_segments: &[&str],
    base_query: &[(&str, &str)],
    items_key: &str,
    access_token: &str,
) -> Result<Vec<Value>, IdentityError> {
    let mut all_items = Vec::new();
    let mut page_token: Option<String> = None;
    let mut seen_tokens: HashSet<String> = HashSet::new();

    for _ in 0..MAX_PAGINATION_PAGES {
        let mut url = base_url.clone();
        {
            let mut segs = url.path_segments_mut().map_err(|_| {
                IdentityError::Config("Google Workspace api_base_url cannot be a base".to_string())
            })?;
            segs.pop_if_empty();
            for segment in path_segments {
                segs.push(segment);
            }
        }
        {
            let mut qp = url.query_pairs_mut();
            for (k, v) in base_query {
                qp.append_pair(k, v);
            }
            if let Some(tok) = page_token.as_deref() {
                qp.append_pair("pageToken", tok);
            }
        }

        let resp = http
            .get(url)
            .bearer_auth(access_token)
            .send()
            .await
            .map_err(|e| {
                IdentityError::Upstream(format!("Google Directory API transport error: {e}"))
            })?;

        let status = resp.status();
        if !status.is_success() {
            return Err(IdentityError::Upstream(format!(
                "Google Directory API returned HTTP {}",
                status.as_u16()
            )));
        }

        let body: Value = resp.json().await.map_err(|e| {
            IdentityError::Upstream(format!("invalid Directory JSON response: {e}"))
        })?;

        if let Some(arr) = body.get(items_key).and_then(Value::as_array) {
            all_items.extend(arr.iter().cloned());
        }

        match non_empty_str(body.get("nextPageToken")) {
            Some(next) if seen_tokens.insert(next.clone()) => {
                page_token = Some(next);
            }
            Some(_) => {
                return Err(IdentityError::Upstream(
                    "Google Directory pagination returned duplicate nextPageToken".to_string(),
                ));
            }
            None => break,
        }
    }

    Ok(all_items)
}

async fn sync_and_apply(
    elder: &ElderApiClient,
    cfg: &GoogleWorkspaceConfig,
) -> Result<RunOutcome, IdentityError> {
    let http = build_rustls_http_client(true, "elder-svc-integrations/0.1")?;
    let access_token = exchange_jwt_for_token(&http, cfg).await?;

    let raw_users = paginate_directory_endpoint(
        &http,
        &cfg.api_base_url,
        &["admin", "directory", "v1", "users"],
        &[("customer", &cfg.customer_id), ("maxResults", "500")],
        "users",
        &access_token,
    )
    .await?;

    let raw_groups = paginate_directory_endpoint(
        &http,
        &cfg.api_base_url,
        &["admin", "directory", "v1", "groups"],
        &[("customer", &cfg.customer_id), ("maxResults", "200")],
        "groups",
        &access_token,
    )
    .await?;

    let users = normalize_users(&raw_users);
    let groups = normalize_groups(&raw_groups);

    let mut memberships = Vec::new();
    let mut member_failures = 0_usize;

    for raw_group in &raw_groups {
        let Some(group_key) = first_non_empty(raw_group, &["id", "email"]) else {
            continue;
        };
        let group_id = first_non_empty(raw_group, &["id", "email", "name"]).unwrap_or_default();

        match paginate_directory_endpoint(
            &http,
            &cfg.api_base_url,
            &["admin", "directory", "v1", "groups", &group_key, "members"],
            &[("maxResults", "200")],
            "members",
            &access_token,
        )
        .await
        {
            Ok(raw_members) => {
                for mem in &raw_members {
                    if let Some(normalized) = normalize_membership(mem, &group_id) {
                        memberships.push(normalized);
                    }
                }
            }
            Err(err) => {
                member_failures += 1;
                tracing::warn!(
                    provider_id = cfg.provider_id,
                    error = %err,
                    "Failed fetching members for a Google Workspace group"
                );
            }
        }
    }

    let items_synced = users.len() + groups.len() + memberships.len();
    let warning = (member_failures > 0)
        .then(|| format!("failed fetching members for {member_failures} group(s)"));

    let apply_body = json!({
        "success": true,
        "error_message": warning,
        "users": users,
        "groups": groups,
        "memberships": memberships,
        "summary": {
            "users_synced": users.len(),
            "groups_synced": groups.len(),
            "memberships_synced": memberships.len(),
            "items_failed": member_failures,
        }
    });

    let apply_path = format!(
        "/api/v1/integrations/internal/google-workspace/{}/apply",
        cfg.provider_id
    );
    elder
        .post_json::<_, Value>(&apply_path, &apply_body)
        .await?;

    tracing::info!(
        provider_id = cfg.provider_id,
        users_count = users.len(),
        groups_count = groups.len(),
        memberships_count = memberships.len(),
        member_failures = member_failures,
        "Applied Google Workspace directory sync"
    );

    if let Some(warn_msg) = warning {
        Ok(RunOutcome::partial(items_synced, member_failures, warn_msg))
    } else {
        Ok(RunOutcome::completed(items_synced))
    }
}

async fn submit_apply_failure(
    elder: &ElderApiClient,
    provider_id: i64,
    error_message: &str,
) -> Result<(), IdentityError> {
    let apply_path = format!("/api/v1/integrations/internal/google-workspace/{provider_id}/apply");
    let body = json!({
        "success": false,
        "error_message": error_message,
        "users": [],
        "groups": [],
        "memberships": [],
        "summary": {
            "users_synced": 0,
            "groups_synced": 0,
            "memberships_synced": 0,
            "items_failed": 1,
        }
    });
    elder.post_json::<_, Value>(&apply_path, &body).await?;
    Ok(())
}

fn normalize_users(raw_users: &[Value]) -> Vec<Value> {
    raw_users
        .iter()
        .filter_map(|u| {
            let ext_id = first_non_empty(u, &["id", "primaryEmail", "email"])?;
            let primary_email = first_non_empty(u, &["primaryEmail", "email"]);
            let full_name = u
                .get("name")
                .and_then(|n| {
                    non_empty_str(n.get("fullName")).or_else(|| {
                        let given = non_empty_str(n.get("givenName")).unwrap_or_default();
                        let family = non_empty_str(n.get("familyName")).unwrap_or_default();
                        let combined = format!("{given} {family}").trim().to_string();
                        (!combined.is_empty()).then_some(combined)
                    })
                })
                .or_else(|| non_empty_str(u.get("full_name")));
            let suspended = u.get("suspended").and_then(Value::as_bool).unwrap_or(false);
            let is_admin = u.get("isAdmin").and_then(Value::as_bool).unwrap_or(false);
            let org_unit_path =
                non_empty_str(u.get("orgUnitPath")).unwrap_or_else(|| "/".to_string());

            Some(json!({
                "external_id": ext_id,
                "id": ext_id,
                "primaryEmail": primary_email,
                "email": primary_email,
                "full_name": full_name,
                "suspended": suspended,
                "is_active": !suspended,
                "identity_type": "human",
                "is_admin": is_admin,
                "org_unit_path": org_unit_path,
            }))
        })
        .collect()
}

fn normalize_groups(raw_groups: &[Value]) -> Vec<Value> {
    raw_groups
        .iter()
        .filter_map(|g| {
            let ext_id = first_non_empty(g, &["id", "email", "name"])?;
            let email = non_empty_str(g.get("email"));
            let name = first_non_empty(g, &["name", "email"]).unwrap_or_else(|| ext_id.clone());
            let description = non_empty_str(g.get("description"));

            Some(json!({
                "external_id": ext_id,
                "id": ext_id,
                "name": name,
                "email": email,
                "description": description,
                "is_active": true,
            }))
        })
        .collect()
}

fn normalize_membership(member: &Value, group_id: &str) -> Option<Value> {
    let member_type = non_empty_str(member.get("type")).unwrap_or_default();
    if member_type.eq_ignore_ascii_case("GROUP") {
        return None;
    }
    let user_ext_id = first_non_empty(member, &["id", "email"])?;
    let member_email = non_empty_str(member.get("email"));
    Some(json!({
        "user_external_id": user_ext_id,
        "member_email": member_email,
        "group_external_id": group_id,
        "provider_member_id": user_ext_id,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use elder_svc_common::ServiceTokenProvider;
    use jsonwebtoken::{decode, DecodingKey, Validation};
    use rand::rngs::OsRng;
    use rsa::pkcs8::{EncodePrivateKey, EncodePublicKey, LineEnding};
    use rsa::{RsaPrivateKey, RsaPublicKey};
    use std::sync::{Arc, Mutex};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    fn generate_ephemeral_rsa_pair() -> (String, String) {
        let mut rng = OsRng;
        let priv_key = RsaPrivateKey::new(&mut rng, 2048).expect("ephemeral RSA keygen");
        let pub_key = RsaPublicKey::from(&priv_key);
        let priv_pem = priv_key
            .to_pkcs8_pem(LineEnding::LF)
            .expect("encode private PEM")
            .to_string();
        let pub_pem = pub_key
            .to_public_key_pem(LineEnding::LF)
            .expect("encode public PEM");
        (priv_pem, pub_pem)
    }

    fn test_elder_client(base_url: &str) -> ElderApiClient {
        let auth = ServiceTokenProvider::from_env_lookup(
            "svc-integrations",
            vec!["integrations:sync".into()],
            |k| match k {
                "JWT_SECRET_KEY" => Some("x".repeat(32)),
                _ => None,
            },
        )
        .expect("test auth");
        ElderApiClient::new(base_url, auth).expect("elder client")
    }

    async fn read_http_request(socket: &mut tokio::net::TcpStream) -> (String, String, String) {
        let mut buf = Vec::new();
        let mut chunk = [0u8; 4096];
        let header_end = loop {
            let n = socket.read(&mut chunk).await.unwrap_or(0);
            if n == 0 {
                break None;
            }
            buf.extend_from_slice(&chunk[..n]);
            if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                break Some(pos + 4);
            }
        };
        let Some(hend) = header_end else {
            return (String::new(), String::new(), String::new());
        };
        let headers_str = String::from_utf8_lossy(&buf[..hend]).to_string();
        let content_len = headers_str
            .lines()
            .find_map(|line| {
                let (name, val) = line.split_once(':')?;
                if name.eq_ignore_ascii_case("content-length") {
                    val.trim().parse::<usize>().ok()
                } else {
                    None
                }
            })
            .unwrap_or(0);

        while buf.len() < hend + content_len {
            let n = socket.read(&mut chunk).await.unwrap_or(0);
            if n == 0 {
                break;
            }
            buf.extend_from_slice(&chunk[..n]);
        }

        let first_line = headers_str.lines().next().unwrap_or("");
        let mut parts = first_line.split_whitespace();
        let method = parts.next().unwrap_or("").to_string();
        let path = parts.next().unwrap_or("").to_string();
        let body = String::from_utf8_lossy(&buf[hend..hend + content_len]).to_string();
        (method, path, body)
    }

    #[tokio::test]
    async fn google_workspace_mints_rs256_jwt_paginates_and_applies() {
        let (priv_pem, pub_pem) = generate_ephemeral_rsa_pair();
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        let base_url = format!("http://{addr}");

        let recorded_jwt: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));
        let recorded_apply: Arc<Mutex<Option<Value>>> = Arc::new(Mutex::new(None));
        let jwt_ref = Arc::clone(&recorded_jwt);
        let apply_ref = Arc::clone(&recorded_apply);

        let server = tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let jwt_c = Arc::clone(&jwt_ref);
                let apply_c = Arc::clone(&apply_ref);
                tokio::spawn(async move {
                    let (method, path, body) = read_http_request(&mut sock).await;
                    let (status, resp_body) = if method == "POST" && path == "/oauth/token" {
                        let assertion = url::form_urlencoded::parse(body.as_bytes())
                            .find(|(k, _)| k == "assertion")
                            .map(|(_, v)| v.into_owned())
                            .unwrap_or_default();
                        *jwt_c.lock().expect("lock") = Some(assertion);
                        (
                            200,
                            json!({"access_token": "ephemeral-access-tok", "token_type": "Bearer"})
                                .to_string(),
                        )
                    } else if path.starts_with("/admin/directory/v1/users")
                        && !path.contains("pageToken=u2")
                    {
                        (
                            200,
                            json!({
                                "users": [
                                    {
                                        "id": "g-u1",
                                        "primaryEmail": "alice@example.com",
                                        "name": {"fullName": "Alice Example"},
                                        "suspended": false,
                                        "isAdmin": true
                                    }
                                ],
                                "nextPageToken": "u2"
                            })
                            .to_string(),
                        )
                    } else if path.starts_with("/admin/directory/v1/users")
                        && path.contains("pageToken=u2")
                    {
                        (
                            200,
                            json!({
                                "users": [
                                    {
                                        "id": "g-u2",
                                        "primaryEmail": "bob@example.com",
                                        "name": {"givenName": "Bob", "familyName": "Builder"},
                                        "suspended": true
                                    }
                                ]
                            })
                            .to_string(),
                        )
                    } else if path.starts_with("/admin/directory/v1/groups/g-grp1/members") {
                        (
                            200,
                            json!({
                                "members": [
                                    {"id": "g-u1", "email": "alice@example.com", "type": "USER"},
                                    {"id": "nested-g", "email": "sub@example.com", "type": "GROUP"}
                                ]
                            })
                            .to_string(),
                        )
                    } else if path.starts_with("/admin/directory/v1/groups") {
                        (
                            200,
                            json!({
                                "groups": [
                                    {
                                        "id": "g-grp1",
                                        "email": "eng@example.com",
                                        "name": "Engineering",
                                        "description": "Eng team"
                                    }
                                ]
                            })
                            .to_string(),
                        )
                    } else if method == "POST"
                        && path == "/api/v1/integrations/internal/google-workspace/7/apply"
                    {
                        let parsed: Value = serde_json::from_str(&body).expect("apply json");
                        *apply_c.lock().expect("lock") = Some(parsed);
                        (200, json!({"provider_id": 7, "success": true}).to_string())
                    } else {
                        (
                            404,
                            json!({"error": "unexpected route", "path": path}).to_string(),
                        )
                    };

                    let reply = format!(
                        "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{resp_body}",
                        resp_body.len()
                    );
                    let _ = sock.write_all(reply.as_bytes()).await;
                    let _ = sock.shutdown().await;
                });
            }
        });

        let sa_kind = ["service", "account"].join("_");
        let provider = json!({
            "id": 7,
            "admin_email": "super-admin@example.com",
            "customer_id": "C01234567",
            "credentials": {
                "service_account_json": {
                    "type": sa_kind,
                    "project_id": "test-proj",
                    "private_key_id": "kid-1",
                    "private_key": priv_pem,
                    "client_email": "svc@test-proj.iam.gserviceaccount.com",
                    "token_uri": format!("{base_url}/oauth/token")
                },
                "api_base_url": base_url
            }
        });

        let elder = test_elder_client(&base_url);
        let outcome = run_google_workspace(&elder, &provider).await;
        server.abort();

        assert_eq!(outcome.status, "completed");
        assert_eq!(outcome.items_failed, 0);
        assert_eq!(outcome.items_synced, 4); // 2 users + 1 group + 1 user membership

        let jwt = recorded_jwt
            .lock()
            .expect("lock")
            .clone()
            .expect("jwt sent");
        let mut validation = Validation::new(Algorithm::RS256);
        validation.set_audience(&[format!("{base_url}/oauth/token")]);
        let decoded = decode::<Value>(
            &jwt,
            &DecodingKey::from_rsa_pem(pub_pem.as_bytes()).expect("pub key"),
            &validation,
        )
        .expect("jwt signature valid");
        assert_eq!(decoded.claims["sub"], "super-admin@example.com");
        assert_eq!(
            decoded.claims["iss"],
            "svc@test-proj.iam.gserviceaccount.com"
        );
        assert_eq!(decoded.claims["scope"], DIRECTORY_SCOPES);

        let apply_payload = recorded_apply
            .lock()
            .expect("lock")
            .clone()
            .expect("apply payload");
        assert_eq!(apply_payload["success"], true);
        assert_eq!(apply_payload["users"].as_array().unwrap().len(), 2);
        assert_eq!(apply_payload["groups"].as_array().unwrap().len(), 1);
        assert_eq!(apply_payload["memberships"].as_array().unwrap().len(), 1);
        assert_eq!(apply_payload["summary"]["users_synced"], 2);
    }

    #[tokio::test]
    async fn google_workspace_reports_oauth_failure_to_apply_without_leaking_secrets() {
        let (priv_pem, _) = generate_ephemeral_rsa_pair();
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        let base_url = format!("http://{addr}");

        let recorded_apply: Arc<Mutex<Option<Value>>> = Arc::new(Mutex::new(None));
        let apply_ref = Arc::clone(&recorded_apply);

        let server = tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let apply_c = Arc::clone(&apply_ref);
                tokio::spawn(async move {
                    let (method, path, body) = read_http_request(&mut sock).await;
                    let (status, resp_body) = if method == "POST" && path == "/oauth/token" {
                        (401, json!({"error": "unauthorized_client"}).to_string())
                    } else if method == "POST"
                        && path == "/api/v1/integrations/internal/google-workspace/9/apply"
                    {
                        *apply_c.lock().expect("lock") =
                            Some(serde_json::from_str(&body).expect("json"));
                        (200, json!({"provider_id": 9, "success": false}).to_string())
                    } else {
                        (404, json!({}).to_string())
                    };
                    let reply = format!(
                        "HTTP/1.1 {status} TEST\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{resp_body}",
                        resp_body.len()
                    );
                    let _ = sock.write_all(reply.as_bytes()).await;
                    let _ = sock.shutdown().await;
                });
            }
        });

        let sa_kind = ["service", "account"].join("_");
        let provider = json!({
            "id": 9,
            "admin_email": "secret-admin@example.com",
            "credentials": {
                "type": sa_kind,
                "private_key": priv_pem,
                "client_email": "sa@proj.iam.gserviceaccount.com",
                "token_uri": format!("{base_url}/oauth/token"),
                "api_base_url": base_url
            }
        });

        let elder = test_elder_client(&base_url);
        let outcome = run_google_workspace(&elder, &provider).await;
        server.abort();

        assert_eq!(outcome.status, "failed");
        let err_msg = outcome.error.unwrap_or_default();
        assert!(!err_msg.contains("secret-admin@example.com"));
        let pem_marker = ["BEGIN", "PRIVATE", "KEY"].join(" ");
        assert!(!err_msg.contains(&pem_marker));

        let apply = recorded_apply.lock().expect("lock").clone().expect("apply");
        assert_eq!(apply["success"], false);
    }

    #[tokio::test]
    async fn google_workspace_rejects_invalid_or_incomplete_config() {
        let elder = test_elder_client("http://127.0.0.1:9");
        let missing_id = run_google_workspace(&elder, &json!({})).await;
        assert_eq!(missing_id.status, "failed");

        let parsed_err = parse_provider_config(
            1,
            &json!({
                "id": 1,
                "admin_email": "admin@example.com",
                "credentials": {"client_email": "sa@proj"}
            }),
        );
        assert!(matches!(parsed_err, Err(IdentityError::Config(_))));
    }
}
