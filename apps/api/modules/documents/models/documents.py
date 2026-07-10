"""Documents module models - knowledge base, collections, versions."""

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


class DocDocument(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Knowledge base document (ported from Ruffled KbArticle)."""

    __tablename__ = "doc_documents"

    title = Column(String(500), nullable=False)
    # Globally unique slug for public URL /public/<slug> — no tenant component.
    slug = Column(String(500), nullable=False)
    body_html = Column(Text, nullable=False)
    body_text = Column(Text, nullable=True, comment="Plaintext extraction for search")
    body_markdown = Column(
        Text, nullable=True, comment="Raw markdown source for editing round-trip"
    )
    category = Column(String(100), nullable=True)
    tags = Column(JSON, nullable=True, comment="JSON array of tags")
    status = Column(
        String(20),
        default="draft",
        nullable=False,
        server_default="draft",
        comment="draft, published, archived",
    )
    is_public = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
        comment="Visible without login",
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
        comment="Allowed user UUIDs for 'users' visibility",
    )
    author_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    published_at = Column(DateTime(timezone=True), nullable=True)

    # Globally unique slug — public resolution unambiguous across tenants.
    __table_args__ = (UniqueConstraint("slug", name="uq_doc_slug"),)


class DocCollection(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Hierarchical collection (folder) for organizing documents."""

    __tablename__ = "doc_collections"

    village_id = Column(String(32), nullable=True, unique=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=True, comment="URL-safe name, optional")
    description = Column(Text, nullable=True)
    # Self-referential FK for hierarchical tree (parent_id null → root collection).
    parent_id = Column(
        Integer,
        ForeignKey("doc_collections.id", ondelete="SET NULL"),
        nullable=True,
    )


class DocDocumentCollection(Base):
    """M:N relationship between documents and collections."""

    __tablename__ = "doc_document_collections"

    doc_document_id = Column(
        Integer,
        ForeignKey("doc_documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    doc_collection_id = Column(
        Integer,
        ForeignKey("doc_collections.id", ondelete="CASCADE"),
        primary_key=True,
    )


class DocVersion(Base, IDMixin, TimestampMixin):
    """Immutable version snapshot of a document."""

    __tablename__ = "doc_versions"

    doc_document_id = Column(
        Integer,
        ForeignKey("doc_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number = Column(
        Integer, nullable=False, comment="Monotonic version 1, 2, 3..."
    )
    title = Column(String(500), nullable=False)
    body_html = Column(Text, nullable=False)
    body_text = Column(Text, nullable=True, comment="Plaintext extraction for search")
    body_markdown = Column(
        Text, nullable=True, comment="Raw markdown source for editing round-trip"
    )
    author_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Unique constraint: one version number per document.
    __table_args__ = (
        UniqueConstraint("doc_document_id", "version_number", name="uq_doc_version"),
    )
