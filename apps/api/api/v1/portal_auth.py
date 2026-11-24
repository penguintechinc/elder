"""Portal authentication API endpoints for v2.2.0 Enterprise Edition.

Provides REST endpoints for portal user authentication, registration,
MFA management, and password operations with tenant context.
"""

# flake8: noqa: E501

import json
from datetime import UTC, datetime, timezone
from functools import wraps
from inspect import iscoroutinefunction

import jwt
from pydantic import ValidationError
from quart import Blueprint, current_app, jsonify, make_response, request

from apps.api.auth.portal_cookies import (
    clear_portal_auth_cookies,
    get_access_token_from_cookie,
    get_refresh_token_from_cookie,
    set_portal_auth_cookies,
)
from apps.api.models.schemas import PortalLoginRequest, PortalRegisterRequest
from apps.api.services.portal_auth import (
    PortalAuthService,
    compute_user_mfa_status,
    user_has_mfa_enrolled,
)
from apps.api.utils.api_responses import ApiResponse

bp = Blueprint("portal_auth", __name__)


def portal_token_required(f):
    """Decorator to require portal user JWT authentication.

    Sync/async-aware: wraps `f` with a same-flavor `decorated` function so an
    `async def` view actually gets awaited. A plain `def decorated` wrapping
    an async view would return the un-awaited coroutine object itself as the
    "response", which Quart rejects with `TypeError: The response value type
    (coroutine) is not valid` -- this bit every async view using this
    decorator (e.g. tenants.py::create_tenant) until fixed here.
    """

    def _authenticate() -> tuple | None:
        """Validate the Bearer token, stashing the payload on request.portal_user.

        Returns:
            An `ApiResponse.unauthorized(...)` tuple if auth failed, else None.
        """
        token = None
        auth_header = request.headers.get("Authorization")

        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

        # Browser client: no Authorization header, fall back to the SPA's
        # HttpOnly access-token cookie (gh security audit -- tokens no
        # longer live in localStorage for this decorator to read via header).
        if not token:
            token = get_access_token_from_cookie()

        if not token:
            return ApiResponse.unauthorized("Token is missing")

        try:
            secret_key = current_app.config.get(
                "JWT_SECRET_KEY"
            ) or current_app.config.get("SECRET_KEY")
            payload = jwt.decode(token, secret_key, algorithms=["HS256"])

            if payload.get("type") != "portal_user":
                return ApiResponse.unauthorized("Invalid token type")

            request.portal_user = payload
        except jwt.ExpiredSignatureError:
            return ApiResponse.unauthorized("Token has expired")
        except jwt.InvalidTokenError:
            return ApiResponse.unauthorized("Invalid token")

        return None

    if iscoroutinefunction(f):

        @wraps(f)
        async def decorated(*args, **kwargs):
            error = _authenticate()
            if error is not None:
                return error
            return await f(*args, **kwargs)

    else:

        @wraps(f)
        def decorated(*args, **kwargs):
            error = _authenticate()
            if error is not None:
                return error
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

    now = datetime.now(UTC)

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


async def _issue_token_response(
    result: dict, tokens: dict, *, roles: list, status_code: int = 200
):
    """Build the register/login/MFA-verify success response with HttpOnly auth cookies.

    The JSON body still carries the tokens (react-libs LoginPageBuilder's
    `LoginResponse.token`/`refreshToken` fields, and any non-browser caller
    that predates this change) -- but the browser client must not persist
    them; it relies on the cookies set here instead (gh security audit,
    High: XSS-exfiltratable localStorage tokens).
    """
    response_data = {
        "success": True,
        "user": {
            "id": str(result["id"]),
            "email": result["email"],
            "name": result.get("full_name"),
            "roles": roles,
        },
        "token": tokens.get("access_token"),
        "refreshToken": tokens.get("refresh_token"),
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "token_type": tokens.get("token_type"),
        "expires_in": tokens.get("expires_in"),
        "mfa_enforced": bool(result.get("mfa_enforced", False)),
        "mfa_setup_required": bool(result.get("mfa_setup_required", False)),
        "mfa_grace_expires_at": result.get("mfa_grace_expires_at"),
        "mfa_grace_days_remaining": result.get("mfa_grace_days_remaining"),
        "mfa_locked_out": bool(result.get("mfa_locked_out", False)),
    }

    response = await make_response(jsonify(response_data), status_code)
    secure = not current_app.config.get("DEBUG", False)
    set_portal_auth_cookies(
        response,
        tokens["access_token"],
        tokens["refresh_token"],
        access_max_age=int(
            current_app.config["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds()
        ),
        refresh_max_age=int(
            current_app.config["JWT_REFRESH_TOKEN_EXPIRES"].total_seconds()
        ),
        secure=secure,
    )
    return response


@bp.route("/register", methods=["POST"])
async def register():
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
    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("No data provided")

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
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Valid tenant is required",
                    "errorCode": "INVALID_TENANT",
                }
            ),
            400,
        )

    # Verify tenant exists. tenants is the top-level tenant table itself
    # (nothing to scope it by), and this is a pre-auth public registration
    # flow -- there is no caller tenant context yet.
    tenant = current_app.db.tenants[tenant_id]  # tenant-scope-exempt: see above
    if not tenant or not tenant.is_active:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid tenant",
                    "errorCode": "INVALID_TENANT",
                }
            ),
            400,
        )

    result = await PortalAuthService.create_portal_user(
        tenant_id=tenant_id,
        email=validated_data.email,
        password=validated_data.password,
        full_name=validated_data.full_name,
        tenant_role="reader",  # Default role for self-registration
    )

    if "error" in result:
        status_code = result.get("status_code", 400)
        error_code = "LIMIT_REACHED" if status_code == 402 else "REGISTRATION_FAILED"
        return (
            jsonify(
                {
                    "success": False,
                    "error": result["error"],
                    "errorCode": error_code,
                }
            ),
            status_code,
        )

    # Generate tokens
    tokens = generate_tokens(result)

    return await _issue_token_response(
        result, tokens, roles=["reader"], status_code=201
    )


