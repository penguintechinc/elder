"""Flow-execution orchestration (penguin-dal, synchronous core).

``execute_promotion_pipeline`` is the testable heart of the invoker: given a
promotion, it records an ``iceflows_executions`` lifecycle row and runs the
full pipeline for the promotion's target stage:

1. **Plan** — resolve the stage's configured tests/calls (slice 1).
2. **Git** — clone the flow's repository and merge the promotion's source ref
   into the target stage branch, locally (slice 2, :mod:`gitops`).
3. **Tests** — run each configured stage test through the hardened sandbox
   (slice 2, :mod:`sandbox`); required-test failure fails the execution.
4. **Review** — Darwin AI review gate; required reviews fail closed when the
   reviewer is unavailable (slice 3, :mod:`review`).
5. **Calls (pre-merge)** — dispatch ``trigger_on='pre_merge'`` stage calls;
   a failed blocking call stops the pipeline (slice 3, :mod:`calls`).
6. **Merge** — push the merge and advance the promotion to ``merged`` — only
   when the stage's approval policy is satisfied (``require_approval`` ⇒
   promotion must be ``approved``); otherwise the push is skipped and
   recorded, leaving the promotion untouched.
7. **Calls (post-merge)** — dispatch ``post_merge``/``on_promotion`` calls.

The function is deliberately DB-agnostic (takes a penguin-dal ``db``) so it
can be exercised directly against a live test database without the Redis job
bus. Credential tokens are read tenant-scoped at execution time, passed only
to git subprocesses (see :mod:`gitops`), and scrubbed from all captured
output before it reaches the execution log.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from apps.flows_invoker import gitops
from apps.flows_invoker.calls import dispatch_stage_call
from apps.flows_invoker.review import run_darwin_review
from apps.flows_invoker.sandbox import (
    SandboxError,
    build_env,
    parse_command,
    run_command,
)

logger = logging.getLogger(__name__)

# Bytes of a single test's output retained in the JSON execution_log (the
# sandbox already caps process output; this bounds DB row growth).
_LOG_OUTPUT_BYTES = 8 * 1024


class PipelineAbort(Exception):
    """Internal control-flow: fail the execution with a recorded reason."""


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


def _load_flow(db, tenant_id: int, flow_id: Optional[int]):
    """Load a flow by primary key, tenant-scoped. Returns the row or None."""
    if flow_id is None:
        return None
    return (
        db((db.iceflows.id == flow_id) & (db.iceflows.tenant_id == tenant_id))
        .select()
        .first()
    )


def _load_credential_token(
    db, tenant_id: int, credential_id: Optional[int]
) -> Optional[str]:
    """Resolve the flow's git credential token (tenant-scoped, active only)."""
    if not credential_id:
        return None
    cred = (
        db(
            (db.iceflows_credentials.id == credential_id)
            & (db.iceflows_credentials.tenant_id == tenant_id)
        )
        .select()
        .first()
    )
    if not cred or not cred.is_active or not cred.access_token:
        return None
    db(db.iceflows_credentials.id == cred.id).update(
        last_used_at=datetime.now(timezone.utc)
    )
    db.commit()
    return cred.access_token


def _stage_tests(db, tenant_id: int, stage_id: int):
    return db(
        (db.iceflows_stage_tests.stage_id == stage_id)
        & (db.iceflows_stage_tests.tenant_id == tenant_id)
    ).select(orderby=db.iceflows_stage_tests.execution_order)


def _stage_calls(db, tenant_id: int, stage_id: int):
    return db(
        (db.iceflows_stage_calls.stage_id == stage_id)
        & (db.iceflows_stage_calls.tenant_id == tenant_id)
    ).select(orderby=db.iceflows_stage_calls.execution_order)


def _stage_review(db, tenant_id: int, stage_id: int):
    return (
        db(
            (db.iceflows_stage_reviews.stage_id == stage_id)
            & (db.iceflows_stage_reviews.tenant_id == tenant_id)
        )
        .select()
        .first()
    )


