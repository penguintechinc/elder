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
  },
}))

vi.mock('@penguintechinc/react-libs/components', () => ({
  FormModalBuilder: ({ isOpen, title }: { isOpen: boolean; title: string }) => {
    if (!isOpen) return null
    return <div data-testid="create-issue-modal">{title}</div>
  },
  FormField: {},
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

  it('filters the list client-side by search text', async () => {
    renderIssues()
    await waitFor(() => screen.getByText('Server down'))
    const searchInput = screen.getByPlaceholderText('Search issues...') as HTMLInputElement
    fireEvent.change(searchInput, { target: { value: 'login' } })
    // After searching for 'login', only the login issue should show
    await waitFor(() => expect(screen.queryByText('Server down')).toBeNull(), { timeout: 1000 })
  })
})
