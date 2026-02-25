"""Discovery job executor for the worker service.

Polls discovery_jobs table directly for pending cloud discovery jobs
(aws, gcp, azure, kubernetes) and executes them. Scanner-type jobs
(network, banner, http_screenshot) are NOT handled here — those are
polled by the scanner service via the API.
"""

# flake8: noqa: E501

import logging
from datetime import datetime, timezone
from typing import Optional

from pydal import DAL

from apps.worker.discovery.service import DiscoveryService

logger = logging.getLogger(__name__)

# Cloud provider types handled by the worker
CLOUD_PROVIDERS = {"aws", "gcp", "azure", "kubernetes"}


class DiscoveryExecutor:
    """Polls DB for pending discovery jobs and executes them.

    The executor uses the worker's direct DB connection to:
    1. Query discovery_jobs where next_run_at <= now and status is pending
    2. Execute cloud discovery via DiscoveryService
    3. Store results directly in the database

    Scanner-type jobs (network, banner, http_screenshot) are left for
    the scanner service, which polls the API for those.
    """

    def __init__(self, db_write: DAL, db_read: Optional[DAL] = None):
        """Initialize the executor.

        Args:
            db_write: PyDAL database instance (write connection)
            db_read: PyDAL database instance (read replica, defaults to db_write)
        """
        self.db_write = db_write
        self.db_read = db_read or db_write
        self.discovery_service = DiscoveryService(db_write)

    def get_pending_jobs(self) -> list:
        """Get discovery jobs that are due for execution.

        Returns jobs where:
        - enabled = True
        - provider is a cloud type (aws, gcp, azure, kubernetes)
        - next_run_at <= now OR last_run_at is None (never run)
        """
        now = datetime.now(timezone.utc)

        try:
            query = (
                (self.db_read.discovery_jobs.enabled == True)  # noqa: E712
                & (self.db_read.discovery_jobs.provider.belongs(list(CLOUD_PROVIDERS)))
            )

            # Filter by schedule: either never run or overdue
            jobs = self.db_read(query).select()

            pending = []
            for job in jobs:
                if job.last_run_at is None:
                    # Never been run — execute immediately
                    pending.append(job)
                elif job.schedule_interval:
                    # Check if enough time has passed since last run
                    last_run = job.last_run_at
                    if last_run.tzinfo is None:
                        last_run = last_run.replace(tzinfo=timezone.utc)
                    elapsed = (now - last_run).total_seconds()
                    if elapsed >= job.schedule_interval:
                        pending.append(job)

            return pending

        except Exception as e:
            logger.error(f"Failed to query pending discovery jobs: {e}")
            return []

    def execute_job(self, job_id: int) -> Optional[dict]:
        """Execute a single discovery job.

        Args:
            job_id: ID of the discovery job to execute

        Returns:
            Result dict from DiscoveryService.run_discovery(), or None on error
        """
        logger.info(f"Executing discovery job {job_id}")

        try:
            result = self.discovery_service.run_discovery(job_id)

            if result.get("success"):
                logger.info(
                    f"Discovery job {job_id} completed: "
                    f"{result.get('resources_discovered', 0)} resources found"
                )
            else:
                logger.warning(
                    f"Discovery job {job_id} failed: {result.get('error', 'unknown')}"
                )

            return result

        except Exception as e:
            logger.error(f"Discovery job {job_id} raised exception: {e}", exc_info=True)
            return None

    def run_pending(self) -> int:
        """Execute all pending discovery jobs.

        Returns:
            Number of jobs executed
        """
        pending = self.get_pending_jobs()

        if not pending:
            return 0

        logger.info(f"Found {len(pending)} pending discovery job(s)")

        executed = 0
        for job in pending:
            self.execute_job(job.id)
            executed += 1

        return executed
