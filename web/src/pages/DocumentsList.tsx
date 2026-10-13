import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Plus, Trash2, Search, ChevronRight } from 'lucide-react'
import api from '@/lib/api'

interface Document {
  id: number
  village_id: string
  title: string
  slug: string
  category?: string
  status: string
  is_public: boolean
  visibility: string
  tags?: string[]
  published_at?: string
  created_at: string
  updated_at: string
}

interface Collection {
  id: number
  village_id: string
  name: string
  slug: string
  description?: string
  parent_id?: number
  created_at: string
  updated_at: string
  children: Collection[]
}

function CollectionTree({ collections }: { collections: Collection[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const toggleExpanded = (id: number) => {
    const newExpanded = new Set(expanded)
    if (newExpanded.has(id)) {
      newExpanded.delete(id)
    } else {
      newExpanded.add(id)
    }
    setExpanded(newExpanded)
  }

  const renderCollection = (coll: Collection, depth: number = 0) => {
    const hasChildren = coll.children && coll.children.length > 0
    const isExpanded = expanded.has(coll.id)

    return (
      <div key={coll.id} style={{ marginLeft: `${depth * 16}px` }}>
        <div className="flex items-center gap-1 p-2 hover:bg-slate-700 rounded cursor-pointer group">
          {hasChildren && (
            <button
              onClick={() => toggleExpanded(coll.id)}
              className="w-5 h-5 flex items-center justify-center"
            >
              <ChevronRight className={`w-4 h-4 transition-transform ${isExpanded ? 'rotate-90' : ''}`} />
            </button>
          )}
          {!hasChildren && <div className="w-5" />}
          <span className="text-sm text-slate-300 flex-1 truncate">{coll.name}</span>
        </div>

        {hasChildren && isExpanded && (
          <div>
            {coll.children.map((child) => renderCollection(child, depth + 1))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-4">
      <h3 className="text-sm font-semibold text-amber-400 mb-3">Collections</h3>
      {collections.length === 0 ? (
        <p className="text-xs text-slate-400">No collections yet</p>
      ) : (
        <div className="space-y-1">
          {collections.map((coll) => renderCollection(coll))}
        </div>
      )}
    </div>
  )
}

export default function DocumentsList() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const { data: documentsResponse, isLoading: docsLoading } = useQuery({
    queryKey: ['documents', { search, status: statusFilter }],
    queryFn: () => api.getDocuments({
      page: 1,
      per_page: 50,
      status: statusFilter || undefined,
      q: search || undefined,
    }),
  })

  const { data: collectionsResponse } = useQuery({
    queryKey: ['collections'],
    queryFn: () => api.getCollections(),
  })

  const documents: Document[] = documentsResponse?.items || []
  const collections: Collection[] = collectionsResponse?.collections || []

  const deleteMutation = useMutation({
    mutationFn: (docId: number) => api.deleteDocument(docId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
  })

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-bold text-amber-400">Documents</h1>
            <p className="text-slate-400 mt-2">Create and manage documents and knowledge base articles</p>
          </div>
          <button
            onClick={() => navigate('/documents/new')}
            className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 text-slate-900 px-4 py-2 rounded-lg font-semibold transition-colors"
          >
            <Plus className="w-5 h-5" />
            New Document
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          {/* Sidebar */}
          <div className="lg:col-span-1">
            <CollectionTree collections={collections} />
          </div>

          {/* Main content */}
          <div className="lg:col-span-3 space-y-6">
            {/* Filters */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
                <input
                  type="text"
                  placeholder="Search documents..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-amber-500"
                />
              </div>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
              >
                <option value="">All Statuses</option>
                <option value="draft">Draft</option>
                <option value="published">Published</option>
                <option value="archived">Archived</option>
              </select>
            </div>

            {/* Error state */}
            {/* {error && (
              <div className="bg-red-900 border border-red-700 text-red-100 px-4 py-3 rounded-lg mb-6">
                Failed to load documents. Please try again.
              </div>
            )} */}

            {/* Loading state */}
            {docsLoading && (
              <div className="flex justify-center items-center py-12">
                <div className="text-amber-400">Loading documents...</div>
              </div>
            )}

            {/* Empty state */}
            {!docsLoading && documents.length === 0 && (
              <div className="text-center py-16 bg-slate-800 rounded-lg border border-slate-700">
                <h3 className="text-xl font-semibold text-amber-400 mb-2">No documents found</h3>
                <p className="text-slate-400 mb-6">Create your first document to get started</p>
                <button
                  onClick={() => navigate('/documents/new')}
                  className="bg-amber-500 hover:bg-amber-600 text-slate-900 px-6 py-2 rounded-lg font-semibold transition-colors"
                >
                  Create Document
                </button>
              </div>
            )}

            {/* Documents list */}
            {!docsLoading && documents.length > 0 && (
              <div className="space-y-4">
                {documents.map((doc) => (
                  <div
                    key={doc.id}
                    className="bg-slate-800 border border-slate-700 rounded-lg p-4 hover:border-amber-500 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 cursor-pointer" onClick={() => navigate(`/documents/${doc.slug}`)}>
                        <h3 className="text-amber-400 font-semibold mb-1">{doc.title}</h3>
                        <div className="flex items-center gap-2 text-xs text-slate-400 mb-3">
                          <span className="capitalize">{doc.status}</span>
                          {doc.is_public && <span className="text-blue-400">Public</span>}
                          {doc.category && <span className="text-slate-500">• {doc.category}</span>}
                        </div>

                        {/* Tags */}
                        {doc.tags && doc.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {doc.tags.slice(0, 3).map((tag) => (
                              <span
                                key={tag}
                                className="inline-block bg-slate-700 text-slate-300 text-xs px-2 py-1 rounded"
                              >
                                {tag}
                              </span>
                            ))}
                            {doc.tags.length > 3 && (
                              <span className="inline-block text-slate-400 text-xs px-2 py-1">
                                +{doc.tags.length - 3}
                              </span>
                            )}
                          </div>
                        )}
                      </div>

                      <div className="flex gap-2">
                        <button
                          onClick={() => navigate(`/documents/${doc.slug}`)}
                          className="bg-slate-700 hover:bg-slate-600 text-amber-400 px-3 py-2 rounded text-sm font-semibold transition-colors"
                        >
                          View
                        </button>
                        <button
                          onClick={() => deleteMutation.mutate(doc.id)}
                          disabled={deleteMutation.isPending}
                          className="bg-red-900 hover:bg-red-800 disabled:bg-red-700 text-red-200 px-3 py-2 rounded transition-colors"
                          aria-label={`Delete ${doc.title}`}
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
      </div>
    </div>
  )
}
