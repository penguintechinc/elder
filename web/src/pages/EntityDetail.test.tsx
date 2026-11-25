import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import EntityDetail from './EntityDetail'
import api from '@/lib/api'

// Regression coverage for the broken Dependencies card: EntityDetail used to
// query unsupported *_entity_id params (silently ignored by the backend,
// which returned ALL dependencies unfiltered) and render fictional
// dep.target_entity/source_entity fields the API never returns, so every row
// showed "Entity #undefined" linking to /entities/undefined.
vi.mock('@/lib/api', () => ({
  default: {
    getEntity: vi.fn(),
    getEntityMetadata: vi.fn(),
    getDependencies: vi.fn(),
    getOrganization: vi.fn(),
    getIssues: vi.fn(),
    getEntities: vi.fn(),
    deleteEntity: vi.fn(),
    deleteDependency: vi.fn(),
    createDependency: vi.fn(),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const createQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

const ENTITY = {
  id: 42,
  unique_id: 'ent-42',
  name: 'Prod VPC',
  type: 'vpc',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  is_active: true,
}

const renderPage = () => {
  const queryClient = createQueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/entities/42']}>
        <Routes>
          <Route path="/entities/:id" element={<EntityDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('EntityDetail dependencies card', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getEntity).mockResolvedValue(ENTITY)
    vi.mocked(api.getEntityMetadata).mockResolvedValue({ items: [] })
    vi.mocked(api.getIssues).mockResolvedValue({ items: [] })
    vi.mocked(api.getEntities).mockResolvedValue({
      items: [{ id: 99, name: 'Downstream Entity' }],
    })
    vi.mocked(api.getDependencies).mockImplementation((params) => {
      if (params?.source_type === 'entity' && params?.source_id === 42) {
        return Promise.resolve({
          items: [
            {
              id: 1,
              tenant_id: 1,
              source_type: 'entity',
              source_id: 42,
              target_type: 'entity',
              target_id: 99,
              dependency_type: 'depends',
              created_at: '2026-01-01T00:00:00Z',
              updated_at: '2026-01-01T00:00:00Z',
            },
          ],
        })
      }
      if (params?.target_type === 'entity' && params?.target_id === 42) {
        return Promise.resolve({ items: [] })
      }
      return Promise.resolve({ items: [] })
    })
  })

  it('queries dependencies with the params the backend actually supports (source_id/target_id)', async () => {
    renderPage()

    await waitFor(() => {
      expect(api.getDependencies).toHaveBeenCalledWith({ source_type: 'entity', source_id: 42 })
      expect(api.getDependencies).toHaveBeenCalledWith({ target_type: 'entity', target_id: 42 })
    })

    // The old, broken params must never be sent — the backend silently
    // ignores them and returns every dependency unfiltered.
    expect(api.getDependencies).not.toHaveBeenCalledWith(
      expect.objectContaining({ source_entity_id: expect.anything() })
    )
    expect(api.getDependencies).not.toHaveBeenCalledWith(
      expect.objectContaining({ target_entity_id: expect.anything() })
    )
  })

  it('renders the real dependency target name instead of "Entity #undefined"', async () => {
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Downstream Entity')).toBeDefined()
    })
    expect(screen.queryByText(/undefined/i)).toBeNull()
  })
})
