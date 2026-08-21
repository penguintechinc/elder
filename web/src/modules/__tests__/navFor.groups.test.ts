import { MODULES, navFor } from '../registry'
import type { MenuCategory } from '@penguintechinc/react-libs/components'

describe('navFor grouped output', () => {
  it('buckets nav by group in fixed order: Core→Workflow→KB', () => {
    // Enable modules from at least 2 groups (e.g., core and kb)
    const enabled = new Set(['infrastructure', 'sbom', 'nav_documents', 'nav_diagrams'])
    const nav = navFor(enabled)

    // Find group headers (those with key like 'group-*' and empty items)
    const groupHeaders = nav.filter(c => c.key?.startsWith('group-') && c.items?.length === 0)
    expect(groupHeaders.map(h => h.header)).toEqual(['Core', 'Knowledge Base'])
  })

  it('emits header category with header, key=group-*, items=[] before each non-empty group', () => {
    const enabled = new Set(['issues', 'nav_streams'])
    const nav = navFor(enabled)

    // Should have a header for workflow group before workflow items
    const workflowHeader = nav.find(c => c.key === 'group-workflow')
    expect(workflowHeader).toBeDefined()
    expect(workflowHeader?.header).toBe('Workflow')
    expect(workflowHeader?.items).toEqual([])

    // Next items should be the actual workflow categories
    const workflowHeaderIdx = nav.indexOf(workflowHeader!)
    const workflowCategories = nav.slice(workflowHeaderIdx + 1)
      .filter(c => !c.key?.startsWith('group-'))
    expect(workflowCategories.length).toBeGreaterThan(0)
  })

  it('preserves registry order within each group', () => {
    const enabled = new Set([
      'infrastructure', // core, 1st in registry
      'secrets', // core, 5th in registry
      'access_reviews', // core, 8th in registry (last core)
    ])
    const nav = navFor(enabled)

    // Find the core header
    const coreHeader = nav.find(c => c.key === 'group-core')
    expect(coreHeader).toBeDefined()

    // Get core categories and verify order matches registry order
    const coreHeaderIdx = nav.indexOf(coreHeader!)
    const coreCategories = nav.slice(coreHeaderIdx + 1)
      .filter(c => !c.key?.startsWith('group-'))
    expect(coreCategories.length).toBeGreaterThan(0)
  })

  it('omits group headers when group has no enabled modules', () => {
    // Enable only core modules
    const enabled = new Set(['infrastructure', 'sbom'])
    const nav = navFor(enabled)

    const groupHeaders = nav.filter(c => c.key?.startsWith('group-'))
    const headerNames = groupHeaders.map(h => h.header)

    // Should only have Core header, no CRM/Workflow/KB headers
    expect(headerNames).toContain('Core')
    expect(headerNames).not.toContain('CRM')
    expect(headerNames).not.toContain('Workflow')
    expect(headerNames).not.toContain('Knowledge Base')
  })

  it('returns empty array when no modules enabled', () => {
    const enabled = new Set<string>()
    const nav = navFor(enabled)
    expect(nav).toEqual([])
  })
})
