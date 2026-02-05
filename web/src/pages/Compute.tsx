import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Server,
  Box,
  Layers,
  Network,
  HardDrive,
  Shield,
  Ship,
  Container,
  Monitor,
  RefreshCw,
  Plus,
  Cpu,
} from 'lucide-react'
import api from '@/lib/api'
import Card, { CardHeader, CardContent } from '@/components/Card'

// ── API response record types ─────────────────────────────────
// Lightweight shapes matching what the API returns for each resource.
// Using Record<string, unknown> for metadata/attributes avoids `any`.
interface EntityMetadata {
  capacity_cpu?: string
  capacity_memory?: string
  kubelet_version?: string
  os_image?: string
  conditions?: string[]
  phase?: string
  namespace?: string
  node_name?: string
  pod_ip?: string
  containers_count?: number
  [key: string]: unknown
}

interface ComputeEntity {
  id: number
  name: string
  sub_type: string
  entity_type: string
  organization_id?: number
  created_at?: string
  attributes?: { metadata?: EntityMetadata; [key: string]: unknown }
}

interface K8sService {
  id: number
  name: string
  port?: string
  status?: string
  deployment_method?: string
}

interface NetworkResource {
  id: number
  name: string
  network_type?: string
  region?: string
  tags?: string[]
}

interface DataStoreResource {
  id: number
  name: string
  storage_type?: string
  storage_provider?: string
  location_region?: string
  tags?: string[]
}

interface IdentityResource {
  id: number
  username: string
  full_name?: string
  auth_provider?: string
  identity_type?: string
  is_active: boolean
}

// ── Primary tabs ──────────────────────────────────────────────
const PRIMARY_TABS = ['VMs', 'Kubernetes', 'LXD/LXC'] as const
type PrimaryTab = typeof PRIMARY_TABS[number]

const PRIMARY_ICONS: Record<PrimaryTab, typeof Server> = {
  'VMs': Monitor,
  'Kubernetes': Ship,
  'LXD/LXC': Container,
}

// ── K8s sub-tabs ──────────────────────────────────────────────
const K8S_TABS = ['Clusters', 'Nodes', 'Pods', 'Services', 'Namespaces', 'Storage', 'Service Accounts'] as const
type K8sTab = typeof K8S_TABS[number]

const K8S_ICONS: Record<K8sTab, typeof Ship> = {
  'Clusters': Ship,
  'Nodes': Server,
  'Pods': Box,
  'Services': Network,
  'Namespaces': Layers,
  'Storage': HardDrive,
  'Service Accounts': Shield,
}

// ── LXD sub-tabs ──────────────────────────────────────────────
const LXD_TABS = ['Overview', 'Containers', 'VMs', 'Storage Pools', 'Networks'] as const
type LxdTab = typeof LXD_TABS[number]

const LXD_ICONS: Record<LxdTab, typeof Container> = {
  'Overview': Container,
  'Containers': Container,
  'VMs': Monitor,
  'Storage Pools': HardDrive,
  'Networks': Network,
}

// ── Shared components ─────────────────────────────────────────
function EmptyState({ icon: Icon, title, description }: { icon: typeof Server; title: string; description: string }) {
  return (
    <Card><CardContent>
      <div className="flex flex-col items-center justify-center py-12 text-slate-400">
        <Icon className="h-12 w-12 mb-4 opacity-50" />
        <p className="text-lg font-medium">{title}</p>
        <p className="text-sm mt-1">{description}</p>
      </div>
    </CardContent></Card>
  )
}

function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <RefreshCw className="h-6 w-6 text-amber-400 animate-spin" />
      <span className="ml-2 text-slate-400">Loading...</span>
    </div>
  )
}

type LucideIcon = typeof Server

