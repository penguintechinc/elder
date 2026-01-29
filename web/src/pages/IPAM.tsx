import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Trash2, Edit, ChevronRight, ChevronDown, Network, Globe, Wifi } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

// Types
interface IpamPrefix {
  id: number
  prefix: string
  description?: string
  status: string
  is_pool: boolean
  parent_id?: number
  organization_id: number
  vrf_id?: number
  vlan_id?: number
  role?: string
  children_count?: number
  utilization?: number
  created_at: string
  updated_at: string
  children?: IpamPrefix[]
}

interface IpamAddress {
  id: number
  address: string
  description?: string
  status: string
  dns_name?: string
  prefix_id?: number
  organization_id: number
  created_at: string
  updated_at: string
}

interface IpamVlan {
  id: number
  vid: number
  name: string
  description?: string
  status: string
  group_id?: number
  organization_id: number
  created_at: string
  updated_at: string
}

type TabType = 'prefixes' | 'addresses' | 'vlans'

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-green-500/20 text-green-400',
  reserved: 'bg-yellow-500/20 text-yellow-400',
  deprecated: 'bg-red-500/20 text-red-400',
  container: 'bg-blue-500/20 text-blue-400',
  available: 'bg-slate-500/20 text-slate-400',
}

// Status options for forms
const prefixStatusOptions = [
  { value: 'active', label: 'Active' },
  { value: 'reserved', label: 'Reserved' },
  { value: 'deprecated', label: 'Deprecated' },
  { value: 'container', label: 'Container' },
]

const addressStatusOptions = [
  { value: 'active', label: 'Active' },
  { value: 'reserved', label: 'Reserved' },
  { value: 'deprecated', label: 'Deprecated' },
]

const vlanStatusOptions = [
  { value: 'active', label: 'Active' },
  { value: 'reserved', label: 'Reserved' },
  { value: 'deprecated', label: 'Deprecated' },
]

