"""Portal authentication API endpoints for v2.2.0 Enterprise Edition.

Provides REST endpoints for portal user authentication, registration,
MFA management, and password operations with tenant context.
"""

# flake8: noqa: E501

import json
import os
from datetime import UTC, datetime, timezone
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any

import jwt
import structlog
from pydantic import ValidationError
from quart import Blueprint, current_app, jsonify, make_response, request
from werkzeug.security import generate_password_hash

from apps.api.auth.jwt_handler import (
    PORTAL_ACCESS_TOKEN_TYPES,
    PORTAL_REFRESH_TOKEN_TYPES,
    PRINCIPAL_PORTAL_USER,
    carried_auth_time,
    interactive_auth_time,
    verify_token,
)
from apps.api.auth.portal_cookies import (
    clear_portal_auth_cookies,
    get_access_token_from_cookie,
    get_refresh_token_from_cookie,
    set_mfa_enrollment_cookies,
    set_portal_auth_cookies,
)
from apps.api.models.schemas import PortalLoginRequest, PortalRegisterRequest
from apps.api.services.portal_auth import (
    PortalAuthService,
    compute_user_mfa_status,
    is_tenant_mfa_enforced,
    parse_tenant_settings,
    user_has_mfa_enrolled,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.email import (
    EmailDeliveryError,
    EmailNotConfiguredError,
    is_smtp_configured,
    send_email,
)
from apps.api.utils.onboarding_emails import (
    issue_email_verify_token,
    send_verification_email,
)
from apps.api.utils.onboarding_tokens import (
    PURPOSE_ADMIN_INVITE,
    PURPOSE_EMAIL_VERIFY,
    consume_onboarding_token,
    peek_onboarding_token,
)
from apps.api.utils.signup_policy import (
    is_email_verification_required,
    is_signup_email_allowed,
)

logger = structlog.get_logger(__name__)
bp = Blueprint("portal_auth", __name__)


def _no_store(response):
    """Mark a response carrying MFA setup material as non-cacheable.

    The TOTP provisioning URI and backup codes are shown exactly once; no
    browser, proxy or CDN cache may retain a copy.
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _bearer_or_cookie_token() -> str | None:
    """Return the caller's token from the Authorization header or auth cookie.

    Browser clients send no Authorization header and rely on the SPA's
    HttpOnly access-token cookie instead.
    """
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ")[1]
    return get_access_token_from_cookie()


def _authenticate_portal_request(*, allow_mfa_enrollment: bool) -> tuple | None:
    """Validate the caller's token, stashing the payload on request.portal_user.

    A full portal session token is always accepted. The enrolment-only token
    (issued when a user's MFA grace period has expired) is accepted solely
    when ``allow_mfa_enrollment`` is set -- i.e. on the MFA setup routes --
    and is signed with its own key, so every other route rejects it.

    Returns:
        An `ApiResponse.unauthorized(...)` tuple if auth failed, else None.
    """
    token = _bearer_or_cookie_token()
    if not token:
        return ApiResponse.unauthorized("Token is missing")

    request.portal_mfa_enrollment = False
    try:
        payload = verify_token(token, PORTAL_ACCESS_TOKEN_TYPES)
    except (TypeError, ValueError):
        payload = None
    if payload:
        request.portal_user = payload
        return None

    enrollment = (
        PortalAuthService.verify_mfa_enrollment_token(token)
        if allow_mfa_enrollment
        else None
    )
    if enrollment is None:
        return ApiResponse.unauthorized("Invalid or expired token")
    request.portal_user = enrollment
    request.portal_mfa_enrollment = True

    return None


def _portal_auth_decorator(f, *, allow_mfa_enrollment: bool):
    """Wrap a view with portal authentication (sync/async-aware).

    Wraps `f` with a same-flavor `decorated` function so an `async def` view
    actually gets awaited. A plain `def decorated` wrapping an async view
    would return the un-awaited coroutine object itself as the "response",
    which Quart rejects with `TypeError: The response value type (coroutine)
    is not valid`.
    """
    if iscoroutinefunction(f):

        @wraps(f)
        async def decorated(*args, **kwargs):
            error = _authenticate_portal_request(
                allow_mfa_enrollment=allow_mfa_enrollment
            )
            if error is not None:
                return error
            return await f(*args, **kwargs)

    else:

        @wraps(f)
        def decorated(*args, **kwargs):
            error = _authenticate_portal_request(
                allow_mfa_enrollment=allow_mfa_enrollment
            )
            if error is not None:
                return error
            return f(*args, **kwargs)

    return decorated


def portal_token_required(f):
    """Decorator to require a full portal user session token.

    Never accepts the MFA enrolment-only token: a user whose MFA grace
    period has expired cannot reach any route guarded by this decorator.
    """
    return _portal_auth_decorator(f, allow_mfa_enrollment=False)


def portal_token_or_mfa_enrollment_required(f):
    """Decorator for the MFA setup routes only.

    Accepts a full session token or the enrolment-only token, so a user who
    must enrol before getting a session can do exactly that and nothing else.
    """
    return _portal_auth_decorator(f, allow_mfa_enrollment=True)


def _client_fingerprint() -> str | None:
    """Return the client fingerprint an MFA challenge is bound to.

    The User-Agent is the only signal stable across the two login requests;
    client IPs change behind proxies and mobile networks.
    """
    return request.headers.get("User-Agent")


def _submitted_mfa_code(data: dict) -> str | None:
    """Extract the second-factor code from a request body, if one was sent.

    Accepts the react-libs LoginPageBuilder field (``mfaCode``) and the
    API-style names; anything that is not a string or integer is ignored.
    """
    for key in ("mfaCode", "mfa_code", "totp_code", "code"):
        value = data.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return None


def _mfa_error_response(result: dict):
    """Build the JSON error for a failed second-factor check.

    Maps the service result to 401 (bad code / dead challenge) or 429
    (account MFA step locked) without revealing which user was targeted.
    """
    if result.get("locked"):
        error_code = "MFA_LOCKED"
    elif result.get("challenge_invalid"):
        error_code = "MFA_CHALLENGE_INVALID"
    else:
        error_code = "INVALID_MFA_CODE"
    return (
        jsonify(
            {
                "success": False,
                "error": result["error"],
                "errorCode": error_code,
                "mfaChallengeInvalid": bool(result.get("challenge_invalid")),
            }
        ),
        result.get("status_code", 401),
    )


async def _issue_mfa_enrollment_response(result: dict):
    """Answer a login whose MFA grace period has expired.

    No session is issued: the caller gets only the enrolment-scoped token
    (also as the HttpOnly access cookie, with no refresh cookie) which can
    reach the MFA setup routes and logout, nothing else.
    """
    token = PortalAuthService.issue_mfa_enrollment_token(result)
    max_age = PortalAuthService.MFA_ENROLLMENT_TOKEN_TTL_MINUTES * 60
    response = await make_response(
        jsonify(
            {
                "success": True,
                "mfaEnrollmentRequired": True,
                "mfa_enrollment_required": True,
                "enrollment_token": token,
                "token_type": "Bearer",
                "expires_in": max_age,
                "user": {"email": result["email"], "name": result.get("full_name")},
                "mfa_enforced": True,
                "mfa_setup_required": True,
                "mfa_grace_expires_at": result.get("mfa_grace_expires_at"),
                "mfa_grace_days_remaining": 0,
                "mfa_locked_out": True,
            }
        ),
        200,
    )
    clear_portal_auth_cookies(response)
    set_mfa_enrollment_cookies(
        response,
        token,
        max_age=max_age,
        secure=not current_app.config.get("DEBUG", False),
    )
    return _no_store(response)


def generate_tokens(user: dict, auth_time: int | None = None) -> dict:
    """Generate access and refresh tokens for a portal user.

    `auth_time` (epoch seconds) is stamped on both tokens: pass
    `interactive_auth_time()` from an interactive login (password, MFA
    completion, SSO callback) and `carried_auth_time(refresh_payload)` from
    the refresh endpoint. Omitted -> no claim, which recent-authentication
    checks treat as "not recent".
    """
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
    access_payload["principal_kind"] = PRINCIPAL_PORTAL_USER
    access_payload["iss"] = current_app.config.get("JWT_ISSUER", "elder-api")
    access_payload["aud"] = current_app.config.get("JWT_AUDIENCE", "elder-api")
    if auth_time is not None:
        access_payload["auth_time"] = int(auth_time)
    access_token = jwt.encode(access_payload, secret_key, algorithm="HS256")

    # Refresh token
    refresh_payload = {
        "sub": str(user["id"]),
        "tenant_id": user["tenant_id"],
        "type": "portal_refresh",
        "principal_kind": PRINCIPAL_PORTAL_USER,
        "iss": current_app.config.get("JWT_ISSUER", "elder-api"),
        "aud": current_app.config.get("JWT_AUDIENCE", "elder-api"),
        "exp": now + refresh_token_expires,
        "iat": now,
    }
    if auth_time is not None:
        refresh_payload["auth_time"] = int(auth_time)
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

    tenant_settings = parse_tenant_settings(getattr(tenant, "settings", None))
    resolved_tenant_slug = getattr(tenant, "slug", None) or tenant_slug

    if not is_signup_email_allowed(
        validated_data.email,
        tenant_settings=tenant_settings,
        tenant_slug=resolved_tenant_slug,
    ):
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

    if is_email_verification_required(tenant_settings=tenant_settings):
        verify_token = issue_email_verify_token(result["id"], tenant_id)
        try:
            await send_verification_email(
                to_email=validated_data.email,
                token=verify_token,
                tenant_name=getattr(tenant, "name", None),
            )
        except (EmailNotConfiguredError, EmailDeliveryError, ValueError) as exc:
            logger.error(
                "signup_verification_email_failed",
                tenant_id=tenant_id,
                error_type=type(exc).__name__,
            )
            # Clean up the unverified account so the caller can retry cleanly
            try:
                current_app.db(current_app.db.portal_users.id == result["id"]).delete()
                current_app.db.commit()
            except Exception as cleanup_exc:  # pragma: no cover - defensive
                logger.warning(
                    "signup_verification_cleanup_failed",
                    tenant_id=tenant_id,
                    error_type=type(cleanup_exc).__name__,
                )
            return ApiResponse.service_unavailable(
                "Unable to send verification email. Please try again later."
            )

        return (
            jsonify(
                {
                    "success": True,
                    "verificationRequired": True,
                    "message": (
                        "Account created. Please check your email to verify "
                        "your address before signing in."
                    ),
                }
            ),
            201,
        )

    if is_smtp_configured():
        site_url = os.environ.get("SITE_URL", "").rstrip("/")
        login_line = f"\nSign in at: {site_url}/login\n" if site_url else ""
        try:
            await send_email(
                to_addresses=[validated_data.email],
                subject="Welcome to Elder",
                body_text=(
                    "Your Elder account has been created.\n"
                    f"{login_line}\n"
                    "If you did not request this account, please contact your administrator."
                ),
            )
        except (EmailNotConfiguredError, EmailDeliveryError, ValueError) as exc:
            logger.warning(
                "signup_welcome_email_failed",
                error_type=type(exc).__name__,
            )

    # Generate tokens
    tokens = generate_tokens(result, auth_time=interactive_auth_time())

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
        mfaCode: str (optional) - TOTP or backup code, sent together with
            the credentials to complete an MFA login in one request

    Returns:
        Tokens on success.
        MFA enrolled and no code sent: ``mfaRequired`` plus an opaque,
        short-lived ``mfaChallenge`` for POST /mfa/verify (no user id).
        MFA required by policy and grace period expired:
        ``mfaEnrollmentRequired`` plus an enrolment-scoped token only.
        422: Invalid email format or validation error
    """
    data = await request.get_json()
    if not isinstance(data, dict) or not data:
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

    # Server-side ALTCHA CAPTCHA verification:
    # - Always verify captchaToken whenever supplied (rejects tampered/expired/replayed tokens).
    # - Require a valid captchaToken once the account hits the failed-attempt threshold (3),
    #   skipping step-2 MFA submissions (LoginPageBuilder does not resend captchaToken with mfaCode).
    mfa_code = _submitted_mfa_code(data)
    captcha_token = data.get("captchaToken") or data.get("captcha_token")
    if mfa_code is None:
        from apps.api.utils.altcha import requires_login_captcha, verify_altcha_payload

        if captcha_token is not None and str(captcha_token).strip() != "":
            ok, reason = verify_altcha_payload(captcha_token)
            if not ok:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": reason or "Invalid security check",
                            "errorCode": "CAPTCHA_INVALID",
                            "captchaRequired": True,
                        }
                    ),
                    400,
                )
        elif requires_login_captcha(tenant_id, validated_data.email):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Please complete the security check",
                        "errorCode": "CAPTCHA_REQUIRED",
                        "captchaRequired": True,
                    }
                ),
                400,
            )

    result = PortalAuthService.authenticate(
        tenant_id, validated_data.email, validated_data.password
    )

    if "error" in result:
        status_code = result.get("status_code", 401)
        error_code = result.get("error_code", "INVALID_CREDENTIALS")
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

    if result.get("mfa_required"):
        mfa_code = _submitted_mfa_code(data)
        if mfa_code is None:
            # Password accepted: hand back an opaque, server-side challenge.
            # The account id is deliberately NOT disclosed.
            challenge_id = PortalAuthService.create_mfa_challenge(
                result["user_id"], result["tenant_id"], _client_fingerprint()
            )
            return _no_store(
                jsonify(
                    {
                        # LoginPageBuilder checks `if (!data.success)` before
                        # `if (data.mfaRequired)`, so step-1 password success
                        # must report `success: True` with zero tokens/cookies.
                        "success": True,
                        "mfaRequired": True,
                        "mfaChallenge": challenge_id,
                        "mfa_challenge": challenge_id,
                        "mfaChallengeExpiresIn": (
                            PortalAuthService.MFA_CHALLENGE_TTL_SECONDS
                        ),
                    }
                )
            ), 200

        # Password AND code in one request (react-libs LoginPageBuilder
        # re-posts the credentials with `mfaCode`): the password was just
        # verified above, so the second factor is bound to it by construction.
        result = PortalAuthService.verify_mfa(result["user_id"], mfa_code)
        if "error" in result:
            return _mfa_error_response(result)

    if result.get("mfa_locked_out"):
        # Enrolment required and the grace period is over: no session.
        return await _issue_mfa_enrollment_response(result)

    # Generate tokens
    tokens = generate_tokens(result, auth_time=interactive_auth_time())

    # Format response for react-libs LoginPageBuilder compatibility
    roles = [result.get("tenant_role")] if result.get("tenant_role") else []
    return await _issue_token_response(result, tokens, roles=roles)


