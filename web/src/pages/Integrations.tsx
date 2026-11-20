import { useMemo, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Building2,
  Cloud,
  GitBranch,
  KeyRound,
  Laptop,
  LayoutGrid,
  Plug,
  Plus,
  Search,
  Shield,
  Users,
  Webhook,
  X,
} from "lucide-react";
import api, {
  type CloudDiscoveryProvider,
  type DiscoveryJobItem,
  type GoogleWorkspaceProviderItem,
  type IAMProviderItem,
  type IAMProviderType,
  type SyncConfigItem,
} from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardContent, CardHeader } from "@/components/Card";
import SyncConfig from "./SyncConfig";
import CloudProfilesPanel from "@/components/integrations/CloudProfilesPanel";
import EndpointsPanel from "@/components/integrations/EndpointsPanel";
import IdentityIntegrationsPanel from "@/components/integrations/IdentityIntegrationsPanel";
import { formatScheduleInterval } from "@/components/integrations/shared";

type IntegrationCategory =
  | "overview"
  | "work-management"
  | "cloud"
  | "endpoints"
  | "identity";

const VALID_CATEGORIES = new Set<IntegrationCategory>([
  "overview",
  "work-management",
  "cloud",
  "endpoints",
  "identity",
]);

const CLOUD_PROVIDER_KEYS = new Set([
  "aws",
  "gcp",
  "azure",
  "kubernetes",
  "vultr",
]);

interface UnifiedIntegrationRow {
  key: string;
  name: string;
  provider: string;
  category: Exclude<IntegrationCategory, "overview">;
  categoryLabel: string;
  target: string;
  enabled: boolean;
  cadence: string;
}

interface CatalogEntry {
  id: string;
  title: string;
  category: Exclude<IntegrationCategory, "overview">;
  categoryLabel: string;
  description: string;
  cloudProvider?: CloudDiscoveryProvider;
  identityKind?: "google_workspace" | IAMProviderType;
}

const CATALOG_ENTRIES: CatalogEntry[] = [
  {
    id: "github",
    title: "GitHub (App / PAT)",
    category: "work-management",
    categoryLabel: "Work Management",
    description: "Two-way sync for issues, milestones, labels & Projects v2.",
  },
  {
    id: "jira",
    title: "Jira Cloud",
    category: "work-management",
    categoryLabel: "Work Management",
    description: "Sync Jira Cloud projects, JQL issues, versions & components.",
  },
  {
    id: "aws",
    title: "AWS Discovery",
    category: "cloud",
    categoryLabel: "Cloud & Infrastructure",
    description: "EC2, RDS, S3, Lambda, VPCs & IAM via keys, IRSA, or STS.",
    cloudProvider: "aws",
  },
  {
    id: "gcp",
    title: "GCP Discovery",
    category: "cloud",
    categoryLabel: "Cloud & Infrastructure",
    description: "GCE, GCS, Cloud SQL, VPCs via Service Account JSON or ADC.",
    cloudProvider: "gcp",
  },
  {
    id: "azure",
    title: "Azure Discovery",
    category: "cloud",
    categoryLabel: "Cloud & Infrastructure",
    description: "VMs, Storage, Networks via Service Principal or Managed ID.",
    cloudProvider: "azure",
  },
  {
    id: "kubernetes",
    title: "Kubernetes Discovery",
    category: "cloud",
    categoryLabel: "Cloud & Infrastructure",
    description: "Deployments, pods, services & certificates via kubeconfig.",
    cloudProvider: "kubernetes",
  },
  {
    id: "vultr",
    title: "Vultr Discovery",
    category: "cloud",
    categoryLabel: "Cloud & Infrastructure",
    description: "Discover Vultr instances, bare metal & block/object storage.",
    cloudProvider: "vultr",
  },
  {
    id: "fleetdm",
    title: "FleetDM Endpoints",
    category: "endpoints",
    categoryLabel: "Endpoints & Security",
    description: "Hosts, OS versions, CVEs, labels, software & MDM posture.",
  },
  {
    id: "google_workspace",
    title: "Google Workspace",
    category: "identity",
    categoryLabel: "Identity & Directories",
    description: "Sync workspace users, groups & OUs via domain delegation.",
    identityKind: "google_workspace",
  },
  {
    id: "aws_iam",
    title: "AWS IAM Directory",
    category: "identity",
    categoryLabel: "Identity & Directories",
    description: "Audit AWS IAM users, roles, groups & attached policies.",
    identityKind: "aws_iam",
  },
  {
    id: "gcp_iam",
    title: "GCP IAM Directory",
    category: "identity",
    categoryLabel: "Identity & Directories",
    description: "Inventory GCP service accounts and project IAM bindings.",
    identityKind: "gcp_iam",
  },
  {
    id: "k8s_rbac",
    title: "Kubernetes RBAC",
    category: "identity",
    categoryLabel: "Identity & Directories",
    description: "Inspect service accounts, roles & cluster role bindings.",
    identityKind: "kubernetes",
  },
];

