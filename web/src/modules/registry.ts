import type { RouteObject } from 'react-router-dom'
import type { MenuCategory, MenuItem } from '@penguintechinc/react-libs/components'
import type { FrontendModule, ModuleCapabilities } from './types'

// Import module manifests
import infrastructureModule from './infrastructure'
import ipamModule from './ipam'
import sbomModule from './sbom'
import servicesOncallModule from './services_oncall'
import issuesModule from './issues'
import discoveryModule from './discovery'
import secretsModule from './secrets'
import webhooksAlertingModule from './webhooks_alerting'
import accessReviewsModule from './access_reviews'
import helpdeskModule from './helpdesk'
import diagramsModule from './diagrams'
import documentsModule from './documents'
import pagesModule from './pages'

/**
 * Module registry: all available feature modules.
 * Order matters for nav sidebar display.
 */
export const MODULES: FrontendModule[] = [
  infrastructureModule,
  ipamModule,
  sbomModule,
  servicesOncallModule,
  issuesModule,
  discoveryModule,
  secretsModule,
  webhooksAlertingModule,
  accessReviewsModule,
  helpdeskModule,
  diagramsModule,
  documentsModule,
  pagesModule,
]

/**
 * Get routes for enabled modules.
 * @param enabledModuleIds Set of module IDs that are enabled (effective=true from backend)
 * @returns RouteObject[] ready to pass to <Routes>
 */
export function routesFor(enabledModuleIds: Set<string>): RouteObject[] {
  const routes: RouteObject[] = []

  for (const module of MODULES) {
    if (enabledModuleIds.has(module.id)) {
      routes.push(...module.routes)
    }
  }

  return routes
}

/**
 * Get navigation categories for enabled modules.
 * @param enabledModuleIds Set of module IDs that are enabled
 * @returns MenuCategory[] with only nav from enabled modules
 */
export function navFor(enabledModuleIds: Set<string>): MenuCategory[] {
  const navCategories: MenuCategory[] = []

  for (const module of MODULES) {
    if (enabledModuleIds.has(module.id)) {
      navCategories.push(...module.nav)
    }
  }

  return navCategories
}

/**
 * Get admin navigation items for enabled modules.
 * @param enabledModuleIds Set of module IDs that are enabled
 * @returns MenuItem[] with only admin nav from enabled modules
 */
export function adminNavFor(enabledModuleIds: Set<string>): MenuItem[] {
  const adminNavItems: MenuItem[] = []

  for (const module of MODULES) {
    if (enabledModuleIds.has(module.id) && module.adminNav) {
      adminNavItems.push(...module.adminNav)
    }
  }

  return adminNavItems
}

/**
 * Get merged capabilities map for enabled modules.
 * @param enabledModuleIds Set of module IDs that are enabled
 * @param capabilitiesMap Map of module ID to capabilities from backend
 * @returns Merged capabilities object
 */
export function capabilitiesFor(
  enabledModuleIds: Set<string>,
  capabilitiesMap: Map<string, ModuleCapabilities>
): ModuleCapabilities {
  const merged: ModuleCapabilities = {}

  for (const moduleId of enabledModuleIds) {
    const caps = capabilitiesMap.get(moduleId)
    if (caps) {
      Object.assign(merged, caps)
    }
  }

  return merged
}

/**
 * Find a module by ID.
 */
export function findModule(id: string): FrontendModule | undefined {
  return MODULES.find(m => m.id === id)
}
