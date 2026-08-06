import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import Button from '@/components/Button'
import Input from '@/components/Input'
import Card, { CardContent } from '@/components/Card'
import { FormModalBuilder, FormField } from '@penguintechinc/react-libs/components'

export default function HelpdeskCompanies() {
  const [search, setSearch] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.helpdesk.companies({ search }),
    queryFn: () => api.getHelpdeskCompanies({ search: search || undefined }),
  })

  interface Company {
    id: string
    name: string
    description?: string
    email?: string
    website?: string
  }

  interface CreateCompanyPayload {
    name: string
    description?: string
    website?: string
    email?: string
    phone?: string
  }

  const createMutation = useMutation({
    mutationFn: (formData: CreateCompanyPayload) => api.createHelpdeskCompany(formData),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.helpdesk.companies() })
      toast.success('Company created')
      setShowCreateModal(false)
    },
    onError: () => toast.error('Failed to create company'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteHelpdeskCompany(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.helpdesk.companies() })
      toast.success('Company deleted')
    },
    onError: () => toast.error('Failed to delete company'),
  })

  const companies = (data?.items || []) as Company[]

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Companies</h1>
          <p className="mt-2 text-slate-400">Manage customer companies</p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Add Company
        </Button>
      </div>

      <div className="relative mb-6">
        <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
        <Input
          type="text"
          placeholder="Search companies..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-10"
        />
      </div>

      {isLoading ? (
        <p className="text-slate-400">Loading companies...</p>
      ) : companies.length === 0 ? (
        <Card className="bg-slate-800 border-slate-700">
          <CardContent className="p-8 text-center">
            <p className="text-slate-400">No companies found</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {companies.map((company) => (
            <Card key={company.id} className="bg-slate-800 border-slate-700">
              <CardContent className="p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-semibold text-white">{company.name}</h3>
                    {company.email && <p className="text-sm text-slate-400">{company.email}</p>}
                    {company.website && <p className="text-sm text-blue-400">{company.website}</p>}
                  </div>
                  <button
                    onClick={() => deleteMutation.mutate(company.id)}
                    className="text-red-400 hover:text-red-300"
                    data-testid={`delete-company-${company.id}`}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <FormModalBuilder
        isOpen={showCreateModal}
        title="Add Company"
        fields={[
          { name: 'name', label: 'Company Name', type: 'text', required: true } as FormField,
          { name: 'description', label: 'Description', type: 'textarea', required: false } as FormField,
          { name: 'website', label: 'Website', type: 'text', required: false } as FormField,
          { name: 'email', label: 'Email', type: 'email', required: false } as FormField,
          { name: 'phone', label: 'Phone', type: 'tel', required: false } as FormField,
        ]}
        onSubmit={async (data: Record<string, unknown>) => {
          const payload: CreateCompanyPayload = {
            name: String(data.name || ''),
            description: data.description ? String(data.description) : undefined,
            website: data.website ? String(data.website) : undefined,
            email: data.email ? String(data.email) : undefined,
            phone: data.phone ? String(data.phone) : undefined,
          }
          await createMutation.mutateAsync(payload)
        }}
        onClose={() => setShowCreateModal(false)}
      />
    </div>
  )
}
