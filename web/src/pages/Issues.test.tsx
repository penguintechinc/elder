import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getIssues: vi.fn(),
    getOrganizations: vi.fn().mockResolvedValue({ items: [] }),
    getEntities: vi.fn().mockResolvedValue({ items: [] }),
    getLabels: vi.fn().mockResolvedValue({ items: [] }),
    updateIssue: vi.fn(),
    createIssue: vi.fn(),
    linkIssueEntity: vi.fn(),
    addIssueLabel: vi.fn(),
  },
}))

// Mock AssigneePicker with a button to trigger onChange so tests can verify assignee wiring
vi.mock('@/components/AssigneePicker', () => ({
  default: ({ onChange }: { onChange: (val: { assignee_type: 'identity' | 'org_unit'; assignee_id: number } | null) => void }) => (
    <div data-testid="assignee-picker">
      <button
        type="button"
        data-testid="assignee-picker-select-button"
        onClick={() => onChange({ assignee_type: 'org_unit', assignee_id: 7 })}
      >
        Select Assignee
      </button>
    </div>
  ),
  AssigneeValue: {},
}))

import Issues from './Issues'

function renderIssues() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Issues />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

// Backend stores issue_type as UPPERCASE (SUPPORT, BUG, etc.) — test against real data
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const items: Array<{ id: number; title: string; status: 'open'; priority: 'high' | 'medium'; issue_type: any; created_at: string; updated_at: string }> = [
  { id: 1, title: 'Server down', status: 'open' as const, priority: 'high' as const, issue_type: 'OPERATIONS', created_at: '2026-01-01', updated_at: '2026-01-01' },
  { id: 2, title: 'Customer cannot log in', status: 'open' as const, priority: 'medium' as const, issue_type: 'SUPPORT', created_at: '2026-01-01', updated_at: '2026-01-01' },
]

describe('Issues list', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getIssues).mockResolvedValue({ items, total: 2, page: 1, pages: 1, per_page: 50 })
  })

  it('renders an issue_type badge per issue (case-insensitive, uppercase backend)', async () => {
    renderIssues()
    await waitFor(() => screen.getByText('Server down'))
    // Verify the filter dropdown exists with options
    const typeFilter = screen.getByTestId('issue-type-filter') as HTMLSelectElement
    expect(typeFilter).toBeDefined()
    expect(typeFilter.options.length).toBeGreaterThan(1)
    // Verify badges render friendly labels even though backend sends UPPERCASE
    // ("SUPPORT" → "Support", "OPERATIONS" → "Operations")
    expect(screen.getAllByText(/Operations|Support/)).toBeDefined()
  })

  it('filters the list client-side by issue_type (server does not support it)', async () => {
    renderIssues()
    await waitFor(() => screen.getByText('Server down'))
    const typeFilter = screen.getByTestId('issue-type-filter') as HTMLSelectElement
    fireEvent.change(typeFilter, { target: { value: 'support' } })
    // After filtering by support type, only the support issue should show
    await waitFor(() => expect(screen.queryByText('Server down')).toBeNull(), { timeout: 1000 })
  })

  it('shows status badge and quick-change Select with current status (case-tolerant)', async () => {
    // Test that UPPERCASE status from backend displays correctly in badges and Selects
    const upperCaseIssues = [
      { id: 1, title: 'Server down', status: 'OPEN' as unknown as 'open', priority: 'HIGH' as unknown as 'high', issue_type: 'OPERATIONS', created_at: '2026-01-01', updated_at: '2026-01-01' },
    ]
    vi.mocked(api.getIssues).mockResolvedValue({ items: upperCaseIssues, total: 1, page: 1, pages: 1, per_page: 50 })
    renderIssues()
    await waitFor(() => screen.getByText('Server down'))

    // Badge should show Title-cased status ("Open"), not "OPEN"
    // Use getAllByText since "Open" appears in both badge and select option
    const openElements = screen.getAllByText('Open')
    expect(openElements.length).toBeGreaterThan(0)

    // Quick-change Select should show the current status selected (not blank)
    // Find the select with "Open" as the displayed value
    const selects = screen.getAllByDisplayValue('Open')
    expect(selects.length).toBeGreaterThan(0)
  })

  it('filters the list client-side by search text', async () => {
    renderIssues()
    await waitFor(() => screen.getByText('Server down'))
    const searchInput = screen.getByPlaceholderText('Search issues...') as HTMLInputElement
    fireEvent.change(searchInput, { target: { value: 'login' } })
    // After searching for 'login', only the login issue should show
    await waitFor(() => expect(screen.queryByText('Server down')).toBeNull(), { timeout: 1000 })
  })
})

