"""
Pydantic 2 response DTOs for the data stores compliance-inventory module.

Scopes `data_stores`/`issue_labels`/`data_store_labels` PyDAL rows to an
explicit field set — security-audit fix for raw `.as_dict()` serialization
(see apps/api/modules/infrastructure/routes/data_stores.py). No secret/PII
columns on these tables (data_stores tracks *classification metadata* —
contains_pii/contains_phi/contains_pci flags — not actual sensitive payload
data), so this is a contract fix, not a functional trim.
"""

from datetime import datetime
from typing import Any

from penguin_libs.pydantic.base import ImmutableModel


class DataStoreResponse(ImmutableModel):
    """A single data store compliance-inventory record."""

    id: int
    tenant_id: int
    organization_id: int | None = None
    name: str
    external_id: str | None = None
    description: str | None = None
    storage_type: str | None = None
    storage_provider: str | None = None
    location_region: str | None = None
    location_physical: str | None = None
    data_classification: str | None = None
    encryption_at_rest: bool | None = None
    encryption_in_transit: bool | None = None
    encryption_key_id: int | None = None
    retention_days: int | None = None
    backup_enabled: bool | None = None
    backup_frequency: str | None = None
    access_control_type: str | None = None
    poc_identity_id: int | None = None
    compliance_frameworks: list[Any] | dict[str, Any] | None = None
    contains_pii: bool | None = None
    contains_phi: bool | None = None
    contains_pci: bool | None = None
    size_bytes: int | None = None
    last_access_audit: datetime | None = None
    metadata: dict[str, Any] | None = None
    created_by: int | None = None
    is_active: bool | None = None
    village_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DataStoreListResponse(ImmutableModel):
    """Paginated list of data stores."""

    items: list[DataStoreResponse]
    total: int
    page: int
    per_page: int
    pages: int


class DataStoreLabelResponse(ImmutableModel):
    """A label attached to a data store (from the shared `labels` table)."""

    id: int
    name: str
    color: str
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DataStoreLabelListResponse(ImmutableModel):
    """List of labels for a data store."""

    labels: list[DataStoreLabelResponse]


class DataStoreLabelAssignmentResponse(ImmutableModel):
    """A data-store-to-label assignment record."""

    id: int
    data_store_id: int
    label_id: int
    created_at: datetime | None = None
