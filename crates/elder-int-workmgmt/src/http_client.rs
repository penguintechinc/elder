//! Shared pure-`rustls` HTTP helpers, URL normalization, retries, and pagination parsing.

use std::time::Duration;

use reqwest::header::HeaderMap;
use reqwest::{Method, Response, StatusCode};
use serde_json::Value;
use url::Url;

use crate::error::{Result, WorkMgmtError};

const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_ERROR_BODY_BYTES: usize = 1024;
const MAX_RETRY_AFTER_SECS: f64 = 30.0;
const MAX_BACKOFF: Duration = Duration::from_secs(4);

/// Shared connection/retry configuration for `get_with_fallbacks`.
pub struct FallbackGetClient<'a> {
    pub http: &'a reqwest::Client,
    pub platform: &'static str,
    pub base_url: &'a str,
    pub headers: &'a HeaderMap,
    pub base_delay: Duration,
}

/// Build a pure-`rustls` HTTP client with standard timeouts and user agent.
pub fn build_http_client(verify_ssl: bool) -> Result<reqwest::Client> {
    let mut builder = reqwest::Client::builder()
        .connect_timeout(DEFAULT_CONNECT_TIMEOUT)
        .timeout(DEFAULT_REQUEST_TIMEOUT)
        .user_agent("elder-int-workmgmt/0.1");

    if !verify_ssl {
        builder = builder.danger_accept_invalid_certs(true);
    }

    builder.build().map_err(|source| WorkMgmtError::Transport {
        platform: "http_client",
        source,
    })
}

/// Normalize a base URL, optionally prepending `https://` when no scheme is present.
pub fn normalize_base_url(raw: &str, default_https: bool) -> Result<String> {
    let trimmed = raw.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        return Err(WorkMgmtError::Config("base URL must not be empty".into()));
    }

    let candidate = if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
        trimmed.to_string()
    } else if default_https && !trimmed.contains("://") {
        format!("https://{trimmed}")
    } else {
        return Err(WorkMgmtError::Config(format!(
            "unsupported URL scheme in {raw:?}; expected http:// or https://"
        )));
    };

    let parsed = Url::parse(&candidate)?;
    if parsed.host_str().is_none() {
        return Err(WorkMgmtError::Config(format!("URL missing host: {raw:?}")));
    }
    Ok(candidate)
}

/// Ensure `base` ends with `suffix` (e.g., `"/api/v4"` or `"/1"` or `"/api/v3"`).
pub fn ensure_path_suffix(base: &str, suffix: &str) -> String {
    let clean_base = base.trim_end_matches('/');
    let clean_suffix = suffix.trim_end_matches('/');
    if clean_base.ends_with(clean_suffix) {
        clean_base.to_string()
    } else {
        format!("{clean_base}{clean_suffix}")
    }
}

/// Strip a known path suffix from `base` if already present.
pub fn strip_path_suffix(base: &str, suffix: &str) -> String {
    let clean_base = base.trim_end_matches('/');
    let clean_suffix = suffix.trim_end_matches('/');
    clean_base
        .strip_suffix(clean_suffix)
        .unwrap_or(clean_base)
        .trim_end_matches('/')
        .to_string()
}

/// Percent-encode a single URL path segment per RFC 3986 unreserved characters.
pub fn encode_path_segment(segment: &str) -> String {
    let mut out = String::with_capacity(segment.len());
    for byte in segment.as_bytes() {
        let is_unreserved =
            byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~');
        if is_unreserved {
            out.push(*byte as char);
        } else {
            out.push('%');
            out.push_str(&format!("{byte:02X}"));
        }
    }
    out
}

/// Parse RFC 8288 `Link` header and return the URL whose `rel` contains `"next"`.
pub fn parse_next_link(header_value: &str) -> Option<String> {
    for part in header_value.split(',') {
        let mut segments = part.split(';');
        let url_part = segments.next()?.trim();
        let raw_url = url_part
            .strip_prefix('<')
            .and_then(|s| s.strip_suffix('>'))?
            .trim();

        let is_next = segments.any(|param| {
            let trimmed = param.trim();
            if let Some((key, val)) = trimmed.split_once('=') {
                if key.trim().eq_ignore_ascii_case("rel") {
                    let unquoted = val.trim().trim_matches('"').trim_matches('\'');
                    return unquoted
                        .split_whitespace()
                        .any(|tok| tok.eq_ignore_ascii_case("next"));
                }
            }
            false
        });

        if is_next && !raw_url.is_empty() {
            return Some(raw_url.to_string());
        }
    }
    None
}

/// Resolve a potentially relative `Link` target against the base API URL.
pub fn resolve_link_url(base_url: &str, target: &str) -> Result<String> {
    if target.starts_with("http://") || target.starts_with("https://") {
        return Ok(target.to_string());
    }
    let base = Url::parse(&format!("{}/", base_url.trim_end_matches('/')))?;
    Ok(base.join(target)?.to_string())
}

/// Truncate an HTTP error body safely on a UTF-8 character boundary.
pub fn truncate_body(body: &str) -> String {
    if body.len() <= MAX_ERROR_BODY_BYTES {
        return body.to_string();
    }
    let mut end = MAX_ERROR_BODY_BYTES;
    while end > 0 && !body.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}...(truncated)", &body[..end])
}

