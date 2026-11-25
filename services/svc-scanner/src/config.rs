//! Runtime configuration for `svc-scanner` parsed from environment variables.

use std::fmt;
use std::path::PathBuf;

use elder_svc_common::{CommonError, Result, ServiceTokenProvider};

/// Allowed scopes requested by `svc-scanner` (`hub-api` allowlist).
pub const SVC_SCANNING_SCOPES: &[&str] = &[
    "discovery:read",
    "discovery:write",
    "sbom:read",
    "sbom:write",
    "vulnerabilities:write",
];

/// Default poll interval between cycles in `--loop` mode (5 minutes).
pub const DEFAULT_POLL_INTERVAL_SECS: u64 = 300;
/// Minimum allowed poll interval in seconds.
pub const MIN_POLL_INTERVAL_SECS: u64 = 10;
/// Maximum allowed poll interval in seconds.
pub const MAX_POLL_INTERVAL_SECS: u64 = 3600;

/// Default periodic progress heartbeat push interval during job execution (seconds).
pub const DEFAULT_PROGRESS_INTERVAL_SECS: u64 = 30;
/// Minimum allowed progress push interval in seconds.
pub const MIN_PROGRESS_INTERVAL_SECS: u64 = 1;
/// Maximum allowed progress push interval in seconds.
pub const MAX_PROGRESS_INTERVAL_SECS: u64 = 300;

/// Default heartbeat timestamp file path for Kubernetes liveness/readiness probes.
pub const DEFAULT_HEALTH_FILE: &str = "/tmp/scanner.healthy";

/// Clamp a poll interval (seconds) into `[10, 3600]`.
pub fn clamp_poll_interval(secs: u64) -> u64 {
    secs.clamp(MIN_POLL_INTERVAL_SECS, MAX_POLL_INTERVAL_SECS)
}

/// Clamp a progress push interval (seconds) into `[1, 300]`.
pub fn clamp_progress_interval(secs: u64) -> u64 {
    secs.clamp(MIN_PROGRESS_INTERVAL_SECS, MAX_PROGRESS_INTERVAL_SECS)
}

/// Validated runtime settings for `svc-scanner`.
#[derive(Clone)]
pub struct ScanningConfig {
    /// Base URL for `hub-api` (e.g. `http://hub-api:5000`).
    pub elder_api_url: String,
    /// Optional static bearer token for remote datacenter/cloud deployments (`ELDER_INTERNAL_TOKEN`).
    pub elder_internal_token: Option<String>,
    /// Optional cluster identifier for remote datacenter/cloud job routing (`SCANNER_CLUSTER`).
    pub scanner_cluster: Option<String>,
    /// `masscan` packet transmit rate (packets/sec).
    pub masscan_rate: u32,
    /// Maximum concurrent discovery/SBOM jobs per run.
    pub max_concurrent_jobs: usize,
    /// Per-job execution timeout in seconds.
    pub job_timeout_secs: u64,
    /// Directory where temporary screenshot artifacts are stored.
    pub screenshot_dir: PathBuf,
    /// Default interval between cycles in `--loop` mode (seconds, clamped `[10, 3600]`).
    pub default_loop_interval_secs: u64,
    /// Periodic progress/heartbeat push interval while executing a job (seconds, clamped `[1, 300]`).
    pub progress_interval_secs: u64,
    /// Path to the heartbeat timestamp file written on startup and each poll cycle.
    pub health_file: PathBuf,
    /// Enable periodic NVD enrichment sync trigger.
    pub nvd_sync_enabled: bool,
    /// Minimum interval between NVD sync runs in loop mode (seconds).
    pub nvd_sync_interval_secs: u64,
    /// Path to `masscan` binary.
    pub masscan_bin: PathBuf,
    /// Path to `git` binary.
    pub git_bin: PathBuf,
    /// Path to Chromium headless binary.
    pub chromium_bin: PathBuf,
}

