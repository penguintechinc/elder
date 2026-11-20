//! GitHub work-management sync connector (GitHub App RS256 JWT + PAT, Link pagination, two-way issues).

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, AUTHORIZATION, LINK};
use reqwest::{Method, StatusCode};
use serde::Serialize;
use serde_json::{json, Value};
use tokio::sync::Mutex;

use crate::error::{Result, WorkMgmtError};
use crate::http_client::{
    build_http_client, encode_path_segment, normalize_base_url, parse_next_link, resolve_link_url,
    send_with_retry, truncate_body,
};
use crate::sync_common::{
    extract_label_names, labels_to_issue_type, labels_to_priority, make_pushed_mapping,
    parse_datetime_str, reconcile_issues, LocalItem, PlatformSyncOutcome, PulledItem,
    PushedMapping, SyncDirection, SyncJobContext,
};

const GITHUB_API_VERSION: &str = "2022-11-28";
const DEFAULT_GITHUB_BASE: &str = "https://api.github.com";
const APP_JWT_IAT_DRIFT_SECS: i64 = 60;
const APP_JWT_EXPIRY_SECS: i64 = 540;
const TOKEN_REFRESH_BUFFER_SECS: i64 = 300;
const MAX_PAGES: usize = 500;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum GitHubAuthMode {
    GitHubApp,
    Pat,
}

#[derive(Debug, Serialize)]
struct GitHubAppJwtClaims {
    iss: String,
    iat: i64,
    exp: i64,
}

#[derive(Debug)]
struct CachedAppToken {
    token: String,
    expires_at: DateTime<Utc>,
}

struct GitHubAuth {
    mode: GitHubAuthMode,
    pat_token: Option<String>,
    app_id: Option<String>,
    installation_id: Option<i64>,
    private_key_pem: Option<String>,
    cached: Mutex<Option<CachedAppToken>>,
}

impl GitHubAuth {
    fn from_context(ctx: &SyncJobContext) -> Result<Self> {
        let app_id = ctx.get_str(&["app_id"]);
        let installation_id = ctx.get_i64(&["installation_id"]);
        let private_key_pem = ctx.get_str(&["private_key"]).map(|raw| normalize_pem(&raw));

        let mode = match ctx
            .get_str(&["auth_mode"])
            .map(|s| s.to_ascii_lowercase())
            .as_deref()
        {
            Some("github_app") => GitHubAuthMode::GitHubApp,
            Some("pat") => GitHubAuthMode::Pat,
            Some(other) => {
                return Err(WorkMgmtError::Config(format!(
                    "unsupported GitHub auth_mode {other:?}; must be 'github_app' or 'pat'"
                )));
            }
            None => {
                if app_id.is_some() && private_key_pem.is_some() {
                    GitHubAuthMode::GitHubApp
                } else {
                    let pat_opt = ctx.get_str(&["api_token", "access_token", "token"]);
                    if pat_opt.is_some() {
                        GitHubAuthMode::Pat
                    } else if app_id.is_some()
                        || installation_id.is_some()
                        || private_key_pem.is_some()
                    {
                        GitHubAuthMode::GitHubApp
                    } else {
                        GitHubAuthMode::Pat
                    }
                }
            }
        };

        let pat_token = ctx.get_str(&["api_token", "access_token", "token"]);

        match mode {
            GitHubAuthMode::GitHubApp => {
                if app_id.as_deref().unwrap_or("").is_empty() {
                    return Err(WorkMgmtError::Auth(
                        "GitHub App auth requires 'app_id'".into(),
                    ));
                }
                if installation_id.is_none_or(|id| id <= 0) {
                    return Err(WorkMgmtError::Auth(
                        "GitHub App auth requires positive integer 'installation_id'".into(),
                    ));
                }
                let pem = private_key_pem.as_deref().unwrap_or("");
                if pem.is_empty() {
                    return Err(WorkMgmtError::Auth(
                        "GitHub App auth requires PEM 'private_key'".into(),
                    ));
                }
                // Validate PEM parses cleanly before issuing any network calls.
                EncodingKey::from_rsa_pem(pem.as_bytes()).map_err(|e| {
                    WorkMgmtError::Auth(format!("invalid GitHub App RSA private_key: {e}"))
                })?;
            }
            GitHubAuthMode::Pat => {
                if pat_token.as_deref().unwrap_or("").is_empty() {
                    return Err(WorkMgmtError::Auth(
                        "GitHub PAT auth requires 'api_token', 'access_token', or 'token'".into(),
                    ));
                }
            }
        }

        Ok(Self {
            mode,
            pat_token,
            app_id,
            installation_id,
            private_key_pem,
            cached: Mutex::new(None),
        })
    }

