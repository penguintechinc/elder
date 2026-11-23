import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import {
  Plus,
  Play,
  Trash2,
  Search,
  Clock,
  Server,
  Terminal,
  X,
  ChevronRight,
  Database,
  HardDrive,
} from "lucide-react";
import api, {
  type RunFunction,
  type RunFunctionInput,
  type RunRuntime,
} from "@/lib/api";
import VillageIdBadge from "@/components/VillageIdBadge";
import {
  RUN_RUNTIMES,
  getRuntimeMeta,
  getStarterCode,
  getStorageBackendMeta,
} from "@/modules/runs/runtimes";

interface CreateFormState {
  name: string;
  description: string;
  runtime: RunRuntime;
  code: string;
  timeout_seconds: string;
  memory_mb: string;
  schedule_cron: string;
  execution_namespace: string;
  worker_cluster: string;
}

const DEFAULT_CREATE_FORM: CreateFormState = {
  name: "",
  description: "",
  runtime: "nodejs",
  code: getStarterCode("nodejs"),
  timeout_seconds: "30",
  memory_mb: "256",
  schedule_cron: "",
  execution_namespace: "elder-runs",
  worker_cluster: "",
};

export default function RunsList() {
  const queryClient = useQueryClient();
  const [runtimeFilter, setRuntimeFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [form, setForm] = useState<CreateFormState>(DEFAULT_CREATE_FORM);
  const [codeEditedManually, setCodeEditedManually] = useState(false);
  const [invokingId, setInvokingId] = useState<number | null>(null);

  const queryParams = {
    ...(runtimeFilter !== "all" ? { runtime: runtimeFilter } : {}),
    ...(searchQuery.trim() ? { search: searchQuery.trim() } : {}),
    page: 1,
    per_page: 50,
  };

  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["run-functions", runtimeFilter, searchQuery.trim()],
    queryFn: () => api.listRunFunctions(queryParams),
  });

  const functions: RunFunction[] = response?.data || [];

  const createMutation = useMutation({
    mutationFn: (payload: RunFunctionInput) => api.createRunFunction(payload),
    onSuccess: (created) => {
      toast.success(`Created CodeRun "${created.name}"`);
      queryClient.invalidateQueries({ queryKey: ["run-functions"] });
      setIsCreateOpen(false);
      setForm(DEFAULT_CREATE_FORM);
      setCodeEditedManually(false);
    },
    onError: () => {
      toast.error("Failed to create CodeRun");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteRunFunction(id),
    onSuccess: () => {
      toast.success("CodeRun deleted");
      queryClient.invalidateQueries({ queryKey: ["run-functions"] });
    },
    onError: () => {
      toast.error("Failed to delete CodeRun");
    },
  });

  const invokeMutation = useMutation({
    mutationFn: (id: number) => api.invokeRunFunction(id, {}),
    onMutate: (id) => {
      setInvokingId(id);
    },
    onSuccess: (exec) => {
      toast.success(
        `Execution queued (${exec.execution_id?.slice(0, 8) || exec.status})`,
      );
      queryClient.invalidateQueries({ queryKey: ["run-functions"] });
      queryClient.invalidateQueries({ queryKey: ["run-executions"] });
    },
    onError: () => {
      toast.error("Failed to invoke CodeRun");
    },
    onSettled: () => {
      setInvokingId(null);
    },
  });

  const handleOpenCreate = () => {
    setForm(DEFAULT_CREATE_FORM);
    setCodeEditedManually(false);
    setIsCreateOpen(true);
  };

  const handleCloseCreate = () => {
    setIsCreateOpen(false);
  };

  const handleRuntimeChange = (nextRuntime: RunRuntime) => {
    setForm((prev) => ({
      ...prev,
      runtime: nextRuntime,
      code:
        !codeEditedManually || !prev.code.trim()
          ? getStarterCode(nextRuntime)
          : prev.code,
    }));
  };

  const handleCreateSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
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

    const payload: RunFunctionInput = {
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
    };

    createMutation.mutate(payload);
  };

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-7xl mx-auto">
        <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
          <div>
            <h1 className="text-3xl font-bold text-amber-400 flex items-center gap-2.5">
              <Terminal className="w-7 h-7" aria-hidden="true" />
              CodeRuns (Serverless Functions)
            </h1>
            <p className="text-slate-400 mt-2">
              Execute CodeRuns in isolated rootless containers (Node.js, Rust,
              Python, Go, Ruby) via{" "}
              <code className="text-amber-300 font-mono text-xs bg-slate-800 px-1.5 py-0.5 rounded">
                svc-runworker
              </code>
            </p>
          </div>
          <button
            type="button"
            onClick={handleOpenCreate}
            className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 text-slate-900 px-4 py-2 rounded-lg font-semibold transition-colors"
          >
            <Plus className="w-5 h-5" aria-hidden="true" />
            New CodeRun
          </button>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
          <div
            className="flex flex-wrap items-center gap-2"
            role="group"
            aria-label="Filter by runtime"
          >
            <button
              type="button"
              onClick={() => setRuntimeFilter("all")}
              aria-pressed={runtimeFilter === "all"}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors border ${
                runtimeFilter === "all"
                  ? "bg-amber-500 text-slate-900 border-amber-400 font-semibold"
                  : "bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-500"
              }`}
            >
              All
            </button>
            {RUN_RUNTIMES.map((rt) => (
              <button
                key={rt.value}
                type="button"
                onClick={() => setRuntimeFilter(rt.value)}
                aria-pressed={runtimeFilter === rt.value}
                className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors border ${
                  runtimeFilter === rt.value
                    ? "bg-amber-500 text-slate-900 border-amber-400 font-semibold"
                    : "bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-500"
                }`}
              >
                {rt.label}
              </button>
            ))}
          </div>

          <div className="relative w-full sm:w-72">
            <Search
              className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
              aria-hidden="true"
            />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search CodeRuns..."
              aria-label="Search CodeRuns"
              className="w-full pl-9 pr-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-100 placeholder-slate-400 focus:outline-none focus:border-amber-400"
            />
          </div>
        </div>

        {error && (
          <div
            role="alert"
            className="bg-red-900/80 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6"
          >
            Failed to load CodeRuns. Please try again.
          </div>
        )}

        {isLoading && (
          <div className="flex justify-center items-center py-12">
            <div className="text-amber-400">Loading CodeRuns...</div>
          </div>
        )}

        {!isLoading && !error && functions.length === 0 && (
          <div className="text-center py-16 bg-slate-800 rounded-lg border border-slate-700">
            <h2 className="text-xl font-semibold text-amber-400 mb-2">
              No CodeRuns found
            </h2>
            <p className="text-slate-400 mb-6">
              Create your first CodeRun to execute isolated workloads with
              svc-runworker
            </p>
            <button
              type="button"
              onClick={handleOpenCreate}
              className="bg-amber-500 hover:bg-amber-600 text-slate-900 px-6 py-2 rounded-lg font-semibold transition-colors"
            >
              Create CodeRun
            </button>
          </div>
        )}

        {!isLoading && functions.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {functions.map((fn) => {
              const runtimeMeta = getRuntimeMeta(fn.runtime);
              const isInvoking = invokingId === fn.id;
              return (
                <article
                  key={fn.id}
                  data-testid={`run-function-card-${fn.id}`}
                  className="relative group bg-slate-800 border border-slate-700 hover:border-amber-500/60 rounded-lg p-5 transition-colors flex flex-col justify-between gap-4"
                >
                  <div className="space-y-2.5">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <Link
                          to={`/runs/${fn.id}`}
                          className="text-lg font-semibold text-amber-400 group-hover:text-amber-300 inline-flex items-center gap-1 after:absolute after:inset-0 focus:outline-none"
                        >
                          <span className="truncate">{fn.name}</span>
                          <ChevronRight
                            className="w-4 h-4 shrink-0 opacity-70 group-hover:translate-x-0.5 transition-transform"
                            aria-hidden="true"
                          />
                        </Link>
                      </div>
                      <span
                        data-testid={`runtime-badge-${fn.id}`}
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium shrink-0 ${runtimeMeta.badgeClass}`}
                      >
                        {runtimeMeta.label}
                      </span>
                    </div>

                    {fn.description && (
                      <p className="text-sm text-slate-300 line-clamp-2">
                        {fn.description}
                      </p>
                    )}

                    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                      {fn.village_id && (
                        <div className="relative z-10">
                          <VillageIdBadge villageId={fn.village_id} compact />
                        </div>
                      )}
                      {fn.schedule_cron && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-slate-900 border border-slate-700 text-amber-200 font-mono">
                          <Clock className="w-3 h-3" aria-hidden="true" />
                          {fn.schedule_cron}
                        </span>
                      )}
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-slate-900 border border-slate-700 text-slate-300 font-mono">
                        <Server className="w-3 h-3" aria-hidden="true" />
                        {fn.execution_namespace || "elder-runs"}
                        {fn.worker_cluster ? ` @ ${fn.worker_cluster}` : ""}
                      </span>
                      {fn.storage_backend &&
                        (() => {
                          const storageMeta = getStorageBackendMeta(
                            fn.storage_backend,
                          );
                          return (
                            <span
                              data-testid={`storage-badge-${fn.id}`}
                              title={`Artifact storage: ${storageMeta.label}`}
                              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded font-mono ${storageMeta.badgeClass}`}
                            >
                              {fn.storage_backend === "s3" ? (
                                <HardDrive
                                  className="w-3 h-3"
                                  aria-hidden="true"
                                />
                              ) : (
                                <Database
                                  className="w-3 h-3"
                                  aria-hidden="true"
                                />
                              )}
                              {storageMeta.shortLabel}
                            </span>
                          );
                        })()}
                    </div>
                  </div>

                  <div className="flex items-center justify-between pt-3 border-t border-slate-700/80 gap-2">
                    <div className="text-xs text-slate-400">
                      {fn.timeout_seconds ?? 30}s · {fn.memory_mb ?? 256} MB
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => invokeMutation.mutate(fn.id)}
                        disabled={isInvoking}
                        aria-label={`Run Now ${fn.name}`}
                        className="relative z-10 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 disabled:bg-emerald-800 text-white transition-colors"
                      >
                        <Play className="w-3.5 h-3.5" aria-hidden="true" />
                        {isInvoking ? "Running..." : "Run Now"}
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteMutation.mutate(fn.id)}
                        disabled={deleteMutation.isPending}
                        aria-label={`Delete ${fn.name}`}
                        className="relative z-10 inline-flex items-center justify-center p-1.5 rounded-md text-rose-400 hover:text-rose-200 hover:bg-rose-900/50 disabled:opacity-50 transition-colors"
                      >
                        <Trash2 className="w-4 h-4" aria-hidden="true" />
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

      {isCreateOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 overflow-y-auto"
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-run-modal-title"
        >
          <div className="w-full max-w-2xl bg-slate-800 border border-slate-700 rounded-xl shadow-2xl my-8">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700">
              <h2
                id="create-run-modal-title"
                className="text-lg font-semibold text-amber-400"
              >
                Create CodeRun
              </h2>
              <button
                type="button"
                onClick={handleCloseCreate}
                aria-label="Close modal"
                className="text-slate-400 hover:text-slate-200 p-1 rounded"
              >
                <X className="w-5 h-5" aria-hidden="true" />
              </button>
            </div>

            <form onSubmit={handleCreateSubmit} className="p-6 space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label
                    htmlFor="run-fn-name"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Name
                  </label>
                  <input
                    id="run-fn-name"
                    type="text"
                    required
                    value={form.name}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, name: e.target.value }))
                    }
                    placeholder="e.g. enrich-alert-payload"
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-amber-400"
                  />
                </div>

                <div>
                  <label
                    htmlFor="run-fn-runtime"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Runtime
                  </label>
                  <select
                    id="run-fn-runtime"
                    value={form.runtime}
                    onChange={(e) =>
                      handleRuntimeChange(e.target.value as RunRuntime)
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
                  htmlFor="run-fn-description"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Description
                </label>
                <input
                  id="run-fn-description"
                  type="text"
                  value={form.description}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      description: e.target.value,
                    }))
                  }
                  placeholder="Optional summary of what this CodeRun does"
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-amber-400"
                />
              </div>

              <div>
                <label
                  htmlFor="run-fn-code"
                  className="block text-sm font-medium text-slate-300 mb-1"
                >
                  Code
                </label>
                <textarea
                  id="run-fn-code"
                  rows={10}
                  value={form.code}
                  onChange={(e) => {
                    setCodeEditedManually(true);
                    setForm((prev) => ({ ...prev, code: e.target.value }));
                  }}
                  spellCheck={false}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-700 rounded-lg text-xs font-mono text-slate-100 focus:outline-none focus:border-amber-400"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div>
                  <label
                    htmlFor="run-fn-timeout"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Timeout (seconds)
                  </label>
                  <input
                    id="run-fn-timeout"
                    type="number"
                    min={1}
                    max={300}
                    value={form.timeout_seconds}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        timeout_seconds: e.target.value,
                      }))
                    }
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-amber-400"
                  />
                  <p className="text-xs text-slate-400 mt-1">
                    Max 300s (5 min)
                  </p>
                </div>

                <div>
                  <label
                    htmlFor="run-fn-memory"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Memory (MB)
                  </label>
                  <input
                    id="run-fn-memory"
                    type="number"
                    min={32}
                    max={8192}
                    value={form.memory_mb}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        memory_mb: e.target.value,
                      }))
                    }
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-amber-400"
                  />
                </div>

                <div>
                  <label
                    htmlFor="run-fn-schedule"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Schedule (cron)
                  </label>
                  <input
                    id="run-fn-schedule"
                    type="text"
                    value={form.schedule_cron}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        schedule_cron: e.target.value,
                      }))
                    }
                    placeholder="*/15 * * * *"
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm font-mono text-slate-100 focus:outline-none focus:border-amber-400"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label
                    htmlFor="run-fn-namespace"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Execution Namespace
                  </label>
                  <input
                    id="run-fn-namespace"
                    type="text"
                    value={form.execution_namespace}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        execution_namespace: e.target.value,
                      }))
                    }
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm font-mono text-slate-100 focus:outline-none focus:border-amber-400"
                  />
                </div>

                <div>
                  <label
                    htmlFor="run-fn-cluster"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Worker Cluster
                  </label>
                  <input
                    id="run-fn-cluster"
                    type="text"
                    value={form.worker_cluster}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        worker_cluster: e.target.value,
                      }))
                    }
                    placeholder="Optional (default cluster)"
                    className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm font-mono text-slate-100 focus:outline-none focus:border-amber-400"
                  />
                </div>
              </div>

              <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-700">
                <button
                  type="button"
                  onClick={handleCloseCreate}
                  className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-700 hover:bg-slate-600 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={createMutation.isPending}
                  className="px-5 py-2 rounded-lg text-sm font-semibold bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 transition-colors"
                >
                  {createMutation.isPending ? "Creating..." : "Create CodeRun"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
