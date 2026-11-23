import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import toast from "react-hot-toast";
import AdminWorkers from "./AdminWorkers";
import api, { type RemoteWorker } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    listRemoteWorkers: vi.fn(),
    getRemoteWorker: vi.fn(),
    onboardRemoteWorker: vi.fn(),
    updateRemoteWorker: vi.fn(),
    rotateRemoteWorkerToken: vi.fn(),
    offboardRemoteWorker: vi.fn(),
  },
}));

vi.mock("react-hot-toast", () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const MOCK_WORKERS: RemoteWorker[] = [
  {
    id: 1,
    name: "runworker-us-east-1",
    worker_type: "runworker",
    cluster: "us-east",
    status: "online",
    hostname: "rw-node-01.internal",
    version: "4.0.1",
    token_prefix: "eld_rw_a1b2",
    is_enabled: true,
    config: {
      poll_interval_seconds: 5,
      log_flush_interval_seconds: 2,
      max_concurrent: 8,
      runner_backend: "kata",
    },
    last_heartbeat_at: new Date(Date.now() - 25_000).toISOString(),
  },
  {
    id: 2,
    name: "scanner-dc-west-1",
    worker_type: "scanner",
    cluster: "dc-west",
    status: "stale",
    hostname: "scan-node-02.internal",
    version: "4.0.0",
    token_prefix: "eld_sc_c3d4",
    is_enabled: true,
    config: {
      poll_interval_seconds: 15,
      progress_interval_seconds: 5,
      max_concurrent: 4,
    },
    last_heartbeat_at: new Date(Date.now() - 5 * 60_000).toISOString(),
  },
  {
    id: 3,
    name: "runworker-eu-1",
    worker_type: "runworker",
    cluster: "eu-central",
    status: "offline",
    hostname: "rw-eu-01.internal",
    version: "3.9.9",
    token_prefix: "eld_rw_e5f6",
    is_enabled: false,
    config: {
      poll_interval_seconds: 10,
      log_flush_interval_seconds: 3,
      max_concurrent: 4,
      runner_backend: "docker",
    },
    last_heartbeat_at: null,
  },
  {
    id: 4,
    name: "scanner-legacy-1",
    worker_type: "scanner",
    cluster: "legacy",
    status: "revoked",
    hostname: "scan-old-01.internal",
    version: "3.8.0",
    token_prefix: "eld_sc_9999",
    is_enabled: false,
    config: {},
    last_heartbeat_at: null,
  },
];

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AdminWorkers />
    </QueryClientProvider>,
  );
}

