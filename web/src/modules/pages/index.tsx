import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { BookOpen } from 'lucide-react'

// Lazy-load page components
// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const PagesList = lazy(() => import('@/pages/PagesList'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const PageView = lazy(() => import('@/pages/PageView'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const PageEditor = lazy(() => import('@/pages/PageEditor'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Pages',
    collapsible: false,
    items: [
      { name: 'Pages', href: '/pages', icon: BookOpen },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'pages', element: <PagesList /> },
  { path: 'pages/new', element: <PageEditor /> },
  { path: 'pages/:slug', element: <PageView /> },
  { path: 'pages/:slug/edit', element: <PageEditor /> },
]

const pagesModule: FrontendModule = {
  id: 'nav_pages',
  name: 'Pages',
  nav: navigation,
  routes,
}

export default pagesModule
