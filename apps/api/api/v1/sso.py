"""SSO/SAML/SCIM API endpoints for v2.2.0 Enterprise Edition.

Provides REST endpoints for SSO configuration, SAML authentication,
and SCIM 2.0 user provisioning.
"""

# flake8: noqa: E501

from functools import wraps

from pydantic import ValidationError
from quart import Blueprint, Response, current_app, jsonify, request
from quart_schema import document_request, document_response, validate_response

from apps.api.api.v1.portal_auth import generate_tokens, portal_token_required
from apps.api.auth.decorators import login_required
from apps.api.auth.portal_cookies import set_portal_auth_cookies
from apps.api.models.pydantic.idp import (
    CreateIdPConfigRequest,
    IdPConfigDeleteResponse,
    IdPConfigResponse,
    IdPConfigUpdateResponse,
    IdPErrorResponse,
    UpdateIdPConfigRequest,
)
from apps.api.services.sso import OIDCService, SAMLService, SCIMService
from apps.api.services.sso.idp_access import (
    IdPAccessError,
    IdPCaller,
    IdPForbiddenError,
    IdPNotFoundError,
)
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("sso", __name__)


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
#
# Tenant ownership (regression: idp-config-cross-tenant): every route below
# resolves an ``IdPCaller`` from the validated portal JWT and hands it to the
# service layer, which scopes the DAL query itself. A config outside the
# caller's tenant answers 404 (never 403) so ids cannot be enumerated; only
# ``global_role == "admin"`` may touch other tenants' or global configs.
# Request bodies are validated against strict pydantic schemas and responses go
# through ``IdPConfigResponse`` (an allowlist with no secret field) via
# quart-schema ``@validate_response`` -- secrets are never returned, and no
# request value is ever interpolated into an error message or log line.


def _claim_tenant_id(user: dict) -> int | None:
    """Return the caller's tenant id from the validated JWT claims, else None.

    Only a positive integer claim counts; anything else (missing, bool, str,
    float) is "no tenant" and non-global callers then fail closed.
    """
    raw = user.get("tenant_id")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return None


def _resolve_idp_caller() -> tuple[IdPCaller | None, tuple | None]:
    """Authorize the portal caller for IdP-config admin ops.

    Returns ``(caller, None)`` on success or ``(None, error_response)``:
    403 unless the caller is a global admin or a tenant admin, and 403 for a
    tenant admin whose token carries no tenant claim.
    """
    # ``portal_user`` is attached by @portal_token_required; a missing attribute
    # (decorator forgotten) fails closed to "no claims" -> 403, never an admin.
    user = getattr(request, "portal_user", None) or {}
    is_global_admin = user.get("global_role") == "admin"
    if not (is_global_admin or user.get("tenant_role") == "admin"):
        return None, ApiResponse.forbidden("Admin permission required")

    tenant_id = _claim_tenant_id(user)
    if tenant_id is None and not is_global_admin:
        return None, ApiResponse.forbidden("Tenant claim required")

    return IdPCaller(tenant_id=tenant_id, is_global_admin=is_global_admin), None


def _idp_error_response(exc: IdPAccessError, action: str, caller: IdPCaller) -> tuple:
    """Map an IdP access failure to a 404/403 response and log the denial.

    Not-found (missing or other-tenant) renders one uniform 404 body; the log
    line carries ids only -- no secrets, certificates, emails or request values.
    """
    config_id = getattr(exc, "config_id", None)
    current_app.logger.warning(
        "idp_config_access_denied action=%s config_id=%s caller_tenant_id=%s "
        "global_admin=%s",
        action,
        config_id,
        caller.tenant_id,
        caller.is_global_admin,
        extra={
            "idp_action": action,
            "idp_config_id": config_id,
            "idp_caller_tenant_id": caller.tenant_id,
            "idp_global_admin": caller.is_global_admin,
        },
    )
    if isinstance(exc, IdPNotFoundError):
        return ApiResponse.not_found("IdP configuration", config_id)
    return ApiResponse.forbidden(str(exc))


def _idp_failure_response(exc: Exception, action: str, caller: IdPCaller) -> tuple:
    """Turn an unexpected storage/runtime failure into a sanitized 500.

    Only the exception *type* (and the PostgreSQL SQLSTATE when the driver
    provides one) is logged -- never ``str(exc)`` or a traceback, because
    driver errors embed the failing row, which can contain a submitted
    ``oidc_client_secret``. The response body is a fixed string.
    """
    current_app.logger.error(
        "idp_config_operation_failed action=%s caller_tenant_id=%s exc_type=%s "
        "sqlstate=%s",
        action,
        caller.tenant_id,
        type(exc).__name__,
        getattr(exc, "pgcode", None),
    )
    return ApiResponse.error("IdP configuration operation failed", 500)


