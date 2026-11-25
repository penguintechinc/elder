import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AdminSettings from "./AdminSettings";
import api, { type LicenseStatus, type MfaPolicy } from "@/lib/api";

vi.mock("react-hot-toast", () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  default: {
    health: vi.fn(),
    getPortalProfile: vi.fn(),
    getLicenseStatus: vi.fn(),
    getMfaPolicy: vi.fn(),
    updateMfaPolicy: vi.fn(),
    getLogs: vi.fn(),
    searchLogs: vi.fn(),
  },
}));

const DEFAULT_ENTERPRISE_MFA_POLICY: MfaPolicy = {
  enforced: false,
  provider: "google_authenticator",
  grace_period_days: 7,
  enforced_at: null,
  mfa_enrolled: true,
  license_tier: "enterprise",
  can_manage: true,
};

const DEFAULT_FREE_MFA_POLICY: MfaPolicy = {
  enforced: false,
  provider: "google_authenticator",
  grace_period_days: 7,
  enforced_at: null,
  mfa_enrolled: false,
  license_tier: "free",
  can_manage: false,
};

const ENTERPRISE_BYPASS_STATUS: LicenseStatus = {
  tier: "enterprise",
  status: "active",
  domain_bypass: true,
  bypass_domain: "alpha.elderrms.app",
  limits: {
    max_global_admins: null,
    max_tenant_admins: null,
    max_teams: null,
    max_tenants: null,
    max_objects: null,
    max_nodes_per_type: null,
  },
  features: {
    multi_tenancy: true,
    portal_users: true,
    saml_sso: true,
    scim_provisioning: true,
    audit_logging: true,
    compliance_reports: true,
    access_reviews: true,
    enterprise_integrations: true,
    google_oauth_sso: true,
  },
};

const FREE_STATUS: LicenseStatus = {
  tier: "free",
  status: "active",
  domain_bypass: false,
  bypass_domain: null,
  limits: {
    max_global_admins: 1,
    max_tenant_admins: 0,
    max_teams: 1,
    max_tenants: 1,
    max_objects: 1000,
    max_nodes_per_type: 1,
  },
  features: {
    multi_tenancy: false,
    portal_users: false,
    saml_sso: false,
    scim_provisioning: false,
    audit_logging: false,
    compliance_reports: false,
    access_reviews: false,
    enterprise_integrations: false,
    google_oauth_sso: false,
  },
};

const ALL_FEATURE_KEYS = [
  "multi_tenancy",
  "portal_users",
  "saml_sso",
  "scim_provisioning",
  "audit_logging",
  "compliance_reports",
  "access_reviews",
  "enterprise_integrations",
  "google_oauth_sso",
] as const;

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AdminSettings />
    </QueryClientProvider>,
  );
}

