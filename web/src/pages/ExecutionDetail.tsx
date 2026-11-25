import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  AlertTriangle,
  ExternalLink,
  Pause,
  Play,
  RefreshCw,
} from "lucide-react";
import api from "@/lib/api";

const ACTIVE_PLAYBOOK_STATUSES = new Set([
  "pending",
  "running",
  "waiting_approval",
]);
const POLL_INTERVAL_MS = 10_000;

interface ExecutionRecord {
  id?: number | string;
  execution_id?: string;
  playbook_id?: number;
  stream_id?: number;
  stream_name?: string;
  status?: string;
  triggered_by?: string;
  triggered_by_identity_id?: number | null;
  trigger_type?: string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number | null;
  error_message?: string | null;
  input_data?: unknown;
  output_data?: unknown;
  node_results?: Record<string, unknown>;
}

interface NodeLogItem {
  node_id: string;
  node_type?: string;
  status?: string;
  input_data?: unknown;
  output_data?: unknown;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
}

interface ExecutionDetailEnvelope {
  data?: {
    execution?: ExecutionRecord;
    stream_id?: number;
    stream_name?: string;
    node_logs?: NodeLogItem[];
  };
  execution?: ExecutionRecord;
  stream_id?: number;
  stream_name?: string;
  node_logs?: NodeLogItem[];
}

function extractExecutionStatus(
  raw: ExecutionDetailEnvelope | ExecutionRecord | null | undefined,
): string {
  if (!raw) return "";
  const envelope =
    (raw as ExecutionDetailEnvelope)?.data || (raw as ExecutionDetailEnvelope);
  const exec = envelope?.execution
    ? envelope.execution
    : "status" in raw
      ? (raw as ExecutionRecord)
      : null;
  return String(exec?.status || "").toLowerCase();
}

