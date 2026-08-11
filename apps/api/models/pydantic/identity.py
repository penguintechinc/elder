"""Pydantic 2 models for Identity resource.

Provides validation, serialization, and type safety for identity operations.
"""

# flake8: noqa: E501

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, SecretStr, model_validator

# ==================== Type Definitions ====================

IdentityType = Literal[
    "human",
    "service_account",
    "employee",
    "vendor",
    "bot",
    "serviceAccount",
    "integration",
    "otherHuman",
    "other",
    "customer_contact",
]
AuthProvider = Literal[
    "local",
    "ldap",
    "saml",
    "oauth2",
    "api_key",
    "aws",
    "okta",
    "gcp",
    "google",
    "authentik",
    "kubernetes",
]
PortalRole = Literal["admin", "editor", "viewer"]


# ==================== Base Model Classes ====================


class ImmutableModel(BaseModel):
    """Base immutable model with frozen configuration."""

    model_config = {
        "frozen": True,
        "from_attributes": True,
    }


class RequestModel(BaseModel):
    """Base request model with standard configuration."""

    model_config = {
        "from_attributes": True,
    }


# ==================== Identity DTOs ====================


class IdentityDTO(ImmutableModel):
    """Immutable Identity data transfer object.

    Excludes password_hash and mfa_secret from serialization.
    """

    id: int
    identity_type: IdentityType
    username: str
    email: str | None = None
    full_name: str | None = None
    organization_id: int | None = None
    portal_role: PortalRole
    auth_provider: AuthProvider
    auth_provider_id: str | None = None
    is_active: bool
    is_superuser: bool
    mfa_enabled: bool
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    tenant_id: int | None = None
    village_id: str | None = None

    model_config = {
        "frozen": True,
        "from_attributes": True,
        "exclude": {"password_hash", "mfa_secret"},
    }


# ==================== Create/Update Requests ====================


class CreateIdentityRequest(RequestModel):
    """Request to create a new Identity.

    Requires password for local auth provider.
    """

    username: str
    identity_type: IdentityType
    auth_provider: AuthProvider
    email: str | None = None
    full_name: str | None = None
    password: SecretStr | None = None
    auth_provider_id: str | None = None
    is_active: bool = True
    is_superuser: bool = False
    mfa_enabled: bool = False
    organization_id: int | None = None
    tenant_id: int | None = None

    @model_validator(mode="after")
    def validate_local_auth_requires_password(self) -> "CreateIdentityRequest":
        """Validate that local auth provider requires a password."""
        if self.auth_provider == "local" and not self.password:
            raise ValueError("password is required for local auth provider")
        return self


class UpdateIdentityRequest(RequestModel):
    """Request to update an Identity.

    All fields are optional.
    """

    email: str | None = None
    full_name: str | None = None
    password: SecretStr | None = None
    is_active: bool | None = None
    mfa_enabled: bool | None = None
    portal_role: PortalRole | None = None
    organization_id: int | None = None


# ==================== Identity Group DTOs ====================


class IdentityGroupDTO(ImmutableModel):
    """Immutable Identity Group data transfer object."""

    id: int
    name: str
    description: str | None = None
    ldap_dn: str | None = None
    saml_group: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ==================== Identity Group Requests ====================


class CreateIdentityGroupRequest(RequestModel):
    """Request to create a new Identity Group."""

    name: str
    description: str | None = None
    ldap_dn: str | None = None
    saml_group: str | None = None
    is_active: bool = True


class UpdateIdentityGroupRequest(RequestModel):
    """Request to update an Identity Group.

    All fields are optional.
    """

    name: str | None = None
    description: str | None = None
    ldap_dn: str | None = None
    saml_group: str | None = None
    is_active: bool | None = None
