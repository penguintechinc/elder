import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { User, Mail, Building2, Lock } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import { FormBuilder, FieldConfig as FormField } from '@penguin/react_libs/components/FormBuilder'

export default function Profile() {
  const [isEditing, setIsEditing] = useState(false)
  const [isChangingPassword, setIsChangingPassword] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const queryClient = useQueryClient()

  const { data: profile, isLoading: profileLoading } = useQuery({
    queryKey: ['profile'],
    queryFn: () => api.getProfile(),
  })

  const { data: orgsData } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const updateMutation = useMutation({
    mutationFn: (data: Record<string, any>) => api.updateProfile(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['profile'],
        refetchType: 'all'
      })
      toast.success('Profile updated successfully')
      setIsEditing(false)
    },
    onError: (error: any) => {
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
    onError: (error: any) => {
      toast.error(error.response?.data?.error || 'Failed to change password')
    },
  })

  const profileFields: FormField[] = useMemo(() => [
    {
      name: 'email',
      label: 'Email',
      type: 'email',
      placeholder: 'your.email@example.com',
    },
    {
      name: 'full_name',
      label: 'Full Name',
      type: 'text',
      placeholder: 'John Doe',
    },
    {
      name: 'organization_id',
      label: 'Organization',
      type: 'select',
      options: [
        { value: '', label: 'No Organization' },
        ...(orgsData?.items?.map((org: any) => ({
          value: org.id.toString(),
          label: org.name,
        })) || []),
      ],
    },
  ], [orgsData])

  const handleEdit = () => {
    setIsEditing(true)
  }

  const handleCancel = () => {
    setIsEditing(false)
  }

  const handleSubmit = (data: Record<string, any>) => {
    updateMutation.mutate({
      ...data,
      organization_id: data.organization_id ? parseInt(data.organization_id) : null,
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

  const initialValues = useMemo(() => {
    if (!profile) return {}
    return {
      email: profile.email || '',
      full_name: profile.full_name || '',
      organization_id: profile.organization_id?.toString() || '',
    }
  }, [profile])

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
            <div className="space-y-6">
              {/* Username (read-only) */}
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-2">
                  Username
                </label>
                <div className="flex items-center gap-2 px-4 py-2 bg-slate-800/50 border border-slate-700 rounded-lg text-slate-400">
                  <User className="w-4 h-4" />
                  <span>{profile?.username}</span>
                  <span className="ml-auto text-xs text-slate-500">Read-only</span>
                </div>
              </div>

              <FormBuilder
                mode="inline"
                fields={profileFields}
                initialData={initialValues}
                onSubmit={handleSubmit}
                onCancel={handleCancel}
                loading={updateMutation.isPending}
                submitLabel="Save Changes"
                cancelLabel="Cancel"
              />
            </div>
          ) : (
            <div className="space-y-6">
              {/* Username */}
              <div>
                <label className="block text-sm font-medium text-slate-400 mb-2">
                  Username
                </label>
                <div className="flex items-center gap-2 text-white">
                  <User className="w-4 h-4 text-slate-400" />
                  <span>{profile?.username}</span>
                </div>
              </div>

              {/* Email */}
              <div>
                <label className="block text-sm font-medium text-slate-400 mb-2">
                  Email
                </label>
                <div className="flex items-center gap-2 text-white">
                  <Mail className="w-4 h-4 text-slate-400" />
                  <span>{profile?.email || 'Not set'}</span>
                </div>
              </div>

              {/* Full Name */}
              <div>
                <label className="block text-sm font-medium text-slate-400 mb-2">
                  Full Name
                </label>
                <div className="flex items-center gap-2 text-white">
                  <User className="w-4 h-4 text-slate-400" />
                  <span>{profile?.full_name || 'Not set'}</span>
                </div>
              </div>

              {/* Organization */}
              <div>
                <label className="block text-sm font-medium text-slate-400 mb-2">
                  Organization
                </label>
                <div className="flex items-center gap-2 text-white">
                  <Building2 className="w-4 h-4 text-slate-400" />
                  <span>{profile?.organization_name || 'No organization assigned'}</span>
                </div>
              </div>

              {/* Additional Info */}
              <div className="grid grid-cols-2 gap-6 pt-6 border-t border-slate-700">
                <div>
                  <label className="block text-sm font-medium text-slate-400 mb-2">
                    Identity Type
                  </label>
                  <div className="inline-flex px-3 py-1 rounded-full text-sm font-medium bg-blue-500/20 text-blue-400">
                    {profile?.identity_type}
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-400 mb-2">
                    Authentication Provider
                  </label>
                  <div className="text-white">
                    {profile?.auth_provider}
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-400 mb-2">
                    Account Status
                  </label>
                  <div className={`inline-flex px-3 py-1 rounded-full text-sm font-medium ${
                    profile?.is_active
                      ? 'bg-green-500/20 text-green-400'
                      : 'bg-red-500/20 text-red-400'
                  }`}>
                    {profile?.is_active ? 'Active' : 'Inactive'}
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-400 mb-2">
                    MFA Status
                  </label>
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
