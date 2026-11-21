//! Core async HTTP client for Elder `hub-api`.
//!
//! Built on `reqwest` with pure `rustls` TLS (`default-features = false`,
//! `features = ["json", "rustls-tls", "gzip"]`), bounded exponential backoff
//! retries on transient statuses (`429`, `502`, `503`, `504`) and transport
//! connection/timeout failures, and structured `tracing` instrumentation.

use std::collections::HashMap;
use std::fmt;
use std::sync::{Arc, RwLock};

use reqwest::header::CONTENT_TYPE;
use reqwest::StatusCode;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use tracing::{debug, instrument, warn};

use crate::catalog::{Catalog, HttpMethod};
use crate::config::{Auth, ClientConfig};
use crate::error::{ElderError, Result};

const MAX_ERROR_BODY_BYTES: usize = 2048;
const DEFAULT_PER_PAGE: u32 = 50;
const DEFAULT_MAX_PAGES: usize = 1000;

/// Token bundle returned by [`ElderClient::login`], [`ElderClient::portal_login`],
/// and [`ElderClient::refresh_token`].
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuthTokens {
    /// Short-lived Bearer access JWT.
    pub access_token: String,
    /// Long-lived refresh token, when issued by the endpoint.
    #[serde(default)]
    pub refresh_token: Option<String>,
    /// Token scheme (`"Bearer"`).
    #[serde(default)]
    pub token_type: Option<String>,
    /// Optional lifetime in seconds.
    #[serde(default)]
    pub expires_in: Option<u64>,
    /// Optional user summary returned alongside tokens.
    #[serde(default)]
    pub user: Option<Value>,
}

impl fmt::Debug for AuthTokens {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AuthTokens")
            .field("access_token", &"[REDACTED]")
            .field(
                "refresh_token",
                &self.refresh_token.as_ref().map(|_| "[REDACTED]"),
            )
            .field("token_type", &self.token_type)
            .field("expires_in", &self.expires_in)
            .field("has_user", &self.user.is_some())
            .finish()
    }
}

/// Thin async HTTP client wrapping Elder `hub-api`.
#[derive(Clone)]
pub struct ElderClient {
    http: reqwest::Client,
    config: ClientConfig,
    auth: Arc<RwLock<Auth>>,
    catalog: &'static Catalog,
}

impl fmt::Debug for ElderClient {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let active_auth = self.auth();
        f.debug_struct("ElderClient")
            .field("base_url", &self.config.base_url)
            .field("auth", &active_auth)
            .field("timeout", &self.config.timeout)
            .field("retry", &self.config.retry)
            .field("catalog_ops", &self.catalog.len())
            .finish()
    }
}

impl ElderClient {
    /// Construct a new [`ElderClient`] from a validated [`ClientConfig`].
    pub fn new(config: ClientConfig) -> Result<Self> {
        config.validate()?;
        let http = reqwest::Client::builder()
            .connect_timeout(config.connect_timeout)
            .timeout(config.timeout)
            .user_agent(&config.user_agent)
            .build()?;

        let initial_auth = config.auth.clone();
        Ok(Self {
            http,
            config,
            auth: Arc::new(RwLock::new(initial_auth)),
            catalog: Catalog::embedded(),
        })
    }

    /// Construct a client using [`ClientConfig::from_env`].
    pub fn from_env() -> Result<Self> {
        Self::new(ClientConfig::from_env()?)
    }

    /// Return the normalized base URL configured for this client.
    pub fn base_url(&self) -> &str {
        &self.config.base_url
    }

    /// Return a reference to the client configuration.
    pub fn config(&self) -> &ClientConfig {
        &self.config
    }

