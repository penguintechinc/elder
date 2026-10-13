import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Edit, Trash2, X } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'

interface OnCallRotation {
  id: number
  name: string
  description?: string
  schedule_type: string
  scope_type: string
  is_active: boolean
  organization_id?: number
  service_id?: number
  rotation_length_days?: number
  rotation_start_date?: string
  schedule_cron?: string
  handoff_timezone?: string
  shift_split?: boolean
}

interface CurrentOnCall {
  identity_name?: string
  identity_email?: string
  shift_end: string
  is_override?: boolean
}

interface Participant {
  id: number
  identity_name: string
  order_index: number
  is_active: boolean
}

interface ParticipantsResponse {
  items: Participant[]
}

interface Shift {
  id: number
  identity_name: string
  shift_start: string
  shift_end: string
  is_override?: boolean
  alerts_received: number
  incidents_created: number
}

interface HistoryResponse {
  items: Shift[]
}

interface EscalationPolicy {
  id: number
  level: number
  escalation_type: string
  identity_name?: string
  group_id?: number
  escalation_delay_minutes: number
  notification_channels?: string[]
}

interface EscalationResponse {
  items: EscalationPolicy[]
}

interface OnCallRotationDetailModalProps {
  rotation: OnCallRotation
  onClose: () => void
  onEdit: () => void
}