    fn sign_app_jwt(&self) -> Result<String> {
        let app_id = self
            .app_id
            .as_ref()
            .ok_or_else(|| WorkMgmtError::Auth("missing GitHub App ID".into()))?;
        let pem = self
            .private_key_pem
            .as_ref()
            .ok_or_else(|| WorkMgmtError::Auth("missing GitHub App private_key".into()))?;
        let encoding_key = EncodingKey::from_rsa_pem(pem.as_bytes())
            .map_err(|e| WorkMgmtError::Auth(format!("failed loading GitHub App RSA key: {e}")))?;
        let now = Utc::now().timestamp();
        let claims = GitHubAppJwtClaims {
            iss: app_id.clone(),
            iat: now - APP_JWT_IAT_DRIFT_SECS,
            exp: now + APP_JWT_EXPIRY_SECS,
        };
        encode(&Header::new(Algorithm::RS256), &claims, &encoding_key)
            .map_err(|e| WorkMgmtError::Auth(format!("failed signing GitHub App RS256 JWT: {e}")))
    }

    async fn bearer_token(
        &self,
        http: &reqwest::Client,
        api_base: &str,
        base_delay: Duration,
        force_refresh: bool,
    ) -> Result<String> {
        if self.mode == GitHubAuthMode::Pat {
            return self
                .pat_token
                .clone()
                .ok_or_else(|| WorkMgmtError::Auth("missing GitHub PAT".into()));
        }

        let mut guard = self.cached.lock().await;
        let now = Utc::now();
        if !force_refresh {
            if let Some(cached) = guard.as_ref() {
                if (cached.expires_at - now).num_seconds() > TOKEN_REFRESH_BUFFER_SECS {
                    return Ok(cached.token.clone());
                }
            }
        }

        let installation_id = self
            .installation_id
            .ok_or_else(|| WorkMgmtError::Auth("missing GitHub installation_id".into()))?;
        let app_jwt = self.sign_app_jwt()?;
        let url = format!("{api_base}/app/installations/{installation_id}/access_tokens");
        let headers = github_headers(&app_jwt)?;

        let (status, _, json_body, raw_body) = send_with_retry(
            "github",
            || http.post(&url).headers(headers.clone()),
            3,
            base_delay,
        )
        .await?;

        if !matches!(status, StatusCode::OK | StatusCode::CREATED) {
            return Err(WorkMgmtError::ApiHttp {
                platform: "github",
                status: status.as_u16(),
                body: truncate_body(&raw_body),
            });
        }

        let body = json_body.ok_or_else(|| {
            WorkMgmtError::Auth("GitHub installation token response was not JSON".into())
        })?;
        let token = body
            .get("token")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|t| !t.is_empty())
            .ok_or_else(|| {
                WorkMgmtError::Auth(
                    "GitHub installation access token response missing 'token'".into(),
                )
            })?
            .to_string();

        let expires_at = body
            .get("expires_at")
            .and_then(Value::as_str)
            .and_then(parse_datetime_str)
            .unwrap_or_else(|| now + chrono::Duration::hours(1));

        *guard = Some(CachedAppToken {
            token: token.clone(),
            expires_at,
        });
        Ok(token)
    }
}

/// Normalize escaped `\n` / `\r\n` sequences inside PEM strings.
pub fn normalize_pem(raw: &str) -> String {
    raw.trim()
        .replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\r\n", "\n")
        .replace('\r', "\n")
}

