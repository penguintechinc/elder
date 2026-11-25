//! Fault-isolated integration cycle orchestrator and OpenTelemetry instrumentation.

use std::future::Future;
use std::sync::Arc;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use elder_int_cloud::{run_cloud_discovery, run_cloud_iam};
use elder_int_identity::{
    run_google_workspace, run_identity_discovery, run_identity_provider, RunOutcome,
};
use elder_int_workmgmt::{run_endpoint_discovery, run_sync_config};
use elder_svc_common::{ElderApiClient, Result};
use opentelemetry::global;
use opentelemetry::metrics::{Counter, Histogram, Meter};
use opentelemetry::KeyValue;
use serde::Deserialize;
use serde_json::Value;
use tokio::sync::Semaphore;
use tokio::task::JoinSet;
use tracing::Instrument;

/// Response returned by `GET /api/v1/integrations/internal/pending`.
#[derive(Debug, Default, Clone, Deserialize)]
pub struct PendingIntegrationsResponse {
    /// Cloud / identity / endpoint discovery jobs currently due.
    #[serde(default)]
    pub discovery_jobs: Vec<Value>,
    /// Work-management / ticketing sync configurations currently due.
    #[serde(default)]
    pub sync_configs: Vec<Value>,
    /// Cloud / directory IAM providers currently due.
    #[serde(default)]
    pub iam_providers: Vec<Value>,
    /// Google Workspace providers currently due.
    #[serde(default)]
    pub google_workspace_providers: Vec<Value>,
}

/// Summary counters for a single integration orchestrator cycle.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct CycleSummary {
    /// Total connector tasks dispatched in this cycle.
    pub total_tasks: usize,
    /// Tasks that completed cleanly (`completed` or `partial`).
    pub succeeded: usize,
    /// Tasks that returned an error or failed status.
    pub failed: usize,
    /// Tasks terminated due to exceeding `job_timeout`.
    pub timed_out: usize,
    /// Tasks whose spawned worker panicked (`JoinError`).
    pub panicked: usize,
}

/// OTel counters and duration histogram emitted per orchestrator cycle.
#[derive(Clone)]
pub struct IntegrationsMetrics {
    runs_total: Counter<u64>,
    errors_total: Counter<u64>,
    duration_seconds: Histogram<f64>,
}

impl IntegrationsMetrics {
    /// Build metrics from the global OpenTelemetry meter (`svc-integrations`).
    pub fn global() -> Self {
        Self::from_meter(&global::meter("svc-integrations"))
    }

    /// Build metrics from a caller-provided OpenTelemetry [`Meter`] (used in tests).
    pub fn from_meter(meter: &Meter) -> Self {
        let runs_total = meter
            .u64_counter("svc_integrations.runs.total")
            .with_description("Total integration orchestrator cycles executed")
            .build();
        let errors_total = meter
            .u64_counter("svc_integrations.errors.total")
            .with_description("Total integration connector or orchestration errors")
            .build();
        let duration_seconds = meter
            .f64_histogram("svc_integrations.duration_seconds")
            .with_description("Duration of integration orchestrator cycles and tasks in seconds")
            .with_unit("s")
            .build();

        Self {
            runs_total,
            errors_total,
            duration_seconds,
        }
    }

    /// Record completion of an orchestrator cycle.
    pub fn record_run(&self, status: &str, duration_secs: f64) {
        let attrs = [KeyValue::new("status", status.to_string())];
        self.runs_total.add(1, &attrs);
        self.duration_seconds.record(duration_secs, &attrs);
    }

    /// Record an isolated task or cycle error.
    pub fn record_error(&self, category: &str, kind: &str) {
        let attrs = [
            KeyValue::new("category", category.to_string()),
            KeyValue::new("kind", kind.to_string()),
        ];
        self.errors_total.add(1, &attrs);
    }
}

