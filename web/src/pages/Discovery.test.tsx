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
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [{ id: 1, name: "Default Org" }],
    });
  });

  it("includes FleetDM Endpoint Discovery in the Integration Scan modal", async () => {
    renderDiscovery();

    const buttons = await screen.findAllByRole("button", {
      name: /Integration Scan/i,
    });
    fireEvent.click(buttons[0]);

    expect(
      await screen.findByText("Create Integration Discovery"),
    ).toBeTruthy();

    const fleetdmOption = screen.getByRole("option", {
      name: "FleetDM Endpoint Discovery",
    }) as HTMLOptionElement;
    expect(fleetdmOption).toBeTruthy();
    expect(fleetdmOption.value).toBe("fleetdm");
  });

  it("allows selecting FleetDM Endpoint Discovery and submitting the modal form", async () => {
    vi.mocked(api.createDiscoveryJob).mockResolvedValue({
      id: 42,
      name: "Prod FleetDM",
      provider: "fleetdm",
      config_json: {},
      schedule_interval: 3600,
      enabled: true,
    });
    renderDiscovery();

    const buttons = await screen.findAllByRole("button", {
      name: /Integration Scan/i,
    });
    fireEvent.click(buttons[0]);

    await screen.findByText("Create Integration Discovery");

    const nameInput = screen.getByPlaceholderText("AWS Production Discovery");
    fireEvent.change(nameInput, { target: { value: "Prod FleetDM" } });

    const selectEl = screen
      .getByRole("option", { name: "FleetDM Endpoint Discovery" })
      .closest("select") as HTMLSelectElement;
    fireEvent.change(selectEl, { target: { value: "fleetdm" } });

    const orgSearchInput = screen.getByPlaceholderText("Select organization");
    fireEvent.focus(orgSearchInput);
    const orgOption = await screen.findByText("Default Org");
    fireEvent.click(orgOption);

    const configInput = screen.getByPlaceholderText(
      '{"region": "us-east-1", "services": ["ec2", "rds", "s3"]}',
    );
    fireEvent.change(configInput, { target: { value: "" } });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(api.createDiscoveryJob).toHaveBeenCalledWith({
        name: "Prod FleetDM",
        provider_type: "fleetdm",
        organization_id: 1,
        config: {},
        schedule: undefined,
        enabled: true,
      });
    });
  });
});
