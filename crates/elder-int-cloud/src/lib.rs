//! Stateless pure-`rustls` cloud discovery and IAM sync providers for Elder.
//!
//! Covers 7 infrastructure/cloud discovery providers (`aws`, `gcp`, `azure`,
//! `kubernetes`, `vultr`, `lxd`, `vcenter`) and 3 IAM sync providers (`aws_iam`,
//! `gcp_iam`, `kubernetes`), reporting normalized resources, dependencies, and
//! identities back to the Elder API internal endpoints.

mod common;

pub mod aws;
pub mod azure;
pub mod gcp;
pub mod k8s;
pub mod lxd;
pub mod sigv4;
pub mod vcenter;
pub mod vultr;

use elder_svc_common::ElderApiClient;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::common::{
    extract_config, extract_id, opt_str, CloudError, DiscoveryCollection, IamCollection,
    NormalizedDependency, NormalizedResource,
};

/// Result summary returned by [`run_cloud_discovery`] and [`run_cloud_iam`].
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunOutcome {
    pub items_synced: usize,
    pub items_failed: usize,
    pub status: String,
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
struct CompleteDiscoveryRequest<'a> {
    history_id: &'a Value,
    status: &'a str,
    organization_id: Option<i64>,
    resources: &'a [NormalizedResource],
    dependencies: &'a [NormalizedDependency],
    error_message: Option<&'a str>,
    summary: &'a Value,
}

#[derive(Debug, Serialize)]
struct ApplyIamRequest<'a> {
    success: bool,
    error_message: Option<&'a str>,
    users: &'a [Value],
    groups: &'a [Value],
    roles: &'a [Value],
    memberships: &'a [Value],
    summary: &'a Value,
}

/// Execute a cloud/infrastructure discovery job and report start + completion to Elder API.
pub async fn run_cloud_discovery(elder: &ElderApiClient, job: &Value) -> RunOutcome {
    let Some(job_id) = extract_id(job, &["id", "job_id"]) else {
        return RunOutcome {
            items_synced: 0,
            items_failed: 1,
            status: "failed".to_string(),
            error: Some("discovery job missing numeric id".to_string()),
        };
    };

    let cfg = extract_config(job);
    let organization_id = extract_id(job, &["organization_id"]).or_else(|| {
        extract_id(
            &Value::Object(cfg.clone()),
            &["_organization_id", "organization_id"],
        )
    });

    let raw_provider = job
        .get("provider")
        .and_then(Value::as_str)
        .map(|s| s.trim().to_ascii_lowercase())
        .or_else(|| opt_str(&cfg, &["provider_type", "provider"]).map(|s| s.to_ascii_lowercase()))
        .unwrap_or_default();
    let provider = match raw_provider.as_str() {
        "k8s" => "kubernetes".to_string(),
        other => other.to_string(),
    };

    let start_path = format!("/api/v1/integrations/internal/discovery/{job_id}/start");
    let start_resp: Value = match elder.post_empty(&start_path).await {
        Ok(val) => val,
        Err(err) => {
            tracing::error!(
                job_id,
                provider = %provider,
                error = %err,
                "Failed to start cloud discovery job"
            );
            return RunOutcome {
                items_synced: 0,
                items_failed: 1,
                status: "failed".to_string(),
                error: Some(format!("failed starting discovery job: {err}")),
            };
        }
    };

    let history_id = start_resp
        .get("history_id")
        .or_else(|| start_resp.get("id"))
        .or_else(|| start_resp.get("data").and_then(|d| d.get("history_id")))
        .cloned()
        .unwrap_or(Value::Null);

    let run_res = match provider.as_str() {
        "aws" => aws::discover(&cfg).await,
        "gcp" => gcp::discover(&cfg).await,
        "azure" => azure::discover(&cfg).await,
        "kubernetes" => k8s::discover(&cfg).await,
        "vultr" => vultr::discover(&cfg).await,
        "lxd" => lxd::discover(&cfg).await,
        "vcenter" => vcenter::discover(&cfg).await,
        other => Err(CloudError::Validation(format!(
            "unsupported cloud discovery provider: {other:?}"
        ))),
    };

    let (collection, status, error_message, items_failed) = match run_res {
        Ok(col) => {
            if col.errors.is_empty() {
                (col, "completed".to_string(), None, 0)
            } else if col.succeeded_steps > 0 || !col.resources.is_empty() {
                let err_str = col.errors.join("; ");
                let failed_cnt = col.errors.len();
                (col, "partial".to_string(), Some(err_str), failed_cnt)
            } else {
                let err_str = col.errors.join("; ");
                let failed_cnt = col.errors.len().max(1);
                (col, "failed".to_string(), Some(err_str), failed_cnt)
            }
        }
        Err(err) => {
            let mut empty = DiscoveryCollection::default();
            empty.errors.push(err.to_string());
            (empty, "failed".to_string(), Some(err.to_string()), 1)
        }
    };

    let items_synced = collection.resources.len();
    let summary = collection.summary(&provider);
    let complete_path = format!("/api/v1/integrations/internal/discovery/{job_id}/complete");
    let complete_payload = CompleteDiscoveryRequest {
        history_id: &history_id,
        status: &status,
        organization_id,
        resources: &collection.resources,
        dependencies: &collection.dependencies,
        error_message: error_message.as_deref(),
        summary: &summary,
    };

    if let Err(err) = elder
        .post_json::<_, Value>(&complete_path, &complete_payload)
        .await
    {
        tracing::error!(
            job_id,
            provider = %provider,
            error = %err,
            "Failed submitting cloud discovery completion"
        );
        let combined_err = match error_message {
            Some(prev) => format!("{prev}; failed submitting completion: {err}"),
            None => format!("failed submitting completion: {err}"),
        };
        return RunOutcome {
            items_synced,
            items_failed: items_failed + 1,
            status: "failed".to_string(),
            error: Some(combined_err),
        };
    }

    tracing::info!(
        job_id,
        provider = %provider,
        status = %status,
        items_synced,
        items_failed,
        dependencies = collection.dependencies.len(),
        "Cloud discovery run completed"
    );

    RunOutcome {
        items_synced,
        items_failed,
        status,
        error: error_message,
    }
}

