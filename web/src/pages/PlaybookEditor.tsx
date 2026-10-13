import { useState, useCallback, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ReactFlow,
  Controls,
  Background,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  type Node,
  type Edge,
  type NodeProps,
  type OnNodesChange,
  type OnEdgesChange,
  type OnConnect,
  BackgroundVariant,
  Handle,
  Position,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Save, Play, ArrowLeft, Trash2 } from 'lucide-react'
import api from '@/lib/api'

interface StreamNode extends Node {
  data: { label: string; nodeType: string; config?: Record<string, unknown> }
}

const NODE_TYPES = {
  actions: [
    { type: 'action_log', label: 'Log' },
    { type: 'action_http_request', label: 'HTTP Request' },
    { type: 'action_webhook_out', label: 'Webhook Out' },
  ],
  conditionals: [
    { type: 'conditional_if_then', label: 'If-Then' },
    { type: 'conditional_switch', label: 'Switch' },
    { type: 'conditional_and', label: 'AND' },
    { type: 'conditional_or', label: 'OR' },
    { type: 'conditional_not', label: 'NOT' },
    { type: 'conditional_equals', label: 'Equals' },
    { type: 'conditional_greater_than', label: 'Greater Than' },
    { type: 'conditional_less_than', label: 'Less Than' },
    { type: 'conditional_contains', label: 'Contains' },
    { type: 'conditional_regex', label: 'Regex' },
    { type: 'conditional_for_each', label: 'For Each' },
    { type: 'conditional_while', label: 'While Loop' },
  ],
  transforms: [
    { type: 'transform_delay', label: 'Delay' },
    { type: 'transform_expression', label: 'Expression' },
    { type: 'transform_filter', label: 'Filter' },
    { type: 'transform_json', label: 'JSON Transform' },
    { type: 'transform_merge', label: 'Merge' },
    { type: 'transform_split', label: 'Split' },
  ],
}

function StreamNodeComponent({ data, selected }: NodeProps<StreamNode>) {
  const bgColor = selected ? 'bg-amber-600' : 'bg-slate-700'
  return (
    <div className={`${bgColor} px-4 py-3 rounded-lg border-2 ${selected ? 'border-amber-400' : 'border-slate-600'}`}>
      <Handle type="target" position={Position.Top} className="!w-3 !h-3 !bg-amber-500" />
      <div className="text-white text-sm font-medium">{data.label}</div>
      <Handle type="source" position={Position.Bottom} className="!w-3 !h-3 !bg-amber-500" />
    </div>
  )
}

const nodeTypes = { stream: StreamNodeComponent }

