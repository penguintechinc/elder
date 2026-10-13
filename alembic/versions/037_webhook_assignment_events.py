"""Add tenant_id, village_id, assignment-event filters, and a metadata bag to
`webhooks` (Plan 05: assignment webhooks). Migration 011 already defines the
correct `is_active`/`events`/`headers` columns on `webhooks` and
`status`/`http_status`/`request_payload`/`attempt_count`/`error_message` on
`webhook_deliveries` — those are schema truth. The Python-side model and
WebhookService currently reference a third, non-existent set of column names
(`events_json`, `enabled`, `payload_json`, `success`, ...); that drift is
reconciled in the model/service, not here — this migration only extends the
already-correct `webhooks` table.

Revision ID: 037
Revises: 036
Create Date: 2026-08-07
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade():
    """Add tenant_id/village_id/filter_*/metadata to webhooks; backfill any pre-existing rows (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("webhooks")}

    if "village_id" not in existing_columns:
        op.add_column("webhooks", sa.Column("village_id", sa.String(32), nullable=True))
        op.create_unique_constraint(
            "uq_webhooks_village_id", "webhooks", ["village_id"]
        )

    if "tenant_id" not in existing_columns:
        op.add_column("webhooks", sa.Column("tenant_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_webhooks_tenant_id",
            "webhooks",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_webhooks_tenant_id", "webhooks", ["tenant_id"])

    if "filter_issue_type" not in existing_columns:
        op.add_column(
            "webhooks", sa.Column("filter_issue_type", sa.String(30), nullable=True)
        )

    if "filter_assignee_type" not in existing_columns:
        op.add_column(
            "webhooks", sa.Column("filter_assignee_type", sa.String(16), nullable=True)
        )

    if "filter_assignee_id" not in existing_columns:
        op.add_column(
            "webhooks", sa.Column("filter_assignee_id", sa.Integer(), nullable=True)
        )

    if "metadata" not in existing_columns:
        op.add_column("webhooks", sa.Column("metadata", sa.JSON(), nullable=True))

    # Backfill: the native webhooks feature has been unwired/broken since
    # inception (see plan intro), so in practice zero rows exist against any
    # real deployment — this loop exists only so a stray manually-inserted
    # row is never left with a NULL village_id/tenant_id after this
    # migration runs.
    rows = conn.execute(
        sa.text("SELECT id, organization_id FROM webhooks WHERE village_id IS NULL")
    ).fetchall()
    for row in rows:
        tenant_id = None
        if row.organization_id:
            org_row = conn.execute(
                sa.text("SELECT tenant_id FROM organizations WHERE id = :oid"),
                {"oid": row.organization_id},
            ).fetchone()
            if org_row and org_row.tenant_id:
                tenant_id = org_row.tenant_id
        village_id = f"{(tenant_id or 0):08x}-{uuid.uuid4().hex[:16]}"
        conn.execute(
            sa.text(
                "UPDATE webhooks SET village_id = :vid, tenant_id = :tid WHERE id = :id"
            ),
            {"vid": village_id, "tid": tenant_id, "id": row.id},
        )


def downgrade():
    """Drop the columns added by upgrade() (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("webhooks")}

    if "metadata" in existing_columns:
        op.drop_column("webhooks", "metadata")
    if "filter_assignee_id" in existing_columns:
        op.drop_column("webhooks", "filter_assignee_id")
    if "filter_assignee_type" in existing_columns:
        op.drop_column("webhooks", "filter_assignee_type")
    if "filter_issue_type" in existing_columns:
        op.drop_column("webhooks", "filter_issue_type")
    if "tenant_id" in existing_columns:
        op.drop_index("ix_webhooks_tenant_id", table_name="webhooks")
        op.drop_constraint("fk_webhooks_tenant_id", "webhooks", type_="foreignkey")
        op.drop_column("webhooks", "tenant_id")
    if "village_id" in existing_columns:
        op.drop_constraint("uq_webhooks_village_id", "webhooks", type_="unique")
        op.drop_column("webhooks", "village_id")
