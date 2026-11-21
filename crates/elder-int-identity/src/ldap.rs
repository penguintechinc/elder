//! Pure-Rust LDAP/LDAPS directory connector using `ldap3` + `rustls` (zero OpenSSL).
//!
//! PRIVACY INVARIANT: Never log `bind_dn`, `bind_password`, user DNs, emails,
//! or display names in any `tracing::*` statement or returned error message.

use std::collections::{HashMap, HashSet};
use std::time::Duration;

use ldap3::adapters::{Adapter, EntriesOnly, PagedResults};
use ldap3::{Ldap, LdapConnAsync, LdapConnSettings, Scope, SearchEntry};
use serde_json::Value;
use url::Url;

use crate::{
    first_non_empty, DirectorySyncBatch, IdentityError, NormalizedGroup, NormalizedMembership,
    NormalizedUser,
};

const DEFAULT_USER_FILTER: &str = "(objectClass=person)";
const DEFAULT_GROUP_FILTER: &str = "(objectClass=group)";
const DEFAULT_PAGE_SIZE: i32 = 500;
const DEFAULT_TIMEOUT_SECS: u64 = 30;

/// Pure-Rust TLS options configuring `ldap3` over `rustls` (never OpenSSL).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TlsOptions {
    /// Enable RFC 4511 StartTLS upgrade over plain `ldap://`.
    pub start_tls: bool,
    /// Verify the server X.509 certificate chain against trusted roots.
    pub verify_cert: bool,
    /// Connection establishment timeout.
    pub connect_timeout: Duration,
}

impl Default for TlsOptions {
    fn default() -> Self {
        Self::new()
    }
}

impl TlsOptions {
    /// Create default `rustls` TLS options (certificate verification enabled, StartTLS off).
    pub fn new() -> Self {
        Self {
            start_tls: false,
            verify_cert: true,
            connect_timeout: Duration::from_secs(DEFAULT_TIMEOUT_SECS),
        }
    }

    /// Enable or disable StartTLS negotiation on `ldap://` connections.
    pub fn with_start_tls(mut self, start_tls: bool) -> Self {
        self.start_tls = start_tls;
        self
    }

    /// Enable or disable server TLS certificate verification.
    pub fn with_verify_cert(mut self, verify_cert: bool) -> Self {
        self.verify_cert = verify_cert;
        self
    }

    /// Set the connection timeout duration.
    pub fn with_connect_timeout(mut self, timeout: Duration) -> Self {
        self.connect_timeout = timeout;
        self
    }

    /// Convert into `ldap3::LdapConnSettings` backed by `rustls`.
    pub fn into_conn_settings(&self) -> LdapConnSettings {
        LdapConnSettings::new()
            .set_conn_timeout(self.connect_timeout)
            .set_starttls(self.start_tls)
            .set_no_tls_verify(!self.verify_cert)
    }
}

/// Validated configuration for an LDAP/LDAPS directory sync.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LdapConfig {
    /// Target LDAP URL (`ldap://...` or `ldaps://...`).
    pub url: String,
    /// TLS configuration for `rustls`.
    pub tls: TlsOptions,
    /// Optional bind DN (`None` for anonymous bind when permitted).
    pub bind_dn: Option<String>,
    /// Bind password paired with `bind_dn`.
    pub bind_password: Option<String>,
    /// Base DN for searching user entries.
    pub user_search_base: String,
    /// Base DN for searching group entries.
    pub group_search_base: String,
    /// Validated RFC 4515 filter for user search.
    pub user_filter: String,
    /// Validated RFC 4515 filter for group search.
    pub group_filter: String,
    /// Paged results control page size (`1..=2000`).
    pub page_size: i32,
}

