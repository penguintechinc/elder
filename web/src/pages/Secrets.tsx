import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Key, TestTube, Trash2, Eye, EyeOff } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
// ModalFormBuilder not currently used
import { FormConfig } from '@/types/form'

interface ProviderFormData {
  provider?: string
  [key: string]: unknown
}

const PROVIDER_TYPES = [
  { value: 'aws_secrets_manager', label: 'AWS Secrets Manager' },
  { value: 'gcp_secret_manager', label: 'GCP Secret Manager' },
  { value: 'infisical', label: 'Infisical' },
]

export default function Secrets() {
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [selectedProvider, setSelectedProvider] = useState<number | null>(null)
  const [showSecrets, setShowSecrets] = useState(false)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['secretProviders'],
    queryFn: () => api.getSecretProviders(),
  })

  const testMutation = useMutation({
    mutationFn: (id: number) => api.testSecretProvider(id),
    onSuccess: (result) => {
      if (result.success) {
        toast.success('Provider connection successful')
      } else {
        toast.error(`Connection failed: ${result.message}`)
      }
    },
    onError: () => {
      toast.error('Failed to test provider')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteSecretProvider(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['secretProviders'],
        refetchType: 'all'
      })
      toast.success('Provider deleted')
    },
    onError: () => {
      toast.error('Failed to delete provider')
    },
  })

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Secrets Management</h1>
          <p className="mt-2 text-slate-400">Manage secret providers and access secrets securely</p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Add Provider
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.providers?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <Key className="w-12 h-12 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No secret providers configured</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Add your first provider
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {data?.providers?.map((provider) => (
            <Card key={provider.id}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Key className="w-5 h-5 text-primary-400" />
                    <div>
                      <h3 className="text-lg font-semibold text-white">{provider.name}</h3>
                      <p className="text-sm text-slate-400">
                        {PROVIDER_TYPES.find(t => t.value === provider.provider_type)?.label || provider.provider_type}
                      </p>
                    </div>
                  </div>
                  <span className={`px-2 py-1 text-xs font-medium rounded ${
                    provider.enabled ? 'bg-green-500/20 text-green-400' : 'bg-slate-500/20 text-slate-400'
                  }`}>
                    {provider.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
              </CardHeader>
              <CardContent>
                {provider.description && (
                  <p className="text-sm text-slate-400 mb-4">{provider.description}</p>
                )}
                <div className="flex gap-2 flex-wrap">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setSelectedProvider(provider.id)
                      setShowSecrets(true)
                    }}
                  >
                    <Eye className="w-4 h-4 mr-2" />
                    View Secrets
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => testMutation.mutate(provider.id)}
                    isLoading={testMutation.isPending}
                  >
                    <TestTube className="w-4 h-4 mr-2" />
                    Test
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => deleteMutation.mutate(provider.id)}
                    isLoading={deleteMutation.isPending}
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {showCreateModal && (
        <CreateSecretProviderModal
          onClose={() => setShowCreateModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ['secretProviders'],
              refetchType: 'all'
            })
            setShowCreateModal(false)
          }}
        />
      )}

      {showSecrets && selectedProvider && (
        <SecretsListModal
          providerId={selectedProvider}
          onClose={() => {
            setShowSecrets(false)
            setSelectedProvider(null)
          }}
        />
      )}
    </div>
  )
}

interface CreateSecretProviderModalProps {
  onClose: () => void
  onSuccess: () => Promise<void>
}