def _plan_stage_steps(db, tenant_id: int, stage_id: int) -> Dict[str, Any]:
    """Collect the configured tests and calls for a stage (planning step)."""
    tests = _stage_tests(db, tenant_id, stage_id)
    stage_calls = _stage_calls(db, tenant_id, stage_id)
    return {
        "tests": [
            {"test_id": t.test_id, "name": t.name, "test_type": t.test_type}
            for t in tests
        ],
        "calls": [
            {"call_id": c.call_id, "name": c.name, "call_type": c.call_type}
            for c in stage_calls
        ],
    }


def _tail(text: str, limit: int = _LOG_OUTPUT_BYTES) -> str:
    """Keep the tail of command output for the execution log (failures last)."""
    if not text:
        return ""
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return text
    return "...[truncated]...\n" + raw[-limit:].decode("utf-8", "replace")


def _resolve_test_cwd(repo_dir: str, test) -> str:
    """Resolve the working directory for a test, contained within the repo."""
    if test.path_mode == "centralized":
        raise PipelineAbort(
            f"test {test.name!r}: centralized path mode is not supported"
        )
    rel = (test.repo_relative_path or "").strip().lstrip("/")
    if not rel:
        return repo_dir
    repo_real = os.path.realpath(repo_dir)
    target = os.path.realpath(os.path.join(repo_real, rel))
    if target != repo_real and not target.startswith(repo_real + os.sep):
        raise PipelineAbort(
            f"test {test.name!r}: repo_relative_path escapes the repository"
        )
    if not os.path.isdir(target):
        raise PipelineAbort(
            f"test {test.name!r}: repo_relative_path does not exist in the repo"
        )
    return target


def _run_stage_tests(
    db,
    tenant_id: int,
    stage_id: int,
    repo_dir: str,
    token: Optional[str],
    log: List[Dict[str, Any]],
) -> bool:
    """Run each configured test through the sandbox. Returns overall pass."""
    all_required_passed = True
    for test in _stage_tests(db, tenant_id, stage_id):
        entry: Dict[str, Any] = {
            "step": "test",
            "test_id": test.test_id,
            "name": test.name,
            "test_type": test.test_type,
        }
        try:
            if not (test.command or "").strip():
                raise SandboxError("no command configured")
            cwd = _resolve_test_cwd(repo_dir, test)
            env_extra = {
                k: v
                for k, v in (test.env_vars or {}).items()
                if isinstance(k, str) and isinstance(v, str)
            }
            result = run_command(
                parse_command(test.command),
                cwd,
                timeout=int(test.timeout_seconds or 600),
                env=build_env(cwd, env_extra),
            )
            entry.update(
                {
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                    "duration_seconds": result.duration_seconds,
                    "output": gitops.scrub(_tail(result.stdout), token),
                    "passed": result.success,
                }
            )
            passed = result.success
        except (SandboxError, PipelineAbort) as e:
            entry.update({"passed": False, "error": str(e)})
            passed = False
        log.append(entry)

        if not passed:
            if test.is_blocking:
                # A blocking failure stops the pipeline immediately —
                # remaining tests never ran, so the run cannot pass.
                log.append(
                    {
                        "step": "tests_aborted",
                        "reason": f"blocking test {test.name!r} failed",
                    }
                )
                return False
            if test.is_required:
                all_required_passed = False
    return all_required_passed


