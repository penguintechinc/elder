import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import SSOConfiguration from './SSOConfiguration'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getIdPConfigs: vi.fn(),
    createIdPConfig: vi.fn(),
    updateIdPConfig: vi.fn(),
    deleteIdPConfig: vi.fn(),
  },
}))

vi.mock('@penguintechinc/react-libs/components', () => ({
  FormModalBuilder: () => null,
}))

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <SSOConfiguration />
    </QueryClientProvider>,
  )
}

describe('SSOConfiguration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // regression: gh-313
  it('renders IdP configurations when GET /api/v1/sso/idp returns a bare array', async () => {
    vi.mocked(api.getIdPConfigs).mockResolvedValue([
      {
        id: 1,
        tenant_id: 2,
        idp_type: 'saml',
        name: 'Okta Corp',
        entity_id: 'https://okta.example.com/saml',
        jit_provisioning_enabled: true,
        default_role: 'reader',
        is_active: true,
      },
      {
        id: 2,
        tenant_id: 2,
        idp_type: 'oidc',
        name: 'Google Workspace',
        jit_provisioning_enabled: false,
        default_role: 'editor',
        is_active: true,
      },
    ])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Okta Corp')).toBeDefined()
      expect(screen.getByText('Google Workspace')).toBeDefined()
    })
    expect(screen.getByText('SAML')).toBeDefined()
    expect(screen.getByText('OIDC')).toBeDefined()
    expect(screen.queryByText('No identity providers configured')).toBeNull()
  })

  // regression: gh-313
  it('renders IdP configurations when response is wrapped in an items object', async () => {
    vi.mocked(api.getIdPConfigs).mockResolvedValue({
      items: [
        {
          id: 3,
          tenant_id: 2,
          idp_type: 'oidc',
          name: 'Auth0 Tenant',
          jit_provisioning_enabled: true,
          default_role: 'admin',
          is_active: true,
        },
      ],
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Auth0 Tenant')).toBeDefined()
    })
  })

  // regression: gh-313
  it('renders empty state when GET /api/v1/sso/idp returns an empty bare array', async () => {
    vi.mocked(api.getIdPConfigs).mockResolvedValue([])

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('No identity providers configured')).toBeDefined()
    })
  })
})
