"""
Pydantic 2 response DTOs for the discovery sync module.

Scopes `sync_configs`/`sync_conflicts` PyDAL rows to an explicit field set —
security-audit fix for raw `.as_dict()` serialization
(see apps/api/modules/discovery/routes/sync.py).

SECURITY FIX (not just a contract fix): `sync_configs.webhook_secret` and
`sync_configs.config_json` (which holds third-party platform credentials,
e.g. `api_token` for the GitHub/GitLab/Jira/Trello/OpenProject connectors —
see apps/worker/sync/platforms/github_client.py's `config.get("api_token")`)
were previously serialized in full to any `discovery:read`-scoped caller via
`.as_dict()`. This DTO drops `webhook_secret` entirely (replaced with a
`has_webhook_secret` boolean) and redacts well-known credential keys inside
`config_json` rather than omitting the whole blob, since callers legitimately
need non-secret operational fields (e.g. `elder_organization_id`).
"""

from datetime import datetime
from typing import Any

from penguin_libs.pydantic.base import ImmutableModel

from shared.redaction import redact_credential_keys

# Backward-compatible alias -- redaction logic now lives in
# shared.redaction.redact_credential_keys (shared with
# apps/api/models/pydantic/cost.py's cost_sync_jobs.config_json redaction).
redact_config_json = redact_credential_keys


class SyncConfigResponse(ImmutableModel):
    """A sync configuration — never includes webhook_secret or raw credentials."""

    id: int
    name: str
    platform: str
    enabled: bool
    sync_interval: int
    batch_fallback_enabled: bool
    batch_size: int
    two_way_create: bool
    webhook_enabled: bool
    has_webhook_secret: bool
    last_sync_at: datetime | None = None
    last_batch_sync_at: datetime | None = None
    config_json: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> "SyncConfigResponse":
        """Build from a PyDAL row/dict, redacting secret fields."""
        d = row.as_dict() if hasattr(row, "as_dict") else dict(row)
        return cls(
            id=d["id"],
            name=d["name"],
            platform=d["platform"],
            enabled=d["enabled"],
            sync_interval=d["sync_interval"],
            batch_fallback_enabled=d["batch_fallback_enabled"],
            batch_size=d["batch_size"],
            two_way_create=d["two_way_create"],
            webhook_enabled=d["webhook_enabled"],
            has_webhook_secret=bool(d.get("webhook_secret")),
            last_sync_at=d.get("last_sync_at"),
            last_batch_sync_at=d.get("last_batch_sync_at"),
            config_json=redact_config_json(d.get("config_json")),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )


class SyncConfigListResponse(ImmutableModel):
    """List of sync configurations."""

    configs: list[SyncConfigResponse]


class SyncConfigEnvelopeResponse(ImmutableModel):
    """Single sync configuration wrapped in a `config` key (matches existing
    API shape: `{"config": {...}}`)."""

    config: SyncConfigResponse


class SyncConflictResponse(ImmutableModel):
    """A sync conflict record between Elder and an external platform."""

    id: int
    mapping_id: int
    conflict_type: str
    elder_data: dict[str, Any] | list[Any] | None = None
    external_data: dict[str, Any] | list[Any] | None = None
    resolution_strategy: str | None = None
    resolved: bool
    resolved_at: datetime | None = None
    resolved_by_id: int | None = None
    created_at: datetime | None = None


class SyncConflictEnvelopeResponse(ImmutableModel):
    """Single sync conflict wrapped in a `conflict` key."""

    conflict: SyncConflictResponse
