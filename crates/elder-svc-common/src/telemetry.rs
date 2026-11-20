//! OpenTelemetry (logs, metrics, traces) + structured `tracing` setup.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use http::header::{HeaderName, HeaderValue};
use opentelemetry::global;
use opentelemetry::metrics::{Counter, Histogram};
use opentelemetry::trace::TracerProvider as _;
use opentelemetry::KeyValue;
use opentelemetry_appender_tracing::layer::OpenTelemetryTracingBridge;
use opentelemetry_http::{Bytes, HttpClient, HttpError, Request, Response, ResponseBodyTooLarge};
use opentelemetry_otlp::{Protocol, WithExportConfig, WithHttpConfig};
use opentelemetry_sdk::logs::log_processor_with_async_runtime::BatchLogProcessor;
use opentelemetry_sdk::logs::SdkLoggerProvider;
use opentelemetry_sdk::metrics::periodic_reader_with_async_runtime::PeriodicReader;
use opentelemetry_sdk::metrics::SdkMeterProvider;
use opentelemetry_sdk::runtime;
use opentelemetry_sdk::trace::span_processor_with_async_runtime::BatchSpanProcessor;
use opentelemetry_sdk::trace::SdkTracerProvider;
use opentelemetry_sdk::Resource;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;
use tracing_subscriber::EnvFilter;

use crate::error::{CommonError, Result};

const MAX_OTLP_RESPONSE_BYTES: usize = 4 * 1024 * 1024;
const OTLP_FLUSH_TIMEOUT: Duration = Duration::from_secs(5);

/// Supported OTLP transport protocols.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OtlpProtocol {
    /// OTLP over HTTP/protobuf (default port 4318).
    HttpProtobuf,
    /// OTLP over gRPC/Tonic (default port 4317).
    Grpc,
}

/// Telemetry initialization settings parsed from standard environment variables.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TelemetryConfig {
    /// Service name emitted as `service.name` resource attribute (`OTEL_SERVICE_NAME`).
    pub service_name: String,
    /// Collector endpoint (`OTEL_EXPORTER_OTLP_ENDPOINT`), or `None` to disable OTLP export.
    pub otlp_endpoint: Option<String>,
    /// Selected OTLP protocol (`OTEL_EXPORTER_OTLP_PROTOCOL` or port-inferred).
    pub protocol: OtlpProtocol,
    /// Emit structured JSON logs (`true`) or human-readable output (`false`) from `LOG_FORMAT`.
    pub json_logs: bool,
}

impl TelemetryConfig {
    /// Parse telemetry settings from environment variables.
    pub fn from_env(default_service_name: &str) -> Result<Self> {
        Self::from_env_lookup(default_service_name, |name| std::env::var(name).ok())
    }

    /// Parse telemetry settings from a lookup function (for deterministic tests).
    pub fn from_env_lookup<F>(default_service_name: &str, lookup: F) -> Result<Self>
    where
        F: Fn(&str) -> Option<String>,
    {
        let service_name = non_empty(lookup("OTEL_SERVICE_NAME"))
            .unwrap_or_else(|| default_service_name.to_string());
        let otlp_endpoint = non_empty(lookup("OTEL_EXPORTER_OTLP_ENDPOINT"));
        let protocol = resolve_protocol(
            otlp_endpoint.as_deref(),
            non_empty(lookup("OTEL_EXPORTER_OTLP_PROTOCOL")).as_deref(),
        )?;
        let json_logs = resolve_json_logs(non_empty(lookup("LOG_FORMAT")).as_deref())?;

        Ok(Self {
            service_name,
            otlp_endpoint,
            protocol,
            json_logs,
        })
    }
}

/// Guard holding initialized OpenTelemetry providers for clean shutdown flush.
pub struct TelemetryGuard {
    tracer_provider: Option<SdkTracerProvider>,
    meter_provider: Option<SdkMeterProvider>,
    logger_provider: Option<SdkLoggerProvider>,
}

