import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { Terminal } from 'lucide-react'

// Lazy-load page components
// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const RunsList = lazy(() => import('@/pages/RunsList'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const RunDetail = lazy(() => import('@/pages/RunDetail'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Automation',
    collapsible: false,
    items: [{ name: 'Runs', href: '/runs', icon: Terminal }],
  },
]

const routes: RouteObject[] = [
  { path: 'runs', element: <RunsList /> },
  { path: 'runs/:id', element: <RunDetail /> },
]

const runsModule: FrontendModule = {
  id: 'runs',
  name: 'Runs',
  group: 'workstreams',
  nav: navigation,
  routes,
}

export default runsModule
