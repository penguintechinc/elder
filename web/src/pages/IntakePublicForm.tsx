import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'

interface PublicFormField {
  id: string
  label: string
  type: 'text' | 'email' | 'textarea' | 'select'
  required: boolean
  options?: string[]
}

/**
 * Unauthenticated public submission page for a configured intake form
 * (GET/POST /api/v1/intake/:slug). CAPTCHA (Altcha) widget rendering is a
 * follow-up — forms with captcha_required=true cannot complete a real
 * submission from this page yet (the /submit call requires a solved
 * `altcha` payload the backend verifies; see
 * apps/api/modules/helpdesk/services/altcha.py). Intentionally scoped small
 * per the unified-issues-ui plan: proves the wiring end-to-end for
 * non-CAPTCHA forms, not the full public-facing experience.
 */
export default function IntakePublicForm() {
  const { slug } = useParams<{ slug: string }>()
  const [values, setValues] = useState<Record<string, string>>({})
  const [submitted, setSubmitted] = useState(false)

  const { data: form, isLoading, error } = useQuery({
    queryKey: ['public-intake-form', slug],
    queryFn: () => api.getPublicIntakeForm(slug!),
    enabled: !!slug,
    retry: false,
  })

  const submitMutation = useMutation({
    mutationFn: () => api.submitPublicIntakeForm(slug!, { fields: values }),
    onSuccess: () => setSubmitted(true),
  })

  const handleChange = (fieldId: string, value: string) => {
    setValues((prev) => ({ ...prev, [fieldId]: value }))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    submitMutation.mutate()
  }

  if (isLoading) {
    return <div className="min-h-screen bg-slate-900" />
  }

  if (error || !form) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-900 p-4">
        <Card className="w-full max-w-md">
          <CardContent className="text-center py-12">
            <p className="text-slate-400">This form is not available.</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (submitted) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-900 p-4">
        <Card className="w-full max-w-md">
          <CardContent className="text-center py-12">
            <p className="text-white text-lg font-semibold mb-2">Thank you</p>
            <p className="text-slate-400">Your request has been submitted.</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900 p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <h1 className="text-xl font-semibold text-white">{form.name}</h1>
          {form.description && <p className="text-sm text-slate-400 mt-1">{form.description}</p>}
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4" data-testid="public-intake-form">
            {(form.fields as PublicFormField[]).map((field) => (
              <div key={field.id}>
                <label htmlFor={`field-${field.id}`} className="block text-sm font-medium text-slate-300 mb-1.5">
                  {field.label}{field.required && ' *'}
                </label>
                {field.type === 'textarea' ? (
                  <textarea
                    id={`field-${field.id}`}
                    required={field.required}
                    value={values[field.id] || ''}
                    onChange={(e) => handleChange(field.id, e.target.value)}
                    rows={4}
                    className="block w-full px-4 py-2 text-sm bg-slate-800 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                ) : (
                  <input
                    id={`field-${field.id}`}
                    type={field.type === 'email' ? 'email' : 'text'}
                    required={field.required}
                    value={values[field.id] || ''}
                    onChange={(e) => handleChange(field.id, e.target.value)}
                    className="block w-full px-4 py-2 text-sm bg-slate-800 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                )}
              </div>
            ))}
            {form.captcha_required && (
              <p className="text-xs text-yellow-400">
                This form requires CAPTCHA verification, which is not yet implemented on this page.
              </p>
            )}
            <Button type="submit" isLoading={submitMutation.isPending} disabled={form.captcha_required} className="w-full">
              Submit
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
