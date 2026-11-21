//! HTTP metadata inspection and headless Chromium screenshot capture.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::process::Command;
use tokio::sync::Semaphore;

use elder_svc_common::{CommonError, Result};

const MAX_BODY_READ_BYTES: usize = 256 * 1024;
const MAX_CONCURRENCY: usize = 4;
const SECURITY_HEADERS: &[&str] = &[
    "server",
    "x-powered-by",
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
];

#[derive(Debug, Clone, Deserialize)]
struct HttpScreenshotConfig {
    #[serde(default)]
    targets: Vec<String>,
    #[serde(default)]
    urls: Vec<String>,
    #[serde(default = "default_timeout")]
    timeout: u64,
    #[serde(default = "default_capture_screenshots")]
    capture_screenshots: bool,
}

fn default_timeout() -> u64 {
    15
}

fn default_capture_screenshots() -> bool {
    true
}

/// Captured HTTP metadata and optional PNG screenshot artifact.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HttpTargetResult {
    pub url: String,
    pub final_url: Option<String>,
    pub status_code: Option<u16>,
    pub title: Option<String>,
    pub headers: HashMap<String, String>,
    pub screenshot_captured: bool,
    pub screenshot_size_bytes: Option<usize>,
    pub screenshot_base64: Option<String>,
    pub captured_at: String,
    pub error: Option<String>,
}

/// Validate target URL strictly (allows only `http://` and `https://`).
pub fn validate_http_url(raw: &str) -> Result<String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err(CommonError::Validation("empty URL target".into()));
    }
    if trimmed.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return Err(CommonError::Validation(format!(
            "URL contains forbidden whitespace or control chars: {trimmed:?}"
        )));
    }
    let normalized = if trimmed.contains("://") {
        trimmed.to_string()
    } else {
        format!("https://{trimmed}")
    };

    let parsed = url::Url::parse(&normalized)
        .map_err(|e| CommonError::Validation(format!("invalid URL {trimmed:?}: {e}")))?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err(CommonError::Validation(format!(
            "unsupported URL scheme {:?} (only http/https allowed)",
            parsed.scheme()
        )));
    }
    if parsed.host_str().is_none() {
        return Err(CommonError::Validation(format!(
            "URL missing host: {trimmed:?}"
        )));
    }
    Ok(parsed.to_string())
}

/// Inspect HTTP metadata and capture headless Chromium screenshots for targets.
pub async fn run_http_screenshot_scan(
    config_json: &Value,
    screenshot_dir: &Path,
    chromium_bin: &Path,
) -> Result<Value> {
    let cfg: HttpScreenshotConfig = serde_json::from_value(config_json.clone())
        .map_err(|e| CommonError::Validation(format!("invalid http_screenshot config: {e}")))?;

    if cfg.timeout == 0 || cfg.timeout > 120 {
        return Err(CommonError::Validation(format!(
            "http_screenshot timeout must be 1..=120s, got {}",
            cfg.timeout
        )));
    }

    let mut urls = Vec::new();
    for raw in cfg.targets.iter().chain(cfg.urls.iter()) {
        urls.push(validate_http_url(raw)?);
    }
    if urls.is_empty() {
        return Err(CommonError::Validation(
            "http_screenshot scan requires at least one URL/target".into(),
        ));
    }

    tokio::fs::create_dir_all(screenshot_dir)
        .await
        .map_err(|e| {
            CommonError::Execution(format!(
                "failed preparing screenshot directory {}: {e}",
                screenshot_dir.display()
            ))
        })?;

    let timeout = Duration::from_secs(cfg.timeout);
    let http_client = reqwest::Client::builder()
        .connect_timeout(timeout)
        .timeout(timeout)
        .redirect(reqwest::redirect::Policy::limited(5))
        .user_agent("elder-svc-scanner/0.1")
        .build()?;

    let sem = Arc::new(Semaphore::new(MAX_CONCURRENCY));
    let mut handles = Vec::with_capacity(urls.len());

    for url in urls {
        let permit_sem = Arc::clone(&sem);
        let client = http_client.clone();
        let dir = screenshot_dir.to_path_buf();
        let bin = chromium_bin.to_path_buf();
        let do_shot = cfg.capture_screenshots;

        handles.push(tokio::spawn(async move {
            let _permit = permit_sem.acquire_owned().await.ok()?;
            Some(inspect_and_capture(&url, timeout, &client, &dir, &bin, do_shot).await)
        }));
    }

    let mut results = Vec::new();
    let mut errors = Vec::new();
    for handle in handles {
        match handle.await {
            Ok(Some(res)) => {
                if let Some(err) = &res.error {
                    errors.push(format!("{}: {err}", res.url));
                }
                results.push(res);
            }
            Ok(None) => {}
            Err(e) => errors.push(format!("screenshot task failed: {e}")),
        }
    }

    let screenshots_captured = results.iter().filter(|r| r.screenshot_captured).count();
    Ok(json!({
        "urls_scanned": results.len(),
        "screenshots_captured": screenshots_captured,
        "results": results,
        "errors": errors,
    }))
}