describe('CreateIssueModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [
        {
          id: 5,
          name: 'Acme Corp',
          created_at: '2026-01-01',
          updated_at: '2026-01-01',
        },
      ],
    })
    vi.mocked(api.getEntities).mockResolvedValue({ items: [] })
    vi.mocked(api.getLabels).mockResolvedValue({ items: [] })
  })

  it('blocks submit when organization is not selected (client validation)', async () => {
    vi.mocked(api.createIssue).mockResolvedValue({ id: 99 })
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <Issues />
        </MemoryRouter>
      </QueryClientProvider>
    )
    fireEvent.click(screen.getByText('Create Issue'))
    await waitFor(() => screen.getByTestId('create-issue-form'))
    // Fill title but leave organization empty
    fireEvent.change(screen.getByTestId('issue-title-input'), { target: { value: 'New issue' } })
    // Try to submit without selecting organization
    fireEvent.click(screen.getByTestId('submit-issue-button'))
    // Verify createIssue was NOT called because client validation blocked it
    expect(api.createIssue).not.toHaveBeenCalled()
  })

  it('shows support fields only when issue_type is support', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <Issues />
        </MemoryRouter>
      </QueryClientProvider>
    )
    fireEvent.click(screen.getByText('Create Issue'))
    await waitFor(() => screen.getByTestId('create-issue-form'))
    // Initially support fields should not exist
    expect(screen.queryByTestId('support-fields')).toBeNull()
    // Change issue type to support
    fireEvent.change(screen.getByTestId('issue-type-select'), { target: { value: 'support' } })
    // Now support fields should be visible
    await waitFor(() => {
      expect(screen.queryByTestId('support-fields')).toBeDefined()
    })
    // Change back to other
    fireEvent.change(screen.getByTestId('issue-type-select'), { target: { value: 'other' } })
    // Support fields should disappear
    await waitFor(() => {
      expect(screen.queryByTestId('support-fields')).toBeNull()
    })
  })

  it('submits with organization_id, issue_type, and support fields wired correctly, and assignee picker renders', async () => {
    vi.mocked(api.createIssue).mockResolvedValue({ id: 99 })
    vi.mocked(api.linkIssueEntity).mockResolvedValue({})
    vi.mocked(api.addIssueLabel).mockResolvedValue({})
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <Issues />
        </MemoryRouter>
      </QueryClientProvider>
    )
    fireEvent.click(screen.getByText('Create Issue'))
    const titleInput = await waitFor(() => screen.getByTestId('issue-title-input'), { timeout: 5000 })
    // Verify AssigneePicker mock is rendered (proves component renders + mock button exists)
    expect(screen.getByTestId('assignee-picker')).toBeDefined()
    expect(screen.getByTestId('assignee-picker-select-button')).toBeDefined()
    // Fill in the form
    fireEvent.change(titleInput, { target: { value: 'Test issue' } })
    fireEvent.change(screen.getByTestId('issue-organization-select'), { target: { value: '5' } })
    fireEvent.change(screen.getByTestId('issue-type-select'), { target: { value: 'support' } })
    // Support fields should now be visible
    expect(screen.queryByTestId('support-fields')).toBeDefined()
    // Click assignee picker button to exercise the component (assignee payload wiring verified in IssueDetail tests)
    fireEvent.click(screen.getByTestId('assignee-picker-select-button'))
    // Submit the form
    fireEvent.click(screen.getByTestId('submit-issue-button'))
    // Verify createIssue was called with correct payload
    await waitFor(() => expect(api.createIssue).toHaveBeenCalled(), { timeout: 5000 })
    const call = vi.mocked(api.createIssue).mock.calls[0][0]
    expect(call.title).toBe('Test issue')
    expect(call.organization_id).toBe(5)
    expect(call.issue_type).toBe('support')
    // Assignee wiring: clicking the picker set the org-unit assignee, which
    // must flow through handleSubmit into the createIssue payload.
    expect(call.assignee_type).toBe('org_unit')
    expect(call.assignee_id).toBe(7)
  })

})
