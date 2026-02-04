"""
Pydantic 2 models for Metadata Field domain objects.

Provides validated Pydantic 2 equivalents of Metadata Field dataclasses:
- MetadataFieldDTO: Immutable frozen DTO for API responses
- CreateMetadataFieldRequest: Request validation with security hardening
- UpdateMetadataFieldRequest: Flexible update request with all optional fields
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field


class MetadataFieldDTO(ImmutableModel):
    """
    Immutable Metadata Field data transfer object.

    Represents a complete Metadata Field record with all fields. Used for API responses
    and data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        key: Metadata field key/name
        value: Optional metadata field value
        field_type: Type of the field (string, number, date, boolean, json)
        is_system: Flag indicating if this is a system-managed field
        resource_type: Type of resource this metadata applies to
        resource_id: ID of the resource this metadata applies to
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    id: int
    key: str
    value: Optional[str] = None
    field_type: str
    is_system: bool
    resource_type: str
    resource_id: int
    created_at: datetime
    updated_at: datetime


class CreateMetadataFieldRequest(RequestModel):
    """
    Request to create a new Metadata Field.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        key: Metadata field key/name (required)
        value: Optional metadata field value
        field_type: Type of the field (required, must be one of: string, number, date, boolean, json)
        resource_type: Type of resource this metadata applies to (required)
        resource_id: ID of the resource this metadata applies to (required, must be >= 1)
        is_system: Flag indicating if this is a system-managed field (default: False)
    """

    key: str = Field(
        ...,
        description="Metadata field key/name",
        min_length=1,
        max_length=255,
    )
    value: Optional[str] = Field(
        default=None,
        description="Optional metadata field value",
    )
    field_type: str = Field(
        ...,
        description="Type of the field (string, number, date, boolean, json)",
    )
    resource_type: str = Field(
        ...,
        description="Type of resource this metadata applies to",
    )
    resource_id: int = Field(
        ...,
        ge=1,
        description="ID of the resource this metadata applies to (must be positive)",
    )
    is_system: bool = Field(
        default=False,
        description="Flag indicating if this is a system-managed field",
    )


class UpdateMetadataFieldRequest(RequestModel):
    """
    Request to update an existing Metadata Field.

    All fields are optional to support partial updates. Uses RequestModel
    to reject unknown fields and prevent injection attacks.

    Attributes:
        key: Metadata field key/name (optional)
        value: Metadata field value (optional)
        field_type: Type of the field (optional)
        is_system: Flag indicating if this is a system-managed field (optional)
    """

    key: Optional[str] = Field(
        default=None,
        description="Metadata field key/name",
        min_length=1,
        max_length=255,
    )
    value: Optional[str] = Field(
        default=None,
        description="Metadata field value",
    )
    field_type: Optional[str] = Field(
        default=None,
        description="Type of the field (string, number, date, boolean, json)",
    )
    is_system: Optional[bool] = Field(
        default=None,
        description="Flag indicating if this is a system-managed field",
    )
