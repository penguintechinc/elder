import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Edit, Trash2, FolderKanban, Calendar } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { getStatusColor } from '@/lib/colorHelpers'
import { confirmDelete } from '@/lib/confirmActions'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

const PROJECT_STATUSES = [
  { value: 'active', label: 'Active' },
  { value: 'completed', label: 'Completed' },
  { value: 'archived', label: 'Archived' },
  { value: 'on_hold', label: 'On Hold' },
]

export default function Projects() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingProject, setEditingProject] = useState<any>(null)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['projects', { search, status: statusFilter }],
    queryFn: () => api.getProjects({ search, status: statusFilter || undefined }),
  })

  const { data: organizations, isLoading: orgsLoading } = useQuery({
    queryKey: ['organizations-all'],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteProject(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['projects'],
        refetchType: 'all'
      })
      toast.success('Project deleted successfully')
    },
    onError: () => {
      toast.error('Failed to delete project')
    },
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.createProject(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['projects'],
        refetchType: 'all'
      })
      toast.success('Project created successfully')
      setShowCreateModal(false)
    },
    onError: () => {
      toast.error('Failed to create project')
    },
  })

  const updateMutation = useMutation({
    mutationFn: (data: any) => api.updateProject(editingProject.id, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['projects'],
        refetchType: 'all'
      })
      toast.success('Project updated successfully')
      setEditingProject(null)
    },
    onError: () => {
      toast.error('Failed to update project')
    },
  })

  const handleDelete = (id: number, name: string) => {
    confirmDelete(`project "${name}"`, () => {
      deleteMutation.mutate(id)
    })
  }

  const handleCreate = (data: Record<string, any>) => {
    createMutation.mutate({
      ...data,
      organization_id: parseInt(data.organization_id),
      start_date: data.start_date || undefined,
      end_date: data.end_date || undefined,
    })
  }

  const handleUpdate = (data: Record<string, any>) => {
    updateMutation.mutate({
      ...data,
      organization_id: parseInt(data.organization_id),
      start_date: data.start_date || undefined,
      end_date: data.end_date || undefined,
    })
  }

  // Build organization options
  const orgOptions = useMemo(() => {
    if (orgsLoading) {
      return [{ value: '', label: 'Loading...' }]
    }
    if (!organizations?.items?.length) {
      return [{ value: '', label: 'No organizations found - create one first' }]
    }
    return organizations.items.map((org: any) => ({
      value: org.id.toString(),
      label: org.name,
    }))
  }, [organizations, orgsLoading])

  // Form fields for project creation
  const projectFields: FormField[] = useMemo(() => [
    {
      name: 'name',
      label: 'Name',
      type: 'text',
      required: true,
      placeholder: 'Q1 2024 Infrastructure Upgrade',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'Project description (optional)',
      rows: 3,
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      options: orgOptions,
    },
    {
      name: 'status',
      label: 'Status',
      type: 'select',
      defaultValue: 'active',
      options: PROJECT_STATUSES,
    },
    {
      name: 'start_date',
      label: 'Start Date',
      type: 'date',
    },
    {
      name: 'end_date',
      label: 'End Date',
      type: 'date',
    },
  ], [orgOptions])

  // Edit form fields with current values as defaults
  const editFields: FormField[] = useMemo(() => {
    if (!editingProject) return projectFields
    return [
      {
        name: 'name',
        label: 'Name',
        type: 'text',
        required: true,
        placeholder: 'Q1 2024 Infrastructure Upgrade',
        defaultValue: editingProject.name,
      },
      {
        name: 'description',
        label: 'Description',
        type: 'textarea',
        placeholder: 'Project description (optional)',
        rows: 3,
        defaultValue: editingProject.description || '',
      },
      {
        name: 'organization_id',
        label: 'Organization',
        type: 'select',
        required: true,
        options: orgOptions,
        defaultValue: editingProject.organization_id?.toString() || '',
      },
      {
        name: 'status',
        label: 'Status',
        type: 'select',
        options: PROJECT_STATUSES,
        defaultValue: editingProject.status,
      },
      {
        name: 'start_date',
        label: 'Start Date',
        type: 'date',
        defaultValue: editingProject.start_date || '',
      },
      {
        name: 'end_date',
        label: 'End Date',
        type: 'date',
        defaultValue: editingProject.end_date || '',
      },
    ]
  }, [editingProject, orgOptions, projectFields])

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Projects</h1>
          <p className="mt-2 text-slate-400">
            Manage projects and their milestones
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Project
        </Button>
      </div>

      {/* Filters */}
      <div className="flex gap-4 mb-6">
        <div className="flex-1 max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search projects..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Statuses</option>
          {PROJECT_STATUSES.map((status) => (
            <option key={status.value} value={status.value}>
              {status.label}
            </option>
          ))}
        </Select>
      </div>

      {/* Projects List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <FolderKanban className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No projects found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first project
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data?.items?.map((project: any) => (
            <Card key={project.id} className="cursor-pointer hover:border-primary-500/50 transition-colors" onClick={() => navigate(`/projects/${project.id}`)}>
              <CardContent>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <FolderKanban className="w-5 h-5 text-primary-400 flex-shrink-0" />
                    <h3 className="text-lg font-semibold text-white truncate">
                      {project.name}
                    </h3>
                  </div>
                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setEditingProject(project)
                      }}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDelete(project.id, project.name)
                      }}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {project.description && (
                  <p className="text-sm text-slate-400 mb-3 line-clamp-2">
                    {project.description}
                  </p>
                )}

                <div className="flex items-center justify-between mb-3">
                  <span className={`text-xs px-2 py-1 rounded ${getStatusColor(project.status)}`}>
                    {project.status.replace('_', ' ')}
                  </span>
                  {project.start_date && (
                    <div className="flex items-center gap-1 text-xs text-slate-500">
                      <Calendar className="w-3 h-3" />
                      {new Date(project.start_date).toLocaleDateString()}
                    </div>
                  )}
                </div>

                {project.end_date && (
                  <div className="text-xs text-slate-500">
                    Due: {new Date(project.end_date).toLocaleDateString()}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <FormModalBuilder
          title="Create Project"
          fields={projectFields}
          isOpen={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          onSubmit={handleCreate}
          submitButtonText="Create"
        />
      )}

      {/* Edit Modal */}
      {editingProject && (
        <FormModalBuilder
          title="Edit Project"
          fields={editFields}
          isOpen={!!editingProject}
          onClose={() => setEditingProject(null)}
          onSubmit={handleUpdate}
          submitButtonText="Update"
        />
      )}
    </div>
  )
}