fn github_headers(token: &str) -> Result<HeaderMap> {
    let mut headers = HeaderMap::new();
    let auth_val = HeaderValue::from_str(&format!("Bearer {token}"))
        .map_err(|e| WorkMgmtError::Auth(format!("invalid bearer header value: {e}")))?;
    headers.insert(AUTHORIZATION, auth_val);
    headers.insert(
        ACCEPT,
        HeaderValue::from_static("application/vnd.github+json"),
    );
    headers.insert(
        "X-GitHub-Api-Version",
        HeaderValue::from_static(GITHUB_API_VERSION),
    );
    Ok(headers)
}

async fn github_request(
    http: &reqwest::Client,
    auth: &GitHubAuth,
    api_base: &str,
    method: Method,
    url: &str,
    body: Option<&Value>,
    base_delay: Duration,
) -> Result<(StatusCode, HeaderMap, Option<Value>, String)> {
    let token = auth.bearer_token(http, api_base, base_delay, false).await?;
    let headers = github_headers(&token)?;
    let res = send_with_retry(
        "github",
        || {
            let req = http.request(method.clone(), url).headers(headers.clone());
            match body {
                Some(payload) => req.json(payload),
                None => req,
            }
        },
        3,
        base_delay,
    )
    .await?;

    if res.0 == StatusCode::UNAUTHORIZED && auth.mode == GitHubAuthMode::GitHubApp {
        let refreshed = auth.bearer_token(http, api_base, base_delay, true).await?;
        let retry_headers = github_headers(&refreshed)?;
        return send_with_retry(
            "github",
            || {
                let req = http
                    .request(method.clone(), url)
                    .headers(retry_headers.clone());
                match body {
                    Some(payload) => req.json(payload),
                    None => req,
                }
            },
            3,
            base_delay,
        )
        .await;
    }

    Ok(res)
}

/// Run batch two-way issue synchronization against GitHub REST API (`2022-11-28`).
pub async fn sync(ctx: &SyncJobContext) -> Result<PlatformSyncOutcome> {
    let owner = ctx.get_str(&["owner", "org_name"]).ok_or_else(|| {
        WorkMgmtError::Config("GitHub sync requires 'owner' or 'org_name'".into())
    })?;
    let repo = ctx
        .get_str(&["repo_name", "repo"])
        .ok_or_else(|| WorkMgmtError::Config("GitHub sync requires 'repo_name'".into()))?;
    let raw_base = ctx
        .get_str(&["api_base_url", "base_url"])
        .unwrap_or_else(|| DEFAULT_GITHUB_BASE.to_string());
    let api_base = normalize_base_url(&raw_base, true)?;
    let verify_ssl = ctx.get_bool(&["verify_ssl"]).unwrap_or(true);
    let http = build_http_client(verify_ssl)?;
    let auth = Arc::new(GitHubAuth::from_context(ctx)?);

    let repo_prefix = format!(
        "{api_base}/repos/{}/{}",
        encode_path_segment(&owner),
        encode_path_segment(&repo)
    );

    let pulled_raw = if ctx.direction != SyncDirection::Push {
        pull_issues(&http, &auth, &api_base, &repo_prefix, ctx).await?
    } else {
        Vec::new()
    };

    let plan = reconcile_issues(ctx, pulled_raw);
    let mut pushed_mappings: Vec<PushedMapping> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    let mut items_failed: usize = 0;

    for item in &plan.to_create {
        match push_create_issue(&http, &auth, &api_base, &repo_prefix, ctx, item).await {
            Ok(mapping) => pushed_mappings.push(mapping),
            Err(err) => {
                items_failed += 1;
                errors.push(format!("create elder issue {}: {err}", item.elder_id));
            }
        }
    }

    for (item, mapping) in &plan.to_update {
        match push_update_issue(
            &http,
            &auth,
            &api_base,
            &repo_prefix,
            ctx,
            item,
            &mapping.external_id,
        )
        .await
        {
            Ok(upd) => pushed_mappings.push(upd),
            Err(err) => {
                items_failed += 1;
                errors.push(format!(
                    "update GitHub issue #{}: {err}",
                    mapping.external_id
                ));
            }
        }
    }

    Ok(PlatformSyncOutcome {
        pulled_items: plan.pulled_items,
        pushed_mappings,
        conflicts: plan.conflicts,
        items_failed,
        errors,
        metadata: json!({
            "owner": owner,
            "repo": repo,
            "api_base_url": api_base,
        }),
    })
}

