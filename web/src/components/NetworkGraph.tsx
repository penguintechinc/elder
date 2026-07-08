import React, { useCallback, useMemo, useEffect } from 'react';
import {
  ReactFlow,
  Node,
  Edge,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  NodeTypes,
  MarkerType,
  Handle,
  Position,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

// Entity type color scheme
const entityTypeColors: Record<string, string> = {
  // Organizations
  organization: '#10b981', // green-500

  // Applications & Services
  application: '#8b5cf6', // violet-500
  service: '#a855f7', // purple-500
  repository: '#7c3aed', // violet-600

  // Infrastructure
  datacenter: '#3b82f6', // blue-500
  vpc: '#6366f1', // indigo-500
  subnet: '#6366f1', // indigo-500
  compute: '#ec4899', // pink-500
  network: '#14b8a6', // teal-500

  // Data
  storage: '#0ea5e9', // sky-500
  database: '#06b6d4', // cyan-500

  // Security & Identity
  user: '#f59e0b', // amber-500
  security_issue: '#ef4444', // red-500

  // Default fallback
  default: '#64748b', // slate-500
};

interface GraphNode {
  id: string;
  label: string;
  type: string;
  metadata?: Record<string, unknown>;
}

interface GraphEdge {
  from: string;
  to: string;
  label?: string;
}

interface NetworkGraphProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  height?: string;
  onNodeClick?: (node: GraphNode) => void;
}

// Custom node component for better styling
interface CustomNodeData {
  nodeType: string;
  label: string;
  metadata?: Record<string, unknown>;
}

const CustomNode: React.FC<{ data: CustomNodeData }> = ({ data }) => {
  const color = entityTypeColors[data.nodeType] || entityTypeColors.default;

  return (
    <>
      {/* Connection handles for top-to-bottom flow */}
      <Handle type="target" position={Position.Top} />
      <Handle type="source" position={Position.Bottom} />

      <div
        className="px-4 py-2 shadow-lg rounded-lg border-2 min-w-[150px] text-center cursor-pointer hover:shadow-xl transition-shadow"
        style={{
          backgroundColor: `${color}20`,
          borderColor: color,
        }}
      >
        <div className="font-semibold text-sm" style={{ color }}>
          {data.nodeType === 'organization' ? '🏢' : '📦'} {data.nodeType}
        </div>
        <div className="text-xs text-slate-700 mt-1 font-medium">{data.label}</div>
      </div>
    </>
  );
};

const nodeTypes: NodeTypes = {
  custom: CustomNode,
};

