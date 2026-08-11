import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { Workflow, CheckSquare, Clock } from 'lucide-react'

// Lazy-load page components
// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const StreamsList = lazy(() => import('@/pages/StreamsList'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const PlaybookEditor = lazy(() => import('@/pages/PlaybookEditor'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const ExecutionsList = lazy(() => import('@/pages/ExecutionsList'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const ExecutionDetail = lazy(() => import('@/pages/ExecutionDetail'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const ApprovalCenter = lazy(() => import('@/pages/ApprovalCenter'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Streams',
    collapsible: false,
    items: [
      { name: 'Playbooks', href: '/streams', icon: Workflow },
      { name: 'Executions', href: '/streams/executions', icon: Clock },
      { name: 'Approvals', href: '/streams/approvals', icon: CheckSquare },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'streams', element: <StreamsList /> },
  { path: 'streams/:id', element: <PlaybookEditor /> },
  { path: 'streams/executions', element: <ExecutionsList /> },
  { path: 'streams/executions/:executionId', element: <ExecutionDetail /> },
  { path: 'streams/approvals', element: <ApprovalCenter /> },
]

const streamsModule: FrontendModule = {
  id: 'nav_streams',
  name: 'Streams',
  group: 'workflow',
  nav: navigation,
  routes,
}

export default streamsModule
