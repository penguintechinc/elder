import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  ReactFlow,
  Controls,
  Background,
  MiniMap,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  type Node,
  type Edge,
  type NodeProps,
  type OnNodesChange,
  type OnEdgesChange,
  type OnConnect,
  type NodeTypes,
  BackgroundVariant,
  Panel,
  Handle,
  Position,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Save, Trash2, ArrowLeft, Users } from 'lucide-react'
import api from '@/lib/api'
import { useDiagramCollaboration } from '@/hooks/useDiagramCollaboration'

const handleStyle = '!w-3 !h-3 !bg-amber-500 !border-2 !border-amber-600'

interface CloudProviderNodeData extends Record<string, unknown> {
  label: string
  provider: string
  color: string
}

function CloudProviderNode({ data, selected }: NodeProps<Node<CloudProviderNodeData>>) {
  return (
    <div
      className={`relative px-4 py-3 rounded-lg shadow-lg border-2 ${selected ? 'border-amber-400' : 'border-slate-600'}`}
      style={{ backgroundColor: data.color || '#1f2937' }}
    >
      <Handle type="target" position={Position.Top} id="top-target" className={handleStyle} />
      <Handle type="source" position={Position.Top} id="top-source" className={handleStyle} style={{ top: -6 }} />
      <Handle type="target" position={Position.Bottom} id="bottom-target" className={handleStyle} />
      <Handle type="source" position={Position.Bottom} id="bottom-source" className={handleStyle} style={{ bottom: -6 }} />
      <Handle type="target" position={Position.Left} id="left-target" className={handleStyle} />
      <Handle type="source" position={Position.Left} id="left-source" className={handleStyle} style={{ left: -6 }} />
      <Handle type="target" position={Position.Right} id="right-target" className={handleStyle} />
      <Handle type="source" position={Position.Right} id="right-source" className={handleStyle} style={{ right: -6 }} />
      <div className="flex flex-col items-center gap-2 text-white text-sm font-medium">{data.label}</div>
    </div>
  )
}

interface InfrastructureNodeData extends Record<string, unknown> {
  label: string
  type: string
  color: string
}

function InfrastructureNode({ data, selected }: NodeProps<Node<InfrastructureNodeData>>) {
  return (
    <div
      className={`relative px-4 py-3 rounded-lg shadow-lg border-2 ${selected ? 'border-amber-400' : 'border-slate-600'}`}
      style={{ backgroundColor: data.color || '#1f2937' }}
    >
      <Handle type="target" position={Position.Top} id="top-target" className={handleStyle} />
      <Handle type="source" position={Position.Top} id="top-source" className={handleStyle} style={{ top: -6 }} />
      <Handle type="target" position={Position.Bottom} id="bottom-target" className={handleStyle} />
      <Handle type="source" position={Position.Bottom} id="bottom-source" className={handleStyle} style={{ bottom: -6 }} />
      <Handle type="target" position={Position.Left} id="left-target" className={handleStyle} />
      <Handle type="source" position={Position.Left} id="left-source" className={handleStyle} style={{ left: -6 }} />
      <Handle type="target" position={Position.Right} id="right-target" className={handleStyle} />
      <Handle type="source" position={Position.Right} id="right-source" className={handleStyle} style={{ right: -6 }} />
      <div className="flex flex-col items-center gap-2 text-white text-sm font-medium">{data.label}</div>
    </div>
  )
}

interface ShapeNodeData extends Record<string, unknown> {
  label: string
  color: string
  shape: string
}

function ShapeNode({ data, selected }: NodeProps<Node<ShapeNodeData>>) {
  const shapeStyles: Record<string, string> = {
    rectangle: 'rounded-md',
    rounded: 'rounded-xl',
    circle: 'rounded-full',
    diamond: 'rotate-45',
  }

  return (
    <div className={`relative ${data.shape === 'circle' ? 'w-20 h-20' : 'px-4 py-3'}`}>
      <Handle type="target" position={Position.Top} id="top-target" className={handleStyle} />
      <Handle type="source" position={Position.Top} id="top-source" className={handleStyle} style={{ top: -6 }} />
      <Handle type="target" position={Position.Bottom} id="bottom-target" className={handleStyle} />
      <Handle type="source" position={Position.Bottom} id="bottom-source" className={handleStyle} style={{ bottom: -6 }} />
      <Handle type="target" position={Position.Left} id="left-target" className={handleStyle} />
      <Handle type="source" position={Position.Left} id="left-source" className={handleStyle} style={{ left: -6 }} />
      <Handle type="target" position={Position.Right} id="right-target" className={handleStyle} />
      <Handle type="source" position={Position.Right} id="right-source" className={handleStyle} style={{ right: -6 }} />
      <div
        className={`flex items-center justify-center shadow-lg border-2 ${selected ? 'border-amber-400' : 'border-transparent'} ${shapeStyles[data.shape] || shapeStyles.rectangle} ${data.shape === 'circle' ? 'w-full h-full' : 'px-4 py-2'}`}
        style={{ backgroundColor: data.color || '#374151' }}
      >
        <span className={`text-white text-sm font-medium ${data.shape === 'diamond' ? '-rotate-45 block' : ''}`}>
          {data.label}
        </span>
      </div>
    </div>
  )
}

