import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Edit, Trash2, Database, AlertCircle } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { invalidateCache } from '@/lib/invalidateCache'
import { confirmDelete } from '@/lib/confirmActions'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

const DATA_CLASSIFICATIONS = [
  { value: 'public', label: 'Public', color: 'bg-green-500/20 text-green-400' },
  { value: 'internal', label: 'Internal', color: 'bg-yellow-500/20 text-yellow-400' },
  { value: 'confidential', label: 'Confidential', color: 'bg-orange-500/20 text-orange-400' },
  { value: 'restricted', label: 'Restricted', color: 'bg-red-500/20 text-red-400' },
]

const STORAGE_TYPES = [
  { value: 'database', label: 'Database' },
  { value: 'file_storage', label: 'File Storage' },
  { value: 'data_warehouse', label: 'Data Warehouse' },
  { value: 'data_lake', label: 'Data Lake' },
  { value: 'cache', label: 'Cache' },
  { value: 'message_queue', label: 'Message Queue' },
  { value: 'search_index', label: 'Search Index' },
  { value: 'time_series', label: 'Time Series' },
  { value: 'blob_storage', label: 'Blob Storage' },
  { value: 'other', label: 'Other' },
]

const COMPLIANCE_FRAMEWORKS = [
  { value: 'gdpr', label: 'GDPR' },
  { value: 'hipaa', label: 'HIPAA' },
  { value: 'pci_dss', label: 'PCI-DSS' },
  { value: 'sox', label: 'SOX' },
  { value: 'ccpa', label: 'CCPA' },
  { value: 'none', label: 'None' },
]

const REGIONS = [
  { value: 'us-east-1', label: 'US East (N. Virginia)' },
  { value: 'us-west-2', label: 'US West (Oregon)' },
  { value: 'eu-west-1', label: 'EU (Ireland)' },
  { value: 'eu-central-1', label: 'EU (Frankfurt)' },
  { value: 'ap-southeast-1', label: 'Asia Pacific (Singapore)' },
  { value: 'ap-northeast-1', label: 'Asia Pacific (Tokyo)' },
  { value: 'on-premises', label: 'On-Premises' },
  { value: 'hybrid', label: 'Hybrid' },
  { value: 'other', label: 'Other' },
]

