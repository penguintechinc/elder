import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Plus, Search, MessageSquare, Tag, User, AlertTriangle } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { invalidateCache } from '@/lib/invalidateCache'
import { getStatusColor, getPriorityColor } from '@/lib/colorHelpers'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

type IssueStatus = 'open' | 'in_progress' | 'closed'
type IssuePriority = 'low' | 'medium' | 'high' | 'critical'

export default function Issues() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<IssueStatus | ''>('')
  const [priorityFilter, setPriorityFilter] = useState<IssuePriority | ''>('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const organizationId = searchParams.get('organization_id')
  const entityId = searchParams.get('entity_id')

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.issues.list({ search, status: statusFilter, priority: priorityFilter, organizationId, entityId }),
    queryFn: () => api.getIssues({
      search,
      status: statusFilter || undefined,
      priority: priorityFilter || undefined,
      organization_id: organizationId ? parseInt(organizationId) : undefined,
      entity_id: entityId ? parseInt(entityId) : undefined,
    }),
  })

  const updateStatusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: IssueStatus }) =>
      api.updateIssue(id, { status }),
    onSuccess: async () => {
      await invalidateCache.issues(queryClient)
      toast.success('Issue status updated')
    },
    onError: () => {
      toast.error('Failed to update issue status')
    },
  })


  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Issues</h1>
          <p className="mt-2 text-slate-400">
            Track and manage issues across your infrastructure
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Issue
        </Button>
      </div>

      {/* Filters */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search issues..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as IssueStatus | '')}
        >
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="closed">Closed</option>
        </Select>
        <Select
          value={priorityFilter}
          onChange={(e) => setPriorityFilter(e.target.value as IssuePriority | '')}
        >
          <option value="">All Priorities</option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="critical">Critical</option>
        </Select>
      </div>

      {/* Issues List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">No issues found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first issue
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {(data as any)?.items?.map((issue: any) => (
            <Card
              key={issue.id}
              className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all"
              onClick={() => navigate(`/issues/${issue.id}`)}
            >
              <CardContent>
                <div className="flex items-start gap-4">
                  {/* Issue Icon */}
                  <div className="flex-shrink-0 mt-1">
                    {issue.is_incident ? (
                      <AlertTriangle className="w-5 h-5 text-red-500" />
                    ) : (
                      <MessageSquare className="w-5 h-5 text-primary-400" />
                    )}
                  </div>

                  {/* Issue Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <h3 className="text-lg font-semibold text-white mb-1">
                          {issue.title}
                        </h3>
                        {issue.description && (
                          <p className="text-sm text-slate-400 line-clamp-2 mb-3">
                            {issue.description}
                          </p>
                        )}
                        <div className="flex flex-wrap items-center gap-3">
                          <span className="text-xs text-slate-500">#{issue.id}</span>
                          {issue.is_incident === 1 && (
                            <span className="text-xs px-2 py-0.5 rounded bg-red-500/20 text-red-400 border border-red-500/30 font-semibold">
                              INCIDENT
                            </span>
                          )}
                          <span className={`text-xs px-2 py-0.5 rounded border ${getStatusColor(issue.status)}`}>
                            {issue.status.replace('_', ' ')}
                          </span>
                          <span className={`text-xs px-2 py-0.5 rounded border ${getPriorityColor(issue.priority)}`}>
                            {issue.priority}
                          </span>
                          {issue.assignee_id && (
                            <span className="flex items-center gap-1 text-xs text-slate-400">
                              <User className="w-3 h-3" />
                              Assigned
                            </span>
                          )}
                          {issue.labels && issue.labels.length > 0 && (
                            <span className="flex items-center gap-1 text-xs text-slate-400">
                              <Tag className="w-3 h-3" />
                              {issue.labels.length} label(s)
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Quick Status Change */}
                      <div onClick={(e) => e.stopPropagation()}>
                        <Select
                          value={issue.status}
                          onChange={(e) =>
                            updateStatusMutation.mutate({
                              id: issue.id,
                              status: e.target.value as IssueStatus,
                            })
                          }
                          className="text-sm"
                        >
                          <option value="open">Open</option>
                          <option value="in_progress">In Progress</option>
                          <option value="closed">Closed</option>
                        </Select>
                      </div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <CreateIssueModal
          onClose={() => setShowCreateModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ['issues'],
              refetchType: 'all'
            })
            setShowCreateModal(false)
          }}
          defaultOrganizationId={organizationId ? parseInt(organizationId) : undefined}
          defaultEntityId={entityId ? parseInt(entityId) : undefined}
        />
      )}
    </div>
  )
}

export interface CreateIssueModalProps {
  onClose: () => void
  onSuccess: () => void
  defaultOrganizationId?: number
  defaultEntityId?: number
  parentIssueId?: number
}

