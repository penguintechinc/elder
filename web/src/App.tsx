import { useEffect } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Organizations from './pages/Organizations'
import OrganizationDetail from './pages/OrganizationDetail'
import Entities from './pages/Entities'
import EntityDetail from './pages/EntityDetail'
import Dependencies from './pages/Dependencies'
import Issues from './pages/Issues'
import IssueDetail from './pages/IssueDetail'
import Projects from './pages/Projects'
import ProjectDetail from './pages/ProjectDetail'
import Milestones from './pages/Milestones'
import Labels from './pages/Labels'
import Search from './pages/Search'
import Map from './pages/Map'
import Login from './pages/LoginPageWrapper'
import Register from './pages/Register'
import RelationshipGraph from './pages/RelationshipGraph'
import Profile from './pages/Profile'
// v1.2.0 Pages
import Secrets from './pages/Secrets'
import Keys from './pages/Keys'
import Certificates from './pages/Certificates'
import IAM from './pages/IAM'
import Discovery from './pages/Discovery'
import Webhooks from './pages/Webhooks'
import Backups from './pages/Backups'
// v2.0.0 Pages
import Networking from './pages/Networking'
// v2.3.0 Pages
import Software from './pages/Software'
import Services from './pages/Services'
import DataStores from './pages/DataStores'
import IPAM from './pages/IPAM'
import Vulnerabilities from './pages/Vulnerabilities'
// v2.2.0 Enterprise Admin Pages
import Tenants from './pages/Tenants'
import TenantDetail from './pages/TenantDetail'
import SSOConfiguration from './pages/SSOConfiguration'
import AuditLogs from './pages/AuditLogs'
import AdminSettings from './pages/AdminSettings'
import SyncConfig from './pages/SyncConfig'
import LicensePolicies from './pages/LicensePolicies'
import SBOMDashboard from '@/pages/SBOMDashboard'
import ServiceEndpoints from '@/pages/ServiceEndpoints'
import OnCallRotations from '@/pages/OnCallRotations'
// v3.1.0 Pages
import Compute from './pages/Compute'
// Village ID Redirect
import VillageIdRedirect from './components/VillageIdRedirect'
import { AppConsoleVersion } from '@penguintechinc/react-libs/components'

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
        <Route index element={<Dashboard />} />
        <Route path="search" element={<Search />} />
        <Route path="map" element={<Map />} />
        <Route path="profile" element={<Profile />} />
        <Route path="organizations" element={<Organizations />} />
        <Route path="organizations/:id" element={<OrganizationDetail />} />
        <Route path="relationships/:id" element={<RelationshipGraph />} />
        <Route path="entities" element={<Entities />} />
        <Route path="entities/:id" element={<EntityDetail />} />
        <Route path="dependencies" element={<Dependencies />} />
        <Route path="issues" element={<Issues />} />
        <Route path="issues/:id" element={<IssueDetail />} />
        <Route path="projects" element={<Projects />} />
        <Route path="projects/:id" element={<ProjectDetail />} />
        <Route path="milestones" element={<Milestones />} />
        <Route path="labels" element={<Labels />} />
        <Route path="secrets" element={<Secrets />} />
        <Route path="keys" element={<Keys />} />
        <Route path="certificates" element={<Certificates />} />
        <Route path="iam" element={<IAM />} />
        <Route path="discovery" element={<Discovery />} />
        <Route path="webhooks" element={<Webhooks />} />
        <Route path="backups" element={<Backups />} />
        <Route path="networking" element={<Networking />} />
        {/* v2.3.0 Routes */}
        <Route path="software" element={<Software />} />
        <Route path="services" element={<Services />} />
        <Route path="data-stores" element={<DataStores />} />
        <Route path="ipam" element={<IPAM />} />
        <Route path="vulnerabilities" element={<Vulnerabilities />} />
        <Route path="sbom" element={<SBOMDashboard />} />
        <Route path="service-endpoints" element={<ServiceEndpoints />} />
        <Route path="on-call-rotations" element={<OnCallRotations />} />
        {/* v3.1.0 Routes */}
        <Route path="compute" element={<Compute />} />
        <Route path="kubernetes" element={<Navigate to="/compute?tab=Kubernetes" replace />} />
        <Route path="lxd" element={<Navigate to="/compute?tab=LXD%2FLXC" replace />} />
        {/* v2.2.0 Enterprise Admin Routes */}
        <Route path="admin/tenants" element={<Tenants />} />
        <Route path="admin/tenants/:id" element={<TenantDetail />} />
        <Route path="admin/sso" element={<SSOConfiguration />} />
        <Route path="admin/audit-logs" element={<AuditLogs />} />
        <Route path="admin/settings" element={<AdminSettings />} />
        <Route path="admin/sync-config" element={<SyncConfig />} />
        <Route path="admin/license-policies" element={<LicensePolicies />} />
      </Route>
      <Route path="*" element={<RouteNotFound />} />
      </Routes>
    </>
  )
}
