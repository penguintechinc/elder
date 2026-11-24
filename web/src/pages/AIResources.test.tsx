import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import api from "@/lib/api";
import AIResources from "./AIResources";
import type { AIResource } from "@/types";

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/lib/api", () => ({
  default: {
    getOrganizations: vi.fn(),
    getAIResources: vi.fn(),
    createAIResource: vi.fn(),
    updateAIResource: vi.fn(),
    deleteAIResource: vi.fn(),
  },
}));

function renderPage(ui: ReactElement, initialEntry = "/ai") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const SAMPLE_RESOURCES: AIResource[] = [
  {
    id: 1,
    tenant_id: 1,
    name: "Sandbox Code Mode",
    description: "Executes sandboxed TypeScript tool calls",
    organization_id: 10,
    resource_type: "code_mode_server",
    provider: "Internal",
    endpoint_url: "https://codemode.internal/api",
    status: "allowed",
    agreement_level: "enterprise",
    metadata: { transport: "streamable-http", auth_type: "bearer" },
    is_active: true,
    village_id: "0001-ai0001",
  },
  {
    id: 2,
    tenant_id: 1,
    name: "Enterprise UTCP Hub",
    description: "Universal tool call protocol catalog",
    organization_id: 10,
    resource_type: "utcp_server",
    provider: "Internal",
    endpoint_url: "https://utcp.internal/utcp.json",
    status: "pending_review",
    agreement_level: "team",
    metadata: { transport: "http", auth_type: "api_key" },
    is_active: true,
    village_id: "0001-ai0002",
  },
];

describe("AIResources — Code Mode & UTCP Servers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [{ id: 10, name: "Platform Org" }],
      total: 1,
    });
    vi.mocked(api.getAIResources).mockImplementation(async (params) => {
      const filtered = params?.resource_type
        ? SAMPLE_RESOURCES.filter(
            (r) => r.resource_type === params.resource_type,
          )
        : SAMPLE_RESOURCES;
      return {
        items: filtered,
        total: filtered.length,
        page: 1,
        per_page: 100,
        pages: 1,
      };
    });
    vi.mocked(api.createAIResource).mockResolvedValue(SAMPLE_RESOURCES[0]);
    vi.mocked(api.updateAIResource).mockResolvedValue(SAMPLE_RESOURCES[0]);
  });

  it("renders dedicated tabs for Code Mode Servers and UTCP Servers and filters queries", async () => {
    renderPage(<AIResources />);

    expect(
      screen.getByRole("button", { name: "Code Mode Servers" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "UTCP Servers" })).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText("Sandbox Code Mode")).toBeTruthy();
      expect(screen.getByText("Enterprise UTCP Hub")).toBeTruthy();
    });

    // Type badges and transport/auth metadata badges render in table
    expect(screen.getByText("Code Mode Server")).toBeTruthy();
    expect(screen.getByText("UTCP Server")).toBeTruthy();
    expect(screen.getByText("streamable-http")).toBeTruthy();
    expect(screen.getByText("auth: bearer")).toBeTruthy();

    // Switch to Code Mode Servers tab
    fireEvent.click(screen.getByRole("button", { name: "Code Mode Servers" }));
    await waitFor(() => {
      expect(api.getAIResources).toHaveBeenCalledWith(
        expect.objectContaining({ resource_type: "code_mode_server" }),
      );
    });

    // Switch to UTCP Servers tab
    fireEvent.click(screen.getByRole("button", { name: "UTCP Servers" }));
    await waitFor(() => {
      expect(api.getAIResources).toHaveBeenCalledWith(
        expect.objectContaining({ resource_type: "utcp_server" }),
      );
    });
  });

  it("creates a Code Mode Server with transport and auth_type metadata", async () => {
    renderPage(<AIResources defaultType="code_mode_server" />);

    await waitFor(() => {
      expect(screen.getByText("Sandbox Code Mode")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Add AI Resource/i }));

    const typeSelect = screen.getByLabelText(
      /Resource Type/i,
    ) as HTMLSelectElement;
    expect(typeSelect.value).toBe("code_mode_server");

    fireEvent.change(screen.getByLabelText(/Name \*/i), {
      target: { value: "Prod Code Mode" },
    });
    fireEvent.change(screen.getByLabelText(/Endpoint URL/i), {
      target: { value: "https://codemode.prod/api" },
    });
    fireEvent.change(screen.getByLabelText(/Protocol \/ Transport/i), {
      target: { value: "streamable-http" },
    });
    fireEvent.change(screen.getByLabelText(/Auth Type/i), {
      target: { value: "oauth2" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create Resource" }));

    await waitFor(() => {
      expect(api.createAIResource).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Prod Code Mode",
          organization_id: 10,
          resource_type: "code_mode_server",
          endpoint_url: "https://codemode.prod/api",
          metadata: {
            transport: "streamable-http",
            auth_type: "oauth2",
          },
        }),
      );
    });
  });

  it("creates a UTCP Server and displays transport/auth in detail view", async () => {
    renderPage(<AIResources defaultType="utcp_server" />);

    await waitFor(() => {
      expect(screen.getByText("Enterprise UTCP Hub")).toBeTruthy();
    });

    // Open detail view on UTCP server
    fireEvent.click(screen.getByTitle("View Details"));
    await waitFor(() => {
      expect(screen.getByText("Protocol / Transport")).toBeTruthy();
      expect(screen.getByText("Auth Type")).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    // Open create modal — defaults to utcp_server
    fireEvent.click(screen.getByRole("button", { name: /Add AI Resource/i }));
    const typeSelect = screen.getByLabelText(
      /Resource Type/i,
    ) as HTMLSelectElement;
    expect(typeSelect.value).toBe("utcp_server");

    fireEvent.change(screen.getByLabelText(/Name \*/i), {
      target: { value: "Billing UTCP Server" },
    });
    fireEvent.change(screen.getByLabelText(/Endpoint URL/i), {
      target: { value: "https://billing.internal/utcp.json" },
    });
    fireEvent.change(screen.getByLabelText(/Protocol \/ Transport/i), {
      target: { value: "http" },
    });
    fireEvent.change(screen.getByLabelText(/Auth Type/i), {
      target: { value: "api_key" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create Resource" }));

    await waitFor(() => {
      expect(api.createAIResource).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Billing UTCP Server",
          organization_id: 10,
          resource_type: "utcp_server",
          endpoint_url: "https://billing.internal/utcp.json",
          metadata: {
            transport: "http",
            auth_type: "api_key",
          },
        }),
      );
    });
  });

  it("populates transport and auth_type when editing an existing server", async () => {
    renderPage(<AIResources defaultType="code_mode_server" />);

    await waitFor(() => {
      expect(screen.getByText("Sandbox Code Mode")).toBeTruthy();
    });

    fireEvent.click(screen.getByTitle("Edit"));

    const transportSelect = screen.getByLabelText(
      /Protocol \/ Transport/i,
    ) as HTMLSelectElement;
    const authSelect = screen.getByLabelText(
      /Auth Type/i,
    ) as HTMLSelectElement;
    expect(transportSelect.value).toBe("streamable-http");
    expect(authSelect.value).toBe("bearer");

    fireEvent.change(authSelect, { target: { value: "mtls" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(api.updateAIResource).toHaveBeenCalledWith(
        1,
        expect.objectContaining({
          resource_type: "code_mode_server",
          metadata: {
            transport: "streamable-http",
            auth_type: "mtls",
          },
        }),
      );
    });
  });
});
