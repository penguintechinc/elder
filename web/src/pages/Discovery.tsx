import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Plug,
  Compass,
  Play,
  Trash2,
  Clock,
  CheckCircle,
  XCircle,
  AlertCircle,
  Radar,
  Settings,
} from "lucide-react";
import toast from "react-hot-toast";
import type { Organization } from "@/types";
import api, { type DiscoveryJobItem } from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardContent } from "@/components/Card";
import ModalFormBuilder from "@/components/ModalFormBuilder";
import VillageIdBadge from "@/components/VillageIdBadge";
import { formatScheduleInterval } from "@/components/integrations/shared";
import { FormField } from "@/types/form";
import { getStatusColor } from "@/lib/colorHelpers";
import { confirmDelete } from "@/lib/confirmActions";

interface DiscoveryJob extends DiscoveryJobItem {
  discovery_type?: string;
  schedule?: string;
  village_id?: string | null;
}

interface DiscoveryRun {
  id: number;
  status: string;
  started_at: string;
  completed_at?: string;
  entities_discovered: number;
  error_message?: string;
}

type DiscoveryFilterTab = "all" | "scans" | "integrations";

// Local/unauthenticated scan types managed directly on this page
const SCAN_TYPES = [
  { value: "network", label: "Network Scan" },
  { value: "http_screenshot", label: "HTTP Screenshot" },
  { value: "banner", label: "Banner Grab" },
];

const SCAN_TYPE_SET = new Set(SCAN_TYPES.map((t) => t.value));

// Display labels for integration-backed discovery queries (configured under /admin/integrations)
const INTEGRATION_QUERY_LABELS: Record<string, string> = {
  aws: "AWS Discovery Query",
  gcp: "GCP Discovery Query",
  azure: "Azure Discovery Query",
  kubernetes: "Kubernetes Discovery Query",
  vultr: "Vultr Discovery Query",
  digitalocean: "DigitalOcean Discovery Query",
  fleetdm: "FleetDM Endpoint Query",
};

function getJobProvider(job: DiscoveryJob): string {
  return (job.discovery_type || job.provider || "").toLowerCase();
}

function isScanJob(job: DiscoveryJob): boolean {
  return SCAN_TYPE_SET.has(getJobProvider(job));
}

function getJobTypeLabel(job: DiscoveryJob): string {
  const provider = getJobProvider(job);
  const scanType = SCAN_TYPES.find((t) => t.value === provider);
  if (scanType) return scanType.label;
  return INTEGRATION_QUERY_LABELS[provider] || `${provider || "Integration"} Query`;
}

function getIntegrationConfigPath(job: DiscoveryJob): string {
  const provider = getJobProvider(job);
  if (provider === "fleetdm") {
    return "/admin/integrations?tab=endpoints";
  }
  return "/admin/integrations?tab=cloud";
}

