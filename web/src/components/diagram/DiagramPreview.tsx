import { useId, useMemo } from 'react'

export interface DiagramPreviewShape {
  id: string
  x: number
  y: number
  width: number
  height: number
  shape_type?: string
  label?: string
  color?: string | null
}

export interface DiagramPreviewConnector {
  id: string
  source: string
  target: string
  connector_type?: string
  color?: string | null
}

export interface DiagramPreviewData {
  shapes: DiagramPreviewShape[]
  connectors: DiagramPreviewConnector[]
}

interface DiagramPreviewProps {
  title: string
  thumbnailUrl?: string | null
  preview?: DiagramPreviewData | null
}

const DEFAULT_SHAPE_FILL = '#1e293b'
const DEFAULT_SHAPE_STROKE = '#64748b'
const DEFAULT_CONNECTOR_STROKE = '#f59e0b'
const ASPECT_RATIO = 16 / 9

function computeEdgeEndpoints(
  src: DiagramPreviewShape,
  dst: DiagramPreviewShape,
): { x1: number; y1: number; x2: number; y2: number } {
  const sx = src.x + src.width / 2
  const sy = src.y + src.height / 2
  const tx = dst.x + dst.width / 2
  const ty = dst.y + dst.height / 2

  const dx = tx - sx
  const dy = ty - sy
  const dist = Math.hypot(dx, dy)
  if (dist < 1) {
    return { x1: sx, y1: sy, x2: tx, y2: ty }
  }

  // Intersect ray from source center to target center with target box boundary
  const halfW = dst.width / 2 + 4
  const halfH = dst.height / 2 + 4
  const scaleX = Math.abs(dx) > 0.001 ? halfW / Math.abs(dx) : Number.POSITIVE_INFINITY
  const scaleY = Math.abs(dy) > 0.001 ? halfH / Math.abs(dy) : Number.POSITIVE_INFINITY
  const t = Math.min(scaleX, scaleY, 0.9)

  return {
    x1: sx,
    y1: sy,
    x2: tx - dx * t,
    y2: ty - dy * t,
  }
}

function truncateLabel(label: string, width: number): string {
  const maxChars = Math.max(6, Math.floor(width / 8))
  if (label.length <= maxChars) return label
  return `${label.slice(0, maxChars - 1).trimEnd()}…`
}

