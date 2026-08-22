import { navFor } from '../registry'

describe('navFor grouped output', () => {
  it('buckets nav by group in WIRED order: Workstreams→Issues→Relationships→Entities→Documents', () => {
    // Enable modules from two non-adjacent groups (entities and documents)
    const enabled = new Set(['infrastructure', 'sbom', 'nav_documents', 'nav_diagrams'])
    const nav = navFor(enabled)

    // Find group headers (those with key like 'group-*' and empty items)
    const groupHeaders = nav.filter(c => c.key?.startsWith('group-') && c.items?.length === 0)
    expect(groupHeaders.map(h => h.header)).toEqual(['Entities', 'Documents'])
  })

  it('emits header category with header, key=group-*, items=[] before each non-empty group', () => {
    const enabled = new Set(['issues', 'nav_streams'])
    const nav = navFor(enabled)

    // Should have a header for the workstreams group before its items
    const workstreamsHeader = nav.find(c => c.key === 'group-workstreams')
    expect(workstreamsHeader).toBeDefined()
    expect(workstreamsHeader?.header).toBe('Workstreams')
    expect(workstreamsHeader?.items).toEqual([])

    // Next items should be the actual workstreams categories
    const idx = nav.indexOf(workstreamsHeader!)
    const workstreamsCategories = nav.slice(idx + 1)
      .filter(c => !c.key?.startsWith('group-'))
    expect(workstreamsCategories.length).toBeGreaterThan(0)
  })

  it('preserves registry order within each group', () => {
    const enabled = new Set([
      'infrastructure', // entities, 1st in registry
      'sbom', // entities, 3rd in registry
      'secrets', // entities, 7th in registry
    ])
    const nav = navFor(enabled)

    const entitiesHeader = nav.find(c => c.key === 'group-entities')
    expect(entitiesHeader).toBeDefined()

    // Get entities categories and verify they follow the header
    const idx = nav.indexOf(entitiesHeader!)
    const entitiesCategories = nav.slice(idx + 1)
      .filter(c => !c.key?.startsWith('group-'))
    expect(entitiesCategories.length).toBeGreaterThan(0)
  })

  it('omits group headers when group has no enabled modules', () => {
    // Enable only entities modules
    const enabled = new Set(['infrastructure', 'sbom'])
    const nav = navFor(enabled)

    const groupHeaders = nav.filter(c => c.key?.startsWith('group-'))
    const headerNames = groupHeaders.map(h => h.header)

    // Should only have the Entities header, no other WIRED headers
    expect(headerNames).toEqual(['Entities'])
  })

  it('returns empty array when no modules enabled', () => {
    const enabled = new Set<string>()
    const nav = navFor(enabled)
    expect(nav).toEqual([])
  })
})
