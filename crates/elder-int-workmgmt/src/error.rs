//! Unified error types for `elder-int-workmgmt`.

use thiserror::Error;

/// Error returned by work management sync and endpoint discovery connectors.
#[derive(Debug, Error)]
pub enum WorkMgmtError {
    /// Missing or malformed integration configuration.
    #[error("configuration error: {0}")]
    Config(String),

    /// Authentication, token minting, or credential failure.
    #[error("authentication error: {0}")]
    Auth(String),

    /// Non-success HTTP response from an external platform API.
    #[error("{platform} API returned HTTP {status}: {body}")]
    ApiHttp {
        /// External platform identifier (e.g., `"github"`, `"fleetdm"`).
        platform: &'static str,
        /// HTTP status code.
        status: u16,
        /// Truncated response body.
        body: String,
    },

    /// Transport-level failure contacting an external platform API.
    #[error("HTTP transport error contacting {platform}: {source}")]
    Transport {
        /// External platform identifier.
        platform: &'static str,
        /// Underlying `reqwest` error.
        #[source]
        source: reqwest::Error,
    },

    /// JSON serialization or deserialization error.
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    /// Invalid URL encountered while building a request.
    #[error("invalid URL: {0}")]
    Url(#[from] url::ParseError),

    /// Failure communicating with Elder internal API endpoints.
    #[error("Elder API error: {0}")]
    ElderApi(#[from] elder_svc_common::CommonError),
}

/// Convenience result alias for `elder-int-workmgmt`.
pub type Result<T> = std::result::Result<T, WorkMgmtError>;