export default function DiagramPreview({
  title,
  thumbnailUrl,
  preview,
}: DiagramPreviewProps) {
  const uid = useId().replace(/:/g, '')
  const gridPatternId = `dg-grid-${uid}`
  const arrowMarkerId = `dg-arrow-${uid}`

  const shapes = preview?.shapes ?? []
  const connectors = preview?.connectors ?? []

  const layout = useMemo(() => {
    if (shapes.length === 0) return null

    let minX = Number.POSITIVE_INFINITY
    let minY = Number.POSITIVE_INFINITY
    let maxX = Number.NEGATIVE_INFINITY
    let maxY = Number.NEGATIVE_INFINITY

    const shapeMap = new Map<string, DiagramPreviewShape>()

    for (const s of shapes) {
      shapeMap.set(s.id, s)
      if (s.x < minX) minX = s.x
      if (s.y < minY) minY = s.y
      if (s.x + s.width > maxX) maxX = s.x + s.width
      if (s.y + s.height > maxY) maxY = s.y + s.height
    }

    const contentW = Math.max(maxX - minX, 1)
    const contentH = Math.max(maxY - minY, 1)
    const pad = Math.max(28, Math.max(contentW, contentH) * 0.14)

    const rawW = Math.max(contentW + pad * 2, 320)
    const rawH = Math.max(contentH + pad * 2, 180)

    let vbW = rawW
    let vbH = rawH
    if (rawW / rawH < ASPECT_RATIO) {
      vbW = rawH * ASPECT_RATIO
    } else {
      vbH = rawW / ASPECT_RATIO
    }

    const centerX = (minX + maxX) / 2
    const centerY = (minY + maxY) / 2
    const vbX = centerX - vbW / 2
    const vbY = centerY - vbH / 2
    const gridStep = Math.max(24, Math.round(Math.max(vbW, vbH) / 18))
    const strokeWidth = Math.max(1.5, Math.min(3.5, vbW / 240))

    return {
      viewBox: `${vbX.toFixed(1)} ${vbY.toFixed(1)} ${vbW.toFixed(1)} ${vbH.toFixed(1)}`,
      vbX,
      vbY,
      vbW,
      vbH,
      gridStep,
      strokeWidth,
      shapeMap,
    }
  }, [shapes])

  if (thumbnailUrl) {
    return (
      <img
        src={thumbnailUrl}
        alt={title}
        className="w-full h-full object-cover"
      />
    )
  }

  if (!layout || shapes.length === 0) {
    return (
      <div className="text-slate-500 text-sm select-none">Empty diagram</div>
    )
  }

  return (
    <svg
      viewBox={layout.viewBox}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={`Preview of ${title}`}
      className="w-full h-full bg-slate-900 block"
      data-testid="diagram-preview-svg"
    >
      <defs>
        <pattern
          id={gridPatternId}
          width={layout.gridStep}
          height={layout.gridStep}
          patternUnits="userSpaceOnUse"
        >
          <circle
            cx={layout.gridStep / 2}
            cy={layout.gridStep / 2}
            r={Math.max(1, layout.strokeWidth * 0.55)}
            fill="#334155"
            opacity={0.55}
          />
        </pattern>
        <marker
          id={arrowMarkerId}
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M 0 1 L 10 5 L 0 9 z" fill={DEFAULT_CONNECTOR_STROKE} />
        </marker>
      </defs>

      {/* Background dot grid */}
      <rect
        x={layout.vbX}
        y={layout.vbY}
        width={layout.vbW}
        height={layout.vbH}
        fill={`url(#${gridPatternId})`}
      />

      {/* Connectors */}
      <g data-testid="diagram-preview-connectors">
        {connectors.map((conn) => {
          const src = layout.shapeMap.get(conn.source)
          const dst = layout.shapeMap.get(conn.target)
          if (!src || !dst) return null

          const { x1, y1, x2, y2 } = computeEdgeEndpoints(src, dst)
          const stroke = conn.color || DEFAULT_CONNECTOR_STROKE
          const isArrow = conn.connector_type !== 'line'

          return (
            <line
              key={conn.id}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke={stroke}
              strokeWidth={layout.strokeWidth}
              strokeOpacity={0.85}
              markerEnd={isArrow ? `url(#${arrowMarkerId})` : undefined}
            />
          )
        })}
      </g>

      {/* Shapes */}
      <g data-testid="diagram-preview-shapes">
        {shapes.map((shape) => {
          const cx = shape.x + shape.width / 2
          const cy = shape.y + shape.height / 2
          const isTransparent = shape.color === 'transparent'
          const fill = isTransparent
            ? 'none'
            : shape.color || DEFAULT_SHAPE_FILL
          const stroke = shape.color && !isTransparent
            ? 'rgba(255, 255, 255, 0.22)'
            : DEFAULT_SHAPE_STROKE
          const shapeType = shape.shape_type || 'rounded'
          const fontSize = Math.max(10, Math.min(14, shape.height * 0.24))
          const displayLabel = shape.label
            ? truncateLabel(shape.label, shape.width)
            : ''

          let geometry = null
          if (shapeType === 'circle' || shapeType === 'ellipse') {
            geometry = (
              <ellipse
                cx={cx}
                cy={cy}
                rx={shape.width / 2}
                ry={shape.height / 2}
                fill={fill}
                stroke={stroke}
                strokeWidth={layout.strokeWidth}
              />
            )
          } else if (shapeType === 'diamond') {
            const points = `${cx},${shape.y} ${shape.x + shape.width},${cy} ${cx},${shape.y + shape.height} ${shape.x},${cy}`
            geometry = (
              <polygon
                points={points}
                fill={fill}
                stroke={stroke}
                strokeWidth={layout.strokeWidth}
              />
            )
          } else if (shapeType !== 'text') {
            geometry = (
              <rect
                x={shape.x}
                y={shape.y}
                width={shape.width}
                height={shape.height}
                rx={shapeType === 'rectangle' ? 4 : 8}
                fill={fill}
                stroke={stroke}
                strokeWidth={layout.strokeWidth}
              />
            )
          }

          return (
            <g key={shape.id}>
              {geometry}
              {displayLabel && (
                <text
                  x={cx}
                  y={cy}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fill={shapeType === 'text' ? '#fbbf24' : '#f1f5f9'}
                  fontSize={fontSize}
                  fontWeight={500}
                  fontFamily="ui-sans-serif, system-ui, sans-serif"
                >
                  {displayLabel}
                </text>
              )}
            </g>
          )
        })}
      </g>
    </svg>
  )
}
