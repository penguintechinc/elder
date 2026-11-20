//! Runtime configuration for `svc-integrations` parsed from environment variables.

use elder_svc_common::{CommonError, Result, ServiceTokenProvider};

/// Allowed scopes requested by `svc-integrations` matching
/// `apps/api/auth/jwt_handler.py::SERVICE_WORKER_ALLOWED_SCOPES["svc-integrations"]`.
pub const SVC_INTEGRATIONS_SCOPES: &[&str] = &[
    "discovery:read",
    "discovery:write",
    "entities:read",
    "entities:write",
    "organizations:read",
    "organizations:write",
    "dependencies:read",
    "dependencies:write",
    "identities:read",
    "identities:write",
    "issues:read",
    "issues:write",
    "projects:read",
    "projects:write",
    "integrations:sync",
];

/// Return the 15 allowed scopes for `svc-integrations`.
pub fn svc_integrations_scopes() -> Vec<String> {
    SVC_INTEGRATIONS_SCOPES
        .iter()
        .map(|s| (*s).to_string())
        .collect()
}

/// Validated runtime settings for `svc-integrations`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IntegrationsConfig {
    /// Base URL for `hub-api` (e.g. `http://hub-api:5000`).
    pub elder_api_url: String,
    /// Polling interval in seconds for `--loop` mode (`INTEGRATIONS_INTERVAL_SECONDS`, default 60).
    pub interval_seconds: u64,
    /// Maximum concurrent connector tasks per cycle (`INTEGRATIONS_MAX_CONCURRENCY`, default 8).
    pub max_concurrency: usize,
    /// Per-connector execution timeout in seconds (`INTEGRATIONS_JOB_TIMEOUT_SECONDS`, default 120).
    pub job_timeout_seconds: u64,
}

impl IntegrationsConfig {
    /// Load configuration from process environment variables.
    pub fn from_env() -> Result<Self> {
        Self::from_env_lookup(|name| std::env::var(name).ok())
    }

    /// Load configuration using an arbitrary env lookup (for deterministic tests).
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

        let interval_seconds =
            parse_bounded(&lookup, "INTEGRATIONS_INTERVAL_SECONDS", 60_u64, 5, 86_400)?;
        let max_concurrency =
            parse_bounded(&lookup, "INTEGRATIONS_MAX_CONCURRENCY", 8_usize, 1, 64)?;
        let job_timeout_seconds = parse_bounded(
            &lookup,
            "INTEGRATIONS_JOB_TIMEOUT_SECONDS",
            120_u64,
            5,
            3600,
        )?;

        Ok(Self {
            elder_api_url,
            interval_seconds,
            max_concurrency,
            job_timeout_seconds,
        })
    }

    /// Build a `ServiceTokenProvider` configured with `svc-integrations` scopes.
    pub fn build_auth_provider(&self) -> Result<ServiceTokenProvider> {
        ServiceTokenProvider::from_env("svc-integrations", svc_integrations_scopes())
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
        let cfg = IntegrationsConfig::from_env_lookup(lookup_from(&[(
            "ELDER_API_URL",
            "http://hub-api:5000",
        )]))
        .expect("default config");
        assert_eq!(cfg.elder_api_url, "http://hub-api:5000");
        assert_eq!(cfg.interval_seconds, 60);
        assert_eq!(cfg.max_concurrency, 8);
        assert_eq!(cfg.job_timeout_seconds, 120);

        let custom = IntegrationsConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "https://api.elder.local"),
            ("INTEGRATIONS_INTERVAL_SECONDS", "30"),
            ("INTEGRATIONS_MAX_CONCURRENCY", "16"),
            ("INTEGRATIONS_JOB_TIMEOUT_SECONDS", "240"),
        ]))
        .expect("custom config");
        assert_eq!(custom.interval_seconds, 30);
        assert_eq!(custom.max_concurrency, 16);
        assert_eq!(custom.job_timeout_seconds, 240);
    }

    #[test]
    fn rejects_invalid_url_and_out_of_range_values() {
        assert!(IntegrationsConfig::from_env_lookup(lookup_from(&[])).is_err());
        assert!(IntegrationsConfig::from_env_lookup(lookup_from(&[(
            "ELDER_API_URL",
            "hub-api:5000"
        )]))
        .is_err());
        assert!(IntegrationsConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("INTEGRATIONS_MAX_CONCURRENCY", "0"),
        ]))
        .is_err());
        assert!(IntegrationsConfig::from_env_lookup(lookup_from(&[
            ("ELDER_API_URL", "http://hub-api:5000"),
            ("INTEGRATIONS_JOB_TIMEOUT_SECONDS", "1"),
        ]))
        .is_err());
    }

    #[test]
    fn exposes_all_fifteen_allowed_scopes() {
        let scopes = svc_integrations_scopes();
        assert_eq!(scopes.len(), 15);
        assert!(scopes.contains(&"integrations:sync".to_string()));
        assert!(scopes.contains(&"identities:write".to_string()));
        assert!(scopes.contains(&"discovery:write".to_string()));
        assert!(scopes.contains(&"issues:write".to_string()));
    }
}
