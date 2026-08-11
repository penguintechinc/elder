import type { RouteObject } from 'react-router-dom'
import type { MenuCategory, MenuItem } from '@penguintechinc/react-libs/components'

/**
 * Frontend module manifest: defines routes, navigation, and metadata for a feature module.
 * Modules are loaded lazily and gated by the /api/v1/modules backend endpoint.
 */
export interface FrontendModule {
  /** Unique module identifier (matches backend nav_id) */
  id: string
  /** Human-readable module name */
  name: string
  /** Group bucket: core|crm|workflow|kb (matches backend) */
  group: string
  /** Navigation categories to add to sidebar when module is enabled */
  nav: MenuCategory[]
  /** Optional admin navigation items to add to admin section when module is enabled */
  adminNav?: MenuItem[]
  /** Route objects (use React.lazy for page components) to register when module is enabled */
  routes: RouteObject[]
}

/**
 * Capabilities map returned by useModules hook.
 * Modules can report feature availability (e.g., semantic search, graph queries).
 */
export interface ModuleCapabilities {
  [key: string]: boolean
}

/**
 * Module info from backend /api/v1/modules endpoint.
 */
export interface ModuleInfo {
  name: string
  title: string
  installed: boolean
  licensed: boolean
  tenant_enabled: boolean
  effective: boolean
  nav_id: string
  group?: string
  capabilities: ModuleCapabilities
}

/**
 * Modules API response structure.
 */
export interface ModulesResponse {
  modules: ModuleInfo[]
}