function SubTabNav<T extends string>({ tabs, icons, active, onChange }: {
  tabs: readonly T[]
  icons: Record<T, LucideIcon>
  active: T
  onChange: (tab: T) => void
}) {
  return (
    <div className="border-b border-slate-700/50 mb-4">
      <nav className="flex gap-1 -mb-px">
        {tabs.map((tab) => {
          const Icon: LucideIcon = icons[tab]
          return (
            <button
              key={tab}
              onClick={() => onChange(tab)}
              className={`flex items-center gap-2 px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
                active === tab
                  ? 'border-amber-400/70 text-amber-300'
                  : 'border-transparent text-slate-500 hover:text-slate-300 hover:border-slate-600'
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {tab}
            </button>
          )
        })}
      </nav>
    </div>
  )
}

// ── VM sub-type filters ───────────────────────────────────────
const VM_SUB_TYPES = ['ec2_instance', 'gce_instance', 'azure_vm', 'lxd_vm'] as const

// ── VMs Tab ───────────────────────────────────────────────────
function VMsTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['compute-vms'],
    queryFn: () => api.getEntities({ entity_type: 'compute' }),
  })

  const allEntities: ComputeEntity[] = data?.items || data?.data || []
  const vms = allEntities.filter(
    (e) => (VM_SUB_TYPES as readonly string[]).includes(e.sub_type)
  )

  if (isLoading) return <LoadingSpinner />

  if (vms.length === 0) {
    return <EmptyState icon={Monitor} title="No virtual machines discovered" description="Run a discovery job to see VM instances from AWS, GCP, Azure, or LXD" />
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-700">
            <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
            <th className="text-left py-3 px-4 text-slate-400 font-medium">Type</th>
            <th className="text-left py-3 px-4 text-slate-400 font-medium">Provider</th>
            <th className="text-left py-3 px-4 text-slate-400 font-medium">Organization</th>
            <th className="text-left py-3 px-4 text-slate-400 font-medium">Created</th>
          </tr>
        </thead>
        <tbody>
          {vms.map((vm) => {
            const provider = vm.sub_type?.startsWith('ec2') ? 'AWS'
              : vm.sub_type?.startsWith('gce') ? 'GCP'
              : vm.sub_type?.startsWith('azure') ? 'Azure'
              : vm.sub_type?.startsWith('lxd') ? 'LXD'
              : 'Unknown'
            return (
              <tr key={vm.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                <td className="py-3 px-4 text-white font-medium">{vm.name}</td>
                <td className="py-3 px-4 text-slate-300">{vm.sub_type?.replace(/_/g, ' ')}</td>
                <td className="py-3 px-4">
                  <span className="px-2 py-1 text-xs rounded-full bg-slate-700 text-slate-300">{provider}</span>
                </td>
                <td className="py-3 px-4 text-slate-300">{vm.organization_id || '\u2014'}</td>
                <td className="py-3 px-4 text-slate-300">{vm.created_at ? new Date(vm.created_at).toLocaleDateString() : '\u2014'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Kubernetes Tab ────────────────────────────────────────────
function KubernetesTab() {
  const [subTab, setSubTab] = useState<K8sTab>('Clusters')

  const { data: clustersData, isLoading: loadingClusters, refetch: refetchClusters } = useQuery({
    queryKey: ['k8s-clusters'],
    queryFn: () => api.getEntities({ entity_type: 'compute', sub_type: 'kubernetes_cluster' }),
    enabled: subTab === 'Clusters',
  })

  const { data: nodesData, isLoading: loadingNodes, refetch: refetchNodes } = useQuery({
    queryKey: ['k8s-nodes'],
    queryFn: () => api.getEntities({ entity_type: 'compute', sub_type: 'k8s_node' }),
    enabled: subTab === 'Nodes',
  })

  const { data: podsData, isLoading: loadingPods, refetch: refetchPods } = useQuery({
    queryKey: ['k8s-pods'],
    queryFn: () => api.getEntities({ entity_type: 'compute', sub_type: 'k8s_pod' }),
    enabled: subTab === 'Pods',
  })

  const { data: servicesData, isLoading: loadingServices, refetch: refetchServices } = useQuery({
    queryKey: ['k8s-services'],
    queryFn: () => api.getServices({ deployment_method: 'kubernetes' }),
    enabled: subTab === 'Services',
  })

  const { data: namespacesData, isLoading: loadingNamespaces, refetch: refetchNamespaces } = useQuery({
    queryKey: ['k8s-namespaces'],
    queryFn: () => api.listNetworks({ network_type: 'namespace' }),
    enabled: subTab === 'Namespaces',
  })

  const { data: storageData, isLoading: loadingStorage, refetch: refetchStorage } = useQuery({
    queryKey: ['k8s-storage'],
    queryFn: () => api.getDataStores({ storage_type: 'disk' }),
    enabled: subTab === 'Storage',
  })

  const { data: saData, isLoading: loadingSA, refetch: refetchSA } = useQuery({
    queryKey: ['k8s-service-accounts'],
    queryFn: () => api.getIdentities({ identity_type: 'serviceAccount' }),
    enabled: subTab === 'Service Accounts',
  })

  const handleRefresh = () => {
    const refetchMap: Record<K8sTab, () => void> = {
      'Clusters': () => refetchClusters(),
      'Nodes': () => refetchNodes(),
      'Pods': () => refetchPods(),
      'Services': () => refetchServices(),
      'Namespaces': () => refetchNamespaces(),
      'Storage': () => refetchStorage(),
      'Service Accounts': () => refetchSA(),
    }
    refetchMap[subTab]()
  }

  const isLoading = {
    'Clusters': loadingClusters,
    'Nodes': loadingNodes,
    'Pods': loadingPods,
    'Services': loadingServices,
    'Namespaces': loadingNamespaces,
    'Storage': loadingStorage,
    'Service Accounts': loadingSA,
  }[subTab]

  const clusters: ComputeEntity[] = clustersData?.items || clustersData?.data || []
  const nodes: ComputeEntity[] = nodesData?.items || nodesData?.data || []
  const pods: ComputeEntity[] = podsData?.items || podsData?.data || []
  const services: K8sService[] = servicesData?.items || servicesData?.data || []
  const namespaces: NetworkResource[] = namespacesData?.items || namespacesData?.data || []
  const storage: DataStoreResource[] = storageData?.items || storageData?.data || []
  const serviceAccounts: IdentityResource[] = saData?.items || saData?.data || []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <SubTabNav tabs={K8S_TABS} icons={K8S_ICONS} active={subTab} onChange={setSubTab} />
        <button
          onClick={handleRefresh}
          className="flex items-center gap-2 px-3 py-1.5 bg-slate-700 text-slate-200 rounded-lg hover:bg-slate-600 transition-colors text-sm"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </button>
      </div>

      {isLoading && <LoadingSpinner />}

      {subTab === 'Clusters' && !loadingClusters && (
        clusters.length === 0 ? (
          <EmptyState icon={Ship} title="No Kubernetes clusters discovered" description="Run a Kubernetes discovery job to see clusters here" />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {clusters.map((cluster) => (
              <Card key={cluster.id}>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <Ship className="h-5 w-5 text-amber-400" />
                    <h3 className="font-semibold text-white truncate">{cluster.name}</h3>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Type</span>
                      <span className="text-slate-200">{cluster.sub_type}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Organization</span>
                      <span className="text-slate-200">{cluster.organization_id || '\u2014'}</span>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )
      )}

      {subTab === 'Nodes' && !loadingNodes && (
        nodes.length === 0 ? (
          <EmptyState icon={Server} title="No nodes discovered" description="Nodes appear after running Kubernetes discovery" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">CPU</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Memory</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Kubelet</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">OS</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((node) => {
                  const meta: EntityMetadata = node.attributes?.metadata || {}
                  return (
                    <tr key={node.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                      <td className="py-3 px-4 text-white font-medium">{node.name}</td>
                      <td className="py-3 px-4 text-slate-300">{meta.capacity_cpu || '\u2014'}</td>
                      <td className="py-3 px-4 text-slate-300">{meta.capacity_memory || '\u2014'}</td>
                      <td className="py-3 px-4 text-slate-300">{meta.kubelet_version || '\u2014'}</td>
                      <td className="py-3 px-4 text-slate-300">{meta.os_image || '\u2014'}</td>
                      <td className="py-3 px-4">
                        <span className="px-2 py-1 text-xs rounded-full bg-green-900/50 text-green-400">
                          {(meta.conditions || []).includes('Ready') ? 'Ready' : 'Unknown'}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )
      )}

      {subTab === 'Pods' && !loadingPods && (
        pods.length === 0 ? (
          <EmptyState icon={Box} title="No pods discovered" description="Pods appear after running Kubernetes discovery" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Namespace</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Node</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">IP</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Containers</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Phase</th>
                </tr>
              </thead>
              <tbody>
                {pods.map((pod) => {
                  const meta: EntityMetadata = pod.attributes?.metadata || {}
                  const phase = meta.phase || 'Unknown'
                  const phaseColor = phase === 'Running'
                    ? 'bg-green-900/50 text-green-400'
                    : phase === 'Succeeded'
                    ? 'bg-blue-900/50 text-blue-400'
                    : phase === 'Pending'
                    ? 'bg-yellow-900/50 text-yellow-400'
                    : 'bg-red-900/50 text-red-400'
                  return (
                    <tr key={pod.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                      <td className="py-3 px-4 text-white font-medium">{pod.name}</td>
                      <td className="py-3 px-4 text-slate-300">{meta.namespace || '\u2014'}</td>
                      <td className="py-3 px-4 text-slate-300">{meta.node_name || '\u2014'}</td>
                      <td className="py-3 px-4 text-slate-300 font-mono text-xs">{meta.pod_ip || '\u2014'}</td>
                      <td className="py-3 px-4 text-slate-300">{meta.containers_count || 0}</td>
                      <td className="py-3 px-4">
                        <span className={`px-2 py-1 text-xs rounded-full ${phaseColor}`}>{phase}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )
      )}

      {subTab === 'Services' && !loadingServices && (
        services.length === 0 ? (
          <EmptyState icon={Network} title="No Kubernetes services found" description="K8s services appear after running discovery" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Port</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Status</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Deployment</th>
                </tr>
              </thead>
              <tbody>
                {services.map((svc) => (
                  <tr key={svc.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                    <td className="py-3 px-4 text-white font-medium">{svc.name}</td>
                    <td className="py-3 px-4 text-slate-300">{svc.port || '\u2014'}</td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-1 text-xs rounded-full ${
                        svc.status === 'active' ? 'bg-green-900/50 text-green-400' : 'bg-slate-700 text-slate-400'
                      }`}>{svc.status || 'unknown'}</span>
                    </td>
                    <td className="py-3 px-4 text-slate-300">{svc.deployment_method || '\u2014'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {subTab === 'Namespaces' && !loadingNamespaces && (
        namespaces.length === 0 ? (
          <EmptyState icon={Layers} title="No namespaces discovered" description="Namespaces appear after running Kubernetes discovery" />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {namespaces.map((ns) => (
              <Card key={ns.id}>
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <Layers className="h-5 w-5 text-blue-400" />
                    <h3 className="font-semibold text-white">{ns.name}</h3>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Type</span>
                      <span className="text-slate-200">{ns.network_type}</span>
                    </div>
                    {ns.tags && ns.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {ns.tags.map((tag, i) => (
                          <span key={i} className="px-2 py-0.5 text-xs rounded bg-slate-700 text-slate-300">{tag}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )
      )}

      {subTab === 'Storage' && !loadingStorage && (
        storage.length === 0 ? (
          <EmptyState icon={HardDrive} title="No Kubernetes storage found" description="Persistent volumes appear after running discovery" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Type</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Provider</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Region</th>
                </tr>
              </thead>
              <tbody>
                {storage.map((ds) => (
                  <tr key={ds.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                    <td className="py-3 px-4 text-white font-medium">{ds.name}</td>
                    <td className="py-3 px-4 text-slate-300">{ds.storage_type || '\u2014'}</td>
                    <td className="py-3 px-4 text-slate-300">{ds.storage_provider || '\u2014'}</td>
                    <td className="py-3 px-4 text-slate-300">{ds.location_region || '\u2014'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {subTab === 'Service Accounts' && !loadingSA && (
        serviceAccounts.length === 0 ? (
          <EmptyState icon={Shield} title="No service accounts discovered" description="K8s service accounts appear after running discovery" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Username</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Provider</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Type</th>
                  <th className="text-left py-3 px-4 text-slate-400 font-medium">Active</th>
                </tr>
              </thead>
              <tbody>
                {serviceAccounts.map((sa) => (
                  <tr key={sa.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                    <td className="py-3 px-4 text-white font-mono text-xs">{sa.username}</td>
                    <td className="py-3 px-4 text-slate-300">{sa.full_name || sa.username}</td>
                    <td className="py-3 px-4 text-slate-300">{sa.auth_provider || '\u2014'}</td>
                    <td className="py-3 px-4 text-slate-300">{sa.identity_type || '\u2014'}</td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-1 text-xs rounded-full ${
                        sa.is_active ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
                      }`}>{sa.is_active ? 'Active' : 'Inactive'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  )
}

// ── LXD/LXC Tab ──────────────────────────────────────────────
function LxdTab() {
  const [subTab, setSubTab] = useState<LxdTab>('Overview')

  const { data: containersData, isLoading: loadingContainers } = useQuery({
    queryKey: ['lxd-containers'],
    queryFn: () => api.getEntities({ entity_type: 'compute', sub_type: 'lxd_container' }),
    enabled: subTab === 'Containers' || subTab === 'Overview',
  })

  const { data: vmsData, isLoading: loadingVMs } = useQuery({
    queryKey: ['lxd-vms'],
    queryFn: () => api.getEntities({ entity_type: 'compute', sub_type: 'lxd_vm' }),
    enabled: subTab === 'VMs' || subTab === 'Overview',
  })

  const { data: storageData, isLoading: loadingStorage } = useQuery({
    queryKey: ['lxd-storage'],
    queryFn: () => api.getDataStores({ storage_type: 'other' }),
    enabled: subTab === 'Storage Pools' || subTab === 'Overview',
  })

  const { data: networksData, isLoading: loadingNetworks } = useQuery({
    queryKey: ['lxd-networks'],
    queryFn: () => api.listNetworks({ network_type: 'other' }),
    enabled: subTab === 'Networks' || subTab === 'Overview',
  })

  const allContainers: ComputeEntity[] = containersData?.items || containersData?.data || []
  const containers = allContainers.filter((c) => c.sub_type === 'lxd_container')
  const allVms: ComputeEntity[] = vmsData?.items || vmsData?.data || []
  const vms = allVms.filter((v) => v.sub_type === 'lxd_vm')
  const allStorage: DataStoreResource[] = storageData?.items || storageData?.data || []
  const storagePools = allStorage.filter((s) => (s.tags || []).includes('lxd'))
  const allNetworks: NetworkResource[] = networksData?.items || networksData?.data || []
  const networks = allNetworks.filter((n) => (n.tags || []).includes('lxd'))

  const totalResources = containers.length + vms.length + storagePools.length + networks.length
  const isAnyLoading = loadingContainers || loadingVMs || loadingStorage || loadingNetworks

  return (
    <div className="space-y-4">
      <SubTabNav tabs={LXD_TABS} icons={LXD_ICONS} active={subTab} onChange={setSubTab} />

      {subTab === 'Overview' && (
        <div className="space-y-6">
          {totalResources === 0 && !isAnyLoading ? (
            <Card><CardContent>
              <div className="flex flex-col items-center justify-center py-16 text-slate-400">
                <Container className="h-16 w-16 mb-6 opacity-30" />
                <h2 className="text-xl font-semibold text-white mb-2">No LXD resources discovered yet</h2>
                <p className="text-sm text-slate-400 text-center max-w-md">
                  Connect an LXD cluster to get started. Once an LXD discovery connector is configured,
                  containers, VMs, storage pools, and networks will appear here.
                </p>
                <div className="mt-6 flex items-center gap-2 px-4 py-2 bg-slate-800 rounded-lg text-sm text-slate-300">
                  <Plus className="h-4 w-4" />
                  LXD discovery connector coming soon
                </div>
              </div>
            </CardContent></Card>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <Card><CardContent>
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-blue-900/50 rounded-lg">
                    <Container className="h-5 w-5 text-blue-400" />
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-white">{containers.length}</p>
                    <p className="text-sm text-slate-400">Containers</p>
                  </div>
                </div>
              </CardContent></Card>
              <Card><CardContent>
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-purple-900/50 rounded-lg">
                    <Monitor className="h-5 w-5 text-purple-400" />
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-white">{vms.length}</p>
                    <p className="text-sm text-slate-400">Virtual Machines</p>
                  </div>
                </div>
              </CardContent></Card>
              <Card><CardContent>
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-green-900/50 rounded-lg">
                    <HardDrive className="h-5 w-5 text-green-400" />
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-white">{storagePools.length}</p>
                    <p className="text-sm text-slate-400">Storage Pools</p>
                  </div>
                </div>
              </CardContent></Card>
              <Card><CardContent>
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-amber-900/50 rounded-lg">
                    <Network className="h-5 w-5 text-amber-400" />
                  </div>
                  <div>
                    <p className="text-2xl font-bold text-white">{networks.length}</p>
                    <p className="text-sm text-slate-400">Networks</p>
                  </div>
                </div>
              </CardContent></Card>
            </div>
          )}
        </div>
      )}

      {subTab === 'Containers' && !loadingContainers && (
        containers.length === 0 ? (
          <EmptyState icon={Container} title="No LXD containers discovered" description="Containers will appear once an LXD discovery connector is configured" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-700">
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Type</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Organization</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Created</th>
              </tr></thead>
              <tbody>
                {containers.map((c) => (
                  <tr key={c.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                    <td className="py-3 px-4 text-white font-medium">{c.name}</td>
                    <td className="py-3 px-4 text-slate-300">{c.sub_type}</td>
                    <td className="py-3 px-4 text-slate-300">{c.organization_id || '\u2014'}</td>
                    <td className="py-3 px-4 text-slate-300">{c.created_at ? new Date(c.created_at).toLocaleDateString() : '\u2014'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {subTab === 'VMs' && !loadingVMs && (
        vms.length === 0 ? (
          <EmptyState icon={Monitor} title="No LXD virtual machines discovered" description="VMs will appear once an LXD discovery connector is configured" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-700">
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Type</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Organization</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Created</th>
              </tr></thead>
              <tbody>
                {vms.map((vm) => (
                  <tr key={vm.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                    <td className="py-3 px-4 text-white font-medium">{vm.name}</td>
                    <td className="py-3 px-4 text-slate-300">{vm.sub_type}</td>
                    <td className="py-3 px-4 text-slate-300">{vm.organization_id || '\u2014'}</td>
                    <td className="py-3 px-4 text-slate-300">{vm.created_at ? new Date(vm.created_at).toLocaleDateString() : '\u2014'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {subTab === 'Storage Pools' && !loadingStorage && (
        storagePools.length === 0 ? (
          <EmptyState icon={HardDrive} title="No LXD storage pools found" description="Storage pools will appear once an LXD discovery connector is configured" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-700">
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Type</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Region</th>
              </tr></thead>
              <tbody>
                {storagePools.map((s) => (
                  <tr key={s.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                    <td className="py-3 px-4 text-white font-medium">{s.name}</td>
                    <td className="py-3 px-4 text-slate-300">{s.storage_type || '\u2014'}</td>
                    <td className="py-3 px-4 text-slate-300">{s.location_region || '\u2014'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {subTab === 'Networks' && !loadingNetworks && (
        networks.length === 0 ? (
          <EmptyState icon={Network} title="No LXD networks found" description="Networks will appear once an LXD discovery connector is configured" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-700">
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Type</th>
                <th className="text-left py-3 px-4 text-slate-400 font-medium">Region</th>
              </tr></thead>
              <tbody>
                {networks.map((n) => (
                  <tr key={n.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                    <td className="py-3 px-4 text-white font-medium">{n.name}</td>
                    <td className="py-3 px-4 text-slate-300">{n.network_type || '\u2014'}</td>
                    <td className="py-3 px-4 text-slate-300">{n.region || '\u2014'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {isAnyLoading && subTab !== 'Overview' && <LoadingSpinner />}
    </div>
  )
}

// ── Main Compute Page ─────────────────────────────────────────
export default function Compute() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab') as PrimaryTab | null
  const [activeTab, setActiveTab] = useState<PrimaryTab>(
    tabParam && PRIMARY_TABS.includes(tabParam) ? tabParam : 'VMs'
  )

  // Sync URL query param with tab state
  useEffect(() => {
    if (tabParam && PRIMARY_TABS.includes(tabParam) && tabParam !== activeTab) {
      setActiveTab(tabParam)
    }
  }, [tabParam, activeTab])

  const handleTabChange = (tab: PrimaryTab) => {
    setActiveTab(tab)
    setSearchParams({ tab })
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <Cpu className="h-8 w-8 text-amber-400" />
        <div>
          <h1 className="text-2xl font-bold text-white">Compute</h1>
          <p className="text-sm text-slate-400">Virtual machines, containers, and orchestration platforms</p>
        </div>
      </div>

      <div className="border-b border-slate-700">
        <nav className="flex gap-1 -mb-px">
          {PRIMARY_TABS.map((tab) => {
            const Icon = PRIMARY_ICONS[tab]
            return (
              <button
                key={tab}
                onClick={() => handleTabChange(tab)}
                className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab
                    ? 'border-amber-400 text-amber-400'
                    : 'border-transparent text-slate-400 hover:text-slate-200 hover:border-slate-600'
                }`}
              >
                <Icon className="h-4 w-4" />
                {tab}
              </button>
            )
          })}
        </nav>
      </div>

      <div>
        {activeTab === 'VMs' && <VMsTab />}
        {activeTab === 'Kubernetes' && <KubernetesTab />}
        {activeTab === 'LXD/LXC' && <LxdTab />}
      </div>
    </div>
  )
}
