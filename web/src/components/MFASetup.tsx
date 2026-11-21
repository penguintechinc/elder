import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Input from '@/components/Input'

interface MFASetupProps {
  isOpen: boolean
  onClose: () => void
  onSuccess?: () => void
}

interface ApiError {
  response?: {
    data?: {
      message?: string
    }
  }
  message?: string
}

interface MFAResponse {
  qr_code?: string
  secret?: string
}

export default function MFASetup({ isOpen, onClose, onSuccess }: MFASetupProps) {
  const [step, setStep] = useState<'setup' | 'verify'>('setup')
  const [qrCode, setQrCode] = useState('')
  const [secret, setSecret] = useState('')
  const [verificationCode, setVerificationCode] = useState('')

  const enableMutation = useMutation({
    mutationFn: () => api.portalMfaEnable(),
    onSuccess: (data: MFAResponse) => {
      setQrCode(data.qr_code || '')
      setSecret(data.secret || '')
      setStep('verify')
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.message || 'Failed to enable MFA')
    },
  })

  const verifyMutation = useMutation({
    mutationFn: (code: string) => api.portalMfaVerify(code),
    onSuccess: () => {
      toast.success('MFA enabled successfully!')
      onSuccess?.()
      onClose()
      setStep('setup')
      setVerificationCode('')
    },
    onError: (error: ApiError) => {
      toast.error(error.response?.data?.message || 'Invalid verification code')
    },
  })

  const handleEnable = () => {
    enableMutation.mutate()
  }

  const handleVerify = (e: React.FormEvent) => {
    e.preventDefault()
    verifyMutation.mutate(verificationCode)
  }

  const handleClose = () => {
    setStep('setup')
    setVerificationCode('')
    onClose()
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 w-full max-w-md">
        <h3 className="text-xl font-semibold text-gray-200 mb-4">
          {step === 'setup' ? 'Enable Two-Factor Authentication' : 'Verify Setup'}
        </h3>

        {step === 'setup' ? (
          <div className="space-y-4">
            <p className="text-gray-400 text-sm">
              Two-factor authentication adds an extra layer of security to your account.
              You'll need an authenticator app like Google Authenticator or Authy.
            </p>
            <div className="flex gap-3 pt-4">
              <Button
                onClick={handleEnable}
                className="flex-1 bg-gradient-to-r from-yellow-600 to-amber-600 hover:from-yellow-500 hover:to-amber-500 text-gray-900 font-semibold"
                isLoading={enableMutation.isPending}
              >
                Get Started
              </Button>
              <Button
                variant="ghost"
                onClick={handleClose}
                className="flex-1"
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-gray-400 text-sm">
              Scan the QR code with your authenticator app, then enter the verification code below.
            </p>

            {qrCode && (
              <div className="flex justify-center p-4 bg-white rounded-lg">
                <img src={qrCode} alt="MFA QR Code" className="w-48 h-48" />
              </div>
            )}

            {secret && (
              <div className="text-center">
                <p className="text-xs text-gray-500 mb-1">Or enter this code manually:</p>
                <code className="text-sm text-yellow-500 bg-gray-800 px-2 py-1 rounded">
                  {secret}
                </code>
              </div>
            )}

            <form onSubmit={handleVerify} className="space-y-4">
              <Input
                label="Verification Code"
                type="text"
                required
                value={verificationCode}
                onChange={(e) => setVerificationCode(e.target.value)}
                placeholder="Enter 6-digit code"
                maxLength={6}
                autoComplete="one-time-code"
              />
              <div className="flex gap-3">
                <Button
                  type="submit"
                  className="flex-1 bg-gradient-to-r from-yellow-600 to-amber-600 hover:from-yellow-500 hover:to-amber-500 text-gray-900 font-semibold"
                  isLoading={verifyMutation.isPending}
                >
                  Verify & Enable
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={handleClose}
                  className="flex-1"
                >
                  Cancel
                </Button>
              </div>
            </form>
          </div>
        )}
      </div>
    </div>
  )
}
