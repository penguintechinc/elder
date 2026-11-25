//! Platform secure credential storage (`keyring`), env-var auth resolution, and secret masking.
//!
//! **Security invariants (`critical-rules.md` & `client.md`)**:
//! - Credentials are NEVER accepted as CLI arguments (`--token`, `--password`, `--api-key`).
//! - Credentials are NEVER written to plaintext files on disk.
//! - Credentials are resolved from environment variables or OS secure storage (`keyring`).
//! - Displayed token previews are always masked via [`mask_secret`].

use std::collections::HashMap;
use std::sync::Mutex;

use elder_sdk::Auth;
use tracing::debug;

use crate::error::{CliError, Result};

/// Service namespace used inside the OS secure credential store.
pub const KEYRING_SERVICE: &str = "elder-cli";

/// Kind of credential stored for a profile in the OS secure store.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SecretKind {
    /// Bearer access JWT.
    AccessToken,
    /// Long-lived refresh token.
    RefreshToken,
    /// Elder API key (`X-API-Key`).
    ApiKey,
}

impl SecretKind {
    fn suffix(self) -> &'static str {
        match self {
            Self::AccessToken => "access_token",
            Self::RefreshToken => "refresh_token",
            Self::ApiKey => "api_key",
        }
    }
}

/// Source from which active authentication credentials were resolved.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthSource {
    /// No credentials available.
    None,
    /// `ELDER_API_TOKEN` or `ELDER_TOKEN` environment variable.
    EnvBearerToken,
    /// `ELDER_API_KEY` environment variable.
    EnvApiKey,
    /// Platform secure keyring access token.
    KeyringBearerToken,
    /// Platform secure keyring API key.
    KeyringApiKey,
}

impl AuthSource {
    /// Human-readable label for display in `elder auth whoami`.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::EnvBearerToken => "env:ELDER_API_TOKEN",
            Self::EnvApiKey => "env:ELDER_API_KEY",
            Self::KeyringBearerToken => "keyring:access_token",
            Self::KeyringApiKey => "keyring:api_key",
        }
    }
}

/// Resolved authentication credentials paired with their provenance and masked preview.
#[derive(Debug, Clone)]
pub struct ResolvedAuth {
    /// SDK auth enum passed to [`elder_sdk::ClientConfig`].
    pub auth: Auth,
    /// Where the credential came from.
    pub source: AuthSource,
    /// Safe masked preview for display (never the raw secret).
    pub masked_preview: Option<String>,
}

/// Abstraction over secure credential storage (backed by [`KeyringStore`] in production).
pub trait CredentialStore: Send + Sync {
    /// Store `secret` for `(profile, kind)`.
    fn set_secret(&self, profile: &str, kind: SecretKind, secret: &str) -> Result<()>;
    /// Retrieve the stored secret for `(profile, kind)`, returning `Ok(None)` when absent.
    fn get_secret(&self, profile: &str, kind: SecretKind) -> Result<Option<String>>;
    /// Delete the stored secret for `(profile, kind)` if present.
    fn delete_secret(&self, profile: &str, kind: SecretKind) -> Result<()>;
}

/// OS secure credential store backed by the `keyring` crate.
#[derive(Debug, Default, Clone)]
pub struct KeyringStore;

impl KeyringStore {
    fn entry(profile: &str, kind: SecretKind) -> Result<keyring::Entry> {
        let account = format!("{}:{}", profile.trim(), kind.suffix());
        keyring::Entry::new(KEYRING_SERVICE, &account)
            .map_err(|e| CliError::Keyring(format!("failed to initialize keyring entry: {e}")))
    }
}

impl CredentialStore for KeyringStore {
    fn set_secret(&self, profile: &str, kind: SecretKind, secret: &str) -> Result<()> {
        let entry = Self::entry(profile, kind)?;
        entry
            .set_password(secret)
            .map_err(|e| CliError::Keyring(format!("failed to store credential in keyring: {e}")))
    }

