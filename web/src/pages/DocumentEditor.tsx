import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Save, X } from 'lucide-react'
import api from '@/lib/api'

interface DocumentForm {
  title: string
  body: string
  category?: string
  tags: string[]
  visibility: 'public' | 'authenticated' | 'roles' | 'users'
  visibility_roles?: string[]
  visibility_users?: number[]
}

export default function DocumentEditor() {
  const { slug } = useParams<{ slug?: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const isNew = !slug || slug === 'new'

  const [form, setForm] = useState<DocumentForm>({
    title: '',
    body: '',
    category: '',
    tags: [],
    visibility: 'authenticated',
  })
  const [newTag, setNewTag] = useState('')
  const [showPreview, setShowPreview] = useState(false)

  const { data: document, isLoading } = useQuery({
    queryKey: ['document', slug],
    queryFn: () => api.getDocument(slug as string),
    enabled: !isNew && !!slug,
  })

  useEffect(() => {
    if (document && !isNew) {
      setForm({
        title: document.title,
        // Prefer the raw markdown source so editing round-trips without
        // formatting loss; body_text is a tag-stripped fallback for older rows.
        body: document.body_markdown ?? document.body_text ?? '',
        category: document.category || '',
        tags: document.tags || [],
        visibility: document.visibility || 'authenticated',
        visibility_roles: document.visibility_roles,
        visibility_users: document.visibility_users,
      })
    }
  }, [document, isNew])

  const createMutation = useMutation({
    mutationFn: (data: DocumentForm) => api.createDocument(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      navigate(`/documents/${result.slug}`)
    },
  })

  const updateMutation = useMutation({
    mutationFn: (data: Partial<DocumentForm>) =>
      api.updateDocument(document?.id as number, data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      queryClient.invalidateQueries({ queryKey: ['document', slug] })
      navigate(`/documents/${result.slug || slug}`)
    },
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!form.title.trim()) {
      alert('Title is required')
      return
    }

    if (!form.body.trim()) {
      alert('Content is required')
      return
    }

    if (isNew) {
      createMutation.mutate(form)
    } else {
      updateMutation.mutate(form)
    }
  }

  const handleAddTag = () => {
    if (newTag.trim() && !form.tags.includes(newTag.trim())) {
      setForm((prev) => ({
        ...prev,
        tags: [...prev.tags, newTag.trim()],
      }))
      setNewTag('')
    }
  }

  const handleRemoveTag = (tag: string) => {
    setForm((prev) => ({
      ...prev,
      tags: prev.tags.filter((t) => t !== tag),
    }))
  }

  if (!isNew && isLoading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-amber-400">Loading document...</div>
      </div>
    )
  }

  const isSubmitting = createMutation.isPending || updateMutation.isPending

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <button
            onClick={() => navigate('/documents')}
            className="flex items-center gap-2 text-amber-400 hover:text-amber-300 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
            Back to Documents
          </button>
          <div className="flex gap-2">
            <button
              onClick={handleSubmit}
              disabled={isSubmitting}
              className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-4 py-2 rounded-lg font-semibold transition-colors"
            >
              <Save className="w-5 h-5" />
              {isSubmitting ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Title */}
          <div>
            <label htmlFor="title" className="block text-sm font-semibold text-amber-400 mb-2">
              Title *
            </label>
            <input
              id="title"
              type="text"
              value={form.title}
              onChange={(e) => setForm((prev) => ({ ...prev, title: e.target.value }))}
              placeholder="Document title..."
              className="w-full px-4 py-3 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-amber-500"
            />
          </div>

          {/* Category */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label htmlFor="category" className="block text-sm font-semibold text-amber-400 mb-2">
                Category
              </label>
              <input
                id="category"
                type="text"
                value={form.category || ''}
                onChange={(e) => setForm((prev) => ({ ...prev, category: e.target.value }))}
                placeholder="e.g., Technical, Process, Policy..."
                className="w-full px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-amber-500"
              />
            </div>

            {/* Visibility */}
            <div>
              <label htmlFor="visibility" className="block text-sm font-semibold text-amber-400 mb-2">
                Visibility
              </label>
              <select
                id="visibility"
                value={form.visibility}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    visibility: e.target.value as DocumentForm['visibility'],
                  }))
                }
                className="w-full px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
              >
                <option value="authenticated">Authenticated users only</option>
                <option value="public">Public (anyone can view)</option>
                <option value="roles">Specific roles</option>
                <option value="users">Specific users</option>
              </select>
            </div>
          </div>

          {/* Tags */}
          <div>
            <label htmlFor="tags" className="block text-sm font-semibold text-amber-400 mb-2">
              Tags
            </label>
            <div className="flex gap-2 mb-2">
              <input
                id="tags"
                type="text"
                value={newTag}
                onChange={(e) => setNewTag(e.target.value)}
                onKeyPress={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    handleAddTag()
                  }
                }}
                placeholder="Add a tag and press Enter..."
                className="flex-1 px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-amber-500"
              />
              <button
                type="button"
                onClick={handleAddTag}
                className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition-colors"
              >
                Add
              </button>
            </div>
            {form.tags.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {form.tags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center gap-2 bg-slate-700 text-slate-300 px-3 py-1 rounded-full text-sm"
                  >
                    {tag}
                    <button
                      type="button"
                      onClick={() => handleRemoveTag(tag)}
                      className="hover:text-red-400 transition-colors"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Content */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label htmlFor="body" className="block text-sm font-semibold text-amber-400">
                Content * (Markdown)
              </label>
              <button
                type="button"
                onClick={() => setShowPreview(!showPreview)}
                className="text-xs text-amber-400 hover:text-amber-300 transition-colors"
              >
                {showPreview ? 'Hide' : 'Show'} Preview
              </button>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <textarea
                id="body"
                value={form.body}
                onChange={(e) => setForm((prev) => ({ ...prev, body: e.target.value }))}
                placeholder="Write your document in Markdown format..."
                rows={20}
                className="px-4 py-3 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-amber-500 font-mono text-sm"
              />
              {showPreview && (
                <div className="bg-slate-800 border border-slate-700 rounded-lg p-4 overflow-y-auto max-h-[600px]">
                  <div className="prose prose-invert max-w-none text-sm">
                    {form.body ? (
                      <pre className="text-slate-300 whitespace-pre-wrap break-words text-xs">
                        {form.body}
                      </pre>
                    ) : (
                      <p className="text-slate-400">Preview will appear here...</p>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Submit */}
          <div className="flex gap-4 pt-6 border-t border-slate-700">
            <button
              type="submit"
              disabled={isSubmitting}
              className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-6 py-3 rounded-lg font-semibold transition-colors"
            >
              <Save className="w-5 h-5" />
              {isSubmitting ? 'Saving...' : isNew ? 'Create Document' : 'Update Document'}
            </button>
            <button
              type="button"
              onClick={() => navigate('/documents')}
              className="px-6 py-3 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg font-semibold transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