impl LdapConfig {
    /// Parse and validate LDAP connector configuration from JSON.
    pub fn from_value(config: &Value) -> Result<Self, IdentityError> {
        let start_tls = config
            .get("start_tls")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let verify_cert = config
            .get("verify_cert")
            .or_else(|| config.get("ldap_verify_cert"))
            .and_then(Value::as_bool)
            .unwrap_or(true);
        let timeout_secs = config
            .get("timeout_seconds")
            .and_then(Value::as_u64)
            .unwrap_or(DEFAULT_TIMEOUT_SECS)
            .clamp(5, 300);

        let url = build_ldap_url(config)?;
        let parsed_url = Url::parse(&url)
            .map_err(|e| IdentityError::Config(format!("invalid LDAP URL: {e}")))?;

        if parsed_url.scheme() == "ldaps" && start_tls {
            return Err(IdentityError::Config(
                "start_tls cannot be enabled together with ldaps:// scheme".into(),
            ));
        }

        let bind_dn = first_non_empty(config, &["bind_dn", "ldap_bind_dn"]);
        let bind_password = first_non_empty(config, &["bind_password", "ldap_bind_password"]);
        if bind_dn.is_some() && bind_password.is_none() {
            return Err(IdentityError::Config(
                "bind_password is required when bind_dn is specified".into(),
            ));
        }

        let base_dn = first_non_empty(config, &["base_dn", "ldap_base_dn"]);
        let user_search_base = first_non_empty(config, &["user_search_base"])
            .or_else(|| base_dn.clone())
            .ok_or_else(|| {
                IdentityError::Config("LDAP config requires user_search_base or base_dn".into())
            })?;
        let group_search_base = first_non_empty(config, &["group_search_base"])
            .or(base_dn)
            .unwrap_or_else(|| user_search_base.clone());

        let raw_user_filter = first_non_empty(config, &["user_filter", "ldap_user_filter"])
            .unwrap_or_else(|| DEFAULT_USER_FILTER.to_string());
        let raw_group_filter = first_non_empty(config, &["group_filter", "ldap_group_filter"])
            .unwrap_or_else(|| DEFAULT_GROUP_FILTER.to_string());

        let user_filter = validate_ldap_filter(&raw_user_filter)?;
        let group_filter = validate_ldap_filter(&raw_group_filter)?;

        let page_size = config
            .get("page_size")
            .and_then(Value::as_i64)
            .map(|n| n.clamp(1, 2000) as i32)
            .unwrap_or(DEFAULT_PAGE_SIZE);

        let tls = TlsOptions::new()
            .with_start_tls(start_tls)
            .with_verify_cert(verify_cert)
            .with_connect_timeout(Duration::from_secs(timeout_secs));

        Ok(Self {
            url,
            tls,
            bind_dn,
            bind_password,
            user_search_base,
            group_search_base,
            user_filter,
            group_filter,
            page_size,
        })
    }
}

