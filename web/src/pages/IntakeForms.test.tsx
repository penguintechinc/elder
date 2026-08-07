import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import IntakeForms from './IntakeForms'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getPortalProfile: vi.fn(),
    getIntakeForms: vi.fn(),
    getOrganizations: vi.fn().mockResolvedValue({ items: [] }),
    getIdentities: vi.fn().mockResolvedValue({ items: [] }),
  },
}))

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <IntakeForms />
    </QueryClientProvider>
  )
}

describe('IntakeForms admin page', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows an admin-required message for non-admins', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({ global_role: 'viewer', tenant_role: null })
    vi.mocked(api.getIntakeForms).mockResolvedValue({ items: [] })
    renderPage()
    await waitFor(() => screen.getByText(/admin access required/i))
  })

  it('lists intake forms for an admin', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({ global_role: 'admin', tenant_role: null })
    vi.mocked(api.getIntakeForms).mockResolvedValue({
      items: [{ id: 1, village_id: 'v1', name: 'Support Request', slug: 'support-request', fields: [], issue_type: 'support', is_public: true, captcha_required: true, is_active: true }],
    })
    renderPage()
    await waitFor(() => screen.getByText('Support Request'))
    const slugElement = screen.getByText('/intake/support-request')
    expect(slugElement).toBeTruthy()
  })

})
