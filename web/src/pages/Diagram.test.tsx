import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Diagram from './Diagram'
import api from '@/lib/api'

// Regression coverage for the cloud-discovery graph rendering fix: the
// Diagram page must request + default-select the 4 cloud resource types so
// discovered networking/data-store/service/software nodes show up without
// the user having to find and enable filters manually.
vi.mock('@/lib/api', () => ({
  default: {
    getMap: vi.fn(),
    getOrganizations: vi.fn(),
  },
}))

// NetworkGraph renders @xyflow/react which needs layout/ResizeObserver APIs
// jsdom doesn't provide — stub it so this test stays focused on Diagram's
// own filter state and query params.
vi.mock('@/components/NetworkGraph', () => ({
  NetworkGraph: () => <div data-testid="network-graph-stub" />,
}))

const createQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

describe('Diagram', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getOrganizations).mockResolvedValue({ items: [] })
    vi.mocked(api.getMap).mockResolvedValue({
      nodes: [],
      edges: [],
      stats: { node_count: 0, edge_count: 0, truncated: false },
    })
  })

  it('defaults all resource type filters to selected, including the cloud discovery types', () => {
    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <Diagram />
      </QueryClientProvider>
    )

    for (const label of ['Networking Resources', 'Data Stores', 'Services', 'Software']) {
      const button = screen.getByRole('button', { name: new RegExp(label) })
      expect(button.className).toContain('border-primary-500')
    }
  })

  it('sends the 4 cloud discovery resource types to GET /graph/map by default', async () => {
    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <Diagram />
      </QueryClientProvider>
    )

    await waitFor(() => expect(api.getMap).toHaveBeenCalled())

    const callArgs = vi.mocked(api.getMap).mock.calls[0][0]
    const types = (callArgs?.resource_types ?? '').split(',')
    for (const t of ['networking_resource', 'data_store', 'service', 'software']) {
      expect(types).toContain(t)
    }
  })
})
