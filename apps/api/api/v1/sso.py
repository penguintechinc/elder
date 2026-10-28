"""SSO/SAML/SCIM API endpoints for v2.2.0 Enterprise Edition.

Provides REST endpoints for SSO configuration, SAML authentication,
and SCIM 2.0 user provisioning.
"""

# flake8: noqa: E501

from functools import wraps
from inspect import iscoroutinefunction
from urllib.parse import urlsplit

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
from apps.api.services.sso import OIDCService, SAMLService, SCIMService, oidc_state
from apps.api.services.sso.idp_access import (
    IdPAccessError,
    IdPCaller,
    IdPForbiddenError,
    IdPNotFoundError,
)
from apps.api.services.sso.oidc_state import OIDCLoginError
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


def _scim_error(detail: str, status: int) -> tuple:
    """Render a SCIM 2.0 error message (RFC 7644 section 3.12) with ``status``."""
    return (
        jsonify(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "detail": detail,
                "status": status,
            }
        ),
        status,
    )


def _scim_authenticate(tenant_id: int) -> tuple | None:
    """Check the SCIM bearer token against the tenant in the URL path.

    The token is validated for exactly ``tenant_id`` (token and tenant must
    match on one active config), so a token for tenant X never authorizes a
    request addressed to tenant Y. Returns an error response tuple, or None
    when the request is authenticated.
    """
    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        return _scim_error("Authorization required", 401)

    token = auth_header[len("Bearer ") :].strip()
    if not token:
        return _scim_error("Authorization required", 401)

    if not SCIMService.validate_bearer_token(tenant_id, token):
        return _scim_error("Invalid bearer token", 401)

    return None


# SCIM authentication decorator
def scim_auth_required(f):
    """Decorator to require SCIM bearer token authentication.

    Sync/async-aware (regression: scim-async-decorator): the wrapper has the
    same flavor as the view it wraps. A plain ``def`` wrapper around an
    ``async def`` view returned the un-awaited coroutine as the "response",
    which Quart rejects -- every SCIM create/update 500'd. The async flavor
    runs the blocking token lookup off the event loop.
    """

    if iscoroutinefunction(f):

        @wraps(f)
        async def decorated(tenant_id, *args, **kwargs):
            denied = await run_in_threadpool(_scim_authenticate, tenant_id)
            if denied is not None:
                return denied
            return await f(tenant_id, *args, **kwargs)

    else:

        @wraps(f)
        def decorated(tenant_id, *args, **kwargs):
            denied = _scim_authenticate(tenant_id)
            if denied is not None:
                return denied
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


def _resolve_idp_caller(
    require_admin: bool = True,
) -> tuple[IdPCaller | None, tuple | None]:
    """Authorize the portal caller for IdP-config / SCIM-config tenant ops.

    Returns ``(caller, None)`` on success or ``(None, error_response)``:
    403 unless the caller is a global admin or a tenant admin (skipped with
    ``require_admin=False``, for end-user OIDC session routes), and 403 for a
    caller whose token carries no tenant claim (unless a global admin).
    """
    # ``portal_user`` is attached by @portal_token_required; a missing attribute
    # (decorator forgotten) fails closed to "no claims" -> 403, never an admin.
    user = getattr(request, "portal_user", None) or {}
    is_global_admin = user.get("global_role") == "admin"
    if require_admin and not (is_global_admin or user.get("tenant_role") == "admin"):
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


def _oidc_json(payload: dict, status: int):
    """Build a no-store JSON response for the OIDC login endpoints.

    Login responses carry or relate to one-time secrets (state, cookies,
    tokens), so intermediaries and the browser must never cache them.
    """
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


def _oidc_login_error(exc: OIDCLoginError, *, clear_binding: bool = False):
    """Render an ``OIDCLoginError`` as ``{"error": <code>, "message": <safe text>}``.

    Only the stable code and a fixed sentence leave the server (never request
    values, upstream text or secrets). ``clear_binding`` also expires the
    login binding cookie once its state has been consumed.
    """
    current_app.logger.warning(
        "oidc_login_denied code=%s status=%s", exc.code, exc.status
    )
    response, status = _oidc_json(
        {"error": exc.code, "message": exc.message}, exc.status
    )
    if clear_binding:
        response.delete_cookie(
            oidc_state.BINDING_COOKIE_NAME, path=oidc_state.BINDING_COOKIE_PATH
        )
    return response, status


def _oidc_upstream_error(result: dict, *, clear_binding: bool = True):
    """Render a service-layer ``{"error": <code>}`` result (stable codes only).

    ``oidc_idp_not_found`` is a 404; every other service failure is a 400.
    """
    code = str(result.get("error", "oidc_login_failed"))
    status = 404 if code == "oidc_idp_not_found" else 400
    return _oidc_login_error(
        OIDCLoginError(code, status, "The identity provider request failed"),
        clear_binding=clear_binding,
    )


