-- Per-service database accounts for Elder
-- These users provide least-privilege access for each service.
-- Passwords should be overridden via K8s Secrets in production.

-- elder_worker: discovery + entity write, org read
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'elder_worker') THEN
        CREATE ROLE elder_worker WITH LOGIN PASSWORD 'elder_worker_password';
    END IF;
END
$$;

-- elder_scanner: scan job read, discovery_history write
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'elder_scanner') THEN
        CREATE ROLE elder_scanner WITH LOGIN PASSWORD 'elder_scanner_password';
    END IF;
END
$$;

-- Note: Table-level GRANT statements are applied by Alembic migration 010
-- after tables are created. This script only creates the roles for fresh installs.
