"""Pydantic models for SBOM scanning, components, and vulnerabilities.

These models provide type-safe validation and serialization for SBOM-related
operations including component management, vulnerability tracking, and scan
scheduling.
"""

# flake8: noqa: E501

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, validator

# ==================== SBOM Components ====================


class SBOMComponentDTO(BaseModel):
    """Data transfer object for an SBOM Component."""

    id: int
    tenant_id: int
    village_id: str
    parent_type: str
    parent_id: int
    name: str
    version: str | None = None
    purl: str | None = None
    package_type: str
    scope: str | None = None
    direct: bool = True
    license_id: int | None = None
    license_name: str | None = None
    license_url: str | None = None
    source_file: str | None = None
    repository_url: str | None = None
    homepage_url: str | None = None
    description: str | None = None
    hash_sha256: str | None = None
    hash_sha512: str | None = None
    metadata: dict | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class CreateSBOMComponentRequest(BaseModel):
    """Request to create a new SBOM Component."""

    parent_type: str = Field(
        ..., description="Type of parent resource (service, software, entity)"
    )
    parent_id: int = Field(..., description="ID of parent resource")
    name: str = Field(..., min_length=1, description="Component name")
    package_type: str = Field(
        ...,
        description="Package type (library, framework, application, container, etc)",
    )
    version: str | None = Field(None, description="Component version")
    purl: str | None = Field(None, description="Package URL (PURL) identifier")
    scope: str | None = Field(
        None, description="Scope (required, optional, dev, test, etc)"
    )
    direct: bool = Field(True, description="Direct dependency indicator")
    license_id: int | None = Field(None, description="License ID")
    license_name: str | None = Field(None, description="License name")
    source_file: str | None = Field(None, description="Source file path")
    metadata: dict | None = Field(None, description="Additional component metadata")

    class Config:
        from_attributes = True


class UpdateSBOMComponentRequest(BaseModel):
    """Request to update an SBOM Component."""

    name: str | None = Field(None, description="Component name")
    version: str | None = Field(None, description="Component version")
    purl: str | None = Field(None, description="Package URL identifier")
    package_type: str | None = Field(None, description="Package type")
    scope: str | None = Field(None, description="Dependency scope")
    direct: bool | None = Field(None, description="Direct dependency")
    license_id: int | None = Field(None, description="License ID")
    license_name: str | None = Field(None, description="License name")
    license_url: str | None = Field(None, description="License URL")
    source_file: str | None = Field(None, description="Source file path")
    repository_url: str | None = Field(None, description="Repository URL")
    homepage_url: str | None = Field(None, description="Homepage URL")
    description: str | None = Field(None, description="Component description")
    hash_sha256: str | None = Field(None, description="SHA256 hash")
    hash_sha512: str | None = Field(None, description="SHA512 hash")
    metadata: dict | None = Field(None, description="Metadata")
    is_active: bool | None = Field(None, description="Active status")

    class Config:
        from_attributes = True


# ==================== SBOM Scans ====================


class SBOMScanDTO(BaseModel):
    """Data transfer object for an SBOM Scan."""

    id: int
    tenant_id: int
    village_id: str
    parent_type: str
    parent_id: int
    scan_type: str
    status: str
    repository_url: str | None = None
    repository_branch: str | None = None
    commit_hash: str | None = None
    files_scanned: dict | None = None
    components_found: int = 0
    components_added: int = 0
    components_updated: int = 0
    components_removed: int = 0
    error_message: str | None = None
    scan_duration_ms: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    credential_type: str | None = None
    credential_id: int | None = None
    has_credentials: bool = False

    class Config:
        from_attributes = True


class CreateSBOMScanRequest(BaseModel):
    """Request to create a new SBOM Scan."""

    parent_type: str = Field(
        ..., description="Type of parent resource (service, software, entity)"
    )
    parent_id: int = Field(..., description="ID of parent resource")
    scan_type: str = Field(
        ...,
        description="Scan type (manifest, lockfile, repository, container, etc)",
    )
    repository_url: str | None = Field(
        None, description="Repository URL for remote scans"
    )
    repository_branch: str | None = Field(
        None, description="Repository branch for remote scans"
    )

    @validator("scan_type")
    def validate_scan_type(cls, v):
        """Validate scan type is supported."""
        valid_types = {
            "manifest",
            "lockfile",
            "repository",
            "container",
            "binary",
            "source",
        }
        if v not in valid_types:
            raise ValueError(f"Invalid scan_type: {v}. Must be one of {valid_types}")
        return v

    class Config:
        from_attributes = True


class SBOMScanScheduleDTO(BaseModel):
    """Data transfer object for an SBOM Scan Schedule."""

    id: int
    tenant_id: int
    village_id: str
    parent_type: str
    parent_id: int
    schedule_cron: str
    is_active: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    credential_type: str | None = None
    credential_id: int | None = None
    has_credentials: bool = False

    class Config:
        from_attributes = True


# ==================== Vulnerabilities ====================


