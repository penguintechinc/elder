"""Python 3.12 dataclasses with slots for Elder application.

Using @dataclass(slots=True) provides 30-50% memory reduction and faster attribute access.
"""

# flake8: noqa: E501

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Optional, Union

# ==================== Organization Units (OUs) ====================


@dataclass(slots=True, frozen=True)
class OrganizationDTO:
    """Immutable Organization Unit (OU) data transfer object."""

    id: int
    name: str
    description: str | None
    type: str | None  # organization type
    parent_id: int | None
    owner_identity_id: int | None
    owner_group_id: int | None
    created_at: datetime
    updated_at: datetime
    slug: str | None = None
    tenant_id: int | None = None
    display_name: str | None = None
    cloud_provider: str | None = None
    cloud_account_id: str | None = None
    region: str | None = None
    is_active: bool = True
    settings: dict | None = None
    tags: list | None = None
    metadata: dict | None = None


@dataclass(slots=True)
class CreateOrganizationRequest:
    """Request to create a new Organization Unit (OU)."""

    name: str
    description: str | None = None
    type: str | None = None
    parent_id: int | None = None
    owner_identity_id: int | None = None
    owner_group_id: int | None = None
    cloud_provider: str | None = None
    cloud_account_id: str | None = None
    region: str | None = None
    slug: str | None = None
    display_name: str | None = None


@dataclass(slots=True)
class UpdateOrganizationRequest:
    """Request to update an Organization Unit (OU)."""

    name: str | None = None
    description: str | None = None
    type: str | None = None
    parent_id: int | None = None
    owner_identity_id: int | None = None
    owner_group_id: int | None = None
    cloud_provider: str | None = None
    cloud_account_id: str | None = None
    region: str | None = None
    slug: str | None = None
    display_name: str | None = None


# ==================== Entities ====================


@dataclass(slots=True, frozen=True)
class EntityDTO:
    """Immutable Entity data transfer object."""

    id: int
    name: str
    type: str
    organization_id: int | None = None
    parent_id: int | None = None
    sub_type: str | None = None
    external_id: str | None = None
    cloud_provider: str | None = None
    region: str | None = None
    status: str | None = None
    is_managed: bool = False
    # list[str] for user-applied classification tags (via CreateEntityRequest),
    # or dict[str, str] for discovered K8s labels / cloud provider tags
    tags: list | dict | None = None
    metadata: dict | None = None
    last_seen_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class CreateEntityRequest:
    """Request to create a new Entity."""

    name: str
    entity_type: str
    organization_id: int
    description: str | None = None
    sub_type: str | None = None
    parent_id: int | None = None
    attributes: dict | None = None
    tags: list[str] | None = field(default_factory=list)
    default_metadata: dict | None = None
    is_active: bool = True


@dataclass(slots=True)
class UpdateEntityRequest:
    """Request to update an Entity."""

    name: str | None = None
    description: str | None = None
    entity_type: str | None = None
    sub_type: str | None = None
    organization_id: int | None = None
    parent_id: int | None = None
    attributes: dict | None = None
    tags: list[str] | None = None
    default_metadata: dict | None = None
    is_active: bool | None = None


# ==================== Dependencies ====================


@dataclass(slots=True, frozen=True)
class DependencyDTO:
    """Immutable Dependency data transfer object."""

    id: int
    tenant_id: int
    source_type: str
    source_id: int
    target_type: str
    target_id: int
    dependency_type: str
    metadata: dict | None
    created_at: datetime
    updated_at: datetime
    village_id: str | None = None


@dataclass(slots=True)
class CreateDependencyRequest:
    """Request to create a new Dependency."""

    source_type: str
    source_id: int
    target_type: str
    target_id: int
    dependency_type: str
    metadata: dict | None = None


# ==================== Identities ====================


@dataclass(slots=True)
class IdentityDTO:
    """Immutable Identity data transfer object."""

    id: int
    username: str
    email: str | None
    created_at: datetime
    updated_at: datetime
    identity_type: str | None = None
    tenant_id: int | None = None
    external_id: str | None = None
    provider: str | None = None
    full_name: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    is_active: bool = True
    is_service_account: bool = False
    metadata: dict | None = None
    last_seen_at: datetime | None = None


@dataclass(slots=True)
class CreateIdentityRequest:
    """Request to create a new Identity."""

    username: str
    identity_type: str
    auth_provider: str
    email: str | None = None
    full_name: str | None = None
    password: str | None = None  # Will be hashed
    auth_provider_id: str | None = None
    is_active: bool = True
    # NOTE: is_superuser is intentionally NOT accepted here — allowing a client
    # to set it on create is a privilege-escalation / mass-assignment hole. New
    # identities are always created non-superuser; elevating requires a separate
    # admin-controlled path.
    mfa_enabled: bool = False


