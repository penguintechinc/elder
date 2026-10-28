"""OpenID Connect (OIDC) Service implementation for v3.0.0.

Handles OIDC SSO authentication with IdP configurations
at both global and tenant levels using Authlib.

Hardening (regressions: oidc-global-role-carry, oidc-null-tenant,
oidc-refresh-userinfo-logout-ownership, oidc-login-csrf):

* an account holding a stored ``global_role`` can never sign in through OIDC
  (tenant-scoped or global IdP): it must use password + MFA;
* a GLOBAL IdP only serves a tenant that explicitly opted in via
  ``tenants.settings.oidc_allow_global_idp is True``, for existing users only,
  and never creates a user without a concrete target tenant;
* state/nonce/PKCE are enforced (see ``oidc_state``);
* an account is only matched/created when the ID token asserts
  ``email_verified is True`` for its ``email`` claim (regressions:
  oidc-email-unverified, oidc-email-claim-mapping);
* the redirect URI sent to an IdP must exactly match the operator's
  ``OIDC_ALLOWED_REDIRECT_URIS`` allowlist (regression: oidc-redirect-uri);
* upstream error text is never returned or logged -- only stable error codes.
"""

# flake8: noqa: E501

import datetime
import hmac
import json
from typing import Any
from urllib.parse import urlencode, urlsplit

import jwt
import requests
from authlib.integrations.requests_client import OAuth2Session
from quart import current_app

from apps.api.services.sso.idp_access import IdPCaller, IdPNotFoundError
from apps.api.services.sso.oidc_state import OIDCLoginError

# Tenant-settings key a tenant admin sets (strict boolean ``True``) to let the
# platform's GLOBAL OIDC IdP authenticate users of that tenant. Deliberately
# distinct from the SAML opt-in so enabling one protocol never enables the other.
GLOBAL_IDP_OPT_IN_KEY = "oidc_allow_global_idp"

_MAX_EMAIL_CHARS = 255
_MAX_FULL_NAME_CHARS = 255

# App-config key (env var of the same name) listing the only redirect URIs the
# server will ever send to an IdP; unset/empty fails OIDC login closed.
REDIRECT_URIS_CONFIG_KEY = "OIDC_ALLOWED_REDIRECT_URIS"


def _is_plain_http_url(uri: str) -> bool:
    """True for an absolute ``http(s)`` URL with a host and nothing smuggled in.

    Rejects userinfo, fragments, wildcards, whitespace/control characters and
    anything over 2048 characters -- used to vet allowlist entries.
    """
    if len(uri) > 2048 or "*" in uri:
        return False
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in uri):
        return False
    try:
        parts = urlsplit(uri)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    return (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and not parts.fragment
        and "@" not in parts.netloc
        and "#" not in uri
    )


