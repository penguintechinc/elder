"""SSO/SAML/SCIM API endpoints for v2.2.0 Enterprise Edition.

Provides REST endpoints for SSO configuration, SAML authentication,
and SCIM 2.0 user provisioning.
"""

# flake8: noqa: E501

from functools import wraps
from urllib.parse import urlsplit

from quart import Blueprint, Response, current_app, jsonify, request

from apps.api.api.v1.portal_auth import generate_tokens, portal_token_required
from apps.api.auth.decorators import login_required
from apps.api.auth.portal_cookies import set_portal_auth_cookies
from apps.api.common.licensing.tier_gate import require_tier
from apps.api.services.sso import OIDCService, SAMLService, SCIMService
from apps.api.services.sso.saml_service import SAMLError
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("sso", __name__)

# Browser-binding cookie for the SP-initiated SAML flow: carries the random
# token that keys the server-side AuthnRequest state. Scoped to the ACS path
# only, HttpOnly, and SameSite=None;Secure because the IdP's POST to the ACS
# is cross-site (SameSite=Lax/Strict cookies would not be sent on it).
_SAML_STATE_COOKIE = "elder_saml_state"


def _saml_cookie_flags() -> dict:
    """Return the Secure/SameSite flags for the SAML state cookie.

    Secure unless the app runs in DEBUG (plain-http local dev), where browsers
    would refuse SameSite=None without Secure and Lax is used instead.
    """
    secure = not current_app.config.get("DEBUG", False)
    return {"secure": secure, "samesite": "None" if secure else "Lax"}


def _saml_acs_path(tenant_id: int) -> str:
    """Return the URL path of the tenant's ACS (the state cookie's Path scope)."""
    _entity, acs_url = SAMLService.sp_urls(SAMLService.sp_base_url(), tenant_id)
    return urlsplit(acs_url).path


def _saml_error_response(error: SAMLError):
    """Render a SAMLError as a specific-status JSON error (never a silent fallback)."""
    return jsonify({"error": error.code, "message": error.message}), error.status_code


def _clear_saml_state_cookie(response, tenant_id: int) -> None:
    """Expire the SAML state cookie on an ACS response (the state is single use).

    Skipped silently if the SP base URL is unusable -- in that case no cookie
    could have been issued for this path either.
    """
    try:
        path = _saml_acs_path(tenant_id)
    except SAMLError:
        return
    response.delete_cookie(_SAML_STATE_COOKIE, path=path, **_saml_cookie_flags())


def _attach_auth_cookies(response, tokens: dict) -> None:
    """Attach the portal-auth HttpOnly/Secure/SameSite auth cookies to an SSO response.

    Mirrors apps.api.api.v1.portal_auth._issue_token_response: the JSON body
    still carries the tokens for non-browser callers, but the browser must
    rely on these cookies instead (gh #285 -- the SSO callbacks previously
    returned the raw access/refresh JWTs in the body with no cookie-setting,
    the same XSS-exfiltratable exposure portal-auth already closed).
    """
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


# SCIM authentication decorator
def scim_auth_required(f):
    """Decorator to require SCIM bearer token authentication."""

    @wraps(f)
    def decorated(tenant_id, *args, **kwargs):
        auth_header = request.headers.get("Authorization")

        if not auth_header or not auth_header.startswith("Bearer "):
            return (
                jsonify(
                    {
                        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                        "detail": "Authorization required",
                        "status": 401,
                    }
                ),
                401,
            )

        token = auth_header.split(" ")[1]

        if not SCIMService.validate_bearer_token(tenant_id, token):
            return (
                jsonify(
                    {
                        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                        "detail": "Invalid bearer token",
                        "status": 401,
                    }
                ),
                401,
            )

        return f(tenant_id, *args, **kwargs)

    return decorated


# =============================================================================
# IdP Configuration Endpoints
# =============================================================================