async fn inspect_and_capture(
    url: &str,
    timeout: Duration,
    client: &reqwest::Client,
    screenshot_dir: &Path,
    chromium_bin: &Path,
    capture_screenshots: bool,
) -> HttpTargetResult {
    let mut result = HttpTargetResult {
        url: url.to_string(),
        final_url: None,
        status_code: None,
        title: None,
        headers: HashMap::new(),
        screenshot_captured: false,
        screenshot_size_bytes: None,
        screenshot_base64: None,
        captured_at: Utc::now().to_rfc3339(),
        error: None,
    };

    match client.get(url).send().await {
        Ok(mut resp) => {
            result.final_url = Some(resp.url().to_string());
            result.status_code = Some(resp.status().as_u16());
            for (k, v) in resp.headers() {
                let lower = k.as_str().to_ascii_lowercase();
                if SECURITY_HEADERS.contains(&lower.as_str()) {
                    if let Ok(val) = v.to_str() {
                        result.headers.insert(lower, val.to_string());
                    }
                }
            }

            let mut body_bytes = Vec::new();
            while let Ok(Some(chunk)) = resp.chunk().await {
                if body_bytes.len().saturating_add(chunk.len()) > MAX_BODY_READ_BYTES {
                    let remaining = MAX_BODY_READ_BYTES - body_bytes.len();
                    body_bytes.extend_from_slice(&chunk[..remaining]);
                    break;
                }
                body_bytes.extend_from_slice(&chunk);
            }
            let body = String::from_utf8_lossy(&body_bytes);
            result.title = extract_html_title(&body);
        }
        Err(e) => {
            result.error = Some(format!("HTTP request failed: {e}"));
            return result;
        }
    }

    if capture_screenshots {
        if !chromium_bin.exists() {
            result.error = Some(format!(
                "chromium binary not found at {}",
                chromium_bin.display()
            ));
            return result;
        }
        match capture_chromium_png(url, timeout, screenshot_dir, chromium_bin).await {
            Ok(bytes) => {
                result.screenshot_captured = !bytes.is_empty();
                result.screenshot_size_bytes = Some(bytes.len());
                if !bytes.is_empty() {
                    result.screenshot_base64 = Some(encode_base64(&bytes));
                }
            }
            Err(e) => {
                result.error = Some(format!("chromium screenshot failed: {e}"));
            }
        }
    }

    result
}