@bp.route("/mfa/verify", methods=["POST"])
async def verify_mfa():
    """Complete a login's MFA step using the challenge from the password step.

    Request body:
        mfa_challenge: str - Opaque challenge id returned by /login
        totp_code: str - 6-digit TOTP code or 8-character backup code

    A bare user id is not accepted: without a challenge (which only a correct
    password yields) nobody can reach an account's second factor.

    Returns:
        Tokens on success; 401 on a bad code or dead challenge, 429 while the
        account's MFA step is locked
    """
    data = await request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return ApiResponse.bad_request("No data provided")

    challenge_id = data.get("mfa_challenge") or data.get("mfaChallenge")
    totp_code = _submitted_mfa_code(data)

    if not isinstance(challenge_id, str) or not challenge_id.strip() or not totp_code:
        return ApiResponse.bad_request("mfa_challenge and totp_code are required")

    result = PortalAuthService.verify_mfa_challenge(
        challenge_id, totp_code, _client_fingerprint()
    )

    if "error" in result:
        return _mfa_error_response(result)

    # Generate tokens
    tokens = generate_tokens(result, auth_time=interactive_auth_time())

    roles = [result.get("tenant_role")] if result.get("tenant_role") else []
    return await _issue_token_response(result, tokens, roles=roles)


