import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import RunDetail from "./RunDetail";
import api, { DEFAULT_RUN_PERMISSIONS_CATALOG } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    default: {
      getRunFunction: vi.fn(),
      updateRunFunction: vi.fn(),
      deleteRunFunction: vi.fn(),
      invokeRunFunction: vi.fn(),
      listRunExecutions: vi.fn(),
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

function renderRunDetail(initialEntry = "/runs/7") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/runs" element={<div>Runs List Page</div>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("RunDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getRunPermissionsCatalog).mockResolvedValue(
      DEFAULT_RUN_PERMISSIONS_CATALOG,
    );
    vi.mocked(api.getRunFunction).mockResolvedValue({
      id: 7,
      name: "enrich-alerts",
      description: "Enriches alert payloads",
      runtime: "nodejs",
      code: "export async function handler(i) { return i; }",
      timeout_seconds: 30,
      memory_mb: 256,
      schedule_cron: "*/10 * * * *",
      execution_namespace: "elder-runs",
      worker_cluster: "dal2-alpha",
    });
    vi.mocked(api.listRunExecutions).mockResolvedValue({
      data: [
        {
          id: 101,
          execution_id: "exec-abc-123",
          function_id: 7,
          status: "completed",
          trigger_type: "manual",
          exit_code: 0,
          duration_ms: 142,
          stdout: "hello from container",
          stderr: "",
          output_data: { ok: true },
          created_at: "2026-11-23T12:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      per_page: 25,
    });
  });

  it("loads function details, saves edits, triggers test run, and expands execution logs", async () => {
    vi.mocked(api.updateRunFunction).mockResolvedValue({
      id: 7,
      name: "enrich-alerts-v2",
      description: "Enriches alert payloads",
      runtime: "nodejs",
      code: "export async function handler(i) { return i; }",
      timeout_seconds: 60,
      memory_mb: 256,
      schedule_cron: "*/10 * * * *",
      execution_namespace: "elder-runs",
      worker_cluster: "dal2-alpha",
    });
    vi.mocked(api.invokeRunFunction).mockResolvedValue({
      id: 102,
      execution_id: "exec-new-456",
      function_id: 7,
      status: "completed",
      trigger_type: "manual",
      created_at: "2026-11-23T12:01:00Z",
    });

    renderRunDetail();

    await waitFor(() => {
      expect(screen.getByText("enrich-alerts")).toBeTruthy();
      expect(screen.getByText("exec-abc-123")).toBeTruthy();
    });

    expect(screen.getByText("Back to CodeRuns")).toBeTruthy();
    const timeoutInput = screen.getByLabelText(
      "Timeout (seconds)",
    ) as HTMLInputElement;
    expect(timeoutInput.getAttribute("max")).toBe("300");
    expect(screen.getByText("Max 300s (5 min)")).toBeTruthy();

    // Edit and save
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "enrich-alerts-v2" },
    });
    fireEvent.change(timeoutInput, {
      target: { value: "60" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(api.updateRunFunction).toHaveBeenCalledWith(
        7,
        expect.objectContaining({
          name: "enrich-alerts-v2",
          timeout_seconds: 60,
        }),
      );
    });

    // Test run with custom JSON payload
    fireEvent.change(screen.getByLabelText("JSON Input Payload"), {
      target: { value: '{"env":"prod"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run CodeRun" }));

    await waitFor(() => {
      expect(api.invokeRunFunction).toHaveBeenCalledWith(7, { env: "prod" });
    });

    // Expand execution logs and check View Execution Details link
    const detailLink = screen.getByTestId("view-execution-details-exec-abc-123");
    expect(detailLink.getAttribute("href")).toBe("/runs/executions/exec-abc-123");
    fireEvent.click(screen.getByRole("button", { name: /exec-abc-123/i }));
    await waitFor(() => {
      expect(screen.getByText("hello from container")).toBeTruthy();
      expect(screen.getByText(/"ok": true/)).toBeTruthy();
    });
  });

  it("renders S3 artifact storage metadata with storage key, truncated SHA-256 tooltip, and copy button", async () => {
    const fullSha =
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    vi.mocked(api.getRunFunction).mockResolvedValue({
      id: 7,
      name: "enrich-alerts",
      description: "Enriches alert payloads",
      runtime: "nodejs",
      code: "export async function handler(i) { return i; }",
      storage_backend: "s3",
      storage_key: "tenants/acme/runs/fn-7/artifact.tar.gz",
      code_sha256: fullSha,
      timeout_seconds: 30,
      memory_mb: 256,
      execution_namespace: "elder-runs",
    });

    renderRunDetail();

    await waitFor(() => {
      expect(screen.getByTestId("artifact-storage-section")).toBeTruthy();
    });

    expect(screen.getByTestId("artifact-storage-backend").textContent).toBe(
      "S3 Bucket",
    );
    expect(screen.getByTestId("artifact-storage-key").textContent).toBe(
      "tenants/acme/runs/fn-7/artifact.tar.gz",
    );

    const shaEl = screen.getByTestId("artifact-code-sha256");
    expect(shaEl.textContent).toBe("e3b0c44298fc1c14…");
    expect(shaEl.getAttribute("title")).toBe(fullSha);

    fireEvent.click(screen.getByRole("button", { name: "Copy SHA-256" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(fullSha);
    });
  });

  it("renders Database artifact storage metadata without storage key when absent", async () => {
    vi.mocked(api.getRunFunction).mockResolvedValue({
      id: 7,
      name: "audit-inventory",
      description: "Validates asset inventory",
      runtime: "python",
      code: "def handler(d): return d",
      storage_backend: "database",
      storage_key: null,
      code_sha256:
        "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
      timeout_seconds: 60,
      memory_mb: 512,
      execution_namespace: "elder-runs",
    });

    renderRunDetail();

    await waitFor(() => {
      expect(screen.getByTestId("artifact-storage-section")).toBeTruthy();
    });

    expect(screen.getByTestId("artifact-storage-backend").textContent).toBe(
      "Database",
    );
    expect(screen.queryByTestId("artifact-storage-key")).toBeNull();

    const shaEl = screen.getByTestId("artifact-code-sha256");
    expect(shaEl.textContent).toBe("9f86d081884c7d65…");
    expect(shaEl.getAttribute("title")).toBe(
      "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    );
  });

  it("displays granted scope pills and persists updated permissions and egress domains", async () => {
    vi.mocked(api.getRunFunction).mockResolvedValue({
      id: 7,
      name: "fleetdm-sync",
      description: "Syncs FleetDM assets",
      runtime: "python",
      code: "import elder\ndef handler(d): return d",
      timeout_seconds: 30,
      memory_mb: 256,
      execution_namespace: "elder-runs",
      granted_scopes: ["entities:read", "integrations:sync"],
      allowed_egress_domains: ["fleet.internal.example"],
    });
    vi.mocked(api.updateRunFunction).mockResolvedValue({
      id: 7,
      name: "fleetdm-sync",
      description: "Syncs FleetDM assets",
      runtime: "python",
      code: "import elder\ndef handler(d): return d",
      timeout_seconds: 30,
      memory_mb: 256,
      execution_namespace: "elder-runs",
      granted_scopes: ["entities:read", "integrations:sync", "entities:write"],
      allowed_egress_domains: [
        "fleet.internal.example",
        "hooks.slack.com",
      ],
    });

    renderRunDetail();

    await waitFor(() => {
      expect(screen.getByTestId("granted-scope-pill-entities:read")).toBeTruthy();
      expect(
        screen.getByTestId("granted-scope-pill-integrations:sync"),
      ).toBeTruthy();
      expect(screen.getByTestId("granted-egress-domains-pill")).toBeTruthy();
    });

    // Add entities:write scope and a second egress domain, then save
    fireEvent.click(screen.getByLabelText(/Write Entities/i));
    fireEvent.change(screen.getByLabelText(/Allowed External Egress Domains/i), {
      target: { value: "fleet.internal.example, hooks.slack.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(api.updateRunFunction).toHaveBeenCalledWith(
        7,
        expect.objectContaining({
          granted_scopes: [
            "entities:read",
            "integrations:sync",
            "entities:write",
          ],
          allowed_egress_domains: [
            "fleet.internal.example",
            "hooks.slack.com",
          ],
        }),
      );
    });
  });

  it("displays Isolated pill when no scopes are granted", async () => {
    renderRunDetail();

    await waitFor(() => {
      expect(screen.getByTestId("granted-scope-isolated")).toBeTruthy();
    });
    expect(screen.getByTestId("granted-scope-isolated").textContent).toBe(
      "Isolated",
    );
  });
});