def _run_review_gate(
    db,
    tenant_id: int,
    stage_id: int,
    ws: gitops.GitWorkspace,
    log: List[Dict[str, Any]],
) -> None:
    """Apply the stage's Darwin review policy (fail-closed when required)."""
    config = _stage_review(db, tenant_id, stage_id)
    if not config:
        return
    if not config.is_required:
        # Optional review: attempt it for the record, never block.
        outcome = run_darwin_review(
            gitops.merge_diff(ws),
            review_type=config.review_type or "standard",
            min_score=int(config.min_score or 70),
            block_on_critical=bool(config.block_on_critical),
        )
        log.append(
            {
                "step": "review",
                "required": False,
                "available": outcome.available,
                "passed": outcome.passed if outcome.available else None,
                "score": outcome.score,
                "issue_count": len(outcome.issues),
                "note": (
                    None
                    if outcome.available
                    else "reviewer unavailable — skipped (optional)"
                ),
            }
        )
        return

    outcome = run_darwin_review(
        gitops.merge_diff(ws),
        review_type=config.review_type or "standard",
        min_score=int(config.min_score or 70),
        block_on_critical=bool(config.block_on_critical),
    )
    log.append(
        {
            "step": "review",
            "required": True,
            "available": outcome.available,
            "passed": outcome.passed,
            "score": outcome.score,
            "issue_count": len(outcome.issues),
            "error": outcome.error,
        }
    )
    if not outcome.available:
        # FAIL CLOSED: a required review gate must never be skipped.
        raise PipelineAbort(
            f"required Darwin review unavailable: {outcome.error or 'not configured'}"
        )
    if not outcome.passed:
        raise PipelineAbort(
            f"Darwin review did not pass (score={outcome.score}, "
            f"min={config.min_score}, issues={len(outcome.issues)})"
        )


def _run_calls(
    db,
    tenant_id: int,
    stage_id: int,
    triggers: frozenset,
    context: Dict[str, Any],
    log: List[Dict[str, Any]],
) -> None:
    """Dispatch the stage calls matching ``triggers`` (order-preserving)."""
    for call in _stage_calls(db, tenant_id, stage_id):
        trigger = (call.trigger_on or "on_promotion").lower()
        if trigger not in triggers:
            continue
        outcome = dispatch_stage_call(db, tenant_id, call, context)
        log.append(
            {
                "step": "call",
                "call_id": call.call_id,
                "name": call.name,
                "call_type": call.call_type,
                "trigger_on": trigger,
                "dispatched": outcome.dispatched,
                "passed": outcome.passed,
                "target_execution_id": outcome.execution_id,
                "final_status": outcome.final_status,
                "attempts": outcome.attempts,
                "error": outcome.error,
            }
        )
        if not outcome.passed and call.is_blocking:
            raise PipelineAbort(
                f"blocking stage call {call.name!r} failed: {outcome.error}"
            )


