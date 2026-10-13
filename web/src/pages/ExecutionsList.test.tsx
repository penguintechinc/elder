import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import ExecutionsList from './ExecutionsList'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    listAllStreamExecutions: vi.fn(),
  },
}))

const createQueryClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } })

function renderExecutionsList() {
  const queryClient = createQueryClient()
  render(
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <ExecutionsList />
      </QueryClientProvider>
    </BrowserRouter>
  )
}

describe('ExecutionsList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders executions from the mocked listAllStreamExecutions endpoint', async () => {
    vi.mocked(api.listAllStreamExecutions).mockResolvedValue({
      data: [
        {
          id: 1,
          execution_id: '550e8400-e29b-41d4-a716-446655440000',
          stream_id: 10,
          stream_name: 'Test Stream',
          status: 'success',
          trigger_type: 'manual',
          created_at: '2026-08-07T12:00:00Z',
        },
        {
          id: 2,
          execution_id: '550e8400-e29b-41d4-a716-446655440001',
          stream_id: 11,
          stream_name: 'Another Stream',
          status: 'failed',
          trigger_type: 'scheduled',
          created_at: '2026-08-07T11:00:00Z',
        },
      ],
      total: 2,
      page: 1,
      per_page: 50,
    })

    renderExecutionsList()

    await waitFor(() => {
      expect(screen.getByText('Test Stream')).toBeTruthy()
      expect(screen.getByText('Another Stream')).toBeTruthy()
    })

    expect(vi.mocked(api.listAllStreamExecutions)).toHaveBeenCalledWith({ page: 1, per_page: 50 })
  })

  it('shows a clean empty state when data is empty', async () => {
    vi.mocked(api.listAllStreamExecutions).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      per_page: 50,
    })

    renderExecutionsList()

    await waitFor(() => {
      expect(screen.getByText('No executions')).toBeTruthy()
      expect(screen.getByText('Executions will appear here once playbooks are triggered')).toBeTruthy()
    })

    // Ensure error banner is not shown
    expect(screen.queryByText(/Failed to load executions/)).toBeNull()
  })

  it('renders execution rows with execution_id and stream_name', async () => {
    vi.mocked(api.listAllStreamExecutions).mockResolvedValue({
      data: [
        {
          id: 1,
          execution_id: '550e8400-e29b-41d4-a716-446655440000',
          stream_id: 10,
          stream_name: 'Test Stream',
          status: 'success',
          trigger_type: 'manual',
          created_at: '2026-08-07T12:00:00Z',
        },
      ],
      total: 1,
      page: 1,
      per_page: 50,
    })

    renderExecutionsList()

    await waitFor(() => {
      expect(screen.getByTestId('execution-id').textContent).toBe('550e8400')
      expect(screen.getByTestId('stream-name').textContent).toBe('Test Stream')
    })
  })
})
