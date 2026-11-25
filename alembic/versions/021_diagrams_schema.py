"""Create diagrams module schema — 18 tables for diagram drawing, versioning, sharing.

Revision ID: 021
Revises: 020
Create Date: 2026-07-09

Phase 4a: Author diagrams module schema from IceCharts.
- dg_diagrams: drawing/diagram entity
- dg_diagram_versions: versioned snapshots of diagram content
- dg_shapes: persisted nodes/shapes
- dg_connectors: edges/connections between shapes
- dg_shape_metadata: 1:1 shape metadata
- dg_node_metadata: metadata for JSON nodes
- dg_shares: diagram-level sharing
- dg_collections: folder/collection for grouping diagrams
- dg_collection_items: M:N diagram-collection membership
- dg_collection_shares: collection-level sharing
- dg_share_analytics: access tracking for shares
- dg_comments: spatial comments on diagrams
- dg_comment_replies: replies to comments
- dg_shape_libraries: reusable shape template libraries
- dg_library_shapes: individual shapes within libraries
- dg_templates: diagram templates for new diagrams
- dg_storage_providers: external storage configuration
- dg_collaboration_sessions: active co-editing sessions

Tenant ref: Integer FK to tenants.id (not uuid).
User/actor refs: *_identity_id Integer FK to identities.id.
village_id: VillageIDMixin on dg_diagrams, dg_collections, dg_templates only.
elder_entity_id: soft reference (no FK) on dg_shapes for infrastructure loose coupling.

Table creation order respects FK dependencies:
1. dg_diagrams (root)
2. dg_storage_providers (no internal FKs)
3. dg_diagram_versions (FK to dg_diagrams, dg_storage_providers)
4. dg_shapes (FK to dg_diagrams)
5. dg_connectors (FK to dg_diagrams, dg_shapes)
6-18. All other tables (safe after shapes/connectors created)
"""

import sqlalchemy as sa

