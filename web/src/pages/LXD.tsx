import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Container,
  Monitor,
  HardDrive,
  Network,
  RefreshCw,
  Plus,
} from 'lucide-react'
import api from '@/lib/api'
import Card, { CardContent } from '@/components/Card'

const TABS = ['Overview', 'Containers', 'VMs', 'Storage Pools', 'Networks'] as const
type Tab = typeof TABS[number]

const TAB_ICONS: Record<Tab, typeof Container> = {
  'Overview': Container,
  'Containers': Container,
  'VMs': Monitor,
  'Storage Pools': HardDrive,
  'Networks': Network,
}

function EmptyState({ icon: Icon, title, description }: { icon: typeof Container; title: string; description: string }) {
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

export default function LXD() {
  const [activeTab, setActiveTab] = useState<Tab>('Overview')

  const { data: containersData, isLoading: loadingContainers } = useQuery({
    queryKey: ['lxd-containers'],
    queryFn: () => api.getEntities({ entity_type: 'compute', sub_type: 'lxd_container' }),
    enabled: activeTab === 'Containers' || activeTab === 'Overview',
  })

  const { data: vmsData, isLoading: loadingVMs } = useQuery({
    queryKey: ['lxd-vms'],
    queryFn: () => api.getEntities({ entity_type: 'compute', sub_type: 'lxd_vm' }),
    enabled: activeTab === 'VMs' || activeTab === 'Overview',
  })

  const { data: storageData, isLoading: loadingStorage } = useQuery({
    queryKey: ['lxd-storage'],
    queryFn: () => api.getDataStores({ storage_type: 'other' }),
    enabled: activeTab === 'Storage Pools' || activeTab === 'Overview',
  })

  const { data: networksData, isLoading: loadingNetworks } = useQuery({
    queryKey: ['lxd-networks'],
    queryFn: () => api.listNetworks({ network_type: 'other' }),
    enabled: activeTab === 'Networks' || activeTab === 'Overview',
  })

  const containers = (containersData?.items || containersData?.data || []).filter(
    (c: any) => c.sub_type === 'lxd_container'
  )
  const vms = (vmsData?.items || vmsData?.data || []).filter(
    (v: any) => v.sub_type === 'lxd_vm'
  )
  const storagePools = (storageData?.items || storageData?.data || []).filter(
    (s: any) => (s.tags || []).includes('lxd')
  )
  const networks = (networksData?.items || networksData?.data || []).filter(
    (n: any) => (n.tags || []).includes('lxd')
  )

  const totalResources = containers.length + vms.length + storagePools.length + networks.length
  const isAnyLoading = loadingContainers || loadingVMs || loadingStorage || loadingNetworks

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Container className="h-8 w-8 text-amber-400" />
          <div>
            <h1 className="text-2xl font-bold text-white">LXD</h1>
            <p className="text-sm text-slate-400">Manage LXD containers, VMs, and infrastructure</p>
          </div>
        </div>
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
        {activeTab === 'Overview' && (
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

        {activeTab === 'Containers' && !loadingContainers && (
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
                  {containers.map((c: any) => (
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

        {activeTab === 'VMs' && !loadingVMs && (
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
                  {vms.map((vm: any) => (
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

        {activeTab === 'Storage Pools' && !loadingStorage && (
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
                  {storagePools.map((s: any) => (
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

        {activeTab === 'Networks' && !loadingNetworks && (
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
                  {networks.map((n: any) => (
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

        {isAnyLoading && activeTab !== 'Overview' && (
          <div className="flex items-center justify-center py-12">
            <RefreshCw className="h-6 w-6 text-amber-400 animate-spin" />
            <span className="ml-2 text-slate-400">Loading...</span>
          </div>
        )}
      </div>
    </div>
  )
}
