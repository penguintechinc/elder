import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import { FormModalBuilder, FormField } from '@penguintechinc/react-libs/components'

type TabType = 'sla' | 'responses' | 'teams' | 'email' | 'forms'

interface SettingsItem {
  id: string
  name?: string
  title?: string
  description?: string
}

interface SettingsSectionProps {
  title: string
  items: SettingsItem[]
  onAdd: () => void
  onDelete: (id: string) => void
  isLoading: boolean
}

const SettingsSection = ({ title, items, onAdd, onDelete, isLoading }: SettingsSectionProps) => (
  <div className="space-y-4">
    <div className="flex items-center justify-between">
      <h3 className="text-lg font-bold text-white">{title}</h3>
      <Button onClick={() => onAdd()} size="sm">
        <Plus className="w-4 h-4 mr-2" />
        Add
      </Button>
    </div>

    {isLoading ? (
      <p className="text-slate-400">Loading...</p>
    ) : items?.length === 0 ? (
      <p className="text-slate-400">No items yet</p>
    ) : (
      <div className="space-y-2">
        {items.map((item) => (
          <Card key={item.id} className="bg-slate-800 border-slate-700">
            <CardContent className="p-4 flex items-center justify-between">
              <div>
                <p className="font-semibold text-white">{String(item.name || item.title)}</p>
                <p className="text-sm text-slate-400">{String(item.description || '')}</p>
              </div>
              <button
                onClick={() => onDelete(item.id)}
                className="text-red-400 hover:text-red-300"
                data-testid={`delete-${item.id}`}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </CardContent>
          </Card>
        ))}
      </div>
    )}
  </div>
)

export default function HelpdeskSettings() {
  const [activeTab, setActiveTab] = useState<TabType>('sla')
  const [showModal, setShowModal] = useState(false)
  const [modalTitle, setModalTitle] = useState('')
  const [modalFields, setModalFields] = useState<FormField[]>([])
  const queryClient = useQueryClient()

  const { data: slaPolicies, isLoading: slaPoliciesLoading } = useQuery({
    queryKey: queryKeys.helpdesk.slaPolicies(),
    queryFn: () => api.getHelpdeskSlaPolicies(),
  })

  const { data: responses, isLoading: responsesLoading } = useQuery({
    queryKey: queryKeys.helpdesk.responses(),
    queryFn: () => api.getHelpdeskCannedResponses(),
  })

  const { data: teams, isLoading: teamsLoading } = useQuery({
    queryKey: queryKeys.helpdesk.teams(),
    queryFn: () => api.getHelpdeskTeams(),
  })

  const { data: emailAccounts, isLoading: emailLoading } = useQuery({
    queryKey: queryKeys.helpdesk.emailAccounts(),
    queryFn: () => api.getHelpdeskEmailAccounts(),
  })

  const { data: forms, isLoading: formsLoading } = useQuery({
    queryKey: queryKeys.helpdesk.ticketForms(),
    queryFn: () => api.getHelpdeskTicketForms(),
  })

  interface SlaPayload {
    name: string
    priority: string
    response_time_hours: number
    resolution_time_hours: number
  }

  const createSlaMutation = useMutation({
    mutationFn: (payload: SlaPayload) => api.createHelpdeskSlaPolicy(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.helpdesk.slaPolicies() })
      setShowModal(false)
      toast.success('SLA Policy created')
    },
    onError: () => toast.error('Failed to create SLA Policy'),
  })

  const deleteSlaMutation = useMutation({
    mutationFn: (id: string) => api.deleteHelpdeskSlaPolicy(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.helpdesk.slaPolicies() })
      toast.success('SLA Policy deleted')
    },
    onError: () => toast.error('Failed to delete SLA Policy'),
  })

  interface TabContentItem {
    title: string
    fields: FormField[]
    items: SettingsItem[]
  }

  const tabContent: Record<TabType, TabContentItem> = {
    sla: {
      title: 'SLA Policies',
      fields: [
        { name: 'name', label: 'Name', type: 'text', required: true },
        { name: 'priority', label: 'Priority', type: 'text', required: true },
        { name: 'response_time_hours', label: 'Response Time (hours)', type: 'number', required: true },
        { name: 'resolution_time_hours', label: 'Resolution Time (hours)', type: 'number', required: true },
      ],
      items: (slaPolicies?.items || []) as SettingsItem[],
    },
    responses: {
      title: 'Canned Responses',
      fields: [
        { name: 'title', label: 'Title', type: 'text', required: true },
        { name: 'body', label: 'Body', type: 'textarea', required: true },
      ],
      items: (responses?.items || []) as SettingsItem[],
    },
    teams: {
      title: 'Teams',
      fields: [
        { name: 'name', label: 'Team Name', type: 'text', required: true },
      ],
      items: (teams?.items || []) as SettingsItem[],
    },
    email: {
      title: 'Email Accounts',
      fields: [
        { name: 'name', label: 'Name', type: 'text', required: true },
        { name: 'email', label: 'Email Address', type: 'email', required: true },
      ],
      items: (emailAccounts?.items || []) as SettingsItem[],
    },
    forms: {
      title: 'Ticket Forms',
      fields: [
        { name: 'name', label: 'Form Name', type: 'text', required: true },
      ],
      items: (forms?.items || []) as SettingsItem[],
    },
  }

  const current = tabContent[activeTab]
  const isLoading = [slaPoliciesLoading, responsesLoading, teamsLoading, emailLoading, formsLoading][
    Object.keys(tabContent).indexOf(activeTab)
  ]

  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold text-white mb-8">Helpdesk Settings</h1>

      <div className="flex gap-4 mb-8 border-b border-slate-700">
        {Object.keys(tabContent).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab as TabType)}
            className={`px-4 py-2 font-semibold transition ${
              activeTab === tab
                ? 'text-amber-400 border-b-2 border-amber-400'
                : 'text-slate-400 hover:text-white'
            }`}
            data-testid={`tab-${tab}`}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      <SettingsSection
        title={current.title}
        items={current.items}
        onAdd={() => {
          setModalTitle(`Add ${current.title.slice(0, -1)}`)
          setModalFields(current.fields)
          setShowModal(true)
        }}
        onDelete={(id: string) => {
          if (activeTab === 'sla') deleteSlaMutation.mutate(id)
        }}
        isLoading={isLoading}
      />

      <FormModalBuilder
        isOpen={showModal}
        title={modalTitle}
        fields={modalFields}
        onSubmit={async (data: Record<string, unknown>) => {
          if (activeTab === 'sla') {
            const slaPayload: SlaPayload = {
              name: String(data.name || ''),
              priority: String(data.priority || ''),
              response_time_hours: Number(data.response_time_hours || 0),
              resolution_time_hours: Number(data.resolution_time_hours || 0),
            }
            await createSlaMutation.mutateAsync(slaPayload)
          }
          setShowModal(false)
        }}
        onClose={() => setShowModal(false)}
      />
    </div>
  )
}
