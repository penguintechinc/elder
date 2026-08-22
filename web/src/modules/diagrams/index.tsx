import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { Network } from 'lucide-react'

// Lazy-load page components
// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const DiagramsList = lazy(() => import('@/pages/DiagramsList'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const DiagramEditor = lazy(() => import('@/pages/DiagramEditor'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Diagrams',
    collapsible: false,
    items: [
      { name: 'Diagrams', href: '/diagrams', icon: Network },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'diagrams', element: <DiagramsList /> },
  { path: 'diagrams/:id', element: <DiagramEditor /> },
]

const diagramsModule: FrontendModule = {
  id: 'nav_diagrams',
  name: 'Diagrams',
  group: 'documents',
  nav: navigation,
  routes,
}

export default diagramsModule
