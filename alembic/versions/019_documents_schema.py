"""Create documents module schema — 4 tables for knowledge base, collections, versions.

Revision ID: 019
Revises: 018
Create Date: 2026-07-08

Phase 3b-a: Author documents module schema from Ruffled.
- doc_documents (KbArticle analog with global slug uniqueness)
- doc_collections (hierarchical tree)
- doc_document_collections (M:N join)
- doc_versions (immutable version snapshots)

Tenant ref: Integer FK to tenants.id (not uuid).
User/actor refs: *_identity_id Integer FK to identities.id (from Ruffled uuid).
village_id: VillageIDMixin on doc_documents, doc_collections only.
"""

import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade():
    """Create 4 documents tables with FKs, indexes, unique constraints."""

    # doc_documents — main knowledge base article table
    op.create_table(
        "doc_documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("slug", sa.String(500), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column(
            "body_text", sa.Text(), nullable=True, comment="Plaintext for search"
        ),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True, comment="JSON array of tags"),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="draft",
            comment="draft, published, archived",
        ),
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="Visible without login",
        ),
        sa.Column(
            "visibility",
            sa.String(20),
            nullable=False,
            server_default="authenticated",
            comment="public, authenticated, roles, users",
        ),
        sa.Column(
            "visibility_roles",
            sa.JSON(),
            nullable=True,
            comment="Allowed roles for 'roles' visibility",
        ),
        sa.Column(
            "visibility_users",
            sa.JSON(),
            nullable=True,
            comment="Allowed user UUIDs for 'users' visibility",
        ),
        sa.Column("author_identity_id", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
            ["author_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        # Global slug uniqueness — public URL /public/<slug> has no tenant component.
        sa.UniqueConstraint("slug", name="uq_doc_slug"),
    )
    op.create_index("ix_doc_documents_tenant_id", "doc_documents", ["tenant_id"])
    op.create_index(
        "ix_doc_documents_author_identity_id", "doc_documents", ["author_identity_id"]
    )
    op.create_index("ix_doc_documents_status", "doc_documents", ["status"])

    # doc_collections — hierarchical folder structure
    op.create_table(
        "doc_collections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=True, comment="URL-safe name"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "parent_id",
            sa.Integer(),
            nullable=True,
            comment="Self-ref for tree (null=root)",
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_doc_collections_tenant_id", "doc_collections", ["tenant_id"])
    op.create_index("ix_doc_collections_parent_id", "doc_collections", ["parent_id"])

    # doc_collections.parent_id self-referential FK — deferred until table exists.
    op.create_foreign_key(
        "fk_doc_collections_parent_id",
        "doc_collections",
        "doc_collections",
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # doc_document_collections — M:N join between documents and collections
    op.create_table(
        "doc_document_collections",
        sa.Column(
            "doc_document_id",
            sa.Integer(),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "doc_collection_id",
            sa.Integer(),
            nullable=False,
            primary_key=True,
        ),
        sa.ForeignKeyConstraint(
            ["doc_document_id"], ["doc_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["doc_collection_id"], ["doc_collections.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_doc_document_collections_doc_document_id",
        "doc_document_collections",
        ["doc_document_id"],
    )
    op.create_index(
        "ix_doc_document_collections_doc_collection_id",
        "doc_document_collections",
        ["doc_collection_id"],
    )

    # doc_versions — immutable version history
    op.create_table(
        "doc_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("doc_document_id", sa.Integer(), nullable=False),
        sa.Column(
            "version_number", sa.Integer(), nullable=False, comment="Monotonic 1,2,3..."
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column(
            "body_text", sa.Text(), nullable=True, comment="Plaintext for search"
        ),
        sa.Column("author_identity_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["doc_document_id"], ["doc_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["author_identity_id"], ["identities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        # Unique constraint: one version number per document.
        sa.UniqueConstraint("doc_document_id", "version_number", name="uq_doc_version"),
    )
    op.create_index(
        "ix_doc_versions_doc_document_id", "doc_versions", ["doc_document_id"]
    )


def downgrade():
    """Drop all documents tables in FK-safe order."""
    # Drop tables with FKs to doc_documents/doc_collections first
    op.drop_table("doc_versions")
    op.drop_table("doc_document_collections")

    # Drop doc_collections self-ref FK before table
    op.drop_constraint(
        "fk_doc_collections_parent_id", "doc_collections", type_="foreignkey"
    )
    op.drop_table("doc_collections")

    # Drop doc_documents last (leaf table)
    op.drop_table("doc_documents")
