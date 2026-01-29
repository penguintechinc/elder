import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  Network,
  Trash2,
  Link2,
  Activity,
  Cloud,
  LayoutGrid,
  Shield,
  Globe,
  Router,
  ToggleLeft,
  Circle,
  Waypoints,
  Table,
  Layers,
  Box,
  Server,
  HelpCircle,
  LucideIcon
} from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Select from '@/components/Select'
import NetworkTopologyGraph from '@/components/NetworkTopologyGraph'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

// Icon mapping for network types
const NETWORK_TYPE_ICONS: Record<string, LucideIcon> = {
  vpc: Cloud,
  subnet: LayoutGrid,
  firewall: Shield,
  proxy: Globe,
  router: Router,
  switch: ToggleLeft,
  hub: Circle,
  tunnel: Waypoints,
  route_table: Table,
  vrrf: Layers,
  vxlan: Box,
  vlan: Server,
  namespace: Box,
  other: HelpCircle,
}

const NETWORK_TYPES = [
  { value: 'vpc', label: 'VPC' },
  { value: 'subnet', label: 'Subnet' },
  { value: 'firewall', label: 'Firewall' },
  { value: 'proxy', label: 'Proxy' },
  { value: 'router', label: 'Router' },
  { value: 'switch', label: 'Switch' },
  { value: 'hub', label: 'Hub' },
  { value: 'tunnel', label: 'Tunnel' },
  { value: 'route_table', label: 'Route Table' },
  { value: 'vrrf', label: 'VRRF' },
  { value: 'vxlan', label: 'VXLAN' },
  { value: 'vlan', label: 'VLAN' },
  { value: 'namespace', label: 'Namespace' },
  { value: 'other', label: 'Other' },
]

const CONNECTION_TYPES = [
  { value: 'peering', label: 'Peering' },
  { value: 'vpn', label: 'VPN' },
  { value: 'direct_connect', label: 'Direct Connect' },
  { value: 'transit_gateway', label: 'Transit Gateway' },
  { value: 'route', label: 'Route' },
  { value: 'nat', label: 'NAT' },
  { value: 'load_balancer', label: 'Load Balancer' },
  { value: 'other', label: 'Other' },
]

const TABS = ['Networks', 'Topology', 'Connections'] as const
type Tab = typeof TABS[number]

