import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Edit, Trash2, Package, Calendar, DollarSign, ExternalLink, Clock, Play } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { invalidateCache } from '@/lib/invalidateCache'
import { confirmDelete } from '@/lib/confirmActions'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

const SOFTWARE_TYPES = [
  { value: 'saas', label: 'SaaS' },
  { value: 'on_premise', label: 'On-Premise' },
  { value: 'desktop', label: 'Desktop' },
  { value: 'mobile', label: 'Mobile' },
  { value: 'open_source', label: 'Open Source' },
  { value: 'other', label: 'Other' },
]

const formatCurrency = (amount: number | null): string => {
  if (amount === null || amount === undefined) return '-'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(amount)
}

export default function Software() {
  const [search, setSearch] = useState('')
  const [organizationFilter, setOrganizationFilter] = useState<string>('')
  const [typeFilter, setTypeFilter] = useState<string>('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingSoftware, setEditingSoftware] = useState<any>(null)
  const [viewingSoftware, setViewingSoftware] = useState<any>(null)
  const queryClient = useQueryClient()

  const { data: organizations } = useQuery({
    queryKey: queryKeys.organizations.dropdown,
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.software.list({ search, organization_id: organizationFilter, software_type: typeFilter }),
    queryFn: () => api.getSoftware({
      search,
      organization_id: organizationFilter ? parseInt(organizationFilter) : undefined,
      software_type: typeFilter || undefined
    }),
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.createSoftware(data),
    onSuccess: async () => {
      await invalidateCache.software(queryClient)
      toast.success('Software added successfully')
      setShowCreateModal(false)
    },
    onError: () => {
      toast.error('Failed to add software')
    },
  })

  const updateMutation = useMutation({
    mutationFn: (data: any) => api.updateSoftware(editingSoftware.id, data),
    onSuccess: async () => {
      await invalidateCache.software(queryClient)
      toast.success('Software updated successfully')
      setEditingSoftware(null)
    },
    onError: () => {
      toast.error('Failed to update software')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteSoftware(id),
    onSuccess: async () => {
      await invalidateCache.software(queryClient)
      toast.success('Software deleted successfully')
    },
    onError: () => {
      toast.error('Failed to delete software')
    },
  })

  const handleDelete = (id: number, name: string) => {
    confirmDelete(name, () => deleteMutation.mutate(id))
  }

  const getSoftwareTypeColor = (type: string): string => {
    const typeMap: { [key: string]: string } = {
      'saas': 'bg-blue-500/20 text-blue-400',
      'on_premise': 'bg-purple-500/20 text-purple-400',
      'desktop': 'bg-green-500/20 text-green-400',
      'mobile': 'bg-orange-500/20 text-orange-400',
      'open_source': 'bg-cyan-500/20 text-cyan-400',
    }
    return typeMap[type] || 'bg-slate-500/20 text-slate-400'
  }

  const organizationOptions = organizations?.items?.map((org: any) => ({
    value: org.id.toString(),
    label: org.name,
  })) || []

  // Form fields for software creation
  const createFields: FormField[] = useMemo(() => [
    {
      name: 'name',
      label: 'Name',
      type: 'text',
      required: true,
      placeholder: 'Microsoft 365',
    },
    {
      name: 'vendor',
      label: 'Vendor',
      type: 'text',
      placeholder: 'Microsoft',
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      options: organizationOptions,
      placeholder: organizationOptions.length ? 'Select organization' : 'No organizations found',
    },
    {
      name: 'software_type',
      label: 'Software Type',
      type: 'select',
      options: SOFTWARE_TYPES.map(t => ({ value: t.value, label: t.label })),
      defaultValue: 'saas',
    },
    {
      name: 'version',
      label: 'Version',
      type: 'text',
      placeholder: '2024.1',
    },
    {
      name: 'seats',
      label: 'Seats',
      type: 'number',
      placeholder: '50',
    },
    {
      name: 'cost_monthly',
      label: 'Monthly Cost',
      type: 'number',
      placeholder: '500.00',
    },
    {
      name: 'renewal_date',
      label: 'Renewal Date',
      type: 'date',
    },
    {
      name: 'license_url',
      label: 'License URL',
      type: 'url',
      placeholder: 'https://portal.vendor.com/license',
    },
  ], [organizationOptions])

  // Edit form fields with current values as defaults
  const editFields: FormField[] = useMemo(() => {
    if (!editingSoftware) return createFields
    return [
      {
        name: 'name',
        label: 'Name',
        type: 'text',
        required: true,
        placeholder: 'Microsoft 365',
        defaultValue: editingSoftware.name,
      },
      {
        name: 'vendor',
        label: 'Vendor',
        type: 'text',
        placeholder: 'Microsoft',
        defaultValue: editingSoftware.vendor || '',
      },
      {
        name: 'organization_id',
        label: 'Organization',
        type: 'select',
        required: true,
        options: organizationOptions,
        placeholder: organizationOptions.length ? 'Select organization' : 'No organizations found',
        defaultValue: editingSoftware.organization_id?.toString(),
      },
      {
        name: 'software_type',
        label: 'Software Type',
        type: 'select',
        options: SOFTWARE_TYPES.map(t => ({ value: t.value, label: t.label })),
        defaultValue: editingSoftware.software_type || 'saas',
      },
      {
        name: 'version',
        label: 'Version',
        type: 'text',
        placeholder: '2024.1',
        defaultValue: editingSoftware.version || '',
      },
      {
        name: 'seats',
        label: 'Seats',
        type: 'number',
        placeholder: '50',
        defaultValue: editingSoftware.seats || '',
      },
      {
        name: 'cost_monthly',
        label: 'Monthly Cost',
        type: 'number',
        placeholder: '500.00',
        defaultValue: editingSoftware.cost_monthly || '',
      },
      {
        name: 'renewal_date',
        label: 'Renewal Date',
        type: 'date',
        defaultValue: editingSoftware.renewal_date || '',
      },
      {
        name: 'license_url',
        label: 'License URL',
        type: 'url',
        placeholder: 'https://portal.vendor.com/license',
        defaultValue: editingSoftware.license_url || '',
      },
    ]
  }, [editingSoftware, organizationOptions, createFields])

  const handleCreateSubmit = (data: Record<string, any>) => {
    createMutation.mutate({
      name: data.name?.trim(),
      vendor: data.vendor?.trim() || undefined,
      software_type: data.software_type,
      version: data.version?.trim() || undefined,
      seats: data.seats ? parseInt(data.seats) : undefined,
      cost_monthly: data.cost_monthly ? parseFloat(data.cost_monthly) : undefined,
      renewal_date: data.renewal_date || undefined,
      license_url: data.license_url?.replace(/\s+/g, '') || undefined,
      organization_id: parseInt(data.organization_id),
    })
  }

  const handleEditSubmit = (data: Record<string, any>) => {
    updateMutation.mutate({
      name: data.name?.trim(),
      vendor: data.vendor?.trim() || undefined,
      software_type: data.software_type,
      version: data.version?.trim() || undefined,
      seats: data.seats ? parseInt(data.seats) : undefined,
      cost_monthly: data.cost_monthly ? parseFloat(data.cost_monthly) : undefined,
      renewal_date: data.renewal_date || undefined,
      license_url: data.license_url?.replace(/\s+/g, '') || undefined,
      organization_id: parseInt(data.organization_id),
    })
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Software</h1>
          <p className="mt-2 text-slate-400">
            Track and manage software licenses and subscriptions
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Add Software
        </Button>
      </div>

      {/* Filters */}
      <div className="flex gap-4 mb-6">
        <div className="flex-1 max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search software..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <Select
          value={organizationFilter}
          onChange={(e) => setOrganizationFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Organizations</option>
          {organizations?.items?.map((org: any) => (
            <option key={org.id} value={org.id}>
              {org.name}
            </option>
          ))}
        </Select>
        <Select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Types</option>
          {SOFTWARE_TYPES.map((type) => (
            <option key={type.value} value={type.value}>
              {type.label}
            </option>
          ))}
        </Select>
      </div>

      {/* Software List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <Package className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No software found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Add your first software
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data?.items?.map((software: any) => (
            <Card
              key={software.id}
              className="cursor-pointer hover:border-primary-500/50 transition-colors"
              onClick={() => setViewingSoftware(software)}
            >
              <CardContent>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <Package className="w-5 h-5 text-primary-400 flex-shrink-0" />
                    <h3 className="text-lg font-semibold text-white truncate">
                      {software.name}
                    </h3>
                  </div>
                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setEditingSoftware(software)
                      }}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDelete(software.id, software.name)
                      }}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {software.vendor && (
                  <p className="text-sm text-slate-400 mb-2">
                    {software.vendor}
                  </p>
                )}

                <div className="flex items-center justify-between mb-3">
                  <span className={`text-xs px-2 py-1 rounded ${getSoftwareTypeColor(software.software_type)}`}>
                    {software.software_type?.replace('_', ' ')}
                  </span>
                  {software.version && (
                    <span className="text-xs text-slate-500">
                      v{software.version}
                    </span>
                  )}
                </div>

                <div className="space-y-1 text-xs text-slate-400">
                  {software.seats && (
                    <div>Seats: {software.seats}</div>
                  )}
                  {software.cost_monthly !== null && software.cost_monthly !== undefined && (
                    <div className="flex items-center gap-1">
                      <DollarSign className="w-3 h-3" />
                      {formatCurrency(software.cost_monthly)}/mo
                    </div>
                  )}
                  {software.renewal_date && (
                    <div className="flex items-center gap-1">
                      <Calendar className="w-3 h-3" />
                      Renews: {new Date(software.renewal_date).toLocaleDateString()}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      <FormModalBuilder
        title="Add Software"
        fields={createFields}
        isOpen={showCreateModal}
        onSubmit={handleCreateSubmit}
        onClose={() => setShowCreateModal(false)}
        submitButtonText="Add"
      />

      {/* Edit Modal */}
      <FormModalBuilder
        title="Edit Software"
        fields={editFields}
        isOpen={!!editingSoftware}
        onSubmit={handleEditSubmit}
        onClose={() => setEditingSoftware(null)}
        submitButtonText="Update"
      />

      {/* View Details Modal */}
      {viewingSoftware && (
        <SoftwareDetailModal
          software={viewingSoftware}
          onClose={() => setViewingSoftware(null)}
          onEdit={() => {
            setEditingSoftware(viewingSoftware)
            setViewingSoftware(null)
          }}
        />
      )}
    </div>
  )
}

interface SoftwareDetailModalProps {
  software: any
  onClose: () => void
  onEdit: () => void
}

function SoftwareDetailModal({ software, onClose, onEdit }: SoftwareDetailModalProps) {
  const [activeTab, setActiveTab] = useState<'details' | 'schedules'>('details')

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">{software.name}</h2>
            <Button variant="ghost" size="sm" onClick={onEdit}>
              <Edit className="w-4 h-4 mr-1" />
              Edit
            </Button>
          </div>
          <div className="flex gap-4 mt-4">
            <button
              onClick={() => setActiveTab('details')}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'details'
                  ? 'border-primary-500 text-primary-400'
                  : 'border-transparent text-slate-400 hover:text-white'
              }`}
            >
              Details
            </button>
            <button
              onClick={() => setActiveTab('schedules')}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'schedules'
                  ? 'border-primary-500 text-primary-400'
                  : 'border-transparent text-slate-400 hover:text-white'
              }`}
            >
              SBOM Schedules
            </button>
          </div>
        </CardHeader>
        <CardContent className="overflow-y-auto flex-1">
          {activeTab === 'details' ? (
            <SoftwareDetailsTab software={software} />
          ) : (
            <SoftwareSchedulesTab software={software} />
          )}
        </CardContent>
        <div className="border-t border-slate-700 p-4 flex justify-end">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </Card>
    </div>
  )
}

