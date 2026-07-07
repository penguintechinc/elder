import { lazy } from 'react'
import { Navigate } from 'react-router-dom'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import {
  Box,
  Building2,
  GitBranch,
  MapIcon,
  Server,
  Network,
} from 'lucide-react'

// Lazy-load page components
const Entities = lazy(() => import('@/pages/Entities'))
const EntityDetail = lazy(() => import('@/pages/EntityDetail'))
const Organizations = lazy(() => import('@/pages/Organizations'))
const OrganizationDetail = lazy(() => import('@/pages/OrganizationDetail'))
const Dependencies = lazy(() => import('@/pages/Dependencies'))
const Map = lazy(() => import('@/pages/Map'))
const Compute = lazy(() => import('@/pages/Compute'))
const Networking = lazy(() => import('@/pages/Networking'))
const RelationshipGraph = lazy(() => import('@/pages/RelationshipGraph'))

const navigation: MenuCategory[] = [
  {
    header: 'Assets',
    collapsible: true,
    items: [
      { name: 'Compute', href: '/compute', icon: Server },
      { name: 'Entities', href: '/entities', icon: Box },
      { name: 'Organizations', href: '/organizations', icon: Building2 },
    ],
  },
  {
    header: 'Infrastructure',
    collapsible: true,
    items: [
      { name: 'Networking', href: '/networking', icon: Network },
      { name: 'Dependencies', href: '/dependencies', icon: GitBranch },
      { name: 'Map', href: '/map', icon: MapIcon },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'entities', element: <Entities /> },
  { path: 'entities/:id', element: <EntityDetail /> },
  { path: 'organizations', element: <Organizations /> },
  { path: 'organizations/:id', element: <OrganizationDetail /> },
  { path: 'dependencies', element: <Dependencies /> },
  { path: 'map', element: <Map /> },
  { path: 'compute', element: <Compute /> },
  { path: 'kubernetes', element: <Navigate to="/compute?tab=Kubernetes" replace /> },
  { path: 'lxd', element: <Navigate to="/compute?tab=LXD%2FLXC" replace /> },
  { path: 'networking', element: <Networking /> },
  { path: 'relationships/:id', element: <RelationshipGraph /> },
]

const infrastructureModule: FrontendModule = {
  id: 'infrastructure',
  name: 'Infrastructure',
  nav: navigation,
  routes,
}

export default infrastructureModule
