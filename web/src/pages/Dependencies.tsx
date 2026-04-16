import { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Trash2, ArrowRight, X } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import SearchableSelect from '@/components/SearchableSelect'

const RESOURCE_TYPES = [
  { value: 'entity', label: 'Entity' },
  { value: 'identity', label: 'Identity' },
  { value: 'project', label: 'Project' },
  { value: 'milestone', label: 'Milestone' },
  { value: 'issue', label: 'Issue' },
  { value: 'organization', label: 'Organization' },
]

const DEPENDENCY_TYPE_OPTIONS = [
  { value: 'depends', label: 'Depends On' },
  { value: 'calls', label: 'Calls' },
  { value: 'related', label: 'Related To' },
  { value: 'affects', label: 'Affects' },
  { value: 'manages', label: 'Manages' },
  { value: 'owns', label: 'Owns' },
  { value: 'contains', label: 'Contains' },
  { value: 'connects', label: 'Connects To' },
  { value: 'other', label: 'Other' },
]

interface PolymorphicDependency {
  id: number
  tenant_id: number
  source_type: string
  source_id: number
  target_type: string
  target_id: number
  dependency_type: string
  metadata?: Record<string, any>
  created_at: string
  updated_at: string
}

// Fetch resources by type with optional search
function useResourceSearch(type: string, searchQuery: string) {
  return useQuery({
    queryKey: ['resource-search', type, searchQuery],
    queryFn: () => {
      const params: any = { per_page: 50 }
      if (searchQuery) params.search = searchQuery
      switch (type) {
        case 'entity': return api.getEntities(params)
        case 'identity': return api.getIdentities(params)
        case 'project': return api.getProjects(params)
        case 'milestone': return api.getMilestones(params)
        case 'issue': return api.getIssues(params)
        case 'organization': return api.getOrganizations(params)
        default: return Promise.resolve({ items: [] })
      }
    },
    enabled: !!type,
  })
}

function formatResourceOptions(type: string, items: any[]) {
  return (items || []).map((item: any) => {
    switch (type) {
      case 'entity':
        return { value: item.id, label: `${item.name}${item.type ? ` (${item.type})` : ''}` }
      case 'identity':
        return { value: item.id, label: `${item.username}${item.identity_type ? ` (${item.identity_type})` : ''}` }
      case 'project':
        return { value: item.id, label: `${item.name}${item.status ? ` (${item.status})` : ''}` }
      case 'milestone':
        return { value: item.id, label: `${item.title}${item.status ? ` (${item.status})` : ''}` }
      case 'issue':
        return { value: item.id, label: `${item.title}${item.status ? ` (${item.status})` : ''}` }
      case 'organization':
        return { value: item.id, label: `${item.name}${item.organization_type ? ` (${item.organization_type})` : ''}` }
      default:
        return { value: item.id, label: `#${item.id}` }
    }
  })
}