export default function OnCallRotationDetailModal({
  rotation,
  onClose,
  onEdit,
}: OnCallRotationDetailModalProps) {
  const [activeTab, setActiveTab] = useState<'details' | 'schedule' | 'history' | 'escalations'>('details')
  const queryClient = useQueryClient()

  // Fetch rotation details, participants, history, escalations
  const { data: participants } = useQuery({
    queryKey: queryKeys.onCall.participants(rotation.id),
    queryFn: () => api.getOnCallParticipants(rotation.id) as Promise<ParticipantsResponse>,
  })

  const { data: history } = useQuery({
    queryKey: queryKeys.onCall.history(rotation.id),
    queryFn: () => api.getOnCallHistory(rotation.id) as Promise<HistoryResponse>,
  })

  const { data: escalationPolicies } = useQuery({
    queryKey: queryKeys.onCall.escalations(rotation.id),
    queryFn: () => api.getEscalationPolicies({ rotation_id: rotation.id }) as Promise<EscalationResponse>,
  })

  const { data: currentOnCall } = useQuery({
    queryKey: queryKeys.onCall.current(rotation.scope_type, rotation.scope_type === 'organization' ? rotation.organization_id : rotation.service_id),
    queryFn: () => api.getCurrentOnCall(rotation.scope_type, rotation.scope_type === 'organization' ? rotation.organization_id ?? 0 : rotation.service_id ?? 0) as Promise<CurrentOnCall>,
    enabled: !!rotation.organization_id || !!rotation.service_id,
  })

  const deleteEscalationMutation = useMutation({
    mutationFn: (id: number) => api.deleteEscalationPolicy(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.onCall.escalations(rotation.id) })
      toast.success('Escalation policy deleted')
    },
    onError: () => toast.error('Failed to delete escalation policy'),
  })

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        <CardHeader>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-2xl font-semibold text-white">{rotation.name}</h2>
              {rotation.description && (
                <p className="text-sm text-slate-400 mt-1">{rotation.description}</p>
              )}
            </div>
            <button
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Tabs */}
          <div className="flex gap-4 border-b border-slate-700">
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
              onClick={() => setActiveTab('schedule')}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'schedule'
                  ? 'border-primary-500 text-primary-400'
                  : 'border-transparent text-slate-400 hover:text-white'
              }`}
            >
              Schedule
            </button>
            <button
              onClick={() => setActiveTab('history')}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'history'
                  ? 'border-primary-500 text-primary-400'
                  : 'border-transparent text-slate-400 hover:text-white'
              }`}
            >
              History
            </button>
            <button
              onClick={() => setActiveTab('escalations')}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'escalations'
                  ? 'border-primary-500 text-primary-400'
                  : 'border-transparent text-slate-400 hover:text-white'
              }`}
            >
              Escalations
            </button>
          </div>
        </CardHeader>

        <CardContent className="overflow-y-auto flex-1 space-y-4">
          {activeTab === 'details' && (
            <DetailsTab rotation={rotation} currentOnCall={currentOnCall} participants={participants} />
          )}
          {activeTab === 'schedule' && <ScheduleTab rotation={rotation} />}
          {activeTab === 'history' && <HistoryTab history={history} />}
          {activeTab === 'escalations' && (
            <EscalationsTab
              escalationPolicies={escalationPolicies}
              onDelete={(id) => deleteEscalationMutation.mutate(id)}
              isDeleting={deleteEscalationMutation.isPending}
            />
          )}
        </CardContent>

        <div className="border-t border-slate-700 p-4 flex justify-between">
          <Button variant="ghost" onClick={onEdit}>
            <Edit className="w-4 h-4 mr-2" />
            Edit
          </Button>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </Card>
    </div>
  )
}

interface DetailsTabProps {
  rotation: OnCallRotation
  currentOnCall?: CurrentOnCall
  participants?: ParticipantsResponse
}

function DetailsTab({ rotation, currentOnCall, participants }: DetailsTabProps) {
  return (
    <div className="space-y-6">
      {/* Current On-Call Card */}
      {currentOnCall && (
        <div className="p-4 bg-primary-500/10 border border-primary-500/30 rounded-lg">
          <h3 className="text-sm font-medium text-primary-400 mb-3">Currently On-Call</h3>
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-gradient-to-br from-primary-400 to-primary-600 flex items-center justify-center text-white font-bold text-xl">
              {currentOnCall.identity_name?.charAt(0) || 'U'}
            </div>
            <div>
              <p className="text-lg font-semibold text-white">{currentOnCall.identity_name}</p>
              <p className="text-sm text-slate-400">{currentOnCall.identity_email}</p>
              <p className="text-xs text-slate-500 mt-1">
                Until {new Date(currentOnCall.shift_end).toLocaleDateString()}
              </p>
              {currentOnCall.is_override && (
                <span className="inline-block mt-2 px-2 py-1 text-xs rounded bg-yellow-500/20 text-yellow-400">
                  Override
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Rotation Info */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="text-xs text-slate-500 uppercase">Schedule Type</label>
          <p className="text-sm text-white mt-1 capitalize">{rotation.schedule_type}</p>
        </div>
        <div>
          <label className="text-xs text-slate-500 uppercase">Scope</label>
          <p className="text-sm text-white mt-1 capitalize">{rotation.scope_type}</p>
        </div>
        <div>
          <label className="text-xs text-slate-500 uppercase">Status</label>
          <p className="text-sm mt-1">
            {rotation.is_active ? (
              <span className="px-2 py-1 rounded bg-green-500/20 text-green-400 text-xs">Active</span>
            ) : (
              <span className="px-2 py-1 rounded bg-slate-500/20 text-slate-400 text-xs">Inactive</span>
            )}
          </p>
        </div>
        {rotation.rotation_length_days && (
          <div>
            <label className="text-xs text-slate-500 uppercase">Rotation Length</label>
            <p className="text-sm text-white mt-1">{rotation.rotation_length_days} days</p>
          </div>
        )}
      </div>

      {/* Participants */}
      <div className="border-t border-slate-700 pt-4">
        <h3 className="font-medium text-white mb-3">Participants</h3>
        {participants?.items && participants.items.length > 0 ? (
          <div className="space-y-2">
            {participants.items.map((participant: Participant) => (
              <div key={participant.id} className="p-3 bg-slate-800/50 rounded border border-slate-700 flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-white">{participant.identity_name}</p>
                  <p className="text-xs text-slate-400">Position: {participant.order_index + 1}</p>
                </div>
                <span className={`text-xs px-2 py-1 rounded ${
                  participant.is_active ? 'bg-green-500/20 text-green-400' : 'bg-slate-500/20 text-slate-400'
                }`}>
                  {participant.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-400">No participants added yet</p>
        )}
      </div>
    </div>
  )
}

interface ScheduleTabProps {
  rotation: OnCallRotation
}

function ScheduleTab({ rotation }: ScheduleTabProps) {
  return (
    <div className="space-y-4">
      <div className="p-4 bg-slate-800/50 rounded border border-slate-700">
        <h3 className="text-sm font-medium text-white mb-3">Schedule Configuration</h3>

        {rotation.schedule_type === 'weekly' && (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-400 uppercase">Rotation Length</label>
              <p className="text-sm text-white mt-1">{rotation.rotation_length_days} days</p>
            </div>
            <div>
              <label className="text-xs text-slate-400 uppercase">Start Date</label>
              <p className="text-sm text-white mt-1">{new Date(rotation.rotation_start_date).toLocaleDateString()}</p>
            </div>
          </div>
        )}

        {rotation.schedule_type === 'cron' && (
          <div>
            <label className="text-xs text-slate-400 uppercase">Cron Expression</label>
            <code className="block text-sm text-primary-400 mt-1 p-2 bg-slate-700/50 rounded font-mono">
              {rotation.schedule_cron}
            </code>
          </div>
        )}

        {rotation.schedule_type === 'follow_the_sun' && (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-400 uppercase">Timezone</label>
              <p className="text-sm text-white mt-1">{rotation.handoff_timezone}</p>
            </div>
            <div>
              <label className="text-xs text-slate-400 uppercase">Shift Configuration</label>
              <p className="text-sm text-white mt-1">
                {rotation.shift_split ? 'Multiple shifts per day' : 'Single shift per day'}
              </p>
            </div>
          </div>
        )}

        {rotation.schedule_type === 'manual' && (
          <div>
            <p className="text-sm text-slate-400">Manually set who is on-call</p>
          </div>
        )}
      </div>
    </div>
  )
}

interface HistoryTabProps {
  history?: HistoryResponse
}

function HistoryTab({ history }: HistoryTabProps) {
  if (!history?.items || history.items.length === 0) {
    return <p className="text-sm text-slate-400">No shift history yet</p>
  }

  return (
    <div className="space-y-2">
      {history.items.map((shift: Shift) => (
        <div key={shift.id} className="p-3 bg-slate-800/50 rounded border border-slate-700">
          <div className="flex items-center justify-between mb-1">
            <p className="text-sm font-medium text-white">{shift.identity_name}</p>
            {shift.is_override && (
              <span className="text-xs px-2 py-1 rounded bg-yellow-500/20 text-yellow-400">Override</span>
            )}
          </div>
          <p className="text-xs text-slate-400">
            {new Date(shift.shift_start).toLocaleDateString()} to {new Date(shift.shift_end).toLocaleDateString()}
          </p>
          {shift.alerts_received > 0 || shift.incidents_created > 0 ? (
            <p className="text-xs text-slate-500 mt-1">
              Alerts: {shift.alerts_received} | Incidents: {shift.incidents_created}
            </p>
          ) : null}
        </div>
      ))}
    </div>
  )
}

interface EscalationsTabProps {
  escalationPolicies?: EscalationResponse
  onDelete: (id: number) => void
  isDeleting: boolean
}

function EscalationsTab({ escalationPolicies, onDelete, isDeleting }: EscalationsTabProps) {
  if (!escalationPolicies?.items || escalationPolicies.items.length === 0) {
    return <p className="text-sm text-slate-400">No escalation policies configured</p>
  }

  return (
    <div className="space-y-3">
      {escalationPolicies.items.map((policy: EscalationPolicy) => (
        <div key={policy.id} className="p-4 bg-slate-800/50 rounded border border-slate-700">
          <div className="flex items-center justify-between mb-2">
            <div>
              <h4 className="font-medium text-white">Level {policy.level}</h4>
              <p className="text-sm text-slate-400">
                {policy.escalation_type === 'identity' ? policy.identity_name : policy.group_id ? 'Group' : 'Unknown'}
              </p>
            </div>
            <button
              onClick={() => onDelete(policy.id)}
              disabled={isDeleting}
              className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors disabled:opacity-50"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <span className="text-slate-400">Escalation delay: </span>
              <span className="text-white">{policy.escalation_delay_minutes} minutes</span>
            </div>
            <div>
              <span className="text-slate-400">Channels: </span>
              <span className="text-white">{policy.notification_channels?.join(', ') || 'None'}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
