import { describe, it, expect } from 'vitest'
import helpdeskModule from '../index'
import type { FrontendModule } from '../../types'

describe('Helpdesk Module', () => {
  it('should have correct module metadata', () => {
    expect(helpdeskModule.id).toBe('helpdesk')
    expect(helpdeskModule.name).toBe('Helpdesk')
  })

  it('should have navigation items', () => {
    expect(helpdeskModule.nav).toBeDefined()
    expect(helpdeskModule.nav.length).toBeGreaterThan(0)

    const navCategory = helpdeskModule.nav[0]
    expect(navCategory.header).toBe('Helpdesk')
    expect(navCategory.items).toBeDefined()
    expect(navCategory.items.length).toBeGreaterThan(0)

    const itemNames = navCategory.items.map((item) => item.name)
    expect(itemNames).toContain('Dashboard')
    expect(itemNames).toContain('Tickets')
    expect(itemNames).toContain('Companies')
    expect(itemNames).toContain('Contacts')
  })

  it('should have admin navigation', () => {
    expect(helpdeskModule.adminNav).toBeDefined()
    expect(helpdeskModule.adminNav?.length).toBeGreaterThan(0)

    const adminItem = helpdeskModule.adminNav?.[0]
    expect(adminItem?.name).toBe('Helpdesk Settings')
    expect(adminItem?.href).toBe('/helpdesk/settings')
  })

  it('should have required routes', () => {
    expect(helpdeskModule.routes).toBeDefined()
    expect(helpdeskModule.routes.length).toBeGreaterThan(0)

    const routePaths = helpdeskModule.routes.map((route) => route.path)
    expect(routePaths).toContain('helpdesk')
    expect(routePaths).toContain('helpdesk/tickets')
    expect(routePaths).toContain('helpdesk/tickets/:id')
    expect(routePaths).toContain('helpdesk/companies')
    expect(routePaths).toContain('helpdesk/contacts')
    expect(routePaths).toContain('helpdesk/settings')
  })

  it('should conform to FrontendModule interface', () => {
    const isValid = (mod: FrontendModule): boolean => {
      return (
        typeof mod.id === 'string' &&
        typeof mod.name === 'string' &&
        Array.isArray(mod.nav) &&
        Array.isArray(mod.routes) &&
        (mod.adminNav === undefined || Array.isArray(mod.adminNav))
      )
    }

    expect(isValid(helpdeskModule)).toBe(true)
  })

  it('should have navigation items with href paths', () => {
    const items = helpdeskModule.nav[0].items
    items.forEach((item) => {
      expect(item.href).toBeDefined()
      expect(item.href).toMatch(/^\/helpdesk/)
    })
  })

  it('should have routes with lazy-loaded elements', () => {
    helpdeskModule.routes.forEach((route) => {
      expect(route.path).toBeDefined()
      expect(route.element).toBeDefined()
    })
  })
})
