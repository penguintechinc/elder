import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, FileKey, Trash2, Edit2, Eye } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
// Input is used in form configs only, not directly imported for this component
import Select from '@/components/Select'
import { FormModalBuilder, FormField } from '@penguin/react_libs/components'

const CERT_CREATORS = [
  { value: 'digicert', label: 'DigiCert' },
  { value: 'letsencrypt', label: 'Let\'s Encrypt' },
  { value: 'self_signed', label: 'Self Signed' },
  { value: 'sectigo', label: 'Sectigo' },
  { value: 'globalsign', label: 'GlobalSign' },
  { value: 'godaddy', label: 'GoDaddy' },
  { value: 'entrust', label: 'Entrust' },
  { value: 'certbot', label: 'Certbot' },
  { value: 'acme', label: 'ACME' },
  { value: 'comodo', label: 'Comodo' },
  { value: 'other', label: 'Other' },
]

const CERT_TYPES = [
  { value: 'ca_root', label: 'CA Root' },
  { value: 'ca_intermediate', label: 'CA Intermediate' },
  { value: 'server_cert', label: 'Server Certificate' },
  { value: 'client_cert', label: 'Client Certificate' },
  { value: 'code_signing', label: 'Code Signing' },
  { value: 'wildcard', label: 'Wildcard' },
  { value: 'san', label: 'SAN' },
  { value: 'ecc', label: 'ECC' },
  { value: 'rsa', label: 'RSA' },
  { value: 'email', label: 'Email' },
  { value: 'other', label: 'Other' },
]

function getStatusBadge(status: string, expirationDate?: string, renewalDaysBefore?: number) {
  const today = new Date()
  const expDate = expirationDate ? new Date(expirationDate) : null
  const daysUntilExpiry = expDate ? Math.ceil((expDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24)) : 0
  const renewalDays = renewalDaysBefore || 30

  if (status === 'revoked') {
    return { badge: 'Revoked', className: 'bg-gray-500/20 text-gray-400' }
  }

  if (status === 'expired' || daysUntilExpiry <= 0) {
    return { badge: 'Expired', className: 'bg-red-500/20 text-red-400' }
  }

  if (daysUntilExpiry <= renewalDays) {
    return { badge: 'Expiring Soon', className: 'bg-yellow-500/20 text-yellow-400' }
  }

  return { badge: 'Active', className: 'bg-green-500/20 text-green-400' }
}

