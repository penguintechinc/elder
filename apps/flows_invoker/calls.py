"""Stage-call dispatch for the Flows invoker (slice 3).

Executes a stage's configured external calls (``iceflows_stage_calls``):

- ``icestreams`` — triggers a Streams playbook: inserts a ``stream_executions``
  row (mirroring the Streams execute endpoint) and enqueues an
  ``execute_playbook`` job on the ``streams`` job-bus group. Blocking calls
  poll the execution row until a terminal status or the configured timeout.
- ``iceruns`` — the Runs (FaaS) module has not been ported to Elder yet;
  dispatch resolves to unavailable (a blocking call therefore fails the
  pipeline rather than silently passing).

Dispatch runs inside the invoker's sync executor thread; the async job-bus
enqueue is bridged with a short-lived event loop + Redis connection per call
(call volume is low — one enqueue per configured stage call).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_TERMINAL_PASS = frozenset({"completed", "success"})
_TERMINAL_FAIL = frozenset({"failed", "cancelled", "error", "timeout"})

_POLL_INTERVAL_SECONDS = 2.0


@dataclass(slots=True)
class CallOutcome:
    """Result of dispatching one stage call."""

    dispatched: bool
    passed: bool
    execution_id: Optional[str] = None
    final_status: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 1
    detail: Dict[str, Any] = field(default_factory=dict)


def _enqueue_streams_job(payload: Dict[str, Any], tenant_id: int) -> None:
    """Enqueue an execute_playbook job (fresh loop/connection, then closed)."""
    from apps.worker.config.settings import settings

    if not settings.redis_url:
        raise RuntimeError("REDIS_URL not configured")

    async def _enqueue() -> None:
        import redis.asyncio

        from shared.jobbus import JobBus

        redis_client = redis.asyncio.from_url(settings.redis_url)
        try:
            jobbus = JobBus(redis_client)
            await jobbus.ensure_group("streams")
            await jobbus.enqueue(
                "streams",
                "execute_playbook",
                payload,
                enqueued_at=datetime.now(timezone.utc).isoformat(),
                tenant_id=tenant_id,
                idempotency_key=payload.get("execution_id"),
            )
        finally:
            await redis_client.aclose()

    asyncio.run(_enqueue())


def _dispatch_icestreams(
    db,
    tenant_id: int,
    call,
    context: Dict[str, Any],
) -> CallOutcome:
    """Create a stream_executions row and enqueue the playbook job."""
    try:
        playbook_id = int(call.target_id)
    except (TypeError, ValueError):
        return CallOutcome(
            dispatched=False,
            passed=False,
            error=f"invalid target_id {call.target_id!r}",
        )

    playbook = (
        db(
            (db.stream_playbooks.id == playbook_id)
            & (db.stream_playbooks.tenant_id == tenant_id)
        )
        .select()
        .first()
    )
    if not playbook:
        return CallOutcome(
            dispatched=False, passed=False, error="target playbook not found"
        )

    execution_uuid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    input_json = dict(call.input_template or {})
    input_json["_flows"] = context

    db.stream_executions.insert(
        tenant_id=tenant_id,
        playbook_id=playbook_id,
        execution_id=execution_uuid,
        status="pending",
        trigger_type="manual",
        triggered_by_identity_id=context.get("started_by_identity_id"),
        input_json=input_json,
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.commit()

    try:
        _enqueue_streams_job(
            {
                "execution_id": execution_uuid,
                "playbook_id": playbook_id,
                "tenant_id": tenant_id,
            },
            tenant_id,
        )
    except Exception as e:  # noqa: BLE001 - surface as a failed dispatch
        db(db.stream_executions.execution_id == execution_uuid).update(
            status="failed",
            error_message=f"flows call enqueue failed: {e}",
            updated_at=datetime.now(timezone.utc),
        )
        db.commit()
        return CallOutcome(
            dispatched=False,
            passed=False,
            execution_id=execution_uuid,
            error=f"enqueue failed: {e}",
        )

    return CallOutcome(dispatched=True, passed=True, execution_id=execution_uuid)


def _wait_for_streams_result(
    db, execution_uuid: str, timeout_seconds: int
) -> CallOutcome:
    """Poll the stream_executions row until terminal status or timeout."""
    deadline = time.monotonic() + max(1, timeout_seconds)
    final_status: Optional[str] = None
    while time.monotonic() < deadline:
        row = db(db.stream_executions.execution_id == execution_uuid).select().first()
        final_status = row.status if row else None
        if final_status in _TERMINAL_PASS:
            return CallOutcome(
                dispatched=True,
                passed=True,
                execution_id=execution_uuid,
                final_status=final_status,
            )
        if final_status in _TERMINAL_FAIL:
            return CallOutcome(
                dispatched=True,
                passed=False,
                execution_id=execution_uuid,
                final_status=final_status,
                error=f"playbook execution {final_status}",
            )
        time.sleep(_POLL_INTERVAL_SECONDS)
    return CallOutcome(
        dispatched=True,
        passed=False,
        execution_id=execution_uuid,
        final_status=final_status,
        error=f"timed out after {timeout_seconds}s waiting for playbook",
    )


def dispatch_stage_call(
    db,
    tenant_id: int,
    call,
    context: Dict[str, Any],
) -> CallOutcome:
    """Dispatch one configured stage call, honoring blocking + retry policy."""
    call_type = (call.call_type or "").lower()
    attempts_allowed = 1 + max(0, int(call.retry_count or 0))

    if call_type == "iceruns":
        return CallOutcome(
            dispatched=False,
            passed=False,
            error="runs (FaaS) module is not available in this deployment",
        )
    if call_type != "icestreams":
        return CallOutcome(
            dispatched=False,
            passed=False,
            error=f"unknown call_type {call.call_type!r}",
        )

    last: CallOutcome = CallOutcome(dispatched=False, passed=False)
    for attempt in range(1, attempts_allowed + 1):
        outcome = _dispatch_icestreams(db, tenant_id, call, context)
        if outcome.dispatched and call.is_blocking:
            outcome = _wait_for_streams_result(
                db, outcome.execution_id or "", int(call.timeout_seconds or 300)
            )
        outcome.attempts = attempt
        last = outcome
        if outcome.passed:
            return outcome
        logger.warning(
            "flows_stage_call_attempt_failed",
            extra={
                "call_id": call.call_id,
                "attempt": attempt,
                "error": outcome.error,
            },
        )
    return last
