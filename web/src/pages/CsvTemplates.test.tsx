import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import toast from "react-hot-toast";
import CsvTemplates from "./CsvTemplates";
import api, { type CsvImportJob } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    getCsvTemplates: vi.fn(),
    downloadCsvTemplate: vi.fn(),
    importCsvResource: vi.fn(),
    getCsvImportJobs: vi.fn(),
    getCsvImportJob: vi.fn(),
  },
}));

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

const MOCK_TEMPLATES = {
  max_rows: 1000,
  templates: [
    {
      resource: "services",
      label: "Services",
      max_rows: 1000,
      headers: ["name", "environment"],
      required: ["name"],
      rules: {
        name: "Unique service name",
        environment: "prod, staging, or dev",
      },
      example: ["payments-api", "prod"],
    },
  ],
};

function renderCsvTemplates() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <CsvTemplates />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CsvTemplates", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getCsvTemplates).mockResolvedValue(MOCK_TEMPLATES);
    vi.mocked(api.getCsvImportJobs).mockResolvedValue({ items: [], total: 0 });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders Recent Import Jobs table with statuses, truncated SHA-256, expandable errors, and Refresh", async () => {
    const jobs: CsvImportJob[] = [
      {
        job_id: "job-completed-1",
        resource: "services",
        status: "completed",
        filename: "services-prod.csv",
        storage_backend: "s3",
        file_sha256: "0123456789abcdef0123456789abcdef",
        total_rows: 20,
        imported_count: 18,
        skipped_count: 2,
        errors: [
          "Row 5 (name): duplicate service",
          { row: 12, field: "environment", message: "invalid value" },
        ],
      },
      {
        job_id: "job-failed-2",
        resource: "vulnerabilities",
        status: "failed",
        filename: null,
        storage_backend: "s3",
        file_sha256: "fedcba9876543210",
        total_rows: 5,
        imported_count: 0,
        skipped_count: 0,
        error_message: "S3 object checksum mismatch",
      },
    ];

    vi.mocked(api.getCsvImportJobs).mockResolvedValue({
      items: jobs,
      total: 2,
    });

    renderCsvTemplates();

    expect(await screen.findByText("Recent Import Jobs")).toBeTruthy();
    expect(await screen.findByText("services-prod.csv")).toBeTruthy();
    expect(screen.getByText("Pasted CSV")).toBeTruthy();
    expect(screen.getByText("Completed")).toBeTruthy();
    expect(screen.getByText("Failed")).toBeTruthy();
    expect(screen.getByText("18 imported")).toBeTruthy();
    expect(screen.getByText("2 skipped")).toBeTruthy();

    const truncatedSha = screen.getByText("0123456789ab…");
    expect(truncatedSha.getAttribute("title")).toBe(
      "0123456789abcdef0123456789abcdef",
    );

    // Errors are collapsed initially
    expect(screen.queryByText("Row 5 (name): duplicate service")).toBeNull();

    // Expand errors on first job
    const errorsToggle = screen.getByRole("button", { name: /2 errors/i });
    fireEvent.click(errorsToggle);
    expect(screen.getByText("Row 5 (name): duplicate service")).toBeTruthy();
    expect(screen.getByText("Row 12 (environment): invalid value")).toBeTruthy();

    // Collapse errors
    fireEvent.click(errorsToggle);
    expect(screen.queryByText("Row 5 (name): duplicate service")).toBeNull();

    // Manual Refresh button refetches jobs
    const initialCalls = vi.mocked(api.getCsvImportJobs).mock.calls.length;
    const refreshBtn = screen.getByRole("button", { name: /Refresh/i });
    fireEvent.click(refreshBtn);

    await waitFor(() => {
      expect(vi.mocked(api.getCsvImportJobs).mock.calls.length).toBeGreaterThan(
        initialCalls,
      );
    });
  });

  it("handles 404 from /import/jobs gracefully before backend rollout", async () => {
    vi.mocked(api.getCsvImportJobs).mockRejectedValue({
      response: { status: 404 },
    });

    renderCsvTemplates();

    expect(await screen.findByText("Services")).toBeTruthy();
    expect(await screen.findByText("No recent import jobs yet.")).toBeTruthy();
  });

  it("handles queued 202 Accepted CSV upload and auto-polls until completed", async () => {
    vi.mocked(api.importCsvResource).mockResolvedValue({
      job_id: "job-s3-999",
      status: "pending",
      resource: "services",
      storage_backend: "s3",
      storage_key: "csv-imports/job-s3-999.csv",
      file_sha256: "abcdef1234567890abcdef1234567890",
      total_rows: 12,
      message: "CSV import queued for background processing",
    });

    vi.mocked(api.getCsvImportJob)
      .mockResolvedValueOnce({
        job_id: "job-s3-999",
        resource: "services",
        status: "processing",
        storage_backend: "s3",
        file_sha256: "abcdef1234567890abcdef1234567890",
        total_rows: 12,
        imported_count: 6,
        skipped_count: 0,
        errors: [],
      })
      .mockResolvedValueOnce({
        job_id: "job-s3-999",
        resource: "services",
        status: "completed",
        storage_backend: "s3",
        file_sha256: "abcdef1234567890abcdef1234567890",
        total_rows: 12,
        imported_count: 10,
        skipped_count: 2,
        errors: ["Row 4 (name): duplicate service"],
      });

    renderCsvTemplates();

    const importBtn = await screen.findByRole("button", { name: /^Import$/i });
    fireEvent.click(importBtn);

    const textarea = await screen.findByLabelText(/Or Paste CSV Content/i);
    fireEvent.change(textarea, {
      target: { value: "name,environment\npayments-api,prod" },
    });

    vi.useFakeTimers({ shouldAdvanceTime: true });

    const submitBtn = screen.getByRole("button", {
      name: /Validate & Import/i,
    });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.importCsvResource).toHaveBeenCalledWith(
        "services",
        "name,environment\npayments-api,prod",
      );
    });

    expect(toast.success).toHaveBeenCalledWith(
      "CSV import queued for background processing",
    );

    const activeCard = await screen.findByTestId("active-import-job-card");
    expect(activeCard.textContent).toContain("job-s3-999");
    expect(activeCard.textContent).toContain("S3");
    expect(activeCard.textContent).toContain("abcdef123456…");

    // First poll returns "processing"
    await waitFor(() => {
      expect(api.getCsvImportJob).toHaveBeenCalledWith("job-s3-999");
      expect(activeCard.textContent).toContain("Processing");
    });

    // Advance timer to trigger next poll -> "completed"
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    await waitFor(() => {
      expect(api.getCsvImportJob).toHaveBeenCalledTimes(2);
      expect(activeCard.textContent).toContain("Completed");
      expect(activeCard.textContent).toContain("10 / 12");
      expect(activeCard.textContent).toContain("Row 4 (name): duplicate service");
    });

    // Dismiss active job banner
    const dismissBtn = screen.getByRole("button", {
      name: /Dismiss active import job/i,
    });
    fireEvent.click(dismissBtn);
    expect(screen.queryByTestId("active-import-job-card")).toBeNull();
  });

  it("still supports existing synchronous 201 CSV import responses", async () => {
    vi.mocked(api.importCsvResource).mockResolvedValue({
      resource: "services",
      imported: 5,
    });

    renderCsvTemplates();

    const importBtn = await screen.findByRole("button", { name: /^Import$/i });
    fireEvent.click(importBtn);

    const textarea = await screen.findByLabelText(/Or Paste CSV Content/i);
    fireEvent.change(textarea, {
      target: { value: "name,environment\nauth-api,prod" },
    });

    const submitBtn = screen.getByRole("button", {
      name: /Validate & Import/i,
    });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("Imported 5 row(s) successfully");
    });

    expect(screen.queryByTestId("active-import-job-card")).toBeNull();
    expect(api.getCsvImportJob).not.toHaveBeenCalled();
  });
});
