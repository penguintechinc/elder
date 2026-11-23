//! `svc-scanner`: stateless async Elder data-plane security scanner binary.

mod config;
mod nvd;
mod parsers;
mod scanners;

use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::sync::{oneshot, Semaphore};
use tokio::task::JoinSet;
use tokio::time::MissedTickBehavior;
use tracing::Instrument;

use elder_svc_common::{telemetry, CommonError, ElderApiClient, Result, ServiceMetrics};

use crate::config::{clamp_poll_interval, ScanningConfig};
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

        /// Poll interval in seconds for `--loop` mode (defaults to SCANNER_POLL_INTERVAL_SECONDS, SCANNER_INTERVAL_SECONDS, or 300).
        #[arg(long)]
        interval: Option<u64>,
    },

    /// Validate configuration and JWT credential generation without touching network.
    Healthcheck {
        /// Also verify the poll-loop heartbeat file exists and is fresh.
        #[arg(long)]
        check_heartbeat: bool,
    },
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

#[derive(Debug, Serialize)]
struct JobProgressHeartbeat {
    status: &'static str,
    elapsed_seconds: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    cluster: Option<String>,
    timestamp: String,
}

/// Periodic best-effort progress heartbeat task that cancels cleanly on job completion.
struct ProgressReporter {
    stop_tx: Option<oneshot::Sender<()>>,
    handle: tokio::task::JoinHandle<()>,
}

