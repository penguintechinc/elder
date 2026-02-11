"""Portal authentication API endpoints for v2.2.0 Enterprise Edition.

Provides REST endpoints for portal user authentication, registration,
MFA management, and password operations with tenant context.
"""

# flake8: noqa: E501


from datetime import datetime, timezone
from functools import wraps

import jwt
from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from apps.api.models.schemas import PortalLoginRequest, PortalRegisterRequest
from apps.api.services.portal_auth import PortalAuthService

bp = Blueprint("portal_auth", __name__)


def portal_token_required(f):
    """Decorator to require portal user JWT authentication."""

    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization")

        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

        if not token:
            return jsonify({"error": "Token is missing"}), 401

        try:
            secret_key = current_app.config.get(
                "JWT_SECRET_KEY"
            ) or current_app.config.get("SECRET_KEY")
            payload = jwt.decode(token, secret_key, algorithms=["HS256"])

            if payload.get("type") != "portal_user":
                return jsonify({"error": "Invalid token type"}), 401

            request.portal_user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401

        return f(*args, **kwargs)

    return decorated


def generate_tokens(user: dict) -> dict:
    """Generate access and refresh tokens for a portal user."""
    secret_key = current_app.config.get("JWT_SECRET_KEY") or current_app.config.get(
        "SECRET_KEY"
    )

    # Access token expiration from config
    access_token_expires = current_app.config["JWT_ACCESS_TOKEN_EXPIRES"]
    refresh_token_expires = current_app.config["JWT_REFRESH_TOKEN_EXPIRES"]

    now = datetime.now(timezone.utc)

    # Access token
    access_payload = PortalAuthService.generate_jwt_claims(user)
    access_payload["exp"] = now + access_token_expires
    access_payload["iat"] = now
    access_token = jwt.encode(access_payload, secret_key, algorithm="HS256")

    # Refresh token
    refresh_payload = {
        "sub": str(user["id"]),
        "tenant_id": user["tenant_id"],
        "type": "portal_refresh",
        "exp": now + refresh_token_expires,
        "iat": now,
    }
    refresh_token = jwt.encode(refresh_payload, secret_key, algorithm="HS256")

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": int(access_token_expires.total_seconds()),
    }