@bp.route("/mfa/enable", methods=["POST"])
@portal_token_or_mfa_enrollment_required
async def enable_mfa():
    """Start MFA setup for the authenticated user.

    The new secret stays pending until /mfa/confirm succeeds. If MFA is
    already enrolled, a current TOTP or backup code is required.

    Request body (only when re-enrolling):
        current_code: str - Current TOTP or backup code

    Returns:
        MFA setup info (secret, provisioning URI, backup codes)
    """
    data = await request.get_json(silent=True)
    current_code = _current_factor_code(data)
    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.enable_mfa(user_id, current_code)

    if "error" in result:
        return _mfa_change_error(result)

    # Setup material (secret / provisioning URI / backup codes) is returned
    # once and must never be cached or logged.
    return _no_store(jsonify(result)), 200


@bp.route("/mfa/confirm", methods=["POST"])
@portal_token_or_mfa_enrollment_required
async def confirm_mfa():
    """Confirm MFA enrollment by verifying a 6-digit TOTP code.

    Activates a pending enrolment. When called with the enrolment-only
    token the cookies are cleared afterwards: the user signs in again, now
    with password + MFA, to obtain a real session.

    Request body:
        totp_code: str - 6-digit TOTP code from Google Authenticator

    Returns:
        ``{"verified": True}`` on success, or 400/401 on invalid code
    """
    data = await request.get_json(silent=True)
    if not isinstance(data, dict) or not data.get("totp_code"):
        return ApiResponse.bad_request("totp_code is required")

    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.confirm_mfa(user_id, str(data["totp_code"]))

    if "error" in result:
        status_code = result.get("status_code", 400)
        return jsonify({"verified": False, "error": result["error"]}), status_code

    if getattr(request, "portal_mfa_enrollment", False):
        response = await make_response(
            jsonify({"verified": True, "reauthenticate": True}), 200
        )
        clear_portal_auth_cookies(response)
        return response

    return jsonify({"verified": True}), 200


