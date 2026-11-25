//! Unified CLI error types and process exit code mapping.
//!
//! Exit codes:
//! - `0`: Success
//! - `1`: General, API, HTTP transport, or I/O error
//! - `2`: CLI usage, argument, validation, or configuration error
//! - `3`: Authentication or authorization error (`401` / `403`)

use std::process::ExitCode;

use elder_sdk::ElderError;
use thiserror::Error;

/// Exit code for successful execution.
pub const EXIT_OK: u8 = 0;
/// Exit code for general, API, transport, or runtime errors.
pub const EXIT_GENERAL: u8 = 1;
/// Exit code for CLI usage, validation, or configuration errors.
pub const EXIT_USAGE_OR_CONFIG: u8 = 2;
/// Exit code for authentication or authorization failures (`401` / `403`).
pub const EXIT_AUTH: u8 = 3;

/// Unified error type for `elder-cli`.
#[derive(Debug, Error)]
pub enum CliError {
    /// Invalid CLI arguments, flags, or request input.
    #[error("usage error: {0}")]
    Usage(String),

    /// Invalid or unreadable CLI configuration file.
    #[error("configuration error: {0}")]
    Config(String),

    /// Authentication or authorization failure (`401`, `403`, or missing credentials).
    #[error("authentication error: {0}")]
    Auth(String),

    /// Platform secure credential store failure.
    #[error("credential store error: {0}")]
    Keyring(String),

    /// Error returned by [`elder_sdk::ElderClient`] or [`elder_sdk::Catalog`].
    #[error(transparent)]
    Sdk(#[from] ElderError),

    /// Output serialization or formatting failure.
    #[error("output formatting error: {0}")]
    Format(String),

    /// Filesystem or stdin/stdout I/O error.
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
}

impl CliError {
    /// Map this error to the documented numeric CLI exit code.
    ///
    /// - `3`: Auth (`401` / `403`)
    /// - `2`: CLI usage, validation, or configuration errors
    /// - `1`: General / API / transport / I/O errors
    pub fn exit_code_u8(&self) -> u8 {
        match self {
            Self::Auth(_) => EXIT_AUTH,
            Self::Usage(_) | Self::Config(_) => EXIT_USAGE_OR_CONFIG,
            Self::Sdk(err) => match err {
                ElderError::Auth { .. } => EXIT_AUTH,
                ElderError::Api { status, .. } if *status == 401 || *status == 403 => EXIT_AUTH,
                ElderError::Config(_) | ElderError::Validation(_) | ElderError::Catalog(_) => {
                    EXIT_USAGE_OR_CONFIG
                }
                _ => EXIT_GENERAL,
            },
            Self::Keyring(_) | Self::Format(_) | Self::Io(_) => EXIT_GENERAL,
        }
    }

    /// Convert this error into a [`std::process::ExitCode`].
    pub fn exit_code(&self) -> ExitCode {
        ExitCode::from(self.exit_code_u8())
    }
}

/// Convenience result alias for `elder-cli`.
pub type Result<T> = std::result::Result<T, CliError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exit_code_mapping_matches_specification() {
        assert_eq!(CliError::Usage("bad arg".into()).exit_code_u8(), 2);
        assert_eq!(CliError::Config("bad url".into()).exit_code_u8(), 2);
        assert_eq!(CliError::Auth("denied".into()).exit_code_u8(), 3);
        assert_eq!(
            CliError::Sdk(ElderError::Auth {
                status: 401,
                message: "unauthorized".into(),
            })
            .exit_code_u8(),
            3
        );
        assert_eq!(
            CliError::Sdk(ElderError::Api {
                status: 403,
                code: None,
                message: "forbidden".into(),
                body: None,
            })
            .exit_code_u8(),
            3
        );
        assert_eq!(
            CliError::Sdk(ElderError::Api {
                status: 500,
                code: None,
                message: "boom".into(),
                body: None,
            })
            .exit_code_u8(),
            1
        );
        assert_eq!(
            CliError::Sdk(ElderError::NotFound("missing".into())).exit_code_u8(),
            1
        );
        assert_eq!(
            CliError::Sdk(ElderError::Validation("empty".into())).exit_code_u8(),
            2
        );
        assert_eq!(
            CliError::Sdk(ElderError::Catalog("unknown".into())).exit_code_u8(),
            2
        );
    }
}
