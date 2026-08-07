export interface Organization {
  id: number
  name: string
  description?: string
  organization_type?: string
  parent_id?: number
  metadata?: Record<string, unknown>
  village_id?: string
  village_segment?: string
  tenant_id?: number
  created_at: string
  updated_at: string
}

export type OrganizationType = 'department' | 'organization' | 'team' | 'collection' | 'customer_company' | 'other'

export interface EntityLocation {
  city?: string
  state?: string
  country?: string
  latitude?: number
  longitude?: number
}

// Flat metadata bag written by discovery providers (K8s, cloud, etc). Known
// structural fields are typed for convenience; anything else (including
// out-of-band writes like a geo-enrichment agent's `location`) still flows
// through the index signature.
export interface EntityMetadata {
  location?: EntityLocation
  resource_id?: string
  resource_type?: string
  region?: string
  discovered_at?: string
  capacity_cpu?: string
  capacity_memory?: string
  kubelet_version?: string
  os_image?: string
  conditions?: string[]
  phase?: string
  namespace?: string
  node_name?: string
  pod_ip?: string
  containers_count?: number
  replicas?: number
  available_replicas?: number
  ready_replicas?: number
  images?: string[]
  selector?: Record<string, string>
  [key: string]: unknown
}

// K8s labels / cloud provider tags come back as a {key: value} dict;
// user-applied classification tags (via CreateEntityRequest) are a legacy
// list[str]. Callers should normalize via lib/entityTags before rendering.
export type EntityTags = Record<string, string> | string[]

export interface Entity {
  id: number
  unique_id: string
  name: string
  description?: string
  type: EntityType
  sub_type?: string
  organization_id: number
  parent_id?: number
  owner_identity_id?: number
  metadata?: EntityMetadata
  tags?: EntityTags
  is_active?: boolean
  village_id?: string
  created_at: string
  updated_at: string
  organization?: Organization
}

export type EntityType =
  | 'datacenter'
  | 'vpc'
  | 'subnet'
  | 'compute'
  | 'network'
  | 'user'
  | 'security_issue'

// Matches apps/api/models/dataclasses.py DependencyDTO exactly — the API
// returns polymorphic source/target refs only, it never joins/enriches the
// referenced resource (no source_entity/target_entity name fields).
export interface Dependency {
  id: number
  tenant_id: number
  source_type: string
  source_id: number
  target_type: string
  target_id: number
  dependency_type: DependencyType
  metadata?: Record<string, unknown>
  created_at: string
  updated_at: string
  village_id?: string
}

export type DependencyType = 'calls' | 'related' | 'affects' | 'depends' | 'manages' | 'other'

// Matches apps/api/modules/issues/models/issue.py IssueType enum — the DB
// column is Enum(IssueType) but every value over the wire is the lowercase
// .value string (e.g. "support"), never the Python member name.
export type IssueType =
  | 'operations'
  | 'code'
  | 'config'
  | 'security'
  | 'architecture'
  | 'process'
  | 'approval'
  | 'feature'
  | 'bug'
  | 'support'
  | 'other'

// Disambiguates the polymorphic Issue.assignee_id: 'identity' resolves
// against identities.id, 'org_unit' against organizations.id. Matches
// apps/api/modules/issues/routes/issues.py::_resolve_assignee_type and
// apps/api/modules/helpdesk/routes/intake_forms.py::_VALID_ASSIGNEE_TYPES.
export type IssueAssigneeType = 'identity' | 'org_unit'

// Matches apps/api/models/identity.py IdentityType enum (the DB column).
// NOTE: apps/api/models/pydantic/identity.py's CreateIdentityRequest
// currently restricts identity_type to Literal["human","service_account"]
// only, rejecting every other value below with a 422 — a pre-existing
// backend gap (see this plan's Global Constraints), not fixed here.
export interface Identity {
  id: number
  username: string
  email: string
  full_name: string
  identity_type:
    | 'human'
    | 'service_account'
    | 'employee'
    | 'vendor'
    | 'bot'
    | 'serviceAccount'
    | 'integration'
    | 'otherHuman'
    | 'other'
    | 'customer_contact'
  auth_provider: 'local' | 'saml' | 'oauth2' | 'ldap'
  is_active: boolean
  is_superuser: boolean
  created_at: string
  last_login_at?: string
}

export interface IdentityGroup {
  id: number
  name: string
  description?: string
  created_at: string
  member_count?: number
}