@bp.route("/idp", methods=["GET"])
@portal_token_required
def list_idp_configs():
    """List IdP configurations.

    Query params:
        tenant_id: Optional tenant filter

    Returns:
        List of IdP configurations
    """
    tenant_id = request.args.get("tenant_id", type=int)

    # Check permissions - only admins can view IdP configs
    if (
        request.portal_user.get("global_role") not in ["admin", "support"]
        and request.portal_user.get("tenant_role") != "admin"
    ):
        # Non-admins can only see their tenant's config
        tenant_id = request.portal_user.get("tenant_id")

    configs = SAMLService.list_idp_configs(tenant_id)
    return jsonify(configs), 200


@bp.route("/idp", methods=["POST"])
@login_required
@portal_token_required
async def create_idp_config():
    """Create a new IdP configuration.

    Requires global admin or tenant admin.

    Request body:
        name: str - Display name
        idp_type: str - saml or oidc
        tenant_id: int (optional) - Tenant ID or null for global

        SAML fields (required if idp_type=saml):
            entity_id: str - SAML Entity ID
            metadata_url: str - IdP metadata URL
            sso_url: str - SSO endpoint
            slo_url: str - SLO endpoint
            certificate: str - X.509 certificate

        OIDC fields (required if idp_type=oidc):
            oidc_client_id: str - OIDC Client ID
            oidc_client_secret: str - OIDC Client Secret
            oidc_issuer_url: str - OIDC Issuer URL
            oidc_scopes: str (optional) - Space-separated scopes (default: "openid profile email")
            oidc_response_type: str (optional) - Response type (default: "code")
            oidc_token_endpoint_auth_method: str (optional) - Auth method (default: "client_secret_basic")

        Common fields:
            attribute_mappings: dict (optional) - Attribute mappings
            jit_provisioning_enabled: bool - Enable JIT provisioning (default: true)
            default_role: str - Default role for new users (default: "reader")

    Returns:
        Created configuration
    """
    # Check permissions
    if (
        request.portal_user.get("global_role") != "admin"
        and request.portal_user.get("tenant_role") != "admin"
    ):
        return ApiResponse.forbidden("Admin permission required")

    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("No data provided")

    name = data.get("name")
    if not name:
        return ApiResponse.bad_request("name is required")

    # Tenant admins can only create for their tenant
    tenant_id = data.get("tenant_id")
    if request.portal_user.get("global_role") != "admin":
        tenant_id = request.portal_user.get("tenant_id")

    result = SAMLService.create_idp_config(
        name=name,
        idp_type=data.get("idp_type", "saml"),
        tenant_id=tenant_id,
        entity_id=data.get("entity_id"),
        metadata_url=data.get("metadata_url"),
        sso_url=data.get("sso_url"),
        slo_url=data.get("slo_url"),
        certificate=data.get("certificate"),
        oidc_client_id=data.get("oidc_client_id"),
        oidc_client_secret=data.get("oidc_client_secret"),
        oidc_issuer_url=data.get("oidc_issuer_url"),
        oidc_scopes=data.get("oidc_scopes"),
        oidc_response_type=data.get("oidc_response_type"),
        oidc_token_endpoint_auth_method=data.get("oidc_token_endpoint_auth_method"),
        attribute_mappings=data.get("attribute_mappings"),
        jit_provisioning_enabled=data.get("jit_provisioning_enabled", True),
        default_role=data.get("default_role", "reader"),
    )

    return jsonify(result), 201


@bp.route("/idp/<int:config_id>", methods=["PUT"])
@portal_token_required
async def update_idp_config(config_id):
    """Update an IdP configuration.

    Args:
        config_id: Configuration ID

    Returns:
        Updated configuration
    """
    # Check permissions
    if (
        request.portal_user.get("global_role") != "admin"
        and request.portal_user.get("tenant_role") != "admin"
    ):
        return ApiResponse.forbidden("Admin permission required")

    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("No data provided")

    result = SAMLService.update_idp_config(config_id, **data)

    if "error" in result:
        return jsonify(result), 404

    return jsonify(result), 200


@bp.route("/idp/<int:config_id>", methods=["DELETE"])
@portal_token_required
def delete_idp_config(config_id):
    """Delete an IdP configuration.

    Args:
        config_id: Configuration ID

    Returns:
        Success status
    """
    # Check permissions
    if (
        request.portal_user.get("global_role") != "admin"
        and request.portal_user.get("tenant_role") != "admin"
    ):
        return ApiResponse.forbidden("Admin permission required")

    result = SAMLService.delete_idp_config(config_id)

    if "error" in result:
        return jsonify(result), 404

    return jsonify(result), 200


