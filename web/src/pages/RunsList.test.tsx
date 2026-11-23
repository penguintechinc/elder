import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import RunsList from "./RunsList";
import api, { DEFAULT_RUN_PERMISSIONS_CATALOG } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    default: {
      listRunFunctions: vi.fn(),
      createRunFunction: vi.fn(),
      deleteRunFunction: vi.fn(),
      invokeRunFunction: vi.fn(),
      getRunPermissionsCatalog: vi.fn(),
    },
  };
});

vi.mock("react-hot-toast", () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const createQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

function renderRunsList() {
  const queryClient = createQueryClient();
  return render(
    <MemoryRouter initialEntries={["/runs"]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/runs" element={<RunsList />} />
          <Route path="/runs/:id" element={<div>Run Detail Page</div>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const SAMPLE_FUNCTIONS = [
  {
    id: 1,
    name: "enrich-alerts",
    description: "Enriches alert payloads with CMDB metadata",
    runtime: "nodejs",
    code: "export async function handler() { return {}; }",
    storage_backend: "s3" as const,
    storage_key: "tenants/default/runs/fn-1/v1.tar.gz",
    code_sha256:
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    timeout_seconds: 30,
    memory_mb: 256,
    schedule_cron: "*/5 * * * *",
    execution_namespace: "elder-runs",
    worker_cluster: "dal2-alpha",
    village_id: "vid-run-1",
    granted_scopes: ["entities:read", "entities:write", "integrations:sync"],
    allowed_egress_domains: ["api.fleetdm.local"],
  },
  {
    id: 2,
    name: "audit-inventory",
    description: "Validates asset inventory daily",
    runtime: "python",
    code: "def handler(d): return d",
    storage_backend: "database" as const,
    storage_key: null,
    code_sha256:
      "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    timeout_seconds: 60,
    memory_mb: 512,
    execution_namespace: "elder-runs",
    granted_scopes: ["entities:read"],
  },
  {
    id: 3,
    name: "fast-packet-parser",
    description: "Parses binary telemetry headers",
    runtime: "rust",
    code: "pub fn handler(v: Value) -> Value { v }",
    timeout_seconds: 15,
    memory_mb: 128,
    execution_namespace: "elder-runs",
    granted_scopes: [],
  },
];

describe("RunsList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getRunPermissionsCatalog).mockResolvedValue(
      DEFAULT_RUN_PERMISSIONS_CATALOG,
    );
  });

  it("renders function list with runtime badges, cron schedule, and namespace", async () => {
    vi.mocked(api.listRunFunctions).mockResolvedValue({
      data: SAMPLE_FUNCTIONS,
      total: 3,
      page: 1,
      per_page: 50,
    });

    renderRunsList();

    await waitFor(() => {
      expect(screen.getByText("CodeRuns (Serverless Functions)")).toBeTruthy();
      expect(screen.getByText("enrich-alerts")).toBeTruthy();
      expect(screen.getByText("audit-inventory")).toBeTruthy();
      expect(screen.getByText("fast-packet-parser")).toBeTruthy();
    });

    expect(screen.getByTestId("runtime-badge-1").textContent).toBe("Node.js");
    expect(screen.getByTestId("runtime-badge-2").textContent).toBe("Python");
    expect(screen.getByTestId("runtime-badge-3").textContent).toBe("Rust");
    expect(screen.getByTestId("storage-badge-1").textContent).toBe("S3");
    expect(screen.getByTestId("storage-badge-2").textContent).toBe("DB");
    expect(screen.queryByTestId("storage-badge-3")).toBeNull();
    expect(screen.getByTestId("permissions-badge-1").textContent).toBe(
      "3 scopes",
    );
    expect(screen.getByTestId("permissions-badge-2").textContent).toBe(
      "1 scope",
    );
    expect(screen.getByTestId("permissions-badge-3").textContent).toBe(
      "Isolated",
    );
    expect(screen.getByText("*/5 * * * *")).toBeTruthy();
    expect(screen.getByText("elder-runs @ dal2-alpha")).toBeTruthy();
  });

  it("creates a new function via the Create Function modal", async () => {
    vi.mocked(api.listRunFunctions).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      per_page: 50,
    });
    vi.mocked(api.createRunFunction).mockResolvedValue({
      id: 42,
      name: "sync-dns-records",
      description: "Syncs DNS records",
      runtime: "python",
      code: "def handler(input_data: dict) -> dict:\n    return {}\n",
      timeout_seconds: 45,
      memory_mb: 512,
      schedule_cron: "0 * * * *",
      execution_namespace: "elder-runs",
      worker_cluster: "prod-west",
    });

    renderRunsList();

    await waitFor(() => {
      expect(screen.getByText("New CodeRun")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("New CodeRun"));

    expect(screen.getByRole("dialog")).toBeTruthy();

    const timeoutInput = screen.getByLabelText(
      "Timeout (seconds)",
    ) as HTMLInputElement;
    expect(timeoutInput.getAttribute("max")).toBe("300");
    expect(screen.getByText("Max 300s (5 min)")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "sync-dns-records" },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Syncs DNS records" },
    });
    fireEvent.change(screen.getByLabelText("Runtime"), {
      target: { value: "python" },
    });
    fireEvent.change(timeoutInput, {
      target: { value: "45" },
    });
    fireEvent.change(screen.getByLabelText("Memory (MB)"), {
      target: { value: "512" },
    });
    fireEvent.change(screen.getByLabelText("Schedule (cron)"), {
      target: { value: "0 * * * *" },
    });
    fireEvent.change(screen.getByLabelText("Worker Cluster"), {
      target: { value: "prod-west" },
    });

    const codeInput = screen.getByLabelText("Code") as HTMLTextAreaElement;
    expect(codeInput.value).toContain("def handler");

    // Grant scopes and egress domains in the permissions editor
    fireEvent.click(screen.getByLabelText(/Write Entities/i));
    fireEvent.click(screen.getByLabelText(/Trigger Integration Sync/i));
    fireEvent.change(screen.getByLabelText(/Allowed External Egress Domains/i), {
      target: { value: "api.github.com, hooks.slack.com" },
    });

    const submitButtons = screen.getAllByRole("button", {
      name: "Create CodeRun",
    });
    fireEvent.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => {
      expect(api.createRunFunction).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "sync-dns-records",
          description: "Syncs DNS records",
          runtime: "python",
          timeout_seconds: 45,
          memory_mb: 512,
          schedule_cron: "0 * * * *",
          execution_namespace: "elder-runs",
          worker_cluster: "prod-west",
          granted_scopes: ["entities:write", "integrations:sync"],
          allowed_egress_domains: ["api.github.com", "hooks.slack.com"],
        }),
      );
    });
  });

  it("invokes Run Now for a function card without navigating", async () => {
    vi.mocked(api.listRunFunctions).mockResolvedValue({
      data: [SAMPLE_FUNCTIONS[0]],
      total: 1,
      page: 1,
      per_page: 50,
    });
    vi.mocked(api.invokeRunFunction).mockResolvedValue({
      id: 99,
      execution_id: "exec-12345678",
      function_id: 1,
      status: "queued",
      trigger_type: "manual",
      created_at: "2026-11-23T10:00:00Z",
    });

    renderRunsList();

    await waitFor(() => {
      expect(screen.getByText("enrich-alerts")).toBeTruthy();
    });

    const runButton = screen.getByRole("button", {
      name: "Run Now enrich-alerts",
    });
    fireEvent.click(runButton);

    await waitFor(() => {
      expect(api.invokeRunFunction).toHaveBeenCalledWith(1, {});
    });

    expect(screen.queryByText("Run Detail Page")).toBeNull();
  });

  it("navigates to /runs/:id when clicking the stretched-link card", async () => {
    vi.mocked(api.listRunFunctions).mockResolvedValue({
      data: [SAMPLE_FUNCTIONS[0]],
      total: 1,
      page: 1,
      per_page: 50,
    });

    renderRunsList();

    await waitFor(() => {
      expect(screen.getByText("enrich-alerts")).toBeTruthy();
    });

    const cardLink = screen.getByRole("link", { name: /enrich-alerts/i });
    expect(cardLink.getAttribute("href")).toBe("/runs/1");
    expect(cardLink.className).toContain("after:absolute");
    expect(cardLink.className).toContain("after:inset-0");

    fireEvent.click(cardLink);

    await waitFor(() => {
      expect(screen.getByText("Run Detail Page")).toBeTruthy();
    });
  });

  it("filters by runtime pill when clicked", async () => {
    vi.mocked(api.listRunFunctions).mockResolvedValue({
      data: SAMPLE_FUNCTIONS,
      total: 3,
      page: 1,
      per_page: 50,
    });

    renderRunsList();

    await waitFor(() => {
      expect(screen.getByText("enrich-alerts")).toBeTruthy();
    });

    const rustFilterBtn = screen.getByRole("button", { name: "Rust" });
    fireEvent.click(rustFilterBtn);

    await waitFor(() => {
      expect(api.listRunFunctions).toHaveBeenCalledWith({
        runtime: "rust",
        page: 1,
        per_page: 50,
      });
    });
  });
});
