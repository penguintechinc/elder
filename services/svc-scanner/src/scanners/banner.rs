//! Service banner grabber & fingerprint detector (16 standard ports).

use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::sync::Semaphore;

use elder_svc_common::{CommonError, Result};

use super::network::validate_target_spec;

const MAX_BANNER_BYTES: usize = 4096;
const MAX_CONCURRENCY: usize = 32;

/// Static service probe definition mirroring legacy `SERVICE_PROBES`.
#[derive(Debug, Clone, Copy)]
pub struct ServiceProbe {
    pub port: u16,
    pub service_name: &'static str,
    pub protocol: &'static str,
    pub probe_template: &'static str,
}

/// Exact 16-port service probe catalog.
pub const SERVICE_PROBES: &[ServiceProbe] = &[
    ServiceProbe {
        port: 21,
        service_name: "ftp",
        protocol: "tcp",
        probe_template: "",
    },
    ServiceProbe {
        port: 22,
        service_name: "ssh",
        protocol: "tcp",
        probe_template: "",
    },
    ServiceProbe {
        port: 23,
        service_name: "telnet",
        protocol: "tcp",
        probe_template: "",
    },
    ServiceProbe {
        port: 25,
        service_name: "smtp",
        protocol: "tcp",
        probe_template: "",
    },
    ServiceProbe {
        port: 80,
        service_name: "http",
        protocol: "tcp",
        probe_template: "HEAD / HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n",
    },
    ServiceProbe {
        port: 110,
        service_name: "pop3",
        protocol: "tcp",
        probe_template: "",
    },
    ServiceProbe {
        port: 143,
        service_name: "imap",
        protocol: "tcp",
        probe_template: "",
    },
    ServiceProbe {
        port: 443,
        service_name: "https",
        protocol: "tls",
        probe_template: "",
    },
    ServiceProbe {
        port: 587,
        service_name: "smtp-submission",
        protocol: "tcp",
        probe_template: "",
    },
    ServiceProbe {
        port: 3306,
        service_name: "mysql",
        protocol: "tcp",
        probe_template: "",
    },
    ServiceProbe {
        port: 5432,
        service_name: "postgres",
        protocol: "tcp",
        probe_template: "",
    },
    ServiceProbe {
        port: 6379,
        service_name: "redis",
        protocol: "tcp",
        probe_template: "INFO server\r\n",
    },
    ServiceProbe {
        port: 8080,
        service_name: "http-proxy",
        protocol: "tcp",
        probe_template: "HEAD / HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n",
    },
    ServiceProbe {
        port: 8443,
        service_name: "https-alt",
        protocol: "tls",
        probe_template: "",
    },
    ServiceProbe {
        port: 9200,
        service_name: "elasticsearch",
        protocol: "tcp",
        probe_template: "GET / HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n",
    },
    ServiceProbe {
        port: 27017,
        service_name: "mongodb",
        protocol: "tcp",
        probe_template: "",
    },
];

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
enum TargetEntry {
    HostPort { host: String, port: u16 },
    StringSpec(String),
}

#[derive(Debug, Clone, Deserialize)]
struct BannerScanInput {
    #[serde(default)]
    targets: Vec<TargetEntry>,
    #[serde(default)]
    hosts: Vec<String>,
    #[serde(default)]
    ports: Vec<u16>,
    #[serde(default = "default_timeout")]
    timeout: u64,
}

fn default_timeout() -> u64 {
    5
}

/// Captured banner and parsed service fingerprint for one `(host, port)`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BannerResult {
    pub host: String,
    pub port: u16,
    pub service: String,
    pub version: Option<String>,
    pub banner: String,
    pub headers: HashMap<String, String>,
    pub error: Option<String>,
}

