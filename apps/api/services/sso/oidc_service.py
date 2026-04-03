"""OpenID Connect (OIDC) Service implementation for v3.0.0.

Handles OIDC SSO authentication with IdP configurations
at both global and tenant levels using Authlib.
"""

# flake8: noqa: E501


import datetime
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import jwt
import requests
from authlib.integrations.requests_client import OAuth2Session
from flask import current_app


class OIDCService:
    """OpenID Connect authentication service."""

    @staticmethod
    def get_idp_config(idp_id: int) -> Optional[Dict[str, Any]]:
        """Get OIDC IdP configuration by ID.

        Args:
            idp_id: IdP configuration ID

        Returns:
            IdP configuration dict or None
        """
        db = current_app.db
        config = (
            db(
                (db.idp_configurations.id == idp_id)
                & (db.idp_configurations.idp_type == "oidc")
                & (db.idp_configurations.is_active == True)  # noqa: E712
            )
            .select()
            .first()
        )

        if config:
            return OIDCService._config_to_dict(config)

        return None

    @staticmethod
    def get_idp_config_by_tenant(
        tenant_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get OIDC IdP configuration for a tenant or global.

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
                    & (db.idp_configurations.idp_type == "oidc")
                    & (db.idp_configurations.is_active == True)  # noqa: E712
                )
                .select()
                .first()
            )

            if config:
                return OIDCService._config_to_dict(config)

        # Fall back to global IdP
        config = (
            db(
                (db.idp_configurations.tenant_id == None)  # noqa: E711
                & (db.idp_configurations.idp_type == "oidc")
                & (db.idp_configurations.is_active == True)  # noqa: E712
            )
            .select()
            .first()
        )

        if config:
            return OIDCService._config_to_dict(config)

        return None

    @staticmethod
    def _config_to_dict(config) -> Dict[str, Any]:
        """Convert IdP config record to dict."""
        return {
            "id": config.id,
            "tenant_id": config.tenant_id,
            "idp_type": config.idp_type,
            "name": config.name,
            "oidc_client_id": config.oidc_client_id,
            "oidc_client_secret": config.oidc_client_secret,
            "oidc_issuer_url": config.oidc_issuer_url,
            "oidc_scopes": config.oidc_scopes or "openid profile email",
            "oidc_response_type": config.oidc_response_type or "code",
            "oidc_token_endpoint_auth_method": config.oidc_token_endpoint_auth_method
            or "client_secret_basic",
            "attribute_mappings": config.attribute_mappings or {},
            "jit_provisioning_enabled": config.jit_provisioning_enabled,
            "default_role": config.default_role,
        }

    @staticmethod
    def discover_configuration(issuer_url: str) -> Dict[str, Any]:
        """Perform OIDC Discovery to get provider configuration.

        Args:
            issuer_url: OIDC Issuer URL

        Returns:
            Discovery document dict

        Raises:
            requests.RequestException: If discovery fails
        """
        # Ensure issuer URL doesn't end with /
        issuer_url = issuer_url.rstrip("/")
        discovery_url = f"{issuer_url}/.well-known/openid-configuration"

        try:
            response = requests.get(discovery_url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            current_app.logger.error(f"OIDC discovery failed for {issuer_url}: {e}")
            raise

    @staticmethod
    def get_authorization_url(
        idp_id: int, redirect_uri: str, state: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate OIDC authorization URL.

        Args:
            idp_id: IdP configuration ID
            redirect_uri: Callback redirect URI
            state: Optional state parameter (generated if not provided)

        Returns:
            Dict with authorization_url and state
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "IdP configuration not found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            authorization_endpoint = discovery.get("authorization_endpoint")

            if not authorization_endpoint:
                return {"error": "Authorization endpoint not found in discovery"}

            # Generate state if not provided
            if not state:
                state = secrets.token_urlsafe(32)

            # Parse scopes
            scopes = idp_config["oidc_scopes"].split()

            # Build authorization URL
            params = {
                "client_id": idp_config["oidc_client_id"],
                "redirect_uri": redirect_uri,
                "response_type": idp_config["oidc_response_type"],
                "scope": " ".join(scopes),
                "state": state,
            }

            authorization_url = f"{authorization_endpoint}?{urlencode(params)}"

            return {
                "authorization_url": authorization_url,
                "state": state,
            }

        except Exception as e:
            current_app.logger.error(f"Failed to generate authorization URL: {e}")
            return {"error": str(e)}

    @staticmethod
    def exchange_code_for_tokens(
        idp_id: int, code: str, redirect_uri: str
    ) -> Dict[str, Any]:
        """Exchange authorization code for tokens.

        Args:
            idp_id: IdP configuration ID
            code: Authorization code from IdP
            redirect_uri: Redirect URI used in authorization

        Returns:
            Dict with access_token, id_token, refresh_token, etc.
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "IdP configuration not found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            token_endpoint = discovery.get("token_endpoint")

            if not token_endpoint:
                return {"error": "Token endpoint not found in discovery"}

            # Create OAuth2 session
            client = OAuth2Session(
                client_id=idp_config["oidc_client_id"],
                client_secret=idp_config["oidc_client_secret"],
                token_endpoint_auth_method=idp_config[
                    "oidc_token_endpoint_auth_method"
                ],
            )

            # Exchange code for tokens
            token = client.fetch_token(
                url=token_endpoint,
                grant_type="authorization_code",
                code=code,
                redirect_uri=redirect_uri,
            )

            return {
                "access_token": token.get("access_token"),
                "id_token": token.get("id_token"),
                "refresh_token": token.get("refresh_token"),
                "token_type": token.get("token_type", "Bearer"),
                "expires_in": token.get("expires_in"),
            }

        except Exception as e:
            current_app.logger.error(f"Failed to exchange code for tokens: {e}")
            return {"error": str(e)}

    @staticmethod
    def validate_id_token(idp_id: int, id_token: str) -> Dict[str, Any]:
        """Validate and decode OIDC ID token.

        Args:
            idp_id: IdP configuration ID
            id_token: JWT ID token from IdP

        Returns:
            Decoded token claims or error dict
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "IdP configuration not found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            jwks_uri = discovery.get("jwks_uri")

            if not jwks_uri:
                return {"error": "JWKS URI not found in discovery"}

            # Fetch JWKS
            jwks_response = requests.get(jwks_uri, timeout=10)
            jwks_response.raise_for_status()
            jwks = jwks_response.json()

            # Decode token header to get key ID
            unverified_header = jwt.get_unverified_header(id_token)
            kid = unverified_header.get("kid")

            # Find matching key
            signing_key = None
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
                    break

            if not signing_key:
                return {"error": "Signing key not found in JWKS"}

            # Validate and decode ID token
            claims = jwt.decode(
                id_token,
                signing_key,
                algorithms=["RS256"],
                audience=idp_config["oidc_client_id"],
                issuer=idp_config["oidc_issuer_url"],
                options={"verify_exp": True},
            )

            return claims

        except jwt.ExpiredSignatureError:
            return {"error": "ID token has expired"}
        except jwt.InvalidTokenError as e:
            return {"error": f"Invalid ID token: {str(e)}"}
        except Exception as e:
            current_app.logger.error(f"Failed to validate ID token: {e}")
            return {"error": str(e)}

    @staticmethod
    def get_userinfo(idp_id: int, access_token: str) -> Dict[str, Any]:
        """Get user information from userinfo endpoint.

        Args:
            idp_id: IdP configuration ID
            access_token: Access token from IdP

        Returns:
            User info claims or error dict
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "IdP configuration not found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            userinfo_endpoint = discovery.get("userinfo_endpoint")

            if not userinfo_endpoint:
                return {"error": "Userinfo endpoint not found in discovery"}

            # Call userinfo endpoint
            headers = {"Authorization": f"Bearer {access_token}"}
            response = requests.get(userinfo_endpoint, headers=headers, timeout=10)
            response.raise_for_status()

            return response.json()

        except requests.RequestException as e:
            current_app.logger.error(f"Failed to get userinfo: {e}")
            return {"error": str(e)}

    @staticmethod
    def refresh_tokens(idp_id: int, refresh_token: str) -> Dict[str, Any]:
        """Refresh access token using refresh token.

        Args:
            idp_id: IdP configuration ID
            refresh_token: Refresh token from IdP

        Returns:
            New tokens or error dict
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "IdP configuration not found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            token_endpoint = discovery.get("token_endpoint")

            if not token_endpoint:
                return {"error": "Token endpoint not found in discovery"}

            # Create OAuth2 session
            client = OAuth2Session(
                client_id=idp_config["oidc_client_id"],
                client_secret=idp_config["oidc_client_secret"],
                token_endpoint_auth_method=idp_config[
                    "oidc_token_endpoint_auth_method"
                ],
            )

            # Refresh tokens
            token = client.refresh_token(
                url=token_endpoint,
                refresh_token=refresh_token,
            )

            return {
                "access_token": token.get("access_token"),
                "id_token": token.get("id_token"),
                "refresh_token": token.get("refresh_token"),
                "token_type": token.get("token_type", "Bearer"),
                "expires_in": token.get("expires_in"),
            }

        except Exception as e:
            current_app.logger.error(f"Failed to refresh tokens: {e}")
            return {"error": str(e)}

    @staticmethod
    def jit_provision_user(
        tenant_id: int,
        idp_config: Dict[str, Any],
        id_token_claims: Dict[str, Any],
        userinfo: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Just-in-time provision a user from OIDC claims.

        Args:
            tenant_id: Tenant ID
            idp_config: IdP configuration
            id_token_claims: Claims from ID token
            userinfo: Optional additional userinfo claims

        Returns:
            Created or existing user dict
        """
        db = current_app.db

        # Combine claims (userinfo takes precedence)
        claims = {**id_token_claims, **(userinfo or {})}

        # Get attribute mappings (default OIDC standard claims)
        mappings = idp_config.get("attribute_mappings", {})
        email_claim = mappings.get("email", "email")
        name_claim = mappings.get("full_name", "name")
        external_id_claim = mappings.get("external_id", "sub")

        email = claims.get(email_claim)
        full_name = claims.get(name_claim)
        external_id = claims.get(external_id_claim)

        if not email:
            return {"error": "Email claim not found in OIDC response"}

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

        # Create new user (no password for SSO users)
        user_id = db.portal_users.insert(
            tenant_id=tenant_id,
            email=email.lower(),
            password_hash=None,  # SSO users don't have passwords
            full_name=full_name,
            tenant_role=idp_config.get("default_role", "reader"),
            is_active=True,
            email_verified=True,  # OIDC validates email
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
    def logout(
        idp_id: int,
        id_token_hint: Optional[str] = None,
        post_logout_redirect_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Initiate OIDC logout (RP-Initiated Logout).

        Args:
            idp_id: IdP configuration ID
            id_token_hint: Optional ID token hint for logout
            post_logout_redirect_uri: Optional redirect URI after logout

        Returns:
            Dict with end_session_endpoint URL or error
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "IdP configuration not found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            end_session_endpoint = discovery.get("end_session_endpoint")

            if not end_session_endpoint:
                return {"error": "End session endpoint not supported by IdP"}

            # Build logout URL
            params = {}
            if id_token_hint:
                params["id_token_hint"] = id_token_hint
            if post_logout_redirect_uri:
                params["post_logout_redirect_uri"] = post_logout_redirect_uri

            if params:
                logout_url = f"{end_session_endpoint}?{urlencode(params)}"
            else:
                logout_url = end_session_endpoint

            return {
                "end_session_endpoint": logout_url,
            }

        except Exception as e:
            current_app.logger.error(f"Failed to initiate logout: {e}")
            return {"error": str(e)}
