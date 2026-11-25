import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import DataStores from "./DataStores";
import api from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    getOrganizations: vi.fn().mockResolvedValue({
      items: [{ id: 1, name: "Default Org" }],
    }),
    getIdentities: vi.fn().mockResolvedValue({ items: [] }),
    getGeoRegions: vi.fn().mockResolvedValue({ regions: [] }),
    getLabels: vi.fn(),
    getDataStores: vi.fn(),
    createDataStore: vi.fn(),
    updateDataStore: vi.fn(),
    deleteDataStore: vi.fn(),
    createLabel: vi.fn(),
    updateLabel: vi.fn(),
    deleteLabel: vi.fn(),
    addDataStoreLabel: vi.fn(),
    removeDataStoreLabel: vi.fn(),
  },
}));

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

function renderDataInventory() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DataStores />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DataInventory (DataStores)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getLabels).mockResolvedValue({
      items: [
        { id: 1, name: "PHI", color: "#a855f7" },
        { id: 2, name: "PII", color: "#3b82f6" },
        { id: 3, name: "FedRAMP", color: "#06b6d4" },
        { id: 4, name: "NYDFS", color: "#f59e0b" },
        { id: 5, name: "SOX", color: "#10b981" },
        { id: 6, name: "PCI", color: "#ec4899" },
      ],
    });
    vi.mocked(api.getDataStores).mockResolvedValue({
      items: [
        {
          id: 10,
          name: "Customer DB",
          organization_id: 1,
          data_classification: "restricted",
          storage_type: "database",
          labels: [{ id: 2, name: "PII", color: "#3b82f6" }],
          metadata: { owner: "security" },
        },
      ],
    });
  });

  it("renders Data Inventory heading and filters by data label", async () => {
    renderDataInventory();

    expect(await screen.findByText("Data Inventory")).toBeTruthy();
    expect(await screen.findByText("Customer DB")).toBeTruthy();

    const labelSelect = screen.getByLabelText("Filter by Data Label");
    fireEvent.change(labelSelect, { target: { value: "3" } });

    await waitFor(() => {
      expect(api.getDataStores).toHaveBeenCalledWith(
        expect.objectContaining({ label_id: 3 }),
      );
    });
  });

  it("allows creating a custom label in the Data Labels tab", async () => {
    vi.mocked(api.createLabel).mockResolvedValue({
      id: 99,
      name: "GDPR",
      color: "#3b82f6",
    });

    renderDataInventory();

    const labelsTab = await screen.findByRole("button", {
      name: /Data Labels/i,
    });
    fireEvent.click(labelsTab);

    expect(await screen.findByText("FedRAMP")).toBeTruthy();
    expect(screen.getByText("NYDFS")).toBeTruthy();

    const nameInput = screen.getByPlaceholderText("e.g. HIPAA, GDPR, Secret");
    fireEvent.change(nameInput, { target: { value: "GDPR" } });
    fireEvent.click(screen.getByRole("button", { name: /Add Label/i }));

    await waitFor(() => {
      expect(api.createLabel).toHaveBeenCalledWith(
        expect.objectContaining({ name: "GDPR" }),
      );
    });
  });

  it("submits selected labels and JSON metadata when creating a data store", async () => {
    vi.mocked(api.createDataStore).mockResolvedValue({ id: 11 });

    renderDataInventory();

    const addBtn = await screen.findByRole("button", {
      name: /Add Data Store/i,
    });
    fireEvent.click(addBtn);

    const nameInput = await screen.findByPlaceholderText(
      "Production Customer Database",
    );
    fireEvent.change(nameInput, { target: { value: "Billing Vault" } });

    const pciButton = screen.getByRole("button", { name: "PCI" });
    fireEvent.click(pciButton);

    const metadataBox = screen.getByPlaceholderText(
      '{"owner_team": "platform", "retention_policy": "7-years"}',
    );
    fireEvent.change(metadataBox, { target: { value: '{"tier":"prod"}' } });

    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(api.createDataStore).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Billing Vault",
          label_ids: [6],
          metadata: { tier: "prod" },
        }),
      );
    });
  });
});
