import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { Package, Bug, Layers } from 'lucide-react'

// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Software = lazy(() => import('@/pages/Software'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Vulnerabilities = lazy(() => import('@/pages/Vulnerabilities'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const SBOMDashboard = lazy(() => import('@/pages/SBOMDashboard'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Software & Services',
    collapsible: true,
    items: [
      { name: 'Software', href: '/software', icon: Package },
      { name: 'SBOM Dashboard', href: '/sbom', icon: Layers },
      { name: 'Vulnerabilities', href: '/vulnerabilities', icon: Bug },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'software', element: <Software /> },
  { path: 'vulnerabilities', element: <Vulnerabilities /> },
  { path: 'sbom', element: <SBOMDashboard /> },
]

const sbomModule: FrontendModule = {
  id: 'sbom',
  name: 'SBOM',
  group: 'core',
  nav: navigation,
  routes,
}

export default sbomModule
