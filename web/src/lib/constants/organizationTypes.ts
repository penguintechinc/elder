import type { OrganizationType } from '@/types'

export interface OrganizationTypeOption {
  value: OrganizationType
  label: string
}

/**
 * Canonical organization-type dropdown options — single source of truth for
 * Organizations.tsx (create) and OrganizationDetail.tsx (edit). Previously
 * only OrganizationDetail.tsx had this list, inline, and Organizations.tsx's
 * create form had no type field at all.
 */
export const ORGANIZATION_TYPES: OrganizationTypeOption[] = [
  { value: 'department', label: 'Department' },
  { value: 'organization', label: 'Organization' },
  { value: 'team', label: 'Team' },
  { value: 'collection', label: 'Collection' },
  { value: 'customer_company', label: 'Customer Company' },
  { value: 'other', label: 'Other' },
]
