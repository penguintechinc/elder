//! Shared types, configuration helpers, TLS client builder, and XML/URL utilities.

use std::collections::{BTreeMap, BTreeSet};
use std::time::Duration;

use chrono::Utc;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use thiserror::Error;

const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_ERROR_SNIPPET_BYTES: usize = 512;

/// Unified error type for cloud discovery and IAM sync operations.
#[derive(Debug, Error)]
pub enum CloudError {
    /// Missing or invalid provider configuration.
    #[error("configuration error: {0}")]
    Config(String),

    /// Input validation failure.
    #[error("validation error: {0}")]
    Validation(String),

    /// Authentication or credential signing failure.
    #[error("authentication error: {0}")]
    Auth(String),

    /// Non-success HTTP response from an upstream cloud or hypervisor API.
    #[error("HTTP {status} from upstream API ({endpoint}): {body}")]
    Http {
        /// HTTP status code.
        status: u16,
        /// Sanitized endpoint path/host (no query secrets).
        endpoint: String,
        /// Truncated response body snippet.
        body: String,
    },

    /// Network/TLS transport failure.
    #[error("transport error: {0}")]
    Transport(#[from] reqwest::Error),

    /// Response payload parsing error (XML/JSON).
    #[error("parse error: {0}")]
    Parse(String),

    /// Unexpected runtime execution error.
    #[error("execution error: {0}")]
    Execution(String),
}

/// Convenience result alias for `elder-int-cloud`.
pub type CloudResult<T> = std::result::Result<T, CloudError>;

/// Relationship attached directly to a normalized discovered resource.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResourceRelationship {
    pub target_external_id: String,
    pub target_kind: String,
    pub edge_type: String,
}

/// Top-level dependency edge emitted in `/discovery/{job_id}/complete`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NormalizedDependency {
    pub source_external_id: String,
    pub source_kind: String,
    pub target_external_id: String,
    pub target_kind: String,
    pub dependency_type: String,
    pub edge_type: String,
    pub metadata: Value,
}

/// Normalized discovered resource matching Elder's discovery ingestion contract.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NormalizedResource {
    pub resource_id: String,
    pub external_id: String,
    pub resource_type: String,
    pub category: String,
    pub name: String,
    pub provider: String,
    pub region: Option<String>,
    pub tags: BTreeMap<String, String>,
    pub metadata: Value,
    pub relationships: Vec<ResourceRelationship>,
    pub discovered_at: String,
}

impl NormalizedResource {
    /// Construct a new normalized resource stamped with the current UTC time.
    pub fn new(
        resource_id: impl Into<String>,
        external_id: impl Into<String>,
        resource_type: impl Into<String>,
        category: impl Into<String>,
        name: impl Into<String>,
        provider: impl Into<String>,
    ) -> Self {
        Self {
            resource_id: resource_id.into(),
            external_id: external_id.into(),
            resource_type: resource_type.into(),
            category: category.into(),
            name: name.into(),
            provider: provider.into(),
            region: None,
            tags: BTreeMap::new(),
            metadata: json!({}),
            relationships: Vec::new(),
            discovered_at: Utc::now().to_rfc3339(),
        }
    }

    /// Add a relationship link to this resource.
    pub fn add_relationship(
        &mut self,
        target_external_id: impl Into<String>,
        target_kind: impl Into<String>,
        edge_type: impl Into<String>,
    ) {
        self.relationships.push(ResourceRelationship {
            target_external_id: target_external_id.into(),
            target_kind: target_kind.into(),
            edge_type: edge_type.into(),
        });
    }
}

/// Collected discovery output from a provider run.
#[derive(Debug, Clone, Default)]
pub struct DiscoveryCollection {
    pub resources: Vec<NormalizedResource>,
    pub dependencies: Vec<NormalizedDependency>,
    pub errors: Vec<String>,
    pub attempted_steps: usize,
    pub succeeded_steps: usize,
}

impl DiscoveryCollection {
    /// Record an edge in both the source resource's `relationships` (if found) and `dependencies`.
    pub fn push_dependency(
        &mut self,
        source_external_id: impl Into<String>,
        source_kind: impl Into<String>,
        target_external_id: impl Into<String>,
        target_kind: impl Into<String>,
        edge_type: impl Into<String>,
    ) {
        let src = source_external_id.into();
        let src_kind = source_kind.into();
        let tgt = target_external_id.into();
        let tgt_kind = target_kind.into();
        let edge = edge_type.into();
        if src.is_empty() || tgt.is_empty() {
            return;
        }
        let duplicate = self.dependencies.iter().any(|d| {
            d.source_external_id == src && d.target_external_id == tgt && d.dependency_type == edge
        });
        if !duplicate {
            self.dependencies.push(NormalizedDependency {
                source_external_id: src,
                source_kind: src_kind,
                target_external_id: tgt,
                target_kind: tgt_kind,
                dependency_type: edge.clone(),
                edge_type: edge,
                metadata: json!({ "linked_by": "discovery" }),
            });
        }
    }

