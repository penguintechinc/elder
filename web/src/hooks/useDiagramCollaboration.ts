import { useState, useEffect, useRef, useCallback } from 'react'
import type { Node, Edge } from '@xyflow/react'
import api from '@/lib/api'

export interface RemoteCollaborator {
  identity_id: string
  x: number
  y: number
}

export interface UseDiagramCollaborationOptions {
  enabled: boolean
  onRemoteChange: (nodes: Node[], edges: Edge[]) => void
}

interface WebSocketMessage {
  type:
    | 'cursor'
    | 'drawing_change'
    | 'presence'
    | 'ping'
    | 'cursor_moved'
    | 'drawing_changed'
    | 'user_joined'
    | 'user_left'
    | 'collaborators'
  [key: string]: unknown
}

export function useDiagramCollaboration(
  diagramId: number,
  { enabled, onRemoteChange }: UseDiagramCollaborationOptions
) {
  const [collaborators, setCollaborators] = useState<Map<string, RemoteCollaborator>>(new Map())
  const [connected, setConnected] = useState(false)
  const ws = useRef<WebSocket | null>(null)
  const lastCursorSendTime = useRef<number>(0)
  const applyingRemoteChange = useRef(false)
  const reconnectAttempt = useRef(0)
  const maxReconnectAttempts = 3

  // Build WebSocket URL respecting current protocol and API base
  const getWebSocketUrl = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const apiBase = import.meta.env.VITE_API_URL || ''
    return `${protocol}//${host}${apiBase}/api/v1/diagrams/${diagramId}/collab/ws`
  }, [diagramId])

  // Fetch ticket and connect to WebSocket
  const connect = useCallback(async () => {
    if (!enabled) {
      console.log('[useDiagramCollaboration] Collaboration disabled')
      setConnected(false)
      return
    }

    try {
      const { ticket } = await api.getCollabTicket(diagramId)

      const wsUrl = `${getWebSocketUrl()}?ticket=${encodeURIComponent(ticket)}`
      console.log('[useDiagramCollaboration] Connecting to WebSocket:', wsUrl)

      ws.current = new WebSocket(wsUrl)

      ws.current.onopen = () => {
        console.log('[useDiagramCollaboration] WebSocket connected')
        setConnected(true)
        reconnectAttempt.current = 0
      }

      ws.current.onmessage = (event) => {
        try {
          const message: WebSocketMessage = JSON.parse(event.data)
          handleMessageReceived(message)
        } catch (err) {
          console.error('[useDiagramCollaboration] Failed to parse message:', err)
        }
      }

      ws.current.onerror = (error) => {
        console.error('[useDiagramCollaboration] WebSocket error:', error)
      }

      ws.current.onclose = () => {
        console.log('[useDiagramCollaboration] WebSocket closed')
        setConnected(false)

        // Retry with backoff (only if not explicitly disabled)
        if (enabled && reconnectAttempt.current < maxReconnectAttempts) {
          const delay = Math.min(1000 * Math.pow(2, reconnectAttempt.current), 10000)
          reconnectAttempt.current += 1
          console.log(`[useDiagramCollaboration] Retrying in ${delay}ms (attempt ${reconnectAttempt.current})`)
          setTimeout(connect, delay)
        }
      }
    } catch (err) {
      // Silently handle 403 (collaboration disabled) and other errors
      if (err instanceof Error && err.message.includes('403')) {
        console.log('[useDiagramCollaboration] Collaboration not available (403 Forbidden)')
      } else {
        console.error('[useDiagramCollaboration] Failed to get ticket:', err)
      }
      setConnected(false)
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [diagramId, enabled, getWebSocketUrl, onRemoteChange])

  // Handle incoming messages
  const handleMessageReceived = (message: WebSocketMessage) => {
    switch (message.type) {
      case 'cursor_moved': {
        const { identity_id, x, y } = message as RemoteCollaborator
        setCollaborators((prev) => {
          const updated = new Map(prev)
          updated.set(identity_id, { identity_id, x, y })
          return updated
        })
        break
      }

      case 'drawing_changed': {
        if (applyingRemoteChange.current) return
        const { nodes, edges } = message as { nodes: Node[]; edges: Edge[] }
        applyingRemoteChange.current = true
        onRemoteChange(nodes, edges)
        setTimeout(() => {
          applyingRemoteChange.current = false
        }, 100)
        break
      }

      case 'user_joined': {
        const { identity_id } = message as { identity_id: string }
        console.log('[useDiagramCollaboration] User joined:', identity_id)
        break
      }

      case 'user_left': {
        const { identity_id } = message as { identity_id: string }
        setCollaborators((prev) => {
          const updated = new Map(prev)
          updated.delete(identity_id)
          return updated
        })
        console.log('[useDiagramCollaboration] User left:', identity_id)
        break
      }

      case 'collaborators': {
        const { collaborators: collab } = message as { collaborators: RemoteCollaborator[] }
        const map = new Map(collab.map((c) => [c.identity_id, c]))
        setCollaborators(map)
        console.log('[useDiagramCollaboration] Initial collaborators:', collab.length)
        break
      }

      default:
        break
    }
  }

  // Send cursor position (throttled ~30fps = 33ms)
  const sendCursor = useCallback(
    (x: number, y: number) => {
      if (!connected || !ws.current || ws.current.readyState !== WebSocket.OPEN) return

      const now = Date.now()
      if (now - lastCursorSendTime.current < 33) return

      lastCursorSendTime.current = now
      try {
        ws.current.send(JSON.stringify({ type: 'cursor', x, y }))
      } catch (err) {
        console.error('[useDiagramCollaboration] Failed to send cursor:', err)
      }
    },
    [connected]
  )

  // Send drawing changes (nodes + edges snapshot)
  const sendDrawingChange = useCallback(
    (nodes: Node[], edges: Edge[]) => {
      if (!connected || !ws.current || ws.current.readyState !== WebSocket.OPEN) return

      try {
        ws.current.send(JSON.stringify({ type: 'drawing_change', nodes, edges }))
      } catch (err) {
        console.error('[useDiagramCollaboration] Failed to send drawing change:', err)
      }
    },
    [connected]
  )

  // Connect on mount, disconnect on unmount
  useEffect(() => {
    if (enabled) {
      connect()
    }
    return () => {
      if (ws.current) {
        ws.current.close()
        ws.current = null
      }
      setConnected(false)
    }
  }, [enabled, connect])

  return {
    collaborators: Array.from(collaborators.values()),
    connected,
    sendCursor,
    sendDrawingChange,
  }
}
