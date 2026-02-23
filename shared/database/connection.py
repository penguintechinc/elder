"""Standalone database connection factory using PyDAL.

This module provides a Flask-independent database connection factory
that can be used by any Elder service (API, worker, scanner).
"""

# flake8: noqa: E501

import logging
import time

from pydal import DAL

logger = logging.getLogger(__name__)


def normalize_database_url(url: str) -> str:
    """Normalize a database URL for PyDAL.

    PyDAL uses 'postgres://' not 'postgresql://'.

    Args:
        url: Database URL string

    Returns:
        Normalized URL for PyDAL
    """
    if url and url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgres://", 1)
    return url


def create_db_connection(
    database_url: str,
    pool_size: int = 10,
    migrate: bool = False,
    folder: str = "/tmp/pydal",
    max_retries: int = 30,
    retry_delay: int = 1,
) -> DAL:
    """Create a PyDAL DAL instance usable by any service.

    This is the core connection factory. It normalizes the URL,
    retries on failure, defines all shared tables, and returns
    a ready-to-use DAL instance.

    Args:
        database_url: Database connection URL
        pool_size: Connection pool size (default 10)
        migrate: Whether to allow PyDAL migrations (default False)
        folder: PyDAL metadata folder (default /tmp/pydal)
        max_retries: Max connection retry attempts (default 30)
        retry_delay: Seconds between retries (default 1)

    Returns:
        Initialized PyDAL DAL instance with all tables defined

    Raises:
        Exception: If connection fails after all retries
    """
    database_url = normalize_database_url(database_url)

    for attempt in range(max_retries):
        try:
            db = DAL(
                database_url,
                folder=folder,
                migrate=migrate,
                pool_size=pool_size,
                fake_migrate_all=False,
                lazy_tables=False,
            )
            # Test the connection
            db.executesql("SELECT 1")
            logger.info("Database connection established successfully")

            # Define all shared tables
            from shared.models.pydal_models import define_all_tables
            define_all_tables(db)
            db.commit()

            return db

        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"Database connection attempt {attempt + 1}/{max_retries} "
                    f"failed: {e}. Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
            else:
                logger.error(
                    f"Failed to connect to database after {max_retries} attempts"
                )
                raise
