import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Edit, Trash2, Tag } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

// Form fields for label creation/edit
const labelFields: FormField[] = [
  {
    name: 'name',
    label: 'Name',
    type: 'text',
    required: true,
    placeholder: 'Enter label name',
  },
  {
    name: 'description',
    label: 'Description',
    type: 'textarea',
    placeholder: 'Enter description (optional)',
    rows: 3,
  },
  {
    name: 'color',
    label: 'Color',
    type: 'text',
    defaultValue: '#3b82f6',
    placeholder: '#3b82f6',
    helpText: 'Enter a hex color code (e.g., #3b82f6)',
  },
]

export default function Labels() {
  const [search, setSearch] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingLabel, setEditingLabel] = useState<any>(null)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['labels', { search }],
    queryFn: () => api.getLabels({ search }),
  })

  const createMutation = useMutation({
    mutationFn: (data: { name: string; description?: string; color?: string }) =>
      api.createLabel(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['labels'],
        refetchType: 'all'
      })
      toast.success('Label created successfully')
      setShowCreateModal(false)
    },
    onError: () => {
      toast.error('Failed to create label')
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: { name: string; description?: string; color?: string } }) =>
      api.updateLabel(id, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['labels'],
        refetchType: 'all'
      })
      toast.success('Label updated successfully')
      setEditingLabel(null)
    },
    onError: () => {
      toast.error('Failed to update label')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteLabel(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['labels'],
        refetchType: 'all'
      })
      toast.success('Label deleted successfully')
    },
    onError: () => {
      toast.error('Failed to delete label')
    },
  })

  const handleDelete = (id: number, name: string) => {
    if (window.confirm(`Are you sure you want to delete label "${name}"?`)) {
      deleteMutation.mutate(id)
    }
  }

  const handleCreate = (formData: Record<string, any>) => {
    createMutation.mutate(formData as { name: string; description?: string; color?: string })
  }

  const handleUpdate = (formData: Record<string, any>) => {
    if (editingLabel) {
      updateMutation.mutate({
        id: editingLabel.id,
        data: formData as { name: string; description?: string; color?: string }
      })
    }
  }

  const filteredLabels = data?.items?.filter((label: any) => {
    if (!search) return true
    return label.name.toLowerCase().includes(search.toLowerCase()) ||
           label.description?.toLowerCase().includes(search.toLowerCase())
  })

  // Edit form fields with current values as defaults
  const editFields: FormField[] = useMemo(() => {
    if (!editingLabel) return labelFields
    return [
      {
        name: 'name',
        label: 'Name',
        type: 'text',
        required: true,
        placeholder: 'Enter label name',
        defaultValue: editingLabel.name,
      },
      {
        name: 'description',
        label: 'Description',
        type: 'textarea',
        placeholder: 'Enter description (optional)',
        rows: 3,
        defaultValue: editingLabel.description || '',
      },
      {
        name: 'color',
        label: 'Color',
        type: 'text',
        placeholder: '#3b82f6',
        helpText: 'Enter a hex color code (e.g., #3b82f6)',
        defaultValue: editingLabel.color || '#3b82f6',
      },
    ]
  }, [editingLabel])

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Labels</h1>
          <p className="mt-2 text-slate-400">
            Manage labels for organizations, entities, issues, and identities
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Label
        </Button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search labels..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
      </div>

      {/* Labels List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filteredLabels?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">No labels found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first label
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredLabels?.map((label: any) => (
            <Card key={label.id}>
              <CardContent>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <Tag
                      className="w-5 h-5 flex-shrink-0"
                      style={{ color: label.color || '#64748b' }}
                    />
                    <div className="flex-1 min-w-0">
                      <h3
                        className="text-lg font-semibold truncate"
                        style={{ color: label.color || '#ffffff' }}
                      >
                        {label.name}
                      </h3>
                    </div>
                  </div>
                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      onClick={() => setEditingLabel(label)}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(label.id, label.name)}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                {label.description && (
                  <p className="text-sm text-slate-400 mb-3">{label.description}</p>
                )}
                <div className="flex items-center justify-between text-xs text-slate-500">
                  <span
                    className="inline-block px-2 py-1 rounded"
                    style={{
                      backgroundColor: `${label.color || '#64748b'}20`,
                      color: label.color || '#64748b'
                    }}
                  >
                    {label.name}
                  </span>
                  <span>ID: {label.id}</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <FormModalBuilder
          title="Create Label"
          fields={labelFields}
          isOpen={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          onSubmit={handleCreate}
          submitButtonText="Create"
        />
      )}

      {/* Edit Modal */}
      {editingLabel && (
        <FormModalBuilder
          title="Edit Label"
          fields={editFields}
          isOpen={!!editingLabel}
          onClose={() => setEditingLabel(null)}
          onSubmit={handleUpdate}
          submitButtonText="Update"
        />
      )}
    </div>
  )
}
