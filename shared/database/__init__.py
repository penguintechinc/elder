"""Database utilities for Elder application.

Hybrid Approach:
- SQLAlchemy + Alembic: Schema definition and migrations
- PyDAL: Runtime queries and database operations
"""

# flake8: noqa: E501

import logging
import os
import subprocess

from pydal import DAL

logger = logging.getLogger(__name__)


def run_migrations(app):
    """
    Run Alembic database migrations.

    This should be called before init_db() to ensure schema is up-to-date.
    Uses SQLAlchemy URL format (postgresql://).
    """
    try:
        # Get SQLAlchemy-compatible URL (postgresql:// not postgres://)
        database_url = get_database_url(app, for_system="sqlalchemy")

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


def get_database_url(app, for_system: str = "pydal") -> str:
    """
    Get database URL normalized for the target system.

    Different systems use different URI schemes:
    - SQLAlchemy/Alembic: postgresql://user:pass@host:port/db
    - PyDAL:              postgres://user:pass@host:port/db

    Args:
        app: Flask app instance
        for_system: Target system - "sqlalchemy", "pydal", or "raw"

    Returns:
        Database URL formatted for the target system

    Raises:
        ValueError: If DATABASE_URL not configured
    """
    database_url = app.config.get("DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not configured")

    db_type = app.config.get("DB_TYPE") or os.getenv("DB_TYPE")

    # Return raw URL without transformation
    if for_system == "raw":
        return database_url

    # Transform for SQLAlchemy (standard format)
    if for_system == "sqlalchemy":
        # Ensure postgresql:// for SQLAlchemy (may already be correct)
        if database_url.startswith("postgres://") and not database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        return database_url

    # Transform for PyDAL (custom scheme)
    if for_system == "pydal":
        # PyDAL uses postgres:// not postgresql://
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgres://", 1)

        # Validate DB_TYPE if provided
        if db_type:
            db_type = db_type.lower()
            if db_type in ["mariadb", "mysql"]:
                if not database_url.startswith("mysql://"):
                    logger.warning(
                        f"DB_TYPE={db_type} but URL doesn't start with mysql://"
                    )
            elif db_type == "postgresql":
                if not database_url.startswith("postgres://"):
                    logger.warning(
                        f"DB_TYPE=postgresql but URL doesn't start with postgres:// for PyDAL"
                    )
            elif db_type == "sqlite":
                if not database_url.startswith("sqlite://"):
                    logger.warning(
                        f"DB_TYPE={db_type} but URL doesn't start with sqlite://"
                    )

        return database_url

    raise ValueError(f"Unknown system: {for_system}. Use 'sqlalchemy', 'pydal', or 'raw'")


def init_db(app):
    """
    Initialize database for PyDAL runtime queries.

    Note: Schema creation and migrations should be handled by SQLAlchemy/Alembic
    before calling this function. This creates the PyDAL DAL instance for queries.
    """
    # Get PyDAL-compatible URL (postgres:// not postgresql://)
    database_url = get_database_url(app, for_system="pydal")

    db_type = app.config.get("DB_TYPE") or os.getenv("DB_TYPE")
    logger.info(
        f"Initializing PyDAL: {database_url.split('@')[0].split('://')[0]}://*** "
        f"(DB_TYPE: {db_type or 'auto'})"
    )

    # Create PyDAL DAL instance for queries
    db = DAL(
        database_url,
        folder=app.instance_path if hasattr(app, "instance_path") else "/tmp/pydal",
        migrate=True,  # PyDAL can create missing tables
        pool_size=10,
        fake_migrate_all=False,
    )

    # Attach to Flask app for use in endpoints
    app.db = db

    # Import and define all tables
    from apps.api.models.pydal_models import define_all_tables

    define_all_tables(db)

    # Create default admin user if not exists
    _create_default_admin(app, db)

    logger.info("PyDAL database initialized successfully")


def _create_default_admin(app, db):
    """Create default admin user if it doesn't exist."""
    admin_email = app.config.get("ADMIN_EMAIL") or os.getenv(
        "ADMIN_EMAIL", "admin@localhost.local"
    )
    admin_password = app.config.get("ADMIN_PASSWORD") or os.getenv(
        "ADMIN_PASSWORD", "admin123"
    )

    # Ensure default tenant exists (needed for single-tenant deployments)
    try:
        default_tenant = db(db.tenants.slug == "default").select().first()
        if not default_tenant:
            logger.warning("No default tenant found, creating one")
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
        )
        db.commit()
        logger.info(f"Created default admin user: {admin_email}")


def ensure_database_ready(app):
    """Check if database is ready. Returns status dict."""
    try:
        # Get PyDAL-compatible URL for connection test
        database_url = get_database_url(app, for_system="pydal")
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
    """Get database session. Not used with PyDAL."""
    pass


# Mock db object for imports
db = None

__all__ = [
    "db",
    "init_db",
    "run_migrations",
    "get_database_url",
    "get_db_session",
    "ensure_database_ready",
    "log_startup_status",
]
