"""Python 3.12 dataclasses with slots for Elder application.

Using @dataclass(slots=True) provides 30-50% memory reduction and faster attribute access.
"""

# flake8: noqa: E501


from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Optional

# ==================== Organization Units (OUs) ====================


@dataclass(slots=True, frozen=True)
class OrganizationDTO:
    """Immutable Organization Unit (OU) data transfer object."""

    id: int
    name: str
    description: Optional[str]
    organization_type: str  # department, organization, team, collection, other
    parent_id: Optional[int]
    ldap_dn: Optional[str]
    saml_group: Optional[str]
    owner_identity_id: Optional[int]
    owner_group_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    tenant_id: Optional[int] = None
    village_id: Optional[str] = None
    village_segment: Optional[str] = None


@dataclass(slots=True)
class CreateOrganizationRequest:
    """Request to create a new Organization Unit (OU)."""

    name: str
    description: Optional[str] = None
    organization_type: str = (
        "organization"  # department, organization, team, collection, other
    )
    parent_id: Optional[int] = None
    ldap_dn: Optional[str] = None
    saml_group: Optional[str] = None
    owner_identity_id: Optional[int] = None
    owner_group_id: Optional[int] = None


@dataclass(slots=True)
class UpdateOrganizationRequest:
    """Request to update an Organization Unit (OU)."""

    name: Optional[str] = None
    description: Optional[str] = None
    organization_type: Optional[str] = (
        None  # department, organization, team, collection, other
    )
    parent_id: Optional[int] = None
    ldap_dn: Optional[str] = None
    saml_group: Optional[str] = None
    owner_identity_id: Optional[int] = None
    owner_group_id: Optional[int] = None


# ==================== Entities ====================


@dataclass(slots=True, frozen=True)
class EntityDTO:
    """Immutable Entity data transfer object."""

    id: int
    name: str
    description: Optional[str]
    entity_type: str
    sub_type: Optional[str]
    organization_id: int
    parent_id: Optional[int]
    attributes: Optional[dict]
    tags: Optional[list[str]]
    is_active: bool
    default_metadata: Optional[dict]
    status_metadata: Optional[dict]
    created_at: datetime
    updated_at: datetime
    village_id: Optional[str] = None


@dataclass(slots=True)
class CreateEntityRequest:
    """Request to create a new Entity."""

    name: str
    entity_type: str
    organization_id: int
    description: Optional[str] = None
    sub_type: Optional[str] = None
    parent_id: Optional[int] = None
    attributes: Optional[dict] = None
    tags: Optional[list[str]] = field(default_factory=list)
    default_metadata: Optional[dict] = None
    is_active: bool = True


@dataclass(slots=True)
class UpdateEntityRequest:
    """Request to update an Entity."""

    name: Optional[str] = None
    description: Optional[str] = None
    entity_type: Optional[str] = None
    sub_type: Optional[str] = None
    organization_id: Optional[int] = None
    parent_id: Optional[int] = None
    attributes: Optional[dict] = None
    tags: Optional[list[str]] = None
    default_metadata: Optional[dict] = None
    is_active: Optional[bool] = None


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
    metadata: Optional[dict]
    created_at: datetime
    updated_at: datetime
    village_id: Optional[str] = None


@dataclass(slots=True)
class CreateDependencyRequest:
    """Request to create a new Dependency."""

    source_type: str
    source_id: int
    target_type: str
    target_id: int
    dependency_type: str
    metadata: Optional[dict] = None


# ==================== Identities ====================


@dataclass(slots=True)
class IdentityDTO:
    """Immutable Identity data transfer object."""

    id: int
    identity_type: str
    username: str
    email: Optional[str]
    full_name: Optional[str]
    organization_id: Optional[int]  # Link to organization
    portal_role: str  # admin, editor, observer
    auth_provider: str
    auth_provider_id: Optional[str]
    is_active: bool
    is_superuser: bool
    mfa_enabled: bool
    last_login_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    password_hash: Optional[str] = None  # Stored password hash (never expose to client)
    mfa_secret: Optional[str] = None  # MFA secret (never expose to client)
    tenant_id: Optional[int] = None  # Link to tenant
    village_id: Optional[str] = None


