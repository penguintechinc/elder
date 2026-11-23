import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Bot,
  CheckCircle,
  Clock,
  ExternalLink,
  History,
  UserCheck,
  XCircle,
} from 'lucide-react'
import api from '@/lib/api'

interface PendingApproval {
  execution_id: string
  playbook_id?: number
  playbook_name?: string
  stream_name?: string
  requested_by?: string
  requested_at?: string | null
  paused_at?: string | null
  created_at?: string
  required_approvals?: number
  current_approvals?: number
  status?: string
}

interface ApprovalHistoryItem {
  approval_id: string
  execution_id: string
  playbook_id: number | null
  playbook_name: string
  approver_identity_id: number | null
  approver_type: 'human' | 'ai'
  decision: string
  comment: string
  decided_at: string | null
}

type ApprovalTab = 'pending' | 'history'

export default function ApprovalCenter() {
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<ApprovalTab>('pending')
  const [selectedApprovalId, setSelectedApprovalId] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)

  const {
    data: pendingResponse,
    isLoading: pendingLoading,
    error: pendingError,
  } = useQuery({
    queryKey: ['my-approvals'],
    queryFn: () => api.listMyApprovals({ page: 1, per_page: 50 }),
  })

  const {
    data: historyResponse,
    isLoading: historyLoading,
    error: historyError,
  } = useQuery({
    queryKey: ['approval-history'],
    queryFn: () => api.getApprovalHistory({ limit: 100 }),
  })

  const rawPending: PendingApproval[] =
    pendingResponse?.pending_approvals || pendingResponse?.items || []
  const pendingApprovals = rawPending.filter(
    (a) => !a.status || a.status === 'pending' || a.status === 'paused_for_approval',
  )

  const historyItems: ApprovalHistoryItem[] = historyResponse?.history || []

  const approveMutation = useMutation({
    mutationFn: (executionId: string) =>
      api.approveExecution(executionId, { comment: comment || undefined }),
    onSuccess: () => {
      setActionError(null)
      queryClient.invalidateQueries({ queryKey: ['my-approvals'] })
      queryClient.invalidateQueries({ queryKey: ['approval-history'] })
      setSelectedApprovalId(null)
      setComment('')
    },
    onError: (err: unknown) => {
      const msg =
        err instanceof Error ? err.message : 'Failed to approve execution'
      setActionError(msg)
    },
  })

  const rejectMutation = useMutation({
    mutationFn: (executionId: string) =>
      api.rejectExecution(executionId, { comment: comment.trim() }),
    onSuccess: () => {
      setActionError(null)
      queryClient.invalidateQueries({ queryKey: ['my-approvals'] })
      queryClient.invalidateQueries({ queryKey: ['approval-history'] })
      setSelectedApprovalId(null)
      setComment('')
    },
    onError: (err: unknown) => {
      const msg =
        err instanceof Error ? err.message : 'Failed to reject execution'
      setActionError(msg)
    },
  })

  const handleReject = (executionId: string) => {
    if (!comment.trim()) {
      setActionError('A comment is required when rejecting an execution.')
      return
    }
    setActionError(null)
    rejectMutation.mutate(executionId)
  }

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-5xl mx-auto">
        <div className="mb-6">
          <h1 className="text-3xl font-bold text-amber-400">Approval Center</h1>
          <p className="text-slate-400 mt-2">
            Review pending human approval requests and audit historical approval decisions
          </p>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-2 border-b border-slate-700 mb-6">
          <button
            type="button"
            onClick={() => setActiveTab('pending')}
            className={`flex items-center gap-2 px-4 py-3 text-sm font-semibold border-b-2 transition-colors ${
              activeTab === 'pending'
                ? 'border-amber-400 text-amber-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Clock className="w-4 h-4" />
            Pending Approvals
            <span className="ml-1 inline-flex items-center justify-center px-2 py-0.5 text-xs rounded-full bg-slate-800 text-amber-300 border border-slate-700">
              {pendingApprovals.length}
            </span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('history')}
            className={`flex items-center gap-2 px-4 py-3 text-sm font-semibold border-b-2 transition-colors ${
              activeTab === 'history'
                ? 'border-amber-400 text-amber-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <History className="w-4 h-4" />
            Approval History (Audit)
            <span className="ml-1 inline-flex items-center justify-center px-2 py-0.5 text-xs rounded-full bg-slate-800 text-slate-300 border border-slate-700">
              {historyItems.length}
            </span>
          </button>
        </div>

        {actionError && (
          <div className="bg-red-900/60 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6 text-sm">
            {actionError}
          </div>
        )}

        {/* Pending Approvals Tab */}
        {activeTab === 'pending' && (
          <>
            {pendingError && (
              <div className="bg-red-900 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6">
                Failed to load pending approvals. Please try again.
              </div>
            )}

            {pendingLoading && (
              <div className="flex justify-center items-center py-12">
                <div className="text-amber-400">Loading pending approvals...</div>
              </div>
            )}

            {!pendingLoading && pendingApprovals.length === 0 && (
              <div className="text-center py-16 bg-slate-800 rounded-lg border border-slate-700">
                <h3 className="text-xl font-semibold text-amber-400 mb-2">
                  No Pending Approvals
                </h3>
                <p className="text-slate-400">
                  All pending approval requests have been processed.
                </p>
              </div>
            )}

            {!pendingLoading && pendingApprovals.length > 0 && (
              <div className="space-y-4">
                {pendingApprovals.map((approval) => {
                  const displayName =
                    approval.playbook_name || approval.stream_name || 'Untitled Playbook'
                  const timestamp =
                    approval.requested_at || approval.paused_at || approval.created_at
                  const isSelected = selectedApprovalId === approval.execution_id

                  return (
                    <div
                      key={approval.execution_id}
                      className={`bg-slate-800 border-2 rounded-lg p-6 transition-colors cursor-pointer ${
                        isSelected
                          ? 'border-amber-500'
                          : 'border-slate-700 hover:border-slate-600'
                      }`}
                      onClick={() => {
                        setSelectedApprovalId(approval.execution_id)
                        setActionError(null)
                      }}
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1">
                          <div className="flex items-center gap-3 mb-2">
                            <h3 className="text-amber-400 font-semibold text-lg">
                              {displayName}
                            </h3>
                            <Link
                              to={`/streams/executions/${approval.execution_id}`}
                              onClick={(e) => e.stopPropagation()}
                              className="inline-flex items-center gap-1 text-xs text-sky-400 hover:text-sky-300 underline"
                            >
                              View Execution
                              <ExternalLink className="w-3 h-3" />
                            </Link>
                          </div>
                          <p className="text-slate-400 text-sm mb-3">
                            Execution ID: {approval.execution_id.slice(0, 8)}
                            {approval.requested_by ? ` • Requested by ${approval.requested_by}` : ''}
                          </p>
                          <div className="flex flex-wrap items-center gap-6 text-sm">
                            {timestamp && (
                              <span className="text-slate-400">
                                Requested: {new Date(timestamp).toLocaleString()}
                              </span>
                            )}
                            {approval.required_approvals !== undefined && (
                              <span className="text-slate-400">
                                Approvals:{' '}
                                <span className="text-amber-400 font-semibold">
                                  {approval.current_approvals ?? 0}/{approval.required_approvals}
                                </span>
                              </span>
                            )}
                          </div>
                        </div>

                        {isSelected && (
                          <div className="flex flex-col gap-2 ml-4">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                approveMutation.mutate(approval.execution_id)
                              }}
                              disabled={approveMutation.isPending}
                              className="flex items-center gap-2 bg-green-600 hover:bg-green-700 disabled:bg-green-500 text-white px-3 py-2 rounded font-semibold text-sm transition-colors"
                            >
                              <CheckCircle className="w-4 h-4" />
                              Approve
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleReject(approval.execution_id)
                              }}
                              disabled={rejectMutation.isPending}
                              className="flex items-center gap-2 bg-red-600 hover:bg-red-700 disabled:bg-red-500 text-white px-3 py-2 rounded font-semibold text-sm transition-colors"
                            >
                              <XCircle className="w-4 h-4" />
                              Reject
                            </button>
                          </div>
                        )}
                      </div>

                      {isSelected && (
                        <div className="mt-4 pt-4 border-t border-slate-700">
                          <label className="block text-slate-300 text-sm mb-2 font-semibold">
                            Comment (optional for approve, required for reject)
                          </label>
                          <textarea
                            value={comment}
                            onChange={(e) => setComment(e.target.value)}
                            onClick={(e) => e.stopPropagation()}
                            placeholder="Add approval notes or rejection reason..."
                            className="w-full bg-slate-700 text-slate-200 border border-slate-600 rounded p-3 text-sm resize-none focus:outline-none focus:border-amber-500"
                            rows={3}
                          />
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </>
        )}

        {/* Approval History (Audit) Tab */}
        {activeTab === 'history' && (
          <>
            {historyError && (
              <div className="bg-red-900 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6">
                Failed to load approval history. Please try again.
              </div>
            )}

            {historyLoading && (
              <div className="flex justify-center items-center py-12">
                <div className="text-amber-400">Loading approval history...</div>
              </div>
            )}

            {!historyLoading && historyItems.length === 0 && (
              <div className="text-center py-16 bg-slate-800 rounded-lg border border-slate-700">
                <h3 className="text-xl font-semibold text-amber-400 mb-2">
                  No Approval History Yet
                </h3>
                <p className="text-slate-400">
                  Completed human and AI approval decisions will appear here for auditing.
                </p>
              </div>
            )}

            {!historyLoading && historyItems.length > 0 && (
              <div className="space-y-3">
                {historyItems.map((item) => {
                  const isApproved =
                    item.decision === 'approve' || item.decision === 'approved'
                  const isAi = item.approver_type === 'ai'

                  return (
                    <div
                      key={item.approval_id}
                      className="bg-slate-800 border border-slate-700 rounded-lg p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4"
                    >
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-amber-400 font-semibold">
                            {item.playbook_name}
                          </span>
                          <span
                            className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border ${
                              isAi
                                ? 'bg-purple-950 text-purple-200 border-purple-700'
                                : 'bg-sky-950 text-sky-200 border-sky-700'
                            }`}
                          >
                            {isAi ? (
                              <>
                                <Bot className="w-3 h-3" />
                                AI
                              </>
                            ) : (
                              <>
                                <UserCheck className="w-3 h-3" />
                                Human
                              </>
                            )}
                          </span>
                          <span
                            className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${
                              isApproved
                                ? 'bg-green-900 text-green-200'
                                : 'bg-red-900 text-red-200'
                            }`}
                          >
                            {isApproved ? 'Approved' : 'Rejected'}
                          </span>
                        </div>

                        <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
                          <Link
                            to={`/streams/executions/${item.execution_id}`}
                            className="inline-flex items-center gap-1 text-sky-400 hover:text-sky-300 underline font-mono"
                          >
                            {item.execution_id.slice(0, 8)}
                            <ExternalLink className="w-3 h-3" />
                          </Link>
                          {item.approver_identity_id !== null && (
                            <span>Approver ID: {item.approver_identity_id}</span>
                          )}
                          {item.decided_at && (
                            <span>{new Date(item.decided_at).toLocaleString()}</span>
                          )}
                        </div>

                        {item.comment && (
                          <p className="text-sm text-slate-300 pt-1">{item.comment}</p>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
