import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LoginPageWrapper from "./LoginPageWrapper";

const mockNavigate = vi.fn();
const mockMarkAuthenticated = vi.fn();
let capturedProps: {
  onSuccess: (response: Record<string, unknown>) => void;
  api: { loginUrl: string };
  mfa?: { enabled: boolean };
};

vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useSearchParams: () => [new URLSearchParams("next=/entities")],
}));

vi.mock("@/lib/api", () => ({
  markAuthenticated: () => mockMarkAuthenticated(),
}));

vi.mock("@penguintechinc/react-libs/components", () => ({
  ELDER_LOGIN_THEME: {},
  LoginPageBuilder: (props: typeof capturedProps) => {
    capturedProps = props;
    return null;
  },
}));

describe("LoginPageWrapper MFA handling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    render(<LoginPageWrapper />);
  });

  it("posts the MFA step back to the login endpoint (password-bound)", () => {
    // LoginPageBuilder re-submits email + password + mfaCode to loginUrl, so
    // the second factor never travels without the first.
    expect(capturedProps.api.loginUrl).toBe("/api/v1/portal-auth/login");
    expect(capturedProps.mfa?.enabled).toBe(true);
  });

  it("marks the session and navigates on a full login", () => {
    capturedProps.onSuccess({ success: true, token: "t" });
    expect(mockMarkAuthenticated).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith("/entities", { replace: true });
  });

  it("sends an expired-grace user to enrolment without a session", () => {
    capturedProps.onSuccess({ success: true, mfaEnrollmentRequired: true });
    expect(mockMarkAuthenticated).not.toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith("/mfa-enroll", { replace: true });
  });
});
