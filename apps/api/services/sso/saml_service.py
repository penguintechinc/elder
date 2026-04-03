"""SAML 2.0 Service Provider implementation.

Handles SAML SSO authentication with IdP configurations
at both global and tenant levels.
"""

# flake8: noqa: E501


import datetime
from typing import Optional

from flask import current_app


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
    def process_saml_response(
        tenant_id: int, saml_response: str, relay_state: Optional[str] = None
    ) -> dict:
        """Process SAML response and authenticate user.

        Args:
            tenant_id: Tenant context
            saml_response: Base64-encoded SAML response
            relay_state: Optional relay state

        Returns:
            Authenticated user dict or error
        """
        # Get IdP config
        idp_config = SAMLService.get_idp_config(tenant_id)
        if not idp_config:
            return {"error": "No IdP configured for this tenant"}

        # In production, use python3-saml to validate and parse response
        # For now, simulate SAML response parsing
        # This would be replaced with actual SAML validation

        # Simulated parsed attributes (would come from SAML response)
        # parsed_attrs = saml_auth.get_attributes()
        # email = parsed_attrs.get('email', [None])[0]
        # name = parsed_attrs.get('name', [None])[0]

        return {
            "error": "SAML response processing not yet implemented",
            "note": "Requires python3-saml library configuration",
        }

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

        # Create new user (no password for SSO users)
        user_id = db.portal_users.insert(
            tenant_id=tenant_id,
            email=email.lower(),
            password_hash=None,  # SSO users don't have passwords
            full_name=full_name,
            tenant_role=idp_config.get("default_role", "reader"),
            is_active=True,
            email_verified=True,  # SSO validates email
            last_login_at=datetime.datetime.now(datetime.timezone.utc),
        )
        db.commit()

        return {
            "id": user_id,
            "email": email.lower(),
            "tenant_id": tenant_id,
            "tenant_role": idp_config.get("default_role", "reader"),
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
            configs = db(db.idp_configurations).select()

        return [SAMLService._config_to_dict(c) for c in configs]
