import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ModuleToggles from './ModuleToggles'
import api from '@/lib/api'

// Mock the API
vi.mock('@/lib/api', () => ({
  default: {
    getPortalProfile: vi.fn(),
    getTenantModules: vi.fn(),
    getModules: vi.fn(),
    setTenantModule: vi.fn(),
  },
}))

// Mock react-hot-toast
vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

const createQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

describe('ModuleToggles', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders access denied when user is not admin', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      global_role: 'viewer',
      tenant_role: null,
    } as any)

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <ModuleToggles />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(screen.getByText('Access Denied')).toBeDefined()
    })
  })

  it('renders module list for admin user', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      global_role: 'admin',
      tenant_id: 'test-tenant-1',
      tenant_role: null,
    } as any)

    vi.mocked(api.getTenantModules).mockResolvedValue({
      status: 'success',
      data: [
        { module_name: 'infrastructure', enabled: true },
        { module_name: 'services_oncall', enabled: false },
      ],
    })

    vi.mocked(api.getModules).mockResolvedValue({
      modules: [
        { name: 'infrastructure', nav_id: 'infrastructure', title: 'Infrastructure', licensed: true, effective: true },
        { name: 'services_oncall', nav_id: 'services_oncall', title: 'Services & On-Call', licensed: false, effective: false },
      ],
    })

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <ModuleToggles />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(screen.getByText('Infrastructure')).toBeDefined()
      expect(screen.getByText('Services & On-Call')).toBeDefined()
    })
  })

  it('renders licensed badge for unlicensed modules', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      global_role: 'admin',
      tenant_id: 'test-tenant-1',
      tenant_role: null,
    } as any)

    vi.mocked(api.getTenantModules).mockResolvedValue({
      status: 'success',
      data: [{ module_name: 'services_oncall', enabled: false }],
    })

    vi.mocked(api.getModules).mockResolvedValue({
      modules: [
        { name: 'services_oncall', nav_id: 'services_oncall', title: 'Services & On-Call', licensed: false, effective: false },
      ],
    })

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <ModuleToggles />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(screen.getByText('Not Licensed')).toBeDefined()
    })
  })

  it('calls toggle API when module toggle is clicked', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      global_role: 'admin',
      tenant_id: 'test-tenant-1',
      tenant_role: null,
    } as any)

    vi.mocked(api.getTenantModules).mockResolvedValue({
      status: 'success',
      data: [{ module_name: 'infrastructure', enabled: false }],
    })

    vi.mocked(api.getModules).mockResolvedValue({
      modules: [
        { name: 'infrastructure', nav_id: 'infrastructure', title: 'Infrastructure', licensed: true, effective: true },
      ],
    })

    vi.mocked(api.setTenantModule).mockResolvedValue({
      status: 'success',
      data: { module_name: 'infrastructure', enabled: true },
    })

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <ModuleToggles />
      </QueryClientProvider>
    )

    await waitFor(() => {
      const toggleButton = screen.getByTestId('toggle-infrastructure')
      expect(toggleButton).toBeDefined()
      fireEvent.click(toggleButton)
    })

    await waitFor(() => {
      expect(api.setTenantModule).toHaveBeenCalledWith('test-tenant-1', {
        module_name: 'infrastructure',
        enabled: true,
      })
    })
  })

  it('disables toggle for unlicensed modules', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      global_role: 'admin',
      tenant_id: 'test-tenant-1',
      tenant_role: null,
    } as any)

    vi.mocked(api.getTenantModules).mockResolvedValue({
      status: 'success',
      data: [{ module_name: 'services_oncall', enabled: false }],
    })

    vi.mocked(api.getModules).mockResolvedValue({
      modules: [
        { name: 'services_oncall', nav_id: 'services_oncall', title: 'Services & On-Call', licensed: false, effective: false },
      ],
    })

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <ModuleToggles />
      </QueryClientProvider>
    )

    await waitFor(() => {
      const toggleButton = screen.getByTestId('toggle-services_oncall')
      expect(toggleButton?.hasAttribute('disabled')).toBe(true)
    })
  })

  it('renders tenant admin with admin role', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      global_role: null,
      tenant_role: 'admin',
      tenant_id: 'test-tenant-1',
    } as any)

    vi.mocked(api.getTenantModules).mockResolvedValue({
      status: 'success',
      data: [],
    })

    vi.mocked(api.getModules).mockResolvedValue({
      modules: [],
    })

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <ModuleToggles />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(screen.getByText('Module Management')).toBeDefined()
    })
  })

  it('renders effective badge for effective modules', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      global_role: 'admin',
      tenant_id: 'test-tenant-1',
      tenant_role: null,
    } as any)

    vi.mocked(api.getTenantModules).mockResolvedValue({
      status: 'success',
      data: [{ module_name: 'infrastructure', enabled: true }],
    })

    vi.mocked(api.getModules).mockResolvedValue({
      modules: [
        { name: 'infrastructure', nav_id: 'infrastructure', title: 'Infrastructure', licensed: true, effective: true },
      ],
    })

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <ModuleToggles />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(screen.getByText('Effective')).toBeDefined()
    })
  })
})