export default function Certificates() {
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)
  const [showDetailsModal, setShowDetailsModal] = useState(false)
  const [selectedCertificate, setSelectedCertificate] = useState<any>(null)
  const [filterCreator, setFilterCreator] = useState('')
  const [filterType, setFilterType] = useState('')
  const [filterOrg, setFilterOrg] = useState('')
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['certificates'],
    queryFn: () => api.getCertificates(),
  })

  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations(),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteCertificate(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['certificates'],
        refetchType: 'all'
      })
      toast.success('Certificate deleted')
    },
    onError: () => {
      toast.error('Failed to delete certificate')
    },
  })

  const filteredCerts = data?.certificates?.filter((cert: any) => {
    if (filterCreator && cert.creator !== filterCreator) return false
    if (filterType && cert.cert_type !== filterType) return false
    if (filterOrg && cert.organization_id !== parseInt(filterOrg)) return false
    return true
  }) || []

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Certificates</h1>
          <p className="mt-2 text-slate-400">Manage SSL/TLS certificates and certificate authorities</p>
        </div>
        <Button onClick={() => {
          setSelectedCertificate(null)
          setShowCreateModal(true)
        }}>
          <Plus className="w-4 h-4 mr-2" />
          Add Certificate
        </Button>
      </div>

      {/* Filters */}
      <div className="mb-6 grid grid-cols-1 md:grid-cols-3 gap-4">
        <Select
          label="Filter by Creator"
          value={filterCreator}
          onChange={(e) => setFilterCreator(e.target.value)}
          options={[
            { value: '', label: 'All Creators' },
            ...CERT_CREATORS,
          ]}
        />
        <Select
          label="Filter by Type"
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
          options={[
            { value: '', label: 'All Types' },
            ...CERT_TYPES,
          ]}
        />
        <Select
          label="Filter by Organization"
          value={filterOrg}
          onChange={(e) => setFilterOrg(e.target.value)}
          options={[
            { value: '', label: 'All Organizations' },
            ...(orgs?.items || []).map((o: any) => ({ value: String(o.id), label: o.name })),
          ]}
        />
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filteredCerts.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <FileKey className="w-12 h-12 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No certificates found</p>
            <Button className="mt-4" onClick={() => {
              setSelectedCertificate(null)
              setShowCreateModal(true)
            }}>
              Add your first certificate
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {filteredCerts.map((cert: any) => {
            const { badge, className } = getStatusBadge(cert.status, cert.expiration_date, cert.renewal_days_before)
            const creatorLabel = CERT_CREATORS.find(c => c.value === cert.creator)?.label || cert.creator
            const typeLabel = CERT_TYPES.find(t => t.value === cert.cert_type)?.label || cert.cert_type
            const orgName = orgs?.items?.find((o: any) => o.id === cert.organization_id)?.name || 'Unknown'

            return (
              <Card key={cert.id}>
                <CardContent>
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-3 mb-3">
                        <FileKey className="w-5 h-5 text-primary-400" />
                        <div>
                          <h3 className="text-lg font-semibold text-white">{cert.name}</h3>
                          {cert.common_name && (
                            <p className="text-sm text-slate-400">CN: {cert.common_name}</p>
                          )}
                        </div>
                      </div>

                      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mt-4 text-sm">
                        <div>
                          <p className="text-slate-500 mb-1">Creator</p>
                          <span className="inline-block px-2 py-1 text-xs font-medium rounded bg-slate-700 text-slate-300">
                            {creatorLabel}
                          </span>
                        </div>
                        <div>
                          <p className="text-slate-500 mb-1">Type</p>
                          <span className="inline-block px-2 py-1 text-xs font-medium rounded bg-slate-700 text-slate-300">
                            {typeLabel}
                          </span>
                        </div>
                        <div>
                          <p className="text-slate-500 mb-1">Status</p>
                          <span className={`inline-block px-2 py-1 text-xs font-medium rounded ${className}`}>
                            {badge}
                          </span>
                        </div>
                        <div>
                          <p className="text-slate-500 mb-1">Issued</p>
                          <p className="text-white">{cert.issue_date ? new Date(cert.issue_date).toLocaleDateString() : 'N/A'}</p>
                        </div>
                        <div>
                          <p className="text-slate-500 mb-1">Expires</p>
                          <p className="text-white">{cert.expiration_date ? new Date(cert.expiration_date).toLocaleDateString() : 'N/A'}</p>
                        </div>
                      </div>

                      <div className="mt-3 text-xs text-slate-500">
                        Organization: {orgName}
                      </div>
                    </div>

                    <div className="flex gap-2 ml-4">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setSelectedCertificate(cert)
                          setShowDetailsModal(true)
                        }}
                        title="View details"
                      >
                        <Eye className="w-4 h-4" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setSelectedCertificate(cert)
                          setShowEditModal(true)
                        }}
                        title="Edit"
                      >
                        <Edit2 className="w-4 h-4" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => deleteMutation.mutate(cert.id)}
                        isLoading={deleteMutation.isPending}
                        title="Delete"
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

      {showCreateModal && (
        <CreateCertificateModal
          onClose={() => setShowCreateModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ['certificates'],
              refetchType: 'all'
            })
            setShowCreateModal(false)
          }}
        />
      )}

      {showEditModal && selectedCertificate && (
        <EditCertificateModal
          certificate={selectedCertificate}
          onClose={() => {
            setShowEditModal(false)
            setSelectedCertificate(null)
          }}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ['certificates'],
              refetchType: 'all'
            })
            setShowEditModal(false)
            setSelectedCertificate(null)
          }}
        />
      )}

      {showDetailsModal && selectedCertificate && (
        <CertificateDetailsModal
          certificate={selectedCertificate}
          onClose={() => {
            setShowDetailsModal(false)
            setSelectedCertificate(null)
          }}
        />
      )}
    </div>
  )
}

