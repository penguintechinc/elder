import { lazy, useEffect, Suspense, useMemo } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Search from "./pages/Search";
import Login from "./pages/LoginPageWrapper";
import MfaEnroll from "./pages/MfaEnroll";
import Register from "./pages/Register";
import Profile from "./pages/Profile";
// v2.2.0 Enterprise Admin Pages
import Tenants from "./pages/Tenants";
import TenantDetail from "./pages/TenantDetail";
import SSOConfiguration from "./pages/SSOConfiguration";
import AuditLogs from "./pages/AuditLogs";
import AdminSettings from "./pages/AdminSettings";
import Integrations from "./pages/Integrations";
import LicensePolicies from "./pages/LicensePolicies";
import ModuleToggles from "./pages/ModuleToggles";
// Village ID Redirect
import VillageIdRedirect from "./components/VillageIdRedirect";
import { AppConsoleVersion } from "@penguintechinc/react-libs/components";
import { isAuthenticated } from "./lib/api";

// Module framework
import { routesFor } from "./modules/registry";
import { useModules } from "./hooks/useModules";

// Geographic resource map — lazy-loaded: pulls in maplibre-gl, kept out of
// the main bundle since it's a large dependency.
const Map = lazy(() => import("./pages/Map"));
const AdminWorkers = lazy(() => import("./pages/AdminWorkers"));

// Protected route wrapper component. The actual JWTs are HttpOnly cookies
// (gh security audit) -- this checks only the non-sensitive "logged in"
// flag set by api.ts; the cookie itself is what the backend actually
// enforces on every request, this is a UX-only redirect.
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  if (!isAuthenticated()) {
    const intended = `${location.pathname}${location.search}`;
    const loginTarget =
      intended && intended !== "/"
        ? `/login?next=${encodeURIComponent(intended)}`
        : "/login";
    return <Navigate to={loginTarget} replace />;
  }

  return <>{children}</>;
}

// Route catch-all: logs unmatched paths and redirects appropriately
function RouteNotFound() {
  const location = useLocation();
  const { isLoading: modulesLoading } = useModules();
  const hasToken = isAuthenticated();

  useEffect(() => {
    if (!modulesLoading) {
      console.warn("[Elder] Route not found:", location.pathname);
    }
  }, [location.pathname, modulesLoading]);

  // Module routes register asynchronously (useModules fetches /api/v1/modules).
  // Until that resolves, moduleRoutes is empty, so a valid module path (e.g.
  // /entities) matches nothing and falls through to this catch-all. Don't
  // redirect while modules are still loading — show a spinner and let the real
  // route take over once they load. Without this, a hard refresh or deep link on
  // any module page bounces the user to the dashboard.
  if (modulesLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-bg-primary">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-600 border-t-primary-500" />
      </div>
    );
  }

  if (hasToken) {
    return <Navigate to="/" replace />;
  }

  const intended = `${location.pathname}${location.search}`;
  const loginTarget =
    intended && intended !== "/" && location.pathname !== "/login"
      ? `/login?next=${encodeURIComponent(intended)}`
      : "/login";
  return <Navigate to={loginTarget} replace />;
}

export default function App() {
  const { enabled: enabledModules } = useModules();

  // Get module routes only for enabled modules
  const moduleRoutes = useMemo(
    () => routesFor(enabledModules),
    [enabledModules],
  );

  return (
    <>
      <AppConsoleVersion
        appName="Elder"
        webuiVersion={import.meta.env.VITE_VERSION || "0.0.0"}
        webuiBuildEpoch={Number(import.meta.env.VITE_BUILD_TIME) || 0}
        environment={import.meta.env.MODE}
        webuiEmoji="🏛️"
        metadata={{
          "API URL":
            import.meta.env.VITE_API_URL || "(relative - using Express proxy)",
        }}
      />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/mfa-enroll" element={<MfaEnroll />} />
        <Route path="/register" element={<Register />} />
        <Route
          path="/id/:villageId"
          element={
            <ProtectedRoute>
              <VillageIdRedirect />
            </ProtectedRoute>
          }
        />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          {/* Core pages (always available) */}
          <Route index element={<Dashboard />} />
          <Route path="search" element={<Search />} />
          <Route path="profile" element={<Profile />} />
          <Route
            path="map"
            element={
              <Suspense fallback={<div />}>
                <Map />
              </Suspense>
            }
          />

          {/* Module-gated routes: wrap each lazy element in its own Suspense.
              A <Suspense> may NOT be a direct child of a <Route> in react-router
              v6 (only <Route>/<Fragment> are allowed) — doing so throws in
              createRoutesFromChildren and blanks the entire app. */}
          {moduleRoutes.map((route, idx) => (
            <Route
              key={idx}
              path={route.path}
              element={<Suspense fallback={<div />}>{route.element}</Suspense>}
              handle={route.handle}
            />
          ))}

          {/* Admin pages (always available, filtered by role in Layout) */}
          <Route path="admin/tenants" element={<Tenants />} />
          <Route path="admin/tenants/:id" element={<TenantDetail />} />
          <Route path="admin/sso" element={<SSOConfiguration />} />
          <Route path="admin/audit-logs" element={<AuditLogs />} />
          <Route path="admin/settings" element={<AdminSettings />} />
          <Route path="admin/integrations" element={<Integrations />} />
          <Route
            path="admin/sync-config"
            element={
              <Navigate to="/admin/integrations?tab=work-management" replace />
            }
          />
          <Route path="admin/license-policies" element={<LicensePolicies />} />
          <Route path="admin/modules" element={<ModuleToggles />} />
          <Route
            path="admin/workers"
            element={
              <Suspense fallback={<div />}>
                <AdminWorkers />
              </Suspense>
            }
          />
        </Route>
        <Route path="*" element={<RouteNotFound />} />
      </Routes>
    </>
  );
}