/// Pluggable execution seam dispatching pending integration payloads to provider crates.
///
/// `DefaultIntegrationBackend` wires `elder-int-identity` (`google_workspace`, `okta`,
/// `authentik`, `ldap`) and fails loudly with an explicit `Err` for work-management and
/// cloud providers until assembly wires `elder-int-workmgmt` and `elder-int-cloud`.
#[async_trait]
pub trait IntegrationBackend: Send + Sync + 'static {
    /// Execute a `discovery_jobs` item.
    async fn run_discovery_job(
        &self,
        elder: &ElderApiClient,
        job: &Value,
    ) -> std::result::Result<RunOutcome, String>;

    /// Execute a `sync_configs` item.
    async fn run_sync_config(
        &self,
        elder: &ElderApiClient,
        config: &Value,
    ) -> std::result::Result<RunOutcome, String>;

    /// Execute an `iam_providers` item.
    async fn run_iam_provider(
        &self,
        elder: &ElderApiClient,
        provider: &Value,
    ) -> std::result::Result<RunOutcome, String>;

    /// Execute a `google_workspace_providers` item.
    async fn run_google_workspace(
        &self,
        elder: &ElderApiClient,
        provider: &Value,
    ) -> std::result::Result<RunOutcome, String>;
}

/// Default backend wiring all Elder integration crates (`elder-int-workmgmt`,
/// `elder-int-cloud`, and `elder-int-identity`).
#[derive(Debug, Default, Clone, Copy)]
pub struct DefaultIntegrationBackend;

fn map_workmgmt_outcome(out: elder_int_workmgmt::RunOutcome) -> RunOutcome {
    RunOutcome {
        items_synced: out.items_synced,
        items_failed: out.items_failed,
        status: out.status,
        error: out.error,
    }
}

fn map_cloud_outcome(out: elder_int_cloud::RunOutcome) -> RunOutcome {
    RunOutcome {
        items_synced: out.items_synced,
        items_failed: out.items_failed,
        status: out.status,
        error: out.error,
    }
}

#[async_trait]
impl IntegrationBackend for DefaultIntegrationBackend {
    async fn run_discovery_job(
        &self,
        elder: &ElderApiClient,
        job: &Value,
    ) -> std::result::Result<RunOutcome, String> {
        let provider = job
            .get("provider")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase();
        match provider.as_str() {
            "fleetdm" | "iboss" => Ok(map_workmgmt_outcome(
                run_endpoint_discovery(elder, job).await,
            )),
            "aws" | "gcp" | "azure" | "kubernetes" | "vultr" | "lxd" | "vcenter" => {
                Ok(map_cloud_outcome(run_cloud_discovery(elder, job).await))
            }
            "okta" | "authentik" | "ldap" => Ok(run_identity_discovery(elder, job).await),
            other => Err(format!(
                "unsupported discovery provider for svc-integrations: {other:?}"
            )),
        }
    }

    async fn run_sync_config(
        &self,
        elder: &ElderApiClient,
        config: &Value,
    ) -> std::result::Result<RunOutcome, String> {
        let platform = config
            .get("platform")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase();
        match platform.as_str() {
            "github" | "jira" | "gitlab" | "trello" | "openproject" => {
                Ok(map_workmgmt_outcome(run_sync_config(elder, config).await))
            }
            other => Err(format!(
                "unsupported work-management sync platform: {other:?}"
            )),
        }
    }

    async fn run_iam_provider(
        &self,
        elder: &ElderApiClient,
        provider: &Value,
    ) -> std::result::Result<RunOutcome, String> {
        let provider_type = provider
            .get("provider_type")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase();
        match provider_type.as_str() {
            "aws_iam" | "gcp_iam" | "kubernetes" => {
                Ok(map_cloud_outcome(run_cloud_iam(elder, provider).await))
            }
            "okta" | "authentik" | "ldap" => Ok(run_identity_provider(elder, provider).await),
            other => Err(format!(
                "unsupported IAM connector provider_type: {other:?}"
            )),
        }
    }

    async fn run_google_workspace(
        &self,
        elder: &ElderApiClient,
        provider: &Value,
    ) -> std::result::Result<RunOutcome, String> {
        Ok(run_google_workspace(elder, provider).await)
    }
}

#[derive(Debug, Clone, Copy)]
enum TaskKind {
    DiscoveryJob,
    SyncConfig,
    IamProvider,
    GoogleWorkspace,
}

impl TaskKind {
    fn category(self) -> &'static str {
        match self {
            Self::DiscoveryJob => "discovery",
            Self::SyncConfig => "sync",
            Self::IamProvider => "iam",
            Self::GoogleWorkspace => "google_workspace",
        }
    }
}

