import { describe, it, expect } from 'vitest'
import { ORGANIZATION_TYPES } from './organizationTypes'

describe('organizationTypes constants', () => {
  it('includes customer_company alongside the existing organization types', () => {
    const values = ORGANIZATION_TYPES.map((t) => t.value)
    expect(values).toEqual(['department', 'organization', 'team', 'collection', 'customer_company', 'other'])
  })
})