@bp.route("/register", methods=["POST"])
def register():
    """Register a new portal user.

    Request body:
        tenant: str - Tenant slug (or use tenant_id)
        email: str - Email address (validated format)
        password: str - Password (minimum 8 characters)
        full_name: str (optional) - Full name

    Returns:
        User info and tokens on success
        422: Invalid email format or validation error
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    # Validate request using Pydantic schema
    try:
        validated_data = PortalRegisterRequest(**data)
    except ValidationError as e:
        # Convert Pydantic errors to JSON-serializable format
        errors = []
        for error in e.errors():
            errors.append(
                {
                    "field": (
                        error.get("loc", [])[-1] if error.get("loc") else "unknown"
                    ),
                    "message": str(error.get("msg", "Validation failed")),
                    "type": error.get("type", "validation_error"),
                }
            )
        return jsonify({"error": "Validation failed", "details": errors}), 422

    tenant_id = data.get("tenant_id")
    tenant_slug = validated_data.tenant

    # Resolve tenant_id from slug if not provided
    if not tenant_id and tenant_slug:
        tenant_record = (
            current_app.db(current_app.db.tenants.slug == tenant_slug).select().first()
        )
        if tenant_record:
            tenant_id = tenant_record.id

    if not tenant_id:
        return jsonify({
            "success": False,
            "error": "Valid tenant is required",
            "errorCode": "INVALID_TENANT"
        }), 400

    # Verify tenant exists
    tenant = current_app.db.tenants[tenant_id]
    if not tenant or not tenant.is_active:
        return jsonify({
            "success": False,
            "error": "Invalid tenant",
            "errorCode": "INVALID_TENANT"
        }), 400

    result = PortalAuthService.create_portal_user(
        tenant_id=tenant_id,
        email=validated_data.email,
        password=validated_data.password,
        full_name=validated_data.full_name,
        tenant_role="reader",  # Default role for self-registration
    )

    if "error" in result:
        return jsonify({
            "success": False,
            "error": result["error"],
            "errorCode": "REGISTRATION_FAILED"
        }), 400

    # Generate tokens
    tokens = generate_tokens(result)

    response_data = {
        "success": True,
        "user": {
            "id": str(result["id"]),
            "email": result["email"],
            "name": result.get("full_name"),
            "roles": ["reader"],
        },
        "token": tokens.get("access_token"),
        "refreshToken": tokens.get("refresh_token"),
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "token_type": tokens.get("token_type"),
        "expires_in": tokens.get("expires_in"),
    }

    return (
        jsonify(response_data),
        201,
    )


@bp.route("/login", methods=["POST"])
def login():
    """Authenticate a portal user.

    Request body:
        tenant: str - Tenant slug (or use tenant_id)
        email: str - Email address (validated format)
        password: str - Password

    Returns:
        Tokens on success, or MFA challenge
        422: Invalid email format or validation error
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    # Validate request using Pydantic schema
    try:
        validated_data = PortalLoginRequest(**data)
    except ValidationError as e:
        # Convert Pydantic errors to JSON-serializable format
        errors = []
        for error in e.errors():
            errors.append(
                {
                    "field": (
                        error.get("loc", [])[-1] if error.get("loc") else "unknown"
                    ),
                    "message": str(error.get("msg", "Validation failed")),
                    "type": error.get("type", "validation_error"),
                }
            )
        return jsonify({"error": "Validation failed", "details": errors}), 422

    tenant_id = data.get("tenant_id")
    tenant_slug = validated_data.tenant

    # Resolve tenant_id from slug if not provided
    if not tenant_id and tenant_slug:
        tenant = current_app.db(current_app.db.tenants.slug == tenant_slug).select().first()
        if tenant:
            tenant_id = tenant.id

    # Fall back to system/default tenant for single-tenant deployments
    if not tenant_id:
        # Try "system" first (common in Elder v3.x), then "default"
        default_tenant = current_app.db(current_app.db.tenants.slug == "system").select().first()
        if not default_tenant:
            default_tenant = current_app.db(current_app.db.tenants.slug == "default").select().first()
        if default_tenant:
            tenant_id = default_tenant.id

    if not tenant_id:
        return jsonify({"error": "Valid tenant is required"}), 400

    result = PortalAuthService.authenticate(
        tenant_id, validated_data.email, validated_data.password
    )

    if "error" in result:
        return jsonify({
            "success": False,
            "error": result["error"],
            "errorCode": "INVALID_CREDENTIALS"
        }), 401

    if result.get("mfa_required"):
        return jsonify({
            "success": False,
            "mfaRequired": True,
            "user": {
                "id": str(result.get("user_id")),
                "email": result.get("email"),
            }
        }), 200

    # Generate tokens
    tokens = generate_tokens(result)

    # Format response for react-libs LoginPageBuilder compatibility
    response_data = {
        "success": True,
        "user": {
            "id": str(result["id"]),
            "email": result["email"],
            "name": result.get("full_name"),
            "roles": [result.get("tenant_role")] if result.get("tenant_role") else [],
        },
        "token": tokens.get("access_token"),
        "refreshToken": tokens.get("refresh_token"),
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "token_type": tokens.get("token_type"),
        "expires_in": tokens.get("expires_in"),
    }

    return (
        jsonify(response_data),
        200,
    )


@bp.route("/mfa/verify", methods=["POST"])
def verify_mfa():
    """Verify MFA code after initial authentication.

    Request body:
        user_id: int - User ID from login response
        totp_code: str - 6-digit TOTP code

    Returns:
        Tokens on success
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    user_id = data.get("user_id")
    totp_code = data.get("totp_code")

    if not all([user_id, totp_code]):
        return jsonify({"error": "user_id and totp_code are required"}), 400

    result = PortalAuthService.verify_mfa(user_id, totp_code)

    if "error" in result:
        return jsonify({
            "success": False,
            "error": result["error"],
            "errorCode": "INVALID_MFA_CODE"
        }), 401

    # Generate tokens
    tokens = generate_tokens(result)

    response_data = {
        "success": True,
        "user": {
            "id": str(result["id"]),
            "email": result["email"],
            "name": result.get("full_name"),
            "roles": [result.get("tenant_role")] if result.get("tenant_role") else [],
        },
        "token": tokens.get("access_token"),
        "refreshToken": tokens.get("refresh_token"),
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "token_type": tokens.get("token_type"),
        "expires_in": tokens.get("expires_in"),
    }

    return (
        jsonify(response_data),
        200,
    )


@bp.route("/mfa/enable", methods=["POST"])
@portal_token_required
def enable_mfa():
    """Enable MFA for the authenticated user.

    Returns:
        MFA setup info (secret, provisioning URI, backup codes)
    """
    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.enable_mfa(user_id)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@bp.route("/mfa/disable", methods=["POST"])
@portal_token_required
def disable_mfa():
    """Disable MFA for the authenticated user.

    Returns:
        Success status
    """
    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.disable_mfa(user_id)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@bp.route("/password/change", methods=["POST"])
@portal_token_required
def change_password():
    """Change password for the authenticated user.

    Request body:
        current_password: str - Current password
        new_password: str - New password

    Returns:
        Success status
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    current_password = data.get("current_password")
    new_password = data.get("new_password")

    if not all([current_password, new_password]):
        return jsonify({"error": "current_password and new_password are required"}), 400

    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.change_password(user_id, current_password, new_password)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@bp.route("/password/reset", methods=["POST"])
