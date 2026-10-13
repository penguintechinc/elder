"""Base scanner class."""

# flake8: noqa: E501

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseScanner(ABC):
    """Abstract base class for all scanners."""

    @abstractmethod
    async def scan(self, config: dict[str, Any]) -> dict[str, Any]:
        """Execute the scan with the given configuration.

        Args:
            config: Scanner-specific configuration dictionary

        Returns:
            Dictionary containing scan results
        """

    def validate_config(self, config: dict[str, Any], required_fields: list) -> None:
        """Validate that required fields are present in config.

        Args:
            config: Configuration dictionary
            required_fields: List of required field names

        Raises:
            ValueError: If required fields are missing
        """
        missing = [f for f in required_fields if f not in config]
        if missing:
            raise ValueError(f"Missing required config fields: {', '.join(missing)}")