# =============================================================================
# SAML Endpoints
# =============================================================================


@bp.route("/saml/metadata", methods=["GET"])
@bp.route("/saml/metadata/<int:tenant_id>", methods=["GET"])
def get_sp_metadata(tenant_id=None):
    """Get SAML Service Provider metadata.

    Args:
        tenant_id: Optional tenant ID

    Returns:
        SAML SP metadata XML
    """
    # The metadata (entityID/ACS URL) must match what the ACS enforces, so it
    # comes from server configuration, never the request Host header.
    try:
        base_url = SAMLService.sp_base_url()
    except SAMLError as exc:
        return _saml_error_response(exc)
    metadata = SAMLService.get_sp_metadata(base_url, tenant_id)

    return Response(
        metadata,
        mimetype="application/xml",
        headers={"Content-Disposition": "inline; filename=sp-metadata.xml"},
    )


@bp.route("/saml/login/<int:tenant_id>", methods=["GET"])
@require_tier("enterprise")
async def saml_login(tenant_id):
    """Initiate SP-initiated SAML SSO login (Enterprise).

    Builds a real AuthnRequest, records its ID server-side (single use, short
    TTL) and binds it to this browser with an ACS-scoped HttpOnly cookie, so
    the ACS can enforce InResponseTo and reject unsolicited/cross-browser
    responses.

    Args:
        tenant_id: Tenant ID

    Query params:
        return_to: Optional same-origin relative path echoed back after login

    Returns:
        JSON with the IdP redirect URL (``sso_url``) and the state cookie
    """
    try:
        started = await run_in_threadpool(
            SAMLService.begin_login, tenant_id, request.args.get("return_to")
        )
        cookie_path = _saml_acs_path(tenant_id)
    except SAMLError as exc:
        return _saml_error_response(exc)

    response = jsonify(
        {
            "sso_url": started["sso_url"],
            "message": "Redirect to SSO URL to initiate login",
        }
    )
    response.set_cookie(
        _SAML_STATE_COOKIE,
        started["state_token"],
        max_age=started["expires_in"],
        path=cookie_path,
        httponly=True,
        **_saml_cookie_flags(),
    )
    return response, 200


@bp.route("/saml/acs/<int:tenant_id>", methods=["POST"])
@require_tier("enterprise")
async def saml_acs(tenant_id):
    """SAML Assertion Consumer Service endpoint (Enterprise).

    Receives the IdP's POSTed SAMLResponse. The assertion is only trusted if it
    carries a valid IdP signature and passes the audience / destination /
    recipient / InResponseTo / validity-window / replay checks; any failure is
    a specific 4xx. The tenant comes from the ACS URL path and is bound by the
    signed Audience/Recipient, never from the request body. The posted
    ``RelayState`` is deliberately ignored -- the return target is the value
    stored server-side when the login started.

    Args:
        tenant_id: Tenant ID

    Returns:
        JWT tokens (and auth cookies) on success
    """
    # Quart's `request.form` is a coroutine -- it must be awaited (the route
    # was previously a sync `def` and called `.get` on the coroutine, raising
    # AttributeError on every real POST).
    form = await request.form
    saml_response = form.get("SAMLResponse")

    if not saml_response:
        return ApiResponse.bad_request("SAMLResponse is required")

    result = await SAMLService.process_saml_response(
        tenant_id, saml_response, request.cookies.get(_SAML_STATE_COOKIE)
    )

    if "error" in result:
        status = result.pop("status_code", 400)
        response = jsonify(result)
        _clear_saml_state_cookie(response, tenant_id)
        return response, status

    # Explicit response DTO: only these identity fields ever leave the ACS
    # (never the raw assertion, attributes, NameID or SAML session data).
    user = {
        "id": result["id"],
        "email": result["email"],
        "tenant_id": result["tenant_id"],
        "tenant_role": result.get("tenant_role"),
        "global_role": result.get("global_role"),
    }

    # Generate tokens through the same helper every portal/SSO login uses. The
    # sso_tenant_scoped marker (not part of the response body) rides in the
    # refresh token so refresh cannot re-attach a global_role.
    tokens = generate_tokens(
        {**user, "sso_tenant_scoped": bool(result.get("sso_tenant_scoped"))}
    )

    # Carry the JWTs as HttpOnly/Secure/SameSite cookies (gh #285) -- the body
    # keeps them only for non-browser callers, mirroring portal-auth.
    response = jsonify(
        {
            "user": user,
            "return_to": result.get("return_to"),
            **tokens,
        }
    )
    _attach_auth_cookies(response, tokens)
    _clear_saml_state_cookie(response, tenant_id)
    return response, 200


