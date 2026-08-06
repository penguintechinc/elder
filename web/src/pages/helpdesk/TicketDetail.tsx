import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Send, Lock } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'

interface Ticket {
  id: string
  subject: string
  status: string
  priority: string
  created_at: string
  assigned_to?: number
  assigned_to_name?: string
}

interface Message {
  id: string
  body: string
  author_name: string
  is_internal: boolean
  created_at: string
}

export default function TicketDetail() {
  const { id } = useParams<{ id: string }>()
  const [messageBody, setMessageBody] = useState('')
  const [isInternal, setIsInternal] = useState(false)
  const queryClient = useQueryClient()
  // TODO: integrate with auth provider for admin role check
  const isAdmin = false

  // Use empty string as default to always call hooks
  const ticketId = id || ''

  const { data: ticket, isLoading: ticketLoading } = useQuery({
    queryKey: queryKeys.helpdesk.ticket(ticketId),
    queryFn: () => api.getHelpdeskTicket(ticketId),
    enabled: !!ticketId,
  })

  const { data: messagesData, isLoading: messagesLoading } = useQuery({
    queryKey: queryKeys.helpdesk.messages(ticketId),
    queryFn: () => api.getHelpdeskMessages(ticketId),
    enabled: !!ticketId,
  })

  const updateMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.updateHelpdeskTicket(ticketId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.helpdesk.ticket(ticketId) })
      toast.success('Ticket updated')
    },
    onError: () => {
      toast.error('Failed to update ticket')
    },
  })

  const messageMutation = useMutation({
    mutationFn: () => api.createHelpdeskMessage(ticketId, {
      body: messageBody,
      is_internal: isInternal,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.helpdesk.messages(ticketId) })
      setMessageBody('')
      setIsInternal(false)
      toast.success('Message added')
    },
    onError: () => {
      toast.error('Failed to add message')
    },
  })

  const ticketData = (ticket || {}) as Ticket
  const messages = (messagesData?.items || []) as Message[]

  if (!id) return <p className="text-slate-400">Invalid ticket ID</p>

  return (
    <div className="p-8">
      {ticketLoading ? (
        <p className="text-slate-400">Loading ticket...</p>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-6 mb-8">
            <div className="col-span-2">
              <Card className="bg-slate-800 border-slate-700">
                <CardContent className="p-6">
                  <h1 className="text-2xl font-bold text-white mb-4">{ticketData.subject}</h1>
                  <div className="space-y-4 text-slate-300">
                    <p><strong>Status:</strong> {ticketData.status}</p>
                    <p><strong>Priority:</strong> {ticketData.priority}</p>
                    <p><strong>Created:</strong> {ticketData.created_at}</p>
                    {ticketData.assigned_to && (
                      <p><strong>Assigned to:</strong> {ticketData.assigned_to_name}</p>
                    )}
                  </div>
                  {isAdmin && (
                    <div className="mt-6 space-y-3">
                      <select
                        value={ticketData.status}
                        onChange={(e) => updateMutation.mutate({ status: e.target.value })}
                        className="w-full px-3 py-2 bg-slate-700 border border-slate-600 text-white rounded"
                        data-testid="status-select"
                      >
                        <option value="open">Open</option>
                        <option value="in_progress">In Progress</option>
                        <option value="on_hold">On Hold</option>
                        <option value="closed">Closed</option>
                      </select>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            <Card className="bg-slate-800 border-slate-700">
              <CardContent className="p-6">
                <h3 className="text-lg font-bold text-white mb-4">Actions</h3>
                {isAdmin && (
                  <Button
                    onClick={() => updateMutation.mutate({ status: 'closed' })}
                    className="w-full mb-3"
                    data-testid="close-ticket-btn"
                  >
                    Close Ticket
                  </Button>
                )}
              </CardContent>
            </Card>
          </div>

          <Card className="bg-slate-800 border-slate-700 mb-8">
            <CardContent className="p-6">
              <h2 className="text-xl font-bold text-white mb-4">Messages</h2>
              {messagesLoading ? (
                <p className="text-slate-400">Loading messages...</p>
              ) : messages.length === 0 ? (
                <p className="text-slate-400">No messages yet</p>
              ) : (
                <div className="space-y-4">
                  {messages.map((msg) => (
                    <div key={msg.id} className="p-4 bg-slate-700 rounded">
                      <div className="flex items-center justify-between mb-2">
                        <p className="font-semibold text-white">{msg.author_name}</p>
                        {msg.is_internal && (
                          <span className="flex items-center text-xs text-amber-400">
                            <Lock className="w-3 h-3 mr-1" /> Internal
                          </span>
                        )}
                      </div>
                      <p className="text-slate-300 text-sm">{msg.body}</p>
                      <p className="text-slate-500 text-xs mt-2">{msg.created_at}</p>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="bg-slate-800 border-slate-700">
            <CardContent className="p-6">
              <h2 className="text-lg font-bold text-white mb-4">Add Message</h2>
              <div className="space-y-4">
                <textarea
                  value={messageBody}
                  onChange={(e) => setMessageBody(e.target.value)}
                  placeholder="Type your message..."
                  className="w-full px-3 py-2 bg-slate-700 border border-slate-600 text-white rounded h-24"
                  data-testid="message-textarea"
                />
                <label className="flex items-center text-slate-300">
                  <input
                    type="checkbox"
                    checked={isInternal}
                    onChange={(e) => setIsInternal(e.target.checked)}
                    className="mr-2"
                    data-testid="internal-toggle"
                  />
                  Internal note
                </label>
                <Button
                  onClick={() => messageMutation.mutate()}
                  disabled={!messageBody.trim()}
                  data-testid="send-message-btn"
                >
                  <Send className="w-4 h-4 mr-2" />
                  Send
                </Button>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
