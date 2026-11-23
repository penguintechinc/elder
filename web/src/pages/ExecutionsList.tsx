import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ChevronRight, Search } from "lucide-react";
import api from "@/lib/api";

type SourceFilter = "all" | "playbook" | "coderun" | "webhook";
type ExecutionSource = "playbook" | "coderun" | "webhook";

interface CountsBySource {
  all?: number;
  playbook?: number;
  coderun?: number;
  webhook?: number;
}

interface Execution {
  id?: number;
  execution_id: string;
  source?: ExecutionSource | string;
  name?: string;
  stream_id?: number;
  stream_name?: string;
  status: string;
  trigger_type?: string;
  playbook_id?: number;
  function_id?: number;
  function_name?: string;
  runtime?: string;
  exit_code?: number | null;
  webhook_id?: number;
  webhook_name?: string;
  event_type?: string;
  http_status?: number | null;
  input_data?: Record<string, unknown>;
  output_data?: Record<string, unknown>;
  error_message?: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  duration_ms?: number | null;
  metadata?: Record<string, unknown>;
}

const SOURCE_OPTIONS: { value: SourceFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "playbook", label: "Playbooks" },
  { value: "coderun", label: "CodeRuns" },
  { value: "webhook", label: "Webhooks" },
];

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "All Statuses" },
  { value: "completed", label: "Completed" },
  { value: "success", label: "Success" },
  { value: "failed", label: "Failed" },
  { value: "running", label: "Running" },
  { value: "pending", label: "Pending" },
  { value: "queued", label: "Queued" },
  { value: "timeout", label: "Timeout" },
];

function normalizeSource(source?: string): ExecutionSource {
  const normalized = (source || "").toLowerCase();
  if (normalized === "coderun") return "coderun";
  if (normalized === "webhook") return "webhook";
  return "playbook";
}

function getSourceBadge(source: ExecutionSource): {
  label: string;
  className: string;
} {
  switch (source) {
    case "coderun":
      return {
        label: "CodeRun",
        className:
          "bg-cyan-900/70 text-cyan-200 border border-cyan-700/60",
      };
    case "webhook":
      return {
        label: "Webhook",
        className:
          "bg-purple-900/70 text-purple-200 border border-purple-700/60",
      };
    default:
      return {
        label: "Playbook",
        className:
          "bg-amber-900/60 text-amber-200 border border-amber-700/60",
      };
  }
}

function getDetailPath(exec: Execution, source: ExecutionSource): string {
  if (source === "coderun") {
    return exec.function_id ? `/runs/${exec.function_id}` : "/runs";
  }
  if (source === "webhook") {
    return "/webhooks";
  }
  return `/streams/executions/${exec.execution_id}`;
}

function getExecutionTitle(exec: Execution, source: ExecutionSource): string {
  if (source === "coderun") {
    return (
      exec.name ||
      exec.function_name ||
      exec.stream_name ||
      (exec.function_id ? `CodeRun #${exec.function_id}` : "CodeRun")
    );
  }
  if (source === "webhook") {
    return (
      exec.name ||
      exec.webhook_name ||
      exec.stream_name ||
      (exec.webhook_id ? `Webhook #${exec.webhook_id}` : "Webhook")
    );
  }
  const playbookId = exec.playbook_id ?? exec.stream_id;
  return (
    exec.name ||
    exec.stream_name ||
    (playbookId ? `Playbook #${playbookId}` : "Playbook")
  );
}

function readStringMeta(
  direct: unknown,
  meta: Record<string, unknown> | undefined,
  key: string,
): string | undefined {
  if (typeof direct === "string" && direct.trim()) return direct;
  const val = meta?.[key];
  if (typeof val === "string" && val.trim()) return val;
  return undefined;
}

function readNumberMeta(
  direct: unknown,
  meta: Record<string, unknown> | undefined,
  key: string,
): number | undefined {
  if (typeof direct === "number" && Number.isFinite(direct)) return direct;
  const val = meta?.[key];
  if (typeof val === "number" && Number.isFinite(val)) return val;
  return undefined;
}

