import { useQuery } from '@tanstack/react-query'
import api from '@/lib/api'
import type { ModulesResponse, ModuleCapabilities, ModuleInfo } from '@/modules/types'

/**
 * Hook to fetch module status and availability from the backend.
 * Returns enabled module IDs, loading state, error, and capabilities.
 *
 * Implements Phase 0 default: all modules effective=true (no toggle UI yet).
 * Caches for 60s; during loading, treats as empty set to avoid sidebar flicker.
 */
export function useModules() {
  const { data, isLoading, error } = useQuery<ModulesResponse | undefined>({
    queryKey: ['modules'],
    queryFn: () => api.getModules(),
    staleTime: 60 * 1000, // 60s cache
    retry: 1,
    throwOnError: false, // Don't throw; degrade gracefully
  })

  // Build set of enabled module IDs (effective=true).
  //
  // Match on BOTH the backend `name` (bare, e.g. "infrastructure") and `nav_id`
  // (prefixed, e.g. "nav_infrastructure"). The frontend module manifests are
  // inconsistent: older modules use a bare `id` ("infrastructure", "issues",
  // "secrets", ...) while newer ones use the nav_-prefixed form
  // ("nav_diagrams", ...). Keying only on `nav_id` (as before) silently dropped
  // every bare-id module — its routes and sidebar nav never registered — so
  // e.g. /organizations, /entities, /iam, /issues fell through to RouteNotFound.
  // Including both keys makes `enabled.has(module.id)` correct for either form.
  const enabledModuleIds = new Set<string>()
  data?.modules
    ?.filter((m: ModuleInfo) => m.effective)
    .forEach((m: ModuleInfo) => {
      if (m.nav_id) enabledModuleIds.add(m.nav_id)
      if (m.name) enabledModuleIds.add(m.name)
    })

  // Build capabilities map keyed on both name and nav_id, for the same reason.
  const capabilitiesMap = new Map<string, ModuleCapabilities>()
  data?.modules?.forEach((m: ModuleInfo) => {
    if (m.nav_id) capabilitiesMap.set(m.nav_id, m.capabilities)
    if (m.name) capabilitiesMap.set(m.name, m.capabilities)
  })

  return {
    enabled: enabledModuleIds,
    isLoading,
    error,
    capabilities: capabilitiesMap,
    modules: data?.modules ?? [],
  }
}
