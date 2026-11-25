import { User, Bot, Shield, Building2 } from 'lucide-react'
import type { ComponentType } from 'react'

export interface IdentityTypeOption {
  value: string
  label: string
  icon: ComponentType<{ className?: string }>
  color: string
}

/**
 * Canonical identity-type dropdown options — single source of truth for
 * IAM.tsx and CreateIdentityModal.tsx, which previously hand-duplicated
 * this list (CreateIdentityModal's copy had no icon/color, and neither
 * included customer_contact). Values match apps/api/models/identity.py's
 * IdentityType enum (excluding 'human'/'service_account', which are not
 * offered in this dropdown today and are the only two values the backend's
 * create-identity Pydantic validator currently accepts — see this plan's
 * Global Constraints for that pre-existing gap).
 */
export const IDENTITY_TYPES: IdentityTypeOption[] = [
  { value: 'employee', label: 'Employee', icon: User, color: 'blue' },
  { value: 'vendor', label: 'Vendor', icon: User, color: 'purple' },
  { value: 'bot', label: 'Bot', icon: Bot, color: 'green' },
  { value: 'serviceAccount', label: 'Service Account', icon: Shield, color: 'orange' },
  { value: 'integration', label: 'Integration', icon: Shield, color: 'cyan' },
  { value: 'otherHuman', label: 'Other Human', icon: User, color: 'slate' },
  { value: 'customer_contact', label: 'Customer Contact', icon: Building2, color: 'pink' },
  { value: 'other', label: 'Other', icon: User, color: 'slate' },
]
