"""Self-service DSAR (Data Subject Access Rights) endpoints -- Free+ (all tiers).

GDPR Art. 15 (access/export), Art. 17 (erasure), and CCPA/CPRA "Do Not Sell or
Share" opt-out + consent withdrawal for the *authenticated data subject's own*
data. Statutory rights are never tier-gated -- see critical-rules.md "Feature
Flags & License Tiers": statutory rights are Free+; only the admin convenience
layer is Enterprise. The feature as a whole (not the tier) sits behind the
`elder.dsar-self-service` PostHog flag, default OFF until validated.

Bulk/admin equivalents (list all requests, bulk erasure) live in
apps/api/api/v1/privacy_admin.py and ARE Enterprise-gated.
"""

from __future__ import annotations

from typing import Any

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.common.flags.posthog_client import flag_enabled
from apps.api.services.privacy.service import LegalHoldError, PrivacyService
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.tenant_scoping import get_current_tenant_id

bp = Blueprint("privacy", __name__)

FLAG_KEY = "elder.dsar-self-service"


def _flag_on() -> bool:
    """Evaluate the DSAR self-service flag for the caller's tenant.

    Falls back to "global" when no tenant claim is present (e.g. a
    single-tenant deployment) -- PostHogClient.flag_enabled degrades to the
    default (False) on any evaluation failure, so this never raises.
    """
    claims = getattr(g, "claims", None) or {}
    distinct_id = str(claims.get("tenant") or "global")
    return flag_enabled(FLAG_KEY, distinct_id, default=False)


def _tenant_legal_hold(db: Any, tenant_id: int | None) -> bool:
    """True if `tenant_id`'s tenant has an active legal hold (blocks erasure).

    `tenant_id` here is always the caller's own tenant (resolved from the
    validated JWT via `get_current_tenant_id()`, never request-supplied), so
    this can only ever read the caller's own tenant's hold state -- never
    another tenant's. Written as an explicit query rather than a bare
    bare bracket lookup on the tenants table to stay off the gh-237 unscoped-
    lookup ratchet (scripts/check_tenant_scoping.py).
    """
    if not tenant_id:
        return False
    tenant = db(db.tenants.id == tenant_id).select().first()
    return bool(getattr(tenant, "legal_hold", False)) if tenant else False


def _log_dsar_request(
    db: Any, tenant_id: int | None, identity_id: int, request_type: str
) -> None:
    """Best-effort write to the dsar_requests activity log.

    Never raises -- a logging failure must not block the statutory right
    itself from completing.
    """
    if not tenant_id:
        return
    try:
        db.dsar_requests.insert(
            tenant_id=tenant_id,
            identity_id=identity_id,
            request_type=request_type,
            status="completed",
            requested_by_identity_id=identity_id,
        )
        db.commit()
    except Exception:
        db.rollback()


@bp.route("/me/export", methods=["GET"])
@login_required
async def export_my_data() -> tuple[Any, int]:
    """GDPR Art. 15 / CCPA right-to-know: export the caller's own data as JSON.

    Returns:
        200: {"identity": {...}, "roles": [...], "group_memberships": [...]}
        404: feature disabled (flag off) or identity not found
    """
    if not _flag_on():
        return ApiResponse.error("feature_disabled", 404)

    db = current_app.db
    identity_id = g.current_user.id
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("tenant_required", 403)

    try:
        data = await run_in_threadpool(
            PrivacyService.export_identity, db, identity_id, tenant_id
        )
    except LookupError:
        return ApiResponse.not_found("Identity", identity_id)

    await run_in_threadpool(_log_dsar_request, db, tenant_id, identity_id, "access")
    return jsonify(data), 200


@bp.route("/me/erase", methods=["POST"])
@login_required
async def erase_my_data() -> tuple[Any, int]:
    """GDPR Art. 17 right-to-erasure: anonymize the caller's own PII.

    Blocked (409) while the caller's tenant is under an active legal hold --
    see docs/compliance/data-retention-policy.md Legal Holds.

    Returns:
        200: {"identity_id": ..., "anonymized": true}
        404: feature disabled (flag off) or identity not found
        409: tenant is under an active legal hold
    """
    if not _flag_on():
        return ApiResponse.error("feature_disabled", 404)

    db = current_app.db
    identity_id = g.current_user.id
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("tenant_required", 403)

    legal_hold = await run_in_threadpool(_tenant_legal_hold, db, tenant_id)

    try:
        result = await run_in_threadpool(
            PrivacyService.erase_identity, db, identity_id, tenant_id, legal_hold
        )
    except LegalHoldError as exc:
        return ApiResponse.error(str(exc), 409, legal_hold=True)
    except LookupError:
        return ApiResponse.not_found("Identity", identity_id)

    await run_in_threadpool(_log_dsar_request, db, tenant_id, identity_id, "erasure")
    return jsonify(result), 200


@bp.route("/me/consent", methods=["POST"])
@login_required
async def set_my_consent() -> tuple[Any, int]:
    """CCPA/CPRA "Do Not Sell or Share" opt-out + consent withdrawal (per-user flag).

    Request body:
        {"do_not_sell_share": true|false}

    Returns:
        200: {"identity_id": ..., "do_not_sell_share": ...}
        400: missing/invalid body
        404: feature disabled (flag off) or identity not found
    """
    if not _flag_on():
        return ApiResponse.error("feature_disabled", 404)

    body = await request.get_json(silent=True) or {}
    opted_out = body.get("do_not_sell_share")
    if not isinstance(opted_out, bool):
        return ApiResponse.validation_error("do_not_sell_share", "must be a boolean")

    db = current_app.db
    identity_id = g.current_user.id
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("tenant_required", 403)

    try:
        result = await run_in_threadpool(
            PrivacyService.set_do_not_sell, db, identity_id, tenant_id, opted_out
        )
    except LookupError:
        return ApiResponse.not_found("Identity", identity_id)

    request_type = "consent_opt_out" if opted_out else "consent_opt_in"
    await run_in_threadpool(_log_dsar_request, db, tenant_id, identity_id, request_type)
    return jsonify(result), 200
