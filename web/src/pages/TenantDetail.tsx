import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ArrowLeft, Building2, Users, Database, Shield, Edit, Trash2, Copy, Plus } from 'lucide-react'
import api from '@/lib/api'
import Button from '@/components/Button'
import Input from '@/components/Input'
import Card, { CardHeader, CardContent } from '@/components/Card'
import CreateIdentityModal from '@/components/CreateIdentityModal'
import type { PortalUser } from '@/types'

interface ApiError {
  response?: {
    data?: { error?: string }
    status?: number
  }
  message?: string
}

export default function TenantDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [editingUser, setEditingUser] = useState<PortalUser | null>(null)
  const [showAddUser, setShowAddUser] = useState(false)
  const [userFormData, setUserFormData] = useState({
    full_name: '',
    tenant_role: 'reader',
    is_active: true,
  })

  const tenantId = parseInt(id || '0')

  const { data: tenant, isLoading: tenantLoading } = useQuery({
    queryKey: ['tenant', tenantId],
    queryFn: () => api.getTenant(tenantId),
    enabled: !!tenantId,
  })

  const { data: users, isLoading: usersLoading } = useQuery({
    queryKey: ['tenant-users', tenantId],
    queryFn: () => api.getTenantUsers(tenantId),
    enabled: !!tenantId,
  })

  const { data: stats } = useQuery({
    queryKey: ['tenant-stats', tenantId],
    queryFn: () => api.getTenantStats(tenantId),
    enabled: !!tenantId,
  })

  const updateUserMutation = useMutation({
    mutationFn: ({ userId, data }: { userId: number; data: typeof userFormData }) =>
      api.updateTenantUser(tenantId, userId, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tenant-users', tenantId] })
      toast.success('User updated successfully')
      setEditingUser(null)
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to update user')
    },
  })

  const deleteUserMutation = useMutation({
    mutationFn: (userId: number) => api.deleteTenantUser(tenantId, userId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tenant-users', tenantId] })
      toast.success('User deactivated successfully')
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to deactivate user')
    },
  })

  const handleEditUser = (user: PortalUser) => {
    setEditingUser(user)
    setUserFormData({
      full_name: user.full_name || '',
      tenant_role: user.tenant_role,
      is_active: user.is_active,
    })
  }

  const handleUpdateUser = (e: React.FormEvent) => {
    e.preventDefault()
    if (editingUser) {
      updateUserMutation.mutate({ userId: editingUser.id, data: userFormData })
    }
  }

  const handleDeleteUser = (user: PortalUser) => {
    if (confirm(`Are you sure you want to deactivate "${user.email}"?`)) {
      deleteUserMutation.mutate(user.id)
    }
  }

  const getTierColor = (tier: string) => {
    switch (tier) {
      case 'enterprise': return 'bg-yellow-500/20 text-yellow-400'
      case 'professional': return 'bg-blue-500/20 text-blue-400'
      default: return 'bg-gray-500/20 text-gray-400'
    }
  }

  const getRoleColor = (role: string) => {
    switch (role) {
      case 'admin': return 'bg-red-500/20 text-red-400'
      case 'editor': return 'bg-blue-500/20 text-blue-400'
      default: return 'bg-gray-500/20 text-gray-400'
    }
  }

  if (tenantLoading) {
    return (
      <div className="p-6">
        <div className="text-center py-8 text-slate-400">Loading tenant...</div>
      </div>
    )
  }

  if (!tenant) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="text-center py-8">
            <p className="text-slate-400">Tenant not found</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <Button
          variant="ghost"
          onClick={() => navigate('/admin/tenants')}
          className="mb-4"
        >
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Tenants
        </Button>
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 bg-slate-700 rounded-lg flex items-center justify-center">
            <Building2 className="w-6 h-6 text-slate-400" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-bold text-white">{tenant.name}</h1>
              <span className={`px-2 py-0.5 rounded text-xs font-medium ${getTierColor(tenant.subscription_tier)}`}>
                {tenant.subscription_tier}
              </span>
              {!tenant.is_active && (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-red-500/20 text-red-400">
                  Inactive
                </span>
              )}
            </div>
            <p className="text-slate-400">{tenant.slug} {tenant.domain && `• ${tenant.domain}`}</p>
          </div>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <Users className="w-8 h-8 text-blue-400" />
              <div>
                <p className="text-2xl font-bold text-white">{stats?.portal_users?.total || 0}</p>
                <p className="text-sm text-slate-400">Portal Users</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <Building2 className="w-8 h-8 text-green-400" />
              <div>
                <p className="text-2xl font-bold text-white">{stats?.organizations || 0}</p>
                <p className="text-sm text-slate-400">Organizations</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <Database className="w-8 h-8 text-yellow-400" />
              <div>
                <p className="text-2xl font-bold text-white">{tenant.storage_quota_gb} GB</p>
                <p className="text-sm text-slate-400">Storage Quota</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <Shield className="w-8 h-8 text-purple-400" />
              <div>
                <p className="text-2xl font-bold text-white">{tenant.data_retention_days}</p>
                <p className="text-sm text-slate-400">Retention Days</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Tenant Details */}
      <Card className="mb-6">
        <CardHeader>
          <h2 className="text-lg font-semibold text-white">Tenant Details</h2>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <dt className="text-sm font-medium text-slate-400">Slug</dt>
              <dd className="mt-1 text-sm text-white">{tenant.slug}</dd>
            </div>
            {tenant.domain && (
              <div>
                <dt className="text-sm font-medium text-slate-400">Domain</dt>
                <dd className="mt-1 text-sm text-white">{tenant.domain}</dd>
              </div>
            )}
            <div>
              <dt className="text-sm font-medium text-slate-400">Created</dt>
              <dd className="mt-1 text-sm text-white">{new Date(tenant.created_at).toLocaleDateString()}</dd>
            </div>
            {tenant.village_id && (
              <div>
                <dt className="text-sm font-medium text-slate-400">Village ID</dt>
                <dd className="mt-1 flex items-center gap-2">
                  <a
                    href={`/id/${tenant.village_id}`}
                    className="text-sm text-primary-400 hover:text-primary-300 font-mono"
                  >
                    {tenant.village_id}
                  </a>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(`${window.location.origin}/id/${tenant.village_id}`)
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
          </dl>
        </CardContent>
      </Card>

      {/* Users List */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">Portal Users</h2>
            <Button
              size="sm"
              onClick={() => setShowAddUser(true)}
            >
              <Plus className="w-4 h-4 mr-1" />
              Add User
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {usersLoading ? (
            <div className="text-center py-8 text-slate-400">Loading users...</div>
          ) : !users || users.length === 0 ? (
            <div className="text-center py-8 text-slate-400">No users found</div>
          ) : (
            <div className="space-y-2">
              {users.map((user: PortalUser) => (
                <div
                  key={user.id}
                  className="flex items-center justify-between p-3 bg-slate-900/50 rounded-lg"
                >
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-white">{user.email}</span>
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${getRoleColor(user.tenant_role)}`}>
                        {user.tenant_role}
                      </span>
                      {user.global_role && (
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-yellow-500/20 text-yellow-400">
                          {user.global_role}
                        </span>
                      )}
                      {!user.is_active && (
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-red-500/20 text-red-400">
                          Inactive
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-slate-400">
                      {user.full_name || 'No name'} •
                      {user.mfa_enabled ? ' MFA enabled' : ' No MFA'} •
                      {user.last_login_at ? ` Last login: ${new Date(user.last_login_at).toLocaleDateString()}` : ' Never logged in'}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleEditUser(user)}
                    >
                      <Edit className="w-4 h-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDeleteUser(user)}
                      className="text-red-400 hover:text-red-300"
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Edit User Modal */}
      {editingUser && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-slate-800 border border-slate-700 rounded-lg p-6 w-full max-w-md">
            <h3 className="text-xl font-semibold text-white mb-4">Edit User</h3>
            <form onSubmit={handleUpdateUser} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">Email</label>
                <p className="text-white">{editingUser.email}</p>
              </div>
              <Input
                label="Full Name"
                value={userFormData.full_name}
                onChange={(e) => setUserFormData({ ...userFormData, full_name: e.target.value })}
                placeholder="Full name"
              />
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">
                  Tenant Role
                </label>
                <select
                  value={userFormData.tenant_role}
                  onChange={(e) => setUserFormData({ ...userFormData, tenant_role: e.target.value })}
                  className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-primary-500"
                >
                  <option value="reader">Reader</option>
                  <option value="editor">Editor</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="is_active"
                  checked={userFormData.is_active}
                  onChange={(e) => setUserFormData({ ...userFormData, is_active: e.target.checked })}
                  className="rounded border-slate-600"
                />
                <label htmlFor="is_active" className="text-sm text-slate-300">Active</label>
              </div>
              <div className="flex gap-3 pt-4">
                <Button
                  type="submit"
                  className="flex-1"
                  isLoading={updateUserMutation.isPending}
                >
                  Save Changes
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  className="flex-1"
                  onClick={() => setEditingUser(null)}
                >
                  Cancel
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Add User Modal */}
      <CreateIdentityModal
        isOpen={showAddUser}
        onClose={() => setShowAddUser(false)}
        defaultTenantId={tenantId}
        defaultIsPortalUser={true}
        defaultPortalRole="viewer"
        defaultPermissionScope="tenant"
        invalidateQueryKeys={[
          ['tenant-users', tenantId],
          ['tenant-stats', tenantId],
          ['identities'],
        ]}
      />
    </div>
  )
}