export default function Networking() {
  const [activeTab, setActiveTab] = useState<Tab>('Networks')
  const [selectedNetworkType, setSelectedNetworkType] = useState<string>('all')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showCreateConnectionModal, setShowCreateConnectionModal] = useState(false)
  const [showTopologyModal, setShowTopologyModal] = useState(false)
  const [selectedOrg, setSelectedOrg] = useState<number | null>(null)
  const queryClient = useQueryClient()

  const { data: networks, isLoading: networksLoading } = useQuery({
    queryKey: ['networks', selectedOrg],
    queryFn: () => api.listNetworks({ organization_id: selectedOrg || undefined }),
    enabled: !!selectedOrg,
  })

  const { data: connections } = useQuery({
    queryKey: ['networkConnections'],
    queryFn: () => api.listTopologyConnections(),
    enabled: activeTab === 'Connections',
  })

  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations(),
  })

  const { data: allNetworks } = useQuery({
    queryKey: ['allNetworks'],
    queryFn: () => api.listNetworks({}),
    enabled: showCreateConnectionModal,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteNetwork(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['networks'],
        refetchType: 'all'
      })
      toast.success('Network deleted')
    },
  })

  const createNetworkMutation = useMutation({
    mutationFn: (data: any) => api.createNetwork(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['networks'],
        refetchType: 'all'
      })
      toast.success('Network created')
      setShowCreateModal(false)
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to create network')
    },
  })

  const createConnectionMutation = useMutation({
    mutationFn: (data: any) => api.createTopologyConnection(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['networkConnections'],
        refetchType: 'all'
      })
      toast.success('Connection created')
      setShowCreateConnectionModal(false)
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to create connection')
    },
  })

  // Form fields for creating networks
  const networkFields: FormField[] = useMemo(() => [
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      defaultValue: selectedOrg?.toString() || '',
      options: (orgs?.items || []).map((o: any) => ({ value: o.id, label: o.name })),
    },
    {
      name: 'name',
      label: 'Name',
      type: 'text',
      required: true,
      placeholder: 'Production VPC',
    },
    {
      name: 'network_type',
      label: 'Network Type',
      type: 'select',
      required: true,
      options: NETWORK_TYPES,
    },
    {
      name: 'cidr',
      label: 'CIDR Block',
      type: 'text',
      required: true,
      placeholder: '10.0.0.0/16',
      helpText: 'IPv4 CIDR notation (e.g., 10.0.1.0/24, 172.16.0.0/12)',
      showWhen: (values) => {
        const networkType = values.network_type
        // Show CIDR for network types that have IP addressing
        return ['vpc', 'subnet', 'vlan', 'vxlan', 'namespace'].includes(networkType)
      },
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'Main production network',
    },
    {
      name: 'region',
      label: 'Region',
      type: 'text',
      placeholder: 'us-east-1',
    },
    {
      name: 'location',
      label: 'Location',
      type: 'text',
      placeholder: 'AWS Virginia',
    },
  ], [orgs?.items, selectedOrg])

  // Form fields for creating connections
  const connectionFields: FormField[] = useMemo(() => [
    {
      name: 'source_network_id',
      label: 'Source Network',
      type: 'select',
      required: true,
      options: (allNetworks?.networks || []).map((n: any) => ({
        value: n.id,
        label: `${n.name} (${n.network_type})`
      })),
    },
    {
      name: 'target_network_id',
      label: 'Target Network',
      type: 'select',
      required: true,
      options: (allNetworks?.networks || []).map((n: any) => ({
        value: n.id,
        label: `${n.name} (${n.network_type})`
      })),
    },
    {
      name: 'connection_type',
      label: 'Connection Type',
      type: 'select',
      required: true,
      options: CONNECTION_TYPES,
    },
    {
      name: 'bandwidth',
      label: 'Bandwidth',
      type: 'text',
      placeholder: '1 Gbps',
    },
    {
      name: 'latency',
      label: 'Latency (ms)',
      type: 'number',
      placeholder: '5',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'VPC peering between prod and staging',
    },
  ], [allNetworks?.networks])

  const handleCreateNetwork = (data: Record<string, any>) => {
    if (!data.organization_id) {
      toast.error('Please select an organization first')
      return
    }

    // For network types that don't have IP addressing, use a placeholder CIDR
    // to satisfy backend requirements (this is a workaround until backend makes CIDR optional)
    const needsCidr = ['vpc', 'subnet', 'vlan', 'vxlan', 'namespace'].includes(data.network_type)
    const cidr = data.cidr || (needsCidr ? undefined : '0.0.0.0/0')

    createNetworkMutation.mutate({
      ...data,
      organization_id: parseInt(data.organization_id),
      cidr,
    })
  }

  const handleCreateConnection = (data: Record<string, any>) => {
    createConnectionMutation.mutate({
      ...data,
      source_network_id: parseInt(data.source_network_id),
      target_network_id: parseInt(data.target_network_id),
      latency: data.latency ? parseInt(data.latency) : undefined,
    })
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Network Topology</h1>
          <p className="mt-2 text-slate-400">Manage networking resources and visualize topology</p>
        </div>
        <div className="flex gap-3">
          <Button variant="ghost" onClick={() => setShowTopologyModal(true)}>
            <Activity className="w-4 h-4 mr-2" />
            View Topology
          </Button>
          <Button onClick={() => setShowCreateModal(true)}>
            <Plus className="w-4 h-4 mr-2" />
            Add Network
          </Button>
        </div>
      </div>

      {/* Organization Selector */}
      <div className="mb-6 max-w-md">
        <Select
          label="Organization"
          value={selectedOrg?.toString() || ''}
          onChange={(e) => setSelectedOrg(parseInt(e.target.value))}
          options={[
            { value: '', label: 'Select organization' },
            ...(orgs?.items || []).map((o: any) => ({ value: o.id, label: o.name })),
          ]}
        />
      </div>

      {/* Tabs */}
      <div className="flex gap-2 mb-6 border-b border-slate-700">
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 font-medium transition-colors border-b-2 ${
              activeTab === tab
                ? 'text-primary-400 border-primary-400'
                : 'text-slate-400 border-transparent hover:text-slate-300'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === 'Networks' && (
        <div>
          {!selectedOrg ? (
            <Card>
              <CardContent className="text-center py-12">
                <Network className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">Select an organization to view networks</p>
              </CardContent>
            </Card>
          ) : networksLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : networks?.networks?.length === 0 ? (
            <Card>
              <CardContent className="text-center py-12">
                <Network className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">No networks configured</p>
                <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
                  Add your first network
                </Button>
              </CardContent>
            </Card>
          ) : (
            <>
              {/* Network Type Filter */}
              <div className="flex flex-wrap gap-2 mb-6">
                <button
                  onClick={() => setSelectedNetworkType('all')}
                  className={`px-3 py-1.5 text-sm font-medium rounded-lg transition-colors ${
                    selectedNetworkType === 'all'
                      ? 'bg-primary-600 text-white'
                      : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                  }`}
                >
                  All ({networks?.networks?.length || 0})
                </button>
                {NETWORK_TYPES.map((type) => {
                  const count = networks?.networks?.filter((n: any) => n.network_type === type.value).length || 0
                  if (count === 0) return null
                  return (
                    <button
                      key={type.value}
                      onClick={() => setSelectedNetworkType(type.value)}
                      className={`px-3 py-1.5 text-sm font-medium rounded-lg transition-colors ${
                        selectedNetworkType === type.value
                          ? 'bg-primary-600 text-white'
                          : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                      }`}
                    >
                      {type.label} ({count})
                    </button>
                  )
                })}
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
                {networks?.networks
                  ?.filter((network: any) => selectedNetworkType === 'all' || network.network_type === selectedNetworkType)
                  .map((network: any) => (
                <Card key={network.id}>
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        {(() => {
                          const TypeIcon = NETWORK_TYPE_ICONS[network.network_type] || Network
                          return <TypeIcon className="w-5 h-5 text-primary-400" />
                        })()}
                        <div>
                          <h3 className="text-lg font-semibold text-white">{network.name}</h3>
                          <p className="text-sm text-slate-400">
                            {NETWORK_TYPES.find(t => t.value === network.network_type)?.label}
                          </p>
                        </div>
                      </div>
                      <span className={`px-2 py-1 text-xs font-medium rounded ${
                        network.is_active ? 'bg-green-500/20 text-green-400' : 'bg-slate-500/20 text-slate-400'
                      }`}>
                        {network.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </div>
                  </CardHeader>
                  <CardContent>
                    {network.description && (
                      <p className="text-sm text-slate-400 mb-3">{network.description}</p>
                    )}
                    {network.region && (
                      <p className="text-sm text-slate-500">Region: {network.region}</p>
                    )}
                    {network.location && (
                      <p className="text-sm text-slate-500">Location: {network.location}</p>
                    )}
                    <div className="flex gap-2 mt-4">
                      <Button size="sm" variant="ghost" onClick={() => deleteMutation.mutate(network.id)}>
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
              </div>
            </>
          )}
        </div>
      )}

      {activeTab === 'Topology' && (
        <div>
          {!selectedOrg ? (
            <Card>
              <CardContent className="text-center py-12">
                <Activity className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">Select an organization to view network topology</p>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="p-0">
                <NetworkTopologyGraph organizationId={selectedOrg} />
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {activeTab === 'Connections' && (
        <div className="space-y-3">
          <div className="flex justify-end mb-4">
            <Button onClick={() => setShowCreateConnectionModal(true)}>
              <Plus className="w-4 h-4 mr-2" />
              Add Connection
            </Button>
          </div>
          {connections?.connections?.length === 0 ? (
            <Card>
              <CardContent className="text-center py-12">
                <Link2 className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">No network connections configured</p>
                <Button className="mt-4" onClick={() => setShowCreateConnectionModal(true)}>
                  Create your first connection
                </Button>
              </CardContent>
            </Card>
          ) : (
            connections?.connections?.map((conn: any) => (
              <Card key={conn.id}>
                <CardContent>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <Link2 className="w-5 h-5 text-primary-400" />
                      <div>
                        <p className="text-white font-medium">{conn.connection_type}</p>
                        <p className="text-sm text-slate-400">
                          Network {conn.source_network_id} → Network {conn.target_network_id}
                        </p>
                      </div>
                    </div>
                    <div className="text-right text-sm">
                      {conn.bandwidth && <p className="text-slate-400">Bandwidth: {conn.bandwidth}</p>}
                      {conn.latency && <p className="text-slate-400">Latency: {conn.latency}ms</p>}
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))
          )}
        </div>
      )}

      {showCreateModal && (
        <FormModalBuilder
          isOpen={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          title="Add Network Resource"
          fields={networkFields}
          onSubmit={handleCreateNetwork}
          submitButtonText="Create"
        />
      )}

      {showTopologyModal && selectedOrg && (
        <TopologyModal
          organizationId={selectedOrg}
          onClose={() => setShowTopologyModal(false)}
        />
      )}

      {showCreateConnectionModal && (
        <FormModalBuilder
          isOpen={showCreateConnectionModal}
          onClose={() => setShowCreateConnectionModal(false)}
          title="Create Network Connection"
          fields={connectionFields}
          onSubmit={handleCreateConnection}
          submitButtonText="Create"
        />
      )}
    </div>
  )
}

function TopologyModal({ organizationId, onClose }: any) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-6xl max-h-[90vh] overflow-hidden flex flex-col">
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">Network Topology Visualization</h2>
            <Button variant="ghost" onClick={onClose}>Close</Button>
          </div>
        </CardHeader>
        <CardContent className="flex-1 p-0">
          <NetworkTopologyGraph organizationId={organizationId} />
        </CardContent>
      </Card>
    </div>
  )
}
