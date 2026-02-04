import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Server,
  Box,
  Layers,
  Network,
  HardDrive,
  Shield,
  Ship,
  RefreshCw,
} from 'lucide-react'
import api from '@/lib/api'
import Card, { CardHeader, CardContent } from '@/components/Card'

const TABS = ['Clusters', 'Nodes', 'Pods', 'Services', 'Namespaces', 'Storage', 'Service Accounts'] as const
type Tab = typeof TABS[number]

const TAB_ICONS: Record<Tab, typeof Ship> = {
  'Clusters': Ship,
  'Nodes': Server,
  'Pods': Box,
  'Services': Network,
  'Namespaces': Layers,
  'Storage': HardDrive,
  'Service Accounts': Shield,
}

export default function Kubernetes() {
  const [activeTab, setActiveTab] = useState<Tab>('Clusters')

  const { data: clustersData, isLoading: loadingClusters, refetch: refetchClusters } = useQuery({
    queryKey: ['k8s-clusters'],
    queryFn: () => api.getEntities({ entity_type: 'compute', sub_type: 'kubernetes_cluster' }),
    enabled: activeTab === 'Clusters',
  })

  const { data: nodesData, isLoading: loadingNodes, refetch: refetchNodes } = useQuery({
    queryKey: ['k8s-nodes'],
    queryFn: () => api.getEntities({ entity_type: 'compute', sub_type: 'k8s_node' }),
    enabled: activeTab === 'Nodes',
  })

  const { data: podsData, isLoading: loadingPods, refetch: refetchPods } = useQuery({
    queryKey: ['k8s-pods'],
    queryFn: () => api.getEntities({ entity_type: 'compute', sub_type: 'k8s_pod' }),
    enabled: activeTab === 'Pods',
  })

  const { data: servicesData, isLoading: loadingServices, refetch: refetchServices } = useQuery({
    queryKey: ['k8s-services'],
    queryFn: () => api.getServices({ deployment_method: 'kubernetes' }),
    enabled: activeTab === 'Services',
  })

  const { data: namespacesData, isLoading: loadingNamespaces, refetch: refetchNamespaces } = useQuery({
    queryKey: ['k8s-namespaces'],
    queryFn: () => api.listNetworks({ network_type: 'namespace' }),
    enabled: activeTab === 'Namespaces',
  })

  const { data: storageData, isLoading: loadingStorage, refetch: refetchStorage } = useQuery({
    queryKey: ['k8s-storage'],
    queryFn: () => api.getDataStores({ storage_type: 'disk' }),
    enabled: activeTab === 'Storage',
  })

  const { data: saData, isLoading: loadingSA, refetch: refetchSA } = useQuery({
    queryKey: ['k8s-service-accounts'],
    queryFn: () => api.getIdentities({ identity_type: 'serviceAccount' }),
    enabled: activeTab === 'Service Accounts',
  })

  const handleRefresh = () => {
    const refetchMap: Record<Tab, () => void> = {
      'Clusters': () => refetchClusters(),
      'Nodes': () => refetchNodes(),
      'Pods': () => refetchPods(),
      'Services': () => refetchServices(),
      'Namespaces': () => refetchNamespaces(),
      'Storage': () => refetchStorage(),
      'Service Accounts': () => refetchSA(),
    }
    refetchMap[activeTab]()
  }

  const isLoading = {
    'Clusters': loadingClusters,
    'Nodes': loadingNodes,
    'Pods': loadingPods,
    'Services': loadingServices,
    'Namespaces': loadingNamespaces,
    'Storage': loadingStorage,
    'Service Accounts': loadingSA,
  }[activeTab]

  const clusters = clustersData?.items || clustersData?.data || []
  const nodes = nodesData?.items || nodesData?.data || []
  const pods = podsData?.items || podsData?.data || []
  const services = servicesData?.items || servicesData?.data || []
  const namespaces = namespacesData?.items || namespacesData?.data || []
  const storage = storageData?.items || storageData?.data || []
  const serviceAccounts = saData?.items || saData?.data || []

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Ship className="h-8 w-8 text-amber-400" />
          <div>
            <h1 className="text-2xl font-bold text-white">Kubernetes</h1>
            <p className="text-sm text-slate-400">Manage and monitor Kubernetes cluster resources</p>
          </div>
        </div>
        <button
          onClick={handleRefresh}
          className="flex items-center gap-2 px-4 py-2 bg-slate-700 text-slate-200 rounded-lg hover:bg-slate-600 transition-colors"
        >
          <RefreshCw className="h-4 w-4" />
          Refresh
        </button>
      </div>

      <div className="border-b border-slate-700">
        <nav className="flex gap-1 -mb-px">
          {TABS.map((tab) => {
            const Icon = TAB_ICONS[tab]
            return (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
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
        {isLoading && (
          <div className="flex items-center justify-center py-12">
            <RefreshCw className="h-6 w-6 text-amber-400 animate-spin" />
            <span className="ml-2 text-slate-400">Loading...</span>
          </div>
        )}

        {activeTab === 'Clusters' && !loadingClusters && (
          <div className="space-y-4">
            {clusters.length === 0 ? (
              <Card><CardContent>
                <div className="flex flex-col items-center justify-center py-12 text-slate-400">
                  <Ship className="h-12 w-12 mb-4 opacity-50" />
                  <p className="text-lg font-medium">No Kubernetes clusters discovered</p>
                  <p className="text-sm mt-1">Run a Kubernetes discovery job to see clusters here</p>
                </div>
              </CardContent></Card>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {clusters.map((cluster: any) => (
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
            )}
          </div>
        )}

        {activeTab === 'Nodes' && !loadingNodes && (
          <div className="space-y-4">
            {nodes.length === 0 ? (
              <Card><CardContent>
                <div className="flex flex-col items-center justify-center py-12 text-slate-400">
                  <Server className="h-12 w-12 mb-4 opacity-50" />
                  <p className="text-lg font-medium">No nodes discovered</p>
                  <p className="text-sm mt-1">Nodes appear after running Kubernetes discovery</p>
                </div>
              </CardContent></Card>
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
                    {nodes.map((node: any) => {
                      const meta = node.attributes?.metadata || {}
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
            )}
          </div>
        )}

        {activeTab === 'Pods' && !loadingPods && (
          <div className="space-y-4">
            {pods.length === 0 ? (
              <Card><CardContent>
                <div className="flex flex-col items-center justify-center py-12 text-slate-400">
                  <Box className="h-12 w-12 mb-4 opacity-50" />
                  <p className="text-lg font-medium">No pods discovered</p>
                  <p className="text-sm mt-1">Pods appear after running Kubernetes discovery</p>
                </div>
              </CardContent></Card>
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
                    {pods.map((pod: any) => {
                      const meta = pod.attributes?.metadata || {}
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
            )}
          </div>
        )}

        {activeTab === 'Services' && !loadingServices && (
          <div className="space-y-4">
            {services.length === 0 ? (
              <Card><CardContent>
                <div className="flex flex-col items-center justify-center py-12 text-slate-400">
                  <Network className="h-12 w-12 mb-4 opacity-50" />
                  <p className="text-lg font-medium">No Kubernetes services found</p>
                  <p className="text-sm mt-1">K8s services appear after running discovery</p>
                </div>
              </CardContent></Card>
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
                    {services.map((svc: any) => (
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
            )}
          </div>
        )}

        {activeTab === 'Namespaces' && !loadingNamespaces && (
          <div className="space-y-4">
            {namespaces.length === 0 ? (
              <Card><CardContent>
                <div className="flex flex-col items-center justify-center py-12 text-slate-400">
                  <Layers className="h-12 w-12 mb-4 opacity-50" />
                  <p className="text-lg font-medium">No namespaces discovered</p>
                  <p className="text-sm mt-1">Namespaces appear after running Kubernetes discovery</p>
                </div>
              </CardContent></Card>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {namespaces.map((ns: any) => (
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
                            {ns.tags.map((tag: string, i: number) => (
                              <span key={i} className="px-2 py-0.5 text-xs rounded bg-slate-700 text-slate-300">{tag}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === 'Storage' && !loadingStorage && (
          <div className="space-y-4">
            {storage.length === 0 ? (
              <Card><CardContent>
                <div className="flex flex-col items-center justify-center py-12 text-slate-400">
                  <HardDrive className="h-12 w-12 mb-4 opacity-50" />
                  <p className="text-lg font-medium">No Kubernetes storage found</p>
                  <p className="text-sm mt-1">Persistent volumes appear after running discovery</p>
                </div>
              </CardContent></Card>
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
                    {storage.map((ds: any) => (
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
            )}
          </div>
        )}

        {activeTab === 'Service Accounts' && !loadingSA && (
          <div className="space-y-4">
            {serviceAccounts.length === 0 ? (
              <Card><CardContent>
                <div className="flex flex-col items-center justify-center py-12 text-slate-400">
                  <Shield className="h-12 w-12 mb-4 opacity-50" />
                  <p className="text-lg font-medium">No service accounts discovered</p>
                  <p className="text-sm mt-1">K8s service accounts appear after running discovery</p>
                </div>
              </CardContent></Card>
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
                    {serviceAccounts.map((sa: any) => (
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
            )}
          </div>
        )}
      </div>
    </div>
  )
}