@dataclass(slots=True)
class CreateIdentityRequest:
    """Request to create a new Identity."""

    username: str
    identity_type: str
    auth_provider: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[str] = None  # Will be hashed
    auth_provider_id: Optional[str] = None
    is_active: bool = True
    is_superuser: bool = False
    mfa_enabled: bool = False


@dataclass(slots=True)
class UpdateIdentityRequest:
    """Request to update an Identity."""

    email: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[str] = None  # Will be hashed
    is_active: Optional[bool] = None
    mfa_enabled: Optional[bool] = None


# ==================== Identity Groups ====================


@dataclass(slots=True, frozen=True)
class IdentityGroupDTO:
    """Immutable Identity Group data transfer object."""

    id: int
    name: str
    description: Optional[str]
    ldap_dn: Optional[str]
    saml_group: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CreateIdentityGroupRequest:
    """Request to create a new Identity Group."""

    name: str
    description: Optional[str] = None
    ldap_dn: Optional[str] = None
    saml_group: Optional[str] = None
    is_active: bool = True


# ==================== Roles & Permissions ====================


@dataclass(slots=True, frozen=True)
class RoleDTO:
    """Immutable Role data transfer object."""

    id: int
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class PermissionDTO:
    """Immutable Permission data transfer object."""

    id: int
    name: str
    resource_type: str
    action: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


# ==================== Resource Roles (Enterprise) ====================


@dataclass(slots=True, frozen=True)
class ResourceRoleDTO:
    """Immutable Resource Role data transfer object."""

    id: int
    identity_id: Optional[int]
    group_id: Optional[int]
    role: str  # maintainer, operator, viewer
    resource_type: str
    resource_id: Optional[int]
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CreateResourceRoleRequest:
    """Request to create a Resource Role assignment."""

    role: str
    resource_type: str
    identity_id: Optional[int] = None
    group_id: Optional[int] = None
    resource_id: Optional[int] = None


# ==================== Issues (Enterprise) ====================


@dataclass(slots=True, frozen=True)
class IssueDTO:
    """Immutable Issue data transfer object."""

    id: int
    title: str
    description: Optional[str]
    status: str
    priority: str
    issue_type: str
    reporter_id: int
    assignee_id: Optional[int]
    organization_id: Optional[int]
    is_incident: int
    closed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    tenant_id: Optional[int] = None
    village_id: Optional[str] = None
    parent_issue_id: Optional[int] = None


@dataclass(slots=True)
class CreateIssueRequest:
    """Request to create a new Issue."""

    title: str
    reporter_id: int
    description: Optional[str] = None
    status: str = "open"
    priority: str = "medium"
    issue_type: str = "other"
    assignee_id: Optional[int] = None
    organization_id: Optional[int] = None
    is_incident: int = 0
    parent_issue_id: Optional[int] = None


@dataclass(slots=True)
class UpdateIssueRequest:
    """Request to update an Issue."""

    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    issue_type: Optional[str] = None
    assignee_id: Optional[int] = None
    is_incident: Optional[int] = None
    parent_issue_id: Optional[int] = None


@dataclass(slots=True, frozen=True)
class IssueLabelDTO:
    """Immutable Issue Label data transfer object."""

    id: int
    name: str
    color: str
    description: Optional[str]
    created_at: datetime


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
    description: Optional[str] = None
    color: Optional[str] = "#cccccc"


@dataclass(slots=True)
class UpdateLabelRequest:
    """Request to update a Label."""

    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None


# ==================== Projects ====================


@dataclass(slots=True, frozen=True)
class ProjectDTO:
    """Immutable Project data transfer object."""

    id: int
    name: str
    description: Optional[str]
    status: str
    organization_id: int
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    village_id: Optional[str] = None


