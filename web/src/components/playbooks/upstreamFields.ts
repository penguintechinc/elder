import type { Edge } from "@xyflow/react";
import type {
  NodeCatalogItem,
  OutputFieldSchema,
  PlaybookNode,
  UpstreamFieldPill,
} from "./types";

const PASSTHROUGH_NODE_TYPES = new Set([
  "transform_filter",
  "transform_delay",
  "conditional_if_then",
  "conditional_switch",
  "conditional_equals",
  "conditional_greater_than",
  "conditional_less_than",
  "conditional_contains",
  "conditional_regex",
  "action_log",
]);

function parseJsonObjectSafe(raw: unknown): Record<string, unknown> | null {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    return raw as Record<string, unknown>;
  }
  if (typeof raw === "string" && raw.trim().startsWith("{")) {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      return null;
    }
  }
  return null;
}

function inferJsonValueType(value: unknown): string {
  if (Array.isArray(value)) return "array";
  if (value === null) return "any";
  return typeof value;
}

function extractObjectSchemaFields(
  obj: Record<string, unknown>,
  sourceDesc: string,
  depth = 0,
  prefix = "",
): OutputFieldSchema[] {
  const fields: OutputFieldSchema[] = [];
  for (const [key, val] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    fields.push({
      name: path,
      type: inferJsonValueType(val),
      description: sourceDesc,
    });
    if (
      depth === 0 &&
      val &&
      typeof val === "object" &&
      !Array.isArray(val) &&
      Object.keys(val).length <= 8
    ) {
      fields.push(
        ...extractObjectSchemaFields(
          val as Record<string, unknown>,
          sourceDesc,
          depth + 1,
          path,
        ),
      );
    }
  }
  return fields;
}

export function extractNodeDeclaredFields(
  node: PlaybookNode,
  catalogItem?: NodeCatalogItem,
): OutputFieldSchema[] {
  const fields: OutputFieldSchema[] = [];
  const seen = new Set<string>();
  const pushField = (f: OutputFieldSchema) => {
    if (!f.name || seen.has(f.name)) return;
    seen.add(f.name);
    fields.push(f);
  };

  for (const schemaField of catalogItem?.output_schema || []) {
    pushField(schemaField);
  }

  const nodeType = node.data?.nodeType || "";
  const config = (node.data?.config || {}) as Record<string, unknown>;

  if (nodeType === "transform_set_fields") {
    const assignments = Array.isArray(config.assignments)
      ? config.assignments
      : [];
    for (const item of assignments) {
      if (!item || typeof item !== "object") continue;
      const rec = item as Record<string, unknown>;
      const name = String(rec.name || "").trim();
      if (!name) continue;
      pushField({
        name,
        type: String(rec.type || "string"),
        description: `Assigned by ${node.data?.label || "Set Fields"}`,
      });
    }
  }

  if (nodeType === "trigger_manual" || nodeType === "trigger_webhook") {
    const sampleObj = parseJsonObjectSafe(config.sample_payload);
    if (sampleObj) {
      for (const f of extractObjectSchemaFields(
        sampleObj,
        `Sample field from ${node.data?.label || nodeType}`,
      )) {
        pushField(f);
      }
    }
  }

  if (nodeType === "trigger_schedule") {
    const staticObj = parseJsonObjectSafe(config.static_payload);
    if (staticObj) {
      for (const f of extractObjectSchemaFields(
        staticObj,
        `Static schedule field from ${node.data?.label || "Schedule"}`,
      )) {
        pushField(f);
      }
    }
  }

  return fields;
}

