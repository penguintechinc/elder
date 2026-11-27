import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle, XCircle, Mail, Loader2 } from "lucide-react";
import toast from "react-hot-toast";
import api from "@/lib/api";
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

type VerifyState = "loading" | "success" | "error";

export default function VerifyEmail() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get("token")?.trim() || "";

  const [status, setStatus] = useState<VerifyState>(
    token ? "loading" : "error",
  );
  const [statusMessage, setStatusMessage] = useState<string>(
    token ? "" : "Verification link is missing a token.",
  );
  const [resendEmail, setResendEmail] = useState("");
  const [resendSent, setResendSent] = useState(false);

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setStatusMessage("Verification link is missing a token.");
      return;
    }

    let cancelled = false;
    setStatus("loading");

    api
      .verifyEmail(token)
      .then((res) => {
        if (cancelled) return;
        setStatus("success");
        setStatusMessage(
          res.message || "Your email address has been verified.",
        );
      })
      .catch((err: ApiError) => {
        if (cancelled) return;
        setStatus("error");
        setStatusMessage(
          err.response?.data?.error ||
            err.response?.data?.message ||
            "This verification link is invalid or has expired.",
        );
      });

    return () => {
      cancelled = true;
    };
  }, [token]);

  const resendMutation = useMutation({
    mutationFn: (email: string) => api.resendVerificationEmail({ email }),
    onSuccess: (res) => {
      setResendSent(true);
      toast.success(
        res.message || "Verification email sent. Please check your inbox.",
      );
    },
    onError: (err: ApiError) => {
      toast.error(
        err.response?.data?.error ||
          err.response?.data?.message ||
          "Failed to resend verification email",
      );
    },
  });

  const handleResendSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = resendEmail.replace(/\s+/g, "");
    if (!cleaned) {
      toast.error("Please enter your email address");
      return;
    }
    resendMutation.mutate(cleaned);
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
          <p className="text-gray-500">Email Verification</p>
        </div>

        <Card className="bg-gray-900/50 border-gray-800/50 backdrop-blur-sm">
          <CardHeader>
            <h2 className="text-xl font-semibold text-gray-200 flex items-center gap-2">
              <Mail className="w-5 h-5 text-yellow-500" />
              {status === "loading" && "Verifying your email..."}
              {status === "success" && "Email Verified"}
              {status === "error" && "Verification Failed"}
            </h2>
          </CardHeader>
          <CardContent>
            {status === "loading" && (
              <div
                className="flex flex-col items-center justify-center py-8 gap-3 text-slate-300"
                data-testid="verify-email-loading"
              >
                <Loader2 className="w-8 h-8 animate-spin text-yellow-500" />
                <p className="text-sm text-slate-400">
                  Please wait while we verify your email address...
                </p>
              </div>
            )}

            {status === "success" && (
              <div className="space-y-5" data-testid="verify-email-success">
                <div className="flex items-start gap-3 p-4 rounded-lg bg-emerald-500/10 border border-emerald-500/30">
                  <CheckCircle className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
                  <p className="text-sm text-emerald-200">{statusMessage}</p>
                </div>
                <Button
                  type="button"
                  className="w-full bg-gradient-to-r from-yellow-600 to-amber-600 hover:from-yellow-500 hover:to-amber-500 text-gray-900 font-semibold"
                  onClick={() => navigate("/login")}
                >
                  Sign In
                </Button>
              </div>
            )}

            {status === "error" && (
              <div className="space-y-5" data-testid="verify-email-error">
                <div className="flex items-start gap-3 p-4 rounded-lg bg-red-500/10 border border-red-500/30">
                  <XCircle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
                  <p className="text-sm text-red-200">{statusMessage}</p>
                </div>

                <div className="pt-2 border-t border-slate-700">
                  <h3 className="text-sm font-medium text-slate-200 mb-2">
                    Resend Verification Email
                  </h3>
                  <p className="text-xs text-slate-400 mb-3">
                    Enter your email address to receive a new verification link.
                  </p>
                  <form onSubmit={handleResendSubmit} className="space-y-3">
                    <div>
                      <label
                        htmlFor="resend-verification-email"
                        className="block text-sm font-medium text-yellow-500 mb-1"
                      >
                        Email
                      </label>
                      <input
                        id="resend-verification-email"
                        type="email"
                        required
                        value={resendEmail}
                        onChange={(e) => setResendEmail(e.target.value)}
                        placeholder="your@email.com"
                        autoComplete="email"
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
                      />
                    </div>
                    {resendSent && (
                      <p
                        className="text-xs text-emerald-300"
                        data-testid="resend-success-message"
                      >
                        If an unverified account exists for that email, a new
                        verification link has been sent.
                      </p>
                    )}
                    <div className="flex flex-col gap-2">
                      <Button
                        type="submit"
                        className="w-full bg-gradient-to-r from-yellow-600 to-amber-600 hover:from-yellow-500 hover:to-amber-500 text-gray-900 font-semibold"
                        isLoading={resendMutation.isPending}
                      >
                        Resend Verification Email
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
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
