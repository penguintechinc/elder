"""Elder Worker Service - Main orchestrator."""

# flake8: noqa: E501

import asyncio
import os
import signal
import socket
import sys
import time
from typing import List

import aiocron
import redis.asyncio
from quart import Quart, jsonify

from apps.worker.config.settings import settings
from apps.worker.connectors.authentik_connector import AuthentikConnector
from apps.worker.connectors.aws_connector import AWSConnector
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.connectors.gcp_connector import GCPConnector
from apps.worker.connectors.google_workspace_connector import GoogleWorkspaceConnector
from apps.worker.connectors.ldap_connector import LDAPConnector
from apps.worker.connectors.lxd_connector import LXDConnector
from apps.worker.connectors.okta_connector import OktaConnector
from apps.worker.jobs.groups import resolve_worker_groups
from apps.worker.jobs.registry import get_handler
from apps.worker.utils.logger import configure_logging, get_logger
from shared.jobbus import JobBus
from shared.redaction import redact_url

# Configure logging
configure_logging()
logger = get_logger(__name__)


# OpenTelemetry metrics (Phase 0.5 observability)
def _init_otel_metrics():
    """Initialize OTel metrics for worker sync/discovery operations."""
    from shared.observability import get_meter

    meter = get_meter("elder-worker")

    # Counters for job/sync execution and errors
    job_counter = meter.create_counter(
        "worker.discovery.jobs.executed",
        unit="1",
        description="Total number of discovery jobs executed",
    )
    sync_counter = meter.create_counter(
        "worker.sync.operations",
        unit="1",
        description="Total number of sync operations",
    )
    error_counter = meter.create_counter(
        "worker.sync.errors",
        unit="1",
        description="Total number of sync errors",
    )

    # Histograms for duration measurements
    poll_duration_histogram = meter.create_histogram(
        "worker.discovery.poll.duration",
        unit="s",
        description="Discovery poll cycle duration",
    )
    sync_duration_histogram = meter.create_histogram(
        "worker.sync.duration",
        unit="s",
        description="Sync operation duration",
    )

    return {
        "job_counter": job_counter,
        "sync_counter": sync_counter,
        "error_counter": error_counter,
        "poll_duration_histogram": poll_duration_histogram,
        "sync_duration_histogram": sync_duration_histogram,
    }


# Initialize OTel metrics at module load
try:
    _otel_metrics = _init_otel_metrics()
except Exception as e:
    logger.warning(f"Failed to initialize OTel metrics: {e}")
    _otel_metrics = None