export default function DataStores() {
  const [search, setSearch] = useState('')
  const [organizationFilter, setOrganizationFilter] = useState<string>('')
  const [classificationFilter, setClassificationFilter] = useState<string>('')
  const [storageTypeFilter, setStorageTypeFilter] = useState<string>('')
  const [regionFilter, setRegionFilter] = useState<string>('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingDataStore, setEditingDataStore] = useState<any>(null)
  const [viewingDataStore, setViewingDataStore] = useState<any>(null)
  const queryClient = useQueryClient()

  const { data: organizations } = useQuery({
    queryKey: queryKeys.organizations.dropdown,
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const { data: identities } = useQuery({
    queryKey: queryKeys.identities.list({}),
    queryFn: () => api.getIdentities({ per_page: 1000 }),
  })

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.dataStores.list({
      search,
      organization_id: organizationFilter,
      data_classification: classificationFilter,
      storage_type: storageTypeFilter,
      location_region: regionFilter,
    }),
    queryFn: () => api.getDataStores({
      search,
      organization_id: organizationFilter ? parseInt(organizationFilter) : undefined,
      data_classification: classificationFilter || undefined,
      storage_type: storageTypeFilter || undefined,
      location_region: regionFilter || undefined,
    }),
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.createDataStore(data),
    onSuccess: async () => {
      await invalidateCache.dataStores(queryClient)
      toast.success('Data store created successfully')
      setShowCreateModal(false)
    },
    onError: () => {
      toast.error('Failed to create data store')
    },
  })

  const updateMutation = useMutation({
    mutationFn: (data: any) => api.updateDataStore(editingDataStore.id, data),
    onSuccess: async () => {
      await invalidateCache.dataStores(queryClient)
      toast.success('Data store updated successfully')
      setEditingDataStore(null)
    },
    onError: () => {
      toast.error('Failed to update data store')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteDataStore(id),
    onSuccess: async () => {
      await invalidateCache.dataStores(queryClient)
      toast.success('Data store deleted successfully')
    },
    onError: () => {
      toast.error('Failed to delete data store')
    },
  })

  const handleDelete = (id: number, name: string) => {
    confirmDelete(`data store "${name}"`, () => {
      deleteMutation.mutate(id)
    })
  }

  const getClassificationColor = (classification: string): string => {
    const classif = DATA_CLASSIFICATIONS.find(c => c.value === classification)
    return classif?.color || 'bg-slate-500/20 text-slate-400'
  }

  const organizationOptions = organizations?.items?.map((org: any) => ({
    value: org.id.toString(),
    label: org.name,
  })) || []

  const pocOptions = identities?.items?.map((identity: any) => ({
    value: identity.id.toString(),
    label: identity.full_name || identity.username,
  })) || []

  const dataStoreFields: FormField[] = useMemo(() => [
    {
      name: 'name',
      label: 'Name',
      type: 'text',
      required: true,
      placeholder: 'Production Customer Database',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      placeholder: 'Brief description of the data store',
      rows: 2,
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      options: [
        { value: '', label: organizationOptions.length ? 'Select organization' : 'No organizations found' },
        ...organizationOptions,
      ],
    },
    {
      name: 'data_classification',
      label: 'Data Classification',
      type: 'select',
      required: true,
      options: DATA_CLASSIFICATIONS.map(c => ({ value: c.value, label: c.label })),
      defaultValue: 'internal',
    },
    {
      name: 'storage_type',
      label: 'Storage Type',
      type: 'select',
      required: true,
      options: STORAGE_TYPES,
      defaultValue: 'database',
    },
    {
      name: 'location_region',
      label: 'Location/Region',
      type: 'select',
      options: REGIONS,
      defaultValue: 'us-east-1',
    },
    {
      name: 'location_region_other',
      label: 'Custom Region/Location',
      type: 'text',
      placeholder: 'Enter custom location or region',
      showWhen: (values: Record<string, any>) => values.location_region === 'other',
    },
    {
      name: 'contains_pii',
      label: 'Contains Personally Identifiable Information (PII)',
      type: 'checkbox',
      defaultValue: false,
    },
    {
      name: 'contains_phi',
      label: 'Contains Protected Health Information (PHI)',
      type: 'checkbox',
      defaultValue: false,
    },
    {
      name: 'contains_pci',
      label: 'Contains Payment Card Industry (PCI) Data',
      type: 'checkbox',
      defaultValue: false,
    },
    {
      name: 'compliance_framework',
      label: 'Compliance Framework',
      type: 'select',
      options: COMPLIANCE_FRAMEWORKS,
      defaultValue: 'none',
    },
    {
      name: 'poc_identity_id',
      label: 'Point of Contact',
      type: 'select',
      options: [
        { value: '', label: pocOptions.length ? 'Select contact (optional)' : 'No identities found' },
        ...pocOptions,
      ],
    },
  ], [organizationOptions, pocOptions])

  const editFields: FormField[] = useMemo(() => dataStoreFields.map(field => ({
    ...field,
    defaultValue: field.name === 'organization_id' || field.name === 'poc_identity_id'
      ? editingDataStore?.[field.name]?.toString()
      : editingDataStore?.[field.name],
  })), [dataStoreFields, editingDataStore])

  const handleCreateSubmit = (data: Record<string, any>) => {
    // Use custom region if "other" is selected
    const locationRegion = data.location_region === 'other'
      ? data.location_region_other?.trim()
      : data.location_region

    createMutation.mutate({
      name: data.name?.trim(),
      description: data.description?.trim() || undefined,
      organization_id: parseInt(data.organization_id),
      data_classification: data.data_classification,
      storage_type: data.storage_type,
      location_region: locationRegion || undefined,
      contains_pii: data.contains_pii || false,
      contains_phi: data.contains_phi || false,
      contains_pci: data.contains_pci || false,
      compliance_framework: data.compliance_framework || undefined,
      poc_identity_id: data.poc_identity_id ? parseInt(data.poc_identity_id) : undefined,
    })
  }

  const handleEditSubmit = (data: Record<string, any>) => {
    // Use custom region if "other" is selected
    const locationRegion = data.location_region === 'other'
      ? data.location_region_other?.trim()
      : data.location_region

    updateMutation.mutate({
      name: data.name?.trim(),
      description: data.description?.trim() || undefined,
      data_classification: data.data_classification,
      storage_type: data.storage_type,
      location_region: locationRegion || undefined,
      contains_pii: data.contains_pii || false,
      contains_phi: data.contains_phi || false,
      contains_pci: data.contains_pci || false,
      compliance_framework: data.compliance_framework || undefined,
      poc_identity_id: data.poc_identity_id ? parseInt(data.poc_identity_id) : undefined,
    })
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Data Stores</h1>
          <p className="mt-2 text-slate-400">
            Catalog and manage all data repositories and storage systems
          </p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Add Data Store
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4 mb-6">
        <div className="flex-1 min-w-[200px] max-w-md">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
            <Input
              type="text"
              placeholder="Search data stores..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10"
            />
          </div>
        </div>
        <Select
          value={organizationFilter}
          onChange={(e) => setOrganizationFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Organizations</option>
          {organizations?.items?.map((org: any) => (
            <option key={org.id} value={org.id}>
              {org.name}
            </option>
          ))}
        </Select>
        <Select
          value={classificationFilter}
          onChange={(e) => setClassificationFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Classifications</option>
          {DATA_CLASSIFICATIONS.map((classif) => (
            <option key={classif.value} value={classif.value}>
              {classif.label}
            </option>
          ))}
        </Select>
        <Select
          value={storageTypeFilter}
          onChange={(e) => setStorageTypeFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Storage Types</option>
          {STORAGE_TYPES.map((type) => (
            <option key={type.value} value={type.value}>
              {type.label}
            </option>
          ))}
        </Select>
        <Select
          value={regionFilter}
          onChange={(e) => setRegionFilter(e.target.value)}
          className="w-48"
        >
          <option value="">All Regions</option>
          {REGIONS.map((region) => (
            <option key={region.value} value={region.value}>
              {region.label}
            </option>
          ))}
        </Select>
      </div>

      {/* Data Stores List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <Database className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No data stores found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first data store
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data?.items?.map((dataStore: any) => (
            <Card
              key={dataStore.id}
              className="cursor-pointer hover:border-primary-500/50 transition-colors"
              onClick={() => setViewingDataStore(dataStore)}
            >
              <CardContent>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <Database className="w-5 h-5 text-primary-400 flex-shrink-0" />
                    <h3 className="text-lg font-semibold text-white truncate">
                      {dataStore.name}
                    </h3>
                  </div>
                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setEditingDataStore(dataStore)
                      }}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                    >
                      <Edit className="w-4 h-4" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDelete(dataStore.id, dataStore.name)
                      }}
                      className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-500/10 rounded transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {dataStore.description && (
                  <p className="text-sm text-slate-400 mb-3 line-clamp-2">
                    {dataStore.description}
                  </p>
                )}

                <div className="flex flex-wrap gap-2 mb-3">
                  <span className={`text-xs px-2 py-1 rounded ${getClassificationColor(dataStore.data_classification)}`}>
                    {DATA_CLASSIFICATIONS.find(c => c.value === dataStore.data_classification)?.label || 'Unknown'}
                  </span>
                  <span className="text-xs px-2 py-1 rounded bg-slate-700 text-slate-300">
                    {STORAGE_TYPES.find(t => t.value === dataStore.storage_type)?.label || dataStore.storage_type}
                  </span>
                </div>

                <div className="flex flex-wrap gap-2 mb-3">
                  {dataStore.contains_pii && (
                    <div className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-blue-500/20 text-blue-400">
                      <AlertCircle className="w-3 h-3" />
                      PII
                    </div>
                  )}
                  {dataStore.contains_phi && (
                    <div className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-purple-500/20 text-purple-400">
                      <AlertCircle className="w-3 h-3" />
                      PHI
                    </div>
                  )}
                  {dataStore.contains_pci && (
                    <div className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-pink-500/20 text-pink-400">
                      <AlertCircle className="w-3 h-3" />
                      PCI
                    </div>
                  )}
                </div>

                {dataStore.location_region && (
                  <div className="text-xs text-slate-500 mb-2">
                    Region: {REGIONS.find(r => r.value === dataStore.location_region)?.label || dataStore.location_region}
                  </div>
                )}

                {dataStore.compliance_framework && dataStore.compliance_framework !== 'none' && (
                  <div className="text-xs text-slate-500">
                    Compliance: {COMPLIANCE_FRAMEWORKS.find(f => f.value === dataStore.compliance_framework)?.label || dataStore.compliance_framework}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <FormModalBuilder
          isOpen={showCreateModal}
          title="Add Data Store"
          fields={dataStoreFields}
          onSubmit={handleCreateSubmit}
          onClose={() => setShowCreateModal(false)}
          submitButtonText="Create"
        />
      )}

      {/* Edit Modal */}
      {!!editingDataStore && (
        <FormModalBuilder
          isOpen={!!editingDataStore}
          title="Edit Data Store"
          fields={editFields}
          onSubmit={handleEditSubmit}
          onClose={() => setEditingDataStore(null)}
          submitButtonText="Save"
        />
      )}

      {/* View Details Modal */}
      {viewingDataStore && (
        <DataStoreDetailModal
          dataStore={viewingDataStore}
          identities={identities?.items || []}
          onClose={() => setViewingDataStore(null)}
          onEdit={() => {
            setEditingDataStore(viewingDataStore)
            setViewingDataStore(null)
          }}
        />
      )}
    </div>
  )
}

interface DataStoreDetailModalProps {
  dataStore: any
  identities: any[]
  onClose: () => void
  onEdit: () => void
}

function DataStoreDetailModal({ dataStore, identities, onClose, onEdit }: DataStoreDetailModalProps) {
  const getPOCName = (pocId: number) => {
    const identity = identities.find(i => i.id === pocId)
    return identity?.full_name || identity?.username || 'Unknown'
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Database className="w-5 h-5 text-primary-400" />
              <h2 className="text-xl font-semibold text-white">{dataStore.name}</h2>
            </div>
            <button
              onClick={onEdit}
              className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
            >
              <Edit className="w-4 h-4" />
            </button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {dataStore.description && (
              <p className="text-sm text-slate-400">{dataStore.description}</p>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-slate-500 uppercase">Classification</label>
                <div className="mt-1">
                  <span className={`text-xs px-2 py-1 rounded ${getClassificationColor(dataStore.data_classification)}`}>
                    {DATA_CLASSIFICATIONS.find(c => c.value === dataStore.data_classification)?.label}
                  </span>
                </div>
              </div>
              <div>
                <label className="text-xs text-slate-500 uppercase">Storage Type</label>
                <p className="text-sm text-white mt-1">{STORAGE_TYPES.find(t => t.value === dataStore.storage_type)?.label}</p>
              </div>
              <div>
                <label className="text-xs text-slate-500 uppercase">Region</label>
                <p className="text-sm text-white mt-1">{REGIONS.find(r => r.value === dataStore.location_region)?.label || dataStore.location_region || '-'}</p>
              </div>
              <div>
                <label className="text-xs text-slate-500 uppercase">Compliance</label>
                <p className="text-sm text-white mt-1">{COMPLIANCE_FRAMEWORKS.find(f => f.value === dataStore.compliance_framework)?.label || dataStore.compliance_framework || '-'}</p>
              </div>
            </div>

            <div className="bg-slate-800/50 rounded-lg p-4 space-y-2">
              <label className="text-xs text-slate-500 uppercase block">Data Sensitivity Indicators</label>
              <div className="flex flex-wrap gap-2">
                {dataStore.contains_pii ? (
                  <div className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-blue-500/20 text-blue-400">
                    <AlertCircle className="w-3 h-3" />
                    Contains PII
                  </div>
                ) : (
                  <div className="text-xs px-2 py-1 rounded bg-slate-700 text-slate-400">
                    No PII
                  </div>
                )}
                {dataStore.contains_phi ? (
                  <div className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-purple-500/20 text-purple-400">
                    <AlertCircle className="w-3 h-3" />
                    Contains PHI
                  </div>
                ) : (
                  <div className="text-xs px-2 py-1 rounded bg-slate-700 text-slate-400">
                    No PHI
                  </div>
                )}
                {dataStore.contains_pci ? (
                  <div className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-pink-500/20 text-pink-400">
                    <AlertCircle className="w-3 h-3" />
                    Contains PCI
                  </div>
                ) : (
                  <div className="text-xs px-2 py-1 rounded bg-slate-700 text-slate-400">
                    No PCI
                  </div>
                )}
              </div>
            </div>

            {dataStore.poc_identity_id && (
              <div>
                <label className="text-xs text-slate-500 uppercase">Point of Contact</label>
                <p className="text-sm text-white mt-1">{getPOCName(dataStore.poc_identity_id)}</p>
              </div>
            )}

            <div className="flex justify-end pt-4">
              <Button variant="ghost" onClick={onClose}>
                Close
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function getClassificationColor(classification: string): string {
  const classif = DATA_CLASSIFICATIONS.find(c => c.value === classification)
  return classif?.color || 'bg-slate-500/20 text-slate-400'
}
