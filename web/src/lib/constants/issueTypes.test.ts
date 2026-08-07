import { describe, it, expect } from 'vitest'
import { ISSUE_TYPES, SUPPORT_ISSUE_TYPE, issueTypeLabel } from './issueTypes'

describe('issueTypes constants', () => {
  it('includes all 11 backend IssueType enum values, support last-but-one before other', () => {
    const values = ISSUE_TYPES.map((t) => t.value)
    expect(values).toEqual([
      'operations', 'code', 'config', 'security', 'architecture',
      'process', 'approval', 'feature', 'bug', 'support', 'other',
    ])
  })

  it('SUPPORT_ISSUE_TYPE matches the support entry', () => {
    expect(SUPPORT_ISSUE_TYPE).toBe('support')
    expect(ISSUE_TYPES.some((t) => t.value === SUPPORT_ISSUE_TYPE)).toBe(true)
  })

  it('issueTypeLabel resolves known values case-insensitively (backend returns UPPERCASE)', () => {
    expect(issueTypeLabel('support')).toBe('Support')
    // Backend stores/returns issue_type as UPPERCASE — must still resolve.
    expect(issueTypeLabel('SUPPORT')).toBe('Support')
    expect(issueTypeLabel('OPERATIONS')).toBe('Operations')
  })

  it('issueTypeLabel Title-Cases unknown values and shows a dash for null/undefined', () => {
    expect(issueTypeLabel('made-up')).toBe('Made-up')
    expect(issueTypeLabel(null)).toBe('—')
    expect(issueTypeLabel(undefined)).toBe('—')
    expect(issueTypeLabel('')).toBe('—')
  })
})