@bp.route("/saml/slo/<int:tenant_id>", methods=["GET", "POST"])
def saml_slo(tenant_id):
    """SAML Single Logout endpoint -- not implemented, answers 501.

    Single Logout is not supported yet (tracked as a follow-up). It used to be
    an unauthenticated stub that claimed "Logged out successfully" without
    processing anything; failing loudly is correct, and the SP metadata no
    longer advertises a SingleLogoutService. Users end their Elder session
    with ``POST /api/v1/portal-auth/logout``.

    Args:
        tenant_id: Tenant ID

    Returns:
        501 with a ``saml_slo_not_implemented`` error
    """
    return (
        jsonify(
            {
                "error": "saml_slo_not_implemented",
                "message": "SAML Single Logout is not supported; "
                "end the session with POST /api/v1/portal-auth/logout",
            }
        ),
        501,
    )


# =============================================================================
# OIDC Endpoints (v3.0.0 Enterprise Feature)
# =============================================================================


@bp.route("/oidc/authorize/<int:idp_id>", methods=["GET"])
def oidc_authorize(idp_id):
    """Initiate OIDC authorization flow.

    Args:
        idp_id: IdP configuration ID

    Query params:
        redirect_uri: Callback redirect URI
        state: Optional state parameter

    Returns:
        Redirect to IdP authorization endpoint
    """
    redirect_uri = request.args.get("redirect_uri")
    if not redirect_uri:
        return ApiResponse.bad_request("redirect_uri is required")

    state = request.args.get("state")

    result = OIDCService.get_authorization_url(idp_id, redirect_uri, state)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@bp.route("/oidc/callback", methods=["GET"])
def oidc_callback():
    """Handle OIDC callback with authorization code.

    Query params:
        code: Authorization code from IdP
        state: State parameter for CSRF protection
        idp_id: IdP configuration ID
        redirect_uri: Original redirect URI

    Returns:
        JWT tokens on success
    """
    code = request.args.get("code")
    # TODO: Validate state parameter for CSRF protection
    request.args.get("state")
    idp_id = request.args.get("idp_id", type=int)
    redirect_uri = request.args.get("redirect_uri")

    if not code:
        return ApiResponse.bad_request("code is required")
    if not idp_id:
        return ApiResponse.bad_request("idp_id is required")
    if not redirect_uri:
        return ApiResponse.bad_request("redirect_uri is required")

    # Exchange code for tokens
    tokens = OIDCService.exchange_code_for_tokens(idp_id, code, redirect_uri)

    if "error" in tokens:
        return jsonify(tokens), 400

    # Validate ID token
    id_token_claims = OIDCService.validate_id_token(idp_id, tokens["id_token"])

    if "error" in id_token_claims:
        return jsonify(id_token_claims), 400

    # Get additional userinfo (optional)
    userinfo = OIDCService.get_userinfo(idp_id, tokens["access_token"])
    if "error" in userinfo:
        userinfo = None

    # Get IdP config for JIT provisioning
    idp_config = OIDCService.get_idp_config(idp_id)
    if not idp_config:
        return ApiResponse.error("IdP configuration not found", 404)

    tenant_id = idp_config.get("tenant_id", 1)  # Default to tenant 1 if global

    # JIT provision user if enabled
    if idp_config.get("jit_provisioning_enabled"):
        user = OIDCService.jit_provision_user(
            tenant_id=tenant_id,
            idp_config=idp_config,
            id_token_claims=id_token_claims,
            userinfo=userinfo,
        )

        if "error" in user:
            return jsonify(user), 400

        # Generate Elder JWT tokens
        elder_tokens = generate_tokens(user)

        # Carry the JWTs as HttpOnly/Secure/SameSite cookies (gh #285) -- the
        # body keeps them only for non-browser callers, mirroring portal-auth.
        response = jsonify(
            {
                "user": user,
                **elder_tokens,
            }
        )
        _attach_auth_cookies(response, elder_tokens)
        return response, 200

    return ApiResponse.forbidden("JIT provisioning is disabled for this IdP")


