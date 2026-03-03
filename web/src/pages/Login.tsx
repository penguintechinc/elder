import { useNavigate } from 'react-router-dom'
import { LoginPageBuilder } from '@penguintechinc/react-libs/components'
import type { LoginResponse } from '@penguintechinc/react-libs/components'

export default function Login() {
  const navigate = useNavigate()

  const handleSuccess = (response: LoginResponse) => {
    if (response.token) {
      localStorage.setItem('elder_token', response.token)
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
