//! Embedded full `hub-api` operation catalog and path resolver.
//!
//! ## Name Normalization
//!
//! All canonical `service` and `operation` identifiers in `api-catalog.json`
//! use lowercase `kebab-case` (for example `access-reviews`, `license-policies`,
//! `list-issues`) so they map directly to CLI subcommands.
//!
//! Lookup methods ([`Catalog::operations`] and [`Catalog::find`]) normalize
//! inputs by trimming whitespace, replacing `_` with `-`, and converting ASCII
//! letters to lowercase, so both `kebab-case` and `snake_case` callers
//! (`"access-reviews"` or `"access_reviews"`, `"list-issues"` or `"list_issues"`)
//! resolve to the same operation.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::fmt;
use std::str::FromStr;
use std::sync::OnceLock;

use percent_encoding::{utf8_percent_encode, AsciiSet, NON_ALPHANUMERIC};
use serde::{Deserialize, Serialize};

use crate::error::{ElderError, Result};

const EMBEDDED_CATALOG_JSON: &str = include_str!("../catalog/api-catalog.json");

/// RFC 3986 unreserved characters (`ALPHA / DIGIT / "-" / "." / "_" / "~"`).
/// Everything else in a path parameter value is percent-encoded so slashes,
/// query markers, fragments, and spaces cannot alter route structure.
const PATH_SEGMENT_ENCODE_SET: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'-')
    .remove(b'.')
    .remove(b'_')
    .remove(b'~');

static EMBEDDED_CATALOG: OnceLock<Catalog> = OnceLock::new();

/// HTTP method supported by Elder `hub-api` operations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum HttpMethod {
    /// HTTP `GET`.
    Get,
    /// HTTP `POST`.
    Post,
    /// HTTP `PUT`.
    Put,
    /// HTTP `PATCH`.
    Patch,
    /// HTTP `DELETE`.
    Delete,
}

impl HttpMethod {
    /// Return the uppercase HTTP method token.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Get => "GET",
            Self::Post => "POST",
            Self::Put => "PUT",
            Self::Patch => "PATCH",
            Self::Delete => "DELETE",
        }
    }

    /// Convert into [`reqwest::Method`].
    pub fn to_reqwest(&self) -> reqwest::Method {
        match self {
            Self::Get => reqwest::Method::GET,
            Self::Post => reqwest::Method::POST,
            Self::Put => reqwest::Method::PUT,
            Self::Patch => reqwest::Method::PATCH,
            Self::Delete => reqwest::Method::DELETE,
        }
    }
}

impl fmt::Display for HttpMethod {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl FromStr for HttpMethod {
    type Err = ElderError;

    fn from_str(s: &str) -> Result<Self> {
        match s.trim().to_ascii_uppercase().as_str() {
            "GET" => Ok(Self::Get),
            "POST" => Ok(Self::Post),
            "PUT" => Ok(Self::Put),
            "PATCH" => Ok(Self::Patch),
            "DELETE" => Ok(Self::Delete),
            other => Err(ElderError::Catalog(format!(
                "unsupported HTTP method {other:?}"
            ))),
        }
    }
}

/// Metadata describing a single `hub-api` REST operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApiOperation {
    /// Canonical kebab-case service group (e.g. `"issues"`, `"access-reviews"`).
    pub service: String,
    /// Canonical kebab-case operation name (e.g. `"list-issues"`, `"get-issue"`).
    pub operation: String,
    /// HTTP method.
    pub method: HttpMethod,
    /// Route path template with `{param}` placeholders (e.g. `"/api/v1/issues/{id}"`).
    pub path: String,
    /// Ordered list of path parameter names required by `path`.
    pub path_params: Vec<String>,
    /// Short human-readable summary of the endpoint.
    pub summary: String,
    /// Whether the endpoint reads/accepts a request body.
    pub has_body: bool,
}

/// Embedded or parsed catalog of all `hub-api` operations.
#[derive(Debug, Clone)]
pub struct Catalog {
    operations: Vec<ApiOperation>,
    index: HashMap<(String, String), usize>,
}

