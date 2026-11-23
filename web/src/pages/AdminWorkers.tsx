import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  Cpu,
  Radar,
  RefreshCw,
  Plus,
  Settings,
  KeyRound,
  Trash2,
  Copy,
  Check,
  AlertTriangle,
  X,
  Terminal,
  ShieldAlert,
} from "lucide-react";
import api, {
  type RemoteWorker,
  type RemoteWorkerType,
  type RemoteWorkerStatus,
  type RemoteWorkerConfig,
  type OnboardRemoteWorkerInput,
  type OnboardRemoteWorkerResponse,
  type UpdateRemoteWorkerInput,
} from "@/lib/api";
import Card, { CardContent } from "@/components/Card";
import Button from "@/components/Button";

interface ApiError {
  response?: {
    data?: { error?: string; message?: string };
    status?: number;
  };
  message?: string;
}

function getErrorMessage(error: unknown, fallback: string): string {
  const err = error as ApiError | undefined;
  return (
    err?.response?.data?.error ||
    err?.response?.data?.message ||
    err?.message ||
    fallback
  );
}

function formatRelativeTime(isoTimestamp?: string | null): string {
  if (!isoTimestamp) return "Never";
  const parsed = new Date(isoTimestamp);
  if (Number.isNaN(parsed.getTime())) return "Unknown";
  const diffSeconds = Math.floor((Date.now() - parsed.getTime()) / 1000);
  if (diffSeconds < 0) return "Just now";
  if (diffSeconds < 60) return `${diffSeconds}s ago`;
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

function workerTypeLabel(type: RemoteWorkerType): string {
  return type === "runworker" ? "svc-runworker" : "svc-scanner";
}

function maskTokenPrefix(prefix?: string | null): string {
  if (!prefix) return "—";
  return `${prefix}••••••••`;
}

function buildDockerSnippet(
  workerType: RemoteWorkerType,
  cluster: string,
  token: string,
): string {
  const serviceName = workerTypeLabel(workerType);
  const apiUrl =
    typeof window !== "undefined" ? window.location.origin : "https://elder.example.com";
  return [
    `docker run -d --name ${serviceName} \\`,
    `  -e ELDER_API_URL="${apiUrl}" \\`,
    `  -e ELDER_INTERNAL_TOKEN="${token}" \\`,
    `  -e ELDER_WORKER_CLUSTER="${cluster || "default"}" \\`,
    `  ghcr.io/penguintechinc/elder/${serviceName}:latest`,
  ].join("\n");
}

function buildHelmSnippet(
  workerType: RemoteWorkerType,
  cluster: string,
  token: string,
): string {
  const serviceName = workerTypeLabel(workerType);
  const apiUrl =
    typeof window !== "undefined" ? window.location.origin : "https://elder.example.com";
  const valuesKey = workerType === "runworker" ? "runworker" : "scanner";
  return [
    `helm upgrade --install ${serviceName} oci://ghcr.io/penguintechinc/charts/elder-worker \\`,
    `  --set workerType=${valuesKey} \\`,
    `  --set apiUrl="${apiUrl}" \\`,
    `  --set cluster="${cluster || "default"}" \\`,
    `  --set internalToken="${token}"`,
  ].join("\n");
}

function StatusBadge({ status }: { status: RemoteWorkerStatus }) {
  switch (status) {
    case "online":
      return (
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
          data-testid="worker-status-online"
        >
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400" />
          </span>
          Online
        </span>
      );
    case "stale":
      return (
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-500/20 text-amber-300 border border-amber-500/30"
          data-testid="worker-status-stale"
        >
          <span className="h-2 w-2 rounded-full bg-amber-400" />
          Stale
        </span>
      );
    case "offline":
      return (
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-500/20 text-red-300 border border-red-500/30"
          data-testid="worker-status-offline"
        >
          <span className="h-2 w-2 rounded-full bg-red-400" />
          Offline
        </span>
      );
    case "revoked":
    default:
      return (
        <span
          className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-600/30 text-slate-300 border border-slate-600"
          data-testid="worker-status-revoked"
        >
          <span className="h-2 w-2 rounded-full bg-slate-400" />
          Revoked
        </span>
      );
  }
}

function WorkerTypeBadge({ workerType }: { workerType: RemoteWorkerType }) {
  if (workerType === "runworker") {
    return (
      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-mono font-medium bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
        <Cpu className="w-3.5 h-3.5" />
        svc-runworker
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-mono font-medium bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
      <Radar className="w-3.5 h-3.5" />
      svc-scanner
    </span>
  );
}

interface TokenRevealPanelProps {
  token: string;
  workerType: RemoteWorkerType;
  cluster: string;
  title: string;
  onDone: () => void;
}

function TokenRevealPanel({
  token,
  workerType,
  cluster,
  title,
  onDone,
}: TokenRevealPanelProps) {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [snippetTab, setSnippetTab] = useState<"docker" | "helm">("docker");

  const dockerSnippet = useMemo(
    () => buildDockerSnippet(workerType, cluster, token),
    [workerType, cluster, token],
  );
  const helmSnippet = useMemo(
    () => buildHelmSnippet(workerType, cluster, token),
    [workerType, cluster, token],
  );

  const handleCopy = async (text: string, key: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      toast.success(`${label} copied to clipboard`);
      window.setTimeout(() => {
        setCopiedKey((current) => (current === key ? null : current));
      }, 2000);
    } catch {
      toast.error("Failed to copy to clipboard");
    }
  };

  return (
    <div className="space-y-4" data-testid="one-time-token-panel">
      <div className="flex items-start gap-3 p-3.5 rounded-lg bg-amber-500/10 border border-amber-500/40 text-amber-200">
        <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
        <div className="text-sm">
          <p className="font-semibold text-amber-300">{title}</p>
          <p className="text-amber-200/90 mt-0.5">
            This <code className="font-mono">ELDER_INTERNAL_TOKEN</code> is only
            shown once. Copy and store it securely now — it cannot be retrieved
            again after you close this dialog.
          </p>
        </div>
      </div>

      <div>
        <label
          htmlFor="one-time-worker-token"
          className="block text-xs font-medium uppercase tracking-wide text-slate-400 mb-1.5"
        >
          ELDER_INTERNAL_TOKEN
        </label>
        <div className="flex items-center gap-2">
          <input
            id="one-time-worker-token"
            type="text"
            readOnly
            value={token}
            data-testid="one-time-token-value"
            className="flex-1 px-3 py-2 bg-slate-950 border border-slate-700 rounded-lg font-mono text-xs text-emerald-300 select-all focus:outline-none focus:border-primary-500"
          />
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => handleCopy(token, "token", "Token")}
            data-testid="copy-token-btn"
          >
            {copiedKey === "token" ? (
              <>
                <Check className="w-4 h-4 mr-1.5 text-emerald-400" />
                Copied
              </>
            ) : (
              <>
                <Copy className="w-4 h-4 mr-1.5" />
                Copy Token
              </>
            )}
          </Button>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-slate-400" />
            <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Deployment Snippet
            </span>
          </div>
          <div className="inline-flex rounded-lg bg-slate-900 p-0.5 border border-slate-700">
            <button
              type="button"
              onClick={() => setSnippetTab("docker")}
              className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                snippetTab === "docker"
                  ? "bg-slate-700 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Docker
            </button>
            <button
              type="button"
              onClick={() => setSnippetTab("helm")}
              className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                snippetTab === "helm"
                  ? "bg-slate-700 text-white"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Helm
            </button>
          </div>
        </div>
        <div className="relative">
          <pre
            className="p-3.5 bg-slate-950 border border-slate-700 rounded-lg font-mono text-xs text-slate-200 overflow-x-auto whitespace-pre-wrap break-all"
            data-testid="deployment-snippet"
          >
            {snippetTab === "docker" ? dockerSnippet : helmSnippet}
          </pre>
          <button
            type="button"
            onClick={() =>
              handleCopy(
                snippetTab === "docker" ? dockerSnippet : helmSnippet,
                snippetTab,
                snippetTab === "docker" ? "Docker snippet" : "Helm snippet",
              )
            }
            className="absolute top-2 right-2 inline-flex items-center gap-1 px-2 py-1 text-xs bg-slate-800 hover:bg-slate-700 text-slate-200 rounded border border-slate-600"
            data-testid="copy-snippet-btn"
          >
            {copiedKey === snippetTab ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-400" />
                Copied
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" />
                Copy
              </>
            )}
          </button>
        </div>
      </div>

      <div className="flex justify-end pt-2 border-t border-slate-700">
        <Button type="button" onClick={onDone}>
          Done
        </Button>
      </div>
    </div>
  );
}

