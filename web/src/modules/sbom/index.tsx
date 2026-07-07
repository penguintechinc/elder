import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { Package, Bug, Layers } from 'lucide-react'

const Software = lazy(() => import('@/pages/Software'))
const Vulnerabilities = lazy(() => import('@/pages/Vulnerabilities'))
const SBOMDashboard = lazy(() => import('@/pages/SBOMDashboard'))

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
  nav: navigation,
  routes,
}

export default sbomModule
