import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Plus, Trash2, Search } from 'lucide-react'
import api from '@/lib/api'

interface Page {
  id: number
  village_id: string
  title: string
  slug: string
  status: string
  visibility: string
  is_public: boolean
  author_id: number
  published_at?: string
  created_at: string
  updated_at: string
}

export default function PagesList() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const { data: pagesResponse, isLoading } = useQuery({
    queryKey: ['pages', { search, status: statusFilter }],
    queryFn: () => api.getPages({
      page: 1,
      per_page: 50,
      status: statusFilter || undefined,
      search: search || undefined,
    }),
  })

  const pages: Page[] = pagesResponse?.items || []

  const deleteMutation = useMutation({
    mutationFn: (slug: string) => api.deletePage(slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pages'] })
    },
  })

  const handleStatusToggle = async (page: Page) => {
    const newStatus = page.status === 'draft' ? 'published' : 'draft'
    try {
      await api.updatePage(page.slug, { status: newStatus })
      queryClient.invalidateQueries({ queryKey: ['pages'] })
    } catch (error) {
      console.error('[PagesList] Status toggle failed', error)
    }
  }

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-amber-400">Pages</h1>
            <p className="text-slate-400 mt-2">Create and manage wiki pages</p>
          </div>
          <button
            onClick={() => navigate('/pages/new')}
            className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 text-slate-900 px-4 py-2 rounded-lg font-semibold transition-colors"
          >
            <Plus className="w-5 h-5" />
            New Page
          </button>
        </div>

        {/* Filters */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search pages..."
              className="w-full pl-10 pr-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-amber-500"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
          >
            <option value="">All statuses</option>
            <option value="draft">Draft</option>
            <option value="published">Published</option>
          </select>
        </div>

        {/* Pages table */}
        <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
          {isLoading ? (
            <div className="p-6 text-center text-slate-400">Loading pages...</div>
          ) : pages.length === 0 ? (
            <div className="p-6 text-center text-slate-400">No pages found. Create one to get started!</div>
          ) : (
            <table className="w-full">
              <thead className="border-b border-slate-700 bg-slate-750">
                <tr>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-amber-400">Title</th>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-amber-400">Status</th>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-amber-400">Visibility</th>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-amber-400">Updated</th>
                  <th className="px-6 py-3 text-right text-sm font-semibold text-amber-400">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700">
                {pages.map((page) => (
                  <tr key={page.slug} className="hover:bg-slate-700 transition-colors">
                    <td className="px-6 py-4">
                      <button
                        onClick={() => navigate(`/pages/${page.slug}`)}
                        className="text-amber-400 hover:text-amber-300 font-medium transition-colors text-left"
                      >
                        {page.title}
                      </button>
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-block px-3 py-1 rounded-full text-xs font-semibold ${
                          page.status === 'published'
                            ? 'bg-green-900 text-green-200'
                            : 'bg-slate-700 text-slate-300'
                        }`}
                      >
                        {page.status === 'published' ? '✓ Published' : '📝 Draft'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-300">
                      {page.is_public ? '🌐 Public' : page.visibility}
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-400">
                      {new Date(page.updated_at).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => navigate(`/pages/${page.slug}/edit`)}
                          className="text-slate-400 hover:text-amber-400 transition-colors text-sm"
                        >
                          Edit
                        </button>
                        {page.status === 'draft' && (
                          <button
                            onClick={() => handleStatusToggle(page)}
                            className="text-slate-400 hover:text-green-400 transition-colors text-sm"
                          >
                            Publish
                          </button>
                        )}
                        <button
                          onClick={() => deleteMutation.mutate(page.slug)}
                          disabled={deleteMutation.isPending}
                          className="text-slate-400 hover:text-red-400 transition-colors disabled:text-slate-600"
                          aria-label={`Delete ${page.title}`}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
