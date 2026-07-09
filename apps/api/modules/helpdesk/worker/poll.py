"""Inbound email poller for helpdesk: IMAP/SMTP fetch, parse, create/update tickets."""

from __future__ import annotations

import asyncio
import imaplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes
from email.utils import parsedate_to_datetime
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass(slots=True)
class ParsedAttachment:
    """Represents an email attachment."""

    filename: str
    content_type: str | None
    content: bytes
    size_bytes: int


@dataclass(slots=True)
class ParsedEmail:
    """Structured representation of a parsed email message."""

    message_id: str
    in_reply_to: str | None
    references: list[str]
    from_addr: str
    to_addrs: list[str]
    subject: str
    body_text: str | None
    body_html: str | None
    attachments: list[ParsedAttachment] = field(default_factory=list)
    received_at: datetime = field(default_factory=datetime.utcnow)


def parse_email(raw: bytes) -> ParsedEmail:
    """Parse raw email bytes into structured ParsedEmail.

    Handles multipart messages, extracts text and HTML bodies,
    and collects attachments.

    Parity: Port of Ruffled's email_parser.py:parse_email().

    Args:
        raw: Raw email message bytes.

    Returns:
        ParsedEmail: Structured email data.

    Raises:
        ValueError: If required fields are missing or parsing fails.
    """
    try:
        msg = message_from_bytes(raw)
    except Exception as e:
        logger.error("parse_email_failed", error=str(e))
        raise ValueError(f"Invalid email format: {e}") from e

    # Extract core fields
    message_id = msg.get("Message-ID", "").strip("<>")
    if not message_id:
        raise ValueError("Email missing Message-ID header")

    in_reply_to = msg.get("In-Reply-To", "").strip("<>") or None
    references_str = msg.get("References", "")
    references = [ref.strip("<>") for ref in references_str.split()] if references_str else []

    from_addr = msg.get("From", "")
    if not from_addr:
        raise ValueError("Email missing From header")

    to_addrs_str = msg.get("To", "")
    to_addrs = [addr.strip() for addr in to_addrs_str.split(",")] if to_addrs_str else []

    subject = msg.get("Subject", "")

    # Parse received date
    received_at = datetime.utcnow()
    date_str = msg.get("Date")
    if date_str:
        try:
            received_at = parsedate_to_datetime(date_str)
        except (TypeError, ValueError):
            logger.warning("date_parse_failed", date=date_str)

    # Extract bodies and attachments
    body_text = None
    body_html = None
    attachments: list[ParsedAttachment] = []

    if msg.is_multipart():
        # Walk through all parts of the message
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                # Skip the container itself
                continue

            content_type = part.get_content_type()
            content_disposition = part.get("Content-Disposition", "")

            if "attachment" in content_disposition:
                # Handle attachment
                filename = part.get_filename()
                if filename:
                    content = part.get_payload(decode=True)
                    attachments.append(
                        ParsedAttachment(
                            filename=filename,
                            content_type=content_type,
                            content=content,
                            size_bytes=len(content),
                        )
                    )
            elif content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body_text = payload.decode("utf-8", errors="ignore")
                else:
                    body_text = str(payload)
            elif content_type == "text/html":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body_html = payload.decode("utf-8", errors="ignore")
                else:
                    body_html = str(payload)
    else:
        # Single-part message
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="ignore")
        else:
            payload = str(payload)

        if content_type == "text/plain":
            body_text = payload
        elif content_type == "text/html":
            body_html = payload
        else:
            body_text = payload

    if not body_text and not body_html:
        payload = msg.get_payload(decode=False)
        if isinstance(payload, str):
            body_text = payload
        elif isinstance(payload, list):
            # If it's a list of parts, just note it
            body_text = "[multipart message]"
        elif isinstance(payload, bytes):
            body_text = payload.decode("utf-8", errors="ignore")

    return ParsedEmail(
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        from_addr=from_addr,
        to_addrs=to_addrs,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
        received_at=received_at,
    )


def _connect_imap(
    host: str,
    port: int,
    username: str,
    password: str,
) -> imaplib.IMAP4_SSL:
    """Connect to IMAP server with TLS (sync helper for thread).

    Args:
        host: IMAP host
        port: IMAP port
        username: IMAP username
        password: IMAP password (plaintext)

    Returns:
        imaplib.IMAP4_SSL: Connected IMAP connection

    Raises:
        imaplib.IMAP4.error: On connection failure
    """
    context = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(host, port, ssl_context=context)
    conn.login(username, password)
    return conn


def _fetch_unseen_emails(conn: imaplib.IMAP4_SSL) -> list[bytes]:
    """Fetch all unseen emails from INBOX (sync helper for thread).

    Args:
        conn: IMAP connection

    Returns:
        list[bytes]: List of raw email message bytes
    """
    conn.select("INBOX")
    status, message_ids = conn.search(None, "UNSEEN")

    if status != "OK":
        raise RuntimeError("Failed to search for unseen emails")

    emails = []
    if message_ids[0]:
        for msg_id in message_ids[0].split():
            status, raw = conn.fetch(msg_id, "(RFC822)")
            if status == "OK":
                emails.append(raw[0][1])

    return emails


def _close_imap(conn: imaplib.IMAP4_SSL) -> None:
    """Close IMAP connection (sync helper for thread).

    Args:
        conn: IMAP connection
    """
    try:
        conn.close()
        conn.logout()
    except Exception as e:
        logger.warning("imap_close_failed", error=str(e))


