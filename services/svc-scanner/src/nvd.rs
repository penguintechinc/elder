//! Periodic NVD CVE enrichment sync trigger against `hub-api`.

use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};

use elder_svc_common::{ElderApiClient, Result};

/// Summary returned by `GET /api/v1/vulnerabilities/nvd-sync/status`.
#[derive(Debug, Clone, Deserialize)]
pub struct NvdSyncStatus {
    pub total_cves: u64,
    pub never_synced: u64,
    pub stale_sync: u64,
    pub recently_synced: u64,
    pub needs_sync: u64,
}

#[derive(Debug, Serialize)]
struct NvdSyncRequest {
    max_vulns: u32,
}

#[derive(Debug, Deserialize)]
struct NvdSyncResponse {
    #[allow(dead_code)]
    message: Option<String>,
    stats: Option<NvdSyncStats>,
}

/// Counts returned by `POST /api/v1/vulnerabilities/nvd-sync`.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct NvdSyncStats {
    #[serde(default)]
    pub processed: u64,
    #[serde(default)]
    pub updated: u64,
    #[serde(default)]
    pub skipped: u64,
    #[serde(default)]
    pub errors: u64,
}

/// Evaluate whether an NVD sync attempt is due based on the elapsed window.
pub fn should_trigger_sync(
    last_sync_at: Option<DateTime<Utc>>,
    interval_secs: u64,
    now: DateTime<Utc>,
) -> bool {
    let Some(prev) = last_sync_at else {
        return true;
    };
    let window = Duration::seconds(i64::try_from(interval_secs).unwrap_or(i64::MAX));
    now.signed_duration_since(prev) >= window
}

/// Query sync backlog from `hub-api` and trigger enrichment when needed.
pub async fn maybe_trigger_nvd_sync(
    client: &ElderApiClient,
    last_sync_at: &mut Option<DateTime<Utc>>,
    interval_secs: u64,
) -> Result<Option<NvdSyncStats>> {
    let now = Utc::now();
    if !should_trigger_sync(*last_sync_at, interval_secs, now) {
        return Ok(None);
    }

    let status: NvdSyncStatus = client
        .get_json("/api/v1/vulnerabilities/nvd-sync/status")
        .await?;
    *last_sync_at = Some(now);
    if status.needs_sync == 0 {
        tracing::debug!(
            total_cves = status.total_cves,
            recently_synced = status.recently_synced,
            "No NVD CVEs require enrichment sync"
        );
        return Ok(None);
    }

    tracing::info!(
        needs_sync = status.needs_sync,
        never_synced = status.never_synced,
        stale_sync = status.stale_sync,
        "Triggering NVD vulnerability enrichment sync"
    );

    let resp: NvdSyncResponse = client
        .post_json(
            "/api/v1/vulnerabilities/nvd-sync",
            &NvdSyncRequest { max_vulns: 500 },
        )
        .await?;
    let stats = resp.stats.unwrap_or_default();
    tracing::info!(
        processed = stats.processed,
        updated = stats.updated,
        skipped = stats.skipped,
        errors = stats.errors,
        "Completed NVD vulnerability enrichment sync"
    );
    Ok(Some(stats))
}

#[cfg(test)]
mod tests {
    use super::*;
    use elder_svc_common::ServiceTokenProvider;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    fn test_client(base_url: String) -> ElderApiClient {
        let auth = ServiceTokenProvider::from_env_lookup(
            "svc-scanner",
            vec!["sbom:read".into(), "vulnerabilities:write".into()],
            |k| match k {
                "JWT_SECRET_KEY" => Some("x".repeat(32)),
                _ => None,
            },
        )
        .expect("auth");
        ElderApiClient::new(base_url, auth).expect("client")
    }

    #[test]
    fn sync_window_respects_interval_and_missing_timestamp() {
        let now = Utc::now();
        assert!(should_trigger_sync(None, 21_600, now));
        assert!(!should_trigger_sync(
            Some(now - Duration::seconds(300)),
            21_600,
            now
        ));
        assert!(should_trigger_sync(
            Some(now - Duration::seconds(21_600)),
            21_600,
            now
        ));
    }

    #[tokio::test]
    async fn maybe_trigger_nvd_sync_honors_backlog_and_triggers_when_due() {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        let hits = Arc::new(AtomicUsize::new(0));
        let hits_c = Arc::clone(&hits);

        tokio::spawn(async move {
            let responses = [
                (
                    200,
                    r#"{"total_cves":10,"never_synced":0,"stale_sync":0,"recently_synced":10,"needs_sync":0}"#,
                ),
                (
                    200,
                    r#"{"total_cves":10,"never_synced":2,"stale_sync":1,"recently_synced":7,"needs_sync":3}"#,
                ),
                (
                    202,
                    r#"{"message":"ok","stats":{"processed":3,"updated":2,"skipped":1,"errors":0}}"#,
                ),
            ];
            for (code, body) in responses {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                hits_c.fetch_add(1, Ordering::SeqCst);
                let mut buf = [0u8; 4096];
                let _ = sock.read(&mut buf).await;
                let resp = format!(
                    "HTTP/1.1 {code} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = sock.write_all(resp.as_bytes()).await;
            }
        });

        let client = test_client(format!("http://{addr}"));
        let mut last_sync = None;
        let skip = maybe_trigger_nvd_sync(&client, &mut last_sync, 21_600)
            .await
            .expect("status ok");
        assert!(skip.is_none());
        assert_eq!(hits.load(Ordering::SeqCst), 1);

        last_sync = None;
        let triggered = maybe_trigger_nvd_sync(&client, &mut last_sync, 21_600)
            .await
            .expect("sync ok")
            .expect("should return stats");
        assert_eq!(triggered.processed, 3);
        assert_eq!(triggered.updated, 2);
        assert_eq!(hits.load(Ordering::SeqCst), 3);
    }
}
