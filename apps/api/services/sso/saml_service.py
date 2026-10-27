"""SAML 2.0 Service Provider implementation.

Handles SAML SSO authentication with IdP configurations
at both global and tenant levels.

Security model (SP-initiated Web Browser SSO, HTTP-POST ACS binding):

* ``begin_login`` builds a real AuthnRequest, and stores its ID server-side
  (Redis, short TTL, single use) under a random token that is handed to the
  browser as an ACS-path-scoped cookie. The token is also what ties the
  browser that *started* the login to the browser that *finishes* it.
* ``process_saml_response`` consumes that state, then validates the posted
  Response with python3-saml (strict mode) **plus** a second, independent set
  of exact-match checks (audience, Destination, Recipient, InResponseTo,
  validity window) because the library's Destination/Recipient comparisons
  are prefix/substring based -- unsafe with numeric tenant ids in the ACS
  path (``/acs/1`` is a prefix of ``/acs/12``). The signature is verified
  against the IdP certificate stored in the SSO provider config; embedded
  KeyInfo certificates are never trusted. The assertion must itself be
  signed (Response-only signatures are rejected), which also narrows the XML
  signature-wrapping surface. Replayed assertion ids are rejected via an
  atomic Redis ``SET NX EX`` whose TTL covers the assertion's validity.
* The SP identity (entityId / ACS URL) comes from server configuration
  (``SAML_SP_BASE_URL`` / ``SITE_URL``), never from the request Host header,
  so a forged Host cannot retarget a response issued for another SP.
"""

# flake8: noqa: E501

import base64
import binascii
import datetime
import hashlib
import html
import json
import math
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import structlog
from cryptography import x509
from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.constants import OneLogin_Saml2_Constants
from onelogin.saml2.errors import OneLogin_Saml2_Error, OneLogin_Saml2_ValidationError
from onelogin.saml2.response import OneLogin_Saml2_Response
from onelogin.saml2.settings import OneLogin_Saml2_Settings
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from quart import current_app
from redis.exceptions import RedisError

from apps.api.common.licensing.enforce import check_limit
from apps.api.models.schemas import validate_email_with_local
from apps.api.utils.async_utils import run_in_threadpool
from shared.observability import get_meter, get_tracer

logger = structlog.get_logger()
_meter = get_meter("elder-api.sso.saml")
_tracer = get_tracer("elder-api.sso.saml")
_acs_counter = _meter.create_counter(
    "elder.saml.acs.requests",
    unit="1",
    description="SAML ACS requests by outcome (authenticated or a rejection code).",
)
_acs_duration = _meter.create_histogram(
    "elder.saml.acs.duration",
    unit="ms",
    description="SAML ACS end-to-end processing time.",
)

# Allowed IdP/SP clock skew. Stricter than python3-saml's 300s default; the
# library's looser check still runs, ours is the binding one.
_CLOCK_SKEW_SECONDS = 120
# AuthnRequest state lives only as long as a user plausibly needs at the IdP.
_AUTHN_REQUEST_TTL_SECONDS = 600
# An assertion claiming to stay valid longer than this is rejected outright so
# the replay-cache TTL can never be shorter than the assertion's usable life.
_MAX_ASSERTION_LIFETIME_SECONDS = 86400
_REPLAY_TTL_FLOOR_SECONDS = 60
# Hard cap on the posted (base64) SAMLResponse; real responses are tens of KB.
_MAX_SAML_RESPONSE_B64_CHARS = 1_048_576
_MAX_NAME_ID_CHARS = 1024
_MAX_ASSERTION_ID_CHARS = 256
_MAX_EMAIL_CHARS = 255
_MAX_FULL_NAME_CHARS = 255
_STATE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_STATE_KEY_PREFIX = "elder:saml:authnreq"
_REPLAY_KEY_PREFIX = "elder:saml:assertion"
_VALID_TENANT_ROLES = ("reader", "maintainer", "admin")  # ascending privilege
_RETURN_TO_RE = re.compile(r"^/(?!/)[^\\\r\n\x00]{0,2047}$")

