//! `svc-scanner`: stateless async Elder data-plane security scanner binary.

mod config;
mod nvd;
mod parsers;
mod scanners;

use std::sync::Arc;
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::sync::Semaphore;
use tokio::task::JoinSet;
use tracing::Instrument;

use elder_svc_common::{telemetry, CommonError, ElderApiClient, Result, ServiceMetrics};

use crate::config::ScanningConfig;
use crate::nvd::maybe_trigger_nvd_sync;
use crate::scanners::banner::run_banner_scan;
use crate::scanners::http_screenshot::run_http_screenshot_scan;
use crate::scanners::network::run_network_scan;
use crate::scanners::sbom::{
    execute_sbom_scan, process_due_schedules, report_sbom_result, PendingSbomScan, SbomScanOutcome,
};

#[derive(Debug, Parser)]
#[command(
    name = "svc-scanner",
    version,
    about = "Elder stateless data-plane scanner worker"
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Run pending discovery, SBOM, schedule, and NVD sync work (default: once).
    Run {
        /// Execute a single scan cycle and exit (CronJob mode).
        #[arg(long, conflicts_with = "loop_mode")]
        once: bool,

        /// Continuously poll on an interval until SIGTERM/SIGINT.
        #[arg(long = "loop")]
        loop_mode: bool,

        /// Poll interval in seconds for `--loop` mode (defaults to SCANNER_INTERVAL_SECONDS or 60).
        #[arg(long)]
        interval: Option<u64>,
    },

    /// Validate configuration and JWT credential generation without touching network.
    Healthcheck,
}

#[derive(Debug, Deserialize)]
struct PendingJobsResponse {
    #[serde(default)]
    jobs: Vec<DiscoveryJob>,
}

#[derive(Debug, Clone, Deserialize)]
struct DiscoveryJob {
    id: i64,
    provider: String,
    #[serde(default)]
    config_json: Value,
}

#[derive(Debug, Serialize)]
struct CompleteDiscoveryJobRequest {
    success: bool,
    results: Value,
    error_message: Option<String>,
}

#[tokio::main]
async fn main() -> std::process::ExitCode {
    let cli = Cli::parse();

    let telemetry_cfg = match telemetry::TelemetryConfig::from_env("svc-scanner") {
        Ok(cfg) => cfg,
        Err(err) => {
            let fallback = telemetry::TelemetryConfig {
                service_name: "svc-scanner".to_string(),
                otlp_endpoint: None,
                protocol: telemetry::OtlpProtocol::HttpProtobuf,
                json_logs: true,
            };
            let guard = telemetry::init(&fallback);
            tracing::error!(error = %err, "Invalid telemetry configuration");
            guard.shutdown().await;
            return std::process::ExitCode::FAILURE;
        }
    };
    let guard = telemetry::init(&telemetry_cfg);

    let exit_code = match run_cli(cli).await {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(err) => {
            tracing::error!(error = %err, "svc-scanner exited with failure");
            std::process::ExitCode::FAILURE
        }
    };

    guard.shutdown().await;
    exit_code
}

async fn run_cli(cli: Cli) -> Result<()> {
    match cli.command {
        Some(Command::Healthcheck) => run_healthcheck(),
        Some(Command::Run {
            loop_mode,
            interval,
            ..
        }) => execute_runner(loop_mode, interval).await,
        None => execute_runner(false, None).await,
    }
}

fn run_healthcheck() -> Result<()> {
    let cfg = ScanningConfig::from_env()?;
    let auth = cfg.build_auth_provider()?;
    auth.validate()?;
    let _client = ElderApiClient::new(&cfg.elder_api_url, auth)?;
    tracing::info!(
        elder_api_url = %cfg.elder_api_url,
        "svc-scanner healthcheck passed"
    );
    Ok(())
}

async fn execute_runner(loop_mode: bool, interval_override: Option<u64>) -> Result<()> {
    let cfg = Arc::new(ScanningConfig::from_env()?);
    let auth = cfg.build_auth_provider()?;
    auth.validate()?;

    let client = Arc::new(ElderApiClient::new(&cfg.elder_api_url, auth)?);
    let metrics = Arc::new(ServiceMetrics::new("svc-scanner", "svc_scanner"));
    let poll_interval =
        Duration::from_secs(interval_override.unwrap_or(cfg.default_loop_interval_secs));
    let mut last_nvd_sync: Option<DateTime<Utc>> = None;

    if !loop_mode {
        run_cycle(cfg, client, metrics, &mut last_nvd_sync).await?;
        return Ok(());
    }

    tracing::info!(
        interval_secs = poll_interval.as_secs(),
        "Starting svc-scanner polling loop"
    );

    loop {
        if let Err(err) = run_cycle(
            Arc::clone(&cfg),
            Arc::clone(&client),
            Arc::clone(&metrics),
            &mut last_nvd_sync,
        )
        .await
        {
            tracing::error!(error = %err, "Scanning cycle encountered an error");
        }

        tokio::select! {
            _ = shutdown_signal() => {
                tracing::info!("Shutdown signal received; exiting polling loop cleanly");
                break;
            }
            _ = tokio::time::sleep(poll_interval) => {}
        }
    }

    Ok(())
}

