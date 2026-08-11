"""
Pydantic 2 models for Software domain objects.

Provides validated Pydantic 2 equivalents of Software dataclasses:
- SoftwareDTO: Immutable frozen DTO for API responses
- CreateSoftwareRequest: Request validation with security hardening
- UpdateSoftwareRequest: Flexible update request with all optional fields
"""

# flake8: noqa: E501

from datetime import date, datetime
from typing import Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field


class SoftwareDTO(ImmutableModel):
    """
    Immutable Software data transfer object.

    Represents a complete Software record with all fields. Used for API responses
    and data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        tenant_id: Associated tenant ID
        name: Software product name (1-255 chars)
        description: Optional detailed description
        organization_id: Associated organization ID
        purchasing_poc_id: Optional purchasing point of contact identity ID
        license_url: Optional URL to license documentation
        version: Optional software version
        business_purpose: Optional business purpose/justification
        software_type: Type classification (e.g., 'commercial', 'open-source')
        seats: Optional number of purchased licenses/seats
        cost_monthly: Optional monthly cost in currency units
        renewal_date: Optional license renewal date
        vendor: Optional software vendor name
        support_contact: Optional support contact information
        notes: Optional additional notes
        tags: Optional list of classification tags
        is_active: Active status flag
        created_at: Creation timestamp
        updated_at: Last update timestamp
        village_id: Optional unique hierarchical identifier
    """

    id: int
    tenant_id: int
    name: str
    description: str | None = None
    organization_id: int
    purchasing_poc_id: int | None = None
    license_url: str | None = None
    version: str | None = None
    business_purpose: str | None = None
    software_type: str
    seats: int | None = None
    cost_monthly: float | None = None
    renewal_date: date | None = None
    vendor: str | None = None
    support_contact: str | None = None
    notes: str | None = None
    tags: list | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None
    village_id: str | None = None


class CreateSoftwareRequest(RequestModel):
    """
    Request to create a new Software record.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        name: Software product name (1-255 chars, required)
        organization_id: Associated organization ID (required, must be >= 1)
        software_type: Type classification (required)
        description: Optional detailed description
        purchasing_poc_id: Optional purchasing point of contact identity ID
        license_url: Optional URL to license documentation
        version: Optional software version
        business_purpose: Optional business purpose/justification
        seats: Optional number of purchased licenses/seats
        cost_monthly: Optional monthly cost
        renewal_date: Optional license renewal date
        vendor: Optional software vendor name
        support_contact: Optional support contact information
        notes: Optional additional notes
        tags: Optional classification tags (default: empty list)
        is_active: Active status (default: True)
    """

    name: str = Field(
        ...,
        description="Software product name",
    )
    organization_id: int = Field(
        ...,
        ge=1,
        description="Associated organization ID (must be positive)",
    )
    software_type: str = Field(
        ...,
        description="Type classification (e.g., 'commercial', 'open-source')",
    )
    description: str | None = Field(
        default=None,
        description="Optional detailed description",
    )
    purchasing_poc_id: int | None = Field(
        default=None,
        description="Optional purchasing point of contact identity ID",
    )
    license_url: str | None = Field(
        default=None,
        description="Optional URL to license documentation",
    )
    version: str | None = Field(
        default=None,
        description="Optional software version",
    )
    business_purpose: str | None = Field(
        default=None,
        description="Optional business purpose/justification",
    )
    seats: int | None = Field(
        default=None,
        ge=0,
        description="Optional number of purchased licenses/seats",
    )
    cost_monthly: float | None = Field(
        default=None,
        ge=0,
        description="Optional monthly cost",
    )
    renewal_date: date | None = Field(
        default=None,
        description="Optional license renewal date",
    )
    vendor: str | None = Field(
        default=None,
        description="Optional software vendor name",
    )
    support_contact: str | None = Field(
        default=None,
        description="Optional support contact information",
    )
    notes: str | None = Field(
        default=None,
        description="Optional additional notes",
    )
    tags: list[str] | None = Field(
        default_factory=list,
        description="Optional classification tags",
    )
    is_active: bool = Field(
        default=True,
        description="Active status",
    )


class UpdateSoftwareRequest(RequestModel):
    """
    Request to update an existing Software record.

    All fields are optional to support partial updates. Uses RequestModel
    to reject unknown fields and prevent injection attacks.

    Attributes:
        name: Software product name (optional)
        description: Detailed description (optional)
        software_type: Type classification (optional)
        purchasing_poc_id: Purchasing point of contact identity ID (optional)
        license_url: URL to license documentation (optional)
        version: Software version (optional)
        business_purpose: Business purpose/justification (optional)
        seats: Number of purchased licenses/seats (optional)
        cost_monthly: Monthly cost (optional)
        renewal_date: License renewal date (optional)
        vendor: Software vendor name (optional)
        support_contact: Support contact information (optional)
        notes: Additional notes (optional)
        tags: Classification tags (optional)
        is_active: Active status (optional)
    """

    name: str | None = Field(
        default=None,
        description="Software product name",
    )
    description: str | None = Field(
        default=None,
        description="Detailed description",
    )
    software_type: str | None = Field(
        default=None,
        description="Type classification",
    )
    purchasing_poc_id: int | None = Field(
        default=None,
        description="Purchasing point of contact identity ID",
    )
    license_url: str | None = Field(
        default=None,
        description="URL to license documentation",
    )
    version: str | None = Field(
        default=None,
        description="Software version",
    )
    business_purpose: str | None = Field(
        default=None,
        description="Business purpose/justification",
    )
    seats: int | None = Field(
        default=None,
        ge=0,
        description="Number of purchased licenses/seats",
    )
    cost_monthly: float | None = Field(
        default=None,
        ge=0,
        description="Monthly cost",
    )
    renewal_date: date | None = Field(
        default=None,
        description="License renewal date",
    )
    vendor: str | None = Field(
        default=None,
        description="Software vendor name",
    )
    support_contact: str | None = Field(
        default=None,
        description="Support contact information",
    )
    notes: str | None = Field(
        default=None,
        description="Additional notes",
    )
    tags: list[str] | None = Field(
        default=None,
        description="Classification tags",
    )
    is_active: bool | None = Field(
        default=None,
        description="Active status",
    )