# python3-saml ValidationError.code -> (stable client-facing error code, HTTP status),
# declared as outcome -> codes so each client-facing code is stated once.
_E = OneLogin_Saml2_ValidationError
_VALIDATION_OUTCOMES: dict[tuple[str, int], tuple[int, ...]] = {
    ("saml_unsigned", 401): (
        _E.NO_SIGNATURE_FOUND,
        _E.NO_SIGNED_ASSERTION,
        _E.NO_SIGNED_MESSAGE,
    ),
    ("saml_signature_invalid", 401): (
        _E.INVALID_SIGNATURE,
        _E.WRONG_SIGNED_ELEMENT,
        _E.INVALID_SIGNED_ELEMENT,
        _E.ID_NOT_FOUND_IN_SIGNED_ELEMENT,
        _E.DUPLICATED_ID_IN_SIGNED_ELEMENTS,
        _E.DUPLICATED_REFERENCE_IN_SIGNED_ELEMENTS,
        _E.UNEXPECTED_SIGNED_ELEMENTS,
        _E.WRONG_NUMBER_OF_SIGNATURES_IN_RESPONSE,
        _E.WRONG_NUMBER_OF_SIGNATURES_IN_ASSERTION,
        _E.DEPRECATED_SIGNATURE_METHOD,
        _E.DEPRECATED_DIGEST_METHOD,
    ),
    ("saml_assertion_structure_invalid", 401): (
        _E.WRONG_NUMBER_OF_ASSERTIONS,
        _E.MISSING_CONDITIONS,
        _E.WRONG_NUMBER_OF_AUTHSTATEMENTS,
        _E.NO_NAMEID,
        _E.EMPTY_NAMEID,
        _E.SP_NAME_QUALIFIER_NAME_MISMATCH,
        _E.DUPLICATED_ATTRIBUTE_NAME_FOUND,
        _E.ENCRYPTED_ATTRIBUTES,
        _E.NO_ENCRYPTED_ASSERTION,
        _E.NO_ENCRYPTED_NAMEID,
    ),
    ("saml_response_malformed", 400): (
        _E.INVALID_XML_FORMAT,
        _E.UNSUPPORTED_SAML_VERSION,
        _E.MISSING_ID,
    ),
    ("saml_assertion_not_yet_valid", 401): (_E.ASSERTION_TOO_EARLY,),
    ("saml_assertion_expired", 401): (
        _E.ASSERTION_EXPIRED,
        _E.SESSION_EXPIRED,
        _E.RESPONSE_EXPIRED,
    ),
    ("saml_audience_mismatch", 401): (_E.WRONG_AUDIENCE,),
    ("saml_destination_mismatch", 401): (_E.WRONG_DESTINATION, _E.EMPTY_DESTINATION),
    ("saml_in_response_to_mismatch", 401): (_E.WRONG_INRESPONSETO,),
    ("saml_subject_confirmation_invalid", 401): (_E.WRONG_SUBJECTCONFIRMATION,),
    ("saml_issuer_mismatch", 401): (
        _E.WRONG_ISSUER,
        _E.ISSUER_MULTIPLE_IN_RESPONSE,
        _E.ISSUER_NOT_FOUND_IN_ASSERTION,
    ),
    ("saml_idp_error_status", 401): (_E.STATUS_CODE_IS_NOT_SUCCESS,),
}
_VALIDATION_CODE_MAP: dict[int, tuple[str, int]] = {
    code: outcome for outcome, codes in _VALIDATION_OUTCOMES.items() for code in codes
}


class SAMLError(Exception):
    """A SAML flow failure carrying a stable error code and HTTP status.

    Raised by every validation/configuration/state failure so the ACS and
    login endpoints can fail loudly with a specific 4xx/5xx instead of ever
    falling back to a permissive path. The ``message`` is generic on purpose:
    it never echoes assertion contents.
    """

    def __init__(self, code: str, status_code: int, message: str) -> None:
        """Create the error.

        Args:
            code: Stable machine-readable error code (e.g. ``saml_unsigned``)
            status_code: HTTP status the endpoint should answer with
            message: Generic, content-free human-readable explanation
        """
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.message = message

    def as_dict(self) -> dict:
        """Return the service-level error dict (``error`` + ``status_code``)."""
        return {
            "error": self.code,
            "message": self.message,
            "status_code": self.status_code,
        }


@dataclass(slots=True, frozen=True)
class ValidatedAssertion:
    """The fully validated content of a SAML assertion.

    Only ever constructed after signature, audience, destination, recipient,
    InResponseTo and validity-window checks all passed, so downstream code can
    treat every field as IdP-attested.
    """

    name_id: str
    attributes: dict[str, list[str]]
    session_index: str | None
    assertion_id: str
    in_response_to: str
    not_after: float = field(default=0.0)