#[derive(Debug, Clone)]
struct QueuedTask {
    kind: TaskKind,
    item_id: i64,
    subtype: String,
    payload: Value,
}

enum TaskResolution {
    Succeeded,
    Failed,
    TimedOut,
}

/// Fetch pending integration work and run every item in fault-isolated `tokio::spawn` tasks.
pub async fn run_cycle<B, F>(
    elder: Arc<ElderApiClient>,
    backend: Arc<B>,
    metrics: Arc<IntegrationsMetrics>,
    max_concurrency: usize,
    job_timeout: Duration,
    shutdown: F,
) -> Result<CycleSummary>
where
    B: IntegrationBackend,
    F: Future<Output = ()>,
{
    let cycle_start = Instant::now();
    let cycle_span = tracing::info_span!("svc_integrations.cycle");

    async move {
        let pending = match elder
            .get_json::<PendingIntegrationsResponse>("/api/v1/integrations/internal/pending")
            .await
        {
            Ok(resp) => resp,
            Err(err) => {
                tracing::error!(error = %err, "Failed fetching pending integrations");
                metrics.record_error("pending", "api_fetch");
                metrics.record_run("error", cycle_start.elapsed().as_secs_f64());
                return Err(err);
            }
        };

        let tasks_to_run = flatten_pending(pending);
        let mut summary = CycleSummary {
            total_tasks: tasks_to_run.len(),
            ..Default::default()
        };

        if tasks_to_run.is_empty() {
            tracing::debug!("No pending integration work items due");
            metrics.record_run("ok", cycle_start.elapsed().as_secs_f64());
            return Ok(summary);
        }

        tracing::info!(
            total_tasks = summary.total_tasks,
            max_concurrency = max_concurrency,
            "Dispatching pending integration tasks"
        );

        let semaphore = Arc::new(Semaphore::new(max_concurrency.max(1)));
        let mut join_set = JoinSet::new();

        for task in tasks_to_run {
            let sem_c = Arc::clone(&semaphore);
            let elder_c = Arc::clone(&elder);
            let backend_c = Arc::clone(&backend);
            let metrics_c = Arc::clone(&metrics);
            join_set.spawn(async move {
                let _permit = sem_c.acquire_owned().await.ok();
                execute_isolated_task(task, elder_c, backend_c, metrics_c, job_timeout).await
            });
        }

        tokio::pin!(shutdown);

        loop {
            tokio::select! {
                biased;
                _ = &mut shutdown => {
                    tracing::warn!(
                        in_flight = join_set.len(),
                        "Shutdown signal received while integration tasks are in flight; draining"
                    );
                    while let Some(joined) = join_set.join_next().await {
                        record_task_join(joined, &mut summary, &metrics);
                    }
                    break;
                }
                joined_opt = join_set.join_next() => {
                    match joined_opt {
                        Some(joined) => record_task_join(joined, &mut summary, &metrics),
                        None => break,
                    }
                }
            }
        }

        let run_status = if summary.failed + summary.timed_out + summary.panicked > 0 {
            "partial"
        } else {
            "ok"
        };
        metrics.record_run(run_status, cycle_start.elapsed().as_secs_f64());

        tracing::info!(
            total_tasks = summary.total_tasks,
            succeeded = summary.succeeded,
            failed = summary.failed,
            timed_out = summary.timed_out,
            panicked = summary.panicked,
            "Completed integration orchestrator cycle"
        );
        Ok(summary)
    }
    .instrument(cycle_span)
    .await
}

fn record_task_join(
    joined: std::result::Result<TaskResolution, tokio::task::JoinError>,
    summary: &mut CycleSummary,
    metrics: &IntegrationsMetrics,
) {
    match joined {
        Ok(TaskResolution::Succeeded) => summary.succeeded += 1,
        Ok(TaskResolution::Failed) => summary.failed += 1,
        Ok(TaskResolution::TimedOut) => summary.timed_out += 1,
        Err(join_err) => {
            summary.panicked += 1;
            metrics.record_error("task", "panic");
            tracing::error!(
                is_panic = join_err.is_panic(),
                error = %join_err,
                "Integration connector task panicked; isolated from siblings"
            );
        }
    }
}

