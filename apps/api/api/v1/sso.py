"""SSO/SAML/SCIM API endpoints for v2.2.0 Enterprise Edition.

Provides REST endpoints for SSO configuration, SAML authentication,
and SCIM 2.0 user provisioning.
"""

# flake8: noqa: E501


from functools import wraps

from quart import Blueprint, Response, jsonify, request

from apps.api.api.v1.portal_auth import generate_tokens, portal_token_required
from apps.api.auth.decorators import login_required
from apps.api.services.sso import OIDCService, SAMLService, SCIMService

bp = Blueprint("sso", __name__)


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
def create_idp_config():
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
        return jsonify({"error": "Admin permission required"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    name = data.get("name")
    if not name:
        return jsonify({"error": "name is required"}), 400

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
def update_idp_config(config_id):
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
        return jsonify({"error": "Admin permission required"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

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
        return jsonify({"error": "Admin permission required"}), 403

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
        return jsonify({"error": "No IdP configured for this tenant"}), 404

    if not idp_config.get("sso_url"):
        return jsonify({"error": "IdP SSO URL not configured"}), 400

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
def saml_acs(tenant_id):
    """SAML Assertion Consumer Service endpoint.

    Receives and processes SAML responses from IdP.

    Args:
        tenant_id: Tenant ID

    Returns:
        JWT tokens on success
    """
    saml_response = request.form.get("SAMLResponse")
    relay_state = request.form.get("RelayState")

    if not saml_response:
        return jsonify({"error": "SAMLResponse is required"}), 400

    result = SAMLService.process_saml_response(tenant_id, saml_response, relay_state)

    if "error" in result:
        return jsonify(result), 400

    # Generate tokens
    tokens = generate_tokens(result)

    return (
        jsonify(
            {
                "user": result,
                **tokens,
            }
        ),
        200,
    )


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
        return jsonify({"error": "redirect_uri is required"}), 400

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
        return jsonify({"error": "code is required"}), 400
    if not idp_id:
        return jsonify({"error": "idp_id is required"}), 400
    if not redirect_uri:
        return jsonify({"error": "redirect_uri is required"}), 400

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
        return jsonify({"error": "IdP configuration not found"}), 404

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

        return (
            jsonify(
                {
                    "user": user,
                    **elder_tokens,
                }
            ),
            200,
        )

    return jsonify({"error": "JIT provisioning is disabled for this IdP"}), 403


@bp.route("/oidc/logout/<int:idp_id>", methods=["POST"])
@portal_token_required
def oidc_logout(idp_id):
    """Initiate OIDC logout (RP-Initiated Logout).

    Args:
        idp_id: IdP configuration ID

    Request body:
        id_token_hint: Optional ID token hint
        post_logout_redirect_uri: Optional redirect URI after logout

    Returns:
        End session endpoint URL
    """
    data = request.get_json() or {}
    id_token_hint = data.get("id_token_hint")
    post_logout_redirect_uri = data.get("post_logout_redirect_uri")

    result = OIDCService.logout(idp_id, id_token_hint, post_logout_redirect_uri)

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
        return jsonify({"error": "X-OIDC-Access-Token header is required"}), 400

    result = OIDCService.get_userinfo(idp_id, access_token)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@bp.route("/oidc/refresh/<int:idp_id>", methods=["POST"])
@portal_token_required
def oidc_refresh(idp_id):
    """Refresh OIDC access token.

    Args:
        idp_id: IdP configuration ID

    Request body:
        refresh_token: Refresh token from IdP

    Returns:
        New tokens
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        return jsonify({"error": "refresh_token is required"}), 400

    result = OIDCService.refresh_tokens(idp_id, refresh_token)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


# =============================================================================
# SCIM Endpoints
# =============================================================================


@bp.route("/scim/config", methods=["POST"])
@login_required
@portal_token_required
def create_scim_config():
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
        return jsonify({"error": "Admin permission required"}), 403

    data = request.get_json() or {}

    # Tenant admins can only configure their tenant
    tenant_id = data.get("tenant_id")
    if request.portal_user.get("global_role") != "admin":
        tenant_id = request.portal_user.get("tenant_id")

    if not tenant_id:
        return jsonify({"error": "tenant_id is required"}), 400

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
        return jsonify({"error": "SCIM not configured"}), 404

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
        return jsonify({"error": "Admin permission required"}), 403

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
def scim_create_user(tenant_id):
    """SCIM 2.0 - Create user.

    Request body:
        SCIM User resource

    Returns:
        Created SCIM User
    """
    scim_user = request.get_json()
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
def scim_update_user(tenant_id, user_id):
    """SCIM 2.0 - Replace user.

    Args:
        tenant_id: Tenant ID
        user_id: User ID

    Request body:
        SCIM User resource

    Returns:
        Updated SCIM User
    """
    scim_user = request.get_json()
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