    /// Mark one discovery sub-step as succeeded.
    pub fn mark_step_ok(&mut self) {
        self.attempted_steps += 1;
        self.succeeded_steps += 1;
    }

    /// Mark one discovery sub-step as failed with a diagnostic message.
    pub fn mark_step_err(&mut self, msg: impl Into<String>) {
        self.attempted_steps += 1;
        self.errors.push(msg.into());
    }

    /// Build a structured summary JSON object for `/complete`.
    pub fn summary(&self, provider: &str) -> Value {
        let mut by_category: BTreeMap<String, usize> = BTreeMap::new();
        let mut by_type: BTreeMap<String, usize> = BTreeMap::new();
        for res in &self.resources {
            *by_category.entry(res.category.clone()).or_insert(0) += 1;
            *by_type.entry(res.resource_type.clone()).or_insert(0) += 1;
        }
        json!({
            "provider": provider,
            "resources_count": self.resources.len(),
            "dependencies_count": self.dependencies.len(),
            "attempted_steps": self.attempted_steps,
            "succeeded_steps": self.succeeded_steps,
            "error_count": self.errors.len(),
            "by_category": by_category,
            "by_type": by_type,
        })
    }
}

/// Normalized IAM collection returned by IAM sync providers.
#[derive(Debug, Clone, Default)]
pub struct IamCollection {
    pub users: Vec<Value>,
    pub groups: Vec<Value>,
    pub roles: Vec<Value>,
    pub memberships: Vec<Value>,
    pub errors: Vec<String>,
    pub attempted_steps: usize,
    pub succeeded_steps: usize,
}

impl IamCollection {
    /// Total count of normalized IAM items synced.
    pub fn total_items(&self) -> usize {
        self.users.len() + self.groups.len() + self.roles.len() + self.memberships.len()
    }

    /// Record a successful IAM fetch step.
    pub fn mark_step_ok(&mut self) {
        self.attempted_steps += 1;
        self.succeeded_steps += 1;
    }

    /// Record a failed IAM fetch step.
    pub fn mark_step_err(&mut self, msg: impl Into<String>) {
        self.attempted_steps += 1;
        self.errors.push(msg.into());
    }

    /// Build summary JSON for `/iam/{provider_id}/apply`.
    pub fn summary(&self, provider_type: &str) -> Value {
        json!({
            "provider_type": provider_type,
            "users_count": self.users.len(),
            "groups_count": self.groups.len(),
            "roles_count": self.roles.len(),
            "memberships_count": self.memberships.len(),
            "attempted_steps": self.attempted_steps,
            "succeeded_steps": self.succeeded_steps,
            "error_count": self.errors.len(),
        })
    }
}

/// Pure-rustls TLS options for upstream cloud / cluster / hypervisor clients.
#[derive(Clone, Default)]
pub struct TlsOptions {
    pub verify_ssl: bool,
    pub ca_cert_pem: Option<String>,
    pub identity_pem: Option<String>,
}

impl std::fmt::Debug for TlsOptions {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TlsOptions")
            .field("verify_ssl", &self.verify_ssl)
            .field("has_ca_cert_pem", &self.ca_cert_pem.is_some())
            .field("has_identity_pem", &self.identity_pem.is_some())
            .finish()
    }
}

