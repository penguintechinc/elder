import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Shield, Key, Plus, Trash2, Edit, Copy } from 'lucide-react'
import api from '@/lib/api'
import Button from '@/components/Button'
import Input from '@/components/Input'
import Card, { CardHeader, CardContent } from '@/components/Card'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'
import type { IdPConfiguration } from '@/types'

const idpFields: FormField[] = [
  {
    name: 'name',
    label: 'Name',
    type: 'text',
    required: true,
    placeholder: 'Okta, Azure AD, etc.',
  },
  {
    name: 'idp_type',
    label: 'Type',
    type: 'select',
    required: true,
    options: [
      { value: 'saml', label: 'SAML 2.0' },
      { value: 'oidc', label: 'OpenID Connect' },
    ],
    defaultValue: 'saml',
  },
  {
    name: 'entity_id',
    label: 'Entity ID',
    type: 'text',
    placeholder: 'https://idp.example.com/...',
  },
  {
    name: 'metadata_url',
    label: 'Metadata URL',
    type: 'url',
    placeholder: 'https://idp.example.com/metadata',
  },
  {
    name: 'sso_url',
    label: 'SSO URL',
    type: 'url',
    placeholder: 'https://idp.example.com/sso',
  },
  {
    name: 'slo_url',
    label: 'SLO URL',
    type: 'url',
    placeholder: 'https://idp.example.com/slo',
  },
  {
    name: 'certificate',
    label: 'Certificate',
    type: 'textarea',
    placeholder: 'Paste certificate here...',
    rows: 4,
  },
  {
    name: 'default_role',
    label: 'Default Role',
    type: 'select',
    required: true,
    options: [
      { value: 'reader', label: 'Reader' },
      { value: 'editor', label: 'Editor' },
      { value: 'admin', label: 'Admin' },
    ],
    defaultValue: 'reader',
  },
  {
    name: 'jit_provisioning_enabled',
    label: 'Enable JIT (Just-in-Time) Provisioning',
    type: 'checkbox',
    defaultValue: true,
  },
]

