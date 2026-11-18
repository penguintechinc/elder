"""SAML 2.0 Service Provider implementation.

Handles SAML SSO authentication with IdP configurations
at both global and tenant levels.
"""

# flake8: noqa: E501

import datetime
from collections.abc import Mapping
from typing import Any, Optional

from quart import current_app

from apps.api.services.sso.idp_access import (
    IdPCaller,
    IdPConfigView,
    IdPForbiddenError,
    IdPNotFoundError,
)

# Columns an admin may change through update_idp_config. ``tenant_id`` and
# ``idp_type`` are deliberately absent: a config can never be re-homed to a
# different tenant (regression: idp-config-cross-tenant).
_UPDATABLE_FIELDS = frozenset(
    {
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
)


class SAMLService:
    """SAML 2.0 Service Provider implementation."""

    @staticmethod
    def get_idp_config(tenant_id: int | None = None) -> dict | None:
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
        caller: IdPCaller,
        name: str,
        idp_type: str = "saml",
        tenant_id: int | None = None,
        entity_id: str | None = None,
        metadata_url: str | None = None,
        sso_url: str | None = None,
        slo_url: str | None = None,
        certificate: str | None = None,
        oidc_client_id: str | None = None,
        oidc_client_secret: str | None = None,
        oidc_issuer_url: str | None = None,
        oidc_scopes: str | None = None,
        oidc_response_type: str | None = None,
        oidc_token_endpoint_auth_method: str | None = None,
        attribute_mappings: dict | None = None,
        jit_provisioning_enabled: bool = True,
        default_role: str = "reader",
    ) -> dict:
        """Create a new IdP configuration bound to the caller's tenant.

        A non-global caller always creates in their own tenant -- passing any
        other ``tenant_id`` raises ``IdPForbiddenError``. Only a global admin
        may choose the owning tenant (``None`` = a global config).

        Args:
            caller: Authenticated admin creating the config
            name: Display name for the IdP
            idp_type: Type (saml or oidc)
            tenant_id: Requested owning tenant (global admins only; ignored
                unless it equals the caller's own tenant for everyone else)
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
            Created configuration (secret-free ``IdPConfigView`` dict)
        """
        db = current_app.db
        if caller.is_global_admin:
            owner_tenant_id = tenant_id
        else:
            if caller.tenant_id is None:
                raise IdPForbiddenError(
                    "Tenant claim required for IdP configuration access"
                )
            if tenant_id is not None and tenant_id != caller.tenant_id:
                raise IdPForbiddenError(
                    "Cannot create an IdP configuration for another tenant"
                )
            owner_tenant_id = caller.tenant_id

        config_id = db.idp_configurations.insert(
            tenant_id=owner_tenant_id,
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

        created = db(db.idp_configurations.id == config_id).select().first()
        return IdPConfigView.from_record(created).to_dict()

    @staticmethod
    def update_idp_config(
        config_id: int, caller: IdPCaller, updates: Mapping[str, Any]
    ) -> dict:
        """Update an IdP configuration the caller owns.

        The row is loaded through the caller's tenant-scoped query, so another
        tenant's (or a global) config raises ``IdPNotFoundError`` for non-global
        callers. ``tenant_id`` in ``updates`` must equal the config's current
        tenant -- re-homing is never allowed (``IdPForbiddenError``).

        Args:
            config_id: Configuration ID
            caller: Authenticated admin performing the update
            updates: Requested field changes (non-updatable keys are ignored)

        Returns:
            Updated configuration (secret-free ``IdPConfigView`` dict) plus
            ``"updated": True``
        """
        db = current_app.db
        table = db.idp_configurations
        owned = caller.scoped_query(db) & (table.id == config_id)

        config = db(owned).select().first()
        if config is None:
            raise IdPNotFoundError(config_id)

        if "tenant_id" in updates and updates["tenant_id"] != config.tenant_id:
            raise IdPForbiddenError(
                "tenant_id of an IdP configuration cannot be changed"
            )

        changes = {k: v for k, v in updates.items() if k in _UPDATABLE_FIELDS}
        # The UPDATE carries the same tenant-scoped predicate as the load, so a
        # row deleted/moved between the statements simply matches nothing.
        if changes:
            db(owned).update(**changes)
            db.commit()

        # penguin-dal runs each statement in its own session, so a concurrent
        # DELETE can land before this reload: that is a 404, never a 500.
        fresh = db(owned).select().first()
        if fresh is None:
            raise IdPNotFoundError(config_id)
        return {**IdPConfigView.from_record(fresh).to_dict(), "updated": True}

    @staticmethod
    def delete_idp_config(config_id: int, caller: IdPCaller) -> dict:
        """Delete an IdP configuration the caller owns.

        The delete itself carries the tenant-scoped predicate (one atomic
        statement, no check-then-act gap); zero rows deleted means the config
        is missing or not the caller's, reported as ``IdPNotFoundError``.

        Args:
            config_id: Configuration ID
            caller: Authenticated admin performing the delete

        Returns:
            ``{"deleted": True}``
        """
        db = current_app.db
        owned = caller.scoped_query(db) & (db.idp_configurations.id == config_id)

        if not db(owned).delete():
            raise IdPNotFoundError(config_id)
        db.commit()

        return {"deleted": True}

    @staticmethod
    def process_saml_response(
        tenant_id: int, saml_response: str, relay_state: str | None = None
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
                last_login_at=datetime.datetime.now(datetime.UTC)
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
            last_login_at=datetime.datetime.now(datetime.UTC),
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
    def get_sp_metadata(base_url: str, tenant_id: int | None = None) -> str:
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
    def list_idp_configs(caller: IdPCaller, tenant_id: int | None = None) -> list:
        """List the IdP configurations the caller may see (secret-free).

        Non-global callers get exactly their own tenant's configs (global
        configs are not listed); asking for a different ``tenant_id`` raises
        ``IdPForbiddenError``. Global admins get every config, or -- with a
        ``tenant_id`` filter -- that tenant's configs plus global ones.

        Args:
            caller: Authenticated admin listing configs
            tenant_id: Optional tenant filter

        Returns:
            List of ``IdPConfigView`` dicts (never includes secrets)
        """
        db = current_app.db
        table = db.idp_configurations
        query = caller.scoped_query(db)

        if tenant_id is not None:
            if caller.is_global_admin:
                query = query & (
                    (table.tenant_id == tenant_id) | (table.tenant_id == None)  # noqa: E711
                )
            elif tenant_id != caller.tenant_id:
                raise IdPForbiddenError(
                    "Cannot list IdP configurations for another tenant"
                )

        configs = db(query).select(orderby=table.id)
        return [IdPConfigView.from_record(c).to_dict() for c in configs]
