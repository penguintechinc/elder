import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NodePropertyModal } from "./FlowPropertyModals";
import { fetchCategoryResources } from "./resourceLookup";
import NodeInspector from "@/components/playbooks/NodeInspector";
import type {
  NodeCatalogItem,
  PlaybookNode,
} from "@/components/playbooks/types";
import api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    listNetworks: vi.fn(),
    getIdentities: vi.fn(),
    getEntities: vi.fn(),
    getDataStores: vi.fn(),
    getServices: vi.fn(),
    getSoftware: vi.fn(),
    getAIResources: vi.fn(),
    getOrganizations: vi.fn(),
    getProjects: vi.fn(),
    getIssues: vi.fn(),
  },
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("FlowPropertyModals & NodeInspector resource lookup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("unpacks { networks: [...] } from api.listNetworks instead of returning blank", async () => {
    vi.mocked(api.listNetworks).mockResolvedValue({
      networks: [
        {
          id: 11,
          name: "Corp VPC",
          provider: "aws",
          network_type: "vpc",
          region: "us-east-1",
        },
        {
          id: 12,
          name: "DMZ Subnet",
          network_type: "subnet",
        },
      ],
      total: 2,
    });

    const opts = await fetchCategoryResources("network");
    expect(opts).toEqual([
      {
        value: "11",
        label: "Corp VPC · aws · vpc · us-east-1",
        rawName: "Corp VPC",
      },
      {
        value: "12",
        label: "DMZ Subnet · subnet",
        rawName: "DMZ Subnet",
      },
    ]);
  });

  it("renders network options in NodePropertyModal and shows No options available when empty", async () => {
    vi.mocked(api.listNetworks).mockResolvedValue({
      networks: [{ id: 5, name: "Prod LAN", network_type: "vlan" }],
    });
    vi.mocked(api.getDataStores).mockResolvedValue({ items: [] });

    wrap(
      <NodePropertyModal
        node={{
          id: "n1",
          position: { x: 0, y: 0 },
          data: { label: "Gateway" },
        }}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    const catSelect = screen.getByLabelText("Associate Resource Category");
    fireEvent.change(catSelect, { target: { value: "network" } });

    const combo = await screen.findByRole("combobox", {
      name: "Associate resource",
    });
    fireEvent.focus(combo);
    expect(await screen.findByText("Prod LAN · vlan")).toBeTruthy();

    fireEvent.change(catSelect, { target: { value: "data_store" } });
    fireEvent.focus(combo);
    expect(await screen.findByText("No options available")).toBeTruthy();
  });

  it("merges resource association & box color into NodeInspector and supports resource-state conditions", async () => {
    vi.mocked(api.getIdentities).mockResolvedValue({
      items: [
        { id: 42, name: "Jane Doe", email: "jane@example.com", identity_type: "user" },
      ],
    });

    const onUpdateNodeData = vi.fn();
    const onChangeConfig = vi.fn();
    const node: PlaybookNode = {
      id: "gate-1",
      type: "stream",
      position: { x: 0, y: 0 },
      data: {
        label: "If-Then",
        nodeType: "conditional_if_then",
        config: {
          logic: "and",
          conditions: [
            { source: "input", field: "severity", operator: "eq", value: "high" },
          ],
        },
      },
    };
    const catalogItem = {
      node_type: "conditional_if_then",
      name: "If-Then",
      description: "Conditional",
      category: "conditionals",
      stage: "process",
      module: "core",
      module_label: "Core",
      enabled: true,
      icon: "GitBranch",
      color: "#8b5cf6",
      inputs: [{ name: "in", required: true, data_type: "any" }],
      outputs: [
        { name: "true", data_type: "any" },
        { name: "false", data_type: "any" },
      ],
      config_schema: [
        {
          field: "conditions",
          label: "Conditions",
          type: "conditions",
          required: false,
          supports_expressions: true,
        },
      ],
      output_schema: [],
    } as unknown as NodeCatalogItem;

    wrap(
      <NodeInspector
        node={node}
        catalogItem={catalogItem}
        upstreamFields={[]}
        operatorOptions={[{ value: "eq", label: "Equals (=)" }]}
        onChangeLabel={vi.fn()}
        onChangeConfig={onChangeConfig}
        onUpdateNodeData={onUpdateNodeData}
        onDeleteNode={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    // Associate an Identity / User directly in the right sidebar
    const catSelect = screen.getByLabelText("Resource Category");
    fireEvent.change(catSelect, { target: { value: "identity" } });
    expect(onUpdateNodeData).toHaveBeenCalledWith("gate-1", {
      resourceType: "identity",
      resourceId: undefined,
      resourceName: undefined,
    });

    // Switch condition source to Resource State / Relationship
    const sourceSelect = screen.getByLabelText("Condition 1 source");
    fireEvent.change(sourceSelect, { target: { value: "resource" } });
    await waitFor(() => {
      expect(onChangeConfig).toHaveBeenCalledWith(
        "gate-1",
        expect.objectContaining({
          conditions: [
            expect.objectContaining({
              source: "resource",
              field: "severity",
            }),
          ],
        }),
      );
    });
  });
});
