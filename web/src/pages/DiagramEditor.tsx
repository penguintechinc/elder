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
import { Save, Trash2, ArrowLeft } from 'lucide-react'
import api from '@/lib/api'

const handleStyle = '!w-3 !h-3 !bg-amber-500 !border-2 !border-amber-600'

function CloudProviderNode({ data, selected }: { data: { label: string; provider: string; color: string }; selected: boolean }) {
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

function InfrastructureNode({ data, selected }: { data: { label: string; type: string; color: string }; selected: boolean }) {
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

function ShapeNode({ data, selected }: { data: { label: string; color: string; shape: string }; selected: boolean }) {
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

function TextNode({ data, selected }: { data: { label: string; fontSize: string }; selected: boolean }) {
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

export default function DiagramEditor() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [selectedColor, setSelectedColor] = useState('#3B82F6')
  const nodeIdCounter = useRef(1)

  const { data: diagram, isLoading, error } = useQuery({
    queryKey: ['diagram', id],
    queryFn: () => api.getDiagram(Number(id)),
    enabled: !!id && id !== 'new',
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

  const onNodesChange: OnNodesChange = useCallback((changes) => setNodes((nds) => applyNodeChanges(changes, nds)), [])
  const onEdgesChange: OnEdgesChange = useCallback((changes) => setEdges((eds) => applyEdgeChanges(changes, eds)), [])
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
    <div className="h-screen bg-slate-900 flex flex-col">
      {/* Toolbar */}
      <div className="bg-slate-800 border-b border-slate-700 p-4 flex items-center justify-between gap-4">
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
      <div className="flex-1 relative">
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
