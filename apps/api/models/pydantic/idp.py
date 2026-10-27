"""Pydantic 2 request/response schemas for SSO IdP configuration endpoints.

regression: idp-config-cross-tenant. Requests are validated against strict,
explicit schemas (wrong types are a 400, never a 500 whose exception text could
carry a submitted ``oidc_client_secret`` into logs), and responses are emitted
through ``IdPConfigResponse`` -- a field allowlist that has no secret field, so
``oidc_client_secret`` can never leave the API even if a service dict grows an
extra key (``extra="ignore"`` drops it rather than echoing it).
"""

# flake8: noqa: E501

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
)

# Columns an administrator may change on an existing IdP config. Mirrors
# ``saml_service._UPDATABLE_FIELDS``; a test pins both so neither can widen
# silently (``tenant_id`` / ``idp_type`` / ``id`` are deliberately absent).
UPDATABLE_IDP_FIELDS: frozenset[str] = frozenset(
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

Name = Annotated[StrictStr, StringConstraints(min_length=1, max_length=255)]
RoleName = Annotated[StrictStr, StringConstraints(min_length=1, max_length=50)]
OptStr50 = Annotated[StrictStr, StringConstraints(max_length=50)] | None
OptStr100 = Annotated[StrictStr, StringConstraints(max_length=100)] | None
OptStr512 = Annotated[StrictStr, StringConstraints(max_length=512)] | None
OptStr1024 = Annotated[StrictStr, StringConstraints(max_length=1024)] | None
OptCertificate = Annotated[StrictStr, StringConstraints(max_length=65536)] | None
AttributeMappings = Annotated[dict[StrictStr, StrictStr], Field(max_length=64)] | None


class _IdPWriteBase(BaseModel):
    """Shared strict field types; unknown body keys are ignored, never stored."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    entity_id: OptStr512 = None
    metadata_url: OptStr1024 = None
    sso_url: OptStr1024 = None
    slo_url: OptStr1024 = None
    certificate: OptCertificate = None
    oidc_client_id: OptStr512 = None
    # SecretStr: repr()/logging of the model masks the value.
    oidc_client_secret: Annotated[SecretStr, Field(max_length=512)] | None = None
    oidc_issuer_url: OptStr1024 = None
    oidc_scopes: OptStr512 = None
    oidc_response_type: OptStr50 = None
    oidc_token_endpoint_auth_method: OptStr100 = None
    attribute_mappings: AttributeMappings = None
    # Present-but-null is distinguishable from absent via ``model_fields_set``.
    tenant_id: StrictInt | None = None


class CreateIdPConfigRequest(_IdPWriteBase):
    """POST /sso/idp body."""

    name: Name
    idp_type: Literal["saml", "oidc"] = "saml"
    jit_provisioning_enabled: StrictBool = True
    default_role: RoleName = "reader"


class UpdateIdPConfigRequest(_IdPWriteBase):
    """PUT /sso/idp/<id> body: every field optional; ``null`` only where nullable."""

    name: Name = None  # type: ignore[assignment]  # default never validated; null is rejected
    jit_provisioning_enabled: StrictBool = None  # type: ignore[assignment]
    default_role: RoleName = None  # type: ignore[assignment]
    is_active: StrictBool = None  # type: ignore[assignment]

    def to_changes(self) -> dict[str, Any]:
        """Return only the updatable fields the caller actually sent.

        Secret values are unwrapped here -- the one place a ``SecretStr`` becomes
        a plain string, immediately before it is handed to the service.
        """
        changes: dict[str, Any] = {}
        for field_name in self.model_fields_set & UPDATABLE_IDP_FIELDS:
            value = getattr(self, field_name)
            changes[field_name] = (
                value.get_secret_value() if isinstance(value, SecretStr) else value
            )
        return changes


class IdPConfigResponse(BaseModel):
    """Allowlisted IdP configuration as returned by the API (never a secret).

    Fields belonging to the other protocol (SAML vs OIDC) are ``null``.
    ``extra="ignore"`` makes this model the allowlist: any key not declared here
    (e.g. a secret column) is dropped, never serialized and never echoed.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    tenant_id: int | None
    idp_type: str
    name: str
    attribute_mappings: dict[str, Any]
    jit_provisioning_enabled: bool
    default_role: str | None
    is_active: bool
    entity_id: str | None = None
    metadata_url: str | None = None
    sso_url: str | None = None
    slo_url: str | None = None
    certificate: str | None = None
    oidc_client_id: str | None = None
    oidc_issuer_url: str | None = None
    oidc_scopes: str | None = None
    oidc_response_type: str | None = None
    oidc_token_endpoint_auth_method: str | None = None
    has_client_secret: bool = False


class IdPConfigUpdateResponse(IdPConfigResponse):
    """PUT response: the updated config plus the legacy ``updated`` marker."""

    updated: bool = True


class IdPConfigDeleteResponse(BaseModel):
    """DELETE response."""

    deleted: bool


class IdPErrorResponse(BaseModel):
    """Error body (400 validation, 403, 404) -- never contains request values."""

    error: str
    validation_errors: list[dict[str, str]] | None = None
