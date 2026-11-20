import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Edge,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Play,
  Save,
  ShieldCheck,
  Webhook,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import {
  EdgePropertyModal,
  NodePropertyModal,
} from "@/components/diagram/FlowPropertyModals";
import NodeInspector from "@/components/playbooks/NodeInspector";
import NodePalette from "@/components/playbooks/NodePalette";
import PlaybookNodeCard from "@/components/playbooks/PlaybookNodeCard";
import type {
  NodeCatalogItem,
  NodeCatalogResponse,
  PlaybookNode,
  PlaybookNodeData,
  PlaybookValidationResult,
} from "@/components/playbooks/types";
import { computeUpstreamFields } from "@/components/playbooks/upstreamFields";
import api from "@/lib/api";

const nodeTypes = { stream: PlaybookNodeCard };

function buildInitialConfig(item: NodeCatalogItem): Record<string, unknown> {
  const cfg: Record<string, unknown> = {};
  for (const field of item.config_schema || []) {
    if (field.default !== undefined && field.default !== null) {
      cfg[field.field] = structuredClone(field.default);
    }
  }
  return cfg;
}

function hydrateNodesWithCatalog(
  rawNodes: PlaybookNode[],
  catalogByType: Record<string, NodeCatalogItem>,
): PlaybookNode[] {
  return rawNodes.map((n) => {
    const nt = n.data?.nodeType || "";
    const cat = catalogByType[nt];
    if (!cat) return n;
    return {
      ...n,
      type: n.type || "stream",
      data: {
        ...n.data,
        label: n.data?.label || cat.name,
        nodeType: nt,
        category: n.data?.category || cat.category,
        stage: n.data?.stage || cat.stage,
        module: n.data?.module || cat.module,
        moduleLabel: n.data?.moduleLabel || cat.module_label,
        icon: n.data?.icon || cat.icon,
        color:
          n.data?.color && n.data.color !== "transparent"
            ? n.data.color
            : cat.color,
        inputs: n.data?.inputs ?? cat.inputs,
        outputs: n.data?.outputs ?? cat.outputs,
        enabled: cat.enabled,
        disabledReason: cat.disabled_reason,
      },
    };
  });
}

