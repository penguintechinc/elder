//! Official pure-`rustls` Rust SDK for Elder `hub-api`.
//!
//! `elder-sdk` provides a thin, async HTTP client over all Elder `hub-api`
//! endpoints alongside an embedded, collision-free operation catalog
//! (`catalog/api-catalog.json`) shared with the `elder` CLI.
//!
//! # Key Guarantees
//!
//! - **Pure `rustls` TLS**: uses `reqwest` with `default-features = false`
//!   and `features = ["json", "rustls-tls", "gzip"]` (zero OpenSSL or native-tls).
//! - **Masked credentials**: `Auth`, `ClientConfig`, `AuthTokens`, and
//!   `ElderClient` redact tokens, passwords, and API keys from `Debug` output
//!   and structured `tracing` logs.
//! - **Targeted retries**: retries with exponential backoff strictly on
//!   `429`, `502`, `503`, `504`, and transient connection/timeout failures;
//!   never retries `400`, `401`, `403`, `404`, or `422`.
//! - **Normalized catalog lookup**: [`Catalog::find`] and [`Catalog::operations`]
//!   accept both canonical `kebab-case` (`"access-reviews"`, `"list-issues"`)
//!   and `snake_case` (`"access_reviews"`, `"list_issues"`).

pub mod catalog;
pub mod client;
pub mod config;
pub mod error;
pub mod resources;
pub mod runs;
pub mod workers;

pub use catalog::{normalize_name, ApiOperation, Catalog, HttpMethod};
pub use client::{AuthTokens, ElderClient};
pub use config::{Auth, ClientConfig, RetryConfig};
pub use error::{ElderError, Result};
pub use resources::ResourceClient;
pub use runs::{
    CreateRunFunctionRequest, InvokeRunRequest, InvokeRunResponse, ListExecutionsParams,
    ListRunFunctionsParams, RunExecution, RunFunction, RunPage, RunsClient,
    UpdateRunFunctionRequest,
};
pub use workers::{
    ListRemoteWorkersParams, OnboardRemoteWorkerRequest, OnboardRemoteWorkerResponse, RemoteWorker,
    RemoteWorkerListResponse, RotateTokenResponse, UpdateRemoteWorkerRequest,
    WorkerHeartbeatRequest, WorkersClient,
};
