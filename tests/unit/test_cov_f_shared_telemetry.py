"""Coverage tests for ``shared.observability`` and ``shared.logging.logger``."""

import importlib.util
import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import structlog

import shared.logging as shared_logging
from shared import observability as obs
from shared.logging import logger as lg


def _load_fresh(module_path: Path, name: str, blocked: tuple[str, ...]):
    """Execute a source file as a private module while the named imports fail."""
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {b: None for b in blocked}):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def restore_logging():
    """Snapshot and restore structlog config plus the root logger around a test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    config = structlog.get_config()
    yield
    root.handlers = handlers
    root.setLevel(level)
    structlog.configure(**config)


# --------------------------------------------------------------------------- observability
def test_optional_instrumentation_imports_degrade_to_none():
    """Missing psycopg2/redis instrumentation packages leave the placeholders as None."""
    mod = _load_fresh(
        Path(obs.__file__),
        "covf_obs_fresh",
        (
            "opentelemetry.instrumentation.psycopg2",
            "opentelemetry.instrumentation.redis",
        ),
    )
    assert mod.Psycopg2Instrumentor is None and mod.RedisInstrumentor is None


def _patched_otel():
    """Patch every SDK object init_telemetry builds so no thread, socket or global is touched."""
    names = [
        "TracerProvider",
        "MeterProvider",
        "LoggerProvider",
        "OTLPSpanExporter",
        "OTLPMetricExporter",
        "OTLPLogExporter",
        "BatchSpanProcessor",
        "BatchLogRecordProcessor",
        "PeriodicExportingMetricReader",
    ]
    stack = [patch.object(obs, n) for n in names]
    stack += [
        patch.object(obs.trace, "set_tracer_provider"),
        patch.object(obs.metrics, "set_meter_provider"),
        patch.object(obs, "set_logger_provider"),
        patch.object(obs.atexit, "register"),
    ]
    return stack


def _enter_all(stack):
    """Enter a list of patchers and return the started mocks by index."""
    return [p.start() for p in stack]


def _exit_all(stack):
    """Stop every patcher in the list."""
    for p in stack:
        p.stop()


def test_init_telemetry_enabled(monkeypatch):
    """With an endpoint configured all three signals are wired and cleanup is registered."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", " http://collector.invalid:4318 ")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
    stack = _patched_otel()
    mocks = _enter_all(stack)
    try:
        result = obs.init_telemetry("svc")
        register = mocks[-1]
        register.assert_called_once()
        register.call_args.args[
            0
        ]()  # the flush lambda runs against the provider doubles
    finally:
        _exit_all(stack)
    assert result["enabled"] is True
    mocks[0].return_value.add_span_processor.assert_called_once()
    mocks[2].return_value.add_log_record_processor.assert_called_once()


def test_init_telemetry_disabled_without_endpoint(monkeypatch):
    """No endpoint (or an unsupported protocol) degrades to disabled without exporters."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    stack = _patched_otel()
    mocks = _enter_all(stack)
    try:
        assert obs.init_telemetry("svc")["enabled"] is False
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://c.invalid")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
        assert obs.init_telemetry("svc")["enabled"] is False
    finally:
        _exit_all(stack)
    mocks[3].assert_not_called()


def test_init_telemetry_exporter_failures_degrade(monkeypatch):
    """Exporter construction errors are logged and never raised."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://c.invalid")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
    stack = _patched_otel()
    mocks = _enter_all(stack)
    try:
        for idx in (3, 4, 5):  # span, metric and log exporters
            mocks[idx].side_effect = RuntimeError("bad")
        assert obs.init_telemetry("svc")["enabled"] is False
    finally:
        _exit_all(stack)


def test_auto_instrument_app_all_installed():
    """Middleware and every instrumentor are installed when available."""
    app = types.SimpleNamespace(asgi_app=object())
    with (
        patch.object(obs, "OpenTelemetryMiddleware") as mw,
        patch.object(obs, "RedisInstrumentor") as redis_i,
        patch.object(obs, "Psycopg2Instrumentor") as pg_i,
        patch.object(obs, "HTTPXClientInstrumentor") as httpx_i,
    ):
        obs.auto_instrument_app(app)
    assert app.asgi_app is mw.return_value
    for inst in (redis_i, pg_i, httpx_i):
        inst.return_value.instrument.assert_called_once()


