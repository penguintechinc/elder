"""Issues: reconcile legacy created_by_id/assigned_to_id -> reporter_id/assignee_id

Migration 001 created the `issues` table with `created_by_id` and
`assigned_to_id` physical columns. The SQLAlchemy model
(apps/api/modules/issues/models/issue.py) has always mapped these as
`reporter_id` and `assignee_id`, and no migration in between ever renamed
the columns to match — so any real database built by replaying Alembic
history from 001 has physical columns that disagree with the ORM model.
(A database whose schema instead came from `Base.metadata.create_all()`,
e.g. the test suite, was never affected: create_all() emits the model's
column names directly.)

The rename is guarded/idempotent: it only fires if the legacy column is
present and the target column is not, so this migration is a no-op on any
database that already matches the model (including one where 001 has been
edited in place, or a create_all()-built schema stamped at head).

Associated indexes (ix_issues_created_by_id, ix_issues_assigned_to_id) are
renamed alongside their columns for consistency; this is Postgres-specific
(ALTER INDEX ... RENAME TO), matching the rest of the issues migration
history which targets the project's single supported Alembic backend
(sqlalchemy.url in alembic.ini is postgresql://...).

Also closes a 028/001-replay interaction gap: migration 028 tries to drop
assignee_id's single-table FK to identities.id (assignee_id became
polymorphic there -- identities.id or organizations.id, disambiguated by
assignee_type) by matching `constrained_columns == ["assignee_id"]`. On a
database built by replaying history from 001, the column was still named
assigned_to_id when 028 ran, so that guard never matched and the legacy FK
survived untouched. Left in place, it would reject every org_unit
assignment once this migration renames the column to assignee_id (the FK
travels with the rename). This migration re-runs 028's drop, now checking
the post-rename name, so a 001-replayed database ends up schema-identical
to a create_all()-built one, where the model never declared that FK at all.

Revision ID: 030
Revises: 029
Create Date: 2026-08-06
"""

import sqlalchemy as sa

from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade():
    """Rename created_by_id/assigned_to_id to reporter_id/assignee_id if present."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("issues")]
    index_names = {idx["name"] for idx in inspector.get_indexes("issues")}

    if "created_by_id" in columns and "reporter_id" not in columns:
        op.alter_column("issues", "created_by_id", new_column_name="reporter_id")
        if "ix_issues_created_by_id" in index_names:
            op.execute(
                "ALTER INDEX ix_issues_created_by_id RENAME TO ix_issues_reporter_id"
            )

    if "assigned_to_id" in columns and "assignee_id" not in columns:
        op.alter_column("issues", "assigned_to_id", new_column_name="assignee_id")
        if "ix_issues_assigned_to_id" in index_names:
            op.execute(
                "ALTER INDEX ix_issues_assigned_to_id RENAME TO ix_issues_assignee_id"
            )

        # Re-run migration 028's FK drop against the now-correct column
        # name (see module docstring): re-inspect rather than reuse
        # `inspector`, since the rename above changed the table's state.
        for fk in sa.inspect(conn).get_foreign_keys("issues"):
            if fk.get("constrained_columns") == ["assignee_id"]:
                op.drop_constraint(fk["name"], "issues", type_="foreignkey")


def downgrade():
    """Rename reporter_id/assignee_id back to the legacy 001 column names.

    Best-effort reverse, matching the style of migrations 026-029: not
    re-guarded against the columns already being in the legacy state, since
    downgrade is a manual operator action (see backend-database.md) and not
    exercised by normal forward-only deploys.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    index_names = {idx["name"] for idx in inspector.get_indexes("issues")}

    if "ix_issues_assignee_id" in index_names:
        op.execute(
            "ALTER INDEX ix_issues_assignee_id RENAME TO ix_issues_assigned_to_id"
        )
    op.alter_column("issues", "assignee_id", new_column_name="assigned_to_id")

    if "ix_issues_reporter_id" in index_names:
        op.execute(
            "ALTER INDEX ix_issues_reporter_id RENAME TO ix_issues_created_by_id"
        )
    op.alter_column("issues", "reporter_id", new_column_name="created_by_id")
