//! Git-based SBOM repository cloning, manifest/endpoint scanning, and cron schedule execution.

use std::collections::HashSet;
use std::path::Path;
use std::str::FromStr;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tempfile::TempDir;
use tokio::process::Command;

use elder_svc_common::{CommonError, ElderApiClient, Result};

use crate::parsers::{
    parse_dependency_file, parse_endpoint_file, DiscoveredEndpoint, SbomComponent,
};

const MAX_SCAN_FILE_BYTES: u64 = 2 * 1024 * 1024;
const SKIP_DIRS: &[&str] = &[
    ".git",
    "node_modules",
    "target",
    "vendor",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
];

/// Pending SBOM scan record returned by `GET /api/v1/sbom/scans/pending`.
#[derive(Debug, Clone, Deserialize)]
pub struct PendingSbomScan {
    pub id: i64,
    pub parent_type: String,
    pub parent_id: i64,
    pub scan_type: String,
    pub repository_url: Option<String>,
    pub repository_branch: Option<String>,
    #[serde(rename = "_resolved_token")]
    pub resolved_token: Option<String>,
}

/// Due SBOM schedule returned by `GET /api/v1/sbom/schedules/due`.
#[derive(Debug, Clone, Deserialize)]
pub struct DueSbomSchedule {
    pub id: i64,
    pub parent_type: String,
    pub parent_id: i64,
    pub schedule_cron: String,
}

#[derive(Debug, Serialize)]
struct SubmitSbomResults<'a> {
    success: bool,
    components: &'a [SbomComponent],
    files_scanned: &'a [String],
    commit_hash: Option<String>,
    error_message: Option<String>,
    scan_duration_ms: i64,
    endpoints: &'a [DiscoveredEndpoint],
    endpoints_found: usize,
}

#[derive(Debug, Serialize)]
struct TriggerSbomScanRequest<'a> {
    parent_type: &'a str,
    parent_id: i64,
    scan_type: &'a str,
}

#[derive(Debug, Serialize)]
struct UpdateScheduleRunRequest {
    last_run_at: String,
    next_run_at: String,
}

/// Strip credentials/userinfo from a Git repository URL before logging or reporting.
pub fn sanitize_git_url(raw: &str) -> String {
    let Ok(mut parsed) = url::Url::parse(raw) else {
        return "<invalid-url>".to_string();
    };
    let _ = parsed.set_username("");
    let _ = parsed.set_password(None);
    parsed.to_string()
}

/// Validate repository URL and optional branch before passing to `git clone`.
pub fn validate_git_inputs(
    repo_url: &str,
    branch: Option<&str>,
) -> Result<(String, Option<String>)> {
    let trimmed_url = repo_url.trim();
    if trimmed_url.is_empty() || trimmed_url.starts_with('-') {
        return Err(CommonError::Validation(
            "repository_url must be a non-empty http(s) URL not starting with '-'".into(),
        ));
    }
    if trimmed_url
        .chars()
        .any(|c| c.is_control() || c.is_whitespace())
    {
        return Err(CommonError::Validation(
            "repository_url contains whitespace or control characters".into(),
        ));
    }

    let parsed = url::Url::parse(trimmed_url)
        .map_err(|e| CommonError::Validation(format!("invalid repository_url: {e}")))?;
    if !matches!(parsed.scheme(), "https" | "http") || parsed.host_str().is_none() {
        return Err(CommonError::Validation(format!(
            "repository_url scheme must be http or https, got {:?}",
            parsed.scheme()
        )));
    }

    let validated_branch = match branch.map(str::trim).filter(|b| !b.is_empty()) {
        Some(b) => {
            if b.starts_with('-')
                || b.contains("..")
                || b.chars().any(|c| c.is_control() || c.is_whitespace())
            {
                return Err(CommonError::Validation(format!(
                    "unsafe repository_branch: {b:?}"
                )));
            }
            Some(b.to_string())
        }
        None => None,
    };

    Ok((parsed.to_string(), validated_branch))
}