def test_auto_instrument_app_missing_drivers_and_errors():
    """Missing optional drivers are skipped and instrumentation errors are swallowed."""
    app = types.SimpleNamespace(asgi_app=object())
    with (
        patch.object(obs, "OpenTelemetryMiddleware", side_effect=RuntimeError("x")),
        patch.object(obs, "RedisInstrumentor", None),
        patch.object(obs, "Psycopg2Instrumentor", None),
        patch.object(obs, "HTTPXClientInstrumentor") as httpx_i,
    ):
        httpx_i.return_value.instrument.side_effect = RuntimeError("x")
        obs.auto_instrument_app(app)
    failing = MagicMock()
    failing.return_value.instrument.side_effect = RuntimeError("x")
    with (
        patch.object(obs, "OpenTelemetryMiddleware"),
        patch.object(obs, "RedisInstrumentor", failing),
        patch.object(obs, "Psycopg2Instrumentor", failing),
        patch.object(obs, "HTTPXClientInstrumentor", failing),
    ):
        obs.auto_instrument_app(app)


def test_structlog_otel_bridge_variants():
    """The bridge uses a supplied or global provider and tolerates provider errors."""
    provider = MagicMock()
    obs.setup_structlog_otel_bridge(provider)
    provider.get_logger.assert_called_once_with("elder")
    with patch.object(obs, "get_logger_provider", return_value=provider) as glp:
        obs.setup_structlog_otel_bridge()
    glp.assert_called_once()
    provider.get_logger.side_effect = RuntimeError("x")
    obs.setup_structlog_otel_bridge(provider)


def test_get_meter_and_tracer():
    """Meter and tracer helpers delegate to the OTel API with the given scope name."""
    assert obs.get_meter("n") is not None
    assert obs.get_tracer("n") is not None
    assert obs.get_meter() is not None and obs.get_tracer() is not None


# --------------------------------------------------------------------------- logger: sanitiser import
def test_sanitizer_processor_and_missing_sanitizer():
    """The sanitiser processor delegates to the helper; without the helper the flag is False."""
    if lg._HAS_SANITIZER:
        out = lg._sanitize_processor(None, "info", {"event": "x"})
        assert isinstance(out, dict)
    fresh = _load_fresh(
        Path(lg.__file__), "covf_logger_fresh", ("penguintechinc_utils",)
    )
    assert fresh._HAS_SANITIZER is False
    assert shared_logging.HAS_SANITIZED_LOGGING is lg._HAS_SANITIZER


# --------------------------------------------------------------------------- logger: handlers
def test_kafka_handler_emit_paths():
    """Kafka handler posts with optional auth, tolerates failures and closes its session."""
    client = MagicMock()
    with patch("httpx.Client", return_value=client):
        h = lg.KafkaHTTP3Handler("http://k.invalid", "t", api_key="k-" + "x")
    rec = logging.LogRecord("n", logging.INFO, "f", 1, "msg", None, None)
    h.emit(rec)
    assert client.post.call_args.kwargs["headers"]["Authorization"].startswith(
        "Bearer "
    )
    client.post.side_effect = RuntimeError("net")
    h.emit(rec)  # swallowed
    h.close()
    client.close.assert_called_once()

    with patch("httpx.Client", return_value=client):
        anon = lg.KafkaHTTP3Handler("http://k.invalid", "t")
    client.post.side_effect = None
    anon.emit(rec)
    assert "Authorization" not in client.post.call_args.kwargs["headers"]


def test_kafka_handler_without_httpx():
    """Without httpx the handler has no session; emit and close are no-ops."""
    with patch.dict(sys.modules, {"httpx": None}):
        h = lg.KafkaHTTP3Handler("http://k.invalid", "t")
    assert h.session is None
    h.emit(logging.LogRecord("n", logging.INFO, "f", 1, "m", None, None))
    h.close()


class _AlreadyExists(Exception):
    """Stand-in for the CloudWatch ResourceAlreadyExistsException."""


def _boto3_module(client):
    """Return a fake boto3 module whose ``client`` factory yields ``client``."""
    mod = types.ModuleType("boto3")
    mod.client = MagicMock(return_value=client)
    return mod