impl Catalog {
    /// Return the lazily-initialized embedded catalog compiled into `elder-sdk`.
    pub fn embedded() -> &'static Catalog {
        EMBEDDED_CATALOG.get_or_init(|| {
            Self::from_json_str(EMBEDDED_CATALOG_JSON)
                .expect("embedded api-catalog.json must be valid and collision-free")
        })
    }

    /// Parse and validate a catalog JSON document.
    pub fn from_json_str(raw: &str) -> Result<Self> {
        let operations: Vec<ApiOperation> = serde_json::from_str(raw)?;
        let mut index = HashMap::with_capacity(operations.len());
        let mut seen_routes = HashSet::with_capacity(operations.len());

        for (idx, op) in operations.iter().enumerate() {
            let svc = normalize_name(&op.service);
            let name = normalize_name(&op.operation);
            if svc.is_empty() || name.is_empty() {
                return Err(ElderError::Catalog(format!(
                    "catalog entry at index {idx} has empty service or operation"
                )));
            }
            if index.insert((svc.clone(), name.clone()), idx).is_some() {
                return Err(ElderError::Catalog(format!(
                    "duplicate catalog entry for ({svc}, {name})"
                )));
            }
            if !seen_routes.insert((op.method, op.path.clone())) {
                return Err(ElderError::Catalog(format!(
                    "duplicate route ({}, {}) in catalog",
                    op.method, op.path
                )));
            }
        }

        Ok(Self { operations, index })
    }

    /// Return all operations in deterministic catalog order.
    pub fn all(&self) -> &[ApiOperation] {
        &self.operations
    }

    /// Return total number of operations in the catalog.
    pub fn len(&self) -> usize {
        self.operations.len()
    }

    /// Return `true` if the catalog contains no operations.
    pub fn is_empty(&self) -> bool {
        self.operations.is_empty()
    }

    /// Return sorted, deduplicated canonical service names.
    pub fn services(&self) -> Vec<&str> {
        let unique: BTreeSet<&str> = self.operations.iter().map(|o| o.service.as_str()).collect();
        unique.into_iter().collect()
    }

    /// Return all operations belonging to `service` (accepts `kebab-case` or `snake_case`).
    pub fn operations(&self, service: &str) -> Vec<&ApiOperation> {
        let norm = normalize_name(service);
        self.operations
            .iter()
            .filter(|op| normalize_name(&op.service) == norm)
            .collect()
    }

    /// Look up an operation by `(service, operation)`, accepting both `kebab-case` and `snake_case`.
    pub fn find(&self, service: &str, operation: &str) -> Option<&ApiOperation> {
        let key = (normalize_name(service), normalize_name(operation));
        self.index.get(&key).map(|&idx| &self.operations[idx])
    }

    /// Interpolate `op.path` with URL-encoded values from `path_params`.
    pub fn resolve_path(
        op: &ApiOperation,
        path_params: &HashMap<String, String>,
    ) -> Result<String> {
        let mut resolved = op.path.clone();

        for param in &op.path_params {
            let raw_value = path_params.get(param).ok_or_else(|| {
                ElderError::Catalog(format!(
                    "missing required path parameter '{param}' for '{}.{}' ({})",
                    op.service, op.operation, op.path
                ))
            })?;

            let trimmed = raw_value.trim();
            if trimmed.is_empty() {
                return Err(ElderError::Validation(format!(
                    "path parameter '{param}' for '{}.{}' cannot be empty",
                    op.service, op.operation
                )));
            }
            if trimmed == "." || trimmed == ".." {
                return Err(ElderError::Validation(format!(
                    "path parameter '{param}' cannot be '.' or '..'"
                )));
            }

            let encoded = utf8_percent_encode(trimmed, PATH_SEGMENT_ENCODE_SET).to_string();
            let placeholder = format!("{{{param}}}");
            resolved = resolved.replace(&placeholder, &encoded);
        }

        if resolved.contains('{') || resolved.contains('}') {
            return Err(ElderError::Catalog(format!(
                "unresolved path template placeholders remain in '{resolved}'"
            )));
        }

        Ok(resolved)
    }

    /// Convenience wrapper around [`Catalog::resolve_path`] accepting key/value pairs.
    pub fn resolve_path_pairs<'a, I>(op: &ApiOperation, pairs: I) -> Result<String>
    where
        I: IntoIterator<Item = (&'a str, &'a str)>,
    {
        let map: HashMap<String, String> = pairs
            .into_iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        Self::resolve_path(op, &map)
    }
}