    fn get_secret(&self, profile: &str, kind: SecretKind) -> Result<Option<String>> {
        let entry = Self::entry(profile, kind)?;
        match entry.get_password() {
            Ok(secret) => {
                let trimmed = secret.trim().to_string();
                if trimmed.is_empty() {
                    Ok(None)
                } else {
                    Ok(Some(trimmed))
                }
            }
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(err) => {
                // On headless/container hosts without an active keyring session, degrade cleanly
                // so env-var and unauthenticated commands continue working.
                debug!(error = %err, profile = %profile, "keyring unavailable during credential read");
                Ok(None)
            }
        }
    }

    fn delete_secret(&self, profile: &str, kind: SecretKind) -> Result<()> {
        let entry = Self::entry(profile, kind)?;
        match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(err) => Err(CliError::Keyring(format!(
                "failed to remove credential from keyring: {err}"
            ))),
        }
    }
}

/// In-memory credential store for deterministic unit and integration tests.
#[derive(Debug, Default)]
pub struct MemoryCredentialStore {
    entries: Mutex<HashMap<(String, &'static str), String>>,
}

impl MemoryCredentialStore {
    /// Construct an empty in-memory credential store.
    pub fn new() -> Self {
        Self::default()
    }
}

impl CredentialStore for MemoryCredentialStore {
    fn set_secret(&self, profile: &str, kind: SecretKind, secret: &str) -> Result<()> {
        let mut guard = self
            .entries
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        guard.insert((profile.to_string(), kind.suffix()), secret.to_string());
        Ok(())
    }

    fn get_secret(&self, profile: &str, kind: SecretKind) -> Result<Option<String>> {
        let guard = self
            .entries
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        Ok(guard.get(&(profile.to_string(), kind.suffix())).cloned())
    }

