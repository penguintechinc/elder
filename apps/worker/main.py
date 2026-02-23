"""Elder Worker Service - Main orchestrator."""

# flake8: noqa: E501


import asyncio
import signal
import sys
from typing import List

import aiocron
from flask import Flask, jsonify
from prometheus_client import Counter, Gauge, Histogram, generate_latest

from apps.worker.config.settings import settings
from apps.worker.connectors.authentik_connector import AuthentikConnector
from apps.worker.connectors.aws_connector import AWSConnector
from apps.worker.connectors.base import BaseConnector, SyncResult
from apps.worker.connectors.gcp_connector import GCPConnector
from apps.worker.connectors.google_workspace_connector import (
    GoogleWorkspaceConnector,
)
from apps.worker.connectors.ldap_connector import LDAPConnector
from apps.worker.connectors.lxd_connector import LXDConnector
from apps.worker.connectors.okta_connector import OktaConnector
from apps.worker.utils.logger import configure_logging, get_logger

# Configure logging
configure_logging()
logger = get_logger(__name__)

# Prometheus metrics
discovery_jobs_executed = Counter(
    "worker_discovery_jobs_executed_total",
    "Total number of discovery jobs executed",
    ["provider", "status"],
)
discovery_poll_duration = Histogram(
    "worker_discovery_poll_duration_seconds",
    "Discovery poll cycle duration",
)
sync_total = Counter(
    "worker_sync_total",
    "Total number of sync operations",
    ["connector", "status"],
)
sync_duration = Histogram(
    "worker_sync_duration_seconds",
    "Sync operation duration",
    ["connector"],
)
sync_errors = Counter(
    "worker_sync_errors_total",
    "Total number of sync errors",
    ["connector"],
)
entities_synced = Gauge(
    "worker_entities_synced",
    "Number of entities synced",
    ["connector", "operation"],
)
organizations_synced = Gauge(
    "worker_organizations_synced",
    "Number of organizations synced",
    ["connector", "operation"],
)
last_sync_timestamp = Gauge(
    "worker_last_sync_timestamp",
    "Timestamp of last successful sync",
    ["connector"],
)


class WorkerService:
    """Main worker service orchestrator."""

    def __init__(self):
        """Initialize worker service."""
        self.connectors: List[BaseConnector] = []
        self.running = False
        self.sync_tasks: List[asyncio.Task] = []
        self.db_manager = None
        self.discovery_executor = None
        self.health_app = Flask(__name__)
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
        """Setup Flask health check and metrics endpoints."""

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

        @self.health_app.route("/metrics")
        def metrics():
            """Prometheus metrics endpoint."""
            return generate_latest(), 200

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

        with sync_duration.labels(connector=connector.name).time():
            try:
                # Connect to connector
                await connector.connect()

                # Perform sync
                result = await connector.sync()

                # Update metrics
                if result.has_errors:
                    sync_total.labels(connector=connector.name, status="partial").inc()
                    sync_errors.labels(connector=connector.name).inc(len(result.errors))
                else:
                    sync_total.labels(connector=connector.name, status="success").inc()

                entities_synced.labels(
                    connector=connector.name,
                    operation="created",
                ).set(result.entities_created)
                entities_synced.labels(
                    connector=connector.name,
                    operation="updated",
                ).set(result.entities_updated)
                organizations_synced.labels(
                    connector=connector.name,
                    operation="created",
                ).set(result.organizations_created)
                organizations_synced.labels(
                    connector=connector.name,
                    operation="updated",
                ).set(result.organizations_updated)

                import time

                last_sync_timestamp.labels(connector=connector.name).set(time.time())

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
                sync_total.labels(connector=connector.name, status="failed").inc()
                sync_errors.labels(connector=connector.name).inc()

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
            with discovery_poll_duration.time():
                executed = self.discovery_executor.run_pending()
                if executed > 0:
                    discovery_jobs_executed.labels(provider="cloud", status="success").inc(executed)
                    logger.info(f"Discovery poll completed: {executed} job(s) executed")
        except Exception as e:
            discovery_jobs_executed.labels(provider="cloud", status="failed").inc()
            logger.error(f"Discovery poll failed: {e}", exc_info=True)

    def _setup_scheduled_syncs(self):
        """Setup scheduled sync tasks using aiocron."""
        logger.info("Setting up scheduled syncs")

        # Schedule discovery job polling (every 5 minutes)
        if self.discovery_executor:
            @aiocron.crontab("*/5 * * * *")
            async def discovery_poll():
                await self._run_discovery_poll()

            logger.info("Scheduled discovery job polling (every 5 minutes)")

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

        # Run initial sync if configured
        if settings.sync_on_startup:
            logger.info("Running initial sync on startup")
            await self._run_sync_cycle()
            # Also run initial discovery poll
            await self._run_discovery_poll()

        # Setup scheduled syncs (includes discovery polling)
        self._setup_scheduled_syncs()

        logger.info(
            "Elder Worker Service started",
            health_port=settings.health_check_port,
        )

    async def stop(self):
        """Stop the worker service."""
        logger.info("Stopping Elder Worker Service")
        self.running = False

        # Cancel all sync tasks
        for task in self.sync_tasks:
            task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self.sync_tasks, return_exceptions=True)

        # Close database connections
        if self.db_manager:
            self.db_manager.close()

        logger.info("Elder Worker Service stopped")

    def run_health_server(self):
        """Run Flask health check server in a separate thread."""
        import threading

        def run_flask():
            self.health_app.run(
                host="0.0.0.0",
                port=settings.health_check_port,
                debug=False,
                use_reloader=False,
            )

        health_thread = threading.Thread(target=run_flask, daemon=True)
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
