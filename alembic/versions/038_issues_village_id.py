"""Issues village_id: add village_id column to issues (idempotent)

Revision ID: 038
Revises: 037
Create Date: 2026-08-08

The Issue model declares village_id via VillageIDMixin, and the create-path
now mints it (apps/api/modules/issues/routes/issues.py::create_issue), but no
migration ever added the physical column. create_all() only adds missing
*tables*, not missing *columns*, so any issues table that predates the mixin
(alembic-built DBs, or a create_all DB where the table already existed) lacks
the column -- and an insert that supplies village_id then fails. This closes
that drift, mirroring 034 (issue_comments) and 016 (entities/orgs).
"""

import sqlalchemy as sa

from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade():
    """Add village_id to issues (idempotent).

    village_id cannot be minted for existing rows here -- generation requires a
    live Redis connection for the per-tenant sequence counter (see
    shared.utils.village_id.generate_village_id), which is impractical inside a
    migration. Existing rows are left with village_id NULL (the column is
    nullable); only newly created/updated issues get one, via the create-path
    wiring. This matches how 034 handled issue_comments.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("issues")]

    if "village_id" not in columns:
        op.add_column(
            "issues",
            sa.Column("village_id", sa.String(32), nullable=True, unique=True),
        )
        op.create_index(
            "ix_issues_village_id",
            "issues",
            ["village_id"],
            unique=True,
        )


def downgrade():
    op.drop_index("ix_issues_village_id", table_name="issues")
    op.drop_column("issues", "village_id")
