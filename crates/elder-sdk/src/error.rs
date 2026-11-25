//! Unified error types returned by [`elder_sdk`](crate).

use serde_json::Value;
use thiserror::Error;

/// Unified error type for all `elder-sdk` operations.
#[derive(Debug, Error)]
pub enum ElderError {
    /// Missing or invalid SDK configuration or environment variable.
    #[error("configuration error: {0}")]
    Config(String),

    /// Transport-level HTTP failure (DNS, connect, TLS, timeout, stream error).
    #[error("HTTP transport error: {0}")]
    Http(#[from] reqwest::Error),

    /// Structured non-success response returned by `hub-api`.
    #[error("API error (HTTP {status}{}): {message}", format_code(.code))]
    Api {
        /// HTTP status code returned by `hub-api`.
        status: u16,
        /// Optional machine-readable error code parsed from response JSON.
        code: Option<String>,
        /// Human-readable error message.
        message: String,
        /// Optional parsed response body (or raw text wrapped as string).
        body: Option<Value>,
    },

    /// Authentication or authorization failure (`401` / `403`, or malformed auth response).
    #[error("authentication error (HTTP {status}): {message}")]
    Auth {
        /// HTTP status code (`401`, `403`, or `0` for local auth-response errors).
        status: u16,
        /// Sanitized error message.
        message: String,
    },

    /// Requested resource or endpoint was not found (`404`).
    #[error("not found: {0}")]
    NotFound(String),

    /// Rate limit exceeded (`429`) after exhausting retry attempts.
    #[error("rate limited (HTTP 429): {0}")]
    RateLimited(String),

    /// Client-side input, URL, or parameter validation error.
    #[error("validation error: {0}")]
    Validation(String),

    /// JSON serialization or deserialization error.
    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    /// API operation catalog lookup or path-parameter interpolation error.
    #[error("catalog error: {0}")]
    Catalog(String),
}

impl ElderError {
    /// Return the HTTP status code associated with this error, if applicable.
    pub fn status(&self) -> Option<u16> {
        match self {
            Self::Api { status, .. } => Some(*status),
            Self::Auth { status, .. } if *status > 0 => Some(*status),
            Self::NotFound(_) => Some(404),
            Self::RateLimited(_) => Some(429),
            _ => None,
        }
    }

    /// Return `true` if this error represents a client authentication/permission failure.
    pub fn is_auth(&self) -> bool {
        matches!(self, Self::Auth { .. })
    }

    /// Return `true` if this error represents HTTP 404 Not Found.
    pub fn is_not_found(&self) -> bool {
        matches!(self, Self::NotFound(_))
    }

    /// Return `true` if this error represents HTTP 429 Rate Limited.
    pub fn is_rate_limited(&self) -> bool {
        matches!(self, Self::RateLimited(_))
    }
}

fn format_code(code: &Option<String>) -> String {
    match code {
        Some(c) if !c.is_empty() => format!(" [{c}]"),
        _ => String::new(),
    }
}

/// Convenience result alias for `elder-sdk`.
pub type Result<T> = std::result::Result<T, ElderError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exposes_status_and_classification_helpers() {
        let auth_err = ElderError::Auth {
            status: 401,
            message: "unauthorized".into(),
        };
        assert_eq!(auth_err.status(), Some(401));
        assert!(auth_err.is_auth());
        assert!(!auth_err.is_not_found());

        let not_found = ElderError::NotFound("entity 42".into());
        assert_eq!(not_found.status(), Some(404));
        assert!(not_found.is_not_found());

        let rate = ElderError::RateLimited("slow down".into());
        assert_eq!(rate.status(), Some(429));
        assert!(rate.is_rate_limited());

        let api = ElderError::Api {
            status: 422,
            code: Some("VALIDATION_ERROR".into()),
            message: "bad field".into(),
            body: None,
        };
        assert_eq!(api.status(), Some(422));
        assert!(api.to_string().contains("[VALIDATION_ERROR]"));
    }
}