export default function IPAM() {
  const [activeTab, setActiveTab] = useState<TabType>('prefixes')
  const [search, setSearch] = useState('')
  const [organizationFilter, setOrganizationFilter] = useState<number | undefined>()
  const [prefixFilter, setPrefixFilter] = useState<number | undefined>()

  // Modals
  const [showCreatePrefixModal, setShowCreatePrefixModal] = useState(false)
  const [showCreateAddressModal, setShowCreateAddressModal] = useState(false)
  const [showCreateVlanModal, setShowCreateVlanModal] = useState(false)
  const [editingPrefix, setEditingPrefix] = useState<IpamPrefix | null>(null)
  const [editingAddress, setEditingAddress] = useState<IpamAddress | null>(null)
  const [editingVlan, setEditingVlan] = useState<IpamVlan | null>(null)

  const queryClient = useQueryClient()

  // Fetch organizations for filters
  const { data: organizations } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations(),
  })

  // Prefixes query
  const { data: prefixesData, isLoading: prefixesLoading } = useQuery({
    queryKey: ['ipam-prefixes', organizationFilter],
    queryFn: () => api.getIpamPrefixes({ organization_id: organizationFilter }),
    enabled: activeTab === 'prefixes',
  })

  // Addresses query
  const { data: addressesData, isLoading: addressesLoading } = useQuery({
    queryKey: ['ipam-addresses', organizationFilter, prefixFilter],
    queryFn: () => api.getIpamAddresses({ organization_id: organizationFilter, prefix_id: prefixFilter }),
    enabled: activeTab === 'addresses',
  })

  // VLANs query
  const { data: vlansData, isLoading: vlansLoading } = useQuery({
    queryKey: ['ipam-vlans', organizationFilter],
    queryFn: () => api.getIpamVlans({ organization_id: organizationFilter }),
    enabled: activeTab === 'vlans',
  })

  // Create mutations
  const createPrefixMutation = useMutation({
    mutationFn: (data: any) => api.createIpamPrefix(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ipam-prefixes'] })
      setShowCreatePrefixModal(false)
      toast.success('Prefix created successfully')
    },
    onError: () => toast.error('Failed to create prefix'),
  })

  const createAddressMutation = useMutation({
    mutationFn: (data: any) => api.createIpamAddress(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ipam-addresses'] })
      setShowCreateAddressModal(false)
      toast.success('Address created successfully')
    },
    onError: () => toast.error('Failed to create address'),
  })

  const createVlanMutation = useMutation({
    mutationFn: (data: any) => api.createIpamVlan(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ipam-vlans'] })
      setShowCreateVlanModal(false)
      toast.success('VLAN created successfully')
    },
    onError: () => toast.error('Failed to create VLAN'),
  })

  // Update mutations
  const updatePrefixMutation = useMutation({
    mutationFn: (data: any) => api.updateIpamPrefix(editingPrefix!.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ipam-prefixes'] })
      setEditingPrefix(null)
      toast.success('Prefix updated successfully')
    },
    onError: () => toast.error('Failed to update prefix'),
  })

  const updateAddressMutation = useMutation({
    mutationFn: (data: any) => api.updateIpamAddress(editingAddress!.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ipam-addresses'] })
      setEditingAddress(null)
      toast.success('Address updated successfully')
    },
    onError: () => toast.error('Failed to update address'),
  })

  const updateVlanMutation = useMutation({
    mutationFn: (data: any) => api.updateIpamVlan(editingVlan!.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ipam-vlans'] })
      setEditingVlan(null)
      toast.success('VLAN updated successfully')
    },
    onError: () => toast.error('Failed to update VLAN'),
  })

  // Delete mutations
  const deletePrefixMutation = useMutation({
    mutationFn: (id: number) => api.deleteIpamPrefix(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ipam-prefixes'] })
      toast.success('Prefix deleted successfully')
    },
    onError: () => toast.error('Failed to delete prefix'),
  })

  const deleteAddressMutation = useMutation({
    mutationFn: (id: number) => api.deleteIpamAddress(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ipam-addresses'] })
      toast.success('Address deleted successfully')
    },
    onError: () => toast.error('Failed to delete address'),
  })

  const deleteVlanMutation = useMutation({
    mutationFn: (id: number) => api.deleteIpamVlan(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ipam-vlans'] })
      toast.success('VLAN deleted successfully')
    },
    onError: () => toast.error('Failed to delete VLAN'),
  })

  const handleDelete = (type: string, id: number, name: string) => {
    if (window.confirm(`Are you sure you want to delete "${name}"?`)) {
      if (type === 'prefix') deletePrefixMutation.mutate(id)
      else if (type === 'address') deleteAddressMutation.mutate(id)
      else if (type === 'vlan') deleteVlanMutation.mutate(id)
    }
  }

  // Organization options for forms
  const organizationOptions = useMemo(() =>
    organizations?.items?.map((org: any) => ({
      value: org.id,
      label: org.name,
    })) || [],
    [organizations]
  )

  // Form field configurations using useMemo
  const createPrefixFields: FormField[] = useMemo(() => [
    {
      name: 'prefix',
      label: 'Prefix (CIDR)',
      type: 'text',
      required: true,
      placeholder: 'e.g., 192.168.1.0/24',
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      options: [{ value: '', label: 'Select organization...' }, ...organizationOptions],
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'Enter description (optional)',
    },
    {
      name: 'status',
      label: 'Status',
      type: 'select',
      defaultValue: 'active',
      options: prefixStatusOptions,
    },
    {
      name: 'is_pool',
      label: 'Is Pool (can allocate from this prefix)',
      type: 'checkbox',
      defaultValue: false,
    },
  ], [organizationOptions])

  const editPrefixFields: FormField[] = useMemo(() => [
    {
      name: 'prefix',
      label: 'Prefix (CIDR)',
      type: 'text',
      required: true,
      placeholder: 'e.g., 192.168.1.0/24',
      defaultValue: editingPrefix?.prefix || '',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'Enter description (optional)',
      defaultValue: editingPrefix?.description || '',
    },
    {
      name: 'status',
      label: 'Status',
      type: 'select',
      options: prefixStatusOptions,
      defaultValue: editingPrefix?.status || 'active',
    },
    {
      name: 'is_pool',
      label: 'Is Pool',
      type: 'checkbox',
      defaultValue: editingPrefix?.is_pool || false,
    },
  ], [editingPrefix])

  const createAddressFields: FormField[] = useMemo(() => [
    {
      name: 'address',
      label: 'IP Address',
      type: 'text',
      required: true,
      placeholder: 'e.g., 192.168.1.1/32',
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      options: [{ value: '', label: 'Select organization...' }, ...organizationOptions],
    },
    {
      name: 'dns_name',
      label: 'DNS Name',
      type: 'text',
      placeholder: 'e.g., server1.example.com',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'Enter description (optional)',
    },
    {
      name: 'status',
      label: 'Status',
      type: 'select',
      defaultValue: 'active',
      options: addressStatusOptions,
    },
  ], [organizationOptions])

  const editAddressFields: FormField[] = useMemo(() => [
    {
      name: 'address',
      label: 'IP Address',
      type: 'text',
      required: true,
      placeholder: 'e.g., 192.168.1.1/32',
      defaultValue: editingAddress?.address || '',
    },
    {
      name: 'dns_name',
      label: 'DNS Name',
      type: 'text',
      placeholder: 'e.g., server1.example.com',
      defaultValue: editingAddress?.dns_name || '',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'Enter description (optional)',
      defaultValue: editingAddress?.description || '',
    },
    {
      name: 'status',
      label: 'Status',
      type: 'select',
      options: addressStatusOptions,
      defaultValue: editingAddress?.status || 'active',
    },
  ], [editingAddress])

  const createVlanFields: FormField[] = useMemo(() => [
    {
      name: 'vid',
      label: 'VLAN ID',
      type: 'number',
      required: true,
      placeholder: 'e.g., 100',
    },
    {
      name: 'name',
      label: 'Name',
      type: 'text',
      required: true,
      placeholder: 'e.g., Production',
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      options: [{ value: '', label: 'Select organization...' }, ...organizationOptions],
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'Enter description (optional)',
    },
    {
      name: 'status',
      label: 'Status',
      type: 'select',
      defaultValue: 'active',
      options: vlanStatusOptions,
    },
  ], [organizationOptions])

  const editVlanFields: FormField[] = useMemo(() => [
    {
      name: 'vid',
      label: 'VLAN ID',
      type: 'number',
      required: true,
      placeholder: 'e.g., 100',
      defaultValue: editingVlan?.vid,
    },
    {
      name: 'name',
      label: 'Name',
      type: 'text',
      required: true,
      placeholder: 'e.g., Production',
      defaultValue: editingVlan?.name || '',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'Enter description (optional)',
      defaultValue: editingVlan?.description || '',
    },
    {
      name: 'status',
      label: 'Status',
      type: 'select',
      options: vlanStatusOptions,
      defaultValue: editingVlan?.status || 'active',
    },
  ], [editingVlan])

  const tabs = [
    { id: 'prefixes' as TabType, label: 'Prefixes', icon: Network },
    { id: 'addresses' as TabType, label: 'Addresses', icon: Globe },
    { id: 'vlans' as TabType, label: 'VLANs', icon: Wifi },
  ]

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">IP Address Management</h1>
          <p className="mt-2 text-slate-400">
            Manage IP prefixes, addresses, and VLANs
          </p>
        </div>
        <Button onClick={() => {
          if (activeTab === 'prefixes') setShowCreatePrefixModal(true)
          else if (activeTab === 'addresses') setShowCreateAddressModal(true)
          else if (activeTab === 'vlans') setShowCreateVlanModal(true)
        }}>
          <Plus className="w-4 h-4 mr-2" />
          Create {activeTab === 'prefixes' ? 'Prefix' : activeTab === 'addresses' ? 'Address' : 'VLAN'}
        </Button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-slate-800 p-1 rounded-lg w-fit">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === tab.id
                ? 'bg-primary-600 text-white'
                : 'text-slate-400 hover:text-white hover:bg-slate-700'
            }`}
          >
            <tab.icon className="w-4 h-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* Filters */}
      <div className="flex gap-4 mb-6">
        <div className="relative max-w-xs">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
        <Select
          value={organizationFilter || ''}
          onChange={(e) => setOrganizationFilter(e.target.value ? Number(e.target.value) : undefined)}
          className="max-w-xs"
        >
          <option value="">All Organizations</option>
          {organizations?.items?.map((org: any) => (
            <option key={org.id} value={org.id}>{org.name}</option>
          ))}
        </Select>
        {activeTab === 'addresses' && (
          <Select
            value={prefixFilter || ''}
            onChange={(e) => setPrefixFilter(e.target.value ? Number(e.target.value) : undefined)}
            className="max-w-xs"
          >
            <option value="">All Prefixes</option>
            {prefixesData?.items?.map((prefix: IpamPrefix) => (
              <option key={prefix.id} value={prefix.id}>{prefix.prefix}</option>
            ))}
          </Select>
        )}
      </div>

      {/* Content */}
      {activeTab === 'prefixes' && (
        <PrefixesTab
          data={prefixesData}
          isLoading={prefixesLoading}
          search={search}
          onEdit={setEditingPrefix}
          onDelete={(id, name) => handleDelete('prefix', id, name)}
          onCreate={() => setShowCreatePrefixModal(true)}
        />
      )}

      {activeTab === 'addresses' && (
        <AddressesTab
          data={addressesData}
          isLoading={addressesLoading}
          search={search}
          onEdit={setEditingAddress}
          onDelete={(id, name) => handleDelete('address', id, name)}
          onCreate={() => setShowCreateAddressModal(true)}
        />
      )}

      {activeTab === 'vlans' && (
        <VlansTab
          data={vlansData}
          isLoading={vlansLoading}
          search={search}
          onEdit={setEditingVlan}
          onDelete={(id, name) => handleDelete('vlan', id, name)}
          onCreate={() => setShowCreateVlanModal(true)}
        />
      )}

      {/* Modals */}
      {showCreatePrefixModal && (
        <FormModalBuilder
          isOpen={showCreatePrefixModal}
          onClose={() => setShowCreatePrefixModal(false)}
          title="Create Prefix"
          fields={createPrefixFields}
          onSubmit={(data) => createPrefixMutation.mutate(data)}
          submitButtonText="Create"
        />
      )}

      {showCreateAddressModal && (
        <FormModalBuilder
          isOpen={showCreateAddressModal}
          onClose={() => setShowCreateAddressModal(false)}
          title="Create Address"
          fields={createAddressFields}
          onSubmit={(data) => createAddressMutation.mutate(data)}
          submitButtonText="Create"
        />
      )}

      {showCreateVlanModal && (
        <FormModalBuilder
          isOpen={showCreateVlanModal}
          onClose={() => setShowCreateVlanModal(false)}
          title="Create VLAN"
          fields={createVlanFields}
          onSubmit={(data) => createVlanMutation.mutate(data)}
          submitButtonText="Create"
        />
      )}

      {editingPrefix && (
        <FormModalBuilder
          isOpen={!!editingPrefix}
          onClose={() => setEditingPrefix(null)}
          title="Edit Prefix"
          fields={editPrefixFields}
          onSubmit={(data) => updatePrefixMutation.mutate(data)}
          submitButtonText="Update"
        />
      )}

      {editingAddress && (
        <FormModalBuilder
          isOpen={!!editingAddress}
          onClose={() => setEditingAddress(null)}
          title="Edit Address"
          fields={editAddressFields}
          onSubmit={(data) => updateAddressMutation.mutate(data)}
          submitButtonText="Update"
        />
      )}

      {editingVlan && (
        <FormModalBuilder
          isOpen={!!editingVlan}
          onClose={() => setEditingVlan(null)}
          title="Edit VLAN"
          fields={editVlanFields}
          onSubmit={(data) => updateVlanMutation.mutate(data)}
          submitButtonText="Update"
        />
      )}
    </div>
  )
}

// Prefixes Tab with Tree View
interface PrefixesTabProps {
  data: any
  isLoading: boolean
  search: string
  onEdit: (prefix: IpamPrefix) => void
  onDelete: (id: number, name: string) => void
  onCreate: () => void
}

function PrefixesTab({ data, isLoading, search, onEdit, onDelete, onCreate }: PrefixesTabProps) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  // Build tree structure from flat list
  const prefixes = data?.items || []
  const filteredPrefixes = search
    ? prefixes.filter((p: IpamPrefix) =>
        p.prefix.toLowerCase().includes(search.toLowerCase()) ||
        p.description?.toLowerCase().includes(search.toLowerCase())
      )
    : prefixes

  // Group by parent to build tree
  const rootPrefixes = filteredPrefixes.filter((p: IpamPrefix) => !p.parent_id)
  const childrenMap = new Map<number, IpamPrefix[]>()

  filteredPrefixes.forEach((p: IpamPrefix) => {
    if (p.parent_id) {
      const children = childrenMap.get(p.parent_id) || []
      children.push(p)
      childrenMap.set(p.parent_id, children)
    }
  })

  if (filteredPrefixes.length === 0) {
    return (
      <Card>
        <CardContent className="text-center py-12">
          <p className="text-slate-400">No prefixes found</p>
          <Button className="mt-4" onClick={onCreate}>
            Create your first prefix
          </Button>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardContent className="p-0">
        <div className="divide-y divide-slate-700">
          {rootPrefixes.map((prefix: IpamPrefix) => (
            <PrefixTreeNode
              key={prefix.id}
              prefix={prefix}
              childrenMap={childrenMap}
              depth={0}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

// Recursive tree node component
interface PrefixTreeNodeProps {
  prefix: IpamPrefix
  childrenMap: Map<number, IpamPrefix[]>
  depth: number
  onEdit: (prefix: IpamPrefix) => void
  onDelete: (id: number, name: string) => void
}

function PrefixTreeNode({ prefix, childrenMap, depth, onEdit, onDelete }: PrefixTreeNodeProps) {
  const [expanded, setExpanded] = useState(false)
  const children = childrenMap.get(prefix.id) || []
  const hasChildren = children.length > 0

  return (
    <div>
      <div
        className="flex items-center gap-3 px-4 py-3 hover:bg-slate-700/50 transition-colors"
        style={{ paddingLeft: `${16 + depth * 24}px` }}
      >
        {/* Expand/collapse button */}
        <button
          onClick={() => setExpanded(!expanded)}
          className={`p-1 rounded hover:bg-slate-600 ${!hasChildren ? 'invisible' : ''}`}
        >
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-slate-400" />
          ) : (
            <ChevronRight className="w-4 h-4 text-slate-400" />
          )}
        </button>

        {/* Prefix info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3">
            <Network className="w-4 h-4 text-primary-400 flex-shrink-0" />
            <span className="font-mono font-medium text-white">{prefix.prefix}</span>
            <span className={`px-2 py-0.5 rounded text-xs ${STATUS_COLORS[prefix.status] || STATUS_COLORS.available}`}>
              {prefix.status}
            </span>
            {hasChildren && (
              <span className="text-xs text-slate-500">
                {children.length} child{children.length !== 1 ? 'ren' : ''}
              </span>
            )}
            {prefix.utilization !== undefined && (
              <span className="text-xs text-slate-500">
                {prefix.utilization}% utilized
              </span>
            )}
          </div>
          {prefix.description && (
            <p className="text-sm text-slate-400 mt-1 truncate">{prefix.description}</p>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2">
          <button
            onClick={() => onEdit(prefix)}
            className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
          >
            <Edit className="w-4 h-4" />
          </button>
          <button
            onClick={() => onDelete(prefix.id, prefix.prefix)}
            className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Children */}
      {expanded && hasChildren && (
        <div>
          {children.map((child) => (
            <PrefixTreeNode
              key={child.id}
              prefix={child}
              childrenMap={childrenMap}
              depth={depth + 1}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// Addresses Tab
interface AddressesTabProps {
  data: any
  isLoading: boolean
  search: string
  onEdit: (address: IpamAddress) => void
  onDelete: (id: number, name: string) => void
  onCreate: () => void
}

function AddressesTab({ data, isLoading, search, onEdit, onDelete, onCreate }: AddressesTabProps) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const addresses = data?.items || []
  const filteredAddresses = search
    ? addresses.filter((a: IpamAddress) =>
        a.address.toLowerCase().includes(search.toLowerCase()) ||
        a.description?.toLowerCase().includes(search.toLowerCase()) ||
        a.dns_name?.toLowerCase().includes(search.toLowerCase())
      )
    : addresses

  if (filteredAddresses.length === 0) {
    return (
      <Card>
        <CardContent className="text-center py-12">
          <p className="text-slate-400">No addresses found</p>
          <Button className="mt-4" onClick={onCreate}>
            Create your first address
          </Button>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardContent className="p-0">
        <table className="w-full">
          <thead>
            <tr className="border-b border-slate-700 text-left">
              <th className="px-4 py-3 text-sm font-medium text-slate-400">Address</th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">DNS Name</th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">Status</th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">Description</th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {filteredAddresses.map((address: IpamAddress) => (
              <tr key={address.id} className="hover:bg-slate-700/50 transition-colors">
                <td className="px-4 py-3">
                  <span className="font-mono text-white">{address.address}</span>
                </td>
                <td className="px-4 py-3 text-slate-300">{address.dns_name || '-'}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-xs ${STATUS_COLORS[address.status] || STATUS_COLORS.available}`}>
                    {address.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-400 text-sm truncate max-w-xs">
                  {address.description || '-'}
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-2">
                    <button
                      onClick={() => onEdit(address)}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => onDelete(address.id, address.address)}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}