@bp.route("/login", methods=["POST"])
async def login():
    """Authenticate a portal user.

    Request body:
        tenant: str - Tenant slug (or use tenant_id)
        email: str - Email address (validated format)
        password: str - Password

    Returns:
        Tokens on success, or MFA challenge
        422: Invalid email format or validation error
    """
    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("No data provided")

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
        tenant = (
            current_app.db(current_app.db.tenants.slug == tenant_slug).select().first()
        )
        if tenant:
            tenant_id = tenant.id

    # Fall back to system/default tenant for single-tenant deployments
    if not tenant_id:
        # Try "system" first (common in Elder v3.x), then "default"
        default_tenant = (
            current_app.db(current_app.db.tenants.slug == "system").select().first()
        )
        if not default_tenant:
            default_tenant = (
                current_app.db(current_app.db.tenants.slug == "default")
                .select()
                .first()
            )
        if default_tenant:
            tenant_id = default_tenant.id

    if not tenant_id:
        return ApiResponse.bad_request("Valid tenant is required")

    result = PortalAuthService.authenticate(
        tenant_id, validated_data.email, validated_data.password
    )

    if "error" in result:
        return (
            jsonify(
                {
                    "success": False,
                    "error": result["error"],
                    "errorCode": "INVALID_CREDENTIALS",
                }
            ),
            401,
        )

    if result.get("mfa_required"):
        return (
            jsonify(
                {
                    "success": False,
                    "mfaRequired": True,
                    "user": {
                        "id": str(result.get("user_id")),
                        "email": result.get("email"),
                    },
                }
            ),
            200,
        )

    # Generate tokens
    tokens = generate_tokens(result)

    # Format response for react-libs LoginPageBuilder compatibility
    roles = [result.get("tenant_role")] if result.get("tenant_role") else []
    return await _issue_token_response(result, tokens, roles=roles)


@bp.route("/mfa/verify", methods=["POST"])
async def verify_mfa():
    """Verify MFA code after initial authentication.

    Request body:
        user_id: int - User ID from login response
        totp_code: str - 6-digit TOTP code

    Returns:
        Tokens on success
    """
    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("No data provided")

    user_id = data.get("user_id")
    totp_code = data.get("totp_code")

    if not all([user_id, totp_code]):
        return ApiResponse.bad_request("user_id and totp_code are required")

    result = PortalAuthService.verify_mfa(user_id, totp_code)

    if "error" in result:
        return (
            jsonify(
                {
                    "success": False,
                    "error": result["error"],
                    "errorCode": "INVALID_MFA_CODE",
                }
            ),
            401,
        )

    # Generate tokens
    tokens = generate_tokens(result)

    roles = [result.get("tenant_role")] if result.get("tenant_role") else []
    return await _issue_token_response(result, tokens, roles=roles)


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


@bp.route("/mfa/confirm", methods=["POST"])
@portal_token_required
async def confirm_mfa():
    """Confirm MFA enrollment by verifying a 6-digit TOTP code.

    Request body:
        totp_code: str - 6-digit TOTP code from Google Authenticator

    Returns:
        ``{"verified": True}`` on success, or 400/401 on invalid code
    """
    data = await request.get_json(silent=True)
    if not data or not data.get("totp_code"):
        return ApiResponse.bad_request("totp_code is required")

    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.confirm_mfa(user_id, str(data["totp_code"]))

    if "error" in result:
        status_code = result.get("status_code", 400)
        return jsonify({"verified": False, "error": result["error"]}), status_code

    return jsonify({"verified": True}), 200


