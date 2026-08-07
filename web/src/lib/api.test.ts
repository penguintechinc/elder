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

describe('ApiClient - Intake Forms', () => {
  beforeEach(() => vi.clearAllMocks())

  it('createIntakeForm posts to /intake-forms', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { id: 1, slug: 'support-request' } })
    await api.createIntakeForm({
      name: 'Support Request',
      slug: 'support-request',
      fields: [{ id: 'email', label: 'Email', type: 'email', required: true }],
    })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/intake-forms', {
      name: 'Support Request',
      slug: 'support-request',
      fields: [{ id: 'email', label: 'Email', type: 'email', required: true }],
    })
  })

  it('updateIntakeForm sends PATCH to /intake-forms/:id', async () => {
    mockAxiosInstance.patch.mockResolvedValue({ data: { id: 1 } })
    await api.updateIntakeForm(1, { is_active: false })
    expect(mockAxiosInstance.patch).toHaveBeenCalledWith('/intake-forms/1', { is_active: false })
  })

  it('submitPublicIntakeForm posts to /intake/:slug/submit', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { status: 'created', reference: 'abc-123' } })
    await api.submitPublicIntakeForm('support-request', { fields: { email: 'a@b.com' } })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/intake/support-request/submit', {
      fields: { email: 'a@b.com' },
    })
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