impl fmt::Debug for ScanningConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ScanningConfig")
            .field("elder_api_url", &self.elder_api_url)
            .field(
                "elder_internal_token",
                &self.elder_internal_token.as_ref().map(|_| "***"),
            )
            .field("scanner_cluster", &self.scanner_cluster)
            .field("masscan_rate", &self.masscan_rate)
            .field("max_concurrent_jobs", &self.max_concurrent_jobs)
            .field("job_timeout_secs", &self.job_timeout_secs)
            .field("screenshot_dir", &self.screenshot_dir)
            .field(
                "default_loop_interval_secs",
                &self.default_loop_interval_secs,
            )
            .field("progress_interval_secs", &self.progress_interval_secs)
            .field("health_file", &self.health_file)
            .field("nvd_sync_enabled", &self.nvd_sync_enabled)
            .field("nvd_sync_interval_secs", &self.nvd_sync_interval_secs)
            .field("masscan_bin", &self.masscan_bin)
            .field("git_bin", &self.git_bin)
            .field("chromium_bin", &self.chromium_bin)
            .finish()
    }
}

impl ScanningConfig {
    /// Load configuration from process environment variables.
    pub fn from_env() -> Result<Self> {
        Self::from_env_lookup(|name| std::env::var(name).ok())
    }

