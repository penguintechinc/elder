import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import api from "@/lib/api";
import Services from "./Services";

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/components/OnCallBadge", () => ({
  default: () => <div data-testid="on-call-badge" />,
}));

vi.mock("@/components/VillageIdBadge", () => ({
  default: ({ villageId }: { villageId: string }) => (
    <span data-testid="village-id-badge">{villageId}</span>
  ),
}));

vi.mock("@/lib/api", () => ({
  default: {
    getOrganizations: vi.fn(),
    getServices: vi.fn(),
    createService: vi.fn(),
    updateService: vi.fn(),
    deleteService: vi.fn(),
    getSBOMSchedules: vi.fn(),
    getSBOMScans: vi.fn(),
  },
}));

const MOCK_ORGS = {
  items: [
    { id: 10, name: "Acme Corp" },
    { id: 20, name: "Beta Org" },
  ],
};

const MOCK_SERVICE = {
  id: 42,
  name: "billing-api",
  description: "Handles invoicing and payments",
  organization_id: 10,
  language: "python",
  deployment_method: "kubernetes",
  status: "active",
  port: 8080,
  is_public: false,
  domains: ["billing.internal"],
  paths: ["GET /api/v1/invoices", "POST /api/v1/charges"],
};

function renderWithRouter(ui: ReactElement, initialEntry = "/services") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Services page with tabbed Endpoints management", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getOrganizations).mockResolvedValue(MOCK_ORGS);
    vi.mocked(api.getServices).mockResolvedValue({
      items: [MOCK_SERVICE],
    });
    vi.mocked(api.createService).mockResolvedValue({
      id: 99,
      name: "auth-api",
      organization_id: 10,
      paths: ["POST /api/v1/token"],
    });
    vi.mocked(api.updateService).mockResolvedValue({
      ...MOCK_SERVICE,
      paths: [
        "GET /api/v1/invoices",
        "POST /api/v1/charges",
        "DELETE /api/v1/invoices/:id",
      ],
    });
    vi.mocked(api.getSBOMSchedules).mockResolvedValue({ items: [] });
    vi.mocked(api.getSBOMScans).mockResolvedValue({ items: [] });
  });

  it("renders Details and Endpoints tabs in the View Service modal and loads endpoints on tab switch", async () => {
    renderWithRouter(<Services />);

    const cardHeading = await screen.findByText("billing-api");
    fireEvent.click(cardHeading);

    const detailsTab = screen.getByRole("tab", { name: /^Details$/i });
    const endpointsTab = screen.getByRole("tab", { name: /Endpoints\s*2/i });
    expect(detailsTab.getAttribute("aria-selected")).toBe("true");
    expect(endpointsTab.getAttribute("aria-selected")).toBe("false");

    fireEvent.click(endpointsTab);

    expect(endpointsTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("/api/v1/invoices")).toBeTruthy();
    expect(screen.getByText("/api/v1/charges")).toBeTruthy();
  });

  it("opens directly to the Endpoints tab from the service card action button", async () => {
    renderWithRouter(<Services />);

    const endpointsBtn = await screen.findByRole("button", {
      name: "Manage endpoints for billing-api",
    });
    fireEvent.click(endpointsBtn);

    const endpointsTab = screen.getByRole("tab", { name: /Endpoints\s*2/i });
    expect(endpointsTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("/api/v1/invoices")).toBeTruthy();
  });

  it("adds and deletes an endpoint from the Endpoints tab and calls api.updateService", async () => {
    renderWithRouter(<Services />);

    const endpointsBtn = await screen.findByRole("button", {
      name: "Manage endpoints for billing-api",
    });
    fireEvent.click(endpointsBtn);

    fireEvent.change(screen.getByLabelText("Method"), {
      target: { value: "DELETE" },
    });
    fireEvent.change(screen.getByLabelText("Path"), {
      target: { value: "/api/v1/invoices/:id" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add Endpoint/i }));

    await waitFor(() => {
      expect(api.updateService).toHaveBeenCalledWith(42, {
        paths: [
          "GET /api/v1/invoices",
          "POST /api/v1/charges",
          "DELETE /api/v1/invoices/:id",
        ],
      });
    });

    await waitFor(() => {
      expect(screen.getByText("/api/v1/invoices/:id")).toBeTruthy();
      expect(screen.getByRole("tab", { name: /Endpoints\s*3/i })).toBeTruthy();
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: "Delete endpoint POST /api/v1/charges",
      }),
    );

    await waitFor(() => {
      expect(api.updateService).toHaveBeenLastCalledWith(42, {
        paths: ["GET /api/v1/invoices", "DELETE /api/v1/invoices/:id"],
      });
    });
  });

  it("renders Details and Endpoints tabs in Create Service modal and saves staged endpoints", async () => {
    renderWithRouter(<Services />);

    await screen.findByText("billing-api");
    fireEvent.click(screen.getByRole("button", { name: /Create Service/i }));

    const detailsTab = screen.getByRole("tab", { name: /^Details$/i });
    const endpointsTab = screen.getByRole("tab", { name: /Endpoints\s*0/i });
    expect(detailsTab.getAttribute("aria-selected")).toBe("true");

    // Fill required Details fields
    fireEvent.change(screen.getByPlaceholderText("api-gateway"), {
      target: { value: "auth-api" },
    });
    const orgInput = screen.getByLabelText("Organization");
    fireEvent.focus(orgInput);
    const orgOption = (await screen.findAllByText("Acme Corp")).find(
      (el) => el.tagName === "BUTTON",
    );
    expect(orgOption).toBeTruthy();
    fireEvent.click(orgOption!);

    // Switch to Endpoints tab and stage an endpoint
    fireEvent.click(endpointsTab);
    expect(endpointsTab.getAttribute("aria-selected")).toBe("true");

    fireEvent.change(screen.getByLabelText("Endpoint 1 method"), {
      target: { value: "POST" },
    });
    const pathInput = screen.getByLabelText("Endpoint 1 path");
    fireEvent.focus(pathInput);
    fireEvent.change(pathInput, { target: { value: "/api/v1/token" } });
    fireEvent.keyDown(pathInput, { key: "Enter" });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /Endpoints\s*1/i })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /^Create$/i }));

    await waitFor(() => {
      expect(api.createService).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "auth-api",
          organization_id: 10,
          paths: ["POST /api/v1/token"],
        }),
      );
    });
  });

  it("renders Details and Endpoints tabs in Edit Service modal with existing endpoints", async () => {
    renderWithRouter(<Services />);

    const editBtn = await screen.findByRole("button", {
      name: "Edit billing-api",
    });
    fireEvent.click(editBtn);

    const endpointsTab = screen.getByRole("tab", { name: /Endpoints\s*2/i });
    fireEvent.click(endpointsTab);

    expect(endpointsTab.getAttribute("aria-selected")).toBe("true");
    expect(
      (screen.getByLabelText("Endpoint 1 method") as HTMLSelectElement).value,
    ).toBe("GET");
    expect(
      (screen.getByLabelText("Endpoint 2 method") as HTMLSelectElement).value,
    ).toBe("POST");
  });
});
