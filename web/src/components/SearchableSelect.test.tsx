import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import SearchableSelect from './SearchableSelect'

describe('SearchableSelect accessibility', () => {
  it('applies aria-label and combobox role to the input', () => {
    render(
      <SearchableSelect
        options={[{ value: 1, label: 'Option A' }]}
        onChange={vi.fn()}
        ariaLabel="Assignee"
      />
    )
    const input = screen.getByRole('combobox', { name: 'Assignee' })
    expect(input).toBeDefined()
  })
})
