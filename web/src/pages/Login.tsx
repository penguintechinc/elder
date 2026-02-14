import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { LoginPageBuilder } from '@penguintechinc/react-libs/components'
import type { LoginPayload, LoginResponse } from '@penguintechinc/react-libs/components'
import api from '@/lib/api'

export default function Login() {
  const navigate = useNavigate()

  // Check if guest login is enabled
  const { data: guestConfig } = useQuery({
    queryKey: ['guest-enabled'],
    queryFn: () => api.getGuestEnabled(),
    staleTime: 60000, // Cache for 1 minute
  })

  const loginMutation = useMutation({
    mutationFn: ({ username, password, tenant }: { username: string; password: string; tenant?: string }) =>
      api.portalLogin(username, password, tenant === 'Global' || !tenant ? 'system' : tenant),
    onSuccess: () => {
      toast.success('Login successful!')
      navigate('/')
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Login failed')
    },
  })

  const handleLogin = async (payload: LoginPayload): Promise<LoginResponse> => {
    try {
      await loginMutation.mutateAsync({
        username: payload.username,
        password: payload.password,
        tenant: payload.tenant || 'Global',
      })
      return { success: true }
    } catch (error: any) {
      return {
        success: false,
        error: error.response?.data?.error || 'Login failed',
      }
    }
  }

  const handleGuestLogin = async (): Promise<LoginResponse> => {
    if (!guestConfig?.username) {
      return { success: false, error: 'Guest login not available' }
    }

    try {
      await loginMutation.mutateAsync({
        username: `${guestConfig.username}@localhost`,
        password: guestConfig.username,
        tenant: 'Global',
      })
      return { success: true }
    } catch (error: any) {
      return {
        success: false,
        error: error.response?.data?.error || 'Guest login failed',
      }
    }
  }

  return (
    <LoginPageBuilder
      branding={{
        appName: 'Elder',
        tagline: 'Entity Relationship Tracking System',
        logo: '/elder-logo.png',
        logoHeight: 300,
      }}
      api={{
        login: handleLogin,
      }}
      features={{
        showTenantField: true,
        tenantFieldLabel: 'Tenant',
        tenantFieldPlaceholder: 'Global',
        tenantFieldHelp: 'Leave as "Global" for system-wide access',
        showRegisterLink: true,
        registerLinkText: "Don't have an account? Register here",
        onRegisterClick: () => navigate('/register'),
        customButtons: guestConfig?.enabled
          ? [
              {
                label: 'Continue as Guest (Read-Only)',
                onClick: handleGuestLogin,
                variant: 'secondary',
              },
            ]
          : undefined,
      }}
      themeMode="dark"
    />
  )
}
