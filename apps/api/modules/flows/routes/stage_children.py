"""Flows stage-children endpoints (approvers, tests, calls, reviews).

All routes are nested under a stage: ``/<flow_id>/stages/<stage_id>/...`` and
are tenant-scoped end to end. Approver management requires ``flows:admin`` —
adding an approver defines WHO may approve promotions, so a plain ``flows:write``
editor must not be able to self-authorize (separation of duties, matching the
policy-field gate in stages.py).
"""

import logging
import uuid
from datetime import datetime, timezone

from quart import Blueprint, current_app, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

logger = logging.getLogger(__name__)

bp = Blueprint("flows_stage_children", __name__)


def _get_tenant_id() -> int:
    """Extract tenant_id from g.claims."""
    from quart import g

    claims = getattr(g, "claims", {}) or {}
    tenant_str = claims.get("tenant", "")
    if not tenant_str:
        return None
    try:
        return int(tenant_str)
    except (ValueError, TypeError):
        return None


def _has_scope(scope: str) -> bool:
    """True if the current request carries ``scope`` (or is a superuser)."""
    from quart import g

    if getattr(g, "current_user", None) and getattr(
        g.current_user, "is_superuser", False
    ):
        return True
    claims = getattr(g, "claims", {}) or {}
    return scope in set(claims.get("scope", []))


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _serialize_approver(approver):
    return {
        "id": approver.id,
        "approver_id": approver.approver_id,
        "stage_id": approver.stage_id,
        "identity_id": approver.identity_id,
        "group_id": approver.group_id,
        "role": approver.role,
        "can_override": approver.can_override,
        "created_at": (
            approver.created_at.isoformat() if approver.created_at else None
        ),
    }


def _serialize_test(test):
    return {
        "id": test.id,
        "test_id": test.test_id,
        "stage_id": test.stage_id,
        "name": test.name,
        "test_type": test.test_type,
        "path_mode": test.path_mode,
        "centralized_path": test.centralized_path,
        "repo_relative_path": test.repo_relative_path,
        "command": test.command,
        "timeout_seconds": test.timeout_seconds,
        "is_blocking": test.is_blocking,
        "is_required": test.is_required,
        "execution_order": test.execution_order,
        "env_vars": test.env_vars or {},
        "created_at": test.created_at.isoformat() if test.created_at else None,
        "updated_at": test.updated_at.isoformat() if test.updated_at else None,
    }


def _serialize_call(call):
    return {
        "id": call.id,
        "call_id": call.call_id,
        "stage_id": call.stage_id,
        "name": call.name,
        "call_type": call.call_type,
        "target_id": call.target_id,
        "trigger_on": call.trigger_on,
        "input_template": call.input_template or {},
        "timeout_seconds": call.timeout_seconds,
        "is_blocking": call.is_blocking,
        "retry_count": call.retry_count,
        "execution_order": call.execution_order,
        "created_at": call.created_at.isoformat() if call.created_at else None,
        "updated_at": call.updated_at.isoformat() if call.updated_at else None,
    }


