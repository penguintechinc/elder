import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import api from '@/lib/api'

interface ExecutionDetail {
  id: string
  stream_id: number
  stream_name: string
  status: string
  triggered_by?: string
  created_at: string
  completed_at?: string
  node_results?: Record<string, unknown>
}

export default function ExecutionDetail() {
  const { executionId } = useParams<{ executionId: string }>()
  const navigate = useNavigate()

  const { data: execution, isLoading, error } = useQuery({
    queryKey: ['execution', executionId],
    queryFn: () => executionId ? api.getStreamExecution(0, executionId) : null,
    enabled: !!executionId,
  })

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'success':
        return 'bg-green-900 text-green-200'
      case 'failed':
        return 'bg-red-900 text-red-200'
      case 'running':
        return 'bg-blue-900 text-blue-200'
      case 'pending':
        return 'bg-yellow-900 text-yellow-200'
      default:
        return 'bg-slate-700 text-slate-300'
    }
  }

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-4xl mx-auto">
        <button
          onClick={() => navigate('/streams/executions')}
          className="flex items-center gap-2 text-amber-400 hover:text-amber-300 transition-colors mb-6"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Executions
        </button>

        {error && (
          <div className="bg-red-900 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6">
            Failed to load execution details.
          </div>
        )}

        {isLoading && (
          <div className="flex justify-center items-center py-12">
            <div className="text-amber-400">Loading execution...</div>
          </div>
        )}

        {execution && (
          <>
            <div className="mb-8">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h1 className="text-3xl font-bold text-amber-400">{execution.stream_name}</h1>
                  <p className="text-slate-400 mt-1">Execution ID: <span className="font-mono text-sm">{execution.id}</span></p>
                </div>
                <span className={`inline-flex px-4 py-2 rounded-lg text-sm font-semibold ${getStatusColor(execution.status)}`}>
                  {execution.status.charAt(0).toUpperCase() + execution.status.slice(1)}
                </span>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4 mb-8">
              <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
                <p className="text-slate-400 text-sm mb-1">Started</p>
                <p className="text-amber-400 font-semibold">
                  {new Date(execution.created_at).toLocaleString()}
                </p>
              </div>

              {execution.completed_at && (
                <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
                  <p className="text-slate-400 text-sm mb-1">Completed</p>
                  <p className="text-amber-400 font-semibold">
                    {new Date(execution.completed_at).toLocaleString()}
                  </p>
                </div>
              )}

              {execution.triggered_by && (
                <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
                  <p className="text-slate-400 text-sm mb-1">Triggered By</p>
                  <p className="text-amber-400 font-semibold">{execution.triggered_by}</p>
                </div>
              )}
            </div>

            {execution.node_results && Object.keys(execution.node_results).length > 0 && (
              <div className="bg-slate-800 border border-slate-700 rounded-lg p-6">
                <h2 className="text-xl font-semibold text-amber-400 mb-4">Node Results</h2>
                <div className="space-y-4">
                  {Object.entries(execution.node_results).map(([nodeId, result]) => (
                    <div key={nodeId} className="bg-slate-900 rounded p-4 border border-slate-700">
                      <h3 className="text-amber-400 font-semibold mb-2">{nodeId}</h3>
                      <pre className="text-slate-300 text-sm bg-slate-800 rounded p-3 overflow-x-auto">
                        {JSON.stringify(result, null, 2)}
                      </pre>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