/// Parse `Retry-After` seconds header with bounded fallback exponential delay.
pub fn parse_retry_after(headers: &HeaderMap, attempt: usize, base_delay: Duration) -> Duration {
    if let Some(raw) = headers.get(reqwest::header::RETRY_AFTER) {
        if let Ok(text) = raw.to_str() {
            if let Ok(secs) = text.trim().parse::<f64>() {
                let bounded = secs.clamp(0.0, MAX_RETRY_AFTER_SECS);
                return Duration::from_secs_f64(bounded);
            }
        }
    }
    backoff_delay(base_delay, attempt)
}

/// Compute bounded exponential backoff delay.
pub fn backoff_delay(base: Duration, attempt: usize) -> Duration {
    let shift = u32::try_from(attempt).unwrap_or(6).min(6);
    base.saturating_mul(1u32 << shift).min(MAX_BACKOFF)
}

/// Parse JSON from an HTTP response safely, returning `None` on empty/non-JSON bodies.
pub async fn response_json_lossy(resp: Response) -> (StatusCode, HeaderMap, Option<Value>, String) {
    let status = resp.status();
    let headers = resp.headers().clone();
    if status == StatusCode::NO_CONTENT {
        return (status, headers, None, String::new());
    }
    let text = resp.text().await.unwrap_or_default();
    if text.trim().is_empty() {
        return (status, headers, None, text);
    }
    let parsed = serde_json::from_str::<Value>(&text).ok();
    (status, headers, parsed, text)
}

/// Execute an HTTP request with retries on `429` (`Retry-After`), `5xx`, and transport errors.
pub async fn send_with_retry<F>(
    platform: &'static str,
    build_req: F,
    max_attempts: usize,
    base_delay: Duration,
) -> Result<(StatusCode, HeaderMap, Option<Value>, String)>
where
    F: Fn() -> reqwest::RequestBuilder,
{
    let attempts = max_attempts.max(1);
    for attempt in 0..attempts {
        match build_req().send().await {
            Ok(resp) => {
                let status = resp.status();
                let headers = resp.headers().clone();
                if status == StatusCode::TOO_MANY_REQUESTS && attempt + 1 < attempts {
                    let delay = parse_retry_after(&headers, attempt, base_delay);
                    tracing::warn!(
                        platform,
                        status = status.as_u16(),
                        attempt = attempt + 1,
                        delay_ms = delay.as_millis() as u64,
                        "External API returned 429; backing off"
                    );
                    if !delay.is_zero() {
                        tokio::time::sleep(delay).await;
                    }
                    continue;
                }
                if status.is_server_error() && attempt + 1 < attempts {
                    let delay = backoff_delay(base_delay, attempt);
                    tracing::warn!(
                        platform,
                        status = status.as_u16(),
                        attempt = attempt + 1,
                        delay_ms = delay.as_millis() as u64,
                        "External API returned 5xx; retrying"
                    );
                    if !delay.is_zero() {
                        tokio::time::sleep(delay).await;
                    }
                    continue;
                }
                return Ok(response_json_lossy(resp).await);
            }
            Err(source) => {
                if attempt + 1 < attempts {
                    let delay = backoff_delay(base_delay, attempt);
                    tracing::warn!(
                        platform,
                        attempt = attempt + 1,
                        delay_ms = delay.as_millis() as u64,
                        error = %source,
                        "Transport error contacting external API; retrying"
                    );
                    if !delay.is_zero() {
                        tokio::time::sleep(delay).await;
                    }
                    continue;
                }
                return Err(WorkMgmtError::Transport { platform, source });
            }
        }
    }

    Err(WorkMgmtError::Auth(format!(
        "{platform} request retry loop exhausted"
    )))
}

/// Execute an HTTP `GET` against `primary_path` with `404`/`410` fallback paths,
/// `429` `Retry-After` handling, and `5xx` retries.
pub async fn get_with_fallbacks(
    cfg: &FallbackGetClient<'_>,
    primary_path: &str,
    fallback_paths: &[&str],
    query: &[(String, String)],
) -> Result<(StatusCode, Option<Value>, String)> {
    let mut paths = Vec::with_capacity(1 + fallback_paths.len());
    paths.push(primary_path);
    paths.extend_from_slice(fallback_paths);

    let clean_base = cfg.base_url.trim_end_matches('/');
    for (idx, path) in paths.iter().enumerate() {
        let has_next = idx + 1 < paths.len();
        let full_url = if path.starts_with('/') {
            format!("{clean_base}{path}")
        } else {
            format!("{clean_base}/{path}")
        };

        let (status, _resp_headers, json_body, raw_body) = send_with_retry(
            cfg.platform,
            || {
                let mut req = cfg
                    .http
                    .request(Method::GET, &full_url)
                    .headers(cfg.headers.clone());
                if !query.is_empty() {
                    req = req.query(query);
                }
                req
            },
            3,
            cfg.base_delay,
        )
        .await?;

        if matches!(status, StatusCode::NOT_FOUND | StatusCode::GONE) && has_next {
            tracing::debug!(
                platform = cfg.platform,
                path = *path,
                status = status.as_u16(),
                "Endpoint returned 404/410; trying fallback path"
            );
            continue;
        }

        return Ok((status, json_body, raw_body));
    }

    Ok((StatusCode::NOT_FOUND, None, String::new()))
}
