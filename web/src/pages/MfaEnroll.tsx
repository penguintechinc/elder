import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import QRCode from 'qrcode'
import api, { type MfaSetupResponse } from '@/lib/api'
import Button from '@/components/Button'
import Input from '@/components/Input'

interface ApiError {
  response?: {
    data?: {
      error?: string
    }
  }
}

/**
 * Standalone MFA enrolment screen for users whose grace period has expired.
 * The server gave them only an enrolment-scoped token, so the normal app
 * shell (profile, navigation, every other API route) is unavailable: this
 * page talks to /mfa/enable and /mfa/confirm and then returns to sign-in.
 */
export default function MfaEnroll() {
  const navigate = useNavigate()
  const [setup, setSetup] = useState<MfaSetupResponse | null>(null)
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    if (!setup?.provisioning_uri) {
      setQrDataUrl(null)
      return
    }
    QRCode.toString(setup.provisioning_uri, {
      type: 'svg',
      width: 220,
      margin: 2,
      errorCorrectionLevel: 'M',
    })
      .then((svg) => {
        if (!cancelled) {
          setQrDataUrl(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`)
        }
      })
      .catch(() => {
        if (!cancelled) setQrDataUrl(null)
      })
    return () => {
      cancelled = true
    }
  }, [setup?.provisioning_uri])

  const backToLogin = async () => {
    await api.logout()
    navigate('/login', { replace: true })
  }

  const handleStart = async () => {
    setError(null)
    setBusy(true)
    try {
      setSetup(await api.enableMfa())
    } catch (err) {
      setError(
        (err as ApiError).response?.data?.error ||
          'Could not start MFA setup. Sign in again and retry.',
      )
    } finally {
      setBusy(false)
    }
  }

  const handleConfirm = async (e: React.FormEvent) => {
    e.preventDefault()
    const cleaned = code.replace(/\s+/g, '')
    if (!/^\d{6}$/.test(cleaned)) {
      setError('Enter the 6-digit code from your authenticator app.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      await api.confirmMfa(cleaned)
      // Enrolled. A real session requires signing in again with password + MFA.
      await backToLogin()
    } catch (err) {
      setError((err as ApiError).response?.data?.error || 'Invalid verification code')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900 px-4">
      <div className="w-full max-w-md space-y-5 bg-slate-800 border border-slate-700 rounded-xl p-6">
        <h1 className="text-xl font-semibold text-white">Set up multi-factor authentication</h1>
        <p className="text-sm text-slate-300" data-testid="mfa-enroll-notice">
          Your organization requires MFA and the grace period has expired. Enroll an
          authenticator app to continue. You will sign in again afterwards.
        </p>

        {error && (
          <p
            role="alert"
            className="text-sm text-red-200 bg-red-500/15 border border-red-500/40 rounded-lg px-3 py-2"
          >
            {error}
          </p>
        )}

        {!setup ? (
          <Button type="button" isLoading={busy} onClick={handleStart}>
            Set up Google Authenticator
          </Button>
        ) : (
          <form onSubmit={handleConfirm} className="space-y-4">
            {qrDataUrl && (
              <img
                src={qrDataUrl}
                alt="Google Authenticator QR Code"
                data-testid="mfa-enroll-qr"
                className="mx-auto rounded bg-white p-2"
                width={220}
                height={220}
              />
            )}
            <code
              data-testid="mfa-enroll-secret"
              className="block px-2 py-1.5 bg-slate-900 border border-slate-700 rounded text-center text-xs font-mono text-slate-200 break-all"
            >
              {setup.secret}
            </code>
            {setup.backup_codes.length > 0 && (
              <div>
                <p className="text-xs font-medium text-emerald-300 mb-2">
                  Backup codes — save these now (shown once)
                </p>
                <div data-testid="mfa-enroll-backup-codes" className="grid grid-cols-2 gap-2">
                  {setup.backup_codes.map((backupCode) => (
                    <code
                      key={backupCode}
                      className="px-2 py-1.5 bg-slate-900 border border-slate-700 rounded text-center text-xs font-mono text-slate-200"
                    >
                      {backupCode}
                    </code>
                  ))}
                </div>
              </div>
            )}
            <Input
              label="6-Digit Verification Code"
              type="text"
              inputMode="numeric"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="123456"
              autoComplete="one-time-code"
              data-testid="mfa-enroll-code"
            />
            <Button type="submit" isLoading={busy}>
              Verify and finish
            </Button>
          </form>
        )}

        <Button type="button" variant="ghost" onClick={backToLogin} disabled={busy}>
          Back to sign in
        </Button>
      </div>
    </div>
  )
}
