"""Flows stage CRUD endpoints using penguin-dal."""

import logging
import uuid
from datetime import datetime, timezone

from quart import Blueprint, current_app, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

logger = logging.getLogger(__name__)

bp = Blueprint("flows_stages", __name__)


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


def _serialize_stage(stage):
    """Serialize stage database row to JSON-friendly dict."""
    return {
        "id": stage.id,
        "stage_id": stage.stage_id,
        "flow_id": stage.flow_id,
        "stage_order": stage.stage_order,
        "branch_name": stage.branch_name,
        "display_name": stage.display_name or stage.branch_name,
        "description": stage.description or "",
        "is_production": stage.is_production or False,
        "auto_promote": stage.auto_promote or False,
        "require_approval": stage.require_approval or True,
        "min_approvers": stage.min_approvers or 1,
        "override_min_approvers": stage.override_min_approvers or 2,
        "day_restrictions": stage.day_restrictions or {},
        "time_restrictions": stage.time_restrictions or {},
        "notification_config": stage.notification_config or {},
        "is_enabled": stage.is_enabled or True,
        "created_at": stage.created_at.isoformat() if stage.created_at else None,
        "updated_at": stage.updated_at.isoformat() if stage.updated_at else None,
    }


@bp.route("/<flow_id>/stages", methods=["GET"])
@login_required
@require_scope("flows:read")
async def list_stages(flow_id: str):
    """List all stages for a flow."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def list_():
        flow = (
            db(
                (db.iceflows.id == int(flow_id) if flow_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not flow:
            return None, 404

        stages = db(db.iceflows_stages.flow_id == flow.id).select(
            orderby=db.iceflows_stages.stage_order
        )

        return {"data": [_serialize_stage(s) for s in stages]}, 200

    result = await run_in_threadpool(list_)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result


@bp.route("/<flow_id>/stages", methods=["POST"])
@login_required
@require_scope("flows:write")
async def create_stage(flow_id: str):
    """Create a new stage for a flow."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json()
    if not data:
        data = {}

    if not data.get("branch_name"):
        return ApiResponse.error("branch_name is required", 400)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def create():
        flow = (
            db(
                (db.iceflows.id == int(flow_id) if flow_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not flow:
            return None, 404

        # Get max stage_order
        max_order = (
            db(db.iceflows_stages.flow_id == flow.id)
            .select(db.iceflows_stages.stage_order.max())
            .first()
        )
        next_order = (max_order[db.iceflows_stages.stage_order.max()] or 0) + 1

        # Create stage
        stage_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        db_id = db.iceflows_stages.insert(
            tenant_id=tenant_id,
            stage_id=stage_id,
            flow_id=flow.id,
            stage_order=next_order,
            branch_name=data["branch_name"],
            display_name=data.get("display_name") or data["branch_name"],
            description=data.get("description", ""),
            is_production=data.get("is_production", False),
            auto_promote=data.get("auto_promote", False),
            require_approval=data.get("require_approval", True),
            min_approvers=data.get("min_approvers", 1),
            override_min_approvers=data.get("override_min_approvers", 2),
            day_restrictions=data.get("day_restrictions", {}),
            time_restrictions=data.get("time_restrictions", {}),
            notification_config=data.get("notification_config", {}),
            is_enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        stage = db(db.iceflows_stages.id == db_id).select().first()

        return _serialize_stage(stage), 201

    result = await run_in_threadpool(create)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result


@bp.route("/<flow_id>/stages/<stage_id>", methods=["GET"])
@login_required
@require_scope("flows:read")
async def get_stage(flow_id: str, stage_id: str):
    """Get stage details."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def get():
        flow = (
            db(
                (db.iceflows.id == int(flow_id) if flow_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not flow:
            return None, 404

        stage = (
            db(
                (db.iceflows_stages.stage_id == stage_id)
                & (db.iceflows_stages.flow_id == flow.id)
                & (db.iceflows_stages.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not stage:
            return None, 404

        return _serialize_stage(stage), 200

    result = await run_in_threadpool(get)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result


@bp.route("/<flow_id>/stages/<stage_id>", methods=["PUT"])
@login_required
@require_scope("flows:write")
async def update_stage(flow_id: str, stage_id: str):
    """Update stage configuration."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    data = await request.get_json()
    if not data:
        data = {}

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def update():
        flow = (
            db(
                (db.iceflows.id == int(flow_id) if flow_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not flow:
            return None, 404

        stage = (
            db(
                (db.iceflows_stages.stage_id == stage_id)
                & (db.iceflows_stages.flow_id == flow.id)
                & (db.iceflows_stages.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not stage:
            return None, 404

        # Build update dict
        update_data = {"updated_at": datetime.now(timezone.utc)}

        for key in [
            "branch_name",
            "display_name",
            "description",
            "is_production",
            "auto_promote",
            "require_approval",
            "min_approvers",
            "override_min_approvers",
            "day_restrictions",
            "time_restrictions",
            "notification_config",
            "is_enabled",
        ]:
            if key in data:
                update_data[key] = data[key]

        db(db.iceflows_stages.id == stage.id).update(**update_data)
        db.commit()

        updated_stage = db(db.iceflows_stages.id == stage.id).select().first()

        return _serialize_stage(updated_stage), 200

    result = await run_in_threadpool(update)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result


@bp.route("/<flow_id>/stages/<stage_id>", methods=["DELETE"])
@login_required
@require_scope("flows:write")
async def delete_stage(flow_id: str, stage_id: str):
    """Delete a stage from a flow."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def delete():
        flow = (
            db(
                (db.iceflows.id == int(flow_id) if flow_id.isdigit() else False)
                & (db.iceflows.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not flow:
            return None, 404

        stage = (
            db(
                (db.iceflows_stages.stage_id == stage_id)
                & (db.iceflows_stages.flow_id == flow.id)
                & (db.iceflows_stages.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not stage:
            return None, 404

        # Delete stage (cascade handles approvers, tests, calls)
        db(db.iceflows_stages.id == stage.id).delete()
        db.commit()

        # Reorder remaining stages
        remaining_stages = db(db.iceflows_stages.flow_id == flow.id).select(
            orderby=db.iceflows_stages.stage_order
        )

        for idx, s in enumerate(remaining_stages, start=1):
            if s.stage_order != idx:
                db(db.iceflows_stages.id == s.id).update(stage_order=idx)
        db.commit()

        return {"message": "Stage deleted successfully"}, 204

    result = await run_in_threadpool(delete)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result[0], result[1]
