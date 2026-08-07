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

  it('issueTypeLabel resolves a known value and falls back to the raw value otherwise', () => {
    expect(issueTypeLabel('support')).toBe('Support')
    expect(issueTypeLabel('made-up')).toBe('made-up')
  })
})