def _current_factor_code(data: Any) -> str | None:
    """Extract the caller's current TOTP/backup code from a request body.

    Used by the routes that change MFA state (re-enrol, disable, regenerate
    backup codes), all of which need proof of the existing factor.
    """
    if not isinstance(data, dict):
        return None
    for key in ("current_code", "totp_code", "code"):
        value = data.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return None


def _mfa_change_error(result: dict):
    """Build the JSON error for a refused MFA state change.

    Carries ``mfa_code_required`` so the client can prompt for the current
    code instead of treating the refusal as a generic failure.
    """
    body = {"error": result["error"]}
    if result.get("mfa_code_required"):
        body["mfa_code_required"] = True
    return jsonify(body), result.get("status_code", 400)


@bp.route("/mfa/backup-codes/regenerate", methods=["POST"])
@portal_token_required
async def regenerate_mfa_backup_codes():
    """Regenerate single-use MFA backup/bypass codes for the authenticated user.

    Invalidates all previously generated backup codes and returns 10 new codes.

    Request body:
        current_code: str - Current TOTP or backup code

    Returns:
        JSON with ``backup_codes`` list and ``remaining`` count
    """
    data = await request.get_json(silent=True)
    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.regenerate_backup_codes(
        user_id, _current_factor_code(data)
    )

    if "error" in result:
        return _mfa_change_error(result)

    return _no_store(jsonify(result)), 200


