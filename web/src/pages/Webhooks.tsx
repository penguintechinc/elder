import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Webhook, TestTube, Trash2, CheckCircle, XCircle, Edit } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import AssigneePicker, { AssigneeValue } from '@/components/AssigneePicker'
import { ISSUE_TYPES, issueTypeLabel } from '@/lib/constants/issueTypes'
import { Organization } from '@/types'

interface WebhookConfig {
  id: number
  name: string
  url: string
  organization_id: number
  events: string[]
  is_active: boolean
  filter_issue_type?: string
  filter_assignee_type?: 'identity' | 'org_unit'
  filter_assignee_id?: number
}

interface WebhooksResponse {
  webhooks: WebhookConfig[]
}

interface OrganizationResponse {
  items: Organization[]
}

const EVENT_TYPES = [
  { value: 'entity.created', label: 'Entity Created' },
  { value: 'entity.updated', label: 'Entity Updated' },
  { value: 'entity.deleted', label: 'Entity Deleted' },
  { value: 'organization.created', label: 'Organization Created' },
  { value: 'organization.updated', label: 'Organization Updated' },
  { value: 'issue.created', label: 'Issue Created' },
  { value: 'issue.updated', label: 'Issue Updated' },
  { value: 'issue.closed', label: 'Issue Closed' },
  { value: 'issue.assigned', label: 'Issue Assigned' },
]

export default function Webhooks() {
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingWebhook, setEditingWebhook] = useState<WebhookConfig | null>(null)
  const queryClient = useQueryClient()

  const { data: profile } = useQuery({
    queryKey: ['portal-profile'],
    queryFn: () => api.getPortalProfile(),
    staleTime: 60000,
  })
  const isAdmin = profile?.global_role === 'admin' || profile?.tenant_role === 'admin'

  const { data, isLoading } = useQuery({
    queryKey: ['webhooks'],
    queryFn: () => api.getWebhooks() as Promise<WebhooksResponse>,
    enabled: isAdmin,
  })

  const testMutation = useMutation({
    mutationFn: (id: number) => api.testWebhook(id),
    onSuccess: () => toast.success('Test webhook sent'),
    onError: () => toast.error('Failed to send test webhook'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteWebhook(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['webhooks'], refetchType: 'all' })
      toast.success('Webhook deleted')
    },
  })

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">Admin access required</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Webhooks</h1>
          <p className="mt-2 text-slate-400">Configure event webhooks and notifications</p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Webhook
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.webhooks?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <Webhook className="w-12 h-12 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No webhooks configured</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first webhook
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {data?.webhooks?.map((webhook: WebhookConfig) => (
            <Card key={webhook.id}>
              <CardContent>
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-3 mb-2">
                      {webhook.is_active ? (
                        <CheckCircle className="w-5 h-5 text-green-400" />
                      ) : (
                        <XCircle className="w-5 h-5 text-slate-500" />
                      )}
                      <h3 className="text-lg font-semibold text-white">{webhook.name}</h3>
                    </div>
                    <p className="text-sm text-slate-400 mb-3 font-mono">{webhook.url}</p>
                    <div className="flex flex-wrap gap-2">
                      {webhook.events?.map((event: string) => (
                        <span key={event} className="px-2 py-1 text-xs bg-primary-500/20 text-primary-400 rounded">
                          {event}
                        </span>
                      ))}
                      {webhook.filter_issue_type && (
                        <span className="px-2 py-1 text-xs bg-yellow-500/20 text-yellow-400 rounded">
                          type={issueTypeLabel(webhook.filter_issue_type)}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" variant="ghost" title="Edit webhook" onClick={() => setEditingWebhook(webhook)}>
                      <Edit className="w-4 h-4" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => testMutation.mutate(webhook.id)}>
                      <TestTube className="w-4 h-4" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => deleteMutation.mutate(webhook.id)}>
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {showCreateModal && (
        <CreateWebhookModal
          onClose={() => setShowCreateModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({ queryKey: ['webhooks'], refetchType: 'all' })
            setShowCreateModal(false)
          }}
        />
      )}

      {editingWebhook && (
        <EditWebhookModal
          webhook={editingWebhook}
          onClose={() => setEditingWebhook(null)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({ queryKey: ['webhooks'], refetchType: 'all' })
            setEditingWebhook(null)
          }}
        />
      )}
    </div>
  )
}

interface CreateWebhookModalProps {
  onClose: () => void
  onSuccess: () => Promise<void>
}