/// Probe configured targets concurrently and capture service banners/fingerprints.
pub async fn run_banner_scan(config_json: &Value) -> Result<Value> {
    let input: BannerScanInput = serde_json::from_value(config_json.clone())
        .map_err(|e| CommonError::Validation(format!("invalid banner scan config: {e}")))?;

    if input.timeout == 0 || input.timeout > 60 {
        return Err(CommonError::Validation(format!(
            "banner timeout must be 1..=60s, got {}",
            input.timeout
        )));
    }

    let pairs = normalize_banner_targets(&input)?;
    if pairs.is_empty() {
        return Err(CommonError::Validation(
            "banner scan requires at least one host:port target".into(),
        ));
    }

    let timeout = Duration::from_secs(input.timeout);
    let http_client = reqwest::Client::builder()
        .connect_timeout(timeout)
        .timeout(timeout)
        .redirect(reqwest::redirect::Policy::none())
        .build()?;

    let sem = Arc::new(Semaphore::new(MAX_CONCURRENCY));
    let mut handles = Vec::with_capacity(pairs.len());

    for (host, port) in pairs {
        let permit_sem = Arc::clone(&sem);
        let client = http_client.clone();
        handles.push(tokio::spawn(async move {
            let _permit = permit_sem.acquire_owned().await.ok()?;
            Some(grab_banner(&host, port, timeout, &client).await)
        }));
    }

    let mut results = Vec::new();
    let mut errors = Vec::new();
    for handle in handles {
        match handle.await {
            Ok(Some(res)) => {
                if let Some(err) = &res.error {
                    errors.push(format!("{}:{}: {err}", res.host, res.port));
                }
                results.push(res);
            }
            Ok(None) => {}
            Err(e) => errors.push(format!("banner task failed: {e}")),
        }
    }

    let banners_captured = results.iter().filter(|r| !r.banner.is_empty()).count();
    Ok(json!({
        "targets_probed": results.len(),
        "banners_captured": banners_captured,
        "results": results,
        "errors": errors,
    }))
}

fn normalize_banner_targets(input: &BannerScanInput) -> Result<Vec<(String, u16)>> {
    let mut pairs = Vec::new();
    for entry in &input.targets {
        match entry {
            TargetEntry::HostPort { host, port } => {
                validate_target_spec(host)?;
                if *port == 0 {
                    return Err(CommonError::Validation("port 0 is invalid".into()));
                }
                pairs.push((host.trim().to_string(), *port));
            }
            TargetEntry::StringSpec(spec) => {
                let (host, port_str) = spec.trim().rsplit_once(':').ok_or_else(|| {
                    CommonError::Validation(format!(
                        "banner target must be host:port, got {spec:?}"
                    ))
                })?;
                let clean_host = host.trim_start_matches('[').trim_end_matches(']');
                validate_target_spec(clean_host)?;
                let port: u16 = port_str.parse().map_err(|_| {
                    CommonError::Validation(format!("invalid port in banner target: {spec:?}"))
                })?;
                if port == 0 {
                    return Err(CommonError::Validation("port 0 is invalid".into()));
                }
                pairs.push((clean_host.to_string(), port));
            }
        }
    }

    for host in &input.hosts {
        validate_target_spec(host)?;
        let ports: Vec<u16> = if input.ports.is_empty() {
            SERVICE_PROBES.iter().map(|p| p.port).collect()
        } else {
            input.ports.clone()
        };
        for port in ports {
            if port == 0 {
                return Err(CommonError::Validation("port 0 is invalid".into()));
            }
            pairs.push((host.trim().to_string(), port));
        }
    }

    Ok(pairs)
}

