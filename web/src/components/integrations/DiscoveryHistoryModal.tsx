import { useQuery } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Clock, X, XCircle } from "lucide-react";
import api, {
  type DiscoveryJobHistoryItem,
  type DiscoveryJobItem,
} from "@/lib/api";
import Button from "@/components/Button";

interface DiscoveryHistoryModalProps {
  job: DiscoveryJobItem | null;
  onClose: () => void;
}

function getStatusBadge(status: string) {
  switch (status) {
    case "completed":
      return {
        icon: <CheckCircle2 className="w-4 h-4 text-emerald-400" />,
        className:
          "bg-emerald-950/60 text-emerald-300 border border-emerald-700/60",
      };
    case "failed":
      return {
        icon: <XCircle className="w-4 h-4 text-red-400" />,
        className: "bg-red-950/60 text-red-300 border border-red-700/60",
      };
    case "running":
      return {
        icon: <Clock className="w-4 h-4 text-amber-400 animate-pulse" />,
        className: "bg-amber-950/60 text-amber-300 border border-amber-700/60",
      };
    default:
      return {
        icon: <AlertCircle className="w-4 h-4 text-slate-400" />,
        className: "bg-slate-800 text-slate-300 border border-slate-700",
      };
  }
}

export default function DiscoveryHistoryModal({
  job,
  onClose,
}: DiscoveryHistoryModalProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["discoveryHistory", job?.id ?? null],
    queryFn: () => api.getDiscoveryJobHistory(job!.id),
    enabled: job !== null,
  });

  if (!job) return null;

  const entries: DiscoveryJobHistoryItem[] = data?.history ?? data?.runs ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 overflow-y-auto"
      role="dialog"
      aria-modal="true"
      aria-labelledby="discovery-history-title"
    >
      <div className="w-full max-w-3xl rounded-xl border border-slate-700 bg-slate-900 shadow-2xl my-8">
        <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
          <div>
            <h3
              id="discovery-history-title"
              className="text-lg font-semibold text-white"
            >
              {job.name} — Run History
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Provider: {job.provider.toUpperCase()} · Profile #{job.id}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close run history"
            className="text-slate-400 hover:text-white"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 max-h-[65vh] overflow-y-auto space-y-3">
          {isLoading ? (
            <p className="text-sm text-slate-400 py-6 text-center">
              Loading run history...
            </p>
          ) : entries.length === 0 ? (
            <div className="text-center py-8 space-y-2">
              <Clock className="w-8 h-8 text-slate-600 mx-auto" />
              <p className="text-sm text-slate-400">
                No execution runs recorded yet.
              </p>
            </div>
          ) : (
            entries.map((run) => {
              const badge = getStatusBadge(run.status);
              const durationSec =
                run.completed_at && run.started_at
                  ? Math.max(
                      0,
                      Math.round(
                        (new Date(run.completed_at).getTime() -
                          new Date(run.started_at).getTime()) /
                          1000,
                      ),
                    )
                  : null;

              return (
                <div
                  key={run.id}
                  className="rounded-lg border border-slate-800 bg-slate-950/70 p-4 space-y-2"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2.5">
                      {badge.icon}
                      <div>
                        <p className="text-sm font-medium text-white">
                          {new Date(run.started_at).toLocaleString()}
                        </p>
                        {durationSec !== null && (
                          <p className="text-xs text-slate-400">
                            Duration: {durationSec}s
                          </p>
                        )}
                      </div>
                    </div>
                    <span
                      className={`px-2 py-0.5 text-xs font-medium rounded-full ${badge.className}`}
                    >
                      {run.status}
                    </span>
                  </div>
                  <p className="text-xs text-slate-300">
                    Entities discovered:{" "}
                    <span className="font-semibold text-yellow-400">
                      {run.entities_discovered}
                    </span>
                  </p>
                  {run.error_message && (
                    <div className="rounded border border-red-800/60 bg-red-950/40 p-2 text-xs text-red-300">
                      {run.error_message}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        <div className="flex justify-end border-t border-slate-800 px-6 py-3">
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}
