"""Enterprise admin convenience layer for DSAR handling.

Bulk erasure and the request-visibility dashboard are the *admin convenience
layer* on top of the statutory rights in apps/api/api/v1/privacy.py -- per
critical-rules.md "Feature Flags & License Tiers", only this layer is
Enterprise-gated (`require_tier("enterprise")`); the underlying statutory
rights themselves are never tier-gated.
"""

from __future__ import annotations

from typing import Any

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import admin_required, login_required
from apps.api.common.licensing.tier_gate import require_tier
from apps.api.services.privacy.service import PrivacyService
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("privacy_admin", __name__)

_MAX_LIST_LIMIT = 200


def _tenant_legal_hold(db: Any, tenant_id: int | None) -> bool:
    """True if `tenant_id`'s tenant has an active legal hold (blocks bulk erasure)."""
    if not tenant_id:
        return False
    tenant = db.tenants[tenant_id]
    return bool(getattr(tenant, "legal_hold", False)) if tenant else False


def _list_requests(db: Any, tenant_id: int | None) -> list[dict[str, Any]]:
    """Tenant-scoped page of the DSAR activity log, most recent first."""
    query = db.dsar_requests.id > 0
    if tenant_id is not None:
        query = db.dsar_requests.tenant_id == tenant_id
    rows = db(query).select(
        orderby=~db.dsar_requests.id,
        limitby=(0, _MAX_LIST_LIMIT),
    )
    return [
        {
            "id": r.id,
            "tenant_id": r.tenant_id,
            "identity_id": r.identity_id,
            "request_type": r.request_type,
            "status": r.status,
            "requested_by_identity_id": r.requested_by_identity_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@bp.route("/requests", methods=["GET"])
@login_required
@admin_required
@require_tier("enterprise")
async def list_dsar_requests() -> tuple[Any, int]:
    """List DSAR self-service + bulk activity for the admin's tenant (Enterprise).

    Returns:
        200: {"requests": [...], "count": N}
        403: caller's deployment tier is below Enterprise
    """
    db = current_app.db
    tenant_id = getattr(g.current_user, "tenant_id", None)

    requests_list = await run_in_threadpool(_list_requests, db, tenant_id)
    return jsonify({"requests": requests_list, "count": len(requests_list)}), 200


@bp.route("/bulk-erase", methods=["POST"])
@login_required
@admin_required
@require_tier("enterprise")
async def bulk_erase() -> tuple[Any, int]:
    """Bulk right-to-erasure across many identities in one call (Enterprise).

    Request body:
        {"identity_ids": [1, 2, 3]}

    Returns:
        200: {"results": [{"identity_id": ..., "anonymized": bool, ...}, ...]}
        400: missing/invalid identity_ids
        403: caller's deployment tier is below Enterprise
    """
    body = await request.get_json(silent=True) or {}
    identity_ids = body.get("identity_ids")
    if (
        not isinstance(identity_ids, list)
        or not identity_ids
        or not all(isinstance(i, int) for i in identity_ids)
    ):
        return ApiResponse.validation_error(
            "identity_ids", "must be a non-empty list of integers"
        )

    db = current_app.db
    tenant_id = getattr(g.current_user, "tenant_id", None)
    admin_id = g.current_user.id

    legal_hold = await run_in_threadpool(_tenant_legal_hold, db, tenant_id)
    result = await run_in_threadpool(
        PrivacyService.bulk_erase, db, identity_ids, legal_hold
    )

    for entry in result["results"]:
        if entry.get("anonymized"):
            await run_in_threadpool(
                _log_bulk_request, db, tenant_id, entry["identity_id"], admin_id
            )

    return jsonify(result), 200


def _log_bulk_request(
    db: Any, tenant_id: int | None, identity_id: int, admin_id: int
) -> None:
    """Best-effort write to the dsar_requests activity log for a bulk erasure."""
    if not tenant_id:
        return
    try:
        db.dsar_requests.insert(
            tenant_id=tenant_id,
            identity_id=identity_id,
            request_type="bulk_erasure",
            status="completed",
            requested_by_identity_id=admin_id,
        )
        db.commit()
    except Exception:
        db.rollback()
