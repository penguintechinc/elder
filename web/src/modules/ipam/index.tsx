import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { Globe } from 'lucide-react'

// eslint-disable-next-line react-refresh/only-export-components -- Intentional: exports both lazy component and module manifest
const IPAM = lazy(() => import('@/pages/IPAM'))

const navigation: MenuCategory[] = [
  {
    header: 'Networking',
    collapsible: true,
    items: [
      { name: 'IPAM', href: '/ipam', icon: Globe },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'ipam', element: <IPAM /> },
]

const ipamModule: FrontendModule = {
  id: 'ipam',
  name: 'IPAM',
  group: 'entities',
  nav: navigation,
  routes,
}

export default ipamModule