    /// Load configuration using an arbitrary env lookup (deterministic tests).
    pub fn from_env_lookup<F>(lookup: F) -> Result<Self>
    where
        F: Fn(&str) -> Option<String>,
    {
        let elder_api_url = non_empty(lookup("ELDER_API_URL")).ok_or_else(|| {
            CommonError::Config("ELDER_API_URL environment variable is required".into())
        })?;
        if !elder_api_url.starts_with("http://") && !elder_api_url.starts_with("https://") {
            return Err(CommonError::Config(format!(
                "ELDER_API_URL must start with http:// or https://, got {elder_api_url:?}"
            )));
        }

        let elder_internal_token = non_empty(lookup("ELDER_INTERNAL_TOKEN"));
        let scanner_cluster = non_empty(lookup("SCANNER_CLUSTER"));

        let masscan_rate = parse_bounded(&lookup, "SCANNER_MASSCAN_RATE", 1000_u32, 1, 1_000_000)?;
        let max_concurrent_jobs =
            parse_bounded(&lookup, "SCANNER_MAX_CONCURRENT_JOBS", 5_usize, 1, 64)?;
        let job_timeout_secs =
            parse_bounded(&lookup, "SCANNER_JOB_TIMEOUT_SECONDS", 300_u64, 10, 3600)?;

        let default_loop_interval_secs = parse_clamped_with_fallback(
            &lookup,
            "SCANNER_POLL_INTERVAL_SECONDS",
            Some("SCANNER_INTERVAL_SECONDS"),
            DEFAULT_POLL_INTERVAL_SECS,
            clamp_poll_interval,
        )?;
        let progress_interval_secs = parse_clamped_with_fallback(
            &lookup,
            "SCANNER_PROGRESS_INTERVAL_SECONDS",
            None,
            DEFAULT_PROGRESS_INTERVAL_SECS,
            clamp_progress_interval,
        )?;
        let health_file = PathBuf::from(
            non_empty(lookup("SCANNER_HEALTH_FILE"))
                .unwrap_or_else(|| DEFAULT_HEALTH_FILE.to_string()),
        );

        let nvd_sync_interval_secs =
            parse_bounded(&lookup, "NVD_SYNC_INTERVAL", 21_600_u64, 60, 604_800)?;

        let nvd_sync_enabled = non_empty(lookup("NVD_SYNC_ENABLED"))
            .map(|v| matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
            .unwrap_or(true);

        let screenshot_dir = PathBuf::from(
            non_empty(lookup("SCREENSHOT_DIR")).unwrap_or_else(|| "/tmp/screenshots".to_string()),
        );
        let masscan_bin = PathBuf::from(
            non_empty(lookup("MASSCAN_BIN")).unwrap_or_else(|| "/usr/bin/masscan".to_string()),
        );
        let git_bin = PathBuf::from(
            non_empty(lookup("GIT_BIN")).unwrap_or_else(|| "/usr/bin/git".to_string()),
        );
        let chromium_bin = PathBuf::from(
            non_empty(lookup("CHROMIUM_BIN"))
                .unwrap_or_else(|| "/usr/lib/chromium/chromium".to_string()),
        );

        Ok(Self {
            elder_api_url,
            elder_internal_token,
            scanner_cluster,
            masscan_rate,
            max_concurrent_jobs,
            job_timeout_secs,
            screenshot_dir,
            default_loop_interval_secs,
            progress_interval_secs,
            health_file,
            nvd_sync_enabled,
            nvd_sync_interval_secs,
            masscan_bin,
            git_bin,
            chromium_bin,
        })
    }

    /// Build a `ServiceTokenProvider` configured with `svc-scanner` scopes.
    ///
    /// Prefers `ELDER_INTERNAL_TOKEN` when configured (for remote deployments)
    /// and falls back to `JWT_SECRET_KEY` / `SECRET_KEY` / `ELDER_API_TOKEN`
    /// in-cluster.
    pub fn build_auth_provider(&self) -> Result<ServiceTokenProvider> {
        self.build_auth_provider_with_lookup(|name| std::env::var(name).ok())
    }

    /// Build a `ServiceTokenProvider` using a custom env lookup (deterministic tests).
    pub fn build_auth_provider_with_lookup<F>(&self, lookup: F) -> Result<ServiceTokenProvider>
    where
        F: Fn(&str) -> Option<String>,
    {
        let scopes: Vec<String> = SVC_SCANNING_SCOPES
            .iter()
            .map(|s| (*s).to_string())
            .collect();
        if let Some(internal_token) = self
            .elder_internal_token
            .clone()
            .or_else(|| non_empty(lookup("ELDER_INTERNAL_TOKEN")))
        {
            return ServiceTokenProvider::from_env_lookup("svc-scanner", scopes, move |name| {
                if name == "ELDER_API_TOKEN" {
                    Some(internal_token.clone())
                } else {
                    None
                }
            });
        }
        ServiceTokenProvider::from_env_lookup("svc-scanner", scopes, lookup)
    }
}

fn parse_clamped_with_fallback<F, C>(
    lookup: &F,
    primary: &str,
    fallback: Option<&str>,
    default: u64,
    clamp_fn: C,
) -> Result<u64>
where
    F: Fn(&str) -> Option<String>,
    C: Fn(u64) -> u64,
{
    let (name, raw_opt) = match non_empty(lookup(primary)) {
        Some(val) => (primary, Some(val)),
        None => match fallback {
            Some(fb) => (fb, non_empty(lookup(fb))),
            None => (primary, None),
        },
    };
    let Some(raw) = raw_opt else {
        return Ok(default);
    };
    let parsed = raw
        .parse::<u64>()
        .map_err(|e| CommonError::Config(format!("invalid {name} {raw:?}: {e}")))?;
    Ok(clamp_fn(parsed))
}

fn parse_bounded<T, F>(lookup: &F, name: &str, default: T, min: T, max: T) -> Result<T>
where
    T: std::str::FromStr + PartialOrd + Copy + std::fmt::Display,
    <T as std::str::FromStr>::Err: std::fmt::Display,
    F: Fn(&str) -> Option<String>,
{
    let Some(raw) = non_empty(lookup(name)) else {
        return Ok(default);
    };
    let parsed = raw
        .parse::<T>()
        .map_err(|e| CommonError::Config(format!("invalid {name} {raw:?}: {e}")))?;
    if parsed < min || parsed > max {
        return Err(CommonError::Config(format!(
            "{name} must be within {min}..={max}, got {parsed}"
        )));
    }
    Ok(parsed)
}

fn non_empty(value: Option<String>) -> Option<String> {
    value.and_then(|v| {
        let trimmed = v.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn lookup_from(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
            .collect();
        move |k: &str| map.get(k).cloned()
    }

    #[test]
    fn loads_defaults_and_custom_bounds() {
        let cfg = ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("SCANNER_MASSCAN_RATE", "2500"),
            ("SCANNER_MAX_CONCURRENT_JOBS", "8"),
            ("NVD_SYNC_ENABLED", "false"),
        ]))
        .expect("valid config");

        assert_eq!(cfg.elder_api_url, "http://hub-api:5000");
        assert_eq!(cfg.masscan_rate, 2500);
        assert_eq!(cfg.max_concurrent_jobs, 8);
        assert_eq!(cfg.job_timeout_secs, 300);
        assert_eq!(cfg.default_loop_interval_secs, 300);
        assert_eq!(cfg.progress_interval_secs, 30);
        assert_eq!(cfg.health_file, PathBuf::from("/tmp/scanner.healthy"));
        assert!(cfg.scanner_cluster.is_none());
        assert!(cfg.elder_internal_token.is_none());
        assert!(!cfg.nvd_sync_enabled);
    }

