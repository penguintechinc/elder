import { MODULES } from '../registry'

const VALID_GROUPS = ['core', 'workflow', 'kb']
const EXPECTED: Record<string, string> = {
  infrastructure: 'core',
  ipam: 'core',
  discovery: 'core',
  sbom: 'core',
  secrets: 'core',
  services_oncall: 'core',
  access_reviews: 'core',
  webhooks_alerting: 'core',
  issues: 'workflow',
  nav_streams: 'workflow',
  nav_documents: 'kb',
  nav_pages: 'kb',
  nav_diagrams: 'kb',
}

describe('Module Grouping', () => {
  it('every module has a valid group', () => {
    for (const module of MODULES) {
      expect(VALID_GROUPS).toContain(module.group)
    }
  })

  it('group mapping matches spec', () => {
    const got = Object.fromEntries(MODULES.map(m => [m.id, m.group]))
    expect(got).toEqual(EXPECTED)
  })

  it('issues module is in workflow group', () => {
    const issuesModule = MODULES.find(m => m.id === 'issues')
    expect(issuesModule?.group).toBe('workflow')
  })

  it('diagrams module is in kb group', () => {
    const diagramsModule = MODULES.find(m => m.id === 'nav_diagrams')
    expect(diagramsModule?.group).toBe('kb')
  })
})
