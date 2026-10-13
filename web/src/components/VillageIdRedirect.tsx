import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api from '@/lib/api'

interface ErrorWithResponse {
  response?: {
    data?: {
      message?: string
    }
  }
  message?: string
}

export default function VillageIdRedirect() {
  const { villageId } = useParams<{ villageId: string }>()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!villageId) {
      setError('No Village ID provided')
      return
    }

    const resolveAndRedirect = async () => {
      try {
        const response = await api.resolveVillageId(villageId)
        if (response.redirect_url) {
          // Use navigate for internal routes, or window.location for external
          if (response.redirect_url.startsWith('/')) {
            navigate(response.redirect_url, { replace: true })
          } else {
            window.location.href = response.redirect_url
          }
        } else {
          setError('No redirect URL found for this Village ID')
        }
      } catch (err: unknown) {
        const errorWithResponse = err as ErrorWithResponse | undefined
        const errorMessage = errorWithResponse?.response?.data?.message
          || (errorWithResponse instanceof Error ? errorWithResponse.message : undefined)
          || 'Failed to resolve Village ID'
        setError(errorMessage)
      }
    }

    resolveAndRedirect()
  }, [villageId, navigate])

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-slate-900">
        <div className="text-center">
          <h2 className="text-xl font-semibold text-red-400 mb-2">Error</h2>
          <p className="text-slate-400">{error}</p>
          <button
            onClick={() => navigate('/')}
            className="mt-4 px-4 py-2 bg-primary-600 text-white rounded hover:bg-primary-700 transition-colors"
          >
            Go to Dashboard
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-slate-900">
      <div className="text-center">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
        <p className="text-slate-400">Resolving Village ID...</p>
      </div>
    </div>
  )
}
