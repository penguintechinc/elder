import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  ArrowLeft,
  AlertTriangle,
  CheckCircle2,
  Circle,
  Clock,
  Copy,
  Check,
  Database,
  ExternalLink,
  HardDrive,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  Terminal,
  XCircle,
} from "lucide-react";
import api, { type RunExecution } from "@/lib/api";
import {
  getExecutionStatusBadgeClass,
  getRuntimeMeta,
  getStorageBackendMeta,
} from "@/modules/runs/runtimes";

interface RunExecutionDetailRecord extends RunExecution {
  worker_id?: string | null;
  stdout_log?: string | null;
  stderr_log?: string | null;
  execution_namespace?: string | null;
  storage_backend?: "database" | "s3" | string | null;
  storage_key?: string | null;
  code_sha256?: string | null;
  worker_cluster?: string | null;
}

type StepState = "completed" | "active" | "failed" | "pending";

interface TimelineStep {
  id: string;
  label: string;
  state: StepState;
  timestamp?: string | null;
  detail?: string | null;
}

const ACTIVE_STATUSES = new Set(["pending", "running", "queued"]);
const FAILED_STATUSES = new Set(["failed", "error", "timeout"]);
const COMPLETED_STATUSES = new Set(["completed", "success"]);
const POLL_INTERVAL_MS = 10_000;

function isExecutionActive(status?: string | null): boolean {
  return ACTIVE_STATUSES.has((status || "").toLowerCase());
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "0 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)} s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${seconds}s`;
}

function formatTimestamp(iso?: string | null): string | null {
  if (!iso) return null;
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return iso;
  return new Date(parsed).toLocaleString();
}

function buildTimelineSteps(
  execution: RunExecutionDetailRecord,
  elapsedMs: number | null,
): TimelineStep[] {
  const status = (execution.status || "").toLowerCase();
  const isPending = status === "pending" || status === "queued";
  const isRunning = status === "running";
  const isFailed = FAILED_STATUSES.has(status);
  const isTimedOut = status === "timeout";
  const isFinished = COMPLETED_STATUSES.has(status);
  const isTerminal = isFinished || isFailed;
  const hasClaim = Boolean(execution.worker_id || execution.started_at);

  const queuedState: StepState =
    isPending && !hasClaim ? "active" : "completed";

  const claimedState: StepState = isTerminal || isRunning
    ? "completed"
    : hasClaim
      ? "active"
      : "pending";

  const runningState: StepState = isRunning
    ? "active"
    : isTerminal
      ? "completed"
      : "pending";

  const finalLabel = isTimedOut
    ? "Timed Out"
    : isFailed
      ? "Failed"
      : "Finished";
  const finalState: StepState = isFinished
    ? "completed"
    : isFailed
      ? "failed"
      : "pending";

  const durationDetail =
    execution.duration_ms !== undefined && execution.duration_ms !== null
      ? formatDuration(execution.duration_ms)
      : elapsedMs !== null
        ? `${formatDuration(elapsedMs)} elapsed`
        : null;

  const exitDetail =
    execution.exit_code !== undefined && execution.exit_code !== null
      ? `exit ${execution.exit_code}`
      : null;

  const stdoutText = execution.stdout || execution.stdout_log || "";
  const isWarmingUp =
    isRunning &&
    (stdoutText.trim().length === 0 ||
      stdoutText.trim().endsWith("warming up..."));
  const warmingState: StepState = isPending
    ? "pending"
    : isWarmingUp
      ? "active"
      : "completed";
  const runningCodeState: StepState =
    isPending || isWarmingUp
      ? "pending"
      : isRunning
        ? "active"
        : runningState;

  return [
    {
      id: "queued",
      label: "Queued",
      state: queuedState,
      timestamp: execution.created_at,
      detail: execution.trigger_type ? `Trigger: ${execution.trigger_type}` : null,
    },
    {
      id: "claimed",
      label: "Claimed by Worker",
      state: claimedState,
      timestamp: execution.started_at,
      detail: execution.worker_id
        ? execution.worker_id
        : isPending
          ? "Waiting for worker..."
          : null,
    },
    {
      id: "warming",
      label: "Warming Up",
      state: warmingState,
      timestamp: execution.started_at,
      detail: isWarmingUp ? "Preparing sandbox..." : null,
    },
    {
      id: "running",
      label: "Running Code",
      state: runningCodeState,
      timestamp: execution.started_at,
      detail: durationDetail,
    },
    {
      id: "final",
      label: finalLabel,
      state: finalState,
      timestamp: execution.completed_at,
      detail: exitDetail,
    },
  ];
}

