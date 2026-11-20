//! Short-lived HS256 `service_worker` JWT minting and static token fallback.

use chrono::{Duration, Utc};
use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use serde::{Deserialize, Serialize};

use crate::error::{CommonError, Result};

/// Hard cap enforced by `elder-api` on `service_worker` JWT lifetime (seconds).
pub const MAX_SERVICE_WORKER_TTL_SECONDS: i64 = 600;

/// Default short-lived JWT lifetime minted per request/window (seconds).
pub const DEFAULT_SERVICE_WORKER_TTL_SECONDS: i64 = 300;

/// Minimum length enforced for HMAC signing secrets to reject trivial misconfigurations.
pub const MIN_JWT_SECRET_BYTES: usize = 16;

/// Claims included in every Elder `service_worker` JWT.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ServiceWorkerClaims {
    /// Service identity subject (e.g. `"svc-scanning"`, `"svc-integrations"`).
    pub sub: String,
    /// Fixed token discriminator validated by `elder-api`.
    #[serde(rename = "type")]
    pub token_type: String,
    /// Target tenant (`"system"` for cross-tenant platform workers, or positive tenant id).
    pub tenant: String,
    /// Explicitly requested scopes (validated against per-`sub` allowlist server-side).
    pub scope: Vec<String>,
    /// Issuer claim matching `JWT_ISSUER`.
    pub iss: String,
    /// Audience claim matching `JWT_AUDIENCE`.
    pub aud: String,
    /// Issued-at Unix timestamp (seconds).
    pub iat: i64,
    /// Expiration Unix timestamp (seconds).
    pub exp: i64,
}

/// Configuration used when minting `service_worker` HS256 JWTs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServiceAuthConfig {
    /// Subject (`"svc-scanning"` or `"svc-integrations"`).
    pub sub: String,
    /// Tenant claim (`"system"` or positive numeric string).
    pub tenant: String,
    /// Granted scope list.
    pub scopes: Vec<String>,
    /// Expected JWT issuer (`JWT_ISSUER`).
    pub issuer: String,
    /// Expected JWT audience (`JWT_AUDIENCE`).
    pub audience: String,
    /// Token lifetime in seconds (`1..=600`).
    pub ttl_seconds: i64,
}

impl ServiceAuthConfig {
    /// Validate configuration invariants before signing tokens.
    pub fn validate(&self) -> Result<()> {
        if self.sub.trim().is_empty() {
            return Err(CommonError::Auth("service subject cannot be empty".into()));
        }
        if self.scopes.is_empty() || self.scopes.iter().any(|s| s.trim().is_empty()) {
            return Err(CommonError::Auth(
                "at least one non-empty scope is required".into(),
            ));
        }
        let tenant = self.tenant.trim();
        let valid_numeric = tenant.chars().all(|c| c.is_ascii_digit())
            && tenant.parse::<u64>().is_ok_and(|n| n > 0);
        if tenant != "system" && !valid_numeric {
            return Err(CommonError::Config(format!(
                "ELDER_TENANT_ID must be \"system\" or a positive integer, got {tenant:?}"
            )));
        }
        if self.issuer.trim().is_empty() || self.audience.trim().is_empty() {
            return Err(CommonError::Config(
                "JWT_ISSUER and JWT_AUDIENCE must not be empty".into(),
            ));
        }
        if self.ttl_seconds <= 0 || self.ttl_seconds > MAX_SERVICE_WORKER_TTL_SECONDS {
            return Err(CommonError::Config(format!(
                "service_worker TTL must be 1..={MAX_SERVICE_WORKER_TTL_SECONDS} seconds, got {}",
                self.ttl_seconds
            )));
        }
        Ok(())
    }
}

#[derive(Clone)]
enum AuthSource {
    Jwt {
        encoding_key: EncodingKey,
        config: ServiceAuthConfig,
    },
    StaticToken(String),
}

/// Provides `Authorization: Bearer <token>` credentials for `elder-api`.
#[derive(Clone)]
pub struct ServiceTokenProvider {
    source: AuthSource,
}

