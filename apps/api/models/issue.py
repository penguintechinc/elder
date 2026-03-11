"""GitHub-style issues system for Elder enterprise features."""

# flake8: noqa: E501


import enum
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, relationship

from apps.api.models.base import Base, IDMixin, TimestampMixin
from shared.utils.village_id import generate_village_id


class IssueStatus(enum.Enum):
    """Issue status values."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"
    RESOLVED = "resolved"


class IssuePriority(enum.Enum):
    """Issue priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IssueType(enum.Enum):
    """Issue type categories."""

    OPERATIONS = "operations"
    CODE = "code"
    CONFIG = "config"
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    PROCESS = "process"
    APPROVAL = "approval"
    FEATURE = "feature"
    BUG = "bug"
    OTHER = "other"


class IssueLinkType(enum.Enum):
    """Types of links between issues and entities."""

    RELATED = "related"  # General relation
    BLOCKS = "blocks"  # This issue blocks the entity
    BLOCKED_BY = "blocked_by"  # This issue is blocked by the entity
    FIXES = "fixes"  # This issue fixes something in the entity


# Association table for issue labels (many-to-many)
issue_label_assignments = Table(
    "issue_label_assignments",
    Base.metadata,
    Column(
        "issue_id",
        Integer,
        ForeignKey("issues.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "label_id",
        Integer,
        ForeignKey("issue_labels.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Issue(Base, IDMixin, TimestampMixin):
    """
    GitHub-style issue model for tracking problems and tasks.

    Issues can be attached to entities or organizations.
    Supports status, priority, assignments, labels, comments, and entity links.

    Permission Requirements:
    - Maintainer: Full CRUD
    - Operator: Create, close (not delete/edit), add comments/labels
    - Viewer: View, create new issues, add comments
    """

    __tablename__ = "issues"

    # Resource association (entity or organization)
    resource_type = Column(
        String(20),
        nullable=False,
        index=True,
        comment="Type of resource (entity or organization)",
    )

    resource_id = Column(
        Integer,
        nullable=False,
        index=True,
        comment="ID of the entity or organization",
    )

    # Core fields
    title = Column(
        String(255),
        nullable=False,
        index=True,
        comment="Issue title",
    )

    description = Column(
        Text,
        nullable=True,
        comment="Detailed issue description (supports Markdown)",
    )

    # Status and priority
    status = Column(
        Enum(IssueStatus),
        nullable=False,
        default=IssueStatus.OPEN,
        index=True,
        comment="Current issue status",
    )

    priority = Column(
        Enum(IssuePriority),
        nullable=False,
        default=IssuePriority.MEDIUM,
        index=True,
        comment="Issue priority level",
    )

    issue_type = Column(
        Enum(IssueType),
        nullable=False,
        default=IssueType.OTHER,
        index=True,
        comment="Issue type category",
    )

    # Incident tracking
    is_incident = Column(
        Integer,
        nullable=False,
        default=0,
        index=True,
        comment="Whether this issue is marked as an incident (0=no, 1=yes)",
    )

    # User relationships
    reporter_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who created this issue",
    )

    assignee_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User assigned to this issue",
    )

    organization_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
        comment="Organization this issue belongs to",
    )

    # Closure tracking
    due_date = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Issue due date",
    )

    closed_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the issue was closed",
    )

    closed_by_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who closed this issue",
    )

    village_id = Column(
        String(32),
        unique=True,
        nullable=True,
        index=True,
        default=generate_village_id,
        comment="Unique cross-system reference ID",
    )

    # Relationships
    reporter: Mapped[Optional["Identity"]] = relationship(
        "Identity",
        foreign_keys=[reporter_id],
        backref="reported_issues",
    )

    assignee: Mapped[Optional["Identity"]] = relationship(
        "Identity",
        foreign_keys=[assignee_id],
        backref="assigned_issues",
    )

    closed_by: Mapped[Optional["Identity"]] = relationship(
        "Identity",
        foreign_keys=[closed_by_id],
        backref="closed_issues",
    )

    labels: Mapped[List["IssueLabel"]] = relationship(
        "IssueLabel",
        secondary=issue_label_assignments,
        back_populates="issues",
    )

    comments: Mapped[List["IssueComment"]] = relationship(
        "IssueComment",
        back_populates="issue",
        cascade="all, delete-orphan",
        order_by="IssueComment.created_at",
    )

    entity_links: Mapped[List["IssueEntityLink"]] = relationship(
        "IssueEntityLink",
        back_populates="issue",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        """String representation of issue."""
        return f"<Issue(id={self.id}, title='{self.title}', type={self.issue_type.value}, status={self.status.value}, priority={self.priority.value})>"

    def close(self, closed_by_id: int) -> None:
        """
        Close the issue.

        Args:
            closed_by_id: Identity ID of user closing the issue
        """
        self.status = IssueStatus.CLOSED
        self.closed_at = datetime.now(timezone.utc)
        self.closed_by_id = closed_by_id

    def reopen(self) -> None:
        """Reopen a closed issue."""
        self.status = IssueStatus.OPEN
        self.closed_at = None
        self.closed_by_id = None

    def is_overdue(self) -> bool:
        """Check if issue is past due date."""
        if not self.due_date:
            return False
        if self.status in [IssueStatus.CLOSED, IssueStatus.RESOLVED]:
            return False
        return datetime.now(timezone.utc) > self.due_date


class IssueLabel(Base, IDMixin, TimestampMixin):
    """
    Label for categorizing issues.

    Labels are global across all issues (like GitHub labels).
    """

    __tablename__ = "issue_labels"

    name = Column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
        comment="Label name (e.g., 'bug', 'enhancement')",
    )

    color = Column(
        String(7),
        nullable=False,
        default="#808080",
        comment="Hex color code for label (e.g., '#ff0000')",
    )

    description = Column(
        Text,
        nullable=True,
        comment="Label description",
    )

    # Relationships
    issues: Mapped[List["Issue"]] = relationship(
        "Issue",
        secondary=issue_label_assignments,
        back_populates="labels",
    )

    def __repr__(self) -> str:
        """String representation of label."""
        return f"<IssueLabel(id={self.id}, name='{self.name}', color='{self.color}')>"