/// Execute a cloud or Kubernetes IAM sync and apply normalized results to Elder API.
pub async fn run_cloud_iam(elder: &ElderApiClient, provider: &Value) -> RunOutcome {
    let Some(provider_id) = extract_id(provider, &["id", "provider_id"]) else {
        return RunOutcome {
            items_synced: 0,
            items_failed: 1,
            status: "failed".to_string(),
            error: Some("IAM provider missing numeric id".to_string()),
        };
    };

    let cfg = extract_config(provider);
    let provider_type = provider
        .get("provider_type")
        .and_then(Value::as_str)
        .map(|s| s.trim().to_ascii_lowercase())
        .or_else(|| opt_str(&cfg, &["provider_type", "type"]).map(|s| s.to_ascii_lowercase()))
        .unwrap_or_default();
    let normalized_type = match provider_type.as_str() {
        "k8s" => "kubernetes".to_string(),
        other => other.to_string(),
    };

    let run_res = match normalized_type.as_str() {
        "aws_iam" => aws::sync_iam(&cfg).await,
        "gcp_iam" => gcp::sync_iam(&cfg).await,
        "kubernetes" => k8s::sync_iam(&cfg).await,
        other => Err(CloudError::Validation(format!(
            "unsupported cloud IAM provider_type: {other:?}"
        ))),
    };

    let (col, success, status, error_message, items_failed) = match run_res {
        Ok(c) => {
            if c.errors.is_empty() {
                (c, true, "completed".to_string(), None, 0)
            } else if c.succeeded_steps > 0 || c.total_items() > 0 {
                let err_str = c.errors.join("; ");
                let failed_cnt = c.errors.len();
                (c, true, "partial".to_string(), Some(err_str), failed_cnt)
            } else {
                let err_str = c.errors.join("; ");
                let failed_cnt = c.errors.len().max(1);
                (c, false, "failed".to_string(), Some(err_str), failed_cnt)
            }
        }
        Err(err) => {
            let mut empty = IamCollection::default();
            empty.errors.push(err.to_string());
            (empty, false, "failed".to_string(), Some(err.to_string()), 1)
        }
    };

    let items_synced = col.total_items();
    let summary = col.summary(&normalized_type);
    let apply_path = format!("/api/v1/integrations/internal/iam/{provider_id}/apply");
    let apply_payload = ApplyIamRequest {
        success,
        error_message: error_message.as_deref(),
        users: &col.users,
        groups: &col.groups,
        roles: &col.roles,
        memberships: &col.memberships,
        summary: &summary,
    };

    if let Err(err) = elder
        .post_json::<_, Value>(&apply_path, &apply_payload)
        .await
    {
        tracing::error!(
            provider_id,
            provider_type = %normalized_type,
            error = %err,
            "Failed submitting cloud IAM apply payload"
        );
        let combined_err = match error_message {
            Some(prev) => format!("{prev}; failed submitting IAM apply: {err}"),
            None => format!("failed submitting IAM apply: {err}"),
        };
        return RunOutcome {
            items_synced,
            items_failed: items_failed + 1,
            status: "failed".to_string(),
            error: Some(combined_err),
        };
    }

    let _ = json!(null);
    tracing::info!(
        provider_id,
        provider_type = %normalized_type,
        status = %status,
        items_synced,
        items_failed,
        "Cloud IAM sync completed"
    );

    RunOutcome {
        items_synced,
        items_failed,
        status,
        error: error_message,
    }
}
