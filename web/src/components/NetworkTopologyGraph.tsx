import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import api from '@/lib/api'

interface NetworkTopologyGraphProps {
  organizationId: number
}

interface TopologyNode {
  id: number
  name: string
  network_type: string
  region?: string
}

interface TopologyEdge {
  id: number
  source: number
  target: number
  connection_type: string
}

const NODE_COLORS = {
  subnet: '#3b82f6',
  firewall: '#ef4444',
  proxy: '#8b5cf6',
  router: '#10b981',
  switch: '#f59e0b',
  hub: '#6366f1',
  tunnel: '#ec4899',
  route_table: '#14b8a6',
  vrrf: '#f97316',
  vxlan: '#06b6d4',
  vlan: '#84cc16',
  namespace: '#a855f7',
  other: '#64748b',
}

export default function NetworkTopologyGraph({ organizationId }: NetworkTopologyGraphProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])

  const { data: graphData, isLoading } = useQuery({
    queryKey: ['networkTopologyGraph', organizationId],
    queryFn: () => api.getNetworkTopologyGraph(organizationId, true),
  })

  useEffect(() => {
    if (graphData?.nodes && graphData?.edges) {
      // Convert API nodes to ReactFlow nodes
      const flowNodes: Node[] = graphData.nodes.map((node: TopologyNode, index: number) => {
        const color = NODE_COLORS[node.network_type as keyof typeof NODE_COLORS] || NODE_COLORS.other

        return {
          id: node.id.toString(),
          type: 'default',
          position: {
            x: (index % 5) * 250,
            y: Math.floor(index / 5) * 150,
          },
          data: {
            label: (
              <div className="text-center">
                <div className="font-semibold text-sm">{node.name}</div>
                <div className="text-xs text-slate-500">{node.network_type}</div>
                {node.region && <div className="text-xs text-slate-400">{node.region}</div>}
              </div>
            ),
          },
          style: {
            background: color,
            color: 'white',
            border: `2px solid ${color}`,
            borderRadius: '8px',
            padding: '10px',
            minWidth: '150px',
          },
        }
      })

      // Convert API edges to ReactFlow edges
      const flowEdges: Edge[] = graphData.edges.map((edge: TopologyEdge) => ({
        id: edge.id.toString(),
        source: edge.source.toString(),
        target: edge.target.toString(),
        label: edge.connection_type,
        type: 'smoothstep',
        animated: true,
        style: { stroke: '#64748b' },
        labelStyle: { fill: '#94a3b8', fontSize: 10 },
        labelBgStyle: { fill: '#1e293b', fillOpacity: 0.9 },
      }))

      setNodes(flowNodes)
      setEdges(flowEdges)
    }
  }, [graphData, setNodes, setEdges])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96 bg-slate-900">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!graphData || nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-96 bg-slate-900 text-slate-400">
        <p>No network topology data available</p>
      </div>
    )
  }

  return (
    <div className="h-[600px] bg-slate-900 rounded-lg overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
        attributionPosition="bottom-left"
      >
        <Background color="#475569" gap={16} />
        <Controls />
        <MiniMap
          nodeColor={(node) => {
            const networkType = graphData.nodes.find((n: TopologyNode) => n.id.toString() === node.id)?.network_type
            return NODE_COLORS[networkType as keyof typeof NODE_COLORS] || NODE_COLORS.other
          }}
          maskColor="rgba(15, 23, 42, 0.8)"
        />
      </ReactFlow>
    </div>
  )
}
