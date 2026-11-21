import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Organizations from './Organizations'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getOrganizations: vi.fn().mockResolvedValue({ items: [] }),
    createOrganization: vi.fn(),
    deleteOrganization: vi.fn(),
    updateOrganization: vi.fn(),
  },
}))

interface MockFieldOption {
  value: string
  label: string
}

interface MockField {
  name: string
  label: string
  type: string
  defaultValue?: string
  options?: MockFieldOption[]
}

interface MockFormModalBuilderProps {
  title: string
  fields: MockField[]
  isOpen: boolean
  onClose: () => void
  onSubmit: (data: Record<string, unknown>) => void
  submitButtonText: string
}

vi.mock('@penguintechinc/react-libs/components', () => ({
  FormModalBuilder: ({ title, fields, isOpen, onClose, onSubmit, submitButtonText }: MockFormModalBuilderProps) => {
    if (!isOpen) return null
    return (
      <div data-testid="form-modal">
        <h2>{title}</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            const formData: Record<string, unknown> = {}
            fields.forEach((field: MockField) => {
              const input = document.querySelector(
                `[name="${field.name}"]`
              ) as HTMLInputElement | HTMLSelectElement
              if (input) {
                formData[field.name] = input.value
              }
            })
            onSubmit(formData)
          }}
        >
          {fields.map((field: MockField) => (
            <div key={field.name}>
              <label htmlFor={field.name}>{field.label}</label>
              {field.type === 'select' ? (
                <select id={field.name} name={field.name} defaultValue={field.defaultValue}>
                  {field.options?.map((opt: MockFieldOption) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              ) : field.type === 'textarea' ? (
                <textarea id={field.name} name={field.name} defaultValue={field.defaultValue} />
              ) : (
                <input
                  id={field.name}
                  type={field.type}
                  name={field.name}
                  defaultValue={field.defaultValue}
                />
              )}
            </div>
          ))}
          <button type="submit">{submitButtonText}</button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </form>
      </div>
    )
  },
  FormField: {},
}))

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Organizations />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('Organizations create form', () => {
  beforeEach(() => vi.clearAllMocks())

  it('offers Customer Company as an organization type', async () => {
    renderPage()
    fireEvent.click(screen.getByText('Create Organization Unit'))
    await waitFor(() => screen.getByText('Customer Company'))
  })

  it('submits organization_type along with name/description', async () => {
    vi.mocked(api.createOrganization).mockResolvedValue({ id: 1 })
    renderPage()
    fireEvent.click(screen.getByText('Create Organization Unit'))
    await waitFor(() => screen.getByLabelText('Name'))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Acme Support' } })
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'customer_company' } })
    fireEvent.click(screen.getByText('Create'))
    await waitFor(() => expect(api.createOrganization).toHaveBeenCalled())
    expect(vi.mocked(api.createOrganization).mock.calls[0][0]).toMatchObject({
      name: 'Acme Support',
      organization_type: 'customer_company',
    })
  })
})
