"""Base cost provider abstract class."""

# flake8: noqa: E501

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseCostProvider(ABC):
    """Abstract base class for cloud cost providers."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def test_connection(self) -> bool:
        """Test connectivity to cost provider."""
        ...

    @abstractmethod
    def fetch_costs(
        self, resource_type: str, resource_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """Fetch cost data for a specific resource.

        Returns list of daily cost entries:
        [{"date": "2025-01-01", "amount": 12.50, "currency": "USD", "usage_quantity": 720, "usage_unit": "hours"}]
        """
        ...

    @abstractmethod
    def get_recommendations(
        self, resource_type: str, resource_id: str
    ) -> List[Dict[str, Any]]:
        """Get cost optimization recommendations.

        Returns list of recommendations:
        [{"type": "rightsizing", "title": "...", "description": "...", "estimated_savings": 25.00}]
        """
        ...