/// Normalize a catalog service or operation identifier to lowercase `kebab-case`.
///
/// Accepts `kebab-case`, `snake_case`, and mixed ASCII casing.
pub fn normalize_name(name: &str) -> String {
    name.trim().replace('_', "-").to_ascii_lowercase()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedded_catalog_is_complete_and_unique() {
        let cat = Catalog::embedded();
        let services = cat.services();

        assert!(
            services.len() >= 60,
            "expected at least 60 services, got {}",
            services.len()
        );
        assert!(
            cat.len() >= 690,
            "expected at least 690 operations, got {}",
            cat.len()
        );

        let mut pairs = HashSet::new();
        let mut routes = HashSet::new();
        for op in cat.all() {
            assert!(
                pairs.insert((op.service.as_str(), op.operation.as_str())),
                "duplicate (service, operation): ({}, {})",
                op.service,
                op.operation
            );
            assert!(
                routes.insert((op.method, op.path.as_str())),
                "duplicate (method, path): ({}, {})",
                op.method,
                op.path
            );
        }

        // Verify newer routes outside older OpenAPI baseline are present.
        assert!(cat.find("licensing", "status").is_some());
        assert_eq!(
            cat.find("licensing", "status").unwrap().path,
            "/api/v1/licensing/status"
        );
        assert!(cat.find("lookup", "resolve-village-id").is_some());
        assert_eq!(
            cat.find("lookup", "resolve-village-id").unwrap().path,
            "/api/v1/id/{village_id}"
        );
        assert!(cat.find("search", "ai-research").is_some());
        assert_eq!(
            cat.find("search", "ai-research").unwrap().path,
            "/api/v1/search/ai-research"
        );
        assert!(cat
            .find("integrations-internal", "get-pending-integrations")
            .is_some());
        assert!(cat.find("sync", "list-sync-configs").is_some());
        assert!(cat.find("discovery", "list-discovery-jobs").is_some());
        assert!(cat.find("geo", "list-regions").is_some());
        assert!(cat.find("ai", "list-ai-resources").is_some());
    }

    #[test]
    fn lookup_accepts_kebab_case_and_snake_case() {
        let cat = Catalog::embedded();

        let a = cat.find("access-reviews", "list-reviews");
        let b = cat.find("access_reviews", "list_reviews");
        let c = cat.find("ACCESS_REVIEWS", "LIST_REVIEWS");
        assert!(a.is_some());
        assert_eq!(a, b);
        assert_eq!(a, c);

        let lp_kebab = cat.operations("license-policies");
        let lp_snake = cat.operations("license_policies");
        assert!(!lp_kebab.is_empty());
        assert_eq!(lp_kebab, lp_snake);
    }

    #[test]
    fn resolve_path_interpolates_and_url_encodes_params() {
        let cat = Catalog::embedded();
        let attach = cat
            .find("pages", "attach-collection")
            .expect("pages.attach-collection");
        let mut params = HashMap::new();
        params.insert("slug".into(), "team docs/v1?draft#1".into());
        params.insert("collection_id".into(), "42".into());

        let path = Catalog::resolve_path(attach, &params).expect("resolve path");
        assert_eq!(
            path,
            "/api/v1/pages/team%20docs%2Fv1%3Fdraft%231/collections/42"
        );

        let missing = Catalog::resolve_path(attach, &HashMap::from([("slug".into(), "a".into())]));
        assert!(matches!(missing, Err(ElderError::Catalog(_))));

        let dot_dot = Catalog::resolve_path_pairs(attach, [("slug", ".."), ("collection_id", "1")]);
        assert!(matches!(dot_dot, Err(ElderError::Validation(_))));

        let empty = Catalog::resolve_path_pairs(attach, [("slug", "  "), ("collection_id", "1")]);
        assert!(matches!(empty, Err(ElderError::Validation(_))));
    }

    #[test]
    fn rejects_duplicate_catalog_entries() {
        let dup_json = r#"[
            {"service":"s","operation":"op","method":"GET","path":"/a","path_params":[],"summary":"1","has_body":false},
            {"service":"s","operation":"op","method":"POST","path":"/b","path_params":[],"summary":"2","has_body":true}
        ]"#;
        let err = Catalog::from_json_str(dup_json).unwrap_err();
        assert!(matches!(err, ElderError::Catalog(_)));
    }

    #[test]
    fn http_method_parsing_and_formatting() {
        assert_eq!(HttpMethod::from_str("get").unwrap(), HttpMethod::Get);
        assert_eq!(HttpMethod::from_str("POST").unwrap(), HttpMethod::Post);
        assert_eq!(HttpMethod::from_str("Patch").unwrap(), HttpMethod::Patch);
        assert_eq!(HttpMethod::Delete.to_string(), "DELETE");
        assert!(HttpMethod::from_str("TRACE").is_err());
    }
}
