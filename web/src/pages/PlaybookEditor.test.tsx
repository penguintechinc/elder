import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import PlaybookEditor from "./PlaybookEditor";
import api from "@/lib/api";

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({
    nodes,
    onNodeClick,
    children,
  }: {
    nodes: Array<{ id: string; data: { label: string; nodeType: string } }>;
    onNodeClick?: (_e: unknown, n: unknown) => void;
    children?: React.ReactNode;
  }) => (
    <div data-testid="reactflow-canvas">
      {nodes.map((node) => (
        <button
          key={node.id}
          type="button"
          data-testid={`canvas-node-${node.id}`}
          onClick={() => onNodeClick?.({}, node)}
        >
          {node.data.label}
        </button>
      ))}
      {children}
    </div>
  ),
  Controls: () => null,
  Background: () => null,
  MiniMap: () => null,
  BackgroundVariant: { Dots: "dots" },
  Handle: () => null,
  Position: { Top: "top", Bottom: "bottom" },
  addEdge: (conn: unknown, eds: unknown[]) => [...eds, conn],
  applyNodeChanges: (_changes: unknown, nds: unknown[]) => nds,
  applyEdgeChanges: (_changes: unknown, eds: unknown[]) => eds,
}));

vi.mock("react-hot-toast", () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  default: {
    getStream: vi.fn(),
    getStreamNodeCatalog: vi.fn(),
    getInboundWebhooks: vi.fn(),
    updateStream: vi.fn(),
    validateStream: vi.fn(),
    executeStream: vi.fn(),
  },
}));

const MOCK_CATALOG = {
  nodes: [
    {
      node_type: "trigger_manual",
      name: "Manual Trigger",
      description: "Start playbook manually",
      category: "triggers",
      stage: "triggers",
      source: "builtin",
      connector_id: null,
      module: "core",
      required_module: null,
      module_label: "Core & Generic",
      subcategory: "Core Triggers",
      icon: "Play",
      color: "#10b981",
      inputs: [],
      outputs: [{ name: "out" }],
      config_schema: [
        {
          field: "sample_payload",
          label: "Sample Payload",
          type: "json",
          required: false,
          default: {},
        },
      ],
      output_schema: [{ name: "trigger", type: "string" }],
      endpoint: null,
      method: null,
      enabled: true,
      disabled_code: null,
      disabled_reason: null,
    },
    {
      node_type: "action_elder_create_issue",
      name: "Create Issue",
      description: "Create an issue",
      category: "actions",
      stage: "actions",
      source: "elder",
      connector_id: "elder",
      module: "issues",
      required_module: "issues",
      module_label: "Issues",
      subcategory: "Issues",
      icon: "AlertCircle",
      color: "#f97316",
      inputs: [{ name: "in", required: false }],
      outputs: [{ name: "out" }],
      config_schema: [
        {
          field: "title",
          label: "Issue Title",
          type: "string",
          required: true,
          supports_expressions: true,
        },
        {
          field: "priority",
          label: "Priority",
          type: "select",
          required: false,
          default: "medium",
          options: [
            { value: "low", label: "low" },
            { value: "medium", label: "medium" },
            { value: "high", label: "high" },
          ],
        },
      ],
      output_schema: [{ name: "id", type: "number" }],
      endpoint: "/api/v1/issues",
      method: "POST",
      enabled: true,
      disabled_code: null,
      disabled_reason: null,
    },
    {
      node_type: "action_elder_create_access_review",
      name: "Create Access Review",
      description: "Enterprise access review",
      category: "actions",
      stage: "actions",
      source: "elder",
      connector_id: "elder",
      module: "access_reviews",
      required_module: "access_reviews",
      module_label: "Access Reviews",
      subcategory: "Access Reviews",
      icon: "UserCheck",
      color: "#14b8a6",
      inputs: [{ name: "in", required: false }],
      outputs: [{ name: "out" }],
      config_schema: [],
      output_schema: [],
      endpoint: "/api/v1/access-reviews",
      method: "POST",
      enabled: false,
      disabled_code: "MODULE_UNLICENSED",
      disabled_reason:
        "Module 'Access Reviews' requires license feature 'access-reviews'",
    },
  ],
  modules: [
    {
      name: "core",
      display_name: "Core & Generic",
      enabled: true,
      node_count: 1,
    },
    { name: "issues", display_name: "Issues", enabled: true, node_count: 1 },
    {
      name: "access_reviews",
      display_name: "Access Reviews",
      enabled: false,
      node_count: 1,
      disabled_reason:
        "Module 'Access Reviews' requires license feature 'access-reviews'",
    },
  ],
  operators: [{ value: "eq", label: "Equals (=)" }],
  total: 3,
  enabled_count: 2,
};