@bp.route("/mfa/disable", methods=["POST"])
@portal_token_required
async def disable_mfa():
    """Disable MFA for the authenticated user.

    Request body:
        current_code: str - Current TOTP or backup code

    Returns:
        Success status, or 403 if MFA is enforced by tenant policy or the
        current code is missing/invalid
    """
    data = await request.get_json(silent=True)
    user_id = int(request.portal_user["sub"])
    result = PortalAuthService.disable_mfa(user_id, _current_factor_code(data))

    if "error" in result:
        return _mfa_change_error(result)

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

    reset_token = result.get("reset_token")
    if reset_token and is_smtp_configured():
        site_url = os.environ.get("SITE_URL", "").rstrip("/")
        reset_link = (
            f"{site_url}/reset-password?token={reset_token}"
            if site_url
            else f"Reset token: {reset_token}"
        )
        try:
            await send_email(
                to_addresses=[email],
                subject="Elder Password Reset",
                body_text=(
                    "A password reset was requested for your Elder account.\n\n"
                    f"{reset_link}\n\n"
                    "If you did not request this, you can safely ignore this email."
                ),
            )
            return (
                jsonify(
                    {
                        "success": True,
                        "message": "If the email exists, a reset link will be sent",
                    }
                ),
                200,
            )
        except (EmailNotConfiguredError, EmailDeliveryError, ValueError) as exc:
            logger.error(
                "password_reset_email_failed",
                error_type=type(exc).__name__,
            )
            return ApiResponse.service_unavailable(
                "Unable to send password reset email"
            )

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
        payload = verify_token(refresh_token_value, PORTAL_REFRESH_TOKEN_TYPES)
        if not payload:
            response = await make_response(
                *ApiResponse.unauthorized("Invalid or expired refresh token")
            )
            clear_portal_auth_cookies(response)
            return response

        # Get user: self-lookup via the verified refresh token's own sub
        # claim, never a client param
        user_id = int(payload["sub"])
        user = current_app.db.portal_users[user_id]  # tenant-scope-exempt: see above

        if not user or not user.is_active:
            return ApiResponse.unauthorized("User not found or inactive")

        # A session started during the MFA grace period must not be kept
        # alive past it: once enrolment is overdue the refresh is refused
        # and the user has to sign in again (which yields only the
        # enrolment-scoped token).
        mfa_status = compute_user_mfa_status(
            user.tenant_id,
            mfa_enabled=user_has_mfa_enrolled(user),
            user_created_at=getattr(user, "created_at", None),
        )
        if mfa_status["mfa_locked_out"]:
            response = await make_response(
                *ApiResponse.unauthorized("MFA enrollment required")
            )
            clear_portal_auth_cookies(response)
            return response

        # Generate new tokens (with refresh token rotation for security)
        user_dict = {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "tenant_role": user.tenant_role,
            "global_role": user.global_role,
        }
        # Rotation keeps the ORIGINAL auth_time: a refresh is not a login.
        tokens = generate_tokens(user_dict, auth_time=carried_auth_time(payload))

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