impl ProgressReporter {
    fn spawn(
        client: Arc<ElderApiClient>,
        path: String,
        interval: Duration,
        cluster: Option<String>,
        started_at: Instant,
    ) -> Self {
        let (stop_tx, mut stop_rx) = oneshot::channel::<()>();
        let handle = tokio::spawn(async move {
            let mut ticker = tokio::time::interval(interval);
            ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);

            // Consume the immediate first tick so progress pushes start after one full interval.
            tokio::select! {
                biased;
                _ = &mut stop_rx => return,
                _ = ticker.tick() => {}
            }

            loop {
                tokio::select! {
                    biased;
                    _ = &mut stop_rx => break,
                    _ = ticker.tick() => {
                        let payload = JobProgressHeartbeat {
                            status: "running",
                            elapsed_seconds: started_at.elapsed().as_secs(),
                            cluster: cluster.clone(),
                            timestamp: Utc::now().to_rfc3339(),
                        };
                        tokio::select! {
                            biased;
                            _ = &mut stop_rx => break,
                            res = client.post_json::<_, Value>(&path, &payload) => {
                                if let Err(err) = res {
                                    match err {
                                        CommonError::Http { status: 404 | 405, .. } => {
                                            tracing::debug!(
                                                path = %path,
                                                error = %err,
                                                "Progress heartbeat endpoint not available; ignoring"
                                            );
                                        }
                                        other => {
                                            tracing::warn!(
                                                path = %path,
                                                error = %other,
                                                "Best-effort job progress heartbeat failed"
                                            );
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        });

        Self {
            stop_tx: Some(stop_tx),
            handle,
        }
    }

    async fn stop(mut self) {
        if let Some(tx) = self.stop_tx.take() {
            let _ = tx.send(());
        }
        let _ = (&mut self.handle).await;
    }
}

impl Drop for ProgressReporter {
    fn drop(&mut self) {
        if let Some(tx) = self.stop_tx.take() {
            let _ = tx.send(());
        }
        self.handle.abort();
    }
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
        Some(Command::Healthcheck { check_heartbeat }) => run_healthcheck(check_heartbeat),
        Some(Command::Run {
            loop_mode,
            interval,
            ..
        }) => execute_runner(loop_mode, interval).await,
        None => execute_runner(false, None).await,
    }
}

fn run_healthcheck(check_heartbeat: bool) -> Result<()> {
    let cfg = ScanningConfig::from_env()?;
    let auth = cfg.build_auth_provider()?;
    auth.validate()?;
    let _client = ElderApiClient::new(&cfg.elder_api_url, auth)?;
    if check_heartbeat {
        let max_age = max_heartbeat_age_secs(cfg.default_loop_interval_secs, cfg.job_timeout_secs);
        verify_heartbeat(&cfg.health_file, max_age, Utc::now().timestamp())?;
    }
    tracing::info!(
        elder_api_url = %cfg.elder_api_url,
        cluster = cfg.scanner_cluster.as_deref().unwrap_or("default"),
        check_heartbeat,
        "svc-scanner healthcheck passed"
    );
    Ok(())
}

fn write_heartbeat(path: &Path) {
    let ts = format!("{}\n", Utc::now().timestamp());
    if let Err(err) = std::fs::write(path, ts) {
        tracing::warn!(
            health_file = %path.display(),
            error = %err,
            "Failed writing scanner heartbeat file"
        );
    }
}

async fn send_worker_heartbeat(client: &ElderApiClient, cfg: &ScanningConfig) {
    let hostname = std::env::var("POD_NAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .unwrap_or_else(|_| "svc-scanner".to_string());
    let worker_id = std::env::var("SCANNER_WORKER_ID")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .unwrap_or_else(|| hostname.clone());
    let payload = json!({
        "worker_id": worker_id,
        "worker_type": "scanner",
        "hostname": hostname,
        "version": env!("CARGO_PKG_VERSION"),
        "cluster": cfg.scanner_cluster,
        "capabilities": ["network", "banner", "http_screenshot", "sbom", "nvd"],
    });
    if let Err(err) = client
        .post_json::<_, Value>("/api/v1/admin/workers/heartbeat", &payload)
        .await
    {
        tracing::debug!(error = %err, "Worker fleet heartbeat failed (best-effort)");
    }
}

fn max_heartbeat_age_secs(poll_interval_secs: u64, job_timeout_secs: u64) -> u64 {
    60_u64.max(
        poll_interval_secs
            .saturating_mul(2)
            .saturating_add(job_timeout_secs),
    )
}

fn verify_heartbeat(path: &Path, max_age_secs: u64, now_unix: i64) -> Result<()> {
    let raw = std::fs::read_to_string(path).map_err(|err| {
        CommonError::Execution(format!(
            "heartbeat file unreadable at {}: {err}",
            path.display()
        ))
    })?;
    let trimmed = raw.trim();
    let ts = trimmed.parse::<f64>().map_err(|err| {
        CommonError::Execution(format!(
            "heartbeat file {} contains invalid timestamp {trimmed:?}: {err}",
            path.display()
        ))
    })?;
    if !ts.is_finite() {
        return Err(CommonError::Execution(format!(
            "heartbeat file {} contains non-finite timestamp {trimmed:?}",
            path.display()
        )));
    }
    let age = (now_unix as f64) - ts;
    if age > max_age_secs as f64 {
        return Err(CommonError::Execution(format!(
            "heartbeat stale ({age:.1}s > {max_age_secs}s)"
        )));
    }
    Ok(())
}

fn pending_endpoint_path(base_path: &str, cluster: Option<&str>) -> String {
    match cluster.map(str::trim).filter(|c| !c.is_empty()) {
        Some(c) => {
            let encoded: String = url::form_urlencoded::byte_serialize(c.as_bytes()).collect();
            format!("{base_path}?cluster={encoded}")
        }
        None => base_path.to_string(),
    }
}

async fn execute_runner(loop_mode: bool, interval_override: Option<u64>) -> Result<()> {
    let cfg = Arc::new(ScanningConfig::from_env()?);
    let auth = cfg.build_auth_provider()?;
    auth.validate()?;

    let client = Arc::new(ElderApiClient::new(&cfg.elder_api_url, auth)?);
    let metrics = Arc::new(ServiceMetrics::new("svc-scanner", "svc_scanner"));
    let poll_interval = Duration::from_secs(
        interval_override
            .map(clamp_poll_interval)
            .unwrap_or(cfg.default_loop_interval_secs),
    );
    let mut last_nvd_sync: Option<DateTime<Utc>> = None;

    write_heartbeat(&cfg.health_file);
    send_worker_heartbeat(&client, &cfg).await;

    if !loop_mode {
        run_cycle(Arc::clone(&cfg), client, metrics, &mut last_nvd_sync).await?;
        write_heartbeat(&cfg.health_file);
        return Ok(());
    }

    let hb_interval_secs = std::env::var("SCANNER_HEARTBEAT_INTERVAL_SECONDS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(30)
        .clamp(10, 300);
    let hb_client = Arc::clone(&client);
    let hb_cfg = Arc::clone(&cfg);
    let heartbeat_task = tokio::spawn(async move {
        let mut ticker = tokio::time::interval(Duration::from_secs(hb_interval_secs));
        // First tick fires immediately, skip since startup already sent one.
        ticker.tick().await;
        loop {
            ticker.tick().await;
            write_heartbeat(&hb_cfg.health_file);
            send_worker_heartbeat(&hb_client, &hb_cfg).await;
        }
    });

    tracing::info!(
        interval_secs = poll_interval.as_secs(),
        progress_interval_secs = cfg.progress_interval_secs,
        heartbeat_interval_secs = hb_interval_secs,
        cluster = cfg.scanner_cluster.as_deref().unwrap_or("default"),
        health_file = %cfg.health_file.display(),
        "Starting svc-scanner polling loop"
    );

    loop {
        write_heartbeat(&cfg.health_file);
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
        write_heartbeat(&cfg.health_file);
        send_worker_heartbeat(&client, &cfg).await;

        tokio::select! {
            _ = shutdown_signal() => {
                tracing::info!("Shutdown signal received; exiting polling loop cleanly");
                break;
            }
            _ = tokio::time::sleep(poll_interval) => {}
        }
    }

    heartbeat_task.abort();

    Ok(())
}

async fn run_cycle(
    cfg: Arc<ScanningConfig>,
    client: Arc<ElderApiClient>,
    metrics: Arc<ServiceMetrics>,
    last_nvd_sync: &mut Option<DateTime<Utc>>,
) -> Result<()> {
    let cycle_start = Instant::now();
    let cycle_span = tracing::info_span!(
        "svc_scanner.cycle",
        cluster = cfg.scanner_cluster.as_deref().unwrap_or("default")
    );

    let res = async {
        // 1. Evaluate due SBOM schedules first so newly created pending scans are picked up in the same pass.
        if let Err(err) = process_due_schedules(&client).await {
            tracing::error!(error = %err, "Failed processing due SBOM schedules");
        }

        // 2. Fetch pending discovery + SBOM jobs (filtered by optional SCANNER_CLUSTER).
        let discovery_path = pending_endpoint_path(
            "/api/v1/discovery/jobs/pending",
            cfg.scanner_cluster.as_deref(),
        );
        let pending_discovery = match client.get_json::<PendingJobsResponse>(&discovery_path).await
        {
            Ok(resp) => resp.jobs,
            Err(err) => {
                tracing::error!(error = %err, "Failed fetching pending discovery jobs");
                Vec::new()
            }
        };

        let sbom_path = pending_endpoint_path(
            "/api/v1/sbom/scans/pending",
            cfg.scanner_cluster.as_deref(),
        );
        let pending_sbom = match client.get_json::<Vec<PendingSbomScan>>(&sbom_path).await {
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
                run_discovery_job(&job, &cfg_c, client_c, &metrics_c, job_timeout).await;
            });
        }

        for scan in pending_sbom {
            let sem_c = Arc::clone(&sem);
            let cfg_c = Arc::clone(&cfg);
            let client_c = Arc::clone(&client);
            let metrics_c = Arc::clone(&metrics);
            tasks.spawn(async move {
                let _permit = sem_c.acquire_owned().await.ok();
                run_sbom_job(&scan, &cfg_c, client_c, &metrics_c, job_timeout).await;
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
    client: Arc<ElderApiClient>,
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

        let progress = ProgressReporter::spawn(
            Arc::clone(&client),
            format!("/api/v1/discovery/jobs/{}/progress", job.id),
            Duration::from_secs(cfg.progress_interval_secs),
            cfg.scanner_cluster.clone(),
            start,
        );

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

        progress.stop().await;

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
    client: Arc<ElderApiClient>,
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

        let progress = ProgressReporter::spawn(
            Arc::clone(&client),
            format!("/api/v1/sbom/scans/{}/progress", scan.id),
            Duration::from_secs(cfg.progress_interval_secs),
            cfg.scanner_cluster.clone(),
            start,
        );

        let exec_res =
            tokio::time::timeout(job_timeout, execute_sbom_scan(scan, &cfg.git_bin)).await;

        progress.stop().await;

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
        if let Err(err) = report_sbom_result(&client, scan.id, &outcome).await {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Mutex;
    use tempfile::TempDir;

    #[test]
    fn heartbeat_file_verification_accepts_fresh_and_rejects_missing_or_stale() {
        let dir = TempDir::new().expect("tempdir");
        let health_path = dir.path().join("scanner.healthy");

        // Missing file must fail.
        assert!(verify_heartbeat(&health_path, 900, 1_700_000_000).is_err());

        // Invalid content must fail.
        std::fs::write(&health_path, "not-a-timestamp\n").expect("write invalid");
        assert!(verify_heartbeat(&health_path, 900, 1_700_000_000).is_err());

        // Fresh heartbeat must pass.
        write_heartbeat(&health_path);
        let now = Utc::now().timestamp();
        assert!(verify_heartbeat(&health_path, 900, now).is_ok());

        // Stale heartbeat must fail.
        std::fs::write(&health_path, format!("{}\n", now - 1000)).expect("write stale");
        assert!(verify_heartbeat(&health_path, 900, now).is_err());
    }

    #[test]
    fn pending_endpoint_path_appends_cluster_when_configured() {
        assert_eq!(
            pending_endpoint_path("/api/v1/discovery/jobs/pending", None),
            "/api/v1/discovery/jobs/pending"
        );
        assert_eq!(
            pending_endpoint_path("/api/v1/discovery/jobs/pending", Some("   ")),
            "/api/v1/discovery/jobs/pending"
        );
        assert_eq!(
            pending_endpoint_path("/api/v1/discovery/jobs/pending", Some("dal2-edge")),
            "/api/v1/discovery/jobs/pending?cluster=dal2-edge"
        );
    }

    #[tokio::test]
    async fn progress_reporter_pushes_periodically_and_cancels_cleanly_on_stop() {
        use elder_svc_common::ServiceTokenProvider;
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        let hits = Arc::new(AtomicUsize::new(0));
        let hits_clone = Arc::clone(&hits);
        let request_lines: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let lines_clone = Arc::clone(&request_lines);

        let server = tokio::spawn(async move {
            while let Ok((mut sock, _)) = listener.accept().await {
                let mut buf = [0u8; 4096];
                let n = sock.read(&mut buf).await.unwrap_or(0);
                let req = String::from_utf8_lossy(&buf[..n]);
                if let Some(line) = req.lines().next() {
                    lines_clone.lock().expect("lock").push(line.to_string());
                }
                hits_clone.fetch_add(1, Ordering::SeqCst);
                let body = r#"{"ok":true}"#;
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = sock.write_all(resp.as_bytes()).await;
                let _ = sock.shutdown().await;
            }
        });

        let auth = ServiceTokenProvider::from_env_lookup(
            "svc-scanner",
            vec!["discovery:write".into()],
            |k| match k {
                "ELDER_API_TOKEN" => Some("remote-token".into()),
                _ => None,
            },
        )
        .expect("auth");
        let client = Arc::new(
            ElderApiClient::new(format!("http://{addr}"), auth)
                .expect("client")
                .with_retry_config(0, Duration::from_millis(5)),
        );

        let reporter = ProgressReporter::spawn(
            client,
            "/api/v1/discovery/jobs/42/progress".to_string(),
            Duration::from_millis(40),
            Some("dal2-edge".to_string()),
            Instant::now(),
        );

        // Wait until at least 2 periodic progress heartbeats have been sent.
        let deadline = Instant::now() + Duration::from_secs(2);
        while hits.load(Ordering::SeqCst) < 2 && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(15)).await;
        }
        assert!(
            hits.load(Ordering::SeqCst) >= 2,
            "expected at least 2 progress heartbeats before stop"
        );

        reporter.stop().await;
        let count_after_stop = hits.load(Ordering::SeqCst);

        // Ensure no further progress requests are emitted after cancellation.
        tokio::time::sleep(Duration::from_millis(150)).await;
        assert_eq!(
            hits.load(Ordering::SeqCst),
            count_after_stop,
            "progress reporter must not emit requests after stop()"
        );

        let lines = request_lines.lock().expect("lock").clone();
        assert!(
            lines
                .iter()
                .all(|l| l.starts_with("POST /api/v1/discovery/jobs/42/progress ")),
            "unexpected request lines: {lines:?}"
        );

        server.abort();
    }
}
