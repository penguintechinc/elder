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
import diagramsModule from './diagrams'
import documentsModule from './documents'
import pagesModule from './pages'
import streamsModule from './streams'

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
  diagramsModule,
  documentsModule,
  pagesModule,
  streamsModule,
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

/** The five WIRED pillars, in acronym order — this is the sidebar's top level. */
const GROUP_ORDER: [string, string][] = [
  ['workstreams', 'Workstreams'],
  ['issues', 'Issues'],
  ['relationships', 'Relationships'],
  ['entities', 'Entities'],
  ['documents', 'Documents'],
]

/**
 * Get navigation categories for enabled modules: exactly one collapsible section
 * per WIRED pillar, in acronym order, holding every enabled module's nav items.
 *
 * Modules each author their own `nav` categories, and those headers collide across
 * modules — `sbom` and `services_oncall` both ship a "Software & Services", and a
 * single-item module renders its name twice (header + item). Emitting one section
 * per pillar removes both classes of duplicate by construction. It also has to be
 * this shape: `MenuCategory` is flat, and `SidebarMenu` drops any category whose
 * visible items are empty, so a header-only pillar row renders as nothing at all.
 *
 * Module sub-headers are intentionally discarded; the items themselves are still
 * contributed by module manifests and gated per tenant by the caller's enabled set.
 *
 * @param enabledModuleIds Set of module IDs that are enabled (effective=true)
 * @returns One MenuCategory per populated pillar; empty pillars are omitted
 */
export function navFor(enabledModuleIds: Set<string>): MenuCategory[] {
  const out: MenuCategory[] = []

  for (const [key, label] of GROUP_ORDER) {
    const items: MenuItem[] = []
    const seenHrefs = new Set<string>()

    for (const module of MODULES) {
      if (module.group !== key || !enabledModuleIds.has(module.id)) continue
      for (const category of module.nav) {
        for (const item of category.items) {
          // Two modules can surface the same route; keep the first registration.
          if (seenHrefs.has(item.href)) continue
          seenHrefs.add(item.href)
          items.push(item)
        }
      }
    }

    if (items.length) {
      out.push({ header: label, key: `group-${key}`, collapsible: true, items })
    }
  }

  return out
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
