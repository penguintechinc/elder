"""Flows promotions & approvals API endpoints using penguin-dal.

Provides promotion request and approval workflow management.
"""

import logging
import uuid
from datetime import datetime, timezone

from quart import Blueprint, current_app, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

logger = logging.getLogger(__name__)

bp = Blueprint("flows_promotions", __name__)


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


def _get_identity_id() -> int:
    """Extract identity_id from g.claims."""
    from quart import g

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")
    if identity_id:
        try:
            return int(identity_id)
        except (ValueError, TypeError):
            pass
    return None


def _serialize_promotion(promotion):
    """Serialize promotion record to JSON-friendly dict."""
    return {
        "id": promotion.id,
        "promotion_id": promotion.promotion_id,
        "flow_id": promotion.flow_id,
        "source_stage_id": promotion.source_stage_id,
        "target_stage_id": promotion.target_stage_id,
        "status": promotion.status,
        "requested_by_identity_id": promotion.requested_by_identity_id,
        "merged_by_identity_id": promotion.merged_by_identity_id,
        "source_commit": promotion.source_commit,
        "created_at": (
            promotion.created_at.isoformat() if promotion.created_at else None
        ),
        "updated_at": (
            promotion.updated_at.isoformat() if promotion.updated_at else None
        ),
    }


def _serialize_approval(approval):
    """Serialize approval record to JSON-friendly dict."""
    return {
        "id": approval.id,
        "approval_id": approval.approval_id,
        "promotion_id": approval.promotion_id,
        "approver_identity_id": approval.approver_identity_id,
        "decision": approval.decision,
        "comment": approval.comment,
        "can_override": approval.can_override,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
    }


