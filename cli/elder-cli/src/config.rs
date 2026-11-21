//! Non-secret CLI profile configuration stored at `~/.config/elder/config.toml`.
//!
//! **Security invariant**: This module stores ONLY non-secret profile metadata
//! (`url`, `default_output`, `timeout_secs`, `default_profile`). Tokens, API
//! keys, refresh tokens, and passwords are NEVER persisted to `config.toml`.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use elder_sdk::config::{DEFAULT_API_URL, DEFAULT_TIMEOUT_SECS};
use serde::{Deserialize, Serialize};

use crate::error::{CliError, Result};
use crate::output::OutputFormat;

/// Default profile name used when `--profile` and `ELDER_PROFILE` are unset.
pub const DEFAULT_PROFILE_NAME: &str = "default";

/// Root configuration document serialized at `~/.config/elder/config.toml`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConfigFile {
    /// Active default profile name.
    #[serde(default = "default_profile_name")]
    pub default_profile: String,
    /// Named non-secret profiles.
    #[serde(default)]
    pub profiles: BTreeMap<String, ProfileConfig>,
}

impl Default for ConfigFile {
    fn default() -> Self {
        Self {
            default_profile: DEFAULT_PROFILE_NAME.to_string(),
            profiles: BTreeMap::new(),
        }
    }
}

/// Non-secret settings for a single named Elder profile.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProfileConfig {
    /// Base URL for Elder `hub-api` (e.g. `https://elder.example.com`).
    pub url: String,
    /// Optional default output format for this profile.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_output: Option<OutputFormat>,
    /// Optional default HTTP request timeout in seconds.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_secs: Option<u64>,
}

impl Default for ProfileConfig {
    fn default() -> Self {
        Self {
            url: DEFAULT_API_URL.to_string(),
            default_output: None,
            timeout_secs: None,
        }
    }
}

/// Effective non-secret runtime settings resolved from CLI flags, env vars, and `config.toml`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedSettings {
    /// Active profile name.
    pub profile: String,
    /// Normalized `hub-api` base URL.
    pub url: String,
    /// Request timeout in seconds.
    pub timeout_secs: u64,
    /// Output format.
    pub output: OutputFormat,
}

fn default_profile_name() -> String {
    DEFAULT_PROFILE_NAME.to_string()
}

/// Resolve the path to `~/.config/elder/config.toml`.
///
/// Precedence:
/// 1. `ELDER_CONFIG_DIR` environment variable (`$ELDER_CONFIG_DIR/config.toml`)
/// 2. `XDG_CONFIG_HOME` environment variable (`$XDG_CONFIG_HOME/elder/config.toml`)
/// 3. `~/.config/elder/config.toml` (via `$HOME`) or platform config directory
pub fn config_file_path() -> Result<PathBuf> {
    if let Ok(dir) = std::env::var("ELDER_CONFIG_DIR") {
        let trimmed = dir.trim();
        if !trimmed.is_empty() {
            return Ok(PathBuf::from(trimmed).join("config.toml"));
        }
    }
    if let Ok(xdg) = std::env::var("XDG_CONFIG_HOME") {
        let trimmed = xdg.trim();
        if !trimmed.is_empty() {
            return Ok(PathBuf::from(trimmed).join("elder").join("config.toml"));
        }
    }
    if let Some(home) = dirs::home_dir() {
        return Ok(home.join(".config").join("elder").join("config.toml"));
    }
    if let Some(cfg_dir) = dirs::config_dir() {
        return Ok(cfg_dir.join("elder").join("config.toml"));
    }
    Err(CliError::Config(
        "could not determine configuration directory; set ELDER_CONFIG_DIR or HOME".into(),
    ))
}