impl TelemetryGuard {
    /// Flush pending telemetry batches cleanly with a bounded timeout.
    ///
    /// A dead or unreachable OTLP collector emits a warning and never fails shutdown.
    pub async fn shutdown(mut self) {
        let tracer = self.tracer_provider.take();
        let meter = self.meter_provider.take();
        let logger = self.logger_provider.take();

        let flush_handle = tokio::task::spawn_blocking(move || {
            if let Some(tp) = tracer {
                if let Err(err) = tp.shutdown() {
                    tracing::warn!(error = %err, "Failed to flush OTLP tracer provider cleanly");
                }
            }
            if let Some(mp) = meter {
                if let Err(err) = mp.shutdown() {
                    tracing::warn!(error = %err, "Failed to flush OTLP meter provider cleanly");
                }
            }
            if let Some(lp) = logger {
                if let Err(err) = lp.shutdown() {
                    tracing::warn!(error = %err, "Failed to flush OTLP logger provider cleanly");
                }
            }
        });

        match tokio::time::timeout(OTLP_FLUSH_TIMEOUT, flush_handle).await {
            Ok(Ok(())) => {}
            Ok(Err(join_err)) => {
                tracing::warn!(error = %join_err, "OTLP shutdown task failed");
            }
            Err(_) => {
                tracing::warn!(
                    timeout_secs = OTLP_FLUSH_TIMEOUT.as_secs(),
                    "Timed out flushing OTLP telemetry; continuing shutdown"
                );
            }
        }
    }
}

/// Standard counter + latency histogram bundle emitted by Elder data-plane services.
#[derive(Clone)]
pub struct ServiceMetrics {
    runs_total: Counter<u64>,
    jobs_total: Counter<u64>,
    duration_seconds: Histogram<f64>,
}

impl ServiceMetrics {
    /// Create service metrics under the global OpenTelemetry meter.
    pub fn new(meter_name: &'static str, prefix: &str) -> Self {
        let meter = global::meter(meter_name);
        let runs_total = meter
            .u64_counter(format!("{prefix}.runs_total"))
            .with_description("Total service execution cycles")
            .build();
        let jobs_total = meter
            .u64_counter(format!("{prefix}.jobs_total"))
            .with_description("Total scanning jobs processed")
            .build();
        let duration_seconds = meter
            .f64_histogram(format!("{prefix}.duration_seconds"))
            .with_description("Execution duration in seconds")
            .with_unit("s")
            .build();

        Self {
            runs_total,
            jobs_total,
            duration_seconds,
        }
    }

    /// Record a top-level cycle execution.
    pub fn record_run(&self, success: bool, duration_secs: f64) {
        let status = if success { "ok" } else { "error" };
        let attrs = [KeyValue::new("status", status)];
        self.runs_total.add(1, &attrs);
        self.duration_seconds.record(duration_secs, &attrs);
    }

    /// Record an individual job/task execution.
    pub fn record_job(&self, kind: &str, success: bool, duration_secs: f64) {
        let status = if success { "ok" } else { "error" };
        let attrs = [
            KeyValue::new("job_kind", kind.to_string()),
            KeyValue::new("status", status),
        ];
        self.jobs_total.add(1, &attrs);
        self.duration_seconds.record(duration_secs, &attrs);
    }
}

/// Adapter using `reqwest` 0.12 (`rustls-tls`) so `opentelemetry-otlp` HTTP export
/// shares the workspace TLS stack with zero OpenSSL or `aws-lc-sys` build dependency.
#[derive(Debug, Clone)]
struct OtelReqwestClient {
    client: reqwest::Client,
}

impl OtelReqwestClient {
    fn new(timeout: Duration) -> std::result::Result<Self, reqwest::Error> {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .timeout(timeout)
            .build()?;
        Ok(Self { client })
    }
}

#[async_trait]
impl HttpClient for OtelReqwestClient {
    async fn send_bytes(
        &self,
        request: Request<Bytes>,
    ) -> std::result::Result<Response<Bytes>, HttpError> {
        let (parts, body) = request.into_parts();
        let method = reqwest::Method::from_bytes(parts.method.as_str().as_bytes())
            .map_err(|e| Box::new(e) as HttpError)?;
        let url = parts.uri.to_string();

        let mut builder = self.client.request(method, &url).body(body);
        for (name, value) in &parts.headers {
            builder = builder.header(name.as_str(), value.as_bytes());
        }

        let mut res = builder.send().await.map_err(|e| Box::new(e) as HttpError)?;
        if let Some(len) = res.content_length() {
            if len as usize > MAX_OTLP_RESPONSE_BYTES {
                return Err(Box::new(ResponseBodyTooLarge::new()));
            }
        }

        let status = http::StatusCode::from_u16(res.status().as_u16())
            .map_err(|e| Box::new(e) as HttpError)?;
        let mut resp_builder = http::Response::builder().status(status);
        for (name, value) in res.headers() {
            if let (Ok(hn), Ok(hv)) = (
                HeaderName::from_bytes(name.as_str().as_bytes()),
                HeaderValue::from_bytes(value.as_bytes()),
            ) {
                resp_builder = resp_builder.header(hn, hv);
            }
        }

        let mut collected = Vec::new();
        while let Some(chunk) = res.chunk().await.map_err(|e| Box::new(e) as HttpError)? {
            if collected.len().saturating_add(chunk.len()) > MAX_OTLP_RESPONSE_BYTES {
                return Err(Box::new(ResponseBodyTooLarge::new()));
            }
            collected.extend_from_slice(&chunk);
        }

        resp_builder
            .body(Bytes::from(collected))
            .map_err(|e| Box::new(e) as HttpError)
    }
}

