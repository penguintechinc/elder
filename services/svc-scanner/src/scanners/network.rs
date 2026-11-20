//! Network discovery scanner (`tcp_connect` native async + `masscan` SYN/UDP).

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tempfile::NamedTempFile;
use tokio::net::TcpStream;
use tokio::process::Command;
use tokio::sync::Semaphore;

use elder_svc_common::{CommonError, Result};

const DEFAULT_PORTS: &str = "22,80,443,3306,5432,6379,8080,8443";
const MAX_TCP_CONNECT_PROBES: usize = 65_536;
const MAX_CONNECT_CONCURRENCY: usize = 64;

const ALLOWED_MASSCAN_FLAGS: &[(&str, bool)] = &[
    ("--wait", true),
    ("--open-only", false),
    ("--open", false),
    ("--banners", false),
    ("--randomize-hosts", false),
    ("--ping", false),
];

/// Parsed configuration for a network discovery job (`config_json`).
#[derive(Debug, Clone, Deserialize)]
pub struct NetworkScanConfig {
    #[serde(default)]
    pub targets: Vec<String>,
    #[serde(default = "default_ports")]
    pub ports: String,
    #[serde(default = "default_scan_type")]
    pub scan_type: String,
    pub rate: Option<u32>,
    #[serde(default = "default_timeout_secs")]
    pub timeout: u64,
    #[serde(default)]
    pub extra_args: Vec<String>,
}

fn default_ports() -> String {
    DEFAULT_PORTS.to_string()
}

fn default_scan_type() -> String {
    "tcp_connect".to_string()
}

fn default_timeout_secs() -> u64 {
    2
}

/// Discovered open port record serialized into job results.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OpenPort {
    pub ip: String,
    pub port: u16,
    pub protocol: String,
    pub state: String,
    pub reason: String,
    pub timestamp: String,
}

#[derive(Debug, Deserialize)]
struct MasscanHostRecord {
    ip: String,
    timestamp: Option<String>,
    #[serde(default)]
    ports: Vec<MasscanPortEntry>,
}

#[derive(Debug, Deserialize)]
struct MasscanPortEntry {
    port: u16,
    #[serde(default = "default_proto")]
    proto: String,
    #[serde(default = "default_state")]
    status: String,
    #[serde(default)]
    reason: String,
}

fn default_proto() -> String {
    "tcp".to_string()
}

fn default_state() -> String {
    "open".to_string()
}

/// Validate targets, ports, scan type, and optional flags before execution.
pub fn validate_network_config(cfg: &NetworkScanConfig) -> Result<()> {
    if cfg.targets.is_empty() {
        return Err(CommonError::Validation(
            "network scan requires non-empty 'targets'".into(),
        ));
    }
    for target in &cfg.targets {
        validate_target_spec(target)?;
    }
    if parse_port_spec(&cfg.ports)?.is_empty() {
        return Err(CommonError::Validation(
            "network scan requires at least one valid port".into(),
        ));
    }
    if !matches!(cfg.scan_type.as_str(), "tcp_connect" | "tcp_syn" | "udp") {
        return Err(CommonError::Validation(format!(
            "unsupported scan_type {:?} (expected tcp_connect, tcp_syn, or udp)",
            cfg.scan_type
        )));
    }
    if cfg.timeout == 0 || cfg.timeout > 60 {
        return Err(CommonError::Validation(format!(
            "network scan timeout must be 1..=60s, got {}",
            cfg.timeout
        )));
    }
    validate_extra_args(&cfg.extra_args)?;
    Ok(())
}

