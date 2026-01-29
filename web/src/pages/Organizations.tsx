import { useState, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Plus, Search, Trash2, Edit } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { invalidateCache } from '@/lib/invalidateCache'
import { confirmDelete } from '@/lib/confirmActions'
import type { Organization } from '@/types'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

// Form fields for organization creation
const orgFields: FormField[] = [
  {
    name: 'name',
    label: 'Name',
    type: 'text',
    required: true,
    placeholder: 'Enter organization name'
  },
  {
    name: 'description',
    label: 'Description',
    type: 'textarea',
    placeholder: 'Enter description (optional)',
    rows: 3
  }
]

export default function Organizations() {
  const [search, setSearch] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingOrg, setEditingOrg] = useState<Organization | null>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const initialParentId = searchParams.get('parent_id')

  // Auto-open create modal if parent_id is in query params
  useEffect(() => {
    if (initialParentId) {
      setShowCreateModal(true)
    }
  }, [initialParentId])

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.organizations.list({ search }),
    queryFn: () => api.getOrganizations({ search }),
  })

  const createMutation = useMutation({
    mutationFn: (data: { name: string; description?: string; parent_id?: number }) =>
      api.createOrganization(data),
    onSuccess: async () => {
      await invalidateCache.organizations(queryClient)
      setShowCreateModal(false)
      toast.success('Organization created successfully')
      if (initialParentId) {
        navigate('/organizations', { replace: true })
      }
    },
    onError: (error) => {
      console.error('Create organization error:', error)
      toast.error('Failed to create organization')
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: { name: string; description?: string } }) =>
      api.updateOrganization(id, data),
    onSuccess: async () => {
      await invalidateCache.organizations(queryClient)
      setEditingOrg(null)
      toast.success('Organization updated successfully')
    },
    onError: () => {
      toast.error('Failed to update organization')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteOrganization(id),
    onSuccess: async () => {
      await invalidateCache.organizations(queryClient)
      toast.success('Organization deleted successfully')
    },
    onError: () => {
      toast.error('Failed to delete organization')
    },
  })

  const handleDelete = (id: number, name: string) => {
    confirmDelete(name, () => deleteMutation.mutate(id))
  }

  // Edit form fields with current values as defaults
  const editFields: FormField[] = useMemo(() => {
    if (!editingOrg) return orgFields
    return [
      {
        name: 'name',
        label: 'Name',
        type: 'text',
        required: true,
        placeholder: 'Enter organization name',
        defaultValue: editingOrg.name,
      },
      {
        name: 'description',
        label: 'Description',
        type: 'textarea',
        placeholder: 'Enter description (optional)',
        rows: 3,
        defaultValue: editingOrg.description || '',
      }
    ]
  }, [editingOrg])

  const handleCreate = (formData: Record<string, any>) => {
    const data: { name: string; description?: string; parent_id?: number } = {
      name: formData.name,
      description: formData.description || undefined,
    }
    if (initialParentId) {
      data.parent_id = parseInt(initialParentId)
    }
    createMutation.mutate(data)
  }

  const handleUpdate = (formData: Record<string, any>) => {
    if (!editingOrg) return
    updateMutation.mutate({
      id: editingOrg.id,
      data: {
        name: formData.name,
        description: formData.description || undefined,
      }
    })
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Organization Units</h1>
          <p className="mt-2 text-slate-400">
            Manage your organizational hierarchy
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Organization Unit
        </Button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search organizations..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
      </div>

      {/* Organizations List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">No organization units found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first organization unit
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {data?.items?.map((org: Organization) => (
            <Card key={org.id} className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all">
              <CardContent onClick={() => navigate(`/organizations/${org.id}`)}>
                <div className="flex items-start justify-between mb-3">
                  <h3 className="text-lg font-semibold text-white">{org.name}</h3>
                  <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => setEditingOrg(org)}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(org.id, org.name)}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                {org.description && (
                  <p className="text-sm text-slate-400 mb-4">{org.description}</p>
                )}
                <div className="flex items-center justify-between text-xs text-slate-500">
                  <span>ID: {org.id}</span>
                  <span>{new Date(org.created_at).toLocaleDateString()}</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <FormModalBuilder
          title="Create Organization Unit"
          fields={orgFields}
          isOpen={showCreateModal}
          onClose={() => {
            setShowCreateModal(false)
            if (initialParentId) {
              navigate('/organizations', { replace: true })
            }
          }}
          onSubmit={handleCreate}
          submitButtonText="Create"
        />
      )}

      {/* Edit Modal */}
      {editingOrg && (
        <FormModalBuilder
          title="Edit Organization Unit"
          fields={editFields}
          isOpen={!!editingOrg}
          onClose={() => setEditingOrg(null)}
          onSubmit={handleUpdate}
          submitButtonText="Update"
        />
      )}
    </div>
  )
}