export function CreateIssueModal({ onClose, onSuccess, defaultOrganizationId, defaultEntityId, parentIssueId }: CreateIssueModalProps) {
  const { data: organizations } = useQuery({
    queryKey: ['organizations-all'],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const { data: entities } = useQuery({
    queryKey: ['entities-all'],
    queryFn: () => api.getEntities({ per_page: 1000 }),
  })

  const { data: labels } = useQuery({
    queryKey: ['labels-all'],
    queryFn: () => api.getLabels({ per_page: 1000 }),
  })

  const createMutation = useMutation({
    mutationFn: (data: {
      title: string
      description?: string
      priority: string
      organization_id?: number
      entity_ids?: number[]
      label_ids?: number[]
      is_incident?: number
      parent_issue_id?: number
    }) => api.createIssue(data),
    onSuccess: () => {
      toast.success(parentIssueId ? 'Sub-task created successfully' : 'Issue created successfully')
      onSuccess()
    },
    onError: () => {
      toast.error(parentIssueId ? 'Failed to create sub-task' : 'Failed to create issue')
    },
  })

  // Build form fields dynamically based on available data
  const fields: FormField[] = useMemo(() => [
    {
      name: 'title',
      type: 'text' as const,
      label: 'Title',
      required: true,
      placeholder: 'Enter issue title',
    },
    {
      name: 'description',
      type: 'textarea' as const,
      label: 'Description',
      placeholder: 'Enter description (optional)',
      rows: 4,
    },
    {
      name: 'priority',
      type: 'select' as const,
      label: 'Priority',
      required: true,
      defaultValue: 'medium',
      options: [
        { value: 'low', label: 'Low' },
        { value: 'medium', label: 'Medium' },
        { value: 'high', label: 'High' },
        { value: 'critical', label: 'Critical' },
      ],
    },
    {
      name: 'assignment_type',
      type: 'radio' as const,
      label: 'Assign To',
      defaultValue: 'organization',
      options: [
        { value: 'organization', label: 'Organization' },
        { value: 'entity', label: 'Entity' },
      ],
    },
    {
      name: 'organization_id',
      type: 'select' as const,
      label: 'Organization',
      defaultValue: defaultOrganizationId?.toString() || '',
      options: [
        { value: '', label: 'None' },
        ...((organizations as any)?.items?.map((org: any) => ({
          value: org.id.toString(),
          label: org.name,
        })) || []),
      ],
      showWhen: (values: Record<string, any>) => values.assignment_type === 'organization',
    },
    {
      name: 'entity_ids',
      type: 'checkbox_multi' as const,
      label: 'Entities',
      helpText: 'Select one or more entities to assign this issue',
      options: (entities as any)?.items?.map((entity: any) => ({
        value: entity.id.toString(),
        label: entity.name,
      })) || [],
      showWhen: (values: Record<string, any>) => values.assignment_type === 'entity',
    },
    {
      name: 'label_ids',
      type: 'checkbox_multi' as const,
      label: 'Labels',
      helpText: 'Optionally select labels to categorize this issue',
      options: (labels as any)?.items?.map((label: any) => ({
        value: label.id.toString(),
        label: label.name,
      })) || [],
    },
    {
      name: 'is_incident',
      type: 'checkbox' as const,
      label: 'Mark as Incident',
      defaultValue: false,
    },
  ], [organizations, entities, labels, defaultOrganizationId, defaultEntityId])

  const handleSubmit = async (data: Record<string, any>) => {
    // Convert form data to API format
    const apiData: any = {
      title: data.title,
      description: data.description || undefined,
      priority: data.priority,
      is_incident: data.is_incident ? 1 : 0,
    }

    // Handle assignment
    if (data.assignment_type === 'organization' && data.organization_id) {
      apiData.organization_id = parseInt(data.organization_id)
    } else if (data.assignment_type === 'entity' && data.entity_ids?.length > 0) {
      apiData.entity_ids = data.entity_ids.map((id: string) => parseInt(id))
    }

    // Handle labels
    if (data.label_ids?.length > 0) {
      apiData.label_ids = data.label_ids.map((id: string) => parseInt(id))
    }

    // Handle parent issue for sub-tasks
    if (parentIssueId) {
      apiData.parent_issue_id = parentIssueId
    }

    createMutation.mutate(apiData)
  }

  return (
    <FormModalBuilder
      title={parentIssueId ? 'Create Sub-Task' : 'Create Issue'}
      fields={fields}
      isOpen={true}
      onClose={onClose}
      onSubmit={handleSubmit}
      submitButtonText={parentIssueId ? 'Create Sub-Task' : 'Create Issue'}
      cancelButtonText="Cancel"
    />
  )
}
