import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Edit, Trash2, Server, Globe, Lock, ExternalLink, Clock, Play } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { invalidateCache } from '@/lib/invalidateCache'
import { getStatusColor } from '@/lib/colorHelpers'
import { confirmDelete } from '@/lib/confirmActions'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import { FormModalBuilder, FormField } from '@penguintechinc/react-libs/components'
import OnCallBadge from '@/components/OnCallBadge'

const SERVICE_STATUSES = [
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
  { value: 'maintenance', label: 'Maintenance' },
  { value: 'deprecated', label: 'Deprecated' },
]

const LANGUAGES = [
  { value: 'python', label: 'Python' },
  { value: 'go', label: 'Go' },
  { value: 'javascript', label: 'JavaScript' },
  { value: 'typescript', label: 'TypeScript' },
  { value: 'java', label: 'Java' },
  { value: 'rust', label: 'Rust' },
  { value: 'ruby', label: 'Ruby' },
  { value: 'php', label: 'PHP' },
  { value: 'csharp', label: 'C#' },
  { value: 'other', label: 'Other' },
]

const DEPLOYMENT_METHODS = [
  { value: 'kubernetes', label: 'Kubernetes' },
  { value: 'docker', label: 'Docker' },
  { value: 'docker-compose', label: 'Docker Compose' },
  { value: 'serverless', label: 'Serverless' },
  { value: 'vm', label: 'Virtual Machine' },
  { value: 'bare-metal', label: 'Bare Metal' },
  { value: 'paas', label: 'PaaS' },
  { value: 'other', label: 'Other' },
]

