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

OrganizationType = Literal[
    "department", "organization", "team", "collection", "other", "customer_company"
]
"""Organization unit type enumeration."""


class OrganizationDTO(ImmutableModel):
    """Immutable Organization Unit (OU) data transfer object."""

    id: int
    name: str
    description: str | None = None
    organization_type: str
    parent_id: int | None = None
    ldap_dn: str | None = None
    saml_group: str | None = None
    owner_identity_id: int | None = None
    owner_group_id: int | None = None
    created_at: datetime
    updated_at: datetime
    slug: str | None = None
    tenant_id: int | None = None
    village_id: str | None = None
    village_segment: str | None = None


class CreateOrganizationRequest(RequestModel):
    """Request to create a new Organization Unit (OU)."""

    name: Name255
    description: str | None = Field(None, max_length=1000)
    organization_type: str = "organization"
    parent_id: int | None = None
    ldap_dn: str | None = None
    saml_group: str | None = None
    owner_identity_id: int | None = None
    owner_group_id: int | None = None

    @field_validator("name")
    @classmethod
    def name_not_whitespace(cls, v: str) -> str:
        """Ensure name is not just whitespace."""
        if v.strip() == "":
            raise ValueError("name cannot be empty or whitespace-only")
        return v


class UpdateOrganizationRequest(RequestModel):
    """Request to update an Organization Unit (OU)."""

    name: Name255 | None = None
    description: str | None = Field(None, max_length=1000)
    organization_type: str | None = None
    parent_id: int | None = None
    ldap_dn: str | None = None
    saml_group: str | None = None
    owner_identity_id: int | None = None
    owner_group_id: int | None = None

    @field_validator("name")
    @classmethod
    def name_not_whitespace(cls, v: str | None) -> str | None:
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
