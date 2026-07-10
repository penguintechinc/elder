import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle, XCircle } from 'lucide-react'
import api from '@/lib/api'

interface Approval {
  execution_id: string
  stream_name: string
  created_at: string
  required_approvals: number
  current_approvals: number
  status: string
}

export default function ApprovalCenter() {
  const queryClient = useQueryClient()
  const [selectedApprovalId, setSelectedApprovalId] = useState<string | null>(null)
  const [comment, setComment] = useState('')

  const { data: response, isLoading, error } = useQuery({
    queryKey: ['my-approvals'],
    queryFn: () => api.listMyApprovals({ page: 1, per_page: 50 }),
  })

  const approvals: Approval[] = response?.items || []

  const approveMutation = useMutation({
    mutationFn: (executionId: string) => api.approveExecution(executionId, { comment: comment || undefined }),
    onSuccess: () => {
      console.log('[ApprovalCenter] Approved execution', { executionId: selectedApprovalId })
      queryClient.invalidateQueries({ queryKey: ['my-approvals'] })
      setSelectedApprovalId(null)
      setComment('')
    },
  })

  const rejectMutation = useMutation({
    mutationFn: (executionId: string) => api.rejectExecution(executionId, { comment: comment || undefined }),
    onSuccess: () => {
      console.log('[ApprovalCenter] Rejected execution', { executionId: selectedApprovalId })
      queryClient.invalidateQueries({ queryKey: ['my-approvals'] })
      setSelectedApprovalId(null)
      setComment('')
    },
  })

  const pendingApprovals = approvals.filter(a => a.status === 'pending')
  const completedApprovals = approvals.filter(a => a.status !== 'pending')

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-4xl mx-auto">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-amber-400">Approval Center</h1>
          <p className="text-slate-400 mt-2">Review and approve pending workflow executions</p>
        </div>

        {error && (
          <div className="bg-red-900 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6">
            Failed to load approvals. Please try again.
          </div>
        )}

        {isLoading && (
          <div className="flex justify-center items-center py-12">
            <div className="text-amber-400">Loading approvals...</div>
          </div>
        )}

        {!isLoading && pendingApprovals.length === 0 && (
          <div className="text-center py-16 bg-slate-800 rounded-lg border border-slate-700 mb-8">
            <h3 className="text-xl font-semibold text-amber-400 mb-2">No Pending Approvals</h3>
            <p className="text-slate-400">All pending approvals have been processed</p>
          </div>
        )}

        {!isLoading && pendingApprovals.length > 0 && (
          <div className="mb-8">
            <h2 className="text-2xl font-bold text-amber-400 mb-4">Pending Approvals</h2>
            <div className="space-y-4">
              {pendingApprovals.map((approval) => (
                <div
                  key={approval.execution_id}
                  className={`bg-slate-800 border-2 rounded-lg p-6 transition-colors cursor-pointer ${
                    selectedApprovalId === approval.execution_id
                      ? 'border-amber-500'
                      : 'border-slate-700 hover:border-slate-600'
                  }`}
                  onClick={() => setSelectedApprovalId(approval.execution_id)}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <h3 className="text-amber-400 font-semibold text-lg mb-2">{approval.stream_name}</h3>
                      <p className="text-slate-400 text-sm mb-3">ID: {approval.execution_id.slice(0, 8)}</p>
                      <div className="flex items-center gap-6 text-sm">
                        <span className="text-slate-400">
                          Created: {new Date(approval.created_at).toLocaleString()}
                        </span>
                        <span className="text-slate-400">
                          Approvals: <span className="text-amber-400 font-semibold">{approval.current_approvals}/{approval.required_approvals}</span>
                        </span>
                      </div>
                    </div>
                    {selectedApprovalId === approval.execution_id && (
                      <div className="flex flex-col gap-2 ml-4">
                        <button
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
                          onClick={(e) => {
                            e.stopPropagation()
                            rejectMutation.mutate(approval.execution_id)
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

                  {selectedApprovalId === approval.execution_id && (
                    <div className="mt-4 pt-4 border-t border-slate-700">
                      <label className="block text-slate-300 text-sm mb-2 font-semibold">Comment (optional)</label>
                      <textarea
                        value={comment}
                        onChange={(e) => setComment(e.target.value)}
                        placeholder="Add a comment..."
                        className="w-full bg-slate-700 text-slate-300 border border-slate-600 rounded p-3 text-sm resize-none focus:outline-none focus:border-amber-500"
                        rows={3}
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {completedApprovals.length > 0 && (
          <div>
            <h2 className="text-2xl font-bold text-amber-400 mb-4">Completed Approvals</h2>
            <div className="space-y-3">
              {completedApprovals.map((approval) => (
                <div
                  key={approval.execution_id}
                  className="bg-slate-800 border border-slate-700 rounded-lg p-4 flex items-center justify-between"
                >
                  <div>
                    <p className="text-amber-400 font-semibold">{approval.stream_name}</p>
                    <p className="text-slate-400 text-sm">{approval.execution_id.slice(0, 8)}</p>
                  </div>
                  <span className={`inline-flex px-3 py-1 rounded-full text-xs font-medium ${
                    approval.status === 'approved'
                      ? 'bg-green-900 text-green-200'
                      : 'bg-red-900 text-red-200'
                  }`}>
                    {approval.status.charAt(0).toUpperCase() + approval.status.slice(1)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