export default function Services() {
  const [search, setSearch] = useState('')
  const [organizationFilter, setOrganizationFilter] = useState<string>('')
  const [languageFilter, setLanguageFilter] = useState<string>('')
  const [deploymentFilter, setDeploymentFilter] = useState<string>('')
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingService, setEditingService] = useState<any>(null)
  const [viewingService, setViewingService] = useState<any>(null)
  const queryClient = useQueryClient()

  const { data: organizations } = useQuery({
    queryKey: queryKeys.organizations.dropdown,
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.services.list({ search, organization_id: organizationFilter, language: languageFilter, deployment_method: deploymentFilter, status: statusFilter }),
    queryFn: () => api.getServices({
      search,
      organization_id: organizationFilter ? parseInt(organizationFilter) : undefined,
      language: languageFilter || undefined,
      deployment_method: deploymentFilter || undefined,
      status: statusFilter || undefined,
    }),
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.createService(data),
    onSuccess: async () => {
      await invalidateCache.services(queryClient)
      toast.success('Service created successfully')
      setShowCreateModal(false)
    },
    onError: () => {
      toast.error('Failed to create service')
    },
  })

  const updateMutation = useMutation({
    mutationFn: (data: any) => api.updateService(editingService.id, data),
    onSuccess: async () => {
      await invalidateCache.services(queryClient)
      toast.success('Service updated successfully')
      setEditingService(null)
    },
    onError: () => {
      toast.error('Failed to update service')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteService(id),
    onSuccess: async () => {
      await invalidateCache.services(queryClient)
      toast.success('Service deleted successfully')
    },
    onError: () => {
      toast.error('Failed to delete service')
    },
  })

  const handleDelete = (id: number, name: string) => {
    confirmDelete(`service "${name}"`, () => {
      deleteMutation.mutate(id)
    })
  }

  const getLanguageColor = (language: string) => {
    switch (language) {
      case 'python':
        return 'bg-blue-500/20 text-blue-400'
      case 'go':
        return 'bg-cyan-500/20 text-cyan-400'
      case 'javascript':
      case 'typescript':
        return 'bg-yellow-500/20 text-yellow-400'
      case 'java':
        return 'bg-orange-500/20 text-orange-400'
      case 'rust':
        return 'bg-red-500/20 text-red-400'
      default:
        return 'bg-purple-500/20 text-purple-400'
    }
  }

  // Build organization options for form
  const organizationOptions = organizations?.items?.map((org: any) => ({
    value: org.id.toString(),
    label: org.name,
  })) || []

  // Form fields for create modal
  const serviceFields: FormField[] = useMemo(() => [
    {
      name: 'name',
      label: 'Name',
      type: 'text',
      required: true,
      placeholder: 'api-gateway',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'Service description (optional)',
      rows: 2,
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      options: [
        { value: '', label: organizationOptions.length ? 'Select organization' : 'No organizations found' },
        ...organizationOptions,
      ],
    },
    {
      name: 'language',
      label: 'Language',
      type: 'select',
      options: LANGUAGES,
      defaultValue: 'python',
    },
    {
      name: 'deployment_method',
      label: 'Deployment Method',
      type: 'select',
      options: DEPLOYMENT_METHODS,
      defaultValue: 'kubernetes',
    },
    {
      name: 'status',
      label: 'Status',
      type: 'select',
      options: SERVICE_STATUSES,
      defaultValue: 'active',
    },
    {
      name: 'port',
      label: 'Port',
      type: 'number',
      placeholder: '8080',
    },
    {
      name: 'is_public',
      label: 'Public service (accessible from internet)',
      type: 'checkbox',
      defaultValue: false,
    },
    {
      name: 'domains',
      label: 'Domains (one per line)',
      type: 'multiline',
      placeholder: 'api.example.com\napi-v2.example.com',
      rows: 2,
    },
    {
      name: 'paths',
      label: 'Paths (one per line)',
      type: 'multiline',
      placeholder: '/api/v1\n/api/v2',
      rows: 3,
    },
  ], [organizationOptions])

  // Form fields for edit modal with defaultValues from editingService
  const editFields: FormField[] = useMemo(() => {
    if (!editingService) return []
    return [
      {
        name: 'name',
        label: 'Name',
        type: 'text',
        required: true,
        placeholder: 'api-gateway',
        defaultValue: editingService.name || '',
      },
      {
        name: 'description',
        label: 'Description',
        type: 'textarea',
        placeholder: 'Service description (optional)',
        rows: 2,
        defaultValue: editingService.description || '',
      },
      {
        name: 'organization_id',
        label: 'Organization',
        type: 'select',
        required: true,
        options: [
          { value: '', label: organizationOptions.length ? 'Select organization' : 'No organizations found' },
          ...organizationOptions,
        ],
        defaultValue: editingService.organization_id?.toString() || '',
      },
      {
        name: 'language',
        label: 'Language',
        type: 'select',
        options: LANGUAGES,
        defaultValue: editingService.language || 'python',
      },
      {
        name: 'deployment_method',
        label: 'Deployment Method',
        type: 'select',
        options: DEPLOYMENT_METHODS,
        defaultValue: editingService.deployment_method || 'kubernetes',
      },
      {
        name: 'status',
        label: 'Status',
        type: 'select',
        options: SERVICE_STATUSES,
        defaultValue: editingService.status || 'active',
      },
      {
        name: 'port',
        label: 'Port',
        type: 'number',
        placeholder: '8080',
        defaultValue: editingService.port?.toString() || '',
      },
      {
        name: 'is_public',
        label: 'Public service (accessible from internet)',
        type: 'checkbox',
        defaultValue: editingService.is_public || false,
      },
      {
        name: 'domains',
        label: 'Domains (one per line)',
        type: 'multiline',
        placeholder: 'api.example.com\napi-v2.example.com',
        rows: 2,
        defaultValue: editingService.domains?.join('\n') || '',
      },
      {
        name: 'paths',
        label: 'Paths (one per line)',
        type: 'multiline',
        placeholder: '/api/v1\n/api/v2',
        rows: 3,
        defaultValue: editingService.paths?.join('\n') || '',
      },
    ]
  }, [editingService, organizationOptions])

  const handleCreateSubmit = (data: Record<string, any>) => {
    createMutation.mutate({
      ...data,
      organization_id: parseInt(data.organization_id),
    })
  }

  const handleEditSubmit = (data: Record<string, any>) => {
    updateMutation.mutate({
      ...data,
      organization_id: parseInt(data.organization_id),
    })
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Services</h1>
          <p className="mt-2 text-slate-400">
            Track and manage microservices across your infrastructure
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Service
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4 mb-6">
        <div className="flex-1 min-w-[200px] max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search services..."
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
          value={languageFilter}
          onChange={(e) => setLanguageFilter(e.target.value)}
          className="w-40"
        >
          <option value="">All Languages</option>
          {LANGUAGES.map((lang) => (
            <option key={lang.value} value={lang.value}>
              {lang.label}
            </option>
          ))}
        </Select>
        <Select
          value={deploymentFilter}
          onChange={(e) => setDeploymentFilter(e.target.value)}
          className="w-44"
        >
          <option value="">All Deployments</option>
          {DEPLOYMENT_METHODS.map((method) => (
            <option key={method.value} value={method.value}>
              {method.label}
            </option>
          ))}
        </Select>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="w-36"
        >
          <option value="">All Statuses</option>
          {SERVICE_STATUSES.map((status) => (
            <option key={status.value} value={status.value}>
              {status.label}
            </option>
          ))}
        </Select>
      </div>

      {/* Services List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <Server className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No services found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first service
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data?.items?.map((service: any) => (
            <Card
              key={service.id}
              className="cursor-pointer hover:border-primary-500/50 transition-colors"
              onClick={() => setViewingService(service)}
            >
              <CardContent>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <Server className="w-5 h-5 text-primary-400 flex-shrink-0" />
                    <h3 className="text-lg font-semibold text-white truncate">
                      {service.name}
                    </h3>
                    {service.is_public ? (
                      <span title="Public"><Globe className="w-4 h-4 text-green-400 flex-shrink-0" /></span>
                    ) : (
                      <span title="Private"><Lock className="w-4 h-4 text-slate-400 flex-shrink-0" /></span>
                    )}
                  </div>
                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setEditingService(service)
                      }}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDelete(service.id, service.name)
                      }}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {service.description && (
                  <p className="text-sm text-slate-400 mb-3 line-clamp-2">
                    {service.description}
                  </p>
                )}

                <div className="flex flex-wrap gap-2 mb-3">
                  <span className={`text-xs px-2 py-1 rounded ${getStatusColor(service.status)}`}>
                    {service.status}
                  </span>
                  <span className={`text-xs px-2 py-1 rounded ${getLanguageColor(service.language)}`}>
                    {service.language}
                  </span>
                  <span className="text-xs px-2 py-1 rounded bg-slate-700 text-slate-300">
                    {service.deployment_method}
                  </span>
                </div>

                {service.port && (
                  <div className="text-xs text-slate-500 mb-3">
                    Port: {service.port}
                  </div>
                )}

                <div className="mb-3">
                  <OnCallBadge scopeType="service" scopeId={service.id} compact />
                </div>

                {service.domains && service.domains.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {service.domains.slice(0, 2).map((domain: string, idx: number) => (
                      <span key={idx} className="text-xs px-2 py-0.5 rounded bg-primary-500/20 text-primary-400">
                        {domain}
                      </span>
                    ))}
                    {service.domains.length > 2 && (
                      <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-400">
                        +{service.domains.length - 2} more
                      </span>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      <FormModalBuilder
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        title="Create Service"
        fields={serviceFields}
        onSubmit={handleCreateSubmit}
        submitButtonText="Create"
      />

      {/* Edit Modal */}
      <FormModalBuilder
        isOpen={!!editingService}
        onClose={() => setEditingService(null)}
        title="Edit Service"
        fields={editFields}
        onSubmit={handleEditSubmit}
        submitButtonText="Update"
      />

      {/* View Details Modal */}
      {viewingService && (
        <ServiceDetailsModal
          service={viewingService}
          onClose={() => setViewingService(null)}
          onEdit={() => {
            setEditingService(viewingService)
            setViewingService(null)
          }}
        />
      )}
    </div>
  )
}