@bp.route("/oidc/logout/<int:idp_id>", methods=["POST"])
@portal_token_required
async def oidc_logout(idp_id):
    """Initiate OIDC logout (RP-Initiated Logout).

    Args:
        idp_id: IdP configuration ID

    Request body:
        id_token_hint: Optional ID token hint
        post_logout_redirect_uri: Optional redirect URI after logout

    Returns:
        End session endpoint URL
    """
    data = await request.get_json() or {}
    id_token_hint = data.get("id_token_hint")
    post_logout_redirect_uri = data.get("post_logout_redirect_uri")

    # OIDCService.logout() makes a blocking `requests` call (OIDC discovery
    # document fetch) -- run off the event loop so one slow/unresponsive IdP
    # can't stall every other concurrent request (finding #7).
    result = await run_in_threadpool(
        OIDCService.logout, idp_id, id_token_hint, post_logout_redirect_uri
    )

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@bp.route("/oidc/userinfo/<int:idp_id>", methods=["GET"])
@portal_token_required
def oidc_userinfo(idp_id):
    """Get current user info from OIDC userinfo endpoint.

    Args:
        idp_id: IdP configuration ID

    Headers:
        X-OIDC-Access-Token: Access token from IdP

    Returns:
        User info claims
    """
    access_token = request.headers.get("X-OIDC-Access-Token")

    if not access_token:
        return ApiResponse.bad_request("X-OIDC-Access-Token header is required")

    result = OIDCService.get_userinfo(idp_id, access_token)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@bp.route("/oidc/refresh/<int:idp_id>", methods=["POST"])
@portal_token_required
async def oidc_refresh(idp_id):
    """Refresh OIDC access token.

    Args:
        idp_id: IdP configuration ID

    Request body:
        refresh_token: Refresh token from IdP

    Returns:
        New tokens
    """
    data = await request.get_json()
    if not data:
        return ApiResponse.bad_request("No data provided")

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        return ApiResponse.bad_request("refresh_token is required")

    # OIDCService.refresh_tokens() makes blocking `requests`/authlib
    # OAuth2Session HTTP calls (discovery + token endpoint) -- run off the
    # event loop so a slow IdP can't stall every other concurrent request
    # (finding #7).
    result = await run_in_threadpool(OIDCService.refresh_tokens, idp_id, refresh_token)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


# =============================================================================
# SCIM Endpoints
# =============================================================================


@bp.route("/scim/config", methods=["POST"])
@login_required
@portal_token_required
async def create_scim_config():
    """Create SCIM configuration for a tenant.

    Request body:
        tenant_id: int - Tenant ID (admin only)

    Returns:
        SCIM configuration with bearer token
    """
    # Check permissions
    if (
        request.portal_user.get("global_role") != "admin"
        and request.portal_user.get("tenant_role") != "admin"
    ):
        return ApiResponse.forbidden("Admin permission required")

    data = await request.get_json() or {}

    # Tenant admins can only configure their tenant
    tenant_id = data.get("tenant_id")
    if request.portal_user.get("global_role") != "admin":
        tenant_id = request.portal_user.get("tenant_id")

    if not tenant_id:
        return ApiResponse.bad_request("tenant_id is required")

    result = SCIMService.create_scim_config(tenant_id)

    return jsonify(result), 201


@bp.route("/scim/config/<int:tenant_id>", methods=["GET"])
@portal_token_required
def get_scim_config(tenant_id):
    """Get SCIM configuration for a tenant.

    Args:
        tenant_id: Tenant ID

    Returns:
        SCIM configuration (without bearer token)
    """
    config = SCIMService.get_scim_config(tenant_id)

    if not config:
        return ApiResponse.error("SCIM not configured", 404)

    return jsonify(config), 200