/// Initialize structured `tracing` and optional OTLP logs, metrics, and traces.
///
/// Must be called inside a Tokio runtime when OTLP export is enabled.
/// If building an OTLP exporter fails, logs a warning and continues with local logging.
pub fn init(config: &TelemetryConfig) -> TelemetryGuard {
    let mut deferred_warning: Option<String> = None;

    let (tracer_provider, meter_provider, logger_provider) = if config.otlp_endpoint.is_some() {
        match build_otlp_providers(config) {
            Ok((tp, mp, lp)) => {
                global::set_tracer_provider(tp.clone());
                global::set_meter_provider(mp.clone());
                (Some(tp), Some(mp), Some(lp))
            }
            Err(err) => {
                deferred_warning = Some(format!(
                    "OTLP exporter initialization degraded to stdout tracing: {err}"
                ));
                (None, None, None)
            }
        }
    } else {
        (None, None, None)
    };

    let otel_trace_layer = tracer_provider.as_ref().map(|tp| {
        tracing_opentelemetry::layer().with_tracer(tp.tracer(config.service_name.clone()))
    });
    let otel_log_layer = logger_provider
        .as_ref()
        .map(OpenTelemetryTracingBridge::new);

    let env_filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));

    let (json_fmt, pretty_fmt) = if config.json_logs {
        (
            Some(
                tracing_subscriber::fmt::layer()
                    .json()
                    .with_current_span(false)
                    .with_target(true),
            ),
            None,
        )
    } else {
        (
            None,
            Some(tracing_subscriber::fmt::layer().with_target(true)),
        )
    };

    let init_res = tracing_subscriber::registry()
        .with(env_filter)
        .with(json_fmt)
        .with(pretty_fmt)
        .with(otel_trace_layer)
        .with(otel_log_layer)
        .try_init();

    if let Err(err) = init_res {
        // Subscriber already registered (common across parallel unit tests).
        tracing::debug!(error = %err, "tracing subscriber already initialized");
    }

    if let Some(warning) = deferred_warning {
        tracing::warn!("{warning}");
    }

    TelemetryGuard {
        tracer_provider,
        meter_provider,
        logger_provider,
    }
}

