"""Database utilities for Elder application."""

# flake8: noqa: E501

import logging
import os

from pydal import DAL

logger = logging.getLogger(__name__)


def init_db(app):
    """Initialize database. PyDAL handles table creation via migrate=True."""
    database_url = app.config.get("DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not configured")

    # Create PyDAL DAL instance
    db = DAL(
        database_url,
        folder=app.instance_path if hasattr(app, "instance_path") else "/tmp/pydal",
        migrate=True,
        pool_size=10,
        fake_migrate_all=False,
    )

    # Attach to Flask app
    app.db = db

    # Import and define all tables
    from apps.api.models.pydal_models import define_all_tables

    define_all_tables(db)

    # Create default admin user if not exists
    _create_default_admin(app, db)

    logger.info("Database initialized successfully")


def _create_default_admin(app, db):
    """Create default admin user if it doesn't exist."""
    admin_email = app.config.get("ADMIN_EMAIL") or os.getenv(
        "ADMIN_EMAIL", "admin@localhost.local"
    )
    admin_password = app.config.get("ADMIN_PASSWORD") or os.getenv(
        "ADMIN_PASSWORD", "admin123"
    )

    # Check if admin user exists
    existing_user = db(db.auth_users.email == admin_email).select().first()
    if not existing_user:
        from werkzeug.security import generate_password_hash

        db.auth_users.insert(
            email=admin_email,
            username=admin_email,
            password_hash=generate_password_hash(admin_password),
            is_active=True,
            is_admin=True,
        )
        db.commit()
        logger.info(f"Created default admin user: {admin_email}")


def ensure_database_ready(app):
    """Check if database is ready. Returns status dict."""
    try:
        database_url = app.config.get("DATABASE_URL") or os.getenv("DATABASE_URL")
        if not database_url:
            return {"connected": False, "error": "DATABASE_URL not configured"}

        # Try to connect with a test DAL instance
        test_db = DAL(database_url, migrate=False, pool_size=1)
        test_db.close()

        return {
            "connected": True,
            "version": "1.0.0",
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
    "get_db_session",
    "ensure_database_ready",
    "log_startup_status",
]