def test_cloudwatch_handler_setup_and_emit():
    """The handler tolerates existing group/stream, uses explicit keys, emits and survives errors."""
    client = MagicMock()
    client.exceptions.ResourceAlreadyExistsException = _AlreadyExists
    client.create_log_group.side_effect = _AlreadyExists()
    client.create_log_stream.side_effect = _AlreadyExists()
    boto3 = _boto3_module(client)
    with patch.dict(sys.modules, {"boto3": boto3}):
        h = lg.CloudWatchHandler("g", "s")
        keyed = lg.CloudWatchHandler("g", "s", "eu-west-1", "ak", "sk")
    assert "aws_access_key_id" in boto3.client.call_args.kwargs
    assert keyed.region == "eu-west-1"
    rec = logging.LogRecord("n", logging.INFO, "f", 1, "msg", None, None)
    h.emit(rec)
    assert client.put_log_events.call_args.kwargs["logGroupName"] == "g"
    client.put_log_events.side_effect = RuntimeError("x")
    h.emit(rec)  # swallowed


def test_cloudwatch_handler_without_boto3():
    """A missing boto3 disables the handler instead of raising."""
    with patch.dict(sys.modules, {"boto3": None}):
        h = lg.CloudWatchHandler("g", "s")
    assert h.client is None
    h.emit(logging.LogRecord("n", logging.INFO, "f", 1, "m", None, None))


# --------------------------------------------------------------------------- logger: configure
class _FakeSyslog(logging.NullHandler):
    """Syslog handler double that never opens a socket."""

    def __init__(self, address=None, socktype=None):
        """Accept the real constructor's arguments and ignore them."""
        super().__init__()


class _FakeKafka(logging.NullHandler):
    """Kafka handler double that never opens a connection."""

    def __init__(self, url, topic, api_key=None):
        """Accept the real constructor's arguments and ignore them."""
        super().__init__()


class _FakeCloudWatch(logging.NullHandler):
    """CloudWatch handler double that never opens a connection."""

    def __init__(self, group, stream, region="us-east-1"):
        """Accept the real constructor's arguments and ignore them."""
        super().__init__()


def _gcp_module(client_error=None):
    """Return a fake google.cloud.logging module with a Client and handler factory."""
    mod = types.ModuleType("google.cloud.logging")
    mod.Client = MagicMock(side_effect=client_error)
    mod.handlers = types.SimpleNamespace(
        CloudLoggingHandler=lambda client, name: logging.NullHandler()
    )
    return mod


def _gcp_modules(client_error=None, missing=False):
    """Return a sys.modules patch dict isolating google.cloud from other tests' stubs.

    Guards `from google.cloud import logging` against stale attributes left on a shared package.
    """
    cloud = types.ModuleType("google.cloud")
    google = types.ModuleType("google")
    google.cloud = cloud
    if missing:
        return {"google": google, "google.cloud": cloud, "google.cloud.logging": None}
    mod = _gcp_module(client_error)
    cloud.logging = mod
    return {"google": google, "google.cloud": cloud, "google.cloud.logging": mod}


def test_structured_logger_levels_and_console_only(restore_logging):
    """Verbosity maps to levels and a console-only configure returns a logger."""
    assert lg.StructuredLogger(verbosity=1).log_level == logging.WARNING
    assert lg.StructuredLogger(verbosity=3).log_level == logging.DEBUG
    assert lg.StructuredLogger(verbosity=9).log_level == logging.INFO
    sl = lg.StructuredLogger("app", 2)
    assert sl.get_logger() is not None
    log = sl.configure()
    assert log is not None and len(sl.handlers) == 1
    assert sl.get_logger() is sl.logger
    assert sl.get_logger("named") is not None
    sl.close()
    off = lg.StructuredLogger()
    off.configure(enable_console=False)
    assert off.handlers == []


def test_structured_logger_all_destinations(restore_logging):
    """Every optional destination attaches a handler when its dependency works."""
    sl = lg.StructuredLogger()
    with (
        patch.object(lg, "SysLogHandler", _FakeSyslog),
        patch.object(lg, "KafkaHTTP3Handler", _FakeKafka),
        patch.object(lg, "CloudWatchHandler", _FakeCloudWatch),
        patch.dict(sys.modules, _gcp_modules()),
    ):
        sl.configure(
            enable_syslog=True,
            syslog_host="s.invalid",
            enable_kafka=True,
            kafka_url="http://k.invalid",
            enable_cloudwatch=True,
            cloudwatch_log_group="g",
            cloudwatch_log_stream="s",
            enable_gcp=True,
            gcp_project_id="p",
        )
    assert len(sl.handlers) == 5
    sl.close()