fn build_otlp_providers(
    config: &TelemetryConfig,
) -> Result<(SdkTracerProvider, SdkMeterProvider, SdkLoggerProvider)> {
    let resource = Resource::builder()
        .with_service_name(config.service_name.clone())
        .build();

    let (span_exporter, metric_exporter, log_exporter) = match config.protocol {
        OtlpProtocol::HttpProtobuf => {
            let http_client: Arc<dyn HttpClient> = Arc::new(
                OtelReqwestClient::new(Duration::from_secs(10))
                    .map_err(|e| CommonError::Execution(format!("OTLP HTTP client init: {e}")))?,
            );
            let span_exp = opentelemetry_otlp::SpanExporter::builder()
                .with_http()
                .with_protocol(Protocol::HttpBinary)
                .with_shared_http_client(Arc::clone(&http_client))
                .build()
                .map_err(|e| CommonError::Execution(format!("OTLP span exporter: {e}")))?;
            let metric_exp = opentelemetry_otlp::MetricExporter::builder()
                .with_http()
                .with_protocol(Protocol::HttpBinary)
                .with_shared_http_client(Arc::clone(&http_client))
                .build()
                .map_err(|e| CommonError::Execution(format!("OTLP metric exporter: {e}")))?;
            let log_exp = opentelemetry_otlp::LogExporter::builder()
                .with_http()
                .with_protocol(Protocol::HttpBinary)
                .with_shared_http_client(http_client)
                .build()
                .map_err(|e| CommonError::Execution(format!("OTLP log exporter: {e}")))?;
            (span_exp, metric_exp, log_exp)
        }
        OtlpProtocol::Grpc => {
            let span_exp = opentelemetry_otlp::SpanExporter::builder()
                .with_tonic()
                .build()
                .map_err(|e| CommonError::Execution(format!("OTLP gRPC span exporter: {e}")))?;
            let metric_exp = opentelemetry_otlp::MetricExporter::builder()
                .with_tonic()
                .build()
                .map_err(|e| CommonError::Execution(format!("OTLP gRPC metric exporter: {e}")))?;
            let log_exp = opentelemetry_otlp::LogExporter::builder()
                .with_tonic()
                .build()
                .map_err(|e| CommonError::Execution(format!("OTLP gRPC log exporter: {e}")))?;
            (span_exp, metric_exp, log_exp)
        }
    };

    let span_processor = BatchSpanProcessor::builder(span_exporter, runtime::Tokio).build();
    let tracer_provider = SdkTracerProvider::builder()
        .with_resource(resource.clone())
        .with_span_processor(span_processor)
        .build();

    let metric_reader = PeriodicReader::builder(metric_exporter, runtime::Tokio).build();
    let meter_provider = SdkMeterProvider::builder()
        .with_resource(resource.clone())
        .with_reader(metric_reader)
        .build();

    let log_processor = BatchLogProcessor::builder(log_exporter, runtime::Tokio).build();
    let logger_provider = SdkLoggerProvider::builder()
        .with_resource(resource)
        .with_log_processor(log_processor)
        .build();

    Ok((tracer_provider, meter_provider, logger_provider))
}

fn resolve_protocol(endpoint: Option<&str>, configured: Option<&str>) -> Result<OtlpProtocol> {
    if let Some(proto) = configured {
        return match proto.to_ascii_lowercase().as_str() {
            "grpc" => Ok(OtlpProtocol::Grpc),
            "http/protobuf" | "http-proto" | "http_proto" | "http" => {
                Ok(OtlpProtocol::HttpProtobuf)
            }
            other => Err(CommonError::Config(format!(
                "unsupported OTEL_EXPORTER_OTLP_PROTOCOL {other:?}"
            ))),
        };
    }

    if let Some(ep) = endpoint {
        if ep.contains(":4317") {
            return Ok(OtlpProtocol::Grpc);
        }
    }
    Ok(OtlpProtocol::HttpProtobuf)
}

