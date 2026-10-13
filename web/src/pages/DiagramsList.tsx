import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Plus, Trash2 } from 'lucide-react'
import api from '@/lib/api'

interface Diagram {
  id: number
  title: string
  description?: string
  status: string
  is_public: boolean
  is_template: boolean
  tags: string[]
  thumbnail_url?: string
  owner_identity_id: number
  created_at: string
  updated_at: string
}

export default function DiagramsList() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: response, isLoading, error } = useQuery({
    queryKey: ['diagrams'],
    queryFn: () => api.getDiagrams({ page: 1, per_page: 50 }),
  })

  const diagrams: Diagram[] = response?.items || []

  const createMutation = useMutation({
    mutationFn: () =>
      api.createDiagram({
        title: 'Untitled Diagram',
        description: '',
        status: 'draft',
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['diagrams'] })
      navigate(`/diagrams/${data.id}`)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteDiagram(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagrams'] })
    },
  })

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-amber-400">Diagrams</h1>
            <p className="text-slate-400 mt-2">Create and manage infrastructure diagrams</p>
          </div>
          <button
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
            className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-4 py-2 rounded-lg font-semibold transition-colors"
          >
            <Plus className="w-5 h-5" />
            New Diagram
          </button>
        </div>

        {/* Error state */}
        {error && (
          <div className="bg-red-900 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6">
            Failed to load diagrams. Please try again.
          </div>
        )}

        {/* Loading state */}
        {isLoading && (
          <div className="flex justify-center items-center py-12">
            <div className="text-amber-400">Loading diagrams...</div>
          </div>
        )}

        {/* Empty state */}
        {!isLoading && diagrams.length === 0 && (
          <div className="text-center py-16 bg-slate-800 rounded-lg border border-slate-700">
            <h3 className="text-xl font-semibold text-amber-400 mb-2">No diagrams yet</h3>
            <p className="text-slate-400 mb-6">Create your first diagram to get started</p>
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending}
              className="bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-6 py-2 rounded-lg font-semibold transition-colors"
            >
              Create Diagram
            </button>
          </div>
        )}

        {/* Diagrams grid */}
        {!isLoading && diagrams.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {diagrams.map((diagram) => (
              <div
                key={diagram.id}
                className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden hover:border-amber-500 transition-colors"
              >
                {/* Thumbnail */}
                <div className="aspect-video bg-slate-700 border-b border-slate-700 flex items-center justify-center">
                  {diagram.thumbnail_url ? (
                    <img
                      src={diagram.thumbnail_url}
                      alt={diagram.title}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <div className="text-slate-500 text-sm">No preview</div>
                  )}
                </div>

                {/* Content */}
                <div className="p-4">
                  <h3 className="text-amber-400 font-semibold truncate mb-1">{diagram.title}</h3>
                  {diagram.description && (
                    <p className="text-slate-400 text-sm line-clamp-2 mb-3">{diagram.description}</p>
                  )}

                  {/* Tags */}
                  {diagram.tags && diagram.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-3">
                      {diagram.tags.slice(0, 2).map((tag) => (
                        <span
                          key={tag}
                          className="inline-block bg-slate-700 text-slate-300 text-xs px-2 py-1 rounded"
                        >
                          {tag}
                        </span>
                      ))}
                      {diagram.tags.length > 2 && (
                        <span className="inline-block text-slate-400 text-xs px-2 py-1">
                          +{diagram.tags.length - 2}
                        </span>
                      )}
                    </div>
                  )}

                  {/* Metadata */}
                  <div className="flex items-center justify-between text-xs text-slate-500 mb-4 border-t border-slate-700 pt-3">
                    <span className="capitalize">{diagram.status}</span>
                    {diagram.is_public && <span className="text-blue-400">Public</span>}
                  </div>

                  {/* Actions */}
                  <div className="flex gap-2">
                    <button
                      onClick={() => navigate(`/diagrams/${diagram.id}`)}
                      className="flex-1 bg-slate-700 hover:bg-slate-600 text-amber-400 px-3 py-2 rounded font-semibold text-sm transition-colors"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => deleteMutation.mutate(diagram.id)}
                      disabled={deleteMutation.isPending}
                      className="bg-red-900 hover:bg-red-800 disabled:bg-red-700 text-red-200 px-3 py-2 rounded transition-colors"
                      aria-label={`Delete ${diagram.title}`}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
