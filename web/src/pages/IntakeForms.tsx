import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, FileText, Trash2, Edit, X } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import AssigneePicker, { AssigneeValue } from '@/components/AssigneePicker'
import { ISSUE_TYPES, issueTypeLabel } from '@/lib/constants/issueTypes'
import type { Organization } from '@/types'

interface IntakeFormFieldSpec {
  id: string
  label: string
  type: 'text' | 'email' | 'textarea' | 'select'
  required: boolean
  options?: string[]
}

interface IntakeForm {
  id: number
  village_id: string
  name: string
  slug: string
  description?: string
  fields: IntakeFormFieldSpec[]
  issue_type: string
  default_assignee_type?: 'identity' | 'org_unit'
  default_assignee_id?: number
  organization_id?: number
  is_public: boolean
  captcha_required: boolean
  is_active: boolean
}

/**
 * Admin builder for configurable intake forms (POST /api/v1/intake-forms)
 * — the CRM-facing entry point that lets a public form submission create a
 * native support Issue without requiring a login. Gated to Admin.
 */
export default function IntakeForms() {
  const [showModal, setShowModal] = useState<'create' | IntakeForm | null>(null)
  const queryClient = useQueryClient()

  const { data: profile } = useQuery({
    queryKey: ['portal-profile'],
    queryFn: () => api.getPortalProfile(),
    staleTime: 60000,
  })
  const isAdmin = profile?.global_role === 'admin' || profile?.tenant_role === 'admin'

  const { data, isLoading } = useQuery({
    queryKey: ['intake-forms'],
    queryFn: () => api.getIntakeForms({ per_page: 100 }),
    enabled: isAdmin,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => {
      console.log('[IntakeForms] Delete form', { id })
      return api.deleteIntakeForm(id)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['intake-forms'], refetchType: 'all' })
      toast.success('Intake form deleted')
    },
    onError: () => toast.error('Failed to delete intake form'),
  })

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">Admin access required</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Intake Forms</h1>
          <p className="mt-2 text-slate-400">
            Public forms that create support issues without requiring a login
          </p>
        </div>
        <Button onClick={() => setShowModal('create')} data-testid="create-intake-form-button">
          <Plus className="w-4 h-4 mr-2" />
          Create Intake Form
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <FileText className="w-12 h-12 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No intake forms configured</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {data?.items?.map((form: IntakeForm) => (
            <Card key={form.id}>
              <CardContent>
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="text-lg font-semibold text-white">{form.name}</h3>
                    <p className="text-sm text-slate-400 font-mono">/intake/{form.slug}</p>
                    <div className="flex gap-2 mt-2">
                      <span className="text-xs px-2 py-0.5 rounded bg-primary-500/20 text-primary-400">
                        {issueTypeLabel(form.issue_type)}
                      </span>
                      {form.is_public && (
                        <span className="text-xs px-2 py-0.5 rounded bg-green-500/20 text-green-400">public</span>
                      )}
                      {form.captcha_required && (
                        <span className="text-xs px-2 py-0.5 rounded bg-yellow-500/20 text-yellow-400">captcha</span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" variant="ghost" onClick={() => setShowModal(form)}>
                      <Edit className="w-4 h-4" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => deleteMutation.mutate(form.id)}>
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {showModal && (
        <IntakeFormModal
          existing={showModal === 'create' ? null : showModal}
          onClose={() => setShowModal(null)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({ queryKey: ['intake-forms'], refetchType: 'all' })
            setShowModal(null)
          }}
        />
      )}
    </div>
  )
}

interface IntakeFormModalProps {
  existing: IntakeForm | null
  onClose: () => void
  onSuccess: () => Promise<void>
}

