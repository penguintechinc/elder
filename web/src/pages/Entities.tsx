import { useState, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Plus, Search } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { invalidateCache } from '@/lib/invalidateCache'
import { getTypeColor } from '@/lib/colorHelpers'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import VillageIdBadge from '@/components/VillageIdBadge'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

export default function Entities() {
  const [search, setSearch] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const initialOrgId = searchParams.get('organization_id')

  // Auto-open create modal if organization_id is in query params
  useEffect(() => {
    if (initialOrgId) {
      setShowCreateModal(true)
    }
  }, [initialOrgId])

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.entities.list({ search }),
    queryFn: () => api.getEntities({ search }),
  })

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Entities</h1>
          <p className="mt-2 text-slate-400">Manage infrastructure and resources</p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Entity
        </Button>
      </div>

      <div className="mb-6">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search entities..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">No entities found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first entity
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {data?.items?.map((entity: any) => (
            <Card
              key={entity.id}
              className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all"
              onClick={() => navigate(`/entities/${entity.id}`)}
            >
              <CardContent>
                <h3 className="text-lg font-semibold text-white mb-2">{entity.name}</h3>
                <div className="flex gap-2 flex-wrap mb-3">
                  <span className={`inline-block px-2 py-1 text-xs font-medium rounded ${getTypeColor(entity.entity_type)}`}>
                    {entity.entity_type.replace('_', ' ').toUpperCase()}
                  </span>
                  {entity.entity_sub_type && (
                    <span className={`inline-block px-2 py-1 text-xs font-medium rounded ${getTypeColor(entity.entity_sub_type)}`}>
                      {entity.entity_sub_type.replace('_', ' ').toUpperCase()}
                    </span>
                  )}
                </div>
                {entity.description && (
                  <p className="text-sm text-slate-400 mt-3">{entity.description}</p>
                )}
                <div className="flex items-center justify-between text-xs text-slate-500 mt-4">
                  <span>ID: {entity.id}</span>
                  {entity.village_id && (
                    <VillageIdBadge villageId={entity.village_id} />
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {showCreateModal && (
        <CreateEntityModal
          initialOrganizationId={initialOrgId}
          onClose={() => {
            setShowCreateModal(false)
            // Clear query params when closing modal
            if (initialOrgId) {
              navigate('/entities', { replace: true })
            }
          }}
          onSuccess={async () => {
            await invalidateCache.entities(queryClient)
            setShowCreateModal(false)
            // Clear query params on success
            if (initialOrgId) {
              navigate('/entities', { replace: true })
            }
          }}
        />
      )}
    </div>
  )
}

function CreateEntityModal({ initialOrganizationId, onClose, onSuccess }: any) {
  // Fetch entity types from API
  const { data: entityTypesData } = useQuery({
    queryKey: ['entityTypes'],
    queryFn: () => api.getEntityTypes(),
  })

  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations(),
  })

  // Get category options from entity_types array
  const categoryOptions = entityTypesData?.entity_types?.map((et: any) => ({
    value: et.type,
    label: et.type.charAt(0).toUpperCase() + et.type.slice(1).replace('_', ' ')
  })) || []

  const createMutation = useMutation({
    mutationFn: (data: any) => api.createEntity(data),
    onSuccess: () => {
      toast.success('Entity created successfully')
      onSuccess()
    },
    onError: () => {
      toast.error('Failed to create entity')
    },
  })

  const fields: FormField[] = useMemo(() => [
    {
      name: 'name',
      label: 'Name',
      type: 'text',
      required: true,
    },
    {
      name: 'entity_type',
      label: 'Category',
      type: 'select',
      required: true,
      options: categoryOptions,
    },
    {
      name: 'entity_sub_type',
      label: 'Sub-Type',
      type: 'select',
      // Options are dynamically computed based on selected category
      // Since FormBuilder doesn't support dynamic options, we include all possible options
      // and use showWhen for visibility
      options: [
        // Flatten all subtypes from all categories
        ...(entityTypesData?.entity_types?.flatMap((et: any) =>
          (et.subtypes || []).map((subtype: string) => ({
            value: subtype,
            label: subtype.replace('_', ' ').split(' ').map((w: string) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
          }))
        ) || [])
      ],
      showWhen: (values) => {
        const category = values.entity_type
        const categoryData = entityTypesData?.entity_types?.find((et: any) => et.type === category)
        return categoryData?.subtypes?.length > 0
      },
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      defaultValue: initialOrganizationId || '',
      options: (orgs?.items || []).map((o: any) => ({
        value: o.id,
        label: o.name,
      })),
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      rows: 3,
    },
  ], [categoryOptions, orgs, entityTypesData, initialOrganizationId])

  const handleSubmit = (data: Record<string, any>) => {
    createMutation.mutate({
      name: data.name,
      description: data.description || undefined,
      entity_type: data.entity_type,
      sub_type: data.entity_sub_type || undefined,
      organization_id: parseInt(data.organization_id),
    })
  }

  return (
    <FormModalBuilder
      isOpen={true}
      onClose={onClose}
      title="Create Entity"
      fields={fields}
      onSubmit={handleSubmit}
      submitButtonText="Create"
    />
  )
}
