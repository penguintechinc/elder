//! Resilient HTTP client for `hub-api` using `rustls-tls` and per-request JWTs.

use std::time::Duration;

use reqwest::{Method, StatusCode};
use serde::de::DeserializeOwned;
use serde::Serialize;

use crate::auth::ServiceTokenProvider;
use crate::error::{CommonError, Result};

const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
const DEFAULT_MAX_RETRIES: usize = 3;
const DEFAULT_INITIAL_BACKOFF: Duration = Duration::from_millis(250);
const MAX_BACKOFF: Duration = Duration::from_secs(10);
const MAX_ERROR_BODY_BYTES: usize = 1024;

/// Typed REST client for Elder API endpoints with bounded exponential backoff retries.
#[derive(Debug, Clone)]
pub struct ElderApiClient {
    http: reqwest::Client,
    base_url: String,
    auth: ServiceTokenProvider,
    max_retries: usize,
    initial_backoff: Duration,
}

impl ElderApiClient {
    /// Construct a new Elder API client using `rustls-tls` (never OpenSSL).
    pub fn new(base_url: impl AsRef<str>, auth: ServiceTokenProvider) -> Result<Self> {
        let base = normalize_base_url(base_url.as_ref())?;
        let http = reqwest::Client::builder()
            .connect_timeout(DEFAULT_CONNECT_TIMEOUT)
            .timeout(DEFAULT_REQUEST_TIMEOUT)
            .user_agent("elder-svc-common/0.1")
            .build()?;

        Ok(Self {
            http,
            base_url: base,
            auth,
            max_retries: DEFAULT_MAX_RETRIES,
            initial_backoff: DEFAULT_INITIAL_BACKOFF,
        })
    }

    /// Override retry attempts and initial exponential backoff delay.
    pub fn with_retry_config(mut self, max_retries: usize, initial_backoff: Duration) -> Self {
        self.max_retries = max_retries;
        self.initial_backoff = initial_backoff;
        self
    }

    /// Return the normalized base URL configured for this client.
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// Issue a `GET` request and deserialize the JSON response.
    pub async fn get_json<T: DeserializeOwned>(&self, path: &str) -> Result<T> {
        self.request_json::<(), T>(Method::GET, path, None).await
    }

    /// Issue a `POST` request with a JSON body and deserialize the JSON response.
    pub async fn post_json<B: Serialize + Sync, T: DeserializeOwned>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        self.request_json(Method::POST, path, Some(body)).await
    }

    /// Issue a `POST` request with an empty body and deserialize the JSON response.
    pub async fn post_empty<T: DeserializeOwned>(&self, path: &str) -> Result<T> {
        self.request_json::<(), T>(Method::POST, path, None).await
    }

    /// Issue a `PUT` request with a JSON body and deserialize the JSON response.
    pub async fn put_json<B: Serialize + Sync, T: DeserializeOwned>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        self.request_json(Method::PUT, path, Some(body)).await
    }

    /// Issue a `PATCH` request with a JSON body and deserialize the JSON response.
    pub async fn patch_json<B: Serialize + Sync, T: DeserializeOwned>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        self.request_json(Method::PATCH, path, Some(body)).await
    }

    /// Return whether an HTTP status code is transient and safe to retry.
    ///
    /// Retries strictly on `5xx` server errors; all `4xx` responses (including
    /// `401`, `403`, `404`, and `429`) fail fast without retrying.
    pub fn is_retryable_status(status: StatusCode) -> bool {
        status.is_server_error()
    }

    async fn request_json<B: Serialize + Sync, T: DeserializeOwned>(
        &self,
        method: Method,
        path: &str,
        body: Option<&B>,
    ) -> Result<T> {
        let url = join_url(&self.base_url, path);
        let serialized_body = body.map(serde_json::to_vec).transpose()?;

        for attempt in 0..=self.max_retries {
            let token = self.auth.token()?;
            let mut req = self.http.request(method.clone(), &url).bearer_auth(token);
            if let Some(bytes) = &serialized_body {
                req = req
                    .header(reqwest::header::CONTENT_TYPE, "application/json")
                    .body(bytes.clone());
            }

            match req.send().await {
                Ok(resp) => {
                    let status = resp.status();
                    if status.is_success() {
                        let parsed = resp.json::<T>().await?;
                        return Ok(parsed);
                    }

                    let body_text = resp.text().await.unwrap_or_default();
                    let truncated = truncate_body(&body_text, MAX_ERROR_BODY_BYTES);
                    if Self::is_retryable_status(status) && attempt < self.max_retries {
                        let delay = backoff_delay(self.initial_backoff, attempt);
                        tracing::warn!(
                            method = %method,
                            url = %url,
                            status = status.as_u16(),
                            attempt = attempt + 1,
                            delay_ms = delay.as_millis() as u64,
                            "Transient 5xx from Elder API; retrying"
                        );
                        tokio::time::sleep(delay).await;
                        continue;
                    }

                    return Err(CommonError::Http {
                        status: status.as_u16(),
                        body: truncated,
                    });
                }
                Err(err) => {
                    if attempt < self.max_retries {
                        let delay = backoff_delay(self.initial_backoff, attempt);
                        tracing::warn!(
                            method = %method,
                            url = %url,
                            attempt = attempt + 1,
                            delay_ms = delay.as_millis() as u64,
                            error = %err,
                            "Transport error contacting Elder API; retrying"
                        );
                        tokio::time::sleep(delay).await;
                        continue;
                    }
                    return Err(CommonError::Transport(err));
                }
            }
        }

        Err(CommonError::Execution(
            "retry loop exhausted unexpectedly".into(),
        ))
    }
}