/// Compute the next UTC execution timestamp for a 5-field or 6-field cron expression.
pub fn compute_next_run(schedule_cron: &str, from: DateTime<Utc>) -> Result<DateTime<Utc>> {
    let trimmed = schedule_cron.trim();
    let normalized = match trimmed.split_whitespace().count() {
        5 => format!("0 {trimmed}"),
        6 | 7 => trimmed.to_string(),
        n => {
            return Err(CommonError::Validation(format!(
                "invalid cron expression {trimmed:?}: expected 5 or 6 fields, found {n}"
            )))
        }
    };

    let schedule = cron::Schedule::from_str(&normalized).map_err(|e| {
        CommonError::Validation(format!("failed parsing cron expression {trimmed:?}: {e}"))
    })?;
    schedule
        .after(&from)
        .next()
        .ok_or_else(|| CommonError::Validation(format!("cron {trimmed:?} has no next occurrence")))
}

/// Execute one SBOM scan (`git clone --depth=1`, native dependency + endpoint scan).
pub async fn execute_sbom_scan(
    scan: &PendingSbomScan,
    git_bin: &Path,
) -> Result<(
    Vec<SbomComponent>,
    Vec<DiscoveredEndpoint>,
    Vec<String>,
    Option<String>,
)> {
    let raw_url = scan.repository_url.as_deref().ok_or_else(|| {
        CommonError::Validation(format!("SBOM scan {} has no repository_url", scan.id))
    })?;
    let (repo_url, branch) = validate_git_inputs(raw_url, scan.repository_branch.as_deref())?;
    let safe_url = sanitize_git_url(&repo_url);

    let clone_dir = TempDir::new()
        .map_err(|e| CommonError::Execution(format!("failed creating SBOM tempdir: {e}")))?;

    let mut cmd = Command::new(git_bin);
    cmd.kill_on_drop(true)
        .env("GIT_TERMINAL_PROMPT", "0")
        .arg("clone")
        .arg("--depth")
        .arg("1")
        .arg("--single-branch");

    if let Some(b) = &branch {
        cmd.arg("--branch").arg(b);
    }

    // Pass resolved git credentials purely via environment variables so tokens
    // never appear in `/proc/<pid>/cmdline`, git remote URLs, or logs.
    if let Some(token) = scan
        .resolved_token
        .as_deref()
        .map(str::trim)
        .filter(|t| !t.is_empty())
    {
        cmd.env("GIT_CONFIG_COUNT", "1")
            .env("GIT_CONFIG_KEY_0", "http.extraHeader")
            .env(
                "GIT_CONFIG_VALUE_0",
                format!("Authorization: Bearer {token}"),
            );
    }

    cmd.arg(&repo_url).arg(clone_dir.path());

    tracing::info!(
        scan_id = scan.id,
        parent_type = %scan.parent_type,
        parent_id = scan.parent_id,
        repository_url = %safe_url,
        branch = branch.as_deref().unwrap_or("default"),
        "Cloning repository for SBOM scan"
    );

    let out = cmd
        .output()
        .await
        .map_err(|e| CommonError::Execution(format!("failed invoking git clone: {e}")))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(CommonError::Execution(format!(
            "git clone failed for {safe_url} (exit {}): {}",
            out.status,
            stderr.trim()
        )));
    }

    let commit_hash = read_git_head(git_bin, clone_dir.path()).await;
    let root = clone_dir.path().to_path_buf();
    let (components, endpoints, files_scanned) =
        tokio::task::spawn_blocking(move || scan_workspace(&root))
            .await
            .map_err(|e| CommonError::Execution(format!("SBOM workspace scan task failed: {e}")))?;

    Ok((components, endpoints, files_scanned, commit_hash))
}

async fn read_git_head(git_bin: &Path, repo_dir: &Path) -> Option<String> {
    let out = Command::new(git_bin)
        .arg("-C")
        .arg(repo_dir)
        .arg("rev-parse")
        .arg("HEAD")
        .output()
        .await
        .ok()?;
    if out.status.success() {
        let hash = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if !hash.is_empty() {
            return Some(hash);
        }
    }
    None
}

