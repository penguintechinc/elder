import { useNavigate } from 'react-router-dom'
import { LoginPageBuilder, ELDER_LOGIN_THEME } from '@penguintechinc/react-libs/components'
import type { LoginResponse } from '@penguintechinc/react-libs/components'

export default function LoginPageWrapper() {
  const navigate = useNavigate()

  return (
    <LoginPageBuilder
      api={{
        loginUrl: '/api/v1/portal-auth/login',
      }}
      branding={{
        appName: 'Elder',
        logo: '/elder-logo.png',
        logoHeight: 128,
        tagline: 'Entity Relationship Tracking System',
        githubRepo: 'penguintechinc/elder',
      }}
      colors={ELDER_LOGIN_THEME}
      onSuccess={(response: LoginResponse) => {
        if (response.token) {
          localStorage.setItem('elder_token', response.token)
        }
        navigate('/')
      }}
      onError={(error: Error) => {
        console.error('Login failed:', error.message)
      }}
      showForgotPassword={true}
      forgotPasswordUrl="/forgot-password"
      showSignUp={true}
      signUpUrl="/register"
      showRememberMe={true}
      captcha={{
        enabled: true,
        provider: 'altcha',
        challengeUrl: '/api/v1/auth/captcha-challenge',
        failedAttemptsThreshold: 3,
      }}
      mfa={{
        enabled: true,
        codeLength: 6,
        allowRememberDevice: true,
      }}
      gdpr={{
        enabled: true,
        privacyPolicyUrl: 'https://penguintech.io/privacy',
      }}
    />
  )
}