    /// Return the embedded API operation catalog.
    pub fn catalog(&self) -> &'static Catalog {
        self.catalog
    }

    /// Return a clone of the currently active [`Auth`] credential state.
    pub fn auth(&self) -> Auth {
        self.auth
            .read()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone()
    }

    /// Replace the active [`Auth`] credential state on this client.
    pub fn set_auth(&self, auth: Auth) -> Result<()> {
        auth.validate()?;
        let mut guard = self
            .auth
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        *guard = auth;
        Ok(())
    }

    /// Return whether an HTTP status code is transient and eligible for retry.
    ///
    /// Retries strictly on `429`, `502`, `503`, and `504`. Client errors such as
    /// `400`, `401`, `403`, `404`, and `422` are never retried.
    pub fn is_retryable_status(status: StatusCode) -> bool {
        matches!(
            status,
            StatusCode::TOO_MANY_REQUESTS
                | StatusCode::BAD_GATEWAY
                | StatusCode::SERVICE_UNAVAILABLE
                | StatusCode::GATEWAY_TIMEOUT
        )
    }

    /// Return whether a transport error is transient and eligible for retry.
    pub fn is_retryable_error(err: &reqwest::Error) -> bool {
        err.is_timeout() || err.is_connect() || err.is_request()
    }

    /// Execute an HTTP request against `path` and return the parsed JSON response.
    ///
    /// Empty `204 No Content` or zero-byte `2xx` responses return `Value::Null`.
    /// Non-JSON `2xx` text payloads (for example SVG/CSV/XML exports) are returned
    /// as `Value::String`.
    pub async fn request(
        &self,
        method: HttpMethod,
        path: &str,
        query: Option<&[(String, String)]>,
        body: Option<&Value>,
    ) -> Result<Value> {
        self.send_request(method, path, query, body, true).await
    }

    /// Execute an HTTP request and deserialize the JSON response into `T`.
    pub async fn request_typed<T: DeserializeOwned>(
        &self,
        method: HttpMethod,
        path: &str,
        query: Option<&[(String, String)]>,
        body: Option<&Value>,
    ) -> Result<T> {
        let value = self.request(method, path, query, body).await?;
        Ok(serde_json::from_value(value)?)
    }

    /// Look up `(service, operation)` in the embedded [`Catalog`], interpolate
    /// `path_params`, and execute the request against `hub-api`.
    #[instrument(skip(self, path_params, query, body), fields(service = %service, operation = %operation))]
    pub async fn invoke(
        &self,
        service: &str,
        operation: &str,
        path_params: &HashMap<String, String>,
        query: Option<&[(String, String)]>,
        body: Option<&Value>,
    ) -> Result<Value> {
        let op = self.catalog.find(service, operation).ok_or_else(|| {
            ElderError::Catalog(format!("unknown operation '{service}.{operation}'"))
        })?;
        let path = Catalog::resolve_path(op, path_params)?;
        self.request(op.method, &path, query, body).await
    }

    /// Invoke a catalog operation and deserialize the JSON response into `T`.
    pub async fn invoke_typed<T: DeserializeOwned>(
        &self,
        service: &str,
        operation: &str,
        path_params: &HashMap<String, String>,
        query: Option<&[(String, String)]>,
        body: Option<&Value>,
    ) -> Result<T> {
        let value = self
            .invoke(service, operation, path_params, query, body)
            .await?;
        Ok(serde_json::from_value(value)?)
    }

    /// Paginate a catalog `GET` operation by `(service, operation)`.
    #[instrument(skip(self, path_params, query), fields(service = %service, operation = %operation))]
    pub async fn invoke_paginate(
        &self,
        service: &str,
        operation: &str,
        path_params: &HashMap<String, String>,
        query: Option<&[(String, String)]>,
        per_page: Option<u32>,
        max_pages: Option<usize>,
    ) -> Result<Vec<Value>> {
        let op = self.catalog.find(service, operation).ok_or_else(|| {
            ElderError::Catalog(format!("unknown operation '{service}.{operation}'"))
        })?;
        if op.method != HttpMethod::Get {
            return Err(ElderError::Validation(format!(
                "cannot paginate non-GET operation '{}.{}' ({})",
                op.service, op.operation, op.method
            )));
        }
        let path = Catalog::resolve_path(op, path_params)?;
        self.paginate(&path, query, per_page, max_pages).await
    }

    /// Collect all items across paginated `GET` responses for `path`.
    ///
    /// Supports both Elder envelope shapes:
    /// - Top-level `{ "items": [...], "total": N, "page": P, "per_page": K, "pages": M }`
    /// - Nested `{ "items": [...], "pagination": { "page": P, "per_page": K, "total": N, "pages": M } }`
    ///
    /// Also handles non-paginated list envelopes (`"data"`, single array field, or raw JSON array)
    /// without looping endlessly.
    #[instrument(skip(self, query), fields(path = %path))]
    pub async fn paginate(
        &self,
        path: &str,
        query: Option<&[(String, String)]>,
        per_page: Option<u32>,
        max_pages: Option<usize>,
    ) -> Result<Vec<Value>> {
        if let Some(0) = per_page {
            return Err(ElderError::Validation(
                "per_page must be greater than zero".into(),
            ));
        }
        if let Some(0) = max_pages {
            return Err(ElderError::Validation(
                "max_pages must be greater than zero".into(),
            ));
        }

        let effective_per_page = per_page
            .or_else(|| {
                query.and_then(|pairs| {
                    pairs
                        .iter()
                        .find(|(k, _)| k == "per_page")
                        .and_then(|(_, v)| v.parse::<u32>().ok())
                })
            })
            .unwrap_or(DEFAULT_PER_PAGE);

        let initial_page = query
            .and_then(|pairs| {
                pairs
                    .iter()
                    .find(|(k, _)| k == "page")
                    .and_then(|(_, v)| v.parse::<u32>().ok())
            })
            .unwrap_or(1)
            .max(1);

        let base_query: Vec<(String, String)> = query
            .unwrap_or(&[])
            .iter()
            .filter(|(k, _)| k != "page" && k != "per_page")
            .cloned()
            .collect();

        let page_limit = max_pages.unwrap_or(DEFAULT_MAX_PAGES);
        let mut collected = Vec::new();

        for (fetched_pages, current_page) in (initial_page..).take(page_limit).enumerate() {
            let mut page_query = base_query.clone();
            page_query.push(("page".into(), current_page.to_string()));
            page_query.push(("per_page".into(), effective_per_page.to_string()));

            let payload = self
                .request(HttpMethod::Get, path, Some(&page_query), None)
                .await?;

            let page_slice = extract_page_items(&payload)?;
            let batch_len = page_slice.items.len();
            collected.extend(page_slice.items);

            if batch_len == 0 {
                break;
            }
            if let Some(total_pages) = page_slice.pages {
                if total_pages == 0 || current_page >= total_pages {
                    break;
                }
            } else if let Some(total_items) = page_slice.total {
                if collected.len() >= total_items as usize {
                    break;
                }
            } else if !page_slice.has_pagination_meta || batch_len < effective_per_page as usize {
                // Non-paginated endpoint or short final page without metadata.
                break;
            }

            if fetched_pages + 1 >= page_limit {
                break;
            }
        }

        Ok(collected)
    }

    /// Authenticate with email/username and password via `POST /api/v1/auth/login`.
    ///
    /// On success, updates this client's active credentials to [`Auth::Bearer`]
    /// with the returned `access_token` and returns the full [`AuthTokens`].
    #[instrument(skip(self, email, password))]
    pub async fn login(&self, email: &str, password: &str) -> Result<AuthTokens> {
        let trimmed_email = email.trim();
        if trimmed_email.is_empty() {
            return Err(ElderError::Validation("email must not be empty".into()));
        }
        if password.is_empty() {
            return Err(ElderError::Validation("password must not be empty".into()));
        }

        let payload = json!({
            "username": trimmed_email,
            "password": password,
        });
        let value = self
            .send_request(
                HttpMethod::Post,
                "/api/v1/auth/login",
                None,
                Some(&payload),
                false,
            )
            .await?;

        self.apply_auth_token_response(value, None)
    }

    /// Authenticate a portal user via `POST /api/v1/portal-auth/login`.
    ///
    /// On success, updates this client's active credentials to [`Auth::Bearer`].
    #[instrument(skip(self, email, password, tenant))]
    pub async fn portal_login(
        &self,
        email: &str,
        password: &str,
        tenant: Option<&str>,
    ) -> Result<AuthTokens> {
        let trimmed_email = email.trim();
        if trimmed_email.is_empty() {
            return Err(ElderError::Validation("email must not be empty".into()));
        }
        if password.is_empty() {
            return Err(ElderError::Validation("password must not be empty".into()));
        }

        let mut map = Map::new();
        map.insert("email".into(), Value::String(trimmed_email.to_string()));
        map.insert("password".into(), Value::String(password.to_string()));
        if let Some(t) = tenant.map(str::trim).filter(|s| !s.is_empty()) {
            map.insert("tenant".into(), Value::String(t.to_string()));
        }

        let value = self
            .send_request(
                HttpMethod::Post,
                "/api/v1/portal-auth/login",
                None,
                Some(&Value::Object(map)),
                false,
            )
            .await?;

        self.apply_auth_token_response(value, None)
    }

    /// Exchange a refresh token for a fresh access token via `POST /api/v1/auth/refresh`.
    ///
    /// Updates this client's active credentials to [`Auth::Bearer`] on success.
    #[instrument(skip(self, refresh))]
    pub async fn refresh_token(&self, refresh: &str) -> Result<AuthTokens> {
        let trimmed = refresh.trim();
        if trimmed.is_empty() {
            return Err(ElderError::Validation(
                "refresh token must not be empty".into(),
            ));
        }

        let payload = json!({ "refresh_token": trimmed });
        let value = self
            .send_request(
                HttpMethod::Post,
                "/api/v1/auth/refresh",
                None,
                Some(&payload),
                false,
            )
            .await?;

        self.apply_auth_token_response(value, Some(trimmed.to_string()))
    }

    /// Fetch operational status from `GET /api/v1/status`.
    pub async fn status(&self) -> Result<Value> {
        self.request(HttpMethod::Get, "/api/v1/status", None, None)
            .await
    }

    /// Fetch health check status from `GET /healthz`.
    pub async fn healthz(&self) -> Result<Value> {
        self.request(HttpMethod::Get, "/healthz", None, None).await
    }

    /// Resolve a universal Village ID via `GET /api/v1/id/{village_id}`.
    pub async fn resolve_village_id(&self, vid: &str) -> Result<Value> {
        let trimmed = vid.trim();
        if trimmed.is_empty() {
            return Err(ElderError::Validation(
                "village_id must not be empty".into(),
            ));
        }
        let mut params = HashMap::new();
        params.insert("village_id".to_string(), trimmed.to_string());
        self.invoke("lookup", "resolve-village-id", &params, None, None)
            .await
    }

    /// Fetch deployment license tier, domain bypass state, limits, and entitlements
    /// from `GET /api/v1/licensing/status`.
    pub async fn licensing_status(&self) -> Result<Value> {
        self.request(HttpMethod::Get, "/api/v1/licensing/status", None, None)
            .await
    }

    /// Run a read-only AI Researcher query via `POST /api/v1/search/ai-research`.
    pub async fn ai_research(
        &self,
        query: &str,
        model: Option<&str>,
        history: Option<&[Value]>,
        max_steps: Option<u32>,
    ) -> Result<Value> {
        let trimmed = query.trim();
        if trimmed.is_empty() {
            return Err(ElderError::Validation(
                "AI research query must not be empty".into(),
            ));
        }

        let mut payload = Map::new();
        payload.insert("query".into(), Value::String(trimmed.to_string()));
        if let Some(m) = model.map(str::trim).filter(|s| !s.is_empty()) {
            payload.insert("model".into(), Value::String(m.to_string()));
        }
        if let Some(hist) = history {
            payload.insert("history".into(), Value::Array(hist.to_vec()));
        }
        if let Some(steps) = max_steps {
            payload.insert("max_steps".into(), Value::Number(steps.into()));
        }

        self.request(
            HttpMethod::Post,
            "/api/v1/search/ai-research",
            None,
            Some(&Value::Object(payload)),
        )
        .await
    }

    /// Fetch AI Researcher configuration from `GET /api/v1/search/ai-research/config`.
    pub async fn ai_research_config(&self) -> Result<Value> {
        self.request(
            HttpMethod::Get,
            "/api/v1/search/ai-research/config",
            None,
            None,
        )
        .await
    }

    fn apply_auth_token_response(
        &self,
        value: Value,
        fallback_refresh: Option<String>,
    ) -> Result<AuthTokens> {
        let mut tokens: AuthTokens =
            serde_json::from_value(value).map_err(|e| ElderError::Auth {
                status: 0,
                message: format!("invalid auth response payload: {e}"),
            })?;

        if tokens.access_token.trim().is_empty() {
            return Err(ElderError::Auth {
                status: 0,
                message: "authentication response did not include a valid access_token".into(),
            });
        }
        if tokens.refresh_token.is_none() {
            tokens.refresh_token = fallback_refresh;
        }

        self.set_auth(Auth::Bearer(tokens.access_token.clone()))?;
        Ok(tokens)
    }

    #[instrument(skip(self, query, body), fields(method = %method, path = %path))]
    async fn send_request(
        &self,
        method: HttpMethod,
        path: &str,
        query: Option<&[(String, String)]>,
        body: Option<&Value>,
        include_auth: bool,
    ) -> Result<Value> {
        let url = join_api_url(&self.config.base_url, path)?;
        let serialized_body = body.map(serde_json::to_vec).transpose()?;
        let max_retries = self.config.retry.max_retries;

        let auth_header = if include_auth {
            self.auth().header_pair()?
        } else {
            None
        };

        for attempt in 0..=max_retries {
            let mut req = self.http.request(method.to_reqwest(), &url);
            if let Some((name, value)) = &auth_header {
                req = req.header(name.clone(), value.clone());
            }
            if let Some(q) = query {
                if !q.is_empty() {
                    req = req.query(q);
                }
            }
            if let Some(bytes) = &serialized_body {
                req = req
                    .header(CONTENT_TYPE, "application/json")
                    .body(bytes.clone());
            }

            match req.send().await {
                Ok(resp) => {
                    let status = resp.status();
                    if status.is_success() {
                        debug!(
                            method = %method,
                            path = %path,
                            status = status.as_u16(),
                            "Elder API request succeeded"
                        );
                        return parse_success_body(resp, status).await;
                    }

                    let raw_bytes = resp.bytes().await.unwrap_or_default();
                    if Self::is_retryable_status(status) && attempt < max_retries {
                        let delay = self.config.retry.delay_for_attempt(attempt);
                        warn!(
                            method = %method,
                            path = %path,
                            status = status.as_u16(),
                            attempt = attempt + 1,
                            delay_ms = delay.as_millis() as u64,
                            "Transient HTTP status from Elder API; retrying"
                        );
                        tokio::time::sleep(delay).await;
                        continue;
                    }

                    return Err(map_error_response(status, &raw_bytes));
                }
                Err(err) => {
                    if Self::is_retryable_error(&err) && attempt < max_retries {
                        let delay = self.config.retry.delay_for_attempt(attempt);
                        warn!(
                            method = %method,
                            path = %path,
                            attempt = attempt + 1,
                            delay_ms = delay.as_millis() as u64,
                            error = %err,
                            "Transient transport error contacting Elder API; retrying"
                        );
                        tokio::time::sleep(delay).await;
                        continue;
                    }
                    return Err(ElderError::Http(err));
                }
            }
        }

        Err(ElderError::Api {
            status: 503,
            code: Some("RETRY_EXHAUSTED".into()),
            message: "retry loop exhausted unexpectedly".into(),
            body: None,
        })
    }
}

