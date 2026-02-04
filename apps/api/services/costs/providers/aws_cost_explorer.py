"""AWS Cost Explorer provider."""

# flake8: noqa: E501

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from apps.api.services.costs.base import BaseCostProvider

logger = logging.getLogger(__name__)

try:
    import boto3
except ImportError:
    boto3 = None


class AWSCostExplorerProvider(BaseCostProvider):
    """AWS Cost Explorer integration for cost tracking."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        if not boto3:
            raise ImportError("boto3 required. Install: pip install boto3")

        self.ce_client = boto3.client(
            "ce",
            aws_access_key_id=config.get("aws_access_key_id"),
            aws_secret_access_key=config.get("aws_secret_access_key"),
            region_name=config.get("region", "us-east-1"),
        )

    def test_connection(self) -> bool:
        """Test AWS Cost Explorer connectivity."""
        try:
            end = datetime.utcnow().strftime("%Y-%m-%d")
            start = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
            self.ce_client.get_cost_and_usage(
                TimePeriod={"Start": start, "End": end},
                Granularity="DAILY",
                Metrics=["BlendedCost"],
            )
            return True
        except Exception as e:
            logger.warning("AWS Cost Explorer connection test failed: %s", e)
            return False

    def fetch_costs(
        self, resource_type: str, resource_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """Fetch costs from AWS Cost Explorer for a resource."""
        try:
            response = self.ce_client.get_cost_and_usage(
                TimePeriod={"Start": start_date, "End": end_date},
                Granularity="DAILY",
                Metrics=["BlendedCost", "UsageQuantity"],
                Filter={
                    "Dimensions": {
                        "Key": "RESOURCE_ID",
                        "Values": [resource_id],
                    }
                },
            )

            costs = []
            for result in response.get("ResultsByTime", []):
                period_start = result["TimePeriod"]["Start"]
                blended = result["Total"].get("BlendedCost", {})
                usage = result["Total"].get("UsageQuantity", {})
                costs.append({
                    "date": period_start,
                    "amount": float(blended.get("Amount", 0)),
                    "currency": blended.get("Unit", "USD"),
                    "usage_quantity": float(usage.get("Amount", 0)),
                    "usage_unit": usage.get("Unit", ""),
                })
            return costs
        except Exception as e:
            logger.warning("Failed to fetch AWS costs for %s/%s: %s", resource_type, resource_id, e)
            return []

    def get_recommendations(
        self, resource_type: str, resource_id: str
    ) -> List[Dict[str, Any]]:
        """Get rightsizing recommendations from AWS."""
        try:
            response = self.ce_client.get_rightsizing_recommendation(
                Service="AmazonEC2",
                Configuration={
                    "RecommendationTarget": "SAME_INSTANCE_FAMILY",
                    "BenefitsConsidered": True,
                },
            )

            recommendations = []
            for rec in response.get("RightsizingRecommendations", []):
                if rec.get("CurrentInstance", {}).get("ResourceId") == resource_id:
                    action = rec.get("RightsizingType", "Unknown")
                    target = rec.get("ModifyRecommendationDetail", {}).get(
                        "TargetInstances", [{}]
                    )
                    savings = rec.get("CurrentInstance", {}).get(
                        "MonthlyCost", "0"
                    )
                    recommendations.append({
                        "type": "rightsizing",
                        "title": f"{action} recommendation",
                        "description": (
                            f"Consider {action.lower()} this instance"
                            + (f" to {target[0].get('InstanceType', 'N/A')}" if target else "")
                        ),
                        "estimated_savings": float(savings),
                    })
            return recommendations
        except Exception as e:
            logger.warning("Failed to get AWS recommendations: %s", e)
            return []
