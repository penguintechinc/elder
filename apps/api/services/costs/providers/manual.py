"""Manual cost entry provider."""

# flake8: noqa: E501

import logging
from typing import Any, Dict, List

from apps.api.services.costs.base import BaseCostProvider

logger = logging.getLogger(__name__)


class ManualCostProvider(BaseCostProvider):
    """Manual cost entry - no external provider."""

    def test_connection(self) -> bool:
        """Always returns True for manual provider."""
        return True

    def fetch_costs(
        self, resource_type: str, resource_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """Manual provider does not fetch costs externally."""
        return []

    def get_recommendations(
        self, resource_type: str, resource_id: str
    ) -> List[Dict[str, Any]]:
        """Manual provider does not provide recommendations."""
        return []
