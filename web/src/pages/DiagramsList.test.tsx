import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import DiagramsList from './DiagramsList'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getDiagrams: vi.fn(),
    createDiagram: vi.fn(),
    deleteDiagram: vi.fn(),
  },
}))

const createQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

describe('DiagramsList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders SVG previews for diagrams with shapes, Empty diagram for blank ones, and img for thumbnail_url', async () => {
    vi.mocked(api.getDiagrams).mockResolvedValue({
      items: [
        {
          id: 1,
          title: 'Core Topology',
          description: 'Seeded diagram',
          status: 'published',
          is_public: false,
          is_template: false,
          tags: ['prod'],
          thumbnail_url: null,
          preview: {
            shapes: [
              {
                id: '10',
                x: 80,
                y: 120,
                width: 160,
                height: 70,
                shape_type: 'rectangle',
                label: 'Storefront',
              },
              {
                id: '11',
                x: 300,
                y: 120,
                width: 160,
                height: 70,
                shape_type: 'rectangle',
                label: 'Orders API',
              },
            ],
            connectors: [
              {
                id: '100',
                source: '10',
                target: '11',
                connector_type: 'arrow',
              },
            ],
          },
          owner_identity_id: 1,
          created_at: '2026-11-24T00:00:00Z',
          updated_at: '2026-11-24T00:00:00Z',
        },
        {
          id: 2,
          title: 'Blank Draft',
          description: '',
          status: 'draft',
          is_public: false,
          is_template: false,
          tags: [],
          thumbnail_url: null,
          preview: { shapes: [], connectors: [] },
          owner_identity_id: 1,
          created_at: '2026-11-24T00:00:00Z',
          updated_at: '2026-11-24T00:00:00Z',
        },
        {
          id: 3,
          title: 'Explicit Thumbnail',
          description: '',
          status: 'active',
          is_public: true,
          is_template: false,
          tags: [],
          thumbnail_url: 'https://cdn.example.com/diagram-3.png',
          preview: { shapes: [], connectors: [] },
          owner_identity_id: 1,
          created_at: '2026-11-24T00:00:00Z',
          updated_at: '2026-11-24T00:00:00Z',
        },
      ],
      pagination: { page: 1, per_page: 50, total: 3, pages: 1 },
    })

    const queryClient = createQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <DiagramsList />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(screen.getByText('Core Topology')).toBeTruthy()
    })

    // Card 1: SVG preview rendered with shapes & labels
    expect(screen.getByRole('img', { name: 'Preview of Core Topology' })).toBeTruthy()
    expect(screen.getByText('Storefront')).toBeTruthy()
    expect(screen.getByText('Orders API')).toBeTruthy()

    // Card 2: Genuine 0-shape fallback says "Empty diagram", never "No preview"
    expect(screen.getByText('Empty diagram')).toBeTruthy()
    expect(screen.queryByText('No preview')).toBeNull()

    // Card 3: Explicit thumbnail_url image preserved
    const thumbImg = screen.getByRole('img', { name: 'Explicit Thumbnail' })
    expect(thumbImg.tagName).toBe('IMG')
    expect(thumbImg.getAttribute('src')).toBe('https://cdn.example.com/diagram-3.png')
  })
})
