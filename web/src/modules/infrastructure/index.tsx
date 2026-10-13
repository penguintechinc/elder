import { lazy } from 'react'
import { Navigate } from 'react-router-dom'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import {
  Box,
  Building2,
  GitBranch,
  Server,
  Network,
  Share2,
} from 'lucide-react'

// Lazy-load page components
// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Entities = lazy(() => import('@/pages/Entities'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const EntityDetail = lazy(() => import('@/pages/EntityDetail'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Organizations = lazy(() => import('@/pages/Organizations'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const OrganizationDetail = lazy(() => import('@/pages/OrganizationDetail'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Dependencies = lazy(() => import('@/pages/Dependencies'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Diagram = lazy(() => import('@/pages/Diagram'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Compute = lazy(() => import('@/pages/Compute'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Networking = lazy(() => import('@/pages/Networking'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const RelationshipGraph = lazy(() => import('@/pages/RelationshipGraph'))
// eslint-enable react-refresh/only-export-components

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
      { name: 'Diagram', href: '/diagram', icon: Share2 },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'entities', element: <Entities /> },
  { path: 'entities/:id', element: <EntityDetail /> },
  { path: 'organizations', element: <Organizations /> },
  { path: 'organizations/:id', element: <OrganizationDetail /> },
  { path: 'dependencies', element: <Dependencies /> },
  { path: 'diagram', element: <Diagram /> },
  { path: 'compute', element: <Compute /> },
  { path: 'kubernetes', element: <Navigate to="/compute?tab=Kubernetes" replace /> },
  { path: 'lxd', element: <Navigate to="/compute?tab=LXD%2FLXC" replace /> },
  { path: 'networking', element: <Networking /> },
  { path: 'relationships/:id', element: <RelationshipGraph /> },
]

const infrastructureModule: FrontendModule = {
  id: 'infrastructure',
  name: 'Infrastructure',
  group: 'entities',
  nav: navigation,
  routes,
}

export default infrastructureModule
