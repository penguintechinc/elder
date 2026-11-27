import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Settings,
  Shield,
  Database,
  Clock,
  CheckCircle,
  XCircle,
  FileText,
  Search,
  RefreshCw,
  Lock,
  Mail,
} from "lucide-react";
import toast from "react-hot-toast";
import api, {
  type LicenseFeatures,
  type TenantSignupPolicySource,
} from "@/lib/api";
import Card, { CardHeader, CardContent } from "@/components/Card";

const ENTERPRISE_FEATURES: Array<{
  key: keyof LicenseFeatures;
  label: string;
}> = [
  { key: "multi_tenancy", label: "Multi-Tenancy" },
  { key: "portal_users", label: "Portal Users" },
  { key: "saml_sso", label: "SAML SSO" },
  { key: "scim_provisioning", label: "SCIM Provisioning" },
  { key: "audit_logging", label: "Audit Logging" },
  { key: "compliance_reports", label: "Compliance Reports" },
  { key: "access_reviews", label: "Access Reviews" },
  { key: "enterprise_integrations", label: "Enterprise Integrations" },
  { key: "google_oauth_sso", label: "Google OAuth SSO" },
];

function formatTierLabel(tier: string | undefined, isLoading: boolean): string {
  if (isLoading && !tier) return "Loading...";
  const normalized = (tier || "free").toLowerCase();
  if (normalized === "enterprise") return "Enterprise";
  if (normalized === "professional") return "Professional";
  if (normalized === "free") return "Free";
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function getTierBadgeClass(tier: string | undefined): string {
  const normalized = (tier || "free").toLowerCase();
  if (normalized === "enterprise") return "bg-emerald-500/20 text-emerald-400";
  if (normalized === "professional") return "bg-blue-500/20 text-blue-400";
  return "bg-yellow-500/20 text-yellow-400";
}

function getSignupSourceBadge(source: TenantSignupPolicySource | undefined): {
  label: string;
  className: string;
} {
  if (source === "tenant") {
    return {
      label: "Tenant override",
      className: "bg-blue-500/20 text-blue-300",
    };
  }
  if (source === "env_default") {
    return {
      label: "Default env",
      className: "bg-amber-500/20 text-amber-300",
    };
  }
  return {
    label: "Unrestricted",
    className: "bg-slate-700 text-slate-300",
  };
}

export default function AdminSettings() {
  const [logSearch, setLogSearch] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const [gracePeriodInput, setGracePeriodInput] = useState("7");
  const [signupDomainsInput, setSignupDomainsInput] = useState("");
  const [requireEmailVerification, setRequireEmailVerification] =
    useState(false);
  const queryClient = useQueryClient();

  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    refetchInterval: 30000,
  });

  const { data: profile } = useQuery({
    queryKey: ["portal-profile"],
    queryFn: () => api.getPortalProfile(),
  });

  const { data: licenseStatus, isLoading: licenseLoading } = useQuery({
    queryKey: ["license-status"],
    queryFn: () => api.getLicenseStatus(),
  });

  const isAdmin = profile?.global_role === "admin";
  const isMfaAdmin =
    isAdmin ||
    profile?.global_role === "super_admin" ||
    profile?.tenant_role === "admin";
  const normalizedTier = (licenseStatus?.tier || "free").toLowerCase();
  const isEnterprise = normalizedTier === "enterprise";

  const { data: mfaPolicy } = useQuery({
    queryKey: ["mfa-policy"],
    queryFn: () => api.getMfaPolicy(),
    enabled: isMfaAdmin,
  });

  useEffect(() => {
    if (typeof mfaPolicy?.grace_period_days === "number") {
      setGracePeriodInput(String(mfaPolicy.grace_period_days));
    }
  }, [mfaPolicy?.grace_period_days]);

  const mfaPolicyMutation = useMutation({
    mutationFn: (payload: { enforced: boolean; grace_period_days: number }) =>
      api.updateMfaPolicy({
        enforced: payload.enforced,
        provider: "google_authenticator",
        grace_period_days: payload.grace_period_days,
      }),
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: ["mfa-policy"] });
      await queryClient.invalidateQueries({ queryKey: ["portal-profile"] });
      await queryClient.invalidateQueries({ queryKey: ["profile"] });
      toast.success(
        updated.enforced
          ? `Enterprise MFA enforcement active (${updated.grace_period_days}-day grace period)`
          : "Enterprise MFA enforcement turned off",
      );
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data
          ?.error || "Failed to update MFA enforcement policy";
      toast.error(msg);
    },
  });

  const mfaEnforced = Boolean(mfaPolicy?.enforced);
  const canManageMfa = Boolean(mfaPolicy?.can_manage ?? isEnterprise);

  const parseGraceDays = (): number => {
    const parsed = Number.parseInt(gracePeriodInput, 10);
    if (Number.isNaN(parsed)) return 7;
    return Math.max(0, Math.min(90, parsed));
  };

  const handleToggleMfaEnforcement = () => {
    mfaPolicyMutation.mutate({
      enforced: !mfaEnforced,
      grace_period_days: parseGraceDays(),
    });
  };

  const handleSaveGracePeriod = (e: React.FormEvent) => {
    e.preventDefault();
    const days = parseGraceDays();
    setGracePeriodInput(String(days));
    mfaPolicyMutation.mutate({
      enforced: mfaEnforced,
      grace_period_days: days,
    });
  };

  const { data: signupPolicy } = useQuery({
    queryKey: ["tenant-signup-policy"],
    queryFn: () => api.getTenantSignupPolicy(),
    enabled: isMfaAdmin,
  });

  useEffect(() => {
    if (!signupPolicy) return;
    setSignupDomainsInput(
      (signupPolicy.allowed_signup_domains || []).join(", "),
    );
    setRequireEmailVerification(
      Boolean(signupPolicy.require_email_verification),
    );
  }, [signupPolicy]);

  const signupPolicyMutation = useMutation({
    mutationFn: (payload: {
      allowed_signup_domains: string[];
      require_email_verification: boolean;
    }) => api.updateTenantSignupPolicy(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["tenant-signup-policy"],
      });
      toast.success("Signup & email verification policy updated");
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data
          ?.error || "Failed to update signup policy";
      toast.error(msg);
    },
  });

  const canManageSignupPolicy = Boolean(signupPolicy?.can_manage ?? isMfaAdmin);
  const signupSourceBadge = getSignupSourceBadge(signupPolicy?.source);

  const handleSaveSignupPolicy = (e: React.FormEvent) => {
    e.preventDefault();
    const domains = signupDomainsInput
      .split(",")
      .map((d) => d.trim().toLowerCase())
      .filter(Boolean);
    signupPolicyMutation.mutate({
      allowed_signup_domains: domains,
      require_email_verification: requireEmailVerification,
    });
  };
  const tierLabel = formatTierLabel(licenseStatus?.tier, licenseLoading);
  const tierBadgeClass = getTierBadgeClass(licenseStatus?.tier);
  const statusLabel = licenseStatus?.status
    ? licenseStatus.status.charAt(0).toUpperCase() +
      licenseStatus.status.slice(1)
    : "Active";

  const {
    data: logs,
    isLoading: logsLoading,
    refetch: refetchLogs,
  } = useQuery({
    queryKey: ["admin-logs"],
    queryFn: () => api.getLogs(),
    enabled: isAdmin && !isSearching,
  });

  const {
    data: searchResults,
    isLoading: searchLoading,
    refetch: refetchSearch,
  } = useQuery({
    queryKey: ["admin-logs-search", logSearch],
    queryFn: () => api.searchLogs(logSearch),
    enabled: isAdmin && isSearching && logSearch.length > 0,
  });

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (logSearch.trim()) {
      setIsSearching(true);
      refetchSearch();
    }
  };

  const handleClearSearch = () => {
    setLogSearch("");
    setIsSearching(false);
    refetchLogs();
  };

  const displayedLogs = isSearching ? searchResults : logs;
  const isLoadingLogs = isSearching ? searchLoading : logsLoading;

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-2">Admin Settings</h1>
        <p className="text-slate-400">System configuration and health status</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* System Health */}
        <Card>
          <CardHeader>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Shield className="w-5 h-5" />
              System Health
            </h2>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-slate-300">API Status</span>
                {health?.status === "healthy" ? (
                  <span className="flex items-center gap-2 text-green-400">
                    <CheckCircle className="w-4 h-4" />
                    Healthy
                  </span>
                ) : (
                  <span className="flex items-center gap-2 text-red-400">
                    <XCircle className="w-4 h-4" />
                    Unhealthy
                  </span>
                )}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-300">Service</span>
                <span className="text-slate-400">
                  {health?.service || "Elder"}
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* License Info */}
        <Card>
          <CardHeader>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Database className="w-5 h-5" />
              License Information
            </h2>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-2">
                <span className="text-slate-300">Tier</span>
                <div className="flex items-center gap-2 flex-wrap justify-end">
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-medium ${tierBadgeClass}`}
                    data-testid="license-tier-badge"
                  >
                    {tierLabel}
                  </span>
                  {licenseStatus?.domain_bypass && (
                    <span
                      className="px-2 py-0.5 rounded text-xs font-medium bg-indigo-500/20 text-indigo-300 border border-indigo-500/30"
                      data-testid="license-domain-bypass-badge"
                      title={licenseStatus.bypass_domain ?? undefined}
                    >
                      Domain Bypass
                      {licenseStatus.bypass_domain
                        ? ` (${licenseStatus.bypass_domain})`
                        : ""}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-300">Status</span>
                <span className="text-green-400">{statusLabel}</span>
              </div>
              <p className="text-sm text-slate-500 pt-2 border-t border-slate-700">
                {licenseStatus?.domain_bypass
                  ? `Enterprise license bypass active for ${licenseStatus.bypass_domain ?? "this domain"}.`
                  : isEnterprise
                    ? "Enterprise license active."
                    : "Upgrade to Professional or Enterprise for advanced features like SSO, SCIM, and priority support."}
              </p>
            </div>
          </CardContent>
        </Card>

        {/* User Info */}
        <Card>
          <CardHeader>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Settings className="w-5 h-5" />
              Current User
            </h2>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-slate-300">Email</span>
                <span className="text-slate-400">{profile?.email || "-"}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-300">Global Role</span>
                <span className="text-slate-400">
                  {profile?.global_role || "None"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-300">Tenant Role</span>
                <span className="text-slate-400">
                  {profile?.tenant_role || "-"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-300">MFA</span>
                <span className="text-slate-400">
                  {profile?.mfa_enabled ? "Enabled" : "Disabled"}
                </span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Feature Flags */}
        <Card>
          <CardHeader>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Clock className="w-5 h-5" />
              Enterprise Features
            </h2>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {ENTERPRISE_FEATURES.map(({ key, label }) => {
                const enabled = Boolean(licenseStatus?.features?.[key]);
                return (
                  <div
                    key={key}
                    className="flex items-center justify-between"
                    data-testid={`license-feature-${key}`}
                    data-enabled={enabled ? "true" : "false"}
                  >
                    <span className="text-slate-300">{label}</span>
                    {enabled ? (
                      <CheckCircle
                        className="w-4 h-4 text-green-400"
                        aria-label={`${label} enabled`}
                      />
                    ) : (
                      <XCircle
                        className="w-4 h-4 text-slate-500"
                        aria-label={`${label} disabled`}
                      />
                    )}
                  </div>
                );
              })}
            </div>
            <p className="text-sm text-slate-500 pt-3 mt-3 border-t border-slate-700">
              {licenseStatus?.domain_bypass
                ? `Enterprise license bypass active for ${licenseStatus.bypass_domain ?? "this domain"}.`
                : isEnterprise
                  ? "Enterprise license active."
                  : "Add a license key to enable enterprise features."}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Enterprise MFA Enforcement - Admin Only */}
      {isMfaAdmin && (
        <div className="mt-6">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <Lock className="w-5 h-5" />
                  Enterprise MFA Enforcement (Google Authenticator / Passkey)
                </h2>
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/20 text-emerald-400">
                  Enterprise
                </span>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="flex items-center justify-between gap-4 flex-wrap">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-slate-200 font-medium">
                        Require Google Authenticator (TOTP) or Passkey
                      </span>
                      <span
                        data-testid="mfa-policy-status"
                        className={`px-2 py-0.5 rounded text-xs font-medium ${
                          mfaEnforced
                            ? "bg-emerald-500/20 text-emerald-300"
                            : "bg-slate-700 text-slate-300"
                        }`}
                      >
                        {mfaEnforced ? "Enforced" : "Optional"}
                      </span>
                    </div>
                    <p className="text-sm text-slate-400 mt-1">
                      Require all organization users to enroll Google
                      Authenticator (TOTP) or a Passkey. Users without MFA
                      enrolled are locked out after the grace period expires.
                    </p>
                  </div>

                  <button
                    type="button"
                    role="switch"
                    aria-checked={mfaEnforced}
                    aria-label="Enforce MFA with Google Authenticator or Passkey"
                    data-testid="mfa-policy-toggle"
                    disabled={!canManageMfa || mfaPolicyMutation.isPending}
                    onClick={handleToggleMfaEnforcement}
                    className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2 focus:ring-offset-slate-900 ${
                      mfaEnforced ? "bg-emerald-500" : "bg-slate-600"
                    } ${
                      !canManageMfa || mfaPolicyMutation.isPending
                        ? "opacity-50 cursor-not-allowed"
                        : "cursor-pointer"
                    }`}
                  >
                    <span
                      className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                        mfaEnforced ? "translate-x-6" : "translate-x-1"
                      }`}
                    />
                  </button>
                </div>

                <form
                  onSubmit={handleSaveGracePeriod}
                  className="pt-4 border-t border-slate-700 flex flex-col sm:flex-row sm:items-end gap-4"
                >
                  <div className="flex-1 max-w-xs">
                    <label
                      htmlFor="mfa-grace-period-days"
                      className="block text-sm font-medium text-slate-300 mb-1"
                    >
                      Grace Period (days)
                    </label>
                    <input
                      id="mfa-grace-period-days"
                      data-testid="mfa-grace-period-input"
                      type="number"
                      min={0}
                      max={90}
                      value={gracePeriodInput}
                      disabled={!canManageMfa || mfaPolicyMutation.isPending}
                      onChange={(e) => setGracePeriodInput(e.target.value)}
                      className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-emerald-500 disabled:opacity-50"
                    />
                  </div>
                  <button
                    type="submit"
                    data-testid="mfa-grace-period-save"
                    disabled={!canManageMfa || mfaPolicyMutation.isPending}
                    className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Save Grace Period
                  </button>
                </form>
                <p className="text-xs text-slate-400">
                  Valid range: 0–90 days (0 = immediate). Counts from when
                  enforcement is turned on or when the user registered,
                  whichever is further out.
                </p>

                {!isEnterprise && (
                  <p
                    data-testid="mfa-enterprise-upgrade-notice"
                    className="text-sm text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2"
                  >
                    Upgrade to Enterprise to enforce Google Authenticator (TOTP)
                    or Passkey MFA across all organization users.
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Signup & Email Verification Policy - Admin Only */}
      {isMfaAdmin && (
        <div className="mt-6">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <Mail className="w-5 h-5" />
                  Signup &amp; Email Verification Policy
                </h2>
                <span
                  data-testid="signup-policy-source-badge"
                  className={`px-2 py-0.5 rounded text-xs font-medium ${signupSourceBadge.className}`}
                >
                  {signupSourceBadge.label}
                </span>
              </div>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSaveSignupPolicy} className="space-y-4">
                <div>
                  <label
                    htmlFor="signup-allowed-domains"
                    className="block text-sm font-medium text-slate-300 mb-1"
                  >
                    Allowed Signup Email Domains
                  </label>
                  <input
                    id="signup-allowed-domains"
                    data-testid="signup-allowed-domains-input"
                    type="text"
                    value={signupDomainsInput}
                    disabled={
                      !canManageSignupPolicy || signupPolicyMutation.isPending
                    }
                    onChange={(e) => setSignupDomainsInput(e.target.value)}
                    placeholder="e.g. penguintech.io, example.com (leave blank for unrestricted)"
                    className="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-emerald-500 disabled:opacity-50"
                  />
                  <p className="text-xs text-slate-400 mt-1">
                    Comma-separated list of email domains allowed to
                    self-register. Leave blank to allow any email domain.
                  </p>
                </div>

                <div className="flex items-center justify-between gap-4 pt-3 border-t border-slate-700 flex-wrap">
                  <div>
                    <span className="text-slate-200 font-medium block">
                      Require Email Verification on Self-Signup
                    </span>
                    <p className="text-sm text-slate-400 mt-0.5">
                      New self-registered users must click a verification link
                      in their email before signing in.
                    </p>
                  </div>

                  <button
                    type="button"
                    role="switch"
                    aria-checked={requireEmailVerification}
                    aria-label="Require Email Verification on Self-Signup"
                    data-testid="signup-require-verification-toggle"
                    disabled={
                      !canManageSignupPolicy || signupPolicyMutation.isPending
                    }
                    onClick={() =>
                      setRequireEmailVerification((prev) => !prev)
                    }
                    className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2 focus:ring-offset-slate-900 ${
                      requireEmailVerification
                        ? "bg-emerald-500"
                        : "bg-slate-600"
                    } ${
                      !canManageSignupPolicy || signupPolicyMutation.isPending
                        ? "opacity-50 cursor-not-allowed"
                        : "cursor-pointer"
                    }`}
                  >
                    <span
                      className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                        requireEmailVerification
                          ? "translate-x-6"
                          : "translate-x-1"
                      }`}
                    />
                  </button>
                </div>

                <div className="pt-2">
                  <button
                    type="submit"
                    data-testid="signup-policy-save"
                    disabled={
                      !canManageSignupPolicy || signupPolicyMutation.isPending
                    }
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {signupPolicyMutation.isPending
                      ? "Saving..."
                      : "Save Signup Policy"}
                  </button>
                </div>
              </form>
            </CardContent>
          </Card>
        </div>
      )}

      {/* System Logs - Admin Only */}
      {isAdmin && (
        <div className="mt-6">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between w-full">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <FileText className="w-5 h-5" />
                  System Logs
                </h2>
                <button
                  onClick={() =>
                    isSearching ? refetchSearch() : refetchLogs()
                  }
                  className="p-2 text-slate-400 hover:text-white hover:bg-slate-700 rounded"
                  title="Refresh logs"
                >
                  <RefreshCw
                    className={`w-4 h-4 ${isLoadingLogs ? "animate-spin" : ""}`}
                  />
                </button>
              </div>
            </CardHeader>
            <CardContent>
              {/* Search Bar */}
              <form onSubmit={handleSearch} className="flex gap-2 mb-4">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                  <input
                    type="text"
                    value={logSearch}
                    onChange={(e) => setLogSearch(e.target.value)}
                    placeholder="Search logs..."
                    className="w-full pl-10 pr-4 py-2 bg-slate-800 border border-slate-600 rounded text-white placeholder:text-slate-500 focus:outline-none focus:border-blue-500"
                  />
                </div>
                <button
                  type="submit"
                  className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                  disabled={!logSearch.trim()}
                >
                  Search
                </button>
                {isSearching && (
                  <button
                    type="button"
                    onClick={handleClearSearch}
                    className="px-4 py-2 bg-slate-600 text-white rounded hover:bg-slate-500"
                  >
                    Clear
                  </button>
                )}
              </form>

              {/* Log Info */}
              <div className="flex items-center justify-between text-sm text-slate-400 mb-2">
                <span>
                  {isSearching
                    ? `${searchResults?.total_matches || 0} matches for "${searchResults?.query}"`
                    : `${logs?.total || 0} total lines`}
                </span>
                <span>
                  Showing last {displayedLogs?.lines?.length || 0} lines
                </span>
              </div>

              {/* Log Content */}
              <div className="bg-slate-900 rounded border border-slate-700 p-4 max-h-96 overflow-auto font-mono text-xs">
                {isLoadingLogs ? (
                  <div className="text-slate-400 text-center py-4">
                    Loading logs...
                  </div>
                ) : displayedLogs?.lines?.length ? (
                  <pre className="text-slate-300 whitespace-pre-wrap break-all">
                    {displayedLogs.lines.join("\n")}
                  </pre>
                ) : (
                  <div className="text-slate-500 text-center py-4">
                    {isSearching ? "No matches found" : "No logs available"}
                  </div>
                )}
              </div>

              {displayedLogs?.log_file && (
                <div className="mt-2 text-xs text-slate-500">
                  Log file: {displayedLogs.log_file}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
