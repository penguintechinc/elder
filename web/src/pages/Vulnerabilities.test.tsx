import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import api from "@/lib/api";

vi.mock("react-hot-toast", () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  default: {
    getVulnerabilities: vi.fn(),
    getServices: vi.fn().mockResolvedValue({ items: [] }),
    getSoftware: vi.fn().mockResolvedValue({ items: [] }),
    getOrganizations: vi.fn(),
    getEntities: vi.fn(),
    createVulnerability: vi.fn(),
    updateVulnerability: vi.fn(),
    createVulnerabilityIssue: vi.fn(),
    assignVulnerability: vi.fn(),
  },
}));

import Vulnerabilities from "./Vulnerabilities";

function renderVulnerabilities() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Vulnerabilities />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const mockOrgs = [
  { id: 10, name: "Platform Security OU" },
  { id: 11, name: "Payments Engineering" },
];

const mockEntities = [
  { id: 20, name: "edge-gateway-01", type: "server" },
  { id: 21, name: "billing-db-primary", type: "database" },
];

const mockVulns = [
  {
    id: 1,
    cve_id: "CVE-2026-1001",
    title: "OpenSSL buffer overflow",
    severity: "critical",
    cvss_score: 9.8,
    adjusted_risk_score: "4.2",
    owner_organization_id: 10,
    owner_organization_name: "Platform Security OU",
    owner_entity_id: 20,
    owner_entity_name: "edge-gateway-01",
    remediation_issue_id: 42,
    remediation_issue: {
      id: 42,
      title: "[CVE-2026-1001] Remediate OpenSSL buffer overflow",
      status: "open",
      priority: "critical",
      village_id: "VLG-ISS-42",
    },
    status: "open",
    published_at: "2026-11-20T00:00:00Z",
    description: "Remote memory corruption in TLS handshake.",
    affected_entities: [],
  },
  {
    id: 2,
    cve_id: "CVE-2026-1002",
    title: "Unassigned library flaw",
    severity: "high",
    cvss_score: null,
    adjusted_risk_score: null,
    owner_organization_id: null,
    owner_organization_name: null,
    owner_entity_id: null,
    owner_entity_name: null,
    remediation_issue_id: null,
    remediation_issue: null,
    status: "open",
    published_at: null,
    description: "Needs triage.",
    affected_entities: [],
  },
  {
    id: 3,
    cve_id: "CVE-2026-1003",
    title: "Owned without issue yet",
    severity: "medium",
    cvss_score: 5.5,
    adjusted_risk_score: "low",
    owner_organization_id: 11,
    owner_organization_name: "Payments Engineering",
    owner_entity_id: null,
    owner_entity_name: null,
    remediation_issue_id: null,
    remediation_issue: null,
    status: "open",
    published_at: null,
    description: "Owned by Payments.",
    affected_entities: [],
  },
];