@bp.route("/mfa/backup-codes/regenerate", methods=["POST"])
@portal_token_required
def regenerate_mfa_backup_codes():
    """Regenerate single-use MFA backup/bypass codes for the authenticated user.

    Invalidates all previously generated backup codes and returns 10 new codes.

    Returns:
        JSON with ``backup_codes`` list and ``remaining`` count
    """
    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.regenerate_backup_codes(user_id)

    if "error" in result:
        status_code = result.get("status_code", 400)
        return jsonify({"error": result["error"]}), status_code

    return jsonify(result), 200


@bp.route("/mfa/disable", methods=["POST"])
@portal_token_required
def disable_mfa():
    """Disable MFA for the authenticated user.

    Returns:
        Success status, or 403 if MFA is enforced by tenant policy
    """
    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.disable_mfa(user_id)

    if "error" in result:
        status_code = result.get("status_code", 400)
        return jsonify({"error": result["error"]}), status_code

    return jsonify(result), 200


@bp.route("/password/change", methods=["POST"])
@portal_token_required
async def change_password():
    """Change password for the authenticated user.

    Request body:
        current_password: str - Current password
        new_password: str - New password

    Returns:
        Success status
    """
    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("No data provided")

    current_password = data.get("current_password")
    new_password = data.get("new_password")

    if not all([current_password, new_password]):
        return ApiResponse.bad_request("current_password and new_password are required")

    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.change_password(user_id, current_password, new_password)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@bp.route("/password/reset", methods=["POST"])
async def reset_password():
    """Initiate password reset.

    Request body:
        tenant_id: int - Tenant context
        email: str - Email address

    Returns:
        Success message (token in development only)
    """
    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("No data provided")

    tenant_id = data.get("tenant_id")
    email = data.get("email")

    if not all([tenant_id, email]):
        return ApiResponse.bad_request("tenant_id and email are required")

    result = PortalAuthService.reset_password(email, tenant_id)

    return jsonify(result), 200


