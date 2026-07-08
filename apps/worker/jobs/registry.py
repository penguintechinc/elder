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


# Handler registry: group name -> async handler
HANDLER_REGISTRY: dict[str, JobHandler] = {
    "discovery": handle_discovery,
    "sbom_scan": handle_sbom_scan,
}


def get_handler(group: str) -> Optional[JobHandler]:
    """Get handler for a job group.

    Args:
        group: Worker task group name

    Returns:
        Handler function if found, None otherwise
    """
    return HANDLER_REGISTRY.get(group)
