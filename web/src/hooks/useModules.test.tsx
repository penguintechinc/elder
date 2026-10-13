import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useModules } from './useModules'
import api from '@/lib/api'

// Mock the API
vi.mock('@/lib/api', () => ({
  default: {
    getModules: vi.fn(),
  },
}))

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe('useModules', () => {
  // regression: useModules must enable modules whether the frontend id is bare (matches backend name) or nav_-prefixed (matches nav_id)
  it('enables modules keyed on both bare name and nav_-prefixed id', async () => {
    vi.mocked(api.getModules).mockResolvedValue({
      modules: [
        {
          name: 'infrastructure',
          nav_id: 'nav_infrastructure',
          title: 'Infrastructure',
          installed: true,
          licensed: true,
          tenant_enabled: true,
          effective: true,
          capabilities: {},
        },
        {
          name: 'diagrams',
          nav_id: 'nav_diagrams',
          title: 'Diagrams',
          installed: true,
          licensed: true,
          tenant_enabled: true,
          effective: true,
          capabilities: {},
        },
        {
          name: 'services_oncall',
          nav_id: 'nav_services_oncall',
          title: 'Services & On-Call',
          installed: true,
          licensed: false,
          tenant_enabled: false,
          effective: false,
          capabilities: {},
        },
      ],
    })

    const { result } = renderHook(() => useModules(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.enabled.has('infrastructure')).toBe(true)
    expect(result.current.enabled.has('nav_infrastructure')).toBe(true)
    expect(result.current.enabled.has('nav_diagrams')).toBe(true)

    expect(result.current.enabled.has('services_oncall')).toBe(false)
    expect(result.current.enabled.has('nav_services_oncall')).toBe(false)
  })
})
