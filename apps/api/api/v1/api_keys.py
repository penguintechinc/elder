"""API Key management endpoints."""

# flake8: noqa: E501


import hashlib
import secrets
from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import get_current_user, login_required
from apps.api.models.dataclasses import (
    APIKeyDTO,
    CreateAPIKeyResponse,
    PaginatedResponse,
    from_pydal_rows,
)
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("api_keys", __name__)


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        tuple: (full_key, key_hash, prefix)
    """
    # Generate a secure random key
    key = f"elder_{secrets.token_urlsafe(32)}"

    # Create SHA256 hash for storage
    key_hash = hashlib.sha256(key.encode()).hexdigest()

    # Extract prefix for display (e.g., "elder_abc...")
    prefix = key[:15] + "..."

    return key, key_hash, prefix


@bp.route("", methods=["GET"])
@login_required
async def list_api_keys():
    """List all API keys for the current user."""
    db = current_app.db
    user = get_current_user()

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

    # Build query for user's API keys
    query = db.api_keys.identity_id == user.id

    # Execute database queries
    def get_api_keys():
        total = db(query).count()
        rows = db(query).select(
            db.api_keys.id,
            db.api_keys.identity_id,
            db.api_keys.name,
            db.api_keys.prefix,
            db.api_keys.last_used_at,
            db.api_keys.expires_at,
            db.api_keys.is_active,
            db.api_keys.created_at,
            db.api_keys.updated_at,
            orderby=~db.api_keys.created_at,  # Most recent first
            limitby=(offset, offset + per_page),
        )
        return total, rows

    total, rows = await run_in_threadpool(get_api_keys)

    # Calculate total pages
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # Convert PyDAL rows to DTOs
    items = from_pydal_rows(rows, APIKeyDTO)

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
async def create_api_key():
    """Create a new API key for the current user."""
    db = current_app.db
    user = get_current_user()

    # Parse request data
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body is required"}), 400

    # Validate required fields
    if "name" not in data or not data["name"].strip():
        return jsonify({"error": "Name is required"}), 400

    # Generate API key
    full_key, key_hash, prefix = generate_api_key()

    # Prepare insert data
    insert_data = {
        "identity_id": user.id,
        "name": data["name"].strip(),
        "key_hash": key_hash,
        "prefix": prefix,
        "is_active": True,
    }

    # Optional expiration date
    if "expires_at" in data and data["expires_at"]:
        insert_data["expires_at"] = data["expires_at"]

    # Insert into database
    def create_key():
        key_id = db.api_keys.insert(**insert_data)
        db.commit()
        return db.api_keys[key_id]

    api_key_row = await run_in_threadpool(create_key)

    # Create response with full key (shown only once!)
    response = CreateAPIKeyResponse(
        id=api_key_row.id,
        name=api_key_row.name,
        api_key=full_key,  # Full key - only shown once!
        prefix=api_key_row.prefix,
        expires_at=api_key_row.expires_at,
        created_at=api_key_row.created_at,
    )

    return jsonify(asdict(response)), 201


@bp.route("/<int:key_id>", methods=["DELETE"])
@login_required
async def delete_api_key(key_id: int):
    """Delete (revoke) an API key."""
    db = current_app.db
    user = get_current_user()

    # Verify the key belongs to the current user
    def revoke_key():
        api_key = db.api_keys[key_id]
        if not api_key:
            return None, "API key not found", 404

        if api_key.identity_id != user.id:
            return None, "Access denied", 403

        # Delete the key
        del db.api_keys[key_id]
        db.commit()
        return api_key, None, None

    api_key, error, status = await run_in_threadpool(revoke_key)

    if error:
        return jsonify({"error": error}), status

    return jsonify({"message": "API key revoked successfully"}), 200