class WorkerService:
    """Main worker service orchestrator."""

    def __init__(self):
        """Initialize worker service."""
        self.connectors: list[BaseConnector] = []
        self.running = False
        self.sync_tasks: list[asyncio.Task] = []
        self.db_manager = None
        self.discovery_executor = None
        # Job-bus consumer components
        self.redis = None
        self.jobbus = None
        self.worker_groups: set[str] = set()
        self.consumer_task: asyncio.Task | None = None
        self.consumer_name: str = os.environ.get("POD_NAME") or socket.gethostname()
        self.health_app = Quart(__name__)
        self._setup_health_endpoints()
        self._init_database()
        self._init_discovery_executor()

    def _init_database(self):
        """Initialize database connection if DATABASE_URL is configured."""
        if not settings.database_url:
            logger.info("DATABASE_URL not set, worker running without direct DB access")
            return

        try:
            from shared.database.manager import DatabaseManager

            self.db_manager = DatabaseManager(
                primary_url=settings.database_url,
                replica_url=settings.database_read_url,
                pool_size=settings.db_pool_size,
            )
            logger.info("Database connection established (direct DB access enabled)")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}", exc_info=True)
            logger.warning("Worker will continue without direct DB access")

    def _init_discovery_executor(self):
        """Initialize the discovery executor if DB is available."""
        if not self.db_manager:
            logger.info("Discovery executor disabled (no DB connection)")
            return

        try:
            from apps.worker.discovery.executor import DiscoveryExecutor

            self.discovery_executor = DiscoveryExecutor(
                db_write=self.db_manager.write,
                db_read=self.db_manager.read,
            )
            logger.info("Discovery executor initialized")
        except Exception as e:
            logger.error(f"Failed to initialize discovery executor: {e}", exc_info=True)
            logger.warning("Worker will continue without discovery execution")

    def _setup_health_endpoints(self):
        """Setup Quart health check and metrics endpoints."""

        @self.health_app.route("/healthz")
        def health_check():
            """Health check endpoint."""
            health_status = {
                "status": "healthy" if self.running else "stopped",
                "connectors": {},
            }

            for connector in self.connectors:
                health_status["connectors"][connector.name] = {
                    "enabled": True,
                    "healthy": True,  # Will be updated by actual health checks
                }

            return jsonify(health_status), 200

        @self.health_app.route("/status")
        def status():
            """Detailed status endpoint."""
            return (
                jsonify(
                    {
                        "service": "elder-worker",
                        "running": self.running,
                        "connectors": [
                            {
                                "name": c.name,
                                "type": c.__class__.__name__,
                            }
                            for c in self.connectors
                        ],
                        "discovery_executor": self.discovery_executor is not None,
                        "database_connected": self.db_manager is not None,
                        "settings": {
                            "sync_on_startup": settings.sync_on_startup,
                            "aws_enabled": settings.aws_enabled,
                            "gcp_enabled": settings.gcp_enabled,
                            "google_workspace_enabled": settings.google_workspace_enabled,
                            "ldap_enabled": settings.ldap_enabled,
                            "okta_enabled": settings.okta_enabled,
                            "authentik_enabled": settings.authentik_enabled,
                            "lxd_enabled": settings.lxd_enabled,
                        },
                    }
                ),
                200,
            )

    def _initialize_connectors(self):
        """Initialize enabled connectors."""
        logger.info("Initializing connectors")

        if settings.aws_enabled:
            logger.info("AWS connector enabled")
            self.connectors.append(AWSConnector())

        if settings.gcp_enabled:
            logger.info("GCP connector enabled")
            self.connectors.append(GCPConnector())

        if settings.google_workspace_enabled:
            logger.info("Google Workspace connector enabled")
            self.connectors.append(GoogleWorkspaceConnector())

        if settings.ldap_enabled:
            logger.info("LDAP connector enabled")
            self.connectors.append(LDAPConnector())

        if settings.okta_enabled:
            logger.info("Okta connector enabled (Enterprise)")
            self.connectors.append(OktaConnector())

        if settings.authentik_enabled:
            logger.info("Authentik connector enabled (Enterprise)")
            self.connectors.append(AuthentikConnector())

        if settings.lxd_enabled:
            logger.info("LXD connector enabled")
            self.connectors.append(LXDConnector())

        if not self.connectors:
            logger.warning("No connectors enabled! Check your configuration.")

        logger.info(f"Initialized {len(self.connectors)} connector(s)")

    async def _sync_connector(self, connector: BaseConnector) -> SyncResult:
        """
        Sync a single connector with metrics.

        Args:
            connector: Connector to sync

        Returns:
            SyncResult
        """
        logger.info(f"Starting sync for {connector.name}")

        start_time = time.time()
        try:
            # Connect to connector
            await connector.connect()

            # Perform sync
            result = await connector.sync()

            # Update OTel metrics
            duration = time.time() - start_time
            if _otel_metrics:
                _otel_metrics["sync_duration_histogram"].record(
                    duration, {"connector": connector.name}
                )
                if result.has_errors:
                    _otel_metrics["sync_counter"].add(
                        1, {"connector": connector.name, "status": "partial"}
                    )
                    _otel_metrics["error_counter"].add(
                        len(result.errors), {"connector": connector.name}
                    )
                else:
                    _otel_metrics["sync_counter"].add(
                        1, {"connector": connector.name, "status": "success"}
                    )

            logger.info(
                f"Sync completed for {connector.name}",
                **result.to_dict(),
            )

            return result

        except Exception as e:
            logger.error(
                f"Sync failed for {connector.name}",
                error=str(e),
                exc_info=True,
            )
            duration = time.time() - start_time
            if _otel_metrics:
                _otel_metrics["sync_counter"].add(
                    1, {"connector": connector.name, "status": "failed"}
                )
                _otel_metrics["error_counter"].add(1, {"connector": connector.name})
                _otel_metrics["sync_duration_histogram"].record(
                    duration, {"connector": connector.name}
                )

            return SyncResult(
                connector_name=connector.name,
                errors=[str(e)],
            )

        finally:
            # Disconnect connector
            await connector.disconnect()

    async def _run_sync_cycle(self):
        """Run a complete sync cycle for all connectors."""
        logger.info("Starting sync cycle for all connectors")

        results = await asyncio.gather(
            *[self._sync_connector(connector) for connector in self.connectors],
            return_exceptions=True,
        )

        total_ops = sum(
            r.total_operations for r in results if isinstance(r, SyncResult)
        )
        total_errors = sum(len(r.errors) for r in results if isinstance(r, SyncResult))

        logger.info(
            "Sync cycle completed",
            total_operations=total_ops,
            total_errors=total_errors,
        )

    async def _run_discovery_poll(self):
        """Poll for and execute pending cloud discovery jobs."""
        if not self.discovery_executor:
            return

        try:
            start_time = time.time()
            executed = self.discovery_executor.run_pending()
            duration = time.time() - start_time
            if _otel_metrics:
                _otel_metrics["poll_duration_histogram"].record(duration)
                if executed > 0:
                    _otel_metrics["job_counter"].add(
                        executed, {"provider": "cloud", "status": "success"}
                    )
            logger.info(f"Discovery poll completed: {executed} job(s) executed")
        except Exception as e:
            if _otel_metrics:
                _otel_metrics["job_counter"].add(
                    1, {"provider": "cloud", "status": "failed"}
                )
            logger.error(f"Discovery poll failed: {e}", exc_info=True)

    async def _init_jobbus(self) -> None:
        """Initialize Redis and JobBus consumer.

        Connects to Redis, creates JobBus instance, resolves worker groups,
        and ensures consumer groups exist in Redis Streams.
        """
        try:
            # Connect to Redis
            self.redis = redis.asyncio.from_url(settings.redis_url)
            logger.info("redis_connected", url=redact_url(settings.redis_url))

            # Create JobBus instance
            self.jobbus = JobBus(self.redis, max_deliveries=5, group="workers")
            logger.info("jobbus_initialized")

            # Resolve worker groups from enabled modules
            self.worker_groups = resolve_worker_groups(os.environ)

            if not self.worker_groups:
                logger.warning("no_worker_groups_enabled")
                return

            # Ensure consumer groups exist for each worker group
            for group in self.worker_groups:
                try:
                    await self.jobbus.ensure_group(group)
                    logger.info("consumer_group_ensured", group=group)
                except Exception as e:
                    logger.error(
                        "consumer_group_ensure_failed",
                        group=group,
                        error=str(e),
                    )

        except Exception as e:
            logger.error(
                "jobbus_init_failed",
                error=str(e),
                exc_info=True,
            )
            # Continue without job-bus; worker can still run sync connectors

    async def _consumer_loop(self) -> None:
        """Main job-bus consumer loop.

        Reads messages from all registered worker groups, dispatches to handlers,
        and acknowledges on success. On handler exception, leaves the message un-acked
        (will be reclaimed by XAUTOCLAIM for retry).
        """
        if not self.jobbus:
            logger.warning("consumer_loop: JobBus not initialized; exiting")
            return

        logger.info(
            "consumer_loop_starting",
            consumer_name=self.consumer_name,
            groups=sorted(self.worker_groups),
        )

        while self.running:
            try:
                for group in self.worker_groups:
                    try:
                        # Read from job-bus with 5s block timeout
                        messages = await self.jobbus.read(
                            group,
                            self.consumer_name,
                            count=10,
                            block_ms=5000,
                        )

                        if not messages:
                            continue

                        logger.debug(
                            "messages_read",
                            group=group,
                            count=len(messages),
                        )

                        for msg_id, envelope in messages:
                            await self._process_job(group, msg_id, envelope)

                    except Exception as e:
                        logger.error(
                            "consumer_read_failed",
                            group=group,
                            error=str(e),
                            exc_info=True,
                        )
                        # Continue to next group on error

            except Exception as e:
                logger.error(
                    "consumer_loop_error",
                    error=str(e),
                    exc_info=True,
                )
                # Sleep briefly before retry to avoid spin-loop
                await asyncio.sleep(1)

        logger.info("consumer_loop_stopped")

    async def _process_job(self, group: str, msg_id: str, envelope) -> None:
        """Process a single job message.

        Args:
            group: Worker task group name
            msg_id: Message ID from Redis Streams
            envelope: JobEnvelope object

        On success:
        - mark_processed (idempotency)
        - publish_result (success)
        - ack (remove from pending)

        On error:
        - Log exception
        - Do NOT ack (message stays pending for XAUTOCLAIM reclaim)
        """
        job_id = envelope.job_id

        try:
            # Check idempotency
            if await self.jobbus.is_duplicate(job_id):
                logger.debug(
                    "job_duplicate_skipped",
                    job_id=job_id,
                    group=group,
                    msg_id=msg_id,
                )
                await self.jobbus.ack(group, msg_id)
                return

            # Get handler for this group
            handler = get_handler(group)
            if not handler:
                logger.error(
                    "no_handler_found",
                    group=group,
                    job_id=job_id,
                )
                # ACK anyway to avoid replay loop
                await self.jobbus.ack(group, msg_id)
                return

            # Execute handler
            logger.info(
                "job_executing",
                job_id=job_id,
                group=group,
                job_type=envelope.job_type,
            )

            result = await handler(envelope)

            # Mark processed (idempotency)
            await self.jobbus.mark_processed(job_id)

            # Publish result
            await self.jobbus.publish_result(
                group,
                job_id,
                "success",
                result or {},
            )

            # Acknowledge
            await self.jobbus.ack(group, msg_id)

            logger.info(
                "job_completed",
                job_id=job_id,
                group=group,
            )

        except Exception as e:
            logger.error(
                "job_failed",
                job_id=job_id,
                group=group,
                msg_id=msg_id,
                error=str(e),
                exc_info=True,
            )
            # Do NOT ack; message stays pending for XAUTOCLAIM

    async def _sweeper_task(self) -> None:
        """XAUTOCLAIM sweeper: reclaim stale messages every minute.

        Runs every minute via aiocron. For each group, calls reclaim_stale()
        to identify messages stuck on this consumer for >= 60 seconds.
        Messages at max_deliveries are auto-routed to DLQ.
        Others are re-dispatched through _process_job.
        """
        if not self.jobbus:
            logger.warning("sweeper_task: JobBus not initialized; exiting")
            return

        for group in self.worker_groups:
            try:
                logger.debug("sweeper_running", group=group)

                reclaimed = await self.jobbus.reclaim_stale(
                    group,
                    self.consumer_name,
                    min_idle_ms=60000,
                    count=10,
                )

                if not reclaimed:
                    continue

                logger.info(
                    "messages_reclaimed",
                    group=group,
                    count=len(reclaimed),
                )

                # Re-dispatch reclaimed messages through handler
                for reclaimed_msg in reclaimed:
                    await self._process_job(
                        group,
                        reclaimed_msg.msg_id,
                        reclaimed_msg.envelope,
                    )

            except Exception as e:
                logger.error(
                    "sweeper_failed",
                    group=group,
                    error=str(e),
                    exc_info=True,
                )
                # Continue to next group; fail-soft

    async def _service_node_heartbeat(self) -> None:
        """Best-effort service_nodes heartbeat (node-count license enforcement).

        Runs every minute via aiocron (see _setup_scheduled_syncs), plus once
        immediately on startup. Registers this pod on first heartbeat if no
        row exists yet (handles worker start racing DB migrations/table
        creation), then refreshes heartbeat_ts on every subsequent call.
        Never raises -- a missed heartbeat just lets this pod's row go stale,
        which self-corrects the node count rather than crashing the worker.
        """
        if not self.db_manager:
            return

        try:
            from apps.api.common.licensing.enforce import check_limit
            from apps.api.models.service_node import heartbeat_node, register_node

            service_type = os.environ.get("ELDER_SERVICE_TYPE", "worker")
            pod_id = os.environ.get("HOSTNAME") or self.consumer_name
            db = self.db_manager.write

            already_registered = await asyncio.get_event_loop().run_in_executor(
                None, lambda: heartbeat_node(db, pod_id)
            )
            if already_registered:
                return

            # Node limits are a soft scale gate: check_limit's WARN log
            # (`license_limit_would_block`) is the entire point of this call
            # -- its 402 return is intentionally discarded so this worker
            # pod is never refused registration, even when the
            # elder.license-enforcement flag is ON (see Phase 2 plan Task 5).
            # self.health_app has no request/DB extensions of its own (it
            # only serves /healthz + /status) -- populate the minimum
            # check_limit needs (db, and a license_client defaulting to None
            # -> community-tier fallback, same graceful degradation as the
            # API app) for the duration of this app_context.
            self.health_app.db = db
            self.health_app.extensions.setdefault("license_client", None)
            async with self.health_app.app_context():
                await check_limit("node", None)

            await asyncio.get_event_loop().run_in_executor(
                None, lambda: register_node(db, service_type, pod_id)
            )
        except Exception as e:
            logger.warning(f"service_node_heartbeat_failed: {e}")

    def _setup_scheduled_syncs(self):
        """Setup scheduled sync tasks using aiocron."""
        logger.info("Setting up scheduled syncs")

        # Schedule service_nodes heartbeat (every minute) for node-count
        # license enforcement (Phase 2, Task 2)
        @aiocron.crontab("* * * * *")
        async def service_node_heartbeat():
            await self._service_node_heartbeat()

        logger.info("Scheduled service_nodes heartbeat (every minute)")

        # Schedule XAUTOCLAIM sweeper (every minute) for job-bus consumer
        if self.jobbus and self.worker_groups:

            @aiocron.crontab("* * * * *")
            async def xautoclaim_sweeper():
                await self._sweeper_task()

            logger.info("Scheduled XAUTOCLAIM sweeper (every minute)")

        # Schedule discovery job polling (every 5 minutes)
        if self.discovery_executor:

            @aiocron.crontab("*/5 * * * *")
            async def discovery_poll():
                await self._run_discovery_poll()

            logger.info("Scheduled discovery job polling (every 5 minutes)")

        # Schedule helpdesk email poll (every 2 minutes) if helpdesk_email_poll group enabled
        if self.jobbus and "helpdesk_email_poll" in self.worker_groups:
            import uuid

            @aiocron.crontab("*/2 * * * *")
            async def helpdesk_email_poll_scheduler():
                """Enqueue helpdesk email poll jobs for all active email accounts."""
                try:
                    from shared.database.manager import DatabaseManager

                    def _enqueue_polls():
                        """Fetch email accounts and enqueue polls (sync)."""
                        import asyncio

                        db_manager = DatabaseManager(
                            primary_url=settings.database_url,
                            replica_url=settings.database_read_url,
                            pool_size=settings.db_pool_size,
                        )

                        try:
                            # Fetch all active email accounts
                            rows = db_manager.write(
                                db_manager.write.hd_email_accounts.is_active == True  # noqa: E712
                            ).select()

                            jobs_to_enqueue = []
                            for account in rows:
                                tenant_id = account.tenant_id
                                account_id = account.id

                                job_id = str(uuid.uuid4())
                                idempotency_key = f"hd_poll_{account_id}_{tenant_id}"

                                jobs_to_enqueue.append(
                                    {
                                        "stream_group": "helpdesk_email_poll",
                                        "job_type": "email_poll",
                                        "payload": {"email_account_id": account_id},
                                        "job_id": job_id,
                                        "tenant_id": tenant_id,
                                        "idempotency_key": idempotency_key,
                                    }
                                )

                            return jobs_to_enqueue

                        finally:
                            db_manager.close()

                    jobs_to_enqueue = await asyncio.to_thread(_enqueue_polls)

                    for job_kwargs in jobs_to_enqueue:
                        await self.jobbus.enqueue(**job_kwargs)
                        logger.debug(
                            "helpdesk_email_poll_enqueued",
                            job_id=job_kwargs["job_id"],
                        )

                except Exception as e:
                    logger.error(
                        "helpdesk_email_poll_scheduler_failed",
                        error=str(e),
                        exc_info=True,
                    )

            logger.info("Scheduled helpdesk email poll (every 2 minutes)")

        # Schedule helpdesk SLA breach checker (every 5 minutes) if helpdesk_sla_breach group enabled
        if self.jobbus and "helpdesk_sla_breach" in self.worker_groups:
            import uuid

            @aiocron.crontab("*/5 * * * *")
            async def helpdesk_sla_breach_scheduler():
                """Enqueue helpdesk SLA breach check jobs for all tenants."""
                try:
                    from shared.database.manager import DatabaseManager

                    def _enqueue_sla_checks():
                        """Fetch tenants and enqueue SLA breach checks (sync)."""
                        import asyncio

                        db_manager = DatabaseManager(
                            primary_url=settings.database_url,
                            replica_url=settings.database_read_url,
                            pool_size=settings.db_pool_size,
                        )

                        try:
                            # Fetch all distinct tenants with helpdesk module enabled
                            # For now, just query all active tenants (will be
                            # filtered by tenant_modules in prod).
                            # penguin-dal selects go through the DAL callable —
                            # `db.<table>.select()` resolves `select` as a column
                            # name and raises AttributeError.
                            rows = db_manager.write(
                                db_manager.write.tenants.is_active == True  # noqa: E712
                            ).select()

                            jobs_to_enqueue = []
                            for tenant in rows:
                                tenant_id = tenant.id

                                job_id = str(uuid.uuid4())
                                idempotency_key = f"hd_sla_breach_{tenant_id}"

                                jobs_to_enqueue.append(
                                    {
                                        "stream_group": "helpdesk_sla_breach",
                                        "job_type": "sla_breach_check",
                                        "payload": {},
                                        "job_id": job_id,
                                        "tenant_id": tenant_id,
                                        "idempotency_key": idempotency_key,
                                    }
                                )

                            return jobs_to_enqueue

                        finally:
                            db_manager.close()

                    jobs_to_enqueue = await asyncio.to_thread(_enqueue_sla_checks)

                    for job_kwargs in jobs_to_enqueue:
                        await self.jobbus.enqueue(**job_kwargs)
                        logger.debug(
                            "helpdesk_sla_breach_enqueued",
                            job_id=job_kwargs["job_id"],
                        )

                except Exception as e:
                    logger.error(
                        "helpdesk_sla_breach_scheduler_failed",
                        error=str(e),
                        exc_info=True,
                    )

            logger.info("Scheduled helpdesk SLA breach check (every 5 minutes)")

        for connector in self.connectors:
            # Determine sync interval based on connector type
            if isinstance(connector, AWSConnector):
                interval = settings.aws_sync_interval
            elif isinstance(connector, GCPConnector):
                interval = settings.gcp_sync_interval
            elif isinstance(connector, GoogleWorkspaceConnector):
                interval = settings.google_workspace_sync_interval
            elif isinstance(connector, LDAPConnector):
                interval = settings.ldap_sync_interval
            elif isinstance(connector, OktaConnector):
                interval = settings.okta_sync_interval
            elif isinstance(connector, AuthentikConnector):
                interval = settings.authentik_sync_interval
            elif isinstance(connector, LXDConnector):
                interval = settings.lxd_sync_interval
            else:
                interval = 3600  # Default to 1 hour

            # Convert interval to cron expression (every X seconds)
            # For simplicity, we'll use minutes if interval >= 60
            if interval >= 3600:
                hours = interval // 3600
                cron_expr = f"0 */{hours} * * *"  # Every N hours
            elif interval >= 60:
                minutes = interval // 60
                cron_expr = f"*/{minutes} * * * *"  # Every N minutes
            else:
                # For intervals < 60 seconds, just run every minute
                # (aiocron doesn't support sub-minute intervals well)
                cron_expr = "* * * * *"

            logger.info(
                f"Scheduling {connector.name}",
                interval=interval,
                cron=cron_expr,
            )

            # Schedule the sync
            @aiocron.crontab(cron_expr)
            async def scheduled_sync(conn=connector):
                await self._sync_connector(conn)

    async def start(self):
        """Start the worker service."""
        logger.info("Starting Elder Worker Service")
        self.running = True

        # Initialize connectors
        self._initialize_connectors()

        # Initialize job-bus consumer
        await self._init_jobbus()

        # Register/heartbeat this pod immediately (node-count license
        # enforcement, Phase 2 Task 2) rather than waiting up to 60s for the
        # first aiocron tick
        await self._service_node_heartbeat()

        # Run initial sync if configured
        if settings.sync_on_startup:
            logger.info("Running initial sync on startup")
            await self._run_sync_cycle()
            # Also run initial discovery poll
            await self._run_discovery_poll()

        # Setup scheduled syncs (includes discovery polling and XAUTOCLAIM sweeper)
        self._setup_scheduled_syncs()

        # Launch consumer task (keeps running in background)
        if self.jobbus and self.worker_groups:
            self.consumer_task = asyncio.create_task(self._consumer_loop())
            logger.info(
                "job_bus_consumer_launched",
                consumer_name=self.consumer_name,
                groups=sorted(self.worker_groups),
            )

        logger.info(
            "Elder Worker Service started",
            health_port=settings.health_check_port,
        )

    async def stop(self):
        """Stop the worker service."""
        logger.info("Stopping Elder Worker Service")
        self.running = False

        # Cancel consumer task if running
        if self.consumer_task and not self.consumer_task.done():
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                pass

        # Cancel all sync tasks
        for task in self.sync_tasks:
            task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self.sync_tasks, return_exceptions=True)

        # Close Redis connection
        if self.redis:
            await self.redis.close()
            logger.info("redis_connection_closed")

        # Close database connections
        if self.db_manager:
            self.db_manager.close()

        logger.info("Elder Worker Service stopped")

    def run_health_server(self):
        """Run health check server in a separate thread using hypercorn."""
        import asyncio
        import threading

        from hypercorn.asyncio import serve
        from hypercorn.config import Config

        def run_hypercorn():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            config = Config()
            config.bind = [f"0.0.0.0:{settings.health_check_port}"]
            config.loglevel = "WARNING"
            # Disable signal handlers — running in non-main thread
            loop.run_until_complete(
                serve(self.health_app, config, shutdown_trigger=asyncio.Event().wait)
            )

        health_thread = threading.Thread(target=run_hypercorn, daemon=True)
        health_thread.start()
        logger.info(
            "Health check server started",
            port=settings.health_check_port,
        )


async def main():
    """Main entry point."""
    service = WorkerService()

    # Setup signal handlers
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        asyncio.create_task(service.stop())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Start health server
        service.run_health_server()

        # Start worker service
        await service.start()

        # Keep running
        while service.running:
            await asyncio.sleep(1)

    except Exception as e:
        logger.error("Fatal error in worker service", error=str(e), exc_info=True)
        sys.exit(1)
    finally:
        await service.stop()


if __name__ == "__main__":
    asyncio.run(main())
