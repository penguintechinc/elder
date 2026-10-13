# Elder Observability Assets

Version-controlled dashboards, alert rules, and SLO definitions built against
the metrics and spans Elder's services **actually emit** via OpenTelemetry.
Replaces the dashboards/alerts that used to live under
`infrastructure/monitoring/` and the `prometheus`/`alertmanager`/`grafana`
services in `docker-compose.yml`, which referenced Prometheus-client-style
metric names (`http_requests_total`, `flask_http_request_total`,
`elder_incident_issue_active`, `elder_entities_total`, ...) from a
`/metrics` scrape endpoint the app has never exposed. Both were removed in
this change -- see "What was removed" below.

## What Elder actually emits (verified against source, 2026-09-23)

Elder ships OTel SDKs configured in `shared/observability.py:init_telemetry`
and pushes traces/metrics/logs via OTLP HTTP to whatever
`OTEL_EXPORTER_OTLP_ENDPOINT` is configured for the environment (see
`k8s/helm/elder/templates/configmap-otel.yaml` + `values.yaml` `otel.endpoint`
-- today that points at the in-cluster SigNoz OTel collector; nothing here
hardcodes that). No custom OTel `View`/bucket-boundary config exists, so all
histograms use the SDK's default explicit bucket boundaries: `0, 5, 10, 25,
50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000` (in each
histogram's own unit).

| Instrument | Type | Unit | Attributes | Source |
|---|---|---|---|---|
| `http.server.duration` | Histogram | ms | `http.method`, `http.host`, `http.scheme`, `http.status_code`, `http.flavor`, `http.server_name`, `net.host.name`, `net.host.port` | ASGI auto-instrumentation, `apps/api/main.py` -> `auto_instrument_app` -> `shared/observability.py` (old/default HTTP semconv -- `OTEL_SEMCONV_STABILITY_OPT_IN` is unset anywhere in this repo, confirmed against `opentelemetry-instrumentation-asgi==0.64b0` source) |
| `http.server.active_requests` | UpDownCounter | 1 | `http.method`, `http.host`, `http.scheme`, `http.flavor`, `http.server_name` | same |
| `worker.discovery.jobs.executed` | Counter | 1 | `provider`, `status` | `apps/worker/main.py:_init_otel_metrics`, recorded in `_run_discovery_poll` |
| `worker.sync.operations` | Counter | 1 | `connector`, `status` (`success`/`partial`/`failed`) | same, recorded in `_sync_connector` |
| `worker.sync.errors` | Counter | 1 | `connector` | same |
| `worker.discovery.poll.duration` | Histogram | s | (none) | same, recorded in `_run_discovery_poll` |
| `worker.sync.duration` | Histogram | s | `connector` | same, recorded in `_sync_connector` |
| `worker.jobbus.job.duration` | Histogram | s | `job.type`, `job.group`, `status` (`success`/`duplicate`/`no_handler`/`error`) | same, recorded in `_process_job` |
| `scanner.scan.duration` | Histogram | s | `job.provider`, `status` | `apps/scanner/main.py`, recorded in `execute_job` / `execute_sbom_scan` |

Spans: `worker.process_job` (attrs `job.id`, `job.type`, `job.group`;
continues the trace extracted from the job envelope), `scanner.execute_job`
(attrs `job.id`, `job.provider`), `scanner.execute_sbom_scan` (attrs
`sbom.scan_id`), plus whatever spans the ASGI/httpx auto-instrumentation
produces for `apps/api`.

## PromQL naming translation

The dashboards and alert rules in this directory are written in PromQL
against a Prometheus-compatible query layer (Grafana + a Prometheus/Mimir/
Cortex datasource, or SigNoz's Prometheus-compatible query API -- both are
valid ways to query the same OTLP data Elder already exports). PromQL cannot
express dots in metric or label names, so every query here applies the
standard OTel->Prometheus translation:

- Metric names: `.` -> `_` (`http.server.duration` -> `http_server_duration`)
- Label/attribute names: `.` -> `_` (`http.status_code` -> `http_status_code`)
- Counters get a `_total` suffix (`worker.sync.errors` -> `worker_sync_errors_total`)
- Histograms expand to `_bucket` / `_sum` / `_count` (`http_server_duration_bucket`, etc.)

Some collector/backend configurations additionally append a unit suffix
(e.g. `_milliseconds`, `_seconds`) -- this repo does not assume that; verify
against your actual OTel Collector / backend config (`add_metric_suffixes`
for the Prometheus/Prometheus-remote-write exporter) and adjust the queries
if your pipeline adds one.

## Layout

```
observability/
  dashboards/
    elder-api-red.json                 # Grafana dashboard JSON (schemaVersion 39): request rate, error rate, latency p50/95/99, in-flight requests
    elder-worker-scanner-jobs.json     # Grafana dashboard JSON: job/sync/scan duration + failure rate
  alerts/
    elder-api-alerts.yaml              # Prometheus alerting-rule format: error rate, p95 latency, metric-absence (dead exporter / no traffic)
    elder-worker-scanner-alerts.yaml   # same format: job/sync/scan failure rate, discovery-poll-stalled
  slo/
    elder-api-slo.yaml                 # OpenSLO v1 YAML: availability (99.5%) + latency (95% < 1s), 30d rolling
    elder-worker-scanner-slo.yaml      # OpenSLO v1 YAML: job success (99%), scan success (98%), job latency (p95 < 750s)