/// Reject shell metacharacters, leading dashes, and malformed targets.
pub fn validate_target_spec(raw: &str) -> Result<()> {
    let target = raw.trim();
    if target.is_empty() {
        return Err(CommonError::Validation("empty target entry".into()));
    }
    if target.starts_with('-') {
        return Err(CommonError::Validation(format!(
            "target must not start with '-': {target:?}"
        )));
    }
    if target.chars().any(|c| {
        c.is_whitespace()
            || matches!(
                c,
                ';' | '|'
                    | '&'
                    | '$'
                    | '`'
                    | '<'
                    | '>'
                    | '('
                    | ')'
                    | '{'
                    | '}'
                    | '['
                    | ']'
                    | '!'
                    | '#'
                    | '*'
                    | '?'
                    | '\''
                    | '"'
                    | '\\'
            )
    }) {
        return Err(CommonError::Validation(format!(
            "target contains forbidden characters: {target:?}"
        )));
    }

    if target.parse::<IpAddr>().is_ok() {
        return Ok(());
    }
    if let Some((ip_part, prefix_part)) = target.split_once('/') {
        let ip = ip_part.parse::<IpAddr>().map_err(|_| {
            CommonError::Validation(format!("invalid CIDR address in target: {target:?}"))
        })?;
        let prefix: u8 = prefix_part.parse().map_err(|_| {
            CommonError::Validation(format!("invalid CIDR prefix in target: {target:?}"))
        })?;
        let max_prefix = if ip.is_ipv4() { 32 } else { 128 };
        if prefix > max_prefix {
            return Err(CommonError::Validation(format!(
                "CIDR prefix out of range in target: {target:?}"
            )));
        }
        return Ok(());
    }

    if is_valid_hostname(target) {
        return Ok(());
    }

    Err(CommonError::Validation(format!(
        "target is neither an IP/CIDR nor a valid hostname: {target:?}"
    )))
}

fn is_valid_hostname(host: &str) -> bool {
    if host.len() > 253 || host.starts_with('.') || host.ends_with('.') {
        return false;
    }
    host.split('.').all(|label| {
        !label.is_empty()
            && label.len() <= 63
            && !label.starts_with('-')
            && !label.ends_with('-')
            && label.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
    })
}

/// Validate and parse port lists/ranges like `22,80,443,8000-8010` or `U:53,T:80`.
pub fn parse_port_spec(spec: &str) -> Result<Vec<(String, u16)>> {
    let trimmed = spec.trim();
    if trimmed.is_empty() {
        return Err(CommonError::Validation(
            "ports specification is empty".into(),
        ));
    }
    if trimmed.chars().any(|c| c.is_whitespace()) {
        return Err(CommonError::Validation(
            "ports specification cannot contain whitespace".into(),
        ));
    }

    let mut out = Vec::new();
    for token in trimmed.split(',') {
        let (proto, rest) = if let Some(r) = token.strip_prefix("U:") {
            ("udp".to_string(), r)
        } else if let Some(r) = token.strip_prefix("T:") {
            ("tcp".to_string(), r)
        } else {
            ("tcp".to_string(), token)
        };

        if let Some((start_s, end_s)) = rest.split_once('-') {
            let start = parse_port(start_s)?;
            let end = parse_port(end_s)?;
            if start > end {
                return Err(CommonError::Validation(format!(
                    "port range start exceeds end: {token:?}"
                )));
            }
            for port in start..=end {
                out.push((proto.clone(), port));
            }
        } else {
            out.push((proto, parse_port(rest)?));
        }
    }
    Ok(out)
}

fn parse_port(raw: &str) -> Result<u16> {
    let p: u16 = raw
        .parse()
        .map_err(|_| CommonError::Validation(format!("invalid port {raw:?}")))?;
    if p == 0 {
        return Err(CommonError::Validation("port 0 is invalid".into()));
    }
    Ok(p)
}

/// Enforce strict allowlist on optional masscan CLI arguments.
pub fn validate_extra_args(args: &[String]) -> Result<Vec<String>> {
    let mut validated = Vec::with_capacity(args.len());
    let mut expect_numeric_value = false;

    for arg in args {
        let trimmed = arg.trim();
        if trimmed.is_empty() {
            return Err(CommonError::Validation("empty extra_args entry".into()));
        }
        if expect_numeric_value {
            let val: u32 = trimmed.parse().map_err(|_| {
                CommonError::Validation(format!("expected integer flag value, got {trimmed:?}"))
            })?;
            if val > 3600 {
                return Err(CommonError::Validation(format!(
                    "flag value out of range: {val}"
                )));
            }
            validated.push(trimmed.to_string());
            expect_numeric_value = false;
            continue;
        }

        if let Some((_, needs_val)) = ALLOWED_MASSCAN_FLAGS
            .iter()
            .find(|(flag, _)| *flag == trimmed)
        {
            validated.push(trimmed.to_string());
            expect_numeric_value = *needs_val;
        } else {
            return Err(CommonError::Validation(format!(
                "disallowed masscan extra_arg: {trimmed:?}"
            )));
        }
    }

    if expect_numeric_value {
        return Err(CommonError::Validation(
            "dangling masscan flag missing integer value".into(),
        ));
    }
    Ok(validated)
}