def _open_execution_row(
    db,
    tenant_id: int,
    promotion,
    started_by_identity_id: Optional[int],
    execution_id: Optional[str],
    now: datetime,
):
    """Adopt the API's pre-created execution row, or create one."""
    if execution_id:
        row = (
            db(
                (db.iceflows_executions.execution_id == execution_id)
                & (db.iceflows_executions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if row:
            db(db.iceflows_executions.id == row.id).update(
                status="in_progress", started_at=now, updated_at=now
            )
            db.commit()
            return row.id, execution_id
    execution_id = execution_id or str(uuid.uuid4())
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
    return exec_db_id, execution_id


def execute_promotion_pipeline(
    db,
    tenant_id: int,
    promotion_id: int,
    started_by_identity_id: Optional[int] = None,
    execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the full CI/CD pipeline for a promotion (see module docstring).

    Returns ``{"execution_id", "status", ...}``. Never raises for expected
    business conditions — those resolve to a failed execution record so the
    job bus can ack and surface the result.
    """
    now = datetime.now(timezone.utc)
    log: List[Dict[str, Any]] = []

    promotion = _load_promotion(db, tenant_id, promotion_id)
    if not promotion:
        logger.warning(
            "flow_execution_promotion_missing",
            extra={"tenant_id": tenant_id, "promotion_id": promotion_id},
        )
        if execution_id:
            db(
                (db.iceflows_executions.execution_id == execution_id)
                & (db.iceflows_executions.tenant_id == tenant_id)
            ).update(
                status="failed",
                error_message="promotion not found",
                completed_at=now,
                updated_at=now,
            )
            db.commit()
        return {
            "execution_id": execution_id,
            "status": "failed",
            "error": "promotion not found",
        }

    exec_db_id, execution_id = _open_execution_row(
        db, tenant_id, promotion, started_by_identity_id, execution_id, now
    )

    status = "success"
    error_message: Optional[str] = None
    merged = False
    ws: Optional[gitops.GitWorkspace] = None
    token: Optional[str] = None
    try:
        target = _load_stage(db, tenant_id, promotion.target_stage_id)
        if not target:
            raise PipelineAbort("target stage not found")
        source = _load_stage(db, tenant_id, promotion.source_stage_id)
        if not source:
            raise PipelineAbort("source stage not found")
        flow = _load_flow(db, tenant_id, promotion.flow_id)
        if not flow:
            raise PipelineAbort("flow not found")

        plan = _plan_stage_steps(db, tenant_id, target.id)
        log.append(
            {
                "step": "plan",
                "stage_id": target.id,
                "branch_name": target.branch_name,
                "test_count": len(plan["tests"]),
                "call_count": len(plan["calls"]),
            }
        )

        # Merge is only permitted once the stage's approval policy is met.
        merge_allowed = (not target.require_approval) or promotion.status == "approved"
        has_review = _stage_review(db, tenant_id, target.id) is not None
        needs_git = bool(plan["tests"]) or has_review or merge_allowed

        call_context = {
            "flow_id": promotion.flow_id,
            "promotion_id": promotion.id,
            "execution_id": execution_id,
            "target_stage_id": target.id,
            "started_by_identity_id": started_by_identity_id,
        }

        if needs_git:
            token = _load_credential_token(db, tenant_id, flow.credential_id)
            ws = gitops.create_workspace(token)
            merge_sha = gitops.clone_and_merge(
                ws,
                flow.repository_url,
                source.branch_name,
                target.branch_name,
                source_commit=promotion.source_commit,
            )
            log.append(
                {
                    "step": "git_merge_local",
                    "source_branch": source.branch_name,
                    "target_branch": target.branch_name,
                    "source_commit": promotion.source_commit,
                    "merge_sha": merge_sha,
                }
            )

            if not _run_stage_tests(db, tenant_id, target.id, ws.repo_dir, token, log):
                raise PipelineAbort("required stage tests failed")

            _run_review_gate(db, tenant_id, target.id, ws, log)

        _run_calls(
            db, tenant_id, target.id, frozenset({"pre_merge"}), call_context, log
        )

        if merge_allowed and ws is not None:
            gitops.push_target(ws, target.branch_name)
            completed = datetime.now(timezone.utc)
            db(db.iceflows_promotions.id == promotion.id).update(
                status="merged",
                merged_by_identity_id=started_by_identity_id,
                merged_at=completed,
                updated_at=completed,
            )
            db.commit()
            merged = True
            log.append({"step": "git_push", "target_branch": target.branch_name})
        else:
            log.append(
                {
                    "step": "merge_skipped",
                    "reason": (
                        "stage requires approval and promotion is not approved"
                        if not merge_allowed
                        else "nothing to merge"
                    ),
                }
            )

        if merged:
            _run_calls(
                db,
                tenant_id,
                target.id,
                frozenset({"post_merge", "on_promotion"}),
                call_context,
                log,
            )
    except PipelineAbort as e:
        status = "failed"
        error_message = str(e)
        log.append({"step": "error", "error": error_message})
    except gitops.GitOpsError as e:
        status = "failed"
        error_message = gitops.scrub(str(e), token)
        log.append(
            {
                "step": "error",
                "error": error_message,
                "output": gitops.scrub(_tail(e.output), token),
            }
        )
    except Exception as e:  # noqa: BLE001 - record as failed execution
        status = "failed"
        error_message = gitops.scrub(str(e), token)
        log.append({"step": "error", "error": error_message})
        logger.error(
            "flow_execution_failed",
            extra={"execution_id": execution_id, "error": error_message},
        )
    finally:
        if ws is not None:
            ws.cleanup()

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
            "merged": merged,
        },
    )
    return {
        "execution_id": execution_id,
        "status": status,
        "promotion_id": promotion.id,
        "merged": merged,
        "error": error_message,
    }
