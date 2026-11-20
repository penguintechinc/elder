import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Edit, Trash2, X, Plus, CalendarClock } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'

const OVERRIDE_REASONS = [
  'Sick Leave / Illness',
  'Vacation / PTO',
  'Family / Personal Emergency',
  'Medical Appointment',
  'Bereavement',
  'Conference / Training',
  'Travel / Flight Delay',
  'Swap / Shift Trade',
  'Workload Balancing / Burnout Prevention',
  'Holiday Coverage',
] as const

function formatValidDate(value?: string | null): string | null {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString()
}

function formatValidDateTime(value?: string | null): string | null {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleString()
}

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

  const { data: currentOnCallRaw } = useQuery({
    queryKey: queryKeys.onCall.current(rotation.scope_type, rotation.scope_type === 'organization' ? rotation.organization_id : rotation.service_id),
    queryFn: () => api.getCurrentOnCall(rotation.scope_type, rotation.scope_type === 'organization' ? rotation.organization_id ?? 0 : rotation.service_id ?? 0) as Promise<Record<string, unknown>>,
    enabled: !!rotation.organization_id || !!rotation.service_id,
  })

  const currentOnCall: CurrentOnCall | undefined = (() => {
    if (!currentOnCallRaw) return undefined
    const rotationsList = currentOnCallRaw.rotations as Array<{ rotation_id?: number; current_oncall?: CurrentOnCall }> | undefined
    if (Array.isArray(rotationsList)) {
      const matched = rotationsList.find((r) => r.rotation_id === rotation.id)?.current_oncall
      if (matched?.identity_name) return matched
      return undefined
    }
    const direct = (currentOnCallRaw.current_oncall as CurrentOnCall | undefined) ?? (currentOnCallRaw as unknown as CurrentOnCall)
    return direct?.identity_name ? direct : undefined
  })()

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
              {formatValidDate(currentOnCall.shift_end) && (
                <p className="text-xs text-slate-500 mt-1">
                  Until {formatValidDate(currentOnCall.shift_end)}
                </p>
              )}
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

interface OverrideRecord {
  id: number
  original_identity_id?: number | null
  original_identity_name?: string | null
  override_identity_id: number
  override_identity_name?: string
  start_datetime: string
  end_datetime: string
  reason?: string | null
}