@dataclass(slots=True)
class UpdateIdentityRequest:
    """Request to update an Identity."""

    email: str | None = None
    full_name: str | None = None
    password: str | None = None  # Will be hashed
    is_active: bool | None = None
    mfa_enabled: bool | None = None


# ==================== Identity Groups ====================


@dataclass(slots=True, frozen=True)
class IdentityGroupDTO:
    """Immutable Identity Group data transfer object."""

    id: int
    name: str
    description: str | None
    ldap_dn: str | None = None
    saml_group: str | None = None
    is_active: bool = True
    created_at: datetime = None
    updated_at: datetime = None


@dataclass(slots=True)
class CreateIdentityGroupRequest:
    """Request to create a new Identity Group."""

    name: str
    description: str | None = None
    ldap_dn: str | None = None
    saml_group: str | None = None
    is_active: bool = True


# ==================== Roles & Permissions ====================


@dataclass(slots=True, frozen=True)
class RoleDTO:
    """Immutable Role data transfer object."""

    id: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class PermissionDTO:
    """Immutable Permission data transfer object."""

    id: int
    name: str
    resource_type: str
    action: str
    description: str | None
    created_at: datetime
    updated_at: datetime


# ==================== Resource Roles (Enterprise) ====================


@dataclass(slots=True, frozen=True)
class ResourceRoleDTO:
    """Immutable Resource Role data transfer object."""

    id: int
    identity_id: int | None
    group_id: int | None
    role: str  # maintainer, operator, viewer
    resource_type: str
    resource_id: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CreateResourceRoleRequest:
    """Request to create a Resource Role assignment."""

    role: str
    resource_type: str
    identity_id: int | None = None
    group_id: int | None = None
    resource_id: int | None = None


# ==================== Issues (Enterprise) ====================


@dataclass(slots=True, frozen=True)
class IssueDTO:
    """Immutable Issue data transfer object."""

    id: int
    title: str
    description: str | None
    status: str
    priority: str
    issue_type: str
    reporter_id: int
    assignee_id: int | None
    resource_type: str
    resource_id: int
    is_incident: int
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    closed_by_id: int | None = None
    due_date: datetime | None = None
    assignee_type: str | None = None
    channel: str | None = None
    category: str | None = None
    hd_sla_policy_id: int | None = None
    sla_breach_at: datetime | None = None
    first_response_at: datetime | None = None
    resolved_at: datetime | None = None
    metadata: dict | None = None
    parent_issue_id: int | None = None
    village_id: str | None = None


@dataclass(slots=True)
class CreateIssueRequest:
    """Request to create a new Issue."""

    title: str
    reporter_id: int
    description: str | None = None
    status: str = "open"
    priority: str = "medium"
    issue_type: str = "other"
    assignee_id: int | None = None
    organization_id: int | None = None
    is_incident: int = 0
    parent_issue_id: int | None = None


@dataclass(slots=True)
class UpdateIssueRequest:
    """Request to update an Issue."""

    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    issue_type: str | None = None
    assignee_id: int | None = None
    is_incident: int | None = None
    parent_issue_id: int | None = None


@dataclass(slots=True, frozen=True)
class IssueLabelDTO:
    """Immutable Issue Label data transfer object."""

    id: int
    name: str
    color: str
    description: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class IssueCommentDTO:
    """Immutable Issue Comment data transfer object."""

    id: int
    issue_id: int
    author_id: int
    content: str
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CreateIssueCommentRequest:
    """Request to create an Issue Comment."""

    issue_id: int
    author_id: int
    content: str


@dataclass(slots=True)
class CreateLabelRequest:
    """Request to create a Label."""

    name: str
    description: str | None = None
    color: str | None = "#cccccc"


@dataclass(slots=True)
class UpdateLabelRequest:
    """Request to update a Label."""

    name: str | None = None
    description: str | None = None
    color: str | None = None


# ==================== Projects ====================


@dataclass(slots=True, frozen=True)
class ProjectDTO:
    """Immutable Project data transfer object."""

    id: int
    name: str
    organization_id: int
    created_at: datetime
    updated_at: datetime
    description: str | None = None
    status: str | None = None
    is_active: bool = True
    settings: dict | None = None
    created_by_id: int | None = None


