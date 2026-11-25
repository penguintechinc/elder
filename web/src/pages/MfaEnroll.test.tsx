import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MfaEnroll from "./MfaEnroll";

const mockEnableMfa = vi.fn();
const mockConfirmMfa = vi.fn();
const mockLogout = vi.fn();
const mockNavigate = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    enableMfa: (...args: unknown[]) => mockEnableMfa(...args),
    confirmMfa: (...args: unknown[]) => mockConfirmMfa(...args),
    logout: (...args: unknown[]) => mockLogout(...args),
  },
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}));

describe("MfaEnroll (enrolment-scoped session)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockEnableMfa.mockResolvedValue({
      secret: "JBSWY3DPEHPK3PXP",
      provisioning_uri:
        "otpauth://totp/Elder:user%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Elder",
      backup_codes: ["a1b2c3d4", "e5f60718"],
      pending: true,
      expires_in: 600,
    });
    mockConfirmMfa.mockResolvedValue({ verified: true, reauthenticate: true });
    mockLogout.mockResolvedValue(undefined);
  });

  it("enrols, then clears the session and returns to sign-in", async () => {
    render(<MfaEnroll />);
    expect(screen.getByTestId("mfa-enroll-notice").textContent).toMatch(
      /grace period has expired/i,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /set up google authenticator/i }),
    );

    // No current code is sent: this user has no factor yet.
    await waitFor(() => expect(mockEnableMfa).toHaveBeenCalledWith());
    expect(
      (await screen.findByTestId("mfa-enroll-secret")).textContent,
    ).toContain("JBSWY3DPEHPK3PXP");
    expect(screen.getByTestId("mfa-enroll-backup-codes").textContent).toContain(
      "a1b2c3d4",
    );
    const qr = await screen.findByTestId("mfa-enroll-qr");
    expect(qr.getAttribute("src")).toMatch(/^data:image\/svg\+xml/);

    fireEvent.change(screen.getByTestId("mfa-enroll-code"), {
      target: { value: "123 456" },
    });
    fireEvent.click(screen.getByRole("button", { name: /verify and finish/i }));

    await waitFor(() => expect(mockConfirmMfa).toHaveBeenCalledWith("123456"));
    await waitFor(() => expect(mockLogout).toHaveBeenCalledTimes(1));
    expect(mockNavigate).toHaveBeenCalledWith("/login", { replace: true });
  });

  it("rejects a malformed code locally and surfaces server errors", async () => {
    render(<MfaEnroll />);
    fireEvent.click(
      screen.getByRole("button", { name: /set up google authenticator/i }),
    );
    const input = await screen.findByTestId("mfa-enroll-code");

    fireEvent.change(input, { target: { value: "12ab" } });
    fireEvent.click(screen.getByRole("button", { name: /verify and finish/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/6-digit/i);
    expect(mockConfirmMfa).not.toHaveBeenCalled();

    mockConfirmMfa.mockRejectedValueOnce({
      response: { data: { error: "Invalid verification code" } },
    });
    fireEvent.change(input, { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: /verify and finish/i }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toMatch(
        /invalid verification code/i,
      ),
    );
    expect(mockLogout).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("shows an error when setup cannot start (expired enrolment token)", async () => {
    mockEnableMfa.mockRejectedValueOnce({ response: { data: {} } });
    render(<MfaEnroll />);
    fireEvent.click(
      screen.getByRole("button", { name: /set up google authenticator/i }),
    );
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /sign in again/i,
    );
  });
});
