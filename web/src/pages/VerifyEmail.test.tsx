import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import VerifyEmail from "./VerifyEmail";
import api from "@/lib/api";

vi.mock("react-hot-toast", () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  default: {
    verifyEmail: vi.fn(),
    resendVerificationEmail: vi.fn(),
  },
}));

function renderVerifyEmail(initialEntry: string) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/verify-email" element={<VerifyEmail />} />
          <Route path="/login" element={<div data-testid="login-page">Login Page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("VerifyEmail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("verifies token on mount and navigates to /login on Sign In click", async () => {
    vi.mocked(api.verifyEmail).mockResolvedValue({
      success: true,
      message: "Email verified successfully.",
    });

    renderVerifyEmail("/verify-email?token=valid-token-123");

    await waitFor(() => {
      expect(api.verifyEmail).toHaveBeenCalledWith("valid-token-123");
    });

    const successBox = await screen.findByTestId("verify-email-success");
    expect(successBox.textContent).toContain("Email verified successfully.");

    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));
    expect(await screen.findByTestId("login-page")).toBeDefined();
  });

  it("shows error state with inline resend form when token is invalid or expired", async () => {
    vi.mocked(api.verifyEmail).mockRejectedValue({
      response: { data: { error: "Verification token has expired." } },
    });
    vi.mocked(api.resendVerificationEmail).mockResolvedValue({
      success: true,
      message: "Verification email resent.",
    });

    renderVerifyEmail("/verify-email?token=expired-token");

    const errorBox = await screen.findByTestId("verify-email-error");
    expect(errorBox.textContent).toContain("Verification token has expired.");

    const emailInput = screen.getByLabelText("Email");
    fireEvent.change(emailInput, { target: { value: "alice@penguintech.io" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Resend Verification Email" }),
    );

    await waitFor(() => {
      expect(api.resendVerificationEmail).toHaveBeenCalledWith({
        email: "alice@penguintech.io",
      });
    });

    expect(await screen.findByTestId("resend-success-message")).toBeDefined();
  });

  it("shows error state immediately when token query param is missing", async () => {
    renderVerifyEmail("/verify-email");

    const errorBox = await screen.findByTestId("verify-email-error");
    expect(errorBox.textContent).toContain(
      "Verification link is missing a token.",
    );
    expect(api.verifyEmail).not.toHaveBeenCalled();
  });
});
