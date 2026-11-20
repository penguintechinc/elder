//! Work-management sync (`github`, `jira`, `gitlab`, `trello`, `openproject`)
//! and endpoint/security discovery (`fleetdm`, `iboss`) connectors for Elder.

mod discovery_common;
pub mod error;
pub mod fleetdm;
pub mod github;
pub mod gitlab;
mod http_client;
pub mod iboss;
pub mod jira;
pub mod openproject;
mod sync_common;
pub mod trello;

use elder_svc_common::ElderApiClient;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tracing::Instrument;

use crate::discovery_common::{run_discovery_lifecycle, DiscoveryJobContext};
use crate::error::WorkMgmtError;
use crate::sync_common::{finalize_sync_run, SyncJobContext};

/// Unified execution outcome returned by `run_sync_config` and `run_endpoint_discovery`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunOutcome {
    /// Count of successfully synced or discovered items.
    pub items_synced: usize,
    /// Count of failed operations.
    pub items_failed: usize,
    /// Normalized run status (`"completed"`, `"partial"`, or `"failed"`).
    pub status: String,
    /// Error summary when failures occurred.
    pub error: Option<String>,
}

/// Dispatch and execute a work-management sync configuration, reporting results to
/// `POST /api/v1/integrations/internal/sync/{config_id}/apply`.
pub async fn run_sync_config(elder: &ElderApiClient, config: &Value) -> RunOutcome {
    let ctx = match SyncJobContext::from_value(config) {
        Ok(c) => c,
        Err(err) => {
            let msg = err.to_string();
            tracing::error!(error = %msg, "Invalid work-management sync configuration");
            return RunOutcome {
                items_synced: 0,
                items_failed: 1,
                status: "failed".to_string(),
                error: Some(msg),
            };
        }
    };

    let span = tracing::info_span!(
        "elder_int_workmgmt.sync",
        platform = %ctx.platform,
        config_id = ctx.config_id,
        correlation_id = %ctx.correlation_id
    );

    async {
        let outcome_res = match ctx.platform.as_str() {
            "github" => github::sync(&ctx).await,
            "jira" => jira::sync(&ctx).await,
            "gitlab" => gitlab::sync(&ctx).await,
            "trello" => trello::sync(&ctx).await,
            "openproject" => openproject::sync(&ctx).await,
            other => Err(WorkMgmtError::Config(format!(
                "unsupported work-management sync platform {other:?}"
            ))),
        };

        if let Err(ref err) = outcome_res {
            tracing::error!(
                platform = %ctx.platform,
                config_id = ctx.config_id,
                error = %err,
                "Work-management sync execution failed"
            );
        }

        finalize_sync_run(elder, &ctx, outcome_res).await
    }
    .instrument(span)
    .await
}

/// Dispatch and execute an endpoint/security discovery job (`fleetdm`, `iboss`),
/// reporting lifecycle transitions to `/start` and `/complete`.
pub async fn run_endpoint_discovery(elder: &ElderApiClient, job: &Value) -> RunOutcome {
    let ctx = match DiscoveryJobContext::from_value(job) {
        Ok(c) => c,
        Err(err) => {
            let msg = err.to_string();
            tracing::error!(error = %msg, "Invalid endpoint discovery job payload");
            return RunOutcome {
                items_synced: 0,
                items_failed: 1,
                status: "failed".to_string(),
                error: Some(msg),
            };
        }
    };

    let span = tracing::info_span!(
        "elder_int_workmgmt.discovery",
        provider = %ctx.provider,
        job_id = ctx.job_id
    );

    run_discovery_lifecycle(elder, &ctx, || async {
        match ctx.provider.as_str() {
            "fleetdm" => fleetdm::discover(&ctx).await,
            "iboss" => iboss::discover(&ctx).await,
            other => Err(WorkMgmtError::Config(format!(
                "unsupported endpoint discovery provider {other:?}"
            ))),
        }
    })
    .instrument(span)
    .await
}
