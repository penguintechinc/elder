import { useLocation, Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard,
  Building2,
  Box,
  GitBranch,
  AlertCircle,
  Tag,
  Search as SearchIcon,
  Map as MapIcon,
  FolderKanban,
  Flag,
  User,
  LogOut,
  Key,
  FileKey,
  Shield,
  Compass,
  Webhook,
  Database,
  Network,
  Settings,
  FileText,
  Users,
  ChevronDown,
  ChevronRight,
  Package,
  Server,
  Globe,
  HardDrive,
  Repeat2,
  Lock,
  Bug,
  Route,
  Layers,
  Clock,
  Ship,
  Container,
} from 'lucide-react'
import { SidebarMenu, MenuCategory, MenuItem } from '@penguintechinc/react-libs/components'
import api from '@/lib/api'

// Navigation organized by categories
const navigationCategories: MenuCategory[] = [
  {
    items: [
      { name: 'Dashboard', href: '/', icon: LayoutDashboard },
      { name: 'Search', href: '/search', icon: SearchIcon },
      { name: 'Map', href: '/map', icon: MapIcon },
    ],
  },
  {
    header: 'Assets',
    collapsible: true,
    items: [
      { name: 'Entities', href: '/entities', icon: Box },
      { name: 'Organizations', href: '/organizations', icon: Building2 },
    ],
  },
  {
    header: 'Software & Services',
    collapsible: true,
    items: [
      { name: 'Software', href: '/software', icon: Package },
      { name: 'Services', href: '/services', icon: Server },
      { name: 'SBOM Dashboard', href: '/sbom', icon: Layers },
      { name: 'Service Endpoints', href: '/service-endpoints', icon: Route },
      { name: 'Vulnerabilities', href: '/vulnerabilities', icon: Bug },
    ],
  },
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
  {
    header: 'Infrastructure',
    collapsible: true,
    items: [
      { name: 'On-Call Rotations', href: '/on-call-rotations', icon: Clock },
      { name: 'Dependencies', href: '/dependencies', icon: GitBranch },
      { name: 'Discovery', href: '/discovery', icon: Compass },
      { name: 'Networking', href: '/networking', icon: Network },
      { name: 'IPAM', href: '/ipam', icon: Globe },
      { name: 'Kubernetes', href: '/kubernetes', icon: Ship },
      { name: 'LXD', href: '/lxd', icon: Container },
    ],
  },
  {
    header: 'Operations',
    collapsible: true,
    items: [
      { name: 'Backups', href: '/backups', icon: Database },
      { name: 'Webhooks', href: '/webhooks', icon: Webhook },
    ],
  },
]

// Admin navigation - shown based on user role
const adminNavigation: MenuItem[] = [
  { name: 'Audit Logs', href: '/admin/audit-logs', icon: FileText, roles: ['admin', 'support', 'tenant_admin'] },
  { name: 'Settings', href: '/admin/settings', icon: Settings, roles: ['admin'] },
  { name: 'SSO Config', href: '/admin/sso', icon: Shield, roles: ['admin', 'tenant_admin'] },
  { name: 'Sync Config', href: '/admin/sync-config', icon: Repeat2, roles: ['admin'] },
  { name: 'License Policies', href: '/admin/license-policies', icon: Lock, roles: ['admin'] },
  { name: 'Tenants', href: '/admin/tenants', icon: Users, roles: ['admin'] },
]

// Footer items for profile and logout
const footerItems: MenuItem[] = [
  { name: 'Profile', href: '/profile', icon: User },
  { name: 'Logout', href: '#logout', icon: LogOut },
]

export default function Layout() {
  const location = useLocation()
  const navigate = useNavigate()

  // Fetch user profile for role-based navigation
  const { data: userProfile } = useQuery({
    queryKey: ['portal-profile'],
    queryFn: () => api.getPortalProfile(),
    staleTime: 60000, // Cache for 1 minute
    retry: false,
  })

  // Determine user roles for admin navigation visibility
  const globalRole = userProfile?.global_role
  const tenantRole = userProfile?.tenant_role

  // Filter admin navigation based on user roles
  const visibleAdminNav = adminNavigation.filter(item => {
    if (globalRole === 'admin') return item.roles?.includes('admin')
    if (globalRole === 'support') return item.roles?.includes('support')
    if (tenantRole === 'admin') return item.roles?.includes('tenant_admin')
    return false
  })

  // Build all categories including admin if visible
  const allCategories: MenuCategory[] = [
    ...navigationCategories,
    ...(visibleAdminNav.length > 0
      ? [
          {
            header: 'Administration',
            collapsible: true,
            items: visibleAdminNav,
          },
        ]
      : []),
  ]

  const handleLogout = () => {
    localStorage.removeItem('elder_token')
    localStorage.removeItem('elder_refresh_token')
    window.location.href = '/login'
  }

  const handleNavigate = (href: string) => {
    if (href === '#logout') {
      handleLogout()
    } else {
      navigate(href)
    }
  }

  return (
    <div className="min-h-screen bg-slate-900">
      {/* Sidebar */}
      <SidebarMenu
        logo={<img src="/elder-logo.png" alt="Elder Logo" className="h-12 w-auto" />}
        categories={allCategories}
        currentPath={location.pathname}
        onNavigate={handleNavigate}
        footerItems={footerItems}
        userRole={globalRole || tenantRole}
        collapseIcon={ChevronDown}
        expandIcon={ChevronRight}
      />

      {/* Main content */}
      <div className="lg:pl-64 pl-0">
        <main className="min-h-screen">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
