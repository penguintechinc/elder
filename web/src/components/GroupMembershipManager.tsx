/**
 * Group Membership Management Component
 * Enterprise feature for managing group ownership, access requests, and memberships.
 */

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Users,
  UserPlus,
  Shield,
  Clock,
  CheckCircle,
  XCircle,
  Settings,
  Search,
  ChevronRight,
  RefreshCw,
} from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import { Identity } from '@/types'

interface GroupMembershipManagerProps {
  organizationId?: number
}

interface ApiError {
  response?: {
    data?: {
      error?: string
    }
  }
  message?: string
}

interface Group {
  id: number
  name: string
  description?: string
  owner_identity_id?: number
  approval_mode?: string
  approval_threshold?: number
  members?: GroupMember[]
  pending_requests?: AccessRequest[]
  provider?: string
  provider_group_id?: string
  sync_enabled?: boolean
  member_count?: number
  pending_count?: number
}

interface GroupMember {
  id: number
  identity_id: number
  joined_at: string
  expires_at?: string
  role?: string
}

interface AccessRequest {
  id: number
  identity_id: number
  status: 'pending' | 'approved' | 'denied'
  reason?: string
  created_at: string
  identity_name?: string
  requester_name?: string
  requester_id?: number
  group_name?: string
}

const APPROVAL_MODES = [
  { value: 'any', label: 'Any Owner (any single owner can approve)' },
  { value: 'all', label: 'All Owners (all owners must approve)' },
  { value: 'threshold', label: 'Threshold (N owners must approve)' },
]

const PROVIDERS = [
  { value: 'internal', label: 'Internal (Elder)' },
  { value: 'ldap', label: 'LDAP/Active Directory' },
  { value: 'okta', label: 'Okta' },
]