@dataclass(slots=True)
class CreateProjectRequest:
    """Request to create a new Project."""

    name: str
    organization_id: int
    description: Optional[str] = None
    status: str = "active"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


@dataclass(slots=True)
class UpdateProjectRequest:
    """Request to update a Project."""

    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


# ==================== Milestones ====================


@dataclass(slots=True, frozen=True)
class MilestoneDTO:
    """Immutable Milestone data transfer object."""

    id: int
    title: str
    description: Optional[str]
    status: str
    organization_id: int
    project_id: Optional[int]
    due_date: Optional[datetime]
    closed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CreateMilestoneRequest:
    """Request to create a new Milestone."""

    title: str
    organization_id: int
    description: Optional[str] = None
    status: str = "open"
    project_id: Optional[int] = None
    due_date: Optional[datetime] = None


@dataclass(slots=True)
class UpdateMilestoneRequest:
    """Request to update a Milestone."""

    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    project_id: Optional[int] = None
    due_date: Optional[datetime] = None
    closed_at: Optional[datetime] = None


# ==================== Metadata (Enterprise) ====================


@dataclass(slots=True, frozen=True)
class MetadataFieldDTO:
    """Immutable Metadata Field data transfer object."""

    id: int
    key: str
    value: Optional[str]
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
    value: Optional[str]
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
    prefix: str  # First few chars for display
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CreateAPIKeyRequest:
    """Request to create a new API Key."""

    name: str
    expires_at: Optional[datetime] = None


@dataclass(slots=True, frozen=True)
class CreateAPIKeyResponse:
    """Response when creating a new API Key (includes full key once)."""

    id: int
    name: str
    api_key: str  # Full key - shown only once!
    prefix: str
    expires_at: Optional[datetime]
    created_at: datetime


# ==================== Auth Requests/Responses ====================


@dataclass(slots=True)
class LoginRequest:
    """Login request with username and password."""

    username: str
    password: str
    mfa_code: Optional[str] = None


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
    full_name: Optional[str] = None


# ==================== Software (v2.3.0) ====================


@dataclass(slots=True, frozen=True)
class SoftwareDTO:
    """Immutable Software data transfer object."""

    id: int
    tenant_id: int
    name: str
    description: Optional[str]
    organization_id: int
    purchasing_poc_id: Optional[int]
    license_url: Optional[str]
    version: Optional[str]
    business_purpose: Optional[str]
    software_type: str
    seats: Optional[int]
    cost_monthly: Optional[float]
    renewal_date: Optional[date]
    vendor: Optional[str]
    support_contact: Optional[str]
    notes: Optional[str]
    tags: Optional[list]
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]
    village_id: Optional[str]


# ==================== Services (v2.3.0) ====================


@dataclass(slots=True, frozen=True)
class ServiceDTO:
    """Immutable Service data transfer object."""

    id: int
    tenant_id: int
    name: str
    description: Optional[str]
    organization_id: int
    domains: Optional[list]
    paths: Optional[list]
    poc_identity_id: Optional[int]
    language: Optional[str]
    deployment_method: Optional[str]
    deployment_type: Optional[str]
    is_public: bool
    port: Optional[int]
    health_endpoint: Optional[str]
    repository_url: Optional[str]
    documentation_url: Optional[str]
    sla_uptime: Optional[float]
    sla_response_time_ms: Optional[int]
    notes: Optional[str]
    tags: Optional[list]
    status: str
    created_at: datetime
    updated_at: Optional[datetime]
    village_id: Optional[str]


# ==================== Audit Logs ====================


@dataclass(slots=True, frozen=True)
class AuditLogDTO:
    """Immutable Audit Log data transfer object."""

    id: int
    identity_id: Optional[int]
    action: str
    resource_type: str
    resource_id: Optional[int]
    details: Optional[dict]
    success: bool
    ip_address: Optional[str]
    user_agent: Optional[str]
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
    version: Optional[str]
    purl: Optional[str]
    package_type: str
    scope: Optional[str]
    direct: bool
    license_id: Optional[int]
    license_name: Optional[str]
    license_url: Optional[str]
    source_file: Optional[str]
    repository_url: Optional[str]
    homepage_url: Optional[str]
    description: Optional[str]
    hash_sha256: Optional[str]
    hash_sha512: Optional[str]
    metadata: Optional[dict]
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]


