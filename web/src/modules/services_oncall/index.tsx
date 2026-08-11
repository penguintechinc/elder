import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { Server, Route, Clock } from 'lucide-react'

// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Services = lazy(() => import('@/pages/Services'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const ServiceEndpoints = lazy(() => import('@/pages/ServiceEndpoints'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const OnCallRotations = lazy(() => import('@/pages/OnCallRotations'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Software & Services',
    collapsible: true,
    items: [
      { name: 'Services', href: '/services', icon: Server },
      { name: 'Service Endpoints', href: '/service-endpoints', icon: Route },
      { name: 'On-Call Rotations', href: '/on-call-rotations', icon: Clock },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'services', element: <Services /> },
  { path: 'service-endpoints', element: <ServiceEndpoints /> },
  { path: 'on-call-rotations', element: <OnCallRotations /> },
]

const servicesOncallModule: FrontendModule = {
  id: 'services_oncall',
  name: 'Services & On-Call',
  group: 'core',
  nav: navigation,
  routes,
}

export default servicesOncallModule
