import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { Webhook, Database } from 'lucide-react'

// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Webhooks = lazy(() => import('@/pages/Webhooks'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Backups = lazy(() => import('@/pages/Backups'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Operations',
    collapsible: true,
    items: [
      { name: 'Backups', href: '/backups', icon: Database },
      { name: 'Webhooks', href: '/webhooks', icon: Webhook },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'webhooks', element: <Webhooks /> },
  { path: 'backups', element: <Backups /> },
]

const webhooksAlertingModule: FrontendModule = {
  id: 'webhooks_alerting',
  name: 'Webhooks & Alerting',
  nav: navigation,
  routes,
}

export default webhooksAlertingModule
