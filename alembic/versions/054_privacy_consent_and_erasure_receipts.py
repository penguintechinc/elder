"""Add consent ledger, erasure receipts, and portal-user subjects on dsar_requests.

Statutory-rights completeness (GDPR Art. 7/15/17, CCPA/CPRA): a withdrawal
timestamp alone cannot prove consent or its lawful basis, and an erasure
needs a PII-free receipt of what was erased/retained and why. Portal users
(portal_users) are data subjects too, so dsar_requests gains portal_user_id
and its identity_id becomes nullable. Must stay in sync with
apps/api/models/privacy.py.

MERGE NOTE: down_revision is "052", the same parent as the in-flight MFA
TOTP-encryption migration 053. Whichever of the two merges second MUST
re-point its down_revision at the other (expected: this file -> "053"),
otherwise alembic has two heads and `alembic upgrade head` fails. The two
migrations touch disjoint tables, so the order between them is free.

Revision ID: 054
Revises: 052
Create Date: 2026-11-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "054"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the consent/receipt tables and widen dsar_requests (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "privacy_consent_records" not in tables:
        op.create_table(
            "privacy_consent_records",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False
            ),
            sa.Column("public_id", sa.String(36), nullable=False),
            sa.Column("subject_type", sa.SmallInteger(), nullable=False),
            sa.Column("subject_id", sa.Integer(), nullable=False),
            sa.Column("purpose", sa.SmallInteger(), nullable=False),
            sa.Column("state", sa.SmallInteger(), nullable=False),
            sa.Column("lawful_basis", sa.SmallInteger(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_privacy_consent_records_public_id",
            "privacy_consent_records",
            ["public_id"],
            unique=True,
        )
        op.create_index(
            "ix_privacy_consent_records_subject_id",
            "privacy_consent_records",
            ["subject_id"],
        )

    if "privacy_erasure_receipts" not in tables:
        op.create_table(
            "privacy_erasure_receipts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False
            ),
            sa.Column("public_id", sa.String(36), nullable=False),
            sa.Column("subject_type", sa.SmallInteger(), nullable=False),
            sa.Column("subject_id", sa.Integer(), nullable=False),
            sa.Column("requested_by_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.SmallInteger(), nullable=False, server_default="1"),
            sa.Column("receipt", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_privacy_erasure_receipts_public_id",
            "privacy_erasure_receipts",
            ["public_id"],
            unique=True,
        )
        op.create_index(
            "ix_privacy_erasure_receipts_subject_id",
            "privacy_erasure_receipts",
            ["subject_id"],
        )

    if "dsar_requests" in tables:
        columns = {c["name"] for c in inspector.get_columns("dsar_requests")}
        # batch_alter_table keeps this portable to SQLite (no ALTER COLUMN).
        with op.batch_alter_table("dsar_requests") as batch:
            if "portal_user_id" not in columns:
                batch.add_column(
                    sa.Column("portal_user_id", sa.Integer(), nullable=True)
                )
                batch.create_index(
                    "ix_dsar_requests_portal_user_id", ["portal_user_id"]
                )
                batch.create_foreign_key(
                    "fk_dsar_requests_portal_user_id",
                    "portal_users",
                    ["portal_user_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
            batch.alter_column("identity_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Drop the consent/receipt tables and the portal_user_id column.

    dsar_requests.identity_id is deliberately left nullable: restoring NOT
    NULL would fail on (or force deletion of) portal-user request rows.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "dsar_requests" in tables:
        columns = {c["name"] for c in inspector.get_columns("dsar_requests")}
        if "portal_user_id" in columns:
            with op.batch_alter_table("dsar_requests") as batch:
                batch.drop_constraint(
                    "fk_dsar_requests_portal_user_id", type_="foreignkey"
                )
                batch.drop_index("ix_dsar_requests_portal_user_id")
                batch.drop_column("portal_user_id")
    if "privacy_erasure_receipts" in tables:
        op.drop_table("privacy_erasure_receipts")
    if "privacy_consent_records" in tables:
        op.drop_table("privacy_consent_records")
