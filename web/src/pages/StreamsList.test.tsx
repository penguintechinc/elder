import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import StreamsList from './StreamsList'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    listStreams: vi.fn(),
    createStream: vi.fn(),
    duplicateStream: vi.fn(),
    deleteStream: vi.fn(),
  },
}))

const createQueryClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } })

function renderStreamsList() {
  const queryClient = createQueryClient()
  render(
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <StreamsList />
      </QueryClientProvider>
    </BrowserRouter>
  )
}

describe('StreamsList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders streams from the mocked listStreams endpoint using .data key', async () => {
    vi.mocked(api.listStreams).mockResolvedValue({
      data: [
        {
          id: 10,
          name: 'Test Stream',
          description: 'A test stream',
          trigger_type: 'manual',
          is_enabled: true,
          execution_count: 5,
          created_at: '2026-08-07T12:00:00Z',
          updated_at: '2026-08-07T12:00:00Z',
        },
        {
          id: 11,
          name: 'Another Stream',
          description: 'Another test',
          trigger_type: 'scheduled',
          is_enabled: false,
          execution_count: 2,
          created_at: '2026-08-07T11:00:00Z',
          updated_at: '2026-08-07T11:00:00Z',
        },
      ],
      total: 2,
      page: 1,
      per_page: 50,
    })

    renderStreamsList()

    await waitFor(() => {
      expect(screen.getByText('Test Stream')).toBeTruthy()
      expect(screen.getByText('Another Stream')).toBeTruthy()
    })

    expect(vi.mocked(api.listStreams)).toHaveBeenCalledWith({ page: 1, per_page: 50 })
  })

  it('shows a clean empty state when data is empty', async () => {
    vi.mocked(api.listStreams).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      per_page: 50,
    })

    renderStreamsList()

    await waitFor(() => {
      expect(screen.getByText('No playbooks yet')).toBeTruthy()
      expect(screen.getByText('Create your first automation workflow to get started')).toBeTruthy()
    })

    // Ensure error banner is not shown
    expect(screen.queryByText(/Failed to load playbooks/)).toBeNull()
  })

  it('renders stream rows with name and execution count', async () => {
    vi.mocked(api.listStreams).mockResolvedValue({
      data: [
        {
          id: 10,
          name: 'Test Stream',
          description: 'A test stream',
          trigger_type: 'manual',
          is_enabled: true,
          execution_count: 5,
          created_at: '2026-08-07T12:00:00Z',
          updated_at: '2026-08-07T12:00:00Z',
        },
      ],
      total: 1,
      page: 1,
      per_page: 50,
    })

    renderStreamsList()

    await waitFor(() => {
      expect(screen.getByText('Test Stream')).toBeTruthy()
      expect(screen.getByText('5')).toBeTruthy()
    })
  })
})
