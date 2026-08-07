import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Plus, Search, MessageSquare, Tag, User, AlertTriangle } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { invalidateCache } from '@/lib/invalidateCache'
import { getStatusColor, getPriorityColor } from '@/lib/colorHelpers'
import { ISSUE_TYPES, issueTypeLabel } from '@/lib/constants/issueTypes'
import { Issue, IssueStatus, IssuePriority, IssueType, Organization, Entity, IssueLabel } from '@/types'
import Button from '@/components/Button'
import Card, { CardContent, CardHeader } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import AssigneePicker, { AssigneeValue } from '@/components/AssigneePicker'

export default function Issues() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<IssueStatus | ''>('')
  const [priorityFilter, setPriorityFilter] = useState<IssuePriority | ''>('')
  const [issueTypeFilter, setIssueTypeFilter] = useState<IssueType | ''>('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const organizationId = searchParams.get('organization_id')
  const entityId = searchParams.get('entity_id')

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.issues.list({ status: statusFilter, priority: priorityFilter, organizationId, entityId }),
    queryFn: () => api.getIssues({
      status: statusFilter || undefined,
      priority: priorityFilter || undefined,
      organization_id: organizationId ? parseInt(organizationId) : undefined,
      entity_id: entityId ? parseInt(entityId) : undefined,
    }),
  })

  // GET /issues has no server-side `search` or `issue_type` filter
  // (apps/api/modules/issues/routes/issues.py::list_issues only applies
  // status/priority/assignee_id/reporter_id) — filter client-side over the
  // fetched page so the search box and type filter actually narrow results.
  // Backend stores issue_type as UPPERCASE (SUPPORT, BUG, etc.); normalize to
  // lowercase for comparison against the constants (which are lowercase).
  const filteredIssues = useMemo(() => {
    const allItems: Issue[] = data?.items || []
    const q = search.trim().toLowerCase()
    return allItems.filter((issue) => {
      const matchesType = !issueTypeFilter || (issue.issue_type?.toLowerCase() === issueTypeFilter)
      const matchesSearch =
        !q ||
        issue.title.toLowerCase().includes(q) ||
        (issue.description || '').toLowerCase().includes(q)
      return matchesType && matchesSearch
    })
  }, [data, search, issueTypeFilter])

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
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
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
        <Select
          value={issueTypeFilter}
          onChange={(e) => setIssueTypeFilter(e.target.value as IssueType | '')}
          data-testid="issue-type-filter"
        >
          <option value="">All Types</option>
          {ISSUE_TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </Select>
      </div>

      {/* Issues List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filteredIssues.length === 0 ? (
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
          {filteredIssues.map((issue: Issue) => (
            <Card
              key={issue.id}
              className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all"
              onClick={() => navigate(`/issues/${issue.id}`)}
              data-testid={`issue-card-${issue.id}`}
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
                          <span className="text-xs px-2 py-0.5 rounded border border-slate-600 bg-slate-700/40 text-slate-300">
                            {issueTypeLabel(issue.issue_type)}
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

export function CreateIssueModal({
  onClose,
  onSuccess,
  defaultOrganizationId,
  defaultEntityId,
  parentIssueId,
}: CreateIssueModalProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<IssuePriority>('medium')
  const [issueType, setIssueType] = useState<IssueType>('other')
  const [organizationId, setOrganizationId] = useState(defaultOrganizationId ? String(defaultOrganizationId) : '')
  const [assignee, setAssignee] = useState<AssigneeValue | null>(null)
  const [entityIds, setEntityIds] = useState<number[]>(defaultEntityId ? [defaultEntityId] : [])
  const [labelIds, setLabelIds] = useState<number[]>([])
  const [isIncident, setIsIncident] = useState(false)
  const [channel, setChannel] = useState('')
  const [category, setCategory] = useState('')

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
    mutationFn: async (data: {
      title: string
      description?: string
      priority: string
      issue_type: string
      organization_id: number
      assignee_id?: number
      assignee_type?: 'identity' | 'org_unit'
      is_incident: number
      channel?: string
      category?: string
      parent_issue_id?: number
    }) => {
      console.log('[CreateIssueModal] Submit', {
        title: data.title,
        issueType: data.issue_type,
        organizationId: data.organization_id,
      })
      const issue = await api.createIssue(data)
      // CreateIssueRequest has no entity_ids/label_ids field (see Task 2
      // recon) — link each selection as a follow-up call against the
      // existing per-item endpoints, same as IssueDetail.tsx's sidebar.
      await Promise.all(entityIds.map((entityId) => api.linkIssueEntity(issue.id, entityId)))
      await Promise.all(labelIds.map((labelId) => api.addIssueLabel(issue.id, labelId)))
      return issue
    },
    onSuccess: () => {
      toast.success(parentIssueId ? 'Sub-task created successfully' : 'Issue created successfully')
      onSuccess()
    },
    onError: () => {
      toast.error(parentIssueId ? 'Failed to create sub-task' : 'Failed to create issue')
    },
  })

  const toggleEntity = (id: number) => {
    setEntityIds((prev) => (prev.includes(id) ? prev.filter((e) => e !== id) : [...prev, id]))
  }

  const toggleLabel = (id: number) => {
    setLabelIds((prev) => (prev.includes(id) ? prev.filter((l) => l !== id) : [...prev, id]))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim() || !organizationId) return
    createMutation.mutate({
      title: title.trim(),
      description: description.trim() || undefined,
      priority,
      issue_type: issueType,
      organization_id: parseInt(organizationId),
      assignee_id: assignee?.assignee_id,
      assignee_type: assignee?.assignee_type,
      is_incident: isIncident ? 1 : 0,
      channel: issueType === 'support' ? (channel.trim() || undefined) : undefined,
      category: issueType === 'support' ? (category.trim() || undefined) : undefined,
      parent_issue_id: parentIssueId,
    })
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">
            {parentIssueId ? 'Create Sub-Task' : 'Create Issue'}
          </h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4" data-testid="create-issue-form">
            <Input
              label="Title"
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Enter issue title"
              data-testid="issue-title-input"
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Enter description (optional)"
                rows={4}
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Select
                label="Priority"
                required
                value={priority}
                onChange={(e) => setPriority(e.target.value as IssuePriority)}
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </Select>
              <Select
                label="Issue Type"
                required
                value={issueType}
                onChange={(e) => setIssueType(e.target.value as IssueType)}
                data-testid="issue-type-select"
              >
                {ISSUE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </Select>
            </div>
            <Select
              label="Organization"
              required
              value={organizationId}
              onChange={(e) => setOrganizationId(e.target.value)}
              data-testid="issue-organization-select"
            >
              <option value="">Select organization</option>
              {organizations?.items?.map((org: Organization) => (
                <option key={org.id} value={org.id}>
                  {org.name}
                </option>
              ))}
            </Select>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Assignee</label>
              <AssigneePicker value={assignee} onChange={setAssignee} />
            </div>
            {issueType === 'support' && (
              <div
                className="grid grid-cols-2 gap-4 p-4 bg-slate-800/30 rounded-lg"
                data-testid="support-fields"
              >
                <Input
                  label="Channel"
                  value={channel}
                  onChange={(e) => setChannel(e.target.value)}
                  placeholder="email, chat, phone..."
                />
                <Input
                  label="Category"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  placeholder="billing, technical..."
                />
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Entities</label>
              <div className="max-h-32 overflow-y-auto space-y-1 border border-slate-700 rounded-lg p-2">
                {entities?.items?.map((entity: Entity) => (
                  <label key={entity.id} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={entityIds.includes(entity.id)}
                      onChange={() => toggleEntity(entity.id)}
                      className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                    />
                    {entity.name}
                  </label>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Labels</label>
              <div className="max-h-32 overflow-y-auto space-y-1 border border-slate-700 rounded-lg p-2">
                {labels?.items?.map((label: IssueLabel) => (
                  <label
                    key={label.id}
                    className="flex items-center gap-2 text-sm cursor-pointer"
                    style={{ color: label.color }}
                  >
                    <input
                      type="checkbox"
                      checked={labelIds.includes(label.id)}
                      onChange={() => toggleLabel(label.id)}
                      className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                    />
                    {label.name}
                  </label>
                ))}
              </div>
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={isIncident}
                onChange={(e) => setIsIncident(e.target.checked)}
                className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
              />
              <span className="text-sm text-slate-300">Mark as Incident</span>
            </label>
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" isLoading={createMutation.isPending} data-testid="submit-issue-button">
                {parentIssueId ? 'Create Sub-Task' : 'Create Issue'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