/// Run a network discovery job using native async TCP connect or `masscan`.
pub async fn run_network_scan(
    config_json: &Value,
    default_rate: u32,
    masscan_bin: &Path,
) -> Result<Value> {
    let cfg: NetworkScanConfig = serde_json::from_value(config_json.clone())
        .map_err(|e| CommonError::Validation(format!("invalid network scan config: {e}")))?;
    validate_network_config(&cfg)?;

    let ports = parse_port_spec(&cfg.ports)?;
    let (open_ports, errors) = if cfg.scan_type == "tcp_connect" {
        run_tcp_connect(&cfg.targets, &ports, Duration::from_secs(cfg.timeout)).await?
    } else {
        let rate = cfg.rate.unwrap_or(default_rate);
        run_masscan(&cfg, rate, masscan_bin).await?
    };

    let mut hosts_up: Vec<String> = open_ports.iter().map(|p| p.ip.clone()).collect();
    hosts_up.sort();
    hosts_up.dedup();

    Ok(json!({
        "targets_scanned": cfg.targets,
        "hosts_up": hosts_up,
        "open_ports": open_ports,
        "errors": errors,
        "scan_type": cfg.scan_type,
        "raw_count": open_ports.len(),
    }))
}

async fn run_tcp_connect(
    targets: &[String],
    ports: &[(String, u16)],
    timeout: Duration,
) -> Result<(Vec<OpenPort>, Vec<String>)> {
    let mut expanded_hosts = Vec::new();
    for target in targets {
        expand_target_hosts(target.trim(), &mut expanded_hosts)?;
    }

    let total_probes = expanded_hosts.len().saturating_mul(ports.len());
    if total_probes > MAX_TCP_CONNECT_PROBES {
        return Err(CommonError::Validation(format!(
            "tcp_connect probe count {total_probes} exceeds limit {MAX_TCP_CONNECT_PROBES}; use tcp_syn/masscan for large ranges"
        )));
    }

    let sem = Arc::new(Semaphore::new(MAX_CONNECT_CONCURRENCY));
    let mut handles = Vec::with_capacity(total_probes);

    for host in expanded_hosts {
        for (proto, port) in ports {
            if proto != "tcp" {
                continue;
            }
            let host_clone = host.clone();
            let port_num = *port;
            let permit_sem = Arc::clone(&sem);

            handles.push(tokio::spawn(async move {
                let Ok(_permit) = permit_sem.acquire_owned().await else {
                    return None;
                };
                if let Ok(ip) = host_clone.parse::<IpAddr>() {
                    let addr = SocketAddr::new(ip, port_num);
                    match tokio::time::timeout(timeout, TcpStream::connect(addr)).await {
                        Ok(Ok(_)) => Some(Ok(OpenPort {
                            ip: ip.to_string(),
                            port: port_num,
                            protocol: "tcp".to_string(),
                            state: "open".to_string(),
                            reason: "syn-ack".to_string(),
                            timestamp: Utc::now().to_rfc3339(),
                        })),
                        _ => None,
                    }
                } else {
                    match tokio::time::timeout(
                        timeout,
                        TcpStream::connect((host_clone.as_str(), port_num)),
                    )
                    .await
                    {
                        Ok(Ok(stream)) => {
                            let peer_ip = stream
                                .peer_addr()
                                .map(|a| a.ip().to_string())
                                .unwrap_or(host_clone);
                            Some(Ok(OpenPort {
                                ip: peer_ip,
                                port: port_num,
                                protocol: "tcp".to_string(),
                                state: "open".to_string(),
                                reason: "syn-ack".to_string(),
                                timestamp: Utc::now().to_rfc3339(),
                            }))
                        }
                        Ok(Err(_)) => None,
                        Err(_) => None,
                    }
                }
            }));
        }
    }

    let mut open_ports = Vec::new();
    let mut errors = Vec::new();
    for handle in handles {
        match handle.await {
            Ok(Some(Ok(port_entry))) => open_ports.push(port_entry),
            Ok(Some(Err(err))) => errors.push(err),
            Ok(None) => {}
            Err(join_err) => errors.push(format!("probe task panic: {join_err}")),
        }
    }

    Ok((open_ports, errors))
}

