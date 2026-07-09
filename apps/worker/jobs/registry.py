"""Worker job handler registry.

Maps worker task group names to async handler functions.
Handlers receive a JobEnvelope and return a result dict.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import structlog

from shared.jobbus import JobEnvelope

logger = structlog.get_logger(__name__)

# Type alias for handler functions
JobHandler = Callable[[JobEnvelope], Any]  # Returns dict or None on success, raises on error


async def handle_discovery(envelope: JobEnvelope) -> dict[str, Any]:
    """Handle discovery job from job-bus.

    Invokes the existing DiscoveryExecutor via run_pending() to poll and execute
    pending discovery jobs from the database. Runs the sync executor in a thread pool
    to avoid blocking the event loop.

    Args:
        envelope: JobEnvelope with discovery job details

    Returns:
        Result dict with status and discovery stats

    Raises:
        Exception: If discovery execution fails
    """
    import asyncio

    from apps.worker.discovery.executor import DiscoveryExecutor
    from shared.database.manager import DatabaseManager

    logger.info(
        "discovery_job_received",
        job_id=envelope.job_id,
        job_type=envelope.job_type,
        payload=envelope.payload,
    )

    from apps.worker.config.settings import settings

    if not settings.database_url:
        raise ValueError("DATABASE_URL not configured; discovery jobs require database access")

    def _run_discovery_sync() -> int:
        """Run discovery jobs synchronously in thread pool."""
        db_manager = DatabaseManager(
            primary_url=settings.database_url,
            replica_url=settings.database_read_url,
            pool_size=settings.db_pool_size,
        )
        try:
            executor = DiscoveryExecutor(
                db_write=db_manager.write,
                db_read=db_manager.read,
            )
            executed = executor.run_pending()
            return executed
        finally:
            db_manager.close()

    try:
        # Run sync executor in thread pool to avoid blocking event loop
        executed = await asyncio.to_thread(_run_discovery_sync)

        result = {
            "status": "success",
            "jobs_executed": executed,
            "job_id": envelope.job_id,
        }

        logger.info(
            "discovery_job_completed",
            job_id=envelope.job_id,
            jobs_executed=executed,
        )

        return result

    except Exception as e:
        logger.error(
            "discovery_job_failed",
            job_id=envelope.job_id,
            error=str(e),
            exc_info=True,
        )
        raise


async def handle_sbom_scan(envelope: JobEnvelope) -> dict[str, Any]:
    """Handle SBOM scan job from job-bus (STUB).

    SBOM scan worker logic is not yet ported from Wave-1.
    This is a placeholder that logs receipt and returns not-implemented status.

    Args:
        envelope: JobEnvelope with SBOM scan job details

    Returns:
        Result dict with stub status

    Raises:
        NotImplementedError: Indicates SBOM scan is not yet implemented
    """
    logger.warning(
        "sbom_scan_job_stub",
        job_id=envelope.job_id,
        job_type=envelope.job_type,
        payload=envelope.payload,
        message="SBOM scan worker logic not yet ported — stub handler",
    )

    raise NotImplementedError(
        "SBOM scan worker logic not yet ported from Wave-1; "
        "this handler is a stub for Wave-2 infrastructure testing"
    )


async def handle_helpdesk_email_send(envelope: JobEnvelope) -> dict[str, Any]:
    """Handle helpdesk email send job.

    Sends outbound email for a ticket message via configured SMTP account.
    Payload: {ticket_id, message_id_db, to_addrs, subject, body_text, body_html, email_account_id}

    Args:
        envelope: JobEnvelope with email send details

    Returns:
        Result dict with status and message_id

    Raises:
        Exception: If email send fails
    """
    from apps.api.modules.helpdesk.worker.send import send_email
    from shared.database.manager import DatabaseManager

    logger.info(
        "helpdesk_email_send_job_received",
        job_id=envelope.job_id,
        payload=envelope.payload,
    )

    from apps.worker.config.settings import settings

    if not settings.database_url:
        raise ValueError("DATABASE_URL not configured; email send requires database access")

    payload = envelope.payload
    ticket_id = payload.get("ticket_id")
    message_id_db = payload.get("message_id_db")
    to_addrs = payload.get("to_addrs", [])
    subject = payload.get("subject", "")
    body_text = payload.get("body_text", "")
    body_html = payload.get("body_html")
    email_account_id = payload.get("email_account_id")

    def _setup_db():
        db_manager = DatabaseManager(
            primary_url=settings.database_url,
            replica_url=settings.database_read_url,
            pool_size=settings.db_pool_size,
        )
        return db_manager

    db_manager = _setup_db()

    try:
        result = await send_email(
            db=db_manager.write,
            ticket_id=ticket_id,
            message_id_db=message_id_db,
            to_addrs=to_addrs,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            email_account_id=email_account_id,
        )

        logger.info(
            "helpdesk_email_send_completed",
            job_id=envelope.job_id,
            result=result,
        )

        return result

    except Exception as e:
        logger.error(
            "helpdesk_email_send_failed",
            job_id=envelope.job_id,
            error=str(e),
            exc_info=True,
        )
        raise

    finally:
        db_manager.close()


async def handle_helpdesk_email_poll(envelope: JobEnvelope) -> dict[str, Any]:
    """Handle helpdesk email poll job.

    Polls IMAP account for new emails, parses them, and creates/updates tickets.
    Payload: {email_account_id, tenant_id}

    Args:
        envelope: JobEnvelope with email poll details

    Returns:
        Result dict with status, tickets_created, messages_appended

    Raises:
        Exception: If email poll fails
    """
    from apps.api.modules.helpdesk.worker.poll import poll_email_account
    from shared.database.manager import DatabaseManager

    logger.info(
        "helpdesk_email_poll_job_received",
        job_id=envelope.job_id,
        payload=envelope.payload,
    )

    from apps.worker.config.settings import settings

    if not settings.database_url:
        raise ValueError("DATABASE_URL not configured; email poll requires database access")

    payload = envelope.payload
    email_account_id = payload.get("email_account_id")
    tenant_id = envelope.tenant_id

    if not tenant_id or not email_account_id:
        raise ValueError("Missing tenant_id or email_account_id in payload")

    def _setup_db():
        db_manager = DatabaseManager(
            primary_url=settings.database_url,
            replica_url=settings.database_read_url,
            pool_size=settings.db_pool_size,
        )
        return db_manager

    db_manager = _setup_db()

    try:
        result = await poll_email_account(
            db=db_manager.write,
            email_account_id=email_account_id,
            tenant_id=tenant_id,
        )

        logger.info(
            "helpdesk_email_poll_completed",
            job_id=envelope.job_id,
            result=result,
        )

        return result

    except Exception as e:
        logger.error(
            "helpdesk_email_poll_failed",
            job_id=envelope.job_id,
            error=str(e),
            exc_info=True,
        )
        raise

    finally:
        db_manager.close()


async def handle_helpdesk_sla_breach(envelope: JobEnvelope) -> dict[str, Any]:
    """Handle helpdesk SLA breach check job.

    Finds tickets with breached SLAs and flags them.
    Payload: {} (tenant_id from envelope)

    Args:
        envelope: JobEnvelope with SLA breach check details

    Returns:
        Result dict with status, breached_count, newly_flagged_count

    Raises:
        Exception: If SLA check fails
    """
    from apps.api.modules.helpdesk.worker.sla_breach import check_sla_breaches
    from shared.database.manager import DatabaseManager

    logger.info(
        "helpdesk_sla_breach_job_received",
        job_id=envelope.job_id,
        tenant_id=envelope.tenant_id,
    )

    from apps.worker.config.settings import settings

    if not settings.database_url:
        raise ValueError("DATABASE_URL not configured; SLA check requires database access")

    tenant_id = envelope.tenant_id
    if not tenant_id:
        raise ValueError("Missing tenant_id in envelope")

    def _setup_db():
        db_manager = DatabaseManager(
            primary_url=settings.database_url,
            replica_url=settings.database_read_url,
            pool_size=settings.db_pool_size,
        )
        return db_manager

    db_manager = _setup_db()

    try:
        result = await check_sla_breaches(
            db=db_manager.write,
            tenant_id=tenant_id,
        )

        logger.info(
            "helpdesk_sla_breach_completed",
            job_id=envelope.job_id,
            result=result,
        )

        return result

    except Exception as e:
        logger.error(
            "helpdesk_sla_breach_failed",
            job_id=envelope.job_id,
            error=str(e),
            exc_info=True,
        )
        raise

    finally:
        db_manager.close()


# Handler registry: group name -> async handler
HANDLER_REGISTRY: dict[str, JobHandler] = {
    "discovery": handle_discovery,
    "sbom_scan": handle_sbom_scan,
    "helpdesk_email_send": handle_helpdesk_email_send,
    "helpdesk_email_poll": handle_helpdesk_email_poll,
    "helpdesk_sla_breach": handle_helpdesk_sla_breach,
}


def get_handler(group: str) -> Optional[JobHandler]:
    """Get handler for a job group.

    Args:
        group: Worker task group name

    Returns:
        Handler function if found, None otherwise
    """
    return HANDLER_REGISTRY.get(group)
