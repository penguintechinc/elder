//! `svc-integrations`: stateless async Elder data-plane integrations CronJob binary.

mod config;
mod orchestrator;

use std::sync::Arc;
use std::time::Duration;

use clap::{Parser, Subcommand};

use elder_svc_common::{telemetry, ElderApiClient, Result};

use crate::config::IntegrationsConfig;
use crate::orchestrator::{run_cycle, DefaultIntegrationBackend, IntegrationsMetrics};

#[derive(Debug, Parser)]
#[command(
    name = "svc-integrations",
    version,
    about = "Elder stateless data-plane integrations worker"
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Run pending discovery, sync, IAM, and Google Workspace jobs (default: once).
    Run {
        /// Execute a single integration cycle and exit (CronJob mode).
        #[arg(long, conflicts_with = "loop_mode")]
        once: bool,

        /// Continuously poll on an interval until SIGTERM/SIGINT.
        #[arg(long = "loop")]
        loop_mode: bool,

        /// Poll interval in seconds for `--loop` mode (defaults to INTEGRATIONS_INTERVAL_SECONDS or 60).
        #[arg(long)]
        interval: Option<u64>,
    },

    /// Validate configuration and JWT credential generation without touching network.
    Healthcheck,
}

#[tokio::main]
async fn main() -> std::process::ExitCode {
    let cli = Cli::parse();

    let telemetry_cfg = match telemetry::TelemetryConfig::from_env("svc-integrations") {
        Ok(cfg) => cfg,
        Err(err) => {
            let fallback = telemetry::TelemetryConfig {
                service_name: "svc-integrations".to_string(),
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
            tracing::error!(error = %err, "svc-integrations exited with failure");
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
    let cfg = IntegrationsConfig::from_env()?;
    let auth = cfg.build_auth_provider()?;
    auth.validate()?;
    let _client = ElderApiClient::new(&cfg.elder_api_url, auth)?;
    tracing::info!(
        elder_api_url = %cfg.elder_api_url,
        "svc-integrations healthcheck passed"
    );
    Ok(())
}

async fn execute_runner(loop_mode: bool, interval_override: Option<u64>) -> Result<()> {
    let cfg = IntegrationsConfig::from_env()?;
    let auth = cfg.build_auth_provider()?;
    auth.validate()?;

    let client = Arc::new(ElderApiClient::new(&cfg.elder_api_url, auth)?);
    let backend = Arc::new(DefaultIntegrationBackend);
    let metrics = Arc::new(IntegrationsMetrics::global());
    let job_timeout = Duration::from_secs(cfg.job_timeout_seconds);
    let poll_interval = Duration::from_secs(interval_override.unwrap_or(cfg.interval_seconds));

    if !loop_mode {
        run_cycle(
            client,
            backend,
            metrics,
            cfg.max_concurrency,
            job_timeout,
            shutdown_signal(),
        )
        .await?;
        return Ok(());
    }

    tracing::info!(
        interval_secs = poll_interval.as_secs(),
        max_concurrency = cfg.max_concurrency,
        job_timeout_secs = cfg.job_timeout_seconds,
        "Starting svc-integrations polling loop"
    );

    loop {
        if let Err(err) = run_cycle(
            Arc::clone(&client),
            Arc::clone(&backend),
            Arc::clone(&metrics),
            cfg.max_concurrency,
            job_timeout,
            shutdown_signal(),
        )
        .await
        {
            tracing::error!(error = %err, "Integration cycle encountered an error");
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
