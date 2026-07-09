"""Diagrams module models - drawings, versioning, sharing, collaboration."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
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


class DgDiagram(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Drawing/diagram entity (ported from IceCharts diagram)."""

    __tablename__ = "dg_diagrams"

    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    owner_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_public = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
        comment="Visible without login",
    )
    is_template = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
        comment="Can be used as template for new diagrams",
    )
    status = Column(
        String(20),
        default="draft",
        nullable=False,
        server_default="draft",
        comment="draft, active, archived",
    )
    tags = Column(JSON, nullable=True, comment="JSON array of string tags")
    thumbnail_url = Column(String(1000), nullable=True)


class DgDiagramVersion(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Versioned snapshot of diagram content (nodes + edges)."""

    __tablename__ = "dg_diagram_versions"

    diagram_id = Column(
        Integer,
        ForeignKey("dg_diagrams.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number = Column(Integer, nullable=False)
    created_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    content_json = Column(
        JSON,
        nullable=False,
        comment="Complete diagram content: nodes, edges, metadata",
    )
    change_summary = Column(Text, nullable=True, comment="Description of changes")
    storage_provider_id = Column(
        Integer,
        ForeignKey("dg_storage_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    storage_path = Column(
        String(1000),
        nullable=True,
        comment="External storage path (S3, GCS, etc.)",
    )

    __table_args__ = (
        UniqueConstraint("diagram_id", "version_number", name="uq_dg_version"),
    )


class DgShape(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Persisted node/shape within a diagram."""

    __tablename__ = "dg_shapes"

    diagram_id = Column(
        Integer,
        ForeignKey("dg_diagrams.id", ondelete="CASCADE"),
        nullable=False,
    )
    shape_type = Column(
        String(100), nullable=True, comment="Shape type: box, circle, etc."
    )
    x_position = Column(Integer, nullable=True)
    y_position = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    style_json = Column(
        JSON, nullable=True, comment="Style properties: color, stroke, etc."
    )
    elder_entity_id = Column(
        Integer,
        index=True,
        nullable=True,
        comment="Soft reference to infrastructure entity (no FK)",
    )


class DgConnector(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Edge/connector between shapes."""

    __tablename__ = "dg_connectors"

    diagram_id = Column(
        Integer,
        ForeignKey("dg_diagrams.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_shape_id = Column(
        Integer,
        ForeignKey("dg_shapes.id", ondelete="CASCADE"),
        nullable=True,
    )
    end_shape_id = Column(
        Integer,
        ForeignKey("dg_shapes.id", ondelete="CASCADE"),
        nullable=True,
    )
    connector_type = Column(
        String(100),
        nullable=True,
        comment="Connector type: line, arrow, curve, etc.",
    )
    style_json = Column(JSON, nullable=True, comment="Style properties")


class DgShapeMetadata(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """1:1 metadata for shapes (label, description, custom data)."""

    __tablename__ = "dg_shape_metadata"

    shape_id = Column(
        Integer,
        ForeignKey("dg_shapes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    diagram_id = Column(
        Integer,
        ForeignKey("dg_diagrams.id", ondelete="CASCADE"),
        nullable=True,
    )
    label = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    custom_data = Column(JSON, nullable=True, comment="Custom metadata object")


class DgNodeMetadata(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Metadata for nodes in the content JSON (not persisted shapes)."""

    __tablename__ = "dg_node_metadata"

    diagram_id = Column(
        Integer,
        ForeignKey("dg_diagrams.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id = Column(
        String(200),
        nullable=False,
        comment="Node ID as it appears in diagram content_json",
    )
    comments = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    updated_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_dg_node_metadata_diagram_node", "diagram_id", "node_id"),
    )


class DgShare(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Diagram sharing — diagram-level permissions."""

    __tablename__ = "dg_shares"

    diagram_id = Column(
        Integer,
        ForeignKey("dg_diagrams.id", ondelete="CASCADE"),
        nullable=False,
    )
    shared_with_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    shared_with_group_id = Column(
        Integer,
        nullable=True,
        comment="Soft reference to groups (no FK to keep loose coupling)",
    )
    shared_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    permission = Column(
        String(20),
        default="viewer",
        nullable=False,
        server_default="viewer",
        comment="viewer, editor, admin",
    )
    expires_at = Column(DateTime(timezone=True), nullable=True)
    share_token = Column(String(64), unique=True, nullable=True)
    is_public = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
    )


class DgCollection(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Collection (folder) for grouping diagrams."""

    __tablename__ = "dg_collections"

    name = Column(String(500), nullable=False)
    owner_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    thumbnail_url = Column(String(1000), nullable=True)
    is_public = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
    )
    share_token = Column(String(64), unique=True, nullable=True)
    share_mode = Column(
        String(20),
        default="private",
        nullable=False,
        server_default="private",
        comment="private, restricted, public",
    )


class DgCollectionItem(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """M:N membership of diagrams in collections."""

    __tablename__ = "dg_collection_items"

    collection_id = Column(
        Integer,
        ForeignKey("dg_collections.id", ondelete="CASCADE"),
        nullable=False,
    )
    diagram_id = Column(
        Integer,
        ForeignKey("dg_diagrams.id", ondelete="CASCADE"),
        nullable=False,
    )
    added_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    order_index = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("collection_id", "diagram_id", name="uq_dg_collection_item"),
    )


class DgCollectionShare(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Collection-level sharing — permissions on entire collection."""

    __tablename__ = "dg_collection_shares"

    collection_id = Column(
        Integer,
        ForeignKey("dg_collections.id", ondelete="CASCADE"),
        nullable=False,
    )
    shared_with_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    shared_with_group_id = Column(
        Integer,
        nullable=True,
        comment="Soft reference to groups (no FK)",
    )
    permission = Column(
        String(20),
        default="viewer",
        nullable=False,
        server_default="viewer",
        comment="viewer, editor, admin",
    )
    created_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )


class DgShareAnalytics(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Access analytics for shares (tracking share usage)."""

    __tablename__ = "dg_share_analytics"

    share_type = Column(
        String(20),
        nullable=False,
        comment="diagram or collection",
    )
    share_id = Column(
        Integer,
        nullable=False,
        comment="Polymorphic ID (no FK) — diagram_id or collection_id",
    )
    share_token = Column(String(64), nullable=True)
    accessed_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    access_ip = Column(String(45), nullable=True, comment="IPv4 or IPv6")
    user_agent = Column(String(500), nullable=True)
    accessed_at = Column(DateTime(timezone=True), nullable=True)


class DgComment(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Comments on diagrams (spatial annotations)."""

    __tablename__ = "dg_comments"

    diagram_id = Column(
        Integer,
        ForeignKey("dg_diagrams.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    text_content = Column(Text, nullable=False)
    x_position = Column(Integer, nullable=True, comment="Canvas x coordinate")
    y_position = Column(Integer, nullable=True, comment="Canvas y coordinate")
    is_resolved = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
    )


class DgCommentReply(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Replies to comments."""

    __tablename__ = "dg_comment_replies"

    comment_id = Column(
        Integer,
        ForeignKey("dg_comments.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    text_content = Column(Text, nullable=False)


class DgShapeLibrary(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Reusable shape library (template collection)."""

    __tablename__ = "dg_shape_libraries"

    name = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    is_public = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
    )
    owner_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="RESTRICT"),
        nullable=False,
    )


class DgLibraryShape(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Individual shape within a library."""

    __tablename__ = "dg_library_shapes"

    library_id = Column(
        Integer,
        ForeignKey("dg_shape_libraries.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(500), nullable=True)
    shape_type = Column(String(100), nullable=True)
    default_width = Column(Integer, nullable=True)
    default_height = Column(Integer, nullable=True)
    svg_content = Column(Text, nullable=True, comment="SVG markup for rendering")
    shape_meta = Column(JSON, nullable=True, comment="Shape metadata/properties")


class DgTemplate(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Diagram template (starter template for new diagrams)."""

    __tablename__ = "dg_templates"

    name = Column(String(500), nullable=False)
    content = Column(JSON, nullable=False, comment="Template diagram content")
    category = Column(
        String(100),
        default="custom",
        nullable=False,
        server_default="custom",
    )
    thumbnail_url = Column(String(1000), nullable=True)
    is_public = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
    )
    created_by_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )


class DgStorageProvider(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """External storage provider configuration (S3, GCS, MinIO, etc.)."""

    __tablename__ = "dg_storage_providers"

    name = Column(String(255), nullable=False)
    provider_type = Column(
        String(50),
        nullable=False,
        comment="minio, s3, gcs, onedrive, googledrive",
    )
    config_json = Column(JSON, nullable=False, comment="Connection configuration")
    storage_config = Column(JSON, nullable=True, comment="Storage-specific settings")
    owner_identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
        server_default="1",
    )
    is_system_default = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="0",
        comment="Default provider for this tenant",
    )


class DgCollaborationSession(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Active collaboration session for real-time co-editing."""

    __tablename__ = "dg_collaboration_sessions"

    diagram_id = Column(
        Integer,
        ForeignKey("dg_diagrams.id", ondelete="CASCADE"),
        nullable=False,
    )
    identity_id = Column(
        Integer,
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id = Column(String(128), nullable=True, comment="WebSocket session ID")
    socket_id = Column(String(128), nullable=True, comment="Pusher/WebSocket socket ID")
    cursor_position_json = Column(JSON, nullable=True, comment="Cursor x,y coordinates")
    last_cursor_x = Column(Integer, nullable=True)
    last_cursor_y = Column(Integer, nullable=True)
    permission = Column(
        String(20),
        default="viewer",
        nullable=False,
        server_default="viewer",
        comment="viewer, editor, admin",
    )
    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
        server_default="1",
    )
    joined_at = Column(DateTime(timezone=True), nullable=True)
    left_at = Column(DateTime(timezone=True), nullable=True)
    last_active_at = Column(DateTime(timezone=True), nullable=True)
