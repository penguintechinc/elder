import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import Integrations from "./Integrations";
import api, {
  type DiscoveryJobItem,
  type GoogleWorkspaceProviderItem,
  type IAMProviderItem,
  type SyncConfigItem,
} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    getSyncConfigs: vi.fn(),
    getSyncStatus: vi.fn(),
    getSyncMappings: vi.fn(),
    getSyncHistory: vi.fn(),
    getSyncConflicts: vi.fn(),
    getOrganizations: vi.fn(),
    getDiscoveryJobs: vi.fn(),
    createDiscoveryJob: vi.fn(),
    updateDiscoveryJob: vi.fn(),
    deleteDiscoveryJob: vi.fn(),
    testDiscoveryJob: vi.fn(),
    runDiscoveryJob: vi.fn(),
    getDiscoveryJobHistory: vi.fn(),
    getGoogleWorkspaceProviders: vi.fn(),
    createGoogleWorkspaceProvider: vi.fn(),
    updateGoogleWorkspaceProvider: vi.fn(),
    deleteGoogleWorkspaceProvider: vi.fn(),
    testGoogleWorkspaceProvider: vi.fn(),
    getIAMProviders: vi.fn(),
    createIAMProvider: vi.fn(),
    updateIAMProvider: vi.fn(),
    deleteIAMProvider: vi.fn(),
    testIAMProvider: vi.fn(),
    syncIAMProvider: vi.fn(),
  },
}));

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

const SAMPLE_SYNC: SyncConfigItem = {
  id: 1,
  name: "Core GitHub Sync",
  platform: "github",
  enabled: true,
  sync_interval: 300,
  batch_fallback_enabled: true,
  batch_size: 100,
  two_way_create: true,
  webhook_enabled: true,
  has_webhook_secret: true,
  last_sync_at: null,
  last_batch_sync_at: null,
  config_json: {
    auth_mode: "github_app",
    owner: "penguintechinc",
    repo_name: "elder",
    resource_types: ["issue"],
  },
  created_at: null,
  updated_at: null,
};

const SAMPLE_CLOUD_JOB: DiscoveryJobItem = {
  id: 10,
  name: "AWS Primary",
  provider: "aws",
  config_json: {
    auth_method: "static_credentials",
    region: "us-east-1",
    access_key_id: "***REDACTED***",
    secret_access_key: "***REDACTED***",
    services: ["ec2", "s3"],
    _organization_id: 1,
  },
  schedule_interval: 3600,
  enabled: true,
  organization_id: 1,
};

const SAMPLE_FLEET_JOB: DiscoveryJobItem = {
  id: 20,
  name: "FleetDM Prod",
  provider: "fleetdm",
  config_json: {
    url: "https://fleet.example.com",
    api_token: "***REDACTED***",
    verify_ssl: true,
    sync_mdm: true,
    sync_labels: true,
    sync_os_versions: true,
    per_page: 100,
    _organization_id: 1,
  },
  schedule_interval: 900,
  enabled: true,
  organization_id: 1,
};

const SAMPLE_GWS_PROVIDER: GoogleWorkspaceProviderItem = {
  id: 30,
  name: "Corp Workspace",
  domain: "example.com",
  admin_email: "admin@example.com",
  organization_id: 1,
  enabled: true,
  customer_id: "C01234567",
  has_credentials: true,
  credentials_json: "***REDACTED***",
};

const SAMPLE_IAM_PROVIDER: IAMProviderItem = {
  id: 40,
  name: "AWS Prod IAM",
  provider_type: "aws_iam",
  config_json: {
    region: "us-east-1",
    access_key_id: "***REDACTED***",
  },
  organization_id: 1,
  enabled: true,
};

