import type { IssueType } from '@/types'

export interface IssueTypeOption {
  value: IssueType
  label: string
}

/**
 * Canonical issue_type dropdown/badge options — single source of truth for
 * the unified Issues list, detail, and create/edit views, and the intake
 * form builder. Matches apps/api/modules/issues/models/issue.py IssueType
 * enum values exactly (lowercase over the wire).
 */
export const ISSUE_TYPES: IssueTypeOption[] = [
  { value: 'operations', label: 'Operations' },
  { value: 'code', label: 'Code' },
  { value: 'config', label: 'Config' },
  { value: 'security', label: 'Security' },
  { value: 'architecture', label: 'Architecture' },
  { value: 'process', label: 'Process' },
  { value: 'approval', label: 'Approval' },
  { value: 'feature', label: 'Feature' },
  { value: 'bug', label: 'Bug' },
  { value: 'support', label: 'Support' },
  { value: 'other', label: 'Other' },
]

/** The single issue_type value that triggers support-specific UI (the
 * support section in the detail view, support fields in create/edit). */
export const SUPPORT_ISSUE_TYPE: IssueType = 'support'

/** Resolve a raw issue_type string to its display label, falling back to
 * the raw value itself for anything not in ISSUE_TYPES. */
export function issueTypeLabel(value: string): string {
  return ISSUE_TYPES.find((t) => t.value === value)?.label ?? value
}
