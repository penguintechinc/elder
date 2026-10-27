"""Tenant-ownership scoping and response allowlist for IdP configuration CRUD.

Security hotfix (regression: idp-config-cross-tenant): the IdP admin routes
used to check only ``tenant_role == "admin"`` and then loaded/mutated
``idp_configurations`` rows by bare primary key, so a tenant-A admin could
read, rewrite (swap the SAML certificate / OIDC issuer+client) or delete
tenant B's -- or the global -- IdP configuration and then log in as a tenant-B
user. Everything that reads or mutates those rows on behalf of an
authenticated admin now goes through this module:

* ``IdPCaller`` carries the caller's tenant (from the validated JWT, never the
  request body/query) and whether they are a global (super) admin.
* ``IdPCaller.scoped_query`` filters the DAL query itself, so a non-global
  caller can never even load another tenant's (or a global) row.
* A row outside the caller's scope is reported as ``IdPNotFoundError`` (HTTP
  404, never 403): a 403 would let a caller tell "exists in another tenant"
  from "does not exist" and enumerate other tenants' config ids -- the same
  rule as ``apps.api.utils.tenant_scoping.get_tenant_scoped``.
* ``IdPConfigView`` is the explicit response schema: secret material
  (``oidc_client_secret``) is never part of it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class IdPAccessError(Exception):
    """Base class for IdP-configuration access failures (mapped to 4xx by routes)."""


class IdPNotFoundError(IdPAccessError):
    """The config does not exist *or* is outside the caller's tenant scope (404)."""

    def __init__(self, config_id: int | None = None) -> None:
        """Remember the requested id so the route can render a uniform 404."""
        super().__init__("IdP configuration not found")
        self.config_id = config_id


class IdPForbiddenError(IdPAccessError):
    """The caller attempted something their scope never permits (403)."""


@dataclass(slots=True, frozen=True)
class IdPCaller:
    """The authenticated admin performing an IdP-configuration operation.

    ``tenant_id`` comes from the validated portal JWT claims; ``is_global_admin``
    is true only for ``global_role == "admin"``. Non-global callers are confined
    to rows whose ``tenant_id`` equals their own -- global (``tenant_id IS NULL``)
    configs are therefore modifiable by global admins only.
    """

    tenant_id: int | None
    is_global_admin: bool = False

    def scoped_query(self, db: Any) -> Any:
        """Return the DAL predicate selecting only rows this caller may touch.

        Global admins match every row; everyone else matches
        ``tenant_id == self.tenant_id``. A non-global caller without a tenant
        claim fails closed (never "skip the filter").
        """
        table = db.idp_configurations
        if self.is_global_admin:
            return table.id > 0
        if self.tenant_id is None:
            raise IdPForbiddenError(
                "Tenant claim required for IdP configuration access"
            )
        return table.tenant_id == self.tenant_id


@dataclass(slots=True, frozen=True)
class IdPConfigView:
    """Explicit, secret-free response schema for an IdP configuration.

    Only fields on this dataclass can ever leave the API. ``has_client_secret``
    reports whether an OIDC client secret is stored without returning it.
    """

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

    @classmethod
    def from_record(cls, record: Any) -> IdPConfigView:
        """Build the view from a DAL row, copying only allowlisted columns."""
        return cls(
            id=record.id,
            tenant_id=record.tenant_id,
            idp_type=record.idp_type,
            name=record.name,
            attribute_mappings=record.attribute_mappings or {},
            jit_provisioning_enabled=bool(record.jit_provisioning_enabled),
            default_role=record.default_role,
            is_active=bool(record.is_active),
            entity_id=record.entity_id,
            metadata_url=record.metadata_url,
            sso_url=record.sso_url,
            slo_url=record.slo_url,
            certificate=record.certificate,
            oidc_client_id=record.oidc_client_id,
            oidc_issuer_url=record.oidc_issuer_url,
            oidc_scopes=record.oidc_scopes or "openid profile email",
            oidc_response_type=record.oidc_response_type or "code",
            oidc_token_endpoint_auth_method=record.oidc_token_endpoint_auth_method
            or "client_secret_basic",
            has_client_secret=bool(record.oidc_client_secret),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize every allowlisted field (never a secret) to a plain dict."""
        return asdict(self)
