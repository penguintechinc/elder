import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import IssueDetail from './IssueDetail'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getIssue: vi.fn(),
    getIssueComments: vi.fn().mockResolvedValue({ items: [] }),
    getIssueLabels: vi.fn().mockResolvedValue({ items: [] }),
    getIssueEntities: vi.fn().mockResolvedValue({ items: [] }),
    getLabels: vi.fn().mockResolvedValue({ items: [] }),
    getEntities: vi.fn().mockResolvedValue({ items: [] }),
    getIdentities: vi.fn().mockResolvedValue({ items: [] }),
    getIdentity: vi.fn(),
    getOrganizations: vi.fn().mockResolvedValue({ items: [] }),
    getProjects: vi.fn().mockResolvedValue({ items: [] }),
    getMilestones: vi.fn().mockResolvedValue({ items: [] }),
    updateIssue: vi.fn(),
    deleteIssue: vi.fn(),
    createIssueComment: vi.fn(),
    addIssueLabel: vi.fn(),
    removeIssueLabel: vi.fn(),
    linkIssueEntity: vi.fn(),
    unlinkIssueEntity: vi.fn(),
    linkIssueToProject: vi.fn(),
    unlinkIssueFromProject: vi.fn(),
    linkIssueToMilestone: vi.fn(),
    unlinkIssueFromMilestone: vi.fn(),
  },
}))

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

vi.mock('./Issues', () => ({
  CreateIssueModal: () => null,
}))

function renderDetail() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/issues/1']}>
        <Routes>
          <Route path="/issues/:id" element={<IssueDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const baseIssue = {
  id: 1,
  title: 'Server down',
  status: 'open' as const,
  priority: 'high' as const,
  issue_type: 'operations' as const,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

describe('IssueDetail', () => {
  beforeEach(() => vi.clearAllMocks())

  it('does not render a support details card for a non-support issue', async () => {
    vi.mocked(api.getIssue).mockResolvedValue(baseIssue)
    renderDetail()
    await waitFor(() => screen.getByText('Server down'))
    expect(screen.queryByTestId('issue-support-section')).toEqual(null)
  })

  it('renders channel and category for a support issue', async () => {
    vi.mocked(api.getIssue).mockResolvedValue({
      ...baseIssue, issue_type: 'support', channel: 'email', category: 'billing',
    })
    renderDetail()
    await waitFor(() => screen.getByTestId('issue-support-section'))
    expect(screen.getByText('email')).toBeDefined()
    expect(screen.getByText('billing')).toBeDefined()
  })

  it('shows an error toast and skips the API call when clearing an assignee', async () => {
    const toast = (await import('react-hot-toast')).default
    vi.mocked(api.getIssue).mockResolvedValue({ ...baseIssue, assignee_id: 42, assignee_type: 'identity' })
    vi.mocked(api.getIdentities).mockResolvedValue({
      items: [{ id: 42, username: 'jane', full_name: 'Jane Doe', email: 'jane@x.com', identity_type: 'employee', auth_provider: 'local', is_active: true, is_superuser: false, created_at: '2026-01-01' }],
    })
    renderDetail()
    await waitFor(() => screen.getByText('Server down'))
    const picker = await screen.findByTestId('assignee-picker')
    picker.querySelector('input')!.focus()
    const unassigned = await screen.findByText('Unassigned')
    fireEvent.click(unassigned)
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(api.updateIssue).not.toHaveBeenCalled()
  })
})