export default function PlaybookEditor() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [nodes, setNodes] = useState<StreamNode[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [selectedNode, setSelectedNode] = useState<StreamNode | null>(null)
  const [nodeConfig, setNodeConfig] = useState<Record<string, unknown>>({})

  const { data: streamData, isLoading, error } = useQuery({
    queryKey: ['stream', id],
    queryFn: () => id ? api.getStream(Number(id)) : null,
    enabled: !!id,
  })

  useEffect(() => {
    if (streamData?.nodes) setNodes(streamData.nodes)
    if (streamData?.edges) setEdges(streamData.edges)
  }, [streamData])

  const saveMutation = useMutation({
    mutationFn: () =>
      id ? api.updateStream(Number(id), { description: '' }) : Promise.resolve(null),
    onSuccess: () => {
      console.log('[PlaybookEditor] Saved playbook', { id })
      queryClient.invalidateQueries({ queryKey: ['stream', id] })
    },
  })

  const executeMutation = useMutation({
    mutationFn: () =>
      id ? api.executeStream(Number(id)) : Promise.resolve(null),
    onSuccess: (data) => {
      console.log('[PlaybookEditor] Executed playbook', { executionId: data?.execution_id })
      alert(`Execution started: ${data?.execution_id}`)
    },
  })

  const onNodesChange: OnNodesChange<StreamNode> = useCallback(
    (changes) => setNodes((nds) => applyNodeChanges(changes, nds)),
    []
  )
  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => setEdges((eds) => applyEdgeChanges(changes, eds)),
    []
  )
  const onConnect: OnConnect = useCallback(
    (connection) => setEdges((eds) => addEdge(connection, eds)),
    []
  )

  const addNode = (nodeType: string, label: string) => {
    const newNode: StreamNode = {
      id: `node-${Date.now()}`,
      data: { label, nodeType, config: {} },
      position: { x: 250, y: 100 + nodes.length * 100 },
      type: 'stream',
    }
    setNodes([...nodes, newNode])
    console.log('[PlaybookEditor] Added node', { nodeType, label })
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-900 p-6 flex items-center justify-center">
        <div className="text-red-400">Failed to load playbook</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-900 flex flex-col">
      <div className="bg-slate-800 border-b border-slate-700 p-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate('/streams')}
              className="text-amber-400 hover:text-amber-300 transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div>
              <h1 className="text-2xl font-bold text-amber-400">Playbook Editor</h1>
            </div>
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => executeMutation.mutate()}
              disabled={executeMutation.isPending || isLoading}
              className="flex items-center gap-2 bg-green-600 hover:bg-green-700 disabled:bg-green-500 text-white px-4 py-2 rounded-lg font-semibold transition-colors"
            >
              <Play className="w-4 h-4" />
              Execute
            </button>
            <button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || isLoading}
              className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-4 py-2 rounded-lg font-semibold transition-colors"
            >
              <Save className="w-4 h-4" />
              Save
            </button>
          </div>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Node Palette */}
        <div className="w-64 bg-slate-800 border-r border-slate-700 p-4 overflow-y-auto">
          <div className="mb-6">
            <h3 className="text-amber-400 font-semibold mb-3">Actions</h3>
            <div className="space-y-2">
              {NODE_TYPES.actions.map((node) => (
                <button
                  key={node.type}
                  onClick={() => addNode(node.type, node.label)}
                  className="w-full bg-slate-700 hover:bg-slate-600 text-slate-300 text-sm px-3 py-2 rounded transition-colors text-left"
                >
                  {node.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mb-6">
            <h3 className="text-amber-400 font-semibold mb-3">Conditionals</h3>
            <div className="space-y-2">
              {NODE_TYPES.conditionals.map((node) => (
                <button
                  key={node.type}
                  onClick={() => addNode(node.type, node.label)}
                  className="w-full bg-slate-700 hover:bg-slate-600 text-slate-300 text-sm px-3 py-2 rounded transition-colors text-left"
                >
                  {node.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <h3 className="text-amber-400 font-semibold mb-3">Transforms</h3>
            <div className="space-y-2">
              {NODE_TYPES.transforms.map((node) => (
                <button
                  key={node.type}
                  onClick={() => addNode(node.type, node.label)}
                  className="w-full bg-slate-700 hover:bg-slate-600 text-slate-300 text-sm px-3 py-2 rounded transition-colors text-left"
                >
                  {node.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Canvas */}
        <div className="flex-1">
          {isLoading ? (
            <div className="flex items-center justify-center h-full text-amber-400">
              Loading playbook...
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              nodeTypes={nodeTypes}
              onNodeClick={(_, node) => {
                setSelectedNode(node as StreamNode)
                setNodeConfig(node.data.config || {})
              }}
            >
              <Background variant={BackgroundVariant.Dots} gap={12} size={1} />
              <Controls />
            </ReactFlow>
          )}
        </div>

        {/* Node Config Panel */}
        {selectedNode && (
          <div className="w-72 bg-slate-800 border-l border-slate-700 p-4 flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-amber-400 font-semibold">Node Config</h3>
              <button
                onClick={() => {
                  setNodes(nodes.filter(n => n.id !== selectedNode.id))
                  setSelectedNode(null)
                }}
                className="text-red-400 hover:text-red-300 transition-colors"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>

            <div className="bg-slate-700 rounded p-3 mb-4">
              <p className="text-slate-300 text-sm font-mono">{selectedNode.data.label}</p>
            </div>

            <div className="flex-1">
              <label className="block text-slate-300 text-sm mb-2 font-semibold">Configuration</label>
              <textarea
                value={JSON.stringify(nodeConfig, null, 2)}
                onChange={(e) => {
                  try {
                    setNodeConfig(JSON.parse(e.target.value))
                  } catch {
                    // ignore parse errors while typing
                  }
                }}
                className="w-full h-32 bg-slate-700 text-slate-300 border border-slate-600 rounded p-2 text-xs font-mono resize-none focus:outline-none focus:border-amber-500"
                placeholder="{}"
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
