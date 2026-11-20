import { describe, expect, it } from "vitest";
import type { Edge } from "@xyflow/react";
import type { NodeCatalogItem, PlaybookNode } from "./types";
import { computeUpstreamFields } from "./upstreamFields";

function makeCatalogItem(
  partial: Partial<NodeCatalogItem> & { node_type: string },
): NodeCatalogItem {
  return {
    name: partial.name || partial.node_type,
    description: "",
    category: partial.category || "actions",
    stage: partial.stage || "actions",
    source: "builtin",
    connector_id: null,
    module: "core",
    required_module: null,
    module_label: "Core & Generic",
    subcategory: "",
    icon: "Box",
    color: "#3b82f6",
    inputs: [],
    outputs: [{ name: "out" }],
    config_schema: [],
    output_schema: partial.output_schema || [],
    endpoint: null,
    method: null,
    enabled: true,
    disabled_code: null,
    disabled_reason: null,
    ...partial,
  };
}

describe("computeUpstreamFields", () => {
  const catalogByType: Record<string, NodeCatalogItem> = {
    trigger_manual: makeCatalogItem({
      node_type: "trigger_manual",
      name: "Manual Trigger",
      category: "triggers",
      stage: "triggers",
      output_schema: [
        { name: "trigger", type: "string" },
        { name: "timestamp", type: "string" },
      ],
    }),
    transform_set_fields: makeCatalogItem({
      node_type: "transform_set_fields",
      name: "Set Fields",
      category: "transforms",
      stage: "process",
    }),
    conditional_if_then: makeCatalogItem({
      node_type: "conditional_if_then",
      name: "If-Then",
      category: "conditionals",
      stage: "process",
    }),
    transform_elder_get_entity: makeCatalogItem({
      node_type: "transform_elder_get_entity",
      name: "Get Entity",
      category: "transforms",
      stage: "process",
      output_schema: [
        { name: "id", type: "number" },
        { name: "name", type: "string" },
        { name: "status", type: "string" },
      ],
    }),
    action_elder_create_issue: makeCatalogItem({
      node_type: "action_elder_create_issue",
      name: "Create Issue",
      category: "actions",
      stage: "actions",
    }),
  };

  it("extracts sample_payload keys, Set Fields assignments, and $node/$trigger/$execution pills", () => {
    const nodes: PlaybookNode[] = [
      {
        id: "t1",
        type: "stream",
        position: { x: 0, y: 0 },
        data: {
          label: "Manual Start",
          nodeType: "trigger_manual",
          config: {
            sample_payload: {
              entity_id: 42,
              meta: { owner: "secops" },
            },
          },
        },
      },
      {
        id: "e1",
        type: "stream",
        position: { x: 0, y: 120 },
        data: {
          label: "Fetch Server",
          nodeType: "transform_elder_get_entity",
          config: { id: "{{$json.entity_id}}" },
        },
      },
      {
        id: "if1",
        type: "stream",
        position: { x: 0, y: 240 },
        data: {
          label: "Check Status",
          nodeType: "conditional_if_then",
          config: {},
        },
      },
      {
        id: "sf1",
        type: "stream",
        position: { x: 0, y: 360 },
        data: {
          label: "Enrich Fields",
          nodeType: "transform_set_fields",
          config: {
            keep_only_set: false,
            assignments: [{ name: "ticket_summary", value: "{{$json.name}}" }],
          },
        },
      },
      {
        id: "act1",
        type: "stream",
        position: { x: 0, y: 480 },
        data: {
          label: "Open Issue",
          nodeType: "action_elder_create_issue",
          config: {},
        },
      },
    ];

    const edges: Edge[] = [
      { id: "e-1", source: "t1", target: "e1" },
      { id: "e-2", source: "e1", target: "if1" },
      { id: "e-3", source: "if1", target: "sf1", sourceHandle: "true" },
      { id: "e-4", source: "sf1", target: "act1" },
    ];

    const pills = computeUpstreamFields("act1", nodes, edges, catalogByType);
    const tokens = new Set(pills.map((p) => p.token));

    // Set Fields custom assignment -> $json and $node
    expect(tokens.has("{{$json.ticket_summary}}")).toBe(true);
    expect(tokens.has('{{$node["Enrich Fields"].ticket_summary}}')).toBe(true);

    // Propagated through passthrough conditional_if_then + keep_only_set=false from Fetch Server
    expect(tokens.has("{{$json.status}}")).toBe(true);
    expect(tokens.has('{{$node["Fetch Server"].status}}')).toBe(true);

    // Trigger sample payload -> $trigger and $node
    expect(tokens.has("{{$trigger.entity_id}}")).toBe(true);
    expect(tokens.has("{{$trigger.meta.owner}}")).toBe(true);
    expect(tokens.has('{{$node["Manual Start"].entity_id}}')).toBe(true);

    // Execution metadata always present
    expect(tokens.has("{{$execution.id}}")).toBe(true);
    expect(tokens.has("{{$execution.tenant_id}}")).toBe(true);
  });

  it("handles graph cycles safely without infinite recursion", () => {
    const nodes: PlaybookNode[] = [
      {
        id: "a",
        type: "stream",
        position: { x: 0, y: 0 },
        data: {
          label: "A",
          nodeType: "transform_elder_get_entity",
          config: {},
        },
      },
      {
        id: "b",
        type: "stream",
        position: { x: 0, y: 100 },
        data: { label: "B", nodeType: "conditional_if_then", config: {} },
      },
    ];
    const edges: Edge[] = [
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "b", target: "a" },
    ];

    const pills = computeUpstreamFields("b", nodes, edges, catalogByType);
    expect(pills.some((p) => p.token === "{{$json.name}}")).toBe(true);
  });
});
