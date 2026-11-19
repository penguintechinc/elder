import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";
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
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Save, Trash2, ArrowLeft, Users, Search, X } from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
import SearchableSelect from "@/components/SearchableSelect";
import { useDiagramCollaboration } from "@/hooks/useDiagramCollaboration";
import {
  IconSearch,
  iconMap,
  getAllIcons,
  getIcon,
  type IconDefinition,
} from "@/components/diagram/icons/index";

const handleStyle = "!w-3 !h-3 !bg-amber-500 !border-2 !border-amber-600";

const PRIMARY_12_COLORS = [
  { hex: "#1f2937", name: "Slate" },
  { hex: "#3b82f6", name: "Blue" },
  { hex: "#6366f1", name: "Indigo" },
  { hex: "#8b5cf6", name: "Purple" },
  { hex: "#ec4899", name: "Pink" },
  { hex: "#ef4444", name: "Red" },
  { hex: "#f97316", name: "Orange" },
  { hex: "#f59e0b", name: "Amber" },
  { hex: "#eab308", name: "Yellow" },
  { hex: "#22c55e", name: "Green" },
  { hex: "#14b8a6", name: "Teal" },
  { hex: "#06b6d4", name: "Cyan" },
];

type ResourceCategory =
  | ""
  | "network"
  | "entity"
  | "data_store"
  | "service"
  | "software"
  | "ai_resource";

const RESOURCE_CATEGORY_OPTIONS: { value: ResourceCategory; label: string }[] =
  [
    { value: "", label: "None" },
    { value: "network", label: "Network Resources" },
    { value: "entity", label: "Entities / Systems" },
    { value: "data_store", label: "Data Stores" },
    { value: "service", label: "Services" },
    { value: "software", label: "Software" },
    { value: "ai_resource", label: "AI Resources" },
  ];

const LINE_TYPE_OPTIONS = [
  { value: "virtual", label: "Virtual" },
  { value: "ethernet", label: "Ethernet" },
  { value: "oc3", label: "OC-3" },
  { value: "oc12", label: "OC-12" },
  { value: "oc48", label: "OC-48" },
  { value: "fiber", label: "Fiber" },
  { value: "mpls", label: "MPLS" },
  { value: "vpn", label: "VPN / Tunnel" },
  { value: "direct_connect", label: "AWS Direct Connect" },
  { value: "express_route", label: "Azure ExpressRoute" },
  { value: "peering", label: "Peering" },
  { value: "wireless", label: "Wireless" },
];

const LINE_SPEED_OPTIONS = [
  { value: "10Mbps", label: "10 Mbps" },
  { value: "100Mbps", label: "100 Mbps" },
  { value: "155Mbps", label: "155 Mbps (OC-3)" },
  { value: "622Mbps", label: "622 Mbps (OC-12)" },
  { value: "1Gbps", label: "1 Gbps" },
  { value: "2.5Gbps", label: "2.5 Gbps" },
  { value: "10Gbps", label: "10 Gbps" },
  { value: "25Gbps", label: "25 Gbps" },
  { value: "40Gbps", label: "40 Gbps" },
  { value: "100Gbps", label: "100 Gbps" },
  { value: "400Gbps", label: "400 Gbps" },
];

const LINE_PROVIDER_OPTIONS = [
  { value: "CenturyLink / Lumen", label: "CenturyLink / Lumen" },
  { value: "AT&T", label: "AT&T" },
  { value: "Verizon", label: "Verizon" },
  { value: "Zayo", label: "Zayo" },
  { value: "Cogent", label: "Cogent" },
  { value: "Comcast", label: "Comcast" },
  { value: "AWS", label: "AWS" },
  { value: "Azure", label: "Azure" },
  { value: "Google Cloud", label: "Google Cloud" },
  { value: "Cloudflare", label: "Cloudflare" },
  { value: "Internal", label: "Internal" },
];

type ArrowDirection = "right" | "left" | "both" | "none";
type LineStyle = "solid" | "dashed" | "dotted";

function dashArrayForStyle(style: LineStyle): string | undefined {
  if (style === "dashed") return "6 4";
  if (style === "dotted") return "2 4";
  return undefined;
}

interface CloudProviderNodeData extends Record<string, unknown> {
  label: string;
  provider: string;
  color: string;
  iconId?: string;
  iconColor?: string;
  resourceType?: ResourceCategory;
  resourceId?: number;
  resourceName?: string;
}

