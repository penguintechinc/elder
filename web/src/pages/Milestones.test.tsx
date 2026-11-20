import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Milestones from "./Milestones";
import api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    getMilestones: vi.fn(),
    getProjects: vi.fn().mockResolvedValue({ items: [] }),
    getOrganizations: vi.fn().mockResolvedValue({
      items: [{ id: 1, name: "Default Org" }],
    }),
    getMilestoneIssues: vi.fn(),
    createMilestone: vi.fn(),
    updateMilestone: vi.fn(),
    deleteMilestone: vi.fn(),
  },
}));

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

function renderMilestones() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Milestones />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Milestones", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getMilestones).mockResolvedValue({
      items: [
        {
          id: 7,
          title: "v4.0 Launch",
          description: "Release readiness",
          status: "open",
          organization_id: 1,
          created_at: "2026-11-01T00:00:00Z",
        },
      ],
    });
  });

  it("opens milestone issues modal when clicking the milestone card", async () => {
    vi.mocked(api.getMilestoneIssues).mockResolvedValue({
      issues: [
        {
          id: 42,
          title: "Fix ingress TLS",
          status: "open",
          priority: "high",
        },
      ],
    });

    renderMilestones();

    const cardTitle = await screen.findByText("v4.0 Launch");
    fireEvent.click(cardTitle);

    await waitFor(() => {
      expect(api.getMilestoneIssues).toHaveBeenCalledWith(7);
    });

    expect(await screen.findByText("Fix ingress TLS")).toBeTruthy();
    const issueLink = screen.getByRole("link", { name: /Fix ingress TLS/i });
    expect(issueLink.getAttribute("href")).toBe("/issues/42");
  });

  it("shows empty state when milestone has no linked issues", async () => {
    vi.mocked(api.getMilestoneIssues).mockResolvedValue({ issues: [] });

    renderMilestones();

    const cardTitle = await screen.findByText("v4.0 Launch");
    fireEvent.click(cardTitle);

    expect(
      await screen.findByText("No issues linked to this milestone yet."),
    ).toBeTruthy();
  });

  it("does not open issues modal when clicking Edit button", async () => {
    renderMilestones();

    const editBtn = await screen.findByRole("button", {
      name: "Edit v4.0 Launch",
    });
    fireEvent.click(editBtn);

    expect(api.getMilestoneIssues).not.toHaveBeenCalled();
    expect(screen.getByText("Edit Milestone")).toBeTruthy();
  });
});