fn resolve_json_logs(configured: Option<&str>) -> Result<bool> {
    match configured {
        None => Ok(true),
        Some(v) => match v.to_ascii_lowercase().as_str() {
            "json" => Ok(true),
            "pretty" | "text" | "console" => Ok(false),
            other => Err(CommonError::Config(format!(
                "unsupported LOG_FORMAT {other:?} (expected json or pretty)"
            ))),
        },
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
    use std::collections::HashMap;

    fn lookup_from(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
            .collect();
        move |k: &str| map.get(k).cloned()
    }

    #[test]
    fn parses_telemetry_env_defaults_and_overrides() {
        let default_cfg = TelemetryConfig::from_env_lookup("svc-scanning", lookup_from(&[]))
            .expect("default config");
        assert_eq!(default_cfg.service_name, "svc-scanning");
        assert_eq!(default_cfg.otlp_endpoint, None);
        assert_eq!(default_cfg.protocol, OtlpProtocol::HttpProtobuf);
        assert!(default_cfg.json_logs);

        let http_cfg = TelemetryConfig::from_env_lookup(
            "svc-scanning",
            lookup_from(&[
                ("OTEL_SERVICE_NAME", "custom-svc"),
                (
                    "OTEL_EXPORTER_OTLP_ENDPOINT",
                    "http://signoz-otel-collector:4318",
                ),
                ("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
                ("LOG_FORMAT", "pretty"),
            ]),
        )
        .expect("http config");
        assert_eq!(http_cfg.service_name, "custom-svc");
        assert_eq!(http_cfg.protocol, OtlpProtocol::HttpProtobuf);
        assert!(!http_cfg.json_logs);

        let grpc_inferred = TelemetryConfig::from_env_lookup(
            "svc-scanning",
            lookup_from(&[("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")]),
        )
        .expect("grpc inferred");
        assert_eq!(grpc_inferred.protocol, OtlpProtocol::Grpc);
    }

    #[test]
    fn rejects_invalid_protocol_or_log_format() {
        let bad_proto = TelemetryConfig::from_env_lookup(
            "svc-scanning",
            lookup_from(&[("OTEL_EXPORTER_OTLP_PROTOCOL", "soap")]),
        );
        assert!(matches!(bad_proto, Err(CommonError::Config(_))));

        let bad_log =
            TelemetryConfig::from_env_lookup("svc-scanning", lookup_from(&[("LOG_FORMAT", "xml")]));
        assert!(matches!(bad_log, Err(CommonError::Config(_))));
    }

    #[tokio::test]
    async fn init_and_shutdown_without_collector_succeeds() {
        let cfg = TelemetryConfig::from_env_lookup("svc-scanning", lookup_from(&[])).expect("cfg");
        let guard = init(&cfg);
        let metrics = ServiceMetrics::new("test", "test");
        metrics.record_run(true, 0.01);
        guard.shutdown().await;
    }

    #[tokio::test]
    async fn otlp_http_exporters_emit_logs_metrics_and_traces_over_wire() {
        use opentelemetry::logs::{LogRecord as _, Logger as _, LoggerProvider as _};
        use opentelemetry::metrics::MeterProvider as _;
        use opentelemetry::trace::Tracer as _;
        use std::sync::Mutex;
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let addr = listener.local_addr().expect("addr");
        let paths: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let paths_clone = Arc::clone(&paths);

        let server = tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else {
                    break;
                };
                let p_ref = Arc::clone(&paths_clone);
                tokio::spawn(async move {
                    let mut buf = vec![0u8; 8192];
                    let n = sock.read(&mut buf).await.unwrap_or(0);
                    let req = String::from_utf8_lossy(&buf[..n]);
                    if let Some(first_line) = req.lines().next() {
                        if let Some(path) = first_line.split_whitespace().nth(1) {
                            p_ref.lock().expect("lock").push(path.to_string());
                        }
                    }
                    let resp = "HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
                    let _ = sock.write_all(resp.as_bytes()).await;
                    let _ = sock.shutdown().await;
                });
            }
        });

        let http_client: Arc<dyn HttpClient> =
            Arc::new(OtelReqwestClient::new(Duration::from_secs(5)).expect("client"));
        let span_exp = opentelemetry_otlp::SpanExporter::builder()
            .with_http()
            .with_protocol(Protocol::HttpBinary)
            .with_endpoint(format!("http://{addr}/v1/traces"))
            .with_shared_http_client(Arc::clone(&http_client))
            .build()
            .expect("span exporter");
        let metric_exp = opentelemetry_otlp::MetricExporter::builder()
            .with_http()
            .with_protocol(Protocol::HttpBinary)
            .with_endpoint(format!("http://{addr}/v1/metrics"))
            .with_shared_http_client(Arc::clone(&http_client))
            .build()
            .expect("metric exporter");
        let log_exp = opentelemetry_otlp::LogExporter::builder()
            .with_http()
            .with_protocol(Protocol::HttpBinary)
            .with_endpoint(format!("http://{addr}/v1/logs"))
            .with_shared_http_client(http_client)
            .build()
            .expect("log exporter");

        let tp = SdkTracerProvider::builder()
            .with_span_processor(BatchSpanProcessor::builder(span_exp, runtime::Tokio).build())
            .build();
        let mp = SdkMeterProvider::builder()
            .with_reader(PeriodicReader::builder(metric_exp, runtime::Tokio).build())
            .build();
        let lp = SdkLoggerProvider::builder()
            .with_log_processor(BatchLogProcessor::builder(log_exp, runtime::Tokio).build())
            .build();

        tp.tracer("test").in_span("test_span", |_cx| {});
        let counter = mp.meter("test").u64_counter("test.counter").build();
        counter.add(1, &[KeyValue::new("ok", "true")]);
        let logger = lp.logger("test");
        let mut rec = logger.create_log_record();
        rec.set_body("test log".into());
        logger.emit(rec);

        TelemetryGuard {
            tracer_provider: Some(tp),
            meter_provider: Some(mp),
            logger_provider: Some(lp),
        }
        .shutdown()
        .await;

        server.abort();
        let recorded = paths.lock().expect("lock").clone();
        assert!(
            recorded.contains(&"/v1/traces".to_string()),
            "got {recorded:?}"
        );
        assert!(
            recorded.contains(&"/v1/metrics".to_string()),
            "got {recorded:?}"
        );
        assert!(
            recorded.contains(&"/v1/logs".to_string()),
            "got {recorded:?}"
        );
    }
}