@bp.route("/promotions", methods=["GET"])
@login_required
@require_scope("flows:read")
async def list_promotions():
    """List all promotions for current tenant."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    # Extract pagination and filters BEFORE threadpool
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, int(request.args.get("per_page", 20)))
    offset = (page - 1) * per_page
    status = request.args.get("status")
    flow_id = request.args.get("flow_id")

    def list_():
        query = db.iceflows_promotions.tenant_id == tenant_id

        if status:
            query &= db.iceflows_promotions.status == status
        if flow_id:
            if flow_id.isdigit():
                query &= db.iceflows_promotions.flow_id == int(flow_id)

        # Count total
        total = db(query).count()

        # Execute query with pagination
        promotions = db(query).select(
            orderby=~db.iceflows_promotions.created_at,
            limitby=(offset, offset + per_page),
        )

        result = [_serialize_promotion(p) for p in promotions]

        return {
            "data": result,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }

    data = await run_in_threadpool(list_)
    return ApiResponse.success(data)


@bp.route("/promotions/<promotion_id>", methods=["GET"])
@login_required
@require_scope("flows:read")
async def get_promotion(promotion_id: str):
    """Get promotion details."""
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def get():
        promotion = (
            db(
                (
                    db.iceflows_promotions.id == int(promotion_id)
                    if promotion_id.isdigit()
                    else False
                )
                & (db.iceflows_promotions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not promotion:
            return None, 404

        # Get approvals
        approvals = db(
            (db.iceflows_approvals.promotion_id == promotion.id)
            & (db.iceflows_approvals.tenant_id == tenant_id)
        ).select(orderby=~db.iceflows_approvals.created_at)

        result = _serialize_promotion(promotion)
        result["approvals"] = [_serialize_approval(a) for a in approvals]

        return {"data": result}, 200

    result = await run_in_threadpool(get)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

    return result


@bp.route("/promotions/<promotion_id>/approve", methods=["POST"])
@login_required
@require_scope("flows:approve")
async def approve_promotion(promotion_id: str):
    """Approve a promotion."""
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    if not identity_id:
        return ApiResponse.error("Identity not found", 403)

    data = await request.get_json()
    if not data:
        data = {}

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def approve():
        promotion = (
            db(
                (
                    db.iceflows_promotions.id == int(promotion_id)
                    if promotion_id.isdigit()
                    else False
                )
                & (db.iceflows_promotions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not promotion:
            return None, 404

        # State guard: only a pending promotion can be acted on. Approving an
        # already merged/rejected/cancelled promotion is an invalid transition.
        if promotion.status != "pending":
            return None, 409

        # Load the target (approval-gate) stage.
        stage = (
            db(
                (db.iceflows_stages.id == promotion.target_stage_id)
                & (db.iceflows_stages.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stage:
            return None, 404

        # AuthZ (fail-closed): the caller must be a configured approver for this
        # stage. flows:approve scope alone is NOT sufficient — an approver row
        # must exist. Group-based approvers are not yet supported (no groups
        # table in Elder), so match on identity_id only. If no approvers are
        # configured for a stage, nobody can approve (secure default).
        is_approver = (
            db(
                (db.iceflows_stage_approvers.stage_id == stage.id)
                & (db.iceflows_stage_approvers.identity_id == identity_id)
                & (db.iceflows_stage_approvers.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not is_approver:
            return None, 403

        # Check if already approved by this user
        existing = (
            db(
                (db.iceflows_approvals.promotion_id == promotion.id)
                & (db.iceflows_approvals.approver_identity_id == identity_id)
                & (db.iceflows_approvals.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        now = datetime.now(timezone.utc)

        if existing:
            # Update decision
            db(db.iceflows_approvals.id == existing.id).update(
                decision="approve",
                comment=data.get("comments", ""),
                updated_at=now,
            )
            db.commit()
        else:
            # Create approval
            approval_id = str(uuid.uuid4())
            db.iceflows_approvals.insert(
                tenant_id=tenant_id,
                approval_id=approval_id,
                promotion_id=promotion.id,
                approver_identity_id=identity_id,
                decision="approve",
                comment=data.get("comments", ""),
                can_override=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        # Tally distinct "approve" decisions. Only advance the promotion to
        # "approved" once the stage's min_approvers threshold is met — a single
        # approver must never unilaterally flip the state.
        approve_count = db(
            (db.iceflows_approvals.promotion_id == promotion.id)
            & (db.iceflows_approvals.decision == "approve")
            & (db.iceflows_approvals.tenant_id == tenant_id)
        ).count()

        required = stage.min_approvers if stage.min_approvers else 1
        new_status = promotion.status
        if stage.require_approval and approve_count >= required:
            new_status = "approved"

        db(db.iceflows_promotions.id == promotion.id).update(
            status=new_status,
            updated_at=now,
        )
        db.commit()

        updated_promotion = (
            db(db.iceflows_promotions.id == promotion.id).select().first()
        )

        result = _serialize_promotion(updated_promotion)
        result["approvals_received"] = approve_count
        result["approvals_required"] = required
        return result, 200

    result = await run_in_threadpool(approve)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    if result[1] == 403:
        return ApiResponse.error("Not an authorized approver for this stage", 403)
    if result[1] == 409:
        return ApiResponse.error("Promotion is not in a pending state", 409)

    return result


@bp.route("/promotions/<promotion_id>/reject", methods=["POST"])
@login_required
@require_scope("flows:approve")
async def reject_promotion(promotion_id: str):
    """Reject a promotion."""
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    if not identity_id:
        return ApiResponse.error("Identity not found", 403)

    data = await request.get_json()
    if not data:
        data = {}

    # Capture db in request context BEFORE threadpool
    db = current_app.db

    def reject():
        promotion = (
            db(
                (
                    db.iceflows_promotions.id == int(promotion_id)
                    if promotion_id.isdigit()
                    else False
                )
                & (db.iceflows_promotions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not promotion:
            return None, 404

        # State guard: only a pending promotion can be rejected.
        if promotion.status != "pending":
            return None, 409

        # Load the target (approval-gate) stage.
        stage = (
            db(
                (db.iceflows_stages.id == promotion.target_stage_id)
                & (db.iceflows_stages.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not stage:
            return None, 404

        # AuthZ (fail-closed): the caller must be a configured approver for this
        # stage. flows:approve scope alone is NOT sufficient.
        is_approver = (
            db(
                (db.iceflows_stage_approvers.stage_id == stage.id)
                & (db.iceflows_stage_approvers.identity_id == identity_id)
                & (db.iceflows_stage_approvers.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not is_approver:
            return None, 403

        # Check if already rejected by this user
        existing = (
            db(
                (db.iceflows_approvals.promotion_id == promotion.id)
                & (db.iceflows_approvals.approver_identity_id == identity_id)
                & (db.iceflows_approvals.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        now = datetime.now(timezone.utc)

        if existing:
            # Update decision
            db(db.iceflows_approvals.id == existing.id).update(
                decision="reject",
                comment=data.get("comments", ""),
                updated_at=now,
            )
            db.commit()
        else:
            # Create approval
            approval_id = str(uuid.uuid4())
            db.iceflows_approvals.insert(
                tenant_id=tenant_id,
                approval_id=approval_id,
                promotion_id=promotion.id,
                approver_identity_id=identity_id,
                decision="reject",
                comment=data.get("comments", ""),
                can_override=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        # Update promotion status to rejected
        db(db.iceflows_promotions.id == promotion.id).update(
            status="rejected",
            updated_at=now,
        )
        db.commit()

        updated_promotion = (
            db(db.iceflows_promotions.id == promotion.id).select().first()
        )

        return _serialize_promotion(updated_promotion), 200

    result = await run_in_threadpool(reject)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    if result[1] == 403:
        return ApiResponse.error("Not an authorized approver for this stage", 403)
    if result[1] == 409:
        return ApiResponse.error("Promotion is not in a pending state", 409)

    return result


@bp.route("/promotions/<promotion_id>/execute", methods=["POST"])
@login_required
@require_scope("flows:execute")
async def execute_promotion(promotion_id: str):
    """Trigger CI/CD execution for a promotion (enqueues a flows-invoker job).

    The isolated flows invoker consumes the job and records an
    ``iceflows_executions`` row. Only a pending or approved promotion may be
    executed. Enqueue failure does not fail the request — the invoker's
    reconcile/sweeper path can pick it up.
    """
    tenant_id = _get_tenant_id()
    identity_id = _get_identity_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    db = current_app.db

    def _validate():
        promotion = (
            db(
                (
                    db.iceflows_promotions.id == int(promotion_id)
                    if promotion_id.isdigit()
                    else False
                )
                & (db.iceflows_promotions.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not promotion:
            return None, 404
        if promotion.status not in ("pending", "approved"):
            return None, 409
        return promotion.id, 202

    result = await run_in_threadpool(_validate)
    if result[1] == 404:
        return ApiResponse.error("Not found", 404)
    if result[1] == 409:
        return ApiResponse.error("Promotion is not in an executable state", 409)

    promo_db_id = result[0]
    enqueue_time = datetime.now(timezone.utc)
    enqueued = False
    try:
        import redis.asyncio

        from apps.worker.config.settings import settings
        from shared.jobbus import JobBus

        if settings.redis_url:
            redis_client = redis.asyncio.from_url(settings.redis_url)
            jobbus = JobBus(redis_client)
            await jobbus.ensure_group("flows")
            await jobbus.enqueue(
                "flows",
                "execute_promotion",
                {
                    "promotion_id": promo_db_id,
                    "tenant_id": tenant_id,
                    "started_by_identity_id": identity_id,
                },
                enqueued_at=enqueue_time.isoformat(),
                tenant_id=tenant_id,
            )
            await redis_client.close()
            enqueued = True
        else:
            logger.warning(
                "REDIS_URL not configured; promotion %s not enqueued", promo_db_id
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Failed to enqueue flow execution for promotion %s: %s",
            promo_db_id,
            e,
        )

    return {
        "promotion_id": promo_db_id,
        "status": "queued",
        "enqueued": enqueued,
    }, 202