function StepIcon({ state }: { state: StepState }) {
  switch (state) {
    case "completed":
      return (
        <CheckCircle2
          className="w-5 h-5 text-emerald-400 shrink-0"
          aria-hidden="true"
        />
      );
    case "active":
      return (
        <Loader2
          className="w-5 h-5 text-sky-400 animate-spin shrink-0"
          aria-hidden="true"
        />
      );
    case "failed":
      return (
        <XCircle
          className="w-5 h-5 text-rose-400 shrink-0"
          aria-hidden="true"
        />
      );
    default:
      return (
        <Circle
          className="w-5 h-5 text-slate-600 shrink-0"
          aria-hidden="true"
        />
      );
  }
}

export default function RunExecutionDetail() {
  const { executionId } = useParams<{ executionId: string }>();
  const [autoPollEnabled, setAutoPollEnabled] = useState(true);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [copiedSha256, setCopiedSha256] = useState(false);

  const {
    data: rawExecution,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useQuery({
    queryKey: ["run-execution", executionId],
    queryFn: () =>
      executionId ? api.getRunExecution(executionId) : Promise.resolve(null),
    enabled: !!executionId,
    refetchInterval: (query) => {
      if (!autoPollEnabled) return false;
      const currentStatus = (
        query.state.data as RunExecutionDetailRecord | null | undefined
      )?.status;
      return isExecutionActive(currentStatus) ? POLL_INTERVAL_MS : false;
    },
  });

  const execution = (rawExecution ?? null) as RunExecutionDetailRecord | null;
  const isActive = isExecutionActive(execution?.status);

  useEffect(() => {
    if (!isActive) return;
    setNowMs(Date.now());
    const timer = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [isActive]);

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

  const stdoutText = execution?.stdout_log ?? execution?.stdout ?? "";
  const stderrText = execution?.stderr_log ?? execution?.stderr ?? "";

  const elapsedAnchor = execution?.started_at || execution?.created_at || null;
  const elapsedMs =
    execution?.duration_ms !== undefined && execution?.duration_ms !== null
      ? execution.duration_ms
      : isActive && elapsedAnchor && !Number.isNaN(Date.parse(elapsedAnchor))
        ? Math.max(0, nowMs - Date.parse(elapsedAnchor))
        : null;

  const timelineSteps = execution
    ? buildTimelineSteps(execution, elapsedMs)
    : [];

  const runtimeMeta = execution?.runtime
    ? getRuntimeMeta(execution.runtime)
    : null;

  const storageBackendMeta =
    execution?.storage_backend === "s3" ||
    execution?.storage_backend === "database"
      ? getStorageBackendMeta(execution.storage_backend)
      : null;

  const title =
    execution?.function_name ||
    (execution?.function_id
      ? `CodeRun #${execution.function_id}`
      : "CodeRun Execution");

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <Link
            to="/streams/executions"
            className="inline-flex items-center gap-2 text-sm text-amber-400 hover:text-amber-300 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" aria-hidden="true" />
            Back to Executions
          </Link>

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
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors"
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
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors"
            >
              <RefreshCw
                className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
              Refresh
            </button>

            {execution?.function_id ? (
              <Link
                to={`/runs/${execution.function_id}`}
                data-testid="open-coderun-link"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-amber-300 border border-slate-700 transition-colors"
              >
                <ExternalLink className="w-3.5 h-3.5" aria-hidden="true" />
                Open CodeRun
              </Link>
            ) : null}
          </div>
        </div>

        {error && (
          <div className="bg-red-900/80 border border-red-700 text-red-100 px-4 py-3 rounded-lg">
            Failed to load CodeRun execution details.
          </div>
        )}

        {isLoading && (
          <div className="flex justify-center items-center py-12">
            <div className="text-amber-400">Loading execution...</div>
          </div>
        )}

        {execution && (
          <>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-3xl font-bold text-amber-400 flex items-center gap-2">
                    <Terminal className="w-7 h-7" aria-hidden="true" />
                    {title}
                  </h1>
                  {runtimeMeta && (
                    <span
                      data-testid="execution-runtime-badge"
                      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${runtimeMeta.badgeClass}`}
                    >
                      {runtimeMeta.label}
                    </span>
                  )}
                  {execution.trigger_type && (
                    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-300 border border-slate-700">
                      {execution.trigger_type}
                    </span>
                  )}
                </div>
                <p className="text-slate-400 text-sm">
                  Execution ID:{" "}
                  <span className="font-mono text-slate-200">
                    {execution.execution_id}
                  </span>
                </p>
              </div>

              <div className="flex items-center gap-3">
                {elapsedMs !== null && (
                  <span
                    data-testid="execution-elapsed"
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono bg-slate-800 text-slate-200 border border-slate-700"
                  >
                    <Clock className="w-3.5 h-3.5 text-amber-400" aria-hidden="true" />
                    {formatDuration(elapsedMs)}
                  </span>
                )}
                <span
                  data-testid="execution-status-badge"
                  className={`inline-flex px-3.5 py-1.5 rounded-lg text-sm font-semibold ${getExecutionStatusBadgeClass(execution.status)}`}
                >
                  {execution.status.charAt(0).toUpperCase() +
                    execution.status.slice(1)}
                </span>
              </div>
            </div>

            {execution.error_message && (
              <div
                data-testid="execution-error-banner"
                role="alert"
                className="flex items-start gap-3 bg-rose-950/70 border border-rose-700/70 text-rose-200 px-4 py-3.5 rounded-xl"
              >
                <AlertTriangle
                  className="w-5 h-5 text-rose-400 shrink-0 mt-0.5"
                  aria-hidden="true"
                />
                <div className="min-w-0 space-y-1">
                  <div className="text-xs font-semibold uppercase tracking-wide text-rose-300">
                    Execution Error
                  </div>
                  <pre className="text-sm font-mono whitespace-pre-wrap break-words text-rose-100">
                    {execution.error_message}
                  </pre>
                </div>
              </div>
            )}

            <section
              data-testid="execution-timeline"
              aria-label="Execution progress"
              className="bg-slate-800 border border-slate-700 rounded-xl p-5"
            >
              <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-4">
                Execution Timeline
              </h2>
              <ol className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {timelineSteps.map((step, index) => {
                  const formattedTs = formatTimestamp(step.timestamp);
                  const borderClass =
                    step.state === "active"
                      ? "border-sky-500/70 bg-sky-950/30"
                      : step.state === "failed"
                        ? "border-rose-600/70 bg-rose-950/30"
                        : step.state === "completed"
                          ? "border-emerald-700/60 bg-slate-900/70"
                          : "border-slate-700/80 bg-slate-900/40";
                  return (
                    <li
                      key={step.id}
                      data-testid={`timeline-step-${step.id}`}
                      data-step-state={step.state}
                      className={`rounded-lg border p-3.5 space-y-1.5 ${borderClass}`}
                    >
                      <div className="flex items-center gap-2">
                        <StepIcon state={step.state} />
                        <span className="text-xs font-mono text-slate-500">
                          {index + 1}.
                        </span>
                        <span className="text-sm font-semibold text-slate-100">
                          {step.label}
                        </span>
                      </div>
                      {step.detail && (
                        <div className="text-xs font-mono text-slate-300 truncate pl-7">
                          {step.detail}
                        </div>
                      )}
                      {formattedTs && (
                        <div className="text-[11px] text-slate-400 pl-7">
                          {formattedTs}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            </section>

            <section className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-4">
              <div className="flex items-center justify-between border-b border-slate-700 pb-3">
                <h2 className="text-lg font-semibold text-amber-400">
                  Stdout / Stderr Logs
                </h2>
                {isActive && (
                  <span className="inline-flex items-center gap-1.5 text-xs text-sky-300 font-medium">
                    <span
                      className="w-2 h-2 rounded-full bg-sky-400 animate-pulse"
                      aria-hidden="true"
                    />
                    Streaming updates
                  </span>
                )}
              </div>

              <div className="space-y-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1.5">
                    stdout
                  </div>
                  <pre
                    data-testid="execution-stdout"
                    className="p-3.5 rounded-lg bg-slate-950 border border-slate-800 text-emerald-200 text-xs font-mono overflow-x-auto whitespace-pre-wrap max-h-96"
                  >
                    {stdoutText ||
                      (isActive ? "Waiting for stdout..." : "(empty)")}
                  </pre>
                </div>

                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-rose-300/90 mb-1.5">
                    stderr
                  </div>
                  <pre
                    data-testid="execution-stderr"
                    className={`p-3.5 rounded-lg bg-slate-950 border text-xs font-mono overflow-x-auto whitespace-pre-wrap max-h-96 ${
                      stderrText
                        ? "border-rose-900/70 text-rose-200"
                        : "border-slate-800 text-slate-500"
                    }`}
                  >
                    {stderrText || "(empty)"}
                  </pre>
                </div>
              </div>
            </section>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <section className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-3">
                <h2 className="text-lg font-semibold text-amber-400 border-b border-slate-700 pb-2.5">
                  Input Payload (JSON)
                </h2>
                <pre
                  data-testid="execution-input-payload"
                  className="p-3.5 rounded-lg bg-slate-950 border border-slate-800 text-slate-200 text-xs font-mono overflow-x-auto whitespace-pre-wrap max-h-80"
                >
                  {execution.input_data !== undefined &&
                  execution.input_data !== null
                    ? JSON.stringify(execution.input_data, null, 2)
                    : "(empty)"}
                </pre>
              </section>

              <section className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-3">
                <h2 className="text-lg font-semibold text-amber-400 border-b border-slate-700 pb-2.5">
                  Structured Output (JSON)
                </h2>
                <pre
                  data-testid="execution-output-data"
                  className="p-3.5 rounded-lg bg-slate-950 border border-slate-800 text-slate-200 text-xs font-mono overflow-x-auto whitespace-pre-wrap max-h-80"
                >
                  {execution.output_data !== undefined &&
                  execution.output_data !== null
                    ? JSON.stringify(execution.output_data, null, 2)
                    : isActive
                      ? "Pending completion..."
                      : "(no structured output)"}
                </pre>
              </section>
            </div>

            <section
              data-testid="execution-metadata"
              className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-4"
            >
              <h2 className="text-lg font-semibold text-amber-400 border-b border-slate-700 pb-3 flex items-center gap-2">
                {execution.storage_backend === "s3" ? (
                  <HardDrive className="w-5 h-5" aria-hidden="true" />
                ) : (
                  <Database className="w-5 h-5" aria-hidden="true" />
                )}
                Execution & Artifact Metadata
              </h2>

              <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-sm">
                <div>
                  <dt className="text-xs font-medium text-slate-400 mb-1">
                    CodeRun Function
                  </dt>
                  <dd>
                    {execution.function_id ? (
                      <Link
                        to={`/runs/${execution.function_id}`}
                        data-testid="metadata-function-link"
                        className="inline-flex items-center gap-1 text-amber-400 hover:text-amber-300 font-medium"
                      >
                        {execution.function_name ||
                          `CodeRun #${execution.function_id}`}
                        <ExternalLink
                          className="w-3.5 h-3.5"
                          aria-hidden="true"
                        />
                      </Link>
                    ) : (
                      <span className="text-slate-300">
                        {execution.function_name || "—"}
                      </span>
                    )}
                  </dd>
                </div>

                <div>
                  <dt className="text-xs font-medium text-slate-400 mb-1">
                    Runtime
                  </dt>
                  <dd>
                    {runtimeMeta ? (
                      <span
                        data-testid="metadata-runtime"
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${runtimeMeta.badgeClass}`}
                      >
                        {runtimeMeta.label}
                      </span>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </dd>
                </div>

                <div>
                  <dt className="text-xs font-medium text-slate-400 mb-1">
                    Exit Code
                  </dt>
                  <dd
                    data-testid="metadata-exit-code"
                    className={`font-mono text-xs font-semibold ${
                      execution.exit_code === 0
                        ? "text-emerald-300"
                        : execution.exit_code !== undefined &&
                            execution.exit_code !== null
                          ? "text-rose-300"
                          : "text-slate-400"
                    }`}
                  >
                    {execution.exit_code !== undefined &&
                    execution.exit_code !== null
                      ? String(execution.exit_code)
                      : "—"}
                  </dd>
                </div>

                <div>
                  <dt className="text-xs font-medium text-slate-400 mb-1">
                    Worker ID
                  </dt>
                  <dd
                    data-testid="metadata-worker-id"
                    className="font-mono text-xs text-slate-200 break-all"
                  >
                    {execution.worker_id || "—"}
                  </dd>
                </div>

                <div>
                  <dt className="text-xs font-medium text-slate-400 mb-1">
                    Execution Namespace
                  </dt>
                  <dd
                    data-testid="metadata-namespace"
                    className="font-mono text-xs text-slate-200 break-all"
                  >
                    {execution.execution_namespace || "—"}
                  </dd>
                </div>

                <div>
                  <dt className="text-xs font-medium text-slate-400 mb-1">
                    Storage Backend
                  </dt>
                  <dd>
                    {storageBackendMeta ? (
                      <span
                        data-testid="metadata-storage-backend"
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${storageBackendMeta.badgeClass}`}
                      >
                        {storageBackendMeta.label}
                      </span>
                    ) : execution.storage_backend ? (
                      <span
                        data-testid="metadata-storage-backend"
                        className="font-mono text-xs text-slate-200"
                      >
                        {execution.storage_backend}
                      </span>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </dd>
                </div>

                <div className="sm:col-span-2">
                  <dt className="text-xs font-medium text-slate-400 mb-1">
                    Storage Key
                  </dt>
                  <dd>
                    {execution.storage_key ? (
                      <code
                        data-testid="metadata-storage-key"
                        className="block text-xs font-mono text-slate-200 bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 break-all"
                      >
                        {execution.storage_key}
                      </code>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </dd>
                </div>

                {execution.code_sha256 && (
                  <div className="sm:col-span-2 lg:col-span-4">
                    <dt className="text-xs font-medium text-slate-400 mb-1">
                      Code SHA-256
                    </dt>
                    <dd className="flex items-center gap-2 max-w-xl">
                      <code
                        data-testid="metadata-code-sha256"
                        title={execution.code_sha256}
                        className="flex-1 min-w-0 truncate text-xs font-mono text-slate-200 bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5"
                      >
                        {execution.code_sha256}
                      </code>
                      <button
                        type="button"
                        onClick={() => handleCopySha256(execution.code_sha256!)}
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
          </>
        )}
      </div>
    </div>
  );
}
