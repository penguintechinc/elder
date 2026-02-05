import { useMemo } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

interface CreateOnCallRotationModalProps {
  isOpen: boolean
  onClose: () => void
  rotation?: any
  onSuccess?: () => void
}

// Timezone options for follow-the-sun schedules
const TIMEZONE_OPTIONS = [
  { value: 'UTC', label: 'UTC' },
  { value: 'America/New_York', label: 'America/New_York (EST/EDT)' },
  { value: 'America/Chicago', label: 'America/Chicago (CST/CDT)' },
  { value: 'America/Denver', label: 'America/Denver (MST/MDT)' },
  { value: 'America/Los_Angeles', label: 'America/Los_Angeles (PST/PDT)' },
  { value: 'Europe/London', label: 'Europe/London (GMT/BST)' },
  { value: 'Europe/Paris', label: 'Europe/Paris (CET/CEST)' },
  { value: 'Europe/Berlin', label: 'Europe/Berlin (CET/CEST)' },
  { value: 'Asia/Tokyo', label: 'Asia/Tokyo (JST)' },
  { value: 'Asia/Shanghai', label: 'Asia/Shanghai (CST)' },
  { value: 'Asia/Singapore', label: 'Asia/Singapore (SGT)' },
  { value: 'Australia/Sydney', label: 'Australia/Sydney (AEDT/AEST)' },
  { value: 'Asia/Kolkata', label: 'Asia/Kolkata (IST)' },
]

