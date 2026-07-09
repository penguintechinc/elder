import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory, MenuItem } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { TicketIcon, BarChart3, Building2, Users } from 'lucide-react'

// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const HelpdeskDashboard = lazy(() => import('@/pages/helpdesk/HelpdeskDashboard'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const TicketList = lazy(() => import('@/pages/helpdesk/TicketList'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const TicketDetail = lazy(() => import('@/pages/helpdesk/TicketDetail'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const HelpdeskSettings = lazy(() => import('@/pages/helpdesk/HelpdeskSettings'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const HelpdeskCompanies = lazy(() => import('@/pages/helpdesk/HelpdeskCompanies'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const HelpdeskContacts = lazy(() => import('@/pages/helpdesk/HelpdeskContacts'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Helpdesk',
    collapsible: true,
    items: [
      { name: 'Dashboard', href: '/helpdesk', icon: BarChart3 },
      { name: 'Tickets', href: '/helpdesk/tickets', icon: TicketIcon },
      { name: 'Companies', href: '/helpdesk/companies', icon: Building2 },
      { name: 'Contacts', href: '/helpdesk/contacts', icon: Users },
    ],
  },
]

const adminNav: MenuItem[] = [
  { name: 'Helpdesk Settings', href: '/helpdesk/settings' },
]

const routes: RouteObject[] = [
  { path: 'helpdesk', element: <HelpdeskDashboard /> },
  { path: 'helpdesk/tickets', element: <TicketList /> },
  { path: 'helpdesk/tickets/:id', element: <TicketDetail /> },
  { path: 'helpdesk/companies', element: <HelpdeskCompanies /> },
  { path: 'helpdesk/contacts', element: <HelpdeskContacts /> },
  { path: 'helpdesk/settings', element: <HelpdeskSettings /> },
]

const helpdeskModule: FrontendModule = {
  id: 'helpdesk',
  name: 'Helpdesk',
  nav: navigation,
  adminNav,
  routes,
}

export default helpdeskModule
