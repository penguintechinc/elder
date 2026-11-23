import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ExecutionDetail from "./ExecutionDetail";
import api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    getExecutionById: vi.fn(),
  },
}));

describe("ExecutionDetail page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads execution details by executionId without bogus stream_id=0 and renders node logs", async () => {
    vi.mocked(api.getExecutionById).mockResolvedValue({
      data: {
        stream_id: 2,
        stream_name: "Alert Triage Playbook",
        execution: {
          execution_id: "7736deca-6892-4b60-98a9-f7f5eae586b5",
          playbook_id: 2,
          status: "completed",
          trigger_type: "manual",
          started_at: "2026-11-23T15:04:08Z",
          completed_at: "2026-11-23T15:04:09Z",
          duration_ms: 412,
        },
        node_logs: [
          {
            node_id: "node-1",
            node_type: "conditional_if_then",
            status: "success",
            duration_ms: 15,
            output_data: { matched: true },
          },
        ],
      },
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter
          initialEntries={[
            "/streams/executions/7736deca-6892-4b60-98a9-f7f5eae586b5",
          ]}
        >
          <Routes>
            <Route
              path="/streams/executions/:executionId"
              element={<ExecutionDetail />}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Alert Triage Playbook")).toBeTruthy();
    expect(api.getExecutionById).toHaveBeenCalledWith(
      "7736deca-6892-4b60-98a9-f7f5eae586b5",
    );
    expect(screen.getAllByText("Completed").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("node-1")).toBeTruthy();
    expect(screen.getByText("412 ms")).toBeTruthy();
    expect(screen.queryByTestId("live-poll-badge")).toBeNull();
  });

  it("shows live auto-poll indicator and Pause/Resume toggle for running playbook executions", async () => {
    vi.mocked(api.getExecutionById).mockResolvedValue({
      data: {
        stream_id: 5,
        stream_name: "Incident Containment",
        execution: {
          execution_id: "exec-running-playbook",
          playbook_id: 5,
          status: "running",
          trigger_type: "manual",
          started_at: "2026-11-23T15:10:00Z",
        },
        node_logs: [],
      },
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter
          initialEntries={["/streams/executions/exec-running-playbook"]}
        >
          <Routes>
            <Route
              path="/streams/executions/:executionId"
              element={<ExecutionDetail />}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Incident Containment")).toBeTruthy();
    expect(screen.getByTestId("live-poll-badge").textContent).toContain(
      "Auto-refreshing every 10s",
    );

    fireEvent.click(screen.getByTestId("toggle-poll-btn"));
    expect(screen.getByTestId("live-poll-badge").textContent).toContain(
      "Auto-refresh paused",
    );
    expect(screen.getByTestId("toggle-poll-btn").textContent).toContain(
      "Resume",
    );
  });
});