@dataclass(slots=True)
class CreateSBOMComponentRequest:
    """Request to create a new SBOM Component."""

    parent_type: str
    parent_id: int
    name: str
    package_type: str
    version: Optional[str] = None
    purl: Optional[str] = None
    scope: Optional[str] = None
    direct: bool = True
    license_id: Optional[int] = None
    license_name: Optional[str] = None
    source_file: Optional[str] = None
    metadata: Optional[dict] = None


@dataclass(slots=True)
class UpdateSBOMComponentRequest:
    """Request to update an SBOM Component."""

    name: Optional[str] = None
    version: Optional[str] = None
    purl: Optional[str] = None
    package_type: Optional[str] = None
    scope: Optional[str] = None
    direct: Optional[bool] = None
    license_id: Optional[int] = None
    license_name: Optional[str] = None
    license_url: Optional[str] = None
    source_file: Optional[str] = None
    repository_url: Optional[str] = None
    homepage_url: Optional[str] = None
    description: Optional[str] = None
    hash_sha256: Optional[str] = None
    hash_sha512: Optional[str] = None
    metadata: Optional[dict] = None
    is_active: Optional[bool] = None


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
    repository_url: Optional[str]
    repository_branch: Optional[str]
    commit_hash: Optional[str]
    files_scanned: Optional[dict]
    components_found: int
    components_added: int
    components_updated: int
    components_removed: int
    error_message: Optional[str]
    scan_duration_ms: Optional[int]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    credential_type: Optional[str] = None
    credential_id: Optional[int] = None
    has_credentials: bool = False


@dataclass(slots=True)
class CreateSBOMScanRequest:
    """Request to create a new SBOM Scan."""

    parent_type: str
    parent_id: int
    scan_type: str
    repository_url: Optional[str] = None
    repository_branch: Optional[str] = None


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
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    credential_type: Optional[str] = None
    credential_id: Optional[int] = None
    has_credentials: bool = False


# ==================== Vulnerabilities ====================


@dataclass(slots=True, frozen=True)
class VulnerabilityDTO:
    """Immutable Vulnerability data transfer object."""

    id: int
    tenant_id: int
    village_id: str
    cve_id: str
    aliases: Optional[list]
    severity: str
    cvss_score: Optional[float]
    cvss_vector: Optional[str]
    title: Optional[str]
    description: Optional[str]
    affected_packages: Optional[list]
    fixed_versions: Optional[list]
    references: Optional[list]
    published_at: Optional[datetime]
    modified_at: Optional[datetime]
    source: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]


# ==================== Component Vulnerabilities ====================


@dataclass(slots=True, frozen=True)
class ComponentVulnerabilityDTO:
    """Immutable Component Vulnerability data transfer object."""

    id: int
    tenant_id: int
    component_id: int
    vulnerability_id: int
    status: str
    remediation_notes: Optional[str]
    remediated_at: Optional[datetime]
    remediated_by_id: Optional[int]
    created_at: datetime
    updated_at: Optional[datetime]


# ==================== License Policies ====================


@dataclass(slots=True, frozen=True)
class LicensePolicyDTO:
    """Immutable License Policy data transfer object."""

    id: int
    tenant_id: int
    organization_id: Optional[int]
    village_id: str
    name: str
    description: Optional[str]
    allowed_licenses: Optional[list]
    denied_licenses: Optional[list]
    action: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime]


@dataclass(slots=True)
class CreateLicensePolicyRequest:
    """Request to create a new License Policy."""

    name: str
    organization_id: int
    action: str = "warn"
    description: Optional[str] = None
    allowed_licenses: Optional[list] = field(default_factory=list)
    denied_licenses: Optional[list] = field(default_factory=list)
    is_active: bool = True