async fn pull_issues(
    http: &reqwest::Client,
    auth: &GitHubAuth,
    api_base: &str,
    repo_prefix: &str,
    ctx: &SyncJobContext,
) -> Result<Vec<PulledItem>> {
    let mut url = format!("{repo_prefix}/issues?state=all&per_page=100");
    if let Some(since) = ctx.since {
        url.push_str(&format!(
            "&since={}",
            encode_path_segment(&since.to_rfc3339())
        ));
    }

    let mut pulled = Vec::new();
    let mut visited: HashSet<String> = HashSet::new();

    for _page in 0..MAX_PAGES {
        if !visited.insert(url.clone()) {
            break;
        }

        let (status, resp_headers, json_body, raw_body) = github_request(
            http,
            auth,
            api_base,
            Method::GET,
            &url,
            None,
            ctx.retry_base_delay,
        )
        .await?;

        if status != StatusCode::OK {
            return Err(WorkMgmtError::ApiHttp {
                platform: "github",
                status: status.as_u16(),
                body: truncate_body(&raw_body),
            });
        }

        let items = json_body
            .as_ref()
            .and_then(Value::as_array)
            .ok_or_else(|| WorkMgmtError::ApiHttp {
                platform: "github",
                status: status.as_u16(),
                body: "expected JSON array from GitHub /issues".into(),
            })?;

        for issue in items {
            if issue.get("pull_request").is_some_and(|v| !v.is_null()) {
                continue;
            }
            if let Some(normalized) = normalize_github_issue(issue) {
                pulled.push(normalized);
            }
        }

        let next_link = resp_headers
            .get(LINK)
            .and_then(|v| v.to_str().ok())
            .and_then(parse_next_link);

        match next_link {
            Some(next_raw) => {
                url = resolve_link_url(api_base, &next_raw)?;
            }
            None => break,
        }
    }

    Ok(pulled)
}

fn normalize_github_issue(issue: &Value) -> Option<PulledItem> {
    let number = issue.get("number").and_then(Value::as_i64)?;
    let external_id = number.to_string();
    let title = issue
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let description = issue
        .get("body")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let state = issue.get("state").and_then(Value::as_str).unwrap_or("open");
    let status = if state.eq_ignore_ascii_case("open") {
        "OPEN".to_string()
    } else {
        "CLOSED".to_string()
    };

    let labels = extract_label_names(issue.get("labels"));
    let priority = labels_to_priority(&labels);
    let issue_type = labels_to_issue_type(&labels);

    let mut assignees: Vec<String> = Vec::new();
    if let Some(arr) = issue.get("assignees").and_then(Value::as_array) {
        for user in arr {
            if let Some(login) = user.get("login").and_then(Value::as_str) {
                assignees.push(login.to_string());
            }
        }
    }

    let external_updated_at = issue
        .get("updated_at")
        .and_then(Value::as_str)
        .map(str::to_string);
    let external_updated_dt = external_updated_at.as_deref().and_then(parse_datetime_str);
    let closed_at = issue
        .get("closed_at")
        .and_then(Value::as_str)
        .map(str::to_string);
    let external_url = issue
        .get("html_url")
        .and_then(Value::as_str)
        .map(str::to_string);

    let mut meta: HashMap<String, Value> = HashMap::new();
    if let Some(ms) = issue.get("milestone").filter(|v| !v.is_null()) {
        meta.insert("milestone".to_string(), ms.clone());
    }

    Some(PulledItem {
        elder_type: "issue".to_string(),
        elder_id: None,
        external_id,
        external_key: Some(format!("#{number}")),
        operation: "create".to_string(),
        title,
        description,
        status,
        priority,
        issue_type,
        labels,
        assignees,
        due_date: None,
        closed_at,
        external_updated_at: external_updated_at.clone(),
        updated_at: external_updated_at,
        external_url,
        metadata: serde_json::to_value(meta).unwrap_or_else(|_| json!({})),
        external_updated_dt,
        deleted: false,
    })
}