function CreateCertificateModal({ onClose, onSuccess }: any) {
  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations(),
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.createCertificate(data),
    onSuccess: () => {
      toast.success('Certificate created successfully')
      onSuccess()
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.message || 'Failed to create certificate')
    },
  })

  const fields: FormField[] = useMemo(() => [
    {
      name: 'name',
      label: 'Certificate Name',
      type: 'text',
      required: true,
      placeholder: 'Production SSL Certificate',
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      options: [
        { value: '', label: 'Select organization' },
        ...(orgs?.items || []).map((o: any) => ({ value: String(o.id), label: o.name })),
      ],
    },
    {
      name: 'creator',
      label: 'Certificate Creator',
      type: 'select',
      required: true,
      options: [
        { value: '', label: 'Select creator' },
        ...CERT_CREATORS,
      ],
    },
    {
      name: 'cert_type',
      label: 'Certificate Type',
      type: 'select',
      required: true,
      options: [
        { value: '', label: 'Select type' },
        ...CERT_TYPES,
      ],
    },
    {
      name: 'common_name',
      label: 'Common Name (CN)',
      type: 'text',
      placeholder: 'example.com',
    },
    {
      name: 'issue_date',
      label: 'Issue Date',
      type: 'date',
      required: true,
    },
    {
      name: 'expiration_date',
      label: 'Expiration Date',
      type: 'date',
      required: true,
    },
    {
      name: 'auto_renew',
      label: 'Auto-Renew',
      type: 'checkbox',
    },
    {
      name: 'certificate_pem',
      label: 'Certificate PEM (optional)',
      type: 'textarea',
      rows: 6,
      placeholder: '-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      rows: 3,
    },
  ], [orgs?.items])

  const handleSubmit = (data: Record<string, any>) => {
    createMutation.mutate({
      name: data.name,
      organization_id: parseInt(data.organization_id),
      creator: data.creator,
      cert_type: data.cert_type,
      common_name: data.common_name || undefined,
      issue_date: data.issue_date,
      expiration_date: data.expiration_date,
      auto_renew: data.auto_renew || false,
      certificate_pem: data.certificate_pem || undefined,
      description: data.description || undefined,
    })
  }

  return (
    <FormModalBuilder
      isOpen={true}
      onClose={onClose}
      title="Create Certificate"
      fields={fields}
      onSubmit={handleSubmit}
      submitButtonText={createMutation.isPending ? 'Creating...' : 'Create Certificate'}
    />
  )
}

