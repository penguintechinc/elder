import { describe, it, expect } from 'vitest'
import { IDENTITY_TYPES } from './identityTypes'

describe('identityTypes constants', () => {
  it('includes customer_contact alongside the existing platform identity types', () => {
    const values = IDENTITY_TYPES.map((t) => t.value)
    expect(values).toEqual([
      'employee', 'vendor', 'bot', 'serviceAccount', 'integration',
      'otherHuman', 'customer_contact', 'other',
    ])
  })

  it('every option has a label, icon component, and color', () => {
    for (const option of IDENTITY_TYPES) {
      expect(option.label.length).toBeGreaterThan(0)
      expect(option.icon).toBeDefined()
      expect(option.color.length).toBeGreaterThan(0)
    }
  })
})
