import { useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { User, Mail, Building2, Lock, Shield, Copy, CheckCircle } from 'lucide-react'
import QRCode from 'qrcode'
import toast from 'react-hot-toast'
import api, { type MfaSetupResponse } from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'

interface ApiError {
  response?: {
    data?: {
      error?: string
    }
  }
  message?: string
}

export default function Profile() {
  const [isEditing, setIsEditing] = useState(false)
  const [isChangingPassword, setIsChangingPassword] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [mfaSetup, setMfaSetup] = useState<MfaSetupResponse | null>(null)
  const [mfaQrDataUrl, setMfaQrDataUrl] = useState<string | null>(null)
  const [totpCode, setTotpCode] = useState('')
  const queryClient = useQueryClient()

  useEffect(() => {
    let cancelled = false
    if (!mfaSetup?.provisioning_uri) {
      setMfaQrDataUrl(null)
      return
    }
    QRCode.toString(mfaSetup.provisioning_uri, {
      type: 'svg',
      width: 220,
      margin: 2,
      errorCorrectionLevel: 'M',
    })
      .then((svg) => {
        if (!cancelled) {
          setMfaQrDataUrl(
            `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`,
          )
        }
      })
      .catch(() => {
        if (!cancelled) setMfaQrDataUrl(null)
      })
    return () => {
      cancelled = true
    }
  }, [mfaSetup?.provisioning_uri])

  const { data: profile, isLoading: profileLoading } = useQuery({
    queryKey: ['profile'],
    queryFn: () => api.getProfile(),
  })

  const { data: orgsData } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const updateMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => api.updateProfile(data as Parameters<typeof api.updateProfile>[0]),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['profile'],
        refetchType: 'all'
      })
      toast.success('Profile updated successfully')
      setIsEditing(false)
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to update profile')
    },
  })

  const changePasswordMutation = useMutation({
    mutationFn: (data: { current_password: string; new_password: string }) =>
      api.changePassword(data.current_password, data.new_password),
    onSuccess: () => {
      toast.success('Password changed successfully')
      setIsChangingPassword(false)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to change password')
    },
  })

  const enableMfaMutation = useMutation({
    mutationFn: () => api.enableMfa(),
    onSuccess: async (data) => {
      setMfaSetup(data)
      setTotpCode('')
      await queryClient.invalidateQueries({ queryKey: ['profile'] })
      await queryClient.invalidateQueries({ queryKey: ['portal-profile'] })
      toast.success('Google Authenticator secret generated. Enter a code to verify.')
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to start MFA setup')
    },
  })

  const confirmMfaMutation = useMutation({
    mutationFn: (code: string) => api.confirmMfa(code),
    onSuccess: async () => {
      setMfaSetup(null)
      setTotpCode('')
      await queryClient.invalidateQueries({ queryKey: ['profile'] })
      await queryClient.invalidateQueries({ queryKey: ['portal-profile'] })
      await queryClient.invalidateQueries({ queryKey: ['mfa-policy'] })
      toast.success('Google Authenticator verified and enabled')
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Invalid verification code')
    },
  })

  const disableMfaMutation = useMutation({
    mutationFn: () => api.disableMfa(),
    onSuccess: async () => {
      setMfaSetup(null)
      setTotpCode('')
      await queryClient.invalidateQueries({ queryKey: ['profile'] })
      await queryClient.invalidateQueries({ queryKey: ['portal-profile'] })
      await queryClient.invalidateQueries({ queryKey: ['mfa-policy'] })
      toast.success('Two-factor authentication disabled')
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.error || 'Failed to disable MFA')
    },
  })

  const handleCopySecret = async () => {
    if (!mfaSetup?.secret) return
    try {
      await navigator.clipboard.writeText(mfaSetup.secret)
      toast.success('Secret copied to clipboard')
    } catch {
      toast.error('Failed to copy secret')
    }
  }

  const handleVerifyMfa = (e: React.FormEvent) => {
    e.preventDefault()
    const cleaned = totpCode.trim()
    if (!cleaned) {
      toast.error('Please enter the 6-digit verification code')
      return
    }
    confirmMfaMutation.mutate(cleaned)
  }

  const [formData, setFormData] = useState({
    email: '',
    full_name: '',
    organization_id: '',
  })

  // Initialize form data when profile loads
  useMemo(() => {
    if (profile && isEditing) {
      setFormData({
        email: profile.email || '',
        full_name: profile.full_name || '',
        organization_id: profile.organization_id?.toString() || '',
      })
    }
  }, [profile, isEditing])

  const handleEdit = () => {
    setIsEditing(true)
    setFormData({
      email: profile?.email || '',
      full_name: profile?.full_name || '',
      organization_id: profile?.organization_id?.toString() || '',
    })
  }

  const handleCancel = () => {
    setIsEditing(false)
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    updateMutation.mutate({
      ...formData,
      organization_id: formData.organization_id ? parseInt(formData.organization_id) : null,
    })
  }

  const handlePasswordSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (newPassword !== confirmPassword) {
      toast.error('New passwords do not match')
      return
    }
    if (newPassword.length < 8) {
      toast.error('New password must be at least 8 characters')
      return
    }
    changePasswordMutation.mutate({
      current_password: currentPassword,
      new_password: newPassword,
    })
  }

  if (profileLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="p-8 max-w-4xl">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-white">Profile</h1>
        <p className="mt-2 text-slate-400">
          Manage your account settings and preferences
        </p>
      </div>

      {/* Profile Information Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-full bg-primary-500/20 flex items-center justify-center">
                <User className="w-6 h-6 text-primary-400" />
              </div>
              <div>
                <h2 className="text-xl font-semibold text-white">Profile Information</h2>
                <p className="text-sm text-slate-400">Your personal details and organization</p>
              </div>
            </div>
            {!isEditing && (
              <Button onClick={handleEdit} variant="ghost">
                Edit Profile
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {isEditing ? (
            <form onSubmit={handleSubmit} className="space-y-6">
              {/* Username (read-only) */}
              <div>
                <span className="block text-sm font-medium text-slate-300 mb-2">
                  Username
                </span>
                <div className="flex items-center gap-2 px-4 py-2 bg-slate-800/50 border border-slate-700 rounded-lg text-slate-400">
                  <User className="w-4 h-4" />
                  <span>{profile?.username}</span>
                  <span className="ml-auto text-xs text-slate-500">Read-only</span>
                </div>
              </div>

              <Input
                label="Email"
                type="email"
                value={formData.email}
                onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                placeholder="your.email@example.com"
              />

              <Input
                label="Full Name"
                type="text"
                value={formData.full_name}
                onChange={(e) => setFormData({ ...formData, full_name: e.target.value })}
                placeholder="John Doe"
              />

              <div>
                <label
                  htmlFor="profile-organization"
                  className="block text-sm font-medium text-slate-300 mb-2"
                >
                  Organization
                </label>
                <select
                  id="profile-organization"
                  className="w-full px-4 py-2 bg-slate-800 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-primary-500"
                  value={formData.organization_id}
                  onChange={(e) => setFormData({ ...formData, organization_id: e.target.value })}
                >
                  <option value="">No Organization</option>
                  {orgsData?.items?.map((org) => (
                    <option key={org.id} value={org.id.toString()}>
                      {org.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex gap-3 pt-4">
                <Button
                  type="submit"
                  isLoading={updateMutation.isPending}
                >
                  Save Changes
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={handleCancel}
                  disabled={updateMutation.isPending}
                >
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <div className="space-y-6">
              {/* Username */}
              <div>
                <span className="block text-sm font-medium text-slate-400 mb-2">
                  Username
                </span>
                <div className="flex items-center gap-2 text-white">
                  <User className="w-4 h-4 text-slate-400" />
                  <span>{profile?.username}</span>
                </div>
              </div>

              {/* Email */}
              <div>
                <span className="block text-sm font-medium text-slate-400 mb-2">
                  Email
                </span>
                <div className="flex items-center gap-2 text-white">
                  <Mail className="w-4 h-4 text-slate-400" />
                  <span>{profile?.email || 'Not set'}</span>
                </div>
              </div>

              {/* Full Name */}
              <div>
                <span className="block text-sm font-medium text-slate-400 mb-2">
                  Full Name
                </span>
                <div className="flex items-center gap-2 text-white">
                  <User className="w-4 h-4 text-slate-400" />
                  <span>{profile?.full_name || 'Not set'}</span>
                </div>
              </div>

              {/* Organization */}
              <div>
                <span className="block text-sm font-medium text-slate-400 mb-2">
                  Organization
                </span>
                <div className="flex items-center gap-2 text-white">
                  <Building2 className="w-4 h-4 text-slate-400" />
                  <span>{profile?.organization_name || 'No organization assigned'}</span>
                </div>
              </div>

              {/* Additional Info */}
              <div className="grid grid-cols-2 gap-6 pt-6 border-t border-slate-700">
                <div>
                  <span className="block text-sm font-medium text-slate-400 mb-2">
                    Identity Type
                  </span>
                  <div className="inline-flex px-3 py-1 rounded-full text-sm font-medium bg-blue-500/20 text-blue-400">
                    {profile?.identity_type}
                  </div>
                </div>
                <div>
                  <span className="block text-sm font-medium text-slate-400 mb-2">
                    Authentication Provider
                  </span>
                  <div className="text-white">
                    {profile?.auth_provider}
                  </div>
                </div>
                <div>
                  <span className="block text-sm font-medium text-slate-400 mb-2">
                    Account Status
                  </span>
                  <div className={`inline-flex px-3 py-1 rounded-full text-sm font-medium ${
                    profile?.is_active
                      ? 'bg-green-500/20 text-green-400'
                      : 'bg-red-500/20 text-red-400'
                  }`}>
                    {profile?.is_active ? 'Active' : 'Inactive'}
                  </div>
                </div>
                <div>
                  <span className="block text-sm font-medium text-slate-400 mb-2">
                    MFA Status
                  </span>
                  <div className={`inline-flex px-3 py-1 rounded-full text-sm font-medium ${
                    profile?.mfa_enabled
                      ? 'bg-green-500/20 text-green-400'
                      : 'bg-slate-500/20 text-slate-400'
                  }`}>
                    {profile?.mfa_enabled ? 'Enabled' : 'Disabled'}
                  </div>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Two-Factor Authentication Card */}
      <Card className="mt-6">
        <CardHeader>
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-full bg-emerald-500/20 flex items-center justify-center">
                <Shield className="w-6 h-6 text-emerald-400" />
              </div>
              <div>
                <h2 className="text-xl font-semibold text-white">
                  Two-Factor Authentication (Google Authenticator)
                </h2>
                <p className="text-sm text-slate-400">
                  Protect your account with Google Authenticator (TOTP) or a Passkey
                </p>
              </div>
            </div>
            {profile?.mfa_enabled && !mfaSetup && (
              <span
                data-testid="profile-mfa-enabled-badge"
                className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-medium bg-emerald-500/20 text-emerald-300"
              >
                <CheckCircle className="w-4 h-4" />
                Enabled (Google Authenticator)
              </span>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {mfaSetup ? (
            <div className="space-y-5">
              <p className="text-sm text-slate-300">
                Scan this QR code with Google Authenticator (or any TOTP authenticator app),
                or enter the Base32 secret key manually, then enter a 6-digit code below to
                verify your setup.
              </p>

              <div className="flex flex-col sm:flex-row gap-6 items-start">
                <div className="flex-shrink-0 flex flex-col items-center">
                  {mfaQrDataUrl ? (
                    <img
                      src={mfaQrDataUrl}
                      alt="Google Authenticator QR Code"
                      width={220}
                      height={220}
                      data-testid="mfa-qr-code"
                      className="rounded-lg bg-white p-2 border border-slate-700"
                    />
                  ) : (
                    <div
                      data-testid="mfa-qr-loading"
                      className="w-[220px] h-[220px] rounded-lg bg-slate-800 border border-slate-700 flex items-center justify-center text-xs text-slate-400"
                    >
                      Generating QR code...
                    </div>
                  )}
                  <span className="mt-2 text-xs text-slate-400">
                    Scan with Google Authenticator
                  </span>
                </div>

                <div className="flex-1 w-full space-y-4">
                  <div>
                    <span className="block text-sm font-medium text-slate-300 mb-1">
                      Manual Setup Key (Base32)
                    </span>
                    <div className="flex items-center gap-2">
                      <code
                        data-testid="mfa-setup-secret"
                        className="flex-1 px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-emerald-300 font-mono text-sm break-all"
                      >
                        {mfaSetup.secret}
                      </code>
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={handleCopySecret}
                        aria-label="Copy Base32 secret"
                      >
                        <Copy className="w-4 h-4 mr-1" />
                        Copy
                      </Button>
                    </div>
                  </div>

                  <div>
                    <span className="block text-sm font-medium text-slate-300 mb-1">
                      Provisioning URI (otpauth://)
                    </span>
                    <code
                      data-testid="mfa-setup-uri"
                      className="block px-3 py-2 bg-slate-800 border border-slate-700 rounded-lg text-slate-300 font-mono text-xs break-all"
                    >
                      {mfaSetup.provisioning_uri}
                    </code>
                  </div>
                </div>
              </div>

              {Array.isArray(mfaSetup.backup_codes) &&
                mfaSetup.backup_codes.length > 0 && (
                  <div>
                    <span className="block text-sm font-medium text-slate-300 mb-2">
                      Backup Codes (save these in a safe place)
                    </span>
                    <div
                      data-testid="mfa-backup-codes"
                      className="grid grid-cols-2 sm:grid-cols-5 gap-2"
                    >
                      {mfaSetup.backup_codes.map((code) => (
                        <code
                          key={code}
                          className="px-2 py-1.5 bg-slate-800 border border-slate-700 rounded text-center text-xs font-mono text-slate-200"
                        >
                          {code}
                        </code>
                      ))}
                    </div>
                  </div>
                )}

              <form onSubmit={handleVerifyMfa} className="space-y-4 pt-2 border-t border-slate-700">
                <Input
                  label="6-Digit Verification Code"
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  required
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="123456"
                  autoComplete="one-time-code"
                />
                <div className="flex gap-3">
                  <Button
                    type="submit"
                    isLoading={confirmMfaMutation.isPending}
                  >
                    Verify Code
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      setMfaSetup(null)
                      setTotpCode('')
                    }}
                    disabled={confirmMfaMutation.isPending}
                  >
                    Close
                  </Button>
                </div>
              </form>
            </div>
          ) : profile?.mfa_enabled ? (
            <div className="space-y-4">
              <p className="text-sm text-slate-300">
                Two-factor authentication is active on your account via Google
                Authenticator (TOTP).
              </p>
              {profile?.mfa_enforced && (
                <p
                  data-testid="profile-mfa-enforced-notice"
                  className="text-sm text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2"
                >
                  MFA is enforced by your organization&apos;s Enterprise security
                  policy and cannot be disabled.
                </p>
              )}
              <div>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={Boolean(profile?.mfa_enforced) || disableMfaMutation.isPending}
                  isLoading={disableMfaMutation.isPending}
                  onClick={() => disableMfaMutation.mutate()}
                >
                  Disable MFA
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {profile?.mfa_setup_required && (
                <p
                  data-testid="profile-mfa-required-notice"
                  className={`text-sm rounded-lg px-3 py-2 border ${
                    profile.mfa_locked_out
                      ? 'text-red-200 bg-red-500/15 border-red-500/40'
                      : 'text-amber-200 bg-amber-500/15 border-amber-500/30'
                  }`}
                >
                  {profile.mfa_locked_out
                    ? 'Your MFA grace period has expired. You must set up Google Authenticator to continue using Elder.'
                    : `Your organization requires Google Authenticator (TOTP) or Passkey MFA. Set up MFA within ${profile.mfa_grace_days_remaining ?? 0} day(s) to avoid lockout.`}
                </p>
              )}
              <p className="text-slate-400">
                Enroll Google Authenticator (TOTP) to add a second layer of security
                to your sign-in.
              </p>
              <Button
                type="button"
                isLoading={enableMfaMutation.isPending}
                onClick={() => enableMfaMutation.mutate()}
              >
                Set Up Google Authenticator
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Password Change Card */}
      <Card className="mt-6">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-full bg-orange-500/20 flex items-center justify-center">
                <Lock className="w-6 h-6 text-orange-400" />
              </div>
              <div>
                <h2 className="text-xl font-semibold text-white">Change Password</h2>
                <p className="text-sm text-slate-400">Update your account password</p>
              </div>
            </div>
            {!isChangingPassword && (
              <Button onClick={() => setIsChangingPassword(true)} variant="ghost">
                Change Password
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {isChangingPassword ? (
            <form onSubmit={handlePasswordSubmit} className="space-y-4">
              <Input
                label="Current Password"
                type="password"
                required
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                placeholder="Enter your current password"
                autoComplete="current-password"
              />
              <Input
                label="New Password"
                type="password"
                required
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="Enter new password (min 8 characters)"
                autoComplete="new-password"
              />
              <Input
                label="Confirm New Password"
                type="password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Confirm new password"
                autoComplete="new-password"
              />
              <div className="flex gap-3 pt-4">
                <Button
                  type="submit"
                  isLoading={changePasswordMutation.isPending}
                >
                  Update Password
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setIsChangingPassword(false)
                    setCurrentPassword('')
                    setNewPassword('')
                    setConfirmPassword('')
                  }}
                  disabled={changePasswordMutation.isPending}
                >
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <p className="text-slate-400">
              Click "Change Password" to update your account password.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
