"""Cost service - business logic for resource cost tracking."""

# flake8: noqa: E501

import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from apps.api.services.costs.base import BaseCostProvider
from apps.api.services.costs.providers.aws_cost_explorer import AWSCostExplorerProvider
from apps.api.services.costs.providers.gcp_billing import GCPBillingProvider
from apps.api.services.costs.providers.manual import ManualCostProvider

logger = logging.getLogger(__name__)


class CostService:
    """Service layer for resource cost operations."""

    PROVIDER_MAP = {
        "aws_cost_explorer": AWSCostExplorerProvider,
        "gcp_billing": GCPBillingProvider,
        "manual": ManualCostProvider,
    }

    def __init__(self, db):
        self.db = db

    def _get_provider(self, provider_name: str, config: Dict[str, Any]) -> BaseCostProvider:
        """Get configured cost provider instance."""
        provider_cls = self.PROVIDER_MAP.get(provider_name)
        if not provider_cls:
            raise ValueError(f"Unsupported cost provider: {provider_name}")
        return provider_cls(config)

    def get_resource_costs(self, resource_type: str, resource_id: int) -> Optional[Dict[str, Any]]:
        """Get cost summary for a resource."""
        record = (
            self.db(
                (self.db.resource_costs.resource_type == resource_type)
                & (self.db.resource_costs.resource_id == resource_id)
            )
            .select()
            .first()
        )

        if not record:
            return None

        result = record.as_dict()

        # Get recent cost history
        history = (
            self.db(self.db.cost_history.resource_cost_id == record.id)
            .select(orderby=~self.db.cost_history.snapshot_date, limitby=(0, 30))
        )
        result["history"] = [h.as_dict() for h in history]

        return result

    def update_resource_costs(
        self, resource_type: str, resource_id: int, cost_data: Dict[str, Any]
    ) -> int:
        """Create or update cost entry for a resource."""
        existing = (
            self.db(
                (self.db.resource_costs.resource_type == resource_type)
                & (self.db.resource_costs.resource_id == resource_id)
            )
            .select()
            .first()
        )

        update_fields = {
            "cost_to_date": cost_data.get("cost_to_date"),
            "cost_ytd": cost_data.get("cost_ytd"),
            "cost_mtd": cost_data.get("cost_mtd"),
            "estimated_monthly_cost": cost_data.get("estimated_monthly_cost"),
            "currency": cost_data.get("currency", "USD"),
            "cost_provider": cost_data.get("cost_provider", "manual"),
            "recommendations": cost_data.get("recommendations"),
            "created_by_identity_id": cost_data.get("created_by_identity_id"),
            "resource_created_at": cost_data.get("resource_created_at"),
            "last_synced_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        # Remove None values
        update_fields = {k: v for k, v in update_fields.items() if v is not None}

        if existing:
            self.db(self.db.resource_costs.id == existing.id).update(**update_fields)
            return existing.id

        update_fields.update({
            "resource_type": resource_type,
            "resource_id": resource_id,
            "organization_id": cost_data.get("organization_id", 1),
            "created_at": datetime.utcnow(),
        })

        return self.db.resource_costs.insert(**update_fields)

    def sync_costs_from_provider(self, job_id: int) -> Dict[str, Any]:
        """Run a cost sync job from a configured provider."""
        job = self.db.cost_sync_jobs[job_id]
        if not job:
            raise ValueError(f"Cost sync job not found: {job_id}")

        provider = self._get_provider(job.provider, job.config_json or {})
        organization_id = job.organization_id

        # Update job run time
        self.db(self.db.cost_sync_jobs.id == job_id).update(
            last_run_at=datetime.utcnow()
        )

        synced = 0
        errors = 0

        # Fetch costs for all tracked resources in this org
        resources = self.db(
            (self.db.resource_costs.organization_id == organization_id)
            & (self.db.resource_costs.cost_provider == job.provider)
        ).select()

        today = date.today()
        start_of_month = today.replace(day=1).isoformat()
        end_date = today.isoformat()

        for resource in resources:
            try:
                costs = provider.fetch_costs(
                    resource.resource_type,
                    str(resource.resource_id),
                    start_of_month,
                    end_date,
                )

                for cost_entry in costs:
                    self.db.cost_history.insert(
                        resource_cost_id=resource.id,
                        snapshot_date=cost_entry["date"],
                        cost_amount=Decimal(str(cost_entry["amount"])),
                        usage_quantity=cost_entry.get("usage_quantity"),
                        usage_unit=cost_entry.get("usage_unit"),
                        created_at=datetime.utcnow(),
                    )

                self.calculate_aggregates(resource.id)

                # Fetch recommendations
                recs = provider.get_recommendations(
                    resource.resource_type, str(resource.resource_id)
                )
                if recs:
                    self.db(self.db.resource_costs.id == resource.id).update(
                        recommendations=recs,
                        updated_at=datetime.utcnow(),
                    )

                synced += 1
            except Exception as e:
                logger.warning("Failed to sync costs for resource %s: %s", resource.id, e)
                errors += 1

        self.db.commit()

        return {"synced": synced, "errors": errors, "job_id": job_id}

    def calculate_aggregates(self, resource_cost_id: int) -> None:
        """Recalculate MTD/YTD/total from cost_history."""
        today = date.today()
        start_of_month = today.replace(day=1)
        start_of_year = today.replace(month=1, day=1)

        # Total cost to date
        total = self.db.cost_history.cost_amount.sum()
        total_result = self.db(
            self.db.cost_history.resource_cost_id == resource_cost_id
        ).select(total).first()
        cost_to_date = total_result[total] or Decimal("0")

        # Year to date
        ytd_result = self.db(
            (self.db.cost_history.resource_cost_id == resource_cost_id)
            & (self.db.cost_history.snapshot_date >= start_of_year)
        ).select(total).first()
        cost_ytd = ytd_result[total] or Decimal("0")

        # Month to date
        mtd_result = self.db(
            (self.db.cost_history.resource_cost_id == resource_cost_id)
            & (self.db.cost_history.snapshot_date >= start_of_month)
        ).select(total).first()
        cost_mtd = mtd_result[total] or Decimal("0")

        self.db(self.db.resource_costs.id == resource_cost_id).update(
            cost_to_date=cost_to_date,
            cost_ytd=cost_ytd,
            cost_mtd=cost_mtd,
            updated_at=datetime.utcnow(),
        )