@dataclass(slots=True)
class CreateProjectRequest:
    """Request to create a new Project."""

    name: str
    organization_id: int
    description: str | None = None
    status: str = "active"
    start_date: datetime | None = None
    end_date: datetime | None = None


@dataclass(slots=True)
class UpdateProjectRequest:
    """Request to update a Project."""

    name: str | None = None
    description: str | None = None
    status: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


# ==================== Milestones ====================


@dataclass(slots=True, frozen=True)
class MilestoneDTO:
    """Immutable Milestone data transfer object."""

    id: int
    title: str
    description: str | None
    status: str
    organization_id: int
    project_id: int | None
    due_date: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CreateMilestoneRequest:
    """Request to create a new Milestone."""

    title: str
    organization_id: int
    description: str | None = None
    status: str = "open"
    project_id: int | None = None
    due_date: datetime | None = None


@dataclass(slots=True)
class UpdateMilestoneRequest:
    """Request to update a Milestone."""

    title: str | None = None
    description: str | None = None
    status: str | None = None
    project_id: int | None = None
    due_date: datetime | None = None
    closed_at: datetime | None = None


# ==================== Metadata (Enterprise) ====================


@dataclass(slots=True, frozen=True)
class MetadataFieldDTO:
    """Immutable Metadata Field data transfer object."""

    id: int
    key: str
    value: str | None
    field_type: str  # string, number, date, boolean, json
    is_system: bool
    resource_type: str
    resource_id: int
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CreateMetadataFieldRequest:
    """Request to create a Metadata Field."""

    key: str
    value: str | None
    field_type: str
    resource_type: str
    resource_id: int
    is_system: bool = False


# ==================== API Keys ====================


@dataclass(slots=True, frozen=True)
class APIKeyDTO:
    """Immutable API Key data transfer object."""

    id: int
    identity_id: int
    name: str
    key_prefix: str  # First few chars for display
    created_at: datetime
    updated_at: datetime
    key_hash: str | None = None
    scopes: str | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool = True


@dataclass(slots=True)
class CreateAPIKeyRequest:
    """Request to create a new API Key."""

    name: str
    expires_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class CreateAPIKeyResponse:
    """Response when creating a new API Key (includes full key once)."""

    id: int
    name: str
    api_key: str  # Full key - shown only once!
    prefix: str
    expires_at: datetime | None
    created_at: datetime


# ==================== Auth Requests/Responses ====================


@dataclass(slots=True)
class LoginRequest:
    """Login request with username and password."""

    username: str
    password: str
    mfa_code: str | None = None


@dataclass(slots=True, frozen=True)
class LoginResponse:
    """Login response with access token."""

    access_token: str
    token_type: str
    expires_in: int
    identity: IdentityDTO


@dataclass(slots=True)
class RegisterRequest:
    """User registration request."""

    username: str
    email: str
    password: str
    full_name: str | None = None


# ==================== Software (v2.3.0) ====================


@dataclass(slots=True, frozen=True)
class SoftwareDTO:
    """Immutable Software data transfer object."""

    id: int
    tenant_id: int
    name: str
    description: str | None
    organization_id: int
    purchasing_poc_id: int | None
    license_url: str | None
    version: str | None
    business_purpose: str | None
    software_type: str
    seats: int | None
    cost_monthly: float | None
    renewal_date: date | None
    vendor: str | None
    support_contact: str | None
    notes: str | None
    tags: list | None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None
    village_id: str | None


# ==================== Services (v2.3.0) ====================


@dataclass(slots=True, frozen=True)
class ServiceDTO:
    """Immutable Service data transfer object."""

    id: int
    name: str
    created_at: datetime
    tenant_id: int | None = None
    organization_id: int | None = None
    identity_id: int | None = None
    type: str | None = None
    sub_type: str | None = None
    external_id: str | None = None
    namespace: str | None = None
    cluster: str | None = None
    endpoint: str | None = None
    port: int | None = None
    protocol: str | None = None
    status: str | None = None
    tags: list | None = None
    metadata: dict | None = None
    last_seen_at: datetime | None = None
    updated_at: datetime | None = None


# ==================== Audit Logs ====================


@dataclass(slots=True, frozen=True)
class AuditLogDTO:
    """Immutable Audit Log data transfer object."""

    id: int
    identity_id: int | None
    action: str
    resource_type: str
    resource_id: int | None
    details: dict | None
    success: bool
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


# ==================== SBOM Components ====================