describe("AdminWorkers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listRemoteWorkers).mockResolvedValue({
      workers: MOCK_WORKERS,
      total: MOCK_WORKERS.length,
    });

    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
      configurable: true,
    });
  });

  it("renders svc-runworker and svc-scanner instances with live status badges and summary counts", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("runworker-us-east-1")).toBeDefined();
    });

    expect(screen.getByText("scanner-dc-west-1")).toBeDefined();
    expect(screen.getByText("runworker-eu-1")).toBeDefined();
    expect(screen.getByText("scanner-legacy-1")).toBeDefined();

    // Live status badges
    expect(screen.getByTestId("worker-status-online").textContent).toContain(
      "Online",
    );
    expect(screen.getByTestId("worker-status-stale").textContent).toContain(
      "Stale",
    );
    expect(screen.getByTestId("worker-status-offline").textContent).toContain(
      "Offline",
    );
    expect(screen.getByTestId("worker-status-revoked").textContent).toContain(
      "Revoked",
    );

    // Worker type badges
    expect(screen.getAllByText("svc-runworker").length).toBeGreaterThanOrEqual(
      2,
    );
    expect(screen.getAllByText("svc-scanner").length).toBeGreaterThanOrEqual(2);

    // Summary cards
    expect(screen.getByTestId("summary-card-online").textContent).toContain(
      "1",
    );
    expect(screen.getByTestId("summary-card-stale").textContent).toContain("1");
    expect(screen.getByTestId("summary-card-offline").textContent).toContain(
      "1",
    );
    expect(screen.getByTestId("summary-card-revoked").textContent).toContain(
      "1",
    );

    // Masked token prefix and metadata
    expect(screen.getByText("eld_rw_a1b2••••••••")).toBeDefined();
    expect(screen.getByText("rw-node-01.internal")).toBeDefined();
    expect(screen.getByText("4.0.1")).toBeDefined();
  });

  it("filters workers by type (All, RunWorkers, Scanners) and by status", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("runworker-us-east-1")).toBeDefined();
    });

    // Filter to RunWorkers
    fireEvent.click(screen.getByTestId("type-filter-runworker"));
    expect(screen.getByText("runworker-us-east-1")).toBeDefined();
    expect(screen.getByText("runworker-eu-1")).toBeDefined();
    expect(screen.queryByText("scanner-dc-west-1")).toBeNull();
    expect(screen.queryByText("scanner-legacy-1")).toBeNull();

    // Filter to Scanners
    fireEvent.click(screen.getByTestId("type-filter-scanner"));
    expect(screen.getByText("scanner-dc-west-1")).toBeDefined();
    expect(screen.getByText("scanner-legacy-1")).toBeDefined();
    expect(screen.queryByText("runworker-us-east-1")).toBeNull();

    // Reset to All and filter by status dropdown
    fireEvent.click(screen.getByTestId("type-filter-all"));
    fireEvent.change(screen.getByLabelText("Status:"), {
      target: { value: "online" },
    });
    expect(screen.getByText("runworker-us-east-1")).toBeDefined();
    expect(screen.queryByText("scanner-dc-west-1")).toBeNull();
    expect(screen.queryByText("runworker-eu-1")).toBeNull();
  });

  it("onboards a worker and displays the one-time token with Docker and Helm snippets", async () => {
    const generatedToken = "test-onboard-token";
    vi.mocked(api.onboardRemoteWorker).mockResolvedValue({
      worker: {
        id: 99,
        name: "scanner-edge-1",
        worker_type: "scanner",
        cluster: "edge-west",
        status: "offline",
        token_prefix: "test-onb",
        is_enabled: true,
      },
      token: generatedToken,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("runworker-us-east-1")).toBeDefined();
    });

    fireEvent.click(screen.getByRole("button", { name: /Onboard Worker/i }));

    // Select svc-scanner
    fireEvent.click(
      screen.getByRole("button", { name: /svc-scanner/i }),
    );

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "scanner-edge-1" },
    });
    fireEvent.change(screen.getByLabelText("Cluster"), {
      target: { value: "edge-west" },
    });
    fireEvent.change(screen.getByLabelText("Poll Interval (seconds)"), {
      target: { value: "20" },
    });
    fireEvent.change(screen.getByLabelText("Progress Interval (seconds)"), {
      target: { value: "10" },
    });
    fireEvent.change(screen.getByLabelText("Max Concurrent"), {
      target: { value: "6" },
    });

    fireEvent.click(
      screen.getByRole("button", { name: /Create & Generate Token/i }),
    );

    await waitFor(() => {
      expect(api.onboardRemoteWorker).toHaveBeenCalledWith({
        name: "scanner-edge-1",
        worker_type: "scanner",
        cluster: "edge-west",
        config: {
          poll_interval_seconds: 20,
          progress_interval_seconds: 10,
          max_concurrent: 6,
        },
      });
    });

    // One-time token & warning shown
    const tokenInput = screen.getByTestId(
      "one-time-token-value",
    ) as HTMLInputElement;
    expect(tokenInput.value).toBe(generatedToken);
    expect(screen.getByText(/only shown once/i)).toBeDefined();

    // Docker snippet includes ELDER_INTERNAL_TOKEN and svc-scanner
    const snippetEl = screen.getByTestId("deployment-snippet");
    expect(snippetEl.textContent).toContain(`ELDER_INTERNAL_TOKEN="${generatedToken}"`);
    expect(snippetEl.textContent).toContain("svc-scanner");

    // Switch to Helm snippet
    fireEvent.click(screen.getByRole("button", { name: "Helm" }));
    expect(screen.getByTestId("deployment-snippet").textContent).toContain(
      "helm upgrade --install svc-scanner",
    );
    expect(screen.getByTestId("deployment-snippet").textContent).toContain(
      `internalToken="${generatedToken}"`,
    );

    // Copy token
    fireEvent.click(screen.getByTestId("copy-token-btn"));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        generatedToken,
      );
    });
  });

  it("configures an existing worker", async () => {
    vi.mocked(api.updateRemoteWorker).mockResolvedValue({
      ...MOCK_WORKERS[0],
      cluster: "us-east-2",
      is_enabled: false,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("runworker-us-east-1")).toBeDefined();
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Configure runworker-us-east-1" }),
    );

    fireEvent.change(screen.getByLabelText("Cluster"), {
      target: { value: "us-east-2" },
    });
    fireEvent.click(screen.getByLabelText("Enabled"));

    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(api.updateRemoteWorker).toHaveBeenCalledWith(1, {
        name: "runworker-us-east-1",
        cluster: "us-east-2",
        is_enabled: false,
        config: {
          poll_interval_seconds: 5,
          log_flush_interval_seconds: 2,
          max_concurrent: 8,
          runner_backend: "kata",
        },
      });
    });

    expect(toast.success).toHaveBeenCalledWith("Worker configuration updated");
  });

  it("rotates a worker token and displays the new one-time token", async () => {
    const newToken = "test-rotated-token";
    vi.mocked(api.rotateRemoteWorkerToken).mockResolvedValue({
      worker: { ...MOCK_WORKERS[0], token_prefix: "test-rot" },
      token: newToken,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("runworker-us-east-1")).toBeDefined();
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Rotate token for runworker-us-east-1",
      }),
    );

    fireEvent.click(screen.getByTestId("confirm-rotate-token-btn"));

    await waitFor(() => {
      expect(api.rotateRemoteWorkerToken).toHaveBeenCalledWith(1);
    });

    const tokenInput = screen.getByTestId(
      "one-time-token-value",
    ) as HTMLInputElement;
    expect(tokenInput.value).toBe(newToken);
    expect(toast.success).toHaveBeenCalledWith("Worker token rotated");
  });

  it("offboards a worker with confirmation and toast feedback", async () => {
    vi.mocked(api.offboardRemoteWorker).mockResolvedValue({
      message: "Worker offboarded",
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("scanner-dc-west-1")).toBeDefined();
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Offboard scanner-dc-west-1" }),
    );

    fireEvent.click(screen.getByTestId("confirm-offboard-btn"));

    await waitFor(() => {
      expect(api.offboardRemoteWorker).toHaveBeenCalledWith(2);
    });

    expect(toast.success).toHaveBeenCalledWith(
      "Worker offboarded successfully",
    );
  });

  it("refreshes workers when clicking the manual Refresh button", async () => {
    renderPage();

    await waitFor(() => {
      expect(api.listRemoteWorkers).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole("button", { name: "Refresh workers" }));

    await waitFor(() => {
      expect(api.listRemoteWorkers).toHaveBeenCalledTimes(2);
    });
  });
});
