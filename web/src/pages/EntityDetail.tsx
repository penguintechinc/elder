import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Edit, Trash2, ArrowRight, Plus, X, Copy, MapPin } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import type { Entity, Dependency, DependencyType, Issue } from '@/types'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Select from '@/components/Select'
import { normalizeTags } from '@/lib/entityTags'

interface MetadataField {
  id: number
  key: string
  value: unknown
}

// Detail routes that actually exist for each dependency resource type — see
// web/src/modules/*/index.tsx. These append `/${resourceId}` to link
// directly to the specific resource.
const DEPENDENCY_DETAIL_ROUTES: Record<string, string> = {
  entity: '/entities',
  organization: '/organizations',
  issue: '/issues',
  project: '/projects',
}

// Resource types with no detail page — clicking navigates to the list page
// instead (no id appended).
const DEPENDENCY_LIST_ROUTES: Record<string, string> = {
  identity: '/iam',
  service: '/services',
  data_store: '/data-stores',
  software: '/software',
  networking: '/networking',
  milestone: '/milestones',
}

function formatResourceType(type: string): string {
  return type.charAt(0).toUpperCase() + type.slice(1)
}

export default function EntityDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showAddDependency, setShowAddDependency] = useState(false)

  const { data: entity, isLoading: entityLoading } = useQuery<Entity>({
    queryKey: ['entity', id],
    queryFn: () => api.getEntity(parseInt(id!)),
    enabled: !!id,
  })

  const { data: metadata } = useQuery({
    queryKey: ['entity-metadata', id],
    queryFn: () => api.getEntityMetadata(parseInt(id!)),
    enabled: !!id,
  })

  // Dependents: dependencies where this entity is the target.
  const { data: incomingDeps } = useQuery({
    queryKey: ['dependencies', 'incoming', id],
    queryFn: () => api.getDependencies({ target_type: 'entity', target_id: parseInt(id!) }),
    enabled: !!id,
  })

  // Depends On: dependencies where this entity is the source.
  const { data: outgoingDeps } = useQuery({
    queryKey: ['dependencies', 'outgoing', id],
    queryFn: () => api.getDependencies({ source_type: 'entity', source_id: parseInt(id!) }),
    enabled: !!id,
  })

  // Used to resolve a human-readable name for the *other* side of a
  // dependency when that side is itself an entity (the common case).
  // Shared queryKey with AddDependencyForm's own fetch below — TanStack
  // Query dedupes identical in-flight/cached queries automatically.
  const { data: allEntities } = useQuery({
    queryKey: ['entities-all'],
    queryFn: () => api.getEntities({ per_page: 1000 }),
  })

  const { data: organization } = useQuery({
    queryKey: ['organization', entity?.organization_id],
    queryFn: () => api.getOrganization(entity!.organization_id),
    enabled: !!entity?.organization_id,
  })

  const { data: issues } = useQuery({
    queryKey: ['issues', { entity_id: id }],
    queryFn: () => api.getIssues({ entity_id: parseInt(id!) }),
    enabled: !!id,
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteEntity(parseInt(id!)),
    onSuccess: () => {
      toast.success('Entity deleted successfully')
      navigate('/entities')
    },
    onError: () => {
      toast.error('Failed to delete entity')
    },
  })

  const deleteDependencyMutation = useMutation({
    mutationFn: (depId: number) => api.deleteDependency(depId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['dependencies'],
        refetchType: 'all'
      })
      toast.success('Dependency removed successfully')
    },
    onError: () => {
      toast.error('Failed to remove dependency')
    },
  })

  const handleDelete = () => {
    if (window.confirm(`Are you sure you want to delete "${entity?.name}"?`)) {
      deleteMutation.mutate()
    }
  }

  const handleDeleteDependency = (depId: number, depName: string) => {
    if (window.confirm(`Remove dependency: ${depName}?`)) {
      deleteDependencyMutation.mutate(depId)
    }
  }

  // Human-readable label for the *other* side of a dependency row. Real
  // entity names are resolved from allEntities; other resource types (which
  // the API never enriches with a name) fall back to "Type #id".
  const getDependencyLabel = (type: string, resourceId: number): string => {
    if (type === 'entity') {
      const match = allEntities?.items?.find((e: Entity) => e.id === resourceId)
      if (match) return match.name
    }
    return `${formatResourceType(type)} #${resourceId}`
  }

  const getDependencyPath = (type: string, resourceId: number): string | null => {
    const detailBase = DEPENDENCY_DETAIL_ROUTES[type]
    if (detailBase) return `${detailBase}/${resourceId}`
    const listPath = DEPENDENCY_LIST_ROUTES[type]
    if (listPath) return listPath
    return null
  }

  if (entityLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!entity) {
    return (
      <div className="p-8">
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">Entity not found</p>
            <Button className="mt-4" onClick={() => navigate('/entities')}>
              Back to Entities
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  const tagChips = normalizeTags(entity.tags)
  const location = entity.metadata?.location
  const locationLabel = location
    ? [location.city, location.state, location.country].filter(Boolean).join(', ')
    : undefined
  // Everything in entity.metadata except `location` (shown separately above)
  // — structural discovery data (namespace, capacity_cpu, images, etc).
  const discoveryMetadataEntries = Object.entries(entity.metadata || {}).filter(
    ([key]) => key !== 'location'
  )

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/entities')}
            className="p-2 hover:bg-slate-800 rounded-lg transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-slate-400" />
          </button>
          <div>
            <h1 className="text-3xl font-bold text-white">{entity.name}</h1>
            <p className="mt-1 text-slate-400">
              {entity.type?.replace('_', ' ').toUpperCase()}
            </p>
          </div>
        </div>
        <div className="flex gap-3">
          <Button
            variant="ghost"
            onClick={() => navigate(`/entities/${id}/edit`)}
          >
            <Edit className="w-4 h-4 mr-2" />
            Edit
          </Button>
          <Button
            variant="ghost"
            onClick={handleDelete}
            className="text-red-500 hover:text-red-400 hover:bg-red-500/10"
          >
            <Trash2 className="w-4 h-4 mr-2" />
            Delete
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Info */}
        <div className="lg:col-span-2 space-y-6">
          {/* Entity Info Card */}
          <Card>
            <CardHeader>
              <h2 className="text-xl font-semibold text-white">Information</h2>
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-1 gap-4">
                <div>
                  <dt className="text-sm font-medium text-slate-400">Name</dt>
                  <dd className="mt-1 text-sm text-white">{entity.name}</dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-slate-400">Type</dt>
                  <dd className="mt-1">
                    <span className="inline-block px-2 py-1 text-xs font-medium bg-primary-500/20 text-primary-400 rounded">
                      {entity.type?.replace('_', ' ').toUpperCase()}
                    </span>
                  </dd>
                </div>
                {entity.description && (
                  <div>
                    <dt className="text-sm font-medium text-slate-400">Description</dt>
                    <dd className="mt-1 text-sm text-white">{entity.description}</dd>
                  </div>
                )}
                <div>
                  <dt className="text-sm font-medium text-slate-400">Organization</dt>
                  <dd className="mt-1">
                    {organization ? (
                      <button
                        onClick={() => navigate(`/organizations/${organization.id}`)}
                        className="text-sm text-primary-400 hover:text-primary-300 transition-colors"
                      >
                        {organization.name}
                      </button>
                    ) : (
                      <span className="text-sm text-slate-400">None</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-slate-400">ID</dt>
                  <dd className="mt-1 text-sm text-white">{entity.id}</dd>
                </div>
                {entity.village_id && (
                  <div>
                    <dt className="text-sm font-medium text-slate-400">Village ID</dt>
                    <dd className="mt-1 flex items-center gap-2">
                      <a
                        href={`/id/${entity.village_id}`}
                        className="text-sm text-primary-400 hover:text-primary-300 font-mono"
                      >
                        {entity.village_id}
                      </a>
                      <button
                        onClick={() => {
                          navigator.clipboard.writeText(`${window.location.origin}/id/${entity.village_id}`)
                          toast.success('Village ID URL copied to clipboard')
                        }}
                        className="p-1 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                        title="Copy shareable link"
                      >
                        <Copy className="w-3.5 h-3.5" />
                      </button>
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="text-sm font-medium text-slate-400">Status</dt>
                  <dd className="mt-1">
                    <span
                      className={`inline-block px-2 py-1 text-xs font-medium rounded ${
                        entity.is_active
                          ? 'bg-green-500/20 text-green-400'
                          : 'bg-red-500/20 text-red-400'
                      }`}
                    >
                      {entity.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </dd>
                </div>
                {tagChips.length > 0 && (
                  <div>
                    <dt className="text-sm font-medium text-slate-400">Labels</dt>
                    <dd className="mt-1 flex flex-wrap gap-1.5">
                      {tagChips.map((chip) => (
                        <span
                          key={chip.key}
                          className="px-2 py-0.5 text-xs font-medium bg-slate-700 text-slate-200 rounded"
                        >
                          {chip.label}
                        </span>
                      ))}
                    </dd>
                  </div>
                )}
                {location && (
                  <div>
                    <dt className="text-sm font-medium text-slate-400">Location</dt>
                    <dd className="mt-1 flex items-start gap-1.5 text-sm text-white">
                      <MapPin className="w-3.5 h-3.5 mt-0.5 text-primary-400 flex-shrink-0" />
                      <span>
                        {locationLabel || 'Unknown'}
                        {(location.latitude !== undefined && location.longitude !== undefined) && (
                          <span className="block text-xs text-slate-400 mt-0.5">
                            {location.latitude}, {location.longitude}
                          </span>
                        )}
                      </span>
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="text-sm font-medium text-slate-400">Created</dt>
                  <dd className="mt-1 text-sm text-white">
                    {new Date(entity.created_at).toLocaleString()}
                  </dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-slate-400">Last Updated</dt>
                  <dd className="mt-1 text-sm text-white">
                    {new Date(entity.updated_at).toLocaleString()}
                  </dd>
                </div>
              </dl>
            </CardContent>
          </Card>

          {/* Metadata Card */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-white">Metadata</h2>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => navigate(`/entities/${id}/metadata`)}
                >
                  <Edit className="w-4 h-4 mr-2" />
                  Manage
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {discoveryMetadataEntries.length > 0 && (
                <>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                    Discovery Data
                  </h3>
                  <dl className="grid grid-cols-1 gap-4 mb-6">
                    {discoveryMetadataEntries.map(([key, value]) => (
                      <div key={key}>
                        <dt className="text-sm font-medium text-slate-400">{key}</dt>
                        <dd className="mt-1 text-sm text-white break-words">
                          {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </>
              )}
              {metadata?.items && metadata.items.length > 0 ? (
                <dl className="grid grid-cols-1 gap-4">
                  {metadata.items.map((field: MetadataField) => (
                    <div key={field.id}>
                      <dt className="text-sm font-medium text-slate-400">{field.key}</dt>
                      <dd className="mt-1 text-sm text-white">
                        {typeof field.value === 'object'
                          ? JSON.stringify(field.value)
                          : String(field.value)}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                discoveryMetadataEntries.length === 0 && (
                  <p className="text-sm text-slate-400">No metadata defined</p>
                )
              )}
            </CardContent>
          </Card>
        </div>

        {/* Dependencies Sidebar */}
        <div className="lg:col-span-1 space-y-6">
          {/* Outgoing Dependencies */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-white">Dependencies</h2>
                <button
                  onClick={() => setShowAddDependency(!showAddDependency)}
                  className="p-1 hover:bg-slate-700 rounded transition-colors"
                >
                  {showAddDependency ? (
                    <X className="w-4 h-4 text-slate-400" />
                  ) : (
                    <Plus className="w-4 h-4 text-slate-400" />
                  )}
                </button>
              </div>
            </CardHeader>
            <CardContent>
              {showAddDependency && (
                <div className="mb-4 p-3 bg-slate-800/50 rounded-lg">
                  <AddDependencyForm
                    sourceEntityId={parseInt(id!)}
                    onSuccess={async () => {
                      await queryClient.invalidateQueries({ queryKey: ['dependencies'], refetchType: 'all' })
                      setShowAddDependency(false)
                    }}
                  />
                </div>
              )}

              <div className="space-y-4">
                <div>
                  <h3 className="text-sm font-medium text-slate-400 mb-2">
                    Depends On ({outgoingDeps?.items?.length || 0})
                  </h3>
                  {outgoingDeps?.items && outgoingDeps.items.length > 0 ? (
                    <div className="space-y-2">
                      {outgoingDeps.items.map((dep: Dependency) => {
                        const label = getDependencyLabel(dep.target_type, dep.target_id)
                        const path = getDependencyPath(dep.target_type, dep.target_id)
                        return (
                          <div
                            key={dep.id}
                            className="flex items-center justify-between p-2 bg-slate-800/30 rounded"
                          >
                            <div className="flex items-center gap-2 flex-1 min-w-0">
                              <ArrowRight className="w-3 h-3 text-primary-500 flex-shrink-0" />
                              <div className="min-w-0 truncate">
                                {path ? (
                                  <button
                                    onClick={() => navigate(path)}
                                    className="text-sm text-white hover:text-primary-400 transition-colors truncate"
                                  >
                                    {label}
                                  </button>
                                ) : (
                                  <span className="text-sm text-white truncate">{label}</span>
                                )}
                                <span className="ml-2 text-xs text-slate-500">
                                  {dep.dependency_type}
                                </span>
                              </div>
                            </div>
                            <button
                              onClick={() => handleDeleteDependency(dep.id, label)}
                              className="p-1 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors flex-shrink-0"
                            >
                              <X className="w-3 h-3" />
                            </button>
                          </div>
                        )
                      })}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-500">No dependencies</p>
                  )}
                </div>

                <div>
                  <h3 className="text-sm font-medium text-slate-400 mb-2">
                    Dependents ({incomingDeps?.items?.length || 0})
                  </h3>
                  {incomingDeps?.items && incomingDeps.items.length > 0 ? (
                    <div className="space-y-2">
                      {incomingDeps.items.map((dep: Dependency) => {
                        const label = getDependencyLabel(dep.source_type, dep.source_id)
                        const path = getDependencyPath(dep.source_type, dep.source_id)
                        return (
                          <div
                            key={dep.id}
                            className="flex items-center gap-2 p-2 bg-slate-800/30 rounded"
                          >
                            <ArrowRight className="w-3 h-3 text-blue-500 flex-shrink-0 transform rotate-180" />
                            <div className="min-w-0 truncate">
                              {path ? (
                                <button
                                  onClick={() => navigate(path)}
                                  className="text-sm text-white hover:text-primary-400 transition-colors truncate"
                                >
                                  {label}
                                </button>
                              ) : (
                                <span className="text-sm text-white truncate">{label}</span>
                              )}
                              <span className="ml-2 text-xs text-slate-500">
                                {dep.dependency_type}
                              </span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-500">No dependents</p>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Issues Card */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-white">Issues</h2>
                <button
                  onClick={() => navigate(`/issues?entity_id=${id}`)}
                  className="text-sm text-primary-400 hover:text-primary-300 transition-colors"
                >
                  View All
                </button>
              </div>
            </CardHeader>
            <CardContent>
              {issues?.items && issues.items.length > 0 ? (
                <div className="space-y-2">
                  {issues.items.slice(0, 5).map((issue: Issue) => (
                    <div
                      key={issue.id}
                      className="p-2 bg-slate-800/30 rounded hover:bg-slate-800/50 cursor-pointer transition-colors"
                      onClick={() => navigate(`/issues/${issue.id}`)}
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex-1 min-w-0">
                          <h4 className="text-sm font-medium text-white truncate">
                            {issue.title}
                          </h4>
                          <div className="flex items-center gap-2 mt-1">
                            <span
                              className={`text-xs px-1.5 py-0.5 rounded ${
                                issue.status === 'open'
                                  ? 'bg-green-500/20 text-green-400'
                                  : issue.status === 'in_progress'
                                  ? 'bg-blue-500/20 text-blue-400'
                                  : 'bg-slate-500/20 text-slate-400'
                              }`}
                            >
                              {issue.status}
                            </span>
                            <span className="text-xs text-slate-500">
                              #{issue.id}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                  {issues.items.length > 5 && (
                    <p className="text-xs text-slate-500 text-center mt-2">
                      +{issues.items.length - 5} more
                    </p>
                  )}
                </div>
              ) : (
                <p className="text-sm text-slate-400">No issues</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

interface AddDependencyFormProps {
  sourceEntityId: number
  onSuccess: () => void
}

function AddDependencyForm({ sourceEntityId, onSuccess }: AddDependencyFormProps) {
  const [targetEntityId, setTargetEntityId] = useState<number | undefined>()
  const [dependencyType, setDependencyType] = useState<DependencyType>('depends')

  const { data: entities } = useQuery({
    queryKey: ['entities-all'],
    queryFn: () => api.getEntities({ per_page: 1000 }),
  })

  const createMutation = useMutation({
    mutationFn: (data: {
      source_type: string
      source_id: number
      target_type: string
      target_id: number
      dependency_type: string
    }) => api.createDependency(data),
    onSuccess: () => {
      toast.success('Dependency added successfully')
      onSuccess()
    },
    onError: () => {
      toast.error('Failed to add dependency')
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!targetEntityId) {
      toast.error('Please select a target entity')
      return
    }
    if (sourceEntityId === targetEntityId) {
      toast.error('Source and target must be different')
      return
    }
    createMutation.mutate({
      source_type: 'entity',
      source_id: sourceEntityId,
      target_type: 'entity',
      target_id: targetEntityId,
      dependency_type: dependencyType,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <Select
        value={targetEntityId?.toString() || ''}
        onChange={(e) => setTargetEntityId(parseInt(e.target.value))}
        required
      >
        <option value="">Select entity...</option>
        {entities?.items
          ?.filter((e: Entity) => e.id !== sourceEntityId)
          .map((entity: Entity) => (
            <option key={entity.id} value={entity.id}>
              {entity.name}
            </option>
          ))}
      </Select>

      <Select
        value={dependencyType}
        onChange={(e) => setDependencyType(e.target.value as DependencyType)}
        required
      >
        <option value="depends">Depends On</option>
        <option value="related">Related To</option>
        <option value="manages">Manages</option>
        <option value="other">Other</option>
      </Select>

      <Button type="submit" size="sm" className="w-full" isLoading={createMutation.isPending}>
        Add Dependency
      </Button>
    </form>
  )
}