def test_structured_logger_destination_failures(restore_logging):
    """Each destination's setup failure is logged and the logger still configures."""
    boom = MagicMock(side_effect=RuntimeError("x"))
    sl = lg.StructuredLogger()
    with (
        patch.object(lg, "SysLogHandler", boom),
        patch.object(lg, "KafkaHTTP3Handler", boom),
        patch.object(lg, "CloudWatchHandler", boom),
        patch.dict(sys.modules, _gcp_modules(RuntimeError("x"))),
    ):
        sl.configure(
            enable_syslog=True,
            syslog_host="s.invalid",
            enable_kafka=True,
            kafka_url="http://k.invalid",
            enable_cloudwatch=True,
            cloudwatch_log_group="g",
            cloudwatch_log_stream="s",
            enable_gcp=True,
            gcp_project_id="p",
        )
    assert len(sl.handlers) == 1  # console only
    with patch.dict(sys.modules, _gcp_modules(missing=True)):
        lg.StructuredLogger().configure(enable_gcp=True, gcp_project_id="p")


# --------------------------------------------------------------------------- logger: env config
_ENV_KEYS = (
    "LOG_LEVEL",
    "VERBOSITY",
    "SYSLOG_ENABLED",
    "SYSLOG_HOST",
    "SYSLOG_PORT",
    "KAFKA_ENABLED",
    "KAFKA_URL",
    "KAFKA_TOPIC",
    "KAFKA_API_KEY",
    "CLOUDWATCH_ENABLED",
    "CLOUDWATCH_LOG_GROUP",
    "CLOUDWATCH_LOG_STREAM",
    "CLOUDWATCH_REGION",
    "GCP_LOGGING_ENABLED",
    "GCP_PROJECT_ID",
    "GCP_LOG_NAME",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Remove every logging-related environment variable for the test."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    ("env", "verbosity"),
    [
        ({}, 2),
        ({"LOG_LEVEL": "debug"}, 3),
        ({"LOG_LEVEL": "warning"}, 1),
        ({"LOG_LEVEL": "nonsense"}, 2),
        ({"VERBOSITY": "3", "LOG_LEVEL": "WARNING"}, 3),
    ],
)
def test_configure_logging_from_env_verbosity(
    clean_env, restore_logging, env, verbosity
):
    """VERBOSITY overrides LOG_LEVEL, which otherwise maps with an INFO default."""
    for k, v in env.items():
        clean_env.setenv(k, v)
    with patch.object(lg.StructuredLogger, "configure", return_value="L") as cfg:
        with patch.object(lg.StructuredLogger, "__init__", return_value=None) as init:
            assert lg.configure_logging_from_env("a") == "L"
    assert init.call_args.kwargs == {"app_name": "a", "verbosity": verbosity}
    assert cfg.call_args.kwargs["enable_syslog"] is False


def test_configure_logging_from_env_destinations(clean_env, restore_logging):
    """Destination env vars are parsed and passed through to configure."""
    values = {
        "SYSLOG_ENABLED": "TRUE",
        "SYSLOG_HOST": "s.invalid",
        "SYSLOG_PORT": "5514",
        "KAFKA_ENABLED": "true",
        "KAFKA_URL": "http://k.invalid",
        "KAFKA_TOPIC": "topic",
        "KAFKA_API_KEY": "k-" + "x",
        "CLOUDWATCH_ENABLED": "true",
        "CLOUDWATCH_LOG_GROUP": "g",
        "CLOUDWATCH_LOG_STREAM": "s",
        "CLOUDWATCH_REGION": "eu-west-1",
        "GCP_LOGGING_ENABLED": "true",
        "GCP_PROJECT_ID": "p",
        "GCP_LOG_NAME": "n",
    }
    for k, v in values.items():
        clean_env.setenv(k, v)
    with patch.object(lg.StructuredLogger, "configure", return_value="L") as cfg:
        lg.configure_logging_from_env()
    kw = cfg.call_args.kwargs
    assert kw["syslog_port"] == 5514 and kw["kafka_topic"] == "topic"
    assert kw["enable_cloudwatch"] and kw["enable_gcp"] and kw["gcp_log_name"] == "n"
    # And a real, console-only run end to end.
    for k in values:
        clean_env.delenv(k)
    assert lg.configure_logging_from_env("real") is not None