function IntakeFormModal({ existing, onClose, onSuccess }: IntakeFormModalProps) {
  const [name, setName] = useState(existing?.name || '')
  const [slug, setSlug] = useState(existing?.slug || '')
  const [description, setDescription] = useState(existing?.description || '')
  const [issueType, setIssueType] = useState(existing?.issue_type?.toLowerCase() || 'support')
  const [organizationId, setOrganizationId] = useState(existing?.organization_id ? String(existing.organization_id) : '')
  const [assignee, setAssignee] = useState<AssigneeValue | null>(
    existing?.default_assignee_id && existing?.default_assignee_type
      ? { assignee_type: existing.default_assignee_type, assignee_id: existing.default_assignee_id }
      : null
  )
  const [isPublic, setIsPublic] = useState(existing?.is_public ?? true)
  const [captchaRequired, setCaptchaRequired] = useState(existing?.captcha_required ?? true)
  const [fields, setFields] = useState<IntakeFormFieldSpec[]>(
    existing?.fields?.length ? existing.fields : [{ id: 'email', label: 'Email', type: 'email', required: true }]
  )

  const { data: organizations } = useQuery({
    queryKey: ['organizations-all'],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        name,
        slug,
        description: description || undefined,
        fields,
        issue_type: issueType,
        default_assignee_type: assignee?.assignee_type,
        default_assignee_id: assignee?.assignee_id,
        organization_id: organizationId ? parseInt(organizationId) : undefined,
        is_public: isPublic,
        captcha_required: captchaRequired,
      }
      const action = existing ? 'Update form' : 'Create form'
      console.log(`[IntakeForms] ${action}`, { slug, fieldCount: fields.length, issueType, isPublic })
      return existing ? api.updateIntakeForm(existing.id, payload) : api.createIntakeForm(payload)
    },
    onSuccess: async () => {
      toast.success(existing ? 'Intake form updated' : 'Intake form created')
      await onSuccess()
    },
    onError: () => toast.error('Failed to save intake form'),
  })

  const addField = () => setFields((prev) => [...prev, { id: '', label: '', type: 'text', required: false }])
  const updateField = (index: number, patch: Partial<IntakeFormFieldSpec>) =>
    setFields((prev) => prev.map((f, i) => (i === index ? { ...f, ...patch } : f)))
  const removeField = (index: number) => setFields((prev) => prev.filter((_, i) => i !== index))

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !slug.trim() || fields.length === 0) return
    // Validate select fields have at least one option
    const hasInvalidSelect = fields.some((f) => f.type === 'select' && (!f.options || f.options.length === 0))
    if (hasInvalidSelect) {
      toast.error('Select fields must have at least one option')
      return
    }
    saveMutation.mutate()
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">
            {existing ? 'Edit Intake Form' : 'Create Intake Form'}
          </h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4" data-testid="intake-form-modal">
            <Input label="Name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Support Request" />
            <Input
              label="Slug"
              required
              disabled={!!existing}
              value={slug}
              onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-'))}
              placeholder="support-request"
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Select label="Issue Type" value={issueType} onChange={(e) => setIssueType(e.target.value)}>
                {ISSUE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </Select>
              <Select label="Organization" value={organizationId} onChange={(e) => setOrganizationId(e.target.value)}>
                <option value="">None</option>
                {organizations?.items?.map((org: Organization) => (
                  <option key={org.id} value={org.id}>{org.name}</option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Default Assignee</label>
              <AssigneePicker value={assignee} onChange={setAssignee} />
            </div>
            <div className="flex gap-6">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500" />
                <span className="text-sm text-slate-300">Public</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={captchaRequired} onChange={(e) => setCaptchaRequired(e.target.checked)} className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500" />
                <span className="text-sm text-slate-300">Require CAPTCHA (Altcha)</span>
              </label>
            </div>
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-slate-300">Fields</label>
                <Button type="button" size="sm" variant="ghost" onClick={addField}>
                  <Plus className="w-4 h-4 mr-1" /> Add Field
                </Button>
              </div>
              <div className="space-y-3">
                {fields.map((field, index) => (
                  <div key={index} className="bg-slate-800/30 p-3 rounded space-y-2">
                    <div className="flex gap-2 items-center">
                      <input
                        className="flex-1 px-2 py-1 text-sm bg-slate-900 border border-slate-700 rounded text-white"
                        placeholder="field id (e.g. email)"
                        value={field.id}
                        onChange={(e) => updateField(index, { id: e.target.value })}
                      />
                      <input
                        className="flex-1 px-2 py-1 text-sm bg-slate-900 border border-slate-700 rounded text-white"
                        placeholder="Label"
                        value={field.label}
                        onChange={(e) => updateField(index, { label: e.target.value })}
                      />
                      <select
                        className="px-2 py-1 text-sm bg-slate-900 border border-slate-700 rounded text-white"
                        value={field.type}
                        onChange={(e) => updateField(index, { type: e.target.value as IntakeFormFieldSpec['type'] })}
                      >
                        <option value="text">Text</option>
                        <option value="email">Email</option>
                        <option value="textarea">Textarea</option>
                        <option value="select">Select</option>
                      </select>
                      <label className="flex items-center gap-1 text-xs text-slate-400">
                        <input
                          type="checkbox"
                          checked={field.required}
                          onChange={(e) => updateField(index, { required: e.target.checked })}
                        />
                        req
                      </label>
                      <button type="button" onClick={() => removeField(index)} className="p-1 text-slate-400 hover:text-red-500">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    {field.type === 'select' && (
                      <div className="pl-2 border-l-2 border-slate-700">
                        <div className="text-xs text-slate-400 mb-1">Options</div>
                        <div className="space-y-1">
                          {(field.options || []).map((option, optIndex) => (
                            <div key={optIndex} className="flex gap-1">
                              <input
                                className="flex-1 px-2 py-1 text-xs bg-slate-900 border border-slate-700 rounded text-white"
                                value={option}
                                onChange={(e) => {
                                  const newOptions = [...(field.options || [])]
                                  newOptions[optIndex] = e.target.value
                                  updateField(index, { options: newOptions })
                                }}
                              />
                              <button
                                type="button"
                                onClick={() => {
                                  const newOptions = (field.options || []).filter((_, i) => i !== optIndex)
                                  updateField(index, { options: newOptions })
                                }}
                                className="p-1 text-slate-400 hover:text-red-500"
                              >
                                <X className="w-3 h-3" />
                              </button>
                            </div>
                          ))}
                          <button
                            type="button"
                            onClick={() => {
                              const newOptions = [...(field.options || []), '']
                              updateField(index, { options: newOptions })
                            }}
                            className="text-xs text-primary-400 hover:text-primary-300"
                          >
                            + Add option
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" isLoading={saveMutation.isPending} data-testid="save-intake-form-button">
                {existing ? 'Save Changes' : 'Create Form'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
