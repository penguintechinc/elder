//! Runtime configuration for `svc-scanning` parsed from environment variables.

use std::path::PathBuf;

use elder_svc_common::{CommonError, Result, ServiceTokenProvider};

/// Allowed scopes requested by `svc-scanning` (`elder-api` allowlist).
pub const SVC_SCANNING_SCOPES: &[&str] = &[
    "discovery:read",
    "discovery:write",
    "sbom:read",
    "sbom:write",
    "vulnerabilities:write",
];

/// Validated runtime settings for `svc-scanning`.
#[derive(Debug, Clone)]
pub struct ScanningConfig {
    /// Base URL for `elder-api` (e.g. `http://elder-api:5000`).
    pub elder_api_url: String,
    /// `masscan` packet transmit rate (packets/sec).
    pub masscan_rate: u32,
    /// Maximum concurrent discovery/SBOM jobs per run.
    pub max_concurrent_jobs: usize,
    /// Per-job execution timeout in seconds.
    pub job_timeout_secs: u64,
    /// Directory where temporary screenshot artifacts are stored.
    pub screenshot_dir: PathBuf,
    /// Default interval between cycles in `--loop` mode (seconds).
    pub default_loop_interval_secs: u64,
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

        let masscan_rate = parse_bounded(&lookup, "SCANNER_MASSCAN_RATE", 1000_u32, 1, 1_000_000)?;
        let max_concurrent_jobs =
            parse_bounded(&lookup, "SCANNER_MAX_CONCURRENT_JOBS", 5_usize, 1, 64)?;
        let job_timeout_secs =
            parse_bounded(&lookup, "SCANNER_JOB_TIMEOUT_SECONDS", 300_u64, 10, 3600)?;
        let default_loop_interval_secs =
            parse_bounded(&lookup, "SCANNER_INTERVAL_SECONDS", 60_u64, 5, 86_400)?;
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
            masscan_rate,
            max_concurrent_jobs,
            job_timeout_secs,
            screenshot_dir,
            default_loop_interval_secs,
            nvd_sync_enabled,
            nvd_sync_interval_secs,
            masscan_bin,
            git_bin,
            chromium_bin,
        })
    }

    /// Build a `ServiceTokenProvider` configured with `svc-scanning` scopes.
    pub fn build_auth_provider(&self) -> Result<ServiceTokenProvider> {
        let scopes = SVC_SCANNING_SCOPES
            .iter()
            .map(|s| (*s).to_string())
            .collect();
        ServiceTokenProvider::from_env("svc-scanning", scopes)
    }
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
            ("ELDER_API_URL", "http://elder-api:5000"),
            ("SCANNER_MASSCAN_RATE", "2500"),
            ("SCANNER_MAX_CONCURRENT_JOBS", "8"),
            ("NVD_SYNC_ENABLED", "false"),
        ]))
        .expect("valid config");

        assert_eq!(cfg.elder_api_url, "http://elder-api:5000");
        assert_eq!(cfg.masscan_rate, 2500);
        assert_eq!(cfg.max_concurrent_jobs, 8);
        assert_eq!(cfg.job_timeout_secs, 300);
        assert!(!cfg.nvd_sync_enabled);
    }

    #[test]
    fn rejects_missing_url_and_out_of_range_values() {
        assert!(ScanningConfig::from_env_lookup(lookup_from(&[])).is_err());
        assert!(ScanningConfig::from_env_lookup(lookup_from(&[(
            "ELDER_API_URL",
            "elder-api:5000"
        )]))
        .is_err());
        assert!(ScanningConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://elder-api:5000"),
            ("SCANNER_MAX_CONCURRENT_JOBS", "0")
        ]))
        .is_err());
    }
}
