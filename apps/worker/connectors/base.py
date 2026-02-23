"""Base connector interface."""

# flake8: noqa: E501


from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List

from apps.worker.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SyncResult:
    """Result of a synchronization operation."""

    connector_name: str
    organizations_created: int = 0
    organizations_updated: int = 0
    entities_created: int = 0
    entities_updated: int = 0
    errors: List[str] = None

    def __post_init__(self):
        """Initialize errors list if not provided."""
        if self.errors is None:
            self.errors = []

    @property
    def total_operations(self) -> int:
        """Total number of operations performed."""
        return (
            self.organizations_created
            + self.organizations_updated
            + self.entities_created
            + self.entities_updated
        )

    @property
    def has_errors(self) -> bool:
        """Check if there were any errors."""
        return len(self.errors) > 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "connector": self.connector_name,
            "organizations_created": self.organizations_created,
            "organizations_updated": self.organizations_updated,
            "entities_created": self.entities_created,
            "entities_updated": self.entities_updated,
            "total_operations": self.total_operations,
            "errors": self.errors,
            "has_errors": self.has_errors,
        }


class BaseConnector(ABC):
    """Base class for all connectors."""

    def __init__(self, name: str):
        """
        Initialize base connector.

        Args:
            name: Connector name
        """
        self.name = name
        self.logger = get_logger(f"connector.{name}")

    @abstractmethod
    async def connect(self) -> None:
        """
        Establish connection to the external service.

        Raises:
            Exception: On connection failure
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the external service."""

    @abstractmethod
    async def sync(self) -> SyncResult:
        """
        Synchronize data from external service to Elder.

        Returns:
            SyncResult with statistics about the sync operation

        Raises:
            Exception: On sync failure
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the connector is healthy and can connect.

        Returns:
            True if healthy, False otherwise
        """

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