/// Construct and validate an `ldap://` or `ldaps://` URL from config fields.
pub fn build_ldap_url(config: &Value) -> Result<String, IdentityError> {
    if let Some(raw_url) = first_non_empty(config, &["url", "ldap_url"]) {
        let parsed = Url::parse(&raw_url)
            .map_err(|e| IdentityError::Config(format!("invalid LDAP URL: {e}")))?;
        let scheme = parsed.scheme();
        if scheme != "ldap" && scheme != "ldaps" {
            return Err(IdentityError::Config(format!(
                "LDAP URL scheme must be ldap:// or ldaps://, got {scheme:?}"
            )));
        }
        if parsed.host_str().is_none() {
            return Err(IdentityError::Config("LDAP URL missing host".into()));
        }
        return Ok(raw_url);
    }

    let server = first_non_empty(config, &["server", "host", "ldap_server"])
        .ok_or_else(|| IdentityError::Config("LDAP config missing url or server".into()))?;
    if server.starts_with("ldap://") || server.starts_with("ldaps://") {
        let parsed = Url::parse(&server)
            .map_err(|e| IdentityError::Config(format!("invalid LDAP server URL: {e}")))?;
        if parsed.host_str().is_none() {
            return Err(IdentityError::Config("LDAP URL missing host".into()));
        }
        return Ok(server);
    }
    if server.contains("://") {
        return Err(IdentityError::Config(
            "LDAP server scheme must be ldap:// or ldaps://".into(),
        ));
    }

    let use_ssl = config
        .get("use_ssl")
        .or_else(|| config.get("ldap_use_ssl"))
        .or_else(|| config.get("use_tls"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let default_port: u16 = if use_ssl { 636 } else { 389 };
    let port = match config.get("port").or_else(|| config.get("ldap_port")) {
        Some(Value::Number(n)) => n
            .as_u64()
            .and_then(|v| u16::try_from(v).ok())
            .filter(|&p| p > 0)
            .ok_or_else(|| IdentityError::Config("invalid LDAP port".into()))?,
        Some(Value::String(s)) if !s.trim().is_empty() => s
            .trim()
            .parse::<u16>()
            .ok()
            .filter(|&p| p > 0)
            .ok_or_else(|| IdentityError::Config("invalid LDAP port".into()))?,
        None | Some(Value::Null) | Some(Value::String(_)) => default_port,
        _ => return Err(IdentityError::Config("invalid LDAP port type".into())),
    };

    let scheme = if use_ssl { "ldaps" } else { "ldap" };
    let host_part = if server.contains(':') && !server.starts_with('[') {
        format!("[{server}]")
    } else {
        server
    };
    Ok(format!("{scheme}://{host_part}:{port}"))
}

/// Decoupled representation of an LDAP directory entry for pure unit testing.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LdapEntryRecord {
    /// Entry Distinguished Name.
    pub dn: String,
    /// Case-preserving string attributes keyed by lowercase attribute name.
    pub attrs: HashMap<String, Vec<String>>,
    /// Binary attributes (e.g. `objectGUID`) keyed by lowercase attribute name.
    pub bin_attrs: HashMap<String, Vec<Vec<u8>>>,
}

impl LdapEntryRecord {
    /// Construct from an `ldap3::SearchEntry`, normalizing attribute keys to lowercase.
    pub fn from_search_entry(entry: SearchEntry) -> Self {
        let attrs = entry
            .attrs
            .into_iter()
            .map(|(k, v)| (k.to_ascii_lowercase(), v))
            .collect();
        let bin_attrs = entry
            .bin_attrs
            .into_iter()
            .map(|(k, v)| (k.to_ascii_lowercase(), v))
            .collect();
        Self {
            dn: entry.dn,
            attrs,
            bin_attrs,
        }
    }

    /// Return the first non-empty string value for any matching attribute key (case-insensitive).
    pub fn first_attr(&self, keys: &[&str]) -> Option<String> {
        for key in keys {
            let lower = key.to_ascii_lowercase();
            if let Some(vals) = self.attrs.get(&lower) {
                for v in vals {
                    let trimmed = v.trim();
                    if !trimmed.is_empty() {
                        return Some(trimmed.to_string());
                    }
                }
            }
        }
        None
    }

    /// Return all non-empty string values for an attribute key (case-insensitive).
    pub fn all_attr(&self, key: &str) -> Vec<String> {
        let lower = key.to_ascii_lowercase();
        self.attrs
            .get(&lower)
            .map(|vals| {
                vals.iter()
                    .map(|v| v.trim())
                    .filter(|v| !v.is_empty())
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Extract a stable UUID/string from `objectGUID` (16-byte AD binary or string).
    pub fn object_guid(&self) -> Option<String> {
        if let Some(vals) = self.bin_attrs.get("objectguid") {
            if let Some(bytes) = vals.first() {
                if bytes.len() == 16 {
                    return Some(format_ad_guid(bytes));
                }
            }
        }
        self.first_attr(&["objectGUID"])
    }
}

/// Format a 16-byte Active Directory `objectGUID` into canonical UUID string form.
pub fn format_ad_guid(bytes: &[u8]) -> String {
    if bytes.len() != 16 {
        return bytes.iter().map(|b| format!("{b:02x}")).collect();
    }
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[3], bytes[2], bytes[1], bytes[0],
        bytes[5], bytes[4],
        bytes[7], bytes[6],
        bytes[8], bytes[9],
        bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]
    )
}

/// Parse an RFC 4514 Distinguished Name into `(attribute, value)` RDN components,
/// preserving escaped commas (`\,`) and escaped backslashes (`\\`).
pub fn parse_dn_components(dn: &str) -> Vec<(String, String)> {
    let mut components = Vec::new();
    let mut current = String::new();
    let mut escaped = false;

    for ch in dn.chars() {
        if escaped {
            current.push(ch);
            escaped = false;
            continue;
        }
        if ch == '\\' {
            escaped = true;
            current.push(ch);
            continue;
        }
        if ch == ',' {
            if let Some(pair) = split_rdn(&current) {
                components.push(pair);
            }
            current.clear();
        } else {
            current.push(ch);
        }
    }
    if let Some(pair) = split_rdn(&current) {
        components.push(pair);
    }

    components
}

fn split_rdn(raw: &str) -> Option<(String, String)> {
    let trimmed = raw.trim();
    let (key, val) = trimmed.split_once('=')?;
    let k = key.trim().to_string();
    let v = unescape_dn_value(val.trim());
    (!k.is_empty() && !v.is_empty()).then_some((k, v))
}

fn unescape_dn_value(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    let mut chars = raw.chars();
    while let Some(ch) = chars.next() {
        if ch == '\\' {
            if let Some(next) = chars.next() {
                out.push(next);
            }
        } else {
            out.push(ch);
        }
    }
    out
}

/// Normalize a DN for case-insensitive lookup across `member` DNs and entry DNs.
pub fn normalize_dn(dn: &str) -> String {
    parse_dn_components(dn)
        .into_iter()
        .map(|(k, v)| format!("{}={}", k.to_ascii_lowercase(), v.to_ascii_lowercase()))
        .collect::<Vec<_>>()
        .join(",")
}

/// Extract the first RDN value matching `attr_name` (case-insensitive) from a DN.
pub fn extract_rdn_value(dn: &str, attr_name: &str) -> Option<String> {
    parse_dn_components(dn)
        .into_iter()
        .find(|(k, _)| k.eq_ignore_ascii_case(attr_name))
        .map(|(_, v)| v)
}

/// Escape special RFC 4515 characters inside an LDAP filter assertion value.
pub fn escape_ldap_filter_value(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for ch in value.chars() {
        match ch {
            '\\' => out.push_str("\\5c"),
            '*' => out.push_str("\\2a"),
            '(' => out.push_str("\\28"),
            ')' => out.push_str("\\29"),
            '\0' => out.push_str("\\00"),
            other => out.push(other),
        }
    }
    out
}

/// Validate that an LDAP search filter is non-empty, free of NUL bytes,
/// enclosed in parentheses, and balanced.
pub fn validate_ldap_filter(filter: &str) -> Result<String, IdentityError> {
    let trimmed = filter.trim();
    if trimmed.is_empty() {
        return Err(IdentityError::Config("LDAP filter cannot be empty".into()));
    }
    if trimmed.contains('\0') {
        return Err(IdentityError::Config(
            "LDAP filter must not contain NUL bytes".into(),
        ));
    }
    if !(trimmed.starts_with('(') && trimmed.ends_with(')')) {
        return Err(IdentityError::Config(
            "LDAP filter must be wrapped in parentheses".into(),
        ));
    }
    let mut depth: i32 = 0;
    let mut saw_inner_char = false;
    for (idx, ch) in trimmed.char_indices() {
        match ch {
            '(' => depth += 1,
            ')' => {
                depth -= 1;
                if depth < 0 {
                    return Err(IdentityError::Config(
                        "LDAP filter has unbalanced parentheses".into(),
                    ));
                }
                if depth == 0 && idx + ch.len_utf8() < trimmed.len() {
                    return Err(IdentityError::Config(
                        "LDAP filter contains trailing characters outside root group".into(),
                    ));
                }
            }
            c if !c.is_whitespace() => saw_inner_char = true,
            _ => {}
        }
    }
    if depth != 0 || !saw_inner_char {
        return Err(IdentityError::Config(
            "LDAP filter has unbalanced or empty parentheses".into(),
        ));
    }
    Ok(trimmed.to_string())
}

/// Combine a base LDAP filter with zero or more additional filter clauses via `(&...)`.
pub fn build_search_filter(
    base_filter: &str,
    extra_clauses: &[&str],
) -> Result<String, IdentityError> {
    let normalized_base = validate_ldap_filter(base_filter)?;
    if extra_clauses.is_empty() {
        return Ok(normalized_base);
    }
    let mut validated_clauses = Vec::with_capacity(extra_clauses.len() + 1);
    validated_clauses.push(normalized_base);
    for clause in extra_clauses {
        validated_clauses.push(validate_ldap_filter(clause)?);
    }
    Ok(format!("(&{})", validated_clauses.join("")))
}

/// Map a raw LDAP user entry into a privacy-safe [`NormalizedUser`].
pub fn map_user_entry(entry: &LdapEntryRecord) -> Option<NormalizedUser> {
    if entry.dn.trim().is_empty() {
        return None;
    }
    let ext_id = entry
        .object_guid()
        .or_else(|| entry.first_attr(&["entryUUID", "ipaUniqueID"]))
        .unwrap_or_else(|| entry.dn.clone());

    let username = entry
        .first_attr(&["sAMAccountName", "uid", "cn"])
        .or_else(|| extract_rdn_value(&entry.dn, "uid"))
        .or_else(|| extract_rdn_value(&entry.dn, "cn"))
        .filter(|u| !u.contains('@'));

    let is_active = is_ldap_user_active(entry);

    Some(NormalizedUser {
        external_id: ext_id.clone(),
        id: ext_id,
        username,
        identity_type: "human".to_string(),
        is_active,
    })
}

fn is_ldap_user_active(entry: &LdapEntryRecord) -> bool {
    if let Some(uac_raw) = entry.first_attr(&["userAccountControl"]) {
        if let Ok(uac) = uac_raw.parse::<u64>() {
            // Bit 2 (0x0002) in AD userAccountControl marks ACCOUNTDISABLE.
            if (uac & 0x2) != 0 {
                return false;
            }
        }
    }
    if let Some(lock) = entry.first_attr(&["nsAccountLock"]) {
        if lock.eq_ignore_ascii_case("true") {
            return false;
        }
    }
    true
}

/// Map a raw LDAP group entry into a [`NormalizedGroup`] plus raw member references.
pub fn map_group_entry(
    entry: &LdapEntryRecord,
) -> Option<(NormalizedGroup, Vec<String>, Vec<String>)> {
    if entry.dn.trim().is_empty() {
        return None;
    }
    let ext_id = entry
        .object_guid()
        .or_else(|| entry.first_attr(&["entryUUID", "ipaUniqueID"]))
        .unwrap_or_else(|| entry.dn.clone());

    let name = entry
        .first_attr(&["cn", "ou", "displayName"])
        .or_else(|| extract_rdn_value(&entry.dn, "cn"))
        .or_else(|| extract_rdn_value(&entry.dn, "ou"))
        .unwrap_or_else(|| ext_id.clone());

    let description = entry.first_attr(&["description"]);
    let mut member_dns = entry.all_attr("member");
    member_dns.extend(entry.all_attr("uniqueMember"));
    let member_uids = entry.all_attr("memberUid");

    let group = NormalizedGroup {
        external_id: ext_id.clone(),
        id: ext_id,
        name,
        description,
        is_active: true,
    };
    Some((group, member_dns, member_uids))
}

/// Build a complete [`DirectorySyncBatch`] from fetched user and group LDAP entries.
pub fn build_directory_batch(
    user_entries: &[LdapEntryRecord],
    group_entries: &[LdapEntryRecord],
) -> DirectorySyncBatch {
    let mut batch = DirectorySyncBatch::default();
    let mut user_id_by_dn: HashMap<String, String> = HashMap::new();
    let mut user_id_by_uid: HashMap<String, String> = HashMap::new();

    for entry in user_entries {
        if let Some(user) = map_user_entry(entry) {
            user_id_by_dn.insert(normalize_dn(&entry.dn), user.external_id.clone());
            if let Some(uid) = entry.first_attr(&["uid", "sAMAccountName"]) {
                user_id_by_uid.insert(uid.to_ascii_lowercase(), user.external_id.clone());
            }
            batch.users.push(user);
        }
    }

    let mut seen_memberships: HashSet<(String, String)> = HashSet::new();
    for entry in group_entries {
        let Some((group, member_dns, member_uids)) = map_group_entry(entry) else {
            continue;
        };

        for member_dn in member_dns {
            let resolved_uid = user_id_by_dn
                .get(&normalize_dn(&member_dn))
                .cloned()
                .unwrap_or(member_dn);
            if seen_memberships.insert((resolved_uid.clone(), group.external_id.clone())) {
                batch.memberships.push(NormalizedMembership {
                    user_external_id: resolved_uid.clone(),
                    group_external_id: group.external_id.clone(),
                    provider_member_id: resolved_uid,
                });
            }
        }

        for member_uid in member_uids {
            let resolved_uid = user_id_by_uid
                .get(&member_uid.to_ascii_lowercase())
                .cloned()
                .unwrap_or(member_uid);
            if seen_memberships.insert((resolved_uid.clone(), group.external_id.clone())) {
                batch.memberships.push(NormalizedMembership {
                    user_external_id: resolved_uid.clone(),
                    group_external_id: group.external_id.clone(),
                    provider_member_id: resolved_uid,
                });
            }
        }

        batch.groups.push(group);
    }

    batch
}

/// Connect via pure-Rust `ldap3` + `rustls`, perform simple bind, run paged searches
/// for users and groups, and return a normalized [`DirectorySyncBatch`].
pub async fn fetch_directory(config: &Value) -> Result<DirectorySyncBatch, IdentityError> {
    let cfg = LdapConfig::from_value(config)?;
    let conn_settings = cfg.tls.into_conn_settings();

    let (conn, mut ldap) = LdapConnAsync::with_settings(conn_settings, &cfg.url)
        .await
        .map_err(|e| IdentityError::Upstream(format!("LDAP connection failed: {e}")))?;
    ldap3::drive!(conn);

    if let (Some(bind_dn), Some(bind_password)) = (&cfg.bind_dn, &cfg.bind_password) {
        let bind_res = ldap
            .simple_bind(bind_dn, bind_password)
            .await
            .map_err(|e| IdentityError::Auth(format!("LDAP bind transport error: {e}")))?;
        bind_res
            .success()
            .map_err(|e| IdentityError::Auth(format!("LDAP simple bind rejected: {e}")))?;
    }

    let user_attrs = vec![
        "cn",
        "uid",
        "sAMAccountName",
        "entryUUID",
        "objectGUID",
        "ipaUniqueID",
        "userAccountControl",
        "nsAccountLock",
    ];
    let group_attrs = vec![
        "cn",
        "ou",
        "displayName",
        "description",
        "entryUUID",
        "objectGUID",
        "ipaUniqueID",
        "member",
        "uniqueMember",
        "memberUid",
    ];

    let user_entries = paged_search(
        &mut ldap,
        &cfg.user_search_base,
        &cfg.user_filter,
        &user_attrs,
        cfg.page_size,
    )
    .await?;

    let group_entries = paged_search(
        &mut ldap,
        &cfg.group_search_base,
        &cfg.group_filter,
        &group_attrs,
        cfg.page_size,
    )
    .await?;

    if let Err(err) = ldap.unbind().await {
        tracing::debug!(error = %err, "LDAP unbind returned warning");
    }

    Ok(build_directory_batch(&user_entries, &group_entries))
}

async fn paged_search(
    ldap: &mut Ldap,
    search_base: &str,
    filter: &str,
    attrs: &[&str],
    page_size: i32,
) -> Result<Vec<LdapEntryRecord>, IdentityError> {
    let adapters: Vec<Box<dyn Adapter<'_, &str, Vec<&str>>>> = vec![
        Box::new(EntriesOnly::new()),
        Box::new(PagedResults::new(page_size)),
    ];
    let mut stream = ldap
        .streaming_search_with(
            adapters,
            search_base,
            Scope::Subtree,
            filter,
            attrs.to_vec(),
        )
        .await
        .map_err(|e| IdentityError::Upstream(format!("LDAP paged search init failed: {e}")))?;

    let mut records = Vec::new();
    while let Some(raw_entry) = stream
        .next()
        .await
        .map_err(|e| IdentityError::Upstream(format!("LDAP paged search iteration failed: {e}")))?
    {
        let parsed = SearchEntry::construct(raw_entry);
        records.push(LdapEntryRecord::from_search_entry(parsed));
    }

    stream
        .finish()
        .await
        .success()
        .map_err(|e| IdentityError::Upstream(format!("LDAP search completed with error: {e}")))?;

    Ok(records)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_dn_components_with_escapes_and_extracts_rdns() {
        let dn = r"cn=Doe\, John, ou=Engineering , dc=example,dc=com";
        let parts = parse_dn_components(dn);
        assert_eq!(
            parts,
            vec![
                ("cn".into(), "Doe, John".into()),
                ("ou".into(), "Engineering".into()),
                ("dc".into(), "example".into()),
                ("dc".into(), "com".into()),
            ]
        );
        assert_eq!(extract_rdn_value(dn, "CN"), Some("Doe, John".to_string()));
        assert_eq!(extract_rdn_value(dn, "OU"), Some("Engineering".to_string()));
        assert_eq!(
            normalize_dn("CN=Alice,OU=Eng,DC=Example,DC=COM"),
            "cn=alice,ou=eng,dc=example,dc=com"
        );
    }

    #[test]
    fn validates_and_constructs_rfc4515_filters_safely() {
        assert_eq!(
            validate_ldap_filter("  (objectClass=person) ").unwrap(),
            "(objectClass=person)"
        );
        assert_eq!(
            build_search_filter(
                "(objectClass=person)",
                &["(uid=alice)", "(memberOf=cn=eng)"]
            )
            .unwrap(),
            "(&(objectClass=person)(uid=alice)(memberOf=cn=eng))"
        );
        assert_eq!(escape_ldap_filter_value(r"a*b(c)d\e"), r"a\2ab\28c\29d\5ce");
        assert!(validate_ldap_filter("").is_err());
        assert!(validate_ldap_filter("objectClass=person").is_err());
        assert!(validate_ldap_filter("((objectClass=person)").is_err());
        assert!(validate_ldap_filter("(objectClass=a)(objectClass=b)").is_err());
        assert!(validate_ldap_filter("(uid=bad\0)").is_err());
    }

    #[test]
    fn maps_user_entries_including_ad_uac_guid_and_nsaccountlock() {
        let guid_bytes = vec![
            0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x66, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd,
            0xee, 0xff,
        ];
        let mut active_attrs = HashMap::new();
        active_attrs.insert("samaccountname".into(), vec!["jdoe".into()]);
        active_attrs.insert("useraccountcontrol".into(), vec!["512".into()]);
        let mut active_bin = HashMap::new();
        active_bin.insert("objectguid".into(), vec![guid_bytes]);
        let active_rec = LdapEntryRecord {
            dn: "cn=John Doe,ou=Users,dc=example,dc=com".into(),
            attrs: active_attrs,
            bin_attrs: active_bin,
        };
        let active_user = map_user_entry(&active_rec).expect("active user");
        assert_eq!(
            active_user.external_id,
            "00112233-4455-6677-8899-aabbccddeeff"
        );
        assert_eq!(active_user.username.as_deref(), Some("jdoe"));
        assert!(active_user.is_active);

        let mut disabled_attrs = HashMap::new();
        disabled_attrs.insert("uid".into(), vec!["disabled_u".into()]);
        disabled_attrs.insert("useraccountcontrol".into(), vec!["514".into()]);
        let disabled_rec = LdapEntryRecord {
            dn: "uid=disabled_u,ou=Users,dc=example,dc=com".into(),
            attrs: disabled_attrs,
            bin_attrs: HashMap::new(),
        };
        assert!(!map_user_entry(&disabled_rec).unwrap().is_active);

        let mut locked_attrs = HashMap::new();
        locked_attrs.insert("uid".into(), vec!["locked_u".into()]);
        locked_attrs.insert("nsaccountlock".into(), vec!["TRUE".into()]);
        let locked_rec = LdapEntryRecord {
            dn: "uid=locked_u,ou=Users,dc=example,dc=com".into(),
            attrs: locked_attrs,
            bin_attrs: HashMap::new(),
        };
        assert!(!map_user_entry(&locked_rec).unwrap().is_active);
    }

    #[test]
    fn maps_groups_and_resolves_member_dns_and_member_uids() {
        let mut u1_attrs = HashMap::new();
        u1_attrs.insert("entryuuid".into(), vec!["uuid-u1".into()]);
        u1_attrs.insert("uid".into(), vec!["alice".into()]);
        let u1 = LdapEntryRecord {
            dn: "uid=alice,ou=Users,dc=example,dc=com".into(),
            attrs: u1_attrs,
            bin_attrs: HashMap::new(),
        };

        let mut u2_attrs = HashMap::new();
        u2_attrs.insert("entryuuid".into(), vec!["uuid-u2".into()]);
        u2_attrs.insert("uid".into(), vec!["bob".into()]);
        let u2 = LdapEntryRecord {
            dn: "uid=bob,ou=Users,dc=example,dc=com".into(),
            attrs: u2_attrs,
            bin_attrs: HashMap::new(),
        };

        let mut g_attrs = HashMap::new();
        g_attrs.insert("entryuuid".into(), vec!["uuid-g1".into()]);
        g_attrs.insert("cn".into(), vec!["Developers".into()]);
        g_attrs.insert("description".into(), vec!["Dev team".into()]);
        g_attrs.insert(
            "member".into(),
            vec!["UID=Alice,OU=Users,DC=example,DC=com".into()],
        );
        g_attrs.insert("memberuid".into(), vec!["bob".into(), "alice".into()]);
        let g1 = LdapEntryRecord {
            dn: "cn=Developers,ou=Groups,dc=example,dc=com".into(),
            attrs: g_attrs,
            bin_attrs: HashMap::new(),
        };

        let batch = build_directory_batch(&[u1, u2], &[g1]);
        assert_eq!(batch.users.len(), 2);
        assert_eq!(batch.groups.len(), 1);
        assert_eq!(batch.groups[0].name, "Developers");
        assert_eq!(batch.memberships.len(), 2);
        assert!(batch
            .memberships
            .iter()
            .any(|m| m.user_external_id == "uuid-u1" && m.group_external_id == "uuid-g1"));
        assert!(batch
            .memberships
            .iter()
            .any(|m| m.user_external_id == "uuid-u2" && m.group_external_id == "uuid-g1"));
    }

    #[test]
    fn validates_ldap_config_urls_and_rustls_tls_options() {
        let ldaps_cfg = LdapConfig::from_value(&json!({
            "server": "directory.example.com",
            "use_ssl": true,
            "verify_cert": true,
            "bind_dn": "cn=readonly,dc=example,dc=com",
            "bind_password": "secret",
            "base_dn": "dc=example,dc=com",
            "user_search_base": "ou=People,dc=example,dc=com"
        }))
        .expect("valid ldaps config");
        assert_eq!(ldaps_cfg.url, "ldaps://directory.example.com:636");
        assert_eq!(ldaps_cfg.user_search_base, "ou=People,dc=example,dc=com");
        assert_eq!(ldaps_cfg.group_search_base, "dc=example,dc=com");
        assert!(!ldaps_cfg.tls.start_tls);
        assert!(ldaps_cfg.tls.verify_cert);
        let _settings = ldaps_cfg.tls.into_conn_settings();

        let starttls_cfg = LdapConfig::from_value(&json!({
            "url": "ldap://ldap.example.com:389",
            "start_tls": true,
            "base_dn": "dc=example,dc=com"
        }))
        .expect("valid starttls config");
        assert!(starttls_cfg.tls.start_tls);

        // Rejects ldaps:// + start_tls=true
        assert!(LdapConfig::from_value(&json!({
            "url": "ldaps://ldap.example.com:636",
            "start_tls": true,
            "base_dn": "dc=example,dc=com"
        }))
        .is_err());

        // Rejects bind_dn without bind_password
        assert!(LdapConfig::from_value(&json!({
            "url": "ldap://ldap.example.com:389",
            "bind_dn": "cn=admin,dc=example,dc=com",
            "base_dn": "dc=example,dc=com"
        }))
        .is_err());

        // Rejects non-LDAP URL scheme and missing base_dn
        assert!(LdapConfig::from_value(&json!({
            "url": "https://ldap.example.com",
            "base_dn": "dc=example,dc=com"
        }))
        .is_err());
        assert!(LdapConfig::from_value(&json!({
            "server": "ldap.example.com"
        }))
        .is_err());
    }
}
