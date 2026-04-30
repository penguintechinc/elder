"""Fix auth_provider and identity_type enums: add aws, normalize to lowercase values

Revision ID: 014
Revises: 013
Create Date: 2026-04-29
"""

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade():
    """
    Fix auth_provider and identity_type enums.

    Changes:
    1. Add 'aws' value to auth_provider enum
    2. Normalize existing values to lowercase (if stored as uppercase names)
    3. Convert identity_type column to use lowercase values consistently
    """
    conn = op.get_bind()

    # Check if identities table exists
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "identities" not in tables:
        return

    columns = [c["name"] for c in inspector.get_columns("identities")]

    # Fix auth_provider column
    if "auth_provider" in columns:
        col_info = next(
            (
                c
                for c in inspector.get_columns("identities")
                if c["name"] == "auth_provider"
            ),
            None,
        )
        col_type = str(col_info["type"]) if col_info else ""

        # If it's a PostgreSQL enum type, convert to varchar first
        if "ENUM" in col_type.upper() or "authprovider" in col_type.lower():
            op.execute(
                "ALTER TABLE identities ALTER COLUMN auth_provider TYPE VARCHAR(50) USING auth_provider::text"
            )
            # Drop the old enum type if it exists
            op.execute("DROP TYPE IF EXISTS authprovider CASCADE")

        # Normalize uppercase names to lowercase values
        # This handles the case where the old enum stored uppercase names (LOCAL, SAML, etc.)
        op.execute(
            "UPDATE identities SET auth_provider = LOWER(auth_provider) WHERE auth_provider = UPPER(auth_provider)"
        )

    # Fix identity_type column
    if "identity_type" in columns:
        col_info2 = next(
            (
                c
                for c in inspector.get_columns("identities")
                if c["name"] == "identity_type"
            ),
            None,
        )
        col_type2 = str(col_info2["type"]) if col_info2 else ""

        # If it's a PostgreSQL enum type, convert to varchar first
        if "ENUM" in col_type2.upper() or "identitytype" in col_type2.lower():
            op.execute(
                "ALTER TABLE identities ALTER COLUMN identity_type TYPE VARCHAR(50) USING identity_type::text"
            )
            # Drop the old enum type if it exists
            op.execute("DROP TYPE IF EXISTS identitytype CASCADE")

        # Normalize uppercase names to lowercase values
        op.execute(
            "UPDATE identities SET identity_type = LOWER(identity_type) WHERE identity_type = UPPER(identity_type)"
        )


def downgrade():
    """Downgrade is a no-op — enum values are canonical lowercase."""
    pass
