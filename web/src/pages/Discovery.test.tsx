import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Discovery from "./Discovery";
import api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    getDiscoveryJobs: vi.fn(),
    getDiscoveryJobHistory: vi.fn(),
    getOrganizations: vi.fn(),
    createDiscoveryJob: vi.fn(),
    runDiscoveryJob: vi.fn(),
    deleteDiscoveryJob: vi.fn(),
  },
}));

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

function renderDiscovery() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Discovery />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Discovery page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getDiscoveryJobs).mockResolvedValue({ jobs: [] });
    vi.mocked(api.getDiscoveryJobHistory).mockResolvedValue({
      history: [],
      runs: [],
    });
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [{ id: 1, name: "Default Org" }],
    });
  });

  it("focuses on scans and links integration configuration to /admin/integrations", async () => {
    renderDiscovery();

    expect(await screen.findByText("Discovery & Scans")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Integration Scan/i })).toBeNull();

    const manageLink = screen.getByRole("link", {
      name: /Manage Integrations/i,
    }) as HTMLAnchorElement;
    expect(manageLink.getAttribute("href")).toBe("/admin/integrations");
  });

  it("only offers scan types in the New Scan modal and submits scan creation", async () => {
    vi.mocked(api.createDiscoveryJob).mockResolvedValue({
      id: 7,
      name: "Office LAN Scan",
      provider: "network",
      config_json: { targets: ["10.0.0.0/24"] },
      schedule_interval: 0,
      enabled: true,
    });
    renderDiscovery();

    const newScanButtons = await screen.findAllByRole("button", {
      name: /New Scan/i,
    });
    fireEvent.click(newScanButtons[0]);

    expect(await screen.findByText("Create Scan")).toBeTruthy();
    expect(screen.getByRole("option", { name: "Network Scan" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "HTTP Screenshot" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Banner Grab" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /AWS/i })).toBeNull();
    expect(screen.queryByRole("option", { name: /FleetDM/i })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("Local Network Scan"), {
      target: { value: "Office LAN Scan" },
    });

    const selectEl = screen
      .getByRole("option", { name: "Network Scan" })
      .closest("select") as HTMLSelectElement;
    fireEvent.change(selectEl, { target: { value: "network" } });

    const orgSearchInput = screen.getByPlaceholderText("Select organization");
    fireEvent.focus(orgSearchInput);
    fireEvent.click(await screen.findByText("Default Org"));

    fireEvent.change(
      screen.getByPlaceholderText(/192\.168\.1\.0\/24/),
      { target: { value: "10.0.0.0/24\n10.0.1.5" } },
    );

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(api.createDiscoveryJob).toHaveBeenCalledWith({
        name: "Office LAN Scan",
        provider_type: "network",
        organization_id: 1,
        config: { targets: ["10.0.0.0/24", "10.0.1.5"] },
        schedule: "0",
        enabled: true,
      });
    });
  });

  it("separates scans and integration queries, allows running queries, and routes integration configuration to /admin/integrations", async () => {
    vi.mocked(api.getDiscoveryJobs).mockResolvedValue({
      jobs: [
        {
          id: 1,
          name: "DMZ Port Scan",
          provider: "network",
          config_json: { targets: ["192.168.1.0/24"] },
          schedule_interval: 3600,
          enabled: true,
        },
        {
          id: 2,
          name: "Prod AWS",
          provider: "aws",
          config_json: { region: "us-east-1" },
          schedule_interval: 3600,
          enabled: true,
        },
        {
          id: 3,
          name: "Corp FleetDM",
          provider: "fleetdm",
          config_json: {},
          schedule_interval: 1800,
          enabled: true,
        },
      ],
    });
    vi.mocked(api.runDiscoveryJob).mockResolvedValue({ status: "started" });
    vi.mocked(api.getDiscoveryJobHistory).mockResolvedValue({
      history: [
        {
          id: 101,
          job_id: 2,
          status: "completed",
          started_at: "2026-11-21T10:00:00Z",
          completed_at: "2026-11-21T10:00:12Z",
          entities_discovered: 14,
        },
      ],
      runs: [
        {
          id: 101,
          job_id: 2,
          status: "completed",
          started_at: "2026-11-21T10:00:00Z",
          completed_at: "2026-11-21T10:00:12Z",
          entities_discovered: 14,
        },
      ],
    });

    renderDiscovery();

    expect(await screen.findByText("DMZ Port Scan")).toBeTruthy();
    expect(screen.getByText("Prod AWS")).toBeTruthy();
    expect(screen.getByText("Corp FleetDM")).toBeTruthy();

    // Only the scan job has a delete button on Discovery; integration jobs link to Integrations
    expect(screen.getByLabelText("Delete DMZ Port Scan")).toBeTruthy();
    expect(screen.queryByLabelText("Delete Prod AWS")).toBeNull();
    expect(screen.queryByLabelText("Delete Corp FleetDM")).toBeNull();

    const configureLinks = screen.getAllByRole("link", {
      name: /Configure in Integrations/i,
    }) as HTMLAnchorElement[];
    expect(configureLinks.map((l) => l.getAttribute("href"))).toEqual([
      "/admin/integrations?tab=cloud",
      "/admin/integrations?tab=endpoints",
    ]);

    // Filter to Integration Queries tab
    fireEvent.click(screen.getByRole("button", { name: /Integration Queries/i }));
    expect(screen.queryByText("DMZ Port Scan")).toBeNull();
    expect(screen.getByText("Prod AWS")).toBeTruthy();
    expect(screen.getByText("Corp FleetDM")).toBeTruthy();

    // Trigger a discovery query against an integration
    const runQueryButtons = screen.getAllByRole("button", {
      name: /Run Query/i,
    });
    fireEvent.click(runQueryButtons[0]);
    await waitFor(() => {
      expect(api.runDiscoveryJob).toHaveBeenCalledWith(2);
    });

    // Select job to view its run history
    fireEvent.click(screen.getByText("Prod AWS"));
    await waitFor(() => {
      expect(api.getDiscoveryJobHistory).toHaveBeenCalledWith(2);
    });
    expect(await screen.findByText("14")).toBeTruthy();
  });
});