function ScheduleTab({ rotation }: ScheduleTabProps) {
  const queryClient = useQueryClient()
  const [overrideIdentityId, setOverrideIdentityId] = useState('')
  const [originalIdentityId, setOriginalIdentityId] = useState('')
  const [startDatetime, setStartDatetime] = useState('')
  const [endDatetime, setEndDatetime] = useState('')
  const [reasonSelect, setReasonSelect] = useState<string>(OVERRIDE_REASONS[0])
  const [customReason, setCustomReason] = useState('')

  const { data: overridesData, isLoading: isLoadingOverrides } = useQuery({
    queryKey: ['on-call-overrides', rotation.id],
    queryFn: () => api.getOnCallOverrides(rotation.id),
  })

  const { data: identitiesData } = useQuery({
    queryKey: ['identities-human-dropdown'],
    queryFn: () => api.getIdentities({ per_page: 1000 }),
  })

  const humanIdentities = (
    (identitiesData?.items || []) as Array<{
      id: number
      username: string
      full_name?: string
      identity_type?: string
    }>
  ).filter((i) => !i.identity_type || i.identity_type.toLowerCase() === 'human')

  const createOverrideMutation = useMutation({
    mutationFn: (payload: {
      override_identity_id: number
      original_identity_id?: number
      start_datetime: string
      end_datetime: string
      reason?: string
    }) => api.createOnCallOverride(rotation.id, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['on-call-overrides', rotation.id] })
      await queryClient.invalidateQueries({ queryKey: queryKeys.onCall.all })
      toast.success('Schedule override added')
      setOverrideIdentityId('')
      setOriginalIdentityId('')
      setStartDatetime('')
      setEndDatetime('')
      setCustomReason('')
    },
    onError: () => toast.error('Failed to add schedule override'),
  })

  const deleteOverrideMutation = useMutation({
    mutationFn: (overrideId: number) => api.deleteOnCallOverride(rotation.id, overrideId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['on-call-overrides', rotation.id] })
      await queryClient.invalidateQueries({ queryKey: queryKeys.onCall.all })
      toast.success('Override removed')
    },
    onError: () => toast.error('Failed to remove override'),
  })

  const handleAddOverride = (e: React.FormEvent) => {
    e.preventDefault()
    if (!overrideIdentityId || !startDatetime || !endDatetime) {
      toast.error('Covering person, start date/time, and end date/time are required')
      return
    }
    if (new Date(endDatetime) <= new Date(startDatetime)) {
      toast.error('Override end time must be after start time')
      return
    }
    const reason =
      reasonSelect === 'other'
        ? customReason.trim() || 'Other'
        : reasonSelect
    createOverrideMutation.mutate({
      override_identity_id: parseInt(overrideIdentityId, 10),
      original_identity_id: originalIdentityId ? parseInt(originalIdentityId, 10) : undefined,
      start_datetime: new Date(startDatetime).toISOString(),
      end_datetime: new Date(endDatetime).toISOString(),
      reason,
    })
  }

  const overrides: OverrideRecord[] = overridesData?.items || overridesData?.overrides || []
  const formattedStartDate = formatValidDate(rotation.rotation_start_date)

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
              <p className="text-sm text-white mt-1">{formattedStartDate || 'Not set'}</p>
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

      {/* Schedule Overrides */}
      <div className="p-4 bg-slate-800/50 rounded border border-slate-700 space-y-4">
        <div className="flex items-center gap-2">
          <CalendarClock className="w-4 h-4 text-primary-400" />
          <h3 className="text-sm font-medium text-white">Schedule Overrides</h3>
        </div>

        <form onSubmit={handleAddOverride} className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Select
            label="Covering Person"
            required
            value={overrideIdentityId}
            onChange={(e) => setOverrideIdentityId(e.target.value)}
          >
            <option value="">Select covering person...</option>
            {humanIdentities.map((ident) => (
              <option key={ident.id} value={ident.id}>
                {ident.full_name || ident.username}
              </option>
            ))}
          </Select>

          <Select
            label="Replacing Person (Optional)"
            value={originalIdentityId}
            onChange={(e) => setOriginalIdentityId(e.target.value)}
          >
            <option value="">Anyone scheduled</option>
            {humanIdentities.map((ident) => (
              <option key={ident.id} value={ident.id}>
                {ident.full_name || ident.username}
              </option>
            ))}
          </Select>

          <Input
            label="Start Date/Time"
            type="datetime-local"
            required
            value={startDatetime}
            onChange={(e) => setStartDatetime(e.target.value)}
          />

          <Input
            label="End Date/Time"
            type="datetime-local"
            required
            value={endDatetime}
            onChange={(e) => setEndDatetime(e.target.value)}
          />

          <Select
            label="Override Reason"
            value={reasonSelect}
            onChange={(e) => setReasonSelect(e.target.value)}
          >
            {OVERRIDE_REASONS.map((reason) => (
              <option key={reason} value={reason}>
                {reason}
              </option>
            ))}
            <option value="other">Other</option>
          </Select>

          {reasonSelect === 'other' && (
            <Input
              label="Custom Reason"
              required
              value={customReason}
              onChange={(e) => setCustomReason(e.target.value)}
              placeholder="Specify override reason..."
            />
          )}

          <div className="md:col-span-2 flex justify-end">
            <Button type="submit" size="sm" disabled={createOverrideMutation.isPending}>
              <Plus className="w-4 h-4 mr-1" />
              Add Override
            </Button>
          </div>
        </form>

        {isLoadingOverrides ? (
          <p className="text-xs text-slate-400">Loading overrides...</p>
        ) : overrides.length === 0 ? (
          <p className="text-xs text-slate-400">No schedule overrides configured</p>
        ) : (
          <div className="space-y-2 pt-2 border-t border-slate-700">
            {overrides.map((ov) => (
              <div
                key={ov.id}
                className="flex items-center justify-between gap-3 p-3 rounded bg-slate-900/60 border border-slate-700"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-white">
                      {ov.override_identity_name || `User #${ov.override_identity_id}`}
                    </span>
                    {ov.original_identity_name && (
                      <span className="text-xs text-slate-400">
                        covering for {ov.original_identity_name}
                      </span>
                    )}
                    {ov.reason && (
                      <span className="text-xs px-2 py-0.5 rounded bg-yellow-500/20 text-yellow-400">
                        {ov.reason}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-400 mt-1">
                    {formatValidDateTime(ov.start_datetime) || ov.start_datetime} —{' '}
                    {formatValidDateTime(ov.end_datetime) || ov.end_datetime}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => deleteOverrideMutation.mutate(ov.id)}
                  aria-label="Delete override"
                  className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
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
