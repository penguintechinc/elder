import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Profile from "./Profile";

const mockGetProfile = vi.fn();
const mockGetOrganizations = vi.fn();
const mockEnableMfa = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    getProfile: (...args: unknown[]) => mockGetProfile(...args),
    getOrganizations: (...args: unknown[]) => mockGetOrganizations(...args),
    enableMfa: (...args: unknown[]) => mockEnableMfa(...args),
    confirmMfa: vi.fn(),
    disableMfa: vi.fn(),
    updateProfile: vi.fn(),
    changePassword: vi.fn(),
  },
}));

describe("Profile Google Authenticator QR code", () => {
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
      organizations: [],
    });
    mockGetOrganizations.mockResolvedValue({ items: [], total: 0 });
    mockEnableMfa.mockResolvedValue({
      secret: "JBSWY3DPEHPK3PXP",
      provisioning_uri:
        "otpauth://totp/Elder:admin%40penguintech.io?secret=JBSWY3DPEHPK3PXP&issuer=Elder",
      backup_codes: ["a1b2c3d4", "e5f60718"],
    });
  });

  it("renders a scannable SVG QR code img from the otpauth provisioning URI", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <Profile />
      </QueryClientProvider>,
    );

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
  });
});
