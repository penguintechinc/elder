"""Point data_store_labels.label_id FK to issue_labels(id).

Revision ID: 043
Revises: 042
Create Date: 2026-11-19
"""

import sqlalchemy as sa

from alembic import op

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade():
    """Update data_store_labels.label_id foreign key from labels to issue_labels."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "data_store_labels" not in inspector.get_table_names():
        return

    fks = inspector.get_foreign_keys("data_store_labels")
    has_issue_labels_fk = False
    for fk in fks:
        if fk.get("constrained_columns") == ["label_id"]:
            if fk.get("referred_table") == "issue_labels":
                has_issue_labels_fk = True
            elif fk.get("name"):
                op.drop_constraint(fk["name"], "data_store_labels", type_="foreignkey")

    if not has_issue_labels_fk:
        op.create_foreign_key(
            "fk_data_store_labels_label_id_issue_labels",
            "data_store_labels",
            "issue_labels",
            ["label_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade():
    """Revert data_store_labels.label_id foreign key back to labels."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "data_store_labels" not in inspector.get_table_names():
        return

    for fk in inspector.get_foreign_keys("data_store_labels"):
        if fk.get("constrained_columns") == ["label_id"] and fk.get("name"):
            op.drop_constraint(fk["name"], "data_store_labels", type_="foreignkey")

    op.create_foreign_key(
        "data_store_labels_label_id_fkey",
        "data_store_labels",
        "labels",
        ["label_id"],
        ["id"],
        ondelete="CASCADE",
    )
