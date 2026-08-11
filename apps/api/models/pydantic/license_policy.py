"""
Pydantic 2 domain models for license policy management.

Provides request and response models for license policy operations with
Pydantic validation and type safety.
"""

# flake8: noqa: E501

from datetime import datetime
from typing import Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from penguin_libs.pydantic.types import Description1000, Name255


class LicensePolicyDTO(ImmutableModel):
    """Immutable License Policy data transfer object."""

    id: int
    tenant_id: int
    organization_id: int | None
    village_id: str
    name: str
    description: str | None
    allowed_licenses: list[str] | None
    denied_licenses: list[str] | None
    action: str  # 'warn' or 'block'
    is_active: bool
    created_at: datetime
    updated_at: datetime | None


class CreateLicensePolicyRequest(RequestModel):
    """Request to create a new License Policy."""

    name: Name255
    organization_id: int
    action: str = "warn"  # 'warn' or 'block'
    description: Description1000 | None = None
    allowed_licenses: list[str] | None = None
    denied_licenses: list[str] | None = None
    is_active: bool = True


class UpdateLicensePolicyRequest(RequestModel):
    """Request to update a License Policy."""

    name: Name255 | None = None
    description: Description1000 | None = None
    allowed_licenses: list[str] | None = None
    denied_licenses: list[str] | None = None
    action: str | None = None  # 'warn' or 'block'
    is_active: bool | None = None


__all__ = [
    "LicensePolicyDTO",
    "CreateLicensePolicyRequest",
    "UpdateLicensePolicyRequest",
]