fn expand_target_hosts(target: &str, out: &mut Vec<String>) -> Result<()> {
    if let Some((ip_part, prefix_part)) = target.split_once('/') {
        let ip: IpAddr = ip_part
            .parse()
            .map_err(|_| CommonError::Validation(format!("invalid CIDR IP: {target:?}")))?;
        let prefix: u8 = prefix_part
            .parse()
            .map_err(|_| CommonError::Validation(format!("invalid CIDR prefix: {target:?}")))?;
        match ip {
            IpAddr::V4(v4) => {
                if prefix < 16 {
                    return Err(CommonError::Validation(format!(
                        "CIDR prefix /{prefix} too broad for tcp_connect (min /16)"
                    )));
                }
                let addr_u32 = u32::from(v4);
                let host_bits = 32 - u32::from(prefix);
                let count = 1u64 << host_bits;
                let mask = if prefix == 0 { 0 } else { !0u32 << host_bits };
                let network = addr_u32 & mask;
                let (start, end) = if prefix >= 31 {
                    (0u64, count)
                } else {
                    (1u64, count - 1)
                };
                for offset in start..end {
                    let candidate = Ipv4Addr::from(network.wrapping_add(offset as u32));
                    out.push(candidate.to_string());
                    if out.len() > MAX_TCP_CONNECT_PROBES {
                        return Err(CommonError::Validation(
                            "expanded target count exceeds limit".into(),
                        ));
                    }
                }
            }
            IpAddr::V6(v6) => {
                if prefix < 120 {
                    return Err(CommonError::Validation(format!(
                        "IPv6 CIDR /{prefix} too broad for tcp_connect (min /120)"
                    )));
                }
                let addr_u128 = u128::from(v6);
                let host_bits = 128 - u32::from(prefix);
                let count = 1u128 << host_bits;
                let mask = if prefix == 0 { 0 } else { !0u128 << host_bits };
                let network = addr_u128 & mask;
                for offset in 0..count {
                    let candidate = Ipv6Addr::from(network.wrapping_add(offset));
                    out.push(candidate.to_string());
                }
            }
        }
    } else {
        out.push(target.to_string());
    }
    Ok(())
}

async fn run_masscan(
    cfg: &NetworkScanConfig,
    rate: u32,
    masscan_bin: &Path,
) -> Result<(Vec<OpenPort>, Vec<String>)> {
    if !masscan_bin.exists() {
        return Err(CommonError::Execution(format!(
            "masscan binary not found at {}",
            masscan_bin.display()
        )));
    }

    let tmp_file = NamedTempFile::new()
        .map_err(|e| CommonError::Execution(format!("failed creating temp output file: {e}")))?;
    let out_path = tmp_file.path().to_path_buf();

    let mut cmd = Command::new(masscan_bin);
    cmd.kill_on_drop(true);
    for target in &cfg.targets {
        cmd.arg(target.trim());
    }

    let port_flag =
        if cfg.scan_type == "udp" && !cfg.ports.contains("U:") && !cfg.ports.contains("T:") {
            format!("-pU:{}", cfg.ports.trim())
        } else {
            format!("-p{}", cfg.ports.trim())
        };
    cmd.arg(port_flag);
    cmd.arg("--rate").arg(rate.to_string());
    cmd.arg("-oJ").arg(&out_path);

    let extra = validate_extra_args(&cfg.extra_args)?;
    for arg in extra {
        cmd.arg(arg);
    }

    tracing::info!(
        scan_type = %cfg.scan_type,
        targets = cfg.targets.len(),
        rate,
        "Spawning masscan"
    );

    let output = cmd
        .output()
        .await
        .map_err(|e| CommonError::Execution(format!("failed executing masscan: {e}")))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(CommonError::Execution(format!(
            "masscan exited with status {}: {}",
            output.status,
            stderr.trim()
        )));
    }

    let raw = tokio::fs::read_to_string(&out_path)
        .await
        .unwrap_or_default();
    let ports = parse_masscan_json(&raw)?;
    Ok((ports, Vec::new()))
}

