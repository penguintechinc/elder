import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Edit2, Trash2, LinkIcon } from 'lucide-react'
import api from '@/lib/api'

interface Backlink {
  source_module: string
  source_type: string
  source_id: string
  context: string
  created_at: string
}

export default function PageView() {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: page, isLoading, error } = useQuery({
    queryKey: ['page', slug],
    queryFn: () => {
      if (!slug) throw new Error('Slug is required')
      return api.getPage(slug)
    },
    enabled: !!slug,
  })

  const { data: backlinksResponse } = useQuery({
    queryKey: ['pageBacklinks', slug],
    queryFn: () => {
      if (!slug) throw new Error('Slug is required')
      return api.getPageBacklinks(slug)
    },
    enabled: !!slug,
  })

  const backlinks: Backlink[] = backlinksResponse?.backlinks || []

  const deleteMutation = useMutation({
    mutationFn: () => {
      if (!slug) throw new Error('Slug is required')
      return api.deletePage(slug)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pages'] })
      navigate('/pages')
    },
  })

  const publishMutation = useMutation({
    mutationFn: () => {
      if (!slug) throw new Error('Slug is required')
      return api.updatePage(slug, { status: 'published' })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['page', slug] })
    },
  })

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-amber-400">Loading page...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-red-400">Failed to load page. Please try again.</div>
      </div>
    )
  }

  if (!page) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-slate-400">Page not found</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <button
            onClick={() => navigate('/pages')}
            className="flex items-center gap-2 text-amber-400 hover:text-amber-300 transition-colors mb-4"
          >
            <ArrowLeft className="w-5 h-5" />
            Back to Pages
          </button>

          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-4xl font-bold text-amber-400">{page.title}</h1>
              <div className="flex items-center gap-3 text-slate-400 mt-3">
                <span className="text-sm">
                  {page.status === 'draft' ? '📝 Draft' : '✓ Published'}
                </span>
                {page.is_public && <span>• 🌐 Public</span>}
                <span>• {page.visibility}</span>
              </div>
            </div>

            <div className="flex gap-2">
              <button
                onClick={() => navigate(`/pages/${slug}/edit`)}
                className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 text-amber-400 px-4 py-2 rounded transition-colors"
              >
                <Edit2 className="w-4 h-4" />
                Edit
              </button>
              {page.status === 'draft' && (
                <button
                  onClick={() => publishMutation.mutate()}
                  disabled={publishMutation.isPending}
                  className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-4 py-2 rounded font-semibold transition-colors"
                >
                  {publishMutation.isPending ? 'Publishing...' : 'Publish'}
                </button>
              )}
              <button
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
                className="bg-red-900 hover:bg-red-800 disabled:bg-red-700 text-red-200 px-4 py-2 rounded transition-colors"
                aria-label={`Delete ${page.title}`}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Metadata */}
          <div className="mt-6 space-y-2 text-sm text-slate-400">
            <div className="flex gap-6">
              <span>Created: {new Date(page.created_at).toLocaleDateString()}</span>
              {page.published_at && (
                <span>Published: {new Date(page.published_at).toLocaleDateString()}</span>
              )}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Main content */}
          <div className="lg:col-span-2">
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-6 prose prose-invert max-w-none">
              <div
                dangerouslySetInnerHTML={{ __html: page.body_html || '<p>No content</p>' }}
              />
            </div>
          </div>

          {/* Backlinks sidebar */}
          <div className="lg:col-span-1">
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 sticky top-6">
              <div className="flex items-center gap-2 mb-4">
                <LinkIcon className="w-5 h-5 text-amber-400" />
                <h3 className="text-sm font-semibold text-amber-400">Backlinks</h3>
              </div>

              {backlinks.length === 0 ? (
                <p className="text-xs text-slate-400">No references to this page</p>
              ) : (
                <div className="space-y-3 max-h-96 overflow-y-auto">
                  {backlinks.map((link, idx) => (
                    <div
                      key={idx}
                      className="p-2 bg-slate-700 rounded text-xs border border-slate-600"
                    >
                      <div className="font-semibold text-amber-300">
                        {link.source_type}
                      </div>
                      <div className="text-slate-300 truncate">{link.source_id}</div>
                      {link.context && (
                        <div className="mt-1 text-slate-400 truncate italic">
                          {link.context}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
