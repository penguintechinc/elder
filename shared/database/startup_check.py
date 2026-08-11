"""Database startup check and auto-initialization using SQLAlchemy.

This module provides database verification and automatic initialization/migration
on API startup. It uses SQLAlchemy for schema introspection and table creation,
while PyDAL handles all runtime operations.

Usage:
    from shared.database.startup_check import ensure_database_ready
    ensure_database_ready(app)  # Call before init_db()
"""

import logging
import os
import time
from typing import Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)

# Core tables that must exist for the application to function
# These are checked in order of dependency (tables with no deps first)
REQUIRED_TABLES = [
    "tenants",  # L0: Multi-tenancy foundation
    "roles",  # L1: Base table
    "permissions",  # L1: Base table
    "identities",  # L1: Users/accounts
    "organizations",  # L2: Depends on tenants, identities
    "portal_users",  # L2: Depends on tenants
    "user_roles",  # L3: Depends on identities, roles
    "role_permissions",  # L3: Depends on roles, permissions
]


def get_sqlalchemy_url(app) -> str:
    """Build SQLAlchemy database URL from Flask config or environment.

    Args:
        app: Flask application instance

    Returns:
        SQLAlchemy-compatible database URL
    """
    # Check for direct DATABASE_URL
    database_url = app.config.get("DATABASE_URL") or os.getenv("DATABASE_URL")

    if database_url:
        # Convert postgres:// to postgresql:// for SQLAlchemy
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        return database_url

    # Build from components
    db_type = app.config.get("DB_TYPE") or os.getenv("DB_TYPE", "postgres")
    db_host = app.config.get("DB_HOST") or os.getenv("DB_HOST", "localhost")
    db_port = app.config.get("DB_PORT") or os.getenv("DB_PORT", "5432")
    db_name = app.config.get("DB_NAME") or os.getenv("DB_NAME", "elder")
    db_user = app.config.get("DB_USER") or os.getenv("DB_USER", "elder")
    db_password = app.config.get("DB_PASSWORD") or os.getenv("DB_PASSWORD", "elder")

    db_type = db_type.lower()

    if db_type == "sqlite":
        return f"sqlite:///{db_name}.sqlite"
    elif db_type in ["mysql", "mariadb", "mariadb-galera"]:
        if not db_port or db_port == "5432":
            db_port = "3306"
        return f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"
    else:  # PostgreSQL (default)
        return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


def wait_for_database(
    engine: Engine, max_retries: int = 30, retry_delay: int = 2
) -> bool:
    """Wait for database to become available.

    Args:
        engine: SQLAlchemy engine
        max_retries: Maximum connection attempts
        retry_delay: Seconds between attempts

    Returns:
        True if connected, False if timed out
    """
    for attempt in range(max_retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                logger.info("Database connection established")
                return True
        except OperationalError as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"Database connection attempt {attempt + 1}/{max_retries} failed: {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
            else:
                logger.error(
                    f"Failed to connect to database after {max_retries} attempts"
                )
                return False
    return False


def check_tables_exist(engine: Engine) -> tuple[bool, list[str]]:
    """Check if all required tables exist in the database.

    Args:
        engine: SQLAlchemy engine

    Returns:
        Tuple of (all_exist, missing_tables)
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    missing = [t for t in REQUIRED_TABLES if t not in existing_tables]

    if missing:
        logger.info(f"Missing tables detected: {missing}")
        return False, missing
    else:
        logger.info("All required tables exist")
        return True, []


def needs_migration(engine: Engine) -> tuple[bool, str | None]:
    """Check if database schema needs migration.

    This performs basic schema version checking by looking at table columns.
    For more complex migrations, consider using Alembic.

    Args:
        engine: SQLAlchemy engine

    Returns:
        Tuple of (needs_migration, reason)
    """
    inspector = inspect(engine)

    # Check for v2.2.0+ schema (tenants table with required columns)
    if "tenants" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("tenants")}
        if "village_id" not in columns:
            return True, "tenants table missing village_id column (pre-v2.3.0)"

    # Check for v2.0.0+ schema (identities with tenant_id)
    if "identities" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("identities")}
        if "tenant_id" not in columns:
            return True, "identities table missing tenant_id column (pre-v2.2.0)"

    # Check for portal_users table (v2.2.0+)
    if "portal_users" not in inspector.get_table_names():
        return True, "portal_users table missing (pre-v2.2.0)"

    return False, None


def ensure_database_ready(app) -> dict:
    """Ensure database is ready for application startup.

    This function:
    1. Waits for database connection
    2. Checks if required tables exist
    3. Reports initialization/migration status

    The actual table creation is handled by PyDAL's migrate=True in init_db().
    This function provides visibility into what will happen.

    Args:
        app: Flask application instance

    Returns:
        Status dict with keys: connected, tables_exist, needs_init, needs_migration
    """
    status = {
        "connected": False,
        "tables_exist": False,
        "needs_init": False,
        "needs_migration": False,
        "migration_reason": None,
        "missing_tables": [],
    }

    # Get SQLAlchemy URL
    db_url = get_sqlalchemy_url(app)

    # Create engine (don't log password)
    safe_url = db_url.split("@")[-1] if "@" in db_url else db_url
    logger.info(f"Checking database at: ...@{safe_url}")

    engine = create_engine(db_url, echo=False)

    # Step 1: Wait for connection
    if not wait_for_database(engine):
        logger.error("Cannot proceed - database not available")
        return status

    status["connected"] = True

    # Step 2: Check if tables exist
    tables_exist, missing = check_tables_exist(engine)
    status["tables_exist"] = tables_exist
    status["missing_tables"] = missing

    if not tables_exist:
        status["needs_init"] = True
        logger.info(
            f"Database initialization required - missing {len(missing)} tables. "
            "PyDAL will create them on startup."
        )
    else:
        # Step 3: Check for migrations
        needs_mig, reason = needs_migration(engine)
        status["needs_migration"] = needs_mig
        status["migration_reason"] = reason

        if needs_mig:
            logger.info(f"Database migration may be needed: {reason}")

    # Cleanup
    engine.dispose()

    return status


def log_startup_status(status: dict) -> None:
    """Log database startup status in a user-friendly format.

    Args:
        status: Status dict from ensure_database_ready()
    """
    if not status["connected"]:
        logger.error("=" * 60)
        logger.error("DATABASE STARTUP CHECK FAILED")
        logger.error("Could not connect to database")
        logger.error("=" * 60)
        return

    logger.info("=" * 60)
    logger.info("DATABASE STARTUP CHECK")
    logger.info("=" * 60)
    logger.info(f"  Connected: {status['connected']}")
    logger.info(f"  Tables exist: {status['tables_exist']}")

    if status["needs_init"]:
        logger.info(f"  Action: INITIALIZING ({len(status['missing_tables'])} tables)")
        logger.info(f"  Missing: {', '.join(status['missing_tables'])}")
    elif status["needs_migration"]:
        logger.info(f"  Action: MIGRATION NEEDED")
        logger.info(f"  Reason: {status['migration_reason']}")
    else:
        logger.info("  Action: READY (no changes needed)")

    logger.info("=" * 60)
