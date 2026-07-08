import { useLocation, Outlet, useNavigate } from 'react-router-dom'
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard,
  Search as SearchIcon,
  MapIcon,
  User,
  LogOut,
  Settings,
  FileText,
  Users,
  ChevronDown,
  ChevronRight,
  Shield,
  Repeat2,
  Lock,
  Zap,
} from 'lucide-react'
import { SidebarMenu, MenuCategory, MenuItem } from '@penguintechinc/react-libs/components'
import api from '@/lib/api'

// Module framework
import { navFor } from '@/modules/registry'
import { useModules } from '@/hooks/useModules'

// Core navigation (always available)
const coreNavigation: MenuCategory[] = [
  {
    items: [
      { name: 'Dashboard', href: '/', icon: LayoutDashboard },
      { name: 'Search', href: '/search', icon: SearchIcon },
      { name: 'Map', href: '/map', icon: MapIcon },
    ],
  },
]

// Admin navigation - shown based on user role
const adminNavigation: MenuItem[] = [
  { name: 'Audit Logs', href: '/admin/audit-logs', icon: FileText, roles: ['admin', 'support', 'tenant_admin'] },
  { name: 'Settings', href: '/admin/settings', icon: Settings, roles: ['admin'] },
  { name: 'SSO Config', href: '/admin/sso', icon: Shield, roles: ['admin', 'tenant_admin'] },
  { name: 'Module Toggles', href: '/admin/modules', icon: Zap, roles: ['admin'] },
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

  // Fetch enabled modules
  const { enabled: enabledModules } = useModules()

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

  // Build navigation: core + module nav + admin
  const allCategories: MenuCategory[] = useMemo(() => {
    const categories = [
      ...coreNavigation,
      ...navFor(enabledModules), // Module-gated nav
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
    return categories
  }, [enabledModules, visibleAdminNav])

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
        <main className="min-h-screen max-w-screen-2xl mx-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
