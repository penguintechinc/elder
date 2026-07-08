import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Edit, Trash2, Clock } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { invalidateCache } from '@/lib/invalidateCache'
import { confirmDelete } from '@/lib/confirmActions'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
// ModalFormBuilder and FormConfig not currently used
import CreateOnCallRotationModal from '@/components/CreateOnCallRotationModal'
import OnCallRotationDetailModal from '@/components/OnCallRotationDetailModal'
import { Organization } from '@/types'

interface OnCallRotation {
  id: number
  name: string
  description?: string
  schedule_type: string
  scope_type: string
  is_active: boolean
  organization_id?: number
  service_id?: number
}

interface Service {
  id: number
  name: string
}

interface PaginatedResponse<T> {
  items: T[]
}

const SCHEDULE_TYPES = [
  { value: 'weekly', label: 'Weekly Rotation' },
  { value: 'cron', label: 'Custom Schedule (Cron)' },
  { value: 'manual', label: 'Manual Assignment' },
  { value: 'follow_the_sun', label: 'Follow-the-Sun (24/7)' },
]

const SCOPE_TYPES = [
  { value: 'organization', label: 'Organization-level' },
  { value: 'service', label: 'Service-level' },
]

const STATUS_OPTIONS = [
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
]

export default function OnCallRotations() {
  const [search, setSearch] = useState('')
  const [organizationFilter, setOrganizationFilter] = useState<string>('')
  const [serviceFilter, setServiceFilter] = useState<string>('')
  const [scheduleTypeFilter, setScheduleTypeFilter] = useState<string>('')
  const [scopeTypeFilter, setScopeTypeFilter] = useState<string>('')
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingRotation, setEditingRotation] = useState<OnCallRotation | null>(null)
  const [viewingRotation, setViewingRotation] = useState<OnCallRotation | null>(null)
  const queryClient = useQueryClient()

  const { data: organizations } = useQuery({
    queryKey: queryKeys.organizations.dropdown,
    queryFn: () => api.getOrganizations({ per_page: 1000 }) as Promise<PaginatedResponse<Organization>>,
  })

  const { data: services } = useQuery({
    queryKey: queryKeys.services.all,
    queryFn: () => api.getServices({ per_page: 1000 }) as Promise<PaginatedResponse<Service>>,
  })

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.onCall.list({
      search,
      organization_id: organizationFilter,
      service_id: serviceFilter,
      schedule_type: scheduleTypeFilter,
      scope_type: scopeTypeFilter,
      status: statusFilter,
    }),
    queryFn: () => api.getOnCallRotations({
      search: search || undefined,
      organization_id: organizationFilter ? parseInt(organizationFilter) : undefined,
      service_id: serviceFilter ? parseInt(serviceFilter) : undefined,
      schedule_type: scheduleTypeFilter || undefined,
      scope_type: scopeTypeFilter || undefined,
      status: statusFilter || undefined,
    }),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteOnCallRotation(id),
    onSuccess: async () => {
      await invalidateCache.onCall(queryClient)
      toast.success('Rotation deleted successfully')
    },
    onError: () => {
      toast.error('Failed to delete rotation')
    },
  })

  const handleDelete = (id: number, name: string) => {
    confirmDelete(`rotation "${name}"`, () => {
      deleteMutation.mutate(id)
    })
  }

  const getScopeLabel = (rotation: OnCallRotation) => {
    if (rotation.scope_type === 'organization' && rotation.organization_id) {
      const org = organizations?.items?.find((o: Organization) => o.id === rotation.organization_id)
      return org?.name || 'Unknown Org'
    }
    if (rotation.scope_type === 'service' && rotation.service_id) {
      const svc = services?.items?.find((s: Service) => s.id === rotation.service_id)
      return svc?.name || 'Unknown Service'
    }
    return 'Unknown'
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">On-Call Rotations</h1>
          <p className="mt-2 text-slate-400">
            Manage on-call schedules and escalation policies
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Rotation
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4 mb-6">
        <div className="flex-1 min-w-[200px] max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search rotations..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <Select
          value={scopeTypeFilter}
          onChange={(e) => setScopeTypeFilter(e.target.value)}
          className="w-40"
        >
          <option value="">All Scopes</option>
          {SCOPE_TYPES.map((scope) => (
            <option key={scope.value} value={scope.value}>
              {scope.label}
            </option>
          ))}
        </Select>
        <Select
          value={organizationFilter}
          onChange={(e) => setOrganizationFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Organizations</option>
          {organizations?.items?.map((org: Organization) => (
            <option key={org.id} value={org.id}>
              {org.name}
            </option>
          ))}
        </Select>
        <Select
          value={serviceFilter}
          onChange={(e) => setServiceFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Services</option>
          {services?.items?.map((svc: Service) => (
            <option key={svc.id} value={svc.id}>
              {svc.name}
            </option>
          ))}
        </Select>
        <Select
          value={scheduleTypeFilter}
          onChange={(e) => setScheduleTypeFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Schedules</option>
          {SCHEDULE_TYPES.map((type) => (
            <option key={type.value} value={type.value}>
              {type.label}
            </option>
          ))}
        </Select>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="w-36"
        >
          <option value="">All Statuses</option>
          {STATUS_OPTIONS.map((status) => (
            <option key={status.value} value={status.value}>
              {status.label}
            </option>
          ))}
        </Select>
      </div>

      {/* Rotations List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <Clock className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No on-call rotations found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first rotation
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data?.items?.map((rotation: OnCallRotation) => (
            <Card
              key={rotation.id}
              className="cursor-pointer hover:border-primary-500/50 transition-colors"
              onClick={() => setViewingRotation(rotation)}
            >
              <CardContent className="pt-4">
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <Clock className="w-5 h-5 text-primary-400 flex-shrink-0" />
                    <h3 className="text-lg font-semibold text-white truncate">
                      {rotation.name}
                    </h3>
                  </div>
                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setEditingRotation(rotation)
                      }}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDelete(rotation.id, rotation.name)
                      }}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {rotation.description && (
                  <p className="text-sm text-slate-400 mb-3 line-clamp-2">
                    {rotation.description}
                  </p>
                )}

                <div className="flex flex-wrap gap-2 mb-3">
                  <span className="text-xs px-2 py-1 rounded bg-primary-500/20 text-primary-400">
                    {rotation.schedule_type}
                  </span>
                  <span className="text-xs px-2 py-1 rounded bg-slate-700 text-slate-300">
                    {rotation.scope_type}
                  </span>
                  {rotation.is_active ? (
                    <span className="text-xs px-2 py-1 rounded bg-green-500/20 text-green-400">
                      Active
                    </span>
                  ) : (
                    <span className="text-xs px-2 py-1 rounded bg-slate-500/20 text-slate-400">
                      Inactive
                    </span>
                  )}
                </div>

                <div className="text-sm text-slate-500">
                  <span className="font-medium">{getScopeLabel(rotation)}</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      <CreateOnCallRotationModal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onSuccess={async () => {
          await invalidateCache.onCall(queryClient)
          setShowCreateModal(false)
          toast.success('Rotation created successfully')
        }}
      />

      {/* Edit Modal */}
      {editingRotation && (
        <CreateOnCallRotationModal
          isOpen={!!editingRotation}
          onClose={() => setEditingRotation(null)}
          rotation={editingRotation}
          onSuccess={async () => {
            await invalidateCache.onCall(queryClient)
            setEditingRotation(null)
            toast.success('Rotation updated successfully')
          }}
        />
      )}

      {/* Detail Modal */}
      {viewingRotation && (
        <OnCallRotationDetailModal
          rotation={viewingRotation}
          onClose={() => setViewingRotation(null)}
          onEdit={() => {
            setEditingRotation(viewingRotation)
            setViewingRotation(null)
          }}
        />
      )}
    </div>
  )
}
