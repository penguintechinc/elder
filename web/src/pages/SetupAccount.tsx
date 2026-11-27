import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import {
  Shield,
  Lock,
  XCircle,
  Loader2,
  Building2,
  Mail,
} from "lucide-react";
import toast from "react-hot-toast";
import api, { type InviteInfoResponse } from "@/lib/api";
import Button from "@/components/Button";
import Card, { CardHeader, CardContent } from "@/components/Card";

interface ApiError {
  response?: {
    data?: {
      error?: string;
      message?: string;
    };
  };
  message?: string;
}

export default function SetupAccount() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get("token")?.trim() || "";

  const [loading, setLoading] = useState(Boolean(token));
  const [inviteInfo, setInviteInfo] = useState<InviteInfoResponse | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(
    token ? null : "Invitation link is missing a token.",
  );

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [setupMfa, setSetupMfa] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setLoading(false);
      setInviteInfo(null);
      setInviteError("Invitation link is missing a token.");
      return;
    }

    let cancelled = false;
    setLoading(true);
    setInviteError(null);

    api
      .getInviteInfo(token)
      .then((res) => {
        if (cancelled) return;
        if (!res.valid) {
          setInviteInfo(null);
          setInviteError(
            res.error || "This invitation link is invalid or has expired.",
          );
        } else {
          setInviteInfo(res);
          setInviteError(null);
        }
      })
      .catch((err: ApiError) => {
        if (cancelled) return;
        setInviteInfo(null);
        setInviteError(
          err.response?.data?.error ||
            err.response?.data?.message ||
            "This invitation link is invalid or has expired.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [token]);

  const completeMutation = useMutation({
    mutationFn: (payload: {
      token: string;
      password: string;
      setup_mfa?: boolean;
    }) => api.completeInvite(payload),
    onSuccess: (res) => {
      if (res.mfaSetupRequired || res.mfaSetupOptional) {
        toast.success(
          "Account created! Complete Google Authenticator setup next.",
        );
        navigate("/profile?mfa_setup=1", { replace: true });
      } else {
        toast.success("Account setup complete! Welcome to Elder.");
        navigate("/", { replace: true });
      }
    },
    onError: (err: ApiError) => {
      const msg =
        err.response?.data?.error ||
        err.response?.data?.message ||
        "Failed to complete account setup";
      setFormError(msg);
      toast.error(msg);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (password.length < 8) {
      const msg = "Password must be at least 8 characters long.";
      setFormError(msg);
      toast.error(msg);
      return;
    }

    if (password !== confirmPassword) {
      const msg = "Passwords do not match.";
      setFormError(msg);
      toast.error(msg);
      return;
    }

    const wantMfa = inviteInfo?.mfa_enforced ? true : setupMfa;
    completeMutation.mutate({
      token,
      password,
      setup_mfa: wantMfa,
    });
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-950 via-blue-950/20 to-gray-950 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <img
            src="/elder-logo.png"
            alt="Elder Logo"
            className="w-24 h-24 mx-auto mb-4 drop-shadow-2xl"
          />
          <h1 className="text-3xl font-bold bg-gradient-to-r from-yellow-500 to-amber-400 bg-clip-text text-transparent mb-1">
            Elder
          </h1>
          <p className="text-gray-500">Complete Your Account Setup</p>
        </div>

        <Card className="bg-gray-900/50 border-gray-800/50 backdrop-blur-sm">
          <CardHeader>
            <h2 className="text-xl font-semibold text-gray-200 flex items-center gap-2">
              <Lock className="w-5 h-5 text-yellow-500" />
              {loading
                ? "Validating invitation..."
                : inviteInfo
                  ? "Set Up Your Account"
                  : "Invalid Invitation"}
            </h2>
          </CardHeader>
          <CardContent>
            {loading && (
              <div
                className="flex flex-col items-center justify-center py-8 gap-3 text-slate-300"
                data-testid="setup-account-loading"
              >
                <Loader2 className="w-8 h-8 animate-spin text-yellow-500" />
                <p className="text-sm text-slate-400">
                  Verifying your invitation link...
                </p>
              </div>
            )}

            {!loading && inviteError && (
              <div className="space-y-5" data-testid="setup-account-invalid">
                <div className="flex items-start gap-3 p-4 rounded-lg bg-red-500/10 border border-red-500/30">
                  <XCircle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
                  <p className="text-sm text-red-200">{inviteError}</p>
                </div>
                <p className="text-xs text-slate-400">
                  Contact your organization administrator to request a new
                  invitation email.
                </p>
                <Button
                  type="button"
                  variant="ghost"
                  className="w-full text-gray-400 hover:text-gray-300 hover:bg-gray-800/50"
                  onClick={() => navigate("/login")}
                >
                  Back to Login
                </Button>
              </div>
            )}

            {!loading && inviteInfo && (
              <form
                onSubmit={handleSubmit}
                className="space-y-4"
                data-testid="setup-account-form"
              >
                {/* Invited account details */}
                <div className="p-3 rounded-lg bg-slate-800/70 border border-slate-700 space-y-2">
                  <div className="flex items-center gap-2 text-sm">
                    <Mail className="w-4 h-4 text-slate-400 shrink-0" />
                    <span className="text-slate-400">Email:</span>
                    <span
                      className="text-white font-medium truncate"
                      data-testid="invite-email"
                    >
                      {inviteInfo.email}
                    </span>
                  </div>
                  {inviteInfo.tenant_name && (
                    <div className="flex items-center gap-2 text-sm">
                      <Building2 className="w-4 h-4 text-slate-400 shrink-0" />
                      <span className="text-slate-400">Organization:</span>
                      <span
                        className="text-white font-medium truncate"
                        data-testid="invite-tenant-name"
                      >
                        {inviteInfo.tenant_name}
                      </span>
                    </div>
                  )}
                </div>

                {formError && (
                  <p
                    role="alert"
                    className="text-sm text-red-200 bg-red-500/15 border border-red-500/40 rounded-lg px-3 py-2"
                  >
                    {formError}
                  </p>
                )}

                <div className="space-y-1">
                  <label
                    htmlFor="setup-password"
                    className="block text-sm font-medium text-yellow-500"
                  >
                    Password
                  </label>
                  <input
                    id="setup-password"
                    type="password"
                    required
                    minLength={8}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="At least 8 characters"
                    autoComplete="new-password"
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
                  />
                </div>

                <div className="space-y-1">
                  <label
                    htmlFor="setup-confirm-password"
                    className="block text-sm font-medium text-yellow-500"
                  >
                    Confirm Password
                  </label>
                  <input
                    id="setup-confirm-password"
                    type="password"
                    required
                    minLength={8}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Re-enter your password"
                    autoComplete="new-password"
                    className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
                  />
                </div>

                {/* MFA Section */}
                {inviteInfo.mfa_enforced ? (
                  <div
                    data-testid="mfa-enforced-banner"
                    className="flex items-start gap-3 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30"
                  >
                    <Shield className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                    <div className="text-xs text-amber-200 space-y-1">
                      <p className="font-medium text-amber-100">
                        Multi-Factor Authentication Required
                      </p>
                      <p>
                        Your organization requires MFA. You will be guided
                        through Google Authenticator setup immediately after
                        setting your password.
                      </p>
                    </div>
                  </div>
                ) : (
                  <div
                    data-testid="mfa-optional-section"
                    className="flex items-start gap-2.5 p-3 rounded-lg bg-slate-800/50 border border-slate-700"
                  >
                    <input
                      id="setup-mfa-checkbox"
                      type="checkbox"
                      checked={setupMfa}
                      onChange={(e) => setSetupMfa(e.target.checked)}
                      className="mt-0.5 rounded border-slate-600 bg-slate-700 text-yellow-500 focus:ring-yellow-500"
                    />
                    <label
                      htmlFor="setup-mfa-checkbox"
                      className="text-sm text-slate-200 cursor-pointer"
                    >
                      Set up Google Authenticator (MFA) now (optional,
                      recommended)
                    </label>
                  </div>
                )}

                <div className="flex flex-col gap-3 pt-2">
                  <Button
                    type="submit"
                    className="w-full bg-gradient-to-r from-yellow-600 to-amber-600 hover:from-yellow-500 hover:to-amber-500 text-gray-900 font-semibold"
                    isLoading={completeMutation.isPending}
                  >
                    Complete Account Setup
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    className="w-full text-gray-400 hover:text-gray-300 hover:bg-gray-800/50"
                    onClick={() => navigate("/login")}
                  >
                    Back to Login
                  </Button>
                </div>
              </form>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