/// Build an async `reqwest::Client` using `rustls-tls` exclusively.
pub fn build_http_client(tls: &TlsOptions) -> CloudResult<Client> {
    let mut builder = Client::builder()
        .connect_timeout(DEFAULT_CONNECT_TIMEOUT)
        .timeout(DEFAULT_REQUEST_TIMEOUT)
        .user_agent("elder-int-cloud/0.1")
        .tls_built_in_root_certs(true);

    if !tls.verify_ssl {
        builder = builder.danger_accept_invalid_certs(true);
    }

    if let Some(ca_pem) = tls
        .ca_cert_pem
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        let certs = reqwest::Certificate::from_pem_bundle(ca_pem.as_bytes())
            .map_err(|e| CloudError::Config(format!("invalid ca_cert_pem certificate: {e}")))?;
        if certs.is_empty() {
            return Err(CloudError::Config(
                "invalid ca_cert_pem: no PEM CERTIFICATE blocks found".into(),
            ));
        }
        for cert in certs {
            builder = builder.add_root_certificate(cert);
        }
    }

    if let Some(id_pem) = tls
        .identity_pem
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        let identity = reqwest::Identity::from_pem(id_pem.as_bytes())
            .map_err(|e| CloudError::Config(format!("invalid client identity PEM: {e}")))?;
        builder = builder.identity(identity);
    }

    builder
        .build()
        .map_err(|e| CloudError::Config(format!("failed building HTTP client: {e}")))
}

/// Merge top-level fields with `config` / `config_json` (dict or JSON string).
pub fn extract_config(raw: &Value) -> Map<String, Value> {
    let mut merged = Map::new();
    if let Some(obj) = raw.as_object() {
        for (k, v) in obj {
            if k != "config" && k != "config_json" {
                merged.insert(k.clone(), v.clone());
            }
        }
        merge_json_map(&mut merged, obj.get("config"));
        merge_json_map(&mut merged, obj.get("config_json"));
    }
    merged
}

fn merge_json_map(dst: &mut Map<String, Value>, maybe_val: Option<&Value>) {
    let Some(val) = maybe_val else {
        return;
    };
    if let Some(obj) = val.as_object() {
        for (k, v) in obj {
            dst.insert(k.clone(), v.clone());
        }
    } else if let Some(raw_str) = val.as_str() {
        if let Ok(Value::Object(obj)) = serde_json::from_str::<Value>(raw_str) {
            for (k, v) in obj {
                dst.insert(k, v);
            }
        }
    }
}

/// Extract an integer ID from `val[key]` supporting integers and numeric strings.
pub fn extract_id(val: &Value, keys: &[&str]) -> Option<i64> {
    for key in keys {
        if let Some(v) = val.get(*key) {
            if let Some(n) = v.as_i64() {
                return Some(n);
            }
            if let Some(u) = v.as_u64() {
                if let Ok(n) = i64::try_from(u) {
                    return Some(n);
                }
            }
            if let Some(s) = v.as_str() {
                if let Ok(n) = s.trim().parse::<i64>() {
                    return Some(n);
                }
            }
        }
    }
    None
}

/// Extract the first non-empty trimmed string across candidate keys.
pub fn opt_str(cfg: &Map<String, Value>, keys: &[&str]) -> Option<String> {
    for key in keys {
        if let Some(s) = cfg.get(*key).and_then(Value::as_str) {
            let trimmed = s.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }
    None
}

/// Extract a required non-empty string across candidate keys.
pub fn req_str(cfg: &Map<String, Value>, keys: &[&str], label: &str) -> CloudResult<String> {
    opt_str(cfg, keys).ok_or_else(|| CloudError::Config(format!("missing required config {label}")))
}

/// Extract a boolean across candidate keys, falling back to `default`.
pub fn opt_bool(cfg: &Map<String, Value>, keys: &[&str], default: bool) -> bool {
    for key in keys {
        if let Some(v) = cfg.get(*key) {
            if let Some(b) = v.as_bool() {
                return b;
            }
            if let Some(s) = v.as_str() {
                match s.trim().to_ascii_lowercase().as_str() {
                    "true" | "1" | "yes" => return true,
                    "false" | "0" | "no" => return false,
                    _ => {}
                }
            }
        }
    }
    default
}

/// Extract an optional list of non-empty lowercased strings (e.g., `services` filter).
pub fn opt_string_set(cfg: &Map<String, Value>, key: &str) -> BTreeSet<String> {
    let mut set = BTreeSet::new();
    if let Some(arr) = cfg.get(key).and_then(Value::as_array) {
        for item in arr {
            if let Some(s) = item.as_str() {
                let norm = s.trim().to_ascii_lowercase();
                if !norm.is_empty() {
                    set.insert(norm);
                }
            }
        }
    }
    set
}

/// Normalize a base HTTP(S) URL, rejecting non-HTTP schemes and missing hosts.
pub fn normalize_http_base_url(raw: &str, label: &str) -> CloudResult<String> {
    let trimmed = raw.trim().trim_end_matches('/');
    if trimmed.is_empty() {
        return Err(CloudError::Config(format!("{label} must not be empty")));
    }
    let parsed = url::Url::parse(trimmed)
        .map_err(|e| CloudError::Config(format!("invalid {label} URL: {e}")))?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return Err(CloudError::Config(format!(
            "{label} must be an http:// or https:// URL with a host"
        )));
    }
    Ok(trimmed.to_string())
}

