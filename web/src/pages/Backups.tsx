import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Play, Trash2, RotateCcw, Calendar, Cloud } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'

interface BackupJob {
  id: number
  name: string
  schedule: string
  retention_days: number
  enabled: boolean
}

interface Backup {
  id: number
  status: string
  created_at: string
  size_bytes: number
}

interface Organization {
  id: number
  name: string
}

interface CreateJobModalProps {
  onClose: () => void
  onSuccess: () => Promise<void>
}

export default function Backups() {
  const [showCreateJobModal, setShowCreateJobModal] = useState(false)
  const queryClient = useQueryClient()

  const { data: jobs, isLoading: jobsLoading } = useQuery({
    queryKey: ['backupJobs'],
    queryFn: () => api.getBackupJobs(),
  })

  const { data: backups } = useQuery({
    queryKey: ['backups'],
    queryFn: () => api.getBackups(),
  })

  const runJobMutation = useMutation({
    mutationFn: (id: number) => api.runBackupJob(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['backups'],
        refetchType: 'all'
      })
      toast.success('Backup job started')
    },
  })

  const deleteJobMutation = useMutation({
    mutationFn: (id: number) => api.deleteBackupJob(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['backupJobs'],
        refetchType: 'all'
      })
      toast.success('Backup job deleted')
    },
  })

  const restoreMutation = useMutation({
    mutationFn: ({ id, dryRun }: { id: number; dryRun?: boolean }) =>
      api.restoreBackup(id, { dry_run: dryRun }),
    onSuccess: (data) => {
      if (data.dry_run) {
        toast.success('Dry run completed successfully')
      } else {
        toast.success('Restore completed successfully')
      }
    },
  })

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Backup Management</h1>
          <p className="mt-2 text-slate-400">Configure backup jobs and restore data</p>
        </div>
        <Button onClick={() => setShowCreateJobModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Backup Job
        </Button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Backup Jobs */}
        <div>
          <h2 className="text-xl font-semibold text-white mb-4">Backup Jobs</h2>
          {jobsLoading ? (
            <div className="flex justify-center py-12">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : (
            <div className="space-y-4">
              {jobs?.jobs?.map((job: BackupJob) => (
                <Card key={job.id}>
                  <CardContent>
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <h3 className="text-lg font-semibold text-white">{job.name}</h3>
                        <p className="text-sm text-slate-400 mt-1">
                          <Calendar className="w-4 h-4 inline mr-1" />
                          {job.schedule}
                        </p>
                        <p className="text-sm text-slate-400">
                          Retention: {job.retention_days} days
                        </p>
                      </div>
                      <span className={`px-2 py-1 text-xs font-medium rounded ${
                        job.enabled ? 'bg-green-500/20 text-green-400' : 'bg-slate-500/20 text-slate-400'
                      }`}>
                        {job.enabled ? 'Enabled' : 'Disabled'}
                      </span>
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" variant="ghost" onClick={() => runJobMutation.mutate(job.id)}>
                        <Play className="w-4 h-4 mr-2" />
                        Run Now
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => deleteJobMutation.mutate(job.id)}>
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>

        {/* Backups */}
        <div>
          <h2 className="text-xl font-semibold text-white mb-4">Available Backups</h2>
          <div className="space-y-4">
            {backups?.backups?.map((backup: Backup) => (
              <Card key={backup.id}>
                <CardContent>
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <h3 className="text-white font-medium">
                        {new Date(backup.created_at).toLocaleString()}
                      </h3>
                      <p className="text-sm text-slate-400">
                        Size: {(backup.size_bytes / 1024 / 1024).toFixed(2)} MB
                      </p>
                      <span className={`inline-block mt-2 px-2 py-1 text-xs font-medium rounded ${
                        backup.status === 'completed' ? 'bg-green-500/20 text-green-400' :
                        backup.status === 'failed' ? 'bg-red-500/20 text-red-400' :
                        'bg-yellow-500/20 text-yellow-400'
                      }`}>
                        {backup.status}
                      </span>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => restoreMutation.mutate({ id: backup.id, dryRun: true })}
                    >
                      <RotateCcw className="w-4 h-4 mr-2" />
                      Dry Run
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => {
                        if (confirm('Are you sure you want to restore this backup?')) {
                          restoreMutation.mutate({ id: backup.id })
                        }
                      }}
                    >
                      <RotateCcw className="w-4 h-4 mr-2" />
                      Restore
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </div>

      {showCreateJobModal && (
        <CreateJobModal
          onClose={() => setShowCreateJobModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ['backupJobs'],
              refetchType: 'all'
            })
            setShowCreateJobModal(false)
          }}
        />
      )}
    </div>
  )
}

function CreateJobModal({ onClose, onSuccess }: CreateJobModalProps) {
  const [name, setName] = useState('')
  const [schedule, setSchedule] = useState('0 2 * * *')
  const [retentionDays, setRetentionDays] = useState('30')
  const [orgId, setOrgId] = useState('')
  const [s3Enabled, setS3Enabled] = useState(false)
  const [s3Endpoint, setS3Endpoint] = useState('')
  const [s3Bucket, setS3Bucket] = useState('')
  const [s3Region, setS3Region] = useState('us-east-1')
  const [s3AccessKey, setS3AccessKey] = useState('')
  const [s3SecretKey, setS3SecretKey] = useState('')
  const [s3Prefix, setS3Prefix] = useState('elder/backups/')

  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations(),
  })

  const createMutation = useMutation({
    mutationFn: (data: unknown) => api.createBackupJob(data as Parameters<typeof api.createBackupJob>[0]),
    onSuccess: () => {
      toast.success('Backup job created')
      onSuccess()
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const backupData: Parameters<typeof api.createBackupJob>[0] = {
      name,
      schedule,
      retention_days: parseInt(retentionDays),
      organization_id: orgId ? parseInt(orgId) : undefined,
      enabled: true,
    }
    createMutation.mutate(backupData)
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Create Backup Job</h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Daily Backup"
            />
            <Input
              label="Schedule (Cron)"
              required
              value={schedule}
              onChange={(e) => setSchedule(e.target.value)}
              placeholder="0 2 * * *"
            />
            <Input
              label="Retention Days"
              type="number"
              required
              value={retentionDays}
              onChange={(e) => setRetentionDays(e.target.value)}
            />
            <Select
              label="Organization (optional)"
              value={orgId}
              onChange={(e) => setOrgId(e.target.value)}
              options={[
                { value: '', label: 'All organizations' },
                ...(orgs?.items || []).map((o: Organization) => ({ value: o.id, label: o.name })),
              ]}
            />

            {/* S3 Configuration Section */}
            <div className="border-t border-slate-700 pt-4 mt-4">
              <div className="flex items-center gap-2 mb-4">
                <Cloud className="w-5 h-5 text-primary-400" />
                <h3 className="text-lg font-medium text-white">S3 Storage (Optional)</h3>
              </div>

              <div className="flex items-center gap-2 mb-4">
                <input
                  type="checkbox"
                  id="s3-enabled"
                  checked={s3Enabled}
                  onChange={(e) => setS3Enabled(e.target.checked)}
                  className="w-4 h-4 rounded border-slate-600 bg-slate-700 text-primary-500 focus:ring-primary-500"
                />
                <label htmlFor="s3-enabled" className="text-sm text-slate-300">
                  Enable S3-compatible storage for this backup job
                </label>
              </div>

              {s3Enabled && (
                <div className="space-y-4 pl-6 border-l-2 border-primary-500/30">
                  <Input
                    label="S3 Endpoint"
                    value={s3Endpoint}
                    onChange={(e) => setS3Endpoint(e.target.value)}
                    placeholder="s3.amazonaws.com (or minio.example.com)"
                    helperText="Leave empty for AWS S3, or specify custom endpoint for MinIO, Wasabi, etc."
                  />
                  <Input
                    label="Bucket Name"
                    required={s3Enabled}
                    value={s3Bucket}
                    onChange={(e) => setS3Bucket(e.target.value)}
                    placeholder="my-backup-bucket"
                  />
                  <Input
                    label="Region"
                    value={s3Region}
                    onChange={(e) => setS3Region(e.target.value)}
                    placeholder="us-east-1"
                  />
                  <Input
                    label="Access Key ID"
                    required={s3Enabled}
                    value={s3AccessKey}
                    onChange={(e) => setS3AccessKey(e.target.value)}
                    placeholder="AKIAIOSFODNN7EXAMPLE"
                  />
                  <Input
                    label="Secret Access Key"
                    type="password"
                    required={s3Enabled}
                    value={s3SecretKey}
                    onChange={(e) => setS3SecretKey(e.target.value)}
                    placeholder="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
                  />
                  <Input
                    label="Key Prefix"
                    value={s3Prefix}
                    onChange={(e) => setS3Prefix(e.target.value)}
                    placeholder="elder/backups/"
                    helperText="Prefix for backup files in the bucket"
                  />
                </div>
              )}
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" isLoading={createMutation.isPending}>Create</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