def _validation_error_response(exc: ValidationError) -> tuple:
    """Render a pydantic ValidationError as a 400 that never echoes input values.

    Only the declared top-level field name, pydantic's static error type code
    and its fixed message are returned -- never ``input``/``ctx`` values and
    never nested (user-supplied) mapping keys.
    """
    errors = [
        {
            "field": str(err["loc"][0]) if err["loc"] else "body",
            "type": err["type"],
            "message": err["msg"],
        }
        for err in exc.errors()
    ]
    return jsonify({"error": "Validation failed", "validation_errors": errors}), 400


@bp.route("/idp", methods=["GET"])
@portal_token_required
@validate_response(list[IdPConfigResponse])
@document_response(IdPErrorResponse, 400)
@document_response(IdPErrorResponse, 403)
async def list_idp_configs():
    """List IdP configurations the caller is allowed to see.

    Requires global admin or tenant admin. Tenant admins see only their own
    tenant's configs; global admins see all (optionally filtered).

    Query params:
        tenant_id: Optional tenant filter -- tenant admins may only pass
            their own tenant id (anything else is 403)

    Returns:
        200: List of IdP configurations (secrets redacted)
        400: tenant_id is not an integer
        403: Not an admin, or tenant_id of another tenant requested
    """
    caller, error = _resolve_idp_caller()
    if error is not None:
        return error

    tenant_id = None
    if "tenant_id" in request.args:
        tenant_id = request.args.get("tenant_id", type=int)
        if tenant_id is None:
            return ApiResponse.bad_request("tenant_id must be an integer")

    try:
        configs = await run_in_threadpool(
            SAMLService.list_idp_configs, caller, tenant_id
        )
    except IdPAccessError as exc:
        return _idp_error_response(exc, "list", caller)
    except Exception as exc:
        return _idp_failure_response(exc, "list", caller)

    return configs, 200


@bp.route("/idp", methods=["POST"])
@login_required
@portal_token_required
@document_request(CreateIdPConfigRequest)
@validate_response(IdPConfigResponse, 201)
@document_response(IdPErrorResponse, 400)
@document_response(IdPErrorResponse, 403)
async def create_idp_config():
    """Create a new IdP configuration.

    Requires global admin or tenant admin. Tenant admins always create in
    their own tenant; supplying a different ``tenant_id`` is 403. Only global
    admins may pick the owning tenant (omit / null = global config).

    Request body:
        name: str - Display name
        idp_type: str - saml or oidc
        tenant_id: int (optional, global admins only) - Tenant ID or null for global

        SAML fields (required if idp_type=saml):
            entity_id: str - SAML Entity ID
            metadata_url: str - IdP metadata URL
            sso_url: str - SSO endpoint
            slo_url: str - SLO endpoint
            certificate: str - X.509 certificate

        OIDC fields (required if idp_type=oidc):
            oidc_client_id: str - OIDC Client ID
            oidc_client_secret: str - OIDC Client Secret (write-only, never returned)
            oidc_issuer_url: str - OIDC Issuer URL
            oidc_scopes: str (optional) - Space-separated scopes (default: "openid profile email")
            oidc_response_type: str (optional) - Response type (default: "code")
            oidc_token_endpoint_auth_method: str (optional) - Auth method (default: "client_secret_basic")

        Common fields:
            attribute_mappings: dict (optional) - Attribute mappings
            jit_provisioning_enabled: bool - Enable JIT provisioning (default: true)
            default_role: str - Default role for new users (default: "reader")

    Returns:
        201: Created configuration (secrets redacted)
        400: Request body failed validation (wrong type, missing name, ...)
        403: Not an admin, or a tenant_id other than the caller's own
    """
    caller, error = _resolve_idp_caller()
    if error is not None:
        return error

    try:
        body = CreateIdPConfigRequest.model_validate(await request.get_json())
    except ValidationError as exc:
        return _validation_error_response(exc)

    # Only a global admin may choose the owning tenant. A tenant admin who
    # sends any tenant_id other than their own (including an explicit null,
    # i.e. "global") is rejected loudly rather than silently re-bound.
    if (
        not caller.is_global_admin
        and "tenant_id" in body.model_fields_set
        and body.tenant_id != caller.tenant_id
    ):
        return _idp_error_response(
            IdPForbiddenError("Cannot create an IdP configuration for another tenant"),
            "create",
            caller,
        )

    secret = body.oidc_client_secret
    fields = body.model_dump(exclude={"oidc_client_secret", "tenant_id"})
    try:
        result = await run_in_threadpool(
            lambda: SAMLService.create_idp_config(
                caller,
                tenant_id=body.tenant_id,
                oidc_client_secret=secret.get_secret_value() if secret else None,
                **fields,
            )
        )
    except IdPAccessError as exc:
        return _idp_error_response(exc, "create", caller)
    except Exception as exc:
        return _idp_failure_response(exc, "create", caller)

    return result, 201