class IssueComment(Base, IDMixin, TimestampMixin):
    """
    Comment on an issue.

    Comments support Markdown formatting.
    """

    __tablename__ = "issue_comments"

    issue_id = Column(
        Integer,
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Issue this comment belongs to",
    )

    author_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who wrote this comment",
    )

    content = Column(
        Text,
        nullable=False,
        comment="Comment content (supports Markdown)",
    )

    # Relationships
    issue: Mapped["Issue"] = relationship(
        "Issue",
        back_populates="comments",
    )

    author: Mapped[Optional["Identity"]] = relationship(
        "Identity",
        backref="issue_comments",
    )

    def __repr__(self) -> str:
        """String representation of comment."""
        content_preview = (
            self.content[:50] + "..." if len(self.content) > 50 else self.content
        )
        return f"<IssueComment(id={self.id}, issue_id={self.issue_id}, content='{content_preview}')>"


class IssueEntityLink(Base, IDMixin, TimestampMixin):
    """
    Link between an issue and an entity.

    Allows tracking which entities are related to, blocked by, or fixed by issues.
    """

    __tablename__ = "issue_entity_links"

    issue_id = Column(
        Integer,
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Issue",
    )

    entity_id = Column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Entity",
    )

    link_type = Column(
        Enum(IssueLinkType),
        nullable=False,
        default=IssueLinkType.RELATED,
        comment="Type of relationship",
    )

    # Relationships
    issue: Mapped["Issue"] = relationship(
        "Issue",
        back_populates="entity_links",
    )

    entity: Mapped["Entity"] = relationship(
        "Entity",
        backref="issue_links",
    )

    def __repr__(self) -> str:
        """String representation of link."""
        return f"<IssueEntityLink(id={self.id}, issue_id={self.issue_id}, entity_id={self.entity_id}, type={self.link_type.value})>"


# Helper functions for recursive issue queries


def get_organization_issues_recursive(organization_id: int) -> List[Issue]:
    """
    Get all issues for an organization recursively.

    Includes:
    - Issues directly on the organization
    - Issues on all child organizations (recursive)
    - Issues on all entities within the organization and children

    Args:
        organization_id: Organization ID

    Returns:
        List of all issues in org hierarchy
    """
    from apps.api.models import Organization
    from shared.database import db

    # Get organization and all children recursively
    org = db.session.get(Organization, organization_id)
    if not org:
        return []

    # Get all child orgs recursively
    child_orgs = org.get_all_children(recursive=True)
    org_ids = [org.id] + [child.id for child in child_orgs]

    # Get issues directly on organizations
    org_issues = (
        db.session.query(Issue)
        .filter(Issue.resource_type == "organization", Issue.resource_id.in_(org_ids))
        .all()
    )

    # Get all entities in these organizations
    from apps.api.models import Entity

    entity_ids = (
        db.session.query(Entity.id).filter(Entity.organization_id.in_(org_ids)).all()
    )
    entity_id_list = [eid[0] for eid in entity_ids]

    # Get issues on entities
    entity_issues = (
        db.session.query(Issue)
        .filter(Issue.resource_type == "entity", Issue.resource_id.in_(entity_id_list))
        .all()
    )

    # Combine and deduplicate
    all_issues = org_issues + entity_issues
    seen = set()
    unique_issues = []
    for issue in all_issues:
        if issue.id not in seen:
            seen.add(issue.id)
            unique_issues.append(issue)

    return unique_issues


def get_entity_issues(entity_id: int) -> List[Issue]:
    """
    Get all issues directly attached to an entity.

    Does NOT include issues from parent organization (only direct issues).

    Args:
        entity_id: Entity ID

    Returns:
        List of issues for this entity
    """
    from shared.database import db

    return (
        db.session.query(Issue)
        .filter(Issue.resource_type == "entity", Issue.resource_id == entity_id)
        .all()
    )
