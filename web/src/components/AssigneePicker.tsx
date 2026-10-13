import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import SearchableSelect from '@/components/SearchableSelect'
import type { IssueAssigneeType, Identity, Organization } from '@/types'

export interface AssigneeValue {
  assignee_type: IssueAssigneeType
  assignee_id: number
}

interface AssigneePickerProps {
  value?: AssigneeValue | null
  onChange: (value: AssigneeValue | null) => void
  disabled?: boolean
  className?: string
}

/** Encodes a polymorphic assignee as one SearchableSelect option value
 * ("identity:42" / "org_unit:7") so a single combobox can search
 * identities and org units together and resolve back to
 * {assignee_type, assignee_id} on selection — matches the Issue
 * assignee_id/assignee_type contract in
 * apps/api/modules/issues/routes/issues.py::_resolve_assignee_type. */
function encodeKey(type: IssueAssigneeType, id: number): string {
  return `${type}:${id}`
}

function decodeKey(key: string): AssigneeValue | null {
  const [type, idStr] = key.split(':')
  const id = Number(idStr)
  if ((type !== 'identity' && type !== 'org_unit') || Number.isNaN(id)) return null
  return { assignee_type: type, assignee_id: id }
}

/**
 * Single search box that resolves an Issue's polymorphic assignee across
 * both identities and org units (organizations) — replaces the old
 * identity-only <Select> in IssueDetail.tsx and the create/edit issue form,
 * and is reused by the intake-form builder and webhook assignment filters.
 */
export default function AssigneePicker({ value, onChange, disabled, className }: AssigneePickerProps) {
  const { data: identities, isLoading: identitiesLoading } = useQuery({
    queryKey: queryKeys.identities.list({ per_page: 1000 }),
    queryFn: () => api.getIdentities({ per_page: 1000 }),
  })

  const { data: orgUnits, isLoading: orgUnitsLoading } = useQuery({
    queryKey: queryKeys.organizations.dropdown,
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const options = useMemo(() => {
    const identityOptions = (identities?.items || []).map((identity: Identity) => ({
      value: encodeKey('identity', identity.id),
      label: `${identity.full_name || identity.username} (Identity)`,
    }))
    const orgUnitOptions = (orgUnits?.items || []).map((org: Organization) => ({
      value: encodeKey('org_unit', org.id),
      label: `${org.name} (Org Unit)`,
    }))
    return [{ value: '', label: 'Unassigned' }, ...identityOptions, ...orgUnitOptions]
  }, [identities, orgUnits])

  const selectedKey = value ? encodeKey(value.assignee_type, value.assignee_id) : ''

  const handleChange = (selectedValue: string | number) => {
    console.log('[AssigneePicker] Change', { selected: String(selectedValue) })
    if (!selectedValue) {
      onChange(null)
      return
    }
    onChange(decodeKey(String(selectedValue)))
  }

  return (
    <div className={className} data-testid="assignee-picker">
      <SearchableSelect
        options={options}
        value={selectedKey}
        onChange={handleChange}
        isLoading={identitiesLoading || orgUnitsLoading}
        disabled={disabled}
        placeholder="Search identities or org units..."
        ariaLabel="Assignee"
      />
    </div>
  )
}