function renderEditor() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={["/streams/7"]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/streams/:id" element={<PlaybookEditor />} />
          <Route path="/webhooks" element={<div>Webhooks Page</div>} />
          <Route path="/streams" element={<div>Streams List</div>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("PlaybookEditor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getStream).mockResolvedValue({
      id: 7,
      name: "Incident Triage",
      description: "Auto-open issues",
      canvas_data: {
        nodes: [
          {
            id: "node-1",
            type: "stream",
            position: { x: 100, y: 50 },
            data: {
              label: "Manual Trigger",
              nodeType: "trigger_manual",
              config: { sample_payload: { alert_name: "CPU High" } },
            },
          },
          {
            id: "node-2",
            type: "stream",
            position: { x: 100, y: 200 },
            data: {
              label: "Create Issue",
              nodeType: "action_elder_create_issue",
              config: { title: "" },
            },
          },
        ],
        edges: [{ id: "e-1", source: "node-1", target: "node-2" }],
      },
    });
    vi.mocked(api.getStreamNodeCatalog).mockResolvedValue(MOCK_CATALOG);
    vi.mocked(api.getInboundWebhooks).mockResolvedValue({
      inbound_webhooks: [],
    });
    vi.mocked(api.updateStream).mockResolvedValue({
      id: 7,
      validation: {
        valid: true,
        errors: [],
        warnings: [],
        disabled_modules: [],
        has_disabled_modules: false,
      },
    });
    vi.mocked(api.validateStream).mockResolvedValue({
      valid: false,
      errors: [
        {
          code: "MISSING_REQUIRED_CONFIG",
          node_id: "node-2",
          message: "Create Issue: required field 'Issue Title' is missing",
        },
      ],
      warnings: [],
      disabled_modules: [],
      has_disabled_modules: false,
    });
    vi.mocked(api.executeStream).mockResolvedValue({
      execution_id: "exec-999",
      status: "queued",
    });
  });

  it("loads catalog, filters palette by search & stage, and saves canvas_data via PUT", async () => {
    renderEditor();

    await waitFor(() => {
      expect(screen.getByText("Incident Triage")).toBeTruthy();
    });

    // Hydrates nodes onto ReactFlow canvas
    expect(screen.getByTestId("canvas-node-node-1")).toBeTruthy();
    expect(screen.getByTestId("canvas-node-node-2")).toBeTruthy();

    // Preserves Incoming Webhook soft-link and unavailable module disclosure
    expect(
      screen.getAllByText("Incoming Webhook").length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Unavailable Modules \(1\)/i)).toBeTruthy();

    // Search filters catalog palette nodes without removing canvas nodes
    const palette = screen.getByLabelText("Playbook Node Palette");
    const searchInput = within(palette).getByLabelText("Search nodes");
    fireEvent.change(searchInput, { target: { value: "Issue" } });
    expect(within(palette).getByText("Create Issue")).toBeTruthy();
    expect(within(palette).queryByText("Manual Trigger")).toBeNull();

    // Save sends full canvas_data via updateStream
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));
    await waitFor(() => {
      expect(api.updateStream).toHaveBeenCalledWith(
        7,
        expect.objectContaining({
          canvas_data: expect.objectContaining({
            nodes: expect.arrayContaining([
              expect.objectContaining({ id: "node-1" }),
              expect.objectContaining({ id: "node-2" }),
            ]),
            edges: expect.arrayContaining([
              expect.objectContaining({
                id: "e-1",
                source: "node-1",
                target: "node-2",
              }),
            ]),
          }),
        }),
      );
    });
  });

  it("opens NodeInspector with upstream field pills and inserts expression on click", async () => {
    renderEditor();

    await waitFor(() => {
      expect(screen.getByTestId("canvas-node-node-2")).toBeTruthy();
    });

    // Select downstream Create Issue node
    fireEvent.click(screen.getByTestId("canvas-node-node-2"));

    await waitFor(() => {
      expect(screen.getByLabelText("Node Label")).toBeTruthy();
    });

    // Upstream field pill from Manual Trigger's sample_payload appears
    const pillBtn = screen.getByRole("button", {
      name: /\$json\.alert_name/i,
    });
    const titleInput = screen.getByLabelText(
      /Issue Title/i,
    ) as HTMLInputElement;
    fireEvent.focus(titleInput);
    fireEvent.click(pillBtn);

    expect(titleInput.value).toContain("{{$json.alert_name}}");
  });

  it("validates graph and runs dry-run/live execution through modal", async () => {
    renderEditor();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Validate$/i })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /^Validate$/i }));
    await waitFor(() => {
      expect(
        screen.getByText(
          /Create Issue: required field 'Issue Title' is missing/i,
        ),
      ).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /^Execute$/i }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeTruthy();

    const payloadBox = screen.getByLabelText(/Trigger Input Payload/i);
    fireEvent.change(payloadBox, {
      target: { value: '{"severity":"critical"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: /Run Playbook/i }));

    await waitFor(() => {
      expect(api.executeStream).toHaveBeenCalledWith(7, {
        input_data: { severity: "critical" },
        dry_run: false,
      });
    });
  });
});