function SoftwareDetailsTab({ software }: { software: any }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="text-xs text-slate-500 uppercase">Vendor</label>
          <p className="text-white">{software.vendor || '-'}</p>
        </div>
        <div>
          <label className="text-xs text-slate-500 uppercase">Type</label>
          <p className="text-white capitalize">{software.software_type?.replace('_', ' ') || '-'}</p>
        </div>
        <div>
          <label className="text-xs text-slate-500 uppercase">Version</label>
          <p className="text-white">{software.version || '-'}</p>
        </div>
        <div>
          <label className="text-xs text-slate-500 uppercase">Seats</label>
          <p className="text-white">{software.seats || '-'}</p>
        </div>
        <div>
          <label className="text-xs text-slate-500 uppercase">Monthly Cost</label>
          <p className="text-white">{formatCurrency(software.cost_monthly)}</p>
        </div>
        <div>
          <label className="text-xs text-slate-500 uppercase">Renewal Date</label>
          <p className="text-white">
            {software.renewal_date
              ? new Date(software.renewal_date).toLocaleDateString()
              : '-'}
          </p>
        </div>
      </div>

      {software.license_url && (
        <div>
          <label className="text-xs text-slate-500 uppercase">License URL</label>
          <a
            href={software.license_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-primary-400 hover:text-primary-300"
          >
            {software.license_url}
            <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      )}
    </div>
  )
}