function escapeNodeLabel(label: string): string {
  return label.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function isPassThroughNode(node: PlaybookNode): boolean {
  const nodeType = node.data?.nodeType || "";
  if (PASSTHROUGH_NODE_TYPES.has(nodeType)) return true;
  if (nodeType === "transform_set_fields") {
    return !(node.data?.config || {}).keep_only_set;
  }
  return false;
}

/**
 * Compute clickable n8n-style field expression pills for a selected node by
 * walking upstream incoming edges recursively.
 */
export function computeUpstreamFields(
  selectedNodeId: string,
  nodes: PlaybookNode[],
  edges: Edge[],
  catalogByType: Record<string, NodeCatalogItem>,
): UpstreamFieldPill[] {
  const nodesById = new Map<string, PlaybookNode>();
  for (const n of nodes) {
    nodesById.set(n.id, n);
  }

  const incomingByTarget = new Map<string, string[]>();
  for (const edge of edges) {
    if (!edge.source || !edge.target) continue;
    const list = incomingByTarget.get(edge.target) || [];
    if (!list.includes(edge.source)) {
      list.push(edge.source);
    }
    incomingByTarget.set(edge.target, list);
  }

  // BFS backwards with distance so immediate parents (distance=1) drive $json.*
  // and pass-through nodes propagate nearest non-passthrough fields into $json.*.
  const visited = new Set<string>([selectedNodeId]);
  const distanceById = new Map<string, number>();
  const orderedAncestors: PlaybookNode[] = [];
  const queue: Array<{ id: string; depth: number }> = [];

  for (const srcId of incomingByTarget.get(selectedNodeId) || []) {
    if (!visited.has(srcId)) {
      visited.add(srcId);
      queue.push({ id: srcId, depth: 1 });
    }
  }

  while (queue.length > 0) {
    const next = queue.shift();
    if (!next) break;
    const node = nodesById.get(next.id);
    if (!node) continue;
    distanceById.set(next.id, next.depth);
    orderedAncestors.push(node);

    for (const parentId of incomingByTarget.get(next.id) || []) {
      if (!visited.has(parentId)) {
        visited.add(parentId);
        queue.push({ id: parentId, depth: next.depth + 1 });
      }
    }
  }

  const pills: UpstreamFieldPill[] = [];
  const seenTokens = new Set<string>();
  const addPill = (pill: UpstreamFieldPill) => {
    const key = `${pill.scope}:${pill.token}`;
    if (seenTokens.has(key)) return;
    seenTokens.add(key);
    pills.push(pill);
  };

  // Determine which ancestors feed the active $json payload (distance=1 plus
  // sources behind pass-through nodes like If-Then/Switch/Filter/Set Fields).
  const jsonFeederIds = new Set<string>();
  const feederQueue: string[] = [
    ...(incomingByTarget.get(selectedNodeId) || []),
  ];
  const seenFeeders = new Set<string>([selectedNodeId]);
  while (feederQueue.length > 0) {
    const fid = feederQueue.shift();
    if (!fid || seenFeeders.has(fid)) continue;
    seenFeeders.add(fid);
    const fNode = nodesById.get(fid);
    if (!fNode) continue;
    jsonFeederIds.add(fid);
    if (isPassThroughNode(fNode)) {
      for (const up of incomingByTarget.get(fid) || []) {
        if (!seenFeeders.has(up)) {
          feederQueue.push(up);
        }
      }
    }
  }

  // Emit $json.* pills from active immediate / pass-through feeders first
  for (const anc of orderedAncestors) {
    if (!jsonFeederIds.has(anc.id)) continue;
    const nodeType = anc.data?.nodeType || "";
    const label = anc.data?.label || nodeType || anc.id;
    const fields = extractNodeDeclaredFields(anc, catalogByType[nodeType]);
    for (const f of fields) {
      addPill({
        token: `{{$json.${f.name}}}`,
        label: `$json.${f.name}`,
        type: f.type || "any",
        sourceNodeId: anc.id,
        sourceNodeLabel: label,
        scope: "json",
        description: f.description,
      });
    }
  }

  // Emit $node["Label"].* and $trigger.* pills for all upstream ancestors
  for (const anc of orderedAncestors) {
    const nodeType = anc.data?.nodeType || "";
    const label = anc.data?.label || nodeType || anc.id;
    const escapedLabel = escapeNodeLabel(label);
    const fields = extractNodeDeclaredFields(anc, catalogByType[nodeType]);

    for (const f of fields) {
      addPill({
        token: `{{$node["${escapedLabel}"].${f.name}}}`,
        label: `$node["${label}"].${f.name}`,
        type: f.type || "any",
        sourceNodeId: anc.id,
        sourceNodeLabel: label,
        scope: "node",
        description: f.description,
      });

      if (nodeType.startsWith("trigger_")) {
        addPill({
          token: `{{$trigger.${f.name}}}`,
          label: `$trigger.${f.name}`,
          type: f.type || "any",
          sourceNodeId: anc.id,
          sourceNodeLabel: label,
          scope: "trigger",
          description: f.description,
        });
      }
    }
  }

  // Always expose $execution.* runtime metadata pills
  const execFields: Array<{ name: string; type: string; desc: string }> = [
    { name: "id", type: "string", desc: "Current execution UUID" },
    { name: "playbook_id", type: "string", desc: "Current playbook ID" },
    { name: "tenant_id", type: "number", desc: "Current tenant ID" },
  ];
  for (const ef of execFields) {
    addPill({
      token: `{{$execution.${ef.name}}}`,
      label: `$execution.${ef.name}`,
      type: ef.type,
      sourceNodeId: "$execution",
      sourceNodeLabel: "Execution",
      scope: "execution",
      description: ef.desc,
    });
  }

  return pills;
}
