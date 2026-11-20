import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { AlertCircle, ArrowLeft, Search } from "lucide-react";
import api from "@/lib/api";

interface ErrorWithResponse {
  response?: {
    status?: number;
    data?: {
      error?: string;
      message?: string;
    };
  };
  message?: string;
}

interface ResolveErrorState {
  status?: number;
  message: string;
}

function isSafeInternalPath(url: string): boolean {
  return (
    url.startsWith("/") &&
    !url.startsWith("//") &&
    !url.startsWith("/\\") &&
    !/[\r\n]/.test(url)
  );
}

export default function VillageIdRedirect() {
  const { villageId } = useParams<{ villageId: string }>();
  const navigate = useNavigate();
  const [error, setError] = useState<ResolveErrorState | null>(null);

  useEffect(() => {
    let active = true;

    if (!villageId) {
      setError({ status: 400, message: "No Village ID provided" });
      return () => {
        active = false;
      };
    }

    setError(null);

    const resolveAndRedirect = async () => {
      try {
        const response = await api.resolveVillageId(villageId);
        if (!active) return;

        if (
          response?.redirect_url &&
          isSafeInternalPath(response.redirect_url)
        ) {
          navigate(response.redirect_url, { replace: true });
        } else {
          setError({
            status: 404,
            message: "No valid redirect URL found for this Village ID",
          });
        }
      } catch (err: unknown) {
        if (!active) return;
        const errorWithResponse = err as ErrorWithResponse | undefined;
        const status = errorWithResponse?.response?.status;
        const fallbackMessage =
          status === 404
            ? "This Village ID could not be found or you do not have access to it."
            : status === 403
              ? "You do not have permission to view this resource."
              : "Failed to resolve Village ID.";
        const errorMessage =
          errorWithResponse?.response?.data?.message ||
          errorWithResponse?.response?.data?.error ||
          (status ? fallbackMessage : undefined) ||
          (errorWithResponse instanceof Error
            ? errorWithResponse.message
            : undefined) ||
          fallbackMessage;
        setError({ status, message: errorMessage });
      }
    };

    resolveAndRedirect();

    return () => {
      active = false;
    };
  }, [villageId, navigate]);

  if (error) {
    const heading =
      error.status === 404
        ? "Village ID Not Found"
        : error.status === 403
          ? "Access Denied"
          : "Unable to Resolve Village ID";

    return (
      <div className="flex items-center justify-center min-h-screen bg-slate-900 p-6">
        <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-800/90 p-8 text-center shadow-2xl">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-500/15 border border-red-500/30">
            <AlertCircle className="h-6 w-6 text-red-400" />
          </div>
          <h1 className="text-xl font-semibold text-white mb-2">{heading}</h1>
          {villageId && (
            <p className="mb-3 inline-block rounded bg-slate-900/90 border border-slate-700 px-2.5 py-1 font-mono text-xs text-amber-300">
              {villageId}
            </p>
          )}
          <p className="text-sm text-slate-400 mb-6">{error.message}</p>
          <div className="flex flex-wrap items-center justify-center gap-3">
            <button
              type="button"
              onClick={() => navigate("/")}
              className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 transition-colors"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to Dashboard
            </button>
            <button
              type="button"
              onClick={() =>
                navigate(
                  villageId
                    ? `/search?q=${encodeURIComponent(villageId)}`
                    : "/search",
                )
              }
              className="inline-flex items-center gap-2 rounded-lg border border-slate-600 bg-slate-700/80 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700 hover:text-white transition-colors"
            >
              <Search className="h-4 w-4" />
              Search
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-slate-900 p-6">
      <div className="text-center">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
        <p className="text-sm text-slate-300">
          Resolving{" "}
          <span className="font-mono text-amber-300">
            {villageId || "Village ID"}
          </span>
          ...
        </p>
      </div>
    </div>
  );
}
