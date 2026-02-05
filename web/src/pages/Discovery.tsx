import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Compass, Play, Trash2, Clock, CheckCircle, XCircle, AlertCircle } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import { FormModalBuilder, FormField } from '@penguintechinc/react-libs/components'
import { getStatusColor } from '@/lib/colorHelpers'
import { confirmDelete } from '@/lib/confirmActions'

// Authenticated integration discovery (requires credentials)
const INTEGRATION_DISCOVERY_TYPES = [
  { value: 'aws', label: 'AWS Discovery' },
  { value: 'gcp', label: 'GCP Discovery' },
  { value: 'azure', label: 'Azure Discovery' },
  { value: 'kubernetes', label: 'Kubernetes Discovery' },
]

// Simple unauthenticated scans
const SCAN_TYPES = [
  { value: 'network', label: 'Network Scan' },
  { value: 'http_screenshot', label: 'HTTP Screenshot' },
  { value: 'banner', label: 'Banner Grab' },
]

// Combined for display purposes
const DISCOVERY_TYPES = [...INTEGRATION_DISCOVERY_TYPES, ...SCAN_TYPES]

export default function Discovery() {
  const [showIntegrationModal, setShowIntegrationModal] = useState(false)
  const [showScanModal, setShowScanModal] = useState(false)
  const [selectedJob, setSelectedJob] = useState<number | null>(null)
  const queryClient = useQueryClient()

  const { data: jobs, isLoading: jobsLoading } = useQuery({
    queryKey: ['discoveryJobs'],
    queryFn: () => api.getDiscoveryJobs(),
  })

  const { data: history } = useQuery({
    queryKey: ['discoveryHistory', selectedJob],
    queryFn: () => api.getDiscoveryJobHistory(selectedJob!),
    enabled: !!selectedJob,
  })

  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations(),
  })

  const runJobMutation = useMutation({
    mutationFn: (id: number) => api.runDiscoveryJob(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['discoveryHistory'],
        refetchType: 'all'
      })
      toast.success('Discovery job started')
    },
  })

  const deleteJobMutation = useMutation({
    mutationFn: (id: number) => api.deleteDiscoveryJob(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['discoveryJobs'],
        refetchType: 'all'
      })
      toast.success('Discovery job deleted')
      if (selectedJob) {
        setSelectedJob(null)
      }
    },
  })

  const createJobMutation = useMutation({
    mutationFn: (data: any) => api.createDiscoveryJob(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['discoveryJobs'],
        refetchType: 'all'
      })
      toast.success('Discovery job created')
      setShowIntegrationModal(false)
      setShowScanModal(false)
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.message || 'Failed to create job')
    },
  })

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="w-5 h-5 text-green-400" />
      case 'failed':
        return <XCircle className="w-5 h-5 text-red-400" />
      case 'running':
        return <Clock className="w-5 h-5 text-yellow-400 animate-pulse" />
      default:
        return <AlertCircle className="w-5 h-5 text-slate-400" />
    }
  }


  // Form fields
  const integrationFields: FormField[] = useMemo(() => [
    {
      name: 'name',
      label: 'Name',
      type: 'text',
      required: true,
      placeholder: 'AWS Production Discovery',
    },
    {
      name: 'provider_type',
      label: 'Integration Type',
      type: 'select',
      required: true,
      options: [
        { value: '', label: 'Select integration type' },
        ...INTEGRATION_DISCOVERY_TYPES,
      ],
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      options: [
        { value: '', label: 'Select organization' },
        ...(orgs?.items || []).map((o: any) => ({ value: o.id, label: o.name })),
      ],
    },
    {
      name: 'schedule',
      label: 'Schedule (Cron, optional)',
      type: 'text',
      placeholder: '0 2 * * *',
    },
    {
      name: 'config',
      label: 'Configuration (JSON)',
      type: 'textarea',
      rows: 8,
      placeholder: '{"region": "us-east-1", "services": ["ec2", "rds", "s3"]}',
      defaultValue: '{}',
    },
  ], [orgs?.items])

  const scanFields: FormField[] = useMemo(() => [
    {
      name: 'name',
      label: 'Name',
      type: 'text',
      required: true,
      placeholder: 'Local Network Scan',
    },
    {
      name: 'provider_type',
      label: 'Scan Type',
      type: 'select',
      required: true,
      options: [
        { value: '', label: 'Select scan type' },
        ...SCAN_TYPES,
      ],
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      options: [
        { value: '', label: 'Select organization' },
        ...(orgs?.items || []).map((o: any) => ({ value: o.id, label: o.name })),
      ],
    },
    {
      name: 'schedule',
      label: 'Schedule (Cron, optional)',
      type: 'text',
      placeholder: '0 2 * * *',
    },
    {
      name: 'config',
      label: 'Configuration (JSON)',
      type: 'textarea',
      rows: 6,
      placeholder: '{"targets": ["192.168.1.0/24"]}',
      defaultValue: '{}',
    },
  ], [orgs?.items])

  const handleIntegrationSubmit = (data: Record<string, any>) => {
    try {
      const configObj = JSON.parse(data.config || '{}')
      const submitData = {
        name: data.name,
        provider_type: data.provider_type,
        organization_id: parseInt(data.organization_id),
        config: configObj,
        schedule: data.schedule || undefined,
        enabled: true,
      }
      createJobMutation.mutate(submitData)
    } catch (err) {
      toast.error('Invalid JSON configuration')
    }
  }

  const handleScanSubmit = (data: Record<string, any>) => {
    try {
      const configObj = JSON.parse(data.config || '{}')
      const submitData = {
        name: data.name,
        provider_type: data.provider_type,
        organization_id: parseInt(data.organization_id),
        config: configObj,
        schedule: data.schedule || '0',
        enabled: true,
      }
      createJobMutation.mutate(submitData)
    } catch (err) {
      toast.error('Invalid JSON configuration')
    }
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Cloud Discovery</h1>
          <p className="mt-2 text-slate-400">Automatically discover and track cloud resources</p>
        </div>
        <div className="flex gap-3">
          <Button variant="secondary" onClick={() => setShowScanModal(true)}>
            <Plus className="w-4 h-4 mr-2" />
            Local Scan
          </Button>
          <Button onClick={() => setShowIntegrationModal(true)}>
            <Plus className="w-4 h-4 mr-2" />
            Integration Scan
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Discovery Jobs */}
        <div>
          <h2 className="text-xl font-semibold text-white mb-4">Discovery Jobs</h2>
          {jobsLoading ? (
            <div className="flex justify-center py-12">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : jobs?.jobs?.length === 0 ? (
            <Card>
              <CardContent className="text-center py-12">
                <Compass className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">No discovery jobs configured</p>
                <div className="flex justify-center gap-3 mt-4">
                  <Button variant="secondary" onClick={() => setShowScanModal(true)}>
                    <Plus className="w-4 h-4 mr-2" />
                    Local Scan
                  </Button>
                  <Button onClick={() => setShowIntegrationModal(true)}>
                    <Plus className="w-4 h-4 mr-2" />
                    Integration Scan
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {jobs?.jobs?.map((job: any) => (
                <Card
                  key={job.id}
                  className={selectedJob === job.id ? 'ring-2 ring-primary-500' : ''}
                >
                  <CardContent>
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex-1 cursor-pointer" onClick={() => setSelectedJob(job.id)}>
                        <h3 className="text-lg font-semibold text-white">{job.name}</h3>
                        <p className="text-sm text-slate-400 mt-1">
                          {DISCOVERY_TYPES.find(t => t.value === job.discovery_type)?.label}
                        </p>
                        {job.schedule && (
                          <p className="text-sm text-slate-400">
                            <Clock className="w-4 h-4 inline mr-1" />
                            {job.schedule}
                          </p>
                        )}
                      </div>
                      <span className={`px-2 py-1 text-xs font-medium rounded ${
                        job.enabled ? 'bg-green-500/20 text-green-400' : 'bg-slate-500/20 text-slate-400'
                      }`}>
                        {job.enabled ? 'Enabled' : 'Disabled'}
                      </span>
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" onClick={() => runJobMutation.mutate(job.id)}>
                        <Play className="w-4 h-4 mr-2" />
                        Run Now
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => confirmDelete(job.name, () => deleteJobMutation.mutate(job.id))}>
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>

        {/* Job History */}
        <div>
          <h2 className="text-xl font-semibold text-white mb-4">Run History</h2>
          {!selectedJob ? (
            <Card>
              <CardContent className="text-center py-12">
                <Clock className="w-12 h-12 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">Select a discovery job to view run history</p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {history?.runs?.length === 0 ? (
                <Card>
                  <CardContent className="text-center py-12">
                    <Clock className="w-8 h-8 text-slate-600 mx-auto mb-3" />
                    <p className="text-slate-400">No runs yet</p>
                  </CardContent>
                </Card>
              ) : (
                history?.runs?.map((run: any) => (
                  <Card key={run.id}>
                    <CardContent>
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex items-center gap-3">
                          {getStatusIcon(run.status)}
                          <div>
                            <p className="text-white font-medium">
                              {new Date(run.started_at).toLocaleString()}
                            </p>
                            {run.completed_at && (
                              <p className="text-sm text-slate-400">
                                Duration: {Math.round((new Date(run.completed_at).getTime() - new Date(run.started_at).getTime()) / 1000)}s
                              </p>
                            )}
                          </div>
                        </div>
                        <span className={`px-2 py-1 text-xs font-medium rounded ${getStatusColor(run.status)}`}>
                          {run.status}
                        </span>
                      </div>
                      {run.entities_discovered > 0 && (
                        <div className="mt-2">
                          <p className="text-sm text-slate-300">
                            Discovered: <span className="font-semibold text-primary-400">{run.entities_discovered}</span> entities
                          </p>
                        </div>
                      )}
                      {run.error_message && (
                        <div className="mt-2 p-2 bg-red-500/10 border border-red-500/20 rounded">
                          <p className="text-sm text-red-400">{run.error_message}</p>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      <FormModalBuilder
        isOpen={showIntegrationModal}
        onClose={() => setShowIntegrationModal(false)}
        title="Create Integration Discovery"
        fields={integrationFields}
        onSubmit={handleIntegrationSubmit}
        submitButtonText="Create"
      />

      <FormModalBuilder
        isOpen={showScanModal}
        onClose={() => setShowScanModal(false)}
        title="Create Scan"
        fields={scanFields}
        onSubmit={handleScanSubmit}
        submitButtonText="Create"
      />
    </div>
  )
}
