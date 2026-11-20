import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import SyncConfig from "./SyncConfig";
import api, { type SyncConfigItem } from "../lib/api";

vi.mock("../lib/api", () => ({
  default: {
    getSyncConfigs: vi.fn(),
    getSyncStatus: vi.fn(),
    getOrganizations: vi.fn(),
    createSyncConfig: vi.fn(),
    updateSyncConfig: vi.fn(),
    deleteSyncConfig: vi.fn(),
    testSyncConfig: vi.fn(),
    triggerSyncConfig: vi.fn(),
    getSyncMappings: vi.fn(),
    getSyncHistory: vi.fn(),
    getSyncConflicts: vi.fn(),
    resolveSyncConflict: vi.fn(),
  },
}));

const SAMPLE_GITHUB_APP_CONFIG: SyncConfigItem = {
  id: 1,
  name: "Core GitHub Repo",
  platform: "github",
  enabled: true,
  sync_interval: 300,
  batch_fallback_enabled: true,
  batch_size: 100,
  two_way_create: true,
  webhook_enabled: true,
  has_webhook_secret: true,
  last_sync_at: "2026-11-20T12:00:00Z",
  last_batch_sync_at: "2026-11-20T12:00:00Z",
  config_json: {
    auth_mode: "github_app",
    owner: "penguintechinc",
    repo_name: "elder",
    app_id: "112233",
    installation_id: "445566",
    private_key: "***REDACTED***",
    elder_organization_id: 1,
    resource_types: ["project", "milestone", "label", "issue"],
    direction: "bidirectional",
  },
  created_at: "2026-11-20T10:00:00Z",
  updated_at: "2026-11-20T12:00:00Z",
};

const SAMPLE_LEGACY_PAT_CONFIG: SyncConfigItem = {
  id: 2,
  name: "Legacy PAT Repo",
  platform: "github",
  enabled: true,
  sync_interval: 300,
  batch_fallback_enabled: true,
  batch_size: 100,
  two_way_create: false,
  webhook_enabled: true,
  has_webhook_secret: false,
  last_sync_at: null,
  last_batch_sync_at: null,
  config_json: {
    owner: "penguintechinc",
    repo_name: "legacy-repo",
    api_token: "***REDACTED***",
    elder_organization_id: 1,
    resource_types: ["issue"],
    direction: "pull",
  },
  created_at: "2026-11-20T10:00:00Z",
  updated_at: "2026-11-20T12:00:00Z",
};