const CTA_LABELS: Record<IntegrationCategory, string> = {
  overview: "Connect Integration",
  "work-management": "New Sync Config",
  cloud: "New Cloud Profile",
  endpoints: "New Endpoint Profile",
  identity: "New Directory Provider",
};

function describeDiscoveryTarget(job: DiscoveryJobItem): string {
  const cfg = (job.config_json || {}) as Record<string, unknown>;
  if (job.provider === "aws") {
    return cfg.region ? `Region ${String(cfg.region)}` : "AWS account";
  }
  if (job.provider === "gcp") {
    return cfg.project_id ? `Project ${String(cfg.project_id)}` : "GCP project";
  }
  if (job.provider === "azure") {
    return cfg.subscription_id
      ? `Sub ${String(cfg.subscription_id)}`
      : "Azure subscription";
  }
  if (job.provider === "kubernetes") {
    return `Namespace ${String(cfg.namespace || "default")}`;
  }
  if (job.provider === "fleetdm") {
    return String(cfg.url || cfg.fleetdm_url || "FleetDM server");
  }
  return job.provider.toUpperCase();
}

export default function Integrations() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get("tab") || "overview";
  const activeTab: IntegrationCategory = VALID_CATEGORIES.has(
    rawTab as IntegrationCategory,
  )
    ? (rawTab as IntegrationCategory)
    : "overview";

  const [overviewFilter, setOverviewFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [showConnectCatalog, setShowConnectCatalog] = useState(false);

  // Signals for triggering create modals inside each panel
  const [syncCreateSignal, setSyncCreateSignal] = useState(0);
  const [cloudCreateRequest, setCloudCreateRequest] = useState<{
    nonce: number;
    provider?: CloudDiscoveryProvider;
  }>({ nonce: 0 });
  const [endpointCreateNonce, setEndpointCreateNonce] = useState(0);
  const [identityCreateRequest, setIdentityCreateRequest] = useState<{
    nonce: number;
    kind?: "google_workspace" | IAMProviderType;
  }>({ nonce: 0 });

  const { data: syncData } = useQuery({
    queryKey: ["sync-configs"],
    queryFn: () => api.getSyncConfigs(),
  });

  const { data: discoveryData } = useQuery({
    queryKey: ["discoveryJobs"],
    queryFn: () => api.getDiscoveryJobs(),
  });

  const { data: gwsData } = useQuery({
    queryKey: ["googleWorkspaceProviders"],
    queryFn: () => api.getGoogleWorkspaceProviders(),
  });

  const { data: iamData } = useQuery({
    queryKey: ["iamProviders"],
    queryFn: () => api.getIAMProviders(),
  });

  const syncConfigs: SyncConfigItem[] = useMemo(
    () => syncData?.configs ?? [],
    [syncData?.configs],
  );
  const allDiscoveryJobs: DiscoveryJobItem[] = useMemo(
    () => discoveryData?.jobs ?? [],
    [discoveryData?.jobs],
  );
  const cloudJobs = useMemo(
    () => allDiscoveryJobs.filter((j) => CLOUD_PROVIDER_KEYS.has(j.provider)),
    [allDiscoveryJobs],
  );
  const fleetJobs = useMemo(
    () => allDiscoveryJobs.filter((j) => j.provider === "fleetdm"),
    [allDiscoveryJobs],
  );
  const gwsProviders: GoogleWorkspaceProviderItem[] = useMemo(
    () => gwsData?.providers ?? [],
    [gwsData?.providers],
  );
  const iamProviders: IAMProviderItem[] = useMemo(
    () => iamData?.providers ?? [],
    [iamData?.providers],
  );

  const identityCount = gwsProviders.length + iamProviders.length;
  const totalCount =
    syncConfigs.length + cloudJobs.length + fleetJobs.length + identityCount;

  const unifiedRows: UnifiedIntegrationRow[] = useMemo(() => {
    const rows: UnifiedIntegrationRow[] = [];

    for (const cfg of syncConfigs) {
      const json = (cfg.config_json || {}) as Record<string, unknown>;
      const target =
        cfg.platform === "github"
          ? `${String(json.owner || "?")}/${String(json.repo_name || "?")}`
          : cfg.platform === "jira"
            ? `${String(json.jira_url || "?")} (${String(json.project_key || "?")})`
            : String(json.base_url || cfg.platform);
      rows.push({
        key: `sync-${cfg.id}`,
        name: cfg.name,
        provider: cfg.platform.toUpperCase(),
        category: "work-management",
        categoryLabel: "Work Management",
        target,
        enabled: cfg.enabled,
        cadence: formatScheduleInterval(cfg.sync_interval),
      });
    }

    for (const job of cloudJobs) {
      rows.push({
        key: `cloud-${job.id}`,
        name: job.name,
        provider: job.provider.toUpperCase(),
        category: "cloud",
        categoryLabel: "Cloud & Infrastructure",
        target: describeDiscoveryTarget(job),
        enabled: job.enabled,
        cadence: formatScheduleInterval(job.schedule_interval),
      });
    }

    for (const job of fleetJobs) {
      rows.push({
        key: `endpoint-${job.id}`,
        name: job.name,
        provider: "FleetDM",
        category: "endpoints",
        categoryLabel: "Endpoints & Security",
        target: describeDiscoveryTarget(job),
        enabled: job.enabled,
        cadence: formatScheduleInterval(job.schedule_interval),
      });
    }

    for (const gws of gwsProviders) {
      rows.push({
        key: `gws-${gws.id}`,
        name: gws.name,
        provider: "Google Workspace",
        category: "identity",
        categoryLabel: "Identity & Directories",
        target: gws.domain || gws.customer_id || "Directory",
        enabled: gws.enabled,
        cadence: gws.last_sync_at
          ? `Synced ${new Date(gws.last_sync_at).toLocaleDateString()}`
          : "On demand",
      });
    }

    for (const iam of iamProviders) {
      rows.push({
        key: `iam-${iam.id}`,
        name: iam.name,
        provider: iam.provider_type.toUpperCase(),
        category: "identity",
        categoryLabel: "Identity & Directories",
        target:
          iam.description ||
          String(
            (iam.config_json || {}).project_id ||
              (iam.config_json || {}).region ||
              (iam.config_json || {}).namespace ||
              "IAM Directory",
          ),
        enabled: iam.enabled,
        cadence: iam.last_sync_at
          ? `Synced ${new Date(iam.last_sync_at).toLocaleDateString()}`
          : "On demand",
      });
    }

    return rows;
  }, [syncConfigs, cloudJobs, fleetJobs, gwsProviders, iamProviders]);

  const activeCount = useMemo(
    () => unifiedRows.filter((row) => row.enabled).length,
    [unifiedRows],
  );

  const filteredOverviewRows = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return unifiedRows.filter((row) => {
      if (overviewFilter !== "all" && row.category !== overviewFilter) {
        return false;
      }
      if (!q) return true;
      return (
        row.name.toLowerCase().includes(q) ||
        row.provider.toLowerCase().includes(q) ||
        row.target.toLowerCase().includes(q)
      );
    });
  }, [unifiedRows, overviewFilter, searchQuery]);

  const categories = useMemo(
    () => [
      {
        id: "overview" as const,
        label: "Overview",
        icon: LayoutGrid,
        count: totalCount,
      },
      {
        id: "work-management" as const,
        label: "Work Management",
        icon: GitBranch,
        count: syncConfigs.length,
      },
      {
        id: "cloud" as const,
        label: "Cloud & Infrastructure",
        icon: Cloud,
        count: cloudJobs.length,
      },
      {
        id: "endpoints" as const,
        label: "Endpoints & Security",
        icon: Laptop,
        count: fleetJobs.length,
      },
      {
        id: "identity" as const,
        label: "Identity & Directories",
        icon: Users,
        count: identityCount,
      },
    ],
    [
      totalCount,
      syncConfigs.length,
      cloudJobs.length,
      fleetJobs.length,
      identityCount,
    ],
  );

  const setActiveCategory = (tab: IntegrationCategory) => {
    if (tab === "overview") {
      setSearchParams({});
    } else {
      setSearchParams({ tab });
    }
  };

  const handlePrimaryCTA = () => {
    if (activeTab === "overview") {
      setShowConnectCatalog(true);
    } else if (activeTab === "work-management") {
      setSyncCreateSignal((n) => n + 1);
    } else if (activeTab === "cloud") {
      setCloudCreateRequest((prev) => ({
        nonce: prev.nonce + 1,
        provider: "aws",
      }));
    } else if (activeTab === "endpoints") {
      setEndpointCreateNonce((n) => n + 1);
    } else if (activeTab === "identity") {
      setIdentityCreateRequest((prev) => ({
        nonce: prev.nonce + 1,
        kind: "google_workspace",
      }));
    }
  };

  const handleSelectCatalogEntry = (entry: CatalogEntry) => {
    setShowConnectCatalog(false);
    setActiveCategory(entry.category);
    if (entry.category === "work-management") {
      setSyncCreateSignal((n) => n + 1);
    } else if (entry.category === "cloud") {
      setCloudCreateRequest((prev) => ({
        nonce: prev.nonce + 1,
        provider: entry.cloudProvider ?? "aws",
      }));
    } else if (entry.category === "endpoints") {
      setEndpointCreateNonce((n) => n + 1);
    } else if (entry.category === "identity") {
      setIdentityCreateRequest((prev) => ({
        nonce: prev.nonce + 1,
        kind: entry.identityKind ?? "google_workspace",
      }));
    }
  };

  return (
    <div className="p-6 lg:p-8 space-y-6">
      {/* Page Header */}
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="flex items-start gap-3">
          <div className="rounded-xl border border-slate-700 bg-slate-900 p-2.5 text-yellow-500 shrink-0">
            <Plug className="w-6 h-6" />
          </div>
          <div>
            <h1 className="text-3xl font-bold text-white">Integrations</h1>
            <p className="mt-1 text-slate-400">
              Manage work management sync, cloud &amp; infrastructure discovery,
              endpoint security, and directory connectors
            </p>
          </div>
        </div>
        <Button
          onClick={handlePrimaryCTA}
          data-testid="integrations-primary-cta"
        >
          <Plus className="w-4 h-4 mr-2" />
          {CTA_LABELS[activeTab]}
        </Button>
      </div>

      {/* Mobile / Tablet Horizontal Category Tabs */}
      <nav
        aria-label="Integration categories"
        className="flex lg:hidden gap-2 overflow-x-auto pb-2"
      >
        {categories.map((cat) => {
          const Icon = cat.icon;
          const isActive = activeTab === cat.id;
          return (
            <button
              key={cat.id}
              type="button"
              onClick={() => setActiveCategory(cat.id)}
              aria-current={isActive ? "page" : undefined}
              className={`inline-flex items-center gap-2 shrink-0 px-3 py-2 rounded-lg text-sm font-medium border transition-colors ${
                isActive
                  ? "bg-yellow-500/15 border-yellow-500/60 text-yellow-300"
                  : "bg-slate-900 border-slate-700 text-slate-300 hover:bg-slate-800"
              }`}
            >
              <Icon className="w-4 h-4" />
              <span>{cat.label}</span>
              <span className="px-1.5 py-0.5 text-xs rounded-full bg-slate-800 text-slate-300">
                {cat.count}
              </span>
            </button>
          );
        })}
      </nav>

      {/* Master-Detail Shell */}
      <div className="flex flex-col lg:flex-row gap-6 items-start">
        {/* Desktop Left Category Rail */}
        <aside className="hidden lg:block w-60 shrink-0 sticky top-6">
          <nav
            aria-label="Integration category rail"
            className="rounded-xl border border-slate-700/80 bg-slate-950/60 p-2 space-y-1"
          >
            {categories.map((cat) => {
              const Icon = cat.icon;
              const isActive = activeTab === cat.id;
              return (
                <button
                  key={cat.id}
                  type="button"
                  onClick={() => setActiveCategory(cat.id)}
                  aria-current={isActive ? "page" : undefined}
                  className={`w-full flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-left ${
                    isActive
                      ? "bg-yellow-500/15 text-yellow-300 border border-yellow-500/40"
                      : "text-slate-300 hover:bg-slate-800/80 hover:text-white border border-transparent"
                  }`}
                >
                  <span className="flex items-center gap-2.5 min-w-0">
                    <Icon className="w-4 h-4 shrink-0" />
                    <span className="truncate">{cat.label}</span>
                  </span>
                  <span
                    className={`px-2 py-0.5 text-xs rounded-full shrink-0 ${
                      isActive
                        ? "bg-yellow-500/20 text-yellow-200"
                        : "bg-slate-800 text-slate-400"
                    }`}
                  >
                    {cat.count}
                  </span>
                </button>
              );
            })}
          </nav>

          <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/50 p-3 space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Access &amp; Events
            </p>
            <Link
              to="/admin/sso"
              className="flex items-center justify-between text-xs text-slate-300 hover:text-yellow-300 py-1"
            >
              <span className="flex items-center gap-2">
                <Shield className="w-3.5 h-3.5 text-slate-400" />
                SSO / SAML / OIDC
              </span>
              <ArrowRight className="w-3 h-3" />
            </Link>
            <Link
              to="/webhooks"
              className="flex items-center justify-between text-xs text-slate-300 hover:text-yellow-300 py-1"
            >
              <span className="flex items-center gap-2">
                <Webhook className="w-3.5 h-3.5 text-slate-400" />
                Webhooks
              </span>
              <ArrowRight className="w-3 h-3" />
            </Link>
          </div>
        </aside>

        {/* Fluid Workspace */}
        <div className="flex-1 min-w-0 w-full">
          {activeTab === "overview" && (
            <div className="space-y-8">
              {/* Summary Metric Cards */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <Card>
                  <CardContent className="p-4">
                    <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
                      Total Integrations
                    </p>
                    <p className="mt-2 text-2xl font-bold text-white">
                      {totalCount}
                    </p>
                  </CardContent>
                </Card>

                <Card>
                  <CardContent className="p-4">
                    <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
                      Active Connectors
                    </p>
                    <p className="mt-2 text-2xl font-bold text-emerald-400">
                      {activeCount}{" "}
                      <span className="text-sm font-normal text-slate-400">
                        / {totalCount} enabled
                      </span>
                    </p>
                  </CardContent>
                </Card>

                <Card>
                  <CardContent className="p-4">
                    <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
                      Cloud &amp; Endpoints
                    </p>
                    <p className="mt-2 text-2xl font-bold text-white">
                      {cloudJobs.length + fleetJobs.length}
                    </p>
                  </CardContent>
                </Card>

                <Card>
                  <CardContent className="p-4">
                    <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
                      Sync &amp; Directories
                    </p>
                    <p className="mt-2 text-2xl font-bold text-white">
                      {syncConfigs.length + identityCount}
                    </p>
                  </CardContent>
                </Card>
              </div>

              {/* Unified Configured Integrations Table */}
              <Card>
                <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div>
                    <h2 className="text-lg font-semibold text-white">
                      Configured Integrations
                    </h2>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Unified inventory of all active and paused profiles across
                      Elder
                    </p>
                  </div>
                  <div className="relative w-full md:w-72">
                    <label htmlFor="integrations-search" className="sr-only">
                      Search integrations
                    </label>
                    <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                    <input
                      id="integrations-search"
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="Search by name or provider..."
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 pl-9 pr-3 py-1.5 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                    />
                  </div>
                </CardHeader>

                <CardContent className="space-y-4">
                  <div className="flex flex-wrap gap-2">
                    {[
                      { value: "all", label: "All" },
                      { value: "work-management", label: "Work Management" },
                      { value: "cloud", label: "Cloud & Infrastructure" },
                      { value: "endpoints", label: "Endpoints & Security" },
                      { value: "identity", label: "Identity & Directories" },
                    ].map((pill) => (
                      <button
                        key={pill.value}
                        type="button"
                        onClick={() => setOverviewFilter(pill.value)}
                        className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                          overviewFilter === pill.value
                            ? "bg-yellow-500/15 border-yellow-500/60 text-yellow-300"
                            : "bg-slate-900 border-slate-700 text-slate-300 hover:border-slate-600"
                        }`}
                      >
                        {pill.label}
                      </button>
                    ))}
                  </div>

                  {filteredOverviewRows.length === 0 ? (
                    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-8 text-center">
                      <p className="text-sm text-slate-400">
                        {totalCount === 0
                          ? "No integrations configured yet. Choose a connector below to get started."
                          : "No integrations match your current filter."}
                      </p>
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-sm text-slate-300">
                        <thead className="border-b border-slate-700 text-xs uppercase text-slate-400">
                          <tr>
                            <th className="py-2.5 pr-4">Name</th>
                            <th className="py-2.5 pr-4">Provider</th>
                            <th className="py-2.5 pr-4">Category</th>
                            <th className="py-2.5 pr-4">Target / Scope</th>
                            <th className="py-2.5 pr-4">Cadence</th>
                            <th className="py-2.5 pr-4">Status</th>
                            <th className="py-2.5 text-right">Action</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800">
                          {filteredOverviewRows.map((row) => (
                            <tr key={row.key}>
                              <td className="py-3 pr-4 font-medium text-white">
                                {row.name}
                              </td>
                              <td className="py-3 pr-4">
                                <span className="px-2 py-0.5 text-xs rounded-full bg-slate-900 border border-slate-700 text-yellow-400">
                                  {row.provider}
                                </span>
                              </td>
                              <td className="py-3 pr-4 text-xs text-slate-400">
                                {row.categoryLabel}
                              </td>
                              <td className="py-3 pr-4 text-xs text-slate-300 font-mono">
                                {row.target}
                              </td>
                              <td className="py-3 pr-4 text-xs text-slate-400">
                                {row.cadence}
                              </td>
                              <td className="py-3 pr-4">
                                <span
                                  className={`px-2 py-0.5 text-xs font-medium rounded-full border ${
                                    row.enabled
                                      ? "bg-emerald-950/60 text-emerald-300 border-emerald-700/60"
                                      : "bg-slate-900 text-slate-400 border-slate-700"
                                  }`}
                                >
                                  {row.enabled ? "Active" : "Paused"}
                                </span>
                              </td>
                              <td className="py-3 text-right">
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() =>
                                    setActiveCategory(row.category)
                                  }
                                >
                                  Manage
                                </Button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Connect an Integration Catalog */}
              <section className="space-y-3">
                <div>
                  <h2 className="text-lg font-semibold text-white">
                    Connect an Integration
                  </h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Select a connector to launch its typed setup workflow
                  </p>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {CATALOG_ENTRIES.map((entry) => (
                    <button
                      key={entry.id}
                      type="button"
                      onClick={() => handleSelectCatalogEntry(entry)}
                      className="text-left rounded-xl border border-slate-700 bg-slate-800/80 p-4 hover:border-yellow-500/60 hover:bg-slate-800 transition-colors flex flex-col justify-between gap-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold text-white">
                          {entry.title}
                        </span>
                        <span className="px-2 py-0.5 text-[11px] rounded-full bg-slate-900 border border-slate-700 text-slate-300">
                          {entry.categoryLabel}
                        </span>
                      </div>
                      <p className="text-xs text-slate-400">
                        {entry.description}
                      </p>
                    </button>
                  ))}
                </div>
              </section>
            </div>
          )}

          {activeTab === "work-management" && (
            <SyncConfig embedded openCreateSignal={syncCreateSignal} />
          )}

          {activeTab === "cloud" && (
            <CloudProfilesPanel openCreateRequest={cloudCreateRequest} />
          )}

          {activeTab === "endpoints" && (
            <EndpointsPanel openCreateNonce={endpointCreateNonce} />
          )}

          {activeTab === "identity" && (
            <IdentityIntegrationsPanel
              openCreateRequest={identityCreateRequest}
            />
          )}
        </div>
      </div>

      {/* Quick Connect Catalog Modal (triggered from Overview CTA) */}
      {showConnectCatalog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-labelledby="connect-catalog-title"
        >
          <div className="w-full max-w-3xl rounded-xl border border-slate-700 bg-slate-900 shadow-2xl my-8">
            <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
              <div>
                <h2
                  id="connect-catalog-title"
                  className="text-xl font-semibold text-white"
                >
                  Connect an Integration
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Choose a provider to configure authentication and sync scopes
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowConnectCatalog(false)}
                aria-label="Close catalog"
                className="text-slate-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-6 max-h-[75vh] overflow-y-auto grid grid-cols-1 gap-3 sm:grid-cols-2">
              {CATALOG_ENTRIES.map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  onClick={() => handleSelectCatalogEntry(entry)}
                  className="text-left rounded-lg border border-slate-700 bg-slate-950/70 p-3.5 hover:border-yellow-500/60 hover:bg-slate-800/80 transition-colors space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-white flex items-center gap-2">
                      {entry.category === "work-management" && (
                        <GitBranch className="w-4 h-4 text-yellow-400" />
                      )}
                      {entry.category === "cloud" && (
                        <Cloud className="w-4 h-4 text-yellow-400" />
                      )}
                      {entry.category === "endpoints" && (
                        <Laptop className="w-4 h-4 text-yellow-400" />
                      )}
                      {entry.category === "identity" &&
                        (entry.identityKind === "google_workspace" ? (
                          <Building2 className="w-4 h-4 text-yellow-400" />
                        ) : (
                          <KeyRound className="w-4 h-4 text-yellow-400" />
                        ))}
                      {entry.title}
                    </span>
                    <span className="text-[11px] text-slate-400">
                      {entry.categoryLabel}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400">{entry.description}</p>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
