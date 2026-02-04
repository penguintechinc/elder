"""User management endpoints (admin only)."""

# flake8: noqa: E501


from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request
from werkzeug.security import generate_password_hash

from apps.api.auth.decorators import get_current_user, login_required, role_required
from apps.api.models.dataclasses import IdentityDTO, PaginatedResponse, from_pydal_rows
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("users", __name__)


@bp.route("", methods=["GET"])
@login_required
@role_required("admin")
async def list_users():
    """List all users (admin only)."""
    db = current_app.db

    # Get pagination parameters
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)

    # Validate pagination
    if page < 1:
        return jsonify({"error": "Page must be >= 1"}), 400
    if per_page < 1 or per_page > 1000:
        return jsonify({"error": "Per page must be between 1 and 1000"}), 400

    # Calculate pagination
    offset = (page - 1) * per_page

    # Build query outside the threadpool function (db is thread-local)
    query = db.identities.id > 0

    # Execute database queries
    def get_users():
        total = db(query).count()
        rows = db(query).select(
            db.identities.id,
            db.identities.identity_type,
            db.identities.username,
            db.identities.email,
            db.identities.full_name,
            db.identities.organization_id,
            db.identities.portal_role,
            db.identities.auth_provider,
            db.identities.auth_provider_id,
            db.identities.is_active,
            db.identities.is_superuser,
            db.identities.mfa_enabled,
            db.identities.last_login_at,
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

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body is required"}), 400

    # Validate required fields
    if "username" not in data or not data["username"].strip():
        return jsonify({"error": "Username is required"}), 400

    if "password" not in data or not data["password"].strip():
        return jsonify({"error": "Password is required"}), 400

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

        user_id = db.identities.insert(**insert_data)
        db.commit()
        return db.identities[user_id], None, None

    user_row, error, status = await run_in_threadpool(create)

    if error:
        return jsonify({"error": error}), status

    # Convert to DTO
    user_dto = IdentityDTO(
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

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body is required"}), 400

    # Prepare update data
    update_data = {}

    allowed_fields = [
        "email",
        "full_name",
        "organization_id",
        "portal_role",
        "is_active",
        "is_superuser",
        "mfa_enabled",
    ]

    for field in allowed_fields:
        if field in data:
            update_data[field] = data[field]

    # Handle password update
    if "password" in data and data["password"]:
        update_data["password_hash"] = generate_password_hash(data["password"])

    if not update_data:
        return jsonify({"error": "No valid fields to update"}), 400

    # Update user
    def update():
        user = db.identities[user_id]
        if not user:
            return None, "User not found", 404

        user.update_record(**update_data)
        db.commit()
        return db.identities[user_id], None, None

    user_row, error, status = await run_in_threadpool(update)

    if error:
        return jsonify({"error": error}), status

    # Convert to DTO
    user_dto = IdentityDTO(
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

    # Prevent self-deletion
    if current_user.id == user_id:
        return jsonify({"error": "Cannot delete your own user account"}), 400

    def delete():
        user = db.identities[user_id]
        if not user:
            return None, "User not found", 404

        del db.identities[user_id]
        db.commit()
        return user, None, None

    user, error, status = await run_in_threadpool(delete)

    if error:
        return jsonify({"error": error}), status

    return jsonify({"message": f"User '{user.username}' deleted successfully"}), 200
