import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { Shield, Key, FileKey } from 'lucide-react'

// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const IAM = lazy(() => import('@/pages/IAM'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Secrets = lazy(() => import('@/pages/Secrets'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Keys = lazy(() => import('@/pages/Keys'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Certificates = lazy(() => import('@/pages/Certificates'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Security',
    collapsible: true,
    items: [
      { name: 'Identity Center', href: '/iam', icon: Shield },
      { name: 'Keys', href: '/keys', icon: Key },
      { name: 'Secrets', href: '/secrets', icon: Key },
      { name: 'Certificates', href: '/certificates', icon: FileKey },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'iam', element: <IAM /> },
  { path: 'secrets', element: <Secrets /> },
  { path: 'keys', element: <Keys /> },
  { path: 'certificates', element: <Certificates /> },
]

const secretsModule: FrontendModule = {
  id: 'secrets',
  name: 'Secrets & Security',
  group: 'core',
  nav: navigation,
  routes,
}

export default secretsModule
