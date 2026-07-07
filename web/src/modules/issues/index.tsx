import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { AlertCircle, Tag, Flag, FolderKanban, HardDrive } from 'lucide-react'

const Issues = lazy(() => import('@/pages/Issues'))
const IssueDetail = lazy(() => import('@/pages/IssueDetail'))
const Projects = lazy(() => import('@/pages/Projects'))
const ProjectDetail = lazy(() => import('@/pages/ProjectDetail'))
const Milestones = lazy(() => import('@/pages/Milestones'))
const Labels = lazy(() => import('@/pages/Labels'))
const DataStores = lazy(() => import('@/pages/DataStores'))

const navigation: MenuCategory[] = [
  {
    header: 'Tracking',
    collapsible: true,
    items: [
      { name: 'Issues', href: '/issues', icon: AlertCircle },
      { name: 'Labels', href: '/labels', icon: Tag },
      { name: 'Milestones', href: '/milestones', icon: Flag },
      { name: 'Projects', href: '/projects', icon: FolderKanban },
      { name: 'Data Stores', href: '/data-stores', icon: HardDrive },
    ],
  },
]

const routes: RouteObject[] = [
  { path: 'issues', element: <Issues /> },
  { path: 'issues/:id', element: <IssueDetail /> },
  { path: 'projects', element: <Projects /> },
  { path: 'projects/:id', element: <ProjectDetail /> },
  { path: 'milestones', element: <Milestones /> },
  { path: 'labels', element: <Labels /> },
  { path: 'data-stores', element: <DataStores /> },
]

const issuesModule: FrontendModule = {
  id: 'issues',
  name: 'Issues & Tracking',
  nav: navigation,
  routes,
}

export default issuesModule
