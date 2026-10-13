import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AssigneePicker from './AssigneePicker'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getIdentities: vi.fn(),
    getOrganizations: vi.fn(),
  },
}))

const createQueryClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } })

function renderPicker(onChange = vi.fn()) {
  const queryClient = createQueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <AssigneePicker value={null} onChange={onChange} />
    </QueryClientProvider>
  )
  return { onChange }
}

describe('AssigneePicker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getIdentities).mockResolvedValue({
      items: [{
        id: 42, username: 'jane', full_name: 'Jane Doe', email: 'jane@x.com',
        identity_type: 'employee', auth_provider: 'local', is_active: true,
        is_superuser: false, created_at: '2026-01-01',
      }],
    })
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [{ id: 7, name: 'Platform Team', created_at: '2026-01-01', updated_at: '2026-01-01' }],
    })
  })

  it('resolves an identity selection to {assignee_type: identity, assignee_id}', async () => {
    const { onChange } = renderPicker()
    fireEvent.focus(screen.getByRole('combobox'))
    await waitFor(() => screen.getByText('Jane Doe (Identity)'))
    fireEvent.click(screen.getByText('Jane Doe (Identity)'))
    expect(onChange).toHaveBeenCalledWith({ assignee_type: 'identity', assignee_id: 42 })
  })

  it('resolves an org unit selection to {assignee_type: org_unit, assignee_id}', async () => {
    const { onChange } = renderPicker()
    fireEvent.focus(screen.getByRole('combobox'))
    await waitFor(() => screen.getByText('Platform Team (Org Unit)'))
    fireEvent.click(screen.getByText('Platform Team (Org Unit)'))
    expect(onChange).toHaveBeenCalledWith({ assignee_type: 'org_unit', assignee_id: 7 })
  })

  it('clicking Unassigned clears the value', async () => {
    const { onChange } = renderPicker()
    fireEvent.focus(screen.getByRole('combobox'))
    await waitFor(() => screen.getByText('Unassigned'))
    fireEvent.click(screen.getByText('Unassigned'))
    expect(onChange).toHaveBeenCalledWith(null)
  })
})
