import { lazy, useEffect, Suspense, useMemo } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Search from './pages/Search'
import Login from './pages/LoginPageWrapper'
import Register from './pages/Register'
import Profile from './pages/Profile'
// v2.2.0 Enterprise Admin Pages
import Tenants from './pages/Tenants'
import TenantDetail from './pages/TenantDetail'
import SSOConfiguration from './pages/SSOConfiguration'
import AuditLogs from './pages/AuditLogs'
import AdminSettings from './pages/AdminSettings'
import SyncConfig from './pages/SyncConfig'
import LicensePolicies from './pages/LicensePolicies'
import ModuleToggles from './pages/ModuleToggles'
// Village ID Redirect
import VillageIdRedirect from './components/VillageIdRedirect'
import { AppConsoleVersion } from '@penguintechinc/react-libs/components'

// Module framework
import { routesFor } from './modules/registry'
import { useModules } from './hooks/useModules'

// Geographic resource map — lazy-loaded: pulls in maplibre-gl, kept out of
// the main bundle since it's a large dependency.
const Map = lazy(() => import('./pages/Map'))

// Protected route wrapper component
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const hasToken = localStorage.getItem('elder_token')

  if (!hasToken) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

// Route catch-all: logs unmatched paths and redirects appropriately
function RouteNotFound() {
  const location = useLocation()
  const hasToken = localStorage.getItem('elder_token')

  useEffect(() => {
    console.warn('[Elder] Route not found:', location.pathname)
  }, [location.pathname])

  if (hasToken) {
    return <Navigate to="/" replace />
  }

  return <Navigate to="/login" replace />
}

export default function App() {
  const { enabled: enabledModules } = useModules()

  // Get module routes only for enabled modules
  const moduleRoutes = useMemo(
    () => routesFor(enabledModules),
    [enabledModules]
  )

  return (
    <>
      <AppConsoleVersion
        appName="Elder"
        webuiVersion={import.meta.env.VITE_VERSION || '0.0.0'}
        webuiBuildEpoch={Number(import.meta.env.VITE_BUILD_TIME) || 0}
        environment={import.meta.env.MODE}
        webuiEmoji="🏛️"
        metadata={{
          'API URL': import.meta.env.VITE_API_URL || '(relative - using nginx proxy)',
        }}
      />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/id/:villageId" element={<ProtectedRoute><VillageIdRedirect /></ProtectedRoute>} />
        <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
          {/* Core pages (always available) */}
          <Route index element={<Dashboard />} />
          <Route path="search" element={<Search />} />
          <Route path="profile" element={<Profile />} />
          <Route
            path="map"
            element={<Suspense fallback={<div />}><Map /></Suspense>}
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
          <Route path="admin/sync-config" element={<SyncConfig />} />
          <Route path="admin/license-policies" element={<LicensePolicies />} />
          <Route path="admin/modules" element={<ModuleToggles />} />
        </Route>
        <Route path="*" element={<RouteNotFound />} />
      </Routes>
    </>
  )
}