impl std::fmt::Debug for ServiceTokenProvider {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match &self.source {
            AuthSource::Jwt { config, .. } => f
                .debug_struct("ServiceTokenProvider::Jwt")
                .field("sub", &config.sub)
                .field("tenant", &config.tenant)
                .field("scopes", &config.scopes)
                .finish(),
            AuthSource::StaticToken(_) => f.write_str("ServiceTokenProvider::StaticToken(***)"),
        }
    }
}

impl ServiceTokenProvider {
    /// Load authentication configuration from process environment variables.
    ///
    /// Checks `JWT_SECRET_KEY` first, then `SECRET_KEY`, and finally falls back to
    /// `ELDER_API_TOKEN` for standalone runners without the shared signing secret.
    pub fn from_env(sub: impl Into<String>, scopes: Vec<String>) -> Result<Self> {
        Self::from_env_lookup(sub, scopes, |name| std::env::var(name).ok())
    }

    /// Construct from a custom env lookup function (used in deterministic tests).
    pub fn from_env_lookup<F>(
        sub: impl Into<String>,
        scopes: Vec<String>,
        lookup: F,
    ) -> Result<Self>
    where
        F: Fn(&str) -> Option<String>,
    {
        let secret_opt =
            non_empty(lookup("JWT_SECRET_KEY")).or_else(|| non_empty(lookup("SECRET_KEY")));
        let static_opt = non_empty(lookup("ELDER_API_TOKEN"));

        if let Some(secret) = secret_opt {
            if secret.len() < MIN_JWT_SECRET_BYTES {
                return Err(CommonError::Auth(format!(
                    "JWT signing secret is too short ({} bytes; minimum {MIN_JWT_SECRET_BYTES})",
                    secret.len()
                )));
            }
            let tenant =
                non_empty(lookup("ELDER_TENANT_ID")).unwrap_or_else(|| "system".to_string());
            let issuer = non_empty(lookup("JWT_ISSUER")).unwrap_or_else(|| "elder-api".to_string());
            let audience =
                non_empty(lookup("JWT_AUDIENCE")).unwrap_or_else(|| "elder-api".to_string());
            let ttl_seconds = non_empty(lookup("SERVICE_WORKER_JWT_TTL_SECONDS"))
                .map(|raw| {
                    raw.parse::<i64>().map_err(|e| {
                        CommonError::Config(format!(
                            "invalid SERVICE_WORKER_JWT_TTL_SECONDS {raw:?}: {e}"
                        ))
                    })
                })
                .transpose()?
                .unwrap_or(DEFAULT_SERVICE_WORKER_TTL_SECONDS);

            let config = ServiceAuthConfig {
                sub: sub.into(),
                tenant,
                scopes,
                issuer,
                audience,
                ttl_seconds,
            };
            config.validate()?;
            return Ok(Self {
                source: AuthSource::Jwt {
                    encoding_key: EncodingKey::from_secret(secret.as_bytes()),
                    config,
                },
            });
        }

        if let Some(static_token) = static_opt {
            return Ok(Self {
                source: AuthSource::StaticToken(static_token),
            });
        }

        Err(CommonError::Auth(
            "authentication unconfigured: set JWT_SECRET_KEY (or SECRET_KEY) or ELDER_API_TOKEN"
                .into(),
        ))
    }

    /// Return a bearer token ready for an API request.
    pub fn token(&self) -> Result<String> {
        match &self.source {
            AuthSource::StaticToken(tok) => Ok(tok.clone()),
            AuthSource::Jwt {
                encoding_key,
                config,
            } => {
                let now = Utc::now();
                let exp = now + Duration::seconds(config.ttl_seconds);
                let claims = ServiceWorkerClaims {
                    sub: config.sub.clone(),
                    token_type: "service_worker".to_string(),
                    tenant: config.tenant.clone(),
                    scope: config.scopes.clone(),
                    iss: config.issuer.clone(),
                    aud: config.audience.clone(),
                    iat: now.timestamp(),
                    exp: exp.timestamp(),
                };
                encode(&Header::new(Algorithm::HS256), &claims, encoding_key)
                    .map_err(|e| CommonError::Auth(format!("failed to sign service JWT: {e}")))
            }
        }
    }

