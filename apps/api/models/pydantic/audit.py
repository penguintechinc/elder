"""
Pydantic 2 response DTOs for the audit retention policy endpoints.

Scopes `apps.api.models.auth_providers.AuditRetentionPolicy` PyDAL rows to
the table's actual columns — security-audit fix for raw `.as_dict()`
serialization (see apps/api/api/v1/audit.py). NOTE: the route code also
references `description`/`event_types` fields that do not exist on this
table (pre-existing, out of scope for this response-shape fix — flagged
separately).
"""

from datetime import datetime

from penguin_libs.pydantic.base import ImmutableModel


class AuditRetentionPolicyResponse(ImmutableModel):
    """A single audit log retention policy."""

    id: int
    resource_type: str
    retention_days: int
    enabled: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AuditRetentionPolicyListResponse(ImmutableModel):
    """List of audit log retention policies."""

    policies: list[AuditRetentionPolicyResponse]
    count: int
