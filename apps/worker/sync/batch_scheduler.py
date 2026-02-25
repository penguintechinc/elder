"""Batch sync scheduler for fallback and periodic synchronization.

This module implements scheduled batch synchronization as a fallback mechanism
when webhooks fail or timeout. It also handles platforms that don't support
webhooks effectively.

The scheduler runs periodic batch syncs based on configurable intervals and
monitors webhook health to automatically trigger fallback syncs when needed.
"""

# flake8: noqa: E501


import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from pydal import DAL

from apps.worker.config.settings import settings
from apps.worker.sync.base import BaseSyncClient, ResourceType


@dataclass
class BatchSyncJob:
    """Represents a batch sync job configuration.

    Attributes:
        job_id: Unique job identifier
        platform: Platform name
        sync_config_id: Sync configuration ID
        resource_types: List of resource types to sync
        interval_seconds: Sync interval in seconds
        enabled: Whether job is enabled
        last_run: Last run timestamp
        next_run: Next scheduled run timestamp
    """

    job_id: str
    platform: str
    sync_config_id: int
    resource_types: List[ResourceType]
    interval_seconds: int
    enabled: bool = True
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None


class BatchSyncScheduler:
    """Scheduler for batch synchronization operations.

    Manages periodic batch syncs with configurable intervals and automatic
    fallback when webhook delivery fails.
    """

    def __init__(
        self,
        db: DAL,
        sync_clients: Dict[str, BaseSyncClient],
        logger: Any,
    ):
        """Initialize batch sync scheduler.

        Args:
            db: PyDAL database instance
            sync_clients: Dictionary of platform sync clients
            logger: Logger instance
        """
        self.db = db
        self.sync_clients = sync_clients
        self.logger = logger
        self.scheduler = AsyncIOScheduler()
        self.jobs: Dict[str, BatchSyncJob] = {}

        # Webhook timeout tracking
        self.webhook_timeouts: Dict[int, int] = {}  # sync_config_id -> timeout_count
        self.max_webhook_timeouts = 3  # Trigger fallback after N timeouts

    def start(self) -> None:
        """Start the batch sync scheduler."""
        self.logger.info("Starting batch sync scheduler")

        # Load sync configurations from database
        self._load_sync_configs()

        # Schedule monitoring job to check webhook health
        if settings.sync_batch_fallback_enabled:
            self.scheduler.add_job(
                self._monitor_webhook_health,
                trigger=IntervalTrigger(seconds=300),  # Check every 5 minutes
                id="webhook_health_monitor",
                name="Webhook Health Monitor",
            )

        # Start scheduler
        self.scheduler.start()
        self.logger.info(f"Batch sync scheduler started with {len(self.jobs)} jobs")

    def stop(self) -> None:
        """Stop the batch sync scheduler."""
        self.logger.info("Stopping batch sync scheduler")
        self.scheduler.shutdown(wait=True)

    def add_batch_job(
        self,
        platform: str,
        sync_config_id: int,
        resource_types: List[ResourceType],
        interval_seconds: Optional[int] = None,
    ) -> str:
        """Add a new batch sync job.

        Args:
            platform: Platform name
            sync_config_id: Sync configuration ID
            resource_types: Resource types to sync
            interval_seconds: Sync interval (uses config default if None)

        Returns:
            Job ID
        """
        job_id = f"{platform}_{sync_config_id}_batch"

        interval = interval_seconds or settings.sync_batch_interval

        job = BatchSyncJob(
            job_id=job_id,
            platform=platform,
            sync_config_id=sync_config_id,
            resource_types=resource_types,
            interval_seconds=interval,
            enabled=settings.sync_batch_fallback_enabled,
        )

        # Add to scheduler if enabled
        if job.enabled:
            self.scheduler.add_job(
                self._run_batch_sync,
                trigger=IntervalTrigger(seconds=interval),
                args=[job],
                id=job_id,
                name=f"Batch Sync: {platform} (config {sync_config_id})",
            )

        self.jobs[job_id] = job

        self.logger.info(
            f"Added batch sync job: {job_id} (interval: {interval}s)",
            extra={"job_id": job_id, "platform": platform},
        )

        return job_id

    def remove_batch_job(self, job_id: str) -> None:
        """Remove a batch sync job.

        Args:
            job_id: Job ID to remove
        """
        if job_id in self.jobs:
            try:
                self.scheduler.remove_job(job_id)
            except Exception as e:
                self.logger.error(f"Error removing job {job_id}: {e}")

            del self.jobs[job_id]
            self.logger.info(f"Removed batch sync job: {job_id}")

    async def _run_batch_sync(self, job: BatchSyncJob) -> None:
        """Execute a batch sync job.

        Args:
            job: Batch sync job to execute
        """
        correlation_id = f"batch_{job.job_id}_{int(datetime.now().timestamp())}"

        self.logger.info(
            f"Starting batch sync: {job.job_id}",
            extra={"correlation_id": correlation_id, "job_id": job.job_id},
        )

        job.last_run = datetime.now()

        # Get sync client
        sync_client = self.sync_clients.get(job.platform)
        if not sync_client:
            self.logger.error(f"No sync client found for platform: {job.platform}")
            return

        # Update sync config last_batch_sync_at
        self.db(self.db.sync_configs.id == job.sync_config_id).update(
            last_batch_sync_at=datetime.now()
        )
        self.db.commit()

        # Sync each resource type
        total_synced = 0
        total_failed = 0

        for resource_type in job.resource_types:
            try:
                # Get last sync time for incremental sync
                config_row = (
                    self.db(self.db.sync_configs.id == job.sync_config_id)
                    .select()
                    .first()
                )
                since = config_row.last_sync_at if config_row else None

                # Perform batch sync
                result = await asyncio.to_thread(
                    sync_client.batch_sync,
                    resource_type=resource_type,
                    since=since,
                )

                total_synced += result.items_synced
                total_failed += result.items_failed

                self.logger.info(
                    f"Batch sync completed for {resource_type.value}: "
                    f"{result.items_synced} synced, {result.items_failed} failed",
                    extra={
                        "correlation_id": correlation_id,
                        "resource_type": resource_type.value,
                        "items_synced": result.items_synced,
                        "items_failed": result.items_failed,
                    },
                )

            except Exception as e:
                self.logger.error(
                    f"Batch sync failed for {resource_type.value}: {e}",
                    extra={"correlation_id": correlation_id, "error": str(e)},
                    exc_info=True,
                )
                total_failed += 1

        # Update sync config last_sync_at
        self.db(self.db.sync_configs.id == job.sync_config_id).update(
            last_sync_at=datetime.now()
        )
        self.db.commit()

        # Record in sync history
        self.db.sync_history.insert(
            sync_config_id=job.sync_config_id,
            correlation_id=correlation_id,
            sync_type="batch",
            items_synced=total_synced,
            items_failed=total_failed,
            started_at=job.last_run,
            completed_at=datetime.now(),
            success=(total_failed == 0),
            sync_metadata={
                "job_id": job.job_id,
                "resource_types": [rt.value for rt in job.resource_types],
            },
        )
        self.db.commit()

        self.logger.info(
            f"Batch sync job completed: {job.job_id} "
            f"({total_synced} synced, {total_failed} failed)",
            extra={
                "correlation_id": correlation_id,
                "total_synced": total_synced,
                "total_failed": total_failed,
            },
        )

    async def _monitor_webhook_health(self) -> None:
        """Monitor webhook health and trigger fallback if needed.

        Checks for sync configs with webhook failures and automatically
        triggers batch sync as fallback.
        """
        self.logger.debug("Monitoring webhook health")

        # Query sync history for recent webhook failures
        cutoff_time = datetime.now() - timedelta(minutes=30)

        recent_failures = self.db(
            (self.db.sync_history.sync_type == "webhook")
            & (self.db.sync_history.success is False)
            & (self.db.sync_history.started_at > cutoff_time)
        ).select()

        # Count failures per sync_config
        failure_counts: Dict[int, int] = {}
        for failure in recent_failures:
            sync_config_id = failure.sync_config_id
            failure_counts[sync_config_id] = failure_counts.get(sync_config_id, 0) + 1

        # Trigger batch fallback for configs with too many failures
        for sync_config_id, count in failure_counts.items():
            if count >= self.max_webhook_timeouts:
                self.logger.warning(
                    f"Webhook failures detected for sync_config {sync_config_id} "
                    f"({count} failures), triggering batch fallback",
                    extra={"sync_config_id": sync_config_id, "failure_count": count},
                )

                # Find corresponding batch job
                for job_id, job in self.jobs.items():
                    if job.sync_config_id == sync_config_id and job.enabled:
                        # Trigger immediate batch sync
                        await self._run_batch_sync(job)

    def _load_sync_configs(self) -> None:
        """Load sync configurations from database and create batch jobs."""
        configs = self.db(
            (self.db.sync_configs.enabled is True)
            & (self.db.sync_configs.batch_fallback_enabled is True)
        ).select()

        for config in configs:
            # Determine resource types to sync
            resource_types = [
                ResourceType.ISSUE,
                ResourceType.PROJECT,
                ResourceType.MILESTONE,
                ResourceType.LABEL,
            ]

            # Create batch job
            self.add_batch_job(
                platform=config.platform,
                sync_config_id=config.id,
                resource_types=resource_types,
                interval_seconds=config.sync_interval,
            )

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a batch sync job.

        Args:
            job_id: Job ID

        Returns:
            Job status dictionary or None if not found
        """
        job = self.jobs.get(job_id)
        if not job:
            return None

        scheduler_job = self.scheduler.get_job(job_id)

        return {
            "job_id": job.job_id,
            "platform": job.platform,
            "sync_config_id": job.sync_config_id,
            "enabled": job.enabled,
            "interval_seconds": job.interval_seconds,
            "last_run": job.last_run.isoformat() if job.last_run else None,
            "next_run": (
                scheduler_job.next_run_time.isoformat() if scheduler_job else None
            ),
        }

    def list_jobs(self) -> List[Dict[str, Any]]:
        """List all batch sync jobs.

        Returns:
            List of job status dictionaries
        """
        return [
            self.get_job_status(job_id)
            for job_id in self.jobs.keys()
            if self.get_job_status(job_id)
        ]
