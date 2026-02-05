"""
Pydantic 2 models for Organization Units (OUs).

Provides immutable DTOs and request models for organization management
with field validation and type safety.
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Literal, Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from penguin_libs.pydantic.types import Name255
from pydantic import Field, field_validator

OrganizationType = Literal["department", "organization", "team", "collection", "other"]
"""Organization unit type enumeration."""


class OrganizationDTO(ImmutableModel):
    """Immutable Organization Unit (OU) data transfer object."""

    id: int
    name: str
    description: Optional[str] = None
    organization_type: str
    parent_id: Optional[int] = None
    ldap_dn: Optional[str] = None
    saml_group: Optional[str] = None
    owner_identity_id: Optional[int] = None
    owner_group_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    tenant_id: Optional[int] = None
    village_id: Optional[str] = None
    village_segment: Optional[str] = None


class CreateOrganizationRequest(RequestModel):
    """Request to create a new Organization Unit (OU)."""

    name: Name255
    description: Optional[str] = Field(None, max_length=1000)
    organization_type: str = "organization"
    parent_id: Optional[int] = None
    ldap_dn: Optional[str] = None
    saml_group: Optional[str] = None
    owner_identity_id: Optional[int] = None
    owner_group_id: Optional[int] = None

    @field_validator("name")
    @classmethod
    def name_not_whitespace(cls, v: str) -> str:
        """Ensure name is not just whitespace."""
        if v.strip() == "":
            raise ValueError("name cannot be empty or whitespace-only")
        return v


class UpdateOrganizationRequest(RequestModel):
    """Request to update an Organization Unit (OU)."""

    name: Optional[Name255] = None
    description: Optional[str] = Field(None, max_length=1000)
    organization_type: Optional[str] = None
    parent_id: Optional[int] = None
    ldap_dn: Optional[str] = None
    saml_group: Optional[str] = None
    owner_identity_id: Optional[int] = None
    owner_group_id: Optional[int] = None

    @field_validator("name")
    @classmethod
    def name_not_whitespace(cls, v: Optional[str]) -> Optional[str]:
        """Ensure name is not just whitespace if provided."""
        if v is not None and v.strip() == "":
            raise ValueError("name cannot be empty or whitespace-only")
        return v


__all__ = [
    "OrganizationType",
    "OrganizationDTO",
    "CreateOrganizationRequest",
    "UpdateOrganizationRequest",
]
