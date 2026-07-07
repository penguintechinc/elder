import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { Compass } from 'lucide-react'

const Discovery = lazy(() => import('@/pages/Discovery'))

const navigation: MenuCategory[] = [
  {
    header: 'Infrastructure',
    collapsible: true,
    items: [
      { name: 'Discovery', href: '/discovery', icon: Compass },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'discovery', element: <Discovery /> },
]

const discoveryModule: FrontendModule = {
  id: 'discovery',
  name: 'Discovery',
  nav: navigation,
  routes,
}

export default discoveryModule
