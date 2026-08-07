import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import IntakePublicForm from './IntakePublicForm'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getPublicIntakeForm: vi.fn(),
    submitPublicIntakeForm: vi.fn(),
  },
}))

function renderPublicForm() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/intake/support-request']}>
        <Routes>
          <Route path="/intake/:slug" element={<IntakePublicForm />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('IntakePublicForm', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders dynamic fields and submits successfully', async () => {
    vi.mocked(api.getPublicIntakeForm).mockResolvedValue({
      name: 'Support Request',
      description: 'Tell us what is wrong',
      fields: [{ id: 'email', label: 'Email', type: 'email', required: true }],
      captcha_required: false,
    })
    vi.mocked(api.submitPublicIntakeForm).mockResolvedValue({ status: 'created', reference: 'abc-123' })

    renderPublicForm()
    await waitFor(() => screen.getByText('Support Request'))
    fireEvent.change(screen.getByLabelText('Email *'), { target: { value: 'a@b.com' } })
    fireEvent.click(screen.getByRole('button', { name: /submit/i }))
    await waitFor(() => screen.getByText('Thank you'))
    expect(api.submitPublicIntakeForm).toHaveBeenCalledWith('support-request', { fields: { email: 'a@b.com' } })
  })

  it('shows a not-available message when the form 404s', async () => {
    vi.mocked(api.getPublicIntakeForm).mockRejectedValue(new Error('not found'))
    renderPublicForm()
    await waitFor(() => screen.getByText('This form is not available.'))
  })

  it('renders select field with options and submits value', async () => {
    vi.mocked(api.getPublicIntakeForm).mockResolvedValue({
      name: 'Feedback Form',
      description: 'Give us feedback',
      fields: [
        { id: 'category', label: 'Category', type: 'select', required: true, options: ['Bug', 'Feature', 'Other'] },
      ],
      captcha_required: false,
    })
    vi.mocked(api.submitPublicIntakeForm).mockResolvedValue({ status: 'created', reference: 'xyz-789' })

    renderPublicForm()
    await waitFor(() => screen.getByText('Feedback Form'))
    const selectElement = screen.getByDisplayValue('Select an option') as HTMLSelectElement
    expect(selectElement).toBeTruthy()
    expect(selectElement.options.length).toBe(4) // Select an option + Bug + Feature + Other
    fireEvent.change(selectElement, { target: { value: 'Feature' } })
    fireEvent.click(screen.getByRole('button', { name: /submit/i }))
    await waitFor(() => screen.getByText('Thank you'))
    expect(api.submitPublicIntakeForm).toHaveBeenCalledWith('support-request', { fields: { category: 'Feature' } })
  })
})
