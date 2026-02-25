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
    organization_id: Optional[int]
    village_id: str
    name: str
    description: Optional[str]
    allowed_licenses: Optional[list[str]]
    denied_licenses: Optional[list[str]]
    action: str  # 'warn' or 'block'
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]


class CreateLicensePolicyRequest(RequestModel):
    """Request to create a new License Policy."""

    name: Name255
    organization_id: int
    action: str = "warn"  # 'warn' or 'block'
    description: Optional[Description1000] = None
    allowed_licenses: Optional[list[str]] = None
    denied_licenses: Optional[list[str]] = None
    is_active: bool = True


class UpdateLicensePolicyRequest(RequestModel):
    """Request to update a License Policy."""

    name: Optional[Name255] = None
    description: Optional[Description1000] = None
    allowed_licenses: Optional[list[str]] = None
    denied_licenses: Optional[list[str]] = None
    action: Optional[str] = None  # 'warn' or 'block'
    is_active: Optional[bool] = None


__all__ = [
    "LicensePolicyDTO",
    "CreateLicensePolicyRequest",
    "UpdateLicensePolicyRequest",
]