export default function ExecutionDetail() {
  const { executionId } = useParams<{ executionId: string }>();
  const navigate = useNavigate();
  const [autoPollEnabled, setAutoPollEnabled] = useState(true);

  const {
    data: rawResponse,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useQuery<ExecutionDetailEnvelope | ExecutionRecord | null>({
    queryKey: ["execution", executionId],
    queryFn: () => (executionId ? api.getExecutionById(executionId) : null),
    enabled: !!executionId,
    refetchInterval: (query) => {
      if (!autoPollEnabled) return false;
      const currentStatus = extractExecutionStatus(query.state.data);
      return ACTIVE_PLAYBOOK_STATUSES.has(currentStatus)
        ? POLL_INTERVAL_MS
        : false;
    },
  });

  const envelope = (rawResponse as ExecutionDetailEnvelope | null)?.data ||
    (rawResponse as ExecutionDetailEnvelope | null);
  const execution: ExecutionRecord | null = envelope?.execution
    ? envelope.execution
    : rawResponse && "status" in rawResponse
      ? (rawResponse as ExecutionRecord)
      : null;
  const nodeLogs: NodeLogItem[] = Array.isArray(envelope?.node_logs)
    ? envelope.node_logs
    : [];
  const streamId =
    envelope?.stream_id ?? execution?.playbook_id ?? execution?.stream_id;
  const streamName =
    envelope?.stream_name ||
    execution?.stream_name ||
    (streamId ? `Playbook #${streamId}` : "Playbook Execution");
  const displayId = execution?.execution_id || execution?.id || executionId;
  const status = String(execution?.status || "unknown");
  const isActive = ACTIVE_PLAYBOOK_STATUSES.has(status.toLowerCase());
  const startedAt = execution?.started_at || execution?.created_at;

  const getStatusColor = (s: string) => {
    switch (s.toLowerCase()) {
      case "success":
      case "completed":
        return "bg-green-900 text-green-200";
      case "failed":
      case "error":
        return "bg-red-900 text-red-200";
      case "running":
        return "bg-blue-900 text-blue-200";
      case "pending":
      case "queued":
      case "waiting_approval":
        return "bg-yellow-900 text-yellow-200";
      default:
        return "bg-slate-700 text-slate-300";
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
          <button
            type="button"
            onClick={() => navigate("/streams/executions")}
            className="flex items-center gap-2 text-amber-400 hover:text-amber-300 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Executions
          </button>

          <div className="flex flex-wrap items-center gap-2.5">
            {isActive && (
              <>
                <span
                  data-testid="live-poll-badge"
                  className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-medium border ${
                    autoPollEnabled
                      ? "bg-sky-950/70 text-sky-200 border-sky-700/70"
                      : "bg-slate-800 text-slate-300 border-slate-700"
                  }`}
                >
                  <span
                    className={`w-2 h-2 rounded-full ${
                      autoPollEnabled
                        ? "bg-sky-400 animate-pulse"
                        : "bg-slate-500"
                    }`}
                    aria-hidden="true"
                  />
                  {autoPollEnabled
                    ? "Auto-refreshing every 10s"
                    : "Auto-refresh paused"}
                </span>

                <button
                  type="button"
                  data-testid="toggle-poll-btn"
                  onClick={() => setAutoPollEnabled((prev) => !prev)}
                  className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors"
                >
                  {autoPollEnabled ? (
                    <>
                      <Pause className="w-3.5 h-3.5" aria-hidden="true" />
                      Pause
                    </>
                  ) : (
                    <>
                      <Play className="w-3.5 h-3.5" aria-hidden="true" />
                      Resume
                    </>
                  )}
                </button>
              </>
            )}

            <button
              type="button"
              data-testid="manual-refresh-btn"
              onClick={() => refetch()}
              disabled={isFetching}
              className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors"
            >
              <RefreshCw
                className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              Refresh
            </button>

            {streamId ? (
              <button
                type="button"
                onClick={() => navigate(`/streams/${streamId}`)}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-amber-300 border border-slate-700"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                Open Playbook
              </button>
            ) : null}
          </div>
        </div>

        {error && (
          <div className="bg-red-900 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6">
            Failed to load execution details.
          </div>
        )}

        {isLoading && (
          <div className="flex justify-center items-center py-12">
            <div className="text-amber-400">Loading execution...</div>
          </div>
        )}

        {execution && (
          <>
            <div className="mb-8">
              <div className="flex items-center justify-between gap-4 mb-4">
                <div>
                  <h1 className="text-3xl font-bold text-amber-400">
                    {streamName}
                  </h1>
                  <p className="text-slate-400 mt-1">
                    Execution ID:{" "}
                    <span className="font-mono text-sm">{String(displayId)}</span>
                  </p>
                </div>
                <span
                  className={`inline-flex px-4 py-2 rounded-lg text-sm font-semibold ${getStatusColor(status)}`}
                >
                  {status.charAt(0).toUpperCase() + status.slice(1)}
                </span>
              </div>

              {execution.error_message && (
                <div className="flex items-start gap-2 bg-rose-950/70 border border-rose-700/70 text-rose-200 px-4 py-3 rounded-lg">
                  <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                  <div className="text-sm font-mono break-all">
                    {execution.error_message}
                  </div>
                </div>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
              {startedAt && (
                <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
                  <p className="text-slate-400 text-sm mb-1">Started</p>
                  <p className="text-amber-400 font-semibold text-sm">
                    {new Date(startedAt).toLocaleString()}
                  </p>
                </div>
              )}

              {execution.completed_at && (
                <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
                  <p className="text-slate-400 text-sm mb-1">Completed</p>
                  <p className="text-amber-400 font-semibold text-sm">
                    {new Date(execution.completed_at).toLocaleString()}
                  </p>
                </div>
              )}

              {execution.duration_ms !== undefined &&
                execution.duration_ms !== null && (
                  <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
                    <p className="text-slate-400 text-sm mb-1">Duration</p>
                    <p className="text-amber-400 font-semibold text-sm">
                      {execution.duration_ms} ms
                    </p>
                  </div>
                )}

              {(execution.trigger_type || execution.triggered_by) && (
                <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
                  <p className="text-slate-400 text-sm mb-1">Trigger</p>
                  <p className="text-amber-400 font-semibold text-sm">
                    {execution.trigger_type || execution.triggered_by}
                  </p>
                </div>
              )}
            </div>

            {nodeLogs.length > 0 && (
              <div className="bg-slate-800 border border-slate-700 rounded-lg p-5 mb-6 space-y-3">
                <h2 className="text-lg font-semibold text-amber-400">
                  Node Execution Steps ({nodeLogs.length})
                </h2>
                <div className="space-y-3">
                  {nodeLogs.map((log, idx) => (
                    <div
                      key={`${log.node_id}-${idx}`}
                      className="bg-slate-900 border border-slate-700 rounded-lg p-3.5 space-y-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <span className="font-mono text-xs text-slate-200 font-semibold">
                            {log.node_id}
                          </span>
                          {log.node_type && (
                            <span className="ml-2 text-xs text-slate-400">
                              ({log.node_type})
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          {log.duration_ms !== undefined &&
                            log.duration_ms !== null && (
                              <span className="text-xs text-slate-400">
                                {log.duration_ms} ms
                              </span>
                            )}
                          <span
                            className={`px-2 py-0.5 rounded text-xs font-medium ${getStatusColor(String(log.status || "unknown"))}`}
                          >
                            {log.status || "unknown"}
                          </span>
                        </div>
                      </div>
                      {log.error_message && (
                        <div className="text-xs font-mono text-rose-300 bg-rose-950/60 border border-rose-800/60 rounded p-2">
                          {log.error_message}
                        </div>
                      )}
                      {log.output_data !== undefined &&
                        log.output_data !== null && (
                          <pre className="text-xs font-mono text-slate-300 bg-slate-950 rounded p-2 overflow-x-auto max-h-48">
                            {typeof log.output_data === "string"
                              ? log.output_data
                              : JSON.stringify(log.output_data, null, 2)}
                          </pre>
                        )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {execution.node_results && (
              <div className="bg-slate-800 border border-slate-700 rounded-lg p-5">
                <h2 className="text-lg font-semibold text-amber-400 mb-3">
                  Node Results
                </h2>
                <pre className="text-xs font-mono text-slate-300 bg-slate-900 rounded p-3 overflow-x-auto">
                  {JSON.stringify(execution.node_results, null, 2)}
                </pre>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