export const NetworkGraph: React.FC<NetworkGraphProps> = ({
  nodes: graphNodes,
  edges: graphEdges,
  height = '600px',
  onNodeClick,
}) => {
  // Convert our graph data to React Flow format
  const initialNodes: Node[] = useMemo(() => {
    // Calculate proper tree depth for each node
    const nodeMap = new Map(graphNodes.map(n => [n.id, n]));
    const nodeLevels = new Map<string, number>();

    // Calculate depth by following parent chain
    const getDepth = (nodeId: string): number => {
      if (nodeLevels.has(nodeId)) {
        return nodeLevels.get(nodeId)!;
      }

      const node = nodeMap.get(nodeId);
      if (!node) return 0;

      // For organizations, check parent_id
      if (node.type === 'organization') {
        if (!node.metadata?.parent_id) {
          nodeLevels.set(nodeId, 0);
          return 0;
        }
        const parentId = `org-${node.metadata.parent_id}`;
        const depth = getDepth(parentId) + 1;
        nodeLevels.set(nodeId, depth);
        return depth;
      }

      // For entities, they're one level below their containing organization
      if (node.metadata?.organization_id) {
        const orgId = `org-${node.metadata.organization_id}`;
        const depth = getDepth(orgId) + 1;
        nodeLevels.set(nodeId, depth);
        return depth;
      }

      return 0;
    };

    // Calculate depth for all nodes
    graphNodes.forEach(node => getDepth(node.id));

    // Group nodes by level
    const nodesByLevel = new Map<number, typeof graphNodes>();
    graphNodes.forEach((node) => {
      const level = nodeLevels.get(node.id) || 0;
      if (!nodesByLevel.has(level)) {
        nodesByLevel.set(level, []);
      }
      nodesByLevel.get(level)!.push(node);
    });

    const nodes: Node[] = [];
    const horizontalSpacing = 250; // Space between nodes at same level
    const verticalSpacing = 150;   // Space between levels (parent to child)

    nodesByLevel.forEach((nodesAtLevel, level) => {
      const totalWidth = (nodesAtLevel.length - 1) * horizontalSpacing;
      const startX = -totalWidth / 2; // Center the nodes at this level

      nodesAtLevel.forEach((node, indexInLevel) => {
        nodes.push({
          id: node.id,
          type: 'custom',
          position: {
            x: startX + indexInLevel * horizontalSpacing + 200,
            y: level * verticalSpacing + 50,
          },
          data: {
            label: node.label,
            nodeType: node.type,
            metadata: node.metadata,
          },
        });
      });
    });

    return nodes;
  }, [graphNodes]);

  const initialEdges: Edge[] = useMemo(() => {
    console.log('NetworkGraph: Converting edges:', graphEdges);
    console.log('NetworkGraph: graphEdges type:', typeof graphEdges, 'isArray:', Array.isArray(graphEdges));

    if (!graphEdges || !Array.isArray(graphEdges)) {
      console.error('NetworkGraph: graphEdges is not an array!', graphEdges);
      return [];
    }

    // Create a set of valid node IDs for quick lookup
    const validNodeIds = new Set(graphNodes.map(n => n.id));

    // Filter out edges where source or target doesn't exist
    const validEdges = graphEdges.filter(edge => {
      const sourceExists = validNodeIds.has(edge.from);
      const targetExists = validNodeIds.has(edge.to);
      if (!sourceExists || !targetExists) {
        console.log(`NetworkGraph: Filtering out edge from ${edge.from} to ${edge.to} - source exists: ${sourceExists}, target exists: ${targetExists}`);
      }
      return sourceExists && targetExists;
    });

    const converted = validEdges.map((edge, index) => {
      console.log(`NetworkGraph: Processing edge ${index}:`, edge);

      // Different colors for different edge types
      const edgeColor = edge.label === 'parent' ? '#10b981' :
                       edge.label === 'contains' ? '#3b82f6' :
                       '#f59e0b'; // dependencies

      const reactFlowEdge: Edge = {
        id: `edge-${index}`,
        // For top-to-bottom flow: parent -> child (use original direction)
        source: edge.from,
        target: edge.to,
        label: edge.label || '',
        type: 'default',
        animated: true,
        style: {
          stroke: edgeColor,
          strokeWidth: 3,
        },
        labelStyle: {
          fill: edgeColor,
          fontWeight: 700,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: edgeColor,
        },
      };
      console.log('NetworkGraph: Created edge:', reactFlowEdge);
      return reactFlowEdge;
    });
    console.log('NetworkGraph: Total edges converted:', converted.length);
    console.log('NetworkGraph: Final edges array:', converted);
    return converted;
  }, [graphEdges, graphNodes]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  console.log('NetworkGraph: Current nodes state:', nodes);
  console.log('NetworkGraph: Node IDs:', nodes.map(n => n.id));
  console.log('NetworkGraph: Current edges state:', edges);
  console.log('NetworkGraph: Edge connections:', edges.map(e => ({ id: e.id, source: e.source, target: e.target })));

  // Check if edges have valid source/target that match node IDs
  edges.forEach((edge) => {
    const sourceExists = nodes.find(n => n.id === edge.source);
    const targetExists = nodes.find(n => n.id === edge.target);
    console.log(`Edge ${edge.id}: source=${edge.source} (exists: ${!!sourceExists}), target=${edge.target} (exists: ${!!targetExists})`);
  });

  // Update nodes when graph data changes
  useEffect(() => {
    console.log('NetworkGraph: Setting nodes:', initialNodes);
    setNodes(initialNodes);
  }, [initialNodes, setNodes]);

  // Update edges when graph data changes
  useEffect(() => {
    console.log('NetworkGraph: Setting edges:', initialEdges);
    // Set edges multiple times to force re-render
    setEdges([]);
    setTimeout(() => {
      setEdges(initialEdges);
      console.log('NetworkGraph: Edges set after timeout');
    }, 100);
  }, [initialEdges, setEdges]);

  const handleNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (onNodeClick) {
        const graphNode: GraphNode = {
          id: node.id,
          label: node.data.label as string,
          type: node.data.nodeType as string,
          metadata: node.data.metadata as Record<string, unknown> | undefined,
        };
        onNodeClick(graphNode);
      }
    },
    [onNodeClick]
  );

  if (graphNodes.length === 0) {
    return (
      <div
        className="flex items-center justify-center bg-slate-50 rounded-lg border-2 border-dashed border-slate-300"
        style={{ height }}
      >
        <div className="text-center text-slate-500">
          <p className="text-lg font-medium">No relationships to display</p>
          <p className="text-sm mt-2">Create entities and dependencies to see the graph</p>
        </div>
      </div>
    );
  }

  return (
    <div style={{ height, width: '100%' }} className="bg-slate-50 rounded-lg border border-slate-200">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        nodeTypes={nodeTypes}
        fitView
      >
        <Background color="#94a3b8" gap={16} />
        <Controls />
        <MiniMap />
      </ReactFlow>
    </div>
  );
};
