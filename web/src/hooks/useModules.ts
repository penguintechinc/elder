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

  // Build set of enabled module IDs (effective=true)
  const enabledModuleIds: Set<string> = new Set(
    data?.modules
      ?.filter((m: ModuleInfo) => m.effective)
      .map((m: ModuleInfo) => m.nav_id) ?? []
  )

  // Build capabilities map: module nav_id -> capabilities
  const capabilitiesMap = new Map<string, ModuleCapabilities>()
  data?.modules?.forEach((m: ModuleInfo) => {
    capabilitiesMap.set(m.nav_id, m.capabilities)
  })

  return {
    enabled: enabledModuleIds,
    isLoading,
    error,
    capabilities: capabilitiesMap,
    modules: data?.modules ?? [],
  }
}