@bp.route("/idp/<int:config_id>", methods=["PUT"])
@portal_token_required
@document_request(UpdateIdPConfigRequest)
@validate_response(IdPConfigUpdateResponse)
@document_response(IdPErrorResponse, 400)
@document_response(IdPErrorResponse, 403)
@document_response(IdPErrorResponse, 404)
async def update_idp_config(config_id):
    """Update an IdP configuration the caller owns.

    Other tenants' (and, for tenant admins, global) configs answer 404.
    ``tenant_id`` in the body must match the config's current tenant (403
    otherwise) -- a config can never be re-homed. Body keys other than the
    documented updatable fields (e.g. ``idp_type``, ``id``) are ignored.

    Args:
        config_id: Configuration ID

    Returns:
        200: Updated configuration (secrets redacted) plus ``updated: true``
        400: Request body failed validation (wrong type, null name, ...)
        403: Not an admin, or a tenant_id change was requested
        404: No such configuration in the caller's scope
    """
    caller, error = _resolve_idp_caller()
    if error is not None:
        return error

    try:
        body = UpdateIdPConfigRequest.model_validate(await request.get_json())
    except ValidationError as exc:
        return _validation_error_response(exc)

    updates = body.to_changes()
    if "tenant_id" in body.model_fields_set:
        updates["tenant_id"] = body.tenant_id

    try:
        result = await run_in_threadpool(
            SAMLService.update_idp_config, config_id, caller, updates
        )
    except IdPAccessError as exc:
        return _idp_error_response(exc, "update", caller)
    except Exception as exc:
        return _idp_failure_response(exc, "update", caller)

    return result, 200


@bp.route("/idp/<int:config_id>", methods=["DELETE"])
@portal_token_required
@validate_response(IdPConfigDeleteResponse)
@document_response(IdPErrorResponse, 403)
@document_response(IdPErrorResponse, 404)
async def delete_idp_config(config_id):
    """Delete an IdP configuration the caller owns.

    Other tenants' (and, for tenant admins, global) configs answer 404.

    Args:
        config_id: Configuration ID

    Returns:
        200: ``{"deleted": true}``
        403: Not an admin
        404: No such configuration in the caller's scope
    """
    caller, error = _resolve_idp_caller()
    if error is not None:
        return error

    try:
        result = await run_in_threadpool(
            SAMLService.delete_idp_config, config_id, caller
        )
    except IdPAccessError as exc:
        return _idp_error_response(exc, "delete", caller)
    except Exception as exc:
        return _idp_failure_response(exc, "delete", caller)

    return result, 200


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
    base_url = request.host_url.rstrip("/")
    metadata = SAMLService.get_sp_metadata(base_url, tenant_id)

    return Response(
        metadata,
        mimetype="application/xml",
        headers={"Content-Disposition": "inline; filename=sp-metadata.xml"},
    )


@bp.route("/saml/login/<int:tenant_id>", methods=["GET"])
def saml_login(tenant_id):
    """Initiate SAML SSO login.

    Args:
        tenant_id: Tenant ID

    Returns:
        Redirect to IdP or error
    """
    idp_config = SAMLService.get_idp_config(tenant_id)

    if not idp_config:
        return ApiResponse.error("No IdP configured for this tenant", 404)

    if not idp_config.get("sso_url"):
        return ApiResponse.bad_request("IdP SSO URL not configured")

    # In production, generate SAML AuthnRequest and redirect
    # For now, return the SSO URL for manual redirect
    return (
        jsonify(
            {
                "sso_url": idp_config["sso_url"],
                "message": "Redirect to SSO URL to initiate login",
            }
        ),
        200,
    )


@bp.route("/saml/acs/<int:tenant_id>", methods=["POST"])
async def saml_acs(tenant_id):
    """SAML Assertion Consumer Service endpoint.

    Receives and processes SAML responses from IdP.

    Args:
        tenant_id: Tenant ID

    Returns:
        JWT tokens on success
    """
    # Quart's `request.form` is a coroutine -- it must be awaited (the route
    # was previously a sync `def` and called `.get` on the coroutine, raising
    # AttributeError on every real POST).
    form = await request.form
    saml_response = form.get("SAMLResponse")
    relay_state = form.get("RelayState")

    if not saml_response:
        return ApiResponse.bad_request("SAMLResponse is required")

    result = SAMLService.process_saml_response(tenant_id, saml_response, relay_state)

    if "error" in result:
        return jsonify(result), 400

    # Generate tokens
    tokens = generate_tokens(result)

    # Carry the JWTs as HttpOnly/Secure/SameSite cookies (gh #285) -- the body
    # keeps them only for non-browser callers, mirroring portal-auth.
    response = jsonify(
        {
            "user": result,
            **tokens,
        }
    )
    _attach_auth_cookies(response, tokens)
    return response, 200


@bp.route("/saml/slo/<int:tenant_id>", methods=["GET", "POST"])
def saml_slo(tenant_id):
    """SAML Single Logout endpoint.

    Args:
        tenant_id: Tenant ID

    Returns:
        Logout confirmation
    """
    # In production, process SLO request/response
    return jsonify({"message": "Logged out successfully", "tenant_id": tenant_id}), 200


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
