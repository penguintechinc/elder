"""
Pydantic 2 models for Service domain objects.

Provides validated Pydantic 2 equivalents of Service dataclasses:
- ServiceDTO: Immutable frozen DTO for API responses
- CreateServiceRequest: Request validation with security hardening
- UpdateServiceRequest: Flexible update request with all optional fields
"""

# flake8: noqa: E501

from datetime import datetime
from typing import Optional

from penguin_libs.pydantic.base import ImmutableModel, RequestModel
from pydantic import Field


class ServiceDTO(ImmutableModel):
    """
    Immutable Service data transfer object.

    Represents a complete Service record with all fields. Used for API responses
    and data serialization. Frozen to prevent accidental modifications.

    Attributes:
        id: Unique database identifier
        tenant_id: Associated tenant ID
        name: Service name
        description: Optional detailed description
        organization_id: Associated organization ID
        domains: Optional list of service domains
        paths: Optional list of service paths
        poc_identity_id: Optional point of contact identity ID
        language: Optional programming language
        deployment_method: Optional deployment method (e.g., 'docker', 'kubernetes')
        deployment_type: Optional deployment type (e.g., 'containerized', 'vm')
        is_public: Public availability flag
        port: Optional service port number
        health_endpoint: Optional health check endpoint
        repository_url: Optional repository URL
        documentation_url: Optional documentation URL
        sla_uptime: Optional SLA uptime percentage
        sla_response_time_ms: Optional SLA response time in milliseconds
        notes: Optional additional notes
        tags: Optional list of classification tags
        status: Service status (e.g., 'active', 'inactive', 'maintenance')
        created_at: Creation timestamp
        updated_at: Last update timestamp
        village_id: Optional unique hierarchical identifier
    """

    id: int
    tenant_id: int
    name: str
    description: str | None = None
    organization_id: int
    domains: list[str] | None = None
    paths: list[str] | None = None
    poc_identity_id: int | None = None
    language: str | None = None
    deployment_method: str | None = None
    deployment_type: str | None = None
    is_public: bool
    port: int | None = None
    health_endpoint: str | None = None
    repository_url: str | None = None
    documentation_url: str | None = None
    sla_uptime: float | None = None
    sla_response_time_ms: int | None = None
    notes: str | None = None
    tags: list[str] | None = None
    status: str
    created_at: datetime
    updated_at: datetime | None = None
    village_id: str | None = None


class CreateServiceRequest(RequestModel):
    """
    Request to create a new Service.

    Validates all required fields and enforces security constraints.
    Uses RequestModel to reject unknown fields and prevent injection attacks.

    Attributes:
        name: Service name (required)
        organization_id: Associated organization ID (required, must be >= 1)
        description: Optional detailed description
        domains: Optional list of service domains
        paths: Optional list of service paths
        poc_identity_id: Optional point of contact identity ID
        language: Optional programming language
        deployment_method: Optional deployment method
        deployment_type: Optional deployment type
        is_public: Public availability flag (default: False)
        port: Optional service port number
        health_endpoint: Optional health check endpoint
        repository_url: Optional repository URL
        documentation_url: Optional documentation URL
        sla_uptime: Optional SLA uptime percentage
        sla_response_time_ms: Optional SLA response time in milliseconds
        notes: Optional additional notes
        tags: Optional classification tags (default: empty list)
        status: Service status (default: 'active')
    """

    name: str = Field(
        ...,
        description="Service name",
    )
    organization_id: int = Field(
        ...,
        ge=1,
        description="Associated organization ID (must be positive)",
    )
    description: str | None = Field(
        default=None,
        description="Optional detailed description",
    )
    domains: list[str] | None = Field(
        default=None,
        description="Optional list of service domains",
    )
    paths: list[str] | None = Field(
        default=None,
        description="Optional list of service paths",
    )
    poc_identity_id: int | None = Field(
        default=None,
        description="Optional point of contact identity ID",
    )
    language: str | None = Field(
        default=None,
        description="Optional programming language",
    )
    deployment_method: str | None = Field(
        default=None,
        description="Optional deployment method (e.g., 'docker', 'kubernetes')",
    )
    deployment_type: str | None = Field(
        default=None,
        description="Optional deployment type (e.g., 'containerized', 'vm')",
    )
    is_public: bool = Field(
        default=False,
        description="Public availability flag",
    )
    port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="Optional service port number (1-65535)",
    )
    health_endpoint: str | None = Field(
        default=None,
        description="Optional health check endpoint",
    )
    repository_url: str | None = Field(
        default=None,
        description="Optional repository URL",
    )
    documentation_url: str | None = Field(
        default=None,
        description="Optional documentation URL",
    )
    sla_uptime: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Optional SLA uptime percentage (0-100)",
    )
    sla_response_time_ms: int | None = Field(
        default=None,
        ge=0,
        description="Optional SLA response time in milliseconds (must be non-negative)",
    )
    notes: str | None = Field(
        default=None,
        description="Optional additional notes",
    )
    tags: list[str] | None = Field(
        default_factory=list,
        description="Optional classification tags",
    )
    status: str = Field(
        default="active",
        description="Service status",
    )


class UpdateServiceRequest(RequestModel):
    """
    Request to update an existing Service.

    All fields are optional to support partial updates. Uses RequestModel
    to reject unknown fields and prevent injection attacks.

    Attributes:
        name: Service name (optional)
        description: Detailed description (optional)
        domains: List of service domains (optional)
        paths: List of service paths (optional)
        poc_identity_id: Point of contact identity ID (optional)
        language: Programming language (optional)
        deployment_method: Deployment method (optional)
        deployment_type: Deployment type (optional)
        is_public: Public availability flag (optional)
        port: Service port number (optional)
        health_endpoint: Health check endpoint (optional)
        repository_url: Repository URL (optional)
        documentation_url: Documentation URL (optional)
        sla_uptime: SLA uptime percentage (optional)
        sla_response_time_ms: SLA response time in milliseconds (optional)
        notes: Additional notes (optional)
        tags: Classification tags (optional)
        status: Service status (optional)
    """

    name: str | None = Field(
        default=None,
        description="Service name",
    )
    description: str | None = Field(
        default=None,
        description="Detailed description",
    )
    domains: list[str] | None = Field(
        default=None,
        description="List of service domains",
    )
    paths: list[str] | None = Field(
        default=None,
        description="List of service paths",
    )
    poc_identity_id: int | None = Field(
        default=None,
        description="Point of contact identity ID",
    )
    language: str | None = Field(
        default=None,
        description="Programming language",
    )
    deployment_method: str | None = Field(
        default=None,
        description="Deployment method (e.g., 'docker', 'kubernetes')",
    )
    deployment_type: str | None = Field(
        default=None,
        description="Deployment type (e.g., 'containerized', 'vm')",
    )
    is_public: bool | None = Field(
        default=None,
        description="Public availability flag",
    )
    port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="Service port number (1-65535)",
    )
    health_endpoint: str | None = Field(
        default=None,
        description="Health check endpoint",
    )
    repository_url: str | None = Field(
        default=None,
        description="Repository URL",
    )
    documentation_url: str | None = Field(
        default=None,
        description="Documentation URL",
    )
    sla_uptime: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="SLA uptime percentage (0-100)",
    )
    sla_response_time_ms: int | None = Field(
        default=None,
        ge=0,
        description="SLA response time in milliseconds (must be non-negative)",
    )
    notes: str | None = Field(
        default=None,
        description="Additional notes",
    )
    tags: list[str] | None = Field(
        default=None,
        description="Classification tags",
    )
    status: str | None = Field(
        default=None,
        description="Service status",
    )