function renderIntegrations(initialEntry = "/admin/integrations") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/admin/integrations" element={<Integrations />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Integrations Hub", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [{ id: 1, name: "Penguin Tech" }],
    });
    vi.mocked(api.getSyncConfigs).mockResolvedValue({ configs: [SAMPLE_SYNC] });
    vi.mocked(api.getSyncStatus).mockResolvedValue({
      configs: { total: 1, enabled: 1 },
      recent_activity: { syncs_24h: 2, failures_24h: 0 },
      conflicts: { unresolved: 0 },
      mappings: { total: 5 },
    });
    vi.mocked(api.getDiscoveryJobs).mockResolvedValue({
      jobs: [SAMPLE_CLOUD_JOB, SAMPLE_FLEET_JOB],
    });
    vi.mocked(api.getGoogleWorkspaceProviders).mockResolvedValue({
      providers: [SAMPLE_GWS_PROVIDER],
    });
    vi.mocked(api.getIAMProviders).mockResolvedValue({
      providers: [SAMPLE_IAM_PROVIDER],
    });
  });

  it("renders Overview with unified counts and inventory across all integration categories", async () => {
    renderIntegrations();

    expect(await screen.findByText("Core GitHub Sync")).toBeTruthy();
    expect(screen.getByText("AWS Primary")).toBeTruthy();
    expect(screen.getByText("FleetDM Prod")).toBeTruthy();
    expect(screen.getByText("Corp Workspace")).toBeTruthy();
    expect(screen.getByText("AWS Prod IAM")).toBeTruthy();

    // Total integrations = 5 (1 sync + 1 cloud + 1 fleet + 1 gws + 1 iam)
    expect(screen.getByText("Total Integrations")).toBeTruthy();
    expect(screen.getAllByText("5").length).toBeGreaterThan(0);

    // Search filters unified list
    fireEvent.change(screen.getByLabelText("Search integrations"), {
      target: { value: "FleetDM" },
    });
    expect(screen.getByText("FleetDM Prod")).toBeTruthy();
    expect(screen.queryByText("Core GitHub Sync")).toBeNull();
  });

  it("respects ?tab=work-management URL query parameter", async () => {
    renderIntegrations("/admin/integrations?tab=work-management");

    expect(await screen.findByText("Work Management Sync")).toBeTruthy();
    expect(await screen.findByText("Core GitHub Sync")).toBeTruthy();
    expect(screen.getByTestId("add-sync-config-btn")).toBeTruthy();
  });

  it("switches to Cloud & Infrastructure tab, creates a typed AWS profile, tests connection, and preserves secrets on edit", async () => {
    vi.mocked(api.createDiscoveryJob).mockResolvedValue({
      ...SAMPLE_CLOUD_JOB,
      id: 11,
      name: "AWS Staging",
    });
    vi.mocked(api.updateDiscoveryJob).mockResolvedValue({
      ...SAMPLE_CLOUD_JOB,
      name: "AWS Primary Renamed",
    });
    vi.mocked(api.testDiscoveryJob).mockResolvedValue({
      job_id: 10,
      success: true,
      tested_at: "2026-11-20T12:00:00Z",
      auth_method: "static_credentials",
      identity: {
        account: "123456789012",
        arn: "arn:aws:iam::123456789012:user/elder-discovery",
      },
    });

    renderIntegrations();
    await screen.findByText("Core GitHub Sync");

    const cloudTabButtons = screen.getAllByRole("button", {
      name: /Cloud & Infrastructure/i,
    });
    fireEvent.click(cloudTabButtons[0]);

    expect(
      await screen.findByText("Cloud & Infrastructure Profiles"),
    ).toBeTruthy();
    expect(screen.getByText("AWS Primary")).toBeTruthy();

    // Test connection on existing AWS profile
    fireEvent.click(screen.getByRole("button", { name: /Test Connection/i }));
    expect(
      await screen.findByText(
        /Connection verified via static_credentials \(arn:aws:iam::123456789012:user\/elder-discovery\)/,
      ),
    ).toBeTruthy();

    // Create new typed AWS profile without raw JSON
    fireEvent.click(screen.getByTestId("add-cloud-profile-btn"));
    fireEvent.change(screen.getByLabelText("Profile Name *"), {
      target: { value: "AWS Staging" },
    });
    fireEvent.change(screen.getByLabelText("Organization *"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText("AWS Region *"), {
      target: { value: "us-west-2" },
    });
    fireEvent.change(screen.getByLabelText("Access Key ID"), {
      target: { value: "AKIA_STAGING" },
    });
    fireEvent.change(screen.getByLabelText("Secret Access Key"), {
      target: { value: "SECRET_STAGING" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save Profile" }));

    await waitFor(() => {
      expect(api.createDiscoveryJob).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "AWS Staging",
          provider_type: "aws",
          organization_id: 1,
          schedule_interval: 3600,
          config: expect.objectContaining({
            auth_method: "static_credentials",
            region: "us-west-2",
            access_key_id: "AKIA_STAGING",
            secret_access_key: "SECRET_STAGING",
            services: ["ec2", "rds", "s3"],
          }),
        }),
      );
    });

    // Edit existing profile leaving secrets blank (must not overwrite with empty strings)
    fireEvent.click(screen.getByRole("button", { name: "Edit AWS Primary" }));
    fireEvent.change(screen.getByLabelText("Profile Name *"), {
      target: { value: "AWS Primary Renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(api.updateDiscoveryJob).toHaveBeenCalledTimes(1);
    });
    const [updatedId, updatePayload] = vi.mocked(api.updateDiscoveryJob).mock
      .calls[0];
    expect(updatedId).toBe(10);
    expect(updatePayload.name).toBe("AWS Primary Renamed");
    expect(updatePayload.config).not.toHaveProperty("access_key_id");
    expect(updatePayload.config).not.toHaveProperty("secret_access_key");
  });

  it("switches to Endpoints & Security tab and creates a typed FleetDM profile", async () => {
    vi.mocked(api.createDiscoveryJob).mockResolvedValue({
      ...SAMPLE_FLEET_JOB,
      id: 21,
      name: "FleetDM EU",
    });
    vi.mocked(api.testDiscoveryJob).mockResolvedValue({
      job_id: 20,
      success: true,
      tested_at: "2026-11-20T12:00:00Z",
    });

    renderIntegrations();
    await screen.findByText("Core GitHub Sync");

    const endpointTabs = screen.getAllByRole("button", {
      name: /Endpoints & Security/i,
    });
    fireEvent.click(endpointTabs[0]);

    expect(
      await screen.findByText("Endpoints & Security (FleetDM)"),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Test Connection/i }));
    expect(
      await screen.findByText(/FleetDM API reachable for "FleetDM Prod"/),
    ).toBeTruthy();

    fireEvent.click(screen.getByTestId("add-fleetdm-profile-btn"));
    fireEvent.change(screen.getByLabelText("Profile Name *"), {
      target: { value: "FleetDM EU" },
    });
    fireEvent.change(screen.getByLabelText("FleetDM Server URL *"), {
      target: { value: "https://fleet-eu.example.com" },
    });
    fireEvent.change(screen.getByLabelText("API-Only User Token *"), {
      target: { value: "fleet_tok_eu" },
    });
    fireEvent.click(screen.getByLabelText("Sync Scripts & Reports"));

    fireEvent.click(screen.getByRole("button", { name: "Save Profile" }));

    await waitFor(() => {
      expect(api.createDiscoveryJob).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "FleetDM EU",
          provider_type: "fleetdm",
          organization_id: 1,
          config: expect.objectContaining({
            url: "https://fleet-eu.example.com",
            api_token: "fleet_tok_eu",
            verify_ssl: true,
            sync_mdm: true,
            sync_labels: true,
            sync_os_versions: true,
            sync_scripts_reports: true,
            per_page: 100,
          }),
        }),
      );
    });
  });

  it("switches to Identity & Directories tab and creates Google Workspace and IAM providers", async () => {
    vi.mocked(api.createGoogleWorkspaceProvider).mockResolvedValue({
      ...SAMPLE_GWS_PROVIDER,
      id: 31,
      name: "EMEA Workspace",
    });
    vi.mocked(api.createIAMProvider).mockResolvedValue({
      ...SAMPLE_IAM_PROVIDER,
      id: 41,
      name: "GCP IAM Prod",
      provider_type: "gcp_iam",
    });

    renderIntegrations();
    await screen.findByText("Core GitHub Sync");

    const identityTabs = screen.getAllByRole("button", {
      name: /Identity & Directories/i,
    });
    fireEvent.click(identityTabs[0]);

    expect(
      await screen.findByText("Google Workspace Directories"),
    ).toBeTruthy();
    expect(screen.getByText("Cloud IAM Providers")).toBeTruthy();

    // Create Google Workspace directory
    fireEvent.click(screen.getByTestId("add-google-workspace-btn"));
    fireEvent.change(screen.getByLabelText("Provider Name *"), {
      target: { value: "EMEA Workspace" },
    });
    fireEvent.change(screen.getByLabelText("Workspace Domain"), {
      target: { value: "emea.example.com" },
    });
    fireEvent.change(screen.getByLabelText("Customer ID *"), {
      target: { value: "C98765432" },
    });
    fireEvent.change(screen.getByLabelText("Delegated Admin Email *"), {
      target: { value: "admin@emea.example.com" },
    });
    fireEvent.change(screen.getByLabelText("Service Account JSON *"), {
      target: {
        value: JSON.stringify({
          type: "service_account", // nosemgrep: generic.secrets.security.detected-google-gcm-service-account.detected-google-gcm-service-account -- identity-type enum value / placeholder text, not a credential
          project_id: "emea-proj",
          private_key: "PEM",
          client_email: "sa@emea.iam.gserviceaccount.com",
        }),
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Directory" }));

    await waitFor(() => {
      expect(api.createGoogleWorkspaceProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "EMEA Workspace",
          organization_id: 1,
          domain: "emea.example.com",
          customer_id: "C98765432",
          admin_email: "admin@emea.example.com",
          service_account_json: expect.objectContaining({
            project_id: "emea-proj",
          }),
        }),
      );
    });

    // Create IAM provider (GCP IAM)
    fireEvent.click(screen.getByTestId("add-iam-provider-btn"));
    fireEvent.change(screen.getByLabelText("Provider Name *"), {
      target: { value: "GCP IAM Prod" },
    });
    fireEvent.change(screen.getByLabelText("Provider Type *"), {
      target: { value: "gcp_iam" },
    });
    fireEvent.change(screen.getByLabelText("GCP Project ID *"), {
      target: { value: "gcp-iam-proj" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Provider" }));

    await waitFor(() => {
      expect(api.createIAMProvider).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "GCP IAM Prod",
          provider_type: "gcp_iam",
          organization_id: 1,
          config: expect.objectContaining({
            project_id: "gcp-iam-proj",
          }),
        }),
      );
    });
  });
});