@dataclass(slots=True)
class UpdateLicensePolicyRequest:
    """Request to update a License Policy."""

    name: Optional[str] = None
    description: Optional[str] = None
    allowed_licenses: Optional[list] = None
    denied_licenses: Optional[list] = None
    action: Optional[str] = None
    is_active: Optional[bool] = None


# ==================== On-Call Rotations ====================


@dataclass(slots=True, frozen=True)
class OnCallRotationDTO:
    """Immutable On-Call Rotation data transfer object."""

    id: int
    tenant_id: int
    village_id: str
    name: str
    description: Optional[str]
    is_active: bool
    scope_type: str  # organization, service
    organization_id: Optional[int]
    service_id: Optional[int]
    schedule_type: str  # weekly, cron, manual, follow_the_sun
    rotation_length_days: Optional[int]
    rotation_start_date: Optional[date]
    schedule_cron: Optional[str]
    handoff_timezone: Optional[str]
    shift_split: bool
    shift_config: Optional[dict]
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class OnCallParticipantDTO:
    """Immutable On-Call Participant data transfer object with joined identity info."""

    id: int
    rotation_id: int
    identity_id: int
    identity_name: str  # From join with identities table
    identity_email: Optional[str]  # From join with identities table
    order_index: int
    is_active: bool
    start_date: Optional[date]
    end_date: Optional[date]
    notification_email: Optional[str]
    notification_phone: Optional[str]
    notification_slack: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class OnCallOverrideDTO:
    """Immutable On-Call Override data transfer object with both identity names."""

    id: int
    rotation_id: int
    original_identity_id: int
    original_identity_name: str  # From join
    original_identity_email: Optional[str]  # From join
    override_identity_id: int
    override_identity_name: str  # From join
    override_identity_email: Optional[str]  # From join
    start_datetime: datetime
    end_datetime: datetime
    reason: Optional[str]
    created_by_id: Optional[int]
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
    override_id: Optional[int]
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
    identity_id: Optional[int]
    identity_name: Optional[str]  # From join if escalation_type is identity
    group_id: Optional[int]
    group_name: Optional[str]  # From join if escalation_type is group
    escalation_delay_minutes: int
    notification_channels: Optional[list[str]]  # ["email", "sms", "slack"]
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class CurrentOnCallDTO:
    """Simplified On-Call assignment for badge display."""

    identity_id: int
    identity_name: str
    identity_email: Optional[str]
    shift_start: datetime
    shift_end: datetime
    is_override: bool
    override_reason: Optional[str]


@dataclass(slots=True)
class CreateOnCallRotationRequest:
    """Request to create a new On-Call Rotation (mutable for validation)."""

    name: str
    scope_type: str  # organization, service
    schedule_type: str  # weekly, cron, manual, follow_the_sun
    description: Optional[str] = None
    organization_id: Optional[int] = None
    service_id: Optional[int] = None
    rotation_length_days: Optional[int] = None
    rotation_start_date: Optional[date] = None
    schedule_cron: Optional[str] = None
    handoff_timezone: Optional[str] = None
    shift_split: bool = False
    shift_config: Optional[dict] = None
    is_active: bool = True


@dataclass(slots=True)
class UpdateOnCallRotationRequest:
    """Request to update an On-Call Rotation (all fields optional)."""

    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    schedule_type: Optional[str] = None
    rotation_length_days: Optional[int] = None
    rotation_start_date: Optional[date] = None
    schedule_cron: Optional[str] = None
    handoff_timezone: Optional[str] = None
    shift_split: Optional[bool] = None
    shift_config: Optional[dict] = None


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
    """Convert PyDAL Row to dataclass DTO."""
    if row is None:
        return None
    return dto_class(**row.as_dict())


def from_pydal_rows(rows, dto_class) -> list:
    """Convert PyDAL Rows to list of dataclass DTOs."""
    return [dto_class(**row.as_dict()) for row in rows]
