"""User Profile API endpoints using PyDAL with async/await."""

# flake8: noqa: E501


from flask import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("profile", __name__)


@bp.route("/me", methods=["GET"])
@login_required
async def get_profile():
    """
    Get current user's profile information.

    Returns:
        200: User profile data
        {
            "id": 1,
            "username": "john.doe",
            "email": "john@example.com",
            "full_name": "John Doe",
            "organization_id": 5,
            "organization_name": "Engineering",
            "is_superuser": false,
            "is_active": true,
            "created_at": "2025-01-01T00:00:00Z"
        }
        404: User not found
    """
    db = current_app.db
    user_id = g.current_user.id  # Get user ID before entering thread pool

    def get_user_profile(uid):
        # Get user from database
        user = db.identities[uid]
        if not user:
            return None, "User not found", 404

        # Get organization name if user has organization_id
        org_name = None
        if hasattr(user, "organization_id") and user.organization_id:
            org = db.organizations[user.organization_id]
            if org:
                org_name = org.name

        # Build response
        profile_data = {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "organization_id": (
                user.organization_id if hasattr(user, "organization_id") else None
            ),
            "organization_name": org_name,
            "is_superuser": user.is_superuser,
            "is_active": user.is_active,
            "identity_type": user.identity_type,
            "auth_provider": user.auth_provider,
            "mfa_enabled": user.mfa_enabled,
            "last_login_at": (
                user.last_login_at.isoformat() if user.last_login_at else None
            ),
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }

        return profile_data, None, None

    profile, error, status = await run_in_threadpool(get_user_profile, user_id)

    if error:
        return jsonify({"error": error}), status

    return jsonify(profile), 200


@bp.route("/me", methods=["PATCH"])
@login_required
async def update_profile():
    """
    Update current user's profile information.

    Request Body:
        {
            "full_name": "string" (optional),
            "email": "string" (optional),
            "organization_id": number (optional)
        }

    Returns:
        200: Profile updated successfully with updated profile data
        400: Validation error
        404: User or organization not found
    """
    db = current_app.db
    user_id = g.current_user.id  # Get user ID before entering thread pool

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    def update_user_profile(uid):
        # Get user from database
        user = db.identities[uid]
        if not user:
            return None, "User not found", 404

        # Prepare update data
        update_data = {}

        # Update full_name if provided
        if "full_name" in data:
            if data["full_name"] and len(data["full_name"]) > 255:
                return None, "full_name must be 255 characters or less", 400
            update_data["full_name"] = data["full_name"]

        # Update email if provided
        if "email" in data:
            if not data["email"]:
                return None, "email cannot be empty", 400
            if len(data["email"]) > 255:
                return None, "email must be 255 characters or less", 400
            # Check if email is already in use by another user
            existing = (
                db(
                    (db.identities.email == data["email"])
                    & (db.identities.id != user.id)
                )
                .select()
                .first()
            )
            if existing:
                return None, "Email already in use", 400
            update_data["email"] = data["email"]

        # Update organization_id if provided
        if "organization_id" in data:
            if data["organization_id"] is not None:
                # Verify organization exists
                org = db.organizations[data["organization_id"]]
                if not org:
                    return None, "Organization not found", 404
            # Only update organization_id if the field exists in the table
            if hasattr(db.identities, "organization_id"):
                update_data["organization_id"] = data["organization_id"]

        # If no valid fields to update, return current profile
        if not update_data:
            org_name = None
            if hasattr(user, "organization_id") and user.organization_id:
                org = db.organizations[user.organization_id]
                if org:
                    org_name = org.name

            profile_data = {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "full_name": user.full_name,
                "organization_id": (
                    user.organization_id if hasattr(user, "organization_id") else None
                ),
                "organization_name": org_name,
                "is_superuser": user.is_superuser,
                "is_active": user.is_active,
                "identity_type": user.identity_type,
                "auth_provider": user.auth_provider,
                "mfa_enabled": user.mfa_enabled,
                "last_login_at": (
                    user.last_login_at.isoformat() if user.last_login_at else None
                ),
                "created_at": user.created_at.isoformat() if user.created_at else None,
            }
            return profile_data, None, None

        # Update user
        user.update_record(**update_data)
        db.commit()

        # Get updated user data
        updated_user = db.identities[uid]

        # Get organization name if user has organization_id
        org_name = None
        if hasattr(updated_user, "organization_id") and updated_user.organization_id:
            org = db.organizations[updated_user.organization_id]
            if org:
                org_name = org.name

        # Build response
        profile_data = {
            "id": updated_user.id,
            "username": updated_user.username,
            "email": updated_user.email,
            "full_name": updated_user.full_name,
            "organization_id": (
                updated_user.organization_id
                if hasattr(updated_user, "organization_id")
                else None
            ),
            "organization_name": org_name,
            "is_superuser": updated_user.is_superuser,
            "is_active": updated_user.is_active,
            "identity_type": updated_user.identity_type,
            "auth_provider": updated_user.auth_provider,
            "mfa_enabled": updated_user.mfa_enabled,
            "last_login_at": (
                updated_user.last_login_at.isoformat()
                if updated_user.last_login_at
                else None
            ),
            "created_at": (
                updated_user.created_at.isoformat() if updated_user.created_at else None
            ),
        }

        return profile_data, None, None

    profile, error, status = await run_in_threadpool(update_user_profile, user_id)

    if error:
        return jsonify({"error": error}), status

    return jsonify(profile), 200