/// Walk an already-checked-out workspace and extract dependency components + HTTP endpoints.
pub fn scan_workspace(root: &Path) -> (Vec<SbomComponent>, Vec<DiscoveredEndpoint>, Vec<String>) {
    let mut stack = vec![root.to_path_buf()];
    let mut files = Vec::new();

    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let file_name = entry.file_name();
            let name = file_name.to_string_lossy();
            let Ok(meta) = std::fs::symlink_metadata(&path) else {
                continue;
            };
            if meta.file_type().is_symlink() {
                continue;
            }
            if meta.is_dir() {
                if !SKIP_DIRS.iter().any(|skip| *skip == name.as_ref()) {
                    stack.push(path);
                }
            } else if meta.is_file() && meta.len() <= MAX_SCAN_FILE_BYTES {
                files.push(path);
            }
        }
    }

    files.sort();

    let mut components = Vec::new();
    let mut endpoints = Vec::new();
    let mut files_scanned = Vec::new();
    let mut seen_components: HashSet<(String, Option<String>, String)> = HashSet::new();

    for abs_path in files {
        let rel_path = abs_path
            .strip_prefix(root)
            .unwrap_or(&abs_path)
            .to_string_lossy()
            .replace('\\', "/");

        let Ok(content) = std::fs::read_to_string(&abs_path) else {
            continue;
        };

        let parsed_deps = parse_dependency_file(&rel_path, &content);
        if !parsed_deps.is_empty() {
            files_scanned.push(rel_path.clone());
            for comp in parsed_deps {
                let key = (
                    comp.name.clone(),
                    comp.version.clone(),
                    comp.package_type.clone(),
                );
                if seen_components.insert(key) {
                    components.push(comp);
                }
            }
        }

        let parsed_eps = parse_endpoint_file(&rel_path, &content);
        if !parsed_eps.is_empty() {
            endpoints.extend(parsed_eps);
        }
    }

    (components, endpoints, files_scanned)
}

/// Completed or failed SBOM scan outcome submitted to `hub-api`.
pub struct SbomScanOutcome<'a> {
    pub success: bool,
    pub components: &'a [SbomComponent],
    pub files_scanned: &'a [String],
    pub commit_hash: Option<String>,
    pub error_message: Option<String>,
    pub scan_duration_ms: i64,
    pub endpoints: &'a [DiscoveredEndpoint],
}

/// Submit completed or failed SBOM scan results back to `hub-api`.
pub async fn report_sbom_result(
    client: &ElderApiClient,
    scan_id: i64,
    outcome: &SbomScanOutcome<'_>,
) -> Result<()> {
    let payload = SubmitSbomResults {
        success: outcome.success,
        components: outcome.components,
        files_scanned: outcome.files_scanned,
        commit_hash: outcome.commit_hash.clone(),
        error_message: outcome.error_message.clone(),
        scan_duration_ms: outcome.scan_duration_ms,
        endpoints: outcome.endpoints,
        endpoints_found: outcome.endpoints.len(),
    };
    let _: Value = client
        .post_json(&format!("/api/v1/sbom/scans/{scan_id}/results"), &payload)
        .await?;
    Ok(())
}

