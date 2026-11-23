import { useEffect, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  ArrowLeft,
  Save,
  Play,
  Trash2,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Terminal,
  Clock,
  Database,
  HardDrive,
  Copy,
  Check,
  ExternalLink,
} from "lucide-react";
import api, {
  type RunExecution,
  type RunFunctionInput,
  type RunRuntime,
} from "@/lib/api";
import VillageIdBadge from "@/components/VillageIdBadge";
import {
  RUN_RUNTIMES,
  getExecutionStatusBadgeClass,
  getRuntimeMeta,
  getStorageBackendMeta,
} from "@/modules/runs/runtimes";

interface EditFormState {
  name: string;
  description: string;
  runtime: RunRuntime | string;
  code: string;
  timeout_seconds: string;
  memory_mb: string;
  schedule_cron: string;
  execution_namespace: string;
  worker_cluster: string;
}

export default function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const functionId = Number(id);
  const isValidId = Number.isInteger(functionId) && functionId > 0;

  const [form, setForm] = useState<EditFormState | null>(null);
  const [payloadText, setPayloadText] = useState<string>('{\n  "name": "world"\n}');
  const [payloadError, setPayloadError] = useState<string | null>(null);
  const [expandedExecutions, setExpandedExecutions] = useState<
    Record<string, boolean>
  >({});
  const [copiedSha256, setCopiedSha256] = useState(false);

  const {
    data: fn,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["run-function", functionId],
    queryFn: () => api.getRunFunction(functionId),
    enabled: isValidId,
  });

  const {
    data: executionsResponse,
    isLoading: executionsLoading,
    isFetching: executionsFetching,
    refetch: refetchExecutions,
  } = useQuery({
    queryKey: ["run-executions", functionId],
    queryFn: () =>
      api.listRunExecutions({ function_id: functionId, page: 1, per_page: 25 }),
    enabled: isValidId,
  });

  useEffect(() => {
    if (!fn) return;
    setForm({
      name: fn.name || "",
      description: fn.description || "",
      runtime: fn.runtime || "nodejs",
      code: fn.code || "",
      timeout_seconds: String(fn.timeout_seconds ?? 30),
      memory_mb: String(fn.memory_mb ?? 256),
      schedule_cron: fn.schedule_cron || "",
      execution_namespace: fn.execution_namespace || "elder-runs",
      worker_cluster: fn.worker_cluster || "",
    });
  }, [fn]);

  const updateMutation = useMutation({
    mutationFn: (payload: Partial<RunFunctionInput>) =>
      api.updateRunFunction(functionId, payload),
    onSuccess: (updated) => {
      toast.success(`Saved "${updated.name}"`);
      queryClient.invalidateQueries({ queryKey: ["run-function", functionId] });
      queryClient.invalidateQueries({ queryKey: ["run-functions"] });
    },
    onError: () => {
      toast.error("Failed to save CodeRun changes");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteRunFunction(functionId),
    onSuccess: () => {
      toast.success("CodeRun deleted");
      queryClient.invalidateQueries({ queryKey: ["run-functions"] });
      navigate("/runs");
    },
    onError: () => {
      toast.error("Failed to delete CodeRun");
    },
  });

  const invokeMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api.invokeRunFunction(functionId, payload),
    onSuccess: (exec) => {
      toast.success(
        `CodeRun triggered (${exec.execution_id?.slice(0, 8) || exec.status})`,
      );
      if (exec.execution_id) {
        setExpandedExecutions((prev) => ({
          ...prev,
          [exec.execution_id]: true,
        }));
      }
      queryClient.invalidateQueries({
        queryKey: ["run-executions", functionId],
      });
      queryClient.invalidateQueries({ queryKey: ["run-function", functionId] });
    },
    onError: () => {
      toast.error("Failed to invoke CodeRun");
    },
  });

  const handleSave = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!form) return;
    const trimmedName = form.name.trim();
    if (!trimmedName) {
      toast.error("CodeRun name is required");
      return;
    }

    const parsedTimeout = Number.parseInt(form.timeout_seconds, 10);
    if (Number.isFinite(parsedTimeout) && parsedTimeout > 300) {
      toast.error("Timeout cannot exceed 300 seconds (5 min)");
      return;
    }
    const parsedMemory = Number.parseInt(form.memory_mb, 10);

    updateMutation.mutate({
      name: trimmedName,
      description: form.description.trim() || undefined,
      runtime: form.runtime,
      code: form.code,
      timeout_seconds:
        Number.isFinite(parsedTimeout) && parsedTimeout > 0
          ? Math.min(parsedTimeout, 300)
          : 30,
      memory_mb:
        Number.isFinite(parsedMemory) && parsedMemory > 0 ? parsedMemory : 256,
      schedule_cron: form.schedule_cron.trim() || null,
      execution_namespace: form.execution_namespace.trim() || "elder-runs",
      worker_cluster: form.worker_cluster.trim() || null,
    });
  };

  const handleTestRun = () => {
    setPayloadError(null);
    const trimmed = payloadText.trim();
    if (!trimmed) {
      invokeMutation.mutate({});
      return;
    }

    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        setPayloadError("Input payload must be a JSON object");
        return;
      }
      invokeMutation.mutate(parsed as Record<string, unknown>);
    } catch {
      setPayloadError("Invalid JSON payload");
    }
  };

  const toggleExecution = (executionId: string) => {
    setExpandedExecutions((prev) => ({
      ...prev,
      [executionId]: !prev[executionId],
    }));
  };

  const handleCopySha256 = async (sha256: string) => {
    try {
      await navigator.clipboard.writeText(sha256);
      setCopiedSha256(true);
      toast.success("SHA-256 copied to clipboard");
      setTimeout(() => setCopiedSha256(false), 2000);
    } catch {
      toast.error("Failed to copy SHA-256");
    }
  };

  if (!isValidId) {
    return (
      <div className="min-h-screen bg-slate-900 p-6">
        <div className="max-w-5xl mx-auto">
          <div className="bg-red-900/80 border border-red-700 text-red-100 px-4 py-3 rounded-lg">
            Invalid CodeRun ID.
          </div>
          <Link
            to="/runs"
            className="inline-flex items-center gap-2 text-amber-400 hover:text-amber-300 mt-4 text-sm"
          >
            <ArrowLeft className="w-4 h-4" aria-hidden="true" />
            Back to CodeRuns
          </Link>
        </div>
      </div>
    );
  }

  if (isLoading || (fn && !form)) {
    return (
      <div className="min-h-screen bg-slate-900 p-6 flex items-center justify-center">
        <div className="text-amber-400">Loading CodeRun...</div>
      </div>
    );
  }

  if (error || !fn || !form) {
    return (
      <div className="min-h-screen bg-slate-900 p-6">
        <div className="max-w-5xl mx-auto">
          <div className="bg-red-900/80 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-4">
            Failed to load CodeRun details.
          </div>
          <Link
            to="/runs"
            className="inline-flex items-center gap-2 text-amber-400 hover:text-amber-300 text-sm"
          >
            <ArrowLeft className="w-4 h-4" aria-hidden="true" />
            Back to CodeRuns
          </Link>
        </div>
      </div>
    );
  }

  const runtimeMeta = getRuntimeMeta(form.runtime);
  const executions: RunExecution[] =
    executionsResponse?.items ?? executionsResponse?.data ?? [];

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <Link
              to="/runs"
              className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-amber-400 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" aria-hidden="true" />
              Back to CodeRuns
            </Link>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-3xl font-bold text-amber-400 flex items-center gap-2">
                <Terminal className="w-7 h-7" aria-hidden="true" />
                {fn.name}
              </h1>
              <span
                className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${runtimeMeta.badgeClass}`}
              >
                {runtimeMeta.label}
              </span>
              {fn.village_id && <VillageIdBadge villageId={fn.village_id} />}
            </div>
            {fn.description && (
              <p className="text-slate-400 text-sm">{fn.description}</p>
            )}
          </div>

          <button
            type="button"
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-rose-900/60 hover:bg-rose-800/80 text-rose-200 border border-rose-700/70 transition-colors"
          >
            <Trash2 className="w-4 h-4" aria-hidden="true" />
            Delete
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <form
            onSubmit={handleSave}
            className="lg:col-span-2 bg-slate-800 border border-slate-700 rounded-xl p-6 space-y-4"
          >
            <div className="flex items-center justify-between border-b border-slate-700 pb-3">
              <h2 className="text-lg font-semibold text-amber-400">
                CodeRun Configuration & Code
              </h2>
              <button
                type="submit"
                disabled={updateMutation.isPending}
                className="inline-flex items-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-4 py-1.5 rounded-lg text-sm font-semibold transition-colors"
              >
                <Save className="w-4 h-4" aria-hidden="true" />
                {updateMutation.isPending ? "Saving..." : "Save"}
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label
                  htmlFor="detail-fn-name"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Name
                </label>
                <input
                  id="detail-fn-name"
                  type="text"
                  required
                  value={form.name}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, name: e.target.value } : prev,
                    )
                  }
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-amber-400"
                />
              </div>

              <div>
                <label
                  htmlFor="detail-fn-runtime"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Runtime
                </label>
                <select
                  id="detail-fn-runtime"
                  value={form.runtime}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, runtime: e.target.value } : prev,
                    )
                  }
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-amber-400"
                >
                  {RUN_RUNTIMES.map((rt) => (
                    <option key={rt.value} value={rt.value}>
                      {rt.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div>
              <label
                htmlFor="detail-fn-description"
                className="block text-sm font-medium text-slate-300 mb-1"
              >
                Description
              </label>
              <input
                id="detail-fn-description"
                type="text"
                value={form.description}
                onChange={(e) =>
                  setForm((prev) =>
                    prev ? { ...prev, description: e.target.value } : prev,
                  )
                }
                className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-amber-400"
              />
            </div>

            <div>
              <label
                htmlFor="detail-fn-code"
                className="block text-sm font-medium text-slate-300 mb-1"
              >
                Source Code
              </label>
              <textarea
                id="detail-fn-code"
                rows={14}
                value={form.code}
                onChange={(e) =>
                  setForm((prev) =>
                    prev ? { ...prev, code: e.target.value } : prev,
                  )
                }
                spellCheck={false}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-700 rounded-lg text-xs font-mono text-slate-100 focus:outline-none focus:border-amber-400"
              />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <label
                  htmlFor="detail-fn-timeout"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Timeout (seconds)
                </label>
                <input
                  id="detail-fn-timeout"
                  type="number"
                  min={1}
                  max={300}
                  value={form.timeout_seconds}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev
                        ? { ...prev, timeout_seconds: e.target.value }
                        : prev,
                    )
                  }
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-amber-400"
                />
                <p className="text-xs text-slate-400 mt-1">
                  Max 300s (5 min)
                </p>
              </div>

              <div>
                <label
                  htmlFor="detail-fn-memory"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Memory (MB)
                </label>
                <input
                  id="detail-fn-memory"
                  type="number"
                  min={32}
                  max={8192}
                  value={form.memory_mb}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, memory_mb: e.target.value } : prev,
                    )
                  }
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-amber-400"
                />
              </div>

              <div>
                <label
                  htmlFor="detail-fn-schedule"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Schedule (cron)
                </label>
                <input
                  id="detail-fn-schedule"
                  type="text"
                  value={form.schedule_cron}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, schedule_cron: e.target.value } : prev,
                    )
                  }
                  placeholder="*/15 * * * *"
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm font-mono text-slate-100 focus:outline-none focus:border-amber-400"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label
                  htmlFor="detail-fn-namespace"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Execution Namespace
                </label>
                <input
                  id="detail-fn-namespace"
                  type="text"
                  value={form.execution_namespace}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev
                        ? { ...prev, execution_namespace: e.target.value }
                        : prev,
                    )
                  }
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm font-mono text-slate-100 focus:outline-none focus:border-amber-400"
                />
              </div>

              <div>
                <label
                  htmlFor="detail-fn-cluster"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Worker Cluster
                </label>
                <input
                  id="detail-fn-cluster"
                  type="text"
                  value={form.worker_cluster}
                  onChange={(e) =>
                    setForm((prev) =>
                      prev ? { ...prev, worker_cluster: e.target.value } : prev,
                    )
                  }
                  placeholder="Optional (default cluster)"
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm font-mono text-slate-100 focus:outline-none focus:border-amber-400"
                />
              </div>
            </div>
          </form>

          <div className="space-y-6 h-fit">
            <section className="bg-slate-800 border border-slate-700 rounded-xl p-6 space-y-4">
              <h2 className="text-lg font-semibold text-amber-400 border-b border-slate-700 pb-3">
                Test CodeRun
              </h2>
              <div>
                <label
                  htmlFor="test-run-payload"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  JSON Input Payload
                </label>
                <textarea
                  id="test-run-payload"
                  rows={8}
                  value={payloadText}
                  onChange={(e) => {
                    setPayloadText(e.target.value);
                    if (payloadError) setPayloadError(null);
                  }}
                  spellCheck={false}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-700 rounded-lg text-xs font-mono text-slate-100 focus:outline-none focus:border-amber-400"
                />
                {payloadError && (
                  <p role="alert" className="text-xs text-rose-300 mt-1">
                    {payloadError}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={handleTestRun}
                disabled={invokeMutation.isPending}
                className="w-full inline-flex items-center justify-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-emerald-800 text-white px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              >
                <Play className="w-4 h-4" aria-hidden="true" />
                {invokeMutation.isPending ? "Running..." : "Run CodeRun"}
              </button>
            </section>

            {(fn.storage_backend || fn.storage_key || fn.code_sha256) && (
              <section
                data-testid="artifact-storage-section"
                className="bg-slate-800 border border-slate-700 rounded-xl p-6 space-y-4"
              >
                <h2 className="text-lg font-semibold text-amber-400 border-b border-slate-700 pb-3 flex items-center gap-2">
                  {fn.storage_backend === "s3" ? (
                    <HardDrive className="w-5 h-5" aria-hidden="true" />
                  ) : (
                    <Database className="w-5 h-5" aria-hidden="true" />
                  )}
                  Artifact Storage
                </h2>

                <dl className="space-y-3 text-sm">
                  {fn.storage_backend &&
                    (() => {
                      const storageMeta = getStorageBackendMeta(
                        fn.storage_backend,
                      );
                      return (
                        <div>
                          <dt className="text-xs font-medium text-slate-400 mb-1">
                            Storage Backend
                          </dt>
                          <dd>
                            <span
                              data-testid="artifact-storage-backend"
                              className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium ${storageMeta.badgeClass}`}
                            >
                              {storageMeta.label}
                            </span>
                          </dd>
                        </div>
                      );
                    })()}

                  {fn.storage_key && (
                    <div>
                      <dt className="text-xs font-medium text-slate-400 mb-1">
                        Storage Key
                      </dt>
                      <dd>
                        <code
                          data-testid="artifact-storage-key"
                          className="block text-xs font-mono text-slate-200 bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 break-all"
                        >
                          {fn.storage_key}
                        </code>
                      </dd>
                    </div>
                  )}

                  {fn.code_sha256 && (
                    <div>
                      <dt className="text-xs font-medium text-slate-400 mb-1">
                        Code SHA-256
                      </dt>
                      <dd className="flex items-center gap-2">
                        <code
                          data-testid="artifact-code-sha256"
                          title={fn.code_sha256}
                          className="flex-1 min-w-0 truncate text-xs font-mono text-slate-200 bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5"
                        >
                          {fn.code_sha256.length > 16
                            ? `${fn.code_sha256.slice(0, 16)}…`
                            : fn.code_sha256}
                        </code>
                        <button
                          type="button"
                          onClick={() => handleCopySha256(fn.code_sha256!)}
                          aria-label="Copy SHA-256"
                          title="Copy SHA-256"
                          className="inline-flex items-center justify-center p-1.5 rounded-md bg-slate-900 hover:bg-slate-700 border border-slate-700 text-slate-300 hover:text-amber-300 transition-colors shrink-0"
                        >
                          {copiedSha256 ? (
                            <Check
                              className="w-3.5 h-3.5 text-emerald-400"
                              aria-hidden="true"
                            />
                          ) : (
                            <Copy className="w-3.5 h-3.5" aria-hidden="true" />
                          )}
                        </button>
                      </dd>
                    </div>
                  )}
                </dl>
              </section>
            )}
          </div>
        </div>

        <section className="bg-slate-800 border border-slate-700 rounded-xl p-6 space-y-4">
          <div className="flex items-center justify-between border-b border-slate-700 pb-3">
            <h2 className="text-lg font-semibold text-amber-400 flex items-center gap-2">
              <Clock className="w-5 h-5" aria-hidden="true" />
              Recent Executions
            </h2>
            <button
              type="button"
              onClick={() => refetchExecutions()}
              disabled={executionsFetching}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-900 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors"
            >
              <RefreshCw
                className={`w-3.5 h-3.5 ${executionsFetching ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              Refresh
            </button>
          </div>

          {executionsLoading && (
            <div className="text-sm text-amber-400 py-4">
              Loading executions...
            </div>
          )}

          {!executionsLoading && executions.length === 0 && (
            <div className="text-sm text-slate-400 py-6 text-center">
              No executions yet. Use the Test CodeRun panel above to invoke this
              CodeRun.
            </div>
          )}

          {!executionsLoading && executions.length > 0 && (
            <div className="space-y-2.5">
              {executions.map((exec) => {
                const isExpanded = !!expandedExecutions[exec.execution_id];
                return (
                  <div
                    key={exec.execution_id}
                    className="bg-slate-900 border border-slate-700 rounded-lg overflow-hidden"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2 hover:bg-slate-800/70 transition-colors">
                      <button
                        type="button"
                        onClick={() => toggleExecution(exec.execution_id)}
                        aria-expanded={isExpanded}
                        className="flex-1 min-w-0 px-4 py-3 flex flex-wrap items-center justify-between gap-3 text-left"
                      >
                        <div className="flex flex-wrap items-center gap-2.5">
                          {isExpanded ? (
                            <ChevronDown
                              className="w-4 h-4 text-slate-400"
                              aria-hidden="true"
                            />
                          ) : (
                            <ChevronRight
                              className="w-4 h-4 text-slate-400"
                              aria-hidden="true"
                            />
                          )}
                          <span
                            className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${getExecutionStatusBadgeClass(exec.status)}`}
                          >
                            {exec.status}
                          </span>
                          <span className="font-mono text-xs text-slate-200">
                            {exec.execution_id}
                          </span>
                          {exec.trigger_type && (
                            <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                              {exec.trigger_type}
                            </span>
                          )}
                        </div>

                        <div className="flex flex-wrap items-center gap-4 text-xs text-slate-400">
                          {exec.exit_code !== undefined &&
                            exec.exit_code !== null && (
                              <span className="font-mono">
                                exit {exec.exit_code}
                              </span>
                            )}
                          {exec.duration_ms !== undefined &&
                            exec.duration_ms !== null && (
                              <span>{exec.duration_ms} ms</span>
                            )}
                          <span>
                            {new Date(exec.created_at).toLocaleString()}
                          </span>
                        </div>
                      </button>

                      <Link
                        to={`/runs/executions/${exec.execution_id}`}
                        data-testid={`view-execution-details-${exec.execution_id}`}
                        className="mr-3 inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium bg-slate-800 hover:bg-slate-700 text-amber-400 hover:text-amber-300 border border-slate-700 transition-colors shrink-0"
                      >
                        View Execution Details
                        <ExternalLink className="w-3 h-3" aria-hidden="true" />
                      </Link>
                    </div>

                    {isExpanded && (
                      <div className="px-4 pb-4 pt-2 border-t border-slate-800 space-y-3 text-xs">
                        {exec.error_message && (
                          <div>
                            <div className="text-rose-300 font-semibold mb-1">
                              Error
                            </div>
                            <pre className="p-3 rounded bg-rose-950/60 border border-rose-800/70 text-rose-200 font-mono overflow-x-auto whitespace-pre-wrap">
                              {exec.error_message}
                            </pre>
                          </div>
                        )}

                        {exec.output_data !== undefined &&
                          exec.output_data !== null && (
                            <div>
                              <div className="text-amber-300 font-semibold mb-1">
                                Output Data
                              </div>
                              <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-slate-200 font-mono overflow-x-auto whitespace-pre-wrap">
                                {JSON.stringify(exec.output_data, null, 2)}
                              </pre>
                            </div>
                          )}

                        <div>
                          <div className="text-slate-300 font-semibold mb-1">
                            stdout
                          </div>
                          <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-emerald-200 font-mono overflow-x-auto whitespace-pre-wrap">
                            {exec.stdout || "(empty)"}
                          </pre>
                        </div>

                        {exec.stderr && (
                          <div>
                            <div className="text-rose-300 font-semibold mb-1">
                              stderr
                            </div>
                            <pre className="p-3 rounded bg-slate-950 border border-rose-900/60 text-rose-200 font-mono overflow-x-auto whitespace-pre-wrap">
                              {exec.stderr}
                            </pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
