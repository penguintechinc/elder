import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useMemo, useEffect } from 'react'
import { AlertCircle, Zap, Lock } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Card, { CardContent } from '@/components/Card'
import { queryKeys } from '@/lib/queryKeys'

interface TenantModule {
  module_name: string
  enabled: boolean
  settings?: Record<string, unknown>
}

interface ModuleStatusInfo extends TenantModule {
  licensed?: boolean
  effective?: boolean
  title?: string
}

interface ModuleInfo {
  name?: string
  nav_id?: string
  title?: string
  licensed?: boolean
  effective?: boolean
}

/**
 * Admin page for managing per-tenant module toggles.
 * Lists all modules with current tenant-enabled state and shows licensed/effective badges.
 * Requires admin:write scope.
 */
export default function ModuleToggles() {
  const queryClient = useQueryClient()

  // Get current user profile to determine tenant and role
  const { data: userProfile } = useQuery({
    queryKey: ['portal-profile'],
    queryFn: () => api.getPortalProfile(),
    staleTime: 60000,
  })

  // Determine if user is admin
  const isAdmin = userProfile?.global_role === 'admin' || userProfile?.tenant_role === 'admin'

  // Get tenant ID - default to current user's tenant
  const [selectedTenantId, setSelectedTenantId] = useState<string | null>(null)

  // Update selected tenant when profile loads
  useEffect(() => {
    if (userProfile?.tenant_id) {
      setSelectedTenantId(String(userProfile.tenant_id))
    } else if (userProfile?.current_tenant) {
      setSelectedTenantId(String(userProfile.current_tenant))
    }
  }, [userProfile?.tenant_id, userProfile?.current_tenant])

  // Fetch tenant modules (only if we have a tenant ID)
  const { data: modulesData, isLoading: modulesLoading, error: modulesError } = useQuery({
    queryKey: queryKeys.modules.tenant(selectedTenantId || ''),
    queryFn: () => {
      if (!selectedTenantId) throw new Error('No tenant selected')
      return api.getTenantModules(selectedTenantId)
    },
    enabled: !!selectedTenantId && isAdmin,
    staleTime: 30000,
  })

  // Fetch module info (licensed/effective state)
  const { data: moduleInfoData } = useQuery({
    queryKey: ['modules'],
    queryFn: () => api.getModules(),
    enabled: isAdmin,
    staleTime: 60000,
  })

  // Combine module data: tenant-enabled + licensed/effective info
  const modules: ModuleStatusInfo[] = useMemo(() => {
    if (!modulesData?.data) return []

    const tenantModules = modulesData.data as TenantModule[]
    const allModules = moduleInfoData?.modules || []

    return tenantModules.map(tm => {
      const info = (allModules as ModuleInfo[]).find(
        m => m.name === tm.module_name || m.nav_id === tm.module_name
      )
      return {
        ...tm,
        title: info?.title || tm.module_name,
        licensed: info?.licensed ?? false,
        effective: info?.effective ?? false,
      }
    })
  }, [modulesData, moduleInfoData])

  // Toggle module mutation
  const { mutate: toggleModule, isPending: isToggling } = useMutation({
    mutationFn: async (moduleName: string) => {
      if (!selectedTenantId) throw new Error('No tenant selected')
      const currentModule = modules.find(m => m.module_name === moduleName)
      if (!currentModule) throw new Error('Module not found')

      console.log(`[ModuleToggles] Toggle { moduleName: "${moduleName}", enabled: ${!currentModule.enabled}, tenant: "${selectedTenantId}" }`)

      return api.setTenantModule(selectedTenantId, {
        module_name: moduleName,
        enabled: !currentModule.enabled,
      })
    },
    onSuccess: () => {
      if (!selectedTenantId) return
      // Invalidate both the tenant modules and general modules queries
      queryClient.invalidateQueries({ queryKey: queryKeys.modules.tenant(selectedTenantId) })
      queryClient.invalidateQueries({ queryKey: ['modules'] })
      toast.success('Module updated successfully')
    },
    onError: (error, moduleName) => {
      const err = error as {
        response?: { data?: { error?: string }; status?: number }
        message?: string
      }
      const message = err.response?.data?.error || err.message
      if (message === 'unknown_module') {
        toast.error('Unknown module name')
      } else if (err.response?.status === 403) {
        toast.error('Permission denied: only admins can toggle modules for other tenants')
      } else {
        toast.error(`Failed to update module: ${message}`)
      }
      console.error(`[ModuleToggles] Error { moduleName: "${moduleName}", status: ${err.response?.status} }`)
    },
  })

  if (!isAdmin) {
    return (
      <div className="p-6">
        <div className="rounded-lg bg-red-950/20 border border-red-400 p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <h2 className="text-red-200 font-semibold">Access Denied</h2>
            <p className="text-red-300 text-sm mt-1">Only administrators can manage module toggles.</p>
          </div>
        </div>
      </div>
    )
  }

  if (!selectedTenantId) {
    return (
      <div className="p-6">
        <div className="rounded-lg bg-amber-950/20 border border-amber-400 p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
          <div>
            <h2 className="text-amber-200 font-semibold">No Tenant Selected</h2>
            <p className="text-amber-300 text-sm mt-1">Please log in with a valid tenant to manage modules.</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-2">Module Management</h1>
        <p className="text-slate-400">Enable or disable modules for tenant {selectedTenantId}</p>
      </div>

      {modulesError && (
        <div className="mb-6 rounded-lg bg-red-950/20 border border-red-400 p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <h2 className="text-red-200 font-semibold">Failed to load modules</h2>
            <p className="text-red-300 text-sm mt-1">
              {modulesError instanceof Error ? modulesError.message : 'Unknown error'}
            </p>
          </div>
        </div>
      )}

      {modulesLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-amber-400" />
          <p className="text-slate-400 mt-4">Loading modules...</p>
        </div>
      ) : modules.length === 0 ? (
        <Card>
          <CardContent>
            <p className="text-slate-400 text-center py-8">No modules available for this tenant</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4">
          {modules.map((module) => (
            <Card key={module.module_name}>
              <CardContent className="py-4">
                <div className="flex items-center justify-between">
                  <div className="flex-1">
                    <h3 className="text-white font-semibold flex items-center gap-2">
                      {module.title || module.module_name}
                      {!module.licensed && (
                        <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-amber-900/30 border border-amber-600 text-amber-300 text-xs font-medium">
                          <Lock className="w-3 h-3" />
                          Not Licensed
                        </span>
                      )}
                      {module.licensed && !module.effective && (
                        <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-blue-900/30 border border-blue-600 text-blue-300 text-xs font-medium">
                          <Zap className="w-3 h-3" />
                          Inactive
                        </span>
                      )}
                      {module.effective && (
                        <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-green-900/30 border border-green-600 text-green-300 text-xs font-medium">
                          <Zap className="w-3 h-3" />
                          Effective
                        </span>
                      )}
                    </h3>
                    <p className="text-slate-400 text-sm mt-1">
                      {module.module_name}
                      {module.licensed && module.effective ? ' (licensed and active)' : ''}
                      {!module.licensed ? ' (unlicensed - enable only if you have a license)' : ''}
                    </p>
                  </div>
                  <button
                    onClick={() => toggleModule(module.module_name)}
                    disabled={isToggling || !module.licensed}
                    aria-label={`Toggle ${module.module_name}`}
                    data-testid={`toggle-${module.module_name}`}
                    className={`relative inline-flex h-8 w-14 items-center rounded-full transition-colors ${
                      module.enabled
                        ? 'bg-green-600 hover:bg-green-700'
                        : 'bg-slate-700 hover:bg-slate-600'
                    } ${isToggling || !module.licensed ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}
                  >
                    <span
                      className={`inline-block h-6 w-6 transform rounded-full bg-white transition-transform ${
                        module.enabled ? 'translate-x-7' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
