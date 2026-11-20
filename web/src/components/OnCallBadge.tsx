import { useQuery } from '@tanstack/react-query'
import { User, AlertCircle } from 'lucide-react'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'

interface OnCallBadgeProps {
  scopeType: 'organization' | 'service'
  scopeId: number
  compact?: boolean
}

export default function OnCallBadge({ scopeType, scopeId, compact = false }: OnCallBadgeProps) {
  const { data: currentOnCall, isLoading } = useQuery({
    queryKey: queryKeys.onCall.current(scopeType, scopeId),
    queryFn: () => api.getCurrentOnCall(scopeType, scopeId),
    refetchInterval: 60000, // Auto-refresh every 60 seconds
    staleTime: 30000, // Consider data fresh for 30 seconds
    retry: false,
  })

  const resolvedCurrent =
    currentOnCall?.current_oncall ??
    currentOnCall?.rotations?.[0]?.current_oncall ??
    currentOnCall

  if (isLoading || !resolvedCurrent || !resolvedCurrent.identity_name) {
    return null
  }

  const formattedShiftEnd =
    resolvedCurrent.shift_end &&
    !Number.isNaN(new Date(resolvedCurrent.shift_end).getTime())
      ? new Date(resolvedCurrent.shift_end).toLocaleDateString()
      : null

  if (compact) {
    return (
      <div className="flex items-center gap-2 text-sm px-3 py-2 rounded bg-primary-500/10 border border-primary-500/20">
        <User className="w-4 h-4 text-primary-400 flex-shrink-0" />
        <span className="text-white">
          On-Call: <strong>{resolvedCurrent.identity_name}</strong>
        </span>
        {resolvedCurrent.is_override && (
          <span className="ml-auto text-xs px-2 py-0.5 rounded bg-yellow-500/20 text-yellow-400">
            Override
          </span>
        )}
      </div>
    )
  }

  // Full mode: card with avatar and details
  return (
    <div className="p-4 bg-gradient-to-br from-primary-500/10 to-primary-600/5 border border-primary-500/20 rounded-lg">
      <h4 className="text-sm font-medium text-primary-400 mb-3">Currently On-Call</h4>
      <div className="flex items-center gap-4">
        {/* Avatar */}
        <div className="w-12 h-12 rounded-full bg-gradient-to-br from-primary-400 to-primary-600 flex items-center justify-center text-white font-bold text-lg flex-shrink-0">
          {resolvedCurrent.identity_name?.charAt(0).toUpperCase() || 'U'}
        </div>

        {/* Details */}
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-white truncate">{resolvedCurrent.identity_name}</p>
          {resolvedCurrent.identity_email && (
            <p className="text-sm text-slate-400 truncate">{resolvedCurrent.identity_email}</p>
          )}
          {formattedShiftEnd && (
            <p className="text-xs text-slate-500 mt-1">
              Until {formattedShiftEnd}
            </p>
          )}
        </div>

        {/* Override badge */}
        {resolvedCurrent.is_override && (
          <div className="flex items-center gap-1 px-2 py-1 rounded bg-yellow-500/20 border border-yellow-500/30 flex-shrink-0">
            <AlertCircle className="w-3 h-3 text-yellow-400" />
            <span className="text-xs text-yellow-400 font-medium">Override</span>
          </div>
        )}
      </div>
    </div>
  )
}
