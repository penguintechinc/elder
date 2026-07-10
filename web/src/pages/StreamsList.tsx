import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Plus, Copy, Trash2 } from 'lucide-react'
import api from '@/lib/api'

interface Stream {
  id: number
  name: string
  description?: string
  trigger_type?: string
  is_enabled: boolean
  execution_count: number
  created_at: string
  updated_at: string
}

export default function StreamsList() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: response, isLoading, error } = useQuery({
    queryKey: ['streams'],
    queryFn: () => api.listStreams({ page: 1, per_page: 50 }),
  })

  const streams: Stream[] = response?.items || []

  const createMutation = useMutation({
    mutationFn: () =>
      api.createStream({
        name: 'Untitled Playbook',
        description: '',
        is_enabled: false,
      }),
    onSuccess: (data) => {
      console.log('[StreamsList] Created stream', { id: data.id })
      queryClient.invalidateQueries({ queryKey: ['streams'] })
      navigate(`/streams/${data.id}`)
    },
  })

  const duplicateMutation = useMutation({
    mutationFn: (id: number) => api.duplicateStream(id),
    onSuccess: () => {
      console.log('[StreamsList] Duplicated stream')
      queryClient.invalidateQueries({ queryKey: ['streams'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteStream(id),
    onSuccess: () => {
      console.log('[StreamsList] Deleted stream')
      queryClient.invalidateQueries({ queryKey: ['streams'] })
    },
  })

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-amber-400">Playbooks</h1>
            <p className="text-slate-400 mt-2">Create and manage automation workflows</p>
          </div>
          <button
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
            className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-4 py-2 rounded-lg font-semibold transition-colors"
          >
            <Plus className="w-5 h-5" />
            New Playbook
          </button>
        </div>

        {error && (
          <div className="bg-red-900 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6">
            Failed to load playbooks. Please try again.
          </div>
        )}

        {isLoading && (
          <div className="flex justify-center items-center py-12">
            <div className="text-amber-400">Loading playbooks...</div>
          </div>
        )}

        {!isLoading && streams.length === 0 && (
          <div className="text-center py-16 bg-slate-800 rounded-lg border border-slate-700">
            <h3 className="text-xl font-semibold text-amber-400 mb-2">No playbooks yet</h3>
            <p className="text-slate-400 mb-6">Create your first automation workflow to get started</p>
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending}
              className="bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-6 py-2 rounded-lg font-semibold transition-colors"
            >
              Create Playbook
            </button>
          </div>
        )}

        {!isLoading && streams.length > 0 && (
          <div className="overflow-x-auto bg-slate-800 border border-slate-700 rounded-lg">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-700 bg-slate-900">
                  <th className="text-left px-6 py-3 text-amber-400 font-semibold">Name</th>
                  <th className="text-left px-6 py-3 text-amber-400 font-semibold">Trigger</th>
                  <th className="text-left px-6 py-3 text-amber-400 font-semibold">Executions</th>
                  <th className="text-left px-6 py-3 text-amber-400 font-semibold">Status</th>
                  <th className="text-right px-6 py-3 text-amber-400 font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {streams.map((stream) => (
                  <tr key={stream.id} className="border-b border-slate-700 hover:bg-slate-700 transition-colors">
                    <td className="px-6 py-4 text-amber-400 font-medium">{stream.name}</td>
                    <td className="px-6 py-4 text-slate-300 text-sm">{stream.trigger_type || '-'}</td>
                    <td className="px-6 py-4 text-slate-300 text-sm">{stream.execution_count}</td>
                    <td className="px-6 py-4">
                      <span className={`inline-flex px-3 py-1 rounded-full text-xs font-medium ${
                        stream.is_enabled
                          ? 'bg-green-900 text-green-200'
                          : 'bg-slate-700 text-slate-400'
                      }`}>
                        {stream.is_enabled ? 'Enabled' : 'Disabled'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <button
                        onClick={() => navigate(`/streams/${stream.id}`)}
                        className="text-amber-400 hover:text-amber-300 font-semibold text-sm mr-3"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => duplicateMutation.mutate(stream.id)}
                        disabled={duplicateMutation.isPending}
                        className="text-blue-400 hover:text-blue-300 font-semibold text-sm mr-3 disabled:text-blue-600"
                        aria-label={`Duplicate ${stream.name}`}
                      >
                        <Copy className="w-4 h-4 inline" />
                      </button>
                      <button
                        onClick={() => deleteMutation.mutate(stream.id)}
                        disabled={deleteMutation.isPending}
                        className="text-red-400 hover:text-red-300 font-semibold text-sm disabled:text-red-600"
                        aria-label={`Delete ${stream.name}`}
                      >
                        <Trash2 className="w-4 h-4 inline" />
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
