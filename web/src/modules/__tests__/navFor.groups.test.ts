import { navFor } from '../registry'

describe('navFor — one section per WIRED pillar', () => {
  it('emits exactly one category per populated pillar, in WIRED order', () => {
    const all = new Set([
      'infrastructure', 'ipam', 'sbom', 'services_oncall', 'secrets',
      'issues', 'discovery', 'access_reviews', 'webhooks_alerting',
      'nav_streams', 'nav_documents', 'nav_pages', 'nav_diagrams',
    ])
    const nav = navFor(all)

    expect(nav.map(c => c.header)).toEqual([
      'Workstreams',
      'Issues',
      'Relationships',
      'Entities',
      'Documents',
    ])
  })

  it('every emitted category has items — a header-only category renders as nothing', () => {
    const all = new Set(['infrastructure', 'issues', 'nav_documents'])
    for (const category of navFor(all)) {
      expect(category.items.length).toBeGreaterThan(0)
    }
  })

  it('merges items from every module in a pillar into that one section', () => {
    // sbom and services_oncall both live in `entities` and both authored a
    // "Software & Services" category — the duplicate header is what this fixes.
    const nav = navFor(new Set(['sbom', 'services_oncall']))
    expect(nav).toHaveLength(1)
    expect(nav[0].header).toBe('Entities')

    const names = nav[0].items.map(i => i.name)
    expect(names).toEqual(expect.arrayContaining(['SBOM Dashboard', 'Services']))
  })

  it('never emits the same header twice', () => {
    const all = new Set([
      'infrastructure', 'ipam', 'sbom', 'services_oncall', 'secrets',
      'issues', 'discovery', 'access_reviews', 'webhooks_alerting',
      'nav_streams', 'nav_documents', 'nav_pages', 'nav_diagrams',
    ])
    const headers = navFor(all).map(c => c.header)
    expect(new Set(headers).size).toBe(headers.length)
  })

  it('dedupes items that share an href across modules', () => {
    const all = new Set([
      'infrastructure', 'ipam', 'sbom', 'services_oncall', 'secrets',
      'issues', 'discovery', 'access_reviews', 'webhooks_alerting',
      'nav_streams', 'nav_documents', 'nav_pages', 'nav_diagrams',
    ])
    for (const category of navFor(all)) {
      const hrefs = category.items.map(i => i.href)
      expect(new Set(hrefs).size).toBe(hrefs.length)
    }
  })

  it('omits a pillar entirely when none of its modules are enabled', () => {
    const nav = navFor(new Set(['nav_documents']))
    expect(nav.map(c => c.header)).toEqual(['Documents'])
  })

  it('sections are collapsible so a large pillar can be folded away', () => {
    const nav = navFor(new Set(['infrastructure', 'ipam', 'sbom']))
    expect(nav[0].collapsible).toBe(true)
  })

  it('keys stay group-scoped so collapse state persists per pillar', () => {
    const nav = navFor(new Set(['issues', 'nav_streams']))
    expect(nav.map(c => c.key)).toEqual(['group-workstreams', 'group-issues'])
  })

  it('returns an empty array when no modules are enabled', () => {
    expect(navFor(new Set<string>())).toEqual([])
  })
})
