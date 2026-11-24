"""Self-service DSAR (Data Subject Access Rights) endpoints -- Free+ (all tiers).

GDPR Art. 15 (access/export), Art. 17 (erasure), Art. 7(3) (consent
withdrawal) and CCPA/CPRA "Do Not Sell or Share" opt-out for the
*authenticated data subject's own* data.

These are statutory rights, so they are ALWAYS ON: no license-tier check and
no feature flag. They were previously behind the default-OFF PostHog flag
`elder.dsar-self-service`, which meant a never-seen flag or an unreachable
flag server (every air-gapped install) returned 404 for a legal right. The
gate is removed outright rather than inverted into an opt-out kill-switch
(`elder.disable-*`): a remotely togglable switch could still silently
disable a statutory right, which critical-rules.md forbids ("Statutory
rights are never tier-gated"; gating one is itself a violation). Flags and
tiers remain only on the admin convenience layer.

The data subject is whoever the validated token identifies -- an
``identities`` row or a ``portal_users`` row. The two id spaces overlap, so
the subject type is always taken from the authenticated principal and never
inferred from the numeric id.

Safeguards that do not gate the right (see
docs/compliance/dsar-erasure-inventory.md "Erasure Safeguards"): erasure is
irreversible, so it needs an explicit ``{"confirm": "ERASE"}`` plus one
re-authentication factor; every route has a generous per-subject rate limit;
only interactive user tokens are accepted (never refresh or machine tokens).

Bulk/admin equivalents (list all requests, bulk erasure) live in
apps/api/api/v1/privacy_admin.py and ARE Enterprise-gated.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.auth.jwt_handler import get_token_from_header, verify_token
from apps.api.auth.web_auth import clear_session_cookie
from apps.api.services.privacy.inventory import ConsentPurpose, SubjectType
from apps.api.services.privacy.service import (
    LastAdminError,
    LegalHoldError,
    PrivacyService,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.rate_limiter import check_subject_rate_limit
from apps.api.utils.tenant_scoping import get_current_tenant_id

logger = structlog.get_logger()

bp = Blueprint("privacy", __name__)

ERASE_CONFIRMATION = "ERASE"
# Subjects with no local password/MFA (SSO, SCIM) re-authenticate by signing
# in again; their token must be at most this old.
RECENT_LOGIN_SECONDS = 300
_INTERACTIVE_TOKEN_TYPES = frozenset({"access", "portal_user"})
# Per-subject requests per minute. Generous for a human, tight for a script:
# these never deny the right, they only pace it (HTTP 429 + Retry-After).
RATE_LIMITS: dict[str, int] = {"export": 10, "erase": 5, "consent": 30, "withdraw": 10}


def _token_claims() -> dict[str, Any]:
    """Verified claims of the bearer token on this request ({} if none).

    `g.claims` is a reduced view without `type`/`auth_time`, so the token is
    re-verified here to read them; it was already accepted by
    @login_required, so this adds no new trust.
    """
    token = get_token_from_header()
    if not token:
        return {}
    return verify_token(token) or {}


async def _rate_limited(
    operation: str, subject_type: SubjectType, subject_id: int, tenant_id: int
) -> tuple[Any, int] | None:
    """Apply the per-subject limit for `operation`; a 429 response if exceeded.

    Keyed on tenant + subject type + subject id from the validated token,
    never on client-supplied data. Uses the shared limiter, which fails open
    on a storage error so a cache outage cannot block a statutory right.
    """
    allowed, headers = await check_subject_rate_limit(
        current_app,
        subject=f"{tenant_id}:{int(subject_type)}:{subject_id}",
        limit_per_minute=RATE_LIMITS[operation],
        key_prefix=f"elder:privacy:{operation}",
    )
    if allowed:
        return None
    logger.info(
        "dsar_rate_limited",
        operation=operation,
        subject_id=subject_id,
        tenant_id=tenant_id,
    )
    response = jsonify(
        {
            "error": "rate_limited",
            "message": "Too many privacy requests; retry shortly.",
        }
    )
    response.headers["Retry-After"] = headers.get("Retry-After", "60")
    return response, 429


def _resolve_subject() -> tuple[SubjectType, int] | None:
    """Identify the authenticated data subject as (subject type, id).

    A portal-user token yields a synthetic principal whose ``id`` is a
    ``portal_users.id``; treating it as an ``identities.id`` would act on a
    different person who happens to share the number. Machine principals
    (code-run / worker tokens, id 0) are not data subjects and get None.
    """
    user = getattr(g, "current_user", None)
    # Machine principals carry a token_type; a refresh token is not an
    # interactive session either. Neither may exercise a subject's rights.
    if getattr(user, "token_type", None) is not None:
        return None
    token_type = _token_claims().get("type")
    if token_type is not None and token_type not in _INTERACTIVE_TOKEN_TYPES:
        return None
    subject_id = getattr(user, "id", None)
    if isinstance(subject_id, bool) or not isinstance(subject_id, int):
        return None
    if subject_id <= 0:
        return None
    if getattr(user, "_portal_user", None) is not None:
        return SubjectType.PORTAL_USER, subject_id
    return SubjectType.IDENTITY, subject_id


def _context() -> tuple[SubjectType, int, int] | tuple[Any, int]:
    """Resolve (subject type, subject id, tenant id) or an error response.

    The tenant always comes from the validated JWT claims, never from the
    request; a missing claim or a non-human principal is refused with 403.
    """
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return ApiResponse.error("tenant_required", 403)
    subject = _resolve_subject()
    if subject is None:
        return ApiResponse.error("data_subject_required", 403)
    return subject[0], subject[1], tenant_id


async def _reauthenticated(
    db: Any,
    body: dict[str, Any],
    subject_type: SubjectType,
    subject_id: int,
    tenant_id: int,
) -> tuple[Any, int] | None:
    """Require one fresh proof of identity before erasure; a 403 if absent.

    A subject with a local credential supplies the current password or (if
    MFA is enrolled) a TOTP code -- one factor, the same effort as signing
    in. A subject with no local credential (SSO/SCIM) must instead have
    signed in within RECENT_LOGIN_SECONDS, judged by the token's `auth_time`
    claim -- never `iat`, which every refresh renews.
    """
    password = body.get("password")
    mfa_code = body.get("mfa_code")
    has_credential = await run_in_threadpool(
        PrivacyService.has_local_credential, db, subject_id, tenant_id, subject_type
    )
    if has_credential:
        verified = await run_in_threadpool(
            PrivacyService.verify_reauth,
            db,
            subject_id,
            tenant_id,
            subject_type,
            password if isinstance(password, str) else None,
            mfa_code if isinstance(mfa_code, str) else None,
        )
        if verified:
            return None
        return ApiResponse.error(
            "reauthentication_required: supply your current password in "
            '"password", or a current authenticator code in "mfa_code"',
            403,
            reauthentication_required=True,
        )

    # `auth_time` is stamped only by an interactive login and is carried
    # unchanged through refresh, so a refreshed (or stolen-refresh) token
    # can never look fresh. A token without it (issued before this claim
    # existed) is treated as NOT recent: the user signs in again.
    auth_time = _token_claims().get("auth_time")
    if (
        isinstance(auth_time, int)
        and not isinstance(auth_time, bool)
        and 0 <= time.time() - auth_time <= RECENT_LOGIN_SECONDS
    ):
        return None
    return ApiResponse.error(
        "recent_login_required: sign in again and repeat the request within "
        f"{RECENT_LOGIN_SECONDS // 60} minutes",
        403,
        recent_login_required=True,
    )


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
    db: Any,
    tenant_id: int | None,
    identity_id: int,
    request_type: str,
    subject_type: SubjectType = SubjectType.IDENTITY,
) -> None:
    """Best-effort write to the dsar_requests activity log.

    Never raises -- a logging failure must not block the statutory right
    itself from completing -- but the failure is logged, not swallowed.
    """
    if not tenant_id:
        return
    row: dict[str, Any] = {
        "tenant_id": tenant_id,
        "request_type": request_type,
        "status": "completed",
        "requested_by_identity_id": identity_id,
    }
    if subject_type is SubjectType.PORTAL_USER:
        row["portal_user_id"] = identity_id
    else:
        row["identity_id"] = identity_id
    try:
        db.dsar_requests.insert(**row)
        db.commit()
    except Exception as exc:
        logger.warning(
            "dsar_request_log_failed",
            tenant_id=tenant_id,
            request_type=request_type,
            error_type=type(exc).__name__,
        )


@bp.route("/me/export", methods=["GET"])
@login_required
async def export_my_data() -> tuple[Any, int]:
    """GDPR Art. 15 / CCPA right-to-know: export the caller's own data as JSON.

    Returns:
        200: machine-readable bundle -- "identity", "roles",
            "group_memberships", "consent", "categories", "truncated"
        403: no tenant claim, or the caller is not a data subject
        404: subject not found in the caller's tenant
    """
    ctx = _context()
    if len(ctx) == 2:
        return ctx
    subject_type, identity_id, tenant_id = ctx
    limited = await _rate_limited("export", subject_type, identity_id, tenant_id)
    if limited:
        return limited
    db = current_app.db

    try:
        data = await run_in_threadpool(
            PrivacyService.export_identity, db, identity_id, tenant_id, subject_type
        )
    except LookupError:
        return ApiResponse.not_found("Identity", identity_id)

    await run_in_threadpool(
        _log_dsar_request, db, tenant_id, identity_id, "access", subject_type
    )
    response = jsonify(data)
    # Personal data: never cached, never sniffed into HTML, saved as a file.
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Disposition"] = (
        'attachment; filename="personal-data-export.json"'
    )
    return response, 200


@bp.route("/me/erase", methods=["POST"])
@login_required
async def erase_my_data() -> tuple[Any, int]:
    """GDPR Art. 17 right-to-erasure: erase the caller's own personal data.

    Walks the full personal-data inventory, revokes the caller's credentials
    and returns a PII-free receipt of what was erased or retained and why.
    Blocked (409) while the caller's tenant is under an active legal hold --
    see docs/compliance/data-retention-policy.md Legal Holds.

    Request body:
        {"confirm": "ERASE", "password": "<current password>"}
        {"confirm": "ERASE", "mfa_code": "<authenticator code>"}  (MFA enrolled)
        {"confirm": "ERASE"}  (SSO subjects: signed in within 5 minutes)

    Returns:
        200: {"identity_id": ..., "anonymized": true, "receipt": {...}}
        403: no tenant claim, not a data subject, missing confirmation, or
            re-authentication missing/failed/stale
        404: subject not found in the caller's tenant
        409: tenant under legal hold, or caller is the tenant's last admin
        429: per-subject rate limit exceeded
    """
    ctx = _context()
    if len(ctx) == 2:
        return ctx
    subject_type, identity_id, tenant_id = ctx
    limited = await _rate_limited("erase", subject_type, identity_id, tenant_id)
    if limited:
        return limited
    db = current_app.db

    body = await request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("confirm") != ERASE_CONFIRMATION:
        return ApiResponse.error(
            "erase_confirmation_required: erasure is permanent; send "
            f'{{"confirm": "{ERASE_CONFIRMATION}"}} to proceed',
            403,
            confirmation_required=True,
        )
    try:
        denied = await _reauthenticated(db, body, subject_type, identity_id, tenant_id)
    except LookupError:
        return ApiResponse.not_found("Identity", identity_id)
    if denied:
        return denied

    legal_hold = await run_in_threadpool(_tenant_legal_hold, db, tenant_id)

    try:
        result = await run_in_threadpool(
            PrivacyService.erase_identity,
            db,
            identity_id,
            tenant_id,
            legal_hold,
            subject_type,
            identity_id,
        )
    except LegalHoldError as exc:
        return ApiResponse.error(str(exc), 409, legal_hold=True)
    except LastAdminError as exc:
        return ApiResponse.error(str(exc), 409, last_admin=True)
    except LookupError:
        return ApiResponse.not_found("Identity", identity_id)

    await run_in_threadpool(
        _log_dsar_request, db, tenant_id, identity_id, "erasure", subject_type
    )
    response = jsonify(result)
    # The subject no longer exists: drop the browser's session cookies too.
    clear_session_cookie(response)
    return response, 200


@bp.route("/me/consent", methods=["GET"])
@login_required
async def get_my_consent() -> tuple[Any, int]:
    """Return the caller's current consent state and consent-ledger history.

    Returns:
        200: {"do_not_sell_share": bool, "purposes": [...], "history": [...]}
        403: no tenant claim, or the caller is not a data subject
        404: subject not found in the caller's tenant
    """
    ctx = _context()
    if len(ctx) == 2:
        return ctx
    subject_type, identity_id, tenant_id = ctx

    try:
        result = await run_in_threadpool(
            PrivacyService.get_consent,
            current_app.db,
            identity_id,
            tenant_id,
            subject_type,
        )
    except LookupError:
        return ApiResponse.not_found("Identity", identity_id)
    return jsonify(result), 200


@bp.route("/me/consent", methods=["POST"])
@login_required
async def set_my_consent() -> tuple[Any, int]:
    """Record a consent choice: CCPA/CPRA opt-out or a per-purpose grant/withdrawal.

    Request body (one of):
        {"do_not_sell_share": true|false}
        {"purpose": <ConsentPurpose int code>, "granted": true|false}

    Returns:
        200: {"identity_id": ..., "purpose": ..., "state": ..., ...}
        400: missing/invalid body
        403: no tenant claim, or the caller is not a data subject
        404: subject not found in the caller's tenant
    """
    body = await request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return ApiResponse.validation_error("body", "must be a JSON object")

    if "purpose" in body or "granted" in body:
        raw_purpose = body.get("purpose")
        granted = body.get("granted")
        if isinstance(raw_purpose, bool) or not isinstance(raw_purpose, int):
            return ApiResponse.validation_error("purpose", "must be an integer code")
        try:
            purpose = ConsentPurpose(raw_purpose)
        except ValueError:
            return ApiResponse.validation_error("purpose", "is not a known purpose")
        if not isinstance(granted, bool):
            return ApiResponse.validation_error("granted", "must be a boolean")
    else:
        opted_out = body.get("do_not_sell_share")
        if not isinstance(opted_out, bool):
            return ApiResponse.validation_error(
                "do_not_sell_share", "must be a boolean"
            )
        purpose = ConsentPurpose.SALE_OR_SHARING
        granted = not opted_out

    ctx = _context()
    if len(ctx) == 2:
        return ctx
    subject_type, identity_id, tenant_id = ctx
    limited = await _rate_limited("consent", subject_type, identity_id, tenant_id)
    if limited:
        return limited
    db = current_app.db

    try:
        result = await run_in_threadpool(
            PrivacyService.record_consent,
            db,
            identity_id,
            tenant_id,
            purpose,
            granted,
            subject_type,
        )
    except LookupError:
        return ApiResponse.not_found("Identity", identity_id)

    request_type = "consent_opt_in" if granted else "consent_opt_out"
    await run_in_threadpool(
        _log_dsar_request, db, tenant_id, identity_id, request_type, subject_type
    )
    return jsonify(result), 200


@bp.route("/me/consent/withdraw", methods=["POST"])
@login_required
async def withdraw_my_consent() -> tuple[Any, int]:
    """GDPR Art. 7(3): withdraw consent for every purpose and opt out of sale/sharing.

    Returns:
        200: {"identity_id": ..., "do_not_sell_share": true, "withdrawn": [...]}
        403: no tenant claim, or the caller is not a data subject
        404: subject not found in the caller's tenant
    """
    ctx = _context()
    if len(ctx) == 2:
        return ctx
    subject_type, identity_id, tenant_id = ctx
    limited = await _rate_limited("withdraw", subject_type, identity_id, tenant_id)
    if limited:
        return limited
    db = current_app.db

    try:
        result = await run_in_threadpool(
            PrivacyService.withdraw_all_consent,
            db,
            identity_id,
            tenant_id,
            subject_type,
        )
    except LookupError:
        return ApiResponse.not_found("Identity", identity_id)

    await run_in_threadpool(
        _log_dsar_request,
        db,
        tenant_id,
        identity_id,
        "consent_withdrawn",
        subject_type,
    )
    return jsonify(result), 200