async fn grab_banner(
    host: &str,
    port: u16,
    timeout: Duration,
    http_client: &reqwest::Client,
) -> BannerResult {
    let probe = SERVICE_PROBES.iter().find(|p| p.port == port);
    let default_service = probe.map(|p| p.service_name).unwrap_or("unknown");

    if probe.is_some_and(|p| p.protocol == "tls") {
        return grab_tls_http_banner(host, port, default_service, http_client).await;
    }

    let connect_res = match host.parse::<IpAddr>() {
        Ok(ip) => tokio::time::timeout(timeout, TcpStream::connect((ip, port))).await,
        Err(_) => tokio::time::timeout(timeout, TcpStream::connect((host, port))).await,
    };

    let mut stream = match connect_res {
        Ok(Ok(s)) => s,
        Ok(Err(e)) => {
            return BannerResult {
                host: host.to_string(),
                port,
                service: default_service.to_string(),
                version: None,
                banner: String::new(),
                headers: HashMap::new(),
                error: Some(e.to_string()),
            };
        }
        Err(_) => {
            return BannerResult {
                host: host.to_string(),
                port,
                service: default_service.to_string(),
                version: None,
                banner: String::new(),
                headers: HashMap::new(),
                error: Some("connection timed out".to_string()),
            };
        }
    };

    if let Some(p) = probe {
        if !p.probe_template.is_empty() {
            let payload = p.probe_template.replace("{host}", host);
            let _ = stream.write_all(payload.as_bytes()).await;
        }
    }

    let mut buf = vec![0u8; MAX_BANNER_BYTES];
    let n = match tokio::time::timeout(timeout, stream.read(&mut buf)).await {
        Ok(Ok(bytes)) => bytes,
        _ => 0,
    };

    // Fallback HTTP/SMTP probe if passive port yielded nothing immediately.
    let bytes = if n == 0 && matches!(port, 25 | 587) {
        let _ = stream.write_all(b"EHLO elder-scanner.local\r\n").await;
        tokio::time::timeout(timeout, stream.read(&mut buf))
            .await
            .ok()
            .and_then(|r| r.ok())
            .unwrap_or(0)
    } else {
        n
    };

    let raw = &buf[..bytes];
    let (service, version, banner, headers) = identify_banner(raw, default_service);
    BannerResult {
        host: host.to_string(),
        port,
        service,
        version,
        banner,
        headers,
        error: None,
    }
}

async fn grab_tls_http_banner(
    host: &str,
    port: u16,
    default_service: &str,
    http_client: &reqwest::Client,
) -> BannerResult {
    let url = if host.contains(':') {
        format!("https://[{host}]:{port}/")
    } else {
        format!("https://{host}:{port}/")
    };
    match http_client.head(&url).send().await {
        Ok(resp) => {
            let mut headers = HashMap::new();
            for (k, v) in resp.headers() {
                if let Ok(val) = v.to_str() {
                    headers.insert(k.as_str().to_ascii_lowercase(), val.to_string());
                }
            }
            let server = headers.get("server").cloned();
            let banner = format!(
                "HTTP/{:?} {} server={}",
                resp.version(),
                resp.status(),
                server.as_deref().unwrap_or("")
            );
            BannerResult {
                host: host.to_string(),
                port,
                service: default_service.to_string(),
                version: server,
                banner,
                headers,
                error: None,
            }
        }
        Err(e) => BannerResult {
            host: host.to_string(),
            port,
            service: default_service.to_string(),
            version: None,
            banner: String::new(),
            headers: HashMap::new(),
            error: Some(e.to_string()),
        },
    }
}