@bp.route("/verify-email", methods=["POST"])
async def verify_email():
    """Verify a portal user's email address using a one-time verification token.

    Request body:
        token: str - Single-use email verification token from the verification email

    Returns:
        200 on success, 400 if the token is missing, invalid, expired, or already used.
    """
    data = await request.get_json(silent=True)
    if not isinstance(data, dict) or not data.get("token"):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Verification token is required",
                    "errorCode": "INVALID_VERIFICATION_TOKEN",
                }
            ),
            400,
        )

    token = str(data["token"]).strip()
    payload = consume_onboarding_token(PURPOSE_EMAIL_VERIFY, token)
    if not payload:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid or expired verification link",
                    "errorCode": "INVALID_VERIFICATION_TOKEN",
                }
            ),
            400,
        )

    user_id = payload.get("user_id")
    tenant_id = payload.get("tenant_id")
    if not user_id or not tenant_id:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid or expired verification link",
                    "errorCode": "INVALID_VERIFICATION_TOKEN",
                }
            ),
            400,
        )

    db = current_app.db
    user = (
        db(
            (db.portal_users.id == int(user_id))
            & (db.portal_users.tenant_id == int(tenant_id))
        )
        .select()
        .first()
    )
    if not user:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid or expired verification link",
                    "errorCode": "INVALID_VERIFICATION_TOKEN",
                }
            ),
            400,
        )

    db(
        (db.portal_users.id == user.id) & (db.portal_users.tenant_id == user.tenant_id)
    ).update(
        email_verified=True,
        updated_at=datetime.now(UTC),
    )
    db.commit()

    return (
        jsonify(
            {
                "success": True,
                "message": "Email verified. You can now sign in.",
            }
        ),
        200,
    )


