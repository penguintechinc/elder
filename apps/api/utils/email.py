"""SMTP email delivery utility for Elder API services.

Reads standard ``SMTP_*`` environment variables so deployments can wire
any SMTP relay (e.g. Google Workspace ``smtp-relay.gmail.com:587`` with
STARTTLS and credentials from a Kubernetes Secret) without baking
provider-specific settings into code.

Fails loudly with ``EmailNotConfiguredError`` whenever delivery is
attempted without ``SMTP_HOST`` and ``SMTP_FROM_EMAIL`` configured.
"""

from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

import structlog

from shared.observability import get_meter, get_tracer

logger = structlog.get_logger(__name__)
_tracer = get_tracer(__name__)
_meter = get_meter(__name__)
_email_sent_counter = _meter.create_counter(
    name="elder.email.sent",
    description="Total SMTP emails sent or failed",
    unit="1",
)
_email_duration_histogram = _meter.create_histogram(
    name="elder.email.delivery.duration",
    description="SMTP email delivery duration in seconds",
    unit="s",
)

DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_TIMEOUT = 30
DEFAULT_SMTP_FROM_NAME = "Elder"


class EmailNotConfiguredError(RuntimeError):
    """Raised when SMTP delivery is attempted without required SMTP config."""


class EmailDeliveryError(RuntimeError):
    """Raised when SMTP transport or authentication fails during delivery."""


@dataclass(frozen=True)
class SMTPConfig:
    """Immutable SMTP transport configuration loaded from environment."""

    host: str
    port: int = DEFAULT_SMTP_PORT
    username: str = ""
    password: str = ""
    use_tls: bool = True
    use_ssl: bool = False
    from_email: str = ""
    from_name: str = DEFAULT_SMTP_FROM_NAME
    ehlo_host: str = ""
    timeout: int = DEFAULT_SMTP_TIMEOUT

    @property
    def is_configured(self) -> bool:
        """Return True when minimum SMTP delivery settings are present."""
        return bool(self.host.strip() and self.from_email.strip())


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value.strip())
        return parsed if parsed > 0 else default
    except ValueError:
        return default


def load_smtp_config() -> SMTPConfig:
    """Load SMTP settings from ``SMTP_*`` environment variables."""
    return SMTPConfig(
        host=os.environ.get("SMTP_HOST", "").strip(),
        port=_parse_int(os.environ.get("SMTP_PORT"), DEFAULT_SMTP_PORT),
        username=os.environ.get("SMTP_USERNAME", "").strip(),
        password=os.environ.get("SMTP_PASSWORD", ""),
        use_tls=_parse_bool(os.environ.get("SMTP_USE_TLS"), True),
        use_ssl=_parse_bool(os.environ.get("SMTP_USE_SSL"), False),
        from_email=os.environ.get("SMTP_FROM_EMAIL", "").strip(),
        from_name=(
            os.environ.get("SMTP_FROM_NAME", "").strip() or DEFAULT_SMTP_FROM_NAME
        ),
        ehlo_host=os.environ.get("SMTP_EHLO_HOST", "").strip(),
        timeout=_parse_int(os.environ.get("SMTP_TIMEOUT"), DEFAULT_SMTP_TIMEOUT),
    )


def is_smtp_configured() -> bool:
    """Return True if ``SMTP_HOST`` and ``SMTP_FROM_EMAIL`` are configured."""
    return load_smtp_config().is_configured


