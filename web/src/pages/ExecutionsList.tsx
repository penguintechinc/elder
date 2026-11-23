import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import api from "@/lib/api";

interface Execution {
  id: number;
  execution_id: string;
  stream_id?: number;
  stream_name?: string;
  status: string;
  trigger_type?: string;
  playbook_id?: number;
  input_data?: Record<string, unknown>;
  output_data?: Record<string, unknown>;
  error_message?: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  duration_ms?: number;
}

export default function ExecutionsList() {
  const {
    data: response,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["stream-executions"],
    queryFn: () => api.listAllStreamExecutions({ page: 1, per_page: 50 }),
  });

  const executions: Execution[] = response?.data || [];

  const getStatusColor = (status: string) => {
    switch (status.toLowerCase()) {
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
        return "bg-yellow-900 text-yellow-200";
      default:
        return "bg-slate-700 text-slate-300";
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-amber-400">Executions</h1>
          <p className="text-slate-400 mt-2">
            View and monitor automation workflow executions
          </p>
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
              Executions will appear here once playbooks are triggered
            </p>
          </div>
        )}

        {!isLoading && executions.length > 0 && (
          <div className="space-y-3">
            {executions.map((exec) => {
              const playbookId = exec.playbook_id ?? exec.stream_id;
              const detailPath = `/streams/executions/${exec.execution_id}`;
              return (
                <div
                  key={exec.execution_id}
                  className="relative group bg-slate-800 border border-slate-700 hover:border-amber-500/60 hover:bg-slate-750 rounded-lg px-5 py-4 transition-colors"
                >
                  <div className="flex flex-wrap items-center justify-between gap-4">
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-2.5">
                        <span
                          className="text-amber-400 font-semibold text-base truncate"
                          data-testid="stream-name"
                        >
                          {exec.stream_name ||
                            (playbookId
                              ? `Playbook #${playbookId}`
                              : "Playbook")}
                        </span>
                        <span
                          className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${getStatusColor(exec.status)}`}
                        >
                          {exec.status.charAt(0).toUpperCase() +
                            exec.status.slice(1)}
                        </span>
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
                      {playbookId ? (
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
