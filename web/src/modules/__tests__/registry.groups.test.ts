import { MODULES, navFor } from '../registry'

const VALID_GROUPS = ['workstreams', 'issues', 'relationships', 'entities', 'documents']
const EXPECTED: Record<string, string> = {
  // W — Workstreams
  nav_streams: 'workstreams',
  webhooks_alerting: 'workstreams',
  // I — Issues
  issues: 'issues',
  // R — Relationships
  discovery: 'relationships',
  access_reviews: 'relationships',
  // E — Entities
  infrastructure: 'entities',
  ipam: 'entities',
  sbom: 'entities',
  services_oncall: 'entities',
  secrets: 'entities',
  // D — Documents
  nav_documents: 'documents',
  nav_pages: 'documents',
  nav_diagrams: 'documents',
}

describe('Module Grouping (WIRED)', () => {
  it('every module has a valid WIRED group', () => {
    for (const module of MODULES) {
      expect(VALID_GROUPS).toContain(module.group)
    }
  })

  it('group mapping matches spec', () => {
    const got = Object.fromEntries(MODULES.map(m => [m.id, m.group]))
    expect(got).toEqual(EXPECTED)
  })

  it('issues module is in the issues group', () => {
    const issuesModule = MODULES.find(m => m.id === 'issues')
    expect(issuesModule?.group).toBe('issues')
  })

  it('diagrams module is in the documents group', () => {
    const diagramsModule = MODULES.find(m => m.id === 'nav_diagrams')
    expect(diagramsModule?.group).toBe('documents')
  })

  it('nav headers render in WIRED acronym order', () => {
    const all = new Set(MODULES.map(m => m.id))
    const headers = navFor(all)
      .filter(c => c.key?.startsWith('group-'))
      .map(c => c.header)
    expect(headers).toEqual([
      'Workstreams',
      'Issues',
      'Relationships',
      'Entities',
      'Documents',
    ])
  })
})