function renderSyncConfig() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SyncConfig />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SyncConfig", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSyncConfigs).mockResolvedValue({
      configs: [SAMPLE_GITHUB_APP_CONFIG],
    });
    vi.mocked(api.getSyncStatus).mockResolvedValue({
      configs: { total: 1, enabled: 1 },
      recent_activity: { syncs_24h: 4, failures_24h: 0 },
      conflicts: { unresolved: 1 },
      mappings: { total: 12 },
    });
    vi.mocked(api.getOrganizations).mockResolvedValue({
      organizations: [{ id: 1, name: "Penguin Tech" }],
    });
  });

  it("renders summary cards, auth mode badges, and resource navigation links", async () => {
    vi.mocked(api.getSyncConfigs).mockResolvedValue({
      configs: [SAMPLE_GITHUB_APP_CONFIG, SAMPLE_LEGACY_PAT_CONFIG],
    });

    renderSyncConfig();

    expect(await screen.findByText("Core GitHub Repo")).toBeTruthy();
    expect(screen.getByText("Legacy PAT Repo")).toBeTruthy();
    expect(screen.getByText("penguintechinc/elder")).toBeTruthy();
    expect(screen.getByText("penguintechinc/legacy-repo")).toBeTruthy();
    expect(screen.getByText("GitHub App")).toBeTruthy();
    expect(screen.getByText("PAT (Legacy)")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Issues" }).getAttribute("href"),
    ).toBe("/issues");
    expect(
      screen.getByRole("link", { name: "Milestones" }).getAttribute("href"),
    ).toBe("/milestones");
    expect(
      screen.getByRole("link", { name: "Projects" }).getAttribute("href"),
    ).toBe("/projects");
    expect(
      screen.getByRole("link", { name: "Labels" }).getAttribute("href"),
    ).toBe("/labels");
  });

  it("defaults new GitHub integrations to GitHub App and submits auth_mode github_app", async () => {
    vi.mocked(api.createSyncConfig).mockResolvedValue({
      config: { ...SAMPLE_GITHUB_APP_CONFIG, id: 3, name: "Infra GitHub App" },
    });

    renderSyncConfig();
    await screen.findByText("Core GitHub Repo");

    fireEvent.click(screen.getByTestId("add-sync-config-btn"));

    const appRadio = screen.getByTestId("github-auth-mode-app");
    const patRadio = screen.getByTestId("github-auth-mode-pat");
    expect(appRadio.getAttribute("aria-checked")).toBe("true");
    expect(patRadio.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText(/Required GitHub App Permissions/)).toBeTruthy();
    expect(screen.queryByTestId("github-pat-deprecation-banner")).toBeNull();

    fireEvent.change(screen.getByLabelText("Integration Name *"), {
      target: { value: "Infra GitHub App" },
    });
    fireEvent.change(screen.getByLabelText("Owner / Organization *"), {
      target: { value: "penguintechinc" },
    });
    fireEvent.change(screen.getByLabelText("Repository Name *"), {
      target: { value: "infra" },
    });
    fireEvent.change(screen.getByLabelText("App ID *"), {
      target: { value: "998877" },
    });
    fireEvent.change(screen.getByLabelText("Installation ID *"), {
      target: { value: "554433" },
    });
    const samplePem = [
      "-----BEGIN RSA",
      "PRIVATE KEY-----\nTESTKEY\n-----END RSA",
      "PRIVATE KEY-----",
    ].join(" ");
    fireEvent.change(screen.getByLabelText(/Private Key \(PEM\)/), {
      target: { value: samplePem },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save Integration" }));

    await waitFor(() => {
      expect(api.createSyncConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Infra GitHub App",
          platform: "github",
          config_json: expect.objectContaining({
            auth_mode: "github_app",
            owner: "penguintechinc",
            repo_name: "infra",
            app_id: "998877",
            installation_id: "554433",
            private_key: samplePem,
            elder_organization_id: 1,
            resource_types: ["project", "milestone", "label", "issue"],
            direction: "bidirectional",
          }),
        }),
      );
    });
  });

  it("shows PAT deprecation nudge when PAT mode is toggled and submits auth_mode pat", async () => {
    vi.mocked(api.createSyncConfig).mockResolvedValue({
      config: { ...SAMPLE_LEGACY_PAT_CONFIG, id: 4, name: "Infra PAT" },
    });

    renderSyncConfig();
    await screen.findByText("Core GitHub Repo");

    fireEvent.click(screen.getByTestId("add-sync-config-btn"));
    fireEvent.click(screen.getByTestId("github-auth-mode-pat"));

    expect(
      screen.getByTestId("github-auth-mode-pat").getAttribute("aria-checked"),
    ).toBe("true");
    expect(screen.getByTestId("github-pat-deprecation-banner")).toBeTruthy();
    expect(
      screen.getByText(/Personal Access Tokens are legacy and not recommended/),
    ).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Integration Name *"), {
      target: { value: "Infra PAT" },
    });
    fireEvent.change(screen.getByLabelText("Owner / Organization *"), {
      target: { value: "penguintechinc" },
    });
    fireEvent.change(screen.getByLabelText("Repository Name *"), {
      target: { value: "infra" },
    });
    fireEvent.change(screen.getByLabelText(/GitHub Personal Access Token/), {
      target: { value: "github_pat_secret" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save Integration" }));

    await waitFor(() => {
      expect(api.createSyncConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Infra PAT",
          platform: "github",
          config_json: expect.objectContaining({
            auth_mode: "pat",
            owner: "penguintechinc",
            repo_name: "infra",
            api_token: "github_pat_secret",
          }),
        }),
      );
    });
  });

  it("supports .pem file upload and editing GitHub App config without overwriting redacted private key", async () => {
    vi.mocked(api.updateSyncConfig).mockResolvedValue({
      config: { ...SAMPLE_GITHUB_APP_CONFIG, name: "Core GitHub Renamed" },
    });

    renderSyncConfig();
    await screen.findByText("Core GitHub Repo");

    // 1. Upload .pem file in Create modal
    fireEvent.click(screen.getByTestId("add-sync-config-btn"));
    const pemContent = [
      "-----BEGIN",
      "PRIVATE KEY-----\nUPLOADED_PEM\n-----END",
      "PRIVATE KEY-----",
    ].join(" ");
    const pemFile = new File([pemContent], "elder-app.pem", {
      type: "application/x-pem-file",
    });
    Object.defineProperty(pemFile, "text", {
      value: vi.fn().mockResolvedValue(pemContent),
    });
    fireEvent.change(screen.getByLabelText("Upload .pem file"), {
      target: { files: [pemFile] },
    });

    await waitFor(() => {
      const textarea = screen.getByLabelText(
        /Private Key \(PEM\)/,
      ) as HTMLTextAreaElement;
      expect(textarea.value).toBe(pemContent);
    });

    fireEvent.click(screen.getByRole("button", { name: "Close modal" }));

    // 2. Edit existing GitHub App config without entering a new private key
    fireEvent.click(
      screen.getByRole("button", { name: "Edit Core GitHub Repo" }),
    );
    fireEvent.change(screen.getByLabelText("Integration Name *"), {
      target: { value: "Core GitHub Renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(api.updateSyncConfig).toHaveBeenCalledTimes(1);
    });
    const [, updatePayload] = vi.mocked(api.updateSyncConfig).mock.calls[0];
    expect(updatePayload.name).toBe("Core GitHub Renamed");
    expect(updatePayload.config_json?.auth_mode).toBe("github_app");
    expect(updatePayload.config_json?.app_id).toBe("112233");
    expect(updatePayload.config_json?.installation_id).toBe("445566");
    expect(updatePayload.config_json).not.toHaveProperty("private_key");
  });

  it("creates a Jira Cloud integration with components label mode", async () => {
    vi.mocked(api.createSyncConfig).mockResolvedValue({
      config: {
        ...SAMPLE_GITHUB_APP_CONFIG,
        id: 5,
        name: "Engineering Jira",
        platform: "jira",
      },
    });

    renderSyncConfig();
    await screen.findByText("Core GitHub Repo");

    fireEvent.click(screen.getByTestId("add-sync-config-btn"));
    fireEvent.change(screen.getByLabelText("Platform *"), {
      target: { value: "jira" },
    });
    fireEvent.change(screen.getByLabelText("Integration Name *"), {
      target: { value: "Engineering Jira" },
    });
    fireEvent.change(screen.getByLabelText("Jira Cloud Site URL *"), {
      target: { value: "acme.atlassian.net" },
    });
    fireEvent.change(screen.getByLabelText("Project Key *"), {
      target: { value: "eng" },
    });
    fireEvent.change(screen.getByLabelText("Atlassian Account Email *"), {
      target: { value: "bot@acme.com" },
    });
    fireEvent.change(screen.getByLabelText(/Jira API Token/), {
      target: { value: "ATATT_token_123" },
    });
    fireEvent.change(screen.getByLabelText("Label Mapping Mode"), {
      target: { value: "components" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save Integration" }));

    await waitFor(() => {
      expect(api.createSyncConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Engineering Jira",
          platform: "jira",
          config_json: expect.objectContaining({
            jira_url: "acme.atlassian.net",
            project_key: "ENG",
            email: "bot@acme.com",
            api_token: "ATATT_token_123",
            label_mode: "components",
          }),
        }),
      );
    });
  });

  it("runs Test Connection and Sync Now actions with feedback banners", async () => {
    vi.mocked(api.testSyncConfig).mockResolvedValue({
      connected: true,
      success: true,
      platform: "github",
      auth_mode: "github_app",
      repository: {
        full_name: "penguintechinc/elder",
        default_branch: "main",
      },
    });
    vi.mocked(api.triggerSyncConfig).mockResolvedValue({
      sync_config_id: 1,
      platform: "github",
      correlation_id: "corr-123",
      direction: "bidirectional",
      resource_types: ["project", "milestone", "label", "issue"],
      success: true,
      items_synced: 6,
      items_failed: 0,
      errors: [],
      results: {},
    });

    renderSyncConfig();
    await screen.findByText("Core GitHub Repo");

    fireEvent.click(screen.getByRole("button", { name: /Test Connection/i }));
    expect(
      await screen.findByText('Connection test passed for "Core GitHub Repo"'),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Sync Now/i }));
    expect(
      await screen.findByText('Sync completed for "Core GitHub Repo"'),
    ).toBeTruthy();
  });

  it("opens Details modal and inspects Webhook Setup, Mappings, History, and resolves Conflicts", async () => {
    vi.mocked(api.getSyncMappings).mockResolvedValue({
      mappings: [
        {
          id: 11,
          elder_type: "issue",
          elder_id: 50,
          external_platform: "github",
          external_id: "101",
          sync_config_id: 1,
          sync_status: "synced",
          sync_method: "manual",
          last_synced_at: "2026-11-20T12:00:00Z",
          elder_updated_at: null,
          external_updated_at: null,
        },
      ],
    });
    vi.mocked(api.getSyncHistory).mockResolvedValue({
      history: [
        {
          id: 21,
          sync_config_id: 1,
          correlation_id: "c1",
          sync_type: "manual",
          items_synced: 4,
          items_failed: 0,
          started_at: "2026-11-20T12:00:00Z",
          completed_at: "2026-11-20T12:00:02Z",
          success: true,
          error_message: null,
          sync_metadata: null,
        },
      ],
      pagination: { page: 1, per_page: 50, total: 1, pages: 1 },
    });
    vi.mocked(api.getSyncConflicts).mockResolvedValue({
      conflicts: [
        {
          id: 31,
          mapping_id: 11,
          conflict_type: "concurrent_modification",
          elder_data: { title: "Elder Title" },
          external_data: { title: "Remote Title" },
          resolution_strategy: null,
          resolved: false,
          resolved_at: null,
          resolved_by_id: null,
          created_at: "2026-11-20T12:00:00Z",
        },
      ],
    });
    vi.mocked(api.resolveSyncConflict).mockResolvedValue({
      conflict: {
        id: 31,
        mapping_id: 11,
        conflict_type: "concurrent_modification",
        elder_data: { title: "Elder Title" },
        external_data: { title: "Remote Title" },
        resolution_strategy: "external_wins",
        resolved: true,
        resolved_at: "2026-11-20T12:05:00Z",
        resolved_by_id: 1,
        created_at: "2026-11-20T12:00:00Z",
      },
    });

    renderSyncConfig();
    await screen.findByText("Core GitHub Repo");

    fireEvent.click(screen.getByRole("button", { name: /Details/i }));
    expect(
      await screen.findByText(/\/api\/v1\/sync\/webhooks\/github\/1/),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Mappings" }));
    expect(await screen.findByText("101")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText("4 / 0")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Conflicts" }));
    expect(
      await screen.findByText(/Conflict #31 \(concurrent_modification\)/),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Keep External" }));
    await waitFor(() => {
      expect(api.resolveSyncConflict).toHaveBeenCalledWith(31, "external_wins");
    });
  });
});