    /// Verify token generation succeeds and returns a non-empty token.
    pub fn validate(&self) -> Result<()> {
        let tok = self.token()?;
        if tok.trim().is_empty() {
            return Err(CommonError::Auth("minted empty token".into()));
        }
        Ok(())
    }
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
    use jsonwebtoken::{decode, DecodingKey, Validation};
    use std::collections::HashMap;

    fn lookup_from(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
            .collect();
        move |k: &str| map.get(k).cloned()
    }

    #[test]
    fn mints_hs256_jwt_with_expected_claims_and_ttl() {
        let key = "x".repeat(32);
        let provider = ServiceTokenProvider::from_env_lookup(
            "svc-scanning",
            vec!["discovery:read".into(), "sbom:write".into()],
            lookup_from(&[("JWT_SECRET_KEY", &key)]),
        )
        .expect("provider should build");

        let jwt_str = provider.token().expect("token should sign");
        let mut validation = Validation::new(Algorithm::HS256);
        validation.set_issuer(&["elder-api"]);
        validation.set_audience(&["elder-api"]);
        let decoded = decode::<ServiceWorkerClaims>(
            &jwt_str,
            &DecodingKey::from_secret(key.as_bytes()),
            &validation,
        )
        .expect("token should decode");

        assert_eq!(decoded.claims.sub, "svc-scanning");
        assert_eq!(decoded.claims.token_type, "service_worker");
        assert_eq!(decoded.claims.tenant, "system");
        assert_eq!(decoded.claims.scope, vec!["discovery:read", "sbom:write"]);
        let ttl = decoded.claims.exp - decoded.claims.iat;
        assert_eq!(ttl, 300);
        assert!(ttl <= MAX_SERVICE_WORKER_TTL_SECONDS);
    }

    #[test]
    fn falls_back_to_secret_key_and_then_static_token() {
        let key = "y".repeat(32);
        let secret_fallback = ServiceTokenProvider::from_env_lookup(
            "svc-integrations",
            vec!["entities:read".into()],
            lookup_from(&[
                ("JWT_SECRET_KEY", "   "),
                ("SECRET_KEY", &key),
                ("ELDER_TENANT_ID", "42"),
            ]),
        )
        .expect("SECRET_KEY fallback should build");
        let jwt_str = secret_fallback.token().expect("should sign");
        let mut val = Validation::new(Algorithm::HS256);
        val.set_audience(&["elder-api"]);
        let decoded = decode::<ServiceWorkerClaims>(
            &jwt_str,
            &DecodingKey::from_secret(key.as_bytes()),
            &val,
        )
        .expect("should verify");
        assert_eq!(decoded.claims.tenant, "42");

        let static_provider = ServiceTokenProvider::from_env_lookup(
            "svc-scanning",
            vec!["discovery:read".into()],
            lookup_from(&[("ELDER_API_TOKEN", "static-runner-token")]),
        )
        .expect("ELDER_API_TOKEN fallback should build");
        assert_eq!(
            static_provider.token().expect("static token"),
            "static-runner-token"
        );
    }

    #[test]
    fn fails_loudly_when_auth_missing_or_secret_too_short_or_ttl_exceeded() {
        let missing = ServiceTokenProvider::from_env_lookup(
            "svc-scanning",
            vec!["discovery:read".into()],
            lookup_from(&[]),
        );
        assert!(matches!(missing, Err(CommonError::Auth(_))));

        let short_secret = ServiceTokenProvider::from_env_lookup(
            "svc-scanning",
            vec!["discovery:read".into()],
            lookup_from(&[("JWT_SECRET_KEY", "too-short")]),
        );
        assert!(matches!(short_secret, Err(CommonError::Auth(_))));

        let key = "x".repeat(32);
        let over_ttl = ServiceTokenProvider::from_env_lookup(
            "svc-scanning",
            vec!["discovery:read".into()],
            lookup_from(&[
                ("JWT_SECRET_KEY", &key),
                ("SERVICE_WORKER_JWT_TTL_SECONDS", "601"),
            ]),
        );
        assert!(matches!(over_ttl, Err(CommonError::Config(_))));
    }
}
