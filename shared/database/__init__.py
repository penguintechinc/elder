"""Database utilities for Elder application."""

# flake8: noqa: E501

import logging
import os

from pydal import DAL

logger = logging.getLogger(__name__)


def _normalize_database_url_for_pydal(database_url: str, db_type: str = None) -> str:
    """
    Normalize database URL for PyDAL compatibility.

    PyDAL uses different URI schemes than SQLAlchemy:
    - SQLAlchemy: postgresql://  ->  PyDAL: postgres://
    - SQLAlchemy: mysql://       ->  PyDAL: mysql://     (same)
    - SQLAlchemy: sqlite://      ->  PyDAL: sqlite://    (same)

    Args:
        database_url: Database connection URL (may be SQLAlchemy format)
        db_type: Optional DB_TYPE override (postgresql, mysql, mariadb, sqlite)

    Returns:
        PyDAL-compatible database URL
    """
    if not database_url:
        return database_url

    # Normalize postgresql -> postgres for PyDAL
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgres://", 1)

    # Handle DB_TYPE override if provided
    if db_type:
        db_type = db_type.lower()
        if db_type in ["mariadb", "mysql"]:
            # MariaDB uses MySQL driver in PyDAL
            if not database_url.startswith("mysql://"):
                logger.warning(
                    f"DB_TYPE={db_type} but URL doesn't start with mysql://, "
                    "this may cause connection issues"
                )
        elif db_type == "postgresql":
            # Ensure postgres:// scheme for PyDAL
            if database_url.startswith("postgresql://"):
                database_url = database_url.replace("postgresql://", "postgres://", 1)
        elif db_type == "sqlite":
            if not database_url.startswith("sqlite://"):
                logger.warning(
                    f"DB_TYPE={db_type} but URL doesn't start with sqlite://, "
                    "this may cause connection issues"
                )

    return database_url


def init_db(app):
    """Initialize database. PyDAL handles table creation via migrate=True."""
    database_url = app.config.get("DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not configured")

    # Get optional DB_TYPE for validation
    db_type = app.config.get("DB_TYPE") or os.getenv("DB_TYPE")

    # Normalize URL for PyDAL (handles postgresql -> postgres conversion)
    database_url = _normalize_database_url_for_pydal(database_url, db_type)

    logger.info(f"Initializing database: {database_url.split('@')[0].split('://')[0]}://***")

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

        # Get optional DB_TYPE for validation
        db_type = app.config.get("DB_TYPE") or os.getenv("DB_TYPE")

        # Normalize URL for PyDAL (handles postgresql -> postgres conversion)
        database_url = _normalize_database_url_for_pydal(database_url, db_type)

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
    "get_db_session",
    "ensure_database_ready",
    "log_startup_status",
]