def reset_password():
    """Initiate password reset.

    Request body:
        tenant_id: int - Tenant context
        email: str - Email address

    Returns:
        Success message (token in development only)
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    tenant_id = data.get("tenant_id")
    email = data.get("email")

    if not all([tenant_id, email]):
        return jsonify({"error": "tenant_id and email are required"}), 400

    result = PortalAuthService.reset_password(email, tenant_id)

    return jsonify(result), 200


@bp.route("/refresh", methods=["POST"])
def refresh_token():
    """Refresh access token using refresh token.

    Request body:
        refresh_token: str - Refresh token

    Returns:
        New access token
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        return jsonify({"error": "refresh_token is required"}), 400

    try:
        secret_key = current_app.config.get(
            "JWT_SECRET_KEY"
        ) or current_app.config.get("SECRET_KEY")
        payload = jwt.decode(refresh_token, secret_key, algorithms=["HS256"])

        if payload.get("type") != "portal_refresh":
            return jsonify({"error": "Invalid token type"}), 401

        # Get user
        user_id = int(payload["sub"])
        user = current_app.db.portal_users[user_id]

        if not user or not user.is_active:
            return jsonify({"error": "User not found or inactive"}), 401

        # Generate new tokens (with refresh token rotation for security)
        user_dict = {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "tenant_role": user.tenant_role,
            "global_role": user.global_role,
        }
        tokens = generate_tokens(user_dict)

        return jsonify(tokens), 200

    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Refresh token has expired"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"error": "Invalid refresh token"}), 401


@bp.route("/me", methods=["GET"])
@portal_token_required
def get_current_user():
    """Get current authenticated portal user info.

    Returns:
        User info and permissions
    """
    user_id = int(request.portal_user["sub"])
    user = current_app.db.portal_users[user_id]

    if not user:
        return jsonify({"error": "User not found"}), 404

    permissions = PortalAuthService.get_user_permissions(user_id)

    return (
        jsonify(
            {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "tenant_id": user.tenant_id,
                "tenant_role": user.tenant_role,
                "global_role": user.global_role,
                "mfa_enabled": bool(user.mfa_secret),
                "email_verified": user.email_verified,
                "permissions": permissions,
            }
        ),
        200,
    )


@bp.route("/org-assignments", methods=["POST"])
@portal_token_required
def assign_org_role():
    """Assign a portal user to an organization with a role.

    Requires tenant admin or global admin.

    Request body:
        portal_user_id: int - User to assign
        organization_id: int - Organization
        role: str - Role (admin/maintainer/reader)

    Returns:
        Assignment result
    """
    # Check if user has admin permissions
    if (
        request.portal_user.get("global_role") != "admin"
        and request.portal_user.get("tenant_role") != "admin"
    ):
        return jsonify({"error": "Admin permission required"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    portal_user_id = data.get("portal_user_id")
    organization_id = data.get("organization_id")
    role = data.get("role")

    if not all([portal_user_id, organization_id, role]):
        return (
            jsonify(
                {"error": "portal_user_id, organization_id, and role are required"}
            ),
            400,
        )

    if role not in ["admin", "maintainer", "reader"]:
        return jsonify({"error": "Invalid role"}), 400

    result = PortalAuthService.assign_org_role(portal_user_id, organization_id, role)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200