from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade():
    """Create 18 diagrams module tables with FKs, indexes, unique constraints."""

    # 1. dg_diagrams — primary drawing/diagram entity (root, no internal FKs)
    op.create_table(
        "dg_diagrams",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_identity_id", sa.Integer(), nullable=False),
        sa.Column("created_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_identity_id", sa.Integer(), nullable=True),
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="Visible without login",
        ),
        sa.Column(
            "is_template",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="Can be used as template for new diagrams",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="draft",
            comment="draft, active, archived",
        ),
        sa.Column("tags", sa.JSON(), nullable=True, comment="JSON array of tags"),
        sa.Column("thumbnail_url", sa.String(1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dg_diagrams_tenant_id", "dg_diagrams", ["tenant_id"])
    op.create_index(
        "ix_dg_diagrams_owner_identity_id", "dg_diagrams", ["owner_identity_id"]
    )
    op.create_index("ix_dg_diagrams_status", "dg_diagrams", ["status"])

    # 2. dg_storage_providers — external storage configuration (no internal FKs)
    op.create_table(
        "dg_storage_providers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "provider_type",
            sa.String(50),
            nullable=False,
            comment="minio, s3, gcs, onedrive, googledrive",
        ),
        sa.Column(
            "config_json",
            sa.JSON(),
            nullable=False,
            comment="Connection configuration",
        ),
        sa.Column("storage_config", sa.JSON(), nullable=True),
        sa.Column("owner_identity_id", sa.Integer(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "is_system_default",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="Default provider for this tenant",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dg_storage_providers_tenant_id",
        "dg_storage_providers",
        ["tenant_id"],
    )

    # 3. dg_diagram_versions — versioned snapshots (FK to dg_diagrams, dg_storage_providers)
    op.create_table(
        "dg_diagram_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("diagram_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("created_by_identity_id", sa.Integer(), nullable=True),
        sa.Column(
            "content_json",
            sa.JSON(),
            nullable=False,
            comment="Complete diagram content: nodes, edges, metadata",
        ),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("storage_provider_id", sa.Integer(), nullable=True),
        sa.Column(
            "storage_path",
            sa.String(1000),
            nullable=True,
            comment="External storage path (S3, GCS, etc.)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["diagram_id"], ["dg_diagrams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["storage_provider_id"],
            ["dg_storage_providers.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("diagram_id", "version_number", name="uq_dg_version"),
    )
    op.create_index(
        "ix_dg_diagram_versions_tenant_id", "dg_diagram_versions", ["tenant_id"]
    )
    op.create_index(
        "ix_dg_diagram_versions_diagram_id", "dg_diagram_versions", ["diagram_id"]
    )

    # 4. dg_shapes — persisted nodes/shapes within diagrams
    op.create_table(
        "dg_shapes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("diagram_id", sa.Integer(), nullable=False),
        sa.Column("shape_type", sa.String(100), nullable=True),
        sa.Column("x_position", sa.Integer(), nullable=True),
        sa.Column("y_position", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("style_json", sa.JSON(), nullable=True),
        sa.Column(
            "elder_entity_id",
            sa.Integer(),
            index=True,
            nullable=True,
            comment="Soft reference to infrastructure entity (no FK)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["diagram_id"], ["dg_diagrams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dg_shapes_tenant_id", "dg_shapes", ["tenant_id"])
    op.create_index("ix_dg_shapes_diagram_id", "dg_shapes", ["diagram_id"])

    # 5. dg_connectors — edges/connections between shapes
    op.create_table(
        "dg_connectors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("diagram_id", sa.Integer(), nullable=False),
        sa.Column("start_shape_id", sa.Integer(), nullable=True),
        sa.Column("end_shape_id", sa.Integer(), nullable=True),
        sa.Column("connector_type", sa.String(100), nullable=True),
        sa.Column("style_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["diagram_id"], ["dg_diagrams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["start_shape_id"], ["dg_shapes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["end_shape_id"], ["dg_shapes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dg_connectors_tenant_id", "dg_connectors", ["tenant_id"])
    op.create_index("ix_dg_connectors_diagram_id", "dg_connectors", ["diagram_id"])

    # 6. dg_shape_metadata — 1:1 metadata for shapes
    op.create_table(
        "dg_shape_metadata",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("shape_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("diagram_id", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("custom_data", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shape_id"], ["dg_shapes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["diagram_id"], ["dg_diagrams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dg_shape_metadata_tenant_id", "dg_shape_metadata", ["tenant_id"]
    )

    # 7. dg_node_metadata — metadata for JSON nodes in content_json
    op.create_table(
        "dg_node_metadata",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("diagram_id", sa.Integer(), nullable=False),
        sa.Column(
            "node_id",
            sa.String(200),
            nullable=False,
            comment="Node ID as it appears in diagram content_json",
        ),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("updated_by_identity_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["diagram_id"], ["dg_diagrams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["updated_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dg_node_metadata_tenant_id", "dg_node_metadata", ["tenant_id"])
    op.create_index(
        "ix_dg_node_metadata_diagram_node",
        "dg_node_metadata",
        ["diagram_id", "node_id"],
    )

    # 8. dg_shares — diagram-level sharing
    op.create_table(
        "dg_shares",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("diagram_id", sa.Integer(), nullable=False),
        sa.Column("shared_with_identity_id", sa.Integer(), nullable=True),
        sa.Column("shared_with_group_id", sa.Integer(), nullable=True),
        sa.Column("shared_by_identity_id", sa.Integer(), nullable=True),
        sa.Column(
            "permission",
            sa.String(20),
            nullable=False,
            server_default="viewer",
            comment="viewer, editor, admin",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("share_token", sa.String(64), nullable=True, unique=True),
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["diagram_id"], ["dg_diagrams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["shared_with_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["shared_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dg_shares_tenant_id", "dg_shares", ["tenant_id"])
    op.create_index("ix_dg_shares_diagram_id", "dg_shares", ["diagram_id"])

    # 9. dg_collections — collection (folder) for grouping diagrams
    op.create_table(
        "dg_collections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("owner_identity_id", sa.Integer(), nullable=False),
        sa.Column("thumbnail_url", sa.String(1000), nullable=True),
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("share_token", sa.String(64), nullable=True, unique=True),
        sa.Column(
            "share_mode",
            sa.String(20),
            nullable=False,
            server_default="private",
            comment="private, restricted, public",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dg_collections_tenant_id", "dg_collections", ["tenant_id"])
    op.create_index(
        "ix_dg_collections_owner_identity_id",
        "dg_collections",
        ["owner_identity_id"],
    )

    # 10. dg_collection_items — M:N membership of diagrams in collections
    op.create_table(
        "dg_collection_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("diagram_id", sa.Integer(), nullable=False),
        sa.Column("added_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["dg_collections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["diagram_id"], ["dg_diagrams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["added_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collection_id", "diagram_id", name="uq_dg_collection_item"
        ),
    )
    op.create_index(
        "ix_dg_collection_items_tenant_id", "dg_collection_items", ["tenant_id"]
    )
    op.create_index(
        "ix_dg_collection_items_collection_id",
        "dg_collection_items",
        ["collection_id"],
    )

    # 11. dg_collection_shares — collection-level sharing
    op.create_table(
        "dg_collection_shares",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("shared_with_identity_id", sa.Integer(), nullable=True),
        sa.Column("shared_with_group_id", sa.Integer(), nullable=True),
        sa.Column(
            "permission",
            sa.String(20),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column("created_by_identity_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["dg_collections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shared_with_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dg_collection_shares_tenant_id", "dg_collection_shares", ["tenant_id"]
    )
    op.create_index(
        "ix_dg_collection_shares_collection_id",
        "dg_collection_shares",
        ["collection_id"],
    )

    # 12. dg_share_analytics — access tracking for shares
    op.create_table(
        "dg_share_analytics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "share_type",
            sa.String(20),
            nullable=False,
            comment="diagram or collection",
        ),
        sa.Column(
            "share_id",
            sa.Integer(),
            nullable=False,
            comment="Polymorphic ID (no FK) — diagram_id or collection_id",
        ),
        sa.Column("share_token", sa.String(64), nullable=True),
        sa.Column("accessed_by_identity_id", sa.Integer(), nullable=True),
        sa.Column("access_ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["accessed_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dg_share_analytics_tenant_id", "dg_share_analytics", ["tenant_id"]
    )

    # 13. dg_comments — spatial comments on diagrams
    op.create_table(
        "dg_comments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("diagram_id", sa.Integer(), nullable=False),
        sa.Column("author_identity_id", sa.Integer(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("x_position", sa.Integer(), nullable=True),
        sa.Column("y_position", sa.Integer(), nullable=True),
        sa.Column(
            "is_resolved",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["diagram_id"], ["dg_diagrams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["author_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dg_comments_tenant_id", "dg_comments", ["tenant_id"])
    op.create_index("ix_dg_comments_diagram_id", "dg_comments", ["diagram_id"])

    # 14. dg_comment_replies — replies to comments
    op.create_table(
        "dg_comment_replies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("comment_id", sa.Integer(), nullable=False),
        sa.Column("author_identity_id", sa.Integer(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["comment_id"], ["dg_comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["author_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dg_comment_replies_tenant_id", "dg_comment_replies", ["tenant_id"]
    )
    op.create_index(
        "ix_dg_comment_replies_comment_id", "dg_comment_replies", ["comment_id"]
    )

    # 15. dg_shape_libraries — reusable shape libraries
    op.create_table(
        "dg_shape_libraries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("owner_identity_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dg_shape_libraries_tenant_id", "dg_shape_libraries", ["tenant_id"]
    )

    # 16. dg_library_shapes — shapes within libraries
    op.create_table(
        "dg_library_shapes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("library_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(500), nullable=True),
        sa.Column("shape_type", sa.String(100), nullable=True),
        sa.Column("default_width", sa.Integer(), nullable=True),
        sa.Column("default_height", sa.Integer(), nullable=True),
        sa.Column(
            "svg_content",
            sa.Text(),
            nullable=True,
            comment="SVG markup for rendering",
        ),
        sa.Column("shape_meta", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["library_id"], ["dg_shape_libraries.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dg_library_shapes_tenant_id", "dg_library_shapes", ["tenant_id"]
    )
    op.create_index(
        "ix_dg_library_shapes_library_id", "dg_library_shapes", ["library_id"]
    )

    # 17. dg_templates — diagram templates
    op.create_table(
        "dg_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column(
            "content",
            sa.JSON(),
            nullable=False,
            comment="Template diagram content",
        ),
        sa.Column(
            "category",
            sa.String(100),
            nullable=False,
            server_default="custom",
        ),
        sa.Column("thumbnail_url", sa.String(1000), nullable=True),
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_by_identity_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by_identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dg_templates_tenant_id", "dg_templates", ["tenant_id"])

    # 18. dg_collaboration_sessions — active co-editing sessions
    op.create_table(
        "dg_collaboration_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("diagram_id", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.String(128), nullable=True),
        sa.Column("socket_id", sa.String(128), nullable=True),
        sa.Column("cursor_position_json", sa.JSON(), nullable=True),
        sa.Column("last_cursor_x", sa.Integer(), nullable=True),
        sa.Column("last_cursor_y", sa.Integer(), nullable=True),
        sa.Column(
            "permission",
            sa.String(20),
            nullable=False,
            server_default="viewer",
            comment="viewer, editor, admin",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["diagram_id"], ["dg_diagrams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["identities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dg_collaboration_sessions_tenant_id",
        "dg_collaboration_sessions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_dg_collaboration_sessions_diagram_id",
        "dg_collaboration_sessions",
        ["diagram_id"],
    )


def downgrade():
    """Drop all diagrams tables in reverse FK dependency order."""
    # Drop in EXACT REVERSE of upgrade order (children before parents)
    op.drop_table("dg_collaboration_sessions")
    op.drop_table("dg_templates")
    op.drop_table("dg_library_shapes")
    op.drop_table("dg_shape_libraries")
    op.drop_table("dg_comment_replies")
    op.drop_table("dg_comments")
    op.drop_table("dg_share_analytics")
    op.drop_table("dg_collection_shares")
    op.drop_table("dg_collection_items")
    op.drop_table("dg_collections")
    op.drop_table("dg_shares")
    op.drop_table("dg_node_metadata")
    op.drop_table("dg_shape_metadata")
    op.drop_table("dg_connectors")
    op.drop_table("dg_shapes")
    # dg_diagram_versions must come before dg_storage_providers (has FK to it)
    op.drop_table("dg_diagram_versions")
    op.drop_table("dg_storage_providers")
    # dg_diagrams last (all others have FKs to it)
    op.drop_table("dg_diagrams")
