"""
Pydantic 2 response DTOs for the cost tracking / cost-sync-job endpoints.

Scopes `cost_sync_jobs` PyDAL rows to an explicit field set — security-audit
fix for raw `.as_dict()` serialization
(see apps/api/modules/webhooks_alerting/routes/costs.py).

SECURITY FIX (not just a contract fix): `cost_sync_jobs.config_json` holds
cloud-provider billing API credentials -- e.g. AWS `aws_access_key_id`/
`aws_secret_access_key` (see apps/api/services/costs/providers/
aws_cost_explorer.py's `boto3.client(..., aws_access_key_id=config.get(...))`)
-- which `list_sync_jobs` previously returned in full to any
webhooks_alerting:read-scoped caller via `.as_dict()`. Redacted the same way
as sync_configs.config_json (see apps/api/models/pydantic/sync.py).
"""

from datetime import datetime
from typing import Any

from penguin_libs.pydantic.base import ImmutableModel

from shared.redaction import redact_credential_keys


class CostSyncJobResponse(ImmutableModel):
    """A cost sync job — config_json never includes provider credentials."""

    id: int
    name: str
    provider: str
    organization_id: int
    config_json: dict[str, Any] | None = None
    schedule_interval: int | None = None
    enabled: bool | None = None
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> "CostSyncJobResponse":
        """Build from a PyDAL row/dict, redacting provider credentials."""
        d = row.as_dict() if hasattr(row, "as_dict") else dict(row)
        return cls(
            id=d["id"],
            name=d["name"],
            provider=d["provider"],
            organization_id=d["organization_id"],
            config_json=redact_credential_keys(d.get("config_json")),
            schedule_interval=d.get("schedule_interval"),
            enabled=d.get("enabled"),
            last_run_at=d.get("last_run_at"),
            next_run_at=d.get("next_run_at"),
            created_at=d.get("created_at"),
        )


class CostSyncJobListResponse(ImmutableModel):
    """`{"data": [...]}` envelope matching the existing API shape."""

    data: list[CostSyncJobResponse]
