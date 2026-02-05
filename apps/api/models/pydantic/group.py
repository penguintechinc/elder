"""
Pydantic 2 models for Group management endpoints.

Provides request/response models for group operations including:
- Group creation and updates
- Access request management
- Member management
- Bulk operations
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field, field_validator

# ==================== Group Request Models ====================


class UpdateGroupRequest(RequestModel):
    """Request model for updating group settings and ownership."""

    owner_identity_id: Optional[int] = Field(
        None, ge=1, description="Identity ID of new group owner"
    )
    owner_group_id: Optional[int] = Field(
        None, ge=1, description="Group ID of new group owner"
    )
    approval_mode: Optional[str] = Field(
        None,
        pattern="^(any|all|threshold)$",
        description="Approval mode: 'any', 'all', or 'threshold'",
    )
    approval_threshold: Optional[int] = Field(
        None, ge=1, description="Approval threshold for threshold mode"
    )
    provider: Optional[str] = Field(
        None,
        pattern="^(internal|ldap|okta)$",
        description="Provider type: 'internal', 'ldap', or 'okta'",
    )
    provider_group_id: Optional[str] = Field(
        None, max_length=500, description="Provider group identifier"
    )
    sync_enabled: Optional[bool] = Field(
        None, description="Enable sync with external provider"
    )

    @field_validator("owner_identity_id", "owner_group_id")
    @classmethod
    def at_least_one_owner(cls, v: Optional[int]) -> Optional[int]:
        """Ensure at least one owner is specified if setting owner."""
        return v


class CreateAccessRequestRequest(RequestModel):
    """Request model for creating group access requests."""

    reason: str = Field(
        ..., min_length=1, max_length=1000, description="Reason for access request"
    )
    expires_at: Optional[datetime] = Field(
        None, description="Optional expiration datetime for the access"
    )


class AddGroupMemberRequest(RequestModel):
    """Request model for adding members to a group."""

    identity_id: int = Field(..., ge=1, description="Identity ID of member to add")
    expires_at: Optional[datetime] = Field(
        None, description="Optional expiration datetime for membership"
    )
    provider_member_id: Optional[str] = Field(
        None, max_length=500, description="Provider member identifier"
    )


class ApproveOrDenyRequestRequest(RequestModel):
    """Request model for approving or denying access requests."""

    comment: Optional[str] = Field(
        None, max_length=1000, description="Optional comment for approval/denial"
    )


class BulkApproveRequestsRequest(RequestModel):
    """Request model for bulk approving access requests."""

    request_ids: list[int] = Field(
        ..., min_items=1, description="List of request IDs to approve"
    )
    comment: Optional[str] = Field(
        None, max_length=1000, description="Optional comment for bulk approval"
    )

    @field_validator("request_ids")
    @classmethod
    def all_ids_positive(cls, v: list[int]) -> list[int]:
        """Ensure all request IDs are positive."""
        if not all(rid > 0 for rid in v):
            raise ValueError("All request IDs must be positive")
        return v


# ==================== Group Response Models ====================


class GroupDTO(ImmutableModel):
    """Immutable Group data transfer object."""

    id: int = Field(description="Group ID")
    name: str = Field(description="Group name")
    owner_identity_id: Optional[int] = Field(
        None, description="Identity ID of group owner"
    )
    owner_group_id: Optional[int] = Field(None, description="Group ID of group owner")
    approval_mode: str = Field(
        default="any", description="Approval mode: 'any', 'all', or 'threshold'"
    )
    approval_threshold: Optional[int] = Field(
        None, description="Approval threshold for threshold mode"
    )
    provider: Optional[str] = Field(None, description="Provider type")
    provider_group_id: Optional[str] = Field(
        None, description="Provider group identifier"
    )
    sync_enabled: bool = Field(default=False, description="Whether sync is enabled")
    member_count: Optional[int] = Field(None, description="Number of members in group")
    pending_request_count: Optional[int] = Field(
        None, description="Number of pending access requests"
    )


class AccessRequestDTO(ImmutableModel):
    """Immutable Access Request data transfer object."""

    id: int = Field(description="Request ID")
    group_id: int = Field(description="Group ID")
    requester_id: int = Field(description="Requester identity ID")
    status: str = Field(description="Request status")
    reason: str = Field(description="Reason for access request")
    expires_at: Optional[datetime] = Field(
        None, description="Optional expiration datetime"
    )
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")


class GroupMemberDTO(ImmutableModel):
    """Immutable Group Member data transfer object."""

    id: int = Field(description="Membership ID")
    group_id: int = Field(description="Group ID")
    identity_id: int = Field(description="Member identity ID")
    expires_at: Optional[datetime] = Field(
        None, description="Optional expiration datetime"
    )
    added_by: Optional[int] = Field(None, description="Identity ID who added member")
    added_at: datetime = Field(description="Addition timestamp")


class ListGroupsResponse(ImmutableModel):
    """Response model for listing groups."""

    groups: list[GroupDTO] = Field(description="List of groups")
    total: int = Field(ge=0, description="Total number of groups")


class ListRequestsResponse(ImmutableModel):
    """Response model for listing access requests."""

    requests: list[AccessRequestDTO] = Field(description="List of requests")
    total: int = Field(ge=0, description="Total number of requests")


class ListMembersResponse(ImmutableModel):
    """Response model for listing group members."""

    members: list[GroupMemberDTO] = Field(description="List of members")
    total: int = Field(ge=0, description="Total number of members")


class BulkApproveResult(ImmutableModel):
    """Result model for bulk approve operations."""

    succeeded: int = Field(ge=0, description="Number of successfully approved requests")
    failed: int = Field(ge=0, description="Number of failed approvals")
    errors: Optional[list[dict]] = Field(
        None, description="List of error details for failed requests"
    )


__all__ = [
    "UpdateGroupRequest",
    "CreateAccessRequestRequest",
    "AddGroupMemberRequest",
    "ApproveOrDenyRequestRequest",
    "BulkApproveRequestsRequest",
    "GroupDTO",
    "AccessRequestDTO",
    "GroupMemberDTO",
    "ListGroupsResponse",
    "ListRequestsResponse",
    "ListMembersResponse",
    "BulkApproveResult",
]