export default function ExecutionsList() {
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState<string>("");

  const trimmedSearch = searchQuery.trim();
  const queryParams = {
    page: 1,
    per_page: 50,
    source: sourceFilter,
    ...(statusFilter ? { status: statusFilter } : {}),
    ...(trimmedSearch ? { search: trimmedSearch } : {}),
  };

  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["stream-executions", sourceFilter, statusFilter, trimmedSearch],
    queryFn: () => api.listAllStreamExecutions(queryParams),
  });

  const executions: Execution[] = response?.data || [];
  const countsBySource: CountsBySource | undefined = response?.counts_by_source;

  const getSourceCount = (value: SourceFilter): number | undefined => {
    if (!countsBySource) return undefined;
    if (value === "all") {
      if (typeof countsBySource.all === "number") return countsBySource.all;
      const parts = [
        countsBySource.playbook,
        countsBySource.coderun,
        countsBySource.webhook,
      ].filter((v): v is number => typeof v === "number");
      if (parts.length > 0) {
        return parts.reduce((acc, n) => acc + n, 0);
      }
      return typeof response?.total === "number" ? response.total : undefined;
    }
    const count = countsBySource[value];
    return typeof count === "number" ? count : undefined;
  };

  const getStatusColor = (status: string) => {
    switch (status.toLowerCase()) {
      case "success":
      case "completed":
        return "bg-green-900 text-green-200";
      case "failed":
      case "error":
      case "timeout":
        return "bg-red-900 text-red-200";
      case "running":
        return "bg-blue-900 text-blue-200";
      case "pending":
      case "queued":
        return "bg-yellow-900 text-yellow-200";
      default:
        return "bg-slate-700 text-slate-300";
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-6">
          <h1 className="text-3xl font-bold text-amber-400">Executions</h1>
          <p className="text-slate-400 mt-2">
            Centralized audit log across Playbooks, CodeRuns, and Webhooks
          </p>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
          <div
            className="flex flex-wrap items-center gap-2"
            role="group"
            aria-label="Filter by source"
          >
            {SOURCE_OPTIONS.map((opt) => {
              const count = getSourceCount(opt.value);
              const isActive = sourceFilter === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setSourceFilter(opt.value)}
                  aria-pressed={isActive}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors border ${
                    isActive
                      ? "bg-amber-500 text-slate-900 border-amber-400 font-semibold"
                      : "bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-500"
                  }`}
                >
                  <span>{opt.label}</span>
                  {count !== undefined && (
                    <span
                      data-testid={`source-count-${opt.value}`}
                      className={`px-1.5 py-0.2 rounded-full text-[11px] font-mono ${
                        isActive
                          ? "bg-slate-900/20 text-slate-900"
                          : "bg-slate-900 text-slate-300"
                      }`}
                    >
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="flex flex-wrap items-center gap-3 w-full sm:w-auto">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              aria-label="Filter by status"
              className="px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-100 focus:outline-none focus:border-amber-400"
            >
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value || "all"} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>

            <div className="relative flex-1 sm:w-64">
              <Search
                className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
                aria-hidden="true"
              />
              <input
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search executions..."
                aria-label="Search executions"
                className="w-full pl-9 pr-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-100 placeholder-slate-400 focus:outline-none focus:border-amber-400"
              />
            </div>
          </div>
        </div>

        {error && (
          <div className="bg-red-900 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6">
            Failed to load executions. Please try again.
          </div>
        )}

        {isLoading && (
          <div className="flex justify-center items-center py-12">
            <div className="text-amber-400">Loading executions...</div>
          </div>
        )}

        {!isLoading && executions.length === 0 && (
          <div className="text-center py-16 bg-slate-800 rounded-lg border border-slate-700">
            <h3 className="text-xl font-semibold text-amber-400 mb-2">
              No executions
            </h3>
            <p className="text-slate-400">
              Executions will appear here once playbooks, CodeRuns, or webhooks
              are triggered
            </p>
          </div>
        )}

        {!isLoading && executions.length > 0 && (
          <div className="space-y-3">
            {executions.map((exec) => {
              const source = normalizeSource(exec.source);
              const sourceBadge = getSourceBadge(source);
              const playbookId = exec.playbook_id ?? exec.stream_id;
              const detailPath = getDetailPath(exec, source);
              const title = getExecutionTitle(exec, source);
              const runtime = readStringMeta(
                exec.runtime,
                exec.metadata,
                "runtime",
              );
              const eventType = readStringMeta(
                exec.event_type,
                exec.metadata,
                "event_type",
              );
              const httpStatus = readNumberMeta(
                exec.http_status,
                exec.metadata,
                "http_status",
              );
              const exitCode = readNumberMeta(
                exec.exit_code,
                exec.metadata,
                "exit_code",
              );

              return (
                <div
                  key={`${source}-${exec.execution_id}`}
                  className="relative group bg-slate-800 border border-slate-700 hover:border-amber-500/60 hover:bg-slate-750 rounded-lg px-5 py-4 transition-colors"
                >
                  <div className="flex flex-wrap items-center justify-between gap-4">
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-2.5">
                        <span
                          data-testid="source-badge"
                          className={`inline-flex px-2 py-0.5 rounded text-xs font-semibold ${sourceBadge.className}`}
                        >
                          {sourceBadge.label}
                        </span>
                        <span
                          className="text-amber-400 font-semibold text-base truncate"
                          data-testid="stream-name"
                        >
                          {title}
                        </span>
                        <span
                          className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${getStatusColor(exec.status)}`}
                        >
                          {exec.status.charAt(0).toUpperCase() +
                            exec.status.slice(1)}
                        </span>
                        {source === "coderun" && runtime && (
                          <span
                            data-testid="execution-runtime"
                            className="text-xs px-2 py-0.5 rounded bg-slate-900 text-cyan-200 border border-slate-700 font-mono"
                          >
                            {runtime}
                          </span>
                        )}
                        {source === "coderun" && exitCode !== undefined && (
                          <span className="text-xs px-2 py-0.5 rounded bg-slate-900 text-slate-300 border border-slate-700 font-mono">
                            exit {exitCode}
                          </span>
                        )}
                        {source === "webhook" && httpStatus !== undefined && (
                          <span
                            data-testid="execution-http-status"
                            className="text-xs px-2 py-0.5 rounded bg-slate-900 text-purple-200 border border-slate-700 font-mono"
                          >
                            HTTP {httpStatus}
                          </span>
                        )}
                        {source === "webhook" && eventType && (
                          <span
                            data-testid="execution-event-type"
                            className="text-xs px-2 py-0.5 rounded bg-slate-900 text-slate-300 border border-slate-700 font-mono"
                          >
                            {eventType}
                          </span>
                        )}
                        {exec.trigger_type && (
                          <span className="text-xs px-2 py-0.5 rounded bg-slate-900 text-slate-300 border border-slate-700">
                            {exec.trigger_type}
                          </span>
                        )}
                      </div>

                      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
                        <span className="font-mono" data-testid="execution-id">
                          {exec.execution_id.slice(0, 8)}
                        </span>
                        <span>
                          {new Date(exec.created_at).toLocaleDateString()}{" "}
                          {new Date(exec.created_at).toLocaleTimeString()}
                        </span>
                        {exec.duration_ms !== undefined &&
                          exec.duration_ms !== null && (
                            <span>{exec.duration_ms} ms</span>
                          )}
                      </div>

                      {exec.error_message && (
                        <p className="text-xs text-rose-300 font-mono truncate">
                          {exec.error_message}
                        </p>
                      )}
                    </div>

                    <div className="flex items-center gap-3 shrink-0">
                      {source === "playbook" && playbookId ? (
                        <Link
                          to={`/streams/${playbookId}`}
                          className="relative z-10 text-xs text-slate-300 hover:text-amber-300 px-2.5 py-1 rounded bg-slate-900 border border-slate-700 hover:border-slate-500 transition-colors"
                        >
                          Open Playbook
                        </Link>
                      ) : null}
                      <Link
                        to={detailPath}
                        className="inline-flex items-center gap-1 text-amber-400 group-hover:text-amber-300 font-semibold text-sm after:absolute after:inset-0"
                        data-testid="view-details-btn"
                      >
                        View Details
                        <ChevronRight className="w-4 h-4" />
                      </Link>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