describe("AdminSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.health).mockResolvedValue({
      status: "healthy",
      service: "elder",
    });
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      email: "admin@penguintech.io",
      global_role: "viewer",
      tenant_role: "admin",
      mfa_enabled: true,
    });
    vi.mocked(api.getMfaPolicy).mockResolvedValue(
      DEFAULT_ENTERPRISE_MFA_POLICY,
    );
    vi.mocked(api.updateMfaPolicy).mockResolvedValue({
      ...DEFAULT_ENTERPRISE_MFA_POLICY,
      enforced: true,
      enforced_at: "2026-11-24T00:00:00Z",
    });
  });

  it("renders Enterprise badge, Domain Bypass badge, and all enabled CheckCircles when bypass is active", async () => {
    vi.mocked(api.getLicenseStatus).mockResolvedValue(ENTERPRISE_BYPASS_STATUS);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("license-tier-badge").textContent).toBe(
        "Enterprise",
      );
    });

    expect(screen.queryByText("Community")).toBeNull();
    expect(
      screen.getByTestId("license-domain-bypass-badge").textContent,
    ).toContain("Domain Bypass (alpha.elderrms.app)");

    for (const key of ALL_FEATURE_KEYS) {
      const row = screen.getByTestId(`license-feature-${key}`);
      expect(row.getAttribute("data-enabled")).toBe("true");
    }

    expect(screen.getAllByLabelText(/enabled$/i)).toHaveLength(
      ALL_FEATURE_KEYS.length,
    );
    expect(screen.queryByLabelText(/disabled$/i)).toBeNull();
    expect(
      screen.getAllByText(
        "Enterprise license bypass active for alpha.elderrms.app.",
      ),
    ).toHaveLength(2);
  });

  it("renders Enterprise active message without Domain Bypass badge when licensed directly", async () => {
    vi.mocked(api.getLicenseStatus).mockResolvedValue({
      ...ENTERPRISE_BYPASS_STATUS,
      domain_bypass: false,
      bypass_domain: null,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("license-tier-badge").textContent).toBe(
        "Enterprise",
      );
    });

    expect(screen.queryByTestId("license-domain-bypass-badge")).toBeNull();
    expect(screen.getAllByText("Enterprise license active.")).toHaveLength(2);
  });

  it("renders Free badge, disabled XCircles, and upgrade prompt on Free tier", async () => {
    vi.mocked(api.getLicenseStatus).mockResolvedValue(FREE_STATUS);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("license-tier-badge").textContent).toBe("Free");
    });

    expect(screen.queryByText("Community")).toBeNull();
    expect(screen.queryByTestId("license-domain-bypass-badge")).toBeNull();

    for (const key of ALL_FEATURE_KEYS) {
      const row = screen.getByTestId(`license-feature-${key}`);
      expect(row.getAttribute("data-enabled")).toBe("false");
    }

    expect(screen.getAllByLabelText(/disabled$/i)).toHaveLength(
      ALL_FEATURE_KEYS.length,
    );
    expect(screen.queryByLabelText(/enabled$/i)).toBeNull();
    expect(
      screen.getByText("Add a license key to enable enterprise features."),
    ).toBeDefined();
  });

  it("allows Enterprise admins to toggle MFA enforcement and save grace period days", async () => {
    vi.mocked(api.getLicenseStatus).mockResolvedValue(ENTERPRISE_BYPASS_STATUS);
    vi.mocked(api.getMfaPolicy).mockResolvedValue(
      DEFAULT_ENTERPRISE_MFA_POLICY,
    );

    renderPage();

    const toggle = await screen.findByTestId("mfa-policy-toggle");
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTestId("mfa-policy-status").textContent).toBe(
      "Optional",
    );

    fireEvent.click(toggle);

    await waitFor(() => {
      expect(api.updateMfaPolicy).toHaveBeenCalledWith({
        enforced: true,
        provider: "google_authenticator",
        grace_period_days: 7,
      });
    });

    const graceInput = screen.getByTestId(
      "mfa-grace-period-input",
    ) as HTMLInputElement;
    fireEvent.change(graceInput, { target: { value: "14" } });
    fireEvent.click(screen.getByTestId("mfa-grace-period-save"));

    await waitFor(() => {
      expect(api.updateMfaPolicy).toHaveBeenCalledWith({
        enforced: false,
        provider: "google_authenticator",
        grace_period_days: 14,
      });
    });
  });

  it("disables MFA enforcement controls and shows upgrade notice on Free tier", async () => {
    vi.mocked(api.getLicenseStatus).mockResolvedValue(FREE_STATUS);
    vi.mocked(api.getMfaPolicy).mockResolvedValue(DEFAULT_FREE_MFA_POLICY);

    renderPage();

    const toggle = (await screen.findByTestId(
      "mfa-policy-toggle",
    )) as HTMLButtonElement;
    await waitFor(() => {
      expect(toggle.disabled).toBe(true);
    });
    expect(screen.getByTestId("mfa-enterprise-upgrade-notice")).toBeDefined();
  });
});
