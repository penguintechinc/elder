import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Edit, Trash2, ArrowRight, Plus, X, Copy } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import type { Entity, Dependency, DependencyType } from '@/types'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Select from '@/components/Select'

export default function EntityDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showAddDependency, setShowAddDependency] = useState(false)

  const { data: entity, isLoading: entityLoading } = useQuery({
    queryKey: ['entity', id],
    queryFn: () => api.getEntity(parseInt(id!)),
    enabled: !!id,
  })

  const { data: metadata } = useQuery({
    queryKey: ['entity-metadata', id],
    queryFn: () => api.getEntityMetadata(parseInt(id!)),
    enabled: !!id,
  })

  const { data: incomingDeps } = useQuery({
    queryKey: ['dependencies', { target_entity_id: id }],
    queryFn: () => api.getDependencies({ target_entity_id: parseInt(id!) }),
    enabled: !!id,
  })

  const { data: outgoingDeps } = useQuery({
    queryKey: ['dependencies', { source_entity_id: id }],
    queryFn: () => api.getDependencies({ source_entity_id: parseInt(id!) }),
    enabled: !!id,
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
              {metadata?.items && metadata.items.length > 0 ? (
                <dl className="grid grid-cols-1 gap-4">
                  {metadata.items.map((field: any) => (
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
                <p className="text-sm text-slate-400">No metadata defined</p>
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
                      {outgoingDeps.items.map((dep: Dependency) => (
                        <div
                          key={dep.id}
                          className="flex items-center justify-between p-2 bg-slate-800/30 rounded"
                        >
                          <div className="flex items-center gap-2 flex-1 min-w-0">
                            <ArrowRight className="w-3 h-3 text-primary-500 flex-shrink-0" />
                            <button
                              onClick={() => navigate(`/entities/${dep.target_entity_id}`)}
                              className="text-sm text-white hover:text-primary-400 transition-colors truncate"
                            >
                              {dep.target_entity?.name || `Entity #${dep.target_entity_id}`}
                            </button>
                          </div>
                          <button
                            onClick={() =>
                              handleDeleteDependency(
                                dep.id,
                                dep.target_entity?.name || `Entity #${dep.target_entity_id}`
                              )
                            }
                            className="p-1 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors flex-shrink-0"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </div>
                      ))}
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
                      {incomingDeps.items.map((dep: Dependency) => (
                        <div
                          key={dep.id}
                          className="flex items-center gap-2 p-2 bg-slate-800/30 rounded"
                        >
                          <ArrowRight className="w-3 h-3 text-blue-500 flex-shrink-0 transform rotate-180" />
                          <button
                            onClick={() => navigate(`/entities/${dep.source_entity_id}`)}
                            className="text-sm text-white hover:text-primary-400 transition-colors truncate"
                          >
                            {dep.source_entity?.name || `Entity #${dep.source_entity_id}`}
                          </button>
                        </div>
                      ))}
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
                  {issues.items.slice(0, 5).map((issue: any) => (
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
      source_entity_id: number
      target_entity_id: number
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
      source_entity_id: sourceEntityId,
      target_entity_id: targetEntityId,
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
        <option value="depends_on">Depends On</option>
        <option value="related_to">Related To</option>
        <option value="part_of">Part Of</option>
      </Select>

      <Button type="submit" size="sm" className="w-full" isLoading={createMutation.isPending}>
        Add Dependency
      </Button>
    </form>
  )
}
