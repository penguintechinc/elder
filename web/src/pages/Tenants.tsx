import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Plus, Search, Building2, Users, Trash2, Edit, Eye } from 'lucide-react'
import api from '@/lib/api'
import Button from '@/components/Button'
import Input from '@/components/Input'
import Card, { CardContent } from '@/components/Card'
import { FormModalBuilder, FormField } from '@penguintechinc/react-libs/components'
import type { Tenant } from '@/types'

// Form fields for tenant creation
const tenantFields: FormField[] = [
  {
    name: 'name',
    label: 'Name',
    type: 'text',
    required: true,
    placeholder: 'Tenant name',
  },
  {
    name: 'slug',
    label: 'Slug',
    type: 'text',
    required: true,
    placeholder: 'tenant-slug',
    helpText: 'Lowercase letters, numbers, and hyphens only',
  },
  {
    name: 'domain',
    label: 'Domain (optional)',
    type: 'text',
    placeholder: 'tenant.example.com',
  },
  {
    name: 'subscription_tier',
    label: 'Subscription Tier',
    type: 'select',
    options: [
      { value: 'community', label: 'Community' },
      { value: 'professional', label: 'Professional' },
      { value: 'enterprise', label: 'Enterprise' },
    ],
    defaultValue: 'community',
  },
  {
    name: 'data_retention_days',
    label: 'Data Retention (days)',
    type: 'number',
    defaultValue: 90,
  },
  {
    name: 'storage_quota_gb',
    label: 'Storage Quota (GB)',
    type: 'number',
    defaultValue: 10,
  },
]

export default function Tenants() {
  const [search, setSearch] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingTenant, setEditingTenant] = useState<Tenant | null>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: tenants, isLoading } = useQuery({
    queryKey: ['tenants'],
    queryFn: () => api.getTenants(),
  })

  const createMutation = useMutation({
    mutationFn: (data: Record<string, any>) => api.createTenant(data as Parameters<typeof api.createTenant>[0]),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tenants'] })
      toast.success('Tenant created successfully')
      setShowCreateModal(false)
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to create tenant')
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Record<string, any> }) =>
      api.updateTenant(id, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tenants'] })
      toast.success('Tenant updated successfully')
      setEditingTenant(null)
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to update tenant')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteTenant(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tenants'] })
      toast.success('Tenant deactivated successfully')
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to deactivate tenant')
    },
  })

  const handleCreate = (data: Record<string, any>) => {
    createMutation.mutate(data)
  }

  const handleUpdate = (data: Record<string, any>) => {
    if (editingTenant) {
      updateMutation.mutate({
        id: editingTenant.id,
        data,
      })
    }
  }

  const handleEdit = (tenant: Tenant) => {
    setEditingTenant(tenant)
  }

  const handleDelete = (tenant: Tenant) => {
    if (tenant.id === 1) {
      toast.error('Cannot deactivate system tenant')
      return
    }
    if (confirm(`Are you sure you want to deactivate "${tenant.name}"?`)) {
      deleteMutation.mutate(tenant.id)
    }
  }

  const filteredTenants = tenants?.filter((t: Tenant) =>
    t.name.toLowerCase().includes(search.toLowerCase()) ||
    t.slug.toLowerCase().includes(search.toLowerCase())
  ) || []

  // Edit form fields with current values as defaults
  const editFields: FormField[] = useMemo(() => {
    if (!editingTenant) return tenantFields
    return [
      {
        name: 'name',
        label: 'Name',
        type: 'text',
        required: true,
        placeholder: 'Tenant name',
        defaultValue: editingTenant.name,
      },
      {
        name: 'slug',
        label: 'Slug',
        type: 'text',
        required: true,
        placeholder: 'tenant-slug',
        helpText: 'Lowercase letters, numbers, and hyphens only',
        defaultValue: editingTenant.slug,
      },
      {
        name: 'domain',
        label: 'Domain (optional)',
        type: 'text',
        placeholder: 'tenant.example.com',
        defaultValue: editingTenant.domain || '',
      },
      {
        name: 'subscription_tier',
        label: 'Subscription Tier',
        type: 'select',
        options: [
          { value: 'community', label: 'Community' },
          { value: 'professional', label: 'Professional' },
          { value: 'enterprise', label: 'Enterprise' },
        ],
        defaultValue: editingTenant.subscription_tier,
      },
      {
        name: 'data_retention_days',
        label: 'Data Retention (days)',
        type: 'number',
        defaultValue: editingTenant.data_retention_days,
      },
      {
        name: 'storage_quota_gb',
        label: 'Storage Quota (GB)',
        type: 'number',
        defaultValue: editingTenant.storage_quota_gb,
      },
    ]
  }, [editingTenant])

  const getTierColor = (tier: string) => {
    switch (tier) {
      case 'enterprise': return 'bg-yellow-500/20 text-yellow-400'
      case 'professional': return 'bg-blue-500/20 text-blue-400'
      default: return 'bg-gray-500/20 text-gray-400'
    }
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-2">Tenant Management</h1>
        <p className="text-slate-400">Manage multi-tenant organizations and their configurations</p>
      </div>

      <div className="flex justify-between items-center mb-6">
        <div className="relative w-64">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-slate-400" />
          <Input
            type="text"
            placeholder="Search tenants..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Tenant
        </Button>
      </div>

      {isLoading ? (
        <div className="text-center py-8 text-slate-400">Loading tenants...</div>
      ) : filteredTenants.length === 0 ? (
        <Card>
          <CardContent className="text-center py-8">
            <Building2 className="w-12 h-12 text-slate-500 mx-auto mb-4" />
            <p className="text-slate-400">No tenants found</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4">
          {filteredTenants.map((tenant: Tenant) => (
            <Card key={tenant.id} className="hover:border-slate-600 transition-colors">
              <CardContent className="p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 bg-slate-700 rounded-lg flex items-center justify-center">
                      <Building2 className="w-5 h-5 text-slate-400" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="font-semibold text-white">{tenant.name}</h3>
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${getTierColor(tenant.subscription_tier)}`}>
                          {tenant.subscription_tier}
                        </span>
                        {!tenant.is_active && (
                          <span className="px-2 py-0.5 rounded text-xs font-medium bg-red-500/20 text-red-400">
                            Inactive
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-slate-400">
                        {tenant.slug} {tenant.domain && `• ${tenant.domain}`}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    {tenant.usage && (
                      <div className="flex items-center gap-4 text-sm text-slate-400">
                        <span className="flex items-center gap-1">
                          <Users className="w-4 h-4" />
                          {tenant.usage.portal_users} users
                        </span>
                        <span>{tenant.usage.organizations} orgs</span>
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => navigate(`/admin/tenants/${tenant.id}`)}
                      >
                        <Eye className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleEdit(tenant)}
                      >
                        <Edit className="w-4 h-4" />
                      </Button>
                      {tenant.id !== 1 && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDelete(tenant)}
                          className="text-red-400 hover:text-red-300"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      )}
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
        <FormModalBuilder
          title="Create Tenant"
          fields={tenantFields}
          isOpen={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          onSubmit={handleCreate}
          submitButtonText="Create Tenant"
        />
      )}

      {/* Edit Modal */}
      {editingTenant && (
        <FormModalBuilder
          title="Edit Tenant"
          fields={editFields}
          isOpen={!!editingTenant}
          onClose={() => setEditingTenant(null)}
          onSubmit={handleUpdate}
          submitButtonText="Save Changes"
        />
      )}
    </div>
  )
}
