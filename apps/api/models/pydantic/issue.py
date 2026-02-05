"""
Pydantic 2 models for Issue domain objects.

Provides validated Pydantic 2 equivalents of Issue dataclasses:
- IssueDTO: Immutable frozen DTO for API responses
- CreateIssueRequest: Request validation with security hardening
- UpdateIssueRequest: Flexible update request with all optional fields
- IssueStatus: Literal type for valid issue statuses
- IssuePriority: Literal type for valid issue priorities
- IssueSeverity: Literal type for valid issue severities
"""

# flake8: noqa: E501


from datetime import datetime
from typing import Literal, Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field

# Issue status types
IssueStatus = Literal["open", "in_progress", "resolved", "closed", "reopened"]
"""
Valid issue status values.

- open: Issue is newly created and awaiting action
- in_progress: Issue is currently being worked on
- resolved: Issue has been resolved but not yet closed
- closed: Issue is completed and closed
- reopened: Previously closed issue has been reopened
"""

# Issue priority types
IssuePriority = Literal["low", "medium", "high", "critical"]
"""
Valid issue priority values.

- low: Minor issues with no urgent timeline
- medium: Standard priority issues
- high: Important issues requiring timely attention
- critical: Urgent issues blocking operations or causing major impact
"""

# Issue severity types
IssueSeverity = Literal["minor", "moderate", "major", "critical"]
"""
Valid issue severity values.

- minor: Minimal impact on operations
- moderate: Noticeable impact but not critical
- major: Significant impact on operations or features
- critical: Severe impact, system/feature down or major data loss risk
"""


class IssueDTO(ImmutableModel):
    """
    Immutable Issue data transfer object.

    Represents a complete Issue record with all fields. Used for API responses
    and data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        title: Issue title or summary (1-255 chars)
        description: Optional detailed issue description
        status: Current issue status (open, in_progress, resolved, closed, reopened)
        priority: Issue priority level (low, medium, high, critical)
        issue_type: Type classification of the issue (e.g., 'bug', 'feature', 'task', 'other')
        reporter_id: Identity ID of the issue reporter
        assignee_id: Optional Identity ID of the assigned person
        organization_id: Optional associated organization ID
        is_incident: Boolean flag indicating if this is an incident (0=false, 1=true)
        closed_at: Optional timestamp when issue was closed
        created_at: Creation timestamp
        updated_at: Last update timestamp
        tenant_id: Optional tenant ID for multi-tenant scenarios
        village_id: Optional unique hierarchical identifier
    """

    id: int
    title: str
    description: Optional[str] = None
    status: str
    priority: str
    issue_type: str
    reporter_id: int
    assignee_id: Optional[int] = None
    organization_id: Optional[int] = None
    is_incident: int
    closed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    tenant_id: Optional[int] = None
    village_id: Optional[str] = None
    parent_issue_id: Optional[int] = None


class CreateIssueRequest(RequestModel):
    """
    Request to create a new Issue.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        title: Issue title or summary (required, 1-255 chars)
        reporter_id: Identity ID of the reporter (required, must be >= 1)
        description: Optional detailed issue description
        status: Issue status (default: "open")
        priority: Issue priority level (default: "medium")
        issue_type: Type classification (default: "other")
        assignee_id: Optional Identity ID to assign the issue
        organization_id: Optional organization this issue belongs to
        is_incident: Flag indicating if this is an incident (default: 0)
    """

    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Issue title or summary",
    )
    reporter_id: int = Field(
        ...,
        ge=1,
        description="Identity ID of the reporter (must be positive)",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional detailed issue description",
    )
    status: str = Field(
        default="open",
        description="Issue status",
    )
    priority: str = Field(
        default="medium",
        description="Issue priority level",
    )
    issue_type: str = Field(
        default="other",
        description="Type classification of the issue",
    )
    assignee_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional Identity ID to assign the issue",
    )
    organization_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional organization this issue belongs to",
    )
    is_incident: int = Field(
        default=0,
        description="Flag indicating if this is an incident",
    )
    parent_issue_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional parent issue ID for sub-tasks",
    )


class UpdateIssueRequest(RequestModel):
    """
    Request to update an existing Issue.

    All fields are optional to support partial updates. Uses RequestModel
    to reject unknown fields and prevent injection attacks.

    Attributes:
        title: Issue title (optional)
        description: Detailed description (optional)
        status: Issue status (optional)
        priority: Priority level (optional)
        issue_type: Type classification (optional)
        assignee_id: Assigned person ID (optional)
        is_incident: Incident flag (optional)
    """

    title: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Issue title",
    )
    description: Optional[str] = Field(
        default=None,
        description="Detailed issue description",
    )
    status: Optional[str] = Field(
        default=None,
        description="Issue status",
    )
    priority: Optional[str] = Field(
        default=None,
        description="Priority level",
    )
    issue_type: Optional[str] = Field(
        default=None,
        description="Type classification",
    )
    assignee_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Assigned person ID",
    )
    is_incident: Optional[int] = Field(
        default=None,
        description="Incident flag",
    )
    parent_issue_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional parent issue ID for sub-tasks",
    )