interface ModalShellProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  maxWidthClass?: string;
}

function ModalShell({
  title,
  onClose,
  children,
  maxWidthClass = "max-w-xl",
}: ModalShellProps) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className={`w-full ${maxWidthClass} bg-slate-800 border border-slate-700 rounded-xl shadow-2xl overflow-hidden max-h-[90vh] flex flex-col`}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700">
          <h2 className="text-lg font-semibold text-white">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1 text-slate-400 hover:text-white hover:bg-slate-700 rounded"
            aria-label="Close modal"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}

export default function AdminWorkers() {
  const queryClient = useQueryClient();

  // Filters
  const [typeFilter, setTypeFilter] = useState<"all" | RemoteWorkerType>("all");
  const [statusFilter, setStatusFilter] = useState<
    "all" | RemoteWorkerStatus
  >("all");

  // Modal states
  const [showOnboardModal, setShowOnboardModal] = useState(false);
  const [onboardResult, setOnboardResult] =
    useState<OnboardRemoteWorkerResponse | null>(null);

  const [configuringWorker, setConfiguringWorker] =
    useState<RemoteWorker | null>(null);
  const [rotatingWorker, setRotatingWorker] = useState<RemoteWorker | null>(
    null,
  );
  const [rotatedResult, setRotatedResult] =
    useState<OnboardRemoteWorkerResponse | null>(null);
  const [offboardingWorker, setOffboardingWorker] =
    useState<RemoteWorker | null>(null);

  // Onboard form state
  const [onboardType, setOnboardType] = useState<RemoteWorkerType>("runworker");
  const [onboardName, setOnboardName] = useState("");
  const [onboardCluster, setOnboardCluster] = useState("default");
  const [onboardPollInterval, setOnboardPollInterval] = useState("5");
  const [onboardLogFlushInterval, setOnboardLogFlushInterval] = useState("2");
  const [onboardProgressInterval, setOnboardProgressInterval] = useState("5");
  const [onboardMaxConcurrent, setOnboardMaxConcurrent] = useState("4");
  const [onboardRunnerBackend, setOnboardRunnerBackend] = useState("docker");

  // Configure form state
  const [editName, setEditName] = useState("");
  const [editCluster, setEditCluster] = useState("");
  const [editEnabled, setEditEnabled] = useState(true);
  const [editPollInterval, setEditPollInterval] = useState("");
  const [editLogFlushInterval, setEditLogFlushInterval] = useState("");
  const [editProgressInterval, setEditProgressInterval] = useState("");
  const [editMaxConcurrent, setEditMaxConcurrent] = useState("");
  const [editRunnerBackend, setEditRunnerBackend] = useState("docker");

  const {
    data: workersResponse,
    isLoading,
    isFetching,
    isError,
    error: queryError,
    refetch,
  } = useQuery({
    queryKey: ["admin-workers"],
    queryFn: () => api.listRemoteWorkers(),
    refetchInterval: 30_000,
  });

  const workers: RemoteWorker[] = useMemo(() => {
    if (!workersResponse) return [];
    if (Array.isArray(workersResponse.workers)) return workersResponse.workers;
    if (Array.isArray(workersResponse.items)) return workersResponse.items;
    if (Array.isArray(workersResponse.data)) return workersResponse.data;
    return [];
  }, [workersResponse]);

  const counts = useMemo(() => {
    const computed = { online: 0, stale: 0, offline: 0, revoked: 0 };
    for (const worker of workers) {
      if (worker.status in computed) {
        computed[worker.status] += 1;
      }
    }
    return {
      online: workersResponse?.counts?.online ?? computed.online,
      stale: workersResponse?.counts?.stale ?? computed.stale,
      offline: workersResponse?.counts?.offline ?? computed.offline,
      revoked: workersResponse?.counts?.revoked ?? computed.revoked,
    };
  }, [workers, workersResponse]);

  const filteredWorkers = useMemo(() => {
    return workers.filter((worker) => {
      if (typeFilter !== "all" && worker.worker_type !== typeFilter) {
        return false;
      }
      if (statusFilter !== "all" && worker.status !== statusFilter) {
        return false;
      }
      return true;
    });
  }, [workers, typeFilter, statusFilter]);

  const onboardMutation = useMutation({
    mutationFn: (payload: OnboardRemoteWorkerInput) =>
      api.onboardRemoteWorker(payload),
    onSuccess: async (result) => {
      setOnboardResult(result);
      toast.success(`Worker "${result.worker.name}" onboarded`);
      await queryClient.invalidateQueries({ queryKey: ["admin-workers"] });
    },
    onError: (err) => {
      toast.error(getErrorMessage(err, "Failed to onboard worker"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: number | string;
      payload: UpdateRemoteWorkerInput;
    }) => api.updateRemoteWorker(id, payload),
    onSuccess: async () => {
      toast.success("Worker configuration updated");
      setConfiguringWorker(null);
      await queryClient.invalidateQueries({ queryKey: ["admin-workers"] });
    },
    onError: (err) => {
      toast.error(getErrorMessage(err, "Failed to update worker"));
    },
  });

  const rotateMutation = useMutation({
    mutationFn: (id: number | string) => api.rotateRemoteWorkerToken(id),
    onSuccess: async (result) => {
      setRotatedResult(result);
      toast.success("Worker token rotated");
      await queryClient.invalidateQueries({ queryKey: ["admin-workers"] });
    },
    onError: (err) => {
      toast.error(getErrorMessage(err, "Failed to rotate worker token"));
    },
  });

  const offboardMutation = useMutation({
    mutationFn: (id: number | string) => api.offboardRemoteWorker(id),
    onSuccess: async () => {
      toast.success("Worker offboarded successfully");
      setOffboardingWorker(null);
      await queryClient.invalidateQueries({ queryKey: ["admin-workers"] });
    },
    onError: (err) => {
      toast.error(getErrorMessage(err, "Failed to offboard worker"));
    },
  });

  const resetOnboardForm = () => {
    setOnboardType("runworker");
    setOnboardName("");
    setOnboardCluster("default");
    setOnboardPollInterval("5");
    setOnboardLogFlushInterval("2");
    setOnboardProgressInterval("5");
    setOnboardMaxConcurrent("4");
    setOnboardRunnerBackend("docker");
    setOnboardResult(null);
  };

  const openOnboardModal = () => {
    resetOnboardForm();
    setShowOnboardModal(true);
  };

  const closeOnboardModal = () => {
    setShowOnboardModal(false);
    setOnboardResult(null);
  };

  const openConfigureModal = (worker: RemoteWorker) => {
    const cfg = worker.config || {};
    setConfiguringWorker(worker);
    setEditName(worker.name);
    setEditCluster(worker.cluster || "default");
    setEditEnabled(worker.is_enabled !== false);
    setEditPollInterval(
      cfg.poll_interval_seconds !== undefined
        ? String(cfg.poll_interval_seconds)
        : "",
    );
    setEditLogFlushInterval(
      cfg.log_flush_interval_seconds !== undefined
        ? String(cfg.log_flush_interval_seconds)
        : "",
    );
    setEditProgressInterval(
      cfg.progress_interval_seconds !== undefined
        ? String(cfg.progress_interval_seconds)
        : "",
    );
    setEditMaxConcurrent(
      cfg.max_concurrent !== undefined ? String(cfg.max_concurrent) : "",
    );
    setEditRunnerBackend(cfg.runner_backend || "docker");
  };

  const handleOnboardSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = onboardName.trim();
    if (!trimmedName) {
      toast.error("Worker name is required");
      return;
    }

    const config: RemoteWorkerConfig = {};
    if (onboardPollInterval.trim()) {
      config.poll_interval_seconds = Number(onboardPollInterval);
    }
    if (onboardMaxConcurrent.trim()) {
      config.max_concurrent = Number(onboardMaxConcurrent);
    }
    if (onboardType === "runworker") {
      if (onboardLogFlushInterval.trim()) {
        config.log_flush_interval_seconds = Number(onboardLogFlushInterval);
      }
      if (onboardRunnerBackend.trim()) {
        config.runner_backend = onboardRunnerBackend.trim();
      }
    } else if (onboardProgressInterval.trim()) {
      config.progress_interval_seconds = Number(onboardProgressInterval);
    }

    onboardMutation.mutate({
      name: trimmedName,
      worker_type: onboardType,
      cluster: onboardCluster.trim() || "default",
      config,
    });
  };

  const handleConfigureSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!configuringWorker) return;
    const trimmedName = editName.trim();
    if (!trimmedName) {
      toast.error("Worker name is required");
      return;
    }

    const config: RemoteWorkerConfig = {
      ...(configuringWorker.config || {}),
    };
    if (editPollInterval.trim()) {
      config.poll_interval_seconds = Number(editPollInterval);
    }
    if (editMaxConcurrent.trim()) {
      config.max_concurrent = Number(editMaxConcurrent);
    }
    if (configuringWorker.worker_type === "runworker") {
      if (editLogFlushInterval.trim()) {
        config.log_flush_interval_seconds = Number(editLogFlushInterval);
      }
      if (editRunnerBackend.trim()) {
        config.runner_backend = editRunnerBackend.trim();
      }
    } else if (editProgressInterval.trim()) {
      config.progress_interval_seconds = Number(editProgressInterval);
    }

    updateMutation.mutate({
      id: configuringWorker.id,
      payload: {
        name: trimmedName,
        cluster: editCluster.trim() || "default",
        is_enabled: editEnabled,
        config,
      },
    });
  };

  const summaryCards: Array<{
    status: RemoteWorkerStatus;
    label: string;
    count: number;
    accentClass: string;
    dotClass: string;
  }> = [
    {
      status: "online",
      label: "Online",
      count: counts.online,
      accentClass: "text-emerald-400",
      dotClass: "bg-emerald-400",
    },
    {
      status: "stale",
      label: "Stale",
      count: counts.stale,
      accentClass: "text-amber-400",
      dotClass: "bg-amber-400",
    },
    {
      status: "offline",
      label: "Offline",
      count: counts.offline,
      accentClass: "text-red-400",
      dotClass: "bg-red-400",
    },
    {
      status: "revoked",
      label: "Revoked",
      count: counts.revoked,
      accentClass: "text-slate-400",
      dotClass: "bg-slate-400",
    },
  ];

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2.5">
            <Cpu className="w-6 h-6 text-primary-400" />
            Remote Workers
          </h1>
          <p className="text-slate-400 mt-1">
            Onboard, configure, rotate credentials, and monitor live heartbeats
            for <code className="text-slate-300">svc-runworker</code> and{" "}
            <code className="text-slate-300">svc-scanner</code> instances.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => void refetch()}
            disabled={isFetching}
            aria-label="Refresh workers"
          >
            <RefreshCw
              className={`w-4 h-4 mr-1.5 ${isFetching ? "animate-spin" : ""}`}
            />
            Refresh
          </Button>
          <Button type="button" size="sm" onClick={openOnboardModal}>
            <Plus className="w-4 h-4 mr-1.5" />
            Onboard Worker
          </Button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {summaryCards.map((card) => {
          const isSelected = statusFilter === card.status;
          return (
            <button
              key={card.status}
              type="button"
              onClick={() =>
                setStatusFilter((prev) =>
                  prev === card.status ? "all" : card.status,
                )
              }
              data-testid={`summary-card-${card.status}`}
              className={`text-left bg-slate-800 border rounded-lg p-4 transition-colors ${
                isSelected
                  ? "border-primary-500 ring-1 ring-primary-500/50"
                  : "border-slate-700 hover:border-slate-600"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-slate-400">
                  {card.label}
                </span>
                <span className={`h-2.5 w-2.5 rounded-full ${card.dotClass}`} />
              </div>
              <div className={`text-3xl font-bold mt-2 ${card.accentClass}`}>
                {card.count}
              </div>
            </button>
          );
        })}
      </div>

      {/* Filters Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div
          className="inline-flex items-center gap-1.5 bg-slate-800 border border-slate-700 rounded-lg p-1"
          role="group"
          aria-label="Filter by worker type"
        >
          {(
            [
              { value: "all", label: "All" },
              { value: "runworker", label: "RunWorkers" },
              { value: "scanner", label: "Scanners" },
            ] as const
          ).map((pill) => (
            <button
              key={pill.value}
              type="button"
              onClick={() => setTypeFilter(pill.value)}
              data-testid={`type-filter-${pill.value}`}
              className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                typeFilter === pill.value
                  ? "bg-primary-600 text-white"
                  : "text-slate-300 hover:text-white hover:bg-slate-700"
              }`}
            >
              {pill.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <label
            htmlFor="worker-status-filter"
            className="text-sm text-slate-400"
          >
            Status:
          </label>
          <select
            id="worker-status-filter"
            value={statusFilter}
            onChange={(e) =>
              setStatusFilter(e.target.value as "all" | RemoteWorkerStatus)
            }
            className="px-3 py-1.5 text-sm bg-slate-800 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
          >
            <option value="all">All Statuses</option>
            <option value="online">Online</option>
            <option value="stale">Stale</option>
            <option value="offline">Offline</option>
            <option value="revoked">Revoked</option>
          </select>
        </div>
      </div>

      {/* Workers Table */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-10 text-center text-slate-400">
              Loading remote workers...
            </div>
          ) : isError ? (
            <div className="p-10 text-center space-y-3">
              <p className="text-red-400 text-sm">
                {getErrorMessage(queryError, "Failed to load remote workers.")}
              </p>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => void refetch()}
              >
                Retry
              </Button>
            </div>
          ) : filteredWorkers.length === 0 ? (
            <div className="p-10 text-center space-y-3">
              <p className="text-slate-400 text-sm">
                {workers.length === 0
                  ? "No remote workers onboarded yet."
                  : "No workers match the active filters."}
              </p>
              {workers.length === 0 && (
                <Button type="button" size="sm" onClick={openOnboardModal}>
                  <Plus className="w-4 h-4 mr-1.5" />
                  Onboard First Worker
                </Button>
              )}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-slate-700 text-xs uppercase tracking-wider text-slate-400 bg-slate-800/80">
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Worker</th>
                    <th className="px-4 py-3 font-medium">Type</th>
                    <th className="px-4 py-3 font-medium">Cluster</th>
                    <th className="px-4 py-3 font-medium">Hostname</th>
                    <th className="px-4 py-3 font-medium">Version</th>
                    <th className="px-4 py-3 font-medium">Last Heartbeat</th>
                    <th className="px-4 py-3 font-medium">Token Prefix</th>
                    <th className="px-4 py-3 font-medium text-right">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700/70 text-sm">
                  {filteredWorkers.map((worker) => (
                    <tr
                      key={String(worker.id)}
                      data-testid={`worker-row-${worker.id}`}
                      className="hover:bg-slate-700/30 transition-colors"
                    >
                      <td className="px-4 py-3 whitespace-nowrap">
                        <StatusBadge status={worker.status} />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-white">
                            {worker.name}
                          </span>
                          {!worker.is_enabled && (
                            <span className="px-1.5 py-0.5 text-[10px] font-medium uppercase rounded bg-slate-700 text-slate-300">
                              Disabled
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <WorkerTypeBadge workerType={worker.worker_type} />
                      </td>
                      <td className="px-4 py-3 text-slate-300 font-mono text-xs">
                        {worker.cluster || "default"}
                      </td>
                      <td className="px-4 py-3 text-slate-300 font-mono text-xs">
                        {worker.hostname || "—"}
                      </td>
                      <td className="px-4 py-3 text-slate-300 font-mono text-xs">
                        {worker.version || "—"}
                      </td>
                      <td
                        className="px-4 py-3 text-slate-300 whitespace-nowrap"
                        title={worker.last_heartbeat_at || "No heartbeat yet"}
                      >
                        {formatRelativeTime(worker.last_heartbeat_at)}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-slate-400">
                        {maskTokenPrefix(worker.token_prefix)}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-right">
                        <div className="inline-flex items-center gap-1.5">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => openConfigureModal(worker)}
                            aria-label={`Configure ${worker.name}`}
                          >
                            <Settings className="w-3.5 h-3.5 mr-1" />
                            Configure
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              setRotatedResult(null);
                              setRotatingWorker(worker);
                            }}
                            aria-label={`Rotate token for ${worker.name}`}
                          >
                            <KeyRound className="w-3.5 h-3.5 mr-1" />
                            Rotate Token
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
                            onClick={() => setOffboardingWorker(worker)}
                            aria-label={`Offboard ${worker.name}`}
                          >
                            <Trash2 className="w-3.5 h-3.5 mr-1" />
                            Offboard
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Onboard Worker Modal */}
      {showOnboardModal && (
        <ModalShell
          title={
            onboardResult ? "Worker Onboarded" : "Onboard Remote Worker"
          }
          onClose={closeOnboardModal}
        >
          {onboardResult ? (
            <TokenRevealPanel
              token={onboardResult.token}
              workerType={onboardResult.worker.worker_type}
              cluster={onboardResult.worker.cluster}
              title={`Credentials generated for "${onboardResult.worker.name}"`}
              onDone={closeOnboardModal}
            />
          ) : (
            <form onSubmit={handleOnboardSubmit} className="space-y-4">
              <div>
                <span className="block text-sm font-medium text-slate-300 mb-2">
                  Worker Type
                </span>
                <div className="grid grid-cols-2 gap-3">
                  <button
                    type="button"
                    onClick={() => {
                      setOnboardType("runworker");
                      setOnboardPollInterval("5");
                    }}
                    className={`p-3 rounded-lg border text-left transition-colors ${
                      onboardType === "runworker"
                        ? "border-primary-500 bg-primary-500/10 text-white"
                        : "border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-600"
                    }`}
                  >
                    <div className="flex items-center gap-2 font-mono text-sm font-semibold">
                      <Cpu className="w-4 h-4 text-indigo-400" />
                      svc-runworker
                    </div>
                    <p className="text-xs text-slate-400 mt-1">
                      Executes serverless CodeRuns & streams
                    </p>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setOnboardType("scanner");
                      setOnboardPollInterval("15");
                    }}
                    className={`p-3 rounded-lg border text-left transition-colors ${
                      onboardType === "scanner"
                        ? "border-primary-500 bg-primary-500/10 text-white"
                        : "border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-600"
                    }`}
                  >
                    <div className="flex items-center gap-2 font-mono text-sm font-semibold">
                      <Radar className="w-4 h-4 text-cyan-400" />
                      svc-scanner
                    </div>
                    <p className="text-xs text-slate-400 mt-1">
                      Remote network & infrastructure discovery
                    </p>
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label
                    htmlFor="onboard-worker-name"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Name
                  </label>
                  <input
                    id="onboard-worker-name"
                    type="text"
                    required
                    value={onboardName}
                    onChange={(e) => setOnboardName(e.target.value)}
                    placeholder={
                      onboardType === "runworker"
                        ? "runworker-prod-1"
                        : "scanner-dc-west-1"
                    }
                    className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                  />
                </div>
                <div>
                  <label
                    htmlFor="onboard-worker-cluster"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Cluster
                  </label>
                  <input
                    id="onboard-worker-cluster"
                    type="text"
                    value={onboardCluster}
                    onChange={(e) => setOnboardCluster(e.target.value)}
                    placeholder="default"
                    className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-primary-500"
                  />
                </div>
              </div>

              <div className="pt-2 border-t border-slate-700">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">
                  Worker Configuration
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label
                      htmlFor="onboard-poll-interval"
                      className="block text-sm font-medium text-slate-300 mb-1"
                    >
                      Poll Interval (seconds)
                    </label>
                    <input
                      id="onboard-poll-interval"
                      type="number"
                      min={1}
                      value={onboardPollInterval}
                      onChange={(e) => setOnboardPollInterval(e.target.value)}
                      className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                    />
                  </div>

                  {onboardType === "runworker" ? (
                    <div>
                      <label
                        htmlFor="onboard-log-flush-interval"
                        className="block text-sm font-medium text-slate-300 mb-1"
                      >
                        Log Flush Interval (seconds)
                      </label>
                      <input
                        id="onboard-log-flush-interval"
                        type="number"
                        min={1}
                        value={onboardLogFlushInterval}
                        onChange={(e) =>
                          setOnboardLogFlushInterval(e.target.value)
                        }
                        className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                      />
                    </div>
                  ) : (
                    <div>
                      <label
                        htmlFor="onboard-progress-interval"
                        className="block text-sm font-medium text-slate-300 mb-1"
                      >
                        Progress Interval (seconds)
                      </label>
                      <input
                        id="onboard-progress-interval"
                        type="number"
                        min={1}
                        value={onboardProgressInterval}
                        onChange={(e) =>
                          setOnboardProgressInterval(e.target.value)
                        }
                        className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                      />
                    </div>
                  )}

                  <div>
                    <label
                      htmlFor="onboard-max-concurrent"
                      className="block text-sm font-medium text-slate-300 mb-1"
                    >
                      Max Concurrent
                    </label>
                    <input
                      id="onboard-max-concurrent"
                      type="number"
                      min={1}
                      value={onboardMaxConcurrent}
                      onChange={(e) => setOnboardMaxConcurrent(e.target.value)}
                      className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                    />
                  </div>

                  {onboardType === "runworker" && (
                    <div>
                      <label
                        htmlFor="onboard-runner-backend"
                        className="block text-sm font-medium text-slate-300 mb-1"
                      >
                        Runner Backend
                      </label>
                      <select
                        id="onboard-runner-backend"
                        value={onboardRunnerBackend}
                        onChange={(e) =>
                          setOnboardRunnerBackend(e.target.value)
                        }
                        className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                      >
                        <option value="docker">docker</option>
                        <option value="kubernetes">kubernetes</option>
                        <option value="kata">kata</option>
                        <option value="subprocess">subprocess</option>
                      </select>
                    </div>
                  )}
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t border-slate-700">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={closeOnboardModal}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  isLoading={onboardMutation.isPending}
                >
                  Create &amp; Generate Token
                </Button>
              </div>
            </form>
          )}
        </ModalShell>
      )}

      {/* Configure Worker Modal */}
      {configuringWorker && (
        <ModalShell
          title={`Configure ${configuringWorker.name}`}
          onClose={() => setConfiguringWorker(null)}
        >
          <form onSubmit={handleConfigureSubmit} className="space-y-4">
            <div className="flex items-center justify-between p-3 rounded-lg bg-slate-900 border border-slate-700">
              <div className="flex items-center gap-2">
                <WorkerTypeBadge workerType={configuringWorker.worker_type} />
                <StatusBadge status={configuringWorker.status} />
              </div>
              <label
                htmlFor="edit-worker-enabled"
                className="inline-flex items-center gap-2 text-sm text-slate-200 cursor-pointer"
              >
                <input
                  id="edit-worker-enabled"
                  type="checkbox"
                  checked={editEnabled}
                  onChange={(e) => setEditEnabled(e.target.checked)}
                  className="rounded border-slate-600 bg-slate-800 text-primary-500 focus:ring-primary-500"
                />
                Enabled
              </label>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label
                  htmlFor="edit-worker-name"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Name
                </label>
                <input
                  id="edit-worker-name"
                  type="text"
                  required
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                />
              </div>
              <div>
                <label
                  htmlFor="edit-worker-cluster"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Cluster
                </label>
                <input
                  id="edit-worker-cluster"
                  type="text"
                  value={editCluster}
                  onChange={(e) => setEditCluster(e.target.value)}
                  className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                />
              </div>
            </div>

            <div className="pt-2 border-t border-slate-700">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">
                Runtime Settings
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label
                    htmlFor="edit-poll-interval"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Poll Interval (seconds)
                  </label>
                  <input
                    id="edit-poll-interval"
                    type="number"
                    min={1}
                    value={editPollInterval}
                    onChange={(e) => setEditPollInterval(e.target.value)}
                    className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                  />
                </div>

                {configuringWorker.worker_type === "runworker" ? (
                  <div>
                    <label
                      htmlFor="edit-log-flush-interval"
                      className="block text-sm font-medium text-slate-300 mb-1"
                    >
                      Log Flush Interval (seconds)
                    </label>
                    <input
                      id="edit-log-flush-interval"
                      type="number"
                      min={1}
                      value={editLogFlushInterval}
                      onChange={(e) => setEditLogFlushInterval(e.target.value)}
                      className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                    />
                  </div>
                ) : (
                  <div>
                    <label
                      htmlFor="edit-progress-interval"
                      className="block text-sm font-medium text-slate-300 mb-1"
                    >
                      Progress Interval (seconds)
                    </label>
                    <input
                      id="edit-progress-interval"
                      type="number"
                      min={1}
                      value={editProgressInterval}
                      onChange={(e) => setEditProgressInterval(e.target.value)}
                      className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                    />
                  </div>
                )}

                <div>
                  <label
                    htmlFor="edit-max-concurrent"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Max Concurrent
                  </label>
                  <input
                    id="edit-max-concurrent"
                    type="number"
                    min={1}
                    value={editMaxConcurrent}
                    onChange={(e) => setEditMaxConcurrent(e.target.value)}
                    className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                  />
                </div>

                {configuringWorker.worker_type === "runworker" && (
                  <div>
                    <label
                      htmlFor="edit-runner-backend"
                      className="block text-sm font-medium text-slate-300 mb-1"
                    >
                      Runner Backend
                    </label>
                    <select
                      id="edit-runner-backend"
                      value={editRunnerBackend}
                      onChange={(e) => setEditRunnerBackend(e.target.value)}
                      className="w-full px-3 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-primary-500"
                    >
                      <option value="docker">docker</option>
                      <option value="kubernetes">kubernetes</option>
                      <option value="kata">kata</option>
                      <option value="subprocess">subprocess</option>
                    </select>
                  </div>
                )}
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-3 border-t border-slate-700">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setConfiguringWorker(null)}
              >
                Cancel
              </Button>
              <Button type="submit" isLoading={updateMutation.isPending}>
                Save Changes
              </Button>
            </div>
          </form>
        </ModalShell>
      )}

      {/* Rotate Token Modal */}
      {rotatingWorker && (
        <ModalShell
          title={`Rotate Token — ${rotatingWorker.name}`}
          onClose={() => {
            setRotatingWorker(null);
            setRotatedResult(null);
          }}
        >
          {rotatedResult ? (
            <TokenRevealPanel
              token={rotatedResult.token}
              workerType={rotatingWorker.worker_type}
              cluster={rotatingWorker.cluster}
              title={`New token issued for "${rotatingWorker.name}"`}
              onDone={() => {
                setRotatingWorker(null);
                setRotatedResult(null);
              }}
            />
          ) : (
            <div className="space-y-4">
              <div className="flex items-start gap-3 p-3.5 rounded-lg bg-amber-500/10 border border-amber-500/40 text-amber-200 text-sm">
                <ShieldAlert className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                <div>
                  <p className="font-semibold text-amber-300">
                    Invalidate existing credential?
                  </p>
                  <p className="mt-1 text-amber-200/90">
                    Rotating the token for{" "}
                    <span className="font-semibold text-white">
                      {rotatingWorker.name}
                    </span>{" "}
                    will immediately invalidate its current{" "}
                    <code className="font-mono">ELDER_INTERNAL_TOKEN</code> (
                    <code className="font-mono">
                      {maskTokenPrefix(rotatingWorker.token_prefix)}
                    </code>
                    ). You will need to update the worker deployment with the
                    new token.
                  </p>
                </div>
              </div>
              <div className="flex justify-end gap-2 pt-2 border-t border-slate-700">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setRotatingWorker(null)}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  onClick={() => rotateMutation.mutate(rotatingWorker.id)}
                  isLoading={rotateMutation.isPending}
                  data-testid="confirm-rotate-token-btn"
                >
                  Confirm Rotate Token
                </Button>
              </div>
            </div>
          )}
        </ModalShell>
      )}

      {/* Offboard Worker Modal */}
      {offboardingWorker && (
        <ModalShell
          title={`Offboard ${offboardingWorker.name}`}
          onClose={() => setOffboardingWorker(null)}
          maxWidthClass="max-w-md"
        >
          <div className="space-y-4">
            <p className="text-sm text-slate-300">
              Are you sure you want to offboard{" "}
              <span className="font-semibold text-white">
                {offboardingWorker.name}
              </span>{" "}
              (
              <code className="font-mono text-xs text-slate-200">
                {workerTypeLabel(offboardingWorker.worker_type)}
              </code>
              , cluster{" "}
              <code className="font-mono text-xs text-slate-200">
                {offboardingWorker.cluster || "default"}
              </code>
              )? Its credentials will be revoked immediately and it will no
              longer be able to authenticate with Elder.
            </p>
            <div className="flex justify-end gap-2 pt-2 border-t border-slate-700">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setOffboardingWorker(null)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="danger"
                onClick={() => offboardMutation.mutate(offboardingWorker.id)}
                isLoading={offboardMutation.isPending}
                data-testid="confirm-offboard-btn"
              >
                Confirm Offboard
              </Button>
            </div>
          </div>
        </ModalShell>
      )}
    </div>
  );
}
