import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { FileText } from 'lucide-react'

// Lazy-load page components
// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const DocumentsList = lazy(() => import('@/pages/DocumentsList'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const DocumentView = lazy(() => import('@/pages/DocumentView'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const DocumentEditor = lazy(() => import('@/pages/DocumentEditor'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Documents',
    collapsible: false,
    items: [
      { name: 'Documents', href: '/documents', icon: FileText },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'documents', element: <DocumentsList /> },
  { path: 'documents/new', element: <DocumentEditor /> },
  { path: 'documents/:slug', element: <DocumentView /> },
  { path: 'documents/:slug/edit', element: <DocumentEditor /> },
]

const documentsModule: FrontendModule = {
  id: 'nav_documents',
  name: 'Documents',
  group: 'documents',
  nav: navigation,
  routes,
}

export default documentsModule