export default function SSOConfiguration() {
  const [activeTab, setActiveTab] = useState<'idp' | 'scim'>('idp')
  const [showCreateIdP, setShowCreateIdP] = useState(false)
  const [editingIdP, setEditingIdP] = useState<IdPConfiguration | null>(null)
  const queryClient = useQueryClient()

  const { data: idpConfigs, isLoading: idpLoading } = useQuery({
    queryKey: ['idp-configs'],
    queryFn: () => api.getIdPConfigs(),
  })

  const createIdPMutation = useMutation({
    mutationFn: (data: Record<string, any>) => api.createIdPConfig(data as Parameters<typeof api.createIdPConfig>[0]),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['idp-configs'] })
      toast.success('IdP configuration created')
      setShowCreateIdP(false)
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to create IdP config')
    },
  })

  const updateIdPMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Record<string, any> }) =>
      api.updateIdPConfig(id, data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['idp-configs'] })
      toast.success('IdP configuration updated')
      setEditingIdP(null)
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to update IdP config')
    },
  })

  const deleteIdPMutation = useMutation({
    mutationFn: (id: number) => api.deleteIdPConfig(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['idp-configs'] })
      toast.success('IdP configuration deleted')
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to delete IdP config')
    },
  })

  const handleEditIdP = (config: IdPConfiguration) => {
    setEditingIdP(config)
  }

  const handleCreateIdP = (data: Record<string, any>) => {
    createIdPMutation.mutate(data)
  }

  const handleUpdateIdP = (data: Record<string, any>) => {
    if (editingIdP) {
      updateIdPMutation.mutate({
        id: editingIdP.id,
        data,
      })
    }
  }

  const handleDeleteIdP = (config: IdPConfiguration) => {
    if (confirm(`Delete IdP configuration "${config.name}"?`)) {
      deleteIdPMutation.mutate(config.id)
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    toast.success('Copied to clipboard')
  }

  // Create edit fields with default values from the editing IdP
  const editIdpFields = useMemo((): FormField[] => {
    if (!editingIdP) return idpFields
    return idpFields.map((field) => {
      const value = editingIdP[field.name as keyof IdPConfiguration]
      return {
        ...field,
        defaultValue: value !== undefined && value !== null ? value : field.defaultValue,
      }
    })
  }, [editingIdP])

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-2">SSO Configuration</h1>
        <p className="text-slate-400">Configure SAML/OIDC identity providers and SCIM provisioning</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 mb-6">
        <Button
          variant={activeTab === 'idp' ? 'primary' : 'ghost'}
          onClick={() => setActiveTab('idp')}
        >
          <Shield className="w-4 h-4 mr-2" />
          Identity Providers
        </Button>
        <Button
          variant={activeTab === 'scim' ? 'primary' : 'ghost'}
          onClick={() => setActiveTab('scim')}
        >
          <Key className="w-4 h-4 mr-2" />
          SCIM Provisioning
        </Button>
      </div>

      {/* IdP Tab */}
      {activeTab === 'idp' && (
        <div>
          <div className="flex justify-end mb-4">
            <Button onClick={() => setShowCreateIdP(true)}>
              <Plus className="w-4 h-4 mr-2" />
              Add IdP Configuration
            </Button>
          </div>

          {idpLoading ? (
            <div className="text-center py-8 text-slate-400">Loading configurations...</div>
          ) : !idpConfigs || idpConfigs.length === 0 ? (
            <Card>
              <CardContent className="text-center py-8">
                <Shield className="w-12 h-12 text-slate-500 mx-auto mb-4" />
                <p className="text-slate-400">No identity providers configured</p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {idpConfigs.map((config: IdPConfiguration) => (
                <Card key={config.id}>
                  <CardContent className="p-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <h3 className="font-semibold text-white">{config.name}</h3>
                          <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-500/20 text-blue-400">
                            {config.idp_type.toUpperCase()}
                          </span>
                        </div>
                        <p className="text-sm text-slate-400 mt-1">
                          Entity ID: {config.entity_id || 'Not set'} •
                          JIT: {config.jit_provisioning_enabled ? 'Enabled' : 'Disabled'} •
                          Default Role: {config.default_role}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Button variant="ghost" size="sm" onClick={() => handleEditIdP(config)}>
                          <Edit className="w-4 h-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDeleteIdP(config)}
                          className="text-red-400 hover:text-red-300"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      {/* SCIM Tab */}
      {activeTab === 'scim' && (
        <Card>
          <CardHeader>
            <h2 className="text-lg font-semibold text-white">SCIM 2.0 Provisioning</h2>
          </CardHeader>
          <CardContent>
            <p className="text-slate-400 mb-4">
              SCIM (System for Cross-domain Identity Management) enables automatic user provisioning
              from your identity provider.
            </p>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">
                  SCIM Endpoint URL
                </label>
                <div className="flex gap-2">
                  <Input
                    value={`${window.location.origin}/api/v1/scim`}
                    readOnly
                    className="flex-1"
                  />
                  <Button
                    variant="ghost"
                    onClick={() => copyToClipboard(`${window.location.origin}/api/v1/scim`)}
                  >
                    <Copy className="w-4 h-4" />
                  </Button>
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1">
                  Bearer Token
                </label>
                <p className="text-sm text-slate-400">
                  Configure SCIM in Tenant Settings to generate a bearer token for your IdP.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Create IdP Modal */}
      {showCreateIdP && (
        <FormModalBuilder
          isOpen={showCreateIdP}
          onClose={() => setShowCreateIdP(false)}
          title="Add IdP Configuration"
          fields={idpFields}
          onSubmit={handleCreateIdP}
          submitButtonText="Save"
        />
      )}

      {/* Edit IdP Modal */}
      {editingIdP && (
        <FormModalBuilder
          isOpen={true}
          onClose={() => setEditingIdP(null)}
          title="Edit IdP Configuration"
          fields={editIdpFields}
          onSubmit={handleUpdateIdP}
          submitButtonText="Save"
        />
      )}
    </div>
  )
}