function CreateSecretProviderModal({ onClose, onSuccess }: CreateSecretProviderModalProps) {
  const [formValues, setFormValues] = useState<ProviderFormData>({
    provider: 'aws_secrets_manager',
  })
  const [showModal, setShowModal] = useState(true)

  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations(),
  })

  const createMutation = useMutation({
    mutationFn: (data: ProviderFormData) => {
      const providerData: Parameters<typeof api.createSecretProvider>[0] = {
        name: String(data.name || ''),
        provider_type: String(data.provider || ''),
        organization_id: typeof data.organization_id === 'string' ? parseInt(data.organization_id, 10) : (data.organization_id as number),
        config: typeof data.config_json === 'string' ? JSON.parse(data.config_json) : (data.config as Record<string, unknown>),
        description: data.description ? String(data.description) : undefined,
      }
      return api.createSecretProvider(providerData)
    },
    onSuccess: () => {
      toast.success('Provider created successfully')
      setShowModal(false)
      onSuccess()
    },
    onError: () => {
      toast.error('Failed to create provider')
    },
  })

  // Get dynamic help text based on provider type
  const getConfigHelpText = (providerType: string): string => {
    switch (providerType) {
      case 'aws_secrets_manager':
        return 'AWS: region, access_key_id, secret_access_key'
      case 'gcp_secret_manager':
        return 'GCP: project_id, credentials (JSON key)'
      case 'infisical':
        return 'Infisical: site_url, client_id, client_secret'
      case 'hashicorp_vault':
        return 'HashiCorp Vault: address, token, mount_path'
      default:
        return 'Enter configuration as valid JSON'
    }
  }

  const formConfig: FormConfig = {
    fields: [
      {
        name: 'name',
        label: 'Name',
        type: 'text',
        required: true,
        placeholder: 'Production Secrets',
      },
      {
        name: 'provider',
        label: 'Provider Type',
        type: 'select',
        required: true,
        options: [
          { value: 'aws_secrets_manager', label: 'AWS Secrets Manager' },
          { value: 'gcp_secret_manager', label: 'GCP Secret Manager' },
          { value: 'infisical', label: 'Infisical' },
          { value: 'hashicorp_vault', label: 'HashiCorp Vault' },
        ],
        defaultValue: 'aws_secrets_manager',
      },
      {
        name: 'organization_id',
        label: 'Organization',
        type: 'select',
        required: true,
        options: [
          { value: '', label: 'Select organization' },
          ...(orgs?.items || []).map((o) => ({
            value: String(o.id),
            label: o.name,
          })),
        ],
      },
      {
        name: 'config_json',
        label: 'Configuration (JSON)',
        type: 'textarea',
        required: true,
        rows: 8,
        placeholder: '{\n  "region": "us-east-1",\n  "access_key_id": "...",\n  "secret_access_key": "..."\n}',
        helpText: getConfigHelpText(formValues.provider || 'aws_secrets_manager'),
        validate: (value: string): string | undefined => {
          if (!value) return undefined
          try {
            JSON.parse(value)
            return undefined
          } catch {
            return 'Configuration must be valid JSON'
          }
        },
      },
    ],
    submitLabel: 'Create Provider',
    cancelLabel: 'Cancel',
  }

  const handleModalClose = () => {
    setShowModal(false)
    onClose()
  }

  const handleSubmit = (data: ProviderFormData) => {
    try {
      const configJson = data.config_json as string | undefined
      if (!configJson) {
        toast.error('Configuration JSON is required')
        return
      }
      const parsedConfig = JSON.parse(configJson)
      createMutation.mutate({
        name: data.name,
        provider_type: data.provider,
        organization_id: parseInt(data.organization_id as string),
        config: parsedConfig,
      })
    } catch {
      toast.error('Invalid JSON configuration')
    }
  }

  return (
    <SecretProviderFormModal
      isOpen={showModal}
      onClose={handleModalClose}
      config={formConfig}
      onSubmit={handleSubmit}
      isLoading={createMutation.isPending}
      onValuesChange={setFormValues}
    />
  )
}

interface SecretProviderFormModalProps {
  isOpen: boolean
  onClose: () => void
  config: FormConfig
  onSubmit: (data: ProviderFormData) => void
  isLoading: boolean
  onValuesChange: (values: ProviderFormData) => void
}

// Wrapper component that handles dynamic help text by re-rendering the config
function SecretProviderFormModal({ isOpen, onClose, config, onSubmit, isLoading, onValuesChange }: SecretProviderFormModalProps) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Add Secret Provider</h2>
        </CardHeader>
        <CardContent>
          <ModalFormBuilderWithDynamicHelp
            config={config as FormConfig & { fields: FormField[] }}
            onSubmit={onSubmit}
            onCancel={onClose}
            isLoading={isLoading}
            onValuesChange={onValuesChange}
          />
        </CardContent>
      </Card>
    </div>
  )
}

interface FormField {
  name: string
  label: string
  type: string
  required?: boolean
  placeholder?: string
  defaultValue?: string
  helpText?: string
  rows?: number
  options?: Array<{ value: string; label: string }>
  validate?: (value: string) => string | undefined
}

interface ModalFormBuilderProps {
  config: FormConfig & { fields: FormField[] }
  onSubmit: (data: ProviderFormData) => void
  onCancel: () => void
  isLoading: boolean
  onValuesChange: (values: ProviderFormData) => void
}

