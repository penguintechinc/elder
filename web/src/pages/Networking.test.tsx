import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import api from "@/lib/api";
import Networking from "./Networking";

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/components/NetworkTopologyGraph", () => ({
  default: () => <div data-testid="topology-graph" />,
}));

vi.mock("@/lib/api", () => ({
  default: {
    getModules: vi.fn(),
    getOrganizations: vi.fn(),
    listNetworks: vi.fn(),
    listTopologyConnections: vi.fn(),
    getIpamPrefixes: vi.fn(),
    getIpamAddresses: vi.fn(),
    getIpamVlans: vi.fn(),
    getGeoRegions: vi.fn(),
    getGeoLocations: vi.fn(),
    getServices: vi.fn(),
    deleteNetwork: vi.fn(),
    createNetwork: vi.fn(),
    createTopologyConnection: vi.fn(),
  },
}));

function renderWithRouter(ui: ReactElement, initialEntry = "/networking") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Networking page with consolidated IPAM tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getModules).mockResolvedValue({
      modules: [
        {
          name: "infrastructure",
          nav_id: "nav_infrastructure",
          effective: true,
          capabilities: {},
        },
        {
          name: "ipam",
          nav_id: "nav_ipam",
          effective: true,
          capabilities: {},
        },
      ],
    } as never);
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [
        { id: 10, name: "Acme Corp" },
        { id: 20, name: "Beta Org" },
      ],
    });
    vi.mocked(api.listNetworks).mockResolvedValue({
      networks: [
        {
          id: 101,
          name: "Prod VPC",
          network_type: "vpc",
          cidr: "10.0.0.0/16",
          is_active: true,
          organization_id: 10,
        },
      ],
    });
    vi.mocked(api.listTopologyConnections).mockResolvedValue({
      connections: [],
    });
    vi.mocked(api.getIpamPrefixes).mockResolvedValue({
      items: [
        {
          id: 1,
          prefix: "10.0.0.0/16",
          description: "Production Supernet",
          status: "active",
          is_pool: false,
          organization_id: 10,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    vi.mocked(api.getIpamAddresses).mockResolvedValue({
      items: [
        {
          id: 2,
          address: "10.0.0.1/32",
          dns_name: "gw.acme.internal",
          status: "active",
          organization_id: 10,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    vi.mocked(api.getIpamVlans).mockResolvedValue({
      items: [
        {
          id: 3,
          vid: 100,
          name: "Prod-VLAN",
          status: "active",
          organization_id: 10,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
  });

  it("auto-selects the first organization and renders networks immediately", async () => {
    renderWithRouter(<Networking />, "/networking");

    await waitFor(() => {
      expect(screen.getByText("Prod VPC")).toBeTruthy();
    });
    expect(api.listNetworks).toHaveBeenCalledWith({ organization_id: 10 });
    expect(
      screen.queryByText("Select an organization to view networks"),
    ).toBeNull();
  });

  it("activates the embedded IPAM tab on /networking?tab=ipam without duplicate header", async () => {
    renderWithRouter(<Networking />, "/networking?tab=ipam");

    await waitFor(() => {
      expect(screen.getByText("10.0.0.0/16")).toBeTruthy();
    });
    // Embedded IPAM should not render its standalone H1 title
    expect(screen.queryByText("IP Address Management")).toBeNull();
    // IPAM sub-tabs should be available and switchable
    fireEvent.click(screen.getByRole("button", { name: "Addresses" }));
    await waitFor(() => {
      expect(screen.getByText("10.0.0.1/32")).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "VLANs" }));
    await waitFor(() => {
      expect(screen.getByText("Prod-VLAN")).toBeTruthy();
    });
  });

  it("switches to IPAM when clicking the IPAM tab", async () => {
    renderWithRouter(<Networking />, "/networking");

    await waitFor(() => {
      expect(screen.getByText("Prod VPC")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "IPAM" }));
    await waitFor(() => {
      expect(screen.getByText("10.0.0.0/16")).toBeTruthy();
    });
  });

  it("hides the IPAM tab when the ipam module is disabled", async () => {
    vi.mocked(api.getModules).mockResolvedValue({
      modules: [
        {
          name: "infrastructure",
          nav_id: "nav_infrastructure",
          effective: true,
          capabilities: {},
        },
        {
          name: "ipam",
          nav_id: "nav_ipam",
          effective: false,
          capabilities: {},
        },
      ],
    } as never);

    renderWithRouter(<Networking />, "/networking?tab=ipam");

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "IPAM" })).toBeNull();
    });
    await waitFor(() => {
      expect(screen.getByText("Prod VPC")).toBeTruthy();
    });
  });
});