    fn delete_secret(&self, profile: &str, kind: SecretKind) -> Result<()> {
        let mut guard = self
            .entries
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        guard.remove(&(profile.to_string(), kind.suffix()));
        Ok(())
    }
}

/// Mask a secret token or API key so raw credentials are never emitted to stdout/stderr/logs.
///
/// For values longer than 8 characters, preserves at most the first 4 and last 4 characters
/// separated by `****`. Shorter values are fully replaced with `****`.
pub fn mask_secret(secret: &str) -> String {
    let trimmed = secret.trim();
    let chars: Vec<char> = trimmed.chars().collect();
    if chars.len() <= 8 {
        return "****".to_string();
    }
    let prefix: String = chars[..4].iter().collect();
    let suffix: String = chars[chars.len() - 4..].iter().collect();
    format!("{prefix}****{suffix}")
}

/// Resolve active credentials for `profile` from environment variables or `store`.
///
/// Precedence:
/// 1. `ELDER_API_TOKEN` (or `ELDER_TOKEN` compatibility alias) -> `Auth::Bearer`
/// 2. `ELDER_API_KEY` -> `Auth::ApiKey`
/// 3. Keyring `access_token` -> `Auth::Bearer`
/// 4. Keyring `api_key` -> `Auth::ApiKey`
/// 5. `Auth::None`
pub fn resolve_auth<F>(
    profile: &str,
    store: &dyn CredentialStore,
    env_lookup: F,
) -> Result<ResolvedAuth>
where
    F: Fn(&str) -> Option<String>,
{
    if let Some(token) =
        non_empty(env_lookup("ELDER_API_TOKEN")).or_else(|| non_empty(env_lookup("ELDER_TOKEN")))
    {
        return Ok(ResolvedAuth {
            masked_preview: Some(mask_secret(&token)),
            auth: Auth::Bearer(token),
            source: AuthSource::EnvBearerToken,
        });
    }

    if let Some(api_key) = non_empty(env_lookup("ELDER_API_KEY")) {
        return Ok(ResolvedAuth {
            masked_preview: Some(mask_secret(&api_key)),
            auth: Auth::ApiKey(api_key),
            source: AuthSource::EnvApiKey,
        });
    }

    if let Some(token) = store.get_secret(profile, SecretKind::AccessToken)? {
        return Ok(ResolvedAuth {
            masked_preview: Some(mask_secret(&token)),
            auth: Auth::Bearer(token),
            source: AuthSource::KeyringBearerToken,
        });
    }

    if let Some(api_key) = store.get_secret(profile, SecretKind::ApiKey)? {
        return Ok(ResolvedAuth {
            masked_preview: Some(mask_secret(&api_key)),
            auth: Auth::ApiKey(api_key),
            source: AuthSource::KeyringApiKey,
        });
    }

    Ok(ResolvedAuth {
        auth: Auth::None,
        source: AuthSource::None,
        masked_preview: None,
    })
}

/// Clear all stored credentials for `profile` from `store`.
pub fn clear_profile_secrets(profile: &str, store: &dyn CredentialStore) -> Result<()> {
    store.delete_secret(profile, SecretKind::AccessToken)?;
    store.delete_secret(profile, SecretKind::RefreshToken)?;
    store.delete_secret(profile, SecretKind::ApiKey)?;
    Ok(())
}

fn non_empty(val: Option<String>) -> Option<String> {
    val.map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn masks_short_and_long_secrets_without_leaking_middle() {
        assert_eq!(mask_secret(""), "****");
        assert_eq!(mask_secret("short"), "****");
        assert_eq!(mask_secret("12345678"), "****");
        assert_eq!(mask_secret("tok_super_secret_1234"), "tok_****1234");
        assert!(!mask_secret("tok_super_secret_1234").contains("super_secret"));
    }

    #[test]
    fn resolves_env_before_keyring() {
        let store = MemoryCredentialStore::new();
        store
            .set_secret("default", SecretKind::AccessToken, "keyring_tok_9999")
            .unwrap();

        let from_env = resolve_auth("default", &store, |k| match k {
            "ELDER_API_TOKEN" => Some("env_secret_token_1234".into()),
            _ => None,
        })
        .unwrap();
        assert_eq!(from_env.source, AuthSource::EnvBearerToken);
        assert_eq!(from_env.masked_preview.as_deref(), Some("env_****1234"));
        assert!(matches!(from_env.auth, Auth::Bearer(ref t) if t == "env_secret_token_1234"));

        let from_env_key = resolve_auth("default", &store, |k| match k {
            "ELDER_API_KEY" => Some("eld_apikey_abcd5678".into()),
            _ => None,
        })
        .unwrap();
        assert_eq!(from_env_key.source, AuthSource::EnvApiKey);
        assert!(matches!(from_env_key.auth, Auth::ApiKey(ref k) if k == "eld_apikey_abcd5678"));

        let from_keyring = resolve_auth("default", &store, |_| None).unwrap();
        assert_eq!(from_keyring.source, AuthSource::KeyringBearerToken);
        assert_eq!(from_keyring.masked_preview.as_deref(), Some("keyr****9999"));
    }

    #[test]
    fn clears_all_profile_secrets() {
        let store = MemoryCredentialStore::new();
        store
            .set_secret("work", SecretKind::AccessToken, "acc_123456789")
            .unwrap();
        store
            .set_secret("work", SecretKind::RefreshToken, "ref_123456789")
            .unwrap();
        store
            .set_secret("work", SecretKind::ApiKey, "key_123456789")
            .unwrap();

        clear_profile_secrets("work", &store).unwrap();
        assert_eq!(
            store.get_secret("work", SecretKind::AccessToken).unwrap(),
            None
        );
        assert_eq!(
            store.get_secret("work", SecretKind::RefreshToken).unwrap(),
            None
        );
        assert_eq!(store.get_secret("work", SecretKind::ApiKey).unwrap(), None);
    }
}
