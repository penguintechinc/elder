"""Database manager with read-replica support.

Provides a DatabaseManager that maintains separate connections for
read and write operations, enabling horizontal read scaling via
database replicas.
"""

# flake8: noqa: E501

import logging

from pydal import DAL

from shared.database.connection import create_db_connection

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages primary and replica database connections.

    Usage:
        manager = DatabaseManager(
            primary_url="postgres://user:pass@primary:5432/elder",
            replica_url="postgres://user:pass@replica:5432/elder",
        )
        # Write operations
        manager.write.entities.insert(name="test")
        manager.write.commit()

        # Read operations (uses replica if configured)
        entities = manager.read(manager.read.entities).select()
    """

    def __init__(
        self,
        primary_url: str,
        replica_url: str | None = None,
        pool_size: int = 10,
        migrate: bool = False,
        folder: str = "/tmp/pydal",
    ):
        """Initialize database connections.

        Args:
            primary_url: Primary (read-write) database URL
            replica_url: Read replica URL (defaults to primary if not set)
            pool_size: Connection pool size per connection
            migrate: Whether to allow PyDAL migrations
            folder: PyDAL metadata folder
        """
        logger.info("Initializing DatabaseManager")

        self._primary = create_db_connection(
            primary_url,
            pool_size=pool_size,
            migrate=migrate,
            folder=folder,
        )
        logger.info("Primary database connection established")

        if replica_url and replica_url != primary_url:
            self._replica = create_db_connection(
                replica_url,
                pool_size=pool_size,
                migrate=False,  # Never migrate on replica
                folder=f"{folder}/replica",
            )
            logger.info("Read replica connection established")
        else:
            self._replica = self._primary
            if not replica_url:
                logger.info("No replica URL configured, using primary for reads")

    @property
    def read(self) -> DAL:
        """Get the read connection (replica if configured, else primary)."""
        return self._replica

    @property
    def write(self) -> DAL:
        """Get the write connection (always primary)."""
        return self._primary

    def close(self):
        """Close all database connections."""
        try:
            self._primary.close()
            if self._replica is not self._primary:
                self._replica.close()
            logger.info("Database connections closed")
        except Exception as e:
            logger.error(f"Error closing database connections: {e}")
