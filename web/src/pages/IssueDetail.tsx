import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Trash2, MessageSquare, Tag, User, Link as LinkIcon,
  Send, X, Plus, Copy, ListTree
} from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { getStatusColor, getPriorityColor } from '@/lib/colorHelpers'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
// Input component not currently used
import Select from '@/components/Select'
import { CreateIssueModal } from '@/pages/Issues'

type IssueStatus = 'open' | 'in_progress' | 'closed'
type IssuePriority = 'low' | 'medium' | 'high' | 'critical'

export default function IssueDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [newComment, setNewComment] = useState('')
  const [showAddLabel, setShowAddLabel] = useState(false)
  const [showLinkEntity, setShowLinkEntity] = useState(false)
  const [showAddProject, setShowAddProject] = useState(false)
  const [showAddMilestone, setShowAddMilestone] = useState(false)
  const [showCreateSubTask, setShowCreateSubTask] = useState(false)

  const { data: issue, isLoading } = useQuery({
    queryKey: ['issue', id],
    queryFn: () => api.getIssue(parseInt(id!)),
    enabled: !!id,
  })

  const { data: comments } = useQuery({
    queryKey: ['issue-comments', id],
    queryFn: () => api.getIssueComments(parseInt(id!)),
    enabled: !!id,
  })

  const { data: labels } = useQuery({
    queryKey: ['issue-labels', id],
    queryFn: () => api.getIssueLabels(parseInt(id!)),
    enabled: !!id,
  })

  const { data: linkedEntities } = useQuery({
    queryKey: ['issue-entities', id],
    queryFn: () => api.getIssueEntities(parseInt(id!)),
    enabled: !!id,
  })

  // Subtasks feature not yet implemented - using empty array for now
  const { data: subtasks } = useQuery({
    queryKey: ['issue-subtasks', id],
    queryFn: async () => [],
    enabled: !!id,
  })

  const { data: allLabels } = useQuery({
    queryKey: ['labels-all'],
    queryFn: () => api.getLabels({ per_page: 1000 }),
  })

  const { data: allEntities } = useQuery({
    queryKey: ['entities-all'],
    queryFn: () => api.getEntities({ per_page: 1000 }),
  })

  const { data: allIdentities } = useQuery({
    queryKey: ['identities-all'],
    queryFn: () => api.getIdentities({ per_page: 1000 }),
  })

  const { data: allProjects } = useQuery({
    queryKey: ['projects-all'],
    queryFn: () => api.getProjects({ per_page: 1000 }),
  })

  const { data: allMilestones } = useQuery({
    queryKey: ['milestones-all'],
    queryFn: () => api.getMilestones({ per_page: 1000 }),
  })

  const updateMutation = useMutation({
    mutationFn: (data: Partial<any>) => api.updateIssue(parseInt(id!), data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['issue', id],
        refetchType: 'all'
      })
      toast.success('Issue updated')
    },
    onError: () => {
      toast.error('Failed to update issue')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteIssue(parseInt(id!)),
    onSuccess: () => {
      toast.success('Issue deleted')
      navigate('/issues')
    },
    onError: () => {
      toast.error('Failed to delete issue')
    },
  })

  const addCommentMutation = useMutation({
    mutationFn: (body: string) => api.createIssueComment(parseInt(id!), { body }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['issue-comments', id],
        refetchType: 'all'
      })
      setNewComment('')
      toast.success('Comment added')
    },
    onError: () => {
      toast.error('Failed to add comment')
    },
  })

  const addLabelMutation = useMutation({
    mutationFn: (labelId: number) => api.addIssueLabel(parseInt(id!), labelId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['issue-labels', id],
        refetchType: 'all'
      })
      toast.success('Label added')
    },
    onError: () => {
      toast.error('Failed to add label')
    },
  })

  const removeLabelMutation = useMutation({
    mutationFn: (labelId: number) => api.removeIssueLabel(parseInt(id!), labelId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['issue-labels', id],
        refetchType: 'all'
      })
      toast.success('Label removed')
    },
    onError: () => {
      toast.error('Failed to remove label')
    },
  })

  const linkEntityMutation = useMutation({
    mutationFn: (entityId: number) => api.linkIssueEntity(parseInt(id!), entityId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['issue-entities', id],
        refetchType: 'all'
      })
      toast.success('Entity linked')
    },
    onError: () => {
      toast.error('Failed to link entity')
    },
  })

  const unlinkEntityMutation = useMutation({
    mutationFn: (entityId: number) => api.unlinkIssueEntity(parseInt(id!), entityId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['issue-entities', id],
        refetchType: 'all'
      })
      toast.success('Entity unlinked')
    },
    onError: () => {
      toast.error('Failed to unlink entity')
    },
  })

  const linkProjectMutation = useMutation({
    mutationFn: (projectId: number) => api.linkIssueToProject(parseInt(id!), projectId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['issue', id],
        refetchType: 'all'
      })
      setShowAddProject(false)
      toast.success('Project linked')
    },
    onError: () => {
      toast.error('Failed to link project')
    },
  })

  const unlinkProjectMutation = useMutation({
    mutationFn: (projectId: number) => api.unlinkIssueFromProject(parseInt(id!), projectId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['issue', id],
        refetchType: 'all'
      })
      toast.success('Project unlinked')
    },
    onError: () => {
      toast.error('Failed to unlink project')
    },
  })
  void unlinkProjectMutation  // Preserve for future use

  const linkMilestoneMutation = useMutation({
    mutationFn: (milestoneId: number) => api.linkIssueToMilestone(parseInt(id!), milestoneId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['issue', id],
        refetchType: 'all'
      })
      setShowAddMilestone(false)
      toast.success('Milestone linked')
    },
    onError: () => {
      toast.error('Failed to link milestone')
    },
  })

  const unlinkMilestoneMutation = useMutation({
    mutationFn: (milestoneId: number) => api.unlinkIssueFromMilestone(parseInt(id!), milestoneId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['issue', id],
        refetchType: 'all'
      })
      toast.success('Milestone unlinked')
    },
    onError: () => {
      toast.error('Failed to unlink milestone')
    },
  })
  void unlinkMilestoneMutation  // Preserve for future use

  const handleDelete = () => {
    if (window.confirm(`Delete issue "${issue?.title}"?`)) {
      deleteMutation.mutate()
    }
  }

  const handleAddComment = (e: React.FormEvent) => {
    e.preventDefault()
    if (newComment.trim()) {
      addCommentMutation.mutate(newComment.trim())
    }
  }

  const getStatusColor = (status: IssueStatus) => {
    switch (status) {
      case 'open':
        return 'bg-green-500/20 text-green-400'
      case 'in_progress':
        return 'bg-blue-500/20 text-blue-400'
      case 'closed':
        return 'bg-slate-500/20 text-slate-400'
    }
  }

  const getPriorityColor = (priority: IssuePriority) => {
    switch (priority) {
      case 'critical':
        return 'bg-red-500/20 text-red-400'
      case 'high':
        return 'bg-orange-500/20 text-orange-400'
      case 'medium':
        return 'bg-yellow-500/20 text-yellow-400'
      case 'low':
        return 'bg-slate-500/20 text-slate-400'
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!issue) {
    return (
      <div className="p-8">
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">Issue not found</p>
            <Button className="mt-4" onClick={() => navigate('/issues')}>
              Back to Issues
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
        <div className="flex items-center gap-4 flex-1 min-w-0">
          <button
            onClick={() => navigate('/issues')}
            className="p-2 hover:bg-slate-800 rounded-lg transition-colors flex-shrink-0"
          >
            <ArrowLeft className="w-5 h-5 text-slate-400" />
          </button>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-1">
              <MessageSquare className="w-6 h-6 text-primary-400 flex-shrink-0" />
              <h1 className="text-2xl font-bold text-white truncate">{issue.title}</h1>
            </div>
            <p className="text-slate-500 text-sm">Issue #{issue.id}</p>
          </div>
        </div>
        <div className="flex gap-3 flex-shrink-0">
          <Button variant="ghost" onClick={handleDelete}>
            <Trash2 className="w-4 h-4 mr-2" />
            Delete
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Content */}
        <div className="lg:col-span-2 space-y-6">
          {/* Issue Details */}
          <Card>
            <CardHeader>
              <h2 className="text-xl font-semibold text-white">Details</h2>
            </CardHeader>
            <CardContent>
              {issue.description ? (
                <p className="text-slate-300 whitespace-pre-wrap">{issue.description}</p>
              ) : (
                <p className="text-slate-500 italic">No description provided</p>
              )}
              <div className="flex flex-wrap gap-3 mt-6 pt-6 border-t border-slate-700">
                <span className={`text-sm px-3 py-1 rounded ${getStatusColor(issue.status)}`}>
                  {issue.status.replace('_', ' ')}
                </span>
                <span className={`text-sm px-3 py-1 rounded ${getPriorityColor(issue.priority)}`}>
                  {issue.priority}
                </span>
                <span className="text-sm text-slate-400">
                  Created {new Date(issue.created_at).toLocaleString()}
                </span>
              </div>
              {issue.village_id && (
                <div className="mt-4 pt-4 border-t border-slate-700">
                  <dt className="text-sm font-medium text-slate-400">Village ID</dt>
                  <dd className="mt-1 flex items-center gap-2">
                    <a
                      href={`/id/${issue.village_id}`}
                      className="text-sm text-primary-400 hover:text-primary-300 font-mono"
                    >
                      {issue.village_id}
                    </a>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(`${window.location.origin}/id/${issue.village_id}`)
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
            </CardContent>
          </Card>

          {/* Comments */}
          <Card>
            <CardHeader>
              <h2 className="text-xl font-semibold text-white flex items-center gap-2">
                <MessageSquare className="w-5 h-5" />
                Comments ({comments?.items?.length || 0})
              </h2>
            </CardHeader>
            <CardContent>
              <div className="space-y-4 mb-6">
                {comments?.items?.map((comment: any) => (
                  <div key={comment.id} className="bg-slate-800/30 p-4 rounded-lg">
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <User className="w-4 h-4 text-slate-400" />
                        <span className="text-sm font-medium text-white">
                          {comment.author?.full_name || comment.author?.username || 'Unknown'}
                        </span>
                      </div>
                      <span className="text-xs text-slate-500">
                        {new Date(comment.created_at).toLocaleString()}
                      </span>
                    </div>
                    <p className="text-slate-300 whitespace-pre-wrap">{comment.body}</p>
                  </div>
                ))}
                {comments?.items?.length === 0 && (
                  <p className="text-slate-500 text-center py-8">No comments yet</p>
                )}
              </div>

              <form onSubmit={handleAddComment}>
                <div className="relative">
                  <textarea
                    value={newComment}
                    onChange={(e) => setNewComment(e.target.value)}
                    placeholder="Add a comment..."
                    rows={3}
                    className="block w-full px-4 py-3 pr-12 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                  />
                  <button
                    type="submit"
                    disabled={!newComment.trim() || addCommentMutation.isPending}
                    className="absolute right-2 bottom-2 p-2 bg-primary-500 hover:bg-primary-600 disabled:bg-slate-700 disabled:cursor-not-allowed rounded transition-colors"
                  >
                    <Send className="w-4 h-4 text-white" />
                  </button>
                </div>
              </form>
            </CardContent>
          </Card>

          {/* Sub-Tasks */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-white flex items-center gap-2">
                  <ListTree className="w-5 h-5" />
                  Sub-Tasks ({subtasks?.items?.length || 0})
                </h2>
                <Button onClick={() => setShowCreateSubTask(true)} variant="ghost">
                  <Plus className="w-4 h-4 mr-2" />
                  Create Sub-Task
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {subtasks?.items?.length === 0 ? (
                <p className="text-slate-500 text-center py-8">No sub-tasks</p>
              ) : (
                <div className="space-y-3">
                  {subtasks?.items?.map((subtask: any) => (
                    <div
                      key={subtask.id}
                      className="p-4 bg-slate-800/30 rounded-lg cursor-pointer hover:bg-slate-800/50 transition-colors"
                      onClick={() => navigate(`/issues/${subtask.id}`)}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <h4 className="text-base font-semibold text-white mb-1">{subtask.title}</h4>
                          {subtask.description && (
                            <p className="text-sm text-slate-400 line-clamp-2 mb-2">{subtask.description}</p>
                          )}
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-xs text-slate-500">#{subtask.id}</span>
                            <span className={`text-xs px-2 py-0.5 rounded ${getStatusColor(subtask.status)}`}>
                              {subtask.status.replace('_', ' ')}
                            </span>
                            <span className={`text-xs px-2 py-0.5 rounded ${getPriorityColor(subtask.priority)}`}>
                              {subtask.priority}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Sidebar */}
        <div className="lg:col-span-1 space-y-6">
          {/* Status & Priority */}
          <Card>
            <CardHeader>
              <h3 className="text-lg font-semibold text-white">Status & Priority</h3>
            </CardHeader>
            <CardContent className="space-y-3">
              <Select
                label="Status"
                value={issue.status}
                onChange={(e) => updateMutation.mutate({ status: e.target.value })}
              >
                <option value="open">Open</option>
                <option value="in_progress">In Progress</option>
                <option value="closed">Closed</option>
              </Select>
              <Select
                label="Priority"
                value={issue.priority}
                onChange={(e) => updateMutation.mutate({ priority: e.target.value })}
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </Select>
            </CardContent>
          </Card>

          {/* Assignee */}
          <Card>
            <CardHeader>
              <h3 className="text-lg font-semibold text-white">Assignee</h3>
            </CardHeader>
            <CardContent>
              <Select
                value={issue.assignee_id || ''}
                onChange={(e) => updateMutation.mutate({ assignee_id: e.target.value ? parseInt(e.target.value) : null })}
              >
                <option value="">Unassigned</option>
                {allIdentities?.items?.map((identity: any) => (
                  <option key={identity.id} value={identity.id}>
                    {identity.full_name || identity.username}
                  </option>
                ))}
              </Select>
            </CardContent>
          </Card>

          {/* Labels */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                  <Tag className="w-5 h-5" />
                  Labels
                </h3>
                <button
                  onClick={() => setShowAddLabel(!showAddLabel)}
                  className="p-1 hover:bg-slate-700 rounded transition-colors"
                >
                  {showAddLabel ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
                </button>
              </div>
            </CardHeader>
            <CardContent>
              {showAddLabel && (
                <div className="mb-4 space-y-2 max-h-48 overflow-y-auto">
                  {allLabels?.items
                    ?.filter((label: any) => !labels?.items?.some((l: any) => l.id === label.id))
                    .map((label: any) => (
                      <button
                        key={label.id}
                        onClick={() => {
                          addLabelMutation.mutate(label.id)
                          setShowAddLabel(false)
                        }}
                        className="w-full text-left px-3 py-2 rounded hover:bg-slate-700 transition-colors"
                        style={{
                          backgroundColor: `${label.color}10`,
                          color: label.color
                        }}
                      >
                        {label.name}
                      </button>
                    ))}
                </div>
              )}
              <div className="space-y-2">
                {labels?.items?.map((label: any) => (
                  <div
                    key={label.id}
                    className="flex items-center justify-between px-3 py-2 rounded"
                    style={{
                      backgroundColor: `${label.color}20`,
                      color: label.color
                    }}
                  >
                    <span className="text-sm font-medium">{label.name}</span>
                    <button
                      onClick={() => removeLabelMutation.mutate(label.id)}
                      className="p-1 hover:bg-black/20 rounded transition-colors"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ))}
                {labels?.items?.length === 0 && !showAddLabel && (
                  <p className="text-slate-500 text-sm">No labels</p>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Linked Entities */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                  <LinkIcon className="w-5 h-5" />
                  Linked Entities
                </h3>
                <button
                  onClick={() => setShowLinkEntity(!showLinkEntity)}
                  className="p-1 hover:bg-slate-700 rounded transition-colors"
                >
                  {showLinkEntity ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
                </button>
              </div>
            </CardHeader>
            <CardContent>
              {showLinkEntity && (
                <div className="mb-4">
                  <Select
                    onChange={(e) => {
                      if (e.target.value) {
                        linkEntityMutation.mutate(parseInt(e.target.value))
                        setShowLinkEntity(false)
                      }
                    }}
                    value=""
                  >
                    <option value="">Select entity...</option>
                    {allEntities?.items
                      ?.filter((entity: any) => !linkedEntities?.items?.some((e: any) => e.id === entity.id))
                      .map((entity: any) => (
                        <option key={entity.id} value={entity.id}>
                          {entity.name} ({entity.entity_type})
                        </option>
                      ))}
                  </Select>
                </div>
              )}
              <div className="space-y-2">
                {linkedEntities?.items?.map((entity: any) => (
                  <div
                    key={entity.id}
                    className="flex items-center justify-between p-2 bg-slate-800/30 rounded cursor-pointer hover:bg-slate-800/50 transition-colors"
                    onClick={() => navigate(`/entities/${entity.id}`)}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white truncate">{entity.name}</p>
                      <p className="text-xs text-slate-500">{entity.entity_type}</p>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        unlinkEntityMutation.mutate(entity.id)
                      }}
                      className="p-1 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ))}
                {linkedEntities?.items?.length === 0 && !showLinkEntity && (
                  <p className="text-slate-500 text-sm">No linked entities</p>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Projects */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                  <LinkIcon className="w-5 h-5" />
                  Projects
                </h3>
                <button
                  onClick={() => setShowAddProject(!showAddProject)}
                  className="p-1 hover:bg-slate-700 rounded transition-colors"
                >
                  {showAddProject ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
                </button>
              </div>
            </CardHeader>
            <CardContent>
              {showAddProject && (
                <div className="mb-4">
                  <Select
                    onChange={(e) => {
                      if (e.target.value) {
                        linkProjectMutation.mutate(parseInt(e.target.value))
                      }
                    }}
                    value=""
                  >
                    <option value="">Select project...</option>
                    {allProjects?.items?.map((project: any) => (
                      <option key={project.id} value={project.id}>
                        {project.name}
                      </option>
                    ))}
                  </Select>
                </div>
              )}
              <div className="space-y-2">
                {/* Note: Projects would be stored in issue data from backend */}
                <p className="text-slate-500 text-sm">No linked projects</p>
              </div>
            </CardContent>
          </Card>

          {/* Milestones */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                  <LinkIcon className="w-5 h-5" />
                  Milestones
                </h3>
                <button
                  onClick={() => setShowAddMilestone(!showAddMilestone)}
                  className="p-1 hover:bg-slate-700 rounded transition-colors"
                >
                  {showAddMilestone ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
                </button>
              </div>
            </CardHeader>
            <CardContent>
              {showAddMilestone && (
                <div className="mb-4">
                  <Select
                    onChange={(e) => {
                      if (e.target.value) {
                        linkMilestoneMutation.mutate(parseInt(e.target.value))
                      }
                    }}
                    value=""
                  >
                    <option value="">Select milestone...</option>
                    {allMilestones?.items?.map((milestone: any) => (
                      <option key={milestone.id} value={milestone.id}>
                        {milestone.title}
                      </option>
                    ))}
                  </Select>
                </div>
              )}
              <div className="space-y-2">
                {/* Note: Milestones would be stored in issue data from backend */}
                <p className="text-slate-500 text-sm">No linked milestones</p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Create Sub-Task Modal */}
      {showCreateSubTask && (
        <CreateIssueModal
          onClose={() => setShowCreateSubTask(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ['issue-subtasks', id],
              refetchType: 'all'
            })
            setShowCreateSubTask(false)
          }}
          defaultOrganizationId={issue?.organization_id}
          parentIssueId={parseInt(id!)}
        />
      )}
    </div>
  )
}
