"""Authentication API endpoints using PyDAL with async/await."""

# flake8: noqa: E501

import os
from dataclasses import asdict
from datetime import UTC, datetime, timezone

import structlog
from pydantic import ValidationError
from quart import Blueprint, current_app, g, jsonify, make_response, request
from werkzeug.security import generate_password_hash

from apps.api.auth import generate_token, login_required, verify_password
from apps.api.auth.jwt_handler import (
    IDENTITY_REFRESH_TOKEN_TYPES,
    carried_auth_time,
    interactive_auth_time,
    verify_token,
)
from apps.api.auth.portal_cookies import set_portal_auth_cookies
from apps.api.auth.web_auth import COOKIE_NAME, clear_session_cookie, set_session_cookie
from apps.api.models.dataclasses import IdentityDTO, from_pydal_row
from apps.api.models.schemas import LoginRequest, RegisterRequest
from apps.api.services.webhooks.user_events import (
    UserCreatedEvent,
    schedule_user_created_webhooks,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.signup_policy import is_signup_email_allowed
from apps.api.utils.village_id import mint_village_id

logger = structlog.get_logger(__name__)
bp = Blueprint("auth", __name__)


@bp.route("/register", methods=["POST"])
async def register():
    """
    Register a new user (local authentication).

    Request Body:
        {
            "username": "email@example.com" (must be valid email for portal auth),
            "password": "string" (minimum 8 characters),
            "email": "email@example.com" (must match username),
            "full_name": "string" (optional)
        }

    Returns:
        201: User created successfully
        400: Validation error or username already exists
        422: Invalid email format
    """
    db = current_app.db

    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("Request body must be JSON")

    # Validate request using Pydantic schema
    try:
        validated_data = RegisterRequest(**data)
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

    if not is_signup_email_allowed(validated_data.email):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Signups are restricted to approved email domains",
                    "errorCode": "SIGNUP_DOMAIN_NOT_ALLOWED",
                }
            ),
            403,
        )

    # Capture request context before entering threadpool
    ip_address = request.remote_addr
    user_agent = request.headers.get("User-Agent", "")[:512]

    # Check if username or email already exists and create user
    def create_user():
        # Check if username already exists
        existing = (
            db(db.identities.username == validated_data.username).select().first()
        )
        if existing:
            return None, "Username already exists", 400

        # Check if email already exists
        existing_email = (
            db(db.identities.email == validated_data.email).select().first()
        )
        if existing_email:
            return None, "Email already exists", 400

        # Get default tenant for new registrations
        default_tenant = db(db.tenants.id > 0).select(limitby=(0, 1)).first()
        default_tenant_id = default_tenant.id if default_tenant else None

        # Create new identity
        now = datetime.now(UTC)
        identity_id = db.identities.insert(
            username=validated_data.username,
            email=validated_data.email,
            full_name=validated_data.full_name,
            identity_type="human",
            auth_provider="local",
            password_hash=generate_password_hash(validated_data.password),
            is_active=True,
            is_superuser=False,
            mfa_enabled=False,
            tenant_id=default_tenant_id,
            village_id=mint_village_id(default_tenant_id),
            # NOT NULL columns with no DB-level server default — penguin-dal's
            # insert() does not apply SQLAlchemy Column(default=...) for
            # kwargs omitted entirely, so these must be supplied explicitly
            # (see apps/api/api/v1/identities.py's identical comment).
            must_change_password=False,
            portal_role="observer",
            created_at=now,
            updated_at=now,
        )
        db.commit()

        # Get created identity
        identity = db.identities[identity_id]  # tenant-scope-exempt

        # Create audit log
        _create_audit_log_sync(
            db=db,
            identity_id=identity.id,
            action="create",
            resource_type="identity",
            resource_id=identity.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return identity, None, None

    identity, error, status = await run_in_threadpool(create_user)

    if error:
        return jsonify({"error": error}), status

    if identity and getattr(identity, "tenant_id", None):
        try:
            schedule_user_created_webhooks(
                db,
                UserCreatedEvent(
                    user_id=identity.id,
                    tenant_id=identity.tenant_id,
                    user_source="identity",
                    identity_type=identity.identity_type or "human",
                    auth_provider="local",
                    is_superuser=bool(identity.is_superuser),
                    portal_role=identity.portal_role or "observer",
                    organization_id=getattr(identity, "organization_id", None),
                    is_active=bool(identity.is_active),
                ),
            )
        except (
            Exception
        ) as exc:  # pragma: no cover - defensive; must never fail register
            logger.warning(
                "user_created_webhook_schedule_failed",
                user_id=identity.id,
                error=str(exc)[:200],
            )

    return (
        jsonify(
            {
                "message": "User registered successfully",
                "user": {
                    "id": identity.id,
                    "username": identity.username,
                    "email": identity.email,
                },
            }
        ),
        201,
    )


@bp.route("/guest-enabled", methods=["GET"])
async def guest_enabled():
    """
    Check if guest login is enabled.

    Returns:
        200: Guest login status
        {
            "enabled": boolean,
            "username": "string" (only if enabled)
        }
    """
    enable_guest = os.getenv("ENABLE_GUEST_LOGIN", "false").lower() == "true"

    response = {"enabled": enable_guest}

    if enable_guest:
        response["username"] = os.getenv("GUEST_USERNAME", "guest")

    return jsonify(response), 200


@bp.route("/login", methods=["POST"])
async def login():
    """
    Login with email and password (portal authentication).

    Request Body:
        {
            "username": "email@example.com" (must be valid email),
            "password": "string"
        }

    Returns:
        200: Login successful with access and refresh tokens
        401: Invalid credentials
        422: Invalid email format
    """
    db = current_app.db

    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("Request body must be JSON")

    # Validate request using Pydantic schema
    try:
        validated_data = LoginRequest(**data)
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

    # Capture request context before entering threadpool
    ip_address = request.remote_addr
    user_agent = request.headers.get("User-Agent", "")[:512]

    # Find user and verify password
    def authenticate():
        identity = (
            db(db.identities.username == validated_data.username).select().first()
        )

        if not identity:
            return None, "Invalid username or password", 401

        # Verify password using the identity row
        if not verify_password(identity, validated_data.password):
            # Create failed login audit log
            _create_audit_log_sync(
                db=db,
                identity_id=identity.id,
                action="login",
                resource_type="auth",
                success=False,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return None, "Invalid username or password", 401

        # Check if account is active
        if not identity.is_active:
            return None, "Account is inactive", 401

        # Update last login
        db(db.identities.id == identity.id).update(last_login_at=datetime.now(UTC))
        db.commit()

        # Refresh identity data after update
        identity = db.identities[identity.id]  # tenant-scope-exempt

        # Create successful login audit log
        _create_audit_log_sync(
            db=db,
            identity_id=identity.id,
            action="login",
            resource_type="auth",
            success=True,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return identity, None, None

    identity, error, status = await run_in_threadpool(authenticate)

    if error:
        return jsonify({"error": error}), status

    # Generate tokens
    # Password login is an interactive authentication: stamp auth_time on
    # both tokens so a later refresh can carry (never renew) it.
    auth_time = interactive_auth_time()
    access_token = generate_token(identity, "access", auth_time=auth_time)
    refresh_token = generate_token(identity, "refresh", auth_time=auth_time)

    response = await make_response(
        jsonify(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "user": {
                    "id": identity.id,
                    "username": identity.username,
                    "email": identity.email,
                    "full_name": identity.full_name,
                    "is_superuser": identity.is_superuser,
                },
            }
        ),
        200,
    )
    # Also set an HttpOnly cookie so the SSR web UI can authenticate
    secure = not current_app.config.get("DEBUG", False)
    set_session_cookie(response, access_token, secure=secure)
    # And set the SPA portal-auth HttpOnly/Secure/SameSite + CSRF cookies so
    # the browser client never has to keep the raw JWTs (gh #285) -- the JSON
    # body keeps them only for non-browser callers, mirroring portal-auth.
    set_portal_auth_cookies(
        response,
        access_token,
        refresh_token,
        access_max_age=int(
            current_app.config["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds()
        ),
        refresh_max_age=int(
            current_app.config["JWT_REFRESH_TOKEN_EXPIRES"].total_seconds()
        ),
        secure=secure,
    )
    return response


@bp.route("/logout", methods=["POST"])
@login_required
async def logout():
    """
    Logout current user.

    Returns:
        200: Logout successful
    """
    db = current_app.db
    ip_address = request.remote_addr
    user_agent = request.headers.get("User-Agent", "")[:512]
    identity_id = g.current_user.id

    # Create logout audit log
    await run_in_threadpool(
        lambda: _create_audit_log_sync(
            db=db,
            identity_id=identity_id,
            action="logout",
            resource_type="auth",
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )

    response = await make_response(jsonify({"message": "Logged out successfully"}), 200)
    clear_session_cookie(response)
    return response


@bp.route("/me", methods=["GET"])
@login_required
async def get_current_user_info():
    """
    Get current authenticated user information.

    Returns:
        200: User information
    """
    db = current_app.db

    # Fetch fresh user data from database
    identity = await run_in_threadpool(
        lambda: db.identities[g.current_user.id]  # tenant-scope-exempt
    )

    if not identity:
        return ApiResponse.error("User not found", 404)

    identity_dto = from_pydal_row(identity, IdentityDTO)
    return jsonify(asdict(identity_dto)), 200


@bp.route("/change-password", methods=["POST"])
@login_required
async def change_password():
    """
    Change current user's password.

    Request Body:
        {
            "current_password": "string",
            "new_password": "string"
        }

    Returns:
        200: Password changed successfully
        400: Validation error
        401: Current password incorrect
    """
    db = current_app.db

    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("Request body must be JSON")

    # Validate required fields
    if not data.get("current_password"):
        return ApiResponse.bad_request("current_password is required")
    if not data.get("new_password"):
        return ApiResponse.bad_request("new_password is required")

    # Validate new password length
    if len(data["new_password"]) < 8:
        return ApiResponse.bad_request("new_password must be at least 8 characters")

    ip_address = request.remote_addr
    user_agent = request.headers.get("User-Agent", "")[:512]

    # Verify current password and update
    def update_password():
        identity = db.identities[g.current_user.id]  # tenant-scope-exempt

        if not identity:
            return None, "User not found", 404

        # Verify current password
        if not verify_password(identity, data["current_password"]):
            return None, "Current password is incorrect", 401

        # Update password
        db(db.identities.id == identity.id).update(
            password_hash=generate_password_hash(data["new_password"])
        )
        db.commit()

        # Create audit log
        _create_audit_log_sync(
            db=db,
            identity_id=identity.id,
            action="update",
            resource_type="identity",
            resource_id=identity.id,
            changes={"action": "password_change"},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return True, None, None

    result, error, status = await run_in_threadpool(update_password)

    if error:
        return jsonify({"error": error}), status

    return jsonify({"message": "Password changed successfully"}), 200


@bp.route("/refresh", methods=["POST"])
async def refresh_token_endpoint():
    """
    Refresh access token using refresh token.

    Request Body:
        {
            "refresh_token": "string"
        }

    Returns:
        200: New access token
        401: Invalid refresh token
    """
    db = current_app.db

    data = await request.get_json() or {}
    refresh_token_str = data.get("refresh_token")

    if not refresh_token_str:
        return ApiResponse.bad_request("Refresh token required")

    # Verify refresh token
    payload = verify_token(refresh_token_str, IDENTITY_REFRESH_TOKEN_TYPES)

    if not payload or payload.get("type") != "refresh":
        return ApiResponse.unauthorized("Invalid refresh token")

    # Get identity: self-lookup via the verified refresh token's own sub
    # claim, never a client param
    identity = await run_in_threadpool(
        lambda: db.identities[payload["sub"]]  # tenant-scope-exempt
    )

    if not identity or not identity.is_active:
        return ApiResponse.unauthorized("User not found or inactive")

    # Generate new access token. auth_time is copied from the refresh
    # token, never reset: refreshing is not re-authenticating.
    access_token = generate_token(
        identity, "access", auth_time=carried_auth_time(payload)
    )

    return (
        jsonify(
            {
                "access_token": access_token,
                "token_type": "Bearer",
            }
        ),
        200,
    )


@bp.route("/captcha-challenge", methods=["GET"])
def captcha_challenge():
    """Generate ALTCHA proof-of-work challenge."""
    from apps.api.utils.altcha import create_altcha_challenge

    return jsonify(create_altcha_challenge())


def _create_audit_log_sync(
    db,
    identity_id: int,
    action: str,
    resource_type: str,
    resource_id: int = None,
    success: bool = True,
    changes: dict = None,
    ip_address: str = None,
    user_agent: str = None,
):
    """
    Helper to create audit log entries (synchronous, call from threadpool).

    ip_address and user_agent must be passed in from the async context
    (request context is not available inside a threadpool).
    """
    now = datetime.now(UTC)
    db.audit_logs.insert(
        identity_id=identity_id,
        action_name=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=changes,  # PyDAL uses 'details' not 'changes'
        ip_address=ip_address or "",
        user_agent=(user_agent or "")[:512],
        success=success,
        created_at=now,
    )
    db.commit()
