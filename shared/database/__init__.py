"""Database utilities for Elder application.

Hybrid Approach:
- SQLAlchemy + Alembic: Schema definition and migrations
- penguin-dal: Runtime queries and database operations (SQLAlchemy-backed)
"""

# flake8: noqa: E501

import logging
import os
import subprocess

from penguin_dal import DAL

logger = logging.getLogger(__name__)


def run_migrations(app):
    """
    Run Alembic database migrations.

    This should be called before init_db() to ensure schema is up-to-date.
    Uses SQLAlchemy URL format (postgresql://).
    """
    try:
        # Get SQLAlchemy-compatible URL (postgresql:// not postgres://)
        database_url = get_database_url(app)

        # Set environment variable for Alembic
        env = os.environ.copy()
        env["DATABASE_URL"] = database_url

        logger.info("Running Alembic migrations...")

        # Run alembic upgrade head
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0:
            logger.info("Alembic migrations completed successfully")
            if result.stdout:
                logger.debug(f"Migration output: {result.stdout}")
        else:
            logger.error(f"Alembic migration failed: {result.stderr}")
            raise RuntimeError(f"Database migrations failed: {result.stderr}")

    except FileNotFoundError:
        logger.warning(
            "Alembic not found - skipping migrations. "
            "Install alembic or ensure it's in PATH."
        )
    except Exception as e:
        logger.error(f"Migration error: {e}")
        raise


def init_sqlalchemy_tables(app):
    """
    Create all SQLAlchemy-defined tables if they don't exist.

    Safe to call on startup — create_all() is idempotent and only creates
    tables that are missing. Does NOT run Alembic migrations.
    For schema migrations on existing databases, use: ./scripts/migrate.sh
    """
    from sqlalchemy import create_engine

    # Import all models so they register with Base.metadata
    from apps.api.models import (  # noqa: F401
        access_review,
        alert_config,
        audit,
        auth_providers,
        dependency,
        discovery,
        entity,
        identity,
        infrastructure,
        ipam,
        issue,
        metadata,
        oncall,
        organization,
        project,
        rbac,
        resource_role,
        secrets,
        security,
        tenant,
        webhooks,
    )
    from apps.api.models.base import Base

    database_url = get_database_url(app)
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    logger.info("SQLAlchemy tables created/verified")


def get_database_url(app, **kwargs) -> str:
    """
    Get database URL in SQLAlchemy format (postgresql://).

    penguin-dal is a SQLAlchemy-compatible wrapper, so returns standard
    SQLAlchemy-compatible URLs for all database systems.

    Args:
        app: Flask app instance
        **kwargs: Backward compatibility (ignored)

    Returns:
        Database URL in SQLAlchemy format

    Raises:
        ValueError: If DATABASE_URL not configured
    """
    database_url = app.config.get("DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not configured")

    # Ensure postgresql:// for SQLAlchemy/penguin-dal (may already be correct)
    if database_url.startswith("postgres://") and not database_url.startswith(
        "postgresql://"
    ):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    return database_url


