import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Save } from 'lucide-react'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Link from '@tiptap/extension-link'
import api from '@/lib/api'

interface PageForm {
  title: string
  body_html: string
  status: 'draft' | 'published'
  visibility: 'public' | 'authenticated' | 'roles' | 'users'
  visibility_roles?: string[]
  visibility_users?: number[]
  is_public?: boolean
}

function EditorToolbar({ editor }: { editor: ReturnType<typeof useEditor> | null }) {
  if (!editor) return null

  return (
    <div className="flex flex-wrap gap-1 p-3 bg-slate-800 border-b border-slate-700 rounded-t-lg">
      <button
        onClick={() => editor.chain().focus().toggleBold().run()}
        disabled={!editor.can().chain().focus().toggleBold().run()}
        className={`px-3 py-1 rounded text-sm font-semibold transition-colors ${
          editor.isActive('bold')
            ? 'bg-amber-500 text-slate-900'
            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
        }`}
      >
        B
      </button>
      <button
        onClick={() => editor.chain().focus().toggleItalic().run()}
        disabled={!editor.can().chain().focus().toggleItalic().run()}
        className={`px-3 py-1 rounded text-sm font-semibold transition-colors ${
          editor.isActive('italic')
            ? 'bg-amber-500 text-slate-900'
            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
        }`}
      >
        I
      </button>
      <button
        onClick={() => editor.chain().focus().toggleStrike().run()}
        disabled={!editor.can().chain().focus().toggleStrike().run()}
        className={`px-3 py-1 rounded text-sm font-semibold transition-colors ${
          editor.isActive('strike')
            ? 'bg-amber-500 text-slate-900'
            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
        }`}
      >
        S
      </button>

      <div className="w-px bg-slate-600 mx-1" />

      <button
        onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}
        className={`px-3 py-1 rounded text-sm font-semibold transition-colors ${
          editor.isActive('heading', { level: 1 })
            ? 'bg-amber-500 text-slate-900'
            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
        }`}
      >
        H1
      </button>
      <button
        onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        className={`px-3 py-1 rounded text-sm font-semibold transition-colors ${
          editor.isActive('heading', { level: 2 })
            ? 'bg-amber-500 text-slate-900'
            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
        }`}
      >
        H2
      </button>
      <button
        onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
        className={`px-3 py-1 rounded text-sm font-semibold transition-colors ${
          editor.isActive('heading', { level: 3 })
            ? 'bg-amber-500 text-slate-900'
            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
        }`}
      >
        H3
      </button>

      <div className="w-px bg-slate-600 mx-1" />

      <button
        onClick={() => editor.chain().focus().toggleBulletList().run()}
        className={`px-3 py-1 rounded text-sm font-semibold transition-colors ${
          editor.isActive('bulletList')
            ? 'bg-amber-500 text-slate-900'
            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
        }`}
      >
        UL
      </button>
      <button
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
        className={`px-3 py-1 rounded text-sm font-semibold transition-colors ${
          editor.isActive('orderedList')
            ? 'bg-amber-500 text-slate-900'
            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
        }`}
      >
        OL
      </button>

      <div className="w-px bg-slate-600 mx-1" />

      <button
        onClick={() => {
          const url = prompt('Enter URL:')
          if (url) {
            editor.chain().focus().extendMarkRange('link').setLink({ href: url }).run()
          }
        }}
        className={`px-3 py-1 rounded text-sm font-semibold transition-colors ${
          editor.isActive('link')
            ? 'bg-amber-500 text-slate-900'
            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
        }`}
      >
        Link
      </button>

      <div className="w-px bg-slate-600 mx-1" />

      <button
        onClick={() => editor.chain().focus().clearNodes().run()}
        className="px-3 py-1 rounded text-sm font-semibold bg-slate-700 text-slate-300 hover:bg-slate-600 transition-colors"
      >
        Clear
      </button>
    </div>
  )
}