function EditCertificateModal({ certificate, onClose, onSuccess }: any) {
  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations(),
  })

  const updateMutation = useMutation({
    mutationFn: (data: any) => api.updateCertificate(certificate.id, data),
    onSuccess: () => {
      toast.success('Certificate updated successfully')
      onSuccess()
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.message || 'Failed to update certificate')
    },
  })

  const editFields: FormField[] = useMemo(() => [
    {
      name: 'name',
      label: 'Certificate Name',
      type: 'text',
      required: true,
      defaultValue: certificate.name,
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      required: true,
      defaultValue: String(certificate.organization_id),
      options: [
        { value: '', label: 'Select organization' },
        ...(orgs?.items || []).map((o: any) => ({ value: String(o.id), label: o.name })),
      ],
    },
    {
      name: 'creator',
      label: 'Certificate Creator',
      type: 'select',
      required: true,
      defaultValue: certificate.creator,
      options: [
        { value: '', label: 'Select creator' },
        ...CERT_CREATORS,
      ],
    },
    {
      name: 'cert_type',
      label: 'Certificate Type',
      type: 'select',
      required: true,
      defaultValue: certificate.cert_type,
      options: [
        { value: '', label: 'Select type' },
        ...CERT_TYPES,
      ],
    },
    {
      name: 'common_name',
      label: 'Common Name (CN)',
      type: 'text',
      defaultValue: certificate.common_name || '',
    },
    {
      name: 'issue_date',
      label: 'Issue Date',
      type: 'date',
      required: true,
      defaultValue: certificate.issue_date,
    },
    {
      name: 'expiration_date',
      label: 'Expiration Date',
      type: 'date',
      required: true,
      defaultValue: certificate.expiration_date,
    },
    {
      name: 'auto_renew',
      label: 'Auto-Renew',
      type: 'checkbox',
      defaultValue: certificate.auto_renew || false,
    },
    {
      name: 'certificate_pem',
      label: 'Certificate PEM (optional)',
      type: 'textarea',
      rows: 6,
      defaultValue: certificate.certificate_pem || '',
    },
    {
      name: 'description',
      label: 'Description',
      type: 'textarea',
      rows: 3,
      defaultValue: certificate.description || '',
    },
  ], [orgs?.items, certificate])

  const handleSubmit = (data: Record<string, any>) => {
    updateMutation.mutate({
      name: data.name,
      organization_id: parseInt(data.organization_id),
      creator: data.creator,
      cert_type: data.cert_type,
      common_name: data.common_name || undefined,
      issue_date: data.issue_date,
      expiration_date: data.expiration_date,
      auto_renew: data.auto_renew || false,
      certificate_pem: data.certificate_pem || undefined,
      description: data.description || undefined,
    })
  }

  return (
    <FormModalBuilder
      isOpen={true}
      onClose={onClose}
      title="Edit Certificate"
      fields={editFields}
      onSubmit={handleSubmit}
      submitButtonText={updateMutation.isPending ? 'Updating...' : 'Update Certificate'}
    />
  )
}

function CertificateDetailsModal({ certificate, onClose }: any) {
  const creatorLabel = CERT_CREATORS.find(c => c.value === certificate.creator)?.label || certificate.creator
  const typeLabel = CERT_TYPES.find(t => t.value === certificate.cert_type)?.label || certificate.cert_type
  const { badge, className } = getStatusBadge(certificate.status, certificate.expiration_date, certificate.renewal_days_before)

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">{certificate.name}</h2>
            <Button variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid grid-cols-2 gap-6">
            <div>
              <p className="text-sm text-slate-500 mb-1">Common Name</p>
              <p className="text-white font-mono text-sm">{certificate.common_name || 'N/A'}</p>
            </div>
            <div>
              <p className="text-sm text-slate-500 mb-1">Status</p>
              <span className={`inline-block px-2 py-1 text-xs font-medium rounded ${className}`}>
                {badge}
              </span>
            </div>
            <div>
              <p className="text-sm text-slate-500 mb-1">Creator</p>
              <p className="text-white">{creatorLabel}</p>
            </div>
            <div>
              <p className="text-sm text-slate-500 mb-1">Type</p>
              <p className="text-white">{typeLabel}</p>
            </div>
            <div>
              <p className="text-sm text-slate-500 mb-1">Issue Date</p>
              <p className="text-white">{certificate.issue_date ? new Date(certificate.issue_date).toLocaleDateString() : 'N/A'}</p>
            </div>
            <div>
              <p className="text-sm text-slate-500 mb-1">Expiration Date</p>
              <p className="text-white">{certificate.expiration_date ? new Date(certificate.expiration_date).toLocaleDateString() : 'N/A'}</p>
            </div>
            <div>
              <p className="text-sm text-slate-500 mb-1">Auto-Renew</p>
              <p className="text-white">{certificate.auto_renew ? 'Yes' : 'No'}</p>
            </div>
            <div>
              <p className="text-sm text-slate-500 mb-1">ID</p>
              <p className="text-white font-mono text-sm">{certificate.id}</p>
            </div>
          </div>

          {certificate.description && (
            <div>
              <p className="text-sm text-slate-500 mb-2">Description</p>
              <p className="text-white">{certificate.description}</p>
            </div>
          )}

          {certificate.certificate_pem && (
            <div>
              <p className="text-sm text-slate-500 mb-2">Certificate PEM</p>
              <textarea
                value={certificate.certificate_pem}
                readOnly
                rows={8}
                className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white font-mono text-xs"
              />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