export default function Dependencies() {
  const [search, setSearch] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [sourceTypeFilter, setSourceTypeFilter] = useState<string>('')
  const [targetTypeFilter, setTargetTypeFilter] = useState<string>('')
  const queryClient = useQueryClient()

  const { data: dependencies, isLoading } = useQuery({
    queryKey: ['dependencies', { sourceTypeFilter, targetTypeFilter }],
    queryFn: () =>
      api.getDependencies({
        source_type: sourceTypeFilter || undefined,
        target_type: targetTypeFilter || undefined,
      }),
  })

  // Small fetches just for display name resolution on existing deps
  const { data: entities } = useQuery({
    queryKey: ['entities-all'],
    queryFn: () => api.getEntities({ per_page: 1000 }),
  })
  const { data: identities } = useQuery({
    queryKey: ['identities-all'],
    queryFn: () => api.getIdentities({ per_page: 1000 }),
  })
  const { data: projects } = useQuery({
    queryKey: ['projects-all'],
    queryFn: () => api.getProjects({ per_page: 1000 }),
  })
  const { data: milestones } = useQuery({
    queryKey: ['milestones-all'],
    queryFn: () => api.getMilestones({ per_page: 1000 }),
  })
  const { data: issues } = useQuery({
    queryKey: ['issues-all'],
    queryFn: () => api.getIssues({ per_page: 1000 }),
  })
  const { data: organizations } = useQuery({
    queryKey: ['organizations-all'],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const getResourceName = (type: string, id: number): string => {
    switch (type) {
      case 'entity':
        return entities?.items?.find((e: any) => e.id === id)?.name || `Entity #${id}`
      case 'identity':
        return identities?.items?.find((i: any) => i.id === id)?.username || `Identity #${id}`
      case 'project':
        return projects?.items?.find((p: any) => p.id === id)?.name || `Project #${id}`
      case 'milestone':
        return milestones?.items?.find((m: any) => m.id === id)?.title || `Milestone #${id}`
      case 'issue':
        return issues?.items?.find((i: any) => i.id === id)?.title || `Issue #${id}`
      case 'organization':
        return organizations?.items?.find((o: any) => o.id === id)?.name || `Organization #${id}`
      default:
        return `${type} #${id}`
    }
  }

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteDependency(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['dependencies'],
        refetchType: 'all',
      })
      toast.success('Dependency deleted successfully')
    },
    onError: () => {
      toast.error('Failed to delete dependency')
    },
  })

  const handleDelete = (id: number, sourceName: string, targetName: string) => {
    if (window.confirm(`Delete dependency: ${sourceName} → ${targetName}?`)) {
      deleteMutation.mutate(id)
    }
  }

  const filteredDependencies = dependencies?.items?.filter((dep: PolymorphicDependency) => {
    if (!search) return true
    const sourceName = getResourceName(dep.source_type, dep.source_id).toLowerCase()
    const targetName = getResourceName(dep.target_type, dep.target_id).toLowerCase()
    const searchLower = search.toLowerCase()
    return (
      sourceName.includes(searchLower) ||
      targetName.includes(searchLower) ||
      dep.dependency_type.toLowerCase().includes(searchLower)
    )
  })

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Dependencies</h1>
          <p className="mt-2 text-slate-400">
            Manage relationships between all resource types
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Dependency
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search dependencies..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
        <Select
          value={sourceTypeFilter}
          onChange={(e) => setSourceTypeFilter(e.target.value)}
        >
          <option value="">All Source Types</option>
          {RESOURCE_TYPES.map((type) => (
            <option key={type.value} value={type.value}>
              {type.label}
            </option>
          ))}
        </Select>
        <Select
          value={targetTypeFilter}
          onChange={(e) => setTargetTypeFilter(e.target.value)}
        >
          <option value="">All Target Types</option>
          {RESOURCE_TYPES.map((type) => (
            <option key={type.value} value={type.value}>
              {type.label}
            </option>
          ))}
        </Select>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filteredDependencies?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">No dependencies found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first dependency
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {filteredDependencies?.map((dep: PolymorphicDependency) => (
            <Card key={dep.id}>
              <CardContent>
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-4 flex-1">
                    <div className="flex-1">
                      <div className="text-sm text-slate-400">Source</div>
                      <div className="text-white font-medium">
                        {getResourceName(dep.source_type, dep.source_id)}
                      </div>
                      <div className="text-xs text-slate-500 capitalize">
                        {dep.source_type}
                      </div>
                    </div>
                    <div className="flex flex-col items-center">
                      <ArrowRight className="w-6 h-6 text-primary-500" />
                      <div className="text-xs text-slate-400 mt-1">
                        {dep.dependency_type}
                      </div>
                    </div>
                    <div className="flex-1">
                      <div className="text-sm text-slate-400">Target</div>
                      <div className="text-white font-medium">
                        {getResourceName(dep.target_type, dep.target_id)}
                      </div>
                      <div className="text-xs text-slate-500 capitalize">
                        {dep.target_type}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-4">
                    <div className="text-xs text-slate-500">
                      {new Date(dep.created_at).toLocaleDateString()}
                    </div>
                    <button
                      onClick={() =>
                        handleDelete(
                          dep.id,
                          getResourceName(dep.source_type, dep.source_id),
                          getResourceName(dep.target_type, dep.target_id)
                        )
                      }
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {showCreateModal && (
        <CreateDependencyModal
          onClose={() => setShowCreateModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ['dependencies'],
              refetchType: 'all',
            })
            setShowCreateModal(false)
          }}
        />
      )}
    </div>
  )
}

function CreateDependencyModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void
  onSuccess: () => void
}) {
  const [sourceType, setSourceType] = useState('entity')
  const [sourceId, setSourceId] = useState<number | string>('')
  const [sourceSearch, setSourceSearch] = useState('')
  const [targetType, setTargetType] = useState('entity')
  const [targetId, setTargetId] = useState<number | string>('')
  const [targetSearch, setTargetSearch] = useState('')
  const [dependencyType, setDependencyType] = useState('depends')

  const { data: sourceData, isLoading: sourceLoading } = useResourceSearch(sourceType, sourceSearch)
  const { data: targetData, isLoading: targetLoading } = useResourceSearch(targetType, targetSearch)

  const sourceOptions = formatResourceOptions(sourceType, sourceData?.items || [])
  const targetOptions = formatResourceOptions(targetType, targetData?.items || [])

  const handleSourceSearch = useCallback((q: string) => setSourceSearch(q), [])
  const handleTargetSearch = useCallback((q: string) => setTargetSearch(q), [])

  const createMutation = useMutation({
    mutationFn: (data: {
      source_type: string
      source_id: number
      target_type: string
      target_id: number
      dependency_type: string
    }) => api.createDependency(data),
    onSuccess: () => {
      toast.success('Dependency created successfully')
      onSuccess()
    },
    onError: () => {
      toast.error('Failed to create dependency')
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const sId = Number(sourceId)
    const tId = Number(targetId)
    if (!sId || !tId) {
      toast.error('Please select both source and target resources')
      return
    }
    if (sourceType === targetType && sId === tId) {
      toast.error('Source and target cannot be the same resource')
      return
    }
    createMutation.mutate({
      source_type: sourceType,
      source_id: sId,
      target_type: targetType,
      target_id: tId,
      dependency_type: dependencyType,
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/60" onClick={onClose} />
      <div className="relative z-10 w-full max-w-lg rounded-xl border border-slate-700 bg-slate-800 p-6 shadow-2xl">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold text-white">Create Dependency</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Source */}
          <div className="space-y-3">
            <h3 className="text-sm font-medium text-slate-300">Source</h3>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Type</label>
              <Select
                value={sourceType}
                onChange={(e) => {
                  setSourceType(e.target.value)
                  setSourceId('')
                  setSourceSearch('')
                }}
              >
                {RESOURCE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Resource</label>
              <SearchableSelect
                options={sourceOptions}
                value={sourceId}
                onChange={(val) => setSourceId(val)}
                onSearch={handleSourceSearch}
                isLoading={sourceLoading}
                placeholder={`Search ${RESOURCE_TYPES.find(t => t.value === sourceType)?.label || ''}...`}
              />
            </div>
          </div>

          {/* Relationship Type */}
          <div>
            <label className="block text-xs text-slate-400 mb-1">Relationship Type</label>
            <Select
              value={dependencyType}
              onChange={(e) => setDependencyType(e.target.value)}
            >
              {DEPENDENCY_TYPE_OPTIONS.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </Select>
          </div>

          {/* Target */}
          <div className="space-y-3">
            <h3 className="text-sm font-medium text-slate-300">Target</h3>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Type</label>
              <Select
                value={targetType}
                onChange={(e) => {
                  setTargetType(e.target.value)
                  setTargetId('')
                  setTargetSearch('')
                }}
              >
                {RESOURCE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">Resource</label>
              <SearchableSelect
                options={targetOptions}
                value={targetId}
                onChange={(val) => setTargetId(val)}
                onSearch={handleTargetSearch}
                isLoading={targetLoading}
                placeholder={`Search ${RESOURCE_TYPES.find(t => t.value === targetType)?.label || ''}...`}
              />
            </div>
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Creating...' : 'Create'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
