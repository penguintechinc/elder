"""Flows invoker service — standalone Redis Streams consumer for flow jobs.

Consumes flow-execution jobs from the ``flows`` job-bus stream and runs them via
``execute_promotion_pipeline``. This is a dedicated, isolated process (its own
image) — it does NOT load the consolidated worker's task groups, keeping the
arbitrary-command execution surface separated from the rest of the platform.

The consolidated worker never claims the ``flows`` group (the flows module
declares ``worker_task_groups=()``), so there is no double-consumption.
"""

import asyncio
import logging
import os
import signal
import socket

import redis.asyncio

from apps.flows_invoker.executor import execute_promotion_pipeline
from apps.worker.config.settings import settings
from shared.jobbus import JobBus

logger = logging.getLogger(__name__)

# Dedicated job-bus stream group for flow executions.
FLOWS_GROUP = "flows"


class FlowsInvokerService:
    """Isolated consumer that runs CI/CD flow-execution jobs."""

    def __init__(self) -> None:
        self.running = False
        self.redis: redis.asyncio.Redis | None = None
        self.jobbus: JobBus | None = None
        self.db_manager = None
        self.consumer_name: str = os.environ.get("POD_NAME") or socket.gethostname()

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        """Initialize dependencies and run the consumer + sweeper loops."""
        self._init_db()
        await self._init_jobbus()
        if not self.jobbus:
            logger.error("flows_invoker: JobBus unavailable, cannot start")
            return

        self.running = True
        self._install_signal_handlers()
        logger.info(
            "flows_invoker_started",
            extra={"consumer": self.consumer_name, "group": FLOWS_GROUP},
        )
        await asyncio.gather(self._consumer_loop(), self._sweeper_loop())

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._request_stop)
            except NotImplementedError:  # pragma: no cover - non-unix
                pass

    def _request_stop(self) -> None:
        logger.info("flows_invoker_stop_requested")
        self.running = False

    def _init_db(self) -> None:
        """Initialize penguin-dal via the shared DatabaseManager."""
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required for the flows invoker")
        from shared.database.manager import DatabaseManager

        self.db_manager = DatabaseManager(
            primary_url=settings.database_url,
            replica_url=settings.database_read_url,
            pool_size=settings.db_pool_size,
        )
        logger.info("flows_invoker_db_ready")

    async def _init_jobbus(self) -> None:
        try:
            self.redis = redis.asyncio.from_url(settings.redis_url)
            self.jobbus = JobBus(self.redis, max_deliveries=5, group="workers")
            await self.jobbus.ensure_group(FLOWS_GROUP)
            logger.info("flows_invoker_jobbus_ready")
        except Exception as e:  # noqa: BLE001
            logger.error(
                "flows_invoker_jobbus_init_failed",
                extra={"error": str(e)},
                exc_info=True,
            )

    # -- job handling ----------------------------------------------------

    async def _handle(self, envelope) -> dict:
        """Run a single flow-execution job in a worker thread."""
        payload = envelope.payload or {}
        tenant_id = envelope.tenant_id or payload.get("tenant_id")
        promotion_id = payload.get("promotion_id")
        started_by = payload.get("started_by_identity_id")
        if tenant_id is None or promotion_id is None:
            raise ValueError("flow job payload missing tenant_id/promotion_id")

        db = self.db_manager.write
        return await asyncio.to_thread(
            execute_promotion_pipeline,
            db,
            int(tenant_id),
            int(promotion_id),
            started_by,
        )

    async def _process(self, msg_id: str, envelope) -> None:
        job_id = envelope.job_id
        try:
            if await self.jobbus.is_duplicate(job_id):
                await self.jobbus.ack(FLOWS_GROUP, msg_id)
                return
            result = await self._handle(envelope)
            await self.jobbus.mark_processed(job_id)
            await self.jobbus.publish_result(
                FLOWS_GROUP, job_id, "success", result or {}
            )
            await self.jobbus.ack(FLOWS_GROUP, msg_id)
            logger.info("flow_job_completed", extra={"job_id": job_id})
        except Exception as e:  # noqa: BLE001
            logger.error(
                "flow_job_failed",
                extra={"job_id": job_id, "msg_id": msg_id, "error": str(e)},
                exc_info=True,
            )
            # Do NOT ack — leave pending for XAUTOCLAIM reclaim.

    async def _consumer_loop(self) -> None:
        while self.running:
            try:
                messages = await self.jobbus.read(
                    FLOWS_GROUP, self.consumer_name, count=5, block_ms=5000
                )
                for msg_id, envelope in messages:
                    await self._process(msg_id, envelope)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "flows_invoker_consumer_error",
                    extra={"error": str(e)},
                    exc_info=True,
                )
                await asyncio.sleep(1)
        logger.info("flows_invoker_consumer_stopped")

    async def _sweeper_loop(self) -> None:
        """Reclaim stale pending messages every 60s (DLQ past max deliveries)."""
        while self.running:
            await asyncio.sleep(60)
            try:
                reclaimed = await self.jobbus.reclaim_stale(
                    FLOWS_GROUP, self.consumer_name, min_idle_ms=60000
                )
                for msg in reclaimed:
                    await self._process(msg.msg_id, msg.envelope)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "flows_invoker_sweeper_error",
                    extra={"error": str(e)},
                    exc_info=True,
                )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(FlowsInvokerService().start())


if __name__ == "__main__":
    main()
