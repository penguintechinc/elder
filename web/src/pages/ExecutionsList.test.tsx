import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import ExecutionsList from "./ExecutionsList";
import api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    listAllStreamExecutions: vi.fn(),
  },
}));

const createQueryClient = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

function renderExecutionsList() {
  const queryClient = createQueryClient();
  render(
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <ExecutionsList />
      </QueryClientProvider>
    </BrowserRouter>,
  );
}

describe("ExecutionsList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders executions from the mocked listAllStreamExecutions endpoint with source badges and counts", async () => {
    vi.mocked(api.listAllStreamExecutions).mockResolvedValue({
      data: [
        {
          id: 1,
          execution_id: "550e8400-e29b-41d4-a716-446655440000",
          source: "playbook",
          stream_id: 10,
          stream_name: "Test Stream",
          status: "success",
          trigger_type: "manual",
          created_at: "2026-08-07T12:00:00Z",
        },
        {
          id: 2,
          execution_id: "550e8400-e29b-41d4-a716-446655440001",
          source: "coderun",
          function_id: 42,
          name: "enrich-alerts",
          runtime: "python",
          exit_code: 0,
          status: "completed",
          created_at: "2026-08-07T11:30:00Z",
        },
        {
          id: 3,
          execution_id: "550e8400-e29b-41d4-a716-446655440002",
          source: "webhook",
          webhook_id: 7,
          name: "PagerDuty Alerts",
          event_type: "incident.triggered",
          http_status: 202,
          status: "success",
          created_at: "2026-08-07T11:00:00Z",
        },
      ],
      counts_by_source: {
        playbook: 1,
        coderun: 1,
        webhook: 1,
      },
      total: 3,
      page: 1,
      per_page: 50,
    });

    renderExecutionsList();

    await waitFor(() => {
      expect(screen.getByText("Test Stream")).toBeTruthy();
      expect(screen.getByText("enrich-alerts")).toBeTruthy();
      expect(screen.getByText("PagerDuty Alerts")).toBeTruthy();
    });

    expect(vi.mocked(api.listAllStreamExecutions)).toHaveBeenCalledWith({
      page: 1,
      per_page: 50,
      source: "all",
    });

    expect(screen.getByTestId("source-count-all").textContent).toBe("3");
    expect(screen.getByTestId("source-count-playbook").textContent).toBe("1");
    expect(screen.getByTestId("source-count-coderun").textContent).toBe("1");
    expect(screen.getByTestId("source-count-webhook").textContent).toBe("1");

    const badges = screen.getAllByTestId("source-badge").map((el) => el.textContent);
    expect(badges).toEqual(["Playbook", "CodeRun", "Webhook"]);

    expect(screen.getByTestId("execution-runtime").textContent).toBe("python");
    expect(screen.getByTestId("execution-http-status").textContent).toBe("HTTP 202");
    expect(screen.getByTestId("execution-event-type").textContent).toBe(
      "incident.triggered",
    );

    const detailLinks = screen.getAllByTestId("view-details-btn");
    expect(detailLinks[0].getAttribute("href")).toBe(
      "/streams/executions/550e8400-e29b-41d4-a716-446655440000",
    );
    expect(detailLinks[1].getAttribute("href")).toBe(
      "/runs/executions/550e8400-e29b-41d4-a716-446655440001",
    );
    expect(detailLinks[2].getAttribute("href")).toBe("/webhooks");
  });

  it("filters by source pill, status dropdown, and search input", async () => {
    vi.mocked(api.listAllStreamExecutions).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      per_page: 50,
    });

    renderExecutionsList();

    await waitFor(() => {
      expect(api.listAllStreamExecutions).toHaveBeenCalledWith({
        page: 1,
        per_page: 50,
        source: "all",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: /^CodeRuns/i }));
    await waitFor(() => {
      expect(api.listAllStreamExecutions).toHaveBeenCalledWith({
        page: 1,
        per_page: 50,
        source: "coderun",
      });
    });

    fireEvent.change(screen.getByLabelText("Filter by status"), {
      target: { value: "failed" },
    });
    await waitFor(() => {
      expect(api.listAllStreamExecutions).toHaveBeenCalledWith({
        page: 1,
        per_page: 50,
        source: "coderun",
        status: "failed",
      });
    });

    fireEvent.change(screen.getByLabelText("Search executions"), {
      target: { value: "enrich" },
    });
    await waitFor(() => {
      expect(api.listAllStreamExecutions).toHaveBeenCalledWith({
        page: 1,
        per_page: 50,
        source: "coderun",
        status: "failed",
        search: "enrich",
      });
    });
  });

  it("shows a clean empty state when data is empty", async () => {
    vi.mocked(api.listAllStreamExecutions).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      per_page: 50,
    });

    renderExecutionsList();

    await waitFor(() => {
      expect(screen.getByText("No executions")).toBeTruthy();
      expect(
        screen.getByText(
          "Executions will appear here once playbooks, CodeRuns, or webhooks are triggered",
        ),
      ).toBeTruthy();
    });

    expect(screen.queryByText(/Failed to load executions/)).toBeNull();
  });

  it("renders legacy execution rows without source field as Playbook", async () => {
    vi.mocked(api.listAllStreamExecutions).mockResolvedValue({
      data: [
        {
          id: 1,
          execution_id: "550e8400-e29b-41d4-a716-446655440000",
          stream_id: 10,
          stream_name: "Test Stream",
          status: "success",
          trigger_type: "manual",
          created_at: "2026-08-07T12:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      per_page: 50,
    });

    renderExecutionsList();

    await waitFor(() => {
      expect(screen.getByTestId("execution-id").textContent).toBe("550e8400");
      expect(screen.getByTestId("stream-name").textContent).toBe("Test Stream");
      expect(screen.getByTestId("source-badge").textContent).toBe("Playbook");
    });
  });
});
