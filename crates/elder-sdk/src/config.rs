//! Client configuration, authentication credentials, and retry policy.
//!
//! All secret-bearing fields (`Bearer`, `ApiKey`) use redacted [`Debug`](std::fmt::Debug)
//! formatting so credentials are never printed or logged.

use std::fmt;
use std::time::Duration;

use reqwest::header::{HeaderName, HeaderValue, AUTHORIZATION};

use crate::error::{ElderError, Result};

/// Default base URL used when `ELDER_API_URL` is unset.
pub const DEFAULT_API_URL: &str = "http://localhost:5000";

/// Default total request timeout (seconds).
pub const DEFAULT_TIMEOUT_SECS: u64 = 30;

/// Default TCP/TLS connect timeout (seconds).
pub const DEFAULT_CONNECT_TIMEOUT_SECS: u64 = 10;

/// Default maximum retry attempts for transient `429` / `502` / `503` / `504` / transport failures.
pub const DEFAULT_MAX_RETRIES: usize = 3;

/// Default initial exponential backoff delay (milliseconds).
pub const DEFAULT_INITIAL_BACKOFF_MS: u64 = 250;

/// Maximum exponential backoff delay cap (seconds).
pub const DEFAULT_MAX_BACKOFF_SECS: u64 = 10;

const MAX_CONFIGURED_TIMEOUT_SECS: u64 = 3600;
const X_API_KEY_HEADER: HeaderName = HeaderName::from_static("x-api-key");

/// Authentication credentials sent on `hub-api` requests.
#[derive(Clone, PartialEq, Eq, Default)]
pub enum Auth {
    /// No credentials attached (suitable for `/healthz`, `/api/v1/status`, and `login()`).
    #[default]
    None,
    /// `Authorization: Bearer <jwt>` credential.
    Bearer(String),
    /// `X-API-Key: <key>` credential.
    ApiKey(String),
}

impl fmt::Debug for Auth {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::None => f.write_str("Auth::None"),
            Self::Bearer(_) => f.write_str("Auth::Bearer([REDACTED])"),
            Self::ApiKey(_) => f.write_str("Auth::ApiKey([REDACTED])"),
        }
    }
}

impl Auth {
    /// Return `true` if credentials are present (`Bearer` or `ApiKey`).
    pub fn is_authenticated(&self) -> bool {
        !matches!(self, Self::None)
    }

    /// Validate that non-`None` credentials are non-empty and valid HTTP header values.
    pub fn validate(&self) -> Result<()> {
        match self {
            Self::None => Ok(()),
            Self::Bearer(token) => {
                if token.trim().is_empty() {
                    return Err(ElderError::Config(
                        "Bearer token cannot be empty".to_string(),
                    ));
                }
                HeaderValue::from_str(&format!("Bearer {}", token.trim())).map_err(|_| {
                    ElderError::Config("Bearer token contains invalid header characters".into())
                })?;
                Ok(())
            }
            Self::ApiKey(key) => {
                if key.trim().is_empty() {
                    return Err(ElderError::Config("API key cannot be empty".to_string()));
                }
                HeaderValue::from_str(key.trim()).map_err(|_| {
                    ElderError::Config("API key contains invalid header characters".into())
                })?;
                Ok(())
            }
        }
    }

    /// Build the `(HeaderName, HeaderValue)` pair for this authentication method.
    pub(crate) fn header_pair(&self) -> Result<Option<(HeaderName, HeaderValue)>> {
        match self {
            Self::None => Ok(None),
            Self::Bearer(token) => {
                let trimmed = token.trim();
                if trimmed.is_empty() {
                    return Err(ElderError::Config(
                        "Bearer token cannot be empty".to_string(),
                    ));
                }
                let val = HeaderValue::from_str(&format!("Bearer {trimmed}")).map_err(|_| {
                    ElderError::Config("Bearer token contains invalid header characters".into())
                })?;
                Ok(Some((AUTHORIZATION, val)))
            }
            Self::ApiKey(key) => {
                let trimmed = key.trim();
                if trimmed.is_empty() {
                    return Err(ElderError::Config("API key cannot be empty".to_string()));
                }
                let val = HeaderValue::from_str(trimmed).map_err(|_| {
                    ElderError::Config("API key contains invalid header characters".into())
                })?;
                Ok(Some((X_API_KEY_HEADER, val)))
            }
        }
    }
}

/// Exponential backoff retry policy for transient failures.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RetryConfig {
    /// Maximum number of retry attempts after the initial request (default `3`).
    pub max_retries: usize,
    /// Initial backoff delay before the first retry (default `250ms`).
    pub initial_backoff: Duration,
    /// Upper bound on backoff delay between retries (default `10s`).
    pub max_backoff: Duration,
}

impl Default for RetryConfig {
    fn default() -> Self {
        Self {
            max_retries: DEFAULT_MAX_RETRIES,
            initial_backoff: Duration::from_millis(DEFAULT_INITIAL_BACKOFF_MS),
            max_backoff: Duration::from_secs(DEFAULT_MAX_BACKOFF_SECS),
        }
    }
}

