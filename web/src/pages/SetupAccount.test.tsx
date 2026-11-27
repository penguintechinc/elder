import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import SetupAccount from "./SetupAccount";
import api from "@/lib/api";

vi.mock("react-hot-toast", () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  default: {
    getInviteInfo: vi.fn(),
    completeInvite: vi.fn(),
  },
}));

function LocationEcho({ testId }: { testId: string }) {
  const location = useLocation();
  return (
    <div data-testid={testId}>
      {location.pathname}
      {location.search}
    </div>
  );
}

function renderSetupAccount(initialEntry: string) {
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
          <Route path="/setup-account" element={<SetupAccount />} />
          <Route path="/profile" element={<LocationEcho testId="profile-page" />} />
          <Route path="/" element={<LocationEcho testId="home-page" />} />
          <Route path="/login" element={<LocationEcho testId="login-page" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SetupAccount", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows enforced MFA banner and redirects to /profile?mfa_setup=1 when mfa_enforced is true", async () => {
    vi.mocked(api.getInviteInfo).mockResolvedValue({
      valid: true,
      email: "newuser@penguintech.io",
      full_name: "New User",
      tenant_name: "PenguinTech",
      mfa_enforced: true,
      mfa_optional: false,
    });
    vi.mocked(api.completeInvite).mockResolvedValue({
      success: true,
      mfaSetupRequired: true,
    });

    renderSetupAccount("/setup-account?token=invite-tok-1");

    await waitFor(() => {
      expect(api.getInviteInfo).toHaveBeenCalledWith("invite-tok-1");
    });

    expect((await screen.findByTestId("invite-email")).textContent).toBe(
      "newuser@penguintech.io",
    );
    expect(screen.getByTestId("invite-tenant-name").textContent).toBe(
      "PenguinTech",
    );
    expect(screen.getByTestId("mfa-enforced-banner")).toBeDefined();
    expect(screen.queryByTestId("mfa-optional-section")).toBeNull();

    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "StrongPass123!" },
    });
    fireEvent.change(screen.getByLabelText("Confirm Password"), {
      target: { value: "StrongPass123!" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Complete Account Setup" }),
    );

    await waitFor(() => {
      expect(api.completeInvite).toHaveBeenCalledWith({
        token: "invite-tok-1",
        password: "StrongPass123!",
        setup_mfa: true,
      });
    });

    const profilePage = await screen.findByTestId("profile-page");
    expect(profilePage.textContent).toBe("/profile?mfa_setup=1");
  });

  it("shows optional MFA checkbox when mfa_enforced is false and redirects to / when unchecked", async () => {
    vi.mocked(api.getInviteInfo).mockResolvedValue({
      valid: true,
      email: "optional@penguintech.io",
      tenant_name: "Acme Corp",
      mfa_enforced: false,
      mfa_optional: true,
    });
    vi.mocked(api.completeInvite).mockResolvedValue({
      success: true,
      mfaSetupRequired: false,
      mfaSetupOptional: false,
    });

    renderSetupAccount("/setup-account?token=invite-tok-2");

    expect(await screen.findByTestId("mfa-optional-section")).toBeDefined();
    expect(screen.queryByTestId("mfa-enforced-banner")).toBeNull();

    const checkbox = screen.getByLabelText(
      /Set up Google Authenticator \(MFA\) now/i,
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);

    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "StrongPass123!" },
    });
    fireEvent.change(screen.getByLabelText("Confirm Password"), {
      target: { value: "StrongPass123!" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Complete Account Setup" }),
    );

    await waitFor(() => {
      expect(api.completeInvite).toHaveBeenCalledWith({
        token: "invite-tok-2",
        password: "StrongPass123!",
        setup_mfa: false,
      });
    });

    const homePage = await screen.findByTestId("home-page");
    expect(homePage.textContent).toBe("/");
  });

  it("redirects to /profile?mfa_setup=1 when optional MFA checkbox is checked", async () => {
    vi.mocked(api.getInviteInfo).mockResolvedValue({
      valid: true,
      email: "optin@penguintech.io",
      tenant_name: "Acme Corp",
      mfa_enforced: false,
      mfa_optional: true,
    });
    vi.mocked(api.completeInvite).mockResolvedValue({
      success: true,
      mfaSetupOptional: true,
    });

    renderSetupAccount("/setup-account?token=invite-tok-3");

    const checkbox = (await screen.findByLabelText(
      /Set up Google Authenticator \(MFA\) now/i,
    )) as HTMLInputElement;
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);

    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "StrongPass123!" },
    });
    fireEvent.change(screen.getByLabelText("Confirm Password"), {
      target: { value: "StrongPass123!" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Complete Account Setup" }),
    );

    await waitFor(() => {
      expect(api.completeInvite).toHaveBeenCalledWith({
        token: "invite-tok-3",
        password: "StrongPass123!",
        setup_mfa: true,
      });
    });

    const profilePage = await screen.findByTestId("profile-page");
    expect(profilePage.textContent).toBe("/profile?mfa_setup=1");
  });

  it("validates password mismatch before calling completeInvite", async () => {
    vi.mocked(api.getInviteInfo).mockResolvedValue({
      valid: true,
      email: "mismatch@penguintech.io",
      tenant_name: "Acme Corp",
      mfa_enforced: false,
      mfa_optional: true,
    });

    renderSetupAccount("/setup-account?token=invite-tok-4");

    await screen.findByTestId("setup-account-form");

    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "StrongPass123!" },
    });
    fireEvent.change(screen.getByLabelText("Confirm Password"), {
      target: { value: "DifferentPass456!" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Complete Account Setup" }),
    );

    expect(await screen.findByRole("alert")).toBeDefined();
    expect(api.completeInvite).not.toHaveBeenCalled();
  });

  it("shows invalid invitation state when token is invalid or expired", async () => {
    vi.mocked(api.getInviteInfo).mockRejectedValue({
      response: { data: { error: "Invitation link has expired." } },
    });

    renderSetupAccount("/setup-account?token=expired-invite");

    const invalidBox = await screen.findByTestId("setup-account-invalid");
    expect(invalidBox.textContent).toContain("Invitation link has expired.");
  });
});