    #[test]
    fn poll_interval_defaults_to_300s_falls_back_and_clamps() {
        let fallback = ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("SCANNER_INTERVAL_SECONDS", "120"),
        ]))
        .expect("fallback config");
        assert_eq!(fallback.default_loop_interval_secs, 120);

        let primary_wins = ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("SCANNER_POLL_INTERVAL_SECONDS", "450"),
            ("SCANNER_INTERVAL_SECONDS", "120"),
        ]))
        .expect("primary wins");
        assert_eq!(primary_wins.default_loop_interval_secs, 450);

        let clamped_low = ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("SCANNER_POLL_INTERVAL_SECONDS", "2"),
        ]))
        .expect("clamped low");
        assert_eq!(clamped_low.default_loop_interval_secs, 10);

        let clamped_high = ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("SCANNER_POLL_INTERVAL_SECONDS", "99999"),
        ]))
        .expect("clamped high");
        assert_eq!(clamped_high.default_loop_interval_secs, 3600);
    }

    #[test]
    fn progress_interval_defaults_to_30s_and_clamps_to_1_300() {
        let default_cfg = ScanningConfig::from_env_lookup(lookup_from(&[(
            "ELDER_API_URL",
            "http://hub-api:5000",
        )]))
        .expect("default");
        assert_eq!(default_cfg.progress_interval_secs, 30);

        let low = ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("SCANNER_PROGRESS_INTERVAL_SECONDS", "0"),
        ]))
        .expect("clamped low");
        assert_eq!(low.progress_interval_secs, 1);

        let high = ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("SCANNER_PROGRESS_INTERVAL_SECONDS", "900"),
        ]))
        .expect("clamped high");
        assert_eq!(high.progress_interval_secs, 300);

        let mid = ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("SCANNER_PROGRESS_INTERVAL_SECONDS", "45"),
        ]))
        .expect("in-range");
        assert_eq!(mid.progress_interval_secs, 45);
    }

    #[test]
    fn supports_remote_cluster_and_internal_token_with_jwt_fallback() {
        let jwt_secret = "s".repeat(32);
        let remote_cfg = ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "https://hub.example.com"),
            ("SCANNER_CLUSTER", "dal2-edge"),
            ("ELDER_INTERNAL_TOKEN", "remote-bearer-tok"),
            ("SCANNER_HEALTH_FILE", "/tmp/custom-scanner.healthy"),
        ]))
        .expect("remote config");
        assert_eq!(remote_cfg.scanner_cluster.as_deref(), Some("dal2-edge"));
        assert_eq!(
            remote_cfg.health_file,
            PathBuf::from("/tmp/custom-scanner.healthy")
        );
        // Debug output must never leak the raw token.
        assert!(!format!("{remote_cfg:?}").contains("remote-bearer-tok"));

        let remote_auth = remote_cfg
            .build_auth_provider_with_lookup(lookup_from(&[("JWT_SECRET_KEY", &jwt_secret)]))
            .expect("remote auth");
        assert_eq!(remote_auth.token().expect("token"), "remote-bearer-tok");

        let incluster_cfg = ScanningConfig::from_env_lookup(lookup_from(&[(
            "ELDER_API_URL",
            "http://hub-api:5000",
        )]))
        .expect("incluster config");
        let incluster_auth = incluster_cfg
            .build_auth_provider_with_lookup(lookup_from(&[("JWT_SECRET_KEY", &jwt_secret)]))
            .expect("incluster jwt auth");
        let jwt = incluster_auth.token().expect("jwt");
        assert_eq!(jwt.split('.').count(), 3);
    }

    #[test]
    fn rejects_missing_url_and_out_of_range_values() {
        assert!(ScanningConfig::from_env_lookup(lookup_from(&[])).is_err());
        assert!(
            ScanningConfig::from_env_lookup(lookup_from(&[("ELDER_API_URL", "hub-api:5000")]))
                .is_err()
        );
        assert!(ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("SCANNER_MAX_CONCURRENT_JOBS", "0")
        ]))
        .is_err());
    }
}
