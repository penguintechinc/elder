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
        "commit_sha": promotion.commit_sha,
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
        "comments": approval.comments,
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
                comments=data.get("comments", ""),
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
                comments=data.get("comments", ""),
                can_override=False,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        # Update promotion status if all approvals received
        db(db.iceflows_promotions.id == promotion.id).update(
            updated_at=now,
        )
        db.commit()

        updated_promotion = (
            db(db.iceflows_promotions.id == promotion.id).select().first()
        )

        return _serialize_promotion(updated_promotion), 200

    result = await run_in_threadpool(approve)

    if result[1] == 404:
        return ApiResponse.error("Not found", 404)

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
                comments=data.get("comments", ""),
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
                comments=data.get("comments", ""),
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

    return result