def _positive_int(raw: str | None) -> int | None:
    """Parse a plain positive decimal integer (no sign, no float) or return None."""
    if raw is None or not raw.isascii() or not raw.isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None


def _is_safe_redirect_uri(uri: str) -> bool:
    """Accept only absolute ``http(s)`` redirect URIs with a host, no control chars."""
    if len(uri) > 2048 or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in uri):
        return False
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


async def _require_scoped_oidc_idp(idp_id: int, action: str) -> tuple | None:
    """Gate an authenticated OIDC route on the caller's right to use ``idp_id``.

    Regression: oidc-refresh-userinfo-logout-ownership -- these routes used to
    take any ``idp_id`` and call the IdP with that config's client secret.
    Another tenant's config (and, for non-global admins, a global one) answers
    the same 404 as a missing config, so nothing is learned and nothing is
    called upstream. Returns an error response, or None if allowed.
    """
    caller, error = _resolve_idp_caller(require_admin=False)
    if caller is None or error is not None:
        return error or ApiResponse.forbidden("Access denied")
    try:
        await run_in_threadpool(OIDCService.get_idp_config_scoped, idp_id, caller)
    except IdPAccessError as exc:
        return _idp_error_response(exc, action, caller)
    return None


@bp.route("/oidc/authorize/<int:idp_id>", methods=["GET"])
def oidc_authorize(idp_id):
    """Initiate OIDC authorization flow.

    Generates the ``state``, ``nonce`` and PKCE challenge server-side, records
    them (single use, 10 minute TTL) in Redis and binds them to this browser
    with an HttpOnly ``elder_oidc_binding`` cookie that the callback requires.
    A client-supplied ``state`` is ignored. A GLOBAL IdP needs ``tenant_id`` and
    only serves a tenant that opted in (``tenants.settings.oidc_allow_global_idp
    is True``); a tenant-scoped IdP only serves its own tenant.

    Args:
        idp_id: IdP configuration ID

    Query params:
        redirect_uri: Callback redirect URI (absolute http/https URL)
        tenant_id: Target tenant (required for a global IdP)

    Returns:
        JSON with the IdP authorization URL and state (+ binding cookie)
    """
    redirect_uri = request.args.get("redirect_uri")
    if not redirect_uri:
        return ApiResponse.bad_request("redirect_uri is required")
    if not _is_safe_redirect_uri(redirect_uri):
        return ApiResponse.bad_request("redirect_uri must be an absolute http(s) URL")

    requested_tenant_id = None
    if "tenant_id" in request.args:
        requested_tenant_id = _positive_int(request.args.get("tenant_id"))
        if requested_tenant_id is None:
            return ApiResponse.bad_request("tenant_id must be a positive integer")

    idp_config = OIDCService.get_idp_config(idp_id)
    if not idp_config:
        return _oidc_upstream_error(
            {"error": "oidc_idp_not_found"}, clear_binding=False
        )

    try:
        tenant_id = OIDCService.resolve_login_tenant(idp_config, requested_tenant_id)
        login = oidc_state.begin_login(idp_id, tenant_id, redirect_uri)
    except OIDCLoginError as exc:
        return _oidc_login_error(exc)

    result = OIDCService.get_authorization_url(
        idp_config,
        redirect_uri,
        state=login.state,
        nonce=login.nonce,
        code_challenge=login.code_challenge,
    )
    if "error" in result:
        return _oidc_upstream_error(result, clear_binding=False)

    response, status = _oidc_json(result, 200)
    response.set_cookie(
        oidc_state.BINDING_COOKIE_NAME,
        login.binding,
        max_age=oidc_state.STATE_TTL_SECONDS,
        httponly=True,
        secure=not current_app.config.get("DEBUG", False),
        # Lax (not Strict): the IdP redirects back with a cross-site top-level GET.
        samesite="Lax",
        path=oidc_state.BINDING_COOKIE_PATH,
    )
    return response, status


