import { useNavigate } from 'react-router-dom'
import { LoginPageBuilder } from '@penguintechinc/react-libs/components'
import type { LoginResponse } from '@penguintechinc/react-libs/components'
import { markAuthenticated } from '@/lib/api'

export default function Login() {
  const navigate = useNavigate()

  const handleSuccess = (response: LoginResponse) => {
    // The access/refresh JWT arrive as HttpOnly cookies on this same
    // response (gh security audit) -- nothing to persist here beyond the
    // non-sensitive "logged in" flag used by route guards.
    if (response.token) {
      markAuthenticated()
    }
    navigate('/')
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
        loginUrl: '/api/v1/portal-auth/login',
      }}
      tenantField={{
        show: true,
        label: 'Tenant',
        placeholder: 'Global',
        helpText: 'Leave as "Global" for system-wide access',
        defaultValue: 'Global',
      }}
      onSuccess={handleSuccess}
      themeMode="dark"
    />
  )
}