class OIDCService:
    """OpenID Connect authentication service."""

    @staticmethod
    def get_idp_config(idp_id: int) -> dict[str, Any] | None:
        """Get an active OIDC IdP configuration by ID, UNSCOPED.

        Only for the unauthenticated login flow (authorize/callback), where the
        config is selected by the login state, not by a caller's tenant. Every
        authenticated route must use ``get_idp_config_scoped`` instead.

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
    def get_idp_config_scoped(idp_id: int, caller: IdPCaller) -> dict[str, Any]:
        """Get an active OIDC IdP config the caller is allowed to use.

        The caller's tenant-scoped predicate is part of the query, so another
        tenant's (or, for non-global admins, a global) config is
        indistinguishable from a missing one.

        Args:
            idp_id: IdP configuration ID
            caller: Authenticated portal user (tenant from the validated JWT)

        Returns:
            IdP configuration dict

        Raises:
            IdPNotFoundError: Missing, inactive, not OIDC, or out of scope
            IdPForbiddenError: Non-global caller without a tenant claim
        """
        db = current_app.db
        table = db.idp_configurations
        query = (
            caller.scoped_query(db)
            & (table.id == idp_id)
            & (table.idp_type == "oidc")
            & (table.is_active == True)  # noqa: E712
        )
        config = db(query).select().first()
        if config is None:
            raise IdPNotFoundError(idp_id)
        return OIDCService._config_to_dict(config)

    @staticmethod
    def get_idp_config_by_tenant(
        tenant_id: int | None = None,
    ) -> dict[str, Any] | None:
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
    def _config_to_dict(config) -> dict[str, Any]:
        """Convert IdP config record to dict (server-internal; includes the secret)."""
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
    def allowed_redirect_uris() -> frozenset[str]:
        """Return the operator's allowlist of OIDC redirect URIs (may be empty).

        Read from ``OIDC_ALLOWED_REDIRECT_URIS`` (comma separated or a JSON
        list). Entries that are not plain absolute ``http(s)`` URLs (userinfo,
        fragment, wildcard, control characters, relative) are dropped, so a
        typo can only shrink the allowlist, never widen it.

        Returns:
            The valid entries as an immutable set; empty means "not configured"
        """
        raw = current_app.config.get(REDIRECT_URIS_CONFIG_KEY)
        entries: list[Any]
        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = []
                entries = parsed if isinstance(parsed, list) else []
            else:
                entries = text.split(",")
        elif isinstance(raw, (list, tuple, set, frozenset)):
            entries = list(raw)
        else:
            entries = []
        cleaned = [e.strip() for e in entries if isinstance(e, str) and e.strip()]
        valid = frozenset(e for e in cleaned if _is_plain_http_url(e))
        if len(valid) != len(set(cleaned)):
            current_app.logger.warning(
                "oidc_redirect_uri_entries_ignored count=%s",
                len(set(cleaned)) - len(valid),
            )
        return valid

    @staticmethod
    def require_allowed_redirect_uri(uri: str) -> str:
        """Return ``uri`` iff it exactly equals an allowlisted redirect URI.

        regression: oidc-redirect-uri -- ``/oidc/authorize`` used to accept any
        ``http(s)`` URL, so an attacker could start a login with their own
        ``redirect_uri`` (holding the state/cookie/PKCE verifier) and, against an
        IdP with loose redirect registration, receive a victim's authorization
        code. Comparison is exact string equality (scheme, host, port, path and
        case all significant); nothing about ``uri`` is echoed on failure.

        Args:
            uri: The caller-supplied (or previously stored) redirect URI

        Returns:
            ``uri`` unchanged

        Raises:
            OIDCLoginError: ``oidc_redirect_uris_not_configured`` (503, fail
                closed when the operator has not set the allowlist) or
                ``oidc_redirect_uri_not_allowed`` (400)
        """
        allowed = OIDCService.allowed_redirect_uris()
        if not allowed:
            raise OIDCLoginError(
                "oidc_redirect_uris_not_configured",
                503,
                "OIDC login is unavailable: the operator has not set OIDC_ALLOWED_REDIRECT_URIS",
            )
        if uri not in allowed:
            raise OIDCLoginError(
                "oidc_redirect_uri_not_allowed",
                400,
                "redirect_uri is not an allowed callback URL",
            )
        return uri

    @staticmethod
    def resolve_login_tenant(
        idp_config: dict[str, Any], requested_tenant_id: int | None
    ) -> int:
        """Resolve the one tenant a login through ``idp_config`` will belong to.

        A tenant-scoped IdP serves exactly its own tenant. A GLOBAL IdP serves
        only a tenant that explicitly opted in (strict boolean ``True`` under
        ``GLOBAL_IDP_OPT_IN_KEY``); an unknown, inactive or non-opted-in tenant
        is one uniform 403 so the endpoint is no tenant-existence oracle. The
        result is never ``None`` -- there is no "default to tenant 1".

        Args:
            idp_config: The IdP configuration dict
            requested_tenant_id: Tenant the login asked for (required for a
                global IdP; must match for a tenant-scoped one)

        Returns:
            The concrete target tenant id

        Raises:
            OIDCLoginError: ``oidc_tenant_mismatch``/``oidc_tenant_required``
                (400) or ``oidc_global_idp_not_enabled`` (403)
        """
        config_tenant = idp_config.get("tenant_id")
        if config_tenant is not None:
            if requested_tenant_id is not None and requested_tenant_id != config_tenant:
                raise OIDCLoginError(
                    "oidc_tenant_mismatch",
                    400,
                    "This identity provider belongs to a different tenant",
                )
            return int(config_tenant)

        if (
            not isinstance(requested_tenant_id, int)
            or isinstance(requested_tenant_id, bool)
            or requested_tenant_id <= 0
        ):
            raise OIDCLoginError(
                "oidc_tenant_required",
                400,
                "tenant_id is required to sign in through the shared identity provider",
            )
        db = current_app.db
        tenant = db(db.tenants.id == requested_tenant_id).select().first()
        tenant_settings = (
            tenant.settings if tenant and isinstance(tenant.settings, dict) else {}
        )
        if (
            tenant is None
            or not tenant.is_active
            or tenant_settings.get(GLOBAL_IDP_OPT_IN_KEY) is not True
        ):
            raise OIDCLoginError(
                "oidc_global_idp_not_enabled",
                403,
                "The shared identity provider is not enabled for this tenant",
            )
        return int(tenant.id)

    @staticmethod
    def discover_configuration(issuer_url: str) -> dict[str, Any]:
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
            # Class only: the message can embed the URL and upstream details.
            current_app.logger.error("oidc_discovery_failed error=%s", type(e).__name__)
            raise

    @staticmethod
    def get_authorization_url(
        idp_config: dict[str, Any],
        redirect_uri: str,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> dict[str, Any]:
        """Generate the OIDC authorization URL for a prepared login attempt.

        Args:
            idp_config: IdP configuration dict
            redirect_uri: Callback redirect URI
            state: Server-issued, browser-bound state (see ``oidc_state``)
            nonce: Server-issued nonce to be echoed in the ID token
            code_challenge: PKCE S256 challenge for the server-held verifier

        Returns:
            Dict with authorization_url and state, or ``{"error": <code>}``
        """
        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            authorization_endpoint = discovery.get("authorization_endpoint")

            if not authorization_endpoint:
                return {"error": "oidc_provider_misconfigured"}

            # Parse scopes
            scopes = idp_config["oidc_scopes"].split()

            # Build authorization URL
            params = {
                "client_id": idp_config["oidc_client_id"],
                "redirect_uri": redirect_uri,
                "response_type": idp_config["oidc_response_type"],
                "scope": " ".join(scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }

            authorization_url = f"{authorization_endpoint}?{urlencode(params)}"

            return {
                "authorization_url": authorization_url,
                "state": state,
            }

        except Exception as e:
            current_app.logger.error(
                "oidc_authorization_url_failed error=%s", type(e).__name__
            )
            return {"error": "oidc_provider_unavailable"}

    @staticmethod
    def exchange_code_for_tokens(
        idp_id: int, code: str, redirect_uri: str, *, code_verifier: str
    ) -> dict[str, Any]:
        """Exchange authorization code for tokens (PKCE verifier included).

        Args:
            idp_id: IdP configuration ID
            code: Authorization code from IdP
            redirect_uri: Redirect URI used in authorization
            code_verifier: PKCE verifier recorded when the login began

        Returns:
            Dict with access_token, id_token, refresh_token, etc., or
            ``{"error": <code>}``
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "oidc_idp_not_found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            token_endpoint = discovery.get("token_endpoint")

            if not token_endpoint:
                return {"error": "oidc_provider_misconfigured"}

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
                code_verifier=code_verifier,
            )

            return {
                "access_token": token.get("access_token"),
                "id_token": token.get("id_token"),
                "refresh_token": token.get("refresh_token"),
                "token_type": token.get("token_type", "Bearer"),
                "expires_in": token.get("expires_in"),
            }

        except Exception as e:
            current_app.logger.error(
                "oidc_token_exchange_failed idp_id=%s error=%s",
                idp_id,
                type(e).__name__,
            )
            return {"error": "oidc_token_exchange_failed"}

    @staticmethod
    def validate_id_token(
        idp_id: int, id_token: str, *, expected_nonce: str
    ) -> dict[str, Any]:
        """Validate and decode OIDC ID token, including the login nonce.

        ``expected_nonce`` is required (no default) so a caller can never
        validate an ID token without binding it to the login that asked for it.

        Args:
            idp_id: IdP configuration ID
            id_token: JWT ID token from IdP
            expected_nonce: Nonce recorded when the login began

        Returns:
            Decoded token claims or ``{"error": <code>}``
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "oidc_idp_not_found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            jwks_uri = discovery.get("jwks_uri")

            if not jwks_uri:
                return {"error": "oidc_provider_misconfigured"}

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
                return {"error": "oidc_signing_key_not_found"}

            # Validate and decode ID token (exp/iss/aud/sub are mandatory)
            claims = jwt.decode(
                id_token,
                signing_key,
                algorithms=["RS256"],
                audience=idp_config["oidc_client_id"],
                issuer=idp_config["oidc_issuer_url"],
                options={
                    "verify_exp": True,
                    "require": ["exp", "iss", "aud", "sub"],
                },
            )

            token_nonce = claims.get("nonce")
            if not isinstance(token_nonce, str) or not hmac.compare_digest(
                token_nonce.encode("utf-8"), expected_nonce.encode("utf-8")
            ):
                return {"error": "oidc_nonce_mismatch"}

            return claims

        except jwt.ExpiredSignatureError:
            return {"error": "oidc_id_token_expired"}
        except jwt.InvalidTokenError:
            return {"error": "oidc_id_token_invalid"}
        except Exception as e:
            current_app.logger.error(
                "oidc_id_token_validation_failed idp_id=%s error=%s",
                idp_id,
                type(e).__name__,
            )
            return {"error": "oidc_id_token_invalid"}

    @staticmethod
    def get_userinfo(idp_id: int, access_token: str) -> dict[str, Any]:
        """Get user information from userinfo endpoint.

        Callers outside the unauthenticated login flow must first pass the
        config through ``get_idp_config_scoped``.

        Args:
            idp_id: IdP configuration ID
            access_token: Access token from IdP

        Returns:
            User info claims or ``{"error": <code>}``
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "oidc_idp_not_found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            userinfo_endpoint = discovery.get("userinfo_endpoint")

            if not userinfo_endpoint:
                return {"error": "oidc_provider_misconfigured"}

            # Call userinfo endpoint
            headers = {"Authorization": f"Bearer {access_token}"}
            response = requests.get(userinfo_endpoint, headers=headers, timeout=10)
            response.raise_for_status()

            return response.json()

        except Exception as e:
            current_app.logger.error(
                "oidc_userinfo_failed idp_id=%s error=%s", idp_id, type(e).__name__
            )
            return {"error": "oidc_userinfo_failed"}

    @staticmethod
    def refresh_tokens(idp_id: int, refresh_token: str) -> dict[str, Any]:
        """Refresh access token using refresh token.

        Callers must first pass the config through ``get_idp_config_scoped``:
        this call authenticates to the IdP with the config's client secret.

        Args:
            idp_id: IdP configuration ID
            refresh_token: Refresh token from IdP

        Returns:
            New tokens or ``{"error": <code>}``
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "oidc_idp_not_found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            token_endpoint = discovery.get("token_endpoint")

            if not token_endpoint:
                return {"error": "oidc_provider_misconfigured"}

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
            current_app.logger.error(
                "oidc_refresh_failed idp_id=%s error=%s", idp_id, type(e).__name__
            )
            return {"error": "oidc_refresh_failed"}

    @staticmethod
    def jit_provision_user(
        tenant_id: int,
        idp_config: dict[str, Any],
        id_token_claims: dict[str, Any],
        userinfo: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve (and, where allowed, just-in-time create) the user for an OIDC login.

        An account holding a stored ``global_role`` can NEVER sign in through
        OIDC -- on a tenant-scoped IdP (controlled by that tenant's admins) or
        on the global one: such accounts use password + MFA. (Stripping the role
        from the session is not enough: ``/portal-auth/refresh`` re-reads it.)
        The returned session user therefore always has ``global_role`` ``None``.
        A GLOBAL IdP (the shared fallback) serves existing users only and never
        creates one. A user is never created without a concrete positive
        ``tenant_id``.

        The account is matched on the ``email`` claim of the validated ID token
        (any other ``attribute_mappings.email`` is refused: ``email_verified``
        only vouches for ``email``), and only if that token carries
        ``email_verified`` as the strict boolean ``True`` (userinfo can supply a
        display name but never the email or the verified flag). There is no
        per-IdP opt-out yet -- see CHANGELOG.

        Args:
            tenant_id: Concrete target tenant (see ``resolve_login_tenant``)
            idp_config: IdP configuration
            id_token_claims: Validated claims from the ID token
            userinfo: Optional additional userinfo claims (name only)

        Returns:
            Existing or newly created user dict (no ``global_role``)

        Raises:
            OIDCLoginError: tenant missing/mismatched, email claim unusable or
                unverified, account deactivated, platform-privileged account on
                a tenant IdP, or user not provisioned (all 4xx, stable codes)
        """
        if (
            not isinstance(tenant_id, int)
            or isinstance(tenant_id, bool)
            or tenant_id <= 0
        ):
            raise OIDCLoginError(
                "oidc_tenant_required", 400, "A target tenant is required"
            )
        config_tenant = idp_config.get("tenant_id")
        if config_tenant is not None and config_tenant != tenant_id:
            raise OIDCLoginError(
                "oidc_tenant_mismatch",
                400,
                "This identity provider belongs to a different tenant",
            )
        is_global_idp = config_tenant is None

        db = current_app.db

        # Get attribute mappings (default OIDC standard claims)
        mappings = idp_config.get("attribute_mappings") or {}
        email_claim = mappings.get("email", "email")
        name_claim = mappings.get("full_name", "name")

        # regression: oidc-email-claim-mapping. ``email_verified`` vouches for
        # the standard ``email`` claim and nothing else; matching on a mapped
        # claim (preferred_username, upn, ...) would let an attacker with a
        # verified email of their own choose the claim that is matched. Until a
        # verified-claim mapping exists, any other mapping is refused.
        if email_claim != "email":
            raise OIDCLoginError(
                "oidc_email_claim_mapping_unsupported",
                403,
                "This identity provider's email attribute mapping is not supported for OIDC login",
            )

        # Identity (the email an account is matched on) comes ONLY from the
        # signature-validated ID token, never from userinfo: ``email_verified``
        # below vouches for the ID token's own email, so letting a userinfo
        # response override either would break that pairing.
        email = id_token_claims.get(email_claim)
        if (
            not isinstance(email, str)
            or not email.strip()
            or len(email.strip()) > _MAX_EMAIL_CHARS
        ):
            raise OIDCLoginError(
                "oidc_email_claim_missing",
                400,
                "A usable email claim was not found in the OIDC response",
            )
        email = email.strip().lower()

        # regression: oidc-email-unverified -- the IdP must assert it verified
        # this address (strict boolean ``True`` in the ID token). Without it a
        # tenant admin, or any IdP that lets users self-register an unverified
        # email, could assert a victim's address and take over that account
        # (existing-user match) or mint a "verified" one (JIT). Checked before
        # any account lookup so denial reveals nothing about which accounts exist.
        email_verified = id_token_claims.get("email_verified") is True
        if not email_verified:
            raise OIDCLoginError(
                "oidc_email_not_verified",
                403,
                "The identity provider did not verify this email address",
            )

        # Non-identity attributes may be enriched from userinfo (it wins).
        claims = {**id_token_claims, **(userinfo or {})}
        full_name = claims.get(name_claim)
        full_name = (
            full_name.strip()[:_MAX_FULL_NAME_CHARS]
            if isinstance(full_name, str) and full_name.strip()
            else None
        )

        # Check if user exists
        existing = (
            db(
                (db.portal_users.email == email)
                & (db.portal_users.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if existing:
            if not existing.is_active:
                raise OIDCLoginError(
                    "oidc_account_deactivated", 403, "Account is deactivated"
                )
            if existing.global_role:
                # regression: oidc-global-role-carry. A platform-privileged
                # account never signs in through OIDC at all: a tenant-scoped
                # IdP is controlled by that tenant's admins, and a stripped
                # session would just get its global_role back on
                # /portal-auth/refresh (which re-reads the stored role). These
                # accounts use password + MFA.
                if is_global_idp:
                    raise OIDCLoginError(
                        "oidc_global_role_account_forbidden",
                        403,
                        "Platform-privileged accounts must sign in with a password and MFA, not OIDC",
                    )
                raise OIDCLoginError(
                    "oidc_tenant_idp_global_role_forbidden",
                    403,
                    "Platform-privileged accounts cannot sign in through a tenant-scoped identity provider",
                )
            # Update last login
            db(db.portal_users.id == existing.id).update(
                last_login_at=datetime.datetime.now(datetime.UTC)
            )
            db.commit()
            return {
                "id": existing.id,
                "email": existing.email,
                "tenant_id": tenant_id,
                "tenant_role": existing.tenant_role,
                "global_role": None,
            }

        if is_global_idp or not idp_config.get("jit_provisioning_enabled"):
            raise OIDCLoginError(
                "oidc_user_not_provisioned",
                403,
                "No account exists for this identity and automatic provisioning is not available",
            )

        # Create new user (no password for SSO users)
        default_role = idp_config.get("default_role") or "reader"
        user_id = db.portal_users.insert(
            tenant_id=tenant_id,
            email=email,
            password_hash=None,  # SSO users don't have passwords
            full_name=full_name,
            tenant_role=default_role,
            is_active=True,
            email_verified=email_verified,  # the IdP's own verified claim
            last_login_at=datetime.datetime.now(datetime.UTC),
        )
        db.commit()

        return {
            "id": user_id,
            "email": email,
            "tenant_id": tenant_id,
            "tenant_role": default_role,
            "global_role": None,
            "jit_provisioned": True,
        }

    @staticmethod
    def logout(
        idp_id: int,
        id_token_hint: str | None = None,
        post_logout_redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """Initiate OIDC logout (RP-Initiated Logout).

        Callers must first pass the config through ``get_idp_config_scoped``.

        Args:
            idp_id: IdP configuration ID
            id_token_hint: Optional ID token hint for logout
            post_logout_redirect_uri: Optional redirect URI after logout

        Returns:
            Dict with end_session_endpoint URL or ``{"error": <code>}``
        """
        idp_config = OIDCService.get_idp_config(idp_id)
        if not idp_config:
            return {"error": "oidc_idp_not_found"}

        try:
            # Discover provider configuration
            discovery = OIDCService.discover_configuration(
                idp_config["oidc_issuer_url"]
            )
            end_session_endpoint = discovery.get("end_session_endpoint")

            if not end_session_endpoint:
                return {"error": "oidc_end_session_unsupported"}

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
            current_app.logger.error(
                "oidc_logout_failed idp_id=%s error=%s", idp_id, type(e).__name__
            )
            return {"error": "oidc_logout_failed"}
