"""Standalone database connection factory using penguin-dal.

This module provides a Flask-independent database connection factory
that can be used by any Elder service (API, worker, scanner).
"""

# flake8: noqa: E501

import logging
import time

from penguin_dal import DAL

logger = logging.getLogger(__name__)


def create_db_connection(
    database_url: str,
    pool_size: int = 10,
    migrate: bool = False,
    max_retries: int = 30,
    retry_delay: int = 1,
) -> DAL:
    """Create a penguin-dal DAL instance usable by any service.

    This is the core connection factory. It retries on failure
    and returns a ready-to-use DAL instance.

    Args:
        database_url: Database connection URL (supports both postgresql:// and postgres://)
        pool_size: Connection pool size (default 10)
        migrate: Whether to allow migrations (default False)
        max_retries: Max connection retry attempts (default 30)
        retry_delay: Seconds between retries (default 1)

    Returns:
        Initialized penguin-dal DAL instance

    Raises:
        Exception: If connection fails after all retries
    """
    for attempt in range(max_retries):
        try:
            db = DAL(database_url, pool_size=pool_size, migrate=migrate)
            logger.info("Database connection established successfully")
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
