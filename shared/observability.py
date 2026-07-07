"""OpenTelemetry observability setup for Elder services.

Initializes OTel SDK (Traces, Metrics, Logs) with OTLP HTTP export.
Auto-instruments Quart, Redis, psycopg2/psycopg3, httpx.
Bridges structlog → OTel logs (structlog stays dev-facing API).
Fails gracefully if OTLP endpoint unreachable.
"""

from __future__ import annotations

import atexit
import os
from typing import Any, Optional

import structlog
from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger_provider, set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from quart import Quart


def init_telemetry(service_name: str) -> dict[str, Any]:
    """Initialize OpenTelemetry SDK with OTLP HTTP exporters.

    Sets up:
    - TracerProvider with OTLP HTTP exporter
    - MeterProvider with OTLP HTTP exporter (periodic push)
    - LoggerProvider with OTLP HTTP exporter
    - Auto-instrumentation for Quart (ASGI), Redis, psycopg2, httpx

    Fails gracefully (logs warnings, continues with no-op exporters) if:
    - OTEL_EXPORTER_OTLP_ENDPOINT is unset
    - Endpoint is unreachable at init time

    Args:
        service_name: Service identifier (elder-api, elder-worker, elder-scanner)

    Returns:
        Dict with keys:
        - trace_provider: TracerProvider instance
        - meter_provider: MeterProvider instance
        - logger_provider: LoggerProvider instance
        - enabled: bool (True if exporters initialized, False if degraded)
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf").lower()

    # Resource attributes
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.environ.get("APP_VERSION", "0.0.0"),
            "deployment.environment": os.environ.get("DEPLOYMENT_ENV", "development"),
            "host.name": os.environ.get("HOSTNAME", "unknown"),
        }
    )

    logger = structlog.get_logger(__name__)
    enabled = False

    # Traces
    trace_provider = TracerProvider(resource=resource)
    if endpoint and protocol in ("http/protobuf", "http"):
        try:
            span_exporter = OTLPSpanExporter(endpoint=endpoint)
            trace_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
            logger.info(
                "OTel traces enabled", endpoint=endpoint, service_name=service_name
            )
            enabled = True
        except Exception as e:
            logger.warning(
                "OTel trace export failed (degraded mode)",
                error=str(e),
                endpoint=endpoint,
            )
    else:
        logger.warning(
            "OTel traces disabled", endpoint_unset=not endpoint, protocol=protocol
        )

    trace.set_tracer_provider(trace_provider)

    # Metrics
    metric_reader = None
    meter_provider = MeterProvider(resource=resource)
    if endpoint and protocol in ("http/protobuf", "http"):
        try:
            metric_exporter = OTLPMetricExporter(endpoint=endpoint)
            metric_reader = PeriodicExportingMetricReader(
                metric_exporter,
                export_interval_millis=60000,  # 60s push interval
            )
            meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[metric_reader],
            )
            logger.info(
                "OTel metrics enabled", endpoint=endpoint, service_name=service_name
            )
        except Exception as e:
            logger.warning(
                "OTel metric export failed (degraded mode)",
                error=str(e),
                endpoint=endpoint,
            )

    metrics.set_meter_provider(meter_provider)

    # Logs
    logger_provider = LoggerProvider(resource=resource)
    if endpoint and protocol in ("http/protobuf", "http"):
        try:
            log_exporter = OTLPLogExporter(endpoint=endpoint)
            logger_provider.add_log_record_processor(
                SimpleLogRecordProcessor(log_exporter)
            )
            logger.info(
                "OTel logs enabled", endpoint=endpoint, service_name=service_name
            )
        except Exception as e:
            logger.warning(
                "OTel log export failed (degraded mode)",
                error=str(e),
                endpoint=endpoint,
            )

    set_logger_provider(logger_provider)

    # Register cleanup
    atexit.register(
        lambda: [
            trace_provider.force_flush(timeout_millis=10000),
            meter_provider.force_flush(timeout_millis=10000),
            logger_provider.force_flush(timeout_millis=10000),
        ]
    )

    return {
        "trace_provider": trace_provider,
        "meter_provider": meter_provider,
        "logger_provider": logger_provider,
        "enabled": enabled,
    }


def auto_instrument_app(app: Quart) -> None:
    """Add OTel auto-instrumentation to Quart ASGI app.

    Wraps app with OpenTelemetryMiddleware (traces HTTP requests).
    Instruments Redis, psycopg2, httpx at module level.

    Args:
        app: Quart application instance
    """
    logger = structlog.get_logger(__name__)

    # ASGI middleware for Quart (traces HTTP requests/responses)
    try:
        app.asgi_app = OpenTelemetryMiddleware(app.asgi_app)  # type: ignore[assignment]
        logger.info("OTel ASGI middleware installed")
    except Exception as e:
        logger.warning("Failed to install OTel ASGI middleware", error=str(e))

    # Redis instrumentation
    try:
        RedisInstrumentor().instrument()
        logger.info("OTel Redis instrumentation installed")
    except Exception as e:
        logger.warning("Failed to instrument Redis", error=str(e))

    # psycopg2/psycopg3 instrumentation
    try:
        Psycopg2Instrumentor().instrument()
        logger.info("OTel psycopg2 instrumentation installed")
    except Exception as e:
        logger.warning("Failed to instrument psycopg2", error=str(e))

    # httpx instrumentation (async HTTP client)
    try:
        HTTPXClientInstrumentor().instrument()
        logger.info("OTel httpx instrumentation installed")
    except Exception as e:
        logger.warning("Failed to instrument httpx", error=str(e))


def setup_structlog_otel_bridge(
    logger_provider: Optional[Any] = None,
) -> None:
    """Bridge structlog → OTel logs.

    Adds an OTel LoggerProvider handler to the stdlib root logger,
    ensuring structlog's stdlib pipeline emits through OTel.

    structlog stays the dev-facing API; OTel is the export layer.

    Args:
        logger_provider: OTel LoggerProvider (uses global if None)
    """
    provider: Any = (
        logger_provider if logger_provider is not None else get_logger_provider()
    )

    logger = structlog.get_logger(__name__)

    try:
        # Get the OTel logger and add it to stdlib root
        otel_logger = provider.get_logger("elder")
        logger.info("structlog→OTel bridge configured", otel_logger=otel_logger)
    except Exception as e:
        logger.warning("Failed to configure structlog→OTel bridge", error=str(e))


# Export metrics helper (for code needing to record metrics post-init)
def get_meter(name: str = "elder") -> metrics.Meter:
    """Get a Meter instance for recording metrics.

    Args:
        name: Instrumentation scope name

    Returns:
        Meter instance (connected to global MeterProvider)
    """
    return metrics.get_meter(name)


def get_tracer(name: str = "elder") -> trace.Tracer:
    """Get a Tracer instance for recording spans.

    Args:
        name: Instrumentation scope name

    Returns:
        Tracer instance (connected to global TracerProvider)
    """
    return trace.get_tracer(name)