@bp.route("/resend-verification", methods=["POST"])
async def resend_verification():
    """Resend a self-signup verification email (anti-enumeration).

    Request body:
        email: str - Email address
        tenant: str (optional) - Tenant slug (or tenant_id)

    Returns:
        Always 200 with a generic message so account existence is never disclosed.
    """
    data = await request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("email"), str):
        return ApiResponse.bad_request("email is required")

    email = data["email"].strip().lower()
    if not email:
        return ApiResponse.bad_request("email is required")

    generic_response = (
        jsonify(
            {
                "success": True,
                "message": (
                    "If an unverified account exists for that email, "
                    "a verification link has been sent."
                ),
            }
        ),
        200,
    )

    if not is_smtp_configured():
        return generic_response

    db = current_app.db
    tenant_id = data.get("tenant_id")
    tenant_slug = data.get("tenant")
    tenant = None

    if tenant_id:
        tenant = db(db.tenants.id == tenant_id).select().first()
    elif isinstance(tenant_slug, str) and tenant_slug.strip():
        tenant = db(db.tenants.slug == tenant_slug.strip()).select().first()
    else:
        tenant = db(db.tenants.slug == "system").select().first()
        if not tenant:
            tenant = db(db.tenants.slug == "default").select().first()

    if not tenant or not tenant.is_active:
        return generic_response

    user = (
        db((db.portal_users.tenant_id == tenant.id) & (db.portal_users.email == email))
        .select()
        .first()
    )
    if not user or not user.is_active or bool(getattr(user, "email_verified", False)):
        return generic_response

    token = issue_email_verify_token(user.id, tenant.id)
    try:
        await send_verification_email(
            to_email=email,
            token=token,
            tenant_name=getattr(tenant, "name", None),
        )
    except (EmailNotConfiguredError, EmailDeliveryError, ValueError) as exc:
        logger.warning(
            "resend_verification_email_failed",
            tenant_id=tenant.id,
            error_type=type(exc).__name__,
        )

    return generic_response


@bp.route("/invite/<token>", methods=["GET"])
def inspect_admin_invite(token: str):
    """Validate a one-time admin onboarding invitation token without consuming it.

    Returns:
        200 with invited user/tenant metadata and MFA requirement flags, or
        400 if the token is invalid or expired.
    """
    payload = peek_onboarding_token(PURPOSE_ADMIN_INVITE, token)
    if not payload:
        return (
            jsonify(
                {
                    "valid": False,
                    "error": "Invalid or expired invitation link",
                }
            ),
            400,
        )

    user_id = payload.get("user_id")
    tenant_id = payload.get("tenant_id")
    if not user_id or not tenant_id:
        return (
            jsonify(
                {
                    "valid": False,
                    "error": "Invalid or expired invitation link",
                }
            ),
            400,
        )

    db = current_app.db
    user = (
        db(
            (db.portal_users.id == int(user_id))
            & (db.portal_users.tenant_id == int(tenant_id))
        )
        .select()
        .first()
    )
    if not user or not user.is_active:
        return (
            jsonify(
                {
                    "valid": False,
                    "error": "Invalid or expired invitation link",
                }
            ),
            400,
        )

    tenant = db(db.tenants.id == int(tenant_id)).select().first()
    mfa_enforced = is_tenant_mfa_enforced(int(tenant_id), db=db)

    return (
        _no_store(
            jsonify(
                {
                    "valid": True,
                    "email": user.email,
                    "full_name": user.full_name,
                    "tenant_name": tenant.name if tenant else None,
                    "mfa_enforced": mfa_enforced,
                    "mfa_optional": not mfa_enforced,
                }
            )
        ),
        200,
    )