fn join_api_url(base_url: &str, path: &str) -> Result<String> {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return Err(ElderError::Validation(
            "request path must not be empty".into(),
        ));
    }
    if trimmed.starts_with("http://")
        || trimmed.starts_with("https://")
        || trimmed.starts_with("//")
    {
        return Err(ElderError::Validation(format!(
            "request path must be relative to base_url, got {trimmed:?}"
        )));
    }
    if trimmed.starts_with('/') {
        Ok(format!("{base_url}{trimmed}"))
    } else {
        Ok(format!("{base_url}/{trimmed}"))
    }
}

async fn parse_success_body(resp: reqwest::Response, status: StatusCode) -> Result<Value> {
    if status == StatusCode::NO_CONTENT {
        return Ok(Value::Null);
    }
    let bytes = resp.bytes().await?;
    if bytes.is_empty() {
        return Ok(Value::Null);
    }
    if let Ok(parsed) = serde_json::from_slice::<Value>(&bytes) {
        return Ok(parsed);
    }
    let text = String::from_utf8_lossy(&bytes).into_owned();
    Ok(Value::String(text))
}

fn map_error_response(status: StatusCode, raw_bytes: &[u8]) -> ElderError {
    let code_status = status.as_u16();
    let parsed_json = if raw_bytes.is_empty() {
        None
    } else {
        serde_json::from_slice::<Value>(raw_bytes).ok()
    };

    let (code, message, body) = match parsed_json {
        Some(Value::Object(ref obj)) => {
            let extracted_msg = extract_str_field(obj, &["message", "detail", "error"])
                .unwrap_or_else(|| {
                    status
                        .canonical_reason()
                        .unwrap_or("API request failed")
                        .to_string()
                });
            let extracted_code = extract_str_field(obj, &["code"]).or_else(|| {
                let err_field = obj.get("error").and_then(Value::as_str)?;
                if obj.contains_key("message") && looks_like_error_code(err_field) {
                    Some(err_field.to_string())
                } else {
                    None
                }
            });
            (
                extracted_code,
                extracted_msg,
                Some(Value::Object(obj.clone())),
            )
        }
        Some(other) => {
            let msg = match &other {
                Value::String(s) if !s.trim().is_empty() => s.clone(),
                _ => status
                    .canonical_reason()
                    .unwrap_or("API request failed")
                    .to_string(),
            };
            (None, msg, Some(other))
        }
        None => {
            let raw_text = String::from_utf8_lossy(raw_bytes);
            let truncated = truncate_str(raw_text.trim(), MAX_ERROR_BODY_BYTES);
            let msg = if truncated.is_empty() {
                status
                    .canonical_reason()
                    .unwrap_or("API request failed")
                    .to_string()
            } else {
                truncated.clone()
            };
            let body = if truncated.is_empty() {
                None
            } else {
                Some(Value::String(truncated))
            };
            (None, msg, body)
        }
    };

    match code_status {
        401 | 403 => ElderError::Auth {
            status: code_status,
            message,
        },
        404 => ElderError::NotFound(message),
        429 => ElderError::RateLimited(message),
        _ => ElderError::Api {
            status: code_status,
            code,
            message,
            body,
        },
    }
}