function SoftwareSchedulesTab({ software }: { software: any }) {
  const [showCreateSchedule, setShowCreateSchedule] = useState(false)
  const queryClient = useQueryClient()

  // Fetch schedules for this software
  const { data: schedules, isLoading } = useQuery({
    queryKey: ['sbom-schedules', 'software', software.id],
    queryFn: () => api.getSBOMSchedules({
      parent_type: 'software',
      parent_id: software.id
    }),
  })

  // Fetch recent scans for this software
  const { data: scans } = useQuery({
    queryKey: ['sbom-scans', 'software', software.id],
    queryFn: () => api.getSBOMScans({
      parent_type: 'software',
      parent_id: software.id,
      per_page: 5
    }),
  })

  const createScheduleMutation = useMutation({
    mutationFn: (data: any) => api.createSBOMSchedule({
      parent_type: 'software',
      parent_id: software.id,
      schedule_cron: data.schedule_cron,
      is_active: data.is_active !== false,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sbom-schedules', 'software', software.id] })
      toast.success('Schedule created successfully')
      setShowCreateSchedule(false)
    },
    onError: () => toast.error('Failed to create schedule'),
  })

  const deleteScheduleMutation = useMutation({
    mutationFn: (id: number) => api.deleteSBOMSchedule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sbom-schedules', 'software', software.id] })
      toast.success('Schedule deleted successfully')
    },
    onError: () => toast.error('Failed to delete schedule'),
  })

  // Trigger an immediate SBOM scan
  const runScanMutation = useMutation({
    mutationFn: () => api.createSBOMScan({
      parent_type: 'software',
      parent_id: software.id,
      scan_type: 'git_clone',
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sbom-scans', 'software', software.id] })
      toast.success('SBOM scan started')
    },
    onError: () => toast.error('Failed to start SBOM scan'),
  })

  // Schedule form fields
  const scheduleFields: FormField[] = useMemo(() => [
    {
      name: 'schedule_cron',
      label: 'Cron Expression',
      type: 'text',
      required: true,
      placeholder: '0 0 * * *',
      helpText: 'E.g., "0 0 * * *" for daily at midnight, "0 */6 * * *" for every 6 hours',
    },
    {
      name: 'is_active',
      label: 'Enable schedule',
      type: 'checkbox',
      defaultValue: true,
    },
  ], [])

  if (isLoading) {
    return <div className="flex justify-center py-8"><div className="w-6 h-6 border-2 border-primary-600 border-t-transparent rounded-full animate-spin" /></div>
  }

  return (
    <div className="space-y-6">
      {/* Run Now Section */}
      <div className="flex justify-between items-center">
        <div>
          <h3 className="text-lg font-semibold text-white">SBOM Scans</h3>
          <p className="text-sm text-slate-400">Scan repository to discover dependencies and vulnerabilities</p>
        </div>
        <Button
          size="sm"
          onClick={() => runScanMutation.mutate()}
          disabled={runScanMutation.isPending}
        >
          <Play className="w-3 h-3 mr-1" />
          {runScanMutation.isPending ? 'Starting...' : 'Scan Now'}
        </Button>
      </div>

      {/* Recent Scans */}
      {scans?.items && scans.items.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium text-slate-400">Recent Scans</h4>
          {scans.items.map((scan: any) => (
            <div key={scan.id} className="flex items-center justify-between p-2 bg-slate-800/30 rounded text-sm">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${
                  scan.status === 'completed' ? 'bg-green-500' :
                  scan.status === 'running' ? 'bg-blue-500 animate-pulse' :
                  scan.status === 'failed' ? 'bg-red-500' : 'bg-yellow-500'
                }`} />
                <span className="text-slate-300 capitalize">{scan.status}</span>
              </div>
              <div className="flex items-center gap-4 text-slate-500">
                {scan.components_found !== undefined && (
                  <span>{scan.components_found} components</span>
                )}
                <span>{new Date(scan.created_at).toLocaleString()}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Schedules Section */}
      <div className="border-t border-slate-700 pt-4">
        <div className="flex justify-between items-center mb-4">
          <div>
            <h4 className="text-sm font-medium text-white">Scheduled Scans</h4>
            <p className="text-xs text-slate-500">Automatically scan on a recurring schedule</p>
          </div>
          <Button size="sm" variant="ghost" onClick={() => setShowCreateSchedule(true)}>
            <Plus className="w-3 h-3 mr-1" />
            Add Schedule
          </Button>
        </div>

        {!schedules?.items || schedules.items.length === 0 ? (
          <div className="text-center py-6 bg-slate-800/30 rounded">
            <Clock className="w-6 h-6 text-slate-600 mx-auto mb-2" />
            <p className="text-sm text-slate-400">No schedules configured</p>
          </div>
        ) : (
          <div className="space-y-2">
            {schedules.items.map((schedule: any) => (
              <div key={schedule.id} className="flex items-center justify-between p-3 bg-slate-800/50 rounded border border-slate-700">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Clock className="w-4 h-4 text-primary-400 flex-shrink-0" />
                    <code className="text-sm font-mono text-white">{schedule.schedule_cron}</code>
                    {schedule.is_active ? (
                      <span className="text-xs px-2 py-0.5 rounded bg-green-500/20 text-green-400">Active</span>
                    ) : (
                      <span className="text-xs px-2 py-0.5 rounded bg-slate-500/20 text-slate-400">Inactive</span>
                    )}
                  </div>
                  {schedule.next_run_at && (
                    <p className="text-xs text-slate-500">
                      Next run: {new Date(schedule.next_run_at).toLocaleString()}
                    </p>
                  )}
                  {schedule.last_run_at && (
                    <p className="text-xs text-slate-500">
                      Last run: {new Date(schedule.last_run_at).toLocaleString()}
                    </p>
                  )}
                </div>
                <button
                  onClick={() => deleteScheduleMutation.mutate(schedule.id)}
                  disabled={deleteScheduleMutation.isPending}
                  className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors disabled:opacity-50"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <FormModalBuilder
        title="Create SBOM Schedule"
        fields={scheduleFields}
        isOpen={showCreateSchedule}
        onSubmit={(data) => createScheduleMutation.mutate(data)}
        onClose={() => setShowCreateSchedule(false)}
        submitButtonText="Create"
      />
    </div>
  )
}