/// Parse `masscan -oJ` JSON output, stripping masscan's known trailing commas safely.
pub fn parse_masscan_json(raw: &str) -> Result<Vec<OpenPort>> {
    let trimmed = raw.trim();
    if trimmed.is_empty() || trimmed == "[]" {
        return Ok(Vec::new());
    }

    // Masscan sometimes emits `, \n ]` or line-delimited JSON objects.
    let cleaned = regex::Regex::new(r",\s*\]")
        .map_err(|e| CommonError::Execution(e.to_string()))?
        .replace_all(trimmed, "]")
        .to_string();

    if let Ok(records) = serde_json::from_str::<Vec<MasscanHostRecord>>(&cleaned) {
        return Ok(flatten_masscan_records(records));
    }

    // Fallback: line-by-line JSON objects (trailing comma per line in older masscan versions).
    let mut records = Vec::new();
    for line in trimmed.lines() {
        let l = line
            .trim()
            .trim_start_matches('[')
            .trim_end_matches(']')
            .trim_end_matches(',')
            .trim();
        if l.is_empty() {
            continue;
        }
        let rec: MasscanHostRecord = serde_json::from_str(l).map_err(|e| {
            CommonError::Execution(format!("failed parsing masscan JSON line {l:?}: {e}"))
        })?;
        records.push(rec);
    }
    Ok(flatten_masscan_records(records))
}

fn flatten_masscan_records(records: Vec<MasscanHostRecord>) -> Vec<OpenPort> {
    let mut out = Vec::new();
    for host in records {
        let ts = host.timestamp.unwrap_or_else(|| Utc::now().to_rfc3339());
        for p in host.ports {
            out.push(OpenPort {
                ip: host.ip.clone(),
                port: p.port,
                protocol: p.proto,
                state: p.status,
                reason: p.reason,
                timestamp: ts.clone(),
            });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::net::TcpListener;

    #[test]
    fn parses_masscan_json_with_trailing_commas_and_empty() {
        assert!(parse_masscan_json("").expect("empty").is_empty());
        assert!(parse_masscan_json("[]").expect("empty array").is_empty());

        let masscan_buggy = r#"[
            { "ip": "10.0.0.5", "timestamp": "1700000000", "ports": [ {"port": 22, "proto": "tcp", "status": "open", "reason": "syn-ack", "ttl": 64} ] },
        ]"#;
        let ports = parse_masscan_json(masscan_buggy).expect("valid parse");
        assert_eq!(ports.len(), 1);
        assert_eq!(ports[0].ip, "10.0.0.5");
        assert_eq!(ports[0].port, 22);
        assert_eq!(ports[0].protocol, "tcp");
    }

    #[test]
    fn rejects_unsafe_targets_ports_and_extra_args() {
        assert!(validate_target_spec("-oX /tmp/pwn").is_err());
        assert!(validate_target_spec("10.0.0.1; id").is_err());
        assert!(validate_target_spec("10.0.0.1/33").is_err());
        assert!(validate_target_spec("10.0.0.0/24").is_ok());
        assert!(validate_target_spec("api.internal.local").is_ok());

        assert!(parse_port_spec("80,443,8000-8002").is_ok());
        assert!(parse_port_spec("9000-8000").is_err());
        assert!(parse_port_spec("0").is_err());

        assert!(validate_extra_args(&["--wait".into(), "2".into()]).is_ok());
        assert!(validate_extra_args(&["--open-only".into()]).is_ok());
        assert!(validate_extra_args(&["-oX".into()]).is_err());
        assert!(validate_extra_args(&["--router-mac".into()]).is_err());
        assert!(validate_extra_args(&["--wait".into()]).is_err());
    }

    #[tokio::test]
    async fn tcp_connect_discovers_loopback_listener() {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let port = listener.local_addr().expect("addr").port();
        tokio::spawn(async move {
            let _ = listener.accept().await;
        });

        let cfg = json!({
            "targets": ["127.0.0.1"],
            "ports": port.to_string(),
            "scan_type": "tcp_connect",
            "timeout": 2
        });
        let res = run_network_scan(&cfg, 1000, Path::new("/usr/bin/masscan"))
            .await
            .expect("scan ok");
        assert_eq!(res["raw_count"], 1);
        assert_eq!(res["open_ports"][0]["port"], port);
        assert_eq!(res["open_ports"][0]["ip"], "127.0.0.1");
    }
}
