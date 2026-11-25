import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import DiagramPreview from './DiagramPreview'

describe('DiagramPreview', () => {
  it('renders an SVG mini-map with shapes, labels, and connectors', () => {
    const { container } = render(
      <DiagramPreview
        title="Orders Architecture"
        preview={{
          shapes: [
            {
              id: 's1',
              x: 80,
              y: 120,
              width: 160,
              height: 70,
              shape_type: 'rectangle',
              label: 'Storefront',
              color: '#1e40af',
            },
            {
              id: 's2',
              x: 300,
              y: 120,
              width: 110,
              height: 110,
              shape_type: 'diamond',
              label: 'Router',
              color: '#b45309',
            },
            {
              id: 's3',
              x: 480,
              y: 120,
              width: 80,
              height: 80,
              shape_type: 'circle',
              label: 'Cache',
              color: '#0f766e',
            },
          ],
          connectors: [
            {
              id: 'c1',
              source: 's1',
              target: 's2',
              connector_type: 'arrow',
              color: '#f59e0b',
            },
            {
              id: 'c2',
              source: 's2',
              target: 's3',
              connector_type: 'line',
            },
          ],
        }}
      />,
    )

    const svg = screen.getByRole('img', { name: 'Preview of Orders Architecture' })
    expect(svg).toBeTruthy()
    expect(svg.getAttribute('viewBox')).toBeTruthy()

    const shapesGroup = screen.getByTestId('diagram-preview-shapes')
    expect(shapesGroup.querySelectorAll('rect').length).toBe(1)
    expect(shapesGroup.querySelectorAll('polygon').length).toBe(1)
    expect(shapesGroup.querySelectorAll('ellipse').length).toBe(1)

    const connectorsGroup = screen.getByTestId('diagram-preview-connectors')
    const lines = connectorsGroup.querySelectorAll('line')
    expect(lines.length).toBe(2)
    expect(lines[0].getAttribute('marker-end')).toMatch(/^url\(#dg-arrow-/)
    expect(lines[1].getAttribute('marker-end')).toBeNull()

    expect(screen.getByText('Storefront')).toBeTruthy()
    expect(screen.getByText('Router')).toBeTruthy()
    expect(screen.getByText('Cache')).toBeTruthy()
    expect(container.textContent).not.toContain('Empty diagram')
  })

  it('falls back to "Empty diagram" when there are 0 shapes', () => {
    render(
      <DiagramPreview
        title="Blank Diagram"
        preview={{ shapes: [], connectors: [] }}
      />,
    )

    expect(screen.getByText('Empty diagram')).toBeTruthy()
    expect(screen.queryByTestId('diagram-preview-svg')).toBeNull()
  })

  it('falls back to "Empty diagram" when preview is undefined', () => {
    render(<DiagramPreview title="Legacy Diagram" />)
    expect(screen.getByText('Empty diagram')).toBeTruthy()
  })

  it('renders an explicit thumbnail_url image when provided', () => {
    render(
      <DiagramPreview
        title="Custom Thumb"
        thumbnailUrl="https://cdn.example.com/thumb.png"
        preview={{
          shapes: [
            { id: '1', x: 0, y: 0, width: 100, height: 50, label: 'Node' },
          ],
          connectors: [],
        }}
      />,
    )

    const img = screen.getByRole('img', { name: 'Custom Thumb' })
    expect(img.tagName).toBe('IMG')
    expect(img.getAttribute('src')).toBe('https://cdn.example.com/thumb.png')
    expect(screen.queryByTestId('diagram-preview-svg')).toBeNull()
  })

  it('renders untrusted label strings safely as text without injecting HTML', () => {
    const { container } = render(
      <DiagramPreview
        title="XSS Check"
        preview={{
          shapes: [
            {
              id: '1',
              x: 0,
              y: 0,
              width: 240,
              height: 60,
              label: '<script>alert(1)</script>',
            },
          ],
          connectors: [],
        }}
      />,
    )

    expect(container.querySelector('script')).toBeNull()
    expect(screen.getByText('<script>alert(1)</script>')).toBeTruthy()
  })
})