interface TextNodeData extends Record<string, unknown> {
  label: string
  fontSize: string
}

function TextNode({ data, selected }: NodeProps<Node<TextNodeData>>) {
  return (
    <div className={`px-2 py-1 ${selected ? 'ring-2 ring-amber-400' : ''}`}>
      <span className={`text-white font-medium ${data.fontSize || 'text-sm'}`}>{data.label}</span>
    </div>
  )
}

const nodeTypes: NodeTypes = {
  cloudProvider: CloudProviderNode,
  infrastructure: InfrastructureNode,
  shape: ShapeNode,
  text: TextNode,
}

const shapeOptions = [
  { type: 'rectangle', label: 'Rectangle' },
  { type: 'rounded', label: 'Rounded' },
  { type: 'circle', label: 'Circle' },
  { type: 'diamond', label: 'Diamond' },
]

interface RemoteCursor {
  identity_id: string
  x: number
  y: number
}

function RemoteCursors({ cursors }: { cursors: RemoteCursor[] }) {
  const colors = ['#fbbf24', '#60a5fa', '#34d399', '#f87171', '#c084fc']

  return (
    <div className="pointer-events-none fixed inset-0">
      {cursors.map((cursor, idx) => {
        const color = colors[idx % colors.length]
        return (
          <div
            key={cursor.identity_id}
            className="absolute w-4 h-6 pointer-events-none"
            style={{
              left: `${cursor.x}px`,
              top: `${cursor.y}px`,
              transform: 'translate(-4px, -2px)',
            }}
          >
            <svg className="w-4 h-6" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path
                d="M5.5 1.5l12 18-4-6.5 6.5-4-12-6.5z"
                fill={color}
                stroke="currentColor"
                strokeWidth="1"
                strokeLinejoin="round"
              />
            </svg>
            <div className="absolute left-4 top-4 bg-slate-900 text-white text-xs px-1 py-0.5 rounded whitespace-nowrap" style={{ color }}>
              {cursor.identity_id.slice(0, 8)}
            </div>
          </div>
        )
      })}
    </div>
  )
}

const colorOptions = [
  { color: '#3B82F6', label: 'Blue' },
  { color: '#10B981', label: 'Green' },
  { color: '#F59E0B', label: 'Amber' },
  { color: '#EF4444', label: 'Red' },
  { color: '#8B5CF6', label: 'Purple' },
  { color: '#EC4899', label: 'Pink' },
  { color: '#6B7280', label: 'Gray' },
  { color: '#1F2937', label: 'Dark' },
]

