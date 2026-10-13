import { describe, it, expect } from 'vitest'
import issuesModule from '../index'
import type { FrontendModule } from '../../types'

describe('Issues Module', () => {
  it('should have correct module metadata', () => {
    expect(issuesModule.id).toBe('issues')
    expect(issuesModule.name).toBe('Issues & Tracking')
  })

  it('should have navigation items including Issues', () => {
    expect(issuesModule.nav.length).toBeGreaterThan(0)
    const itemNames = issuesModule.nav[0].items.map((item) => item.name)
    expect(itemNames).toContain('Issues')
  })

  it('should have required routes including issue detail', () => {
    const routePaths = issuesModule.routes.map((route) => route.path)
    expect(routePaths).toContain('issues')
    expect(routePaths).toContain('issues/:id')
  })

  it('should conform to FrontendModule interface', () => {
    const isValid = (mod: FrontendModule): boolean =>
      typeof mod.id === 'string' &&
      typeof mod.name === 'string' &&
      Array.isArray(mod.nav) &&
      Array.isArray(mod.routes) &&
      (mod.adminNav === undefined || Array.isArray(mod.adminNav))
    expect(isValid(issuesModule)).toBe(true)
  })
})
