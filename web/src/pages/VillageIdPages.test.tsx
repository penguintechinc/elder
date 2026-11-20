import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import api from "@/lib/api";
import Software from "./Software";
import Services from "./Services";
import Vulnerabilities from "./Vulnerabilities";
import SBOMDashboard from "./SBOMDashboard";
import OnCallRotations from "./OnCallRotations";
import Networking from "./Networking";
import IPAM from "./IPAM";
import Dependencies from "./Dependencies";
import Secrets from "./Secrets";
import Keys from "./Keys";
import Certificates from "./Certificates";
import IAM from "./IAM";
import AIResources from "./AIResources";
import Webhooks from "./Webhooks";
import Backups from "./Backups";
import LicensePolicies from "./LicensePolicies";
import Integrations from "./Integrations";

vi.mock("react-hot-toast", () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@penguintechinc/react-libs/components", () => ({
  FormModalBuilder: () => null,
}));

vi.mock("@/components/OnCallBadge", () => ({
  default: () => <span data-testid="oncall-badge" />,
}));

vi.mock("@/components/NetworkTopologyGraph", () => ({
  default: () => <div data-testid="topology-graph" />,
}));

vi.mock("@/components/GroupMembershipManager", () => ({
  default: () => (
    <div data-testid="group-membership-manager">Groups &amp; Roles Manager</div>
  ),
}));

vi.mock("@/components/AccessReviewManager", () => ({
  default: () => <div data-testid="access-review-manager" />,
}));

vi.mock("@/components/CreateOnCallRotationModal", () => ({
  default: () => null,
}));

vi.mock("@/components/OnCallRotationDetailModal", () => ({
  default: () => null,
}));

vi.mock("@/components/CsvImportModal", () => ({
  default: () => null,
}));

vi.mock("@/components/AssigneePicker", () => ({
  default: () => <div data-testid="assignee-picker" />,
}));

vi.mock("@/lib/api", () => ({
  default: {
    getOrganizations: vi.fn(),
    getEntities: vi.fn(),
    getSoftware: vi.fn(),
    getServices: vi.fn(),
    getVulnerabilities: vi.fn(),
    getOnCallRotations: vi.fn(),
    listNetworks: vi.fn(),
    listTopologyConnections: vi.fn(),
    getIpamPrefixes: vi.fn(),
    getIpamAddresses: vi.fn(),
    getIpamVlans: vi.fn(),
    getDependencies: vi.fn(),
    getIdentities: vi.fn(),
    getProjects: vi.fn(),
    getMilestones: vi.fn(),
    getIssues: vi.fn(),
    getDataStores: vi.fn(),
    getSecretProviders: vi.fn(),
    listSecrets: vi.fn(),
    getKeyProviders: vi.fn(),
    getCertificates: vi.fn(),
    getPendingGroupAccessRequests: vi.fn(),
    getIAMProviders: vi.fn(),
    getGoogleWorkspaceProviders: vi.fn(),
    getAIResources: vi.fn(),
    getPortalProfile: vi.fn(),
    getWebhooks: vi.fn(),
    getInboundWebhooks: vi.fn(),
    getInboundWebhookScopes: vi.fn(),
    getInboundWebhookEvents: vi.fn(),
    listStreams: vi.fn(),
    getBackupJobs: vi.fn(),
    getBackups: vi.fn(),
    getLicensePolicies: vi.fn(),
    getSyncConfigs: vi.fn(),
    getDiscoveryJobs: vi.fn(),
  },
}));