@bp.route("/oidc/callback", methods=["GET"])
def oidc_callback():
    """Handle OIDC callback with authorization code.

    Fail-closed order: ``code`` present -> ``state`` + binding cookie consumed
    (single use) -> ``idp_id``/``redirect_uri`` (optional) must equal what the
    login was bound to -> tenant resolved from the bound state (never from this
    request) -> code exchanged with the PKCE verifier -> ID token validated
    including its ``nonce`` -> user resolved. The session never carries a
    stored ``global_role``.

    Query params:
        code: Authorization code from IdP
        state: Server-issued state (required)
        idp_id: Optional; must match the login's IdP
        redirect_uri: Optional; must match the login's redirect URI

    Returns:
        JWT tokens on success
    """
    code = request.args.get("code")

    if not code:
        if request.args.get("error"):
            return _oidc_login_error(
                OIDCLoginError(
                    "oidc_idp_error", 400, "The identity provider reported an error"
                )
            )
        return ApiResponse.bad_request("code is required")

    try:
        login = oidc_state.consume_login(
            request.args.get("state"),
            request.cookies.get(oidc_state.BINDING_COOKIE_NAME),
        )
    except OIDCLoginError as exc:
        return _oidc_login_error(exc)

    # From here the state is burned: every outcome also clears the binding cookie.
    query_idp_id = request.args.get("idp_id")
    if query_idp_id is not None and query_idp_id != str(login.idp_id):
        return _oidc_login_error(
            OIDCLoginError(
                "oidc_idp_mismatch", 400, "Login was started for a different provider"
            ),
            clear_binding=True,
        )
    query_redirect_uri = request.args.get("redirect_uri")
    if query_redirect_uri is not None and query_redirect_uri != login.redirect_uri:
        return _oidc_login_error(
            OIDCLoginError(
                "oidc_redirect_uri_mismatch",
                400,
                "redirect_uri does not match the one used to start the login",
            ),
            clear_binding=True,
        )
    idp_id = login.idp_id

    idp_config = OIDCService.get_idp_config(idp_id)
    if not idp_config:
        return _oidc_upstream_error({"error": "oidc_idp_not_found"})

    try:
        tenant_id = OIDCService.resolve_login_tenant(idp_config, login.tenant_id)
    except OIDCLoginError as exc:
        return _oidc_login_error(exc, clear_binding=True)

    # Exchange code for tokens (PKCE verifier proves we started this login)
    tokens = OIDCService.exchange_code_for_tokens(
        idp_id, code, login.redirect_uri, code_verifier=login.code_verifier
    )
    if "error" in tokens:
        return _oidc_upstream_error(tokens)
    if not tokens.get("id_token"):
        return _oidc_upstream_error({"error": "oidc_id_token_missing"})

    # Validate ID token (signature, iss/aud/exp, and the login's nonce)
    id_token_claims = OIDCService.validate_id_token(
        idp_id, tokens["id_token"], expected_nonce=login.nonce
    )
    if "error" in id_token_claims:
        return _oidc_upstream_error(id_token_claims)

    # Get additional userinfo (optional); per OIDC Core 5.3.2 a userinfo
    # response whose ``sub`` differs from the ID token's must not be used.
    userinfo = None
    if tokens.get("access_token"):
        fetched = OIDCService.get_userinfo(idp_id, tokens["access_token"])
        if "error" not in fetched:
            if fetched.get("sub") == id_token_claims.get("sub"):
                userinfo = fetched
            else:
                current_app.logger.warning(
                    "oidc_userinfo_sub_mismatch idp_id=%s", idp_id
                )

    try:
        user = OIDCService.jit_provision_user(
            tenant_id=tenant_id,
            idp_config=idp_config,
            id_token_claims=id_token_claims,
            userinfo=userinfo,
        )
    except OIDCLoginError as exc:
        return _oidc_login_error(exc, clear_binding=True)

    # Generate Elder JWT tokens
    elder_tokens = generate_tokens(user)

    # Carry the JWTs as HttpOnly/Secure/SameSite cookies (gh #285) -- the
    # body keeps them only for non-browser callers, mirroring portal-auth.
    response, status = _oidc_json({"user": user, **elder_tokens}, 200)
    _attach_auth_cookies(response, elder_tokens)
    response.delete_cookie(
        oidc_state.BINDING_COOKIE_NAME, path=oidc_state.BINDING_COOKIE_PATH
    )
    return response, status


@bp.route("/oidc/logout/<int:idp_id>", methods=["POST"])
@portal_token_required
async def oidc_logout(idp_id):
    """Initiate OIDC logout (RP-Initiated Logout).

    The IdP config must be one the caller may use (own tenant, or any for a
    global admin); anything else is a 404 and the IdP is never contacted.

    Args:
        idp_id: IdP configuration ID

    Request body:
        id_token_hint: Optional ID token hint
        post_logout_redirect_uri: Optional redirect URI after logout

    Returns:
        End session endpoint URL
    """
    denied = await _require_scoped_oidc_idp(idp_id, "oidc_logout")
    if denied is not None:
        return denied

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
async def oidc_userinfo(idp_id):
    """Get current user info from OIDC userinfo endpoint.

    The IdP config must be one the caller may use; anything else is a 404 and
    the IdP is never contacted.

    Args:
        idp_id: IdP configuration ID

    Headers:
        X-OIDC-Access-Token: Access token from IdP

    Returns:
        User info claims
    """
    denied = await _require_scoped_oidc_idp(idp_id, "oidc_userinfo")
    if denied is not None:
        return denied

    access_token = request.headers.get("X-OIDC-Access-Token")

    if not access_token:
        return ApiResponse.bad_request("X-OIDC-Access-Token header is required")

    # Blocking `requests` calls (discovery + userinfo) -- off the event loop.
    result = await run_in_threadpool(OIDCService.get_userinfo, idp_id, access_token)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@bp.route("/oidc/refresh/<int:idp_id>", methods=["POST"])
