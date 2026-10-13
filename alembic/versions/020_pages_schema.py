"""Create pages module schema — 2 tables for wiki-style pages and collections.

Revision ID: 020
Revises: 019
Create Date: 2026-07-08

Phase 3b-c: Author pages module schema from Ruffled.
- pg_pages (Page analog with per-tenant slug uniqueness)
- pg_page_collections (M:N join with doc_collections)

Tenant ref: Integer FK to tenants.id (not uuid).
User/actor refs: author_identity_id Integer FK to identities.id (from Ruffled uuid).
village_id: VillageIDMixin on pg_pages only.
"""

import sqlalchemy as sa

from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade():
    """Create 2 pages tables with FKs, indexes, unique constraints."""

    # pg_pages — wiki-style documentation page
    op.create_table(
        "pg_pages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("slug", sa.String(500), nullable=False),
        sa.Column(
            "body_html",
            sa.Text(),
            nullable=False,
            comment="TipTap HTML output, bleach-sanitized",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="draft",
            comment="draft, published, archived",
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
            comment="Allowed user IDs for 'users' visibility",
        ),
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="Visible without login",
        ),
        sa.Column("author_identity_id", sa.Integer(), nullable=False),
        sa.Column(
            "embedding",
            sa.Text(),
            nullable=True,
            comment="Vector embedding placeholder (TBD)",
        ),
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
        # Per-tenant slug uniqueness — no cross-tenant collisions
        sa.UniqueConstraint("tenant_id", "slug", name="uq_pg_slug_tenant"),
    )
    op.create_index("ix_pg_pages_tenant_id", "pg_pages", ["tenant_id"])
    op.create_index(
        "ix_pg_pages_author_identity_id", "pg_pages", ["author_identity_id"]
    )
    op.create_index("ix_pg_pages_status", "pg_pages", ["status"])

    # pg_page_collections — M:N join between pages and document collections
    op.create_table(
        "pg_page_collections",
        sa.Column(
            "pg_page_id",
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
        sa.ForeignKeyConstraint(["pg_page_id"], ["pg_pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["doc_collection_id"], ["doc_collections.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_pg_page_collections_pg_page_id",
        "pg_page_collections",
        ["pg_page_id"],
    )
    op.create_index(
        "ix_pg_page_collections_doc_collection_id",
        "pg_page_collections",
        ["doc_collection_id"],
    )


def downgrade():
    """Drop all pages tables in FK-safe order."""
    # Drop tables with FKs to pg_pages first
    op.drop_table("pg_page_collections")

    # Drop pg_pages last (leaf table)
    op.drop_table("pg_pages")
