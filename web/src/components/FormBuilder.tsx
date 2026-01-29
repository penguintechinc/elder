import { useState, useEffect, useMemo } from 'react'
import { Key } from 'lucide-react'
import toast from 'react-hot-toast'
import Input from '@/components/Input'
import Select from '@/components/Select'
import { FormField, FormConfig, processFormData, getDefaultValues, validateForm } from '@/types/form'

// Password generator helper
const generatePassword = () => {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
  let password = ''
  for (let i = 0; i < 14; i++) {
    password += chars.charAt(Math.floor(Math.random() * chars.length))
  }
  return password
}

interface FormBuilderProps {
  config: FormConfig
  initialValues?: Record<string, any>
  onSubmit: (data: Record<string, any>) => void
  onCancel?: () => void
  isLoading?: boolean
  className?: string
}

export default function FormBuilder({
  config,
  initialValues,
  onSubmit,
  onCancel,
  isLoading = false,
  className = '',
}: FormBuilderProps) {
  const defaultValues = useMemo(() => getDefaultValues(config.fields), [config.fields])
  const [values, setValues] = useState<Record<string, any>>(() => ({
    ...defaultValues,
    ...initialValues,
  }))
  const [errors, setErrors] = useState<Record<string, string>>({})

  // Reset values when initialValues change
  useEffect(() => {
    setValues({
      ...defaultValues,
      ...initialValues,
    })
    setErrors({})
  }, [initialValues, defaultValues])

  const handleChange = (name: string, value: any) => {
    setValues(prev => ({ ...prev, [name]: value }))
    // Clear error when user starts typing
    if (errors[name]) {
      setErrors(prev => {
        const next = { ...prev }
        delete next[name]
        return next
      })
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()

    console.log('[FormBuilder] Submit triggered, current values:', values)

    // Validate all fields
    const validationErrors = validateForm(values, config.fields)
    if (Object.keys(validationErrors).length > 0) {
      console.warn('[FormBuilder] Validation errors:', validationErrors)
      setErrors(validationErrors)
      return
    }

    const processedData = processFormData(values, config.fields)
    console.log('[FormBuilder] Validation passed, submitting:', processedData)
    onSubmit(processedData)
  }

  const renderField = (field: FormField) => {
    // Check conditional visibility
    if (field.triggerField && !values[field.triggerField]) {
      return null
    }
    if (field.showWhen && !field.showWhen(values)) {
      return null
    }

    if (field.hidden) {
      return null
    }

    const value = values[field.name]
    const error = errors[field.name]

    // Helper to wrap field with error message
    const withError = (element: React.ReactNode) => (
      <div key={field.name}>
        {element}
        {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
      </div>
    )

    switch (field.type) {
      case 'select':
        return withError(
          <Select
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={value}
            onChange={(e) => handleChange(field.name, e.target.value)}
          >
            {!field.required && <option value="">Select...</option>}
            {field.options?.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </Select>
        )

      case 'checkbox':
        return (
          <div key={field.name} className="space-y-2">
            <label className="flex items-center gap-2 text-sm text-yellow-500 cursor-pointer">
              <input
                type="checkbox"
                checked={!!value}
                onChange={(e) => handleChange(field.name, e.target.checked)}
                disabled={field.disabled}
                className="rounded border-slate-600 bg-slate-700 text-primary-500 focus:ring-primary-500"
              />
              {field.label}
            </label>
            {field.helpText && (
              <p className="text-xs text-slate-500 ml-6">{field.helpText}</p>
            )}
          </div>
        )

      case 'textarea':
      case 'multiline':
        return (
          <div key={field.name} className="space-y-1">
            <label className="text-sm font-medium text-yellow-500">{field.label}</label>
            <textarea
              required={field.required}
              disabled={field.disabled}
              value={value || ''}
              onChange={(e) => handleChange(field.name, e.target.value)}
              placeholder={field.placeholder}
              rows={field.rows || 4}
              className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
            />
            {field.helpText && (
              <p className="text-xs text-slate-500">{field.helpText}</p>
            )}
          </div>
        )

      case 'number':
        return withError(
          <Input
            type="number"
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={value || ''}
            onChange={(e) => handleChange(field.name, e.target.value)}
            placeholder={field.placeholder}
          />
        )

      case 'password':
        return (
          <Input
            key={field.name}
            type="password"
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={value || ''}
            onChange={(e) => handleChange(field.name, e.target.value)}
            placeholder={field.placeholder}
          />
        )

      case 'password_generate':
        return (
          <div key={field.name} className="space-y-1">
            <label className="text-sm font-medium text-yellow-500">{field.label}</label>
            <div className="flex gap-2">
              <Input
                type="text"
                required={field.required}
                disabled={field.disabled}
                value={value || ''}
                onChange={(e) => handleChange(field.name, e.target.value)}
                placeholder={field.placeholder}
                className="flex-1 font-mono"
              />
              <button
                type="button"
                onClick={() => {
                  const newPassword = generatePassword()
                  handleChange(field.name, newPassword)
                  toast.success('Password generated')
                }}
                className="px-3 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition-colors"
                title="Generate random password"
              >
                <Key className="w-4 h-4" />
              </button>
            </div>
          </div>
        )

      case 'email':
        return withError(
          <Input
            type="email"
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={value || ''}
            onChange={(e) => handleChange(field.name, e.target.value)}
            placeholder={field.placeholder}
          />
        )

      case 'date':
        return (
          <Input
            key={field.name}
            type="date"
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={value || ''}
            onChange={(e) => handleChange(field.name, e.target.value)}
            placeholder={field.placeholder}
          />
        )

      case 'color':
        return (
          <div key={field.name} className="space-y-1">
            <label className="text-sm font-medium text-yellow-500">{field.label}</label>
            <div className="flex gap-2 items-center">
              <input
                type="color"
                value={value || '#3b82f6'}
                onChange={(e) => handleChange(field.name, e.target.value)}
                disabled={field.disabled}
                className="w-10 h-10 rounded cursor-pointer"
              />
              <Input
                value={value || ''}
                onChange={(e) => handleChange(field.name, e.target.value)}
                placeholder="#3b82f6"
                className="flex-1"
              />
            </div>
          </div>
        )

      // Default: text, username, url, domain, ip, path, slug
      default:
        return withError(
          <Input
            label={field.label}
            required={field.required}
            disabled={field.disabled}
            value={value || ''}
            onChange={(e) => handleChange(field.name, e.target.value)}
            placeholder={field.placeholder}
          />
        )
    }
  }

  return (
    <form onSubmit={handleSubmit} className={`space-y-4 ${className}`}>
      {config.fields.map(renderField)}

      <div className="flex gap-3 pt-4">
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="flex-1 px-4 py-2 text-slate-300 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
          >
            {config.cancelLabel || 'Cancel'}
          </button>
        )}
        <button
          type="submit"
          disabled={isLoading}
          className="flex-1 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors disabled:opacity-50"
        >
          {isLoading ? 'Saving...' : (config.submitLabel || 'Submit')}
        </button>
      </div>
    </form>
  )
}

// Re-export types for convenience
export type { FormField, FormConfig, SelectOption } from '@/types/form'
export { processFieldValue, processFormData, getDefaultValues } from '@/types/form'
