"""
Pydantic 2 models for Label domain objects.

Provides validated Pydantic 2 equivalents of Label dataclasses:
- LabelDTO: Immutable frozen DTO for API responses
- CreateLabelRequest: Request validation for label creation
- UpdateLabelRequest: Flexible update request with all optional fields
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field


class LabelDTO(ImmutableModel):
    """
    Immutable Label data transfer object.

    Represents a complete Label record with all fields. Used for API responses
    and data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        name: Label name (1-255 chars)
        description: Optional detailed description
        color: Hex color code (e.g., '#cccccc')
        created_at: Creation timestamp
    """

    id: int
    name: str
    color: str
    description: Optional[str] = None
    created_at: datetime


class CreateLabelRequest(RequestModel):
    """
    Request to create a new Label.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        name: Label name (1-255 chars, required)
        description: Optional detailed description
        color: Hex color code (optional, default: '#cccccc')
    """

    name: str = Field(
        ...,
        description="Label name",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed description",
    )
    color: Optional[str] = Field(
        default="#cccccc",
        description="Hex color code (default: #cccccc)",
    )


class UpdateLabelRequest(RequestModel):
    """
    Request to update an existing Label.

    All fields are optional to support partial updates. Uses RequestModel
    to reject unknown fields and prevent injection attacks.

    Attributes:
        name: Label name (optional)
        description: Detailed description (optional)
        color: Hex color code (optional)
    """

    name: Optional[str] = Field(
        default=None,
        description="Label name",
    )
    description: Optional[str] = Field(
        default=None,
        description="Detailed description",
    )
    color: Optional[str] = Field(
        default=None,
        description="Hex color code",
    )