impl ConfigFile {
    /// Load `config.toml` from `path`, returning [`ConfigFile::default`] if the file does not exist.
    pub fn load_from(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Ok(Self::default());
        }
        let raw = fs::read_to_string(path).map_err(|e| {
            CliError::Config(format!("failed to read config '{}': {e}", path.display()))
        })?;
        toml::from_str(&raw).map_err(|e| {
            CliError::Config(format!("failed to parse config '{}': {e}", path.display()))
        })
    }

    /// Persist `config.toml` atomically-safe to `path`, creating parent directories as needed.
    pub fn save_to(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| {
                CliError::Config(format!(
                    "failed to create config directory '{}': {e}",
                    parent.display()
                ))
            })?;
        }
        let encoded = toml::to_string_pretty(self)
            .map_err(|e| CliError::Config(format!("failed to serialize config: {e}")))?;
        fs::write(path, encoded).map_err(|e| {
            CliError::Config(format!("failed to write config '{}': {e}", path.display()))
        })?;
        set_restrictive_permissions(path);
        Ok(())
    }

    /// Look up a profile by name.
    pub fn profile(&self, name: &str) -> Option<&ProfileConfig> {
        self.profiles.get(name)
    }

    /// Insert or update a non-secret profile entry.
    pub fn upsert_profile(&mut self, name: impl Into<String>, profile: ProfileConfig) {
        self.profiles.insert(name.into(), profile);
    }

    /// Resolve effective runtime settings from CLI overrides, environment variables, and config file.
    pub fn resolve<F>(
        &self,
        profile_override: Option<&str>,
        url_override: Option<&str>,
        timeout_override: Option<u64>,
        output_override: Option<OutputFormat>,
        stdout_is_tty: bool,
        env_lookup: F,
    ) -> Result<ResolvedSettings>
    where
        F: Fn(&str) -> Option<String>,
    {
        let profile_name = profile_override
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned)
            .or_else(|| non_empty(env_lookup("ELDER_PROFILE")))
            .unwrap_or_else(|| {
                if self.default_profile.trim().is_empty() {
                    DEFAULT_PROFILE_NAME.to_string()
                } else {
                    self.default_profile.clone()
                }
            });

        let profile_cfg = self.profile(&profile_name);

        let url = url_override
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned)
            .or_else(|| non_empty(env_lookup("ELDER_API_URL")))
            .or_else(|| {
                profile_cfg
                    .map(|p| p.url.trim().to_string())
                    .filter(|s| !s.is_empty())
            })
            .unwrap_or_else(|| DEFAULT_API_URL.to_string());

        let timeout_secs = if let Some(t) = timeout_override {
            t
        } else if let Some(raw) = non_empty(env_lookup("ELDER_TIMEOUT_SECS")) {
            raw.parse::<u64>()
                .map_err(|e| CliError::Config(format!("invalid ELDER_TIMEOUT_SECS {raw:?}: {e}")))?
        } else if let Some(t) = profile_cfg.and_then(|p| p.timeout_secs) {
            t
        } else {
            DEFAULT_TIMEOUT_SECS
        };

        if timeout_secs == 0 || timeout_secs > 3600 {
            return Err(CliError::Config(format!(
                "timeout must be between 1 and 3600 seconds, got {timeout_secs}"
            )));
        }

        let output = if let Some(fmt) = output_override {
            fmt
        } else if let Some(raw) = non_empty(env_lookup("ELDER_OUTPUT")) {
            raw.parse::<OutputFormat>()?
        } else if let Some(fmt) = profile_cfg.and_then(|p| p.default_output) {
            fmt
        } else if stdout_is_tty {
            OutputFormat::Table
        } else {
            OutputFormat::Json
        };

        Ok(ResolvedSettings {
            profile: profile_name,
            url,
            timeout_secs,
            output,
        })
    }
}

fn non_empty(val: Option<String>) -> Option<String> {
    val.map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

#[cfg(unix)]
fn set_restrictive_permissions(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
}

#[cfg(not(unix))]
fn set_restrictive_permissions(_path: &Path) {}

#[cfg(test)]
mod tests {
    use tempfile::TempDir;

    use super::*;

    #[test]
    fn round_trips_non_secret_profiles_on_disk() {
        let tmp = TempDir::new().unwrap();
        let cfg_path = tmp.path().join("elder").join("config.toml");

        let mut cfg = ConfigFile::default();
        cfg.upsert_profile(
            "prod",
            ProfileConfig {
                url: "https://elder.example.com".into(),
                default_output: Some(OutputFormat::Yaml),
                timeout_secs: Some(60),
            },
        );
        cfg.default_profile = "prod".into();
        cfg.save_to(&cfg_path).unwrap();

        let raw = fs::read_to_string(&cfg_path).unwrap();
        assert!(!raw.contains("token"));
        assert!(!raw.contains("password"));
        assert!(!raw.contains("api_key"));

        let loaded = ConfigFile::load_from(&cfg_path).unwrap();
        assert_eq!(loaded, cfg);

        let resolved = loaded
            .resolve(None, None, None, None, false, |_| None)
            .unwrap();
        assert_eq!(resolved.profile, "prod");
        assert_eq!(resolved.url, "https://elder.example.com");
        assert_eq!(resolved.timeout_secs, 60);
        assert_eq!(resolved.output, OutputFormat::Yaml);
    }

    #[test]
    fn cli_flags_and_env_override_profile_settings() {
        let mut cfg = ConfigFile::default();
        cfg.upsert_profile(
            "default",
            ProfileConfig {
                url: "https://profile.example.com".into(),
                default_output: Some(OutputFormat::Table),
                timeout_secs: Some(45),
            },
        );

        let from_env = cfg
            .resolve(None, None, None, None, true, |k| match k {
                "ELDER_API_URL" => Some("https://env.example.com".into()),
                "ELDER_TIMEOUT_SECS" => Some("15".into()),
                _ => None,
            })
            .unwrap();
        assert_eq!(from_env.url, "https://env.example.com");
        assert_eq!(from_env.timeout_secs, 15);
        assert_eq!(from_env.output, OutputFormat::Table);

        let from_flags = cfg
            .resolve(
                None,
                Some("https://flag.example.com"),
                Some(90),
                Some(OutputFormat::Json),
                true,
                |k| match k {
                    "ELDER_API_URL" => Some("https://env.example.com".into()),
                    _ => None,
                },
            )
            .unwrap();
        assert_eq!(from_flags.url, "https://flag.example.com");
        assert_eq!(from_flags.timeout_secs, 90);
        assert_eq!(from_flags.output, OutputFormat::Json);
    }

    #[test]
    fn defaults_to_json_when_piped_and_table_when_tty() {
        let cfg = ConfigFile::default();
        let piped = cfg
            .resolve(None, None, None, None, false, |_| None)
            .unwrap();
        assert_eq!(piped.output, OutputFormat::Json);

        let tty = cfg.resolve(None, None, None, None, true, |_| None).unwrap();
        assert_eq!(tty.output, OutputFormat::Table);
    }
}
