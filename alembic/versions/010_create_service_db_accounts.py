"""Create per-service database accounts with least-privilege access.

Revision ID: 010
Revises: 009
Create Date: 2026-02-23

Creates scoped PostgreSQL users for each service:
- elder_worker: discovery + entity write, org read
- elder_scanner: scan job read, discovery_history write

The main 'elder' user (API) retains full access.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = '010'
down_revision = '009'
branch_labels = None
depends_on = None


def upgrade():
    """Create service-scoped database users."""

    # elder_worker: discovery jobs, entities, networking, data stores, etc.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'elder_worker') THEN
                CREATE ROLE elder_worker WITH LOGIN PASSWORD 'elder_worker_password';
            END IF;
        END
        $$;
    """)

    op.execute("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON
            discovery_jobs, discovery_history,
            entities, networking_resources, network_entity_mappings,
            data_stores, services, identities, software, dependencies,
            certificates, builtin_secrets
        TO elder_worker;
    """)

    op.execute("""
        GRANT SELECT ON
            organizations, tenants, portal_users
        TO elder_worker;
    """)

    op.execute("""
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO elder_worker;
    """)

    # elder_scanner: read scan jobs, write results/history
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'elder_scanner') THEN
                CREATE ROLE elder_scanner WITH LOGIN PASSWORD 'elder_scanner_password';
            END IF;
        END
        $$;
    """)

    op.execute("""
        GRANT SELECT ON discovery_jobs TO elder_scanner;
    """)

    op.execute("""
        GRANT SELECT, INSERT, UPDATE ON discovery_history TO elder_scanner;
    """)

    op.execute("""
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO elder_scanner;
    """)


def downgrade():
    """Remove service-scoped database users."""
    op.execute("""
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM elder_worker;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM elder_worker;
        DROP ROLE IF EXISTS elder_worker;
    """)

    op.execute("""
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM elder_scanner;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM elder_scanner;
        DROP ROLE IF EXISTS elder_scanner;
    """)