export interface Issue {
  id: number
  title: string
  description?: string
  status: IssueStatus
  priority: IssuePriority
  issue_type: IssueType
  reporter_id?: number
  assignee_id?: number
  assignee_type?: IssueAssigneeType
  resource_type?: string
  resource_id?: number
  is_incident?: number | boolean
  channel?: string
  category?: string
  requester_contact_id?: number
  hd_sla_policy_id?: number
  sla_breach_at?: string
  first_response_at?: string
  resolved_at?: string
  parent_issue_id?: number
  closed_at?: string
  created_at: string
  updated_at: string
  // Legacy/enrichment-only fields: NOT part of apps/api/models/dataclasses.py
  // IssueDTO (the real GET /issues and GET /issues/:id response shape,
  // confirmed by reading the backend directly). Kept optional so existing
  // call sites that populate them via a *separate* fetch (labels,
  // entity_links) or that were already reading dead fields (organization_id,
  // village_id, created_by, tenant_id, assignee) keep compiling. Do not add
  // new reads of these without confirming the backend actually returns them
  // for that specific endpoint.
  organization_id?: number
  assigned_to?: number
  created_by?: number
  village_id?: string
  tenant_id?: number
  labels?: IssueLabel[]
  entity_links?: Entity[]
  assignee?: Identity
}

export type IssueStatus = 'open' | 'in_progress' | 'resolved' | 'closed'
export type IssuePriority = 'low' | 'medium' | 'high' | 'critical'

export interface IssueLabel {
  id: number
  name: string
  color: string
  description?: string
}

export interface ResourceRole {
  id: number
  resource_type: 'entity' | 'organization'
  resource_id: number
  identity_id: number
  role: ResourceRoleType
  granted_at: string
  granted_by?: number
  identity?: Identity
}

export type ResourceRoleType = 'maintainer' | 'operator' | 'viewer'

export interface GraphNode {
  id: number
  unique_id: string
  name: string
  type: EntityType
  organization_id: number
}

export interface GraphEdge {
  source_id: number
  target_id: number
  dependency_type: DependencyType
}

export interface Graph {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface PaginatedResponse<T> {
  items: T[]
  page: number
  pages: number
  per_page: number
  total: number
}

export interface ApiError {
  error: string
  message: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  identity: Identity
}

// v2.2.0 Enterprise Edition Types

export interface Tenant {
  id: number
  name: string
  slug: string
  domain?: string
  subscription_tier: 'community' | 'professional' | 'enterprise'
  license_key?: string
  settings?: Record<string, unknown>
  feature_flags?: Record<string, boolean>
  data_retention_days: number
  storage_quota_gb: number
  is_active: boolean
  village_id?: string
  created_at: string
  updated_at?: string
  usage?: {
    organizations: number
    portal_users: number
    identities: number
  }
}

export interface PortalUser {
  id: number
  tenant_id: number
  email: string
  full_name?: string
  tenant_role: 'admin' | 'editor' | 'reader'
  global_role?: 'admin' | 'support' | null
  is_active: boolean
  email_verified: boolean
  mfa_enabled: boolean
  last_login_at?: string
  created_at: string
}

export interface PortalAuthResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: PortalUser
  tenant: {
    id: number
    name: string
    slug: string
  }
}

export interface IdPConfiguration {
  id: number
  tenant_id?: number
  idp_type: 'saml' | 'oidc'
  name: string
  entity_id?: string
  metadata_url?: string
  sso_url?: string
  slo_url?: string
  certificate?: string
  attribute_mappings?: Record<string, string>
  jit_provisioning_enabled: boolean
  default_role: string
  is_active?: boolean
}

export interface SCIMConfiguration {
  id: number
  tenant_id: number
  endpoint_url: string
  bearer_token?: string
  sync_groups: boolean
  last_sync_at?: string
}

export interface AuditLog {
  id: number
  identity_id?: number
  action: string
  resource_type: string
  resource_id?: number
  details?: Record<string, unknown>
  success: boolean
  ip_address?: string
  user_agent?: string
  created_at: string
}

export interface AuditLogQuery {
  tenant_id?: number
  resource_type?: string
  resource_id?: number
  action?: string
  category?: string
  identity_id?: number
  portal_user_id?: number
  start_date?: string
  end_date?: string
  success?: boolean
  limit?: number
  offset?: number
}

export interface ComplianceReport {
  title: string
  report_type: string
  tenant_id: number
  period: {
    start: string
    end: string
  }
  summary: {
    total_events: number
    success_count: number
    failure_count: number
    unique_users: number
    unique_resources: number
  }
  events: AuditLog[]
  generated_at: string
}
