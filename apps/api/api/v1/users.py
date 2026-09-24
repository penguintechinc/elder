"""User management endpoints (admin only)."""

# flake8: noqa: E501

from dataclasses import asdict
from datetime import UTC, datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request
from werkzeug.security import generate_password_hash

from apps.api.auth.decorators import get_current_user, login_required, role_required
from apps.api.models.dataclasses import (
    IdentityAdminDTO,
    IdentityDTO,
    PaginatedResponse,
    from_pydal_rows,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.tenant_scoping import get_tenant_scoped

bp = Blueprint("users", __name__)


@bp.route("", methods=["GET"])
@login_required
@role_required("admin")
async def list_users():
    """List all users (admin only)."""
    db = current_app.db
    user = get_current_user()

    # Get pagination parameters
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)

    # Validate pagination
    if page < 1:
        return ApiResponse.bad_request("Page must be >= 1")
    if per_page < 1 or per_page > 1000:
        return ApiResponse.bad_request("Per page must be between 1 and 1000")

    # Calculate pagination
    offset = (page - 1) * per_page

    # Build query outside the threadpool function (db is thread-local).
    # gh-237: role_required("admin") allows a per-tenant portal_role=="admin"
    # user, not just global superusers -- without this filter, any tenant
    # admin could list every tenant's users. Superusers (who bypass the
    # decorator's role check) see everything, matching their existing
    # global-admin bypass elsewhere.
    query = db.identities.id > 0
    if not user.is_superuser:
        query &= db.identities.tenant_id == user.tenant_id

    # Execute database queries
    def get_users():
        total = db(query).count()
        rows = db(query).select(
            db.identities.id,
            db.identities.identity_type,
            db.identities.username,
            db.identities.email,
            db.identities.full_name,
            db.identities.tenant_id,
            db.identities.created_at,
            db.identities.updated_at,
            orderby=db.identities.username,
            limitby=(offset, offset + per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_users)

    # Calculate total pages
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # Convert PyDAL rows to DTOs
    items = from_pydal_rows(rows, IdentityDTO)

    # Create paginated response
    response = PaginatedResponse(
        items=[asdict(item) for item in items],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )

    return jsonify(asdict(response)), 200


@bp.route("", methods=["POST"])
@login_required
@role_required("admin")
async def create_user():
    """Create a new user (admin only)."""
    db = current_app.db

    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("Request body is required")

    # Validate required fields
    if "username" not in data or not data["username"].strip():
        return ApiResponse.bad_request("Username is required")

    if "password" not in data or not data["password"].strip():
        return ApiResponse.bad_request("Password is required")

    # Prepare insert data
    username = data["username"].strip()
    insert_data = {
        "username": username,
        "password_hash": generate_password_hash(data["password"]),
        "identity_type": data.get("identity_type", "human"),
        "auth_provider": "local",
        "email": data.get("email"),
        "full_name": data.get("full_name"),
        "organization_id": data.get("organization_id"),
        "portal_role": data.get("portal_role", "observer"),
        "is_active": data.get("is_active", True),
        "is_superuser": data.get("is_superuser", False),
        "mfa_enabled": data.get("mfa_enabled", False),
        "tenant_id": data.get("tenant_id"),  # Optional from request body
    }

    # Build queries outside threadpool function (db is thread-local)
    username_query = db.identities.username == username
    email_query = (
        (db.identities.email == insert_data["email"])
        if insert_data.get("email")
        else None
    )

    # Create user
    def create():
        # Check if username exists
        existing = db(username_query).select().first()
        if existing:
            return None, "Username already exists", 400

        # Check if email exists (if provided)
        if email_query is not None:
            existing_email = db(email_query).select().first()
            if existing_email:
                return None, "Email already exists", 400

        # Derive tenant_id: from request body, then from current user, then from DB default
        tenant_id = (
            insert_data.pop("tenant_id", None) if "tenant_id" in insert_data else None
        )
        if not tenant_id and hasattr(g, "current_user") and g.current_user:
            tenant_id = g.current_user.tenant_id
        if not tenant_id:
            # Fall back to default tenant from DB
            default_tenant = db(db.tenants.id > 0).select(limitby=(0, 1)).first()
            tenant_id = default_tenant.id if default_tenant else None

        now = datetime.now(UTC)
        user_id = db.identities.insert(
            created_at=now, updated_at=now, tenant_id=tenant_id, **insert_data
        )
        db.commit()
        user_row = db.identities[user_id]  # tenant-scope-exempt
        return (user_row, None, None)

    user_row, error, status = await run_in_threadpool(create)

    if error:
        return jsonify({"error": error}), status

    # Convert to DTO -- IdentityAdminDTO (not IdentityDTO), since this
    # admin-only response includes is_superuser/portal_role.
    user_dto = IdentityAdminDTO(
        id=user_row.id,
        identity_type=user_row.identity_type,
        username=user_row.username,
        email=user_row.email,
        full_name=user_row.full_name,
        organization_id=user_row.organization_id,
        portal_role=user_row.portal_role,
        auth_provider=user_row.auth_provider,
        auth_provider_id=user_row.auth_provider_id,
        is_active=user_row.is_active,
        is_superuser=user_row.is_superuser,
        mfa_enabled=user_row.mfa_enabled,
        last_login_at=user_row.last_login_at,
        created_at=user_row.created_at,
        updated_at=user_row.updated_at,
    )

    return jsonify(asdict(user_dto)), 201


@bp.route("/<int:user_id>", methods=["PATCH"])
@login_required
@role_required("admin")
async def update_user(user_id: int):
    """Update a user (admin only)."""
    db = current_app.db
    caller = get_current_user()
    caller_tenant_id = None if caller.is_superuser else caller.tenant_id

    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("Request body is required")

    # Prepare update data
    update_data = {}

    # Allowlist of client-updatable fields. `is_superuser` is intentionally
    # NOT in this base list -- regression: privilege escalation. A
    # tenant-scoped "admin" (role_required("admin") allows portal_role ==
    # "admin", not just global superusers -- see the gh-237 comment above)
    # could otherwise set is_superuser=true on themselves or another user in
    # their own tenant and escalate to a global superuser. Only append it
    # when the caller is already a verified superuser.
    allowed_fields = [
        "email",
        "full_name",
        "organization_id",
        "portal_role",
        "is_active",
        "mfa_enabled",
    ]
    if caller.is_superuser:
        allowed_fields.append("is_superuser")

    for field in allowed_fields:
        if field in data:
            update_data[field] = data[field]

    # Handle password update
    if "password" in data and data["password"]:
        update_data["password_hash"] = generate_password_hash(data["password"])

    if not update_data:
        return ApiResponse.bad_request("No valid fields to update")

    # Update user. gh-237: role_required("admin") allows a per-tenant
    # portal_role=="admin" user, not just global superusers -- without this
    # check, any tenant admin could update (including granting is_active/
    # portal_role/mfa_enabled on) another tenant's user by guessing its id.
    def update():
        if caller_tenant_id is not None:
            user = get_tenant_scoped(db, db.identities, user_id, caller_tenant_id)
        else:
            user = db.identities[user_id]  # tenant-scope-exempt
        if not user:
            return None, "User not found", 404

        db(db.identities.id == user_id).update(**update_data)
        return db.identities[user_id], None, None  # tenant-scope-exempt: verified above

    user_row, error, status = await run_in_threadpool(update)

    if error:
        return jsonify({"error": error}), status

    # Convert to DTO -- IdentityAdminDTO (not IdentityDTO), since this
    # admin-only response includes is_superuser/portal_role.
    user_dto = IdentityAdminDTO(
        id=user_row.id,
        identity_type=user_row.identity_type,
        username=user_row.username,
        email=user_row.email,
        full_name=user_row.full_name,
        organization_id=user_row.organization_id,
        portal_role=user_row.portal_role,
        auth_provider=user_row.auth_provider,
        auth_provider_id=user_row.auth_provider_id,
        is_active=user_row.is_active,
        is_superuser=user_row.is_superuser,
        mfa_enabled=user_row.mfa_enabled,
        last_login_at=user_row.last_login_at,
        created_at=user_row.created_at,
        updated_at=user_row.updated_at,
    )

    return jsonify(asdict(user_dto)), 200


@bp.route("/<int:user_id>", methods=["DELETE"])
@login_required
@role_required("admin")
async def delete_user(user_id: int):
    """Delete a user (admin only)."""
    db = current_app.db
    current_user = get_current_user()
    caller_tenant_id = None if current_user.is_superuser else current_user.tenant_id

    # Prevent self-deletion
    if current_user.id == user_id:
        return ApiResponse.bad_request("Cannot delete your own user account")

    # gh-237: role_required("admin") allows a per-tenant portal_role=="admin"
    # user, not just global superusers -- without this check, any tenant
    # admin could delete another tenant's user by guessing its id.
    def delete():
        if caller_tenant_id is not None:
            user = get_tenant_scoped(db, db.identities, user_id, caller_tenant_id)
        else:
            user = db.identities[user_id]  # tenant-scope-exempt
        if not user:
            return None, "User not found", 404

        del db.identities[user_id]  # tenant-scope-exempt: verified above
        db.commit()
        return user, None, None

    user, error, status = await run_in_threadpool(delete)

    if error:
        return jsonify({"error": error}), status

    return jsonify({"message": f"User '{user.username}' deleted successfully"}), 200