async fn run_cycle(
    cfg: Arc<ScanningConfig>,
    client: Arc<ElderApiClient>,
    metrics: Arc<ServiceMetrics>,
    last_nvd_sync: &mut Option<DateTime<Utc>>,
) -> Result<()> {
    let cycle_start = Instant::now();
    let cycle_span = tracing::info_span!("svc_scanner.cycle");

    let res = async {
        // 1. Evaluate due SBOM schedules first so newly created pending scans are picked up in the same pass.
        if let Err(err) = process_due_schedules(&client).await {
            tracing::error!(error = %err, "Failed processing due SBOM schedules");
        }

        // 2. Fetch pending discovery + SBOM jobs.
        let pending_discovery = match client
            .get_json::<PendingJobsResponse>("/api/v1/discovery/jobs/pending")
            .await
        {
            Ok(resp) => resp.jobs,
            Err(err) => {
                tracing::error!(error = %err, "Failed fetching pending discovery jobs");
                Vec::new()
            }
        };

        let pending_sbom = match client
            .get_json::<Vec<PendingSbomScan>>("/api/v1/sbom/scans/pending")
            .await
        {
            Ok(scans) => scans,
            Err(err) => {
                tracing::error!(error = %err, "Failed fetching pending SBOM scans");
                Vec::new()
            }
        };

        // 3. Execute all jobs concurrently with per-job fault isolation & bounded concurrency,
        //    awaiting every spawned in-flight task cleanly even if SIGTERM/SIGINT arrives.
        let sem = Arc::new(Semaphore::new(cfg.max_concurrent_jobs));
        let mut tasks = JoinSet::new();
        let job_timeout = Duration::from_secs(cfg.job_timeout_secs);

        for job in pending_discovery {
            let sem_c = Arc::clone(&sem);
            let cfg_c = Arc::clone(&cfg);
            let client_c = Arc::clone(&client);
            let metrics_c = Arc::clone(&metrics);
            tasks.spawn(async move {
                let _permit = sem_c.acquire_owned().await.ok();
                run_discovery_job(&job, &cfg_c, &client_c, &metrics_c, job_timeout).await;
            });
        }

        for scan in pending_sbom {
            let sem_c = Arc::clone(&sem);
            let cfg_c = Arc::clone(&cfg);
            let client_c = Arc::clone(&client);
            let metrics_c = Arc::clone(&metrics);
            tasks.spawn(async move {
                let _permit = sem_c.acquire_owned().await.ok();
                run_sbom_job(&scan, &cfg_c, &client_c, &metrics_c, job_timeout).await;
            });
        }

        if !tasks.is_empty() {
            let drain_fut = async {
                while let Some(joined) = tasks.join_next().await {
                    if let Err(err) = joined {
                        tracing::error!(error = %err, "Spawned scanner task panicked");
                    }
                }
            };

            tokio::select! {
                _ = drain_fut => {}
                _ = shutdown_signal() => {
                    tracing::warn!("Shutdown signal received while jobs are in flight; waiting for completion");
                    while let Some(joined) = tasks.join_next().await {
                        if let Err(err) = joined {
                            tracing::error!(error = %err, "Spawned scanner task panicked during shutdown drain");
                        }
                    }
                }
            }
        }

        // 4. Optional NVD enrichment trigger (stateless check via /nvd-sync/status).
        if cfg.nvd_sync_enabled {
            if let Err(err) =
                maybe_trigger_nvd_sync(&client, last_nvd_sync, cfg.nvd_sync_interval_secs).await
            {
                tracing::error!(error = %err, "Failed checking/triggering NVD sync");
            }
        }

        Ok::<(), CommonError>(())
    }
    .instrument(cycle_span)
    .await;

    metrics.record_run(res.is_ok(), cycle_start.elapsed().as_secs_f64());
    res
}