@dataclass(slots=True, frozen=True)
class SBOMComponentDTO:
    """Immutable SBOM Component data transfer object."""

    id: int
    tenant_id: int
    village_id: str
    parent_type: str
    parent_id: int
    name: str
    version: str | None
    purl: str | None
    package_type: str
    scope: str | None
    direct: bool
    license_id: int | None
    license_name: str | None
    license_url: str | None
    source_file: str | None
    repository_url: str | None
    homepage_url: str | None
    description: str | None
    hash_sha256: str | None
    hash_sha512: str | None
    metadata: dict | None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None


@dataclass(slots=True)
class CreateSBOMComponentRequest:
    """Request to create a new SBOM Component."""

    parent_type: str
    parent_id: int
    name: str
    package_type: str
    version: str | None = None
    purl: str | None = None
    scope: str | None = None
    direct: bool = True
    license_id: int | None = None
    license_name: str | None = None
    source_file: str | None = None
    metadata: dict | None = None


@dataclass(slots=True)
class UpdateSBOMComponentRequest:
    """Request to update an SBOM Component."""

    name: str | None = None
    version: str | None = None
    purl: str | None = None
    package_type: str | None = None
    scope: str | None = None
    direct: bool | None = None
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
    is_active: bool | None = None


# ==================== SBOM Scans ====================


@dataclass(slots=True, frozen=True)
class SBOMScanDTO:
    """Immutable SBOM Scan data transfer object."""

    id: int
    tenant_id: int
    village_id: str
    parent_type: str
    parent_id: int
    scan_type: str
    status: str
    repository_url: str | None
    repository_branch: str | None
    commit_hash: str | None
    files_scanned: dict | None
    components_found: int
    components_added: int
    components_updated: int
    components_removed: int
    error_message: str | None
    scan_duration_ms: int | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    credential_type: str | None = None
    credential_id: int | None = None
    has_credentials: bool = False


@dataclass(slots=True)
class CreateSBOMScanRequest:
    """Request to create a new SBOM Scan."""

    parent_type: str
    parent_id: int
    scan_type: str
    repository_url: str | None = None
    repository_branch: str | None = None


@dataclass(slots=True, frozen=True)
class SBOMScanScheduleDTO:
    """Immutable SBOM Scan Schedule data transfer object."""

    id: int
    tenant_id: int
    village_id: str
    parent_type: str
    parent_id: int
    schedule_cron: str
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    credential_type: str | None = None
    credential_id: int | None = None
    has_credentials: bool = False


# ==================== Vulnerabilities ====================


@dataclass(slots=True, frozen=True)
class VulnerabilityDTO:
    """Immutable Vulnerability data transfer object."""

    id: int
    tenant_id: int
    village_id: str
    cve_id: str
    aliases: list | None
    severity: str
    cvss_score: float | None
    cvss_vector: str | None
    title: str | None
    description: str | None
    affected_packages: list | None
    fixed_versions: list | None
    references: list | None
    published_at: datetime | None
    modified_at: datetime | None
    source: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None


# ==================== Component Vulnerabilities ====================


@dataclass(slots=True, frozen=True)
class ComponentVulnerabilityDTO:
    """Immutable Component Vulnerability data transfer object."""

    id: int
    tenant_id: int
    component_id: int
    vulnerability_id: int
    status: str
    remediation_notes: str | None
    remediated_at: datetime | None
    remediated_by_id: int | None
    created_at: datetime
    updated_at: datetime | None


# ==================== License Policies ====================


@dataclass(slots=True, frozen=True)
class LicensePolicyDTO:
    """Immutable License Policy data transfer object."""

    id: int
    tenant_id: int
    organization_id: int | None
    village_id: str
    name: str
    description: str | None
    allowed_licenses: list | None
    denied_licenses: list | None
    action: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None


@dataclass(slots=True)
class CreateLicensePolicyRequest:
    """Request to create a new License Policy."""

    name: str
    organization_id: int
    action: str = "warn"
    description: str | None = None
    allowed_licenses: list | None = field(default_factory=list)
    denied_licenses: list | None = field(default_factory=list)
    is_active: bool = True


@dataclass(slots=True)
class UpdateLicensePolicyRequest:
    """Request to update a License Policy."""

    name: str | None = None
    description: str | None = None
    allowed_licenses: list | None = None
    denied_licenses: list | None = None
    action: str | None = None
    is_active: bool | None = None


# ==================== On-Call Rotations ====================


