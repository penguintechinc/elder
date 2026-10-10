"""SAML 2.0 Service Provider implementation.

Handles SAML SSO authentication with IdP configurations
at both global and tenant levels.
"""

# flake8: noqa: E501


import datetime
from typing import Optional

from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.constants import OneLogin_Saml2_Constants
from quart import current_app, request


class SAMLService:
    """SAML 2.0 Service Provider implementation."""

    @staticmethod
    def get_idp_config(tenant_id: Optional[int] = None) -> Optional[dict]:
        """Get IdP configuration for a tenant or global.

        Args:
            tenant_id: Tenant ID or None for global IdP

        Returns:
            IdP configuration dict or None
        """
        db = current_app.db
        if tenant_id:
            # Try tenant-specific IdP first
            config = (
                db(
                    (db.idp_configurations.tenant_id == tenant_id)
                    & (db.idp_configurations.is_active == True)  # noqa: E712
                )
                .select()
                .first()
            )

            if config:
                return SAMLService._config_to_dict(config)

        # Fall back to global IdP
        config = (
            db(
                (db.idp_configurations.tenant_id == None)  # noqa: E711
                & (db.idp_configurations.is_active == True)  # noqa: E712
            )
            .select()
            .first()
        )

        if config:
            return SAMLService._config_to_dict(config)

        return None

    @staticmethod
    def _config_to_dict(config) -> dict:
        """Convert IdP config record to dict."""
        result = {
            "id": config.id,
            "tenant_id": config.tenant_id,
            "idp_type": config.idp_type,
            "name": config.name,
            "attribute_mappings": config.attribute_mappings or {},
            "jit_provisioning_enabled": config.jit_provisioning_enabled,
            "default_role": config.default_role,
        }

        # Add SAML-specific fields
        if config.idp_type == "saml":
            result.update(
                {
                    "entity_id": config.entity_id,
                    "metadata_url": config.metadata_url,
                    "sso_url": config.sso_url,
                    "slo_url": config.slo_url,
                    "certificate": config.certificate,
                }
            )

        # Add OIDC-specific fields
        elif config.idp_type == "oidc":
            result.update(
                {
                    "oidc_client_id": config.oidc_client_id,
                    "oidc_client_secret": config.oidc_client_secret,
                    "oidc_issuer_url": config.oidc_issuer_url,
                    "oidc_scopes": config.oidc_scopes or "openid profile email",
                    "oidc_response_type": config.oidc_response_type or "code",
                    "oidc_token_endpoint_auth_method": config.oidc_token_endpoint_auth_method
                    or "client_secret_basic",
                }
            )

        return result

    @staticmethod
    def create_idp_config(
        name: str,
        idp_type: str = "saml",
        tenant_id: Optional[int] = None,
        entity_id: Optional[str] = None,
        metadata_url: Optional[str] = None,
        sso_url: Optional[str] = None,
        slo_url: Optional[str] = None,
        certificate: Optional[str] = None,
        oidc_client_id: Optional[str] = None,
        oidc_client_secret: Optional[str] = None,
        oidc_issuer_url: Optional[str] = None,
        oidc_scopes: Optional[str] = None,
        oidc_response_type: Optional[str] = None,
        oidc_token_endpoint_auth_method: Optional[str] = None,
        attribute_mappings: Optional[dict] = None,
        jit_provisioning_enabled: bool = True,
        default_role: str = "reader",
    ) -> dict:
        """Create a new IdP configuration.

        Args:
            name: Display name for the IdP
            idp_type: Type (saml or oidc)
            tenant_id: Tenant ID or None for global
            entity_id: SAML Entity ID
            metadata_url: IdP metadata URL
            sso_url: SSO endpoint URL
            slo_url: Single Logout URL
            certificate: X.509 certificate
            oidc_client_id: OIDC Client ID
            oidc_client_secret: OIDC Client Secret
            oidc_issuer_url: OIDC Issuer URL
            oidc_scopes: OIDC scopes (space-separated)
            oidc_response_type: OIDC response type
            oidc_token_endpoint_auth_method: Token endpoint auth method
            attribute_mappings: Map IdP attributes to user fields
            jit_provisioning_enabled: Auto-create users on first login
            default_role: Default role for JIT-provisioned users

        Returns:
            Created configuration dict
        """
        db = current_app.db
        config_id = db.idp_configurations.insert(
            tenant_id=tenant_id,
            idp_type=idp_type,
            name=name,
            entity_id=entity_id,
            metadata_url=metadata_url,
            sso_url=sso_url,
            slo_url=slo_url,
            certificate=certificate,
            oidc_client_id=oidc_client_id,
            oidc_client_secret=oidc_client_secret,
            oidc_issuer_url=oidc_issuer_url,
            oidc_scopes=oidc_scopes or "openid profile email",
            oidc_response_type=oidc_response_type or "code",
            oidc_token_endpoint_auth_method=oidc_token_endpoint_auth_method
            or "client_secret_basic",
            attribute_mappings=attribute_mappings or {},
            jit_provisioning_enabled=jit_provisioning_enabled,
            default_role=default_role,
            is_active=True,
        )
        db.commit()

        return {"id": config_id, "name": name, "tenant_id": tenant_id}

    @staticmethod
    def update_idp_config(config_id: int, **kwargs) -> dict:
        """Update an IdP configuration.

        Args:
            config_id: Configuration ID
            **kwargs: Fields to update

        Returns:
            Updated configuration dict or error
        """
        db = current_app.db
        config = db.idp_configurations[config_id]
        if not config:
            return {"error": "IdP configuration not found"}

        # Filter allowed fields
        allowed_fields = {
            "name",
            "entity_id",
            "metadata_url",
            "sso_url",
            "slo_url",
            "certificate",
            "attribute_mappings",
            "jit_provisioning_enabled",
            "default_role",
            "is_active",
            "oidc_client_id",
            "oidc_client_secret",
            "oidc_issuer_url",
            "oidc_scopes",
            "oidc_response_type",
            "oidc_token_endpoint_auth_method",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if updates:
            db(db.idp_configurations.id == config_id).update(**updates)

        return {"id": config_id, "updated": True}

    @staticmethod
    def delete_idp_config(config_id: int) -> dict:
        """Delete an IdP configuration.

        Args:
            config_id: Configuration ID

        Returns:
            Success dict or error
        """
        db = current_app.db
        config = db.idp_configurations[config_id]
        if not config:
            return {"error": "IdP configuration not found"}

        db(db.idp_configurations.id == config_id).delete()
        db.commit()

        return {"deleted": True}

    @staticmethod
    def _build_saml_settings(tenant_id: int, idp_config: dict, base_url: str) -> dict:
        """Build OneLogin SAML settings from a stored IdP configuration.

        Args:
            tenant_id: Tenant ID used to derive the SP ACS/SLO/metadata URLs
            idp_config: IdP configuration dict (from ``get_idp_config``)
            base_url: Scheme + host of this Elder instance (no trailing slash)

        Returns:
            OneLogin settings dict suitable for ``OneLogin_Saml2_Auth``
        """
        base_url = (base_url or "").rstrip("/")
        sp_entity_id = f"{base_url}/saml/metadata/{tenant_id}"
        acs_url = f"{base_url}/api/v1/sso/saml/acs/{tenant_id}"
        slo_url = f"{base_url}/api/v1/sso/saml/slo/{tenant_id}"

        return {
            # strict=True enforces NotBefore/NotOnOrAfter, audience, destination
            # and signature validation — never relax this for production IdPs.
            "strict": True,
            "debug": False,
            "sp": {
                "entityId": sp_entity_id,
                "assertionConsumerService": {
                    "url": acs_url,
                    "binding": OneLogin_Saml2_Constants.BINDING_HTTP_POST,
                },
                "singleLogoutService": {
                    "url": slo_url,
                    "binding": OneLogin_Saml2_Constants.BINDING_HTTP_REDIRECT,
                },
                "NameIDFormat": OneLogin_Saml2_Constants.NAMEID_EMAIL_ADDRESS,
                "x509cert": "",
                "privateKey": "",
            },
            "idp": {
                "entityId": idp_config.get("entity_id") or "",
                "singleSignOnService": {
                    "url": idp_config.get("sso_url") or "",
                    "binding": OneLogin_Saml2_Constants.BINDING_HTTP_REDIRECT,
                },
                "singleLogoutService": {
                    "url": idp_config.get("slo_url") or "",
                    "binding": OneLogin_Saml2_Constants.BINDING_HTTP_REDIRECT,
                },
                "x509cert": idp_config.get("certificate") or "",
            },
            "security": {
                # Require the IdP to sign assertions and validate that signature
                # against the configured x509 certificate.
                "wantAssertionsSigned": True,
                "wantMessagesSigned": False,
                "wantNameId": True,
                "requestedAuthnContext": False,
                "rejectUnsolicitedResponsesWithInResponseTo": False,
            },
        }

    @staticmethod
    def _prepare_request_data(saml_response: str, relay_state: Optional[str]) -> dict:
        """Build the OneLogin request-data dict from the active HTTP request.

        OneLogin uses this to validate the ``Destination`` of the assertion
        against the actual ACS URL. Falls back to minimal data when no request
        context is available.
        """
        post_data = {"SAMLResponse": saml_response}
        if relay_state is not None:
            post_data["RelayState"] = relay_state

        try:
            https = "on" if request.scheme == "https" else "off"
            host = request.host
            # request.path already includes the mounted blueprint prefix.
            script_name = request.path
            port = request.server[1] if request.server else None
        except RuntimeError:
            # No request context (e.g. unit tests mocking OneLogin).
            https, host, script_name, port = "off", "", "", None

        req = {
            "https": https,
            "http_host": host,
            "script_name": script_name,
            "get_data": {},
            "post_data": post_data,
        }
        if port:
            req["server_port"] = str(port)
        return req

    @staticmethod
    def process_saml_response(
        tenant_id: int,
        saml_response: str,
        relay_state: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> dict:
        """Validate and process a SAML response, then authenticate the user.

        Performs full SAML assertion validation via python3-saml (OneLogin) in
        strict mode: the IdP signature is verified against the configured x509
        certificate, and the assertion's audience, conditions (NotBefore /
        NotOnOrAfter) and destination are enforced. Unverified assertions are
        never trusted.

        Args:
            tenant_id: Tenant context (also used to derive SP ACS/SLO URLs)
            saml_response: Base64-encoded SAMLResponse from the IdP POST
            relay_state: Optional relay state echoed by the IdP
            base_url: Scheme + host of this instance (defaults to request host)

        Returns:
            On success, the authenticated/provisioned user dict (ready for
            ``generate_tokens``) augmented with ``name_id``, ``session_index``
            and ``attributes``. On failure, a dict with an ``error`` key.
        """
        idp_config = SAMLService.get_idp_config(tenant_id)
        if not idp_config:
            return {"error": "No IdP configured for this tenant"}

        if idp_config.get("idp_type") != "saml":
            return {"error": "Configured IdP is not a SAML provider"}

        if not idp_config.get("certificate"):
            return {"error": "IdP signing certificate not configured"}

        if base_url is None:
            try:
                base_url = request.host_url
            except RuntimeError:
                base_url = ""

        settings = SAMLService._build_saml_settings(tenant_id, idp_config, base_url)
        request_data = SAMLService._prepare_request_data(saml_response, relay_state)

        try:
            auth = OneLogin_Saml2_Auth(request_data, old_settings=settings)
            auth.process_response()
        except Exception as exc:  # malformed XML, settings errors, etc.
            try:
                current_app.logger.warning(f"SAML response processing failed: {exc}")
            except Exception:
                pass
            return {"error": "Failed to process SAML response", "detail": str(exc)}

        errors = auth.get_errors()
        if errors:
            reason = None
            try:
                reason = auth.get_last_error_reason()
            except Exception:
                pass
            return {
                "error": "SAML validation failed",
                "details": errors,
                "reason": reason,
            }

        if not auth.is_authenticated():
            return {"error": "SAML assertion did not authenticate the user"}

        name_id = auth.get_nameid()
        attributes = auth.get_attributes() or {}
        session_index = auth.get_session_index()

        email = SAMLService._extract_email(name_id, attributes, idp_config)
        if not email:
            return {"error": "No email found in SAML assertion"}

        # Match an existing identity, or JIT-provision when enabled.
        db = current_app.db
        existing = (
            db(
                (db.portal_users.email == email.lower())
                & (db.portal_users.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not existing and not idp_config.get("jit_provisioning_enabled"):
            return {"error": "No matching user and JIT provisioning is disabled"}

        user = SAMLService.jit_provision_user(tenant_id, email, idp_config, attributes)
        if "error" in user:
            return user

        # Attach SAML session context for downstream SLO / auditing.
        user["name_id"] = name_id
        user["session_index"] = session_index
        user["attributes"] = attributes
        return user

    @staticmethod
    def _extract_email(
        name_id: Optional[str], attributes: dict, idp_config: dict
    ) -> Optional[str]:
        """Resolve the user's email from a mapped attribute or the NameID."""
        mappings = idp_config.get("attribute_mappings") or {}
        email_attr = mappings.get("email", "email")

        value = attributes.get(email_attr)
        if isinstance(value, list):
            value = value[0] if value else None
        if value:
            return str(value)

        # Fall back to NameID when it looks like an email address.
        if name_id and "@" in name_id:
            return name_id
        return None

    @staticmethod
    def _map_role(attributes: dict, idp_config: dict) -> str:
        """Map a SAML attribute value to an Elder tenant role.

        Uses ``attribute_mappings.role`` to name the SAML attribute carrying
        the role/group, and an optional ``attribute_mappings.role_map`` to
        translate IdP values to Elder roles. Falls back to ``default_role``.
        """
        mappings = idp_config.get("attribute_mappings") or {}
        default = idp_config.get("default_role") or "reader"

        role_attr = mappings.get("role")
        if not role_attr:
            return default

        value = attributes.get(role_attr)
        if isinstance(value, list):
            value = value[0] if value else None
        if not value:
            return default

        role_map = mappings.get("role_map") or {}
        mapped = role_map.get(value, value)

        valid_roles = {"admin", "maintainer", "reader"}
        return mapped if mapped in valid_roles else default

    @staticmethod
    def jit_provision_user(
        tenant_id: int, email: str, idp_config: dict, attributes: dict
    ) -> dict:
        """Just-in-time provision a user from SAML attributes.

        Args:
            tenant_id: Tenant ID
            email: User email from SAML
            idp_config: IdP configuration
            attributes: SAML attributes

        Returns:
            Created or existing user dict
        """
        db = current_app.db
        # Check if user exists
        existing = (
            db(
                (db.portal_users.email == email.lower())
                & (db.portal_users.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if existing:
            # Update last login
            db(db.portal_users.id == existing.id).update(
                last_login_at=datetime.datetime.now(datetime.timezone.utc)
            )
            return {
                "id": existing.id,
                "email": existing.email,
                "tenant_id": tenant_id,
                "tenant_role": existing.tenant_role,
                "global_role": existing.global_role,
            }

        # Map attributes to user fields
        mappings = idp_config.get("attribute_mappings", {})
        full_name = attributes.get(mappings.get("full_name", "name"))
        if isinstance(full_name, list):
            full_name = full_name[0] if full_name else None

        tenant_role = SAMLService._map_role(attributes, idp_config)

        # Create new user (no password for SSO users)
        user_id = db.portal_users.insert(
            tenant_id=tenant_id,
            email=email.lower(),
            password_hash=None,  # SSO users don't have passwords
            full_name=full_name,
            tenant_role=tenant_role,
            is_active=True,
            email_verified=True,  # SSO validates email
            last_login_at=datetime.datetime.now(datetime.timezone.utc),
        )
        db.commit()

        return {
            "id": user_id,
            "email": email.lower(),
            "tenant_id": tenant_id,
            "tenant_role": tenant_role,
            "global_role": None,
            "jit_provisioned": True,
        }

    @staticmethod
    def get_sp_metadata(base_url: str, tenant_id: Optional[int] = None) -> str:
        """Generate Service Provider SAML metadata XML.

        Args:
            base_url: Base URL of the Elder instance
            tenant_id: Optional tenant ID for tenant-specific metadata

        Returns:
            SAML SP metadata XML string
        """
        entity_id = f"{base_url}/saml/metadata"
        acs_url = f"{base_url}/api/v1/sso/saml/acs"
        slo_url = f"{base_url}/api/v1/sso/saml/slo"

        if tenant_id:
            entity_id = f"{base_url}/saml/metadata/{tenant_id}"
            acs_url = f"{base_url}/api/v1/sso/saml/acs/{tenant_id}"
            slo_url = f"{base_url}/api/v1/sso/saml/slo/{tenant_id}"

        # Basic SP metadata template
        metadata = f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
                     entityID="{entity_id}">
  <md:SPSSODescriptor AuthnRequestsSigned="true"
                      WantAssertionsSigned="true"
                      protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>
    <md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
                                 Location="{acs_url}"
                                 index="1"/>
    <md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
                            Location="{slo_url}"/>
  </md:SPSSODescriptor>
</md:EntityDescriptor>"""

        return metadata

    @staticmethod
    def list_idp_configs(tenant_id: Optional[int] = None) -> list:
        """List all IdP configurations.

        Args:
            tenant_id: Filter by tenant or None for all

        Returns:
            List of IdP configurations
        """
        db = current_app.db
        if tenant_id:
            configs = db(
                (db.idp_configurations.tenant_id == tenant_id)
                | (db.idp_configurations.tenant_id == None)  # noqa: E711
            ).select()
        else:
            configs = db(db.idp_configurations.id > 0).select()

        return [SAMLService._config_to_dict(c) for c in configs]
