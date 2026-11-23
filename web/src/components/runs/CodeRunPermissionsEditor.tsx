import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Shield,
  ShieldCheck,
  Globe,
  Code2,
  ChevronDown,
  ChevronRight,
  Lock,
  Copy,
  Check,
} from "lucide-react";
import toast from "react-hot-toast";
import api, {
  DEFAULT_RUN_PERMISSIONS_CATALOG,
  type RunPermissionGroup,
  type RunPermissionsCatalogResponse,
  type RunRuntime,
} from "@/lib/api";
import { getRuntimeMeta } from "@/modules/runs/runtimes";
import { getHelperSdkSnippet } from "@/modules/runs/permissions";

export interface CodeRunPermissionsEditorProps {
  runtime: RunRuntime | string;
  grantedScopes: string[];
  onGrantedScopesChange: (scopes: string[]) => void;
  allowedEgressDomains: string;
  onAllowedEgressDomainsChange: (value: string) => void;
  idPrefix?: string;
}

export default function CodeRunPermissionsEditor({
  runtime,
  grantedScopes,
  onGrantedScopesChange,
  allowedEgressDomains,
  onAllowedEgressDomainsChange,
  idPrefix = "coderun-perms",
}: CodeRunPermissionsEditorProps) {
  const [isSdkOpen, setIsSdkOpen] = useState(false);
  const [copiedSdk, setCopiedSdk] = useState(false);

  const { data: catalogResponse } = useQuery<RunPermissionsCatalogResponse>({
    queryKey: ["run-permissions-catalog"],
    queryFn: () => api.getRunPermissionsCatalog(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const catalog = catalogResponse ?? DEFAULT_RUN_PERMISSIONS_CATALOG;
  const groups: RunPermissionGroup[] = catalog.groups ?? [];
  const selectedSet = new Set(grantedScopes);
  const runtimeMeta = getRuntimeMeta(runtime);
  const sdkRef = getHelperSdkSnippet(runtime);

  const toggleScope = (scope: string, grantable: boolean) => {
    if (!grantable) return;
    if (selectedSet.has(scope)) {
      onGrantedScopesChange(grantedScopes.filter((s) => s !== scope));
    } else {
      onGrantedScopesChange([...grantedScopes, scope]);
    }
  };

  const handleSelectAllGroup = (group: RunPermissionGroup) => {
    const grantableScopes = group.scopes
      .filter((item) => item.grantable)
      .map((item) => item.scope);
    const next = new Set(grantedScopes);
    for (const s of grantableScopes) {
      next.add(s);
    }
    onGrantedScopesChange(Array.from(next));
  };

  const handleClearGroup = (group: RunPermissionGroup) => {
    const groupScopeSet = new Set(group.scopes.map((item) => item.scope));
    onGrantedScopesChange(
      grantedScopes.filter((s) => !groupScopeSet.has(s)),
    );
  };

  const handleCopySdk = async () => {
    try {
      await navigator.clipboard.writeText(sdkRef.snippet);
      setCopiedSdk(true);
      toast.success("SDK snippet copied");
      setTimeout(() => setCopiedSdk(false), 2000);
    } catch {
      toast.error("Failed to copy snippet");
    }
  };

  return (
    <div
      data-testid="coderun-permissions-editor"
      className="bg-slate-900/70 border border-slate-700 rounded-lg p-4 space-y-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-amber-400 flex items-center gap-1.5">
            {grantedScopes.length > 0 ? (
              <ShieldCheck className="w-4 h-4" aria-hidden="true" />
            ) : (
              <Shield className="w-4 h-4" aria-hidden="true" />
            )}
            Scoped Permissions &amp; Network Access
          </h3>
          <p className="text-xs text-slate-400 mt-1">
            Least-privilege: zero Elder API access unless granted. Only grant
            the specific scopes this CodeRun requires.
          </p>
        </div>
        <span
          data-testid="permissions-editor-count"
          className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border ${
            grantedScopes.length > 0
              ? "bg-amber-900/50 text-amber-200 border-amber-700/60"
              : "bg-slate-800 text-slate-300 border-slate-700"
          }`}
        >
          {grantedScopes.length === 0
            ? "Isolated"
            : `${grantedScopes.length} ${grantedScopes.length === 1 ? "scope" : "scopes"}`}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {groups.map((group) => {
          const grantableInGroup = group.scopes.filter((s) => s.grantable);
          const selectedInGroup = group.scopes.filter((s) =>
            selectedSet.has(s.scope),
          );
          const allGrantableSelected =
            grantableInGroup.length > 0 &&
            grantableInGroup.every((s) => selectedSet.has(s.scope));

          return (
            <fieldset
              key={group.id}
              data-testid={`permission-group-${group.id}`}
              className="bg-slate-950/60 border border-slate-800 rounded-lg p-3 space-y-2.5"
            >
              <div className="flex items-center justify-between gap-2 border-b border-slate-800 pb-2">
                <legend className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
                  <span>{group.label}</span>
                  <span className="text-[11px] font-mono text-slate-400">
                    ({selectedInGroup.length}/{group.scopes.length})
                  </span>
                </legend>
                <div className="flex items-center gap-2 text-[11px]">
                  <button
                    type="button"
                    onClick={() => handleSelectAllGroup(group)}
                    disabled={
                      grantableInGroup.length === 0 || allGrantableSelected
                    }
                    aria-label={`Select all ${group.label} scopes`}
                    className="text-amber-400 hover:text-amber-300 disabled:text-slate-600 disabled:cursor-not-allowed font-medium transition-colors"
                  >
                    Select All
                  </button>
                  <span className="text-slate-700" aria-hidden="true">
                    ·
                  </span>
                  <button
                    type="button"
                    onClick={() => handleClearGroup(group)}
                    disabled={selectedInGroup.length === 0}
                    aria-label={`Clear ${group.label} scopes`}
                    className="text-slate-400 hover:text-slate-200 disabled:text-slate-600 disabled:cursor-not-allowed font-medium transition-colors"
                  >
                    Clear
                  </button>
                </div>
              </div>

              <div className="space-y-2">
                {group.scopes.map((item) => {
                  const checkboxId = `${idPrefix}-scope-${item.scope.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
                  const isChecked = selectedSet.has(item.scope);
                  return (
                    <label
                      key={item.scope}
                      htmlFor={checkboxId}
                      title={
                        item.grantable
                          ? item.description
                          : "You do not hold this scope and cannot grant it"
                      }
                      className={`flex items-start gap-2.5 rounded p-1.5 transition-colors ${
                        item.grantable
                          ? "cursor-pointer hover:bg-slate-800/60"
                          : "opacity-50 cursor-not-allowed"
                      }`}
                    >
                      <input
                        id={checkboxId}
                        type="checkbox"
                        checked={isChecked}
                        disabled={!item.grantable}
                        onChange={() =>
                          toggleScope(item.scope, item.grantable)
                        }
                        className="mt-0.5 h-3.5 w-3.5 rounded border-slate-600 bg-slate-900 text-amber-500 focus:ring-amber-400 disabled:cursor-not-allowed"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-xs font-medium text-slate-200">
                            {item.label}
                          </span>
                          <code className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800 text-amber-300">
                            {item.scope}
                          </code>
                          {!item.grantable && (
                            <span className="inline-flex items-center gap-0.5 text-[10px] text-slate-400">
                              <Lock className="w-2.5 h-2.5" aria-hidden="true" />
                              Not held
                            </span>
                          )}
                        </div>
                        {item.description && (
                          <p className="text-[11px] text-slate-400 leading-snug mt-0.5">
                            {item.description}
                          </p>
                        )}
                      </div>
                    </label>
                  );
                })}
              </div>
            </fieldset>
          );
        })}
      </div>

      <div>
        <label
          htmlFor={`${idPrefix}-egress-domains`}
          className="text-xs font-medium text-slate-300 mb-1 flex items-center gap-1.5"
        >
          <Globe className="w-3.5 h-3.5 text-slate-400" aria-hidden="true" />
          Allowed External Egress Domains{" "}
          <span className="text-slate-500 font-normal">(optional)</span>
        </label>
        <input
          id={`${idPrefix}-egress-domains`}
          type="text"
          value={allowedEgressDomains}
          onChange={(e) => onAllowedEgressDomainsChange(e.target.value)}
          placeholder="e.g. api.github.com, hooks.slack.com"
          className="w-full px-3 py-2 bg-slate-950 border border-slate-700 rounded-lg text-xs font-mono text-slate-100 placeholder-slate-500 focus:outline-none focus:border-amber-400"
        />
        <p className="text-[11px] text-slate-400 mt-1">
          Comma-separated hostnames. Leave empty for zero external network
          access.
        </p>
      </div>

      <div className="border-t border-slate-800 pt-3">
        <button
          type="button"
          onClick={() => setIsSdkOpen((prev) => !prev)}
          aria-expanded={isSdkOpen}
          data-testid="toggle-helper-sdk-ref"
          className="w-full flex items-center justify-between gap-2 text-left text-xs font-semibold text-slate-200 hover:text-amber-300 transition-colors"
        >
          <span className="inline-flex items-center gap-1.5">
            {isSdkOpen ? (
              <ChevronDown className="w-4 h-4 text-slate-400" aria-hidden="true" />
            ) : (
              <ChevronRight className="w-4 h-4 text-slate-400" aria-hidden="true" />
            )}
            <Code2 className="w-4 h-4 text-amber-400" aria-hidden="true" />
            Built-In Helper SDK Quick Reference
          </span>
          <span
            className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium ${runtimeMeta.badgeClass}`}
          >
            {runtimeMeta.label}
          </span>
        </button>

        {isSdkOpen && (
          <div
            data-testid="helper-sdk-panel"
            className="mt-3 bg-slate-950 border border-slate-800 rounded-lg p-3 space-y-2"
          >
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-xs font-semibold text-amber-300">
                  {sdkRef.title}
                </div>
                <p className="text-[11px] text-slate-400 mt-0.5">
                  {sdkRef.subtitle}
                </p>
              </div>
              <button
                type="button"
                onClick={handleCopySdk}
                aria-label="Copy SDK snippet"
                className="inline-flex items-center gap-1 px-2 py-1 rounded bg-slate-900 hover:bg-slate-800 border border-slate-700 text-[11px] text-slate-300 hover:text-amber-300 transition-colors shrink-0"
              >
                {copiedSdk ? (
                  <Check
                    className="w-3 h-3 text-emerald-400"
                    aria-hidden="true"
                  />
                ) : (
                  <Copy className="w-3 h-3" aria-hidden="true" />
                )}
                {copiedSdk ? "Copied" : "Copy"}
              </button>
            </div>
            <pre
              data-testid="helper-sdk-snippet"
              className="p-3 rounded bg-slate-900 border border-slate-800 text-[11px] font-mono text-emerald-200 overflow-x-auto whitespace-pre"
            >
              {sdkRef.snippet}
            </pre>
            <p className="text-[11px] text-slate-400">
              Runtime gateway credentials (
              <code className="text-amber-300 font-mono">ELDER_GATEWAY_URL</code>{" "}
              and{" "}
              <code className="text-amber-300 font-mono">
                ELDER_GATEWAY_TOKEN
              </code>
              ) are injected only when one or more scopes are granted.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
