/**
 * Centralized query key factory for React Query
 *
 * Usage:
 *   import { queryKeys } from '@/lib/queryKeys'
 *
 *   // In useQuery:
 *   useQuery({ queryKey: queryKeys.entities.all, ... })
 *   useQuery({ queryKey: queryKeys.entities.list({ search }), ... })
 *
 *   // In invalidation:
 *   queryClient.invalidateQueries({ queryKey: queryKeys.entities.all })
 */

export const queryKeys = {
  // Dashboard
  dashboard: {
    stats: ['dashboard', 'stats'] as const,
  },

  // Organizations
  organizations: {
    all: ['organizations'] as const,
    list: (filters?: Record<string, unknown>) => ['organizations', filters] as const,
    detail: (id: number) => ['organizations', id] as const,
    tree: (id: number) => ['organizations', id, 'tree'] as const,
    dropdown: ['organizations', 'dropdown'] as const,
  },

  // Entities
  entities: {
    all: ['entities'] as const,
    list: (filters?: Record<string, unknown>) => ['entities', filters] as const,
    detail: (id: number) => ['entities', id] as const,
    types: ['entity-types'] as const,
  },

  // Issues
  issues: {
    all: ['issues'] as const,
    list: (filters?: Record<string, unknown>) => ['issues', filters] as const,
    detail: (id: number) => ['issues', id] as const,
    comments: (id: number) => ['issues', id, 'comments'] as const,
    labels: (id: number) => ['issues', id, 'labels'] as const,
    entities: (id: number) => ['issues', id, 'entities'] as const,
  },

  // Projects
  projects: {
    all: ['projects'] as const,
    list: (filters?: Record<string, unknown>) => ['projects', filters] as const,
    detail: (id: number) => ['projects', id] as const,
  },

  // Labels
  labels: {
    all: ['labels'] as const,
    list: (filters?: Record<string, unknown>) => ['labels', filters] as const,
    detail: (id: number) => ['labels', id] as const,
  },

  // Milestones
  milestones: {
    all: ['milestones'] as const,
    list: (filters?: Record<string, unknown>) => ['milestones', filters] as const,
    detail: (id: number) => ['milestones', id] as const,
  },

  // Software
  software: {
    all: ['software'] as const,
    list: (filters?: Record<string, unknown>) => ['software', filters] as const,
    detail: (id: number) => ['software', id] as const,
  },

  // Services
  services: {
    all: ['services'] as const,
    list: (filters?: Record<string, unknown>) => ['services', filters] as const,
    detail: (id: number) => ['services', id] as const,
  },

  // Data Stores
  dataStores: {
    all: ['data-stores'] as const,
    list: (filters?: Record<string, unknown>) => ['data-stores', filters] as const,
    detail: (id: number) => ['data-stores', id] as const,
    labels: (id: number) => ['data-stores', id, 'labels'] as const,
  },

  // Dependencies
  dependencies: {
    all: ['dependencies'] as const,
    list: (filters?: Record<string, unknown>) => ['dependencies', filters] as const,
    detail: (id: number) => ['dependencies', id] as const,
  },

  // Identity / IAM
  identities: {
    all: ['identities'] as const,
    list: (filters?: Record<string, unknown>) => ['identities', filters] as const,
    detail: (id: number) => ['identities', id] as const,
  },

  // Keys
  keys: {
    all: ['keys'] as const,
    list: (filters?: Record<string, unknown>) => ['keys', filters] as const,
    detail: (id: number) => ['keys', id] as const,
  },

  // Secrets
  secrets: {
    all: ['secrets'] as const,
    list: (filters?: Record<string, unknown>) => ['secrets', filters] as const,
    detail: (id: number) => ['secrets', id] as const,
  },

  // Discovery
  discovery: {
    all: ['discovery'] as const,
    jobs: ['discovery', 'jobs'] as const,
    results: (jobId: number) => ['discovery', 'results', jobId] as const,
  },

  // Webhooks
  webhooks: {
    all: ['webhooks'] as const,
    list: (filters?: Record<string, unknown>) => ['webhooks', filters] as const,
    detail: (id: number) => ['webhooks', id] as const,
  },

  // Backups
  backups: {
    all: ['backups'] as const,
    list: (filters?: Record<string, unknown>) => ['backups', filters] as const,
  },

  // Network
  network: {
    all: ['network'] as const,
    list: (filters?: Record<string, unknown>) => ['network', filters] as const,
    detail: (id: number) => ['network', id] as const,
  },

  // IPAM
  ipam: {
    all: ['ipam'] as const,
    subnets: ['ipam', 'subnets'] as const,
    addresses: ['ipam', 'addresses'] as const,
  },

  // Tenants (Admin)
  tenants: {
    all: ['tenants'] as const,
    list: (filters?: Record<string, unknown>) => ['tenants', filters] as const,
    detail: (id: number) => ['tenants', id] as const,
  },

  // User profile
  profile: {
    current: ['profile'] as const,
  },

  // Search
  search: {
    results: (query: string) => ['search', query] as const,
  },

  // Auth
  auth: {
    guestEnabled: ['guest-enabled'] as const,
  },

  // Modules (tenant module toggles)
  modules: {
    all: ['modules'] as const,
    tenant: (tenantId: string) => ['modules', 'tenant', tenantId] as const,
  },

  // Vulnerabilities
  vulnerabilities: {
    all: ['vulnerabilities'] as const,
    list: (filters?: Record<string, unknown>) => ['vulnerabilities', filters] as const,
    detail: (id: number) => ['vulnerabilities', id] as const,
  },

  // On-Call Rotations
  onCall: {
    all: ['on-call'] as const,
    list: (filters?: Record<string, unknown>) => ['on-call', filters] as const,
    detail: (id: number) => ['on-call', id] as const,
    participants: (rotationId: number) => ['on-call', rotationId, 'participants'] as const,
    overrides: (rotationId: number) => ['on-call', rotationId, 'overrides'] as const,
    history: (rotationId: number, filters?: Record<string, unknown>) => ['on-call', rotationId, 'history', filters] as const,
    escalations: (rotationId: number) => ['on-call', rotationId, 'escalations'] as const,
    current: (scopeType: string, scopeId: number) => ['on-call', 'current', scopeType, scopeId] as const,
  },

  // Helpdesk
  helpdesk: {
    all: ['helpdesk'] as const,
    dashboard: () => ['helpdesk', 'dashboard', 'stats'] as const,
    tickets: (filters?: Record<string, unknown>) => ['helpdesk', 'tickets', filters] as const,
    ticket: (id: string) => ['helpdesk', 'ticket', id] as const,
    messages: (ticketId: string) => ['helpdesk', 'ticket', ticketId, 'messages'] as const,
    slaPolicies: () => ['helpdesk', 'sla-policies'] as const,
    responses: () => ['helpdesk', 'canned-responses'] as const,
    teams: () => ['helpdesk', 'teams'] as const,
    emailAccounts: () => ['helpdesk', 'email-accounts'] as const,
    ticketForms: () => ['helpdesk', 'ticket-forms'] as const,
    companies: (filters?: Record<string, unknown>) => ['helpdesk', 'companies', filters] as const,
    company: (id: string) => ['helpdesk', 'company', id] as const,
    contacts: (filters?: Record<string, unknown>) => ['helpdesk', 'contacts', filters] as const,
    contact: (id: string) => ['helpdesk', 'contact', id] as const,
  },
} as const

export type QueryKeys = typeof queryKeys