```

## Loading these assets

Nothing here hardcodes a vendor endpoint or credential, per
`critical-rules.md` Observability (OTel). Point whichever tool you load
these into at the same OTLP-derived data Elder already exports:

- **Dashboards** (Grafana JSON): import via the Grafana UI (`Dashboards ->
  Import`) or `POST /api/dashboards/db`, after adding a Prometheus-compatible
  datasource that reads from your OTLP backend (SigNoz's query-service
  exposes a Prometheus-compatible API; Grafana Mimir/Cortex/Thanos work
  directly). The datasource UID/URL is an environment-specific Grafana
  config, not baked into these files.
- **Alerts**: load with any Prometheus-rule-file-compatible ruler pointed at
  the same query endpoint -- Prometheus + Alertmanager, Grafana Mimir/Cortex
  ruler, or SigNoz's own alert rule import (which accepts PromQL-style
  conditions). `interval`/`for`/`labels`/`annotations` follow the standard
  Prometheus alerting-rule schema.
- **SLOs**: the OpenSLO v1 YAML is a portable spec, not a deploy artifact by
  itself -- feed it to whatever SLO/error-budget tool your stack uses (Sloth,
  Pyrra, Grafana's SLO plugin, SigNoz alerts built from the same PromQL) or
  read it as documentation of the target + error budget when reviewing an
  incident.

## Known gaps (do not silently "fix" by inventing a metric)

- **No queue-depth metric.** Neither `apps/worker` nor `shared/jobbus` emits
  a queue-depth/backlog gauge (no `create_observable_gauge` over the Redis
  Streams pending/length count today). The worker/scanner dashboard and
  alerts intentionally have no queue-depth panel or alert. Add
  `worker.jobbus.queue.depth` (or similar) in app code first, then extend
  these assets -- don't approximate it from job-duration metrics.
- **`apps/worker` and `apps/scanner` have no httpx/DB auto-instrumentation.**
  `shared/observability.py:auto_instrument_app` (which wires up
  `HTTPXClientInstrumentor`, Redis, and psycopg2 instrumentation) is only
  called from `apps/api/main.py`. Worker and scanner get the manual
  spans/metrics listed above plus SDK init, but no automatic spans for their
  outbound HTTP calls (e.g. scanner -> API) or DB/Redis calls. There is
  intentionally no "external dependency latency" panel for worker/scanner
  here because there's no span/metric to query yet.
- **`http.server.duration`'s old-semconv attribute set has no `http.route`.**
  The old HTTP semantic conventions this ASGI version reports under don't
  include a route/path attribute on the duration histogram (only
  `http.method`, `http.host`, `http.scheme`, `http.status_code`,
  `http.flavor`, `http.server_name`, `net.host.name`, `net.host.port` --
  verified against the `_server_duration_attrs_old` list in
  `opentelemetry-instrumentation==0.64b0`). Per-route latency breakdown isn't
  possible from this metric; use trace spans (which do carry the route) for
  that, not a fabricated `http.route` label on the metric.

## What was removed

- `infrastructure/monitoring/` (`alerts.yml`, `prometheus.yml`,
  `grafana-dashboards.yml`, `grafana-datasources.yml`,
  `grafana-elder-dashboard.json`, `alertmanager.yml`) -- alert expressions and
  dashboard panels referenced `http_requests_total`,
  `http_request_duration_seconds_bucket`, `flask_http_request_total`,
  `grpc_server_handled_total`, `elder_incident_issue_active`,
  `elder_entities_total`, `elder_organizations_total`,
  `elder_dependencies_total`, `container_memory_usage_bytes`,
  `node_filesystem_avail_bytes` -- none of which this app emits (no
  `prometheus_client`, no Flask/gRPC Prometheus interceptor, no `/metrics`
  route, no infra-level exporters deployed).
- The `prometheus`, `alertmanager`, and `grafana` services (and their
  `prometheus_data`/`alertmanager_data`/`grafana_data` volumes) from
  `docker-compose.yml`, which scraped the now-removed
  `infrastructure/monitoring/prometheus.yml` config against a
  `api:5000/metrics` endpoint that doesn't exist. `docker-compose.yml`
  overall remains deprecated per its header (Kubernetes/Helm is the
  supported deployment path); this just stops it advertising a monitoring
  stack that was already non-functional.
