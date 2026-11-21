import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Code2,
  History,
  Laptop,
  Play,
  PlugZap,
  Plus,
  Settings,
  ShieldCheck,
  Trash2,
  Workflow,
  X,
  XCircle,
} from "lucide-react";
import toast from "react-hot-toast";
import api, {
  type DiscoveryJobItem,
  type DiscoveryJobTestResult,
} from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardContent, CardHeader } from "@/components/Card";
import { confirmDelete } from "@/lib/confirmActions";
import DiscoveryHistoryModal from "./DiscoveryHistoryModal";
import {
  SCHEDULE_PRESETS,
  extractOrganizations,
  formatScheduleInterval,
  getApiErrorMessage,
  isSecretConfigured,
  resolveScheduleIntervalNumber,
  resolveScheduleState,
} from "./shared";

interface FleetFormState {
  name: string;
  organization_id: string;
  url: string;
  api_token: string;
  verify_ssl: boolean;
  sync_mdm: boolean;
  sync_labels: boolean;
  sync_os_versions: boolean;
  sync_scripts_reports: boolean;
  per_page: number;
  schedule_preset: string;
  schedule_custom: string;
  description: string;
  enabled: boolean;
}

const INITIAL_FLEET_FORM: FleetFormState = {
  name: "",
  organization_id: "",
  url: "",
  api_token: "",
  verify_ssl: true,
  sync_mdm: true,
  sync_labels: true,
  sync_os_versions: true,
  sync_scripts_reports: false,
  per_page: 100,
  schedule_preset: "3600",
  schedule_custom: "3600",
  description: "",
  enabled: true,
};

function buildFleetConfig(form: FleetFormState): Record<string, unknown> {
  const cfg: Record<string, unknown> = {
    url: form.url.trim(),
    verify_ssl: form.verify_ssl,
    sync_mdm: form.sync_mdm,
    sync_labels: form.sync_labels,
    sync_os_versions: form.sync_os_versions,
    sync_scripts_reports: form.sync_scripts_reports,
    per_page: Math.min(Math.max(Number(form.per_page) || 100, 1), 500),
  };
  if (form.api_token.trim()) {
    cfg.api_token = form.api_token.trim();
  }
  return cfg;
}

function hydrateFleetForm(
  job: DiscoveryJobItem,
  fallbackOrgId: string,
): FleetFormState {
  const cfg = (job.config_json || {}) as Record<string, unknown>;
  const schedule = resolveScheduleState(job.schedule_interval);
  const orgId =
    job.organization_id ?? cfg._organization_id ?? fallbackOrgId ?? "";

  return {
    name: job.name,
    organization_id: orgId !== "" ? String(orgId) : "",
    url: String(cfg.url || cfg.fleetdm_url || ""),
    api_token: "",
    verify_ssl: cfg.verify_ssl !== undefined ? Boolean(cfg.verify_ssl) : true,
    sync_mdm: cfg.sync_mdm !== undefined ? Boolean(cfg.sync_mdm) : true,
    sync_labels:
      cfg.sync_labels !== undefined ? Boolean(cfg.sync_labels) : true,
    sync_os_versions:
      cfg.sync_os_versions !== undefined ? Boolean(cfg.sync_os_versions) : true,
    sync_scripts_reports: Boolean(cfg.sync_scripts_reports),
    per_page: Number(cfg.per_page || 100),
    schedule_preset: schedule.preset,
    schedule_custom: schedule.customSeconds,
    description: String(job.description ?? cfg._description ?? ""),
    enabled: job.enabled,
  };
}

export interface EndpointsPanelProps {
  openCreateNonce?: number;
}

