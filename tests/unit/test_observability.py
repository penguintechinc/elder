"""Tests for shared/observability.py OTel wiring.

Regression coverage: init_telemetry() used to wire SimpleSpanProcessor /
SimpleLogRecordProcessor, which export synchronously on the caller's
thread -- a dead/slow OTLP collector blocked every request (this caused a
real alpha hang). It must use Batch*Processor instead, which exports on a
background thread with a bounded queue so a dead exporter can only ever
drop/delay telemetry, never the request.
"""

from __future__ import annotations

from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from shared.observability import (
    _OTEL_EXPORT_TIMEOUT_MS,
    _OTEL_MAX_EXPORT_BATCH_SIZE,
    _OTEL_MAX_QUEUE_SIZE,
    _OTEL_SCHEDULE_DELAY_MS,
    init_telemetry,
)


class TestBatchProcessorWiring:
    """init_telemetry() must never wire the synchronous Simple*Processor."""

    def test_traces_use_batch_span_processor(self, monkeypatch) -> None:
        """TracerProvider is wired with BatchSpanProcessor, not SimpleSpanProcessor."""
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1/v1/traces"
        )
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

        result = init_telemetry("elder-test-service")
        trace_provider = result["trace_provider"]

        processors = trace_provider._active_span_processor._span_processors
        assert len(processors) == 1
        assert isinstance(processors[0], BatchSpanProcessor)

        # Bounded queue -- the whole point: once full, a dead/slow collector
        # causes dropped spans on a background thread, never a blocked
        # caller. `maxsize` is the underlying queue's public bound.
        batch_internals = processors[0]._batch_processor
        assert batch_internals._max_queue_size == _OTEL_MAX_QUEUE_SIZE
        assert batch_internals._max_export_batch_size == _OTEL_MAX_EXPORT_BATCH_SIZE

        trace_provider.shutdown()

    def test_logs_use_batch_log_record_processor(self, monkeypatch) -> None:
        """LoggerProvider is wired with BatchLogRecordProcessor, not SimpleLogRecordProcessor."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1/v1/logs")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

        result = init_telemetry("elder-test-service")
        logger_provider = result["logger_provider"]

        processors = logger_provider._multi_log_record_processor._log_record_processors
        assert len(processors) == 1
        assert isinstance(processors[0], BatchLogRecordProcessor)

        logger_provider.shutdown()

    def test_no_endpoint_configured_degrades_without_raising(self, monkeypatch) -> None:
        """Unset OTEL_EXPORTER_OTLP_ENDPOINT -> degraded mode, never raises."""
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

        result = init_telemetry("elder-test-service")

        assert result["enabled"] is False
        result["trace_provider"].shutdown()
        result["logger_provider"].shutdown()

    def test_batch_tuning_constants_are_bounded(self) -> None:
        """Sanity bounds on the shared batch-tuning constants themselves."""
        assert _OTEL_MAX_QUEUE_SIZE > 0
        assert _OTEL_SCHEDULE_DELAY_MS > 0
        assert _OTEL_MAX_EXPORT_BATCH_SIZE > 0
        assert _OTEL_EXPORT_TIMEOUT_MS > 0
        # Export timeout must be well under typical request budgets so a
        # background export attempt is bounded, not effectively unbounded.
        assert _OTEL_EXPORT_TIMEOUT_MS <= 60_000