def _db() -> Any:
    """Return the app's penguin-dal handle.

    ``current_app`` is typed as a plain Quart app, which has no ``db``; this
    accessor keeps the new code type-clean (the handle is attached at startup).
    """
    return getattr(current_app, "db")


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
    def sp_base_url() -> str:
        """Return the externally visible base URL of this Service Provider.

        Read from ``SAML_SP_BASE_URL`` (falling back to ``SITE_URL``), never
        from the request Host header, so entityId/ACS/Destination/Recipient
        expectations cannot be steered by a forged Host. Outside DEBUG/TESTING
        the URL must be https.

        Returns:
            Base URL without a trailing slash

        Raises:
            SAMLError: 503 when the configured value is missing or unusable
        """
        raw = (
            current_app.config.get("SAML_SP_BASE_URL")
            or current_app.config.get("SITE_URL")
            or ""
        ).strip()
        raw = raw.rstrip("/")
        parts = urlsplit(raw)
        insecure_ok = bool(
            current_app.config.get("DEBUG") or current_app.config.get("TESTING")
        )
        allowed_schemes = ("http", "https") if insecure_ok else ("https",)
        if (
            parts.scheme not in allowed_schemes
            or not parts.hostname
            or parts.query
            or parts.fragment
            or parts.username
            or parts.password
        ):
            raise SAMLError(
                "saml_sp_base_url_invalid",
                503,
                "SAML is unavailable: the service provider base URL is not configured correctly",
            )
        return raw

    @staticmethod
    def sp_urls(base_url: str, tenant_id: int | None = None) -> tuple[str, str, str]:
        """Return the SP ``(entity_id, acs_url, slo_url)`` for a tenant.

        Args:
            base_url: Base URL of the Elder instance (no trailing slash)
            tenant_id: Tenant ID, or None for the global (tenant-less) endpoints

        Returns:
            Tuple of entityId, Assertion Consumer Service URL, SLO URL
        """
        suffix = f"/{tenant_id}" if tenant_id else ""
        return (
            f"{base_url}/saml/metadata{suffix}",
            f"{base_url}/api/v1/sso/saml/acs{suffix}",
            f"{base_url}/api/v1/sso/saml/slo{suffix}",
        )

    @staticmethod
    def _build_saml_settings(tenant_id: int, idp_config: dict) -> tuple[dict, str, str]:
        """Build python3-saml settings from a stored IdP configuration.

        Args:
            tenant_id: Tenant ID used to derive the SP entityId/ACS/SLO URLs
            idp_config: IdP configuration dict (from ``get_idp_config``)

        Returns:
            ``(settings, sp_entity_id, acs_url)`` -- the settings dict is
            strict-mode and requires a signed Assertion; ``acs_url`` is the
            exact URL a response must be addressed to.
        """
        entity_id, acs_url, slo_url = SAMLService.sp_urls(
            SAMLService.sp_base_url(), tenant_id
        )
        idp: dict[str, Any] = {
            "entityId": idp_config.get("entity_id") or "",
            "singleSignOnService": {
                "url": idp_config.get("sso_url") or "",
                "binding": OneLogin_Saml2_Constants.BINDING_HTTP_REDIRECT,
            },
            "x509cert": idp_config.get("certificate") or "",
        }
        if idp_config.get("slo_url"):
            idp["singleLogoutService"] = {
                "url": idp_config["slo_url"],
                "binding": OneLogin_Saml2_Constants.BINDING_HTTP_REDIRECT,
            }
        settings = {
            # strict=True enforces schema, audience, destination, conditions,
            # InResponseTo and signature checks -- never relax for production.
            "strict": True,
            "debug": False,
            "sp": {
                "entityId": entity_id,
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
            "idp": idp,
            "security": {
                "authnRequestsSigned": False,
                "wantAssertionsSigned": True,
                "wantMessagesSigned": False,
                "wantAssertionsEncrypted": False,
                "wantNameId": True,
                "wantAttributeStatement": False,
                "requestedAuthnContext": False,
                "rejectDeprecatedAlgorithm": True,
                "allowSingleLabelDomains": True,
            },
        }
        return settings, entity_id, acs_url

    @staticmethod
    def _request_data(acs_url: str) -> dict:
        """Build the python3-saml request-data dict from the *configured* ACS URL.

        python3-saml derives "the URL this response was received at" from this
        dict; sourcing it from configuration rather than the live request keeps
        the Destination check independent of Host / X-Forwarded-* headers.
        """
        parts = urlsplit(acs_url)
        return {
            "https": "on" if parts.scheme == "https" else "off",
            "http_host": parts.netloc,
            "script_name": parts.path,
            "get_data": {},
            "post_data": {},
        }

    @staticmethod
    def _state_store() -> Any:
        """Return the shared Redis client used for AuthnRequest + replay state.

        Raises:
            SAMLError: 503 if Redis is not available -- SAML replay/InResponseTo
                protection must never silently degrade, so there is no fallback.
        """
        client = current_app.extensions.get("module_redis")
        if client is None:
            raise SAMLError(
                "saml_state_store_unavailable",
                503,
                "SAML is temporarily unavailable",
            )
        return client

    @staticmethod
    def _state_key(tenant_id: int, token: str) -> str:
        """Redis key for a login-state token (the raw token is never stored)."""
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"{_STATE_KEY_PREFIX}:{tenant_id}:{digest}"

    @staticmethod
    def _load_saml_idp(tenant_id: int) -> dict:
        """Load and sanity-check the SAML IdP config that governs a tenant.

        Args:
            tenant_id: Tenant context (from the ACS URL path)

        Returns:
            IdP configuration dict, guaranteed SAML with entity id and a
            parseable signing certificate

        Raises:
            SAMLError: 404 unknown/inactive tenant or no IdP; 409 IdP incomplete
        """
        db = _db()
        tenant = db(db.tenants.id == tenant_id).select().first()
        if not tenant or not tenant.is_active:
            raise SAMLError("saml_tenant_not_found", 404, "Tenant not found")

        idp_config = SAMLService.get_idp_config(tenant_id)
        if not idp_config:
            raise SAMLError(
                "saml_idp_not_configured", 404, "No IdP configured for this tenant"
            )
        if idp_config.get("idp_type") != "saml":
            raise SAMLError(
                "saml_idp_misconfigured", 409, "Configured IdP is not a SAML provider"
            )
        if not idp_config.get("entity_id"):
            raise SAMLError(
                "saml_idp_misconfigured", 409, "IdP entity id is not configured"
            )
        cert = idp_config.get("certificate")
        if not cert:
            raise SAMLError(
                "saml_idp_misconfigured", 409, "IdP signing certificate not configured"
            )
        try:
            x509.load_pem_x509_certificate(
                OneLogin_Saml2_Utils.format_cert(cert).encode("ascii")
            )
        except (ValueError, UnicodeEncodeError) as exc:
            raise SAMLError(
                "saml_idp_misconfigured",
                409,
                "IdP signing certificate is not a valid X.509 certificate",
            ) from exc
        return idp_config

    @staticmethod
    def begin_login(tenant_id: int, return_to: str | None = None) -> dict:
        """Start SP-initiated SSO: build a real AuthnRequest and record its ID.

        The AuthnRequest ID is stored server-side (single use, short TTL) under
        a random token returned to the caller, which the endpoint must hand to
        the browser as the ACS-scoped state cookie. The ACS later requires that
        token and enforces InResponseTo against the stored ID.

        Args:
            tenant_id: Tenant to log in to
            return_to: Optional same-origin relative path to echo after login

        Returns:
            ``{"sso_url": <IdP redirect URL>, "state_token": <cookie value>,
            "expires_in": <seconds>}``

        Raises:
            SAMLError: on bad return_to, missing/invalid IdP or SP config, or
                an unavailable state store
        """
        if return_to is not None and not _RETURN_TO_RE.match(return_to):
            raise SAMLError(
                "saml_return_to_invalid",
                400,
                "return_to must be a same-origin relative path",
            )
        idp_config = SAMLService._load_saml_idp(tenant_id)
        if not idp_config.get("sso_url"):
            raise SAMLError("saml_idp_misconfigured", 409, "IdP SSO URL not configured")

        settings, _entity_id, acs_url = SAMLService._build_saml_settings(
            tenant_id, idp_config
        )
        try:
            auth = OneLogin_Saml2_Auth(
                SAMLService._request_data(acs_url), old_settings=settings
            )
            redirect_url = auth.login()
            request_id = auth.get_last_request_id()
        except OneLogin_Saml2_Error as exc:
            raise SAMLError(
                "saml_idp_misconfigured", 409, "IdP configuration is invalid"
            ) from exc

        token = secrets.token_urlsafe(32)
        client = SAMLService._state_store()
        try:
            client.set(
                SAMLService._state_key(tenant_id, token),
                json.dumps({"request_id": request_id, "return_to": return_to}),
                ex=_AUTHN_REQUEST_TTL_SECONDS,
            )
        except RedisError as exc:
            raise SAMLError(
                "saml_state_store_unavailable", 503, "SAML is temporarily unavailable"
            ) from exc
        return {
            "sso_url": redirect_url,
            "state_token": token,
            "expires_in": _AUTHN_REQUEST_TTL_SECONDS,
        }

    @staticmethod
    def _consume_login_state(tenant_id: int, token: str | None) -> dict:
        """Atomically fetch-and-delete the stored AuthnRequest state for a token.

        Single use: a second ACS POST with the same cookie finds nothing. The
        key is tenant-scoped, so a login started for tenant A cannot be
        completed at tenant B's ACS.

        Raises:
            SAMLError: 400 when the token is absent/malformed/unknown/expired
                (this also rejects unsolicited, IdP-initiated responses)
        """
        missing = SAMLError(
            "saml_login_state_missing",
            400,
            "SAML login state is missing or expired; start the login again",
        )
        if not token or not _STATE_TOKEN_RE.match(token):
            raise missing
        client = SAMLService._state_store()
        key = SAMLService._state_key(tenant_id, token)
        try:
            pipe = client.pipeline(transaction=True)
            pipe.get(key)
            pipe.delete(key)
            raw, _deleted = pipe.execute()
        except RedisError as exc:
            raise SAMLError(
                "saml_state_store_unavailable", 503, "SAML is temporarily unavailable"
            ) from exc
        if not raw:
            raise missing
        try:
            state = json.loads(raw)
            request_id = state["request_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise missing from exc
        if not isinstance(request_id, str) or not request_id:
            raise missing
        return {"request_id": request_id, "return_to": state.get("return_to")}

    @staticmethod
    def _decode_and_screen(saml_response: str) -> str:
        """Size-limit, strictly base64-decode and DTD/entity-screen a SAMLResponse.

        Defence in depth ahead of python3-saml's own hardened parser: bounds
        the input, then parses the decoded XML with defusedxml forbidding DTDs,
        entity declarations (billion-laughs/XXE) and external references.

        Returns:
            The whitespace-stripped base64 text (what python3-saml consumes)

        Raises:
            SAMLError: 400 malformed, 413 oversized
        """
        malformed = SAMLError(
            "saml_response_malformed", 400, "SAMLResponse is not a valid SAML message"
        )
        if not isinstance(saml_response, str) or not saml_response:
            raise malformed
        if len(saml_response) > _MAX_SAML_RESPONSE_B64_CHARS:
            raise SAMLError(
                "saml_response_too_large", 413, "SAMLResponse exceeds the size limit"
            )
        # Some IdPs MIME-wrap the base64 with newlines.
        cleaned = "".join(saml_response.split())
        try:
            raw = base64.b64decode(cleaned, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise malformed from exc
        try:
            SafeElementTree.fromstring(
                raw, forbid_dtd=True, forbid_entities=True, forbid_external=True
            )
        except (DefusedXmlException, SafeElementTree.ParseError) as exc:
            raise malformed from exc
        return cleaned

    @staticmethod
    def _parse_time(value: str | None) -> float:
        """Parse a SAML xs:dateTime to epoch seconds (400 on garbage)."""
        try:
            return float(OneLogin_Saml2_Utils.parse_SAML_to_time(value))
        except (ValueError, TypeError) as exc:
            raise SAMLError(
                "saml_response_malformed",
                400,
                "SAMLResponse is not a valid SAML message",
            ) from exc

    @staticmethod
    def _enforce_strict_bindings(
        response: OneLogin_Saml2_Response,
        *,
        sp_entity_id: str,
        acs_url: str,
        expected_request_id: str,
        now: float,
    ) -> float:
        """Run the exact-match checks python3-saml does only loosely or optionally.

        Must be called only after ``is_valid`` succeeded (signature verified),
        so the XML it reads is the signed assertion. Enforces: an Audience that
        exactly contains our entityId (a missing AudienceRestriction is
        rejected); an exact Destination when present; an exact Response
        InResponseTo (a missing one -- an unsolicited response -- is rejected);
        a bearer SubjectConfirmationData with an exact Recipient and a
        NotOnOrAfter; and a bounded Conditions window with a 120s skew allowance
        (tighter than python3-saml's 300s).

        Returns:
            The latest NotOnOrAfter (epoch) of the assertion, for replay-TTL sizing
        """
        document = response.document
        ns = OneLogin_Saml2_Constants.NSMAP

        if sp_entity_id not in response.get_audiences():
            raise SAMLError(
                "saml_audience_mismatch",
                401,
                "The assertion is not addressed to this service",
            )

        destination = document.get("Destination")
        if destination is not None and destination != acs_url:
            raise SAMLError(
                "saml_destination_mismatch",
                401,
                "The response was not addressed to this endpoint",
            )

        if response.get_in_response_to() != expected_request_id:
            raise SAMLError(
                "saml_in_response_to_mismatch",
                401,
                "The response does not answer this login request",
            )

        conditions = document.xpath(
            "/samlp:Response/saml:Assertion/saml:Conditions", namespaces=ns
        )
        if len(conditions) != 1 or not conditions[0].get("NotOnOrAfter"):
            raise SAMLError(
                "saml_assertion_unbounded",
                401,
                "The assertion has no bounded validity window",
            )
        cond_not_on_or_after = SAMLService._parse_time(
            conditions[0].get("NotOnOrAfter")
        )
        cond_not_before = conditions[0].get("NotBefore")
        if cond_not_on_or_after + _CLOCK_SKEW_SECONDS <= now:
            raise SAMLError("saml_assertion_expired", 401, "The assertion has expired")
        if (
            cond_not_before
            and SAMLService._parse_time(cond_not_before) - _CLOCK_SKEW_SECONDS > now
        ):
            raise SAMLError(
                "saml_assertion_not_yet_valid", 401, "The assertion is not yet valid"
            )

        scd_xpath = (
            "/samlp:Response/saml:Assertion/saml:Subject/"
            f"saml:SubjectConfirmation[@Method='{OneLogin_Saml2_Constants.CM_BEARER}']/"
            "saml:SubjectConfirmationData"
        )
        # python3-saml already enforces each bearer confirmation's InResponseTo
        # and (zero-skew) NotBefore/NotOnOrAfter; what it does NOT enforce is an
        # exact Recipient (substring match) or that NotOnOrAfter exists at all.
        recipient_seen = False
        scd_not_on_or_after: float | None = None
        for scd in document.xpath(scd_xpath, namespaces=ns):
            if scd.get("Recipient") != acs_url:
                continue
            recipient_seen = True
            if scd.get("NotOnOrAfter"):
                scd_not_on_or_after = SAMLService._parse_time(scd.get("NotOnOrAfter"))
                break
        if scd_not_on_or_after is None:
            if recipient_seen:
                raise SAMLError(
                    "saml_assertion_unbounded",
                    401,
                    "The assertion has no bounded validity window",
                )
            raise SAMLError(
                "saml_recipient_mismatch",
                401,
                "The assertion was not issued for this endpoint",
            )

        not_after = max(cond_not_on_or_after, scd_not_on_or_after)
        if not_after + _CLOCK_SKEW_SECONDS - now > _MAX_ASSERTION_LIFETIME_SECONDS:
            raise SAMLError(
                "saml_assertion_lifetime_invalid",
                401,
                "The assertion's validity window is unreasonably long",
            )
        return not_after

    @staticmethod
    def _validate_response(
        tenant_id: int,
        idp_config: dict,
        saml_response: str,
        expected_request_id: str,
    ) -> ValidatedAssertion:
        """Cryptographically and semantically validate a posted SAMLResponse.

        Args:
            tenant_id: Tenant whose ACS received the response
            idp_config: The IdP config governing this tenant (cert, entity id)
            saml_response: Base64 SAMLResponse from the browser POST
            expected_request_id: ID of the AuthnRequest this login started

        Returns:
            The validated assertion content

        Raises:
            SAMLError: with a specific code/status for every rejection
        """
        cleaned = SAMLService._decode_and_screen(saml_response)
        settings_dict, sp_entity_id, acs_url = SAMLService._build_saml_settings(
            tenant_id, idp_config
        )
        try:
            settings = OneLogin_Saml2_Settings(settings_dict, sp_validation_only=True)
        except OneLogin_Saml2_Error as exc:
            raise SAMLError(
                "saml_sp_misconfigured",
                503,
                "SAML is unavailable: the service provider configuration is invalid",
            ) from exc

        try:
            response = OneLogin_Saml2_Response(settings, cleaned)
        except Exception as exc:  # hostile input: parse/decrypt failures
            raise SAMLError(
                "saml_response_malformed",
                400,
                "SAMLResponse is not a valid SAML message",
            ) from exc

        try:
            response.is_valid(
                SAMLService._request_data(acs_url),
                request_id=expected_request_id,
                raise_exceptions=True,
            )
        except OneLogin_Saml2_ValidationError as exc:
            code, status = _VALIDATION_CODE_MAP.get(
                exc.code, ("saml_validation_failed", 401)
            )
            raise SAMLError(code, status, "SAML assertion validation failed") from exc
        except Exception as exc:  # xmlsec/lxml failures on hostile input
            raise SAMLError(
                "saml_validation_failed", 401, "SAML assertion validation failed"
            ) from exc

        not_after = SAMLService._enforce_strict_bindings(
            response,
            sp_entity_id=sp_entity_id,
            acs_url=acs_url,
            expected_request_id=expected_request_id,
            now=time.time(),
        )

        try:
            name_id = response.get_nameid()
            attributes = response.get_attributes() or {}
            session_index = response.get_session_index()
            assertion_id = response.get_assertion_id()
        except OneLogin_Saml2_ValidationError as exc:
            raise SAMLError(
                "saml_assertion_structure_invalid",
                401,
                "SAML assertion validation failed",
            ) from exc
        if (
            not name_id
            or len(name_id) > _MAX_NAME_ID_CHARS
            or not assertion_id
            or len(assertion_id) > _MAX_ASSERTION_ID_CHARS
        ):
            raise SAMLError(
                "saml_assertion_structure_invalid",
                401,
                "SAML assertion validation failed",
            )
        return ValidatedAssertion(
            name_id=name_id,
            attributes={
                str(k): [str(v) for v in vals] for k, vals in attributes.items()
            },
            session_index=session_index,
            assertion_id=assertion_id,
            in_response_to=expected_request_id,
            not_after=not_after,
        )

    @staticmethod
    def _record_assertion(
        tenant_id: int, idp_entity_id: str, assertion: ValidatedAssertion
    ) -> None:
        """Reject a replayed assertion id; otherwise remember it until it expires.

        Uses a single atomic ``SET NX EX`` so two concurrent submissions of the
        same assertion cannot both succeed. The TTL covers the assertion's
        validity window plus clock skew, so an id is remembered for as long as
        the assertion could still validate.

        Raises:
            SAMLError: 401 on replay; 503 if the store is unavailable
        """
        client = SAMLService._state_store()
        digest = hashlib.sha256(
            f"{idp_entity_id}\n{assertion.assertion_id}".encode()
        ).hexdigest()
        ttl = max(
            _REPLAY_TTL_FLOOR_SECONDS,
            math.ceil(assertion.not_after + _CLOCK_SKEW_SECONDS - time.time())
            + _REPLAY_TTL_FLOOR_SECONDS,
        )
        try:
            first_use = client.set(
                f"{_REPLAY_KEY_PREFIX}:{tenant_id}:{digest}", "1", nx=True, ex=ttl
            )
        except RedisError as exc:
            raise SAMLError(
                "saml_state_store_unavailable", 503, "SAML is temporarily unavailable"
            ) from exc
        if not first_use:
            raise SAMLError(
                "saml_assertion_replayed", 401, "This assertion has already been used"
            )

    @staticmethod
    def _validate_login(
        tenant_id: int, saml_response: str, state_token: str | None
    ) -> tuple[dict, dict, ValidatedAssertion]:
        """Blocking half of ACS processing: config, state, validation, replay.

        Order matters: the single-use login state is consumed *before* the
        response is examined (a failed attempt burns it), and the replay record
        is written only *after* the assertion fully validated.

        Returns:
            ``(idp_config, login_state, validated_assertion)``
        """
        idp_config = SAMLService._load_saml_idp(tenant_id)
        state = SAMLService._consume_login_state(tenant_id, state_token)
        assertion = SAMLService._validate_response(
            tenant_id, idp_config, saml_response, state["request_id"]
        )
        SAMLService._record_assertion(tenant_id, idp_config["entity_id"], assertion)
        return idp_config, state, assertion

    @staticmethod
    def _first_value(value: Any) -> str | None:
        """Return the first non-empty string of a SAML attribute value list."""
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _extract_email(name_id: str | None, attributes: dict, idp_config: dict) -> str:
        """Resolve and validate the user's email from the mapped attribute or NameID.

        Raises:
            SAMLError: 400 if no usable, well-formed email is present
        """
        mappings = idp_config.get("attribute_mappings") or {}
        candidate = SAMLService._first_value(
            attributes.get(mappings.get("email", "email"))
        )
        if not candidate and name_id and "@" in name_id:
            candidate = name_id.strip()
        if not candidate:
            raise SAMLError(
                "saml_email_missing",
                400,
                "The assertion did not carry an email address",
            )
        try:
            if len(candidate) > _MAX_EMAIL_CHARS:
                raise ValueError("too long")
            return validate_email_with_local(candidate)
        except ValueError as exc:
            raise SAMLError(
                "saml_email_invalid",
                400,
                "The assertion carried an invalid email address",
            ) from exc

    @staticmethod
    def _map_role(attributes: dict, idp_config: dict) -> str:
        """Map SAML attribute values to an Elder tenant role for a *new* user.

        ``attribute_mappings.role`` names the SAML attribute carrying the
        group/role values and ``attribute_mappings.role_map`` translates them to
        Elder roles. Only values explicitly listed in ``role_map`` ever confer a
        role, and only reader/maintainer/admin are possible; the highest-ranked
        match wins. Anything else falls back to ``default_role`` (itself
        constrained to the same set). Existing users keep their stored role.
        """
        default = idp_config.get("default_role") or "reader"
        if default not in _VALID_TENANT_ROLES:
            default = "reader"
        mappings = idp_config.get("attribute_mappings") or {}
        role_attr = mappings.get("role")
        role_map = mappings.get("role_map")
        if not role_attr or not isinstance(role_map, dict) or not role_map:
            return default
        values = attributes.get(role_attr) or []
        if not isinstance(values, (list, tuple)):
            values = [values]
        best = None
        for value in values:
            mapped = role_map.get(str(value))
            if mapped in _VALID_TENANT_ROLES and (
                best is None
                or _VALID_TENANT_ROLES.index(mapped) > _VALID_TENANT_ROLES.index(best)
            ):
                best = mapped
        return best or default

    @staticmethod
    def _user_exists(tenant_id: int, email: str) -> bool:
        """True if a portal user with this email already exists in the tenant."""
        db = _db()
        return (
            db(
                (db.portal_users.email == email.lower())
                & (db.portal_users.tenant_id == tenant_id)
            )
            .select()
            .first()
            is not None
        )

    @staticmethod
    async def process_saml_response(
        tenant_id: int, saml_response: str, state_token: str | None
    ) -> dict:
        """Validate a posted SAML response and authenticate (or JIT-create) the user.

        Never trusts the assertion unless the full validation chain passes
        (see the module docstring). Every rejection is returned as an error
        dict ``{"error": <code>, "message": ..., "status_code": <4xx/5xx>}``;
        nothing is silently downgraded.

        Args:
            tenant_id: Tenant context from the ACS URL path
            saml_response: Base64-encoded SAMLResponse from the IdP POST
            state_token: Value of the browser's SAML login-state cookie

        Returns:
            On success the user dict for ``generate_tokens`` (``id``, ``email``,
            ``tenant_id``, ``tenant_role``, ``global_role``) plus ``return_to``;
            on failure the error dict described above.
        """
        started = time.monotonic()
        outcome = "authenticated"
        try:
            with _tracer.start_as_current_span("saml.acs") as span:
                span.set_attribute("saml.tenant_id", tenant_id)
                return await SAMLService._authenticate(
                    tenant_id, saml_response, state_token
                )
        except SAMLError as exc:
            outcome = exc.code
            logger.warning(
                "saml_acs_rejected",
                tenant_id=tenant_id,
                code=exc.code,
                status=exc.status_code,
            )
            return exc.as_dict()
        except Exception:
            outcome = "error"
            raise
        finally:
            attrs = {"outcome": outcome}
            _acs_counter.add(1, attrs)
            _acs_duration.record((time.monotonic() - started) * 1000.0, attrs)

    @staticmethod
    async def _authenticate(
        tenant_id: int, saml_response: str, state_token: str | None
    ) -> dict:
        """Async orchestration behind ``process_saml_response`` (raises SAMLError)."""
        idp_config, state, assertion = await run_in_threadpool(
            SAMLService._validate_login, tenant_id, saml_response, state_token
        )
        email = SAMLService._extract_email(
            assertion.name_id, assertion.attributes, idp_config
        )

        exists = await run_in_threadpool(SAMLService._user_exists, tenant_id, email)
        if not exists:
            if not idp_config.get("jit_provisioning_enabled"):
                raise SAMLError(
                    "saml_jit_disabled",
                    403,
                    "No matching user and just-in-time provisioning is disabled",
                )
            if SAMLService._map_role(assertion.attributes, idp_config) == "admin":
                # Same admin-seat gate create_portal_user applies to new admins.
                blocked = await check_limit("tenant_admin", tenant_id)
                if blocked is not None:
                    raise SAMLError(
                        "limit_reached", 402, "The tenant's admin limit is reached"
                    )

        user = await run_in_threadpool(
            SAMLService.jit_provision_user,
            tenant_id,
            email,
            idp_config,
            assertion.attributes,
        )
        if "error" in user:
            raise SAMLError(
                user["error"], user.get("status_code", 400), user.get("message", "")
            )

        # A tenant-scoped IdP is controlled by that tenant's admins, so it must
        # not be able to mint a session carrying platform-wide privileges.
        if idp_config.get("tenant_id") is not None:
            user["global_role"] = None

        logger.info(
            "saml_acs_authenticated",
            tenant_id=tenant_id,
            idp_id=idp_config.get("id"),
            jit_provisioned=bool(user.get("jit_provisioned")),
        )
        user["return_to"] = state.get("return_to")
        return user

    @staticmethod
    def jit_provision_user(
        tenant_id: int, email: str, idp_config: dict, attributes: dict
    ) -> dict:
        """Just-in-time provision a user from SAML attributes.

        An existing user must still be active (a deactivated account is
        refused, never silently re-enabled); a new user's role comes from
        ``_map_role`` and is limited to reader/maintainer/admin.

        Args:
            tenant_id: Tenant ID
            email: User email from SAML
            idp_config: IdP configuration
            attributes: SAML attributes

        Returns:
            Created or existing user dict, or an error dict
            (``error``/``message``/``status_code``) for a deactivated account
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
            if not existing.is_active:
                return {
                    "error": "saml_account_deactivated",
                    "message": "Account is deactivated",
                    "status_code": 403,
                }
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
                "global_role": existing.global_role,
            }

        # Map attributes to user fields
        mappings = idp_config.get("attribute_mappings") or {}
        full_name = SAMLService._first_value(
            attributes.get(mappings.get("full_name", "name"))
        )
        if full_name:
            full_name = full_name[:_MAX_FULL_NAME_CHARS]
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
            last_login_at=datetime.datetime.now(datetime.UTC),
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
    def get_sp_metadata(base_url: str, tenant_id: int | None = None) -> str:
        """Generate Service Provider SAML metadata XML.

        Args:
            base_url: Base URL of the Elder instance
            tenant_id: Optional tenant ID for tenant-specific metadata

        Returns:
            SAML SP metadata XML string
        """
        entity_id, acs_url, slo_url = (
            html.escape(u, quote=True)
            for u in SAMLService.sp_urls(base_url.rstrip("/"), tenant_id)
        )

        # Basic SP metadata template. AuthnRequestsSigned is "false": this SP
        # holds no signing key, so advertising "true" would make IdPs that
        # honour it reject every AuthnRequest we send.
        metadata = f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
                     entityID="{entity_id}">
  <md:SPSSODescriptor AuthnRequestsSigned="false"
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
    def list_idp_configs(tenant_id: int | None = None) -> list:
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