@bp.route("/scim/config/<int:tenant_id>/regenerate-token", methods=["POST"])
@portal_token_required
def regenerate_scim_token(tenant_id):
    """Regenerate SCIM bearer token.

    Args:
        tenant_id: Tenant ID

    Returns:
        New bearer token
    """
    # Check permissions
    if (
        request.portal_user.get("global_role") != "admin"
        and request.portal_user.get("tenant_role") != "admin"
    ):
        return ApiResponse.forbidden("Admin permission required")

    result = SCIMService.regenerate_token(tenant_id)

    if "error" in result:
        return jsonify(result), 404

    return jsonify(result), 200


# =============================================================================
# SCIM 2.0 User Provisioning Endpoints
# =============================================================================


@bp.route("/scim/<int:tenant_id>/Users", methods=["GET"])
@scim_auth_required
def scim_list_users(tenant_id):
    """SCIM 2.0 - List users.

    Query params:
        startIndex: int - Starting index (1-based)
        count: int - Number of results
        filter: str - SCIM filter

    Returns:
        SCIM ListResponse
    """
    start_index = request.args.get("startIndex", 1, type=int)
    count = request.args.get("count", 100, type=int)
    filter_str = request.args.get("filter")

    result = SCIMService.list_users(tenant_id, start_index, count, filter_str)

    return jsonify(result), 200


@bp.route("/scim/<int:tenant_id>/Users", methods=["POST"])
@scim_auth_required
async def scim_create_user(tenant_id):
    """SCIM 2.0 - Create user.

    Request body:
        SCIM User resource

    Returns:
        Created SCIM User
    """
    scim_user = await request.get_json()
    if not scim_user:
        return (
            jsonify(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "detail": "Request body required",
                    "status": 400,
                }
            ),
            400,
        )

    result = SCIMService.create_user(tenant_id, scim_user)

    if "error" in result:
        status = result.pop("status", 400)
        return (
            jsonify(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "detail": result["error"],
                    "status": status,
                }
            ),
            status,
        )

    return jsonify(result), 201


@bp.route("/scim/<int:tenant_id>/Users/<int:user_id>", methods=["GET"])
@scim_auth_required
def scim_get_user(tenant_id, user_id):
    """SCIM 2.0 - Get user.

    Args:
        tenant_id: Tenant ID
        user_id: User ID

    Returns:
        SCIM User resource
    """
    result = SCIMService.get_user(tenant_id, user_id)

    if "error" in result:
        status = result.pop("status", 404)
        return (
            jsonify(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "detail": result["error"],
                    "status": status,
                }
            ),
            status,
        )

    return jsonify(result), 200


@bp.route("/scim/<int:tenant_id>/Users/<int:user_id>", methods=["PUT"])
@scim_auth_required
async def scim_update_user(tenant_id, user_id):
    """SCIM 2.0 - Replace user.

    Args:
        tenant_id: Tenant ID
        user_id: User ID

    Request body:
        SCIM User resource

    Returns:
        Updated SCIM User
    """
    scim_user = await request.get_json()
    if not scim_user:
        return (
            jsonify(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "detail": "Request body required",
                    "status": 400,
                }
            ),
            400,
        )

    result = SCIMService.update_user(tenant_id, user_id, scim_user)

    if "error" in result:
        status = result.pop("status", 400)
        return (
            jsonify(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "detail": result["error"],
                    "status": status,
                }
            ),
            status,
        )

    return jsonify(result), 200


@bp.route("/scim/<int:tenant_id>/Users/<int:user_id>", methods=["DELETE"])
@scim_auth_required
def scim_delete_user(tenant_id, user_id):
    """SCIM 2.0 - Delete user.

    Args:
        tenant_id: Tenant ID
        user_id: User ID

    Returns:
        204 No Content
    """
    result = SCIMService.delete_user(tenant_id, user_id)

    if "error" in result:
        status = result.pop("status", 404)
        return (
            jsonify(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "detail": result["error"],
                    "status": status,
                }
            ),
            status,
        )

    return "", 204