async fn execute_isolated_task<B: IntegrationBackend>(
    task: QueuedTask,
    elder: Arc<ElderApiClient>,
    backend: Arc<B>,
    metrics: Arc<IntegrationsMetrics>,
    job_timeout: Duration,
) -> TaskResolution {
    let category = task.kind.category();
    let span = tracing::info_span!(
        "svc_integrations.task",
        category = category,
        item_id = task.item_id,
        subtype = %task.subtype
    );

    async move {
        let fut = async {
            match task.kind {
                TaskKind::DiscoveryJob => backend.run_discovery_job(&elder, &task.payload).await,
                TaskKind::SyncConfig => backend.run_sync_config(&elder, &task.payload).await,
                TaskKind::IamProvider => backend.run_iam_provider(&elder, &task.payload).await,
                TaskKind::GoogleWorkspace => {
                    backend.run_google_workspace(&elder, &task.payload).await
                }
            }
        };

        match tokio::time::timeout(job_timeout, fut).await {
            Ok(Ok(outcome)) => {
                if outcome.is_success() {
                    if outcome.items_failed > 0 {
                        metrics.record_error(category, "partial_failure");
                    }
                    TaskResolution::Succeeded
                } else {
                    metrics.record_error(category, "connector_failed");
                    tracing::error!(
                        category = category,
                        item_id = task.item_id,
                        subtype = %task.subtype,
                        error = ?outcome.error,
                        "Integration task reported failure"
                    );
                    TaskResolution::Failed
                }
            }
            Ok(Err(err_msg)) => {
                metrics.record_error(category, "connector_error");
                tracing::error!(
                    category = category,
                    item_id = task.item_id,
                    subtype = %task.subtype,
                    error = %err_msg,
                    "Integration task returned error"
                );
                TaskResolution::Failed
            }
            Err(_) => {
                metrics.record_error(category, "timeout");
                tracing::error!(
                    category = category,
                    item_id = task.item_id,
                    subtype = %task.subtype,
                    timeout_secs = job_timeout.as_secs(),
                    "Integration task timed out"
                );
                TaskResolution::TimedOut
            }
        }
    }
    .instrument(span)
    .await
}

fn flatten_pending(pending: PendingIntegrationsResponse) -> Vec<QueuedTask> {
    let mut tasks = Vec::with_capacity(
        pending.discovery_jobs.len()
            + pending.sync_configs.len()
            + pending.iam_providers.len()
            + pending.google_workspace_providers.len(),
    );

    for job in pending.discovery_jobs {
        let item_id = job.get("id").and_then(Value::as_i64).unwrap_or(0);
        let subtype = job
            .get("provider")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        tasks.push(QueuedTask {
            kind: TaskKind::DiscoveryJob,
            item_id,
            subtype,
            payload: job,
        });
    }

    for sc in pending.sync_configs {
        let item_id = sc.get("id").and_then(Value::as_i64).unwrap_or(0);
        let subtype = sc
            .get("platform")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        tasks.push(QueuedTask {
            kind: TaskKind::SyncConfig,
            item_id,
            subtype,
            payload: sc,
        });
    }

    for iam in pending.iam_providers {
        let item_id = iam.get("id").and_then(Value::as_i64).unwrap_or(0);
        let subtype = iam
            .get("provider_type")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        tasks.push(QueuedTask {
            kind: TaskKind::IamProvider,
            item_id,
            subtype,
            payload: iam,
        });
    }

    for gws in pending.google_workspace_providers {
        let item_id = gws.get("id").and_then(Value::as_i64).unwrap_or(0);
        tasks.push(QueuedTask {
            kind: TaskKind::GoogleWorkspace,
            item_id,
            subtype: "google_workspace".to_string(),
            payload: gws,
        });
    }

    tasks
}

#[cfg(test)]
mod tests {
    use super::*;
    use elder_svc_common::ServiceTokenProvider;
    use opentelemetry::metrics::MeterProvider as _;
    use opentelemetry_sdk::metrics::data::AggregatedMetrics;
    use opentelemetry_sdk::metrics::{InMemoryMetricExporter, PeriodicReader, SdkMeterProvider};
    use serde_json::json;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    fn test_elder_client(base_url: &str) -> ElderApiClient {
        let auth = ServiceTokenProvider::from_env_lookup(
            "svc-integrations",
            vec!["integrations:sync".into()],
            |k| match k {
                "JWT_SECRET_KEY" => Some("x".repeat(32)),
                _ => None,
            },
        )
        .expect("auth");
        ElderApiClient::new(base_url, auth)
            .expect("client")
            .with_retry_config(0, Duration::from_millis(1))
    }

