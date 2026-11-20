//! Shared infrastructure for Elder Rust data-plane services (`svc-scanner`, `svc-integrations`).

pub mod auth;
pub mod elder_client;
pub mod error;
pub mod telemetry;

pub use auth::{ServiceAuthConfig, ServiceTokenProvider, MAX_SERVICE_WORKER_TTL_SECONDS};
pub use elder_client::ElderApiClient;
pub use error::{CommonError, Result};
pub use telemetry::{OtlpProtocol, ServiceMetrics, TelemetryConfig, TelemetryGuard};