def _build_tls_context() -> ssl.SSLContext:
    """Build a TLS 1.2+ SSL context for SMTP transport encryption."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _build_message(
    *,
    config: SMTPConfig,
    to_addresses: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> EmailMessage:
    """Construct a standards-compliant MIME EmailMessage."""
    msg = EmailMessage()
    msg["From"] = formataddr((config.from_name, config.from_email))
    msg["To"] = ", ".join(to_addresses)
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    return msg


def send_email_sync(
    *,
    to_addresses: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    config: SMTPConfig | None = None,
) -> dict[str, Any]:
    """Send an email synchronously over SMTP (TLS 1.2+ enforced when enabled).

    Raises:
        EmailNotConfiguredError: If SMTP_HOST or SMTP_FROM_EMAIL is unset.
        ValueError: If ``to_addresses`` is empty.
        EmailDeliveryError: If the SMTP server rejects the connection/message.
    """
    cfg = config or load_smtp_config()
    if not cfg.is_configured:
        raise EmailNotConfiguredError(
            "SMTP is not configured (set SMTP_HOST and SMTP_FROM_EMAIL)"
        )

    recipients = [addr.strip() for addr in to_addresses if addr and addr.strip()]
    if not recipients:
        raise ValueError("At least one recipient email address is required")

    msg = _build_message(
        config=cfg,
        to_addresses=recipients,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )

    started = time.monotonic()
    local_hostname = cfg.ehlo_host or None
    ssl_ctx = _build_tls_context()

    with _tracer.start_as_current_span("elder.email.send") as span:
        span.set_attribute("smtp.host", cfg.host)
        span.set_attribute("smtp.port", cfg.port)
        span.set_attribute("smtp.recipient_count", len(recipients))
        try:
            if cfg.use_ssl:
                with smtplib.SMTP_SSL(
                    host=cfg.host,
                    port=cfg.port,
                    local_hostname=local_hostname,
                    timeout=cfg.timeout,
                    context=ssl_ctx,
                ) as smtp:
                    if cfg.username and cfg.password:
                        smtp.login(cfg.username, cfg.password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(
                    host=cfg.host,
                    port=cfg.port,
                    local_hostname=local_hostname,
                    timeout=cfg.timeout,
                ) as smtp:
                    smtp.ehlo()
                    if cfg.use_tls:
                        smtp.starttls(context=ssl_ctx)
                        smtp.ehlo()
                    if cfg.username and cfg.password:
                        smtp.login(cfg.username, cfg.password)
                    smtp.send_message(msg)
        except smtplib.SMTPException as exc:
            elapsed = time.monotonic() - started
            _email_sent_counter.add(1, {"outcome": "error"})
            _email_duration_histogram.record(elapsed, {"outcome": "error"})
            logger.error(
                "smtp_delivery_failed",
                smtp_host=cfg.host,
                smtp_port=cfg.port,
                recipient_count=len(recipients),
                error_type=type(exc).__name__,
            )
            raise EmailDeliveryError(
                f"SMTP delivery failed: {type(exc).__name__}"
            ) from exc
        except OSError as exc:
            elapsed = time.monotonic() - started
            _email_sent_counter.add(1, {"outcome": "error"})
            _email_duration_histogram.record(elapsed, {"outcome": "error"})
            logger.error(
                "smtp_connection_failed",
                smtp_host=cfg.host,
                smtp_port=cfg.port,
                recipient_count=len(recipients),
                error_type=type(exc).__name__,
            )
            raise EmailDeliveryError(
                f"SMTP connection failed: {type(exc).__name__}"
            ) from exc

        elapsed = time.monotonic() - started
        _email_sent_counter.add(1, {"outcome": "success"})
        _email_duration_histogram.record(elapsed, {"outcome": "success"})
        logger.info(
            "smtp_delivery_succeeded",
            smtp_host=cfg.host,
            smtp_port=cfg.port,
            recipient_count=len(recipients),
        )
        return {
            "success": True,
            "smtp_host": cfg.host,
            "smtp_port": cfg.port,
            "recipient_count": len(recipients),
        }


async def send_email(
    *,
    to_addresses: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    config: SMTPConfig | None = None,
) -> dict[str, Any]:
    """Send an email asynchronously without blocking the Quart event loop."""
    return await asyncio.to_thread(
        send_email_sync,
        to_addresses=to_addresses,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        config=config,
    )