export default function PlaybookEditor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [nodes, setNodes] = useState<PlaybookNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [isDirty, setIsDirty] = useState(false);
  const [validationResult, setValidationResult] =
    useState<PlaybookValidationResult | null>(null);
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance<
    PlaybookNode,
    Edge
  > | null>(null);

  const [doubleClickNode, setDoubleClickNode] = useState<PlaybookNode | null>(
    null,
  );
  const [doubleClickEdge, setDoubleClickEdge] = useState<Edge | null>(null);

  // Execute modal state
  const [showExecuteModal, setShowExecuteModal] = useState(false);
  const [executeInputJson, setExecuteInputJson] = useState("{}");
  const [executeDryRun, setExecuteDryRun] = useState(false);
  const [executeModalError, setExecuteModalError] = useState<string | null>(
    null,
  );

  const {
    data: streamData,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["stream", id],
    queryFn: () => (id ? api.getStream(Number(id)) : null),
    enabled: !!id,
  });

  const { data: catalogData, isLoading: catalogLoading } =
    useQuery<NodeCatalogResponse>({
      queryKey: ["stream-node-catalog"],
      queryFn: () => api.getStreamNodeCatalog(),
    });

  const { data: linkedInboundData } = useQuery({
    queryKey: ["inbound-webhooks", { streamId: id }],
    queryFn: () =>
      id
        ? (api.getInboundWebhooks({ stream_id: Number(id) }) as Promise<{
            inbound_webhooks?: Array<{
              id: number;
              name: string;
              receive_path: string;
              scopes?: string[];
              is_active: boolean;
            }>;
          }>)
        : Promise.resolve({ inbound_webhooks: [] }),
    enabled: !!id,
  });

  const catalogByType = useMemo(() => {
    const map: Record<string, NodeCatalogItem> = {};
    for (const item of catalogData?.nodes || []) {
      map[item.node_type] = item;
    }
    return map;
  }, [catalogData]);

  const catalogByTypeRef = useRef(catalogByType);
  catalogByTypeRef.current = catalogByType;

  // Hydrate initial canvas_data (or legacy top-level nodes/edges) once loaded
  useEffect(() => {
    if (!streamData) return;
    const rawNodes: PlaybookNode[] =
      streamData.canvas_data?.nodes || streamData.nodes || [];
    const rawEdges: Edge[] =
      streamData.canvas_data?.edges || streamData.edges || [];
    setNodes(hydrateNodesWithCatalog(rawNodes, catalogByTypeRef.current));
    setEdges(rawEdges);
    setIsDirty(false);
  }, [streamData]);

  // Re-enrich existing canvas nodes once catalog arrives
  useEffect(() => {
    if (Object.keys(catalogByType).length === 0) return;
    setNodes((prev) => hydrateNodesWithCatalog(prev, catalogByType));
  }, [catalogByType]);

  const applyValidationToNodes = useCallback(
    (result: PlaybookValidationResult) => {
      setValidationResult(result);
      const errorsByNode = new Map<string, string[]>();
      for (const issue of result.errors || []) {
        if (!issue.node_id) continue;
        const list = errorsByNode.get(issue.node_id) || [];
        list.push(issue.message);
        errorsByNode.set(issue.node_id, list);
      }
      setNodes((prev) =>
        prev.map((n) => ({
          ...n,
          data: {
            ...n.data,
            validationErrors: errorsByNode.get(n.id) || [],
          },
        })),
      );
    },
    [],
  );

  const openIncomingWebhookSoftLink = () => {
    const streamName = encodeURIComponent(streamData?.name || `Playbook ${id}`);
    navigate(
      `/webhooks?tab=inbound&create=1&stream_id=${id}&stream_name=${streamName}`,
    );
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!id) return Promise.resolve(null);
      const cleanNodes = nodes.map((n) => {
        const restData = { ...n.data };
        delete restData.validationErrors;
        return {
          ...n,
          data: restData,
        };
      });
      return api.updateStream(Number(id), {
        canvas_data: {
          nodes: cleanNodes,
          edges,
          viewport: reactFlowInstance?.getViewport(),
        },
      });
    },
    onSuccess: (updated) => {
      setIsDirty(false);
      if (updated?.validation) {
        applyValidationToNodes(updated.validation);
      }
      toast.success("Playbook saved");
      queryClient.invalidateQueries({ queryKey: ["stream", id] });
    },
    onError: () => {
      toast.error("Failed to save playbook");
    },
  });

  const validateMutation = useMutation({
    mutationFn: () =>
      id
        ? api.validateStream(Number(id), { canvas_data: { nodes, edges } })
        : Promise.resolve(null),
    onSuccess: (res: PlaybookValidationResult | null) => {
      if (!res) return;
      applyValidationToNodes(res);
      if (res.valid) {
        toast.success("Playbook validation passed");
      } else {
        toast.error(
          `Validation found ${res.errors.length} issue${res.errors.length === 1 ? "" : "s"}`,
        );
      }
    },
    onError: () => {
      toast.error("Validation request failed");
    },
  });

  const executeMutation = useMutation({
    mutationFn: async () => {
      if (!id) return null;
      let parsedInput: Record<string, unknown> = {};
      if (executeInputJson.trim()) {
        parsedInput = JSON.parse(executeInputJson);
      }
      return api.executeStream(Number(id), {
        input_data: parsedInput,
        dry_run: executeDryRun,
      });
    },
    onSuccess: (data) => {
      if (!data) return;
      if (data.validation) {
        applyValidationToNodes(data.validation);
      }
      if (data.dry_run) {
        if (data.valid) {
          toast.success(data.message || "Dry-run validation passed");
        } else {
          toast.error(data.message || "Dry-run validation failed");
        }
      } else {
        toast.success(`Execution queued: ${data.execution_id || "started"}`);
      }
      setShowExecuteModal(false);
      setExecuteModalError(null);
    },
    onError: (err: unknown) => {
      const apiErr = err as {
        response?: {
          data?: {
            error?: string;
            message?: string;
            validation?: PlaybookValidationResult;
          };
        };
      };
      const payload = apiErr?.response?.data;
      if (payload?.validation) {
        applyValidationToNodes(payload.validation);
      }
      const msg =
        payload?.error || payload?.message || "Failed to execute playbook";
      setExecuteModalError(msg);
      toast.error(msg);
    },
  });

  const onNodesChange: OnNodesChange<PlaybookNode> = useCallback((changes) => {
    setNodes((nds) => applyNodeChanges(changes, nds));
    if (changes.some((c) => c.type !== "select")) {
      setIsDirty(true);
    }
  }, []);

  const onEdgesChange: OnEdgesChange = useCallback((changes) => {
    setEdges((eds) => applyEdgeChanges(changes, eds));
    if (changes.some((c) => c.type !== "select")) {
      setIsDirty(true);
    }
  }, []);

  const onConnect: OnConnect = useCallback((connection) => {
    setEdges((eds) => addEdge(connection, eds));
    setIsDirty(true);
  }, []);

  const createCanvasNode = useCallback(
    (
      item: NodeCatalogItem,
      position?: { x: number; y: number },
    ): PlaybookNode => {
      const data: PlaybookNodeData = {
        label: item.name,
        nodeType: item.node_type,
        category: item.category,
        stage: item.stage,
        module: item.module,
        moduleLabel: item.module_label,
        icon: item.icon,
        color: item.color,
        inputs: item.inputs,
        outputs: item.outputs,
        config: buildInitialConfig(item),
        enabled: item.enabled,
        disabledReason: item.disabled_reason,
      };
      return {
        id: `node-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        type: "stream",
        position: position || {
          x: 260 + (nodes.length % 3) * 40,
          y: 80 + nodes.length * 110,
        },
        data,
      };
    },
    [nodes.length],
  );

  const addNodeFromCatalog = useCallback(
    (item: NodeCatalogItem, position?: { x: number; y: number }) => {
      if (!item.enabled) {
        toast.error(
          item.disabled_reason || "This module is currently disabled",
        );
        return;
      }
      const created = createCanvasNode(item, position);
      setNodes((prev) => [...prev, created]);
      setSelectedNodeId(created.id);
      setIsDirty(true);
    },
    [createCanvasNode],
  );

  const onDragOverCanvas = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDropCanvas = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      const nodeType = event.dataTransfer.getData(
        "application/elder-playbook-node",
      );
      if (!nodeType) return;
      const item = catalogByType[nodeType];
      if (!item) return;

      const bounds = event.currentTarget.getBoundingClientRect();
      const position = reactFlowInstance
        ? reactFlowInstance.screenToFlowPosition({
            x: event.clientX,
            y: event.clientY,
          })
        : {
            x: event.clientX - bounds.left - 100,
            y: event.clientY - bounds.top - 40,
          };
      addNodeFromCatalog(item, position);
    },
    [catalogByType, reactFlowInstance, addNodeFromCatalog],
  );

  const handleChangeNodeLabel = useCallback((nodeId: string, label: string) => {
    setNodes((prev) =>
      prev.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, label } } : n,
      ),
    );
    setIsDirty(true);
  }, []);

  const handleChangeNodeConfig = useCallback(
    (nodeId: string, config: Record<string, unknown>) => {
      setNodes((prev) =>
        prev.map((n) =>
          n.id === nodeId ? { ...n, data: { ...n.data, config } } : n,
        ),
      );
      setIsDirty(true);
    },
    [],
  );

  const handleDeleteNode = useCallback((nodeId: string) => {
    setNodes((prev) => prev.filter((n) => n.id !== nodeId));
    setEdges((prev) =>
      prev.filter((e) => e.source !== nodeId && e.target !== nodeId),
    );
    setSelectedNodeId((curr) => (curr === nodeId ? null : curr));
    setIsDirty(true);
  }, []);

  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedNodeId) || null,
    [nodes, selectedNodeId],
  );

  const upstreamFields = useMemo(() => {
    if (!selectedNode) return [];
    return computeUpstreamFields(selectedNode.id, nodes, edges, catalogByType);
  }, [selectedNode, nodes, edges, catalogByType]);

  const handleOpenExecute = () => {
    setExecuteModalError(null);
    setShowExecuteModal(true);
  };

  const handleConfirmExecute = () => {
    setExecuteModalError(null);
    if (executeInputJson.trim()) {
      try {
        const parsed = JSON.parse(executeInputJson);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          setExecuteModalError("Input data must be a JSON object.");
          return;
        }
      } catch {
        setExecuteModalError("Input data contains invalid JSON.");
        return;
      }
    }
    executeMutation.mutate();
  };

  if (error) {
    return (
      <div className="min-h-screen bg-slate-900 p-6 flex items-center justify-center">
        <div className="text-red-400">Failed to load playbook</div>
      </div>
    );
  }

  return (
    <div className="h-screen bg-slate-900 flex flex-col overflow-hidden">
      {/* Top Toolbar */}
      <header className="bg-slate-800 border-b border-slate-700 px-4 py-3 shrink-0">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <button
              type="button"
              onClick={() => navigate("/streams")}
              aria-label="Back to playbooks"
              className="text-amber-400 hover:text-amber-300 transition-colors p-1 rounded hover:bg-slate-700"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold text-amber-400 truncate">
                  {streamData?.name || "Playbook Editor"}
                </h1>
                {isDirty && (
                  <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40 font-medium">
                    Unsaved changes
                  </span>
                )}
              </div>
              {streamData?.description && (
                <p className="text-xs text-slate-400 truncate max-w-lg">
                  {streamData.description}
                </p>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2.5 flex-wrap">
            <button
              type="button"
              onClick={openIncomingWebhookSoftLink}
              className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 text-amber-300 border border-slate-600 px-3 py-1.5 rounded-lg text-sm font-semibold transition-colors"
              title="Create an authenticated Incoming Webhook with playbook trigger permissions"
            >
              <Webhook className="w-4 h-4" />
              Incoming Webhook
              {linkedInboundData?.inbound_webhooks?.length ? (
                <span className="ml-0.5 px-1.5 py-0.2 text-xs rounded-full bg-amber-500/20 text-amber-300">
                  {linkedInboundData.inbound_webhooks.length}
                </span>
              ) : null}
            </button>

            <button
              type="button"
              onClick={() => validateMutation.mutate()}
              disabled={validateMutation.isPending || isLoading}
              className="flex items-center gap-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-60 text-slate-100 border border-slate-600 px-3 py-1.5 rounded-lg text-sm font-semibold transition-colors"
            >
              <ShieldCheck className="w-4 h-4 text-sky-400" />
              Validate
            </button>

            <button
              type="button"
              onClick={handleOpenExecute}
              disabled={executeMutation.isPending || isLoading}
              className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white px-3.5 py-1.5 rounded-lg text-sm font-semibold transition-colors"
            >
              <Play className="w-4 h-4" />
              Execute
            </button>

            <button
              type="button"
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || isLoading}
              className="flex items-center gap-1.5 bg-amber-500 hover:bg-amber-600 disabled:opacity-60 text-slate-900 px-4 py-1.5 rounded-lg text-sm font-semibold transition-colors"
            >
              <Save className="w-4 h-4" />
              Save
            </button>
          </div>
        </div>
      </header>

      {/* Validation Banner */}
      {validationResult && (
        <div
          role="status"
          className={`px-4 py-2 border-b text-xs flex items-start justify-between gap-3 shrink-0 ${
            validationResult.valid
              ? "bg-emerald-950/60 border-emerald-700/60 text-emerald-200"
              : "bg-rose-950/70 border-rose-700/60 text-rose-200"
          }`}
        >
          <div className="flex items-start gap-2">
            {validationResult.valid ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            ) : (
              <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
            )}
            <div className="space-y-0.5">
              <div className="font-semibold">
                {validationResult.valid
                  ? "Playbook graph and nodes passed validation."
                  : `Validation found ${validationResult.errors.length} issue(s):`}
              </div>
              {validationResult.errors.map((err, i) => (
                <div key={i} className="text-rose-200/90">
                  • {err.message}
                </div>
              ))}
              {validationResult.warnings.map((w, i) => (
                <div key={`w-${i}`} className="text-amber-300">
                  Warning: {w.message}
                </div>
              ))}
            </div>
          </div>
          <button
            type="button"
            aria-label="Dismiss validation banner"
            onClick={() => setValidationResult(null)}
            className="text-slate-300 hover:text-white p-0.5"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Main 3-Pane Editor Layout */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <NodePalette
          nodes={catalogData?.nodes || []}
          modules={catalogData?.modules || []}
          isLoading={catalogLoading}
          linkedWebhooks={linkedInboundData?.inbound_webhooks || []}
          onAddNode={(item) => addNodeFromCatalog(item)}
          onOpenIncomingWebhook={openIncomingWebhookSoftLink}
          onNavigateWebhooks={() => navigate("/webhooks?tab=inbound")}
        />

        {/* Flow Canvas */}
        <div
          className="flex-1 relative min-w-0"
          onDragOver={onDragOverCanvas}
          onDrop={onDropCanvas}
        >
          {isLoading ? (
            <div className="flex items-center justify-center h-full text-amber-400">
              Loading playbook...
            </div>
          ) : (
            <ReactFlow<PlaybookNode, Edge>
              nodes={nodes}
              edges={edges}
              onInit={setReactFlowInstance}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              nodeTypes={nodeTypes}
              onNodeClick={(_, node) => setSelectedNodeId(node.id)}
              onPaneClick={() => setSelectedNodeId(null)}
              onNodeDoubleClick={(_, node) => setDoubleClickNode(node)}
              onEdgeDoubleClick={(_, edge) => setDoubleClickEdge(edge)}
              fitView
            >
              <Background variant={BackgroundVariant.Dots} gap={14} size={1} />
              <Controls />
              <MiniMap<PlaybookNode>
                nodeColor={(n) =>
                  typeof n.data?.color === "string" ? n.data.color : "#f59e0b"
                }
                className="!bg-slate-800 !border !border-slate-700"
              />
            </ReactFlow>
          )}
        </div>

        {/* Typed Schema Inspector */}
        {selectedNode && (
          <NodeInspector
            node={selectedNode}
            catalogItem={catalogByType[selectedNode.data.nodeType]}
            upstreamFields={upstreamFields}
            operatorOptions={catalogData?.operators || []}
            onChangeLabel={handleChangeNodeLabel}
            onChangeConfig={handleChangeNodeConfig}
            onDeleteNode={handleDeleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        )}
      </div>

      {/* Execute Playbook Modal */}
      {showExecuteModal && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="execute-modal-title"
          className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4"
        >
          <div className="bg-slate-800 border border-slate-700 rounded-xl max-w-lg w-full p-5 space-y-4 shadow-2xl">
            <div className="flex items-center justify-between">
              <h2
                id="execute-modal-title"
                className="text-lg font-bold text-amber-400"
              >
                Execute Playbook
              </h2>
              <button
                type="button"
                aria-label="Close execute modal"
                onClick={() => setShowExecuteModal(false)}
                className="text-slate-400 hover:text-white p-1"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {isDirty && (
              <div className="text-xs bg-amber-950/60 border border-amber-700/60 text-amber-200 rounded-lg px-3 py-2">
                You have unsaved canvas changes. Click Save first if you want
                execution to run the latest edits.
              </div>
            )}

            <div className="space-y-1.5">
              <label
                htmlFor="execute-input-json"
                className="block text-xs font-semibold text-slate-200"
              >
                Trigger Input Payload (JSON)
              </label>
              <textarea
                id="execute-input-json"
                rows={5}
                value={executeInputJson}
                onChange={(e) => setExecuteInputJson(e.target.value)}
                className="w-full bg-slate-900 text-slate-100 border border-slate-600 rounded-lg p-2.5 text-xs font-mono focus:outline-none focus:border-amber-500"
                placeholder='{"entity_id": 42, "severity": "high"}'
              />
            </div>

            <div className="flex items-center gap-2">
              <input
                id="execute-dry-run"
                type="checkbox"
                checked={executeDryRun}
                onChange={(e) => setExecuteDryRun(e.target.checked)}
                className="rounded border-slate-600 bg-slate-900 text-amber-500 focus:ring-amber-500"
              />
              <label
                htmlFor="execute-dry-run"
                className="text-xs text-slate-200 cursor-pointer"
              >
                Dry run (validate graph, modules, and node config without
                running)
              </label>
            </div>

            {executeModalError && (
              <div className="text-xs text-rose-300 bg-rose-950/60 border border-rose-700/60 rounded-lg p-2.5">
                {executeModalError}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowExecuteModal(false)}
                className="px-3.5 py-1.5 rounded-lg text-sm font-medium bg-slate-700 hover:bg-slate-600 text-slate-200"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmExecute}
                disabled={executeMutation.isPending}
                className="px-4 py-1.5 rounded-lg text-sm font-semibold bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white"
              >
                {executeMutation.isPending
                  ? "Running..."
                  : executeDryRun
                    ? "Run Validation"
                    : "Run Playbook"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Existing Double-Click Modals for Elder Resource Linking & Edge Labels */}
      <NodePropertyModal
        node={doubleClickNode}
        onClose={() => setDoubleClickNode(null)}
        onSave={(updatedNode) => {
          setNodes((nds) =>
            nds.map((n) =>
              n.id === updatedNode.id ? (updatedNode as PlaybookNode) : n,
            ),
          );
          setDoubleClickNode(null);
          setIsDirty(true);
        }}
      />

      <EdgePropertyModal
        edge={doubleClickEdge}
        onClose={() => setDoubleClickEdge(null)}
        onSave={(updatedEdge) => {
          setEdges((eds) =>
            eds.map((ed) => (ed.id === updatedEdge.id ? updatedEdge : ed)),
          );
          setDoubleClickEdge(null);
          setIsDirty(true);
        }}
      />
    </div>
  );
}