@bp.route("/refresh", methods=["POST"])
async def refresh_token():
    """Refresh access token using refresh token.

    Request body:
        refresh_token: str - Refresh token (non-browser callers). Browser
            callers omit this -- the refresh token is read from the HttpOnly
            `elder_refresh_token` cookie instead (gh security audit, High:
            tokens no longer live in localStorage for the SPA to resend).

    Returns:
        New access token (also re-set as HttpOnly cookies for the browser)
    """
    data = await request.get_json(force=True, silent=True) or {}

    refresh_token_value = data.get("refresh_token") or get_refresh_token_from_cookie()
    if not refresh_token_value:
        return ApiResponse.bad_request("refresh_token is required")

    try:
        secret_key = current_app.config.get("JWT_SECRET_KEY") or current_app.config.get(
            "SECRET_KEY"
        )
        payload = jwt.decode(refresh_token_value, secret_key, algorithms=["HS256"])

        if payload.get("type") != "portal_refresh":
            return ApiResponse.unauthorized("Invalid token type")

        # Get user: self-lookup via the verified refresh token's own sub
        # claim, never a client param
        user_id = int(payload["sub"])
        user = current_app.db.portal_users[user_id]  # tenant-scope-exempt: see above

        if not user or not user.is_active:
            return ApiResponse.unauthorized("User not found or inactive")

        # Generate new tokens (with refresh token rotation for security)
        user_dict = {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "tenant_role": user.tenant_role,
            "global_role": user.global_role,
        }
        tokens = generate_tokens(user_dict)

        response = await make_response(jsonify(tokens), 200)
        secure = not current_app.config.get("DEBUG", False)
        set_portal_auth_cookies(
            response,
            tokens["access_token"],
            tokens["refresh_token"],
            access_max_age=int(
                current_app.config["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds()
            ),
            refresh_max_age=int(
                current_app.config["JWT_REFRESH_TOKEN_EXPIRES"].total_seconds()
            ),
            secure=secure,
        )
        return response

    except jwt.ExpiredSignatureError:
        response = await make_response(
            *ApiResponse.unauthorized("Refresh token has expired")
        )
        clear_portal_auth_cookies(response)
        return response
    except jwt.InvalidTokenError:
        response = await make_response(
            *ApiResponse.unauthorized("Invalid refresh token")
        )
        clear_portal_auth_cookies(response)
        return response


@bp.route("/logout", methods=["POST"])
async def logout():
    """Clear the SPA's HttpOnly auth cookies.

    Portal-auth JWTs are stateless -- there's no server-side session to
    invalidate -- so this is a cookie-clear operation only, mirroring
    apps/api/web's SSR logout (apps.api.auth.web_auth.clear_session_cookie).
    Deliberately unauthenticated: it must still succeed (and clear a stale
    refresh cookie) even if the caller's access token already expired.
    """
    response = await make_response(jsonify({"message": "Logged out successfully"}), 200)
    clear_portal_auth_cookies(response)
    return response


@bp.route("/me", methods=["GET"])
@portal_token_required
def get_current_user():
    """Get current authenticated portal user info.

    Returns:
        User info and permissions
    """
    # Self-lookup via the validated portal token's own sub claim, never a
    # client param
    user_id = int(request.portal_user["sub"])
    user = current_app.db.portal_users[user_id]  # tenant-scope-exempt: see above

    if not user:
        return ApiResponse.error("User not found", 404)

    permissions = PortalAuthService.get_user_permissions(user_id)
    mfa_enabled = user_has_mfa_enrolled(user)
    mfa_status = compute_user_mfa_status(
        user.tenant_id,
        mfa_enabled=mfa_enabled,
        user_created_at=getattr(user, "created_at", None),
    )

    raw_backup_codes = getattr(user, "mfa_backup_codes", None)
    if isinstance(raw_backup_codes, str):
        try:
            raw_backup_codes = json.loads(raw_backup_codes)
        except ValueError:
            raw_backup_codes = []
    backup_codes_remaining = (
        len(raw_backup_codes) if isinstance(raw_backup_codes, list) else 0
    )
    last_login_at = getattr(user, "last_login_at", None)
    created_at = getattr(user, "created_at", None)

    org_id = getattr(user, "organization_id", None)
    org_name = None
    db_tables = getattr(current_app.db, "tables", None)
    if org_id is None and (
        db_tables is None or "portal_user_org_assignments" in db_tables
    ):
        try:
            assignment = (
                current_app.db(
                    current_app.db.portal_user_org_assignments.portal_user_id == user.id
                )
                .select(limitby=(0, 1))
                .first()
            )
            if assignment is not None:
                org_id = assignment.organization_id
        except Exception:
            org_id = None
    if org_id is not None and (db_tables is None or "organizations" in db_tables):
        try:
            org = (
                current_app.db(
                    (current_app.db.organizations.id == org_id)
                    & (current_app.db.organizations.tenant_id == user.tenant_id)
                )
                .select(limitby=(0, 1))
                .first()
            )
            if org is not None:
                org_name = org.name
        except Exception:
            org_name = None

    return (
        jsonify(
            {
                "id": user.id,
                "username": getattr(user, "username", None) or user.email,
                "email": user.email,
                "full_name": user.full_name,
                "tenant_id": user.tenant_id,
                "tenant_role": user.tenant_role,
                "global_role": user.global_role,
                "organization_id": org_id,
                "organization_name": org_name,
                "identity_type": getattr(user, "identity_type", None) or "human",
                "is_active": bool(getattr(user, "is_active", True)),
                "auth_provider": getattr(user, "auth_provider", None) or "local",
                "mfa_enabled": mfa_enabled,
                "mfa_backup_codes_remaining": backup_codes_remaining,
                **mfa_status,
                "email_verified": user.email_verified,
                "last_login_at": (
                    last_login_at.isoformat()
                    if hasattr(last_login_at, "isoformat")
                    else last_login_at
                ),
                "created_at": (
                    created_at.isoformat()
                    if hasattr(created_at, "isoformat")
                    else created_at
                ),
                "permissions": permissions,
            }
        ),
        200,
    )


@bp.route("/me", methods=["PATCH"])
@portal_token_required
def update_current_user():
    """Update current authenticated portal user info.

    Allowed fields: full_name, organization_id
    Email cannot be changed via this endpoint.

    Returns:
        Updated user info
    """
    # Self-lookup via the validated portal token's own sub claim, never a
    # client param
    user_id = int(request.portal_user["sub"])
    user = current_app.db.portal_users[user_id]  # tenant-scope-exempt: see above

    if not user:
        return ApiResponse.error("User not found", 404)

    data = request.get_json(silent=True) or {}

    # NOTE (flagged, not fixed here -- out of tenant-scoping-backfill
    # scope): organization_id below is accepted from the request body with
    # no check that the target organization belongs to this user's own
    # tenant, unlike the equivalent fix in api/v1/profile.py. See final
    # report for follow-up.
    updates = {}
    if "full_name" in data:
        updates["full_name"] = data["full_name"]
    if "organization_id" in data:
        updates["organization_id"] = data["organization_id"]

    if updates:
        current_app.db(current_app.db.portal_users.id == user_id).update(**updates)
        current_app.db.commit()
        user = current_app.db.portal_users[user_id]  # tenant-scope-exempt

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
            }
        ),
        200,
    )


@bp.route("/org-assignments", methods=["POST"])
@portal_token_required
async def assign_org_role():
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
        return ApiResponse.forbidden("Admin permission required")

    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("No data provided")

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
        return ApiResponse.bad_request("Invalid role")

    result = PortalAuthService.assign_org_role(portal_user_id, organization_id, role)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200