export default function GroupMembershipManager({ organizationId: _organizationId }: GroupMembershipManagerProps) {
  const [selectedGroup, setSelectedGroup] = useState<Group | null>(null)
  const [showGroupDetails, setShowGroupDetails] = useState(false)
  const [showRequestModal, setShowRequestModal] = useState(false)
  const [showAddMemberModal, setShowAddMemberModal] = useState(false)
  const [showSettingsModal, setShowSettingsModal] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [activeView, setActiveView] = useState<'groups' | 'pending'>('groups')
  const queryClient = useQueryClient()

  // Form state for access request
  const [requestReason, setRequestReason] = useState('')
  const [requestExpiry, setRequestExpiry] = useState('')

  // Form state for add member
  const [addMemberIdentityId, setAddMemberIdentityId] = useState('')
  const [addMemberExpiry, setAddMemberExpiry] = useState('')

  // Form state for group settings
  const [settingsOwnerIdentity, setSettingsOwnerIdentity] = useState('')
  const [settingsApprovalMode, setSettingsApprovalMode] = useState('any')
  const [settingsApprovalThreshold, setSettingsApprovalThreshold] = useState(1)
  const [settingsProvider, setSettingsProvider] = useState('internal')
  const [settingsProviderGroupId, setSettingsProviderGroupId] = useState('')
  const [settingsSyncEnabled, setSettingsSyncEnabled] = useState(false)

  // Fetch groups
  const { data: groupsData, isLoading: groupsLoading, refetch: refetchGroups } = useQuery({
    queryKey: ['groupMembership', 'groups'],
    queryFn: () => api.getGroupMembershipGroups({ include_members: true, include_pending: true }),
  })

  // Fetch pending requests for current user
  const { data: pendingData, refetch: refetchPending } = useQuery({
    queryKey: ['groupMembership', 'pending'],
    queryFn: () => api.getPendingGroupAccessRequests(),
  })

  // Fetch identities for dropdowns
  const { data: identitiesData } = useQuery({
    queryKey: ['identities'],
    queryFn: () => api.getIdentities({ per_page: 1000 }),
  })

  // Mutations
  const createRequestMutation = useMutation({
    mutationFn: (data: { groupId: number; reason?: string; expires_at?: string }) =>
      api.createGroupAccessRequest(data.groupId, { reason: data.reason, expires_at: data.expires_at }),
    onSuccess: () => {
      toast.success('Access request submitted')
      setShowRequestModal(false)
      setRequestReason('')
      setRequestExpiry('')
      queryClient.invalidateQueries({ queryKey: ['groupMembership'] })
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to submit request')
    },
  })

  const approveRequestMutation = useMutation({
    mutationFn: (data: { requestId: number; comment?: string }) =>
      api.approveGroupAccessRequest(data.requestId, { comment: data.comment }),
    onSuccess: () => {
      toast.success('Request approved')
      queryClient.invalidateQueries({ queryKey: ['groupMembership'] })
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to approve request')
    },
  })

  const denyRequestMutation = useMutation({
    mutationFn: (data: { requestId: number; comment?: string }) =>
      api.denyGroupAccessRequest(data.requestId, { comment: data.comment }),
    onSuccess: () => {
      toast.success('Request denied')
      queryClient.invalidateQueries({ queryKey: ['groupMembership'] })
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to deny request')
    },
  })

  const addMemberMutation = useMutation({
    mutationFn: (data: { groupId: number; identity_id: number; expires_at?: string }) =>
      api.addGroupMember(data.groupId, { identity_id: data.identity_id, expires_at: data.expires_at }),
    onSuccess: () => {
      toast.success('Member added')
      setShowAddMemberModal(false)
      setAddMemberIdentityId('')
      setAddMemberExpiry('')
      queryClient.invalidateQueries({ queryKey: ['groupMembership'] })
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to add member')
    },
  })

  const removeMemberMutation = useMutation({
    mutationFn: (data: { groupId: number; identityId: number }) =>
      api.removeGroupMember(data.groupId, data.identityId),
    onSuccess: () => {
      toast.success('Member removed')
      queryClient.invalidateQueries({ queryKey: ['groupMembership'] })
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to remove member')
    },
  })

  const updateGroupMutation = useMutation({
    mutationFn: (data: { groupId: number; updates: Record<string, unknown> }) =>
      api.updateGroupMembershipGroup(data.groupId, data.updates),
    onSuccess: () => {
      toast.success('Group settings updated')
      setShowSettingsModal(false)
      queryClient.invalidateQueries({ queryKey: ['groupMembership'] })
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to update group')
    },
  })

  const handleRequestAccess = () => {
    if (!selectedGroup) return
    createRequestMutation.mutate({
      groupId: selectedGroup.id,
      reason: requestReason || undefined,
      expires_at: requestExpiry || undefined,
    })
  }

  const handleAddMember = () => {
    if (!selectedGroup || !addMemberIdentityId) return
    addMemberMutation.mutate({
      groupId: selectedGroup.id,
      identity_id: parseInt(addMemberIdentityId),
      expires_at: addMemberExpiry || undefined,
    })
  }

  const handleUpdateSettings = () => {
    if (!selectedGroup) return
    updateGroupMutation.mutate({
      groupId: selectedGroup.id,
      updates: {
        owner_identity_id: settingsOwnerIdentity ? parseInt(settingsOwnerIdentity) : undefined,
        approval_mode: settingsApprovalMode,
        approval_threshold: settingsApprovalThreshold,
        provider: settingsProvider,
        provider_group_id: settingsProviderGroupId || undefined,
        sync_enabled: settingsSyncEnabled,
      },
    })
  }

  const openSettingsModal = (group: Group) => {
    setSelectedGroup(group)
    setSettingsOwnerIdentity(group.owner_identity_id?.toString() || '')
    setSettingsApprovalMode(group.approval_mode || 'any')
    setSettingsApprovalThreshold(group.approval_threshold || 1)
    setSettingsProvider(group.provider || 'internal')
    setSettingsProviderGroupId(group.provider_group_id || '')
    setSettingsSyncEnabled(group.sync_enabled || false)
    setShowSettingsModal(true)
  }

  const groups = (groupsData?.groups || []) as Group[]
  const pendingRequests = (pendingData?.requests || []) as AccessRequest[]

  const filteredGroups = groups.filter((g) =>
    !searchQuery || g.name?.toLowerCase().includes(searchQuery.toLowerCase())
  )

  return (
    <div className="space-y-6">
      {/* Header with view toggle */}
      <div className="flex items-center justify-between">
        <div className="flex gap-2">
          <button
            onClick={() => setActiveView('groups')}
            className={`px-4 py-2 rounded-lg font-medium transition-colors ${
              activeView === 'groups'
                ? 'bg-primary-500/20 text-primary-400'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <Users className="w-4 h-4 inline mr-2" />
            Groups
          </button>
          <button
            onClick={() => setActiveView('pending')}
            className={`px-4 py-2 rounded-lg font-medium transition-colors ${
              activeView === 'pending'
                ? 'bg-primary-500/20 text-primary-400'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            <Clock className="w-4 h-4 inline mr-2" />
            Pending Requests
            {pendingRequests.length > 0 && (
              <span className="ml-2 px-2 py-0.5 text-xs rounded-full bg-orange-500/20 text-orange-400">
                {pendingRequests.length}
              </span>
            )}
          </button>
        </div>
        <Button
          variant="ghost"
          onClick={() => { refetchGroups(); refetchPending(); }}
        >
          <RefreshCw className="w-4 h-4 mr-2" />
          Refresh
        </Button>
      </div>

      {/* Groups View */}
      {activeView === 'groups' && (
        <>
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search groups..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>

          {/* Groups List */}
          {groupsLoading ? (
            <Card>
              <CardContent className="py-12 text-center">
                <RefreshCw className="w-8 h-8 text-slate-400 mx-auto mb-4 animate-spin" />
                <p className="text-slate-400">Loading groups...</p>
              </CardContent>
            </Card>
          ) : filteredGroups.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center">
                <Users className="w-16 h-16 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">No groups found</p>
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-4">
              {filteredGroups.map((group) => (
                <Card
                  key={group.id}
                  className="hover:border-primary-500/50 transition-colors cursor-pointer"
                  onClick={() => { setSelectedGroup(group); setShowGroupDetails(true); }}
                >
                  <CardContent className="p-6">
                    <div className="flex items-start justify-between">
                      <div className="flex items-start gap-4">
                        <div className="p-2 rounded-lg bg-primary-500/20">
                          <Users className="w-6 h-6 text-primary-400" />
                        </div>
                        <div>
                          <h3 className="text-lg font-semibold text-white">{group.name}</h3>
                          {group.description && (
                            <p className="text-sm text-slate-400 mt-1">{group.description}</p>
                          )}
                          <div className="flex gap-4 mt-2 text-sm">
                            <span className="text-slate-400">
                              <Users className="w-4 h-4 inline mr-1" />
                              {group.member_count || 0} members
                            </span>
                            {group.pending_count > 0 && (
                              <span className="text-orange-400">
                                <Clock className="w-4 h-4 inline mr-1" />
                                {group.pending_count} pending
                              </span>
                            )}
                            {group.provider !== 'internal' && (
                              <span className="text-cyan-400">
                                <Shield className="w-4 h-4 inline mr-1" />
                                {PROVIDERS.find(p => p.value === group.provider)?.label || group.provider}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={(e) => {
                            e.stopPropagation()
                            setSelectedGroup(group)
                            setShowRequestModal(true)
                          }}
                        >
                          <UserPlus className="w-4 h-4 mr-1" />
                          Request
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={(e) => {
                            e.stopPropagation()
                            openSettingsModal(group)
                          }}
                        >
                          <Settings className="w-4 h-4" />
                        </Button>
                        <ChevronRight className="w-5 h-5 text-slate-400" />
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </>
      )}

      {/* Pending Requests View */}
      {activeView === 'pending' && (
        <>
          {pendingRequests.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center">
                <CheckCircle className="w-16 h-16 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">No pending requests</p>
                <p className="text-sm text-slate-500 mt-2">
                  Requests for groups you own will appear here
                </p>
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-4">
              {pendingRequests.map((request) => (
                <Card key={request.id}>
                  <CardContent className="p-6">
                    <div className="flex items-start justify-between">
                      <div>
                        <h3 className="text-lg font-semibold text-white">
                          {request.requester_name || `User ${request.requester_id}`}
                        </h3>
                        <p className="text-sm text-slate-400">
                          Requesting access to <span className="text-primary-400">{request.group_name}</span>
                        </p>
                        {request.reason && (
                          <p className="text-sm text-slate-300 mt-2 p-2 bg-slate-800 rounded">
                            {request.reason}
                          </p>
                        )}
                        <p className="text-xs text-slate-500 mt-2">
                          Requested {new Date(request.created_at).toLocaleDateString()}
                        </p>
                      </div>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          onClick={() => approveRequestMutation.mutate({ requestId: request.id })}
                          disabled={approveRequestMutation.isPending}
                        >
                          <CheckCircle className="w-4 h-4 mr-1" />
                          Approve
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => denyRequestMutation.mutate({ requestId: request.id })}
                          disabled={denyRequestMutation.isPending}
                        >
                          <XCircle className="w-4 h-4 mr-1" />
                          Deny
                        </Button>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </>
      )}

      {/* Group Details Modal */}
      {showGroupDetails && selectedGroup && (
        <GroupDetailsModal
          group={selectedGroup}
          identities={identitiesData?.items || []}
          onClose={() => { setShowGroupDetails(false); setSelectedGroup(null); }}
          onAddMember={() => setShowAddMemberModal(true)}
          onRemoveMember={(identityId) => removeMemberMutation.mutate({
            groupId: selectedGroup.id,
            identityId,
          })}
          removingMember={removeMemberMutation.isPending}
        />
      )}

      {/* Request Access Modal */}
      {showRequestModal && selectedGroup && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-md">
            <CardHeader>
              <h2 className="text-xl font-semibold text-white">Request Access</h2>
              <p className="text-sm text-slate-400">
                Request access to {selectedGroup.name}
              </p>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-slate-300 mb-1.5">
                    Reason (optional)
                  </label>
                  <textarea
                    value={requestReason}
                    onChange={(e) => setRequestReason(e.target.value)}
                    placeholder="Why do you need access to this group?"
                    rows={3}
                    className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                </div>
                <Input
                  type="datetime-local"
                  label="Access expires (optional)"
                  value={requestExpiry}
                  onChange={(e) => setRequestExpiry(e.target.value)}
                />
                <div className="flex gap-3 pt-4">
                  <Button
                    variant="ghost"
                    onClick={() => { setShowRequestModal(false); setSelectedGroup(null); }}
                    className="flex-1"
                  >
                    Cancel
                  </Button>
                  <Button
                    onClick={handleRequestAccess}
                    disabled={createRequestMutation.isPending}
                    className="flex-1"
                  >
                    {createRequestMutation.isPending ? 'Submitting...' : 'Submit Request'}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Add Member Modal */}
      {showAddMemberModal && selectedGroup && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-md">
            <CardHeader>
              <h2 className="text-xl font-semibold text-white">Add Member</h2>
              <p className="text-sm text-slate-400">
                Add a member to {selectedGroup.name}
              </p>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <Select
                  label="Identity"
                  required
                  value={addMemberIdentityId}
                  onChange={(e) => setAddMemberIdentityId(e.target.value)}
                >
                  <option value="">Select identity</option>
                  {(identitiesData?.items || []).map((identity) => (
                    <option key={identity.id} value={identity.id}>
                      {identity.full_name || identity.username} ({identity.email})
                    </option>
                  ))}
                </Select>
                <Input
                  type="datetime-local"
                  label="Membership expires (optional)"
                  value={addMemberExpiry}
                  onChange={(e) => setAddMemberExpiry(e.target.value)}
                />
                <div className="flex gap-3 pt-4">
                  <Button
                    variant="ghost"
                    onClick={() => { setShowAddMemberModal(false); }}
                    className="flex-1"
                  >
                    Cancel
                  </Button>
                  <Button
                    onClick={handleAddMember}
                    disabled={addMemberMutation.isPending || !addMemberIdentityId}
                    className="flex-1"
                  >
                    {addMemberMutation.isPending ? 'Adding...' : 'Add Member'}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Group Settings Modal */}
      {showSettingsModal && selectedGroup && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-lg max-h-[90vh] overflow-y-auto">
            <CardHeader>
              <h2 className="text-xl font-semibold text-white">Group Settings</h2>
              <p className="text-sm text-slate-400">{selectedGroup.name}</p>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <Select
                  label="Owner Identity"
                  value={settingsOwnerIdentity}
                  onChange={(e) => setSettingsOwnerIdentity(e.target.value)}
                >
                  <option value="">No owner</option>
                  {(identitiesData?.items || []).map((identity) => (
                    <option key={identity.id} value={identity.id}>
                      {identity.full_name || identity.username}
                    </option>
                  ))}
                </Select>

                <Select
                  label="Approval Mode"
                  value={settingsApprovalMode}
                  onChange={(e) => setSettingsApprovalMode(e.target.value)}
                >
                  {APPROVAL_MODES.map((mode) => (
                    <option key={mode.value} value={mode.value}>
                      {mode.label}
                    </option>
                  ))}
                </Select>

                {settingsApprovalMode === 'threshold' && (
                  <Input
                    type="number"
                    label="Approval Threshold"
                    value={settingsApprovalThreshold.toString()}
                    onChange={(e) => setSettingsApprovalThreshold(parseInt(e.target.value) || 1)}
                    min={1}
                  />
                )}

                <Select
                  label="Provider"
                  value={settingsProvider}
                  onChange={(e) => setSettingsProvider(e.target.value)}
                >
                  {PROVIDERS.map((provider) => (
                    <option key={provider.value} value={provider.value}>
                      {provider.label}
                    </option>
                  ))}
                </Select>

                {settingsProvider !== 'internal' && (
                  <>
                    <Input
                      label="Provider Group ID"
                      placeholder={settingsProvider === 'ldap' ? 'cn=group,dc=example,dc=com' : 'Okta Group ID'}
                      value={settingsProviderGroupId}
                      onChange={(e) => setSettingsProviderGroupId(e.target.value)}
                    />
                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        id="syncEnabled"
                        checked={settingsSyncEnabled}
                        onChange={(e) => setSettingsSyncEnabled(e.target.checked)}
                        className="rounded bg-slate-900 border-slate-700"
                      />
                      <label htmlFor="syncEnabled" className="text-sm text-slate-300">
                        Enable write-back sync to provider
                      </label>
                    </div>
                  </>
                )}

                <div className="flex gap-3 pt-4">
                  <Button
                    variant="ghost"
                    onClick={() => { setShowSettingsModal(false); setSelectedGroup(null); }}
                    className="flex-1"
                  >
                    Cancel
                  </Button>
                  <Button
                    onClick={handleUpdateSettings}
                    disabled={updateGroupMutation.isPending}
                    className="flex-1"
                  >
                    {updateGroupMutation.isPending ? 'Saving...' : 'Save Settings'}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}

// Group Details Modal Component
interface GroupDetailsModalProps {
  group: Group
  identities: Identity[]
  onClose: () => void
  onAddMember: () => void
  onRemoveMember: (identityId: number) => void
  removingMember: boolean
}

function GroupDetailsModal({
  group,
  identities,
  onClose,
  onAddMember,
  onRemoveMember,
  removingMember,
}: GroupDetailsModalProps) {
  const { data: membersData, isLoading } = useQuery({
    queryKey: ['groupMembership', 'members', group.id],
    queryFn: () => api.getGroupMembers(group.id),
  })

  const members = membersData?.members || []

  const getIdentityName = (identityId: number) => {
    const identity = identities.find((i) => i.id === identityId)
    return identity ? (identity.full_name || identity.username) : `User ${identityId}`
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">{group.name}</h2>
              {group.description && (
                <p className="text-sm text-slate-400 mt-1">{group.description}</p>
              )}
            </div>
            <Button onClick={onAddMember}>
              <UserPlus className="w-4 h-4 mr-2" />
              Add Member
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {/* Group Info */}
          <div className="grid grid-cols-2 gap-4 mb-6 p-4 bg-slate-800/50 rounded-lg">
            <div>
              <p className="text-sm text-slate-400">Provider</p>
              <p className="text-white">
                {PROVIDERS.find(p => p.value === group.provider)?.label || 'Internal'}
              </p>
            </div>
            <div>
              <p className="text-sm text-slate-400">Approval Mode</p>
              <p className="text-white capitalize">{group.approval_mode || 'Any'}</p>
            </div>
            {group.owner_identity_id && (
              <div>
                <p className="text-sm text-slate-400">Owner</p>
                <p className="text-white">{getIdentityName(group.owner_identity_id)}</p>
              </div>
            )}
            {group.sync_enabled && (
              <div>
                <p className="text-sm text-slate-400">Sync Status</p>
                <p className="text-green-400">Write-back enabled</p>
              </div>
            )}
          </div>

          {/* Members List */}
          <h3 className="text-lg font-semibold text-white mb-4">
            Members ({members.length})
          </h3>

          {isLoading ? (
            <div className="text-center py-8">
              <RefreshCw className="w-6 h-6 text-slate-400 mx-auto animate-spin" />
            </div>
          ) : members.length === 0 ? (
            <div className="text-center py-8 text-slate-400">
              No members yet
            </div>
          ) : (
            <div className="space-y-2">
              {members.map((member) => (
                <div
                  key={member.identity_id}
                  className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg"
                >
                  <div>
                    <p className="text-white font-medium">
                      {member.identity_name || getIdentityName(member.identity_id)}
                    </p>
                    {member.expires_at && (
                      <p className="text-xs text-orange-400">
                        Expires {new Date(member.expires_at).toLocaleDateString()}
                      </p>
                    )}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onRemoveMember(member.identity_id)}
                    disabled={removingMember}
                  >
                    <XCircle className="w-4 h-4 text-red-400" />
                  </Button>
                </div>
              ))}
            </div>
          )}

          <div className="flex justify-end pt-6">
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