export default function CreateOnCallRotationModal({
  isOpen,
  onClose,
  rotation,
  onSuccess,
}: CreateOnCallRotationModalProps) {
  // Fetch organizations and services
  const { data: organizations } = useQuery({
    queryKey: ['organizations-dropdown'],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const { data: services } = useQuery({
    queryKey: ['services-dropdown'],
    queryFn: () => api.getServices({ per_page: 1000 }),
  })

  // Create/update mutation
  const createMutation = useMutation({
    mutationFn: (data: any) => api.createOnCallRotation(data),
    onSuccess: () => {
      toast.success('Rotation created successfully')
      onSuccess?.()
    },
    onError: (error: any) => {
      const message = error?.response?.data?.message || 'Failed to create rotation'
      toast.error(message)
    },
  })

  const updateMutation = useMutation({
    mutationFn: (data: any) => api.updateOnCallRotation(rotation.id, data),
    onSuccess: () => {
      toast.success('Rotation updated successfully')
      onSuccess?.()
    },
    onError: (error: any) => {
      const message = error?.response?.data?.message || 'Failed to update rotation'
      toast.error(message)
    },
  })

  // Build organization and service options
  const organizationOptions = useMemo(
    () =>
      organizations?.items?.map((org: any) => ({
        value: org.id.toString(),
        label: org.name,
      })) || [],
    [organizations]
  )

  const serviceOptions = useMemo(
    () =>
      services?.items?.map((svc: any) => ({
        value: svc.id.toString(),
        label: svc.name,
      })) || [],
    [services]
  )

  // Form fields using shared FormModalBuilder format
  const fields: FormField[] = useMemo(() => [
    {
      name: 'name',
      label: 'Rotation Name',
      type: 'text' as const,
      required: true,
      placeholder: 'e.g., Backend API On-Call',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea' as const,
      placeholder: 'Optional description of this rotation',
      rows: 2,
    },
    {
      name: 'scope_type',
      label: 'Scope',
      type: 'radio' as const,
      required: true,
      defaultValue: 'organization',
      options: [
        { value: 'organization', label: 'Organization-level' },
        { value: 'service', label: 'Service-level' },
      ],
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select' as const,
      required: true,
      showWhen: (values) => values.scope_type === 'organization',
      options: organizationOptions.length > 0 ? organizationOptions : [{ value: '', label: 'No organizations found' }],
    },
    {
      name: 'service_id',
      label: 'Service',
      type: 'select' as const,
      required: true,
      showWhen: (values) => values.scope_type === 'service',
      options: serviceOptions.length > 0 ? serviceOptions : [{ value: '', label: 'No services found' }],
    },
    {
      name: 'schedule_type',
      label: 'Schedule Type',
      type: 'select' as const,
      required: true,
      defaultValue: 'weekly',
      options: [
        { value: 'weekly', label: 'Weekly Rotation' },
        { value: 'cron', label: 'Custom Schedule (Cron)' },
        { value: 'manual', label: 'Manual Assignment' },
        { value: 'follow_the_sun', label: 'Follow-the-Sun (24/7)' },
      ],
    },
    // Weekly fields
    {
      name: 'rotation_length_days',
      label: 'Rotation Length (days)',
      type: 'number' as const,
      required: true,
      defaultValue: 7,
      placeholder: '7',
      showWhen: (values) => values.schedule_type === 'weekly',
    },
    {
      name: 'rotation_start_date',
      label: 'Rotation Start Date',
      type: 'date' as const,
      required: true,
      showWhen: (values) => values.schedule_type === 'weekly',
    },
    // Cron field
    {
      name: 'schedule_cron',
      label: 'Cron Expression',
      type: 'text' as const,
      required: true,
      placeholder: '0 0 * * *',
      helpText: 'E.g., "0 0 * * *" for daily at midnight, "0 9 * * MON" for Mondays at 9 AM',
      showWhen: (values) => values.schedule_type === 'cron',
    },
    // Follow-the-sun fields
    {
      name: 'handoff_timezone',
      label: 'Handoff Timezone',
      type: 'select' as const,
      required: true,
      defaultValue: 'UTC',
      showWhen: (values) => values.schedule_type === 'follow_the_sun',
      options: TIMEZONE_OPTIONS,
    },
    {
      name: 'shift_split',
      label: 'Split into multiple shifts per day',
      type: 'checkbox' as const,
      defaultValue: false,
      showWhen: (values) => values.schedule_type === 'follow_the_sun',
    },
    // Status
    {
      name: 'is_active',
      label: 'Enable this rotation',
      type: 'checkbox' as const,
      defaultValue: true,
    },
  ], [organizationOptions, serviceOptions])

  // Get initial values for edit mode
  const initialValues = useMemo(() => {
    if (!rotation) return undefined
    return {
      name: rotation.name || '',
      description: rotation.description || '',
      scope_type: rotation.scope_type || 'organization',
      organization_id: rotation.organization_id?.toString() || '',
      service_id: rotation.service_id?.toString() || '',
      schedule_type: rotation.schedule_type || 'weekly',
      rotation_length_days: rotation.rotation_length_days?.toString() || '7',
      rotation_start_date: rotation.rotation_start_date || '',
      schedule_cron: rotation.schedule_cron || '',
      handoff_timezone: rotation.handoff_timezone || 'UTC',
      shift_split: rotation.shift_split || false,
      is_active: rotation.is_active !== false,
    }
  }, [rotation])

  const handleSubmit = (data: Record<string, any>) => {
    console.log('[CreateOnCallRotationModal] Form submitted with data:', data)

    // Clean up data based on schedule type
    const cleanedData: any = {
      name: data.name,
      description: data.description || undefined,
      scope_type: data.scope_type,
      schedule_type: data.schedule_type,
      is_active: data.is_active !== false,
    }

    // Add scope-specific fields
    if (data.scope_type === 'organization') {
      cleanedData.organization_id = parseInt(data.organization_id)
    } else if (data.scope_type === 'service') {
      cleanedData.service_id = parseInt(data.service_id)
    }

    // Add schedule-specific fields
    if (data.schedule_type === 'weekly') {
      cleanedData.rotation_length_days = parseInt(data.rotation_length_days) || 7
      cleanedData.rotation_start_date = data.rotation_start_date
    } else if (data.schedule_type === 'cron') {
      cleanedData.schedule_cron = data.schedule_cron
    } else if (data.schedule_type === 'follow_the_sun') {
      cleanedData.handoff_timezone = data.handoff_timezone || 'UTC'
      cleanedData.shift_split = data.shift_split !== false
      // Default shift config for follow-the-sun (can be customized later)
      cleanedData.shift_config = {
        shifts: [
          { name: 'Day Shift', start_hour: 8, end_hour: 20, timezone: data.handoff_timezone || 'UTC' }
        ]
      }
    }

    console.log('[CreateOnCallRotationModal] Cleaned data for API:', cleanedData)

    if (rotation) {
      updateMutation.mutate(cleanedData)
    } else {
      createMutation.mutate(cleanedData)
    }
  }

  if (!isOpen) return null

  return (
    <FormModalBuilder
      title={rotation ? 'Edit Rotation' : 'Create On-Call Rotation'}
      fields={fields}
      isOpen={isOpen}
      onClose={onClose}
      onSubmit={handleSubmit}
      submitButtonText={rotation ? 'Update' : 'Create'}
    />
  )
}
