import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import VillageIdRedirect from "./VillageIdRedirect";
import LoginPageWrapper from "@/pages/LoginPageWrapper";
import App from "@/App";
import api, { isAuthenticated } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  default: {
    resolveVillageId: vi.fn(),
  },
  isAuthenticated: vi.fn(),
  markAuthenticated: vi.fn(),
}));

vi.mock("@/hooks/useModules", () => ({
  useModules: () => ({ enabled: new Set<string>(), isLoading: false }),
}));

vi.mock("@penguintechinc/react-libs/components", () => ({
  AppConsoleVersion: () => null,
  ELDER_LOGIN_THEME: {},
  LoginPageBuilder: ({
    onSuccess,
  }: {
    onSuccess: (response: { token: string }) => void;
  }) => (
    <button
      type="button"
      onClick={() => onSuccess({ token: "cookie-session" })}
    >
      Complete Mock Login
    </button>
  ),
}));

const SAMPLE_VID = "00000001-000001000000002a";

function LocationEcho() {
  const loc = useLocation();
  return <div data-testid="current-location">{loc.pathname + loc.search}</div>;
}

describe("VillageIdRedirect", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows resolving loading state while request is in flight", () => {
    vi.mocked(api.resolveVillageId).mockImplementation(
      () => new Promise(() => {}),
    );

    render(
      <MemoryRouter initialEntries={[`/id/${SAMPLE_VID}`]}>
        <Routes>
          <Route path="/id/:villageId" element={<VillageIdRedirect />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText(/Resolving/)).toBeTruthy();
    expect(screen.getByText(SAMPLE_VID)).toBeTruthy();
  });

  it("resolves Village ID and navigates to redirect_url with replace", async () => {
    vi.mocked(api.resolveVillageId).mockResolvedValueOnce({
      village_id: SAMPLE_VID,
      resource_type: "entity",
      resource_id: 42,
      title: "prod-web-01",
      module: "infrastructure",
      redirect_url: "/entities/42",
      short_link: `/id/${SAMPLE_VID}`,
    });

    render(
      <MemoryRouter initialEntries={[`/id/${SAMPLE_VID}`]}>
        <Routes>
          <Route path="/id/:villageId" element={<VillageIdRedirect />} />
          <Route path="/entities/:id" element={<LocationEcho />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("current-location").textContent).toBe(
        "/entities/42",
      );
    });
    expect(api.resolveVillageId).toHaveBeenCalledWith(SAMPLE_VID);
  });

  it("navigates to list highlight query routes returned by backend", async () => {
    vi.mocked(api.resolveVillageId).mockResolvedValueOnce({
      village_id: SAMPLE_VID,
      resource_type: "data_store",
      resource_id: 19,
      redirect_url: "/data-inventory?highlight=19",
    });

    render(
      <MemoryRouter initialEntries={[`/id/${SAMPLE_VID}`]}>
        <Routes>
          <Route path="/id/:villageId" element={<VillageIdRedirect />} />
          <Route path="/data-inventory" element={<LocationEcho />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("current-location").textContent).toBe(
        "/data-inventory?highlight=19",
      );
    });
  });

  it("renders actionable 404 state with Dashboard and Search buttons", async () => {
    vi.mocked(api.resolveVillageId).mockRejectedValueOnce({
      response: {
        status: 404,
        data: { error: "Resource not found" },
      },
    });

    render(
      <MemoryRouter initialEntries={[`/id/${SAMPLE_VID}`]}>
        <Routes>
          <Route path="/id/:villageId" element={<VillageIdRedirect />} />
          <Route path="/" element={<LocationEcho />} />
          <Route path="/search" element={<LocationEcho />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Village ID Not Found")).toBeTruthy();
    });
    expect(screen.getByText(SAMPLE_VID)).toBeTruthy();
    expect(screen.getByText("Resource not found")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Search/i }));
    await waitFor(() => {
      expect(screen.getByTestId("current-location").textContent).toBe(
        `/search?q=${encodeURIComponent(SAMPLE_VID)}`,
      );
    });
  });

  it("navigates to dashboard when clicking Back to Dashboard in error state", async () => {
    vi.mocked(api.resolveVillageId).mockRejectedValueOnce({
      response: { status: 404, data: {} },
    });

    render(
      <MemoryRouter initialEntries={[`/id/${SAMPLE_VID}`]}>
        <Routes>
          <Route path="/id/:villageId" element={<VillageIdRedirect />} />
          <Route path="/" element={<LocationEcho />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Village ID Not Found")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Back to Dashboard/i }));
    await waitFor(() => {
      expect(screen.getByTestId("current-location").textContent).toBe("/");
    });
  });

  it("rejects external or protocol-relative redirect_url values", async () => {
    vi.mocked(api.resolveVillageId).mockResolvedValueOnce({
      village_id: SAMPLE_VID,
      resource_type: "entity",
      resource_id: 1,
      redirect_url: "//evil.example/phish",
    });

    render(
      <MemoryRouter initialEntries={[`/id/${SAMPLE_VID}`]}>
        <Routes>
          <Route path="/id/:villageId" element={<VillageIdRedirect />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(
        screen.getByText("No valid redirect URL found for this Village ID"),
      ).toBeTruthy();
    });
  });
});

describe("LoginPageWrapper ?next= redirect handling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("honors a safe relative ?next= path after successful login", async () => {
    const nextUrl = `/id/${SAMPLE_VID}`;

    render(
      <MemoryRouter
        initialEntries={[`/login?next=${encodeURIComponent(nextUrl)}`]}
      >
        <Routes>
          <Route path="/login" element={<LoginPageWrapper />} />
          <Route path="/id/:villageId" element={<LocationEcho />} />
          <Route path="/" element={<LocationEcho />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Complete Mock Login" }),
    );

    await waitFor(() => {
      expect(screen.getByTestId("current-location").textContent).toBe(nextUrl);
    });
  });

  it.each([
    "//evil.example/steal",
    "https://evil.example/steal",
    "/\\evil.example/steal",
    "javascript:alert(1)",
    "/login?next=/admin",
  ])("blocks unsafe ?next=%s and falls back to /", async (unsafeNext) => {
    render(
      <MemoryRouter
        initialEntries={[`/login?next=${encodeURIComponent(unsafeNext)}`]}
      >
        <Routes>
          <Route path="/login" element={<LoginPageWrapper />} />
          <Route path="/" element={<LocationEcho />} />
          <Route path="*" element={<LocationEcho />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Complete Mock Login" }),
    );

    await waitFor(() => {
      expect(screen.getByTestId("current-location").textContent).toBe("/");
    });
  });
});

describe("ProtectedRoute ?next= preservation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(isAuthenticated).mockReturnValue(false);
  });

  it("preserves /id/<villageId> in ?next= when redirecting unauthenticated user to /login", async () => {
    function CaptureAppLocation() {
      return (
        <>
          <App />
          <LocationEcho />
        </>
      );
    }

    render(
      <MemoryRouter initialEntries={[`/id/${SAMPLE_VID}?ref=share`]}>
        <CaptureAppLocation />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("current-location").textContent).toBe(
        `/login?next=${encodeURIComponent(`/id/${SAMPLE_VID}?ref=share`)}`,
      );
    });
  });
});
