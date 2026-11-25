"""Issue comments universal cols: add village_id + metadata + tenant_id

Revision ID: 034
Revises: 033
Create Date: 2026-08-06
"""

import sqlalchemy as sa

from alembic import op

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade():
    """Add village_id, metadata, and tenant_id to issue_comments (idempotent).

    tenant_id is backfilled from the parent issue. village_id cannot be
    minted for existing rows here -- generation requires a live Redis
    connection for the per-tenant sequence counter (see
    shared.utils.village_id.generate_village_id), which is impractical to
    depend on inside a migration. Existing rows are left with village_id
    NULL; only newly created comments get one, via the create-path wiring
    in apps/api/modules/issues/routes/{comments,issues}.py.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("issue_comments")]

    if "village_id" not in columns:
        op.add_column(
            "issue_comments",
            sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        )
        op.create_index(
            "ix_issue_comments_village_id",
            "issue_comments",
            ["village_id"],
            unique=True,
        )

    if "metadata" not in columns:
        op.add_column("issue_comments", sa.Column("metadata", sa.JSON(), nullable=True))

    if "tenant_id" not in columns:
        op.add_column(
            "issue_comments", sa.Column("tenant_id", sa.Integer(), nullable=True)
        )
        op.create_index("ix_issue_comments_tenant_id", "issue_comments", ["tenant_id"])
        op.create_foreign_key(
            "fk_issue_comments_tenant",
            "issue_comments",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Backfill tenant_id from the parent issue. Safe to re-run: only
    # touches NULL rows.
    op.execute("""
        UPDATE issue_comments c SET tenant_id = i.tenant_id
        FROM issues i
        WHERE c.issue_id = i.id AND c.tenant_id IS NULL
        """)


def downgrade():
    op.drop_constraint("fk_issue_comments_tenant", "issue_comments", type_="foreignkey")
    op.drop_index("ix_issue_comments_tenant_id", table_name="issue_comments")
    op.drop_column("issue_comments", "tenant_id")
    op.drop_column("issue_comments", "metadata")
    op.drop_index("ix_issue_comments_village_id", table_name="issue_comments")
    op.drop_column("issue_comments", "village_id")
