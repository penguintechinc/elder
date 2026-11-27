"""Tenant management API endpoints for v2.2.0 Enterprise Edition.

Provides REST endpoints for tenant CRUD operations, configuration,
and usage statistics for the Super Admin Console.
"""

# flake8: noqa: E501

import secrets
from datetime import UTC, datetime, timezone
from typing import Any, Literal, Optional

import redis
import structlog
from penguin_libs.pydantic import Name255, RequestModel, SlugStr
from pydantic import Field, ValidationError
from quart import Blueprint, current_app, jsonify, request

from apps.api.api.v1.portal_auth import portal_token_required
from apps.api.common.licensing.enforce import check_limit
from apps.api.common.licensing.tier_gate import get_tier
from apps.api.services.portal_auth import (
    DEFAULT_MFA_GRACE_PERIOD_DAYS,
    DEFAULT_MFA_PROVIDER,
    PortalAuthService,
    get_tenant_mfa_policy,
    is_tenant_mfa_enforced,
    parse_tenant_settings,
    user_has_mfa_enrolled,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.email import (
    EmailDeliveryError,
    EmailNotConfiguredError,
    is_smtp_configured,
)
from apps.api.utils.onboarding_emails import send_admin_invite_email
from apps.api.utils.onboarding_tokens import (
    ADMIN_INVITE_TTL_SECONDS,
    PURPOSE_ADMIN_INVITE,
    issue_onboarding_token,
)
from apps.api.utils.signup_policy import (
    normalize_signup_domains,
    resolve_tenant_signup_policy,
)
from apps.api.utils.village_id import mint_village_id

logger = structlog.get_logger(__name__)
bp = Blueprint("tenants", __name__)


# Pydantic Models for request validation
class CreateTenantRequest(RequestModel):
    """Validation model for creating a new tenant."""

    name: Name255
    slug: SlugStr
    domain: str | None = Field(default=None, max_length=255)
    subscription_tier: str = Field(default="community", max_length=50)
    license_key: str | None = Field(default=None, max_length=500)
    settings: dict | None = Field(default=None)
    feature_flags: dict | None = Field(default=None)
    data_retention_days: int = Field(default=90, ge=1, le=36500)
    storage_quota_gb: int = Field(default=10, ge=1, le=1000000)


class UpdateTenantRequest(RequestModel):
    """Validation model for updating a tenant."""

    name: Name255 | None = None
    domain: str | None = Field(default=None, max_length=255)
    slug: SlugStr | None = None
    subscription_tier: str | None = Field(default=None, max_length=50)
    license_key: str | None = Field(default=None, max_length=500)
    settings: dict | None = None
    feature_flags: dict | None = None
    data_retention_days: int | None = Field(default=None, ge=1, le=36500)
    storage_quota_gb: int | None = Field(default=None, ge=1, le=1000000)
    is_active: bool | None = None


class UpdateMfaPolicyRequest(RequestModel):
    """Validation model for updating tenant MFA enforcement policy."""

    enforced: bool
    provider: Literal["google_authenticator"] = DEFAULT_MFA_PROVIDER
    grace_period_days: int | None = Field(default=None, ge=0, le=90)


class UpdateSignupPolicyRequest(RequestModel):
    """Validation model for updating tenant signup domain and verification policy."""

    allowed_signup_domains: list[str] | str
    require_email_verification: bool | None = None


class CreateTenantUserRequest(RequestModel):
    """Validation model for creating/inviting a portal user in a tenant."""

    email: str = Field(..., min_length=3, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    tenant_role: Literal["reader", "maintainer", "admin"] = "reader"
    global_role: Literal["admin", "support"] | None = None
    send_invite: bool = True


def _is_mfa_policy_admin(portal_user: dict) -> bool:
    """Return True if the authenticated portal user can administer tenant MFA policy."""
    global_role = portal_user.get("global_role")
    tenant_role = portal_user.get("tenant_role")
    scopes = str(portal_user.get("scope", "")).split()
    return (
        global_role in ("admin", "super_admin", "superadmin")
        or tenant_role == "admin"
        or "*:admin" in scopes
        or "settings:write" in scopes
    )


def global_admin_required(f):
    """Decorator to require global admin permission.

    Sync/async-aware -- see portal_token_required's docstring
    (apps/api/api/v1/portal_auth.py) for why this matters: a plain `def
    decorated` wrapping an `async def` view (e.g. create_tenant) returns an
    un-awaited coroutine as the "response", which Quart rejects.
    """
    from functools import wraps
    from inspect import iscoroutinefunction

    if iscoroutinefunction(f):

        @wraps(f)
        async def decorated(*args, **kwargs):
            if request.portal_user.get("global_role") != "admin":
                return ApiResponse.forbidden("Global admin permission required")
            return await f(*args, **kwargs)

    else:

        @wraps(f)
        def decorated(*args, **kwargs):
            if request.portal_user.get("global_role") != "admin":
                return ApiResponse.forbidden("Global admin permission required")
            return f(*args, **kwargs)

    return decorated


@bp.route("", methods=["GET"])
@portal_token_required
def list_tenants():
    """List all tenants.

    Requires global admin or support role.

    Query params:
        is_active: bool - Filter by active status
        subscription_tier: str - Filter by tier

    Returns:
        List of tenants
    """
    # Check permissions
    if request.portal_user.get("global_role") not in ["admin", "support"]:
        return ApiResponse.forbidden("Global admin or support role required")

    db = current_app.db
    query = db.tenants.id > 0

    if request.args.get("is_active") is not None:
        is_active = request.args.get("is_active").lower() == "true"
        query &= db.tenants.is_active == is_active

    if request.args.get("subscription_tier"):
        query &= db.tenants.subscription_tier == request.args.get("subscription_tier")

    tenants = db(query).select(orderby=db.tenants.name)

    return (
        jsonify(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "slug": t.slug,
                    "domain": t.domain,
                    "subscription_tier": t.subscription_tier,
                    "is_active": t.is_active,
                    "data_retention_days": t.data_retention_days,
                    "storage_quota_gb": t.storage_quota_gb,
                    "village_id": t.village_id,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tenants
            ]
        ),
        200,
    )


@bp.route("/<int:tenant_id>", methods=["GET"])
@portal_token_required
def get_tenant(tenant_id):
    """Get a tenant by ID.

    Args:
        tenant_id: Tenant ID

    Returns:
        Tenant details with usage stats
    """
    # Check permissions - admins or support can view any, tenant admins only their own
    if request.portal_user.get("global_role") not in ["admin", "support"]:
        if (
            request.portal_user.get("tenant_id") != tenant_id
            or request.portal_user.get("tenant_role") != "admin"
        ):
            return ApiResponse.forbidden("Permission denied")

    db = current_app.db
    # The permission check above already restricts non-global-admin/support
    # callers to tenant_id == their own portal_user.tenant_id -- tenants is
    # the top-level tenant table, so there is no "outer" tenant to
    # additionally scope this by.
    tenant = db.tenants[tenant_id]  # tenant-scope-exempt: see above
    if not tenant:
        return ApiResponse.error("Tenant not found", 404)

    # Get usage statistics
    org_count = db(db.organizations.tenant_id == tenant_id).count()
    user_count = db(db.portal_users.tenant_id == tenant_id).count()
    identity_count = db(db.identities.tenant_id == tenant_id).count()

    return (
        jsonify(
            {
                "id": tenant.id,
                "name": tenant.name,
                "slug": tenant.slug,
                "domain": tenant.domain,
                "subscription_tier": tenant.subscription_tier,
                "license_key": (
                    tenant.license_key[:20] + "..." if tenant.license_key else None
                ),
                "settings": tenant.settings,
                "feature_flags": tenant.feature_flags,
                "data_retention_days": tenant.data_retention_days,
                "storage_quota_gb": tenant.storage_quota_gb,
                "is_active": tenant.is_active,
                "village_id": tenant.village_id,
                "created_at": (
                    tenant.created_at.isoformat() if tenant.created_at else None
                ),
                "updated_at": (
                    tenant.updated_at.isoformat() if tenant.updated_at else None
                ),
                "usage": {
                    "organizations": org_count,
                    "portal_users": user_count,
                    "identities": identity_count,
                },
            }
        ),
        200,
    )


@bp.route("", methods=["POST"])
@portal_token_required
@global_admin_required
async def create_tenant():
    """Create a new tenant.

    Requires global admin.

    Request body:
        name: str - Tenant name (1-255 chars)
        slug: str - URL-friendly slug
        domain: str (optional) - Custom domain
        subscription_tier: str - community/professional/enterprise
        license_key: str (optional) - License key
        settings: dict (optional) - Tenant settings
        feature_flags: dict (optional) - Feature flags
        data_retention_days: int - Audit log retention (1-36500 days)
        storage_quota_gb: int - Storage quota (1-1000000 GB)

    Returns:
        Created tenant
    """
    try:
        body = CreateTenantRequest.model_validate(await request.get_json())
    except ValidationError as e:
        errors = [
            {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
            for err in e.errors()
        ]
        return jsonify({"error": "Validation failed", "details": errors}), 400

    # Tenants are counted globally (max_tenants), not per-tenant -- see
    # docs/superpowers/plans/2026-08-20-license-enforcement-phase2.md Task 5.
    blocked = await check_limit("tenant", None)
    if blocked is not None:
        return blocked

    db = current_app.db
    redis_client = getattr(current_app, "redis_client", None)

    def inner():
        # Check if slug already exists
        existing = db(db.tenants.slug == body.slug).select().first()
        if existing:
            return None, "Slug already exists", 400

        now = datetime.now(UTC)
        tenant_id = db.tenants.insert(
            name=body.name,
            slug=body.slug,
            domain=body.domain,
            subscription_tier=body.subscription_tier,
            license_key=body.license_key,
            settings=body.settings,
            feature_flags=body.feature_flags,
            data_retention_days=body.data_retention_days,
            storage_quota_gb=body.storage_quota_gb,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Generate village_id for the tenant using its own ID (with CSPRNG fallback)
        village_id = mint_village_id(tenant_id, redis_client)

        # Update tenant with village_id
        db(db.tenants.id == tenant_id).update(village_id=village_id)
        db.commit()

        return (
            {
                "id": tenant_id,
                "name": body.name,
                "slug": body.slug,
                "village_id": village_id,
            },
            None,
            None,
        )

    result, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), 201


@bp.route("/<int:tenant_id>", methods=["PUT"])
@portal_token_required
async def update_tenant(tenant_id):
    """Update a tenant.

    Args:
        tenant_id: Tenant ID

    Returns:
        Updated tenant
    """
    # Check permissions - global admin or tenant admin for their own tenant
    is_global_admin = request.portal_user.get("global_role") == "admin"
    is_tenant_admin = (
        request.portal_user.get("tenant_id") == tenant_id
        and request.portal_user.get("tenant_role") == "admin"
    )

    if not is_global_admin and not is_tenant_admin:
        return ApiResponse.forbidden("Permission denied")

    db = current_app.db
    # The permission check above already restricts non-global-admin callers
    # to tenant_id == their own tenant.
    tenant = db.tenants[tenant_id]  # tenant-scope-exempt: see above
    if not tenant:
        return ApiResponse.error("Tenant not found", 404)

    try:
        body = UpdateTenantRequest.model_validate(await request.get_json())
    except ValidationError as e:
        errors = [
            {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
            for err in e.errors()
        ]
        return jsonify({"error": "Validation failed", "details": errors}), 400

    # Fields tenant admins can update
    tenant_admin_fields = {"name", "domain", "settings", "feature_flags"}

    # Fields only global admins can update
    global_admin_fields = {
        "slug",
        "subscription_tier",
        "license_key",
        "data_retention_days",
        "storage_quota_gb",
        "is_active",
    }

    updates = {}

    # Collect updates from validated body
    for field_name, field_value in body.model_dump(exclude_none=True).items():
        if field_name in tenant_admin_fields:
            updates[field_name] = field_value
        elif field_name in global_admin_fields and is_global_admin:
            updates[field_name] = field_value

    if updates:
        db(db.tenants.id == tenant.id).update(**updates)

    return jsonify({"id": tenant_id, "updated": True}), 200


@bp.route("/<int:tenant_id>", methods=["DELETE"])
@portal_token_required
@global_admin_required
def delete_tenant(tenant_id):
    """Delete a tenant.

    Requires global admin. Cannot delete system tenant (id=1).

    Args:
        tenant_id: Tenant ID

    Returns:
        Success status
    """
    if tenant_id == 1:
        return ApiResponse.bad_request("Cannot delete system tenant")

    db = current_app.db
    # @global_admin_required above already restricts this whole route to
    # global admins.
    tenant = db.tenants[tenant_id]  # tenant-scope-exempt: see above
    if not tenant:
        return ApiResponse.error("Tenant not found", 404)

    # Soft delete - deactivate instead
    db(db.tenants.id == tenant.id).update(is_active=False)

    return jsonify({"deleted": True, "tenant_id": tenant_id}), 200


@bp.route("/<int:tenant_id>/users", methods=["GET"])
@portal_token_required
def list_tenant_users(tenant_id):
    """List all portal users in a tenant.

    Args:
        tenant_id: Tenant ID

    Returns:
        List of portal users
    """
    # Check permissions
    if request.portal_user.get("global_role") not in ["admin", "support"]:
        if (
            request.portal_user.get("tenant_id") != tenant_id
            or request.portal_user.get("tenant_role") != "admin"
        ):
            return ApiResponse.forbidden("Permission denied")

    db = current_app.db
    users = db(db.portal_users.tenant_id == tenant_id).select(
        orderby=db.portal_users.email
    )

    return (
        jsonify(
            [
                {
                    "id": u.id,
                    "email": u.email,
                    "full_name": u.full_name,
                    "tenant_role": u.tenant_role,
                    "global_role": u.global_role,
                    "is_active": u.is_active,
                    "email_verified": u.email_verified,
                    "mfa_enabled": bool(u.mfa_secret),
                    "last_login_at": (
                        u.last_login_at.isoformat() if u.last_login_at else None
                    ),
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in users
            ]
        ),
        200,
    )


@bp.route("/<int:tenant_id>/users", methods=["POST"])
@portal_token_required
async def create_tenant_user(tenant_id):
    """Create a portal user in a tenant and optionally send a one-time onboarding invite.

    Requires global admin or tenant admin for the target tenant.

    Args:
        tenant_id: Tenant ID

    Request body:
        email: str - User email address
        full_name: str (optional) - Full name
        tenant_role: str - Role within tenant (reader/maintainer/admin)
        global_role: str (optional, global admin only) - Global role (admin/support)
        send_invite: bool (default true) - Send one-time setup email

    Returns:
        201 with created user metadata and ``invite_sent`` status
    """
    is_global_admin = request.portal_user.get("global_role") in (
        "admin",
        "super_admin",
        "superadmin",
    )
    is_tenant_admin = request.portal_user.get(
        "tenant_id"
    ) == tenant_id and _is_mfa_policy_admin(request.portal_user)
    if not is_global_admin and not is_tenant_admin:
        return ApiResponse.forbidden("Admin permission required")

    payload = await request.get_json(silent=True)
    if payload is None:
        return ApiResponse.bad_request("Request body must be JSON")

    try:
        body = CreateTenantUserRequest.model_validate(payload)
    except ValidationError as e:
        errors = [
            {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
            for err in e.errors()
        ]
        return jsonify({"error": "Validation failed", "details": errors}), 400

    email = body.email.strip().lower()
    if "@" not in email or not email.split("@")[0] or "." not in email.split("@")[-1]:
        return ApiResponse.bad_request("Valid email address is required")

    if body.global_role is not None and not is_global_admin:
        return ApiResponse.forbidden("Only a global admin can assign global_role")

    db = current_app.db
    # Permission check above restricts non-global-admins to their own tenant_id.
    tenant = db.tenants[tenant_id]  # tenant-scope-exempt: see above
    if not tenant or not tenant.is_active:
        return ApiResponse.error("Tenant not found", 404)

    # Create portal user with an unusable random password; user sets their real
    # password via the one-time /api/v1/portal-auth/invite/complete flow.
    result = await PortalAuthService.create_portal_user(
        tenant_id=tenant_id,
        email=email,
        password=secrets.token_urlsafe(48),
        full_name=body.full_name,
        tenant_role=body.tenant_role,
        global_role=body.global_role if is_global_admin else None,
    )
    if "error" in result:
        status_code = result.get("status_code", 400)
        return jsonify({"error": result["error"]}), status_code

    mfa_enforced = is_tenant_mfa_enforced(tenant_id, db=db)
    invite_sent = False

    if body.send_invite:
        invite_token = issue_onboarding_token(
            PURPOSE_ADMIN_INVITE,
            {
                "user_id": int(result["id"]),
                "tenant_id": int(tenant_id),
            },
            ttl_seconds=ADMIN_INVITE_TTL_SECONDS,
        )
        if is_smtp_configured():
            try:
                await send_admin_invite_email(
                    to_email=email,
                    token=invite_token,
                    full_name=body.full_name,
                    tenant_name=getattr(tenant, "name", None),
                    mfa_enforced=mfa_enforced,
                )
                invite_sent = True
            except (EmailNotConfiguredError, EmailDeliveryError, ValueError) as exc:
                logger.warning(
                    "tenant_user_invite_email_failed",
                    tenant_id=tenant_id,
                    portal_user_id=result["id"],
                    error_type=type(exc).__name__,
                )

    return (
        jsonify(
            {
                "id": result["id"],
                "email": result["email"],
                "full_name": body.full_name,
                "tenant_id": tenant_id,
                "tenant_role": result["tenant_role"],
                "global_role": result["global_role"],
                "is_active": True,
                "email_verified": False,
                "mfa_enforced": mfa_enforced,
                "invite_sent": invite_sent,
            }
        ),
        201,
    )


@bp.route("/<int:tenant_id>/users/<int:user_id>", methods=["PUT"])
@portal_token_required
async def update_tenant_user(tenant_id, user_id):
    """Update a portal user in a tenant.

    Args:
        tenant_id: Tenant ID
        user_id: Portal user ID

    Returns:
        Updated user
    """
    # Check permissions
    is_global_admin = request.portal_user.get("global_role") == "admin"
    is_tenant_admin = (
        request.portal_user.get("tenant_id") == tenant_id
        and request.portal_user.get("tenant_role") == "admin"
    )

    if not is_global_admin and not is_tenant_admin:
        return ApiResponse.forbidden("Permission denied")

    db = current_app.db
    user = (
        db((db.portal_users.id == user_id) & (db.portal_users.tenant_id == tenant_id))
        .select()
        .first()
    )

    if not user:
        return ApiResponse.error("User not found", 404)

    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("No data provided")

    # Allowed update fields
    allowed_fields = {"full_name", "tenant_role", "is_active"}
    if is_global_admin:
        allowed_fields.add("global_role")

    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if updates:
        db(db.portal_users.id == user.id).update(**updates)

    return jsonify({"id": user_id, "updated": True}), 200


@bp.route("/<int:tenant_id>/users/<int:user_id>", methods=["DELETE"])
@portal_token_required
def delete_tenant_user(tenant_id, user_id):
    """Delete a portal user from a tenant.

    Args:
        tenant_id: Tenant ID
        user_id: Portal user ID

    Returns:
        Success status
    """
    # Check permissions
    is_global_admin = request.portal_user.get("global_role") == "admin"
    is_tenant_admin = (
        request.portal_user.get("tenant_id") == tenant_id
        and request.portal_user.get("tenant_role") == "admin"
    )

    if not is_global_admin and not is_tenant_admin:
        return ApiResponse.forbidden("Permission denied")

    db = current_app.db
    user = (
        db((db.portal_users.id == user_id) & (db.portal_users.tenant_id == tenant_id))
        .select()
        .first()
    )

    if not user:
        return ApiResponse.error("User not found", 404)

    # Soft delete
    db(db.portal_users.id == user.id).update(is_active=False)

    return jsonify({"deleted": True, "user_id": user_id}), 200


@bp.route("/<int:tenant_id>/stats", methods=["GET"])
@portal_token_required
def get_tenant_stats(tenant_id):
    """Get usage statistics for a tenant.

    Args:
        tenant_id: Tenant ID

    Returns:
        Detailed usage statistics
    """
    # Check permissions
    if request.portal_user.get("global_role") not in ["admin", "support"]:
        if (
            request.portal_user.get("tenant_id") != tenant_id
            or request.portal_user.get("tenant_role") != "admin"
        ):
            return ApiResponse.forbidden("Permission denied")

    db = current_app.db
    # The permission check above already restricts non-global-admin/support
    # callers to tenant_id == their own portal_user.tenant_id.
    tenant = db.tenants[tenant_id]  # tenant-scope-exempt: see above
    if not tenant:
        return ApiResponse.error("Tenant not found", 404)

    # Gather statistics
    stats = {
        "tenant_id": tenant_id,
        "name": tenant.name,
        "subscription_tier": tenant.subscription_tier,
        "organizations": db(db.organizations.tenant_id == tenant_id).count(),
        "portal_users": {
            "total": db(db.portal_users.tenant_id == tenant_id).count(),
            "active": db(
                (db.portal_users.tenant_id == tenant_id)
                & (db.portal_users.is_active == True)  # noqa: E712
            ).count(),
        },
        "identities": db(db.identities.tenant_id == tenant_id).count(),
        "idp_configurations": db(db.idp_configurations.tenant_id == tenant_id).count(),
        "scim_configurations": db(
            db.scim_configurations.tenant_id == tenant_id
        ).count(),
    }

    return jsonify(stats), 200


@bp.route("/current/mfa-policy", methods=["GET"])
@portal_token_required
def get_current_tenant_mfa_policy():
    """Get the MFA enforcement policy for the authenticated user's current tenant.

    Returns:
        Tenant MFA policy, user enrollment state, license tier, and manageability.
    """
    tenant_id = request.portal_user.get("tenant_id")
    user_id = int(request.portal_user["sub"])
    db = current_app.db

    tenant = db(db.tenants.id == tenant_id).select().first()
    if not tenant:
        return ApiResponse.error("Tenant not found", 404)

    user = db(db.portal_users.id == user_id).select().first()
    mfa_enrolled = user_has_mfa_enrolled(user, db=db)

    policy = get_tenant_mfa_policy(tenant_id, db=db)
    license_tier = get_tier()
    can_manage = _is_mfa_policy_admin(request.portal_user) and (
        license_tier == "enterprise"
    )

    return (
        jsonify(
            {
                "enforced": policy["enforced"],
                "provider": policy["provider"],
                "grace_period_days": policy["grace_period_days"],
                "enforced_at": policy["enforced_at"],
                "mfa_enrolled": mfa_enrolled,
                "license_tier": license_tier,
                "can_manage": can_manage,
            }
        ),
        200,
    )


@bp.route("/current/mfa-policy", methods=["PUT"])
@portal_token_required
async def update_current_tenant_mfa_policy():
    """Update the MFA enforcement policy for the authenticated user's current tenant.

    Requires an admin role (tenant admin, global admin, or admin scope) and an
    Enterprise license (or PenguinTech bypass domain).

    Request body:
        enforced: bool - Whether Google Authenticator MFA is enforced
        provider: str (optional) - MFA provider (``google_authenticator``)
        grace_period_days: int (optional) - Grace period in days (0..90, default 7)

    Returns:
        Updated tenant MFA policy
    """
    if not _is_mfa_policy_admin(request.portal_user):
        return ApiResponse.forbidden("Admin permission required to manage MFA policy")

    license_tier = get_tier()
    if license_tier != "enterprise":
        return ApiResponse.error(
            "Enterprise license required to manage MFA enforcement policy",
            403,
            required_tier="enterprise",
            tier=license_tier,
            upgrade=True,
        )

    payload = await request.get_json(silent=True)
    if payload is None:
        return ApiResponse.bad_request("Request body must be JSON")

    try:
        body = UpdateMfaPolicyRequest.model_validate(payload)
    except ValidationError as e:
        errors = [
            {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
            for err in e.errors()
        ]
        return jsonify({"error": "Validation failed", "details": errors}), 400

    tenant_id = request.portal_user.get("tenant_id")
    user_id = int(request.portal_user["sub"])
    db = current_app.db

    tenant = db(db.tenants.id == tenant_id).select().first()
    if not tenant:
        return ApiResponse.error("Tenant not found", 404)

    settings = parse_tenant_settings(tenant.settings)
    was_enforced = bool(settings.get("mfa_enforced", False))
    existing_enforced_at = settings.get("mfa_enforced_at")

    if body.enforced:
        enforced_at = (
            existing_enforced_at
            if (was_enforced and existing_enforced_at)
            else datetime.now(UTC).isoformat()
        )
    else:
        enforced_at = None

    if body.grace_period_days is not None:
        grace_period_days = body.grace_period_days
    else:
        try:
            grace_period_days = int(
                settings.get("mfa_grace_period_days", DEFAULT_MFA_GRACE_PERIOD_DAYS)
            )
        except (TypeError, ValueError):
            grace_period_days = DEFAULT_MFA_GRACE_PERIOD_DAYS

    settings["mfa_enforced"] = body.enforced
    settings["mfa_enforced_at"] = enforced_at
    settings["mfa_grace_period_days"] = grace_period_days
    settings["mfa_provider"] = body.provider

    db(db.tenants.id == tenant.id).update(
        settings=settings,
        updated_at=datetime.now(UTC),
    )
    db.commit()

    user = db(db.portal_users.id == user_id).select().first()
    mfa_enrolled = user_has_mfa_enrolled(user, db=db)

    return (
        jsonify(
            {
                "enforced": body.enforced,
                "provider": body.provider,
                "grace_period_days": grace_period_days,
                "enforced_at": enforced_at,
                "mfa_enrolled": mfa_enrolled,
                "license_tier": license_tier,
                "can_manage": True,
            }
        ),
        200,
    )


def _can_manage_tenant_signup_policy(portal_user: dict, tenant_id: int) -> bool:
    """Return True if caller is global admin or tenant admin for ``tenant_id``."""
    if portal_user.get("global_role") in ("admin", "super_admin", "superadmin"):
        return True
    return portal_user.get("tenant_id") == tenant_id and _is_mfa_policy_admin(
        portal_user
    )


def _can_view_tenant_signup_policy(portal_user: dict, tenant_id: int) -> bool:
    """Return True if caller can view signup policy for ``tenant_id``."""
    if portal_user.get("global_role") in (
        "admin",
        "super_admin",
        "superadmin",
        "support",
    ):
        return True
    return portal_user.get("tenant_id") == tenant_id


def _build_signup_policy_response(tenant: Any, *, can_manage: bool) -> dict[str, Any]:
    settings = parse_tenant_settings(getattr(tenant, "settings", None))
    policy = resolve_tenant_signup_policy(
        tenant_settings=settings,
        tenant_slug=getattr(tenant, "slug", None),
    )
    return {
        "allowed_signup_domains": policy["allowed_signup_domains"],
        "require_email_verification": policy["require_email_verification"],
        "source": policy["source"],
        "can_manage": can_manage,
    }


async def _update_tenant_signup_policy_impl(tenant_id: int):
    if not _can_manage_tenant_signup_policy(request.portal_user, tenant_id):
        return ApiResponse.forbidden(
            "Admin permission required to manage signup policy"
        )

    payload = await request.get_json(silent=True)
    if payload is None:
        return ApiResponse.bad_request("Request body must be JSON")

    try:
        body = UpdateSignupPolicyRequest.model_validate(payload)
    except ValidationError as e:
        errors = [
            {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
            for err in e.errors()
        ]
        return jsonify({"error": "Validation failed", "details": errors}), 400

    db = current_app.db
    tenant = db(db.tenants.id == tenant_id).select().first()
    if not tenant:
        return ApiResponse.error("Tenant not found", 404)

    normalized_domains = list(normalize_signup_domains(body.allowed_signup_domains))
    settings = parse_tenant_settings(getattr(tenant, "settings", None))
    settings["allowed_signup_domains"] = normalized_domains
    if body.require_email_verification is not None:
        settings["require_email_verification"] = bool(body.require_email_verification)

    db(db.tenants.id == tenant.id).update(
        settings=settings,
        updated_at=datetime.now(UTC),
    )
    db.commit()

    refreshed = db(db.tenants.id == tenant.id).select().first() or tenant
    return (
        jsonify(_build_signup_policy_response(refreshed, can_manage=True)),
        200,
    )


@bp.route("/current/signup-policy", methods=["GET"])
@portal_token_required
def get_current_tenant_signup_policy():
    """Get the signup domain and email verification policy for the current tenant.

    Returns:
        Tenant signup policy including allowed domains, verification flag,
        policy source, and whether the caller can manage it.
    """
    tenant_id = request.portal_user.get("tenant_id")
    db = current_app.db
    tenant = db(db.tenants.id == tenant_id).select().first()
    if not tenant:
        return ApiResponse.error("Tenant not found", 404)

    can_manage = _can_manage_tenant_signup_policy(request.portal_user, tenant.id)
    return jsonify(_build_signup_policy_response(tenant, can_manage=can_manage)), 200


@bp.route("/current/signup-policy", methods=["PUT"])
@portal_token_required
async def update_current_tenant_signup_policy():
    """Update the signup domain and email verification policy for the current tenant.

    Requires tenant admin or global admin.

    Request body:
        allowed_signup_domains: list[str] | str - Allowed signup email domains
            (empty list allows any domain)
        require_email_verification: bool (optional) - Require email verification

    Returns:
        Updated tenant signup policy
    """
    tenant_id = request.portal_user.get("tenant_id")
    if not tenant_id:
        return ApiResponse.error("Tenant not found", 404)
    return await _update_tenant_signup_policy_impl(int(tenant_id))


@bp.route("/<int:tenant_id>/signup-policy", methods=["GET"])
@portal_token_required
def get_tenant_signup_policy(tenant_id):
    """Get the signup domain and email verification policy for a specific tenant.

    Args:
        tenant_id: Tenant ID

    Returns:
        Tenant signup policy
    """
    if not _can_view_tenant_signup_policy(request.portal_user, tenant_id):
        return ApiResponse.forbidden("Permission denied")

    db = current_app.db
    # Permission check above restricts non-global-admins to their own tenant_id.
    tenant = db.tenants[tenant_id]  # tenant-scope-exempt: see above
    if not tenant:
        return ApiResponse.error("Tenant not found", 404)

    can_manage = _can_manage_tenant_signup_policy(request.portal_user, tenant.id)
    return jsonify(_build_signup_policy_response(tenant, can_manage=can_manage)), 200


@bp.route("/<int:tenant_id>/signup-policy", methods=["PUT"])
@portal_token_required
async def update_tenant_signup_policy(tenant_id):
    """Update the signup domain and email verification policy for a specific tenant.

    Requires global admin or tenant admin for the target tenant.

    Args:
        tenant_id: Tenant ID

    Request body:
        allowed_signup_domains: list[str] | str - Allowed signup email domains
        require_email_verification: bool (optional) - Require email verification

    Returns:
        Updated tenant signup policy
    """
    return await _update_tenant_signup_policy_impl(tenant_id)