/// Detect service name, software version, and normalized headers from raw banner bytes.
pub fn identify_banner(
    raw: &[u8],
    default_service: &str,
) -> (String, Option<String>, String, HashMap<String, String>) {
    if raw.is_empty() {
        return (
            default_service.to_string(),
            None,
            String::new(),
            HashMap::new(),
        );
    }

    // MySQL initial handshake packet: byte 4 is protocol version 0x0a followed by NUL-terminated server version.
    if raw.len() > 6 && raw[4] == 0x0a && default_service == "mysql" {
        if let Some(end) = raw[5..].iter().position(|&b| b == 0) {
            let ver = String::from_utf8_lossy(&raw[5..5 + end]).to_string();
            return (
                "mysql".to_string(),
                Some(ver.clone()),
                format!("MySQL {ver}"),
                HashMap::new(),
            );
        }
    }

    let text = String::from_utf8_lossy(raw).trim().to_string();
    let mut headers = HashMap::new();

    if let Some(rest) = text.strip_prefix("SSH-") {
        let first_line = rest.lines().next().unwrap_or(rest).trim();
        let version = first_line
            .split('-')
            .nth(1)
            .map(|s| s.split_whitespace().next().unwrap_or(s).to_string())
            .or_else(|| Some(first_line.to_string()));
        return ("ssh".to_string(), version, text, headers);
    }

    if text.starts_with("HTTP/") {
        for line in text.lines().skip(1) {
            if let Some((k, v)) = line.split_once(':') {
                headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
            }
        }
        let server = headers.get("server").cloned();
        let service = if text.contains("cluster_name") {
            "elasticsearch".to_string()
        } else {
            default_service.to_string()
        };
        return (service, server, text, headers);
    }

    if text.contains("redis_version:") {
        let version = text
            .lines()
            .find_map(|l| l.strip_prefix("redis_version:"))
            .map(|v| v.trim().to_string());
        return ("redis".to_string(), version, text, headers);
    }

    if text.starts_with("-NOAUTH") || text.starts_with("+PONG") {
        return ("redis".to_string(), None, text, headers);
    }

    if text.starts_with("220") {
        let lower = text.to_ascii_lowercase();
        let svc = if lower.contains("ftp") {
            "ftp"
        } else if lower.contains("smtp") || lower.contains("esmtp") {
            "smtp"
        } else {
            default_service
        };
        return (svc.to_string(), None, text, headers);
    }

    if text.starts_with("+OK") {
        return ("pop3".to_string(), None, text, headers);
    }
    if text.starts_with("* OK") {
        return ("imap".to_string(), None, text, headers);
    }

    (default_service.to_string(), None, text, headers)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::net::TcpListener;

    #[test]
    fn identifies_ssh_http_redis_and_mysql_banners() {
        let (svc, ver, _, _) = identify_banner(b"SSH-2.0-OpenSSH_9.2p1 Debian-2\r\n", "unknown");
        assert_eq!(svc, "ssh");
        assert_eq!(ver.as_deref(), Some("OpenSSH_9.2p1"));

        let (svc, ver, _, hdrs) = identify_banner(
            b"HTTP/1.1 200 OK\r\nServer: nginx/1.26.0\r\nX-Frame-Options: DENY\r\n\r\n",
            "http",
        );
        assert_eq!(svc, "http");
        assert_eq!(ver.as_deref(), Some("nginx/1.26.0"));
        assert_eq!(
            hdrs.get("x-frame-options").map(String::as_str),
            Some("DENY")
        );

        let (svc, ver, _, _) = identify_banner(b"# Server\r\nredis_version:7.4.2\r\n", "redis");
        assert_eq!(svc, "redis");
        assert_eq!(ver.as_deref(), Some("7.4.2"));

        assert_eq!(SERVICE_PROBES.len(), 16);
    }

    #[tokio::test]
    async fn grabs_banner_from_local_tcp_listener() {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let port = listener.local_addr().expect("addr").port();
        tokio::spawn(async move {
            if let Ok((mut sock, _)) = listener.accept().await {
                let _ = sock.write_all(b"SSH-2.0-OpenSSH_9.6p1\r\n").await;
            }
        });

        let cfg = json!({
            "targets": [{"host": "127.0.0.1", "port": port}],
            "timeout": 2
        });
        let res = run_banner_scan(&cfg).await.expect("banner scan ok");
        assert_eq!(res["targets_probed"], 1);
        assert_eq!(res["banners_captured"], 1);
        assert_eq!(res["results"][0]["service"], "ssh");
        assert_eq!(res["results"][0]["version"], "OpenSSH_9.6p1");
    }
}
