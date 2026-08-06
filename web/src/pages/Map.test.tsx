import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import MapPage from './Map'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getEntities: vi.fn(),
  },
}))

// MapLibre GL needs a real WebGL canvas, which jsdom doesn't provide — stub
// the classes so this test stays focused on MapPage's own entity -> marker
// aggregation (exact metadata.location vs. region-approximate fallback) and
// loading/empty states, not MapLibre's rendering internals.
// vi.mock factories are hoisted above imports, so the fake classes must be
// declared inside the factory rather than referenced from module scope.
vi.mock('maplibre-gl', () => {
  class FakeMarker {
    setLngLat = vi.fn().mockReturnThis()
    setPopup = vi.fn().mockReturnThis()
    addTo = vi.fn().mockReturnThis()
    remove = vi.fn()
  }

  class FakePopup {
    setDOMContent = vi.fn().mockReturnThis()
  }

  class FakeMap {
    addControl = vi.fn()
    on = vi.fn((event: string, cb: () => void) => {
      if (event === 'load') cb()
    })
    remove = vi.fn()
  }

  return {
    Map: FakeMap,
    Marker: FakeMarker,
    Popup: FakePopup,
    NavigationControl: vi.fn(),
  }
})

const createQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

describe('MapPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('requests entities with a high per_page to find geolocated resources', async () => {
    vi.mocked(api.getEntities).mockResolvedValue({ items: [], total: 0 })
    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <MapPage />
      </QueryClientProvider>
    )

    await waitFor(() => expect(api.getEntities).toHaveBeenCalledWith({ per_page: 1000 }))
  })

  it('shows the empty state when no entities have a known location', async () => {
    vi.mocked(api.getEntities).mockResolvedValue({ items: [], total: 0 })
    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <MapPage />
      </QueryClientProvider>
    )

    await screen.findByText('No geolocated entities yet')
  })

  it('plots an entity with exact metadata.location coordinates', async () => {
    vi.mocked(api.getEntities).mockResolvedValue({
      items: [
        {
          id: 1,
          name: 'Dallas DC',
          type: 'datacenter',
          sub_type: null,
          region: null,
          metadata: {
            location: { city: 'Dallas', state: 'TX', country: 'US', latitude: 32.7767, longitude: -96.797 },
          },
        },
      ],
      total: 1,
    })

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <MapPage />
      </QueryClientProvider>
    )

    await screen.findByText('1 located')
    expect(screen.queryByText('No geolocated entities yet')).toBeNull()
  })

  it('falls back to region-approximate placement for cloud entities without exact coordinates', async () => {
    vi.mocked(api.getEntities).mockResolvedValue({
      items: [
        {
          id: 2,
          name: 'prod-db',
          type: 'service',
          sub_type: 'database',
          region: 'us-east-1',
          metadata: null,
        },
      ],
      total: 1,
    })

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <MapPage />
      </QueryClientProvider>
    )

    await screen.findByText(/1 region-approximate/)
  })

  it('skips entities with neither exact coordinates nor a recognized region', async () => {
    vi.mocked(api.getEntities).mockResolvedValue({
      items: [
        {
          id: 3,
          name: 'mystery-box',
          type: 'compute',
          sub_type: null,
          region: 'not-a-real-region',
          metadata: null,
        },
      ],
      total: 1,
    })

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <MapPage />
      </QueryClientProvider>
    )

    await screen.findByText('No geolocated entities yet')
  })
})
