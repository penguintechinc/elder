"""Pydantic 2 models for AI Resources domain objects."""

# flake8: noqa: E501

from datetime import datetime
from typing import Any, Literal

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field

AIResourceType = Literal[
    "llm",
    "ai_gateway",
    "mcp_server",
    "code_mode_server",
    "utcp_server",
]
AIResourceStatus = Literal["pending_review", "allowed", "restricted", "not_allowed"]
AIAgreementLevel = Literal["user", "team", "enterprise"]


class AIResourceDTO(ImmutableModel):
    """Immutable AI resource data transfer object."""

    id: int
    tenant_id: int
    name: str
    description: str | None = None
    organization_id: int
    resource_type: AIResourceType
    provider: str | None = None
    model_name: str | None = None
    endpoint_url: str | None = None
    status: AIResourceStatus
    agreement_level: AIAgreementLevel | None = None
    plan_info: str | None = None
    budget_monthly: float | None = None
    cost_per_10m_tokens: float | None = None
    seats: int | None = None
    parent_system_id: int | None = None
    owner_id: int | None = None
    notes: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    village_id: str | None = None


class AIResourceListResponse(ImmutableModel):
    """Paginated list of AI resources."""

    items: list[AIResourceDTO]
    total: int
    page: int
    per_page: int
    pages: int


class CreateAIResourceRequest(RequestModel):
    """Request to create a new AI resource."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    organization_id: int = Field(..., ge=1)
    resource_type: AIResourceType
    provider: str | None = Field(default=None, max_length=255)
    model_name: str | None = Field(default=None, max_length=255)
    endpoint_url: str | None = Field(default=None, max_length=1024)
    status: AIResourceStatus = "pending_review"
    agreement_level: AIAgreementLevel | None = None
    plan_info: str | None = Field(default=None, max_length=255)
    budget_monthly: float | None = Field(default=None, ge=0)
    cost_per_10m_tokens: float | None = Field(default=None, ge=0)
    seats: int | None = Field(default=None, ge=0)
    parent_system_id: int | None = Field(default=None, ge=1)
    owner_id: int | None = Field(default=None, ge=1)
    notes: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    is_active: bool = True


class UpdateAIResourceRequest(RequestModel):
    """Request to update an existing AI resource."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    organization_id: int | None = Field(default=None, ge=1)
    resource_type: AIResourceType | None = None
    provider: str | None = Field(default=None, max_length=255)
    model_name: str | None = Field(default=None, max_length=255)
    endpoint_url: str | None = Field(default=None, max_length=1024)
    status: AIResourceStatus | None = None
    agreement_level: AIAgreementLevel | None = None
    plan_info: str | None = Field(default=None, max_length=255)
    budget_monthly: float | None = Field(default=None, ge=0)
    cost_per_10m_tokens: float | None = Field(default=None, ge=0)
    seats: int | None = Field(default=None, ge=0)
    parent_system_id: int | None = Field(default=None, ge=1)
    owner_id: int | None = Field(default=None, ge=1)
    notes: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    is_active: bool | None = None
