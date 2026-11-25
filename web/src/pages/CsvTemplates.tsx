import { Fragment, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  Upload,
  FileSpreadsheet,
  CheckCircle2,
  Clock,
  Loader2,
  AlertCircle,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  X,
  Cloud,
} from "lucide-react";
import toast from "react-hot-toast";
import api, {
  type CsvImportJob,
  type CsvImportJobError,
  type CsvImportJobStatus,
  type CsvImportJobsResponse,
  type CsvImportResponse,
} from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";
import CsvImportModal from "@/components/CsvImportModal";

interface CsvTemplateSpec {
  resource: string;
  label: string;
  max_rows: number;
  headers: string[];
  required: string[];
  rules: Record<string, string>;
  example: string[];
}

const ACTIVE_JOB_POLL_MS = 2000;
const JOBS_LIST_POLL_MS = 3000;

function isJobActive(status?: CsvImportJobStatus): boolean {
  return status === "pending" || status === "processing";
}

function truncateSha256(hash?: string | null): string {
  if (!hash) return "—";
  return hash.length > 12 ? `${hash.slice(0, 12)}…` : hash;
}

function formatJobError(err: CsvImportJobError): string {
  if (typeof err === "string") return err;
  const prefix =
    err.row !== undefined
      ? `Row ${err.row}${err.field ? ` (${err.field})` : ""}: `
      : err.field
        ? `${err.field}: `
        : "";
  return `${prefix}${err.message || err.error || JSON.stringify(err)}`;
}

function getJobErrorsList(job: CsvImportJob): string[] {
  const list: string[] = [];
  if (job.error_message) {
    list.push(job.error_message);
  }
  if (Array.isArray(job.errors)) {
    for (const item of job.errors) {
      list.push(formatJobError(item));
    }
  }
  return list;
}

function JobStatusBadge({ status }: { status: CsvImportJobStatus }) {
  switch (status) {
    case "pending":
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-500/15 text-amber-300 border border-amber-500/30">
          <Clock className="w-3 h-3" />
          Pending
        </span>
      );
    case "processing":
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-500/15 text-blue-300 border border-blue-500/30">
          <Loader2 className="w-3 h-3 animate-spin" />
          Processing
        </span>
      );
    case "completed":
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-500/15 text-green-300 border border-green-500/30">
          <CheckCircle2 className="w-3 h-3" />
          Completed
        </span>
      );
    case "failed":
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-500/15 text-red-300 border border-red-500/30">
          <AlertCircle className="w-3 h-3" />
          Failed
        </span>
      );
  }
}

