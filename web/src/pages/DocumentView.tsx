import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Edit2, Trash2, Share2, Clock } from 'lucide-react'
import api from '@/lib/api'

interface Version {
  id: number
  version_number: number
  title: string
  author_identity_id: number
  created_at: string
}

interface VersionDetails extends Version {
  body_html: string
  body_text: string
  doc_id: number
}

export default function DocumentView() {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [selectedVersion, setSelectedVersion] = useState<VersionDetails | null>(null)

  const { data: document, isLoading, error } = useQuery({
    queryKey: ['document', slug],
    queryFn: () => {
      if (!slug) throw new Error('Slug is required')
      return api.getDocument(slug)
    },
    enabled: !!slug,
  })

  const { data: versionsResponse } = useQuery({
    queryKey: ['documentVersions', document?.id],
    queryFn: () => {
      if (!document?.id) throw new Error('Document ID is required')
      return api.getDocumentVersions(document.id)
    },
    enabled: !!document?.id,
  })

  const versions: Version[] = versionsResponse?.versions || []

  const deleteMutation = useMutation({
    mutationFn: () => {
      if (!document?.id) throw new Error('Document ID is required')
      return api.deleteDocument(document.id)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      navigate('/documents')
    },
  })

  const publishMutation = useMutation({
    mutationFn: () => {
      if (!document?.id) throw new Error('Document ID is required')
      return api.publishDocument(document.id)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['document', slug] })
    },
  })

  const restoreVersionMutation = useMutation({
    mutationFn: (versionNumber: number) => {
      if (!document?.id) throw new Error('Document ID is required')
      return api.restoreDocumentVersion(document.id, versionNumber)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['document', slug] })
      queryClient.invalidateQueries({ queryKey: ['documentVersions', document?.id] })
      setSelectedVersion(null)
    },
  })

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-amber-400">Loading document...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-red-400">Failed to load document. Please try again.</div>
      </div>
    )
  }

  if (!document) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-slate-400">Document not found</div>
      </div>
    )
  }

  const displayContent = selectedVersion || document

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <button
            onClick={() => navigate('/documents')}
            className="flex items-center gap-2 text-amber-400 hover:text-amber-300 transition-colors mb-4"
          >
            <ArrowLeft className="w-5 h-5" />
            Back to Documents
          </button>

          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-4xl font-bold text-amber-400">{document.title}</h1>
              <div className="flex items-center gap-3 text-slate-400 mt-3">
                <span className="text-sm">
                  {document.status === 'draft' ? '📝 Draft' : '✓ Published'}
                </span>
                {document.category && <span>• {document.category}</span>}
                {document.is_public && <span>• 🌐 Public</span>}
              </div>
            </div>

            <div className="flex gap-2">
              <button
                onClick={() => navigate(`/documents/${slug}/edit`)}
                className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 text-amber-400 px-4 py-2 rounded transition-colors"
              >
                <Edit2 className="w-4 h-4" />
                Edit
              </button>
              {document.status === 'draft' && (
                <button
                  onClick={() => publishMutation.mutate()}
                  disabled={publishMutation.isPending}
                  className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-4 py-2 rounded font-semibold transition-colors"
                >
                  <Share2 className="w-4 h-4" />
                  {publishMutation.isPending ? 'Publishing...' : 'Publish'}
                </button>
              )}
              <button
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
                className="bg-red-900 hover:bg-red-800 disabled:bg-red-700 text-red-200 px-4 py-2 rounded transition-colors"
                aria-label="Delete document"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Tags and metadata */}
          <div className="mt-6 space-y-3 text-sm text-slate-400">
            {document.tags && document.tags.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {document.tags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-block bg-slate-700 text-slate-300 px-3 py-1 rounded-full text-xs"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
            <div className="flex gap-6">
              <span>Created: {new Date(document.created_at).toLocaleDateString()}</span>
              {document.published_at && (
                <span>Published: {new Date(document.published_at).toLocaleDateString()}</span>
              )}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Main content */}
          <div className="lg:col-span-2">
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-6 prose prose-invert max-w-none">
              <div
                dangerouslySetInnerHTML={{ __html: displayContent.body_html || '<p>No content</p>' }}
              />
            </div>
          </div>

          {/* Version history sidebar */}
          <div className="lg:col-span-1">
            <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 sticky top-6">
              <div className="flex items-center gap-2 mb-4">
                <Clock className="w-5 h-5 text-amber-400" />
                <h3 className="text-sm font-semibold text-amber-400">Version History</h3>
              </div>

              {versions.length === 0 ? (
                <p className="text-xs text-slate-400">No previous versions</p>
              ) : (
                <div className="space-y-2 max-h-96 overflow-y-auto">
                  {versions.map((version) => (
                    <button
                      key={version.id}
                      onClick={async () => {
                        const versionDetails = await api.getDocumentVersion(
                          document.id,
                          version.version_number
                        )
                        setSelectedVersion(versionDetails)
                      }}
                      className={`w-full text-left p-2 rounded text-xs transition-colors ${
                        selectedVersion?.id === version.id
                          ? 'bg-amber-500 text-slate-900'
                          : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                      }`}
                    >
                      <div className="font-semibold">v{version.version_number}</div>
                      <div className="text-slate-400">
                        {new Date(version.created_at).toLocaleDateString()}
                      </div>
                    </button>
                  ))}
                </div>
              )}

              {selectedVersion && selectedVersion.id !== document.id && (
                <button
                  onClick={() =>
                    restoreVersionMutation.mutate(selectedVersion.version_number)
                  }
                  disabled={restoreVersionMutation.isPending}
                  className="w-full mt-4 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-500 text-white text-xs font-semibold py-2 px-3 rounded transition-colors"
                >
                  {restoreVersionMutation.isPending ? 'Restoring...' : 'Restore Version'}
                </button>
              )}

              {selectedVersion && (
                <button
                  onClick={() => {
                    setSelectedVersion(null)
                  }}
                  className="w-full mt-2 bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs font-semibold py-2 px-3 rounded transition-colors"
                >
                  View Latest
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