@dataclass(slots=True, frozen=True)
class OnCallRotationDTO:
    """Immutable On-Call Rotation data transfer object."""

    id: int
    tenant_id: int
    village_id: str
    name: str
    description: str | None
    is_active: bool
    scope_type: str  # organization, service
    organization_id: int | None
    service_id: int | None
    schedule_type: str  # weekly, cron, manual, follow_the_sun
    rotation_length_days: int | None
    rotation_start_date: date | None
    schedule_cron: str | None
    handoff_timezone: str | None
    shift_split: bool
    shift_config: dict | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class OnCallParticipantDTO:
    """Immutable On-Call Participant data transfer object with joined identity info."""

    id: int
    rotation_id: int
    identity_id: int
    identity_name: str  # From join with identities table
    identity_email: str | None  # From join with identities table
    order_index: int
    is_active: bool
    start_date: date | None
    end_date: date | None
    notification_email: str | None
    notification_phone: str | None
    notification_slack: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class OnCallOverrideDTO:
    """Immutable On-Call Override data transfer object with both identity names."""

    id: int
    rotation_id: int
    original_identity_id: int
    original_identity_name: str  # From join
    original_identity_email: str | None  # From join
    override_identity_id: int
    override_identity_name: str  # From join
    override_identity_email: str | None  # From join
    start_datetime: datetime
    end_datetime: datetime
    reason: str | None
    created_by_id: int | None
    created_at: datetime


@dataclass(slots=True, frozen=True)
class OnCallShiftDTO:
    """Immutable On-Call Shift historical record with metrics."""

    id: int
    rotation_id: int
    identity_id: int
    identity_name: str  # From join with identities table
    shift_start: datetime
    shift_end: datetime
    is_override: bool
    override_id: int | None
    alerts_received: int
    incidents_created: int
    created_at: datetime


@dataclass(slots=True, frozen=True)
class EscalationPolicyDTO:
    """Immutable Escalation Policy data transfer object."""

    id: int
    rotation_id: int
    level: int
    escalation_type: str  # identity, group, rotation_participant
    identity_id: int | None
    identity_name: str | None  # From join if escalation_type is identity
    group_id: int | None
    group_name: str | None  # From join if escalation_type is group
    escalation_delay_minutes: int
    notification_channels: list[str] | None  # ["email", "sms", "slack"]
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class CurrentOnCallDTO:
    """Simplified On-Call assignment for badge display."""

    identity_id: int
    identity_name: str
    identity_email: str | None
    shift_start: datetime
    shift_end: datetime
    is_override: bool
    override_reason: str | None


@dataclass(slots=True)
class CreateOnCallRotationRequest:
    """Request to create a new On-Call Rotation (mutable for validation)."""

    name: str
    scope_type: str  # organization, service
    schedule_type: str  # weekly, cron, manual, follow_the_sun
    description: str | None = None
    organization_id: int | None = None
    service_id: int | None = None
    rotation_length_days: int | None = None
    rotation_start_date: date | None = None
    schedule_cron: str | None = None
    handoff_timezone: str | None = None
    shift_split: bool = False
    shift_config: dict | None = None
    is_active: bool = True


@dataclass(slots=True)
class UpdateOnCallRotationRequest:
    """Request to update an On-Call Rotation (all fields optional)."""

    name: str | None = None
    description: str | None = None
    is_active: bool | None = None
    schedule_type: str | None = None
    rotation_length_days: int | None = None
    rotation_start_date: date | None = None
    schedule_cron: str | None = None
    handoff_timezone: str | None = None
    shift_split: bool | None = None
    shift_config: dict | None = None


# ==================== Pagination ====================


@dataclass(slots=True, frozen=True)
class PaginatedResponse:
    """Generic paginated response wrapper."""

    items: list
    total: int
    page: int
    per_page: int
    pages: int


# ==================== Helper Functions ====================


def to_dict(obj) -> dict:
    """Convert dataclass to dictionary (handles nested objects)."""
    return asdict(obj)


def from_pydal_row(row, dto_class):
    """Convert PyDAL Row to dataclass DTO, ignoring unknown fields."""
    if row is None:
        return None
    valid = set(getattr(dto_class, "__dataclass_fields__", {}).keys()) or None
    row_dict = row.as_dict()
    if valid:
        row_dict = {k: v for k, v in row_dict.items() if k in valid}
    return dto_class(**row_dict)


def from_pydal_rows(rows, dto_class) -> list:
    """Convert PyDAL Rows to list of dataclass DTOs, ignoring unknown fields."""
    valid = set(getattr(dto_class, "__dataclass_fields__", {}).keys()) or None
    result = []
    for row in rows:
        row_dict = row.as_dict()
        if valid:
            row_dict = {k: v for k, v in row_dict.items() if k in valid}
        result.append(dto_class(**row_dict))
    return result