def _serialize_review(review):
    return {
        "id": review.id,
        "review_id": review.review_id,
        "stage_id": review.stage_id,
        "is_required": review.is_required,
        "review_type": review.review_type,
        "min_score": review.min_score,
        "block_on_critical": review.block_on_critical,
        "allowed_issue_types": review.allowed_issue_types or [],
        "reviewers_notified": review.reviewers_notified,
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "updated_at": review.updated_at.isoformat() if review.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Shared stage resolver (tenant + flow + stage scoping)
# ---------------------------------------------------------------------------


def _load_stage(db, flow_id: str, stage_id: str, tenant_id: int):
    """Resolve the stage row from (flow_id path, stage_id UUID), tenant-scoped.

    Returns the stage row, or None if the flow or stage is missing / not in
    this tenant (uniform 404 — never leaks cross-tenant existence).
    """
    flow = (
        db(
            (db.iceflows.id == int(flow_id) if flow_id.isdigit() else False)
            & (db.iceflows.tenant_id == tenant_id)
        )
        .select()
        .first()
    )
    if not flow:
        return None
    return (
        db(
            (db.iceflows_stages.stage_id == stage_id)
            & (db.iceflows_stages.flow_id == flow.id)
            & (db.iceflows_stages.tenant_id == tenant_id)
        )
        .select()
        .first()
    )


# ===========================================================================
# Approvers  (management requires flows:admin — separation of duties)
# ===========================================================================


@bp.route("/<flow_id>/stages/<stage_id>/approvers", methods=["GET"])
@login_required
@require_scope("flows:read")
async def list_approvers(flow_id: str, stage_id: str):
    """List approvers configured for a stage."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        rows = db(
            (db.iceflows_stage_approvers.stage_id == stage.id)
            & (db.iceflows_stage_approvers.tenant_id == tenant_id)
        ).select(orderby=db.iceflows_stage_approvers.id)
        return {"data": [_serialize_approver(r) for r in rows]}, 200

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/approvers", methods=["POST"])
@login_required
@require_scope("flows:admin")
async def add_approver(flow_id: str, stage_id: str):
    """Add an approver to a stage. Requires flows:admin (defines who approves)."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}
    identity_id = data.get("identity_id")
    group_id = data.get("group_id")
    if not identity_id and not group_id:
        return ApiResponse.error("identity_id or group_id is required", 400)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404

        # Reject duplicate identity approver on the same stage.
        if identity_id is not None:
            dup = (
                db(
                    (db.iceflows_stage_approvers.stage_id == stage.id)
                    & (db.iceflows_stage_approvers.identity_id == identity_id)
                    & (db.iceflows_stage_approvers.tenant_id == tenant_id)
                )
                .select()
                .first()
            )
            if dup:
                return None, 409

        now = datetime.now(timezone.utc)
        db_id = db.iceflows_stage_approvers.insert(
            tenant_id=tenant_id,
            approver_id=str(uuid.uuid4()),
            stage_id=stage.id,
            identity_id=identity_id,
            group_id=group_id,
            role=data.get("role", "approver"),
            can_override=bool(data.get("can_override", False)),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        row = db(db.iceflows_stage_approvers.id == db_id).select().first()
        return _serialize_approver(row), 201

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    if result[1] == 409:
        return ApiResponse.error("Approver already exists for this stage", 409)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/approvers/<approver_id>", methods=["DELETE"])
@login_required
@require_scope("flows:admin")
async def remove_approver(flow_id: str, stage_id: str, approver_id: str):
    """Remove an approver from a stage. Requires flows:admin."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        row = (
            db(
                (db.iceflows_stage_approvers.approver_id == approver_id)
                & (db.iceflows_stage_approvers.stage_id == stage.id)
                & (db.iceflows_stage_approvers.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not row:
            return None, 404
        db(db.iceflows_stage_approvers.id == row.id).delete()
        db.commit()
        return {"message": "Approver removed"}, 204

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result[0], result[1]


# ===========================================================================
# Tests  (config CRUD — flows:write)
# ===========================================================================

_TEST_FIELDS = (
    "name",
    "test_type",
    "path_mode",
    "centralized_path",
    "repo_relative_path",
    "command",
    "timeout_seconds",
    "is_blocking",
    "is_required",
    "execution_order",
    "env_vars",
)


@bp.route("/<flow_id>/stages/<stage_id>/tests", methods=["GET"])
@login_required
@require_scope("flows:read")
async def list_tests(flow_id: str, stage_id: str):
    """List tests configured for a stage."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        rows = db(
            (db.iceflows_stage_tests.stage_id == stage.id)
            & (db.iceflows_stage_tests.tenant_id == tenant_id)
        ).select(orderby=db.iceflows_stage_tests.execution_order)
        return {"data": [_serialize_test(r) for r in rows]}, 200

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/tests", methods=["POST"])
@login_required
@require_scope("flows:write")
async def add_test(flow_id: str, stage_id: str):
    """Add a test to a stage."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}
    if not data.get("name"):
        return ApiResponse.error("name is required", 400)
    if not data.get("test_type"):
        return ApiResponse.error("test_type is required", 400)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        now = datetime.now(timezone.utc)
        db_id = db.iceflows_stage_tests.insert(
            tenant_id=tenant_id,
            test_id=str(uuid.uuid4()),
            stage_id=stage.id,
            name=data["name"],
            test_type=data["test_type"],
            path_mode=data.get("path_mode", "repo_relative"),
            centralized_path=data.get("centralized_path"),
            repo_relative_path=data.get("repo_relative_path"),
            command=data.get("command"),
            timeout_seconds=data.get("timeout_seconds", 600),
            is_blocking=bool(data.get("is_blocking", True)),
            is_required=bool(data.get("is_required", True)),
            execution_order=data.get("execution_order", 0),
            env_vars=data.get("env_vars", {}),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        row = db(db.iceflows_stage_tests.id == db_id).select().first()
        return _serialize_test(row), 201

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/tests/<test_id>", methods=["GET"])
@login_required
@require_scope("flows:read")
async def get_test(flow_id: str, stage_id: str, test_id: str):
    """Get a single stage test."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        row = (
            db(
                (db.iceflows_stage_tests.test_id == test_id)
                & (db.iceflows_stage_tests.stage_id == stage.id)
                & (db.iceflows_stage_tests.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not row:
            return None, 404
        return _serialize_test(row), 200

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/tests/<test_id>", methods=["PUT"])
@login_required
@require_scope("flows:write")
async def update_test(flow_id: str, stage_id: str, test_id: str):
    """Update a stage test."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}
    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        row = (
            db(
                (db.iceflows_stage_tests.test_id == test_id)
                & (db.iceflows_stage_tests.stage_id == stage.id)
                & (db.iceflows_stage_tests.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not row:
            return None, 404
        update_data = {"updated_at": datetime.now(timezone.utc)}
        for key in _TEST_FIELDS:
            if key in data:
                update_data[key] = data[key]
        db(db.iceflows_stage_tests.id == row.id).update(**update_data)
        db.commit()
        updated = db(db.iceflows_stage_tests.id == row.id).select().first()
        return _serialize_test(updated), 200

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/tests/<test_id>", methods=["DELETE"])
@login_required
@require_scope("flows:write")
async def delete_test(flow_id: str, stage_id: str, test_id: str):
    """Delete a stage test."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        row = (
            db(
                (db.iceflows_stage_tests.test_id == test_id)
                & (db.iceflows_stage_tests.stage_id == stage.id)
                & (db.iceflows_stage_tests.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not row:
            return None, 404
        db(db.iceflows_stage_tests.id == row.id).delete()
        db.commit()
        return {"message": "Test deleted"}, 204

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result[0], result[1]


# ===========================================================================
# Calls  (config CRUD — flows:write)
# ===========================================================================

_CALL_FIELDS = (
    "name",
    "call_type",
    "target_id",
    "trigger_on",
    "input_template",
    "timeout_seconds",
    "is_blocking",
    "retry_count",
    "execution_order",
)


@bp.route("/<flow_id>/stages/<stage_id>/calls", methods=["GET"])
@login_required
@require_scope("flows:read")
async def list_calls(flow_id: str, stage_id: str):
    """List external-service calls configured for a stage."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        rows = db(
            (db.iceflows_stage_calls.stage_id == stage.id)
            & (db.iceflows_stage_calls.tenant_id == tenant_id)
        ).select(orderby=db.iceflows_stage_calls.execution_order)
        return {"data": [_serialize_call(r) for r in rows]}, 200

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/calls", methods=["POST"])
@login_required
@require_scope("flows:write")
async def add_call(flow_id: str, stage_id: str):
    """Add an external-service call to a stage."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}
    for req_field in ("name", "call_type", "target_id"):
        if not data.get(req_field):
            return ApiResponse.error(f"{req_field} is required", 400)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        now = datetime.now(timezone.utc)
        db_id = db.iceflows_stage_calls.insert(
            tenant_id=tenant_id,
            call_id=str(uuid.uuid4()),
            stage_id=stage.id,
            name=data["name"],
            call_type=data["call_type"],
            target_id=str(data["target_id"]),
            trigger_on=data.get("trigger_on", "on_promotion"),
            input_template=data.get("input_template", {}),
            timeout_seconds=data.get("timeout_seconds", 300),
            is_blocking=bool(data.get("is_blocking", True)),
            retry_count=data.get("retry_count", 0),
            execution_order=data.get("execution_order", 0),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        row = db(db.iceflows_stage_calls.id == db_id).select().first()
        return _serialize_call(row), 201

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/calls/<call_id>", methods=["GET"])
@login_required
@require_scope("flows:read")
async def get_call(flow_id: str, stage_id: str, call_id: str):
    """Get a single stage call."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        row = (
            db(
                (db.iceflows_stage_calls.call_id == call_id)
                & (db.iceflows_stage_calls.stage_id == stage.id)
                & (db.iceflows_stage_calls.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not row:
            return None, 404
        return _serialize_call(row), 200

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/calls/<call_id>", methods=["PUT"])
@login_required
@require_scope("flows:write")
async def update_call(flow_id: str, stage_id: str, call_id: str):
    """Update a stage call."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}
    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        row = (
            db(
                (db.iceflows_stage_calls.call_id == call_id)
                & (db.iceflows_stage_calls.stage_id == stage.id)
                & (db.iceflows_stage_calls.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not row:
            return None, 404
        update_data = {"updated_at": datetime.now(timezone.utc)}
        for key in _CALL_FIELDS:
            if key in data:
                update_data[key] = str(data[key]) if key == "target_id" else data[key]
        db(db.iceflows_stage_calls.id == row.id).update(**update_data)
        db.commit()
        updated = db(db.iceflows_stage_calls.id == row.id).select().first()
        return _serialize_call(updated), 200

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/calls/<call_id>", methods=["DELETE"])
@login_required
@require_scope("flows:write")
async def delete_call(flow_id: str, stage_id: str, call_id: str):
    """Delete a stage call."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        row = (
            db(
                (db.iceflows_stage_calls.call_id == call_id)
                & (db.iceflows_stage_calls.stage_id == stage.id)
                & (db.iceflows_stage_calls.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not row:
            return None, 404
        db(db.iceflows_stage_calls.id == row.id).delete()
        db.commit()
        return {"message": "Call deleted"}, 204

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result[0], result[1]


# ===========================================================================
# Review  (one per stage — GET + upsert PUT; flows:write)
# ===========================================================================

_REVIEW_FIELDS = (
    "is_required",
    "review_type",
    "min_score",
    "block_on_critical",
    "allowed_issue_types",
    "reviewers_notified",
)


@bp.route("/<flow_id>/stages/<stage_id>/reviews", methods=["GET"])
@login_required
@require_scope("flows:read")
async def get_review(flow_id: str, stage_id: str):
    """Get the Darwin AI review config for a stage (404 if unconfigured)."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        row = (
            db(
                (db.iceflows_stage_reviews.stage_id == stage.id)
                & (db.iceflows_stage_reviews.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not row:
            return None, 404
        return _serialize_review(row), 200

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result


@bp.route("/<flow_id>/stages/<stage_id>/reviews", methods=["PUT"])
@login_required
@require_scope("flows:write")
async def update_review(flow_id: str, stage_id: str):
    """Upsert the Darwin AI review config for a stage (one per stage)."""
    tenant_id = _get_tenant_id()
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json() or {}
    db = current_app.db

    def _run():
        stage = _load_stage(db, flow_id, stage_id, tenant_id)
        if not stage:
            return None, 404
        now = datetime.now(timezone.utc)
        row = (
            db(
                (db.iceflows_stage_reviews.stage_id == stage.id)
                & (db.iceflows_stage_reviews.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if row:
            update_data = {"updated_at": now}
            for key in _REVIEW_FIELDS:
                if key in data:
                    update_data[key] = data[key]
            db(db.iceflows_stage_reviews.id == row.id).update(**update_data)
            db.commit()
            updated = db(db.iceflows_stage_reviews.id == row.id).select().first()
            return _serialize_review(updated), 200

        db_id = db.iceflows_stage_reviews.insert(
            tenant_id=tenant_id,
            review_id=str(uuid.uuid4()),
            stage_id=stage.id,
            is_required=bool(data.get("is_required", True)),
            review_type=data.get("review_type", "inherit"),
            min_score=data.get("min_score", 70),
            block_on_critical=bool(data.get("block_on_critical", True)),
            allowed_issue_types=data.get("allowed_issue_types", []),
            reviewers_notified=bool(data.get("reviewers_notified", True)),
            created_at=now,
            updated_at=now,
        )
        db.commit()
        created = db(db.iceflows_stage_reviews.id == db_id).select().first()
        return _serialize_review(created), 201

    result = await run_in_threadpool(_run)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    return result