interface ServiceDetailsModalProps {
  service: any
  onClose: () => void
  onEdit: () => void
}

function ServiceDetailsModal({ service, onClose, onEdit }: ServiceDetailsModalProps) {
  const [activeTab, setActiveTab] = useState<'details' | 'schedules'>('details')

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Server className="w-5 h-5 text-primary-400" />
              <h2 className="text-xl font-semibold text-white">{service.name}</h2>
              {service.is_public ? (
                <span title="Public"><Globe className="w-4 h-4 text-green-400" /></span>
              ) : (
                <span title="Private"><Lock className="w-4 h-4 text-slate-400" /></span>
              )}
            </div>
            <button
              onClick={onEdit}
              className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
            >
              <Edit className="w-4 h-4" />
            </button>
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
            <ServiceDetailsTab service={service} />
          ) : (
            <ServiceSchedulesTab service={service} />
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

function ServiceDetailsTab({ service }: { service: any }) {
  return (
    <div className="space-y-4">
      {service.description && (
        <p className="text-sm text-slate-400">{service.description}</p>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="text-xs text-slate-500 uppercase">Status</label>
          <div className="mt-1">
            <span className={`text-sm px-2 py-1 rounded ${getStatusColor(service.status)}`}>
              {service.status}
            </span>
          </div>
        </div>
        <div>
          <label className="text-xs text-slate-500 uppercase">Language</label>
          <p className="text-sm text-white mt-1">{service.language}</p>
        </div>
        <div>
          <label className="text-xs text-slate-500 uppercase">Deployment</label>
          <p className="text-sm text-white mt-1">{service.deployment_method}</p>
        </div>
        {service.port && (
          <div>
            <label className="text-xs text-slate-500 uppercase">Port</label>
            <p className="text-sm text-white mt-1">{service.port}</p>
          </div>
        )}
      </div>

      {service.domains && service.domains.length > 0 && (
        <div>
          <label className="text-xs text-slate-500 uppercase">Domains</label>
          <div className="flex flex-wrap gap-2 mt-2">
            {service.domains.map((domain: string, idx: number) => (
              <span key={idx} className="text-sm px-2 py-1 rounded bg-primary-500/20 text-primary-400 flex items-center gap-1">
                {domain}
                <ExternalLink className="w-3 h-3" />
              </span>
            ))}
          </div>
        </div>
      )}

      {service.paths && service.paths.length > 0 && (
        <div>
          <label className="text-xs text-slate-500 uppercase">Paths</label>
          <div className="flex flex-wrap gap-2 mt-2">
            {service.paths.map((path: string, idx: number) => (
              <span key={idx} className="text-sm px-2 py-1 rounded bg-slate-700 text-slate-300">
                {path}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ServiceSchedulesTab({ service }: { service: any }) {
  const [showCreateSchedule, setShowCreateSchedule] = useState(false)
  const queryClient = useQueryClient()

  // Fetch schedules for this service
  const { data: schedules, isLoading } = useQuery({
    queryKey: ['sbom-schedules', 'service', service.id],
    queryFn: () => api.getSBOMSchedules({
      parent_type: 'service',
      parent_id: service.id
    }),
  })

  // Fetch recent scans for this service
  const { data: scans } = useQuery({
    queryKey: ['sbom-scans', 'service', service.id],
    queryFn: () => api.getSBOMScans({
      parent_type: 'service',
      parent_id: service.id,
      per_page: 5
    }),
  })

  const createScheduleMutation = useMutation({
    mutationFn: (data: any) => api.createSBOMSchedule({
      parent_type: 'service',
      parent_id: service.id,
      schedule_cron: data.schedule_cron,
      is_active: data.is_active !== false,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sbom-schedules', 'service', service.id] })
      toast.success('Schedule created successfully')
      setShowCreateSchedule(false)
    },
    onError: () => toast.error('Failed to create schedule'),
  })

  const deleteScheduleMutation = useMutation({
    mutationFn: (id: number) => api.deleteSBOMSchedule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sbom-schedules', 'service', service.id] })
      toast.success('Schedule deleted successfully')
    },
    onError: () => toast.error('Failed to delete schedule'),
  })

  // Trigger an immediate SBOM scan
  const runScanMutation = useMutation({
    mutationFn: () => api.createSBOMScan({
      parent_type: 'service',
      parent_id: service.id,
      scan_type: 'git_clone',
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sbom-scans', 'service', service.id] })
      toast.success('SBOM scan started')
    },
    onError: () => toast.error('Failed to start SBOM scan'),
  })

  // Schedule form fields using useMemo
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
        isOpen={showCreateSchedule}
        title="Create SBOM Schedule"
        fields={scheduleFields}
        onSubmit={(data) => createScheduleMutation.mutate(data)}
        onClose={() => setShowCreateSchedule(false)}
        submitButtonText="Create"
      />
    </div>
  )
}