export default function Discovery() {
  const [showScanModal, setShowScanModal] = useState(false);
  const [activeTab, setActiveTab] = useState<DiscoveryFilterTab>("all");
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const queryClient = useQueryClient();

  const { data: jobs, isLoading: jobsLoading } = useQuery({
    queryKey: ["discoveryJobs"],
    queryFn: () => api.getDiscoveryJobs(),
  });

  const { data: history } = useQuery({
    queryKey: ["discoveryHistory", selectedJobId],
    queryFn: () => api.getDiscoveryJobHistory(selectedJobId!),
    enabled: !!selectedJobId,
  });

  const { data: orgs } = useQuery({
    queryKey: ["organizations"],
    queryFn: () => api.getOrganizations(),
  });

  const allJobs: DiscoveryJob[] = useMemo(
    () => (jobs?.jobs as DiscoveryJob[] | undefined) || [],
    [jobs?.jobs],
  );

  const scanJobs = useMemo(
    () => allJobs.filter((job) => isScanJob(job)),
    [allJobs],
  );

  const integrationJobs = useMemo(
    () => allJobs.filter((job) => !isScanJob(job)),
    [allJobs],
  );

  const visibleJobs = useMemo(() => {
    if (activeTab === "scans") return scanJobs;
    if (activeTab === "integrations") return integrationJobs;
    return allJobs;
  }, [activeTab, allJobs, scanJobs, integrationJobs]);

  const selectedJob = useMemo(
    () => allJobs.find((j) => j.id === selectedJobId) || null,
    [allJobs, selectedJobId],
  );

  const runJobMutation = useMutation({
    mutationFn: (id: number) => api.runDiscoveryJob(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["discoveryHistory"],
        refetchType: "all",
      });
      toast.success("Discovery run started");
    },
  });

  const deleteJobMutation = useMutation({
    mutationFn: (id: number) => api.deleteDiscoveryJob(id),
    onSuccess: async (_data, deletedId) => {
      await queryClient.invalidateQueries({
        queryKey: ["discoveryJobs"],
        refetchType: "all",
      });
      toast.success("Scan deleted");
      if (selectedJobId === deletedId) {
        setSelectedJobId(null);
      }
    },
  });

  interface CreateScanData {
    name: string;
    provider_type: string;
    organization_id: number;
    config: Record<string, unknown>;
    schedule?: string;
    enabled: boolean;
  }

  interface ApiError {
    response?: {
      data?: { message?: string };
      status?: number;
    };
    message?: string;
  }

  const createScanMutation = useMutation({
    mutationFn: (data: CreateScanData) => api.createDiscoveryJob(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["discoveryJobs"],
        refetchType: "all",
      });
      toast.success("Scan created");
      setShowScanModal(false);
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.message || "Failed to create scan");
    },
  });

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "completed":
        return <CheckCircle className="w-5 h-5 text-green-400" />;
      case "failed":
        return <XCircle className="w-5 h-5 text-red-400" />;
      case "running":
        return <Clock className="w-5 h-5 text-yellow-400 animate-pulse" />;
      default:
        return <AlertCircle className="w-5 h-5 text-slate-400" />;
    }
  };

  const scanFields: FormField[] = useMemo(
    () => [
      {
        name: "name",
        label: "Name",
        type: "text",
        required: true,
        placeholder: "Local Network Scan",
      },
      {
        name: "provider_type",
        label: "Scan Type",
        type: "select",
        required: true,
        options: [{ value: "", label: "Select scan type" }, ...SCAN_TYPES],
      },
      {
        name: "organization_id",
        label: "Organization",
        type: "select",
        required: true,
        searchable: true,
        placeholder: "Select organization",
        options: (orgs?.items || []).map((o: Organization) => ({
          value: o.id,
          label: o.name,
        })),
      },
      {
        name: "schedule",
        label: "Schedule (Cron, optional)",
        type: "cron",
        placeholder: "0 2 * * *",
      },
      {
        name: "targets",
        label: "Targets (one CIDR, IP, or URL per line)",
        type: "multiline",
        rows: 5,
        placeholder: "192.168.1.0/24\n10.0.0.10",
      },
    ],
    [orgs?.items],
  );

  const handleScanSubmit = (data: Record<string, unknown>) => {
    const rawTargets = data.targets;
    const targets = Array.isArray(rawTargets)
      ? rawTargets
          .map((t) => String(t).trim())
          .filter((t) => t.length > 0)
      : typeof rawTargets === "string"
        ? rawTargets
            .split("\n")
            .map((t) => t.trim())
            .filter((t) => t.length > 0)
        : [];
    const submitData: CreateScanData = {
      name: data.name as string,
      provider_type: data.provider_type as string,
      organization_id: parseInt(data.organization_id as string, 10),
      config: targets.length > 0 ? { targets } : {},
      schedule: (data.schedule as string) || "0",
      enabled: true,
    };
    createScanMutation.mutate(submitData);
  };

  return (
    <div className="p-8">
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-3xl font-bold text-white">Discovery &amp; Scans</h1>
          <p className="mt-2 text-slate-400">
            Run local network and HTTP scans or trigger discovery queries against
            configured integrations
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Link
            to="/admin/integrations"
            className="inline-flex items-center px-3.5 py-2 text-sm font-medium rounded-lg border border-slate-700 bg-slate-900 text-slate-200 hover:bg-slate-800 hover:text-white transition-colors"
          >
            <Plug className="w-4 h-4 mr-2 text-yellow-500" />
            Manage Integrations
          </Link>
          <Button onClick={() => setShowScanModal(true)}>
            <Plus className="w-4 h-4 mr-2" />
            New Scan
          </Button>
        </div>
      </div>

      {/* Category Filter Tabs */}
      <div className="flex flex-wrap items-center gap-2 mb-6 border-b border-slate-800 pb-3">
        {(
          [
            { id: "all", label: "All", count: allJobs.length },
            { id: "scans", label: "Scans", count: scanJobs.length },
            {
              id: "integrations",
              label: "Integration Queries",
              count: integrationJobs.length,
            },
          ] as const
        ).map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`inline-flex items-center gap-2 px-3.5 py-1.5 text-sm font-medium rounded-lg transition-colors ${
                isActive
                  ? "bg-primary-600/20 text-primary-300 border border-primary-500/40"
                  : "text-slate-400 hover:text-white hover:bg-slate-800/60 border border-transparent"
              }`}
            >
              <span>{tab.label}</span>
              <span className="px-1.5 py-0.5 text-xs rounded bg-slate-800 text-slate-300">
                {tab.count}
              </span>
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Scans & Integration Discovery Queries */}
        <div>
          <h2 className="text-xl font-semibold text-white mb-4">
            {activeTab === "scans"
              ? "Active Scans"
              : activeTab === "integrations"
                ? "Integration Discovery Queries"
                : "Scans & Discovery Queries"}
          </h2>
          {jobsLoading ? (
            <div className="flex justify-center py-12">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : visibleJobs.length === 0 ? (
            <Card>
              <CardContent className="text-center py-12">
                {activeTab === "integrations" ? (
                  <>
                    <Plug className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                    <p className="text-slate-300 font-medium">
                      No integration discovery queries configured
                    </p>
                    <p className="text-sm text-slate-400 mt-1">
                      Connect cloud or endpoint providers on the Integrations
                      page to run discovery queries here.
                    </p>
                    <div className="flex justify-center mt-4">
                      <Link
                        to="/admin/integrations?tab=cloud"
                        className="inline-flex items-center px-4 py-2 text-sm font-medium rounded-lg bg-primary-600 text-white hover:bg-primary-500 transition-colors"
                      >
                        <Plug className="w-4 h-4 mr-2" />
                        Configure Integrations
                      </Link>
                    </div>
                  </>
                ) : (
                  <>
                    <Compass className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                    <p className="text-slate-400">
                      {activeTab === "scans"
                        ? "No scans configured"
                        : "No scans or integration discovery queries configured"}
                    </p>
                    <div className="flex flex-wrap justify-center gap-3 mt-4">
                      <Button onClick={() => setShowScanModal(true)}>
                        <Plus className="w-4 h-4 mr-2" />
                        New Scan
                      </Button>
                      {activeTab === "all" && (
                        <Link
                          to="/admin/integrations?tab=cloud"
                          className="inline-flex items-center px-3.5 py-2 text-sm font-medium rounded-lg border border-slate-700 bg-slate-900 text-slate-200 hover:bg-slate-800 hover:text-white transition-colors"
                        >
                          <Plug className="w-4 h-4 mr-2 text-yellow-500" />
                          Configure Integrations
                        </Link>
                      )}
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {visibleJobs.map((job) => {
                const scan = isScanJob(job);
                const cadence =
                  job.schedule ||
                  (job.schedule_interval
                    ? formatScheduleInterval(job.schedule_interval)
                    : null);
                return (
                  <Card
                    key={job.id}
                    className={
                      selectedJobId === job.id ? "ring-2 ring-primary-500" : ""
                    }
                  >
                    <CardContent>
                      <div className="flex items-start justify-between gap-3 mb-3">
                        <button
                          type="button"
                          className="flex-1 text-left focus:outline-none"
                          onClick={() => setSelectedJobId(job.id)}
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <h3 className="text-lg font-semibold text-white">
                              {job.name}
                            </h3>
                            <span
                              className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded ${
                                scan
                                  ? "bg-sky-500/15 text-sky-300"
                                  : "bg-amber-500/15 text-amber-300"
                              }`}
                            >
                              {scan ? (
                                <Radar className="w-3 h-3" />
                              ) : (
                                <Plug className="w-3 h-3" />
                              )}
                              {scan ? "Scan" : "Integration Query"}
                            </span>
                          </div>
                          <p className="text-sm text-slate-400 mt-1">
                            {getJobTypeLabel(job)}
                          </p>
                          {cadence && (
                            <p className="text-sm text-slate-400 mt-0.5">
                              <Clock className="w-4 h-4 inline mr-1" />
                              {cadence}
                            </p>
                          )}
                        </button>
                        <div className="flex flex-col items-end gap-1.5">
                          <span
                            className={`px-2 py-1 text-xs font-medium rounded ${
                              job.enabled
                                ? "bg-green-500/20 text-green-400"
                                : "bg-slate-500/20 text-slate-400"
                            }`}
                          >
                            {job.enabled ? "Enabled" : "Disabled"}
                          </span>
                          {job.village_id && (
                            <VillageIdBadge
                              villageId={job.village_id}
                              compact
                            />
                          )}
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          size="sm"
                          onClick={() => runJobMutation.mutate(job.id)}
                        >
                          <Play className="w-4 h-4 mr-2" />
                          {scan ? "Run Scan" : "Run Query"}
                        </Button>
                        {scan ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`Delete ${job.name}`}
                            onClick={() =>
                              confirmDelete(job.name, () =>
                                deleteJobMutation.mutate(job.id),
                              )
                            }
                          >
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        ) : (
                          <Link
                            to={getIntegrationConfigPath(job)}
                            className="inline-flex items-center px-2.5 py-1.5 text-xs font-medium rounded-md border border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
                          >
                            <Settings className="w-3.5 h-3.5 mr-1.5" />
                            Configure in Integrations
                          </Link>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
        </div>

        {/* Run History */}
        <div>
          <h2 className="text-xl font-semibold text-white mb-4">
            Run History
            {selectedJob ? (
              <span className="ml-2 text-sm font-normal text-slate-400">
                — {selectedJob.name}
              </span>
            ) : null}
          </h2>
          {!selectedJobId ? (
            <Card>
              <CardContent className="text-center py-12">
                <Clock className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">
                  Select a scan or discovery query to view run history
                </p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {history?.runs?.length === 0 ? (
                <Card>
                  <CardContent className="text-center py-12">
                    <Clock className="w-8 h-8 text-slate-600 mx-auto mb-3" />
                    <p className="text-slate-400">No runs yet</p>
                  </CardContent>
                </Card>
              ) : (
                history?.runs?.map((run: DiscoveryRun) => (
                  <Card key={run.id}>
                    <CardContent>
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex items-center gap-3">
                          {getStatusIcon(run.status)}
                          <div>
                            <p className="text-white font-medium">
                              {new Date(run.started_at).toLocaleString()}
                            </p>
                            {run.completed_at && (
                              <p className="text-sm text-slate-400">
                                Duration:{" "}
                                {Math.round(
                                  (new Date(run.completed_at).getTime() -
                                    new Date(run.started_at).getTime()) /
                                    1000,
                                )}
                                s
                              </p>
                            )}
                          </div>
                        </div>
                        <span
                          className={`px-2 py-1 text-xs font-medium rounded ${getStatusColor(run.status)}`}
                        >
                          {run.status}
                        </span>
                      </div>
                      {run.entities_discovered > 0 && (
                        <div className="mt-2">
                          <p className="text-sm text-slate-300">
                            Discovered:{" "}
                            <span className="font-semibold text-primary-400">
                              {run.entities_discovered}
                            </span>{" "}
                            entities
                          </p>
                        </div>
                      )}
                      {run.error_message && (
                        <div className="mt-2 p-2 bg-red-500/10 border border-red-500/20 rounded">
                          <p className="text-sm text-red-400">
                            {run.error_message}
                          </p>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      <ModalFormBuilder
        isOpen={showScanModal}
        onClose={() => setShowScanModal(false)}
        title="Create Scan"
        fields={scanFields}
        onSubmit={handleScanSubmit}
        submitLabel="Create"
      />
    </div>
  );
}