async fn run_discovery_job(
    job: &DiscoveryJob,
    cfg: &ScanningConfig,
    client: &ElderApiClient,
    metrics: &ServiceMetrics,
    job_timeout: Duration,
) {
    let start = Instant::now();
    let span = tracing::info_span!(
        "svc_scanner.discovery_job",
        job_id = job.id,
        provider = %job.provider
    );
    async {
        if let Err(err) = client
            .post_empty::<Value>(&format!("/api/v1/discovery/jobs/{}/start", job.id))
            .await
        {
            tracing::error!(job_id = job.id, error = %err, "Failed marking discovery job running");
            metrics.record_job(&job.provider, false, start.elapsed().as_secs_f64());
            return;
        }

        let exec_res = tokio::time::timeout(job_timeout, async {
            match job.provider.as_str() {
                "network" => {
                    run_network_scan(&job.config_json, cfg.masscan_rate, &cfg.masscan_bin).await
                }
                "banner" => run_banner_scan(&job.config_json).await,
                "http_screenshot" => {
                    run_http_screenshot_scan(
                        &job.config_json,
                        &cfg.screenshot_dir,
                        &cfg.chromium_bin,
                    )
                    .await
                }
                other => Err(CommonError::Validation(format!(
                    "unsupported discovery provider {other:?}"
                ))),
            }
        })
        .await;

        let completion = match exec_res {
            Ok(Ok(results)) => CompleteDiscoveryJobRequest {
                success: true,
                results,
                error_message: None,
            },
            Ok(Err(err)) => CompleteDiscoveryJobRequest {
                success: false,
                results: json!({}),
                error_message: Some(err.to_string()),
            },
            Err(_) => CompleteDiscoveryJobRequest {
                success: false,
                results: json!({}),
                error_message: Some(format!(
                    "job timed out after {} seconds",
                    job_timeout.as_secs()
                )),
            },
        };

        let succeeded = completion.success;
        if let Err(err) = client
            .post_json::<_, Value>(
                &format!("/api/v1/discovery/jobs/{}/complete", job.id),
                &completion,
            )
            .await
        {
            tracing::error!(job_id = job.id, error = %err, "Failed submitting discovery job completion");
        }
        metrics.record_job(&job.provider, succeeded, start.elapsed().as_secs_f64());
    }
    .instrument(span)
    .await;
}

async fn run_sbom_job(
    scan: &PendingSbomScan,
    cfg: &ScanningConfig,
    client: &ElderApiClient,
    metrics: &ServiceMetrics,
    job_timeout: Duration,
) {
    let start = Instant::now();
    let span = tracing::info_span!(
        "svc_scanner.sbom_scan",
        scan_id = scan.id,
        scan_type = %scan.scan_type
    );
    async {
        if let Err(err) = client
            .post_empty::<Value>(&format!("/api/v1/sbom/scans/{}/start", scan.id))
            .await
        {
            tracing::error!(scan_id = scan.id, error = %err, "Failed marking SBOM scan running");
            metrics.record_job("sbom", false, start.elapsed().as_secs_f64());
            return;
        }

        let exec_res =
            tokio::time::timeout(job_timeout, execute_sbom_scan(scan, &cfg.git_bin)).await;
        let duration_ms = i64::try_from(start.elapsed().as_millis()).unwrap_or(i64::MAX);

        let (success, components, files_scanned, commit_hash, endpoints, err_msg) = match exec_res {
            Ok(Ok((comps, eps, files, hash))) => (true, comps, files, hash, eps, None),
            Ok(Err(err)) => (
                false,
                Vec::new(),
                Vec::new(),
                None,
                Vec::new(),
                Some(err.to_string()),
            ),
            Err(_) => (
                false,
                Vec::new(),
                Vec::new(),
                None,
                Vec::new(),
                Some(format!(
                    "SBOM scan timed out after {} seconds",
                    job_timeout.as_secs()
                )),
            ),
        };

        let outcome = SbomScanOutcome {
            success,
            components: &components,
            files_scanned: &files_scanned,
            commit_hash,
            error_message: err_msg,
            scan_duration_ms: duration_ms,
            endpoints: &endpoints,
        };
        if let Err(err) = report_sbom_result(client, scan.id, &outcome).await {
            tracing::error!(scan_id = scan.id, error = %err, "Failed submitting SBOM scan results");
        }
        metrics.record_job("sbom", success, start.elapsed().as_secs_f64());
    }
    .instrument(span)
    .await;
}

async fn shutdown_signal() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        let mut term = signal(SignalKind::terminate()).ok();
        let ctrl_c = tokio::signal::ctrl_c();
        match term.as_mut() {
            Some(sigterm) => {
                tokio::select! {
                    _ = sigterm.recv() => {}
                    _ = ctrl_c => {}
                }
            }
            None => {
                let _ = ctrl_c.await;
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
