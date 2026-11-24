import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Profile from "./Profile";

const mockGetProfile = vi.fn();
const mockGetOrganizations = vi.fn();
const mockEnableMfa = vi.fn();
const mockRegenerateBackupCodes = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    getProfile: (...args: unknown[]) => mockGetProfile(...args),
    getOrganizations: (...args: unknown[]) => mockGetOrganizations(...args),
    enableMfa: (...args: unknown[]) => mockEnableMfa(...args),
    regenerateMfaBackupCodes: (...args: unknown[]) =>
      mockRegenerateBackupCodes(...args),
    confirmMfa: vi.fn(),
    disableMfa: vi.fn(),
    updateProfile: vi.fn(),
    changePassword: vi.fn(),
  },
}));

describe("Profile Google Authenticator QR code & backup codes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetProfile.mockResolvedValue({
      id: 1,
      email: "admin@penguintech.io",
      full_name: "Admin User",
      auth_provider: "local",
      is_active: true,
      mfa_enabled: false,
      mfa_enforced: true,
      mfa_setup_required: true,
      mfa_backup_codes_remaining: 0,
      organizations: [],
    });
    mockGetOrganizations.mockResolvedValue({ items: [], total: 0 });
    mockEnableMfa.mockResolvedValue({
      secret: "JBSWY3DPEHPK3PXP",
      provisioning_uri:
        "otpauth://totp/Elder:admin%40penguintech.io?secret=JBSWY3DPEHPK3PXP&issuer=Elder",
      backup_codes: ["a1b2c3d4", "e5f60718"],
    });
    mockRegenerateBackupCodes.mockResolvedValue({
      backup_codes: ["11112222", "33334444"],
      remaining: 2,
    });
  });

  it("renders a scannable SVG QR code img and download button from otpauth URI", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <Profile />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Active")).toBeTruthy();

    const setupBtn = await screen.findByRole("button", {
      name: /set up google authenticator/i,
    });
    fireEvent.click(setupBtn);

    const qrImg = await waitFor(() => screen.getByTestId("mfa-qr-code"));
    expect(qrImg.tagName).toBe("IMG");
    expect(qrImg.getAttribute("alt")).toBe("Google Authenticator QR Code");
    expect(qrImg.getAttribute("src")).toMatch(
      /^data:image\/svg\+xml;charset=utf-8,/,
    );
    expect(screen.getByTestId("mfa-setup-secret").textContent).toContain(
      "JBSWY3DPEHPK3PXP",
    );
    expect(
      screen.getByRole("button", { name: /download \(\.txt\)/i }),
    ).toBeTruthy();
  });

  it("allows resetting and downloading new backup codes when MFA is already enabled", async () => {
    mockGetProfile.mockResolvedValue({
      id: 1,
      email: "admin@penguintech.io",
      full_name: "Admin User",
      auth_provider: "local",
      is_active: true,
      mfa_enabled: true,
      mfa_enforced: true,
      mfa_setup_required: false,
      mfa_backup_codes_remaining: 6,
      organizations: [],
    });

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <Profile />
      </QueryClientProvider>,
    );

    expect(
      await screen.findByText(/6 single-use bypass code\(s\) remaining/i),
    ).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: /reset backup codes/i }),
    );

    const codesBox = await waitFor(() =>
      screen.getByTestId("mfa-regenerated-backup-codes"),
    );
    expect(codesBox.textContent).toContain("11112222");
    expect(codesBox.textContent).toContain("33334444");
    expect(
      screen.getByRole("button", { name: /download \(\.txt\)/i }),
    ).toBeTruthy();
  });
});
