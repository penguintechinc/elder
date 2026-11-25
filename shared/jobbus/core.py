"""JobBus: Redis Streams job queue with idempotency, DLQ, and XAUTOCLAIM support.

Design:
- Streams: elder:jobs:<group>, elder:results:<group>, elder:jobs:<group>:dead
- Consumer group: "workers" (constant)
- Envelope: JSON in single "data" field with job_id, job_type, tenant_id, payload, enqueued_at, idempotency_key
- Idempotency: elder:jobs:processed:{job_id} via SET NX EX
- DLQ: after MAX_DELIVERIES (default 5), move to dead stream and XACK
- Bounded memory: every stream has a cap (env-configurable, see ``_env_int``).
  Result and dead-letter streams have no consumer group, so they are trimmed
  with ``XADD ... MAXLEN ~ cap``. Job streams are trimmed with
  ``XTRIM MINID <oldest id any consumer group still needs>``, which can only
  remove entries every group has already delivered AND acknowledged; when the
  un-acked backlog itself reaches the cap, ``enqueue`` refuses new work
  (``JobBusBackpressureError``) instead of silently dropping queued jobs.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
import structlog
from opentelemetry import context as otel_context
from opentelemetry import metrics as otel_metrics
from opentelemetry.propagate import extract as otel_extract
from opentelemetry.propagate import inject as otel_inject
from redis.asyncio import Redis as AsyncRedis

logger = structlog.get_logger(__name__)

# Consumer group name (constant)
DEFAULT_GROUP = "workers"

# Default max deliveries before DLQ
DEFAULT_MAX_DELIVERIES = 5

# Idempotency TTL in seconds (default 24 hours)
DEFAULT_IDEMPOTENCY_TTL = 86400

# Stream caps (entries). Job streams are sized to the worst tolerated backlog;
# results/dead-letter streams are diagnostic history and may be smaller.
DEFAULT_JOB_STREAM_MAXLEN = 50_000
DEFAULT_RESULTS_STREAM_MAXLEN = 10_000
DEFAULT_DEAD_STREAM_MAXLEN = 10_000

# Fraction of the cap at which the backlog is reported as high (alert signal)
# and an acked-entry trim is attempted before admitting more work.
BACKLOG_HIGH_WATERMARK = 0.8


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer from the environment without ever raising.

    A missing, blank, non-numeric or below-minimum value falls back to
    ``default`` (with a warning) so a typo in a deployment value can never
    crash a service at import or enqueue time.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        logger.warning("jobbus_invalid_env_int", name=name, default=default)
        return default
    if value < minimum:
        logger.warning("jobbus_env_int_below_minimum", name=name, default=default)
        return default
    return value


def _env_suffix(name: str) -> str:
    """Normalise a stream group name into an env-var suffix (``A-Z0-9_``)."""
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def _text(value: Any) -> str:
    """Decode a Redis reply element to ``str`` (clients may return bytes)."""
    return value.decode() if isinstance(value, bytes) else str(value)


def _id_tuple(stream_id: Any) -> tuple[int, int]:
    """Parse a stream entry id (``<ms>-<seq>``) into a sortable tuple."""
    ms, _, seq = _text(stream_id).partition("-")
    return int(ms), int(seq or 0)


@dataclass(slots=True)
class StreamStats:
    """Point-in-time health of one job stream (feeds the OTel gauges)."""

    length: int = 0
    pending: int = 0
    oldest_pending_age_seconds: float = 0.0


class JobBusBackpressureError(RuntimeError):
    """Raised by ``enqueue`` when a job stream's un-acked backlog is at its cap.

    Refusing new work is deliberate: the only alternative that keeps Redis
    memory bounded is trimming entries no worker has processed yet.
    """


# Latest stats per stream key; label cardinality is bounded by the fixed set
# of stream groups (never job ids, tenants or consumers).
_STREAM_STATS: dict[str, StreamStats] = {}

_meter = otel_metrics.get_meter("elder")


def _observe(attr: str) -> Any:
    """Build an observable-gauge callback reporting one ``StreamStats`` field."""

    def callback(
        _options: otel_metrics.CallbackOptions,
    ) -> Iterable[otel_metrics.Observation]:
        return [
            otel_metrics.Observation(getattr(stats, attr), {"stream": stream})
            for stream, stats in list(_STREAM_STATS.items())
        ]

    return callback


_meter.create_observable_gauge(
    "elder.jobbus.stream.length",
    callbacks=[_observe("length")],
    description="Entries currently held in a job-bus stream",
)
_meter.create_observable_gauge(
    "elder.jobbus.stream.pending",
    callbacks=[_observe("pending")],
    description="Delivered-but-unacknowledged entries in a job-bus stream",
)
_meter.create_observable_gauge(
    "elder.jobbus.stream.oldest_pending_age",
    callbacks=[_observe("oldest_pending_age_seconds")],
    unit="s",
    description="Age of the oldest unacknowledged entry in a job-bus stream",
)
_trimmed_counter = _meter.create_counter(
    "elder.jobbus.stream.trimmed",
    description="Acknowledged entries removed from a job-bus stream",
)
_dead_letter_counter = _meter.create_counter(
    "elder.jobbus.stream.dead_lettered",
    description="Jobs routed to the dead-letter stream",
)
_backlog_high_counter = _meter.create_counter(
    "elder.jobbus.stream.backlog_high",
    description="Enqueues that found the backlog above the high watermark",
)
_rejected_counter = _meter.create_counter(
    "elder.jobbus.stream.rejected",
    description="Enqueues refused because the un-acked backlog hit the cap",
)


@dataclass(slots=True)
class JobEnvelope:
    """Job message envelope (JSON-serializable).

    `trace_context` carries the OTel propagation carrier (e.g. W3C
    `traceparent`/`tracestate`) captured at enqueue time, so a worker/scanner
    consuming this job can extract it and continue the same distributed
    trace that started in the API request that enqueued it -- rather than
    every job showing up as a disconnected, parentless span.
    """

    job_id: str
    job_type: str
    payload: dict[str, Any]
    enqueued_at: str
    tenant_id: int | None = None
    idempotency_key: str | None = None
    trace_context: dict[str, str] = field(default_factory=dict)

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
                "trace_context": self.trace_context,
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
            trace_context=parsed.get("trace_context") or {},
        )

    def extract_trace_context(self) -> otel_context.Context:
        """Extract the propagated OTel context carried by this envelope.

        Returns the current context unchanged if `trace_context` is empty
        (e.g. jobs enqueued before this field existed, or enqueued with no
        active span) -- callers can pass the result straight to
        `tracer.start_as_current_span(..., context=...)` either way.
        """
        return otel_extract(self.trace_context)


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

    def stream_cap(self, stream_group: str) -> int:
        """Return the entry cap for a job stream.

        ``ELDER_JOBBUS_MAXLEN_<GROUP>`` overrides ``ELDER_JOBBUS_MAXLEN``
        (default 50k) so a busy group can be sized to its own backlog.
        """
        fallback = _env_int("ELDER_JOBBUS_MAXLEN", DEFAULT_JOB_STREAM_MAXLEN)
        return _env_int(f"ELDER_JOBBUS_MAXLEN_{_env_suffix(stream_group)}", fallback)

    def results_cap(self) -> int:
        """Return the entry cap for results streams (no consumer group)."""
        return _env_int("ELDER_JOBBUS_RESULTS_MAXLEN", DEFAULT_RESULTS_STREAM_MAXLEN)

    def dead_cap(self) -> int:
        """Return the entry cap for dead-letter streams (no consumer group)."""
        return _env_int("ELDER_JOBBUS_DEAD_MAXLEN", DEFAULT_DEAD_STREAM_MAXLEN)

    async def trim_acked(self, stream_group: str) -> int:
        """Remove entries that every consumer group has delivered and acked.

        The trim floor is the smallest id still needed by any group: its
        oldest pending (un-acked) id, or its last-delivered id when nothing
        is pending. ``XTRIM MINID`` only deletes ids strictly below that
        floor, so un-acked and not-yet-delivered entries can never be
        dropped. A stream with no consumer group is left untouched (every
        entry is still undelivered).

        Returns:
            Number of entries removed.
        """
        key = self.stream_key(stream_group)
        try:
            groups = await self.redis.xinfo_groups(key)
        except aioredis.ResponseError:
            return 0  # stream does not exist yet
        if not groups:
            return 0

        floor: tuple[int, int] | None = None
        for info in groups:
            name = _text(info.get("name", info.get(b"name", "")))
            candidate = info.get("last-delivered-id", info.get(b"last-delivered-id"))
            if int(info.get("pending", info.get(b"pending", 0)) or 0) > 0:
                summary = await self.redis.xpending(key, name)
                candidate = summary.get("min") or candidate
            if candidate is None:
                return 0
            parsed = _id_tuple(candidate)
            floor = parsed if floor is None else min(floor, parsed)

        if floor is None or floor == (0, 0):
            return 0

        removed = int(
            await self.redis.xtrim(
                key, minid=f"{floor[0]}-{floor[1]}", approximate=False
            )
        )
        if removed:
            _trimmed_counter.add(removed, {"stream": key})
            logger.debug("jobbus_stream_trimmed", stream_key=key, removed=removed)
        return removed

    async def stream_stats(self, stream_group: str) -> StreamStats:
        """Measure length, pending count and oldest-pending age for a stream.

        Also publishes the values to the ``elder.jobbus.stream.*`` gauges so
        a growing backlog or a stuck consumer is visible before the cap bites.
        """
        key = self.stream_key(stream_group)
        stats = StreamStats(length=int(await self.redis.xlen(key)))
        try:
            summary = await self.redis.xpending(key, self.group)
        except aioredis.ResponseError:
            summary = None  # no consumer group yet
        if summary and summary.get("pending"):
            stats.pending = int(summary["pending"])
            oldest_ms = _id_tuple(summary["min"])[0]
            stats.oldest_pending_age_seconds = max(0.0, time.time() - oldest_ms / 1000)
        _STREAM_STATS[key] = stats
        return stats

    async def _admit(self, stream_group: str, stream_key: str, cap: int) -> None:
        """Enforce the job-stream cap before an enqueue.

        Above the high watermark, acked entries are trimmed first; if the
        remaining (genuinely un-acked) backlog is still high it is reported,
        and at the cap the enqueue is refused rather than trimming live jobs.
        """
        high = max(1, int(cap * BACKLOG_HIGH_WATERMARK))
        length = int(await self.redis.xlen(stream_key))
        if length < high:
            return
        length -= await self.trim_acked(stream_group)
        if length < high:
            return
        _backlog_high_counter.add(1, {"stream": stream_key})
        logger.warning(
            "jobbus_backlog_high", stream_key=stream_key, length=length, cap=cap
        )
        if length >= cap:
            _rejected_counter.add(1, {"stream": stream_key})
            logger.error(
                "jobbus_enqueue_rejected", stream_key=stream_key, length=length, cap=cap
            )
            raise JobBusBackpressureError(
                f"job stream {stream_key} backlog {length} reached cap {cap}"
            )

    async def ensure_group(self, stream_group: str) -> None:
        """Ensure consumer group exists (create if not, ignore if exists).

        Uses XGROUP CREATE with MKSTREAM flag (creates stream if absent).
        Ignores BUSYGROUP error (group already exists).

        Args:
            stream_group: Stream group name (e.g., "infrastructure", "helpdesk")
        """
        key = self.stream_key(stream_group)
        try:
            await self.redis.xgroup_create(key, self.group, id="0", mkstream=True)
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

        # Capture the caller's current OTel context (e.g. the in-flight
        # HTTP request span) so the consumer can continue the same
        # distributed trace across the API -> job-bus -> worker boundary.
        # No-ops to an empty dict if there's no active span.
        trace_carrier: dict[str, str] = {}
        otel_inject(trace_carrier)

        # Build envelope
        envelope = JobEnvelope(
            job_id=job_id,
            job_type=job_type,
            tenant_id=tenant_id,
            payload=payload,
            enqueued_at=enqueued_at,
            idempotency_key=idempotency_key,
            trace_context=trace_carrier,
        )

        # Add to stream (single "data" field). _admit keeps the stream at or
        # below its cap without dropping un-acked work; the MAXLEN guard is a
        # last-resort memory ceiling set above the cap so it only engages if
        # concurrent enqueuers overshoot the admission check.
        stream_key = self.stream_key(stream_group)
        cap = self.stream_cap(stream_group)
        await self._admit(stream_group, stream_key, cap)
        await self.redis.xadd(
            stream_key,
            {"data": envelope.to_json()},
            maxlen=cap + max(cap // 10, 1000),
            approximate=True,
        )

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
        # Results have no consumer group, so approximate MAXLEN is loss-safe.
        await self.redis.xadd(
            results_key,
            {"data": json.dumps(result_envelope)},
            maxlen=self.results_cap(),
            approximate=True,
        )
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

        # The sweeper calls this periodically per group: piggy-back the
        # acked-entry trim and gauge refresh. Housekeeping must never fail
        # the reclaim itself.
        try:
            await self.trim_acked(stream_group)
            await self.stream_stats(stream_group)
        except Exception as e:
            logger.warning(
                "jobbus_housekeeping_failed", stream_key=stream_key, error=str(e)
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
        # XADD to the dead stream and XACK the original in one MULTI/EXEC so
        # a crash between the two cannot dead-letter the same job twice.
        stream_key = self.stream_key(stream_group)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.xadd(
                dead_key,
                {"data": json.dumps(dead_envelope)},
                maxlen=self.dead_cap(),
                approximate=True,
            )
            pipe.xack(stream_key, self.group, msg_id)
            await pipe.execute()
        _dead_letter_counter.add(1, {"stream": stream_key})

        logger.warning(
            "job_dead_lettered",
            job_id=envelope.job_id,
            stream_group=stream_group,
            msg_id=msg_id,
            reason=reason,
        )

    async def _get_delivery_count(self, stream_group: str, msg_id: str) -> int:
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

    async def _increment_delivery_count(self, stream_group: str, msg_id: str) -> int:
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
