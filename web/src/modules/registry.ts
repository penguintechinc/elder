import type { RouteObject } from "react-router-dom";
import type {
  MenuCategory,
  MenuItem,
} from "@penguintechinc/react-libs/components";
import type { FrontendModule, ModuleCapabilities } from "./types";

// Import module manifests
import infrastructureModule from "./infrastructure";
import ipamModule from "./ipam";
import sbomModule from "./sbom";
import servicesOncallModule from "./services_oncall";
import issuesModule from "./issues";
import discoveryModule from "./discovery";
import secretsModule from "./secrets";
import webhooksAlertingModule from "./webhooks_alerting";
import accessReviewsModule from "./access_reviews";
import diagramsModule from "./diagrams";
import documentsModule from "./documents";
import pagesModule from "./pages";
import streamsModule from "./streams";
import runsModule from "./runs";
import aiModule from "./ai";

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
  runsModule,
  aiModule,
];

/**
 * Get routes for enabled modules.
 * @param enabledModuleIds Set of module IDs that are enabled (effective=true from backend)
 * @returns RouteObject[] ready to pass to <Routes>
 */
export function routesFor(enabledModuleIds: Set<string>): RouteObject[] {
  const routes: RouteObject[] = [];

  for (const module of MODULES) {
    if (enabledModuleIds.has(module.id)) {
      routes.push(...module.routes);
    }
  }

  return routes;
}

/**
 * Sidebar sections in WIRED order, with the Resources pillar split into
 * Infrastructure, Software, and Security sub-sections.
 */
const NAV_SECTIONS: [string, string][] = [
  ["workstreams", "Workstreams"],
  ["issues", "Work Management"],
  ["relationships", "Relationships"],
  ["resources-infrastructure", "Infrastructure"],
  ["resources-software", "Software"],
  ["resources-security", "Security"],
  ["documents", "Documents"],
];

/** Per-route sidebar section overrides so cross-cutting pages sit in their natural home. */
const HREF_SECTION_OVERRIDE: Record<string, string> = {
  "/dependencies": "relationships",
  "/on-call-rotations": "issues",
  "/vulnerabilities": "resources-security",
};

/** Maps each `entities`-group module to its Resources sub-section. */
const ENTITY_MODULE_SECTION: Record<string, string> = {
  infrastructure: "resources-infrastructure",
  ipam: "resources-infrastructure",
  sbom: "resources-software",
  services_oncall: "resources-software",
  ai: "resources-software",
  secrets: "resources-security",
};

function resolveNavSection(moduleId: string, moduleGroup: string, href: string): string {
  const override = HREF_SECTION_OVERRIDE[href];
  if (override) return override;
  if (moduleGroup === "entities") {
    return ENTITY_MODULE_SECTION[moduleId] ?? "resources-infrastructure";
  }
  return moduleGroup;
}

/**
 * Get navigation categories for enabled modules: one collapsible section per
 * sidebar group (with Resources split into Infrastructure, Software, and Security).
 *
 * Enforces global href deduplication across all sections so no route appears
 * twice in the sidebar, and omits empty sections.
 *
 * @param enabledModuleIds Set of module IDs that are enabled (effective=true)
 * @returns Populated MenuCategory sections in display order
 */
export function navFor(enabledModuleIds: Set<string>): MenuCategory[] {
  const buckets = new Map<string, MenuItem[]>(
    NAV_SECTIONS.map(([key]) => [key, []]),
  );
  const seenHrefs = new Set<string>();

  for (const module of MODULES) {
    if (!enabledModuleIds.has(module.id)) continue;
    for (const category of module.nav) {
      for (const item of category.items) {
        if (seenHrefs.has(item.href)) continue;
        seenHrefs.add(item.href);
        const sectionKey = resolveNavSection(module.id, module.group, item.href);
        const bucket = buckets.get(sectionKey);
        if (bucket) {
          bucket.push(item);
        }
      }
    }
  }

  const out: MenuCategory[] = [];
  for (const [key, label] of NAV_SECTIONS) {
    const items = buckets.get(key) ?? [];
    if (items.length) {
      out.push({
        header: label,
        key: `group-${key}`,
        collapsible: true,
        items,
      });
    }
  }

  return out;
}

/**
 * Get admin navigation items for enabled modules.
 * @param enabledModuleIds Set of module IDs that are enabled
 * @returns MenuItem[] with only admin nav from enabled modules
 */
export function adminNavFor(enabledModuleIds: Set<string>): MenuItem[] {
  const adminNavItems: MenuItem[] = [];

  for (const module of MODULES) {
    if (enabledModuleIds.has(module.id) && module.adminNav) {
      adminNavItems.push(...module.adminNav);
    }
  }

  return adminNavItems;
}

/**
 * Get merged capabilities map for enabled modules.
 * @param enabledModuleIds Set of module IDs that are enabled
 * @param capabilitiesMap Map of module ID to capabilities from backend
 * @returns Merged capabilities object
 */
export function capabilitiesFor(
  enabledModuleIds: Set<string>,
  capabilitiesMap: Map<string, ModuleCapabilities>,
): ModuleCapabilities {
  const merged: ModuleCapabilities = {};

  for (const moduleId of enabledModuleIds) {
    const caps = capabilitiesMap.get(moduleId);
    if (caps) {
      Object.assign(merged, caps);
    }
  }

  return merged;
}

/**
 * Find a module by ID.
 */
export function findModule(id: string): FrontendModule | undefined {
  return MODULES.find((m) => m.id === id);
}
