"""GCP Billing provider."""

# flake8: noqa: E501

import logging
from typing import Any, Dict, List

from apps.api.services.costs.base import BaseCostProvider

logger = logging.getLogger(__name__)

try:
    from google.cloud import bigquery
except ImportError:
    bigquery = None


class GCPBillingProvider(BaseCostProvider):
    """GCP BigQuery billing export integration."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        if not bigquery:
            raise ImportError("google-cloud-bigquery required. Install: pip install google-cloud-bigquery")

        self.project_id = config.get("project_id")
        self.dataset = config.get("billing_dataset", "billing_export")
        self.table = config.get("billing_table", "gcp_billing_export_v1")
        self.bq_client = bigquery.Client(project=self.project_id)

    def test_connection(self) -> bool:
        """Test GCP BigQuery billing connectivity."""
        try:
            query = f"SELECT 1 FROM `{self.project_id}.{self.dataset}.{self.table}` LIMIT 1"
            list(self.bq_client.query(query).result())
            return True
        except Exception as e:
            logger.warning("GCP billing connection test failed: %s", e)
            return False

    def fetch_costs(
        self, resource_type: str, resource_id: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        """Fetch costs from GCP BigQuery billing export."""
        try:
            query = f"""
                SELECT
                    DATE(usage_start_time) as date,
                    SUM(cost) as amount,
                    currency,
                    SUM(usage.amount) as usage_quantity,
                    usage.unit as usage_unit
                FROM `{self.project_id}.{self.dataset}.{self.table}`
                WHERE resource.name = @resource_id
                    AND DATE(usage_start_time) >= @start_date
                    AND DATE(usage_start_time) < @end_date
                GROUP BY date, currency, usage.unit
                ORDER BY date
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("resource_id", "STRING", resource_id),
                    bigquery.ScalarQueryParameter("start_date", "STRING", start_date),
                    bigquery.ScalarQueryParameter("end_date", "STRING", end_date),
                ]
            )

            results = self.bq_client.query(query, job_config=job_config).result()
            costs = []
            for row in results:
                costs.append({
                    "date": str(row.date),
                    "amount": float(row.amount or 0),
                    "currency": row.currency or "USD",
                    "usage_quantity": float(row.usage_quantity or 0),
                    "usage_unit": row.usage_unit or "",
                })
            return costs
        except Exception as e:
            logger.warning("Failed to fetch GCP costs for %s/%s: %s", resource_type, resource_id, e)
            return []

    def get_recommendations(
        self, resource_type: str, resource_id: str
    ) -> List[Dict[str, Any]]:
        """GCP recommendations require Recommender API (not yet implemented)."""
        return []