// FormBuilder wrapper that updates config based on form values
function ModalFormBuilderWithDynamicHelp({ config, onSubmit, onCancel, isLoading, onValuesChange }: ModalFormBuilderProps) {
  const [values, setValues] = useState<ProviderFormData>(() => {
    const defaults: ProviderFormData = {}
    config.fields.forEach((f) => {
      defaults[f.name] = f.defaultValue || ''
    })
    return defaults
  })
  const [errors, setErrors] = useState<Record<string, string>>({})

  const handleChange = (name: string, value: string) => {
    const newValues = { ...values, [name]: value }
    setValues(newValues)
    onValuesChange(newValues)
    if (errors[name]) delete errors[name]
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const newErrors: Record<string, string> = {}

    config.fields.forEach((field) => {
      if (field.required && !values[field.name]) {
        newErrors[field.name] = `${field.label} is required`
      }
      if (field.validate && values[field.name]) {
        const error = field.validate(values[field.name] as string)
        if (error) newErrors[field.name] = error
      }
    })

    if (Object.keys(newErrors).length > 0) {
      setErrors(newErrors)
      return
    }

    onSubmit(values)
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {config.fields.map((field) => {
        const value = values[field.name]
        const error = errors[field.name]
        const helpText = field.type === 'textarea' && field.helpText ? field.helpText : undefined

        if (field.type === 'select') {
          return (
            <div key={field.name}>
              <Select
                label={field.label}
                required={field.required}
                value={String(value || '')}
                onChange={(e) => handleChange(field.name, e.target.value)}
                options={field.options}
              />
              {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
            </div>
          )
        }

        if (field.type === 'textarea') {
          return (
            <div key={field.name}>
              <label className="text-sm font-medium text-yellow-500">{field.label}</label>
              <textarea
                required={field.required}
                value={String(value || '')}
                onChange={(e) => handleChange(field.name, e.target.value)}
                placeholder={field.placeholder}
                rows={field.rows || 4}
                className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500 mt-1"
              />
              {helpText && <p className="text-xs text-slate-500 mt-1">{helpText}</p>}
              {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
            </div>
          )
        }

        return (
          <div key={field.name}>
            <Input
              label={field.label}
              required={field.required}
              value={String(value || '')}
              onChange={(e) => handleChange(field.name, e.target.value)}
              placeholder={field.placeholder}
            />
            {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
          </div>
        )
      })}

      <div className="flex gap-3 pt-4">
        <button
          type="button"
          onClick={onCancel}
          className="flex-1 px-4 py-2 text-slate-300 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={isLoading}
          className="flex-1 px-4 py-2 bg-primary-600 hover:bg-primary-700 text-white rounded-lg transition-colors disabled:opacity-50"
        >
          {isLoading ? 'Saving...' : 'Create Provider'}
        </button>
      </div>
    </form>
  )
}

interface SecretsListModalProps {
  providerId: number
  onClose: () => void
}

function SecretsListModal({ providerId, onClose }: SecretsListModalProps) {
  const [showValues, setShowValues] = useState<Record<string, boolean>>({})

  const { data, isLoading } = useQuery({
    queryKey: ['secrets', providerId],
    queryFn: () => api.listSecrets(providerId),
  })

  const getSecretMutation = useMutation({
    mutationFn: ({ secretName }: { secretName: string }) =>
      api.getSecret(providerId, secretName),
  })

  const toggleSecret = async (secretName: string) => {
    if (showValues[secretName]) {
      setShowValues(prev => ({ ...prev, [secretName]: false }))
    } else {
      try {
        await getSecretMutation.mutateAsync({ secretName })
        setShowValues(prev => ({ ...prev, [secretName]: true }))
      } catch {
        toast.error('Failed to fetch secret value')
      }
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-3xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">Secrets</h2>
            <Button variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : data?.secrets?.length === 0 ? (
            <p className="text-center text-slate-400 py-8">No secrets found</p>
          ) : (
            <div className="space-y-2">
              {data?.secrets?.map((secret) => (
                <div
                  key={secret.name}
                  className="flex items-center justify-between p-3 bg-slate-800 rounded-lg"
                >
                  <div className="flex-1">
                    <p className="text-white font-medium">{secret.name}</p>
                    {showValues[secret.name] && getSecretMutation.data && (
                      <p className="text-sm text-slate-400 mt-1 font-mono break-all">
                        {getSecretMutation.data.value}
                      </p>
                    )}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => toggleSecret(secret.name)}
                  >
                    {showValues[secret.name] ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
