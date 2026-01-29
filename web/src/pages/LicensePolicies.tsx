import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Edit, Trash2, Shield } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

const licensePolicyFields: FormField[] = [
  {
    name: 'name',
    label: 'Policy Name',
    type: 'text',
    required: true,
    placeholder: 'Enter policy name',
  },
  {
    name: 'description',
    label: 'Description',
    type: 'textarea',
    placeholder: 'Enter description (optional)',
    rows: 3,
  },
  {
    name: 'allowed_patterns',
    label: 'Allowed Patterns',
    type: 'textarea',
    placeholder: 'Enter allowed patterns (comma-separated or line-separated)',
    rows: 3,
  },
  {
    name: 'denied_patterns',
    label: 'Denied Patterns',
    type: 'textarea',
    placeholder: 'Enter denied patterns (comma-separated or line-separated)',
    rows: 3,
  },
  {
    name: 'is_active',
    label: 'Active',
    type: 'checkbox',
    defaultValue: true,
  },
]

export default function LicensePolicies() {
  const [search, setSearch] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingPolicy, setEditingPolicy] = useState<any>(null)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['license-policies', { search }],
    queryFn: () => api.getLicensePolicies({ search }),
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.createLicensePolicy(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['license-policies'],
        refetchType: 'all'
      })
      toast.success('License policy created successfully')
      setShowCreateModal(false)
    },
    onError: () => {
      toast.error('Failed to create license policy')
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: any }) =>
      api.updateLicensePolicy(id, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['license-policies'],
        refetchType: 'all'
      })
      toast.success('License policy updated successfully')
      setEditingPolicy(null)
    },
    onError: () => {
      toast.error('Failed to update license policy')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteLicensePolicy(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['license-policies'],
        refetchType: 'all'
      })
      toast.success('License policy deleted successfully')
    },
    onError: () => {
      toast.error('Failed to delete license policy')
    },
  })

  const handleDelete = (id: number, name: string) => {
    if (window.confirm(`Are you sure you want to delete policy "${name}"?`)) {
      deleteMutation.mutate(id)
    }
  }

  const handleCreate = (formData: Record<string, any>) => {
    createMutation.mutate(formData)
  }

  const handleUpdate = (formData: Record<string, any>) => {
    if (editingPolicy) {
      updateMutation.mutate({
        id: editingPolicy.id,
        data: formData
      })
    }
  }

  const filteredPolicies = data?.items?.filter((policy: any) => {
    if (!search) return true
    return policy.name.toLowerCase().includes(search.toLowerCase()) ||
           policy.description?.toLowerCase().includes(search.toLowerCase())
  })

  const editFields: FormField[] = useMemo(() => {
    if (!editingPolicy) return licensePolicyFields
    return licensePolicyFields.map((field) => ({
      ...field,
      defaultValue: field.name === 'name' ? editingPolicy.name :
                    field.name === 'description' ? (editingPolicy.description || '') :
                    field.name === 'allowed_patterns' ? (editingPolicy.allowed_patterns || '') :
                    field.name === 'denied_patterns' ? (editingPolicy.denied_patterns || '') :
                    field.name === 'is_active' ? (editingPolicy.is_active || false) :
                    field.defaultValue,
    }))
  }, [editingPolicy])

  const parsePatterns = (patterns: string): string[] => {
    if (!patterns) return []
    return patterns
      .split(/[,\n]/)
      .map(p => p.trim())
      .filter(p => p.length > 0)
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">License Policies</h1>
          <p className="mt-2 text-slate-400">
            Manage license policies and pattern matching rules
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Policy
        </Button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search policies..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
      </div>

      {/* Policies List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filteredPolicies?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">No license policies found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first policy
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {filteredPolicies?.map((policy: any) => (
            <Card key={policy.id}>
              <CardContent>
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2">
                      <Shield className="w-5 h-5 text-primary-500 flex-shrink-0" />
                      <h3 className="text-lg font-semibold text-white truncate">
                        {policy.name}
                      </h3>
                      {policy.is_active && (
                        <span className="ml-auto inline-block px-2 py-1 text-xs rounded bg-green-500/20 text-green-400 flex-shrink-0">
                          Active
                        </span>
                      )}
                    </div>
                    {policy.description && (
                      <p className="text-sm text-slate-400 mb-3">{policy.description}</p>
                    )}
                    <div className="grid grid-cols-2 gap-4 text-sm">
                      <div>
                        <p className="text-slate-500 font-medium mb-1">Allowed Patterns</p>
                        <div className="bg-slate-800 rounded p-2 max-h-24 overflow-y-auto">
                          {parsePatterns(policy.allowed_patterns).length > 0 ? (
                            <ul className="space-y-1">
                              {parsePatterns(policy.allowed_patterns).slice(0, 3).map((p: string, i: number) => (
                                <li key={i} className="text-slate-300 text-xs">• {p}</li>
                              ))}
                              {parsePatterns(policy.allowed_patterns).length > 3 && (
                                <li className="text-slate-400 text-xs">+{parsePatterns(policy.allowed_patterns).length - 3} more</li>
                              )}
                            </ul>
                          ) : (
                            <p className="text-slate-500 text-xs italic">None</p>
                          )}
                        </div>
                      </div>
                      <div>
                        <p className="text-slate-500 font-medium mb-1">Denied Patterns</p>
                        <div className="bg-slate-800 rounded p-2 max-h-24 overflow-y-auto">
                          {parsePatterns(policy.denied_patterns).length > 0 ? (
                            <ul className="space-y-1">
                              {parsePatterns(policy.denied_patterns).slice(0, 3).map((p: string, i: number) => (
                                <li key={i} className="text-slate-300 text-xs">• {p}</li>
                              ))}
                              {parsePatterns(policy.denied_patterns).length > 3 && (
                                <li className="text-slate-400 text-xs">+{parsePatterns(policy.denied_patterns).length - 3} more</li>
                              )}
                            </ul>
                          ) : (
                            <p className="text-slate-500 text-xs italic">None</p>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-2 flex-shrink-0 ml-4">
                    <button
                      onClick={() => setEditingPolicy(policy)}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => handleDelete(policy.id, policy.name)}
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

      {/* Create Modal */}
      {showCreateModal && (
        <FormModalBuilder
          isOpen={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          title="Create License Policy"
          fields={licensePolicyFields}
          onSubmit={handleCreate}
          submitButtonText="Create"
        />
      )}

      {/* Edit Modal */}
      {editingPolicy && (
        <FormModalBuilder
          isOpen={!!editingPolicy}
          onClose={() => setEditingPolicy(null)}
          title="Edit License Policy"
          fields={editFields}
          onSubmit={handleUpdate}
          submitButtonText="Update"
        />
      )}
    </div>
  )
}