function DiagramEditorContent() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [selectedColor, setSelectedColor] = useState('#3B82F6')
  const nodeIdCounter = useRef(1)
  const drawingChangeTimeoutRef = useRef<number | null>(null)

  const { data: diagram, isLoading, error } = useQuery({
    queryKey: ['diagram', id],
    queryFn: () => api.getDiagram(Number(id)),
    enabled: !!id && id !== 'new',
  })

  // Handle remote drawing changes
  const handleRemoteChange = useCallback((remoteNodes: Node[], remoteEdges: Edge[]) => {
    setNodes(remoteNodes)
    setEdges(remoteEdges)
  }, [])

  // Collaboration hook
  const { collaborators, sendCursor, sendDrawingChange } = useDiagramCollaboration(Number(id) || 0, {
    enabled: !!id && id !== 'new',
    onRemoteChange: handleRemoteChange,
  })

  const saveMutation = useMutation({
    mutationFn: () =>
      api.saveDiagramVersion(Number(id), {
        content: { nodes, edges },
        change_summary: 'Canvas update',
      }),
    onSuccess: () => {
      console.log('[DiagramEditor] Version saved successfully')
    },
  })

  // Load diagram content on mount
  useEffect(() => {
    if (diagram?.content) {
      const content = diagram.content as { nodes?: Node[]; edges?: Edge[] }
      if (content.nodes) setNodes(content.nodes)
      if (content.edges) setEdges(content.edges)
      nodeIdCounter.current = (content.nodes?.length || 0) + 1
    } else if (!diagram && !isLoading) {
      // Initialize empty diagram for new diagrams
      setNodes([])
      setEdges([])
    }
  }, [diagram, isLoading])

  const onNodesChange: OnNodesChange = useCallback((changes) => {
    setNodes((nds) => {
      const updated = applyNodeChanges(changes, nds)
      // Debounce drawing change broadcast
      if (drawingChangeTimeoutRef.current) clearTimeout(drawingChangeTimeoutRef.current)
      drawingChangeTimeoutRef.current = window.setTimeout(() => {
        sendDrawingChange(updated, edges)
      }, 300)
      return updated
    })
  }, [edges, sendDrawingChange])

  const onEdgesChange: OnEdgesChange = useCallback((changes) => {
    setEdges((eds) => {
      const updated = applyEdgeChanges(changes, eds)
      // Debounce drawing change broadcast
      if (drawingChangeTimeoutRef.current) clearTimeout(drawingChangeTimeoutRef.current)
      drawingChangeTimeoutRef.current = window.setTimeout(() => {
        sendDrawingChange(nodes, updated)
      }, 300)
      return updated
    })
  }, [nodes, sendDrawingChange])
  const onConnect: OnConnect = useCallback(
    (connection) =>
      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            animated: true,
            style: { stroke: '#F59E0B', strokeWidth: 2 },
            markerEnd: { type: MarkerType.ArrowClosed, color: '#F59E0B' },
            interactionWidth: 20,
          },
          eds
        )
      ),
    []
  )

  const addIconNode = useCallback((label: string, type: string) => {
    const newNode: Node = {
      id: String(nodeIdCounter.current++),
      type: 'infrastructure',
      position: { x: Math.random() * 400 + 100, y: Math.random() * 300 + 50 },
      data: { label, type, color: selectedColor },
    }
    setNodes((nds) => [...nds, newNode])
  }, [selectedColor])

  const addShapeNode = useCallback((shape: string) => {
    const newNode: Node = {
      id: String(nodeIdCounter.current++),
      type: 'shape',
      position: { x: Math.random() * 400 + 100, y: Math.random() * 300 + 50 },
      data: { label: `Shape ${nodeIdCounter.current - 1}`, shape, color: selectedColor },
    }
    setNodes((nds) => [...nds, newNode])
  }, [selectedColor])

  const deleteSelectedNodes = useCallback(() => {
    setNodes((nds) => nds.filter((n) => !n.selected))
  }, [])

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-amber-400">Loading diagram...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-red-400">Failed to load diagram. Please try again.</div>
      </div>
    )
  }

  return (
    <div className="h-screen bg-slate-900 flex flex-col relative">
      <RemoteCursors cursors={collaborators} />
      {/* Toolbar */}
      <div className="bg-slate-800 border-b border-slate-700 p-4 flex items-center justify-between gap-4 relative z-10">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/diagrams')}
            className="flex items-center gap-2 text-amber-400 hover:text-amber-300 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
            Back
          </button>
          <div className="border-l border-slate-600" />
          <h1 className="text-xl font-bold text-amber-400">{diagram?.title || 'New Diagram'}</h1>
        </div>

        <div className="flex items-center gap-4">
          {/* Collaboration status */}
          <div className="flex items-center gap-2 text-sm px-3 py-1 bg-slate-700 rounded">
            <Users className="w-4 h-4 text-amber-400" />
            <span className="text-slate-200">{collaborators.length + 1}</span>
          </div>

          {/* Node palette */}
          <div className="flex gap-2 border-r border-slate-600 pr-4">
            <button
              onClick={() => addIconNode('Server', 'server')}
              className="bg-slate-700 hover:bg-slate-600 text-slate-200 px-3 py-1 rounded text-sm transition-colors"
            >
              Server
            </button>
            <button
              onClick={() => addIconNode('Database', 'database')}
              className="bg-slate-700 hover:bg-slate-600 text-slate-200 px-3 py-1 rounded text-sm transition-colors"
            >
              DB
            </button>
          </div>

          {/* Shape palette */}
          <div className="flex gap-2 border-r border-slate-600 pr-4">
            {shapeOptions.map((shape) => (
              <button
                key={shape.type}
                onClick={() => addShapeNode(shape.type)}
                className="bg-slate-700 hover:bg-slate-600 text-slate-200 px-3 py-1 rounded text-sm transition-colors"
              >
                {shape.label}
              </button>
            ))}
          </div>

          {/* Color picker */}
          <div className="flex gap-2 border-r border-slate-600 pr-4">
            {colorOptions.map((option) => (
              <button
                key={option.color}
                onClick={() => setSelectedColor(option.color)}
                className={`w-6 h-6 rounded border-2 transition-all ${selectedColor === option.color ? 'border-amber-400 ring-2 ring-amber-300' : 'border-slate-600'}`}
                style={{ backgroundColor: option.color }}
                aria-label={option.label}
              />
            ))}
          </div>

          {/* Save button */}
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-4 py-2 rounded font-semibold transition-colors"
          >
            <Save className="w-4 h-4" />
            {saveMutation.isPending ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {/* Canvas */}
      <div
        className="flex-1 relative"
        onMouseMove={(e) => {
          // Send cursor position in screen coordinates
          sendCursor(e.clientX, e.clientY)
        }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          fitView
        >
          <Background variant={BackgroundVariant.Dots} color="#475569" gap={12} />
          <Controls />
          <MiniMap />
          <Panel position="bottom-left" className="bg-slate-800 border border-slate-700 rounded-lg p-3">
            <div className="flex gap-2">
              <button
                onClick={deleteSelectedNodes}
                className="flex items-center gap-2 bg-red-900 hover:bg-red-800 text-red-200 px-3 py-1 rounded text-sm transition-colors"
              >
                <Trash2 className="w-4 h-4" />
                Delete
              </button>
            </div>
          </Panel>
        </ReactFlow>
      </div>
    </div>
  )
}

export default function DiagramEditor() {
  return <DiagramEditorContent />
}