export default function CsvTemplates() {
  const queryClient = useQueryClient();
  const [importResource, setImportResource] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [queuedJobSeed, setQueuedJobSeed] = useState<CsvImportJob | null>(null);
  const [queuedMessage, setQueuedMessage] = useState<string | null>(null);
  const [expandedJobIds, setExpandedJobIds] = useState<Record<string, boolean>>(
    {},
  );
  const lastTerminalRef = useRef<string | null>(null);

  const { data, isLoading } = useQuery<{
    max_rows: number;
    templates: CsvTemplateSpec[];
  }>({
    queryKey: ["csv-templates"],
    queryFn: () => api.getCsvTemplates(),
  });

  const {
    data: jobsData,
    isLoading: jobsLoading,
    isFetching: jobsFetching,
    refetch: refetchJobs,
  } = useQuery<CsvImportJobsResponse>({
    queryKey: ["csv-import-jobs"],
    queryFn: async () => {
      try {
        return await api.getCsvImportJobs();
      } catch (err: unknown) {
        const status = (err as { response?: { status?: number } })?.response
          ?.status;
        if (status === 404) {
          return { items: [], total: 0 };
        }
        throw err;
      }
    },
    retry: false,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? query.state.data?.jobs ?? [];
      const hasActive = items.some((job) => isJobActive(job.status));
      return hasActive ? JOBS_LIST_POLL_MS : false;
    },
  });

  const { data: polledActiveJob } = useQuery<CsvImportJob>({
    queryKey: ["csv-import-job", activeJobId],
    queryFn: () => api.getCsvImportJob(activeJobId!),
    enabled: Boolean(activeJobId),
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status ?? queuedJobSeed?.status;
      return isJobActive(status) ? ACTIVE_JOB_POLL_MS : false;
    },
  });

  const activeJob: CsvImportJob | null =
    activeJobId && (polledActiveJob || queuedJobSeed)
      ? {
          ...(queuedJobSeed ?? {
            job_id: activeJobId,
            resource: "",
            status: "pending",
          }),
          ...(polledActiveJob ?? {}),
        }
      : null;

  useEffect(() => {
    if (!polledActiveJob) return;
    if (isJobActive(polledActiveJob.status)) return;
    const key = `${polledActiveJob.job_id}:${polledActiveJob.status}`;
    if (lastTerminalRef.current === key) return;
    lastTerminalRef.current = key;
    queryClient.invalidateQueries({ queryKey: ["csv-import-jobs"] });
  }, [polledActiveJob, queryClient]);

  const recentJobs: CsvImportJob[] = jobsData?.items ?? jobsData?.jobs ?? [];

  const handleDownload = async (resource: string) => {
    try {
      const content = await api.downloadCsvTemplate(resource);
      const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${resource}_template.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Failed to download template");
    }
  };

  const handleImportSuccess = (result?: CsvImportResponse) => {
    if (result?.job_id) {
      lastTerminalRef.current = null;
      setActiveJobId(result.job_id);
      setQueuedMessage(result.message ?? null);
      setQueuedJobSeed({
        job_id: result.job_id,
        resource: result.resource || importResource || "",
        status: result.status || "pending",
        filename: result.filename ?? null,
        storage_backend: result.storage_backend ?? null,
        storage_key: result.storage_key ?? null,
        file_sha256: result.file_sha256 ?? null,
        total_rows: result.total_rows,
        imported_count: 0,
        skipped_count: 0,
        errors: result.errors ?? [],
      });
    }
    queryClient.invalidateQueries({ queryKey: ["csv-import-jobs"] });
  };

  const toggleJobErrors = (jobId: string) => {
    setExpandedJobIds((prev) => ({
      ...prev,
      [jobId]: !prev[jobId],
    }));
  };

  const activeJobErrors = activeJob ? getJobErrorsList(activeJob) : [];

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-white flex items-center gap-3">
          <FileSpreadsheet className="w-8 h-8 text-primary-400" />
          CSV Import Templates
        </h1>
        <p className="mt-2 text-slate-400">
          Download exact CSV templates (up to {data?.max_rows ?? 1000} rows per
          upload). Headers and column order are strictly validated on import.
        </p>
      </div>

      {activeJob && (
        <Card
          className="border-primary-500/40 bg-slate-900/80"
          data-testid="active-import-job-card"
        >
          <CardHeader className="flex flex-row items-center justify-between gap-2">
            <div className="flex items-center gap-3 flex-wrap">
              <h2 className="text-lg font-semibold text-white">
                Import Job{" "}
                <span className="font-mono text-primary-300">
                  {activeJob.job_id}
                </span>
              </h2>
              <JobStatusBadge status={activeJob.status} />
              {activeJob.storage_backend && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono bg-slate-800 text-slate-300 border border-slate-700">
                  <Cloud className="w-3 h-3 text-primary-400" />
                  {activeJob.storage_backend.toUpperCase()}
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={() => {
                setActiveJobId(null);
                setQueuedJobSeed(null);
                setQueuedMessage(null);
              }}
              className="p-1 text-slate-400 hover:text-white rounded"
              aria-label="Dismiss active import job"
            >
              <X className="w-4 h-4" />
            </button>
          </CardHeader>
          <CardContent className="space-y-3">
            {queuedMessage && isJobActive(activeJob.status) && (
              <p className="text-sm text-slate-300">{queuedMessage}</p>
            )}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
              <div className="bg-slate-800/70 border border-slate-700 rounded p-2.5">
                <div className="text-slate-400">Resource</div>
                <div className="font-mono text-white mt-0.5">
                  {activeJob.resource || "—"}
                </div>
              </div>
              <div className="bg-slate-800/70 border border-slate-700 rounded p-2.5">
                <div className="text-slate-400">Imported / Total</div>
                <div className="font-semibold text-green-300 mt-0.5">
                  {activeJob.imported_count ?? 0}
                  {activeJob.total_rows !== undefined
                    ? ` / ${activeJob.total_rows}`
                    : ""}
                </div>
              </div>
              <div className="bg-slate-800/70 border border-slate-700 rounded p-2.5">
                <div className="text-slate-400">Skipped</div>
                <div className="font-semibold text-amber-300 mt-0.5">
                  {activeJob.skipped_count ?? 0}
                </div>
              </div>
              <div className="bg-slate-800/70 border border-slate-700 rounded p-2.5">
                <div className="text-slate-400">SHA-256</div>
                <div
                  className="font-mono text-slate-200 mt-0.5"
                  title={activeJob.file_sha256 || undefined}
                >
                  {truncateSha256(activeJob.file_sha256)}
                </div>
              </div>
            </div>

            {activeJobErrors.length > 0 && (
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-xs text-red-300 space-y-1 max-h-40 overflow-y-auto">
                <div className="font-semibold text-red-400 flex items-center gap-1.5">
                  <AlertCircle className="w-3.5 h-3.5" />
                  Row Errors ({activeJobErrors.length})
                </div>
                {activeJobErrors.map((err, idx) => (
                  <div key={idx}>{err}</div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {data?.templates?.map((tpl) => (
            <Card key={tpl.resource}>
              <CardHeader className="flex flex-row items-center justify-between gap-2">
                <div>
                  <h2 className="text-xl font-semibold text-white">
                    {tpl.label}
                  </h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Max {tpl.max_rows} rows • {tpl.headers.length} columns
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => handleDownload(tpl.resource)}
                  >
                    <Download className="w-4 h-4 mr-1.5" />
                    Template
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => setImportResource(tpl.resource)}
                  >
                    <Upload className="w-4 h-4 mr-1.5" />
                    Import
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div>
                  <div className="text-xs uppercase text-slate-500 mb-1">
                    Exact Header Row
                  </div>
                  <div className="text-xs font-mono text-primary-300 bg-slate-900 border border-slate-700 rounded p-2 overflow-x-auto">
                    {tpl.headers.join(",")}
                  </div>
                </div>
                <div className="space-y-1">
                  {tpl.headers.map((h) => (
                    <div
                      key={h}
                      className="text-xs text-slate-400 flex items-start gap-1.5"
                    >
                      <CheckCircle2 className="w-3.5 h-3.5 text-green-400 mt-0.5 flex-shrink-0" />
                      <span>
                        <span className="font-mono text-slate-200">{h}</span>
                        {tpl.required.includes(h) && (
                          <span className="text-red-400 ml-1">*</span>
                        )}
                        : {tpl.rules[h]}
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-2">
          <div>
            <h2 className="text-xl font-semibold text-white">
              Recent Import Jobs
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Async S3-backed batch CSV imports and processing history
            </p>
          </div>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => refetchJobs()}
            disabled={jobsFetching}
          >
            <RefreshCw
              className={`w-4 h-4 mr-1.5 ${jobsFetching ? "animate-spin" : ""}`}
            />
            Refresh
          </Button>
        </CardHeader>
        <CardContent>
          {jobsLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 text-primary-400 animate-spin" />
            </div>
          ) : recentJobs.length === 0 ? (
            <p className="text-sm text-slate-400 py-6 text-center">
              No recent import jobs yet.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-700 text-xs uppercase text-slate-400">
                    <th className="py-2.5 pr-4">Status</th>
                    <th className="py-2.5 pr-4">Resource</th>
                    <th className="py-2.5 pr-4">Filename</th>
                    <th className="py-2.5 pr-4">Progress</th>
                    <th className="py-2.5 pr-4">SHA-256</th>
                    <th className="py-2.5">Errors</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {recentJobs.map((job) => {
                    const jobErrors = getJobErrorsList(job);
                    const isExpanded = Boolean(expandedJobIds[job.job_id]);
                    return (
                      <Fragment key={job.job_id}>
                        <tr className="text-slate-200">
                          <td className="py-3 pr-4 whitespace-nowrap">
                            <JobStatusBadge status={job.status} />
                          </td>
                          <td className="py-3 pr-4 font-mono text-xs whitespace-nowrap">
                            <div>{job.resource}</div>
                            {job.storage_backend && (
                              <span className="text-[11px] text-slate-400">
                                {job.storage_backend}
                              </span>
                            )}
                          </td>
                          <td className="py-3 pr-4 text-xs text-slate-300">
                            {job.filename || "Pasted CSV"}
                          </td>
                          <td className="py-3 pr-4 text-xs whitespace-nowrap">
                            <span className="text-green-300">
                              {job.imported_count ?? 0} imported
                            </span>
                            {job.skipped_count ? (
                              <span className="text-amber-300 ml-2">
                                {job.skipped_count} skipped
                              </span>
                            ) : null}
                            {job.total_rows !== undefined ? (
                              <span className="text-slate-400 ml-1">
                                / {job.total_rows}
                              </span>
                            ) : null}
                          </td>
                          <td
                            className="py-3 pr-4 font-mono text-xs text-slate-400 whitespace-nowrap"
                            title={job.file_sha256 || undefined}
                          >
                            {truncateSha256(job.file_sha256)}
                          </td>
                          <td className="py-3 text-xs whitespace-nowrap">
                            {jobErrors.length > 0 ? (
                              <button
                                type="button"
                                onClick={() => toggleJobErrors(job.job_id)}
                                aria-expanded={isExpanded}
                                className="inline-flex items-center gap-1 text-red-400 hover:text-red-300 font-medium"
                              >
                                {isExpanded ? (
                                  <ChevronDown className="w-3.5 h-3.5" />
                                ) : (
                                  <ChevronRight className="w-3.5 h-3.5" />
                                )}
                                {jobErrors.length}{" "}
                                {jobErrors.length === 1 ? "error" : "errors"}
                              </button>
                            ) : (
                              <span className="text-slate-500">0</span>
                            )}
                          </td>
                        </tr>
                        {isExpanded && jobErrors.length > 0 && (
                          <tr className="bg-red-500/5">
                            <td colSpan={6} className="px-4 py-3">
                              <div className="text-xs text-red-300 space-y-1 font-mono">
                                {jobErrors.map((err, idx) => (
                                  <div key={idx}>{err}</div>
                                ))}
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {importResource && (
        <CsvImportModal
          resource={importResource}
          isOpen={!!importResource}
          onClose={() => setImportResource(null)}
          onSuccess={handleImportSuccess}
        />
      )}
    </div>
  );
}
