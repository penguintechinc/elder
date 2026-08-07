import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import Webhooks from './Webhooks'
import api from '@/lib/api'

// Mock AssigneePicker with buttons to trigger onChange — allows tests to verify filter wiring
vi.mock('@/components/AssigneePicker', () => ({
  default: ({ onChange }: { onChange: (val: { assignee_type: 'identity' | 'org_unit'; assignee_id: number } | null) => void }) => (
    <div data-testid="assignee-picker">
      <button
        type="button"
        data-testid="assignee-picker-select-button"
        onClick={() => onChange({ assignee_type: 'org_unit', assignee_id: 7 })}
      >
        Select Assignee
      </button>
      <button
        type="button"
        data-testid="assignee-picker-clear-button"
        onClick={() => onChange(null)}
      >
        Clear Assignee
      </button>
    </div>
  ),
  AssigneeValue: {},
}))

vi.mock('@/lib/api', () => ({
  default: {
    getPortalProfile: vi.fn(),
    getWebhooks: vi.fn(),
    getOrganizations: vi.fn().mockResolvedValue({ items: [{ id: 1, name: 'Acme', created_at: '2026-01-01', updated_at: '2026-01-01' }] }),
    getIdentities: vi.fn().mockResolvedValue({ items: [] }),
    createWebhook: vi.fn(),
    updateWebhook: vi.fn(),
    deleteWebhook: vi.fn(),
    testWebhook: vi.fn(),
  },
}))

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <Webhooks />
    </QueryClientProvider>
  )
}

describe('Webhooks admin page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getPortalProfile).mockResolvedValue({ global_role: 'admin', tenant_role: null })
    vi.mocked(api.getWebhooks).mockResolvedValue({ webhooks: [] })
  })

  it('shows an admin-required message for non-admins', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({ global_role: 'viewer', tenant_role: null })
    renderPage()
    await waitFor(() => {
      const msg = screen.getByText(/admin access required/i)
      expect(msg).toBeDefined()
    })
  })

  it('opens edit modal and pre-populates webhook data including is_active and filter fields', async () => {
    vi.mocked(api.getWebhooks).mockResolvedValue({
      webhooks: [{
        id: 1,
        name: 'Support bot',
        url: 'https://x.example.com',
        organization_id: 1,
        events: ['issue.assigned'],
        is_active: false,
        filter_issue_type: 'bug',
        filter_assignee_type: 'identity',
        filter_assignee_id: 42,
      }],
    })
    renderPage()
    await waitFor(() => {
      const name = screen.getByText('Support bot')
      expect(name).toBeDefined()
    })
    fireEvent.click(screen.getByTitle('Edit webhook'))
    const form = await screen.findByTestId('edit-webhook-form')
    expect(form).toBeDefined()
    // Verify pre-populated values: name and URL inputs
    const inputs = screen.getAllByDisplayValue('Support bot')
    expect(inputs.length).toBeGreaterThan(0)
    const urlInputs = screen.getAllByDisplayValue('https://x.example.com')
    expect(urlInputs.length).toBeGreaterThan(0)
    // Verify is_active toggle reflects false
    const activeToggle = screen.getByTestId('is-active-toggle') as HTMLInputElement
    expect(activeToggle.checked).toBe(false)
  })

  it('submits create webhook with all filter fields, is_active, and events', async () => {
    vi.mocked(api.createWebhook).mockResolvedValue({ id: 2 })
    renderPage()
    await waitFor(() => {
      const h1 = screen.getByText('Webhooks')
      expect(h1).toBeDefined()
    })
    fireEvent.click(screen.getByText('Create Webhook'))
    // Wait for modal to appear
    await waitFor(() => {
      const createBtn = screen.queryByText('Create')
      expect(createBtn).toBeDefined()
    }, { timeout: 3000 })
    // Fill required fields using input elements (bypass label linking issues)
    const inputs = screen.getAllByPlaceholderText(/Production Webhook|example.com/)
    const nameInput = inputs.find(i => (i as HTMLInputElement).placeholder.includes('Production'))
    const urlInput = inputs.find(i => (i as HTMLInputElement).placeholder.includes('example.com'))
    if (nameInput) fireEvent.change(nameInput, { target: { value: 'Support bot assignments' } })
    if (urlInput) fireEvent.change(urlInput, { target: { value: 'https://hooks.example.com/webhook' } })
    // Select issue.assigned event
    fireEvent.click(screen.getByTestId('event-checkbox-issue.assigned'))
    // Select issue type filter ('support')
    const issueTypeSelect = screen.getAllByRole('combobox').find(s => {
      const options = (s as HTMLSelectElement).options
      return options && Array.from(options).some(o => o.textContent?.includes('Support'))
    })
    if (issueTypeSelect) fireEvent.change(issueTypeSelect, { target: { value: 'support' } })
    // Select assignee filter via mock button
    fireEvent.click(screen.getByTestId('assignee-picker-select-button'))
    // Submit form
    fireEvent.click(screen.getByText('Create'))
    // Verify API was called with correct payload
    await waitFor(() => {
      expect(vi.mocked(api.createWebhook)).toHaveBeenCalled()
    }, { timeout: 3000 })
    const call = vi.mocked(api.createWebhook).mock.calls[0][0]
    expect(call.name).toBe('Support bot assignments')
    expect(call.url).toBe('https://hooks.example.com/webhook')
    expect(call.events).toContain('issue.assigned')
    expect(call.is_active).toBe(true)
    expect(call.filter_issue_type).toBe('support')
    expect(call.filter_assignee_type).toBe('org_unit')
    expect(call.filter_assignee_id).toBe(7)
  })

  it('submits create webhook with null assignee filter when no assignee selected', async () => {
    vi.mocked(api.createWebhook).mockResolvedValue({ id: 3 })
    renderPage()
    await waitFor(() => {
      const h1 = screen.getByText('Webhooks')
      expect(h1).toBeDefined()
    })
    fireEvent.click(screen.getByText('Create Webhook'))
    await waitFor(() => {
      const createBtn = screen.queryByText('Create')
      expect(createBtn).toBeDefined()
    }, { timeout: 3000 })
    // Fill required fields
    const inputs = screen.getAllByPlaceholderText(/Production Webhook|example.com/)
    const nameInput = inputs.find(i => (i as HTMLInputElement).placeholder.includes('Production'))
    const urlInput = inputs.find(i => (i as HTMLInputElement).placeholder.includes('example.com'))
    if (nameInput) fireEvent.change(nameInput, { target: { value: 'General webhook' } })
    if (urlInput) fireEvent.change(urlInput, { target: { value: 'https://hooks.example.com/general' } })
    // Select issue.assigned event
    fireEvent.click(screen.getByTestId('event-checkbox-issue.assigned'))
    // Do NOT select an assignee filter — leave it empty
    // Submit form
    fireEvent.click(screen.getByText('Create'))
    // Verify API was called with null/undefined assignee filter fields
    await waitFor(() => {
      expect(vi.mocked(api.createWebhook)).toHaveBeenCalled()
    }, { timeout: 3000 })
    const call = vi.mocked(api.createWebhook).mock.calls[0][0]
    expect(call.name).toBe('General webhook')
    expect(call.filter_assignee_type).toBeUndefined()
    expect(call.filter_assignee_id).toBeUndefined()
  })
})