function renderWithRouter(ui: ReactElement, initialEntry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("VillageIdBadge & ?highlight= support across infra/security pages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [
        {
          id: 1,
          name: "Acme Org",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    vi.mocked(api.getEntities).mockResolvedValue({ items: [] });
    vi.mocked(api.getIdentities).mockResolvedValue({ items: [] });
    vi.mocked(api.getProjects).mockResolvedValue({ items: [] });
    vi.mocked(api.getMilestones).mockResolvedValue({ items: [] });
    vi.mocked(api.getIssues).mockResolvedValue({ items: [] });
    vi.mocked(api.getDataStores).mockResolvedValue({ items: [] });
    vi.mocked(api.getServices).mockResolvedValue({ items: [] });
    vi.mocked(api.getSoftware).mockResolvedValue({ items: [] });
    vi.mocked(api.listNetworks).mockResolvedValue({ networks: [] });
    vi.mocked(api.listTopologyConnections).mockResolvedValue({
      connections: [],
    });
    vi.mocked(api.getPendingGroupAccessRequests).mockResolvedValue({
      requests: [],
    });
    vi.mocked(api.getIAMProviders).mockResolvedValue({ providers: [] });
    vi.mocked(api.getGoogleWorkspaceProviders).mockResolvedValue({
      providers: [],
    });
    vi.mocked(api.getSyncConfigs).mockResolvedValue({ configs: [] });
    vi.mocked(api.getDiscoveryJobs).mockResolvedValue({ jobs: [] });
    vi.mocked(api.getPortalProfile).mockResolvedValue({
      global_role: "admin",
      tenant_role: null,
    });
    vi.mocked(api.getWebhooks).mockResolvedValue({ webhooks: [] });
    vi.mocked(api.getInboundWebhooks).mockResolvedValue({
      inbound_webhooks: [],
      count: 0,
    });
    vi.mocked(api.getInboundWebhookScopes).mockResolvedValue({ scopes: [] });
    vi.mocked(api.getInboundWebhookEvents).mockResolvedValue({
      events: [],
      count: 0,
    });
    vi.mocked(api.listStreams).mockResolvedValue({ data: [] });
  });

  it("Software renders VillageIdBadge and highlights/opens modal on ?highlight=", async () => {
    vi.mocked(api.getSoftware).mockResolvedValue({
      items: [
        {
          id: 101,
          name: "GitLab Enterprise",
          vendor: "GitLab",
          organization_id: 1,
          software_type: "saas",
          village_id: "0001-sw0101",
        },
        {
          id: 102,
          name: "Legacy Tool",
          organization_id: 1,
          software_type: "desktop",
        },
      ],
    });

    const { container } = renderWithRouter(
      <Software />,
      "/software?highlight=101",
    );

    await waitFor(() => {
      expect(screen.getAllByText("0001-sw0101").length).toBeGreaterThanOrEqual(
        1,
      );
    });
    const highlighted = container.querySelector('[data-highlighted="true"]');
    expect(highlighted).not.toBeNull();
    expect(highlighted?.id).toBe("software-101");
    // Detail modal auto-opens with VillageIdBadge shown inside
    expect(screen.getAllByText("0001-sw0101").length).toBe(2);
  });

  it("Services renders VillageIdBadge and highlights/opens modal on ?highlight=", async () => {
    vi.mocked(api.getServices).mockResolvedValue({
      items: [
        {
          id: 201,
          name: "Billing API",
          organization_id: 1,
          language: "python",
          deployment_method: "kubernetes",
          status: "active",
          village_id: "0001-sv0201",
        },
      ],
    });

    const { container } = renderWithRouter(
      <Services />,
      "/services?highlight=201",
    );

    await waitFor(() => {
      expect(screen.getAllByText("0001-sv0201").length).toBeGreaterThanOrEqual(
        1,
      );
    });
    expect(
      container.querySelector('#service-201[data-highlighted="true"]'),
    ).not.toBeNull();
  });

  it("Networking auto-selects org, renders VillageIdBadge, and highlights matching network on ?highlight=", async () => {
    vi.mocked(api.listNetworks).mockResolvedValue({
      networks: [
        {
          id: 301,
          name: "Prod VPC",
          network_type: "vpc",
          cidr: "10.0.0.0/16",
          is_active: true,
          organization_id: 1,
          village_id: "0001-nw0301",
        },
      ],
    });

    const { container } = renderWithRouter(
      <Networking />,
      "/networking?highlight=301",
    );

    await waitFor(() => {
      expect(screen.getByText("0001-nw0301")).toBeTruthy();
    });
    expect(
      container.querySelector('#network-301[data-highlighted="true"]'),
    ).not.toBeNull();
  });

  it("IPAM honors ?tab=prefixes|addresses|vlans and ?highlight= with VillageIdBadge", async () => {
    vi.mocked(api.getIpamPrefixes).mockResolvedValue({
      items: [
        {
          id: 401,
          prefix: "10.0.0.0/16",
          status: "active",
          is_pool: false,
          organization_id: 1,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          village_id: "0001-pf0401",
        },
        {
          id: 402,
          prefix: "10.0.1.0/24",
          status: "active",
          is_pool: true,
          parent_id: 401,
          organization_id: 1,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          village_id: "0001-pf0402",
        },
      ],
    });
    vi.mocked(api.getIpamAddresses).mockResolvedValue({
      items: [
        {
          id: 411,
          address: "10.0.1.10/32",
          status: "active",
          organization_id: 1,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          village_id: "0001-ip0411",
        },
      ],
    });
    vi.mocked(api.getIpamVlans).mockResolvedValue({
      items: [
        {
          id: 421,
          vid: 100,
          name: "Prod VLAN",
          status: "active",
          organization_id: 1,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          village_id: "0001-vl0421",
        },
      ],
    });

    // Nested child prefix auto-expands and highlights
    const prefixesView = renderWithRouter(
      <IPAM />,
      "/ipam?tab=prefixes&highlight=402",
    );
    await waitFor(() => {
      expect(screen.getByText("0001-pf0402")).toBeTruthy();
    });
    expect(
      prefixesView.container.querySelector(
        '#ipam-prefix-402[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    prefixesView.unmount();

    // Addresses tab
    const addressesView = renderWithRouter(
      <IPAM />,
      "/ipam?tab=addresses&highlight=411",
    );
    await waitFor(() => {
      expect(screen.getByText("0001-ip0411")).toBeTruthy();
    });
    expect(
      addressesView.container.querySelector(
        '#ipam-address-411[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    addressesView.unmount();

    // VLANs tab
    const vlansView = renderWithRouter(
      <IPAM />,
      "/ipam?tab=vlans&highlight=421",
    );
    await waitFor(() => {
      expect(screen.getByText("0001-vl0421")).toBeTruthy();
    });
    expect(
      vlansView.container.querySelector(
        '#ipam-vlan-421[data-highlighted="true"]',
      ),
    ).not.toBeNull();
  });

  it("AIResources renders VillageIdBadge in row & detail modal and highlights on ?highlight=", async () => {
    vi.mocked(api.getAIResources).mockResolvedValue({
      items: [
        {
          id: 501,
          tenant_id: 1,
          name: "Claude Enterprise",
          organization_id: 1,
          resource_type: "llm",
          provider: "Anthropic",
          model_name: "claude-sonnet-4",
          status: "allowed",
          is_active: true,
          village_id: "0001-ai0501",
        },
      ],
      total: 1,
      page: 1,
      per_page: 100,
      pages: 1,
    });

    const { container } = renderWithRouter(
      <AIResources />,
      "/ai?highlight=501",
    );

    await waitFor(() => {
      expect(screen.getAllByText("0001-ai0501").length).toBe(2);
    });
    expect(
      container.querySelector('#ai-resource-501[data-highlighted="true"]'),
    ).not.toBeNull();
  });

  it("Secrets renders VillageIdBadge on provider cards & secret list modal and highlights on ?highlight=", async () => {
    vi.mocked(api.getSecretProviders).mockResolvedValue({
      providers: [
        {
          id: 601,
          name: "Prod AWS Secrets",
          provider_type: "aws_secrets_manager",
          enabled: true,
          village_id: "0001-sp0601",
        },
      ],
    });
    vi.mocked(api.listSecrets).mockResolvedValue({
      secrets: [
        {
          id: 611,
          name: "DB_PASSWORD",
          village_id: "0001-sc0611",
        },
      ],
    });

    const { container } = renderWithRouter(
      <Secrets />,
      "/secrets?highlight=601",
    );

    await waitFor(() => {
      expect(screen.getByText("0001-sp0601")).toBeTruthy();
    });
    expect(
      container.querySelector('#secret-provider-601[data-highlighted="true"]'),
    ).not.toBeNull();

    fireEvent.click(screen.getByText("View Secrets"));
    await waitFor(() => {
      expect(screen.getByText("0001-sc0611")).toBeTruthy();
    });
  });

  it("IAM honors ?highlight= for identities and ?tab=groups for groups", async () => {
    vi.mocked(api.getIdentities).mockResolvedValue({
      items: [
        {
          id: 801,
          username: "jdoe",
          email: "jdoe@example.com",
          full_name: "Jane Doe",
          identity_type: "employee",
          auth_provider: "local",
          is_active: true,
          is_superuser: false,
          created_at: "2026-01-01T00:00:00Z",
          village_id: "0001-id0801",
        } as never,
      ],
    });

    const identityView = renderWithRouter(<IAM />, "/iam?highlight=801");
    await waitFor(() => {
      expect(screen.getAllByText("0001-id0801").length).toBeGreaterThanOrEqual(
        1,
      );
    });
    expect(
      identityView.container.querySelector(
        '#identity-801[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    identityView.unmount();

    renderWithRouter(<IAM />, "/iam?tab=groups&highlight=802");
    await waitFor(() => {
      expect(screen.getByTestId("group-membership-manager")).toBeTruthy();
    });
  });

  it("Webhooks renders VillageIdBadge and auto-switches tab on ?highlight=", async () => {
    vi.mocked(api.getWebhooks).mockResolvedValue({
      webhooks: [
        {
          id: 902,
          name: "PagerDuty Outbound",
          url: "https://hooks.example.com/pd",
          organization_id: 1,
          events: ["issue.created"],
          is_active: true,
          village_id: "0001-wh0902",
        },
      ],
    });

    const { container } = renderWithRouter(
      <Webhooks />,
      "/webhooks?highlight=902",
    );

    await waitFor(() => {
      expect(screen.getByText("0001-wh0902")).toBeTruthy();
    });
    expect(
      container.querySelector('#outbound-webhook-902[data-highlighted="true"]'),
    ).not.toBeNull();
  });

  it("Vulnerabilities, OnCallRotations, Dependencies, Keys, Certificates, Backups, LicensePolicies, and Integrations render VillageIdBadge and highlight cleanly", async () => {
    vi.mocked(api.getVulnerabilities).mockResolvedValue({
      items: [
        {
          id: 11,
          cve_id: "CVE-2026-0001",
          severity: "high",
          component: "openssl",
          status: "open",
          published_date: "2026-01-01T00:00:00Z",
          description: "Buffer issue",
          affected_versions: ["1.0.0"],
          affected_entities: [],
          village_id: "0001-vu0011",
        },
      ],
      total: 1,
      page: 1,
      per_page: 20,
      pages: 1,
    });
    const vulnView = renderWithRouter(
      <Vulnerabilities />,
      "/vulnerabilities?highlight=11",
    );
    await waitFor(() => {
      expect(screen.getByText("0001-vu0011")).toBeTruthy();
    });
    expect(
      vulnView.container.querySelector(
        '#vulnerability-11[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    vulnView.unmount();

    vi.mocked(api.getOnCallRotations).mockResolvedValue({
      items: [
        {
          id: 12,
          name: "SRE Primary",
          schedule_type: "weekly",
          scope_type: "organization",
          is_active: true,
          organization_id: 1,
          village_id: "0001-oc0012",
        },
      ],
    });
    const onCallView = renderWithRouter(
      <OnCallRotations />,
      "/on-call-rotations?highlight=12",
    );
    await waitFor(() => {
      expect(screen.getByText("0001-oc0012")).toBeTruthy();
    });
    expect(
      onCallView.container.querySelector(
        '#rotation-12[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    onCallView.unmount();

    vi.mocked(api.getDependencies).mockResolvedValue({
      items: [
        {
          id: 13,
          tenant_id: 1,
          source_type: "service",
          source_id: 1,
          target_type: "entity",
          target_id: 2,
          dependency_type: "depends",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          village_id: "0001-dp0013",
        },
      ],
    });
    const depsView = renderWithRouter(
      <Dependencies />,
      "/dependencies?highlight=13",
    );
    await waitFor(() => {
      expect(screen.getByText("0001-dp0013")).toBeTruthy();
    });
    expect(
      depsView.container.querySelector(
        '#dependency-13[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    depsView.unmount();

    vi.mocked(api.getKeyProviders).mockResolvedValue({
      providers: [
        {
          id: 14,
          name: "Primary KMS",
          provider_type: "aws_kms",
          enabled: true,
          organization_id: 1,
          village_id: "0001-kp0014",
        },
      ],
    });
    const keysView = renderWithRouter(<Keys />, "/keys?highlight=14");
    await waitFor(() => {
      expect(screen.getByText("0001-kp0014")).toBeTruthy();
    });
    expect(
      keysView.container.querySelector(
        '#key-provider-14[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    keysView.unmount();

    vi.mocked(api.getCertificates).mockResolvedValue({
      certificates: [
        {
          id: 15,
          name: "Wildcard TLS",
          organization_id: 1,
          creator: "letsencrypt",
          cert_type: "wildcard",
          status: "active",
          expiration_date: "2027-12-31T00:00:00Z",
          village_id: "0001-ct0015",
        },
      ],
    });
    const certsView = renderWithRouter(
      <Certificates />,
      "/certificates?highlight=15",
    );
    await waitFor(() => {
      expect(screen.getAllByText("0001-ct0015").length).toBe(2);
    });
    expect(
      certsView.container.querySelector(
        '#certificate-15[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    certsView.unmount();

    vi.mocked(api.getBackupJobs).mockResolvedValue({
      jobs: [
        {
          id: 16,
          name: "Nightly DB",
          schedule: "0 2 * * *",
          retention_days: 30,
          enabled: true,
          village_id: "0001-bj0016",
        },
      ],
    });
    vi.mocked(api.getBackups).mockResolvedValue({
      backups: [
        {
          id: 17,
          status: "completed",
          created_at: "2026-01-01T00:00:00Z",
          size_bytes: 1048576,
          village_id: "0001-bk0017",
        },
      ],
    });
    const backupsView = renderWithRouter(<Backups />, "/backups?highlight=17");
    await waitFor(() => {
      expect(screen.getByText("0001-bj0016")).toBeTruthy();
      expect(screen.getByText("0001-bk0017")).toBeTruthy();
    });
    expect(
      backupsView.container.querySelector(
        '#backup-17[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    backupsView.unmount();

    vi.mocked(api.getLicensePolicies).mockResolvedValue({
      items: [
        {
          id: 18,
          name: "Permissive Only",
          allowed_patterns: "MIT, Apache-2.0",
          denied_patterns: "GPL-*",
          is_active: true,
          village_id: "0001-lp0018",
        },
      ],
    });
    const lpView = renderWithRouter(
      <LicensePolicies />,
      "/admin/license-policies?highlight=18",
    );
    await waitFor(() => {
      expect(screen.getByText("0001-lp0018")).toBeTruthy();
    });
    expect(
      lpView.container.querySelector(
        '#license-policy-18[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    lpView.unmount();

    vi.mocked(api.getDiscoveryJobs).mockResolvedValue({
      jobs: [
        {
          id: 19,
          name: "AWS Prod Discovery",
          provider: "aws",
          config_json: { region: "us-east-1" },
          schedule_interval: 3600,
          enabled: true,
          village_id: "0001-dj0019",
        } as never,
      ],
    });
    const intView = renderWithRouter(
      <Integrations />,
      "/admin/integrations?highlight=19",
    );
    await waitFor(() => {
      expect(screen.getByText("0001-dj0019")).toBeTruthy();
    });
    expect(
      intView.container.querySelector(
        '#integration-cloud-19[data-highlighted="true"]',
      ),
    ).not.toBeNull();
    intView.unmount();

    renderWithRouter(<SBOMDashboard />, "/sbom?highlight=20");
    expect(screen.getByText("SBOM Dashboard")).toBeTruthy();
  });
});