fn extract_str_field(obj: &Map<String, Value>, keys: &[&str]) -> Option<String> {
    for key in keys {
        if let Some(Value::String(s)) = obj.get(*key) {
            let trimmed = s.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }
    None
}

fn looks_like_error_code(val: &str) -> bool {
    !val.is_empty()
        && val
            .bytes()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_' || b == b'-')
}

fn truncate_str(text: &str, max_bytes: usize) -> String {
    if text.len() <= max_bytes {
        return text.to_string();
    }
    let mut end = max_bytes;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}...(truncated)", &text[..end])
}

struct PageSlice {
    items: Vec<Value>,
    total: Option<u64>,
    pages: Option<u32>,
    has_pagination_meta: bool,
}

fn extract_page_items(payload: &Value) -> Result<PageSlice> {
    match payload {
        Value::Array(arr) => Ok(PageSlice {
            items: arr.clone(),
            total: None,
            pages: None,
            has_pagination_meta: false,
        }),
        Value::Object(obj) => {
            let pag_obj = obj.get("pagination").and_then(Value::as_object);
            let total = pag_obj
                .and_then(|p| p.get("total"))
                .or_else(|| obj.get("total"))
                .and_then(Value::as_u64);
            let pages = pag_obj
                .and_then(|p| p.get("pages"))
                .or_else(|| obj.get("pages"))
                .and_then(Value::as_u64)
                .and_then(|v| u32::try_from(v).ok());
            let has_pagination_meta = pag_obj.is_some()
                || obj.contains_key("pages")
                || obj.contains_key("total")
                || obj.contains_key("page");

            if let Some(items) = obj.get("items").and_then(Value::as_array) {
                return Ok(PageSlice {
                    items: items.clone(),
                    total,
                    pages,
                    has_pagination_meta,
                });
            }
            if let Some(items) = obj.get("data").and_then(Value::as_array) {
                return Ok(PageSlice {
                    items: items.clone(),
                    total,
                    pages,
                    has_pagination_meta,
                });
            }

            let array_fields: Vec<&Vec<Value>> = obj.values().filter_map(Value::as_array).collect();
            if array_fields.len() == 1 {
                return Ok(PageSlice {
                    items: array_fields[0].clone(),
                    total,
                    pages,
                    has_pagination_meta,
                });
            }

            Err(ElderError::Serialization(serde_json::Error::io(
                std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    "paginated response did not contain an items array",
                ),
            )))
        }
        Value::Null => Ok(PageSlice {
            items: Vec::new(),
            total: Some(0),
            pages: Some(0),
            has_pagination_meta: true,
        }),
        _ => Err(ElderError::Serialization(serde_json::Error::io(
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "expected array or paginated object response",
            ),
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::RetryConfig;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::Duration;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;
    use tokio::sync::Mutex;

    #[derive(Debug, Clone)]
    struct RecordedRequest {
        method: String,
        target: String,
        headers: HashMap<String, String>,
        body: String,
    }

    async fn spawn_mock_server(
        responses: Vec<(u16, String)>,
    ) -> (String, Arc<AtomicUsize>, Arc<Mutex<Vec<RecordedRequest>>>) {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind loopback");
        let addr = listener.local_addr().expect("addr");
        let hits = Arc::new(AtomicUsize::new(0));
        let hits_bg = Arc::clone(&hits);
        let recorded = Arc::new(Mutex::new(Vec::new()));
        let recorded_bg = Arc::clone(&recorded);

        tokio::spawn(async move {
            for (status, resp_body) in responses {
                let Ok((mut socket, _)) = listener.accept().await else {
                    break;
                };
                hits_bg.fetch_add(1, Ordering::SeqCst);

                let raw = read_http_request(&mut socket).await;
                let parsed = parse_recorded_request(&raw);
                recorded_bg.lock().await.push(parsed);

                let reason = match status {
                    200 => "OK",
                    201 => "Created",
                    204 => "No Content",
                    400 => "Bad Request",
                    401 => "Unauthorized",
                    403 => "Forbidden",
                    404 => "Not Found",
                    422 => "Unprocessable Entity",
                    429 => "Too Many Requests",
                    502 => "Bad Gateway",
                    503 => "Service Unavailable",
                    504 => "Gateway Timeout",
                    _ => "Test",
                };
                let wire = if status == 204 {
                    "HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n".to_string()
                } else {
                    format!(
                        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{resp_body}",
                        resp_body.len()
                    )
                };
                let _ = socket.write_all(wire.as_bytes()).await;
                let _ = socket.shutdown().await;
            }
        });

        (format!("http://{addr}"), hits, recorded)
    }

    async fn read_http_request(socket: &mut tokio::net::TcpStream) -> Vec<u8> {
        let mut buf = Vec::new();
        let mut chunk = [0u8; 4096];
        let mut content_length: Option<usize> = None;
        let mut header_end: Option<usize> = None;

        while let Ok(n) = socket.read(&mut chunk).await {
            if n == 0 {
                break;
            }
            buf.extend_from_slice(&chunk[..n]);
            if header_end.is_none() {
                if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                    header_end = Some(pos + 4);
                    let headers_str = String::from_utf8_lossy(&buf[..pos]);
                    for line in headers_str.lines() {
                        if let Some((k, v)) = line.split_once(':') {
                            if k.trim().eq_ignore_ascii_case("content-length") {
                                content_length = v.trim().parse::<usize>().ok();
                            }
                        }
                    }
                }
            }
            if let Some(hend) = header_end {
                let expected = content_length.unwrap_or(0);
                if buf.len() >= hend + expected {
                    break;
                }
            }
        }
        buf
    }

    fn parse_recorded_request(raw: &[u8]) -> RecordedRequest {
        let split = raw
            .windows(4)
            .position(|w| w == b"\r\n\r\n")
            .unwrap_or(raw.len());
        let header_str = String::from_utf8_lossy(&raw[..split]);
        let body_start = (split + 4).min(raw.len());
        let body = String::from_utf8_lossy(&raw[body_start..]).into_owned();

        let mut lines = header_str.lines();
        let first = lines.next().unwrap_or("");
        let mut parts = first.split_whitespace();
        let method = parts.next().unwrap_or("").to_string();
        let target = parts.next().unwrap_or("").to_string();

        let mut headers = HashMap::new();
        for line in lines {
            if let Some((k, v)) = line.split_once(':') {
                headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
            }
        }

        RecordedRequest {
            method,
            target,
            headers,
            body,
        }
    }

    fn fast_retry() -> RetryConfig {
        RetryConfig {
            max_retries: 2,
            initial_backoff: Duration::from_millis(2),
            max_backoff: Duration::from_millis(10),
        }
    }

    #[test]
    fn retry_predicate_matches_exact_transient_status_set() {
        for code in [
            StatusCode::TOO_MANY_REQUESTS,
            StatusCode::BAD_GATEWAY,
            StatusCode::SERVICE_UNAVAILABLE,
            StatusCode::GATEWAY_TIMEOUT,
        ] {
            assert!(
                ElderClient::is_retryable_status(code),
                "should retry {code}"
            );
        }

        for code in [
            StatusCode::BAD_REQUEST,
            StatusCode::UNAUTHORIZED,
            StatusCode::FORBIDDEN,
            StatusCode::NOT_FOUND,
            StatusCode::UNPROCESSABLE_ENTITY,
            StatusCode::INTERNAL_SERVER_ERROR,
        ] {
            assert!(
                !ElderClient::is_retryable_status(code),
                "must NOT retry {code}"
            );
        }
    }

    #[test]
    fn masks_tokens_and_keys_in_client_and_token_debug() {
        let tokens = AuthTokens {
            access_token: "jwt-access-secret-999".into(),
            refresh_token: Some("jwt-refresh-secret-888".into()),
            token_type: Some("Bearer".into()),
            expires_in: Some(3600),
            user: Some(json!({"email": "alice@example.com"})),
        };
        let t_dbg = format!("{tokens:?}");
        assert!(!t_dbg.contains("jwt-access-secret-999"));
        assert!(!t_dbg.contains("jwt-refresh-secret-888"));
        assert!(!t_dbg.contains("alice@example.com"));

        let client = ElderClient::new(
            ClientConfig::new("http://127.0.0.1:5000")
                .unwrap()
                .with_api_key("elder_live_secret_key"),
        )
        .unwrap();
        let c_dbg = format!("{client:?}");
        assert!(!c_dbg.contains("elder_live_secret_key"));
        assert!(c_dbg.contains("[REDACTED]"));
    }

    #[tokio::test]
    async fn injects_bearer_and_api_key_headers() {
        let (url, _, recorded) = spawn_mock_server(vec![
            (200, r#"{"status":"operational"}"#.into()),
            (200, r#"{"status":"operational"}"#.into()),
        ])
        .await;

        let bearer_client = ElderClient::new(
            ClientConfig::new(&url)
                .unwrap()
                .with_bearer("my-jwt-token")
                .with_retry(RetryConfig::disabled()),
        )
        .unwrap();
        let _ = bearer_client.status().await.expect("bearer status");

        let key_client = ElderClient::new(
            ClientConfig::new(&url)
                .unwrap()
                .with_api_key("elder_abc123")
                .with_retry(RetryConfig::disabled()),
        )
        .unwrap();
        let _ = key_client.status().await.expect("api key status");

        let reqs = recorded.lock().await;
        assert_eq!(
            reqs[0].headers.get("authorization").map(String::as_str),
            Some("Bearer my-jwt-token")
        );
        assert_eq!(
            reqs[1].headers.get("x-api-key").map(String::as_str),
            Some("elder_abc123")
        );
    }

    #[tokio::test]
    async fn retries_on_429_and_503_and_recovers() {
        let (url, hits, _) = spawn_mock_server(vec![
            (429, r#"{"error":"rate limit"}"#.into()),
            (503, r#"{"error":"upstream down"}"#.into()),
            (200, r#"{"status":"healthy"}"#.into()),
        ])
        .await;

        let client =
            ElderClient::new(ClientConfig::new(url).unwrap().with_retry(fast_retry())).unwrap();
        let res = client.healthz().await.expect("recovers on 3rd attempt");
        assert_eq!(res["status"], "healthy");
        assert_eq!(hits.load(Ordering::SeqCst), 3);
    }

    #[tokio::test]
    async fn exhausts_retries_on_429_returning_rate_limited() {
        let (url, hits, _) = spawn_mock_server(vec![
            (429, r#"{"message":"too fast"}"#.into()),
            (429, r#"{"message":"too fast"}"#.into()),
            (429, r#"{"message":"still too fast"}"#.into()),
        ])
        .await;

        let client =
            ElderClient::new(ClientConfig::new(url).unwrap().with_retry(fast_retry())).unwrap();
        let err = client.healthz().await.unwrap_err();
        assert!(matches!(err, ElderError::RateLimited(ref m) if m == "still too fast"));
        assert_eq!(hits.load(Ordering::SeqCst), 3);
    }

    #[tokio::test]
    async fn fails_fast_without_retrying_400_401_403_404_422() {
        for status in [400_u16, 401, 403, 404, 422] {
            let (url, hits, _) = spawn_mock_server(vec![
                (status, r#"{"error":"TEST_CODE","message":"nope"}"#.into()),
                (200, r#"{"ok":true}"#.into()),
            ])
            .await;
            let client =
                ElderClient::new(ClientConfig::new(url).unwrap().with_retry(fast_retry())).unwrap();
            let err = client.status().await.unwrap_err();
            assert_eq!(
                hits.load(Ordering::SeqCst),
                1,
                "status {status} must not retry"
            );
            match status {
                401 | 403 => {
                    assert!(matches!(err, ElderError::Auth { status: s, .. } if s == status))
                }
                404 => assert!(matches!(err, ElderError::NotFound(_))),
                400 | 422 => assert!(matches!(
                    err,
                    ElderError::Api {
                        status: s,
                        code: Some(ref c),
                        ..
                    } if s == status && c == "TEST_CODE"
                )),
                _ => unreachable!(),
            }
        }
    }

    #[tokio::test]
    async fn login_refresh_village_id_licensing_and_ai_research() {
        let (url, _, recorded) = spawn_mock_server(vec![
            (
                200,
                r#"{"access_token":"acc-1","refresh_token":"ref-1","token_type":"Bearer","user":{"id":1}}"#
                    .into(),
            ),
            (
                200,
                r#"{"tier":"enterprise","status":"active","domain_bypass":true}"#.into(),
            ),
            (
                200,
                r#"{"village_id":"00000001-0000000000000001","resource_type":"entity","resource_id":7}"#
                    .into(),
            ),
            (
                200,
                r#"{"answer":"ok","steps":2}"#.into(),
            ),
            (
                200,
                r#"{"access_token":"acc-2","token_type":"Bearer"}"#.into(),
            ),
        ])
        .await;

        let client = ElderClient::new(
            ClientConfig::new(url)
                .unwrap()
                .with_retry(RetryConfig::disabled()),
        )
        .unwrap();

        let tokens = client
            .login("alice@example.com", "pw")
            .await
            .expect("login");
        assert_eq!(tokens.access_token, "acc-1");
        assert_eq!(tokens.refresh_token.as_deref(), Some("ref-1"));
        assert_eq!(client.auth(), Auth::Bearer("acc-1".into()));

        let lic = client.licensing_status().await.expect("licensing");
        assert_eq!(lic["tier"], "enterprise");

        let resolved = client
            .resolve_village_id("00000001-0000000000000001")
            .await
            .expect("village id");
        assert_eq!(resolved["resource_id"], 7);

        let research = client
            .ai_research("what runs on prod?", Some("gemma4:e4b"), None, Some(4))
            .await
            .expect("ai research");
        assert_eq!(research["answer"], "ok");

        let refreshed = client.refresh_token("ref-1").await.expect("refresh");
        assert_eq!(refreshed.access_token, "acc-2");
        assert_eq!(refreshed.refresh_token.as_deref(), Some("ref-1"));
        assert_eq!(client.auth(), Auth::Bearer("acc-2".into()));

        let reqs = recorded.lock().await;
        assert_eq!(reqs[0].method, "POST");
        assert_eq!(reqs[0].target, "/api/v1/auth/login");
        assert!(reqs[0].body.contains(r#""username":"alice@example.com""#));
        assert!(!reqs[0].headers.contains_key("authorization"));

        assert_eq!(reqs[1].target, "/api/v1/licensing/status");
        assert_eq!(
            reqs[1].headers.get("authorization").map(String::as_str),
            Some("Bearer acc-1")
        );

        assert_eq!(reqs[2].target, "/api/v1/id/00000001-0000000000000001");
        assert_eq!(reqs[3].method, "POST");
        assert_eq!(reqs[3].target, "/api/v1/search/ai-research");
        assert_eq!(reqs[4].target, "/api/v1/auth/refresh");
    }

    #[tokio::test]
    async fn paginate_collects_both_flat_and_nested_envelopes() {
        // Test flat PaginatedResponse across 2 pages
        let (url, _, recorded) = spawn_mock_server(vec![
            (
                200,
                r#"{"items":[{"id":1},{"id":2}],"total":3,"page":1,"per_page":2,"pages":2}"#.into(),
            ),
            (
                200,
                r#"{"items":[{"id":3}],"total":3,"page":2,"per_page":2,"pages":2}"#.into(),
            ),
            // Nested pagination envelope (documents/diagrams/helpdesk style)
            (
                200,
                r#"{"items":[{"id":10}],"pagination":{"page":1,"per_page":1,"total":2,"pages":2}}"#
                    .into(),
            ),
            (
                200,
                r#"{"items":[{"id":11}],"pagination":{"page":2,"per_page":1,"total":2,"pages":2}}"#
                    .into(),
            ),
        ])
        .await;

        let client = ElderClient::new(
            ClientConfig::new(url)
                .unwrap()
                .with_bearer("tok")
                .with_retry(RetryConfig::disabled()),
        )
        .unwrap();

        let flat = client
            .paginate(
                "/api/v1/issues",
                Some(&[("status".into(), "open".into())]),
                Some(2),
                None,
            )
            .await
            .expect("flat paginate");
        assert_eq!(flat.len(), 3);
        assert_eq!(flat[2]["id"], 3);

        let nested = client
            .paginate("/api/v1/documents", None, Some(1), None)
            .await
            .expect("nested paginate");
        assert_eq!(nested.len(), 2);
        assert_eq!(nested[1]["id"], 11);

        let reqs = recorded.lock().await;
        assert!(reqs[0].target.contains("/api/v1/issues?"));
        assert!(reqs[0].target.contains("status=open"));
        assert!(reqs[0].target.contains("page=1"));
        assert!(reqs[1].target.contains("page=2"));
    }

    #[tokio::test]
    async fn retries_on_502_504_and_transient_disconnect_but_fails_fast_on_500() {
        // 502 then 504 then 200
        let (url, hits, _) = spawn_mock_server(vec![
            (502, "bad gateway".into()),
            (504, "gateway timeout".into()),
            (200, r#"{"status":"ok"}"#.into()),
        ])
        .await;
        let client =
            ElderClient::new(ClientConfig::new(url).unwrap().with_retry(fast_retry())).unwrap();
        let ok = client.status().await.expect("recovers after 502 and 504");
        assert_eq!(ok["status"], "ok");
        assert_eq!(hits.load(Ordering::SeqCst), 3);

        // 500 must fail fast without retrying
        let (url_500, hits_500, _) = spawn_mock_server(vec![
            (500, r#"{"error":"internal server error"}"#.into()),
            (200, r#"{"status":"ok"}"#.into()),
        ])
        .await;
        let client_500 =
            ElderClient::new(ClientConfig::new(url_500).unwrap().with_retry(fast_retry())).unwrap();
        let err = client_500.status().await.unwrap_err();
        assert!(matches!(err, ElderError::Api { status: 500, .. }));
        assert_eq!(hits_500.load(Ordering::SeqCst), 1);

        // Transient dropped connection on attempt 1, then 200 on attempt 2
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let conn_hits = Arc::new(AtomicUsize::new(0));
        let conn_hits_bg = Arc::clone(&conn_hits);
        tokio::spawn(async move {
            for attempt in 0..2 {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                conn_hits_bg.fetch_add(1, Ordering::SeqCst);
                if attempt == 0 {
                    // Drop immediately without response to simulate transient transport failure.
                    drop(sock);
                } else {
                    let _ = read_http_request(&mut sock).await;
                    let body = r#"{"status":"healthy"}"#;
                    let wire = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                        body.len()
                    );
                    let _ = sock.write_all(wire.as_bytes()).await;
                    let _ = sock.shutdown().await;
                }
            }
        });
        let client_drop = ElderClient::new(
            ClientConfig::new(format!("http://{addr}"))
                .unwrap()
                .with_retry(fast_retry()),
        )
        .unwrap();
        let res = client_drop
            .healthz()
            .await
            .expect("recovers from dropped TCP socket");
        assert_eq!(res["status"], "healthy");
        assert_eq!(conn_hits.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn retries_on_timeout_then_returns_http_error() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let hits = Arc::new(AtomicUsize::new(0));
        let hits_bg = Arc::clone(&hits);

        tokio::spawn(async move {
            for _ in 0..3 {
                let Ok((sock, _)) = listener.accept().await else {
                    break;
                };
                hits_bg.fetch_add(1, Ordering::SeqCst);
                tokio::spawn(async move {
                    tokio::time::sleep(Duration::from_millis(200)).await;
                    drop(sock);
                });
            }
        });

        let client = ElderClient::new(
            ClientConfig::new(format!("http://{addr}"))
                .unwrap()
                .with_timeout(Duration::from_millis(25))
                .with_retry(fast_retry()),
        )
        .unwrap();
        let err = client.healthz().await.unwrap_err();
        assert!(matches!(err, ElderError::Http(_)));
        assert_eq!(hits.load(Ordering::SeqCst), 3);
    }

    #[tokio::test]
    async fn handles_204_non_json_portal_login_ai_config_and_invoke_paginate() {
        let (url, _, recorded) = spawn_mock_server(vec![
            (204, String::new()),
            (200, "<svg></svg>".into()),
            (
                200,
                r#"{"access_token":"p-acc","refresh_token":"p-ref","token_type":"Bearer","expires_in":3600}"#
                    .into(),
            ),
            (200, r#"{"model":"gemma4:e4b","enabled":true}"#.into()),
            (
                200,
                r#"{"items":[{"id":1}],"total":2,"page":1,"per_page":1,"pages":2}"#.into(),
            ),
            (
                200,
                r#"{"items":[{"id":2}],"total":2,"page":2,"per_page":1,"pages":2}"#.into(),
            ),
        ])
        .await;

        let client = ElderClient::new(
            ClientConfig::new(url)
                .unwrap()
                .with_retry(RetryConfig::disabled()),
        )
        .unwrap();

        let no_content = client
            .request(HttpMethod::Delete, "/api/v1/issues/1", None, None)
            .await
            .expect("204");
        assert_eq!(no_content, Value::Null);

        let svg = client
            .request(HttpMethod::Get, "/api/v1/diagrams/1/export/svg", None, None)
            .await
            .expect("svg text");
        assert_eq!(svg, Value::String("<svg></svg>".into()));

        let portal_tokens = client
            .portal_login("portal@example.com", "secret", Some("acme"))
            .await
            .expect("portal login");
        assert_eq!(portal_tokens.access_token, "p-acc");
        assert_eq!(portal_tokens.expires_in, Some(3600));

        let cfg = client.ai_research_config().await.expect("ai config");
        assert_eq!(cfg["model"], "gemma4:e4b");

        let items = client
            .invoke_paginate(
                "issues",
                "list_issues",
                &HashMap::new(),
                None,
                Some(1),
                None,
            )
            .await
            .expect("invoke_paginate");
        assert_eq!(items.len(), 2);

        let reqs = recorded.lock().await;
        assert_eq!(reqs[2].target, "/api/v1/portal-auth/login");
        assert!(reqs[2].body.contains(r#""tenant":"acme""#));
        assert_eq!(reqs[3].target, "/api/v1/search/ai-research/config");
        assert!(reqs[4].target.starts_with("/api/v1/issues?"));
    }
}