function CloudProviderNode({
  data,
  selected,
}: NodeProps<Node<CloudProviderNodeData>>) {
  const IconComp = getIcon(data.iconId || data.provider);
  const isTransparent = data.color === "transparent";
  return (
    <div
      className={`relative px-4 py-3 rounded-lg border-2 ${isTransparent ? "" : "shadow-lg"} ${selected ? "border-amber-400" : "border-slate-600"}`}
      style={{
        backgroundColor: isTransparent
          ? "transparent"
          : data.color || "#1f2937",
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        id="top-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Top}
        id="top-source"
        className={handleStyle}
        style={{ top: -6 }}
      />
      <Handle
        type="target"
        position={Position.Bottom}
        id="bottom-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Bottom}
        id="bottom-source"
        className={handleStyle}
        style={{ bottom: -6 }}
      />
      <Handle
        type="target"
        position={Position.Left}
        id="left-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Left}
        id="left-source"
        className={handleStyle}
        style={{ left: -6 }}
      />
      <Handle
        type="target"
        position={Position.Right}
        id="right-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Right}
        id="right-source"
        className={handleStyle}
        style={{ right: -6 }}
      />
      <div className="flex flex-col items-center gap-2 text-white text-sm font-medium">
        {IconComp && (
          <div style={data.iconColor ? { color: data.iconColor } : undefined}>
            <IconComp className="w-8 h-8" />
          </div>
        )}
        <span>{data.label}</span>
        {data.resourceName && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-black/40 text-amber-300 truncate max-w-[150px]">
            {data.resourceName}
          </span>
        )}
      </div>
    </div>
  );
}

interface InfrastructureNodeData extends Record<string, unknown> {
  label: string;
  type: string;
  color: string;
  iconId?: string;
  iconColor?: string;
  resourceType?: ResourceCategory;
  resourceId?: number;
  resourceName?: string;
}

function InfrastructureNode({
  data,
  selected,
}: NodeProps<Node<InfrastructureNodeData>>) {
  const IconComp = getIcon(data.iconId || data.type);
  const isTransparent = data.color === "transparent";
  return (
    <div
      className={`relative px-4 py-3 rounded-lg border-2 ${isTransparent ? "" : "shadow-lg"} ${selected ? "border-amber-400" : "border-slate-600"}`}
      style={{
        backgroundColor: isTransparent
          ? "transparent"
          : data.color || "#1f2937",
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        id="top-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Top}
        id="top-source"
        className={handleStyle}
        style={{ top: -6 }}
      />
      <Handle
        type="target"
        position={Position.Bottom}
        id="bottom-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Bottom}
        id="bottom-source"
        className={handleStyle}
        style={{ bottom: -6 }}
      />
      <Handle
        type="target"
        position={Position.Left}
        id="left-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Left}
        id="left-source"
        className={handleStyle}
        style={{ left: -6 }}
      />
      <Handle
        type="target"
        position={Position.Right}
        id="right-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Right}
        id="right-source"
        className={handleStyle}
        style={{ right: -6 }}
      />
      <div className="flex flex-col items-center gap-2 text-white text-sm font-medium">
        {IconComp && (
          <div style={data.iconColor ? { color: data.iconColor } : undefined}>
            <IconComp className="w-8 h-8" />
          </div>
        )}
        <span>{data.label}</span>
        {data.resourceName && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-black/40 text-amber-300 truncate max-w-[150px]">
            {data.resourceName}
          </span>
        )}
      </div>
    </div>
  );
}

interface ShapeNodeData extends Record<string, unknown> {
  label: string;
  color: string;
  shape: string;
  resourceType?: ResourceCategory;
  resourceId?: number;
  resourceName?: string;
}

function ShapeNode({ data, selected }: NodeProps<Node<ShapeNodeData>>) {
  const shapeStyles: Record<string, string> = {
    rectangle: "rounded-md",
    rounded: "rounded-xl",
    circle: "rounded-full",
    diamond: "rotate-45",
  };

  return (
    <div
      className={`relative ${data.shape === "circle" ? "w-20 h-20" : "px-4 py-3"}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        id="top-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Top}
        id="top-source"
        className={handleStyle}
        style={{ top: -6 }}
      />
      <Handle
        type="target"
        position={Position.Bottom}
        id="bottom-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Bottom}
        id="bottom-source"
        className={handleStyle}
        style={{ bottom: -6 }}
      />
      <Handle
        type="target"
        position={Position.Left}
        id="left-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Left}
        id="left-source"
        className={handleStyle}
        style={{ left: -6 }}
      />
      <Handle
        type="target"
        position={Position.Right}
        id="right-target"
        className={handleStyle}
      />
      <Handle
        type="source"
        position={Position.Right}
        id="right-source"
        className={handleStyle}
        style={{ right: -6 }}
      />
      <div
        className={`flex flex-col items-center justify-center border-2 ${data.color === "transparent" ? "" : "shadow-lg"} ${selected ? "border-amber-400" : data.color === "transparent" ? "border-slate-500" : "border-transparent"} ${shapeStyles[data.shape] || shapeStyles.rectangle} ${data.shape === "circle" ? "w-full h-full" : "px-4 py-2"}`}
        style={{
          backgroundColor:
            data.color === "transparent"
              ? "transparent"
              : data.color || "#374151",
        }}
      >
        <span
          className={`text-white text-sm font-medium ${data.shape === "diamond" ? "-rotate-45 block" : ""}`}
        >
          {data.label}
        </span>
        {data.resourceName && data.shape !== "diamond" && (
          <span className="text-[10px] px-1.5 py-0.5 mt-1 rounded bg-black/40 text-amber-300 truncate max-w-[140px]">
            {data.resourceName}
          </span>
        )}
      </div>
    </div>
  );
}

interface TextNodeData extends Record<string, unknown> {
  label: string;
  fontSize: string;
}

function TextNode({ data, selected }: NodeProps<Node<TextNodeData>>) {
  return (
    <div className={`px-2 py-1 ${selected ? "ring-2 ring-amber-400" : ""}`}>
      <span className={`text-white font-medium ${data.fontSize || "text-sm"}`}>
        {data.label}
      </span>
    </div>
  );
}

const nodeTypes: NodeTypes = {
  cloudProvider: CloudProviderNode,
  infrastructure: InfrastructureNode,
  shape: ShapeNode,
  text: TextNode,
};

const shapeOptions = [
  { type: "rectangle", label: "Rectangle" },
  { type: "rounded", label: "Rounded" },
  { type: "circle", label: "Circle" },
  { type: "diamond", label: "Diamond" },
];

interface RemoteCursor {
  identity_id: string;
  x: number;
  y: number;
}

function RemoteCursors({ cursors }: { cursors: RemoteCursor[] }) {
  const colors = ["#fbbf24", "#60a5fa", "#34d399", "#f87171", "#c084fc"];

  return (
    <div className="pointer-events-none fixed inset-0">
      {cursors.map((cursor, idx) => {
        const color = colors[idx % colors.length];
        return (
          <div
            key={cursor.identity_id}
            className="absolute w-4 h-6 pointer-events-none"
            style={{
              left: `${cursor.x}px`,
              top: `${cursor.y}px`,
              transform: "translate(-4px, -2px)",
            }}
          >
            <svg
              className="w-4 h-6"
              viewBox="0 0 24 24"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M5.5 1.5l12 18-4-6.5 6.5-4-12-6.5z"
                fill={color}
                stroke="currentColor"
                strokeWidth="1"
                strokeLinejoin="round"
              />
            </svg>
            <div
              className="absolute left-4 top-4 bg-slate-900 text-white text-xs px-1 py-0.5 rounded whitespace-nowrap"
              style={{ color }}
            >
              {cursor.identity_id.slice(0, 8)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const colorOptions = [
  { color: "#3B82F6", label: "Blue" },
  { color: "#10B981", label: "Green" },
  { color: "#F59E0B", label: "Amber" },
  { color: "#EF4444", label: "Red" },
  { color: "#8B5CF6", label: "Purple" },
  { color: "#EC4899", label: "Pink" },
  { color: "#6B7280", label: "Gray" },
  { color: "#1F2937", label: "Dark" },
];

function DiagramEditorContent() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedColor, setSelectedColor] = useState("#3B82F6");
  const nodeIdCounter = useRef(1);
  const drawingChangeTimeoutRef = useRef<number | null>(null);

  const {
    data: diagram,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["diagram", id],
    queryFn: () => api.getDiagram(Number(id)),
    enabled: !!id && id !== "new",
  });

  // Handle remote drawing changes
  const handleRemoteChange = useCallback(
    (remoteNodes: Node[], remoteEdges: Edge[]) => {
      setNodes(remoteNodes);
      setEdges(remoteEdges);
    },
    [],
  );

  // Collaboration hook
  const { collaborators, sendCursor, sendDrawingChange } =
    useDiagramCollaboration(Number(id) || 0, {
      enabled: !!id && id !== "new",
      onRemoteChange: handleRemoteChange,
    });

  const saveMutation = useMutation({
    mutationFn: () =>
      api.saveDiagramVersion(Number(id), {
        content: { nodes, edges },
        change_summary: "Canvas update",
      }),
    onSuccess: () => {
      console.log("[DiagramEditor] Version saved successfully");
    },
  });

  // Load diagram content on mount
  useEffect(() => {
    if (diagram?.content) {
      const content = diagram.content as { nodes?: Node[]; edges?: Edge[] };
      if (content.nodes) setNodes(content.nodes);
      if (content.edges) setEdges(content.edges);
      nodeIdCounter.current = (content.nodes?.length || 0) + 1;
    } else if (!diagram && !isLoading) {
      // Initialize empty diagram for new diagrams
      setNodes([]);
      setEdges([]);
    }
  }, [diagram, isLoading]);

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => {
      setNodes((nds) => {
        const updated = applyNodeChanges(changes, nds);
        // Debounce drawing change broadcast
        if (drawingChangeTimeoutRef.current)
          clearTimeout(drawingChangeTimeoutRef.current);
        drawingChangeTimeoutRef.current = window.setTimeout(() => {
          sendDrawingChange(updated, edges);
        }, 300);
        return updated;
      });
    },
    [edges, sendDrawingChange],
  );

  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => {
      setEdges((eds) => {
        const updated = applyEdgeChanges(changes, eds);
        // Debounce drawing change broadcast
        if (drawingChangeTimeoutRef.current)
          clearTimeout(drawingChangeTimeoutRef.current);
        drawingChangeTimeoutRef.current = window.setTimeout(() => {
          sendDrawingChange(nodes, updated);
        }, 300);
        return updated;
      });
    },
    [nodes, sendDrawingChange],
  );
  const onConnect: OnConnect = useCallback(
    (connection) =>
      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            animated: true,
            style: { stroke: "#F59E0B", strokeWidth: 2 },
            markerEnd: { type: MarkerType.ArrowClosed, color: "#F59E0B" },
            interactionWidth: 20,
          },
          eds,
        ),
      ),
    [],
  );

  const addIconNode = useCallback(
    (
      label: string,
      type: string,
      iconId?: string,
      iconColor?: string,
      isCloud = false,
    ) => {
      const newNode: Node = {
        id: String(nodeIdCounter.current++),
        type: isCloud ? "cloudProvider" : "infrastructure",
        position: { x: Math.random() * 400 + 100, y: Math.random() * 300 + 50 },
        data: isCloud
          ? {
              label,
              provider: type,
              iconId: iconId || type,
              iconColor,
              color: selectedColor,
            }
          : {
              label,
              type,
              iconId: iconId || type,
              iconColor,
              color: selectedColor,
            },
      };
      setNodes((nds) => [...nds, newNode]);
    },
    [selectedColor],
  );

  const [showIconSearch, setShowIconSearch] = useState(false);
  const allIcons = useMemo(() => getAllIcons(), []);

  // Node (object) double-click edit state
  const [editingNodeId, setEditingNodeId] = useState<string | null>(null);
  const [nodeLabel, setNodeLabel] = useState("");
  const [nodeColor, setNodeColor] = useState("#1f2937");
  const [nodeResourceCategory, setNodeResourceCategory] =
    useState<ResourceCategory>("");
  const [nodeResourceId, setNodeResourceId] = useState<string>("");

  // Edge (line) double-click edit state
  const [editingEdgeId, setEditingEdgeId] = useState<string | null>(null);
  const [edgeLabel, setEdgeLabel] = useState("");
  const [edgeLineType, setEdgeLineType] = useState("virtual");
  const [edgeSpeed, setEdgeSpeed] = useState("");
  const [edgeProvider, setEdgeProvider] = useState("");
  const [edgeArrowDirection, setEdgeArrowDirection] =
    useState<ArrowDirection>("right");
  const [edgeColor, setEdgeColor] = useState("#F59E0B");
  const [edgeLineStyle, setEdgeLineStyle] = useState<LineStyle>("solid");
  const [edgeAnimated, setEdgeAnimated] = useState(false);
  const [edgeMetadataText, setEdgeMetadataText] = useState("");
  const [edgeMetadataError, setEdgeMetadataError] = useState<string | null>(
    null,
  );

  const { data: categoryResources = [], isLoading: loadingResources } =
    useQuery({
      queryKey: ["diagram-resources", nodeResourceCategory],
      enabled: editingNodeId !== null && nodeResourceCategory !== "",
      queryFn: async () => {
        if (nodeResourceCategory === "network") {
          const res = await api.listNetworks();
          const items = Array.isArray(res) ? res : res?.items || [];
          return items.map(
            (n: {
              id: number;
              name?: string;
              network_type?: string;
              provider?: string;
              region?: string;
            }) => ({
              value: String(n.id),
              label: [
                n.name || `Network #${n.id}`,
                n.provider,
                n.network_type,
                n.region,
              ]
                .filter(Boolean)
                .join(" · "),
              rawName: n.name || `Network #${n.id}`,
            }),
          );
        }
        if (nodeResourceCategory === "entity") {
          const res = await api.getEntities({ per_page: 250 });
          return (res?.items || []).map(
            (e: { id: number; name: string; entity_type?: string }) => ({
              value: String(e.id),
              label: e.entity_type ? `${e.name} (${e.entity_type})` : e.name,
              rawName: e.name,
            }),
          );
        }
        if (nodeResourceCategory === "data_store") {
          const res = await api.getDataStores({ per_page: 250 });
          const items = Array.isArray(res) ? res : res?.items || [];
          return items.map(
            (d: { id: number; name: string; store_type?: string }) => ({
              value: String(d.id),
              label: d.store_type ? `${d.name} (${d.store_type})` : d.name,
              rawName: d.name,
            }),
          );
        }
        if (nodeResourceCategory === "service") {
          const res = await api.getServices({ per_page: 250 });
          return (res?.items || []).map((s: { id: number; name: string }) => ({
            value: String(s.id),
            label: s.name,
            rawName: s.name,
          }));
        }
        if (nodeResourceCategory === "software") {
          const res = await api.getSoftware({ per_page: 250 });
          return (res?.items || []).map(
            (s: { id: number; name: string; vendor?: string }) => ({
              value: String(s.id),
              label: s.vendor ? `${s.name} (${s.vendor})` : s.name,
              rawName: s.name,
            }),
          );
        }
        if (nodeResourceCategory === "ai_resource") {
          const res = await api.getAIResources({ per_page: 250 });
          return (res?.items || []).map(
            (a: { id: number; name: string; provider?: string | null }) => ({
              value: String(a.id),
              label: a.provider ? `${a.name} (${a.provider})` : a.name,
              rawName: a.name,
            }),
          );
        }
        return [];
      },
    });

  const handleNodeDoubleClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      const data = (node.data || {}) as Record<string, unknown>;
      setEditingNodeId(node.id);
      setNodeLabel(String(data.label || ""));
      setNodeColor(String(data.color || "#1f2937"));
      setNodeResourceCategory((data.resourceType as ResourceCategory) || "");
      setNodeResourceId(data.resourceId ? String(data.resourceId) : "");
    },
    [],
  );

  const handleSaveNodeEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingNodeId) return;
    const picked = categoryResources.find(
      (r: { value: string; label: string; rawName?: string }) =>
        r.value === nodeResourceId,
    );
    const updatedNodes = nodes.map((n) => {
      if (n.id !== editingNodeId) return n;
      const prevData = (n.data || {}) as Record<string, unknown>;
      const nextResourceName =
        nodeResourceCategory && nodeResourceId
          ? picked?.rawName ||
            picked?.label ||
            (prevData.resourceName as string | undefined)
          : undefined;
      return {
        ...n,
        data: {
          ...prevData,
          label: nodeLabel.trim() || prevData.label,
          color: nodeColor.trim() || "#1f2937",
          resourceType: nodeResourceCategory || undefined,
          resourceId:
            nodeResourceCategory && nodeResourceId
              ? Number(nodeResourceId) || nodeResourceId
              : undefined,
          resourceName: nextResourceName,
        },
      };
    });
    setNodes(updatedNodes);
    sendDrawingChange(updatedNodes, edges);
    setEditingNodeId(null);
  };

  const handleEdgeDoubleClick = useCallback(
    (_event: React.MouseEvent, edge: Edge) => {
      const data = (edge.data || {}) as Record<string, unknown>;
      const stroke =
        (edge.style?.stroke as string) || (data.color as string) || "#F59E0B";
      const dash = edge.style?.strokeDasharray;
      let inferredStyle: LineStyle = (data.lineStyle as LineStyle) || "solid";
      if (!data.lineStyle && typeof dash === "string") {
        inferredStyle = dash.startsWith("2") ? "dotted" : "dashed";
      }
      let inferredDir: ArrowDirection =
        (data.arrowDirection as ArrowDirection) || "right";
      if (!data.arrowDirection) {
        if (edge.markerStart && edge.markerEnd) inferredDir = "both";
        else if (edge.markerStart) inferredDir = "left";
        else if (edge.markerEnd) inferredDir = "right";
        else inferredDir = "none";
      }

      setEditingEdgeId(edge.id);
      setEdgeLabel(typeof edge.label === "string" ? edge.label : "");
      setEdgeLineType(String(data.lineType || "virtual"));
      setEdgeSpeed(String(data.speed || ""));
      setEdgeProvider(String(data.provider || ""));
      setEdgeArrowDirection(inferredDir);
      setEdgeColor(stroke);
      setEdgeLineStyle(inferredStyle);
      setEdgeAnimated(Boolean(edge.animated));
      setEdgeMetadataError(null);
      setEdgeMetadataText(
        data.metadata && typeof data.metadata === "object"
          ? JSON.stringify(data.metadata, null, 2)
          : "",
      );
    },
    [],
  );

  const handleSaveEdgeEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingEdgeId) return;
    setEdgeMetadataError(null);

    let parsedMeta: Record<string, unknown> | undefined;
    if (edgeMetadataText.trim()) {
      try {
        const parsed = JSON.parse(edgeMetadataText);
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          Array.isArray(parsed)
        ) {
          setEdgeMetadataError(
            'Metadata must be a JSON object (e.g. {"circuit_id": "CKT-101"})',
          );
          return;
        }
        parsedMeta = parsed as Record<string, unknown>;
      } catch {
        setEdgeMetadataError("Invalid JSON in metadata");
        return;
      }
    }

    const color = edgeColor.trim() || "#F59E0B";
    const marker = { type: MarkerType.ArrowClosed, color };
    const markerStart =
      edgeArrowDirection === "left" || edgeArrowDirection === "both"
        ? marker
        : undefined;
    const markerEnd =
      edgeArrowDirection === "right" || edgeArrowDirection === "both"
        ? marker
        : undefined;
    const autoSummary = [
      edgeLineType.trim(),
      edgeSpeed.trim(),
      edgeProvider.trim(),
    ]
      .filter(Boolean)
      .join(" · ");
    const finalLabel = edgeLabel.trim() || autoSummary || undefined;

    const updatedEdges = edges.map((ed) => {
      if (ed.id !== editingEdgeId) return ed;
      return {
        ...ed,
        label: finalLabel,
        animated: edgeAnimated,
        markerStart,
        markerEnd,
        style: {
          ...ed.style,
          stroke: color,
          strokeWidth: 2,
          strokeDasharray: dashArrayForStyle(edgeLineStyle),
        },
        data: {
          ...(ed.data || {}),
          lineType: edgeLineType.trim() || undefined,
          speed: edgeSpeed.trim() || undefined,
          provider: edgeProvider.trim() || undefined,
          arrowDirection: edgeArrowDirection,
          lineStyle: edgeLineStyle,
          color,
          metadata: parsedMeta,
        },
      };
    });
    setEdges(updatedEdges);
    sendDrawingChange(nodes, updatedEdges);
    setEditingEdgeId(null);
    toast.success("Line updated");
  };

  const handleIconSelect = useCallback(
    (icon: IconDefinition) => {
      const isCloud =
        icon.source === "aws" ||
        icon.source === "azure" ||
        icon.source === "ibm" ||
        icon.source === "gcp";
      addIconNode(icon.label, icon.id, icon.id, icon.color, isCloud);
      setShowIconSearch(false);
    },
    [addIconNode],
  );

  const addShapeNode = useCallback(
    (shape: string) => {
      const newNode: Node = {
        id: String(nodeIdCounter.current++),
        type: "shape",
        position: { x: Math.random() * 400 + 100, y: Math.random() * 300 + 50 },
        data: {
          label: `Shape ${nodeIdCounter.current - 1}`,
          shape,
          color: selectedColor,
        },
      };
      setNodes((nds) => [...nds, newNode]);
    },
    [selectedColor],
  );

  const deleteSelectedNodes = useCallback(() => {
    setNodes((nds) => nds.filter((n) => !n.selected));
  }, []);

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-amber-400">Loading diagram...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-red-400">
          Failed to load diagram. Please try again.
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen bg-slate-900 flex flex-col relative">
      <RemoteCursors cursors={collaborators} />
      {/* Toolbar */}
      <div className="bg-slate-800 border-b border-slate-700 p-4 flex items-center justify-between gap-4 relative z-10">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate("/diagrams")}
            className="flex items-center gap-2 text-amber-400 hover:text-amber-300 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
            Back
          </button>
          <div className="border-l border-slate-600" />
          <h1 className="text-xl font-bold text-amber-400">
            {diagram?.title || "New Diagram"}
          </h1>
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
              onClick={() => addIconNode("Server", "server")}
              className="bg-slate-700 hover:bg-slate-600 text-slate-200 px-3 py-1 rounded text-sm transition-colors"
            >
              Server
            </button>
            <button
              onClick={() => addIconNode("Database", "database")}
              className="bg-slate-700 hover:bg-slate-600 text-slate-200 px-3 py-1 rounded text-sm transition-colors"
            >
              DB
            </button>
            <button
              onClick={() => setShowIconSearch(true)}
              className="flex items-center gap-1.5 bg-slate-700 hover:bg-slate-600 text-amber-300 px-3 py-1 rounded text-sm transition-colors"
            >
              <Search className="w-3.5 h-3.5" />
              Icons
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
                className={`w-6 h-6 rounded border-2 transition-all ${selectedColor === option.color ? "border-amber-400 ring-2 ring-amber-300" : "border-slate-600"}`}
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
            {saveMutation.isPending ? "Saving..." : "Save"}
          </button>
        </div>
      </div>

      {/* Canvas */}
      <div
        className="flex-1 relative"
        onMouseMove={(e) => {
          // Send cursor position in screen coordinates
          sendCursor(e.clientX, e.clientY);
        }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeDoubleClick={handleNodeDoubleClick}
          onEdgeDoubleClick={handleEdgeDoubleClick}
          nodeTypes={nodeTypes}
          fitView
        >
          <Background
            variant={BackgroundVariant.Dots}
            color="#475569"
            gap={12}
          />
          <Controls />
          <MiniMap />
          <Panel
            position="bottom-left"
            className="bg-slate-800 border border-slate-700 rounded-lg p-3"
          >
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

      {showIconSearch && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          role="dialog"
          aria-modal="true"
          aria-label="Select diagram icon"
        >
          <div className="w-full max-w-md bg-slate-800 border border-slate-700 rounded-lg shadow-xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-base font-semibold text-amber-400">
                Select Diagram Icon
              </h2>
              <button
                onClick={() => setShowIconSearch(false)}
                className="text-slate-400 hover:text-white p-1 rounded"
                aria-label="Close icon search"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <IconSearch
              onSelect={handleIconSelect}
              allIcons={allIcons}
              iconMap={iconMap}
              placeholder="Search AWS, Azure, IBM, Iconoir, Infra icons..."
            />
          </div>
        </div>
      )}

      {/* Double-Click Object (Node) Modal */}
      {editingNodeId && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-label="Edit diagram object"
        >
          <div className="w-full max-w-lg bg-slate-800 border border-slate-700 rounded-lg shadow-xl p-5 text-white">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-amber-400">
                Edit Object
              </h2>
              <button
                type="button"
                onClick={() => setEditingNodeId(null)}
                className="text-slate-400 hover:text-white p-1 rounded"
                aria-label="Close object modal"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSaveNodeEdit} className="space-y-4">
              <div>
                <label
                  htmlFor="diagram-node-label"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Display Text
                </label>
                <input
                  id="diagram-node-label"
                  type="text"
                  value={nodeLabel}
                  onChange={(e) => setNodeLabel(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
                  placeholder="Object label"
                />
              </div>

              <div>
                <label
                  htmlFor="diagram-node-color-hex"
                  className="block text-sm font-medium text-slate-300 mb-2"
                >
                  Box Color
                </label>
                <div className="flex flex-wrap items-center gap-2 mb-2">
                  {PRIMARY_12_COLORS.map((c) => (
                    <button
                      key={c.hex}
                      type="button"
                      onClick={() => setNodeColor(c.hex)}
                      title={c.name}
                      aria-label={`Set box color ${c.name}`}
                      className={`w-6 h-6 rounded border-2 transition-transform ${
                        nodeColor.toLowerCase() === c.hex.toLowerCase()
                          ? "border-amber-400 scale-110"
                          : "border-slate-600"
                      }`}
                      style={{ backgroundColor: c.hex }}
                    />
                  ))}
                  <button
                    type="button"
                    onClick={() => setNodeColor("transparent")}
                    className={`px-2 py-1 text-xs rounded border ${
                      nodeColor === "transparent"
                        ? "border-amber-400 text-amber-300 bg-slate-900"
                        : "border-slate-600 text-slate-300 bg-slate-900/50"
                    }`}
                  >
                    Transparent
                  </button>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="color"
                    value={
                      nodeColor.startsWith("#") && nodeColor.length === 7
                        ? nodeColor
                        : "#1f2937"
                    }
                    onChange={(e) => setNodeColor(e.target.value)}
                    aria-label="Custom box color picker"
                    className="w-9 h-9 rounded border border-slate-700 bg-slate-900 cursor-pointer"
                  />
                  <input
                    id="diagram-node-color-hex"
                    type="text"
                    value={nodeColor}
                    onChange={(e) => setNodeColor(e.target.value)}
                    placeholder="#1f2937 or transparent"
                    className="flex-1 bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm font-mono text-white focus:outline-none focus:border-amber-500"
                  />
                </div>
              </div>

              <div>
                <label
                  htmlFor="diagram-node-resource-category"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Associate Resource Category
                </label>
                <select
                  id="diagram-node-resource-category"
                  value={nodeResourceCategory}
                  onChange={(e) => {
                    setNodeResourceCategory(e.target.value as ResourceCategory);
                    setNodeResourceId("");
                  }}
                  className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
                >
                  {RESOURCE_CATEGORY_OPTIONS.map((opt) => (
                    <option key={opt.value || "none"} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>

              {nodeResourceCategory && (
                <div>
                  <label
                    htmlFor="diagram-node-resource-picker"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Resource
                  </label>
                  <SearchableSelect
                    options={categoryResources}
                    value={nodeResourceId}
                    onChange={(val) => setNodeResourceId(String(val))}
                    isLoading={loadingResources}
                    placeholder="Search and select resource..."
                    allowClear
                    ariaLabel="Associate resource"
                  />
                </div>
              )}

              <div className="flex justify-end gap-2 pt-3 border-t border-slate-700">
                <button
                  type="button"
                  onClick={() => setEditingNodeId(null)}
                  className="px-4 py-2 text-sm rounded bg-slate-700 hover:bg-slate-600 text-slate-200"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 text-sm rounded bg-amber-500 hover:bg-amber-600 text-slate-900 font-semibold"
                >
                  Apply
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Double-Click Line (Edge) Modal */}
      {editingEdgeId && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-label="Edit diagram line"
        >
          <div className="w-full max-w-lg bg-slate-800 border border-slate-700 rounded-lg shadow-xl p-5 text-white max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold text-amber-400">
                Edit Line Details
              </h2>
              <button
                type="button"
                onClick={() => setEditingEdgeId(null)}
                className="text-slate-400 hover:text-white p-1 rounded"
                aria-label="Close line modal"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSaveEdgeEdit} className="space-y-4">
              <div>
                <label
                  htmlFor="diagram-edge-label"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Line Label (optional, defaults to Type · Speed · Provider)
                </label>
                <input
                  id="diagram-edge-label"
                  type="text"
                  value={edgeLabel}
                  onChange={(e) => setEdgeLabel(e.target.value)}
                  placeholder="e.g. Primary Fiber Uplink"
                  className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label
                    htmlFor="diagram-edge-type"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Line Type
                  </label>
                  <SearchableSelect
                    options={LINE_TYPE_OPTIONS}
                    value={edgeLineType}
                    onChange={(val) => setEdgeLineType(String(val))}
                    placeholder="virtual, ethernet, oc3..."
                    allowCustom
                    allowClear
                    ariaLabel="Line Type"
                  />
                </div>

                <div>
                  <label
                    htmlFor="diagram-edge-speed"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Speed
                  </label>
                  <SearchableSelect
                    options={LINE_SPEED_OPTIONS}
                    value={edgeSpeed}
                    onChange={(val) => setEdgeSpeed(String(val))}
                    placeholder="1Gbps, 10Gbps, 155Mbps..."
                    allowCustom
                    allowClear
                    ariaLabel="Speed"
                  />
                </div>

                <div className="sm:col-span-2">
                  <label
                    htmlFor="diagram-edge-provider"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Provider
                  </label>
                  <SearchableSelect
                    options={LINE_PROVIDER_OPTIONS}
                    value={edgeProvider}
                    onChange={(val) => setEdgeProvider(String(val))}
                    placeholder="CenturyLink / Lumen, AT&T, AWS..."
                    allowCustom
                    allowClear
                    ariaLabel="Provider"
                  />
                </div>

                <div>
                  <label
                    htmlFor="diagram-edge-direction"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Arrow Direction
                  </label>
                  <select
                    id="diagram-edge-direction"
                    value={edgeArrowDirection}
                    onChange={(e) =>
                      setEdgeArrowDirection(e.target.value as ArrowDirection)
                    }
                    className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
                  >
                    <option value="right">Right (→)</option>
                    <option value="left">Left (←)</option>
                    <option value="both">Both (↔)</option>
                    <option value="none">None (—)</option>
                  </select>
                </div>

                <div>
                  <label
                    htmlFor="diagram-edge-style"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Line Style
                  </label>
                  <select
                    id="diagram-edge-style"
                    value={edgeLineStyle}
                    onChange={(e) =>
                      setEdgeLineStyle(e.target.value as LineStyle)
                    }
                    className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500"
                  >
                    <option value="solid">Solid</option>
                    <option value="dashed">Dashed</option>
                    <option value="dotted">Dotted</option>
                  </select>
                </div>
              </div>

              <div>
                <label
                  htmlFor="diagram-edge-color-hex"
                  className="block text-sm font-medium text-slate-300 mb-2"
                >
                  Line Color
                </label>
                <div className="flex flex-wrap items-center gap-2 mb-2">
                  {PRIMARY_12_COLORS.map((c) => (
                    <button
                      key={c.hex}
                      type="button"
                      onClick={() => setEdgeColor(c.hex)}
                      title={c.name}
                      aria-label={`Set line color ${c.name}`}
                      className={`w-6 h-6 rounded border-2 transition-transform ${
                        edgeColor.toLowerCase() === c.hex.toLowerCase()
                          ? "border-amber-400 scale-110"
                          : "border-slate-600"
                      }`}
                      style={{ backgroundColor: c.hex }}
                    />
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="color"
                    value={
                      edgeColor.startsWith("#") && edgeColor.length === 7
                        ? edgeColor
                        : "#F59E0B"
                    }
                    onChange={(e) => setEdgeColor(e.target.value)}
                    aria-label="Custom line color picker"
                    className="w-9 h-9 rounded border border-slate-700 bg-slate-900 cursor-pointer"
                  />
                  <input
                    id="diagram-edge-color-hex"
                    type="text"
                    value={edgeColor}
                    onChange={(e) => setEdgeColor(e.target.value)}
                    placeholder="#F59E0B"
                    className="flex-1 bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-sm font-mono text-white focus:outline-none focus:border-amber-500"
                  />
                </div>
              </div>

              <label className="inline-flex items-center gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={edgeAnimated}
                  onChange={(e) => setEdgeAnimated(e.target.checked)}
                />
                <span>Animated flow</span>
              </label>

              <div>
                <label
                  htmlFor="diagram-edge-metadata"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Metadata (JSON)
                </label>
                <textarea
                  id="diagram-edge-metadata"
                  rows={3}
                  value={edgeMetadataText}
                  onChange={(e) => {
                    setEdgeMetadataText(e.target.value);
                    setEdgeMetadataError(null);
                  }}
                  placeholder='{"circuit_id": "CKT-1234", "vlan": 100}'
                  className="w-full font-mono bg-slate-900 border border-slate-700 rounded px-3 py-2 text-xs text-white focus:outline-none focus:border-amber-500"
                />
                {edgeMetadataError && (
                  <p className="text-xs text-red-400 mt-1">
                    {edgeMetadataError}
                  </p>
                )}
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t border-slate-700">
                <button
                  type="button"
                  onClick={() => setEditingEdgeId(null)}
                  className="px-4 py-2 text-sm rounded bg-slate-700 hover:bg-slate-600 text-slate-200"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 text-sm rounded bg-amber-500 hover:bg-amber-600 text-slate-900 font-semibold"
                >
                  Apply
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default function DiagramEditor() {
  return <DiagramEditorContent />;
}
