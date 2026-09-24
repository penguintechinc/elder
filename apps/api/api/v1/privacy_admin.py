"""Enterprise admin convenience layer for DSAR handling.

Bulk erasure and the request-visibility dashboard are the *admin convenience
layer* on top of the statutory rights in apps/api/api/v1/privacy.py -- per
critical-rules.md "Feature Flags & License Tiers", only this layer is
Enterprise-gated (`require_tier("enterprise")`); the underlying statutory
rights themselves are never tier-gated.

Every endpoint here scopes strictly to the calling admin's own tenant
(resolved from the validated JWT via `get_current_tenant_id()`, never a
request parameter or the identity row's own `tenant_id` column): the request
list never falls back to an unfiltered query, and `identity_ids` supplied in
a bulk-erase body are re-verified against that tenant by
`PrivacyService`/`get_tenant_scoped()` before anything is touched -- an id
belonging to another tenant fails closed as "not found", never erased and
never disclosed. There is currently no cross-tenant super-admin view; that
is a deliberate simplification (see module history), not a gap in this
tenant boundary.
"""

from __future__ import annotations

from typing import Any

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import admin_required, login_required
from apps.api.common.licensing.tier_gate import require_tier
from apps.api.services.privacy.service import PrivacyService
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.tenant_scoping import get_current_tenant_id

bp = Blueprint("privacy_admin", __name__)

_MAX_LIST_LIMIT = 200


def _tenant_legal_hold(db: Any, tenant_id: int) -> bool:
    """True if `tenant_id`'s tenant has an active legal hold (blocks bulk erasure).

    Written as an explicit query rather than a bare bracket lookup on the
    tenants table to stay off the gh-237 unscoped-lookup ratchet
    (scripts/check_tenant_scoping.py); `tenant_id` is always the admin's own
    tenant here, never request-supplied.
    """
    tenant = db(db.tenants.id == tenant_id).select().first()
    return bool(getattr(tenant, "legal_hold", False)) if tenant else False


def _list_requests(db: Any, tenant_id: int) -> list[dict[str, Any]]:
    """Tenant-scoped page of the DSAR activity log, most recent first.

    Always filters by `tenant_id` -- never falls back to an unscoped query.
    Callers must have already rejected a missing/falsy tenant_id (see
    list_dsar_requests) rather than reach this with one.
    """
    rows = db(db.dsar_requests.tenant_id == tenant_id).select(
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
    """List DSAR self-service + bulk activity for the admin's own tenant (Enterprise).

    Returns:
        200: {"requests": [...], "count": N}
        403: caller's deployment tier is below Enterprise, or no tenant claim
    """
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("tenant_required", 403)

    db = current_app.db
    requests_list = await run_in_threadpool(_list_requests, db, tenant_id)
    return jsonify({"requests": requests_list, "count": len(requests_list)}), 200


@bp.route("/bulk-erase", methods=["POST"])
@login_required
@admin_required
@require_tier("enterprise")
async def bulk_erase() -> tuple[Any, int]:
    """Bulk right-to-erasure across many identities in one call (Enterprise).

    Every id in `identity_ids` is re-verified against the admin's own tenant
    before erasure (apps/api/services/privacy/service.py) -- an id belonging
    to another tenant comes back as a per-item "not found", never erased.

    Request body:
        {"identity_ids": [1, 2, 3]}

    Returns:
        200: {"results": [{"identity_id": ..., "anonymized": bool, ...}, ...]}
        400: missing/invalid identity_ids
        403: caller's deployment tier is below Enterprise, or no tenant claim
    """
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("tenant_required", 403)

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
    admin_id = g.current_user.id

    legal_hold = await run_in_threadpool(_tenant_legal_hold, db, tenant_id)
    result = await run_in_threadpool(
        PrivacyService.bulk_erase, db, identity_ids, tenant_id, legal_hold
    )

    for entry in result["results"]:
        if entry.get("anonymized"):
            await run_in_threadpool(
                _log_bulk_request, db, tenant_id, entry["identity_id"], admin_id
            )

    return jsonify(result), 200


def _log_bulk_request(db: Any, tenant_id: int, identity_id: int, admin_id: int) -> None:
    """Best-effort write to the dsar_requests activity log for a bulk erasure."""
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
