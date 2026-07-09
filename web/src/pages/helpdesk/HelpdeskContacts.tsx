import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Trash2, Mail, Phone } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import Button from '@/components/Button'
import Input from '@/components/Input'
import Card, { CardContent } from '@/components/Card'
import { FormModalBuilder, FormField } from '@penguintechinc/react-libs/components'

export default function HelpdeskContacts() {
  const [search, setSearch] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.helpdesk.contacts({ search }),
    queryFn: () => api.getHelpdeskContacts({ search: search || undefined }),
  })

  interface Contact {
    id: string
    name: string
    email: string
    phone?: string
    company_id?: string
  }

  interface CreateContactPayload {
    name: string
    email: string
    phone?: string
    company_id?: string
  }

  const createMutation = useMutation({
    mutationFn: (formData: CreateContactPayload) => api.createHelpdeskContact(formData),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.helpdesk.contacts() })
      toast.success('Contact created')
      setShowCreateModal(false)
    },
    onError: () => toast.error('Failed to create contact'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteHelpdeskContact(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.helpdesk.contacts() })
      toast.success('Contact deleted')
    },
    onError: () => toast.error('Failed to delete contact'),
  })

  const contacts = (data?.data || []) as Contact[]

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Contacts</h1>
          <p className="mt-2 text-slate-400">Manage customer contacts</p>
        </div>
        <Button onClick={() => setShowCreateModal(true)} data-testid="add-contact-btn">
          <Plus className="w-4 h-4 mr-2" />
          Add Contact
        </Button>
      </div>

      <div className="relative mb-6">
        <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
        <Input
          type="text"
          placeholder="Search contacts..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-10"
          data-testid="search-contacts"
        />
      </div>

      {isLoading ? (
        <p className="text-slate-400">Loading contacts...</p>
      ) : contacts.length === 0 ? (
        <Card className="bg-slate-800 border-slate-700">
          <CardContent className="p-8 text-center">
            <p className="text-slate-400">No contacts found</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {contacts.map((contact) => (
            <Card key={contact.id} className="bg-slate-800 border-slate-700">
              <CardContent className="p-4">
                <div className="flex items-center justify-between">
                  <div className="flex-1">
                    <h3 className="font-semibold text-white">{contact.name}</h3>
                    <div className="flex gap-4 mt-2">
                      {contact.email && (
                        <div className="flex items-center text-sm text-slate-400">
                          <Mail className="w-4 h-4 mr-2" />
                          {contact.email}
                        </div>
                      )}
                      {contact.phone && (
                        <div className="flex items-center text-sm text-slate-400">
                          <Phone className="w-4 h-4 mr-2" />
                          {contact.phone}
                        </div>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={() => deleteMutation.mutate(contact.id)}
                    className="text-red-400 hover:text-red-300"
                    data-testid={`delete-contact-${contact.id}`}
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
        title="Add Contact"
        fields={[
          { name: 'name', label: 'Contact Name', type: 'text', required: true } as FormField,
          { name: 'email', label: 'Email', type: 'email', required: true } as FormField,
          { name: 'phone', label: 'Phone', type: 'tel', required: false } as FormField,
          { name: 'company_id', label: 'Company ID', type: 'text', required: false } as FormField,
        ]}
        onSubmit={async (data: Record<string, unknown>) => {
          const payload: CreateContactPayload = {
            name: String(data.name || ''),
            email: String(data.email || ''),
            phone: data.phone ? String(data.phone) : undefined,
            company_id: data.company_id ? String(data.company_id) : undefined,
          }
          await createMutation.mutateAsync(payload)
        }}
        onClose={() => setShowCreateModal(false)}
      />
    </div>
  )
}