function AssignmentFilters({
  filterIssueType, onFilterIssueTypeChange, filterAssignee, onFilterAssigneeChange,
}: {
  filterIssueType: string
  onFilterIssueTypeChange: (v: string) => void
  filterAssignee: AssigneeValue | null
  onFilterAssigneeChange: (v: AssigneeValue | null) => void
}) {
  return (
    <div className="pt-4 border-t border-slate-700">
      <p className="text-sm font-medium text-slate-300 mb-3">
        Assignment Filters (issue.assigned events only)
      </p>
      <div className="space-y-3">
        <Select label="Issue Type" value={filterIssueType} onChange={(e) => onFilterIssueTypeChange(e.target.value)}>
          <option value="">Any issue type</option>
          {ISSUE_TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </Select>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1.5">Assignee</label>
          <AssigneePicker value={filterAssignee} onChange={onFilterAssigneeChange} />
        </div>
      </div>
    </div>
  )
}

function CreateWebhookModal({ onClose, onSuccess }: CreateWebhookModalProps) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [orgId, setOrgId] = useState('')
  const [selectedEvents, setSelectedEvents] = useState<string[]>([])
  const [secret, setSecret] = useState('')
  const [filterIssueType, setFilterIssueType] = useState('')
  const [filterAssignee, setFilterAssignee] = useState<AssigneeValue | null>(null)

  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations() as Promise<OrganizationResponse>,
  })

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => api.createWebhook(data as Parameters<typeof api.createWebhook>[0]),
    onSuccess: () => {
      console.log('[Webhooks] Create { name: "...", events: [...], filter_issue_type: "...", filter_assignee_type: "..." }')
      toast.success('Webhook created')
      onSuccess()
    },
    onError: () => toast.error('Failed to create webhook'),
  })

  const toggleEvent = (event: string) => {
    setSelectedEvents((prev) => (prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const webhookData: Parameters<typeof api.createWebhook>[0] = {
      name,
      url,
      organization_id: orgId ? parseInt(orgId) : undefined,
      events: selectedEvents,
      secret: secret || undefined,
      is_active: true,
      filter_issue_type: filterIssueType || undefined,
      filter_assignee_type: filterAssignee?.assignee_type,
      filter_assignee_id: filterAssignee?.assignee_id,
    }
    createMutation.mutate(webhookData)
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Create Webhook</h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input label="Name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Production Webhook" />
            <Input label="URL" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/webhook" />
            <Select
              label="Organization"
              value={orgId}
              onChange={(e) => setOrgId(e.target.value)}
            >
              <option value="">Select organization</option>
              {(orgs?.items || []).map((o: Organization) => (
                <option key={o.id} value={String(o.id)}>{o.name}</option>
              ))}
            </Select>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Events</label>
              <div className="space-y-2">
                {EVENT_TYPES.map((event) => (
                  <label key={event.value} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedEvents.includes(event.value)}
                      onChange={() => toggleEvent(event.value)}
                      className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                      data-testid={`event-checkbox-${event.value}`}
                    />
                    <span className="text-sm text-slate-300">{event.label}</span>
                  </label>
                ))}
              </div>
            </div>
            <Input label="Secret (optional)" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Webhook signing secret" />
            <AssignmentFilters
              filterIssueType={filterIssueType}
              onFilterIssueTypeChange={setFilterIssueType}
              filterAssignee={filterAssignee}
              onFilterAssigneeChange={setFilterAssignee}
            />
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

interface EditWebhookModalProps {
  webhook: WebhookConfig
  onClose: () => void
  onSuccess: () => Promise<void>
}

function EditWebhookModal({ webhook, onClose, onSuccess }: EditWebhookModalProps) {
  const [name, setName] = useState(webhook.name)
  const [url, setUrl] = useState(webhook.url)
  const [selectedEvents, setSelectedEvents] = useState<string[]>(webhook.events || [])
  const [isActive, setIsActive] = useState(webhook.is_active)
  const [filterIssueType, setFilterIssueType] = useState(webhook.filter_issue_type || '')
  const [filterAssignee, setFilterAssignee] = useState<AssigneeValue | null>(
    webhook.filter_assignee_id && webhook.filter_assignee_type
      ? { assignee_type: webhook.filter_assignee_type, assignee_id: webhook.filter_assignee_id }
      : null
  )

  const updateMutation = useMutation({
    mutationFn: (data: Parameters<typeof api.updateWebhook>[1]) => api.updateWebhook(webhook.id, data),
    onSuccess: async () => {
      console.log('[Webhooks] Update { name: "...", is_active: true, filter_issue_type: "..." }')
      toast.success('Webhook updated')
      await onSuccess()
    },
    onError: () => toast.error('Failed to update webhook'),
  })

  const toggleEvent = (event: string) => {
    setSelectedEvents((prev) => (prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    updateMutation.mutate({
      name,
      url,
      events: selectedEvents,
      is_active: isActive,
      filter_issue_type: filterIssueType || undefined,
      filter_assignee_type: filterAssignee?.assignee_type,
      filter_assignee_id: filterAssignee?.assignee_id,
    })
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Edit Webhook</h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4" data-testid="edit-webhook-form">
            <Input label="Name" required value={name} onChange={(e) => setName(e.target.value)} />
            <Input label="URL" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} />
            <div>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                  className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                  data-testid="is-active-toggle"
                />
                <span className="text-sm font-medium text-slate-300">Active</span>
              </label>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Events</label>
              <div className="space-y-2">
                {EVENT_TYPES.map((event) => (
                  <label key={event.value} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedEvents.includes(event.value)}
                      onChange={() => toggleEvent(event.value)}
                      className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                      data-testid={`event-checkbox-${event.value}`}
                    />
                    <span className="text-sm text-slate-300">{event.label}</span>
                  </label>
                ))}
              </div>
            </div>
            <AssignmentFilters
              filterIssueType={filterIssueType}
              onFilterIssueTypeChange={setFilterIssueType}
              filterAssignee={filterAssignee}
              onFilterAssigneeChange={setFilterAssignee}
            />
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" isLoading={updateMutation.isPending} data-testid="save-webhook-button">
                Save Changes
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
