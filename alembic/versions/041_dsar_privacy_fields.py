"""Self-service DSAR: identity privacy fields, tenant legal hold, request log

GDPR Art. 15/17 + CCPA/CPRA "Do Not Sell or Share" self-service (see
apps/api/services/privacy/service.py, docs/compliance/data-retention-policy.md).

Revision ID: 041
Revises: 040
Create Date: 2026-09-23
"""

import sqlalchemy as sa

from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade():
    """Add DSAR fields to identities/tenants and create dsar_requests (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    identity_columns = [c["name"] for c in inspector.get_columns("identities")]
    if "do_not_sell_share" not in identity_columns:
        op.add_column(
            "identities",
            sa.Column(
                "do_not_sell_share",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "consent_withdrawn_at" not in identity_columns:
        op.add_column(
            "identities",
            sa.Column(
                "consent_withdrawn_at", sa.DateTime(timezone=True), nullable=True
            ),
        )
    if "anonymized_at" not in identity_columns:
        op.add_column(
            "identities",
            sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True),
        )

    tenant_columns = [c["name"] for c in inspector.get_columns("tenants")]
    if "legal_hold" not in tenant_columns:
        op.add_column(
            "tenants",
            sa.Column(
                "legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )

    if "dsar_requests" not in inspector.get_table_names():
        op.create_table(
            "dsar_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "identity_id",
                sa.Integer(),
                sa.ForeignKey("identities.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("request_type", sa.String(32), nullable=False),
            sa.Column(
                "status", sa.String(20), nullable=False, server_default="completed"
            ),
            sa.Column("requested_by_identity_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    """Drop dsar_requests and the added identity/tenant columns."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "dsar_requests" in inspector.get_table_names():
        op.drop_table("dsar_requests")

    tenant_columns = [c["name"] for c in inspector.get_columns("tenants")]
    if "legal_hold" in tenant_columns:
        op.drop_column("tenants", "legal_hold")

    identity_columns = [c["name"] for c in inspector.get_columns("identities")]
    if "anonymized_at" in identity_columns:
        op.drop_column("identities", "anonymized_at")
    if "consent_withdrawn_at" in identity_columns:
        op.drop_column("identities", "consent_withdrawn_at")
    if "do_not_sell_share" in identity_columns:
        op.drop_column("identities", "do_not_sell_share")
