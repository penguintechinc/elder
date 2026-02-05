"""
Pydantic 2 models for Dependency domain objects.

Provides validated Pydantic 2 equivalents of Dependency dataclasses:
- DependencyDTO: Immutable frozen DTO for API responses
- CreateDependencyRequest: Request validation for creating dependencies
- UpdateDependencyRequest: Flexible update request with all optional fields
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field


class DependencyDTO(ImmutableModel):
    """
    Immutable Dependency data transfer object.

    Represents a complete Dependency record with all fields. Used for API responses
    and data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        tenant_id: Associated tenant ID
        source_type: Type of source resource
        source_id: ID of source resource
        target_type: Type of target resource
        target_id: ID of target resource
        dependency_type: Type of dependency relationship
        metadata: Optional custom metadata dictionary
        created_at: Creation timestamp
        updated_at: Last update timestamp
        village_id: Optional unique hierarchical identifier
    """

    id: int
    tenant_id: int
    source_type: str
    source_id: int
    target_type: str
    target_id: int
    dependency_type: str
    metadata: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    village_id: Optional[str] = None


class CreateDependencyRequest(RequestModel):
    """
    Request to create a new Dependency.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        source_type: Type of source resource (required)
        source_id: ID of source resource (required, must be >= 1)
        target_type: Type of target resource (required)
        target_id: ID of target resource (required, must be >= 1)
        dependency_type: Type of dependency relationship (required)
        metadata: Optional custom metadata dictionary
    """

    source_type: str = Field(
        ...,
        description="Type of source resource",
    )
    source_id: int = Field(
        ...,
        ge=1,
        description="ID of source resource (must be positive)",
    )
    target_type: str = Field(
        ...,
        description="Type of target resource",
    )
    target_id: int = Field(
        ...,
        ge=1,
        description="ID of target resource (must be positive)",
    )
    dependency_type: str = Field(
        ...,
        description="Type of dependency relationship",
    )
    metadata: Optional[dict] = Field(
        default=None,
        description="Optional custom metadata",
    )


class UpdateDependencyRequest(RequestModel):
    """
    Request to update an existing Dependency.

    All fields are optional to support partial updates. Uses RequestModel
    to reject unknown fields and prevent injection attacks.

    Attributes:
        source_type: Type of source resource (optional)
        source_id: ID of source resource (optional)
        target_type: Type of target resource (optional)
        target_id: ID of target resource (optional)
        dependency_type: Type of dependency relationship (optional)
        metadata: Custom metadata (optional)
    """

    source_type: Optional[str] = Field(
        default=None,
        description="Type of source resource",
    )
    source_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="ID of source resource (must be positive)",
    )
    target_type: Optional[str] = Field(
        default=None,
        description="Type of target resource",
    )
    target_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="ID of target resource (must be positive)",
    )
    dependency_type: Optional[str] = Field(
        default=None,
        description="Type of dependency relationship",
    )
    metadata: Optional[dict] = Field(
        default=None,
        description="Custom metadata",
    )