// VLANs Tab
interface VlansTabProps {
  data: any
  isLoading: boolean
  search: string
  onEdit: (vlan: IpamVlan) => void
  onDelete: (id: number, name: string) => void
  onCreate: () => void
}

function VlansTab({ data, isLoading, search, onEdit, onDelete, onCreate }: VlansTabProps) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const vlans = data?.items || []
  const filteredVlans = search
    ? vlans.filter((v: IpamVlan) =>
        v.name.toLowerCase().includes(search.toLowerCase()) ||
        v.description?.toLowerCase().includes(search.toLowerCase()) ||
        v.vid.toString().includes(search)
      )
    : vlans

  if (filteredVlans.length === 0) {
    return (
      <Card>
        <CardContent className="text-center py-12">
          <p className="text-slate-400">No VLANs found</p>
          <Button className="mt-4" onClick={onCreate}>
            Create your first VLAN
          </Button>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardContent className="p-0">
        <table className="w-full">
          <thead>
            <tr className="border-b border-slate-700 text-left">
              <th className="px-4 py-3 text-sm font-medium text-slate-400">VID</th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">Name</th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">Status</th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">Description</th>
              <th className="px-4 py-3 text-sm font-medium text-slate-400">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {filteredVlans.map((vlan: IpamVlan) => (
              <tr key={vlan.id} className="hover:bg-slate-700/50 transition-colors">
                <td className="px-4 py-3">
                  <span className="font-mono text-white">{vlan.vid}</span>
                </td>
                <td className="px-4 py-3 text-white font-medium">{vlan.name}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded text-xs ${STATUS_COLORS[vlan.status] || STATUS_COLORS.available}`}>
                    {vlan.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-400 text-sm truncate max-w-xs">
                  {vlan.description || '-'}
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-2">
                    <button
                      onClick={() => onEdit(vlan)}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => onDelete(vlan.id, vlan.name)}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}