/// Join a normalized base URL and relative path cleanly.
pub fn join_url(base: &str, path: &str) -> String {
    let clean_base = base.trim_end_matches('/');
    if path.starts_with('/') {
        format!("{clean_base}{path}")
    } else {
        format!("{clean_base}/{path}")
    }
}

/// Compute the HTTP `Host` header value from a `url::Url` (including non-default port).
pub fn http_host_header(parsed: &url::Url) -> CloudResult<String> {
    let host = parsed
        .host_str()
        .ok_or_else(|| CloudError::Validation("URL missing host".into()))?;
    match parsed.port() {
        Some(port) => Ok(format!("{host}:{port}")),
        None => Ok(host.to_string()),
    }
}

/// Return a sanitized endpoint label (`host + path`, no query/userinfo) for error messages.
pub fn safe_endpoint_label(raw_url: &str) -> String {
    match url::Url::parse(raw_url) {
        Ok(u) => {
            let host = u.host_str().unwrap_or("unknown");
            format!("{host}{}", u.path())
        }
        Err(_) => "<invalid-url>".to_string(),
    }
}

/// Truncate an upstream HTTP error body safely on UTF-8 char boundaries.
pub fn truncate_body(body: &str) -> String {
    if body.len() <= MAX_ERROR_SNIPPET_BYTES {
        return body.trim().to_string();
    }
    let mut end = MAX_ERROR_SNIPPET_BYTES;
    while end > 0 && !body.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}...(truncated)", body[..end].trim())
}

/// Find direct child element text by local XML tag name (namespace-agnostic).
pub fn xml_child_text<'a>(node: roxmltree::Node<'a, 'a>, tag: &str) -> Option<String> {
    node.children()
        .find(|c| c.is_element() && c.tag_name().name() == tag)
        .and_then(|c| c.text())
        .map(|t| t.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Find first direct child element by local XML tag name.
pub fn xml_child<'a>(node: roxmltree::Node<'a, 'a>, tag: &str) -> Option<roxmltree::Node<'a, 'a>> {
    node.children()
        .find(|c| c.is_element() && c.tag_name().name() == tag)
}

/// Find first descendant element by local XML tag name.
pub fn xml_descendant<'a>(
    node: roxmltree::Node<'a, 'a>,
    tag: &str,
) -> Option<roxmltree::Node<'a, 'a>> {
    node.descendants()
        .find(|c| c.is_element() && c.tag_name().name() == tag)
}