impl RetryConfig {
    /// Disable automatic retries (`max_retries = 0`).
    pub fn disabled() -> Self {
        Self {
            max_retries: 0,
            ..Self::default()
        }
    }

    /// Compute the exponential backoff delay for zero-based retry `attempt`.
    pub fn delay_for_attempt(&self, attempt: usize) -> Duration {
        let shift = u32::try_from(attempt).unwrap_or(10).min(10);
        self.initial_backoff
            .saturating_mul(1u32 << shift)
            .min(self.max_backoff)
    }
}

/// Configuration for [`ElderClient`](crate::ElderClient).
#[derive(Clone, PartialEq, Eq)]
pub struct ClientConfig {
    /// Base URL of Elder `hub-api` (e.g. `https://elder.example.com` or `http://localhost:5000`).
    pub base_url: String,
    /// Authentication credential mode.
    pub auth: Auth,
    /// Overall request timeout.
    pub timeout: Duration,
    /// TCP/TLS connection timeout.
    pub connect_timeout: Duration,
    /// Retry configuration for transient status codes and transport errors.
    pub retry: RetryConfig,
    /// `User-Agent` header sent on all requests.
    pub user_agent: String,
}

impl fmt::Debug for ClientConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ClientConfig")
            .field("base_url", &self.base_url)
            .field("auth", &self.auth)
            .field("timeout", &self.timeout)
            .field("connect_timeout", &self.connect_timeout)
            .field("retry", &self.retry)
            .field("user_agent", &self.user_agent)
            .finish()
    }
}

impl ClientConfig {
    /// Construct a validated configuration targeting `base_url` with [`Auth::None`].
    pub fn new(base_url: impl AsRef<str>) -> Result<Self> {
        let normalized = normalize_base_url(base_url.as_ref())?;
        Ok(Self {
            base_url: normalized,
            auth: Auth::None,
            timeout: Duration::from_secs(DEFAULT_TIMEOUT_SECS),
            connect_timeout: Duration::from_secs(DEFAULT_CONNECT_TIMEOUT_SECS),
            retry: RetryConfig::default(),
            user_agent: format!("elder-sdk/{}", env!("CARGO_PKG_VERSION")),
        })
    }

    /// Load configuration from process environment variables.
    ///
    /// Supported environment variables:
    /// - `ELDER_API_URL`: Base API URL (defaults to `http://localhost:5000` when unset/empty)
    /// - `ELDER_TOKEN`: Bearer JWT token (takes precedence over `ELDER_API_KEY`)
    /// - `ELDER_API_KEY`: API key sent via `X-API-Key`
    /// - `ELDER_TIMEOUT_SECS`: Total request timeout in seconds (`1..=3600`, default `30`)
    pub fn from_env() -> Result<Self> {
        Self::from_env_lookup(|key| std::env::var(key).ok())
    }

    /// Load configuration using an arbitrary key lookup closure (useful for deterministic tests).
    pub fn from_env_lookup<F>(lookup: F) -> Result<Self>
    where
        F: Fn(&str) -> Option<String>,
    {
        let raw_url =
            non_empty(lookup("ELDER_API_URL")).unwrap_or_else(|| DEFAULT_API_URL.to_string());
        let mut cfg = Self::new(raw_url)?;

        if let Some(token) = non_empty(lookup("ELDER_TOKEN")) {
            cfg.auth = Auth::Bearer(token);
        } else if let Some(api_key) = non_empty(lookup("ELDER_API_KEY")) {
            cfg.auth = Auth::ApiKey(api_key);
        }

        if let Some(raw_timeout) = non_empty(lookup("ELDER_TIMEOUT_SECS")) {
            let secs = raw_timeout.parse::<u64>().map_err(|e| {
                ElderError::Config(format!("invalid ELDER_TIMEOUT_SECS {raw_timeout:?}: {e}"))
            })?;
            if secs == 0 || secs > MAX_CONFIGURED_TIMEOUT_SECS {
                return Err(ElderError::Config(format!(
                    "ELDER_TIMEOUT_SECS must be between 1 and {MAX_CONFIGURED_TIMEOUT_SECS}, got {secs}"
                )));
            }
            cfg.timeout = Duration::from_secs(secs);
        }

        cfg.validate()?;
        Ok(cfg)
    }

    /// Set authentication credentials.
    pub fn with_auth(mut self, auth: Auth) -> Self {
        self.auth = auth;
        self
    }

    /// Configure Bearer JWT authentication.
    pub fn with_bearer(mut self, token: impl Into<String>) -> Self {
        self.auth = Auth::Bearer(token.into());
        self
    }

    /// Configure `X-API-Key` authentication.
    pub fn with_api_key(mut self, key: impl Into<String>) -> Self {
        self.auth = Auth::ApiKey(key.into());
        self
    }

