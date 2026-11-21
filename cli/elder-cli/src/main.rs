//! Binary entrypoint for the official `elder` Rust CLI.

use std::io;
use std::process::ExitCode;

use elder_cli::error::EXIT_OK;
use tracing_subscriber::EnvFilter;

fn init_tracing() {
    let verbose_count = std::env::args()
        .filter(|a| a == "-v" || a == "--verbose")
        .count()
        + std::env::args().filter(|a| a.starts_with("-vv")).count() * 2;

    let default_level = match verbose_count {
        0 => "warn",
        1 => "info",
        _ => "debug",
    };

    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(default_level));

    let _ = tracing_subscriber::fmt()
        .with_writer(io::stderr)
        .with_env_filter(filter)
        .with_target(false)
        .try_init();
}

#[tokio::main]
async fn main() -> ExitCode {
    init_tracing();

    match elder_cli::run_process().await {
        Ok(()) => ExitCode::from(EXIT_OK),
        Err(err) => {
            eprintln!("error: {err}");
            err.exit_code()
        }
    }
}
