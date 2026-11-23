import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import RunExecutionDetail from "./RunExecutionDetail";
import api, { type RunExecution } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    getRunExecution: vi.fn(),
  },
}));

vi.mock("react-hot-toast", () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

interface ExtendedRunExecution extends RunExecution {
  worker_id?: string | null;
  stdout_log?: string | null;
  stderr_log?: string | null;
  execution_namespace?: string | null;
  storage_backend?: "database" | "s3" | null;
  storage_key?: string | null;
  code_sha256?: string | null;
}

function renderRunExecutionDetail(executionId = "exec-1234-5678") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return render(
    <MemoryRouter initialEntries={[`/runs/executions/${executionId}`]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route
            path="/runs/executions/:executionId"
            element={<RunExecutionDetail />}
          />
          <Route path="/runs/:id" element={<div>CodeRun Detail Page</div>} />
          <Route
            path="/streams/executions"
            element={<div>Executions List Page</div>}
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("RunExecutionDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders 4-step timeline, live auto-poll indicator, and Pause/Resume toggle for in-progress execution", async () => {
    const runningExec: ExtendedRunExecution = {
      id: 101,
      execution_id: "exec-running-001",
      function_id: 42,
      function_name: "enrich-alerts",
      runtime: "python",
      status: "running",
      trigger_type: "manual",
      worker_id: "svc-runworker-0",
      execution_namespace: "elder-runs",
      input_data: { alert_id: "al-99" },
      stdout_log: "Fetching alert metadata...\nStep 1 complete",
      stderr_log: "",
      started_at: "2026-11-23T21:00:02Z",
      created_at: "2026-11-23T21:00:00Z",
    };

    vi.mocked(api.getRunExecution).mockResolvedValue(runningExec);

    renderRunExecutionDetail("exec-running-001");

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /enrich-alerts/i }),
      ).toBeTruthy();
    });

    expect(api.getRunExecution).toHaveBeenCalledWith("exec-running-001");

    // Live polling indicator and Pause/Resume toggle
    expect(screen.getByTestId("live-poll-badge").textContent).toContain(
      "Auto-refreshing every 10s",
    );
    const toggleBtn = screen.getByTestId("toggle-poll-btn");
    expect(toggleBtn.textContent).toContain("Pause");

    fireEvent.click(toggleBtn);
    expect(screen.getByTestId("live-poll-badge").textContent).toContain(
      "Auto-refresh paused",
    );
    expect(screen.getByTestId("toggle-poll-btn").textContent).toContain(
      "Resume",
    );

    // 4-step timeline states
    expect(
      screen.getByTestId("timeline-step-queued").getAttribute("data-step-state"),
    ).toBe("completed");
    expect(
      screen
        .getByTestId("timeline-step-claimed")
        .getAttribute("data-step-state"),
    ).toBe("completed");
    expect(
      screen
        .getByTestId("timeline-step-running")
        .getAttribute("data-step-state"),
    ).toBe("active");
    expect(
      screen.getByTestId("timeline-step-final").getAttribute("data-step-state"),
    ).toBe("pending");

    // Live stdout and manual refresh
    expect(screen.getByTestId("execution-stdout").textContent).toContain(
      "Fetching alert metadata...",
    );

    fireEvent.click(screen.getByTestId("manual-refresh-btn"));
    await waitFor(() => {
      expect(api.getRunExecution).toHaveBeenCalledTimes(2);
    });
  });

  it("renders completed execution logs, JSON payloads, and full artifact metadata without auto-poll badge", async () => {
    const fullSha =
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    const completedExec: ExtendedRunExecution = {
      id: 102,
      execution_id: "exec-done-002",
      function_id: 7,
      function_name: "sync-inventory",
      runtime: "nodejs",
      status: "completed",
      trigger_type: "schedule",
      worker_id: "svc-runworker-2",
      execution_namespace: "elder-runs-prod",
      storage_backend: "s3",
      storage_key: "tenants/acme/runs/fn-7/artifact.tar.gz",
      code_sha256: fullSha,
      input_data: { region: "us-central" },
      output_data: { synced: 12, ok: true },
      stdout_log: "Synced 12 records",
      stderr_log: "",
      exit_code: 0,
      duration_ms: 845,
      started_at: "2026-11-23T21:00:01Z",
      completed_at: "2026-11-23T21:00:02Z",
      created_at: "2026-11-23T21:00:00Z",
    };

    vi.mocked(api.getRunExecution).mockResolvedValue(completedExec);

    renderRunExecutionDetail("exec-done-002");

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /sync-inventory/i }),
      ).toBeTruthy();
    });

    // No live auto-poll indicator on completed execution
    expect(screen.queryByTestId("live-poll-badge")).toBeNull();

    // All 4 steps completed
    expect(
      screen.getByTestId("timeline-step-queued").getAttribute("data-step-state"),
    ).toBe("completed");
    expect(
      screen
        .getByTestId("timeline-step-claimed")
        .getAttribute("data-step-state"),
    ).toBe("completed");
    expect(
      screen
        .getByTestId("timeline-step-running")
        .getAttribute("data-step-state"),
    ).toBe("completed");
    expect(
      screen.getByTestId("timeline-step-final").getAttribute("data-step-state"),
    ).toBe("completed");

    // Logs and JSON payloads
    expect(screen.getByTestId("execution-stdout").textContent).toContain(
      "Synced 12 records",
    );
    expect(screen.getByTestId("execution-input-payload").textContent).toContain(
      '"region": "us-central"',
    );
    expect(screen.getByTestId("execution-output-data").textContent).toContain(
      '"synced": 12',
    );

    // Artifact and execution metadata
    const fnLink = screen.getByTestId("metadata-function-link");
    expect(fnLink.getAttribute("href")).toBe("/runs/7");
    expect(screen.getByTestId("metadata-runtime").textContent).toBe("Node.js");
    expect(screen.getByTestId("metadata-exit-code").textContent).toBe("0");
    expect(screen.getByTestId("metadata-worker-id").textContent).toBe(
      "svc-runworker-2",
    );
    expect(screen.getByTestId("metadata-namespace").textContent).toBe(
      "elder-runs-prod",
    );
    expect(screen.getByTestId("metadata-storage-backend").textContent).toBe(
      "S3 Bucket",
    );
    expect(screen.getByTestId("metadata-storage-key").textContent).toBe(
      "tenants/acme/runs/fn-7/artifact.tar.gz",
    );
    expect(screen.getByTestId("metadata-code-sha256").textContent).toBe(
      fullSha,
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy SHA-256" }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(fullSha);
    });
  });

  it("renders error banner, stderr, and failed timeline state for a failed execution", async () => {
    const failedExec: ExtendedRunExecution = {
      id: 103,
      execution_id: "exec-fail-003",
      function_id: 9,
      function_name: "broken-runner",
      runtime: "rust",
      status: "failed",
      trigger_type: "manual",
      worker_id: "svc-runworker-1",
      stdout: "starting...\n",
      stderr: "thread 'main' panicked at src/main.rs:14:9: missing key",
      exit_code: 101,
      error_message: "Container exited with non-zero status (101)",
      duration_ms: 320,
      started_at: "2026-11-23T21:05:01Z",
      completed_at: "2026-11-23T21:05:02Z",
      created_at: "2026-11-23T21:05:00Z",
    };

    vi.mocked(api.getRunExecution).mockResolvedValue(failedExec);

    renderRunExecutionDetail("exec-fail-003");

    await waitFor(() => {
      expect(screen.getByTestId("execution-error-banner")).toBeTruthy();
    });

    expect(screen.getByTestId("execution-error-banner").textContent).toContain(
      "Container exited with non-zero status (101)",
    );
    expect(screen.getByTestId("execution-stderr").textContent).toContain(
      "panicked at src/main.rs:14:9",
    );
    expect(screen.getByTestId("metadata-exit-code").textContent).toBe("101");

    const finalStep = screen.getByTestId("timeline-step-final");
    expect(finalStep.getAttribute("data-step-state")).toBe("failed");
    expect(finalStep.textContent).toContain("Failed");
  });
});
