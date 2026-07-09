"""Pages module models - wiki-style documentation, collections, references."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from apps.api.models.base import (
    Base,
    IDMixin,
    TenantScopedMixin,
    TimestampMixin,
    VillageIDMixin,
)


class PgPage(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Wiki-style page (ported from Ruffled Pages)."""

    __tablename__ = "pg_pages"

    title = Column(String(500), nullable=False)
    # Per-tenant slug uniqueness — internal URL /pages/<slug>
    slug = Column(String(500), nullable=False)
    body_html = Column(
        Text, nullable=False, comment="TipTap HTML output, bleach-sanitized"
    )
    status = Column(
        String(20),
        default="draft",
        nullable=False,
        server_default="draft",
        comment="draft, published, archived",
    )
    visibility = Column(
        String(20),
        default="authenticated",
        nullable=False,
        server_default="authenticated",
        comment="public, authenticated, roles, users",
    )
    visibility_roles = Column(
        JSON,
        nullable=True,
        comment="Allowed roles for 'roles' visibility",
    )
    visibility_users = Column(
        JSON,
        nullable=True,
        comment="Allowed user IDs (from identities.id) for 'users' visibility",
    )
    is_public = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
        comment="Visible without login",
    )
    author_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Placeholder for embedding — can be filled in later if pgvector is added
    embedding = Column(
        Text, nullable=True, comment="Vector embedding placeholder (TBD)"
    )
    published_at = Column(DateTime(timezone=True), nullable=True)

    # Per-tenant slug uniqueness — no cross-tenant collisions
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_pg_slug_tenant"),)


class PgPageCollection(Base):
    """M:N relationship between pages and document collections."""

    __tablename__ = "pg_page_collections"

    pg_page_id = Column(
        Integer,
        ForeignKey("pg_pages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    doc_collection_id = Column(
        Integer,
        ForeignKey("doc_collections.id", ondelete="CASCADE"),
        primary_key=True,
    )
