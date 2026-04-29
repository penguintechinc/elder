export interface Organization {
  id: number
  name: string
  description?: string
  organization_type?: string
  parent_id?: number
  metadata?: Record<string, any>
  village_id?: string
  village_segment?: string
  tenant_id?: number
  created_at: string
  updated_at: string
}

export type OrganizationType = 'department' | 'organization' | 'team' | 'collection' | 'other'

export interface Entity {
  id: number
  unique_id: string
  name: string
  description?: string
  type: EntityType
  organization_id: number
  owner_identity_id?: number
  metadata?: Record<string, any>
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

export interface Dependency {
  id: number
  source_type: string
  source_id: number
  target_type: string
  target_id: number
  dependency_type: DependencyType
  metadata?: Record<string, any>
  created_at: string
}

export type DependencyType = 'calls' | 'related' | 'affects' | 'depends' | 'manages' | 'other'

export interface Identity {
  id: number
  username: string
  email: string
  full_name: string
  identity_type: 'human' | 'service_account'
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
  organization_id?: number
  assigned_to?: number
  created_by: number
  village_id?: string
  tenant_id?: number
  created_at: string
  updated_at: string
  closed_at?: string
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
  settings?: Record<string, any>
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
  details?: Record<string, any>
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