@portal_token_required
async def oidc_refresh(idp_id):
    """Refresh OIDC access token.

    The IdP config must be one the caller may use; anything else is a 404 and
    the IdP's token endpoint (which would receive that config's client secret)
    is never contacted.

    Args:
        idp_id: IdP configuration ID

    Request body:
        refresh_token: Refresh token from IdP

    Returns:
        New tokens
    """
    denied = await _require_scoped_oidc_idp(idp_id, "oidc_refresh")
    if denied is not None:
        return denied

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


def _resolve_scim_admin(tenant_id: int, action: str) -> tuple | None:
    """Authorize a SCIM config/token operation on ``tenant_id``.

    Regression: scim-cross-tenant -- these routes checked only the caller's
    role, so a tenant-A admin could read tenant B's SCIM config and rotate its
    bearer token. The caller must be a global admin or a tenant admin whose
    token tenant IS ``tenant_id``; any other tenant answers the same 404 as a
    tenant with no SCIM config, so neither existence nor config ids leak.
    Returns an error response, or None if allowed.
    """
    caller, error = _resolve_idp_caller()
    if caller is None or error is not None:
        return error or ApiResponse.forbidden("Access denied")
    if not caller.is_global_admin and caller.tenant_id != tenant_id:
        current_app.logger.warning(
            "scim_config_access_denied action=%s tenant_id=%s caller_tenant_id=%s",
            action,
            tenant_id,
            caller.tenant_id,
        )
        return ApiResponse.error("SCIM not configured", 404)
    return None


@bp.route("/scim/config", methods=["POST"])
@login_required
@portal_token_required
async def create_scim_config():
    """Create SCIM configuration for a tenant.

    Tenant admins always create for their own tenant (a body ``tenant_id`` is
    ignored); only global admins choose the tenant, which must exist. The
    bearer token in the response is shown once and never readable again.

    Request body:
        tenant_id: int - Tenant ID (global admins only)

    Returns:
        SCIM configuration with bearer token
    """
    caller, error = _resolve_idp_caller()
    if error is not None:
        return error

    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}

    # Tenant admins can only configure their tenant
    tenant_id = data.get("tenant_id") if caller.is_global_admin else caller.tenant_id

    if tenant_id is None:
        return ApiResponse.bad_request("tenant_id is required")
    if not isinstance(tenant_id, int) or isinstance(tenant_id, bool) or tenant_id <= 0:
        return ApiResponse.bad_request("tenant_id must be a positive integer")

    if not await run_in_threadpool(SCIMService.tenant_exists, tenant_id):
        return ApiResponse.not_found("Tenant", tenant_id)

    result = await run_in_threadpool(SCIMService.create_scim_config, tenant_id)

    return jsonify(result), 201


@bp.route("/scim/config/<int:tenant_id>", methods=["GET"])
@portal_token_required
async def get_scim_config(tenant_id):
    """Get SCIM configuration for a tenant (tenant admin of that tenant, or global admin).

    Args:
        tenant_id: Tenant ID

    Returns:
        SCIM configuration (without bearer token)
    """
    denied = _resolve_scim_admin(tenant_id, "get")
    if denied is not None:
        return denied

    config = await run_in_threadpool(SCIMService.get_scim_config, tenant_id)

    if not config:
        return ApiResponse.error("SCIM not configured", 404)

    return jsonify(config), 200


@bp.route("/scim/config/<int:tenant_id>/regenerate-token", methods=["POST"])
@portal_token_required
async def regenerate_scim_token(tenant_id):
    """Regenerate SCIM bearer token (tenant admin of that tenant, or global admin).

    The previous token stops working immediately; the new one is returned
    once and is never readable again.

    Args:
        tenant_id: Tenant ID

    Returns:
        New bearer token
    """
    denied = _resolve_scim_admin(tenant_id, "regenerate")
    if denied is not None:
        return denied

    result = await run_in_threadpool(SCIMService.regenerate_token, tenant_id)

    if "error" in result:
        return ApiResponse.error("SCIM not configured", 404)

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

    result = await run_in_threadpool(SCIMService.create_user, tenant_id, scim_user)

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

    result = await run_in_threadpool(
        SCIMService.update_user, tenant_id, user_id, scim_user
    )

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
