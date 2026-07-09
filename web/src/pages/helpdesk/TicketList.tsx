import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Plus, Search } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import Button from '@/components/Button'
import Input from '@/components/Input'
import Select from '@/components/Select'
import Card, { CardContent } from '@/components/Card'
import { FormModalBuilder, FormField } from '@penguintechinc/react-libs/components'

interface Ticket {
  id: string
  subject: string
  status: string
  priority: string
}

const StatusBadge = ({ status }: { status: string }) => {
  const colors: Record<string, string> = {
    open: 'bg-blue-900 text-blue-200',
    in_progress: 'bg-amber-900 text-amber-200',
    closed: 'bg-green-900 text-green-200',
    on_hold: 'bg-slate-900 text-slate-200',
  }
  return (
    <span className={`px-2 py-1 rounded text-xs font-medium ${colors[status] || colors.open}`}>
      {status.replace('_', ' ')}
    </span>
  )
}

const PriorityBadge = ({ priority }: { priority: string }) => {
  const colors: Record<string, string> = {
    low: 'text-blue-400',
    medium: 'text-amber-400',
    high: 'text-orange-400',
    critical: 'text-red-400',
  }
  return <span className={colors[priority] || colors.medium}>{priority}</span>
}

export default function TicketList() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [priorityFilter, setPriorityFilter] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.helpdesk.tickets({ search, status: statusFilter, priority: priorityFilter }),
    queryFn: () => api.getHelpdeskTickets({
      search: search || undefined,
      status: statusFilter || undefined,
      priority: priorityFilter || undefined,
    }),
  })

  interface CreateTicketPayload {
    subject: string
    description?: string
    priority?: string
  }

  const createMutation = useMutation({
    mutationFn: (payload: CreateTicketPayload) => api.createHelpdeskTicket(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.helpdesk.tickets() })
      toast.success('Ticket created')
      setShowCreateModal(false)
    },
    onError: () => {
      toast.error('Failed to create ticket')
    },
  })

  const tickets = (data?.data || []) as Ticket[]

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Tickets</h1>
          <p className="mt-2 text-slate-400">Manage support tickets</p>
        </div>
        <Button onClick={() => setShowCreateModal(true)} data-testid="create-ticket-btn">
          <Plus className="w-4 h-4 mr-2" />
          Create Ticket
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search tickets..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          data-testid="status-filter"
        >
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="on_hold">On Hold</option>
          <option value="closed">Closed</option>
        </Select>
        <Select
          value={priorityFilter}
          onChange={(e) => setPriorityFilter(e.target.value)}
          data-testid="priority-filter"
        >
          <option value="">All Priorities</option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="critical">Critical</option>
        </Select>
      </div>

      {isLoading ? (
        <p className="text-slate-400">Loading tickets...</p>
      ) : tickets.length === 0 ? (
        <Card className="bg-slate-800 border-slate-700">
          <CardContent className="p-8 text-center">
            <p className="text-slate-400">No tickets found</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {tickets.map((ticket) => (
            <Card
              key={ticket.id}
              className="bg-slate-800 border-slate-700 cursor-pointer hover:border-amber-400 transition"
              onClick={() => navigate(`/helpdesk/tickets/${ticket.id}`)}
              data-testid={`ticket-row-${ticket.id}`}
            >
              <CardContent className="p-4">
                <div className="flex items-center justify-between">
                  <div className="flex-1">
                    <h3 className="font-semibold text-white">{ticket.subject}</h3>
                    <p className="text-sm text-slate-400 mt-1">ID: {ticket.id}</p>
                  </div>
                  <div className="flex items-center gap-4 ml-4">
                    <StatusBadge status={ticket.status} />
                    <PriorityBadge priority={ticket.priority} />
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <FormModalBuilder
        isOpen={showCreateModal}
        title="Create Ticket"
        fields={[
          { name: 'subject', label: 'Subject', type: 'text', required: true } as FormField,
          { name: 'description', label: 'Description', type: 'textarea', required: false } as FormField,
          {
            name: 'priority',
            label: 'Priority',
            type: 'select',
            options: [
              { value: 'low', label: 'Low' },
              { value: 'medium', label: 'Medium' },
              { value: 'high', label: 'High' },
              { value: 'critical', label: 'Critical' },
            ],
          } as FormField,
        ]}
        onSubmit={async (data: Record<string, unknown>) => {
          const payload: CreateTicketPayload = {
            subject: String(data.subject || ''),
            description: data.description ? String(data.description) : undefined,
            priority: data.priority ? String(data.priority) : undefined,
          }
          await createMutation.mutateAsync(payload)
        }}
        onClose={() => setShowCreateModal(false)}
      />
    </div>
  )
}
