import { useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

const IDENTITY_TYPES = [
  { value: 'employee', label: 'Employee' },
  { value: 'vendor', label: 'Vendor' },
  { value: 'bot', label: 'Bot' },
  { value: 'serviceAccount', label: 'Service Account' },
  { value: 'integration', label: 'Integration' },
  { value: 'otherHuman', label: 'Other Human' },
  { value: 'other', label: 'Other' },
]

interface CreateIdentityModalProps {
  isOpen: boolean
  onClose: () => void
  onSuccess?: () => void
  // Defaults for pre-populating form
  defaultTenantId?: number
  defaultOrganizationId?: number
  defaultIsPortalUser?: boolean
  defaultPortalRole?: string
  defaultIdentityType?: string
  defaultPermissionScope?: 'global' | 'tenant' | 'organization'
  // Query keys to invalidate on success
  invalidateQueryKeys?: (string | number)[][]
}

export default function CreateIdentityModal({
  isOpen,
  onClose,
  onSuccess,
  defaultTenantId,
  defaultOrganizationId,
  defaultIsPortalUser = false,
  defaultPortalRole = 'viewer',
  defaultIdentityType = 'employee',
  defaultPermissionScope = 'tenant',
  invalidateQueryKeys = [['identities']],
}: CreateIdentityModalProps) {
  const queryClient = useQueryClient()

  // Fetch organizations for dropdown
  const { data: organizations } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations(),
    enabled: isOpen,
  })

  // Create identity mutation
  const createMutation = useMutation({
    mutationFn: (data: any) => api.createIdentity(data),
    onSuccess: async () => {
      // Invalidate all specified query keys
      for (const queryKey of invalidateQueryKeys) {
        await queryClient.invalidateQueries({
          queryKey,
          refetchType: 'all'
        })
      }
      toast.success('Identity created successfully')
      onClose()
      onSuccess?.()
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to create identity')
    },
  })

  // Build organization options from fetched data
  const organizationOptions = useMemo(() => {
    if (!organizations?.items) return []
    return organizations.items.map((org: any) => ({
      value: org.id,
      label: org.name,
    }))
  }, [organizations])

  // Form fields configuration
  const fields: FormField[] = useMemo(() => [
    {
      name: 'username',
      label: 'Username',
      type: 'text',
      required: true,
      placeholder: 'Enter username',
      defaultValue: '',
    },
    {
      name: 'email',
      label: 'Email',
      type: 'email',
      required: true,
      placeholder: 'Enter email',
      defaultValue: '',
    },
    {
      name: 'full_name',
      label: 'Full Name',
      type: 'text',
      required: true,
      placeholder: 'Enter full name',
      defaultValue: '',
    },
    {
      name: 'identity_type',
      label: 'Identity Type',
      type: 'select',
      required: true,
      options: IDENTITY_TYPES,
      defaultValue: defaultIdentityType,
    },
    {
      name: 'auth_provider',
      label: 'Authentication Provider',
      type: 'select',
      required: true,
      options: [
        { value: 'local', label: 'Local App' },
        { value: 'ldap', label: 'LDAP' },
        { value: 'saml', label: 'SAML' },
        { value: 'oauth2', label: 'OAuth2' },
      ],
      defaultValue: 'local',
    },
    {
      name: 'password',
      label: 'Password',
      type: 'password_generate',
      required: true,
      placeholder: 'Enter or generate password',
      showWhen: (values) => values.auth_provider === 'local',
      defaultValue: '',
    },
    ...(defaultOrganizationId ? [] : [{
      name: 'organization_id',
      label: 'Organization',
      type: 'select' as const,
      required: true,
      options: [{ value: '', label: 'Select organization' }, ...organizationOptions],
      defaultValue: '',
    }]),
    {
      name: 'is_portal_user',
      label: 'Create as Portal User',
      type: 'checkbox',
      helpText: 'Portal users can log in to the Elder web interface',
      defaultValue: defaultIsPortalUser,
    },
    {
      name: 'portal_role',
      label: 'Portal Role',
      type: 'select',
      required: true,
      triggerField: 'is_portal_user',
      options: [
        { value: 'viewer', label: 'Viewer - Read-only access' },
        { value: 'editor', label: 'Editor - Can modify data' },
        { value: 'admin', label: 'Admin - Full access' },
      ],
      defaultValue: defaultPortalRole,
    },
    {
      name: 'permission_scope',
      label: 'Permission Scope',
      type: 'select',
      required: true,
      triggerField: 'is_portal_user',
      options: [
        { value: 'global', label: 'Global - System-wide access' },
        { value: 'tenant', label: 'Tenant - Access within this tenant' },
        { value: 'organization', label: 'Organization - Access within this organization' },
      ],
      defaultValue: defaultPermissionScope,
    },
    {
      name: 'must_change_password',
      label: 'Require password change on first login',
      type: 'checkbox',
      triggerField: 'is_portal_user',
      defaultValue: true,
    },
  ], [organizationOptions, defaultOrganizationId, defaultIdentityType, defaultIsPortalUser, defaultPortalRole, defaultPermissionScope])

  const handleSubmit = (data: Record<string, any>) => {
    const identityData: any = {
      username: data.username,
      email: data.email,
      full_name: data.full_name,
      identity_type: data.identity_type,
      auth_provider: data.auth_provider,
    }

    // Only include password for local auth provider
    if (data.auth_provider === 'local') {
      identityData.password = data.password
    }

    // Include tenant_id if provided
    if (defaultTenantId) {
      identityData.tenant_id = defaultTenantId
    }

    // Include organization and derive tenant_id from it
    const organizationId = data.organization_id || defaultOrganizationId
    if (organizationId) {
      identityData.organization_id = organizationId
      // Get tenant_id from the selected organization if not already set
      if (!defaultTenantId) {
        const selectedOrg = organizations?.items?.find((org: any) => org.id === organizationId)
        if (selectedOrg?.tenant_id) {
          identityData.tenant_id = selectedOrg.tenant_id
        }
      }
    }

    // Include portal user fields if enabled
    if (data.is_portal_user) {
      identityData.is_portal_user = true
      identityData.portal_role = data.portal_role
      identityData.permission_scope = data.permission_scope
    }

    createMutation.mutate(identityData)
  }

  return (
    <FormModalBuilder
      isOpen={isOpen}
      onClose={onClose}
      title="Create Identity"
      fields={fields}
      onSubmit={handleSubmit}
      submitButtonText="Create Identity"
    />
  )
}
