"""
Pydantic 2 models for Entity domain objects.

Provides validated Pydantic 2 equivalents of Entity dataclasses:
- EntityDTO: Immutable frozen DTO for API responses
- CreateEntityRequest: Request validation with security hardening
- UpdateEntityRequest: Flexible update request with all optional fields
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field


class EntityDTO(ImmutableModel):
    """
    Immutable Entity data transfer object.

    Represents a complete Entity record with all fields. Used for API responses
    and data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        name: Entity name (1-255 chars)
        description: Optional detailed description
        entity_type: Type classification (e.g., 'service', 'infrastructure')
        sub_type: Optional sub-type for granular classification
        organization_id: Associated organization ID
        parent_id: Optional parent entity ID for hierarchies
        attributes: Optional custom attributes dictionary
        tags: Optional list of classification tags
        is_active: Active status flag
        default_metadata: Optional default metadata dictionary
        status_metadata: Optional status-specific metadata
        created_at: Creation timestamp
        updated_at: Last update timestamp
        village_id: Optional unique hierarchical identifier
    """

    id: int
    name: str
    description: Optional[str] = None
    entity_type: str
    sub_type: Optional[str] = None
    organization_id: int
    parent_id: Optional[int] = None
    attributes: Optional[dict] = None
    tags: Optional[list[str]] = None
    is_active: bool
    default_metadata: Optional[dict] = None
    status_metadata: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    village_id: Optional[str] = None


class CreateEntityRequest(RequestModel):
    """
    Request to create a new Entity.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        name: Entity name (1-255 chars, required)
        entity_type: Type classification (required)
        organization_id: Associated organization ID (required, must be >= 1)
        description: Optional detailed description
        sub_type: Optional sub-type classification
        parent_id: Optional parent entity ID
        attributes: Optional custom attributes
        tags: Optional classification tags (default: empty list)
        default_metadata: Optional default metadata
        is_active: Active status (default: True)
    """

    name: str = Field(
        ...,
        description="Entity name",
    )
    entity_type: str = Field(
        ...,
        description="Type classification",
    )
    organization_id: int = Field(
        ...,
        ge=1,
        description="Associated organization ID (must be positive)",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    sub_type: Optional[str] = Field(
        default=None,
        description="Optional sub-type classification",
    )
    parent_id: Optional[int] = Field(
        default=None,
        description="Optional parent entity ID",
    )
    attributes: Optional[dict] = Field(
        default=None,
        description="Optional custom attributes",
    )
    tags: Optional[list[str]] = Field(
        default_factory=list,
        description="Optional classification tags",
    )
    default_metadata: Optional[dict] = Field(
        default=None,
        description="Optional default metadata",
    )
    is_active: bool = Field(
        default=True,
        description="Active status",
    )


class UpdateEntityRequest(RequestModel):
    """
    Request to update an existing Entity.

    All fields are optional to support partial updates. Uses RequestModel
    to reject unknown fields and prevent injection attacks.

    Attributes:
        name: Entity name (optional)
        description: Detailed description (optional)
        entity_type: Type classification (optional)
        sub_type: Sub-type classification (optional)
        parent_id: Parent entity ID (optional)
        attributes: Custom attributes (optional)
        tags: Classification tags (optional)
        default_metadata: Default metadata (optional)
        is_active: Active status (optional)
    """

    name: Optional[str] = Field(
        default=None,
        description="Entity name",
    )
    description: Optional[str] = Field(
        default=None,
        description="Detailed description",
    )
    entity_type: Optional[str] = Field(
        default=None,
        description="Type classification",
    )
    sub_type: Optional[str] = Field(
        default=None,
        description="Sub-type classification",
    )
    parent_id: Optional[int] = Field(
        default=None,
        description="Parent entity ID",
    )
    attributes: Optional[dict] = Field(
        default=None,
        description="Custom attributes",
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="Classification tags",
    )
    default_metadata: Optional[dict] = Field(
        default=None,
        description="Default metadata",
    )
    is_active: Optional[bool] = Field(
        default=None,
        description="Active status",
    )