def init_db(app):
    """
    Initialize database for penguin-dal runtime queries.

    Note: Schema creation and migrations should be handled by SQLAlchemy/Alembic
    before calling this function. This creates the penguin-dal DAL instance for queries.
    """
    # Get SQLAlchemy-compatible URL
    database_url = get_database_url(app)

    db_type = app.config.get("DB_TYPE") or os.getenv("DB_TYPE")
    logger.info(
        f"Initializing penguin-dal: {database_url.split('@')[0].split('://')[0]}://*** "
        f"(DB_TYPE: {db_type or 'auto'})"
    )

    # Create penguin-dal DAL instance for queries only.
    # Schema creation is handled by Alembic migrations (run_migrations).
    db = DAL(database_url, pool_size=10, migrate=False)

    # Attach to Flask app for use in endpoints
    app.db = db

    # Register per-request teardown to return connections to pool cleanly.
    # Without this, psycopg2 cursors go stale between requests — the pool hands
    # out a connection whose cursor is already closed, causing "cursor already
    # closed" errors on the next db.table[id] lookup.
    def _teardown_db(exception=None):
        _db = app.db
        try:
            if hasattr(_db, 'commit') and hasattr(_db, 'rollback'):
                if exception:
                    _db.rollback()
                else:
                    _db.commit()
        except Exception as e:
            logger.debug(f"DB teardown error: {e}")
            try:
                if hasattr(_db, 'rollback'):
                    _db.rollback()
            except Exception:
                pass
        # Handle separate read replica if configured
        if hasattr(app, "db_read") and app.db_read is not _db:
            try:
                if hasattr(app.db_read, 'commit') and hasattr(app.db_read, 'rollback'):
                    if exception:
                        app.db_read.rollback()
                    else:
                        app.db_read.commit()
            except Exception as e:
                logger.debug(f"DB read replica teardown error: {e}")

    app.teardown_appcontext(_teardown_db)

    # Initialize read replica connection if configured
    read_url = app.config.get("DATABASE_READ_URL") or os.getenv("DATABASE_READ_URL")
    if read_url and read_url.strip():
        # Normalize read replica URL to SQLAlchemy format
        if read_url.startswith("postgres://") and not read_url.startswith(
            "postgresql://"
        ):
            read_url = read_url.replace("postgres://", "postgresql://", 1)
        logger.info("Initializing read replica connection")
        app.db_read = DAL(read_url, pool_size=10, migrate=False)
    else:
        # No replica configured — reads go to primary
        app.db_read = db

    # Create default admin user if not exists
    _create_default_admin(app, db)

    logger.info("penguin-dal database initialized successfully")


def _create_default_admin(app, db):
    """Create default admin user if it doesn't exist."""
    admin_email = app.config.get("ADMIN_EMAIL") or os.getenv(
        "ADMIN_EMAIL", "admin@localhost.local"
    )
    admin_password = app.config.get("ADMIN_PASSWORD") or os.getenv(
        "ADMIN_PASSWORD", "admin123"
    )

    # Ensure system tenant exists (needed for single-tenant deployments)
    # Try "system" first (created by _init_default_data), then fallback to "default"
    try:
        default_tenant = db(db.tenants.slug == "system").select().first()
        if not default_tenant:
            default_tenant = db(db.tenants.slug == "default").select().first()

        if not default_tenant:
            logger.warning("No system tenant found, creating one with slug 'default'")
            default_tenant_id = db.tenants.insert(
                name="Default",
                slug="default",
                subscription_tier="enterprise",
                is_active=True,
            )
            db.commit()
        else:
            default_tenant_id = default_tenant.id
    except Exception as e:
        logger.error(f"Failed to ensure default tenant: {e}")
        db.rollback()
        return

    # Check if admin user exists in portal_users
    existing_user = db(db.portal_users.email == admin_email).select().first()
    if not existing_user:
        from werkzeug.security import generate_password_hash

        db.portal_users.insert(
            tenant_id=default_tenant_id,
            email=admin_email,
            password_hash=generate_password_hash(admin_password),
            is_active=True,
            is_admin=True,
            global_role="admin",
        )
        db.commit()
        logger.info(f"Created default admin user: {admin_email}")


def ensure_database_ready(app):
    """Check if database is ready. Returns status dict."""
    try:
        # Get SQLAlchemy-compatible URL for connection test
        database_url = get_database_url(app)
        db_type = app.config.get("DB_TYPE") or os.getenv("DB_TYPE")

        # Try to connect with a test DAL instance
        test_db = DAL(database_url, migrate=False, pool_size=1)
        test_db.close()

        return {
            "connected": True,
            "version": "1.0.0",
            "db_type": db_type or "auto-detected",
        }
    except Exception as e:
        logger.error(f"Database connection check failed: {e}")
        return {"connected": False, "error": str(e)}


def log_startup_status(status):
    """Log database startup status."""
    if status.get("connected"):
        logger.info(f"Database ready - version {status.get('version')}")
    else:
        logger.error(f"Database not ready: {status.get('error')}")


def get_db_session():
    """Get database session. Not used with penguin-dal."""
    pass


# Mock db object for imports
db = None

from shared.database.connection import create_db_connection  # noqa: E402
from shared.database.manager import DatabaseManager  # noqa: E402

__all__ = [
    "db",
    "init_db",
    "init_sqlalchemy_tables",
    "run_migrations",
    "get_database_url",
    "get_db_session",
    "ensure_database_ready",
    "log_startup_status",
    "create_db_connection",
    "DatabaseManager",
]