describe("Vulnerabilities page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getVulnerabilities).mockResolvedValue({
      items: mockVulns,
      total: mockVulns.length,
      page: 1,
      per_page: 50,
      pages: 1,
    });
    vi.mocked(api.getOrganizations).mockResolvedValue({ items: mockOrgs });
    vi.mocked(api.getEntities).mockResolvedValue({ items: mockEntities });
    vi.mocked(api.createVulnerability).mockResolvedValue({ id: 99 });
    vi.mocked(api.updateVulnerability).mockResolvedValue({ id: 1 });
    vi.mocked(api.createVulnerabilityIssue).mockResolvedValue({
      issue: {
        id: 77,
        title: "[CVE-2026-1003] Remediate Owned without issue yet",
        status: "open",
        priority: "medium",
      },
      vulnerability: {
        ...mockVulns[2],
        remediation_issue_id: 77,
        remediation_issue: {
          id: 77,
          title: "[CVE-2026-1003] Remediate Owned without issue yet",
          status: "open",
          priority: "medium",
        },
      },
    });
  });

  it("renders CVSS, Adjusted Risk, Owner OU/Resource, and Remediation Issue links in the table", async () => {
    renderVulnerabilities();
    await waitFor(() => screen.getByText("CVE-2026-1001"));

    // CVSS and Adjusted Risk
    expect(screen.getByText("9.8")).toBeDefined();
    expect(screen.getByText("4.2")).toBeDefined();
    expect(screen.getByText("5.5")).toBeDefined();
    expect(screen.getByText("low")).toBeDefined();

    // Owner OU and Resource badges
    expect(screen.getByText("OU: Platform Security OU")).toBeDefined();
    expect(screen.getByText("Resource: edge-gateway-01")).toBeDefined();
    expect(screen.getByText("OU: Payments Engineering")).toBeDefined();

    // Attached remediation issue links to /issues/42
    const issueLink = screen.getByRole("link", { name: /#42 · Open/i });
    expect(issueLink.getAttribute("href")).toBe("/issues/42");

    // Unattached rows show Create Issue buttons
    const createIssueButtons = screen.getAllByRole("button", {
      name: /Create Issue/i,
    });
    expect(createIssueButtons.length).toBe(2);
  });

  it("creates a vulnerability with searchable Owning OU, Owning Resource, CVSS, and Adjusted Risk Score", async () => {
    renderVulnerabilities();
    await waitFor(() => screen.getByText("CVE-2026-1001"));

    fireEvent.click(screen.getByRole("button", { name: /Add Vulnerability/i }));

    fireEvent.change(screen.getByPlaceholderText("CVE-2026-1234"), {
      target: { value: "CVE-2026-9999" },
    });
    fireEvent.change(screen.getByPlaceholderText("Short vulnerability summary"), {
      target: { value: "TLS downgrade bug" },
    });
    fireEvent.change(screen.getByPlaceholderText("7.5"), {
      target: { value: "8.2" },
    });
    fireEvent.change(
      screen.getByPlaceholderText("e.g. 4.2, high, low, accepted"),
      {
        target: { value: "mitigated" },
      },
    );

    // Search and pick Owning OU
    const ouCombo = screen.getByRole("combobox", { name: "Owning OU" });
    fireEvent.focus(ouCombo);
    fireEvent.change(ouCombo, { target: { value: "Platform" } });
    fireEvent.click(screen.getByRole("option", { name: "Platform Security OU" }));

    // Search and pick Owning Resource
    const resCombo = screen.getByRole("combobox", { name: "Owning Resource" });
    fireEvent.focus(resCombo);
    fireEvent.change(resCombo, { target: { value: "billing" } });
    fireEvent.click(
      screen.getByRole("option", { name: "billing-db-primary (database)" }),
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Create Vulnerability/i }),
    );

    await waitFor(() => {
      expect(api.createVulnerability).toHaveBeenCalledWith(
        expect.objectContaining({
          cve_id: "CVE-2026-9999",
          title: "TLS downgrade bug",
          cvss_score: 8.2,
          adjusted_risk_score: "mitigated",
          owner_organization_id: 10,
          owner_entity_id: 21,
        }),
      );
    });
  });

  it("edits a vulnerability and allows updating and clearing ownership + adjusted risk", async () => {
    renderVulnerabilities();
    await waitFor(() => screen.getByText("CVE-2026-1001"));

    const editButtons = screen.getAllByRole("button", { name: /^Edit$/i });
    fireEvent.click(editButtons[0]);

    await waitFor(() => screen.getByText("Edit CVE-2026-1001"));

    // Update adjusted risk score
    const riskInput = screen.getByPlaceholderText(
      "e.g. 4.2, high, low, accepted",
    ) as HTMLInputElement;
    expect(riskInput.value).toBe("4.2");
    fireEvent.change(riskInput, { target: { value: "accepted" } });

    // Clear Owning Resource via SearchableSelect clear button
    const clearButtons = screen.getAllByRole("button", {
      name: "Clear selection",
    });
    // First clear button is Owning OU, second is Owning Resource
    fireEvent.click(clearButtons[1]);

    fireEvent.click(screen.getByRole("button", { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.updateVulnerability).toHaveBeenCalledWith(
        1,
        expect.objectContaining({
          adjusted_risk_score: "accepted",
          owner_organization_id: 10,
          owner_entity_id: null,
        }),
      );
    });
  });

  it("triggers 1-click Create Issue when Owning OU is set and prompts for OU when unset", async () => {
    renderVulnerabilities();
    await waitFor(() => screen.getByText("CVE-2026-1001"));

    const createIssueButtons = screen.getAllByRole("button", {
      name: /Create Issue/i,
    });
    // Second Create Issue button belongs to CVE-2026-1003 (which has owner_organization_id = 11)
    fireEvent.click(createIssueButtons[1]);

    await waitFor(() => {
      expect(api.createVulnerabilityIssue).toHaveBeenCalledWith(3, undefined);
    });

    // First Create Issue button belongs to CVE-2026-1002 (no owning OU yet -> opens modal)
    fireEvent.click(createIssueButtons[0]);

    await waitFor(() =>
      screen.getByText("Create Remediation Issue for CVE-2026-1002"),
    );

    const modalOuCombo = screen.getByRole("combobox", {
      name: "Issue Owning OU",
    });
    fireEvent.focus(modalOuCombo);
    fireEvent.click(screen.getByRole("option", { name: "Platform Security OU" }));

    const submitButtons = screen.getAllByRole("button", {
      name: /Create Issue/i,
    });
    fireEvent.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => {
      expect(api.createVulnerabilityIssue).toHaveBeenCalledWith(2, {
        organization_id: 10,
      });
    });
  });
});
