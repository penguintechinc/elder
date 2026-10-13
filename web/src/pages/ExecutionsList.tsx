import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import api from '@/lib/api'

interface Execution {
  id: number
  execution_id: string
  stream_id: number
  stream_name: string
  status: string
  trigger_type?: string
  playbook_id?: number
  input_data?: Record<string, unknown>
  output_data?: Record<string, unknown>
  error_message?: string
  started_at?: string
  completed_at?: string
  created_at: string
  duration_ms?: number
}

export default function ExecutionsList() {
  const navigate = useNavigate()

  const { data: response, isLoading, error } = useQuery({
    queryKey: ['stream-executions'],
    queryFn: () => api.listAllStreamExecutions({ page: 1, per_page: 50 }),
  })

  const executions: Execution[] = response?.data || []

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
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-amber-400">Executions</h1>
          <p className="text-slate-400 mt-2">View and monitor automation workflow executions</p>
        </div>

        {error && (
          <div className="bg-red-900 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6">
            Failed to load executions. Please try again.
          </div>
        )}

        {isLoading && (
          <div className="flex justify-center items-center py-12">
            <div className="text-amber-400">Loading executions...</div>
          </div>
        )}

        {!isLoading && executions.length === 0 && (
          <div className="text-center py-16 bg-slate-800 rounded-lg border border-slate-700">
            <h3 className="text-xl font-semibold text-amber-400 mb-2">No executions</h3>
            <p className="text-slate-400">Executions will appear here once playbooks are triggered</p>
          </div>
        )}

        {!isLoading && executions.length > 0 && (
          <div className="overflow-x-auto bg-slate-800 border border-slate-700 rounded-lg">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-700 bg-slate-900">
                  <th className="text-left px-6 py-3 text-amber-400 font-semibold">ID</th>
                  <th className="text-left px-6 py-3 text-amber-400 font-semibold">Playbook</th>
                  <th className="text-left px-6 py-3 text-amber-400 font-semibold">Trigger</th>
                  <th className="text-left px-6 py-3 text-amber-400 font-semibold">Status</th>
                  <th className="text-left px-6 py-3 text-amber-400 font-semibold">Created</th>
                  <th className="text-right px-6 py-3 text-amber-400 font-semibold">Action</th>
                </tr>
              </thead>
              <tbody>
                {executions.map((exec) => (
                  <tr key={exec.execution_id} className="border-b border-slate-700 hover:bg-slate-700 transition-colors">
                    <td className="px-6 py-4 text-slate-300 text-sm font-mono" data-testid="execution-id">{exec.execution_id.slice(0, 8)}</td>
                    <td className="px-6 py-4 text-amber-400 font-medium" data-testid="stream-name">{exec.stream_name}</td>
                    <td className="px-6 py-4 text-slate-300 text-sm">{exec.trigger_type || '-'}</td>
                    <td className="px-6 py-4">
                      <span className={`inline-flex px-3 py-1 rounded-full text-xs font-medium ${getStatusColor(exec.status)}`}>
                        {exec.status.charAt(0).toUpperCase() + exec.status.slice(1)}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-slate-400 text-sm">
                      {new Date(exec.created_at).toLocaleDateString()} {new Date(exec.created_at).toLocaleTimeString()}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <button
                        onClick={() => navigate(`/streams/executions/${exec.execution_id}`)}
                        className="text-amber-400 hover:text-amber-300 font-semibold text-sm"
                        data-testid="view-details-btn"
                      >
                        View Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