class VulnerabilityDTO(BaseModel):
    """Data transfer object for a Vulnerability."""

    id: int
    tenant_id: int
    village_id: str
    cve_id: str
    aliases: list | None = None
    severity: str
    cvss_score: float | None = None
    cvss_vector: str | None = None
    title: str | None = None
    description: str | None = None
    affected_packages: list | None = None
    fixed_versions: list | None = None
    references: list | None = None
    published_at: datetime | None = None
    modified_at: datetime | None = None
    source: str | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime | None = None

    @validator("severity")
    def validate_severity(cls, v):
        """Validate severity level."""
        valid_severities = {"critical", "high", "medium", "low", "info"}
        if v.lower() not in valid_severities:
            raise ValueError(
                f"Invalid severity: {v}. Must be one of {valid_severities}"
            )
        return v.lower()

    class Config:
        from_attributes = True


class ComponentVulnerabilityDTO(BaseModel):
    """Data transfer object for Component-Vulnerability association."""

    id: int
    tenant_id: int
    component_id: int
    vulnerability_id: int
    status: str
    remediation_notes: str | None = None
    remediated_at: datetime | None = None
    remediated_by_id: int | None = None
    created_at: datetime
    updated_at: datetime | None = None

    @validator("status")
    def validate_status(cls, v):
        """Validate remediation status."""
        valid_statuses = {"open", "in_progress", "resolved", "ignored"}
        if v not in valid_statuses:
            raise ValueError(f"Invalid status: {v}. Must be one of {valid_statuses}")
        return v

    class Config:
        from_attributes = True


# ==================== License Policies ====================


class LicensePolicyDTO(BaseModel):
    """Data transfer object for a License Policy."""

    id: int
    tenant_id: int
    organization_id: int | None = None
    village_id: str
    name: str
    description: str | None = None
    allowed_licenses: list | None = None
    denied_licenses: list | None = None
    action: str
    is_active: bool = True
    created_at: datetime
    updated_at: datetime | None = None

    @validator("action")
    def validate_action(cls, v):
        """Validate policy action."""
        valid_actions = {"warn", "block", "audit"}
        if v not in valid_actions:
            raise ValueError(f"Invalid action: {v}. Must be one of {valid_actions}")
        return v

    class Config:
        from_attributes = True


class CreateLicensePolicyRequest(BaseModel):
    """Request to create a new License Policy."""

    name: str = Field(..., min_length=1, description="Policy name")
    organization_id: int = Field(..., description="Organization ID")
    action: str = Field("warn", description="Policy action (warn, block, audit)")
    description: str | None = Field(None, description="Policy description")
    allowed_licenses: list | None = Field(
        default_factory=list, description="List of allowed licenses"
    )
    denied_licenses: list | None = Field(
        default_factory=list, description="List of denied licenses"
    )
    is_active: bool = Field(True, description="Policy active status")

    @validator("action")
    def validate_action(cls, v):
        """Validate policy action."""
        valid_actions = {"warn", "block", "audit"}
        if v not in valid_actions:
            raise ValueError(f"Invalid action: {v}. Must be one of {valid_actions}")
        return v

    class Config:
        from_attributes = True


class UpdateLicensePolicyRequest(BaseModel):
    """Request to update a License Policy."""

    name: str | None = Field(None, description="Policy name")
    description: str | None = Field(None, description="Policy description")
    allowed_licenses: list | None = Field(None, description="Allowed licenses")
    denied_licenses: list | None = Field(None, description="Denied licenses")
    action: str | None = Field(None, description="Policy action")
    is_active: bool | None = Field(None, description="Active status")

    @validator("action")
    def validate_action(cls, v):
        """Validate policy action."""
        if v is None:
            return v
        valid_actions = {"warn", "block", "audit"}
        if v not in valid_actions:
            raise ValueError(f"Invalid action: {v}. Must be one of {valid_actions}")
        return v

    class Config:
        from_attributes = True


# ==================== SBOM Upload ====================


class UploadSBOMRequest(BaseModel):
    """Request to upload and import an SBOM file."""

    parent_type: str = Field(
        ..., description="Type of parent resource (service, software)"
    )
    parent_id: int = Field(..., description="ID of parent resource")
    file_content: str = Field(..., min_length=1, description="SBOM file content")
    filename: str = Field(
        ..., min_length=1, description="Original filename (e.g., 'cyclonedx.json')"
    )

    @validator("parent_type")
    def validate_parent_type(cls, v):
        """Validate parent type."""
        valid_types = {"service", "software"}
        if v not in valid_types:
            raise ValueError(f"Invalid parent_type: {v}. Must be one of {valid_types}")
        return v

    class Config:
        from_attributes = True


# ==================== SBOM Scan Results ====================


class SubmitSBOMResultsRequest(BaseModel):
    """Request to submit SBOM scan results."""

    success: bool = Field(..., description="Whether scan succeeded")
    components: list | None = Field(
        default_factory=list, description="List of component dicts"
    )
    files_scanned: list | None = Field(
        default_factory=list, description="Files scanned"
    )
    commit_hash: str | None = Field(None, description="Git commit hash")
    error_message: str | None = Field(None, description="Error message if failed")
    scan_duration_ms: int | None = Field(None, description="Scan duration in ms")

    class Config:
        from_attributes = True