export default function EndpointsPanel({
  openCreateNonce = 0,
}: EndpointsPanelProps) {
  const queryClient = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [editingJob, setEditingJob] = useState<DiscoveryJobItem | null>(null);
  const [formState, setFormState] =
    useState<FleetFormState>(INITIAL_FLEET_FORM);
  const [rawJsonMode, setRawJsonMode] = useState(false);
  const [rawJsonText, setRawJsonText] = useState("{}");
  const [formError, setFormError] = useState<string | null>(null);
  const [historyJob, setHistoryJob] = useState<DiscoveryJobItem | null>(null);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [testResults, setTestResults] = useState<
    Record<number, DiscoveryJobTestResult>
  >({});

  const { data: jobsData, isLoading } = useQuery({
    queryKey: ["discoveryJobs"],
    queryFn: () => api.getDiscoveryJobs(),
  });

  const { data: orgsData } = useQuery({
    queryKey: ["organizations"],
    queryFn: () => api.getOrganizations(),
  });

  const organizations = useMemo(
    () => extractOrganizations(orgsData),
    [orgsData],
  );
  const defaultOrgId = organizations[0] ? String(organizations[0].id) : "";

  const orgMap = useMemo(() => {
    const map = new Map<number, string>();
    for (const org of organizations) {
      map.set(org.id, org.name);
    }
    return map;
  }, [organizations]);

  const fleetJobs = useMemo(
    () => (jobsData?.jobs ?? []).filter((job) => job.provider === "fleetdm"),
    [jobsData],
  );

  const handleOpenCreate = useCallback(() => {
    setEditingJob(null);
    setFormState({
      ...INITIAL_FLEET_FORM,
      organization_id: defaultOrgId,
    });
    setRawJsonMode(false);
    setRawJsonText("{}");
    setFormError(null);
    setShowModal(true);
  }, [defaultOrgId]);

  const lastNonceRef = useRef(0);
  useEffect(() => {
    if (openCreateNonce > 0 && openCreateNonce !== lastNonceRef.current) {
      lastNonceRef.current = openCreateNonce;
      handleOpenCreate();
    }
  }, [openCreateNonce, handleOpenCreate]);

  const handleOpenEdit = (job: DiscoveryJobItem) => {
    setEditingJob(job);
    setFormState(hydrateFleetForm(job, defaultOrgId));
    const cleanedCfg = {
      ...((job.config_json || {}) as Record<string, unknown>),
    };
    delete cleanedCfg._organization_id;
    delete cleanedCfg._description;
    setRawJsonText(JSON.stringify(cleanedCfg, null, 2));
    setRawJsonMode(false);
    setFormError(null);
    setShowModal(true);
  };

  const handleToggleRawJson = () => {
    setFormError(null);
    if (!rawJsonMode) {
      setRawJsonText(JSON.stringify(buildFleetConfig(formState), null, 2));
      setRawJsonMode(true);
    } else {
      try {
        const parsed = JSON.parse(rawJsonText || "{}");
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          setFormError("Raw configuration must be a JSON object.");
          return;
        }
        setRawJsonMode(false);
      } catch {
        setFormError("Invalid JSON configuration.");
      }
    }
  };

  const createMutation = useMutation({
    mutationFn: (payload: {
      name: string;
      provider_type: string;
      organization_id: number;
      config: Record<string, unknown>;
      schedule_interval: number;
      description?: string;
    }) => api.createDiscoveryJob(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["discoveryJobs"] });
      toast.success("FleetDM profile created");
      setShowModal(false);
    },
    onError: (err) => {
      setFormError(getApiErrorMessage(err, "Failed to create FleetDM profile"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: number;
      payload: {
        name?: string;
        config?: Record<string, unknown>;
        schedule_interval?: number;
        description?: string;
        enabled?: boolean;
      };
    }) => api.updateDiscoveryJob(id, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["discoveryJobs"] });
      toast.success("FleetDM profile updated");
      setShowModal(false);
      setEditingJob(null);
    },
    onError: (err) => {
      setFormError(getApiErrorMessage(err, "Failed to update FleetDM profile"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteDiscoveryJob(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["discoveryJobs"] });
      toast.success("FleetDM profile deleted");
    },
    onError: (err) => {
      toast.error(getApiErrorMessage(err, "Failed to delete FleetDM profile"));
    },
  });

  const runMutation = useMutation({
    mutationFn: (id: number) => api.runDiscoveryJob(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["discoveryHistory"] });
      toast.success("FleetDM discovery queued");
    },
    onError: (err) => {
      toast.error(getApiErrorMessage(err, "Failed to run FleetDM profile"));
    },
  });

  const handleTestConnection = async (job: DiscoveryJobItem) => {
    setTestingId(job.id);
    try {
      const result = await api.testDiscoveryJob(job.id);
      setTestResults((prev) => ({ ...prev, [job.id]: result }));
      if (result.success) {
        toast.success(`FleetDM connection verified for "${job.name}"`);
      } else {
        toast.error(result.error || `Connection test failed for "${job.name}"`);
      }
    } catch (err) {
      const message = getApiErrorMessage(err, "Connection test failed");
      setTestResults((prev) => ({
        ...prev,
        [job.id]: {
          job_id: job.id,
          success: false,
          tested_at: new Date().toISOString(),
          error: message,
        },
      }));
      toast.error(message);
    } finally {
      setTestingId(null);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (!formState.name.trim()) {
      setFormError("Profile name is required.");
      return;
    }

    const orgIdNum = Number(formState.organization_id);
    if (!editingJob && (!orgIdNum || Number.isNaN(orgIdNum))) {
      setFormError("Organization is required.");
      return;
    }

    let configPayload: Record<string, unknown>;
    if (rawJsonMode) {
      try {
        const parsed = JSON.parse(rawJsonText || "{}");
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          setFormError("Raw configuration must be a JSON object.");
          return;
        }
        configPayload = parsed as Record<string, unknown>;
      } catch {
        setFormError("Invalid JSON configuration.");
        return;
      }
    } else {
      if (!formState.url.trim()) {
        setFormError("FleetDM Server URL is required.");
        return;
      }
      if (!editingJob && !formState.api_token.trim()) {
        setFormError("FleetDM API-Only User Token is required.");
        return;
      }
      configPayload = buildFleetConfig(formState);
    }

    const scheduleInterval = resolveScheduleIntervalNumber(
      formState.schedule_preset,
      formState.schedule_custom,
    );

    if (editingJob) {
      updateMutation.mutate({
        id: editingJob.id,
        payload: {
          name: formState.name.trim(),
          config: configPayload,
          schedule_interval: scheduleInterval,
          description: formState.description.trim(),
          enabled: formState.enabled,
        },
      });
    } else {
      createMutation.mutate({
        name: formState.name.trim(),
        provider_type: "fleetdm",
        organization_id: orgIdNum,
        config: configPayload,
        schedule_interval: scheduleInterval,
        description: formState.description.trim() || undefined,
      });
    }
  };

  const hasSavedToken = useMemo(
    () =>
      isSecretConfigured(editingJob?.config_json, [
        "api_token",
        "token",
        "fleetdm_api_token",
      ]),
    [editingJob],
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold text-white">
            Endpoints & Security (FleetDM)
          </h2>
          <p className="mt-0.5 text-sm text-slate-400">
            Connect FleetDM servers to sync managed hosts, OS versions,
            software, vulnerabilities, policies, and MDM posture
          </p>
        </div>
        <Button
          onClick={handleOpenCreate}
          data-testid="add-fleetdm-profile-btn"
        >
          <Plus className="w-4 h-4 mr-2" />
          New Endpoint Profile
        </Button>
      </div>

      {/* Playbooks Banner */}
      <div className="rounded-xl border border-slate-700/80 bg-slate-900/90 p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <Workflow className="w-5 h-5 text-yellow-500 mt-0.5 shrink-0" />
          <p className="text-sm text-slate-300">
            Playbooks include 43 FleetDM automation triggers, lookups, and MDM
            actions configured via{" "}
            <code className="text-yellow-300">FLEETDM_URL</code> /{" "}
            <code className="text-yellow-300">FLEETDM_API_TOKEN</code>.
          </p>
        </div>
        <Link
          to="/streams"
          className="inline-flex items-center shrink-0 px-3 py-1.5 text-xs font-medium rounded-lg border border-slate-700 bg-slate-800 text-slate-200 hover:bg-slate-700 hover:text-white transition-colors"
        >
          Open Playbooks
        </Link>
      </div>

      {/* Profiles List */}
      {isLoading ? (
        <Card>
          <CardContent className="p-8 text-center text-slate-400">
            Loading endpoint profiles...
          </CardContent>
        </Card>
      ) : fleetJobs.length === 0 ? (
        <Card>
          <CardContent className="p-10 text-center space-y-4">
            <Laptop className="w-10 h-10 text-yellow-500 mx-auto" />
            <div>
              <h3 className="text-lg font-semibold text-white">
                No FleetDM endpoint profiles configured yet
              </h3>
              <p className="text-sm text-slate-400 mt-1 max-w-xl mx-auto">
                Add your FleetDM server URL and API-only user token to discover
                hosts, software inventory, CVEs, labels, and MDM profiles.
              </p>
            </div>
            <Button onClick={handleOpenCreate}>
              <Plus className="w-4 h-4 mr-2" />
              Connect FleetDM
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {fleetJobs.map((job) => {
            const cfg = (job.config_json || {}) as Record<string, unknown>;
            const orgId = job.organization_id ?? cfg._organization_id;
            const orgName =
              orgId != null ? orgMap.get(Number(orgId)) : undefined;
            const targetUrl = String(cfg.url || cfg.fleetdm_url || "—");
            const hasToken = isSecretConfigured(cfg, [
              "api_token",
              "token",
              "fleetdm_api_token",
            ]);
            const testResult = testResults[job.id];
            const description = job.description ?? (cfg._description as string);

            return (
              <Card key={job.id}>
                <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-lg font-semibold text-white">
                        {job.name}
                      </h3>
                      <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-slate-800 text-yellow-400 border border-slate-700">
                        FleetDM
                      </span>
                      <span
                        className={`px-2 py-0.5 text-xs font-medium rounded-full border ${
                          job.enabled
                            ? "bg-emerald-950/60 text-emerald-300 border-emerald-700/60"
                            : "bg-slate-800 text-slate-400 border-slate-700"
                        }`}
                      >
                        {job.enabled ? "Enabled" : "Paused"}
                      </span>
                      {hasToken && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-blue-950/60 text-blue-300 border border-blue-800/60">
                          <ShieldCheck className="w-3 h-3" />
                          Credentials configured
                        </span>
                      )}
                    </div>

                    <div className="text-sm text-slate-400 flex flex-wrap items-center gap-x-4 gap-y-1">
                      <span>
                        Server:{" "}
                        <code className="text-slate-200">{targetUrl}</code>
                      </span>
                      {orgId != null && (
                        <span>
                          Org:{" "}
                          <span className="text-slate-200">
                            {orgName ?? `#${String(orgId)}`}
                          </span>
                        </span>
                      )}
                      <span className="inline-flex items-center gap-1">
                        <Clock className="w-3.5 h-3.5" />
                        {formatScheduleInterval(job.schedule_interval)}
                      </span>
                      <span>
                        TLS:{" "}
                        {cfg.verify_ssl === false ? "Unverified" : "Verified"}
                      </span>
                      <span>Page size: {String(cfg.per_page || 100)}</span>
                      {job.last_run_at && (
                        <span>
                          Last run {new Date(job.last_run_at).toLocaleString()}
                        </span>
                      )}
                    </div>

                    {description && (
                      <p className="text-xs text-slate-400">{description}</p>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => handleTestConnection(job)}
                      isLoading={testingId === job.id}
                    >
                      <PlugZap className="w-3.5 h-3.5 mr-1.5" />
                      Test Connection
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => runMutation.mutate(job.id)}
                    >
                      <Play className="w-3.5 h-3.5 mr-1.5" />
                      Run Now
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setHistoryJob(job)}
                    >
                      <History className="w-3.5 h-3.5 mr-1.5" />
                      History
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleOpenEdit(job)}
                      aria-label={`Edit ${job.name}`}
                    >
                      <Settings className="w-4 h-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        updateMutation.mutate({
                          id: job.id,
                          payload: { enabled: !job.enabled },
                        })
                      }
                    >
                      {job.enabled ? "Pause" : "Enable"}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        confirmDelete(job.name, () =>
                          deleteMutation.mutate(job.id),
                        )
                      }
                      aria-label={`Delete ${job.name}`}
                    >
                      <Trash2 className="w-4 h-4 text-red-400" />
                    </Button>
                  </div>
                </CardHeader>

                <CardContent className="pt-0 pb-4 space-y-3">
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                    <span>Sync scopes:</span>
                    {cfg.sync_mdm !== false && (
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                        MDM
                      </span>
                    )}
                    {cfg.sync_labels !== false && (
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                        Labels
                      </span>
                    )}
                    {cfg.sync_os_versions !== false && (
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                        OS Versions
                      </span>
                    )}
                    {Boolean(cfg.sync_scripts_reports) && (
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                        Scripts & Reports
                      </span>
                    )}
                  </div>

                  {testResult && (
                    <div
                      className={`rounded-lg border px-3 py-2 text-xs flex items-center gap-2 ${
                        testResult.success
                          ? "border-emerald-700/60 bg-emerald-950/30 text-emerald-200"
                          : "border-red-700/60 bg-red-950/30 text-red-200"
                      }`}
                    >
                      {testResult.success ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                      ) : (
                        <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                      )}
                      <span>
                        {testResult.success
                          ? `FleetDM API reachable for "${job.name}"`
                          : testResult.error || "Connection test failed"}
                      </span>
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Typed FleetDM Modal */}
      {showModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-labelledby="fleet-modal-title"
        >
          <div className="w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-900 shadow-2xl my-8">
            <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
              <h3
                id="fleet-modal-title"
                className="text-xl font-semibold text-white"
              >
                {editingJob
                  ? `Edit ${editingJob.name}`
                  : "New FleetDM Endpoint Profile"}
              </h3>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={handleToggleRawJson}
                >
                  <Code2 className="w-3.5 h-3.5 mr-1.5" />
                  {rawJsonMode ? "Typed Form" : "Edit as Raw JSON"}
                </Button>
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  aria-label="Close FleetDM modal"
                  className="text-slate-400 hover:text-white"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <form
              onSubmit={handleSubmit}
              className="p-6 space-y-5 max-h-[80vh] overflow-y-auto"
            >
              {formError && (
                <div
                  role="alert"
                  className="rounded-lg border border-red-700/70 bg-red-950/50 p-3 text-sm text-red-200 flex items-center gap-2"
                >
                  <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
                  <span>{formError}</span>
                </div>
              )}

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="fleet-name"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Profile Name *
                  </label>
                  <input
                    id="fleet-name"
                    type="text"
                    required
                    value={formState.name}
                    onChange={(e) =>
                      setFormState((p) => ({ ...p, name: e.target.value }))
                    }
                    placeholder="Production FleetDM"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label
                    htmlFor="fleet-org"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Organization *
                  </label>
                  <select
                    id="fleet-org"
                    value={formState.organization_id}
                    disabled={Boolean(editingJob)}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        organization_id: e.target.value,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none disabled:opacity-60"
                  >
                    <option value="">Select organization</option>
                    {organizations.map((org) => (
                      <option key={org.id} value={String(org.id)}>
                        {org.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="fleet-schedule"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Schedule Interval
                  </label>
                  <select
                    id="fleet-schedule"
                    value={formState.schedule_preset}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        schedule_preset: e.target.value,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                  >
                    {SCHEDULE_PRESETS.map((preset) => (
                      <option key={preset.value} value={preset.value}>
                        {preset.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label
                    htmlFor="fleet-description"
                    className="block text-sm font-medium text-slate-300 mb-1.5"
                  >
                    Description
                  </label>
                  <input
                    id="fleet-description"
                    type="text"
                    value={formState.description}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        description: e.target.value,
                      }))
                    }
                    placeholder="Corporate laptops & MDM posture"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                  />
                </div>
              </div>

              {formState.schedule_preset === "custom" && (
                <div>
                  <label
                    htmlFor="fleet-schedule-custom"
                    className="block text-sm font-medium text-slate-300 mb-1.5"
                  >
                    Custom Interval (seconds)
                  </label>
                  <input
                    id="fleet-schedule-custom"
                    type="number"
                    min={0}
                    value={formState.schedule_custom}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        schedule_custom: e.target.value,
                      }))
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                  />
                </div>
              )}

              {rawJsonMode ? (
                <div>
                  <label
                    htmlFor="fleet-raw-json"
                    className="block text-sm font-medium text-yellow-500 mb-1.5"
                  >
                    Raw FleetDM Configuration (JSON)
                  </label>
                  <textarea
                    id="fleet-raw-json"
                    rows={8}
                    value={rawJsonText}
                    onChange={(e) => setRawJsonText(e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-white focus:border-yellow-500 focus:outline-none"
                  />
                </div>
              ) : (
                <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 space-y-4">
                  <div className="flex items-center justify-between">
                    <h4 className="text-sm font-semibold text-white">
                      FleetDM Server & API Settings
                    </h4>
                    {editingJob && hasSavedToken && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-blue-950/60 text-blue-300 border border-blue-800/60">
                        <ShieldCheck className="w-3 h-3" />
                        Credentials configured
                      </span>
                    )}
                  </div>

                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div>
                      <label
                        htmlFor="fleet-url"
                        className="block text-sm font-medium text-yellow-500 mb-1.5"
                      >
                        FleetDM Server URL *
                      </label>
                      <input
                        id="fleet-url"
                        type="url"
                        value={formState.url}
                        onChange={(e) =>
                          setFormState((p) => ({ ...p, url: e.target.value }))
                        }
                        placeholder="https://fleet.example.com"
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>

                    <div>
                      <label
                        htmlFor="fleet-api-token"
                        className="block text-sm font-medium text-yellow-500 mb-1.5"
                      >
                        API-Only User Token {editingJob ? "" : "*"}
                      </label>
                      <input
                        id="fleet-api-token"
                        type="password"
                        value={formState.api_token}
                        onChange={(e) =>
                          setFormState((p) => ({
                            ...p,
                            api_token: e.target.value,
                          }))
                        }
                        placeholder={
                          editingJob && hasSavedToken
                            ? "Leave blank to keep existing secret"
                            : "FleetDM bearer token"
                        }
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white placeholder-slate-500 focus:border-yellow-500 focus:outline-none"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 items-center">
                    <div>
                      <label
                        htmlFor="fleet-per-page"
                        className="block text-sm font-medium text-slate-300 mb-1.5"
                      >
                        Page Size
                      </label>
                      <input
                        id="fleet-per-page"
                        type="number"
                        min={1}
                        max={500}
                        value={formState.per_page}
                        onChange={(e) =>
                          setFormState((p) => ({
                            ...p,
                            per_page: Number(e.target.value),
                          }))
                        }
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-yellow-500 focus:outline-none"
                      />
                    </div>

                    <div className="pt-5">
                      <label
                        htmlFor="fleet-verify-ssl"
                        className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                      >
                        <input
                          id="fleet-verify-ssl"
                          type="checkbox"
                          checked={formState.verify_ssl}
                          onChange={(e) =>
                            setFormState((p) => ({
                              ...p,
                              verify_ssl: e.target.checked,
                            }))
                          }
                          className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                        />
                        Verify TLS Certificates
                      </label>
                    </div>
                  </div>

                  <div>
                    <p className="text-sm font-medium text-slate-300 mb-2">
                      Sync Options
                    </p>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <label
                        htmlFor="fleet-sync-mdm"
                        className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                      >
                        <input
                          id="fleet-sync-mdm"
                          type="checkbox"
                          checked={formState.sync_mdm}
                          onChange={(e) =>
                            setFormState((p) => ({
                              ...p,
                              sync_mdm: e.target.checked,
                            }))
                          }
                          className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                        />
                        Sync MDM Profiles & Status
                      </label>

                      <label
                        htmlFor="fleet-sync-labels"
                        className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                      >
                        <input
                          id="fleet-sync-labels"
                          type="checkbox"
                          checked={formState.sync_labels}
                          onChange={(e) =>
                            setFormState((p) => ({
                              ...p,
                              sync_labels: e.target.checked,
                            }))
                          }
                          className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                        />
                        Sync Host Labels
                      </label>

                      <label
                        htmlFor="fleet-sync-os"
                        className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                      >
                        <input
                          id="fleet-sync-os"
                          type="checkbox"
                          checked={formState.sync_os_versions}
                          onChange={(e) =>
                            setFormState((p) => ({
                              ...p,
                              sync_os_versions: e.target.checked,
                            }))
                          }
                          className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                        />
                        Sync OS Versions
                      </label>

                      <label
                        htmlFor="fleet-sync-scripts"
                        className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                      >
                        <input
                          id="fleet-sync-scripts"
                          type="checkbox"
                          checked={formState.sync_scripts_reports}
                          onChange={(e) =>
                            setFormState((p) => ({
                              ...p,
                              sync_scripts_reports: e.target.checked,
                            }))
                          }
                          className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                        />
                        Sync Scripts & Reports
                      </label>
                    </div>
                  </div>
                </div>
              )}

              {editingJob && (
                <label
                  htmlFor="fleet-enabled"
                  className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer"
                >
                  <input
                    id="fleet-enabled"
                    type="checkbox"
                    checked={formState.enabled}
                    onChange={(e) =>
                      setFormState((p) => ({
                        ...p,
                        enabled: e.target.checked,
                      }))
                    }
                    className="rounded border-slate-600 bg-slate-950 text-yellow-500"
                  />
                  Profile Enabled
                </label>
              )}

              <div className="flex justify-end gap-3 border-t border-slate-800 pt-4">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setShowModal(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  isLoading={
                    createMutation.isPending || updateMutation.isPending
                  }
                >
                  {editingJob ? "Save Changes" : "Save Profile"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      <DiscoveryHistoryModal
        job={historyJob}
        onClose={() => setHistoryJob(null)}
      />
    </div>
  );
}
