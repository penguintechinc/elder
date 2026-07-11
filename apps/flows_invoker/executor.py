"""Flow-execution orchestration (penguin-dal, synchronous core).

``execute_promotion_pipeline`` is the testable heart of the invoker: given a
promotion, it records an ``iceflows_executions`` lifecycle row and walks the
target stage's configured tests and calls. Slice 1 only *plans* those steps
(records them in the structured execution log) — git operations, sandboxed test
execution, external calls, and Darwin review land in later slices. The function
is deliberately DB-agnostic (takes a penguin-dal ``db``) so it can be exercised
directly against a live test database without the Redis job bus.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _load_promotion(db, tenant_id: int, promotion_id: int):
    """Load a promotion, tenant-scoped. Returns the row or None."""
    return (
        db(
            (db.iceflows_promotions.id == promotion_id)
            & (db.iceflows_promotions.tenant_id == tenant_id)
        )
        .select()
        .first()
    )


def _load_stage(db, tenant_id: int, stage_id: Optional[int]):
    """Load a stage by primary key, tenant-scoped. Returns the row or None."""
    if stage_id is None:
        return None
    return (
        db(
            (db.iceflows_stages.id == stage_id)
            & (db.iceflows_stages.tenant_id == tenant_id)
        )
        .select()
        .first()
    )


def _plan_stage_steps(db, tenant_id: int, stage_id: int) -> Dict[str, Any]:
    """Collect the configured tests and calls for a stage (planning only)."""
    tests = db(
        (db.iceflows_stage_tests.stage_id == stage_id)
        & (db.iceflows_stage_tests.tenant_id == tenant_id)
    ).select(orderby=db.iceflows_stage_tests.execution_order)
    calls = db(
        (db.iceflows_stage_calls.stage_id == stage_id)
        & (db.iceflows_stage_calls.tenant_id == tenant_id)
    ).select(orderby=db.iceflows_stage_calls.execution_order)
    return {
        "tests": [
            {"test_id": t.test_id, "name": t.name, "test_type": t.test_type}
            for t in tests
        ],
        "calls": [
            {"call_id": c.call_id, "name": c.name, "call_type": c.call_type}
            for c in calls
        ],
    }


def execute_promotion_pipeline(
    db,
    tenant_id: int,
    promotion_id: int,
    started_by_identity_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Record and orchestrate a flow execution for a promotion.

    Creates an ``iceflows_executions`` row (in_progress → success/failed),
    resolves the target stage's planned steps, and writes a structured log.
    Returns ``{"execution_id", "status", ...}``. Never raises for expected
    business conditions (missing promotion/stage) — those resolve to a failed
    execution record so the job bus can ack and surface the result.
    """
    now = datetime.now(timezone.utc)
    log: list[Dict[str, Any]] = []

    promotion = _load_promotion(db, tenant_id, promotion_id)
    if not promotion:
        logger.warning(
            "flow_execution_promotion_missing",
            extra={"tenant_id": tenant_id, "promotion_id": promotion_id},
        )
        return {
            "execution_id": None,
            "status": "failed",
            "error": "promotion not found",
        }

    # Open the execution record (in_progress).
    execution_id = str(uuid.uuid4())
    exec_db_id = db.iceflows_executions.insert(
        tenant_id=tenant_id,
        execution_id=execution_id,
        promotion_id=promotion.id,
        flow_id=promotion.flow_id,
        status="in_progress",
        started_by_identity_id=started_by_identity_id,
        started_at=now,
        execution_log=[],
        created_at=now,
        updated_at=now,
    )
    db.commit()

    status = "success"
    error_message = None
    try:
        stage = _load_stage(db, tenant_id, promotion.target_stage_id)
        if not stage:
            raise ValueError("target stage not found")

        plan = _plan_stage_steps(db, tenant_id, stage.id)
        log.append(
            {
                "step": "plan",
                "stage_id": stage.id,
                "branch_name": stage.branch_name,
                "test_count": len(plan["tests"]),
                "call_count": len(plan["calls"]),
                "note": "slice 1 skeleton — steps recorded, not executed",
            }
        )
        # Slice 2 executes git ops + tests; slice 3 executes calls + review.
        # Promotion status is intentionally NOT advanced here — merge only
        # happens once real tests pass (later slice).
    except Exception as e:  # noqa: BLE001 - record as failed execution
        status = "failed"
        error_message = str(e)
        log.append({"step": "error", "error": error_message})
        logger.error(
            "flow_execution_failed",
            extra={"execution_id": execution_id, "error": error_message},
        )

    completed = datetime.now(timezone.utc)
    duration = int((completed - now).total_seconds())
    db(db.iceflows_executions.id == exec_db_id).update(
        status=status,
        completed_at=completed,
        duration_seconds=duration,
        error_message=error_message,
        execution_log=log,
        updated_at=completed,
    )
    db.commit()

    logger.info(
        "flow_execution_recorded",
        extra={
            "execution_id": execution_id,
            "promotion_id": promotion.id,
            "status": status,
        },
    )
    return {
        "execution_id": execution_id,
        "status": status,
        "promotion_id": promotion.id,
        "error": error_message,
    }