/// Fetch due SBOM schedules from `hub-api`, create pending scans, and advance `next_run_at`.
pub async fn process_due_schedules(client: &ElderApiClient) -> Result<usize> {
    let due: Vec<DueSbomSchedule> = client.get_json("/api/v1/sbom/schedules/due").await?;
    let mut triggered = 0usize;

    for sched in due {
        let now = Utc::now();
        let next_run = match compute_next_run(&sched.schedule_cron, now) {
            Ok(dt) => dt,
            Err(err) => {
                tracing::error!(
                    schedule_id = sched.id,
                    cron = %sched.schedule_cron,
                    error = %err,
                    "Skipping SBOM schedule with invalid cron expression"
                );
                continue;
            }
        };

        let create_req = TriggerSbomScanRequest {
            parent_type: &sched.parent_type,
            parent_id: sched.parent_id,
            scan_type: "git_clone",
        };
        if let Err(err) = client
            .post_json::<_, Value>("/api/v1/sbom/scans", &create_req)
            .await
        {
            tracing::error!(
                schedule_id = sched.id,
                parent_type = %sched.parent_type,
                parent_id = sched.parent_id,
                error = %err,
                "Failed creating scan for due SBOM schedule"
            );
            continue;
        }

        let update_req = UpdateScheduleRunRequest {
            last_run_at: now.to_rfc3339(),
            next_run_at: next_run.to_rfc3339(),
        };
        if let Err(err) = client
            .put_json::<_, Value>(&format!("/api/v1/sbom/schedules/{}", sched.id), &update_req)
            .await
        {
            tracing::error!(
                schedule_id = sched.id,
                error = %err,
                "Failed advancing next_run_at on SBOM schedule"
            );
            continue;
        }

        triggered += 1;
    }

    let _ = json!(null);
    Ok(triggered)
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn sanitizes_git_credentials_and_rejects_unsafe_inputs() {
        assert_eq!(
            sanitize_git_url("https://oauth2:ghp_secret123@github.com/org/repo.git"),
            "https://github.com/org/repo.git"
        );
        assert!(validate_git_inputs("file:///etc/passwd", None).is_err());
        assert!(
            validate_git_inputs("https://github.com/a/b.git", Some("--upload-pack=sh")).is_err()
        );
        assert!(validate_git_inputs("https://github.com/a/b.git", Some("release/v4.0.X")).is_ok());
    }

    #[test]
    fn computes_next_cron_run_for_5_and_6_field_expressions() {
        let base = Utc.with_ymd_and_hms(2026, 11, 20, 12, 10, 0).unwrap();
        let next_hourly = compute_next_run("0 * * * *", base).expect("5-field cron");
        assert_eq!(
            next_hourly,
            Utc.with_ymd_and_hms(2026, 11, 20, 13, 0, 0).unwrap()
        );

        let next_step = compute_next_run("0 */15 * * * *", base).expect("6-field cron");
        assert_eq!(
            next_step,
            Utc.with_ymd_and_hms(2026, 11, 20, 12, 15, 0).unwrap()
        );

        assert!(compute_next_run("not a cron", base).is_err());
    }

    #[test]
    fn walks_workspace_and_collects_manifests_and_endpoints() {
        let tmp = TempDir::new().expect("tempdir");
        std::fs::write(tmp.path().join("requirements.txt"), "quart==0.20.0\n").unwrap();
        std::fs::create_dir_all(tmp.path().join("src")).unwrap();
        std::fs::write(
            tmp.path().join("src/routes.py"),
            "from flask import Flask\n@app.get('/health')\ndef health():\n    return 'ok'\n",
        )
        .unwrap();
        std::fs::create_dir_all(tmp.path().join("node_modules/fake")).unwrap();
        std::fs::write(
            tmp.path().join("node_modules/fake/package.json"),
            r#"{"dependencies":{"ignored":"1.0.0"}}"#,
        )
        .unwrap();

        let (comps, eps, files) = scan_workspace(tmp.path());
        assert_eq!(files, vec!["requirements.txt".to_string()]);
        assert_eq!(comps.len(), 1);
        assert_eq!(comps[0].name, "quart");
        assert_eq!(eps.len(), 1);
        assert_eq!(eps[0].path, "/health");
    }

    #[tokio::test]
    async fn process_due_schedules_creates_scans_and_updates_next_run() {
        use elder_svc_common::ServiceTokenProvider;
        use std::sync::{Arc, Mutex};
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        let seen_requests: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let seen_clone = Arc::clone(&seen_requests);

        tokio::spawn(async move {
            let responses = [
                (
                    200,
                    r#"[{"id":7,"parent_type":"service","parent_id":99,"schedule_cron":"0 * * * *"}]"#,
                ),
                (201, r#"{"id":101,"status":"pending"}"#),
                (200, r#"{"id":7,"is_active":true}"#),
            ];
            for (code, body) in responses {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let mut buf = [0u8; 4096];
                let n = sock.read(&mut buf).await.unwrap_or(0);
                let req = String::from_utf8_lossy(&buf[..n]);
                if let Some(line) = req.lines().next() {
                    seen_clone.lock().expect("lock").push(line.to_string());
                }
                let resp = format!(
                    "HTTP/1.1 {code} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = sock.write_all(resp.as_bytes()).await;
            }
        });

        let auth = ServiceTokenProvider::from_env_lookup(
            "svc-scanner",
            vec!["sbom:read".into(), "sbom:write".into()],
            |k| match k {
                "JWT_SECRET_KEY" => Some("x".repeat(32)),
                _ => None,
            },
        )
        .expect("auth");
        let client = ElderApiClient::new(format!("http://{addr}"), auth).expect("client");

        let count = process_due_schedules(&client)
            .await
            .expect("process due schedules");
        assert_eq!(count, 1);

        let recorded = seen_requests.lock().expect("lock").clone();
        assert_eq!(recorded.len(), 3);
        assert!(recorded[0].starts_with("GET /api/v1/sbom/schedules/due "));
        assert!(recorded[1].starts_with("POST /api/v1/sbom/scans "));
        assert!(recorded[2].starts_with("PUT /api/v1/sbom/schedules/7 "));
    }
}