/// Find first descendant element's trimmed text by local XML tag name.
pub fn xml_descendant_text<'a>(node: roxmltree::Node<'a, 'a>, tag: &str) -> Option<String> {
    xml_descendant(node, tag)
        .and_then(|c| c.text())
        .map(|t| t.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Parse an XML `tagSet` node into a flat tag map and optional `Name` tag value.
pub fn parse_aws_tag_set(node: roxmltree::Node<'_, '_>) -> BTreeMap<String, String> {
    let mut tags = BTreeMap::new();
    if let Some(tag_set) = xml_child(node, "tagSet") {
        for item in tag_set
            .children()
            .filter(|c| c.is_element() && c.tag_name().name() == "item")
        {
            if let Some(key) = xml_child_text(item, "key") {
                let val = xml_child_text(item, "value").unwrap_or_default();
                tags.insert(key, val);
            }
        }
    }
    tags
}

/// Parse a JSON object of string/primitive labels into a flat `BTreeMap<String, String>`.
pub fn parse_json_tags(val: Option<&Value>) -> BTreeMap<String, String> {
    let mut tags = BTreeMap::new();
    let Some(obj) = val.and_then(Value::as_object) else {
        return tags;
    };
    for (k, v) in obj {
        let str_val = match v {
            Value::String(s) => s.clone(),
            Value::Number(n) => n.to_string(),
            Value::Bool(b) => b.to_string(),
            _ => continue,
        };
        tags.insert(k.clone(), str_val);
    }
    tags
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_config_merges_string_and_object_config_json() {
        let job = json!({
            "id": "42",
            "provider": "aws",
            "region": "us-west-2",
            "config_json": r#"{"_organization_id": 7, "region": "eu-west-1"}"#
        });
        let cfg = extract_config(&job);
        assert_eq!(extract_id(&job, &["id"]), Some(42));
        assert_eq!(opt_str(&cfg, &["region"]).as_deref(), Some("eu-west-1"));
        assert_eq!(cfg.get("_organization_id").and_then(Value::as_i64), Some(7));
    }

    #[test]
    fn build_http_client_rejects_corrupt_ca_pem() {
        let tls = TlsOptions {
            verify_ssl: true,
            ca_cert_pem: Some("not-a-valid-pem".to_string()),
            identity_pem: None,
        };
        let err = build_http_client(&tls).expect_err("corrupt PEM must fail");
        assert!(matches!(err, CloudError::Config(_)));
    }

    #[test]
    fn build_http_client_rejects_corrupt_identity_pem() {
        let tls = TlsOptions {
            verify_ssl: true,
            ca_cert_pem: None,
            identity_pem: Some("corrupt-identity".to_string()),
        };
        let err = build_http_client(&tls).expect_err("corrupt identity must fail");
        assert!(matches!(err, CloudError::Config(_)));
    }

    #[test]
    fn build_http_client_accepts_ephemeral_ca_and_identity_pem() {
        use base64::Engine;
        use rand::rngs::OsRng;
        use rsa::pkcs1v15::SigningKey;
        use rsa::pkcs8::{EncodePrivateKey, EncodePublicKey, LineEnding};
        use rsa::signature::{SignatureEncoding, Signer};
        use rsa::RsaPrivateKey;
        use sha2::Sha256;

        fn der_tlv(tag: u8, content: &[u8]) -> Vec<u8> {
            let mut out = vec![tag];
            let len = content.len();
            if len < 128 {
                out.push(len as u8);
            } else if len <= 0xff {
                out.push(0x81);
                out.push(len as u8);
            } else {
                out.push(0x82);
                out.push((len >> 8) as u8);
                out.push((len & 0xff) as u8);
            }
            out.extend_from_slice(content);
            out
        }

        let priv_key = RsaPrivateKey::new(&mut OsRng, 2048).expect("ephemeral rsa key");
        let key_pem = priv_key
            .to_pkcs8_pem(LineEnding::LF)
            .expect("pkcs8 pem")
            .to_string();
        let spki_der = priv_key
            .to_public_key()
            .to_public_key_der()
            .expect("spki der");

        // sha256WithRSAEncryption OID 1.2.840.113549.1.1.11 + NULL
        let alg_oid = der_tlv(
            0x06,
            &[0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x0b],
        );
        let alg_id = der_tlv(0x30, &[alg_oid, der_tlv(0x05, &[])].concat());
        // CN=elder-test
        let cn_oid = der_tlv(0x06, &[0x55, 0x04, 0x03]);
        let cn_val = der_tlv(0x0c, b"elder-test");
        let atv = der_tlv(0x30, &[cn_oid, cn_val].concat());
        let rdn = der_tlv(0x31, &atv);
        let name = der_tlv(0x30, &rdn);
        // Validity UTCTime 2026-01-01..2030-01-01
        let not_before = der_tlv(0x17, b"260101000000Z");
        let not_after = der_tlv(0x17, b"300101000000Z");
        let validity = der_tlv(0x30, &[not_before, not_after].concat());

        let version = der_tlv(0xa0, &der_tlv(0x02, &[2]));
        let serial = der_tlv(0x02, &[1]);
        let tbs = der_tlv(
            0x30,
            &[
                version,
                serial,
                alg_id.clone(),
                name.clone(),
                validity,
                name,
                spki_der.as_bytes().to_vec(),
            ]
            .concat(),
        );

        let signing_key = SigningKey::<Sha256>::new(priv_key);
        let sig = signing_key.sign(&tbs);
        let mut sig_bits = vec![0x00];
        sig_bits.extend_from_slice(&sig.to_bytes());
        let sig_bit_string = der_tlv(0x03, &sig_bits);
        let cert_der = der_tlv(0x30, &[tbs, alg_id, sig_bit_string].concat());

        let b64 = base64::engine::general_purpose::STANDARD.encode(&cert_der);
        let cert_pem = format!("-----BEGIN CERTIFICATE-----\n{b64}\n-----END CERTIFICATE-----\n");
        let identity_pem = format!("{cert_pem}{key_pem}");

        let tls = TlsOptions {
            verify_ssl: true,
            ca_cert_pem: Some(cert_pem),
            identity_pem: Some(identity_pem),
        };
        build_http_client(&tls).expect("valid ephemeral CA + identity PEM builds rustls client");
    }
}
