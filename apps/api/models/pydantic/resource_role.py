"""Pydantic 2 models for ResourceRole resource.

Provides validation, serialization, and type safety for resource role operations.
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Literal, Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field

# ==================== Type Definitions ====================

ResourceType = Literal["entity", "organization"]
RoleType = Literal["maintainer", "operator", "viewer"]


# ==================== ResourceRole DTOs ====================


class ResourceRoleResponse(ImmutableModel):
    """Immutable ResourceRole data transfer object for API responses."""

    id: int = Field(description="Resource role ID")
    identity_id: Optional[int] = Field(
        None, description="Identity ID (if role assigned to identity)"
    )
    group_id: Optional[int] = Field(
        None, description="Group ID (if role assigned to group)"
    )
    resource_type: ResourceType = Field(
        description="Resource type (entity or organization)"
    )
    resource_id: Optional[int] = Field(None, description="Resource ID")
    role: RoleType = Field(description="Role type (maintainer, operator, viewer)")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")


# ==================== Request Models ====================


class CreateResourceRoleRequest(RequestModel):
    """Request model for creating a resource role.

    Either identity_id or group_id must be provided (but not both).
    """

    identity_id: Optional[int] = Field(None, ge=1, description="Identity ID")
    group_id: Optional[int] = Field(None, ge=1, description="Group ID")
    resource_type: ResourceType = Field(description="Resource type")
    resource_id: Optional[int] = Field(None, ge=1, description="Resource ID")
    role: RoleType = Field(description="Role to assign")


__all__ = [
    "ResourceType",
    "RoleType",
    "ResourceRoleResponse",
    "CreateResourceRoleRequest",
]