fn normalize_base_url(raw: &str) -> Result<String> {
    let trimmed = raw.trim().trim_end_matches('/');
    if !trimmed.starts_with("http://") && !trimmed.starts_with("https://") {
        return Err(CommonError::Config(format!(
            "ELDER_API_URL must start with http:// or https://, got {raw:?}"
        )));
    }
    let parsed = reqwest::Url::parse(trimmed)
        .map_err(|e| CommonError::Config(format!("invalid ELDER_API_URL {raw:?}: {e}")))?;
    if parsed.host_str().is_none() {
        return Err(CommonError::Config(format!(
            "ELDER_API_URL missing host: {raw:?}"
        )));
    }
    Ok(trimmed.to_string())
}

fn join_url(base: &str, path: &str) -> String {
    if path.starts_with('/') {
        format!("{base}{path}")
    } else {
        format!("{base}/{path}")
    }
}

fn backoff_delay(initial: Duration, attempt: usize) -> Duration {
    let shift = u32::try_from(attempt).unwrap_or(10).min(10);
    initial.saturating_mul(1u32 << shift).min(MAX_BACKOFF)
}

fn truncate_body(body: &str, max_bytes: usize) -> String {
    if body.len() <= max_bytes {
        return body.to_string();
    }
    let mut end = max_bytes;
    while end > 0 && !body.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}...(truncated)", &body[..end])
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    #[derive(Debug, Deserialize, PartialEq)]
    struct Pong {
        ok: bool,
    }

    fn test_auth() -> ServiceTokenProvider {
        ServiceTokenProvider::from_env_lookup("svc-scanner", vec!["discovery:read".into()], |k| {
            match k {
                "JWT_SECRET_KEY" => Some("x".repeat(32)),
                _ => None,
            }
        })
        .expect("auth")
    }

    async fn spawn_mock_server(statuses: Vec<(u16, &'static str)>) -> (String, Arc<AtomicUsize>) {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind loopback");
        let addr = listener.local_addr().expect("addr");
        let counter = Arc::new(AtomicUsize::new(0));
        let counter_clone = Arc::clone(&counter);

        tokio::spawn(async move {
            for (code, body) in statuses {
                let Ok((mut socket, _)) = listener.accept().await else {
                    break;
                };
                counter_clone.fetch_add(1, Ordering::SeqCst);
                let mut buf = [0u8; 4096];
                let _ = socket.read(&mut buf).await;
                let resp = format!(
                    "HTTP/1.1 {code} TEST\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = socket.write_all(resp.as_bytes()).await;
                let _ = socket.shutdown().await;
            }
        });

        (format!("http://{addr}"), counter)
    }

    #[test]
    fn retry_predicate_retries_only_5xx() {
        assert!(!ElderApiClient::is_retryable_status(
            StatusCode::BAD_REQUEST
        ));
        assert!(!ElderApiClient::is_retryable_status(
            StatusCode::UNAUTHORIZED
        ));
        assert!(!ElderApiClient::is_retryable_status(StatusCode::FORBIDDEN));
        assert!(!ElderApiClient::is_retryable_status(StatusCode::NOT_FOUND));
        assert!(!ElderApiClient::is_retryable_status(
            StatusCode::TOO_MANY_REQUESTS
        ));
        assert!(ElderApiClient::is_retryable_status(
            StatusCode::INTERNAL_SERVER_ERROR
        ));
        assert!(ElderApiClient::is_retryable_status(StatusCode::BAD_GATEWAY));
        assert!(ElderApiClient::is_retryable_status(
            StatusCode::SERVICE_UNAVAILABLE
        ));
        assert!(ElderApiClient::is_retryable_status(
            StatusCode::GATEWAY_TIMEOUT
        ));
    }

    #[tokio::test]
    async fn retries_on_5xx_then_succeeds() {
        let (url, hits) =
            spawn_mock_server(vec![(503, r#"{"err":1}"#), (200, r#"{"ok":true}"#)]).await;
        let client = ElderApiClient::new(url, test_auth())
            .expect("client")
            .with_retry_config(2, Duration::from_millis(5));
        let res: Pong = client.get_json("/ping").await.expect("should recover");
        assert_eq!(res, Pong { ok: true });
        assert_eq!(hits.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn fails_fast_on_401_and_429_without_retrying() {
        for status in [401, 403, 429] {
            let (url, hits) = spawn_mock_server(vec![
                (status, r#"{"error":"denied"}"#),
                (200, r#"{"ok":true}"#),
            ])
            .await;
            let client = ElderApiClient::new(url, test_auth())
                .expect("client")
                .with_retry_config(3, Duration::from_millis(5));
            let err = client.get_json::<Pong>("/ping").await.unwrap_err();
            assert!(matches!(err, CommonError::Http { status: s, .. } if s == status));
            assert_eq!(hits.load(Ordering::SeqCst), 1);
        }
    }

    #[test]
    fn rejects_invalid_base_url() {
        assert!(ElderApiClient::new("ftp://hub-api:5000", test_auth()).is_err());
        assert!(ElderApiClient::new("http://", test_auth()).is_err());
    }
}
