import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import { FormConfig } from '@/types/form'

interface ApiError {
  response?: {
    data?: {
      message?: string
    }
  }
  message?: string
}

interface RegisterFormData {
  email: string
  password: string
  full_name?: string
  tenant?: string
  confirmPassword?: string
}

const registerFormConfig: FormConfig = {
  fields: [
    {
      name: 'email',
      label: 'Email',
      type: 'email',
      required: true,
      placeholder: 'your@email.com',
    },
    {
      name: 'full_name',
      label: 'Full Name',
      type: 'text',
      placeholder: 'Your full name',
    },
    {
      name: 'password',
      label: 'Password',
      type: 'password',
      required: true,
      placeholder: 'Create a password',
    },
    {
      name: 'confirmPassword',
      label: 'Confirm Password',
      type: 'password',
      required: true,
      placeholder: 'Confirm your password',
    },
    {
      name: 'tenant',
      label: 'Tenant',
      type: 'text',
      placeholder: 'Global',
      defaultValue: 'Global',
      helpText: 'Leave as "Global" for system-wide access',
    },
  ],
}

export default function Register() {
  const navigate = useNavigate()

  const registerMutation = useMutation({
    mutationFn: (data: { email: string; password: string; full_name?: string; tenant?: string }) =>
      api.portalRegister(data),
    onSuccess: () => {
      toast.success('Registration successful! Please login.')
      navigate('/login')
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.message || 'Registration failed')
    },
  })

  const handleSubmit = (data: RegisterFormData) => {
    if (data.password !== data.confirmPassword) {
      toast.error('Passwords do not match')
      return
    }

    registerMutation.mutate({
      email: data.email,
      password: data.password,
      full_name: data.full_name,
      tenant: data.tenant === 'Global' ? 'system' : data.tenant,
    })
  }

  // Custom FormBuilder wrapper to handle custom buttons
  const CustomFormBuilder = () => {
    const [values, setValues] = useState<RegisterFormData>({
      email: '',
      full_name: '',
      password: '',
      confirmPassword: '',
      tenant: 'Global',
    })

    const handleChange = (name: string, value: string) => {
      setValues(prev => ({ ...prev, [name]: value }))
    }

    const onSubmit = (e: React.FormEvent) => {
      e.preventDefault()
      // Process email to strip spaces
      const processedData = {
        ...values,
        email: values.email?.replace(/\s+/g, '') || '',
        full_name: values.full_name?.trim() || undefined,
        tenant: values.tenant?.trim() || 'Global',
      }
      handleSubmit(processedData)
    }

    return (
      <form onSubmit={onSubmit} className="space-y-4">
        {registerFormConfig.fields.map((field) => {
          const value = values[field.name]

          return (
            <div key={field.name}>
              <div className="space-y-1">
                <label className="text-sm font-medium text-yellow-500">{field.label}</label>
                <input
                  type={field.type === 'email' ? 'email' : field.type === 'password' ? 'password' : 'text'}
                  required={field.required}
                  value={value || ''}
                  onChange={(e) => handleChange(field.name, e.target.value)}
                  placeholder={field.placeholder}
                  autoComplete={
                    field.name === 'email' ? 'email' :
                    field.name === 'full_name' ? 'name' :
                    field.name === 'password' || field.name === 'confirmPassword' ? 'new-password' :
                    'off'
                  }
                  className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
                />
                {field.helpText && (
                  <p className="text-xs text-gray-500">{field.helpText}</p>
                )}
              </div>
            </div>
          )
        })}

        <div className="flex flex-col gap-3 pt-4">
          <Button
            type="submit"
            className="w-full bg-gradient-to-r from-yellow-600 to-amber-600 hover:from-yellow-500 hover:to-amber-500 text-gray-900 font-semibold"
            isLoading={registerMutation.isPending}
          >
            Create Account
          </Button>
          <Button
            type="button"
            variant="ghost"
            className="w-full text-gray-400 hover:text-gray-300 hover:bg-gray-800/50"
            onClick={() => navigate('/login')}
          >
            Back to Login
          </Button>
        </div>
      </form>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-950 via-blue-950/20 to-gray-950 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <img
            src="/elder-logo.png"
            alt="Elder Logo"
            className="w-32 h-32 mx-auto mb-4 drop-shadow-2xl"
          />
          <h1 className="text-4xl font-bold bg-gradient-to-r from-yellow-500 to-amber-400 bg-clip-text text-transparent mb-2">Elder</h1>
          <p className="text-gray-500">Entity Relationship Tracking System</p>
        </div>

        <Card className="bg-gray-900/50 border-gray-800/50 backdrop-blur-sm">
          <CardHeader>
            <h2 className="text-xl font-semibold text-gray-300">Create Account</h2>
          </CardHeader>
          <CardContent>
            <CustomFormBuilder />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
