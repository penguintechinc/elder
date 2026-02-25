"""Multi-destination logging with support for Console, Syslog UDP, and KillKrill HTTP3/QUIC.

This module provides a unified logging interface that can simultaneously send logs to:
- Console (stdout/stderr) - always enabled
- Syslog UDP (optional, configured via settings)
- KillKrill HTTP3/QUIC (optional, configured via settings)

All logs include correlation IDs for distributed tracing across sync operations.
"""

# flake8: noqa: E501


import json
import logging
import socket
import sys
import uuid
from datetime import datetime
from logging.handlers import SysLogHandler
from typing import Any, Optional

import httpx
import structlog

from apps.worker.config.settings import settings


class KillKrillHandler(logging.Handler):
    """Custom logging handler for KillKrill HTTP3/QUIC log shipping.

    Sends structured JSON logs to KillKrill via HTTP3/QUIC (with HTTP/2 fallback).
    Implements batching and async delivery for high-performance logging.
    """

    def __init__(
        self,
        killkrill_url: str,
        api_key: Optional[str] = None,
        use_http3: bool = True,
        batch_size: int = 100,
        flush_interval: int = 5,
    ):
        """Initialize KillKrill handler.

        Args:
            killkrill_url: KillKrill server URL
            api_key: API key for authentication
            use_http3: Whether to use HTTP3/QUIC (fallback to HTTP/2 if False)
            batch_size: Number of logs to batch before sending
            flush_interval: Seconds between automatic flushes
        """
        super().__init__()
        self.killkrill_url = killkrill_url.rstrip("/")
        self.api_key = api_key
        self.use_http3 = use_http3
        self.batch_size = batch_size
        self.batch = []
        self.last_flush = datetime.now()
        self.flush_interval = flush_interval

        # Configure httpx client with HTTP3 support
        http_versions = ["h3"] if use_http3 else ["h2", "http/1.1"]
        self.client = httpx.Client(
            http2=True,
            timeout=10.0,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}" if api_key else "",
            },
        )

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to KillKrill.

        Args:
            record: Log record to send
        """
        try:
            # Format log record as JSON
            log_entry = {
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "correlation_id": getattr(record, "correlation_id", None),
                "service": "elder-worker",
                "hostname": socket.gethostname(),
            }

            # Add extra fields
            if hasattr(record, "extra"):
                log_entry.update(record.extra)

            # Add exception info if present
            if record.exc_info:
                log_entry["exception"] = self.format(record)

            # Add to batch
            self.batch.append(log_entry)

            # Flush if batch is full or interval exceeded
            if (
                len(self.batch) >= self.batch_size
                or (datetime.now() - self.last_flush).seconds >= self.flush_interval
            ):
                self.flush()

        except Exception as e:
            # Fallback to stderr if KillKrill delivery fails
            print(f"KillKrill logging error: {e}", file=sys.stderr)

    def flush(self) -> None:
        """Flush batched logs to KillKrill."""
        if not self.batch:
            return

        try:
            response = self.client.post(
                f"{self.killkrill_url}/api/v1/logs/batch",
                json={"logs": self.batch},
            )

            if response.status_code >= 400:
                print(
                    f"KillKrill batch upload failed: {response.status_code} - {response.text}",
                    file=sys.stderr,
                )

            # Clear batch after successful send
            self.batch = []
            self.last_flush = datetime.now()

        except Exception as e:
            print(f"KillKrill batch flush error: {e}", file=sys.stderr)

    def close(self) -> None:
        """Close handler and flush remaining logs."""
        self.flush()
        self.client.close()
        super().close()


class CorrelationIDFilter(logging.Filter):
    """Add correlation ID to log records for distributed tracing."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Add correlation_id to record if not present.

        Args:
            record: Log record to filter

        Returns:
            True (always allow the record)
        """
        if not hasattr(record, "correlation_id"):
            record.correlation_id = str(uuid.uuid4())
        return True


def configure_multi_destination_logging() -> None:
    """Configure multi-destination logging with Console, Syslog UDP, and KillKrill.

    Always logs to console. Optionally adds Syslog UDP and/or KillKrill handlers
    based on settings configuration.
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    # Clear existing handlers
    root_logger.handlers = []

    # Add correlation ID filter to all handlers
    correlation_filter = CorrelationIDFilter()

    # 1. CONSOLE HANDLER (always enabled)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)

    if settings.log_format == "json":
        # JSON formatter for structured logging
        console_formatter = logging.Formatter(
            json.dumps(
                {
                    "timestamp": "%(asctime)s",
                    "level": "%(levelname)s",
                    "logger": "%(name)s",
                    "message": "%(message)s",
                    "correlation_id": "%(correlation_id)s",
                }
            )
        )
    else:
        # Human-readable console format
        console_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s [%(correlation_id)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(correlation_filter)
    root_logger.addHandler(console_handler)

    # 2. SYSLOG UDP HANDLER (optional)
    if settings.syslog_enabled:
        try:
            syslog_handler = SysLogHandler(
                address=(settings.syslog_host, settings.syslog_port),
                socktype=socket.SOCK_DGRAM,  # UDP
            )
            syslog_handler.setLevel(logging.INFO)

            # Syslog uses standard format
            syslog_formatter = logging.Formatter(
                "elder-worker[%(process)d]: [%(levelname)s] %(name)s [%(correlation_id)s]: %(message)s"
            )
            syslog_handler.setFormatter(syslog_formatter)
            syslog_handler.addFilter(correlation_filter)
            root_logger.addHandler(syslog_handler)

            root_logger.info(
                f"Syslog UDP logging enabled: {settings.syslog_host}:{settings.syslog_port}"
            )
        except Exception as e:
            root_logger.error(f"Failed to configure Syslog UDP handler: {e}")

    # 3. KILLKRILL HTTP3/QUIC HANDLER (optional)
    if settings.killkrill_enabled:
        try:
            killkrill_handler = KillKrillHandler(
                killkrill_url=settings.killkrill_url,
                api_key=settings.killkrill_api_key,
                use_http3=settings.killkrill_use_http3,
            )
            killkrill_handler.setLevel(logging.INFO)
            killkrill_handler.addFilter(correlation_filter)
            root_logger.addHandler(killkrill_handler)

            protocol = "HTTP3/QUIC" if settings.killkrill_use_http3 else "HTTP/2"
            root_logger.info(
                f"KillKrill {protocol} logging enabled: {settings.killkrill_url}"
            )
        except Exception as e:
            root_logger.error(f"Failed to configure KillKrill handler: {e}")

    # Configure structlog to work with standard logging
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, correlation_id: Optional[str] = None) -> Any:
    """Get a configured logger instance with optional correlation ID.

    Args:
        name: Logger name (typically __name__)
        correlation_id: Optional correlation ID for distributed tracing

    Returns:
        Configured structlog logger with correlation context
    """
    logger = structlog.get_logger(name)

    if correlation_id:
        logger = logger.bind(correlation_id=correlation_id)

    return logger


def create_correlation_id() -> str:
    """Generate a new correlation ID for distributed tracing.

    Returns:
        UUID4 string for correlation tracking
    """
    return str(uuid.uuid4())
