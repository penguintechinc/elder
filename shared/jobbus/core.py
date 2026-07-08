"""JobBus: Redis Streams job queue with idempotency, DLQ, and XAUTOCLAIM support.

Design:
- Streams: elder:jobs:<group>, elder:results:<group>, elder:jobs:<group>:dead
- Consumer group: "workers" (constant)
- Envelope: JSON in single "data" field with job_id, job_type, tenant_id, payload, enqueued_at, idempotency_key
- Idempotency: elder:jobs:processed:{job_id} via SET NX EX
- DLQ: after MAX_DELIVERIES (default 5), move to dead stream and XACK
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
import structlog
from redis.asyncio import Redis as AsyncRedis

logger = structlog.get_logger(__name__)

# Consumer group name (constant)
DEFAULT_GROUP = "workers"

# Default max deliveries before DLQ
DEFAULT_MAX_DELIVERIES = 5

# Idempotency TTL in seconds (default 24 hours)
DEFAULT_IDEMPOTENCY_TTL = 86400


@dataclass(slots=True)
class JobEnvelope:
    """Job message envelope (JSON-serializable)."""

    job_id: str
    job_type: str
    payload: dict[str, Any]
    enqueued_at: str
    tenant_id: int | None = None
    idempotency_key: str | None = None

    def to_json(self) -> str:
        """Serialize envelope to JSON."""
        return json.dumps(
            {
                "job_id": self.job_id,
                "job_type": self.job_type,
                "tenant_id": self.tenant_id,
                "payload": self.payload,
                "enqueued_at": self.enqueued_at,
                "idempotency_key": self.idempotency_key,
            }
        )

    @staticmethod
    def from_json(data: str) -> JobEnvelope:
        """Deserialize envelope from JSON."""
        parsed = json.loads(data)
        return JobEnvelope(
            job_id=parsed["job_id"],
            job_type=parsed["job_type"],
            payload=parsed.get("payload", {}),
            enqueued_at=parsed.get("enqueued_at", ""),
            tenant_id=parsed.get("tenant_id"),
            idempotency_key=parsed.get("idempotency_key"),
        )


@dataclass(slots=True)
class ReclamedMessage:
    """Message reclaimed by XAUTOCLAIM."""

    msg_id: str
    envelope: JobEnvelope
    delivery_count: int


class JobBus:
    """Redis Streams job queue with idempotency, DLQ, and XAUTOCLAIM support."""

    def __init__(
        self,
        redis_client: AsyncRedis,
        max_deliveries: int = DEFAULT_MAX_DELIVERIES,
        group: str = DEFAULT_GROUP,
        idempotency_ttl: int = DEFAULT_IDEMPOTENCY_TTL,
    ):
        """Initialize JobBus.

        Args:
            redis_client: Async redis.asyncio.Redis instance (injected, not from app context)
            max_deliveries: Max delivery attempts before routing to DLQ (default 5)
            group: Consumer group name (default "workers")
            idempotency_ttl: Idempotency key TTL in seconds (default 86400 = 24h)
        """
        self.redis = redis_client
        self.max_deliveries = max_deliveries
        self.group = group
        self.idempotency_ttl = idempotency_ttl

    def stream_key(self, stream_group: str) -> str:
        """Get the main job stream key for a group."""
        return f"elder:jobs:{stream_group}"

    def results_key(self, stream_group: str) -> str:
        """Get the results stream key for a group."""
        return f"elder:results:{stream_group}"

    def dead_key(self, stream_group: str) -> str:
        """Get the dead-letter stream key for a group."""
        return f"elder:jobs:{stream_group}:dead"

    def idempotency_key(self, job_id: str) -> str:
        """Get the idempotency tracking key for a job."""
        return f"elder:jobs:processed:{job_id}"

    async def ensure_group(self, stream_group: str) -> None:
        """Ensure consumer group exists (create if not, ignore if exists).

        Uses XGROUP CREATE with MKSTREAM flag (creates stream if absent).
        Ignores BUSYGROUP error (group already exists).

        Args:
            stream_group: Stream group name (e.g., "infrastructure", "helpdesk")
        """
        key = self.stream_key(stream_group)
        try:
            await self.redis.xgroup_create(
                key, self.group, id="0", mkstream=True
            )
            logger.debug(
                "consumer_group_created",
                stream_key=key,
                group=self.group,
            )
        except aioredis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.debug(
                    "consumer_group_already_exists",
                    stream_key=key,
                    group=self.group,
                )
            else:
                raise

    async def enqueue(
        self,
        stream_group: str,
        job_type: str,
        payload: dict[str, Any],
        enqueued_at: str,
        tenant_id: int | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Enqueue a job to the stream.

        If idempotency_key is provided and already processed, returns existing job_id.
        Otherwise, generates new UUID job_id and adds to stream.

        Args:
            stream_group: Stream group name (e.g., "infrastructure")
            job_type: Job type identifier (e.g., "sync_entity", "send_email")
            payload: Job payload dict
            enqueued_at: ISO 8601 timestamp (passed in, not generated here)
            tenant_id: Tenant ID (optional)
            idempotency_key: Idempotency key for deduplication (optional)

        Returns:
            Job ID (UUID string)
        """
        from uuid import uuid4

        job_id = str(uuid4())

        # Check idempotency if key provided
        if idempotency_key:
            idempo_key = self.idempotency_key(job_id)
            if await self.is_duplicate(job_id):
                logger.debug(
                    "job_duplicate_detected",
                    job_id=job_id,
                    idempotency_key=idempotency_key,
                    stream_group=stream_group,
                )
                return job_id

        # Build envelope
        envelope = JobEnvelope(
            job_id=job_id,
            job_type=job_type,
            tenant_id=tenant_id,
            payload=payload,
            enqueued_at=enqueued_at,
            idempotency_key=idempotency_key,
        )

        # Add to stream (single "data" field)
        stream_key = self.stream_key(stream_group)
        await self.redis.xadd(stream_key, {"data": envelope.to_json()})

        logger.info(
            "job_enqueued",
            job_id=job_id,
            job_type=job_type,
            stream_group=stream_group,
            tenant_id=tenant_id,
        )

        return job_id

    async def read(
        self,
        stream_group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 5000,
    ) -> list[tuple[str, JobEnvelope]]:
        """Read jobs from consumer group using XREADGROUP.

        Args:
            stream_group: Stream group name
            consumer: Consumer identifier (e.g., pod name)
            count: Max messages to read (default 10)
            block_ms: Block timeout in milliseconds (default 5000)

        Returns:
            List of (msg_id, envelope) tuples
        """
        stream_key = self.stream_key(stream_group)
        try:
            result = await self.redis.xreadgroup(
                groupname=self.group,
                consumername=consumer,
                streams={stream_key: ">"},
                count=count,
                block=block_ms,
            )
        except aioredis.ResponseError as e:
            if "NOGROUP" in str(e):
                logger.warning(
                    "consumer_group_not_found_read",
                    stream_key=stream_key,
                    group=self.group,
                )
                return []
            raise

        if not result:
            return []

        messages: list[tuple[str, JobEnvelope]] = []
        for stream_key_name, msg_list in result:
            for msg_id, msg_data in msg_list:
                msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
                data_json = msg_data.get(b"data") or msg_data.get("data")
                if isinstance(data_json, bytes):
                    data_json = data_json.decode()
                envelope = JobEnvelope.from_json(data_json)
                messages.append((msg_id_str, envelope))

        logger.debug(
            "jobs_read",
            stream_group=stream_group,
            consumer=consumer,
            count=len(messages),
        )
        return messages

    async def ack(self, stream_group: str, msg_id: str) -> None:
        """Acknowledge a message (XACK).

        Args:
            stream_group: Stream group name
            msg_id: Message ID to acknowledge
        """
        stream_key = self.stream_key(stream_group)
        await self.redis.xack(stream_key, self.group, msg_id)
        logger.debug(
            "job_acked",
            msg_id=msg_id,
            stream_group=stream_group,
        )

    async def publish_result(
        self,
        stream_group: str,
        job_id: str,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Publish job result to results stream.

        Args:
            stream_group: Stream group name
            job_id: Job ID
            status: Result status (e.g., "success", "error")
            result: Result payload (optional)
        """
        results_key = self.results_key(stream_group)
        result_envelope = {
            "job_id": job_id,
            "status": status,
            "result": result or {},
        }
        await self.redis.xadd(results_key, {"data": json.dumps(result_envelope)})
        logger.info(
            "result_published",
            job_id=job_id,
            stream_group=stream_group,
            status=status,
        )

    async def is_duplicate(self, job_id: str) -> bool:
        """Check if a job has already been processed (idempotency check).

        Args:
            job_id: Job ID to check

        Returns:
            True if already processed, False otherwise
        """
        idempo_key = self.idempotency_key(job_id)
        exists = await self.redis.exists(idempo_key)
        return bool(exists)

    async def mark_processed(
        self, job_id: str, ttl: int = DEFAULT_IDEMPOTENCY_TTL
    ) -> None:
        """Mark a job as processed (idempotency marker).

        Uses SET key 1 NX EX ttl to atomically set with expiration.

        Args:
            job_id: Job ID to mark
            ttl: Time-to-live in seconds (default 86400 = 24h)
        """
        idempo_key = self.idempotency_key(job_id)
        await self.redis.setex(idempo_key, ttl, "1")
        logger.debug(
            "job_marked_processed",
            job_id=job_id,
            ttl=ttl,
        )

    async def reclaim_stale(
        self,
        stream_group: str,
        consumer: str,
        min_idle_ms: int = 60000,
        count: int = 10,
    ) -> list[ReclamedMessage]:
        """Reclaim stale pending messages using XAUTOCLAIM.

        Messages with delivery_count >= max_deliveries are routed to DLQ
        (not returned). Others are returned for reprocessing.

        Args:
            stream_group: Stream group name
            consumer: Consumer identifier (e.g., pod name)
            min_idle_ms: Min idle time in milliseconds (default 60000 = 60s)
            count: Max messages to reclaim (default 10)

        Returns:
            List of ReclamedMessage (delivery_count < max_deliveries)
        """
        stream_key = self.stream_key(stream_group)
        try:
            result = await self.redis.xautoclaim(
                stream_key,
                self.group,
                consumer,
                min_idle_ms,
                start_id="0-0",
                count=count,
            )
        except aioredis.ResponseError as e:
            if "NOGROUP" in str(e):
                logger.warning(
                    "consumer_group_not_found_reclaim",
                    stream_key=stream_key,
                    group=self.group,
                )
                return []
            raise

        if not result or len(result) < 2:
            return []

        reclaimed: list[ReclamedMessage] = []
        msg_list = result[1]  # Messages (result[0] is next cursor)

        for msg_id, msg_data in msg_list:
            msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else msg_id

            # Increment delivery count for this reclaim
            delivery_count = await self._increment_delivery_count(
                stream_group, msg_id_str
            )

            # If max deliveries reached, route to DLQ
            if delivery_count > self.max_deliveries:
                data_json = msg_data.get(b"data") or msg_data.get("data")
                if isinstance(data_json, bytes):
                    data_json = data_json.decode()
                envelope = JobEnvelope.from_json(data_json)

                await self.to_dead_letter(
                    stream_group,
                    msg_id_str,
                    envelope,
                    reason=f"max_deliveries_exceeded ({delivery_count})",
                )
                logger.info(
                    "job_dlq_routed",
                    msg_id=msg_id_str,
                    stream_group=stream_group,
                    delivery_count=delivery_count,
                )
            else:
                # Return for reprocessing
                data_json = msg_data.get(b"data") or msg_data.get("data")
                if isinstance(data_json, bytes):
                    data_json = data_json.decode()
                envelope = JobEnvelope.from_json(data_json)
                reclaimed.append(
                    ReclamedMessage(
                        msg_id=msg_id_str,
                        envelope=envelope,
                        delivery_count=delivery_count,
                    )
                )

        logger.debug(
            "stale_messages_reclaimed",
            stream_group=stream_group,
            consumer=consumer,
            reclaimed_count=len(reclaimed),
            dlq_count=len(msg_list) - len(reclaimed),
        )
        return reclaimed

    async def to_dead_letter(
        self, stream_group: str, msg_id: str, envelope: JobEnvelope, reason: str
    ) -> None:
        """Route a message to the dead-letter stream and ACK the original.

        Args:
            stream_group: Stream group name
            msg_id: Original message ID
            envelope: Job envelope
            reason: Reason for DLQ routing (logged)
        """
        dead_key = self.dead_key(stream_group)
        dead_envelope = {
            "job_id": envelope.job_id,
            "job_type": envelope.job_type,
            "tenant_id": envelope.tenant_id,
            "payload": envelope.payload,
            "enqueued_at": envelope.enqueued_at,
            "idempotency_key": envelope.idempotency_key,
            "dlq_reason": reason,
        }
        await self.redis.xadd(dead_key, {"data": json.dumps(dead_envelope)})

        # ACK the original message
        stream_key = self.stream_key(stream_group)
        await self.redis.xack(stream_key, self.group, msg_id)

        logger.warning(
            "job_dead_lettered",
            job_id=envelope.job_id,
            stream_group=stream_group,
            msg_id=msg_id,
            reason=reason,
        )

    async def _get_delivery_count(
        self, stream_group: str, msg_id: str
    ) -> int:
        """Get delivery count of a message via counter key.

        Uses a Redis counter key elder:jobs:delivery:{stream_group}:{msg_id}
        incremented each time a message is claimed/read.

        Args:
            stream_group: Stream group name
            msg_id: Message ID

        Returns:
            Delivery count (number of times claimed)
        """
        counter_key = f"elder:jobs:delivery:{stream_group}:{msg_id}"
        try:
            count = await self.redis.get(counter_key)
            return int(count) if count else 0
        except Exception as e:
            logger.warning(
                "delivery_count_lookup_failed",
                msg_id=msg_id,
                error=str(e),
            )
            return 0

    async def _increment_delivery_count(
        self, stream_group: str, msg_id: str
    ) -> int:
        """Increment the delivery count for a message.

        Args:
            stream_group: Stream group name
            msg_id: Message ID

        Returns:
            The new delivery count
        """
        counter_key = f"elder:jobs:delivery:{stream_group}:{msg_id}"
        # Set TTL for counter keys (24 hours, matching idempotency TTL)
        new_count = await self.redis.incr(counter_key)
        await self.redis.expire(counter_key, self.idempotency_ttl)
        return new_count
