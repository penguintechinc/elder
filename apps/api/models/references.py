"""Cross-reference registry model for tracking resource linkage.

Supports wiki-like linking, embedded references, and binding relationships
between any two trackable resources identified by their module, type, and id.
"""

# flake8: noqa: E501

from sqlalchemy import JSON, Column, Index, String

from apps.api.models.base import Base, IDMixin, TenantScopedMixin, TimestampMixin


class Reference(Base, IDMixin, TimestampMixin, TenantScopedMixin):
    """Cross-reference tracking for relationships between resources.

    Enables tracking of all inbound and outbound references between resources
    without requiring explicit foreign keys. References are typed (link, embed,
    binding) and include optional context (e.g., link anchor, embed params).

    Tenant-scoped: all references belong to a single tenant. Query indexes:
    - Backlink lookups (find all refs pointing to a target)
    - Outbound lookups (find all refs from a source)
    """

    __tablename__ = "references"

    # Source resource identification
    source_module = Column(String(100), nullable=False)
    source_type = Column(String(100), nullable=False)
    source_id = Column(String(255), nullable=False)

    # Target resource identification
    target_module = Column(String(100), nullable=False)
    target_type = Column(String(100), nullable=False)
    target_id = Column(String(255), nullable=False)

    # Reference type classifier
    ref_type = Column(String(50), nullable=False, default="link")

    # Optional context (e.g., link text, embed params, binding metadata)
    context = Column(JSON, nullable=True)

    # Creator (user UUID who created the reference)
    created_by = Column(String(36), nullable=True)

    # Indexes for efficient queries
    __table_args__ = (
        # Backlink reverse query: find all refs pointing to a target
        Index(
            "ix_references_target",
            "target_module",
            "target_type",
            "target_id",
            "tenant_id",
        ),
        # Outbound query: find all refs from a source
        Index(
            "ix_references_source",
            "source_module",
            "source_type",
            "source_id",
        ),
        # Tenant isolation
        Index("ix_references_tenant", "tenant_id"),
    )
