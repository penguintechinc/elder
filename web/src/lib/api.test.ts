import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockAxiosInstance = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  interceptors: {
    request: { use: vi.fn() },
    response: { use: vi.fn() },
  },
}

vi.mock('axios', () => ({
  default: { create: vi.fn(() => mockAxiosInstance) },
}))

const { default: api } = await import('@/lib/api')

describe('ApiClient - Issues', () => {
  beforeEach(() => vi.clearAllMocks())

  it('getIssues passes issue_type through as a query param', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: { items: [], total: 0 } })
    await api.getIssues({ issue_type: 'support' })
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/issues', {
      params: { issue_type: 'support' },
    })
  })

  it('createIssue forwards issue_type + assignee_type + assignee_id', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { id: 1 } })
    await api.createIssue({
      title: 'Server down',
      issue_type: 'support',
      assignee_id: 42,
      assignee_type: 'identity',
    })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/issues', {
      title: 'Server down',
      issue_type: 'support',
      assignee_id: 42,
      assignee_type: 'identity',
    })
  })

  it('updateIssue sends PATCH, not PUT (backend only registers PATCH /issues/:id)', async () => {
    mockAxiosInstance.patch.mockResolvedValue({ data: { id: 1 } })
    await api.updateIssue(1, { assignee_id: 7, assignee_type: 'org_unit' })
    expect(mockAxiosInstance.patch).toHaveBeenCalledWith('/issues/1', {
      assignee_id: 7,
      assignee_type: 'org_unit',
    })
    expect(mockAxiosInstance.put).not.toHaveBeenCalled()
  })
})

describe('ApiClient - Webhook filters', () => {
  beforeEach(() => vi.clearAllMocks())

  it('createWebhook forwards filter_issue_type/filter_assignee_type/filter_assignee_id', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { id: 1 } })
    await api.createWebhook({
      name: 'Support bot assignments',
      url: 'https://hooks.example.com/x',
      events: ['issue.assigned'],
      filter_issue_type: 'support',
      filter_assignee_type: 'identity',
      filter_assignee_id: 42,
    })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/webhooks', {
      name: 'Support bot assignments',
      url: 'https://hooks.example.com/x',
      events: ['issue.assigned'],
      filter_issue_type: 'support',
      filter_assignee_type: 'identity',
      filter_assignee_id: 42,
    })
  })
})
