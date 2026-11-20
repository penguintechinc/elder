import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import Search from "./Search";
import api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    getLabels: vi.fn().mockResolvedValue({ items: [] }),
    getOrganizations: vi.fn().mockResolvedValue({ items: [] }),
    getEntities: vi.fn().mockResolvedValue({ items: [] }),
    getIssues: vi.fn().mockResolvedValue({ items: [] }),
    getIdentities: vi.fn().mockResolvedValue({ items: [] }),
    getAIResearcherConfig: vi.fn(),
    runAIResearch: vi.fn(),
  },
}));

function renderSearchPage(initialEntry = "/search") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/search" element={<Search />} />
          <Route
            path="/entities/:id"
            element={<div>Entity Detail Destination</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Search Page - AI Researcher Mode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getLabels).mockResolvedValue({ items: [] });
    vi.mocked(api.getAIResearcherConfig).mockResolvedValue({
      default_model: "gemma4:e4b",
      available_models: ["gemma4:e4b", "llama3.2:latest"],
      mcp_tool_count: 87,
      enabled_modules: ["infrastructure", "sbom"],
      max_steps: 6,
      ollama_reachable: true,
      ollama_error: null,
    });
  });

  it("toggles between Keyword Search and AI Researcher with gemma4:e4b default and live tool badge", async () => {
    renderSearchPage();

    expect(screen.getByPlaceholderText("Search by keyword...")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: /AI Researcher/i }));

    await waitFor(() => {
      expect(screen.getByText("87 MCP tools enabled")).toBeTruthy();
    });

    expect(screen.getByText("Ollama connected")).toBeTruthy();
    const modelSelect = screen.getByLabelText("Model") as HTMLSelectElement;
    expect(modelSelect.value).toBe("gemma4:e4b");

    fireEvent.click(screen.getByRole("tab", { name: /Keyword Search/i }));
    expect(screen.getByPlaceholderText("Search by keyword...")).toBeTruthy();
  });

  it("submits research query, renders structured answer, expandable trace, clickable citations, and follow-up history", async () => {
    vi.mocked(api.runAIResearch)
      .mockResolvedValueOnce({
        answer:
          "## Security Summary\n- Entity **prod-db-01** has `1` critical finding.",
        model: "gemma4:e4b",
        steps: [
          {
            step: 1,
            tool: "search_entities",
            endpoint: "/api/v1/entities",
            arguments: { name: "prod-db-01" },
            status: "ok",
            result_preview: '{"entities":[{"id":42,"name":"prod-db-01"}]}',
          },
        ],
        citations: [
          {
            resource_type: "entity",
            id: 42,
            name: "prod-db-01",
            url: "/entities/42",
          },
        ],
        tool_count: 87,
        enabled_modules: ["infrastructure", "sbom"],
      })
      .mockResolvedValueOnce({
        answer: "Follow-up check completed for `prod-db-01`.",
        model: "gemma4:e4b",
        steps: [],
        citations: [],
        tool_count: 87,
        enabled_modules: ["infrastructure", "sbom"],
      });

    renderSearchPage("/search?mode=ai");

    await waitFor(() => {
      expect(screen.getByText("87 MCP tools enabled")).toBeTruthy();
    });

    const chip = screen.getByRole("button", {
      name: /Which critical vulnerabilities affect our services/i,
    });
    fireEvent.click(chip);

    const textarea = screen.getByLabelText(
      /Ask Elder AI Researcher/i,
    ) as HTMLTextAreaElement;
    expect(textarea.value).toContain("critical vulnerabilities");

    fireEvent.click(screen.getByRole("button", { name: /Run Research/i }));

    await waitFor(() => {
      expect(screen.getByText("Security Summary")).toBeTruthy();
    });

    expect(vi.mocked(api.runAIResearch)).toHaveBeenCalledWith({
      query:
        "Which critical vulnerabilities affect our services and which entities depend on them?",
      model: "gemma4:e4b",
      history: [],
    });

    const traceBtn = screen.getByRole("button", { name: /MCP tool trace/i });
    expect(traceBtn.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(traceBtn);
    expect(traceBtn.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("search_entities")).toBeTruthy();
    expect(screen.getByText("/api/v1/entities")).toBeTruthy();

    fireEvent.change(textarea, {
      target: { value: "What network is it in?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask Follow-up/i }));

    await waitFor(() => {
      expect(screen.getByText(/Follow-up check completed/i)).toBeTruthy();
    });

    expect(vi.mocked(api.runAIResearch)).toHaveBeenLastCalledWith({
      query: "What network is it in?",
      model: "gemma4:e4b",
      history: [
        {
          role: "user",
          content:
            "Which critical vulnerabilities affect our services and which entities depend on them?",
        },
        {
          role: "assistant",
          content:
            "## Security Summary\n- Entity **prod-db-01** has `1` critical finding.",
        },
      ],
    });

    const citationButton = screen.getByRole("button", { name: /prod-db-01/i });
    fireEvent.click(citationButton);
    expect(screen.getByText("Entity Detail Destination")).toBeTruthy();
  });

  it("displays unreachable banner from config and fails loud on 503 query errors", async () => {
    vi.mocked(api.getAIResearcherConfig).mockResolvedValue({
      default_model: "gemma4:e4b",
      available_models: ["gemma4:e4b"],
      mcp_tool_count: 87,
      enabled_modules: ["infrastructure"],
      max_steps: 6,
      ollama_reachable: false,
      ollama_error: "Connection refused to http://ollama.waddleai:11434",
    });

    vi.mocked(api.runAIResearch).mockRejectedValue({
      response: {
        status: 503,
        data: {
          error: "OLLAMA_UNAVAILABLE",
          message: "Unable to reach Ollama at http://ollama.waddleai:11434",
        },
      },
    });

    renderSearchPage("/search?mode=ai");

    await waitFor(() => {
      expect(screen.getByText("Ollama service is unreachable")).toBeTruthy();
    });
    expect(screen.getByText("Ollama unreachable")).toBeTruthy();
    expect(
      screen.getByText("Connection refused to http://ollama.waddleai:11434"),
    ).toBeTruthy();

    const textarea = screen.getByLabelText(/Ask Elder AI Researcher/i);
    fireEvent.change(textarea, { target: { value: "List databases" } });
    fireEvent.click(screen.getByRole("button", { name: /Run Research/i }));

    await waitFor(() => {
      expect(screen.getByText("Research failed")).toBeTruthy();
    });
    expect(
      screen.getByText(
        "Unable to reach Ollama at http://ollama.waddleai:11434",
      ),
    ).toBeTruthy();
  });
});
