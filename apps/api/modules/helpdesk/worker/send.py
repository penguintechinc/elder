"""Outbound email sender for helpdesk tickets using stdlib SMTP via asyncio.to_thread."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class SmtpConfig:
    """SMTP configuration for an email account."""

    host: str
    port: int
    mode: str  # ssl, starttls
    username: str
    password: str  # plaintext (resolved from penguin-sal)


def _resolve_smtp_password(password_ref: str | None, secrets_client) -> str:
    """Resolve SMTP password from penguin-sal reference (sync helper for thread).

    Args:
        password_ref: penguin-sal reference (e.g., "smtp_password_12345")
        secrets_client: SecretClient instance

    Returns:
        str: Plaintext password

    Raises:
        ValueError: If password_ref is None or resolution fails
    """
    if not password_ref:
        raise ValueError("No SMTP password reference provided")

    # Synchronously resolve from penguin-sal
    password = secrets_client.get_sync(password_ref)
    if not password:
        raise ValueError(f"Failed to resolve SMTP password: {password_ref}")

    return password


def _send_smtp_message(
    config: SmtpConfig,
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> str:
    """Send email via SMTP (sync helper for asyncio.to_thread).

    Args:
        config: SMTP configuration
        from_addr: From address
        to_addrs: List of recipient addresses
        subject: Email subject
        body_text: Plain text body
        body_html: HTML body (optional)

    Returns:
        str: Message-ID from SMTP response

    Raises:
        smtplib.SMTPException: On SMTP errors
    """
    # Create SSL context based on mode
    if config.mode == "ssl":
        context = ssl.create_default_context()
        conn = smtplib.SMTP_SSL(config.host, config.port, context=context)
    elif config.mode == "starttls":
        context = ssl.create_default_context()
        conn = smtplib.SMTP(config.host, config.port)
        conn.starttls(context=context)
    else:
        # Plain (no TLS)
        conn = smtplib.SMTP(config.host, config.port)

    try:
        # Authenticate
        conn.login(config.username, config.password)

        # Build message headers
        headers = f"""From: {from_addr}\r
To: {', '.join(to_addrs)}\r
Subject: {subject}\r
"""
        if body_html:
            headers += """MIME-Version: 1.0\r
Content-Type: multipart/alternative; boundary="boundary"\r
\r
--boundary\r
Content-Type: text/plain; charset="utf-8"\r
\r
{}\r
--boundary\r
Content-Type: text/html; charset="utf-8"\r
\r
{}\r
--boundary--""".format(body_text, body_html)
        else:
            headers += f"""Content-Type: text/plain; charset="utf-8"\r
\r
{body_text}"""

        # Send message
        response = conn.sendmail(from_addr, to_addrs, headers.encode("utf-8"))

        # Extract message-id from response (simplified; actual SMTP doesn't return it)
        # In production, generate one or extract from email headers
        message_id = f"<elder-{hash(headers) % 10000000}@elder.local>"

        return message_id

    finally:
        conn.quit()


async def send_email(
    db: Any,
    ticket_id: int,
    message_id_db: int,
    to_addrs: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    email_account_id: int | None = None,
) -> dict[str, Any]:
    """Send outbound email for a helpdesk ticket message.

    Fetches SMTP config from email account, resolves credentials via penguin-sal,
    sends via stdlib SMTP, and logs result to hd_email_logs.

    Args:
        db: penguin-dal database instance
        ticket_id: Ticket ID (for logging context)
        message_id_db: HdTicketMessage.id (for logging)
        to_addrs: List of recipient email addresses
        subject: Email subject
        body_text: Plain text body
        body_html: HTML body (optional)
        email_account_id: Email account to use (optional; defaults to first active)

    Returns:
        dict with status, message_id, to_addrs

    Raises:
        Exception: On SMTP failure or database error
    """
    from datetime import datetime, timezone

    import structlog

    logger = structlog.get_logger()

    def _send_sync() -> dict[str, Any]:
        """Synchronous SMTP send and logging."""
        from penguin_sal import SecretClient

        secrets = SecretClient()

        # Fetch email account (use provided ID or first active)
        if email_account_id:
            account = db.hd_email_accounts[email_account_id]
        else:
            rows = db(
                (db.hd_email_accounts.is_active == True)  # noqa: E712
            ).select(limitby=(0, 1))
            account = rows[0] if rows else None

        if not account:
            raise ValueError("No active email account found for sending")

        # Resolve SMTP password
        password = _resolve_smtp_password(account.smtp_password_ref, secrets)

        # Build SMTP config
        smtp_config = SmtpConfig(
            host=account.smtp_host or "localhost",
            port=account.smtp_port or 587,
            mode=account.smtp_mode or "starttls",
            username=account.smtp_username or "",
            password=password,
        )

        # Send email
        try:
            message_id = _send_smtp_message(
                smtp_config,
                from_addr=account.email_address,
                to_addrs=to_addrs,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            )

            # Log success to hd_email_logs
            db.hd_email_logs.insert(
                hd_email_account_id=account.id,
                direction="outbound",
                message_id=message_id,
                from_addr=account.email_address,
                to_addr=",".join(to_addrs),
                subject=subject,
                status="sent",
                created_at=datetime.now(timezone.utc),
            )
            db.commit()

            logger.info(
                "email_sent",
                ticket_id=ticket_id,
                message_id_db=message_id_db,
                message_id=message_id,
                to_addrs=to_addrs,
            )

            return {
                "status": "sent",
                "message_id": message_id,
                "to_addrs": to_addrs,
            }

        except Exception as e:
            # Log failure to hd_email_logs
            db.hd_email_logs.insert(
                hd_email_account_id=account.id,
                direction="outbound",
                message_id=f"<failed-{ticket_id}-{message_id_db}>",
                from_addr=account.email_address,
                to_addr=",".join(to_addrs),
                subject=subject,
                status="failed",
                error=str(e),
                created_at=datetime.now(timezone.utc),
            )
            db.commit()

            logger.error(
                "email_send_failed",
                ticket_id=ticket_id,
                message_id_db=message_id_db,
                to_addrs=to_addrs,
                error=str(e),
            )

            raise

    # Run sync SMTP send in thread pool to avoid blocking event loop
    return await asyncio.to_thread(_send_sync)