fn build_github_issue_payload(item: &LocalItem, include_state: bool) -> Value {
    let mut map = serde_json::Map::new();
    map.insert("title".to_string(), json!(item.title));
    map.insert("body".to_string(), json!(item.description));
    if include_state {
        let state = if matches!(item.status.as_str(), "OPEN" | "IN_PROGRESS") {
            "open"
        } else {
            "closed"
        };
        map.insert("state".to_string(), json!(state));
    }
    if !item.labels.is_empty() {
        map.insert("labels".to_string(), json!(item.labels));
    }
    if !item.assignees.is_empty() {
        map.insert("assignees".to_string(), json!(item.assignees));
    }
    Value::Object(map)
}

async fn push_create_issue(
    http: &reqwest::Client,
    auth: &GitHubAuth,
    api_base: &str,
    repo_prefix: &str,
    ctx: &SyncJobContext,
    item: &LocalItem,
) -> Result<PushedMapping> {
    let url = format!("{repo_prefix}/issues");
    let payload = build_github_issue_payload(item, false);
    let (status, _, json_body, raw_body) = github_request(
        http,
        auth,
        api_base,
        Method::POST,
        &url,
        Some(&payload),
        ctx.retry_base_delay,
    )
    .await?;

    if !matches!(status, StatusCode::OK | StatusCode::CREATED) {
        return Err(WorkMgmtError::ApiHttp {
            platform: "github",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let created = json_body.ok_or_else(|| WorkMgmtError::ApiHttp {
        platform: "github",
        status: status.as_u16(),
        body: "GitHub create issue returned empty body".into(),
    })?;

    let ext_id = created
        .get("number")
        .and_then(Value::as_i64)
        .map(|n| n.to_string())
        .ok_or_else(|| WorkMgmtError::ApiHttp {
            platform: "github",
            status: status.as_u16(),
            body: "GitHub create issue response missing 'number'".into(),
        })?;

    let mut ext_updated = created
        .get("updated_at")
        .and_then(Value::as_str)
        .map(str::to_string);

    if !matches!(item.status.as_str(), "OPEN" | "IN_PROGRESS")
        && created.get("state").and_then(Value::as_str) == Some("open")
    {
        let patch_url = format!("{repo_prefix}/issues/{}", encode_path_segment(&ext_id));
        let patch_body = json!({ "state": "closed" });
        let (p_status, _, p_json, _) = github_request(
            http,
            auth,
            api_base,
            Method::PATCH,
            &patch_url,
            Some(&patch_body),
            ctx.retry_base_delay,
        )
        .await?;
        if p_status.is_success() {
            if let Some(upd) = p_json
                .as_ref()
                .and_then(|v| v.get("updated_at"))
                .and_then(Value::as_str)
            {
                ext_updated = Some(upd.to_string());
            }
        }
    }

    Ok(make_pushed_mapping(
        "github",
        ctx.sync_config_id_or_zero(),
        item,
        ext_id,
        "create",
        ext_updated,
    ))
}

async fn push_update_issue(
    http: &reqwest::Client,
    auth: &GitHubAuth,
    api_base: &str,
    repo_prefix: &str,
    ctx: &SyncJobContext,
    item: &LocalItem,
    external_id: &str,
) -> Result<PushedMapping> {
    let url = format!("{repo_prefix}/issues/{}", encode_path_segment(external_id));
    let payload = build_github_issue_payload(item, true);
    let (status, _, json_body, raw_body) = github_request(
        http,
        auth,
        api_base,
        Method::PATCH,
        &url,
        Some(&payload),
        ctx.retry_base_delay,
    )
    .await?;

    if status != StatusCode::OK {
        return Err(WorkMgmtError::ApiHttp {
            platform: "github",
            status: status.as_u16(),
            body: truncate_body(&raw_body),
        });
    }

    let ext_updated = json_body
        .as_ref()
        .and_then(|v| v.get("updated_at"))
        .and_then(Value::as_str)
        .map(str::to_string);

    Ok(make_pushed_mapping(
        "github",
        ctx.sync_config_id_or_zero(),
        item,
        external_id.to_string(),
        "update",
        ext_updated,
    ))
}