    async fn spawn_pending_server(payload: Value) -> (String, tokio::task::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        let body = payload.to_string();
        let handle = tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let body_c = body.clone();
                tokio::spawn(async move {
                    let mut buf = [0u8; 4096];
                    let _ = sock.read(&mut buf).await;
                    let resp = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body_c}",
                        body_c.len()
                    );
                    let _ = sock.write_all(resp.as_bytes()).await;
                    let _ = sock.shutdown().await;
                });
            }
        });
        (format!("http://{addr}"), handle)
    }

    struct FaultyMockBackend {
        sibling_completions: Arc<AtomicUsize>,
        slow_duration: Duration,
    }

    #[async_trait]
    impl IntegrationBackend for FaultyMockBackend {
        async fn run_discovery_job(
            &self,
            _elder: &ElderApiClient,
            job: &Value,
        ) -> std::result::Result<RunOutcome, String> {
            match job.get("provider").and_then(Value::as_str) {
                Some("panic_me") => panic!("simulated connector panic inside tokio::spawn"),
                Some("slow_me") => {
                    tokio::time::sleep(self.slow_duration).await;
                    Ok(RunOutcome::completed(1))
                }
                _ => {
                    self.sibling_completions.fetch_add(1, Ordering::SeqCst);
                    Ok(RunOutcome::completed(2))
                }
            }
        }

        async fn run_sync_config(
            &self,
            _elder: &ElderApiClient,
            _config: &Value,
        ) -> std::result::Result<RunOutcome, String> {
            Err("simulated connector error".to_string())
        }

        async fn run_iam_provider(
            &self,
            _elder: &ElderApiClient,
            _provider: &Value,
        ) -> std::result::Result<RunOutcome, String> {
            self.sibling_completions.fetch_add(1, Ordering::SeqCst);
            Ok(RunOutcome::completed(3))
        }

        async fn run_google_workspace(
            &self,
            _elder: &ElderApiClient,
            _provider: &Value,
        ) -> std::result::Result<RunOutcome, String> {
            self.sibling_completions.fetch_add(1, Ordering::SeqCst);
            Ok(RunOutcome::completed(4))
        }
    }

    #[tokio::test]
    async fn isolates_panics_errors_and_timeouts_while_siblings_complete_and_emits_otel_metrics() {
        let exporter = InMemoryMetricExporter::default();
        let reader = PeriodicReader::builder(exporter.clone()).build();
        let provider = SdkMeterProvider::builder().with_reader(reader).build();
        let meter = provider.meter("svc-integrations-test");
        let metrics = Arc::new(IntegrationsMetrics::from_meter(&meter));

        let pending_payload = json!({
            "discovery_jobs": [
                {"id": 1, "provider": "panic_me"},
                {"id": 2, "provider": "slow_me"},
                {"id": 3, "provider": "okta_sibling"}
            ],
            "sync_configs": [
                {"id": 10, "platform": "github"}
            ],
            "iam_providers": [
                {"id": 20, "provider_type": "okta"}
            ],
            "google_workspace_providers": [
                {"id": 30, "admin_email": "admin@example.com"}
            ]
        });

        let (base_url, server_handle) = spawn_pending_server(pending_payload).await;
        let elder = Arc::new(test_elder_client(&base_url));
        let sibling_completions = Arc::new(AtomicUsize::new(0));
        let backend = Arc::new(FaultyMockBackend {
            sibling_completions: Arc::clone(&sibling_completions),
            slow_duration: Duration::from_secs(2),
        });

        let summary = run_cycle(
            elder,
            backend,
            Arc::clone(&metrics),
            4,
            Duration::from_millis(80),
            std::future::pending::<()>(),
        )
        .await
        .expect("cycle itself completes despite isolated connector faults");

        server_handle.abort();

        assert_eq!(summary.total_tasks, 6);
        assert_eq!(summary.succeeded, 3);
        assert_eq!(summary.failed, 1);
        assert_eq!(summary.timed_out, 1);
        assert_eq!(summary.panicked, 1);
        assert_eq!(sibling_completions.load(Ordering::SeqCst), 3);

        provider.force_flush().expect("flush metrics");
        let resource_metrics = exporter.get_finished_metrics().expect("get metrics");

        let mut runs_sum: u64 = 0;
        let mut errors_sum: u64 = 0;
        let mut histogram_count: u64 = 0;

        for rm in &resource_metrics {
            for sm in rm.scope_metrics() {
                for m in sm.metrics() {
                    match m.name() {
                        "svc_integrations.runs.total" => {
                            if let AggregatedMetrics::U64(
                                opentelemetry_sdk::metrics::data::MetricData::Sum(sum),
                            ) = m.data()
                            {
                                runs_sum += sum.data_points().map(|dp| dp.value()).sum::<u64>();
                            }
                        }
                        "svc_integrations.errors.total" => {
                            if let AggregatedMetrics::U64(
                                opentelemetry_sdk::metrics::data::MetricData::Sum(sum),
                            ) = m.data()
                            {
                                errors_sum += sum.data_points().map(|dp| dp.value()).sum::<u64>();
                            }
                        }
                        "svc_integrations.duration_seconds" => {
                            if let AggregatedMetrics::F64(
                                opentelemetry_sdk::metrics::data::MetricData::Histogram(hist),
                            ) = m.data()
                            {
                                histogram_count +=
                                    hist.data_points().map(|dp| dp.count()).sum::<u64>();
                            }
                        }
                        _ => {}
                    }
                }
            }
        }

        assert_eq!(runs_sum, 1, "expected 1 run count");
        assert_eq!(
            errors_sum, 3,
            "expected 3 isolated error increments (1 err + 1 timeout + 1 panic)"
        );
        assert_eq!(
            histogram_count, 1,
            "expected 1 duration histogram observation"
        );

        let _ = provider.shutdown();
    }

    #[tokio::test]
    async fn default_backend_routes_all_providers_and_rejects_unknown_ones() {
        let elder = test_elder_client("http://127.0.0.1:9");
        let backend = DefaultIntegrationBackend;

        let unknown_sync = backend
            .run_sync_config(&elder, &json!({"id": 1, "platform": "unknown_tracker"}))
            .await;
        assert!(unknown_sync.is_err());

        let unknown_disc = backend
            .run_discovery_job(&elder, &json!({"id": 2, "provider": "unknown_cloud"}))
            .await;
        assert!(unknown_disc.is_err());

        let unknown_iam = backend
            .run_iam_provider(&elder, &json!({"id": 3, "provider_type": "unknown_iam"}))
            .await;
        assert!(unknown_iam.is_err());

        // Known providers dispatch into their respective connector crates and fail cleanly when API/config is absent.
        let jira_res = backend
            .run_sync_config(&elder, &json!({"id": 4, "platform": "jira"}))
            .await
            .expect("jira routed");
        assert_eq!(jira_res.status, "failed");

        let aws_disc = backend
            .run_discovery_job(&elder, &json!({"id": 5, "provider": "aws"}))
            .await
            .expect("aws discovery routed");
        assert_eq!(aws_disc.status, "failed");

        let aws_iam = backend
            .run_iam_provider(&elder, &json!({"id": 6, "provider_type": "aws_iam"}))
            .await
            .expect("aws_iam routed");
        assert_eq!(aws_iam.status, "failed");
    }

    #[tokio::test]
    async fn pending_fetch_failure_records_error_and_run_metrics_and_returns_err() {
        let exporter = InMemoryMetricExporter::default();
        let reader = PeriodicReader::builder(exporter.clone()).build();
        let provider = SdkMeterProvider::builder().with_reader(reader).build();
        let meter = provider.meter("svc-integrations-err-test");
        let metrics = Arc::new(IntegrationsMetrics::from_meter(&meter));

        // Bind then drop listener immediately so connection is refused fast.
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        drop(listener);

        let elder = Arc::new(test_elder_client(&format!("http://{addr}")));
        let backend = Arc::new(DefaultIntegrationBackend);

        let res = run_cycle(
            elder,
            backend,
            Arc::clone(&metrics),
            4,
            Duration::from_secs(5),
            std::future::pending::<()>(),
        )
        .await;
        assert!(res.is_err());

        provider.force_flush().expect("flush");
        let finished = exporter.get_finished_metrics().expect("metrics");
        assert!(!finished.is_empty());
        let _ = provider.shutdown();
    }
}
