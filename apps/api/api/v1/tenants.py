"""Tenant management API endpoints for v2.2.0 Enterprise Edition.

Provides REST endpoints for tenant CRUD operations, configuration,
and usage statistics for the Super Admin Console.
"""

# flake8: noqa: E501


from typing import Optional

from flask import Blueprint, current_app, jsonify, request
from flask_login import login_required
from penguin_libs.pydantic import Name255, RequestModel, SlugStr
from pydantic import Field, ValidationError

from apps.api.api.v1.portal_auth import portal_token_required

bp = Blueprint("tenants", __name__)


# Pydantic Models for request validation
class CreateTenantRequest(RequestModel):
    """Validation model for creating a new tenant."""

    name: Name255
    slug: SlugStr
    domain: Optional[str] = Field(default=None, max_length=255)
    subscription_tier: str = Field(default="community", max_length=50)
    license_key: Optional[str] = Field(default=None, max_length=500)
    settings: Optional[dict] = Field(default=None)
    feature_flags: Optional[dict] = Field(default=None)
    data_retention_days: int = Field(default=90, ge=1, le=36500)
    storage_quota_gb: int = Field(default=10, ge=1, le=1000000)


class UpdateTenantRequest(RequestModel):
    """Validation model for updating a tenant."""

    name: Optional[Name255] = None
    domain: Optional[str] = Field(default=None, max_length=255)
    slug: Optional[SlugStr] = None
    subscription_tier: Optional[str] = Field(default=None, max_length=50)
    license_key: Optional[str] = Field(default=None, max_length=500)
    settings: Optional[dict] = None
    feature_flags: Optional[dict] = None
    data_retention_days: Optional[int] = Field(default=None, ge=1, le=36500)
    storage_quota_gb: Optional[int] = Field(default=None, ge=1, le=1000000)
    is_active: Optional[bool] = None


def global_admin_required(f):
    """Decorator to require global admin permission."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if request.portal_user.get("global_role") != "admin":
            return jsonify({"error": "Global admin permission required"}), 403
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
        return jsonify({"error": "Global admin or support role required"}), 403

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
            return jsonify({"error": "Permission denied"}), 403

    db = current_app.db
    tenant = db.tenants[tenant_id]
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

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
@login_required
@portal_token_required
@global_admin_required
def create_tenant():
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
        body = CreateTenantRequest.model_validate(request.get_json())
    except ValidationError as e:
        errors = [
            {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
            for err in e.errors()
        ]
        return jsonify({"error": "Validation failed", "details": errors}), 400

    db = current_app.db
    # Check if slug already exists
    existing = db(db.tenants.slug == body.slug).select().first()
    if existing:
        return jsonify({"error": "Slug already exists"}), 400

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
    )
    db.commit()

    return (
        jsonify(
            {
                "id": tenant_id,
                "name": body.name,
                "slug": body.slug,
            }
        ),
        201,
    )


@bp.route("/<int:tenant_id>", methods=["PUT"])
@portal_token_required
def update_tenant(tenant_id):
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
        return jsonify({"error": "Permission denied"}), 403

    db = current_app.db
    tenant = db.tenants[tenant_id]
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    try:
        body = UpdateTenantRequest.model_validate(request.get_json())
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
        tenant.update_record(**updates)
        db.commit()

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
        return jsonify({"error": "Cannot delete system tenant"}), 400

    db = current_app.db
    tenant = db.tenants[tenant_id]
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    # Soft delete - deactivate instead
    tenant.update_record(is_active=False)
    db.commit()

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
            return jsonify({"error": "Permission denied"}), 403

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


@bp.route("/<int:tenant_id>/users/<int:user_id>", methods=["PUT"])
@portal_token_required
def update_tenant_user(tenant_id, user_id):
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
        return jsonify({"error": "Permission denied"}), 403

    db = current_app.db
    user = (
        db((db.portal_users.id == user_id) & (db.portal_users.tenant_id == tenant_id))
        .select()
        .first()
    )

    if not user:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    # Allowed update fields
    allowed_fields = {"full_name", "tenant_role", "is_active"}
    if is_global_admin:
        allowed_fields.add("global_role")

    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if updates:
        user.update_record(**updates)
        db.commit()

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
        return jsonify({"error": "Permission denied"}), 403

    db = current_app.db
    user = (
        db((db.portal_users.id == user_id) & (db.portal_users.tenant_id == tenant_id))
        .select()
        .first()
    )

    if not user:
        return jsonify({"error": "User not found"}), 404

    # Soft delete
    user.update_record(is_active=False)
    db.commit()

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
            return jsonify({"error": "Permission denied"}), 403

    db = current_app.db
    tenant = db.tenants[tenant_id]
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

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