export default function PageEditor() {
  const { slug } = useParams<{ slug?: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const isNew = !slug || slug === 'new'

  const [form, setForm] = useState<PageForm>({
    title: '',
    body_html: '',
    status: 'draft',
    visibility: 'authenticated',
  })

  const editor = useEditor({
    extensions: [
      StarterKit,
      Link.configure({
        openOnClick: false,
      }),
    ],
    content: '',
    editorProps: {
      attributes: {
        class:
          'prose prose-invert max-w-none px-4 py-3 bg-slate-900 text-slate-100 focus:outline-none min-h-96',
      },
    },
  })

  const { data: page, isLoading } = useQuery({
    queryKey: ['page', slug],
    queryFn: () => api.getPage(slug as string),
    enabled: !isNew && !!slug,
  })

  useEffect(() => {
    if (page && !isNew) {
      setForm({
        title: page.title,
        body_html: page.body_html || '',
        status: page.status || 'draft',
        visibility: page.visibility || 'authenticated',
        visibility_roles: page.visibility_roles,
        visibility_users: page.visibility_users,
        is_public: page.is_public,
      })
      if (editor) {
        editor.commands.setContent(page.body_html || '')
      }
    }
  }, [page, isNew, editor])

  const createMutation = useMutation({
    mutationFn: (data: PageForm) => api.createPage(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['pages'] })
      navigate(`/pages/${result.slug}`)
    },
  })

  const updateMutation = useMutation({
    mutationFn: (data: Partial<PageForm>) => api.updatePage(slug as string, data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['pages'] })
      queryClient.invalidateQueries({ queryKey: ['page', slug] })
      navigate(`/pages/${result.slug || slug}`)
    },
  })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!form.title.trim()) {
      alert('Title is required')
      return
    }

    if (!editor || editor.isEmpty) {
      alert('Content is required')
      return
    }

    const body_html = editor.getHTML()

    if (isNew) {
      createMutation.mutate({
        ...form,
        body_html,
      })
    } else {
      updateMutation.mutate({
        title: form.title,
        body_html,
        status: form.status,
        visibility: form.visibility,
        is_public: form.is_public,
      })
    }
  }

  if (!isNew && isLoading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-amber-400">Loading page...</div>
      </div>
    )
  }

  const isSubmitting = createMutation.isPending || updateMutation.isPending

  return (
    <div className="min-h-screen bg-slate-900 p-6">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <button
            onClick={() => navigate('/pages')}
            className="flex items-center gap-2 text-amber-400 hover:text-amber-300 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
            Back to Pages
          </button>
          <button
            onClick={handleSubmit}
            disabled={isSubmitting}
            className="flex items-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:bg-amber-400 text-slate-900 px-4 py-2 rounded-lg font-semibold transition-colors"
          >
            <Save className="w-5 h-5" />
            {isSubmitting ? 'Saving...' : 'Save'}
          </button>
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
              placeholder="Page title..."
              className="w-full px-4 py-3 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-amber-500"
            />
          </div>

          {/* Status and Visibility */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label htmlFor="status" className="block text-sm font-semibold text-amber-400 mb-2">
                Status
              </label>
              <select
                id="status"
                value={form.status}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    status: e.target.value as 'draft' | 'published',
                  }))
                }
                className="w-full px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
              >
                <option value="draft">Draft</option>
                <option value="published">Published</option>
              </select>
            </div>

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
                    visibility: e.target.value as PageForm['visibility'],
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

          {/* Editor */}
          <div>
            <label className="block text-sm font-semibold text-amber-400 mb-2">
              Content * (WYSIWYG Editor)
            </label>
            <div className="border border-slate-700 rounded-lg overflow-hidden">
              <EditorToolbar editor={editor} />
              <EditorContent editor={editor} />
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
              {isSubmitting ? 'Saving...' : isNew ? 'Create Page' : 'Update Page'}
            </button>
            <button
              type="button"
              onClick={() => navigate('/pages')}
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
