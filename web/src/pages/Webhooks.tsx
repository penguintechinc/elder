import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Webhook, TestTube, Trash2, CheckCircle, XCircle } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import { Organization } from '@/types'

interface WebhookConfig {
  id: number
  name: string
  url: string
  organization_id: number
  events: string[]
  enabled: boolean
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
]

export default function Webhooks() {
  const [showCreateModal, setShowCreateModal] = useState(false)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['webhooks'],
    queryFn: () => api.getWebhooks() as Promise<WebhooksResponse>,
  })

  const testMutation = useMutation({
    mutationFn: (id: number) => api.testWebhook(id),
    onSuccess: () => toast.success('Test webhook sent'),
    onError: () => toast.error('Failed to send test webhook'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteWebhook(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['webhooks'],
        refetchType: 'all'
      })
      toast.success('Webhook deleted')
    },
  })

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
                      {webhook.enabled ? (
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
                    </div>
                  </div>
                  <div className="flex gap-2">
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
            await queryClient.invalidateQueries({
              queryKey: ['webhooks'],
              refetchType: 'all'
            })
            setShowCreateModal(false)
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

function CreateWebhookModal({ onClose, onSuccess }: CreateWebhookModalProps) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [orgId, setOrgId] = useState('')
  const [selectedEvents, setSelectedEvents] = useState<string[]>([])
  const [secret, setSecret] = useState('')

  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations() as Promise<OrganizationResponse>,
  })

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => api.createWebhook(data as Parameters<typeof api.createWebhook>[0]),
    onSuccess: () => {
      toast.success('Webhook created')
      onSuccess()
    },
  })

  const toggleEvent = (event: string) => {
    setSelectedEvents(prev =>
      prev.includes(event) ? prev.filter(e => e !== event) : [...prev, event]
    )
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const webhookData: Parameters<typeof api.createWebhook>[0] = {
      name,
      url,
      organization_id: parseInt(orgId),
      events: selectedEvents,
      secret: secret || undefined,
      enabled: true,
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
            <Input
              label="Name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Production Webhook"
            />
            <Input
              label="URL"
              type="url"
              required
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/webhook"
            />
            <Select
              label="Organization"
              required
              value={orgId}
              onChange={(e) => setOrgId(e.target.value)}
              options={[
                { value: '', label: 'Select organization' },
                ...(orgs?.items || []).map((o: Organization) => ({ value: String(o.id), label: o.name })),
              ]}
            />
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
                    />
                    <span className="text-sm text-slate-300">{event.label}</span>
                  </label>
                ))}
              </div>
            </div>
            <Input
              label="Secret (optional)"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="Webhook signing secret"
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