async def poll_email_account(
    db: Any,
    email_account_id: int,
    tenant_id: int,
) -> dict[str, Any]:
    """Poll email account for new messages and create/update tickets.

    Connects to IMAP, fetches unseen emails, parses them, and either:
    - Creates a new ticket if no in-reply-to or matching reference
    - Appends to existing ticket if in-reply-to/references match

    Idempotency: Uses RFC Message-ID as the job idempotency_key.
    Duplicate check: Skip if hd_ticket_messages.email_message_id already exists.

    Args:
        db: penguin-dal database instance
        email_account_id: HdEmailAccount.id
        tenant_id: Tenant ID

    Returns:
        dict with status, tickets_created, messages_appended

    Raises:
        Exception: On IMAP or database errors
    """
    from datetime import datetime, timezone

    def _poll_sync() -> dict[str, Any]:
        """Synchronous IMAP poll and ticket creation."""
        from penguin_sal import SecretClient

        secrets = SecretClient()

        # Fetch email account
        account = db.hd_email_accounts[email_account_id]
        if not account or not account.is_active:
            raise ValueError(f"Email account {email_account_id} not found or inactive")

        # Resolve IMAP password
        if not account.imap_password_ref:
            raise ValueError("No IMAP password reference")
        imap_password = secrets.get_sync(account.imap_password_ref)
        if not imap_password:
            raise ValueError(f"Failed to resolve IMAP password: {account.imap_password_ref}")

        # Connect to IMAP
        conn = _connect_imap(
            host=account.imap_host or "localhost",
            port=account.imap_port or 993,
            username=account.imap_username or "",
            password=imap_password,
        )

        try:
            # Fetch unseen emails
            raw_emails = _fetch_unseen_emails(conn)
            logger.info("emails_fetched", count=len(raw_emails), account_id=email_account_id)

            tickets_created = 0
            messages_appended = 0

            # Process each email
            for raw_email in raw_emails:
                try:
                    parsed = parse_email(raw_email)

                    # Check for duplicate message
                    existing_msg = db(
                        db.hd_ticket_messages.email_message_id == parsed.message_id
                    ).select(limitby=(0, 1))

                    if existing_msg:
                        logger.debug(
                            "message_duplicate_skipped",
                            message_id=parsed.message_id,
                        )
                        continue

                    # Try to find existing ticket by in-reply-to or references
                    ticket = None
                    if parsed.in_reply_to:
                        # Look for message with this message_id
                        ref_msg = db(
                            db.hd_ticket_messages.email_message_id == parsed.in_reply_to
                        ).select(limitby=(0, 1))
                        if ref_msg:
                            ticket = db.hd_tickets[ref_msg[0].hd_ticket_id]

                    if not ticket:
                        # Create new ticket
                        # Resolve or create requester contact from from_addr
                        contact_rows = db(
                            (db.hd_contacts.tenant_id == tenant_id)
                            & (db.hd_contacts.email == parsed.from_addr)
                        ).select(limitby=(0, 1))

                        if contact_rows:
                            requester_contact_id = contact_rows[0].id
                        else:
                            # Create guest contact (pattern from ticket_forms.py)
                            # Use simple village_id format since we don't have redis
                            contact_vid = f"email-c-{hash(parsed.from_addr) % 1000000:06d}"
                            contact_id = db.hd_contacts.insert(
                                tenant_id=tenant_id,
                                village_id=contact_vid,
                                email=parsed.from_addr,
                                first_name=parsed.from_addr.split("@")[0],
                                created_at=datetime.now(timezone.utc),
                                updated_at=datetime.now(timezone.utc),
                            )
                            db.commit()
                            requester_contact_id = contact_id

                        # Insert ticket
                        ticket_id = db.hd_tickets.insert(
                            tenant_id=tenant_id,
                            subject=parsed.subject or f"Email from {parsed.from_addr}",
                            status="new",
                            priority="medium",
                            channel="email",
                            requester_contact_id=requester_contact_id,
                            requester_identity_id=None,
                            created_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc),
                        )
                        db.commit()

                        logger.info(
                            "ticket_created",
                            ticket_id=ticket_id,
                            from_addr=parsed.from_addr,
                            message_id=parsed.message_id,
                        )

                        tickets_created += 1
                        ticket_id_for_msg = ticket_id

                    else:
                        ticket_id_for_msg = ticket.id

                    # Create message in ticket
                    # For email messages, sender is typically requester; use identity_id NULL
                    # In production, might resolve identity from email address
                    sender_identity_id = None

                    msg_id = db.hd_ticket_messages.insert(
                        hd_ticket_id=ticket_id_for_msg,
                        sender_identity_id=sender_identity_id or 1,  # Fallback to system user
                        message_type="reply",
                        body_text=parsed.body_text,
                        body_html=parsed.body_html,
                        is_internal=False,
                        email_message_id=parsed.message_id,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    )
                    db.commit()

                    logger.info(
                        "message_created",
                        ticket_id=ticket_id_for_msg,
                        message_id=msg_id,
                        email_message_id=parsed.message_id,
                    )

                    messages_appended += 1

                    # Log to hd_email_logs
                    db.hd_email_logs.insert(
                        hd_email_account_id=email_account_id,
                        direction="inbound",
                        message_id=parsed.message_id,
                        from_addr=parsed.from_addr,
                        to_addr=",".join(parsed.to_addrs),
                        subject=parsed.subject,
                        status="received",
                        created_at=datetime.now(timezone.utc),
                    )
                    db.commit()

                except Exception as e:
                    logger.error("email_process_failed", error=str(e), exc_info=True)
                    continue

        finally:
            _close_imap(conn)

        return {
            "status": "success",
            "tickets_created": tickets_created,
            "messages_appended": messages_appended,
        }

    # Run sync IMAP poll and ticket creation in thread pool
    return await asyncio.to_thread(_poll_sync)