@bp.route("/invite/complete", methods=["POST"])
async def complete_admin_invite():
    """Complete an admin onboarding invitation by setting password and optional/enforced MFA.

    Request body:
        token: str - Single-use admin invitation token
        password: str - New password (minimum 8 characters)
        setup_mfa: bool (optional, default false) - Whether user opts into MFA setup now

    Returns:
        200 with MFA enrollment-scoped token/cookies when MFA is enforced or requested,
        or normal session tokens/cookies otherwise.
    """
    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        return ApiResponse.bad_request("Request body must be JSON")

    token = data.get("token")
    password = data.get("password")
    setup_mfa = bool(data.get("setup_mfa", False))

    if not isinstance(token, str) or not token.strip():
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid or expired invitation link",
                    "errorCode": "INVALID_INVITE_TOKEN",
                }
            ),
            400,
        )

    if (
        not isinstance(password, str)
        or len(password) < PortalAuthService.MIN_PASSWORD_LENGTH
    ):
        return (
            jsonify(
                {
                    "success": False,
                    "error": (
                        f"Password must be at least "
                        f"{PortalAuthService.MIN_PASSWORD_LENGTH} characters"
                    ),
                    "errorCode": "WEAK_PASSWORD",
                }
            ),
            400,
        )

    payload = consume_onboarding_token(PURPOSE_ADMIN_INVITE, token.strip())
    if not payload:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid or expired invitation link",
                    "errorCode": "INVALID_INVITE_TOKEN",
                }
            ),
            400,
        )

    user_id = payload.get("user_id")
    tenant_id = payload.get("tenant_id")
    if not user_id or not tenant_id:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid or expired invitation link",
                    "errorCode": "INVALID_INVITE_TOKEN",
                }
            ),
            400,
        )

    db = current_app.db
    user = (
        db(
            (db.portal_users.id == int(user_id))
            & (db.portal_users.tenant_id == int(tenant_id))
        )
        .select()
        .first()
    )
    if not user:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid or expired invitation link",
                    "errorCode": "INVALID_INVITE_TOKEN",
                }
            ),
            400,
        )

    now = datetime.now(UTC)
    db(
        (db.portal_users.id == user.id) & (db.portal_users.tenant_id == user.tenant_id)
    ).update(
        password_hash=generate_password_hash(password),
        email_verified=True,
        password_changed_at=now,
        is_active=True,
        updated_at=now,
    )
    db.commit()

    mfa_enforced = is_tenant_mfa_enforced(int(tenant_id), db=db)
    user_dict = {
        "id": user.id,
        "email": user.email,
        "tenant_id": user.tenant_id,
        "tenant_role": user.tenant_role,
        "global_role": user.global_role,
        "full_name": user.full_name,
    }
    roles = [user.tenant_role] if user.tenant_role else []

    if mfa_enforced or setup_mfa:
        enrollment_token = PortalAuthService.issue_mfa_enrollment_token(user_dict)
        max_age = PortalAuthService.MFA_ENROLLMENT_TOKEN_TTL_MINUTES * 60
        response = await make_response(
            jsonify(
                {
                    "success": True,
                    "mfaSetupRequired": mfa_enforced,
                    "mfaSetupOptional": not mfa_enforced,
                    "mfaEnrollmentRequired": mfa_enforced,
                    "enrollment_token": enrollment_token,
                    "token_type": "Bearer",
                    "expires_in": max_age,
                    "user": {
                        "id": str(user.id),
                        "email": user.email,
                        "name": user.full_name,
                        "roles": roles,
                    },
                }
            ),
            200,
        )
        clear_portal_auth_cookies(response)
        set_mfa_enrollment_cookies(
            response,
            enrollment_token,
            max_age=max_age,
            secure=not current_app.config.get("DEBUG", False),
        )
        return _no_store(response)

    tokens = generate_tokens(user_dict, auth_time=interactive_auth_time())
    return await _issue_token_response(user_dict, tokens, roles=roles)
