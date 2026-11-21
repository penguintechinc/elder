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

const { default: api, isAuthenticated } = await import('@/lib/api')

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

// gh security audit, High: web/src/lib/api.ts used to copy the access +
// refresh JWT into localStorage (XSS-exfiltratable). Both now arrive as
// HttpOnly cookies set directly by the backend response -- this client must
// never persist them itself, only the non-sensitive "logged in" flag.
describe('ApiClient - auth cookie migration (gh security audit)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('portalLogin never writes access/refresh tokens to localStorage', async () => {
    mockAxiosInstance.post.mockResolvedValue({
      data: { access_token: 'a.b.c', refresh_token: 'r.s.t', expires_in: 3600 },
    })

    await api.portalLogin('user@example.com', 'password123')

    expect(localStorage.getItem('elder_token')).toBeNull()
    expect(localStorage.getItem('elder_refresh_token')).toBeNull()
    expect(localStorage.getItem('elder_authenticated')).toBe('1')
  })

  it('login (identity auth) never writes the access token to localStorage', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { access_token: 'a.b.c' } })

    await api.login('someuser', 'password123')

    expect(localStorage.getItem('elder_token')).toBeNull()
    expect(localStorage.getItem('elder_authenticated')).toBe('1')
  })

  it('portalRefreshToken sends no body and never touches localStorage tokens', async () => {
    mockAxiosInstance.post.mockResolvedValue({
      data: { access_token: 'new.token', expires_in: 3600 },
    })

    await api.portalRefreshToken()

    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/portal-auth/refresh')
    expect(localStorage.getItem('elder_token')).toBeNull()
    expect(localStorage.getItem('elder_refresh_token')).toBeNull()
  })

  it('logout calls the backend cookie-clear endpoint and clears the auth flag', async () => {
    localStorage.setItem('elder_authenticated', '1')
    mockAxiosInstance.post.mockResolvedValue({ data: { message: 'Logged out successfully' } })

    await api.logout()

    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/portal-auth/logout')
    expect(localStorage.getItem('elder_authenticated')).toBeNull()
  })

  it('logout still clears the auth flag even if the backend call fails', async () => {
    localStorage.setItem('elder_authenticated', '1')
    mockAxiosInstance.post.mockRejectedValue(new Error('network error'))

    await api.logout()

    expect(localStorage.getItem('elder_authenticated')).toBeNull()
  })

  it('isAuthenticated reflects only the non-sensitive flag, never a token', () => {
    expect(isAuthenticated()).toBe(false)
    localStorage.setItem('elder_authenticated', '1')
    expect(isAuthenticated()).toBe(true)
  })
})