    /// Override total request timeout.
    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }

    /// Override TCP/TLS connection timeout.
    pub fn with_connect_timeout(mut self, connect_timeout: Duration) -> Self {
        self.connect_timeout = connect_timeout;
        self
    }

    /// Override retry configuration.
    pub fn with_retry(mut self, retry: RetryConfig) -> Self {
        self.retry = retry;
        self
    }

    /// Override the `User-Agent` header value.
    pub fn with_user_agent(mut self, user_agent: impl Into<String>) -> Self {
        self.user_agent = user_agent.into();
        self
    }

    /// Validate all configuration invariants before building a client.
    pub fn validate(&self) -> Result<()> {
        normalize_base_url(&self.base_url)?;
        self.auth.validate()?;
        if self.timeout.is_zero() {
            return Err(ElderError::Config(
                "request timeout must be greater than zero".into(),
            ));
        }
        if self.connect_timeout.is_zero() {
            return Err(ElderError::Config(
                "connect timeout must be greater than zero".into(),
            ));
        }
        if self.user_agent.trim().is_empty() {
            return Err(ElderError::Config("user_agent must not be empty".into()));
        }
        Ok(())
    }
}

pub(crate) fn normalize_base_url(raw: &str) -> Result<String> {
    let trimmed = raw.trim().trim_end_matches('/');
    if !trimmed.starts_with("http://") && !trimmed.starts_with("https://") {
        return Err(ElderError::Config(format!(
            "ELDER_API_URL must start with http:// or https://, got {raw:?}"
        )));
    }
    let parsed = url::Url::parse(trimmed)
        .map_err(|e| ElderError::Config(format!("invalid ELDER_API_URL {raw:?}: {e}")))?;
    if parsed.host_str().is_none() {
        return Err(ElderError::Config(format!(
            "ELDER_API_URL missing host: {raw:?}"
        )));
    }
    Ok(trimmed.to_string())
}

fn non_empty(value: Option<String>) -> Option<String> {
    value.and_then(|v| {
        let trimmed = v.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn lookup_from(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
            .collect();
        move |k: &str| map.get(k).cloned()
    }

    #[test]
    fn masks_secrets_in_debug_output() {
        let secret_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.supersecret";
        let secret_key = "elder_super_secret_api_key_value";

        let bearer = Auth::Bearer(secret_token.into());
        let api_key = Auth::ApiKey(secret_key.into());
        assert!(!format!("{bearer:?}").contains("supersecret"));
        assert!(!format!("{api_key:?}").contains("elder_super"));
        assert_eq!(format!("{bearer:?}"), "Auth::Bearer([REDACTED])");
        assert_eq!(format!("{api_key:?}"), "Auth::ApiKey([REDACTED])");

        let cfg = ClientConfig::new("https://elder.example.com/")
            .expect("valid url")
            .with_bearer(secret_token);
        let dbg = format!("{cfg:?}");
        assert!(!dbg.contains("supersecret"));
        assert!(dbg.contains("Auth::Bearer([REDACTED])"));
        assert_eq!(cfg.base_url, "https://elder.example.com");
    }

    #[test]
    fn from_env_lookup_parses_all_supported_variables() {
        let defaults = ClientConfig::from_env_lookup(lookup_from(&[])).expect("default cfg");
        assert_eq!(defaults.base_url, DEFAULT_API_URL);
        assert_eq!(defaults.auth, Auth::None);
        assert_eq!(defaults.timeout, Duration::from_secs(DEFAULT_TIMEOUT_SECS));

        let with_token = ClientConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "https://hub.elder.internal/"),
            ("ELDER_TOKEN", "tok-abc"),
            ("ELDER_API_KEY", "key-ignored-when-token-set"),
            ("ELDER_TIMEOUT_SECS", "45"),
        ]))
        .expect("token cfg");
        assert_eq!(with_token.base_url, "https://hub.elder.internal");
        assert_eq!(with_token.auth, Auth::Bearer("tok-abc".into()));
        assert_eq!(with_token.timeout, Duration::from_secs(45));

        let with_key = ClientConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://127.0.0.1:5000"),
            ("ELDER_API_KEY", "elder_key_123"),
        ]))
        .expect("api key cfg");
        assert_eq!(with_key.auth, Auth::ApiKey("elder_key_123".into()));
    }

    #[test]
    fn rejects_invalid_urls_timeouts_and_header_values() {
        assert!(ClientConfig::new("ftp://elder.example.com").is_err());
        assert!(ClientConfig::new("http://").is_err());

        assert!(
            ClientConfig::from_env_lookup(lookup_from(&[("ELDER_API_URL", "not-a-url"),])).is_err()
        );

        assert!(
            ClientConfig::from_env_lookup(lookup_from(&[("ELDER_TIMEOUT_SECS", "0")])).is_err()
        );

        assert!(
            ClientConfig::from_env_lookup(lookup_from(&[("ELDER_TIMEOUT_SECS", "abc")])).is_err()
        );

        assert!(
            ClientConfig::from_env_lookup(lookup_from(&[("ELDER_TOKEN", "bad\nline")])).is_err()
        );
    }
}
