//! Error types shared across Elder data-plane Rust services.

use thiserror::Error;

/// Unified error type returned by `elder-svc-common`.
#[derive(Debug, Error)]
pub enum CommonError {
    /// Missing or invalid configuration / environment variable.
    #[error("configuration error: {0}")]
    Config(String),

    /// Authentication or JWT minting/validation failure.
    #[error("authentication error: {0}")]
    Auth(String),

    /// Non-success HTTP response returned by Elder API.
    #[error("HTTP {status} from Elder API: {body}")]
    Http {
        /// HTTP status code.
        status: u16,
        /// Truncated response body.
        body: String,
    },

    /// Transport-level failure talking to Elder API.
    #[error("HTTP transport error: {0}")]
    Transport(#[from] reqwest::Error),

    /// Invalid input or schema validation error.
    #[error("validation error: {0}")]
    Validation(String),

    /// Subprocess or runtime execution failure.
    #[error("execution error: {0}")]
    Execution(String),

    /// JSON serialization or deserialization error.
    #[error("JSON serialization error: {0}")]
    Json(#[from] serde_json::Error),
}

/// Convenience result alias for `elder-svc-common`.
pub type Result<T> = std::result::Result<T, CommonError>;
