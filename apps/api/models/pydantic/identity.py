"""Pydantic 2 models for Identity resource.

Provides validation, serialization, and type safety for identity operations.
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, SecretStr, model_validator

# ==================== Type Definitions ====================

IdentityType = Literal["human", "service_account"]
AuthProvider = Literal["local", "ldap", "saml", "oauth2", "api_key"]
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
    email: Optional[str] = None
    full_name: Optional[str] = None
    organization_id: Optional[int] = None
    portal_role: PortalRole
    auth_provider: AuthProvider
    auth_provider_id: Optional[str] = None
    is_active: bool
    is_superuser: bool
    mfa_enabled: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    tenant_id: Optional[int] = None
    village_id: Optional[str] = None

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
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[SecretStr] = None
    auth_provider_id: Optional[str] = None
    is_active: bool = True
    is_superuser: bool = False
    mfa_enabled: bool = False
    organization_id: Optional[int] = None

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

    email: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[SecretStr] = None
    is_active: Optional[bool] = None
    mfa_enabled: Optional[bool] = None
    portal_role: Optional[PortalRole] = None
    organization_id: Optional[int] = None


# ==================== Identity Group DTOs ====================


class IdentityGroupDTO(ImmutableModel):
    """Immutable Identity Group data transfer object."""

    id: int
    name: str
    description: Optional[str] = None
    ldap_dn: Optional[str] = None
    saml_group: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ==================== Identity Group Requests ====================


class CreateIdentityGroupRequest(RequestModel):
    """Request to create a new Identity Group."""

    name: str
    description: Optional[str] = None
    ldap_dn: Optional[str] = None
    saml_group: Optional[str] = None
    is_active: bool = True


class UpdateIdentityGroupRequest(RequestModel):
    """Request to update an Identity Group.

    All fields are optional.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    ldap_dn: Optional[str] = None
    saml_group: Optional[str] = None
    is_active: Optional[bool] = None