async fn capture_chromium_png(
    url: &str,
    timeout: Duration,
    screenshot_dir: &Path,
    chromium_bin: &Path,
) -> Result<Vec<u8>> {
    let temp_dir = TempDir::new_in(screenshot_dir)
        .map_err(|e| CommonError::Execution(format!("failed creating temp dir: {e}")))?;
    let output_png = temp_dir.path().join("capture.png");
    let user_data_dir = temp_dir.path().join("profile");

    let mut cmd = Command::new(chromium_bin);
    cmd.kill_on_drop(true)
        .arg("--headless")
        .arg("--no-sandbox")
        .arg("--disable-gpu")
        .arg("--disable-dev-shm-usage")
        .arg("--disable-background-networking")
        .arg("--hide-scrollbars")
        .arg("--window-size=1280,800")
        .arg(format!("--user-data-dir={}", user_data_dir.display()))
        .arg(format!("--screenshot={}", output_png.display()))
        .arg(url);

    let out = tokio::time::timeout(timeout, cmd.output())
        .await
        .map_err(|_| CommonError::Execution("chromium screenshot timed out".into()))?
        .map_err(|e| CommonError::Execution(format!("failed launching chromium: {e}")))?;

    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(CommonError::Execution(format!(
            "chromium exited {}: {}",
            out.status,
            stderr.trim()
        )));
    }

    let bytes = tokio::fs::read(&output_png)
        .await
        .map_err(|e| CommonError::Execution(format!("missing screenshot output file: {e}")))?;
    Ok(bytes)
}

fn extract_html_title(html: &str) -> Option<String> {
    let re = Regex::new(r"(?is)<title[^>]*>(.*?)</title>").ok()?;
    let caps = re.captures(html)?;
    let title = caps.get(1)?.as_str().trim();
    if title.is_empty() {
        None
    } else {
        Some(title.split_whitespace().collect::<Vec<_>>().join(" "))
    }
}

fn encode_base64(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let b0 = chunk[0];
        let b1 = chunk.get(1).copied().unwrap_or(0);
        let b2 = chunk.get(2).copied().unwrap_or(0);
        let triple = (u32::from(b0) << 16) | (u32::from(b1) << 8) | u32::from(b2);
        out.push(TABLE[((triple >> 18) & 0x3f) as usize] as char);
        out.push(TABLE[((triple >> 12) & 0x3f) as usize] as char);
        out.push(if chunk.len() > 1 {
            TABLE[((triple >> 6) & 0x3f) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            TABLE[(triple & 0x3f) as usize] as char
        } else {
            '='
        });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    #[test]
    fn validates_http_urls_only() {
        assert_eq!(
            validate_http_url("https://example.com/login").expect("ok"),
            "https://example.com/login"
        );
        assert_eq!(
            validate_http_url("example.com").expect("adds https"),
            "https://example.com/"
        );
        assert!(validate_http_url("file:///etc/passwd").is_err());
        assert!(validate_http_url("javascript:alert(1)").is_err());
        assert!(validate_http_url("https://evil.com --flag").is_err());
    }

    #[tokio::test]
    async fn captures_http_metadata_and_title_without_chromium() {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let port = listener.local_addr().expect("addr").port();
        tokio::spawn(async move {
            if let Ok((mut sock, _)) = listener.accept().await {
                let mut buf = [0u8; 1024];
                let _ = sock.read(&mut buf).await;
                let body = "<html><head><title>Elder Portal</title></head><body>ok</body></html>";
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nServer: envoy\r\nContent-Type: text/html\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = sock.write_all(resp.as_bytes()).await;
            }
        });

        let tmp = TempDir::new().expect("tempdir");
        let cfg = json!({
            "targets": [format!("http://127.0.0.1:{port}/")],
            "timeout": 5,
            "capture_screenshots": false
        });
        let res = run_http_screenshot_scan(&cfg, tmp.path(), Path::new("/usr/bin/chromium"))
            .await
            .expect("scan ok");
        assert_eq!(res["urls_scanned"], 1);
        assert_eq!(res["results"][0]["status_code"], 200);
        assert_eq!(res["results"][0]["title"], "Elder Portal");
        assert_eq!(res["results"][0]["headers"]["server"], "envoy");
    }
}
