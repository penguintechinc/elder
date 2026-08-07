# Unified Issues UI + CRM Entity Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Elder React frontend a single unified Issues surface (list/detail/create) that covers every `issue_type` including `support`, driven by a combined identity+org-unit assignee search box, plus the supporting admin UI (intake-form builder, webhook assignment filters) and CRM dropdown values (`customer_company`, `customer_contact`) the backend (Plans 01-05) already accepts.

**Architecture:** The backend is fully merged — `Issue.assignee_id`/`assignee_type` (polymorphic identity/org_unit), `issue_type` (incl. `support` + support-only columns), `/api/v1/intake-forms` (admin CRUD) + `/api/v1/intake/<slug>` (public), and `Webhook.filter_issue_type`/`filter_assignee_type`/`filter_assignee_id` all exist today. This plan is frontend-only: extend the typed API client, build one new shared `AssigneePicker` component on top of the existing `SearchableSelect` primitive, wire it into the existing Issues list/detail/create surfaces (additive — `/helpdesk` is not touched or retired here), then layer the admin-only intake-form builder, webhook filter UI, and CRM dropdown values on top.

**Tech Stack:** React 18 + TypeScript (strict) + TailwindCSS v4 + TanStack Query 5 + `@penguintechinc/react-libs` 1.3.4 (`FormModalBuilder`, `AppConsoleVersion`) + Vitest 3 + Testing Library + Playwright 1.59.

## Global Constraints

- React 18 functional components + hooks only, TypeScript strict (`tsc --noEmit` must pass) — no class components.
- TailwindCSS v4 only — slash-opacity syntax (`bg-slate-900/50`, never `bg-opacity-*`), no v3 patterns. See `styling-with-tailwind-v4` skill if anything looks purged/missing.
- Exact npm versions already pinned in `web/package.json` (react 18.3.1, react-router-dom 6.30.3, `@tanstack/react-query` 5.62.2, `@penguintechinc/react-libs` 1.3.4, tailwindcss 4.1.17, `@playwright/test` 1.59.1, vitest 3.2.4) — do not add new dependencies for anything this plan needs; everything is buildable from what's already installed.
- Reuse `FormModalBuilder`/`FormField` from `@penguintechinc/react-libs/components` wherever a field list is flat and declarative. Where it genuinely cannot express the UI (a live search combobox, or a nested repeating field-list builder — confirmed by reading `@penguintechinc/react-libs`'s shipped `.d.ts`, no `custom`/`render` field type exists), fall back to the **same hand-rolled `<Card>`-shell modal pattern already used twice in this codebase** (`Webhooks.tsx`'s `CreateWebhookModal`, `OrganizationDetail.tsx`'s `EditOrganizationModal`) rather than inventing a new modal primitive. Never hand-roll login/sidebar/banner components.
- Reuse existing components: `SearchableSelect`, `Button`, `Card`/`CardHeader`/`CardContent`, `Select`, `Input`.
- All API calls go through the singleton `api` client (`web/src/lib/api.ts`) + TanStack Query (`queryKeys` factory, `invalidateCache`) — never scattered `fetch`/`axios`.
- Console logging: sanitized `[ComponentName] Action {data}` format only — never log tokens/PII/full emails.
- Role-based rendering (Admin/Maintainer/Viewer) for admin-only controls (intake-form builder, webhook config) — reuse the existing `api.getPortalProfile()` → `profile.global_role === 'admin' || profile.tenant_role === 'admin'` pattern from `web/src/pages/ModuleToggles.tsx:38-45`.
- `data-testid` on every interactive element referenced by a Playwright test. Keyboard-navigate the assignee combobox (already built into `SearchableSelect`'s ArrowUp/ArrowDown/Enter/Escape handling); add `aria-label`/`role="combobox"` per the Accessibility rule.
- Tests: Vitest + Testing Library unit test per new/changed component or page, Playwright smoke test for the unified list + create-with-assignee-picker flow, module manifest test for `issues` module. Coverage per repo's existing Vitest config (`web/vitest.config.ts`).
- File size: keep new components under ~5000 characters; split if they grow past it.
- **Known backend gaps found during recon — flagged, not silently worked around:**
  - `GET /api/v1/issues` (`apps/api/modules/issues/routes/issues.py::list_issues`) has no server-side `search` or `issue_type` query filter (only `status`/`priority`/`assignee_id`/`reporter_id`), and `organization_id`/`entity_id` are explicitly commented out as "removed" — the existing `search`/`organization_id`/`entity_id` params `Issues.tsx` already sends are silently ignored today. This plan adds client-side `issue_type` + `search` filtering as a pragmatic fix for the two params it introduces/depends on; the `organization_id`/`entity_id` gap is pre-existing and out of scope here.
  - `apps/api/models/pydantic/identity.py`'s `CreateIdentityRequest.identity_type` is `Literal["human", "service_account"]` — every other value the identity-type dropdown already offers (`employee`, `vendor`, `bot`, `serviceAccount`, `integration`, `otherHuman`, `other`, and the new `customer_contact`) is accepted by the DB model (`apps/api/models/identity.py::IdentityType`) but **rejected by this Pydantic validator with a 422**. This is a pre-existing, currently-live backend bug this frontend-only plan cannot fix; Task 9 wires the dropdown correctly on the assumption a backend fix lands separately.
  - `PATCH /api/v1/issues/<id>::update_issue` never clears `assignee_id` once set (only sets a new one) — Task 5 surfaces this instead of silently no-op-ing.
  - `POST /api/v1/issues::create_issue` requires `organization_id` (`CreateIssueRequest.organization_id: int = Field(..., ge=1)`) and has no `entity_ids`/`label_ids` fields — the current `CreateIssueModal`'s "assign to entity instead of org" path and its direct `entity_ids`/`label_ids` POST body are both silently broken today (422 / silently-dropped fields). Task 6 fixes this by making `organization_id` always-required and linking entities/labels via the existing per-item `linkIssueEntity`/`addIssueLabel` endpoints after creation.
  - The current `api.updateIssue()` sends `PUT /issues/:id`, but the backend only registers `PATCH` for that route (`apps/api/modules/issues/routes/issues.py:457`) — every issue update in the shipped app currently 405s. Task 2 fixes the verb since Task 5's assignee picker depends on updates actually working.

---

## File Structure

```
web/src/
├── types/index.ts                          [MODIFY] Task 1 — IssueType, IssueAssigneeType, OrganizationType+customer_company, Identity.identity_type, Issue fields
├── lib/
│   ├── api.ts                               [MODIFY] Task 2 — issue_type/assignee_type on issues, intake-forms CRUD, webhook filters, PUT→PATCH fix
│   ├── api.test.ts                          [CREATE] Task 2
│   └── constants/
│       ├── issueTypes.ts                    [CREATE] Task 1 — ISSUE_TYPES, SUPPORT_ISSUE_TYPE, issueTypeLabel()
│       ├── issueTypes.test.ts               [CREATE] Task 1
│       ├── identityTypes.ts                 [CREATE] Task 1 — shared IDENTITY_TYPES (dedup IAM.tsx + CreateIdentityModal.tsx)
│       ├── identityTypes.test.ts            [CREATE] Task 1
│       ├── organizationTypes.ts             [CREATE] Task 9 — shared ORGANIZATION_TYPES (dedup Organizations.tsx + OrganizationDetail.tsx)
│       └── organizationTypes.test.ts        [CREATE] Task 9
├── components/
│   ├── SearchableSelect.tsx                 [MODIFY] Task 3 — add aria-label/role="combobox"
│   ├── SearchableSelect.test.tsx            [CREATE] Task 3
│   ├── AssigneePicker.tsx                   [CREATE] Task 3 — the combined identity+org-unit search box
│   ├── AssigneePicker.test.tsx              [CREATE] Task 3
│   └── CreateIdentityModal.tsx              [MODIFY] Task 9 — use shared IDENTITY_TYPES
├── pages/
│   ├── Issues.tsx                           [MODIFY] Task 4 (list+filter+badge), Task 6 (CreateIssueModal rebuild)
│   ├── Issues.test.tsx                      [CREATE] Task 4 + Task 6
│   ├── IssueDetail.tsx                      [MODIFY] Task 5 — AssigneePicker swap, support section
│   ├── IssueDetail.test.tsx                 [CREATE] Task 5
│   ├── IntakeForms.tsx                      [CREATE] Task 7 — admin builder
│   ├── IntakeForms.test.tsx                 [CREATE] Task 7
│   ├── IntakePublicForm.tsx                 [CREATE] Task 7 — public submit stub
│   ├── IntakePublicForm.test.tsx            [CREATE] Task 7
│   ├── Webhooks.tsx                         [MODIFY] Task 8 — filter fields + new edit modal
│   ├── Webhooks.test.tsx                    [CREATE] Task 8
│   ├── Organizations.tsx                    [MODIFY] Task 9 — organization_type field
│   ├── Organizations.test.tsx               [CREATE] Task 9
│   └── OrganizationDetail.tsx               [MODIFY] Task 9 — use shared ORGANIZATION_TYPES
├── pages/IAM.tsx                            [MODIFY] Task 9 — use shared IDENTITY_TYPES
├── modules/issues/index.tsx                 [MODIFY] Task 7 — intake-forms route + adminNav
├── modules/issues/__tests__/module.test.ts  [CREATE] Task 7
└── App.tsx                                  [MODIFY] Task 7 — public /intake/:slug route

web/tests/e2e/
└── issues-unified.spec.ts                   [CREATE] Task 6 — Playwright smoke
```

---

## Task 1: Types + shared constants

**Files:**
- Modify: `web/src/types/index.ts`
- Create: `web/src/lib/constants/issueTypes.ts`
- Create: `web/src/lib/constants/issueTypes.test.ts`
- Create: `web/src/lib/constants/identityTypes.ts`
- Create: `web/src/lib/constants/identityTypes.test.ts`

**Interfaces:**
- Produces: `IssueType` (union of 11 lowercase strings incl. `'support'`), `IssueAssigneeType = 'identity' | 'org_unit'`, `OrganizationType` (+`'customer_company'`), extended `Issue` interface (`issue_type`, `assignee_type`, `reporter_id`, `resource_type`, `resource_id`, `channel`, `category`, `requester_contact_id`, `hd_sla_policy_id`, `sla_breach_at`, `first_response_at`, `resolved_at`, `parent_issue_id`), extended `Identity.identity_type` union, `ISSUE_TYPES: IssueTypeOption[]`, `SUPPORT_ISSUE_TYPE`, `issueTypeLabel(value: string): string`, `IDENTITY_TYPES: IdentityTypeOption[]`. Every later task imports from these two constants files and the extended types.

- [ ] **Step 1: Write failing tests for the two new constants files**

Create `web/src/lib/constants/issueTypes.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { ISSUE_TYPES, SUPPORT_ISSUE_TYPE, issueTypeLabel } from './issueTypes'

describe('issueTypes constants', () => {
  it('includes all 11 backend IssueType enum values, support last-but-one before other', () => {
    const values = ISSUE_TYPES.map((t) => t.value)
    expect(values).toEqual([
      'operations', 'code', 'config', 'security', 'architecture',
      'process', 'approval', 'feature', 'bug', 'support', 'other',
    ])
  })

  it('SUPPORT_ISSUE_TYPE matches the support entry', () => {
    expect(SUPPORT_ISSUE_TYPE).toBe('support')
    expect(ISSUE_TYPES.some((t) => t.value === SUPPORT_ISSUE_TYPE)).toBe(true)
  })

  it('issueTypeLabel resolves a known value and falls back to the raw value otherwise', () => {
    expect(issueTypeLabel('support')).toBe('Support')
    expect(issueTypeLabel('made-up')).toBe('made-up')
  })
})
```

Create `web/src/lib/constants/identityTypes.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { IDENTITY_TYPES } from './identityTypes'

describe('identityTypes constants', () => {
  it('includes customer_contact alongside the existing platform identity types', () => {
    const values = IDENTITY_TYPES.map((t) => t.value)
    expect(values).toEqual([
      'employee', 'vendor', 'bot', 'serviceAccount', 'integration',
      'otherHuman', 'customer_contact', 'other',
    ])
  })

  it('every option has a label, icon component, and color', () => {
    for (const option of IDENTITY_TYPES) {
      expect(option.label.length).toBeGreaterThan(0)
      expect(option.icon).toBeDefined()
      expect(option.color.length).toBeGreaterThan(0)
    }
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run src/lib/constants/issueTypes.test.ts src/lib/constants/identityTypes.test.ts`
Expected: FAIL — `Cannot find module './issueTypes'` / `'./identityTypes'`

- [ ] **Step 3: Extend `web/src/types/index.ts`**

Add near the top, after the existing `OrganizationType` line (`web/src/types/index.ts:15`):

```ts
export type OrganizationType = 'department' | 'organization' | 'team' | 'collection' | 'customer_company' | 'other'
```

Add a new block after `DependencyType` (`web/src/types/index.ts:103`), before `export interface Identity`:

```ts
// Matches apps/api/modules/issues/models/issue.py IssueType enum — the DB
// column is Enum(IssueType) but every value over the wire is the lowercase
// .value string (e.g. "support"), never the Python member name.
export type IssueType =
  | 'operations'
  | 'code'
  | 'config'
  | 'security'
  | 'architecture'
  | 'process'
  | 'approval'
  | 'feature'
  | 'bug'
  | 'support'
  | 'other'

// Disambiguates the polymorphic Issue.assignee_id: 'identity' resolves
// against identities.id, 'org_unit' against organizations.id. Matches
// apps/api/modules/issues/routes/issues.py::_resolve_assignee_type and
// apps/api/modules/helpdesk/routes/intake_forms.py::_VALID_ASSIGNEE_TYPES.
export type IssueAssigneeType = 'identity' | 'org_unit'
```

Replace the `Identity` interface (`web/src/types/index.ts:105-116`):

```ts
// Matches apps/api/models/identity.py IdentityType enum (the DB column).
// NOTE: apps/api/models/pydantic/identity.py's CreateIdentityRequest
// currently restricts identity_type to Literal["human","service_account"]
// only, rejecting every other value below with a 422 — a pre-existing
// backend gap (see this plan's Global Constraints), not fixed here.
export interface Identity {
  id: number
  username: string
  email: string
  full_name: string
  identity_type:
    | 'human'
    | 'service_account'
    | 'employee'
    | 'vendor'
    | 'bot'
    | 'serviceAccount'
    | 'integration'
    | 'otherHuman'
    | 'other'
    | 'customer_contact'
  auth_provider: 'local' | 'saml' | 'oauth2' | 'ldap'
  is_active: boolean
  is_superuser: boolean
  created_at: string
  last_login_at?: string
}
```

Replace the `Issue` interface (`web/src/types/index.ts:126-146`):

```ts
export interface Issue {
  id: number
  title: string
  description?: string
  status: IssueStatus
  priority: IssuePriority
  issue_type: IssueType
  reporter_id?: number
  assignee_id?: number
  assignee_type?: IssueAssigneeType
  resource_type?: string
  resource_id?: number
  is_incident?: number | boolean
  channel?: string
  category?: string
  requester_contact_id?: number
  hd_sla_policy_id?: number
  sla_breach_at?: string
  first_response_at?: string
  resolved_at?: string
  parent_issue_id?: number
  closed_at?: string
  created_at: string
  updated_at: string
  // Legacy/enrichment-only fields: NOT part of apps/api/models/dataclasses.py
  // IssueDTO (the real GET /issues and GET /issues/:id response shape,
  // confirmed by reading the backend directly). Kept optional so existing
  // call sites that populate them via a *separate* fetch (labels,
  // entity_links) or that were already reading dead fields (organization_id,
  // village_id, created_by, tenant_id, assignee) keep compiling. Do not add
  // new reads of these without confirming the backend actually returns them
  // for that specific endpoint.
  organization_id?: number
  assigned_to?: number
  created_by?: number
  village_id?: string
  tenant_id?: number
  labels?: IssueLabel[]
  entity_links?: Entity[]
  assignee?: Identity
}
```

- [ ] **Step 4: Create `web/src/lib/constants/issueTypes.ts`**

```ts
import type { IssueType } from '@/types'

export interface IssueTypeOption {
  value: IssueType
  label: string
}

/**
 * Canonical issue_type dropdown/badge options — single source of truth for
 * the unified Issues list, detail, and create/edit views, and the intake
 * form builder. Matches apps/api/modules/issues/models/issue.py IssueType
 * enum values exactly (lowercase over the wire).
 */
export const ISSUE_TYPES: IssueTypeOption[] = [
  { value: 'operations', label: 'Operations' },
  { value: 'code', label: 'Code' },
  { value: 'config', label: 'Config' },
  { value: 'security', label: 'Security' },
  { value: 'architecture', label: 'Architecture' },
  { value: 'process', label: 'Process' },
  { value: 'approval', label: 'Approval' },
  { value: 'feature', label: 'Feature' },
  { value: 'bug', label: 'Bug' },
  { value: 'support', label: 'Support' },
  { value: 'other', label: 'Other' },
]

/** The single issue_type value that triggers support-specific UI (the
 * support section in the detail view, support fields in create/edit). */
export const SUPPORT_ISSUE_TYPE: IssueType = 'support'

/** Resolve a raw issue_type string to its display label, falling back to
 * the raw value itself for anything not in ISSUE_TYPES. */
export function issueTypeLabel(value: string): string {
  return ISSUE_TYPES.find((t) => t.value === value)?.label ?? value
}
```

- [ ] **Step 5: Create `web/src/lib/constants/identityTypes.ts`**

```tsx
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
```

- [ ] **Step 6: Run tests to verify they pass, then typecheck**

Run: `cd web && npx vitest run src/lib/constants/issueTypes.test.ts src/lib/constants/identityTypes.test.ts`
Expected: PASS (5 tests)

Run: `cd web && npm run typecheck`
Expected: no new errors. (Existing call sites still compile: `Issue`/`Identity` gained fields, none were removed or narrowed.)

- [ ] **Step 7: Commit**

```bash
git add web/src/types/index.ts web/src/lib/constants/issueTypes.ts web/src/lib/constants/issueTypes.test.ts web/src/lib/constants/identityTypes.ts web/src/lib/constants/identityTypes.test.ts
git commit -m "feat(issues): add issue_type/assignee_type types and shared issue/identity type constants"
```

---

## Task 2: API client extension

**Files:**
- Modify: `web/src/lib/api.ts:621-663` (issues), `web/src/lib/api.ts:1204-1219` (webhooks), add intake-forms methods
- Create: `web/src/lib/api.test.ts`

**Interfaces:**
- Consumes: `IssueType`, `IssueAssigneeType` (Task 1)
- Produces: `api.getIssues(params)` (+`issue_type`), `api.createIssue(data)` (requires `organization_id`, adds `issue_type`/`assignee_type`/`channel`/`category`, drops the never-supported `entity_ids`/`label_ids`), `api.updateIssue(id, data)` (now sends `PATCH`, adds `issue_type`/`assignee_type`), `api.createWebhook`/`api.updateWebhook` (+`filter_issue_type`/`filter_assignee_type`/`filter_assignee_id`), `api.getIntakeForms/getIntakeForm/createIntakeForm/updateIntakeForm/deleteIntakeForm/getPublicIntakeForm/submitPublicIntakeForm`. Task 3 (`AssigneePicker`), Task 4/5/6 (Issues/IssueDetail), Task 7 (IntakeForms), Task 8 (Webhooks) all call these by these exact names.

- [ ] **Step 1: Write failing tests**

Create `web/src/lib/api.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockAxiosInstance = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  interceptors: {
    request: { use: vi.fn() },
    response: { use: vi.fn() },
  },
}

vi.mock('axios', () => ({
  default: { create: vi.fn(() => mockAxiosInstance) },
}))

const { default: api } = await import('@/lib/api')

describe('ApiClient - Issues', () => {
  beforeEach(() => vi.clearAllMocks())

  it('getIssues passes issue_type through as a query param', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: { items: [], total: 0 } })
    await api.getIssues({ issue_type: 'support' })
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/issues', {
      params: { issue_type: 'support' },
    })
  })

  it('createIssue forwards issue_type + assignee_type + assignee_id', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { id: 1 } })
    await api.createIssue({
      title: 'Server down',
      organization_id: 5,
      issue_type: 'support',
      assignee_id: 42,
      assignee_type: 'identity',
    })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/issues', {
      title: 'Server down',
      organization_id: 5,
      issue_type: 'support',
      assignee_id: 42,
      assignee_type: 'identity',
    })
  })

  it('updateIssue sends PATCH, not PUT (backend only registers PATCH /issues/:id)', async () => {
    mockAxiosInstance.patch.mockResolvedValue({ data: { id: 1 } })
    await api.updateIssue(1, { assignee_id: 7, assignee_type: 'org_unit' })
    expect(mockAxiosInstance.patch).toHaveBeenCalledWith('/issues/1', {
      assignee_id: 7,
      assignee_type: 'org_unit',
    })
    expect(mockAxiosInstance.put).not.toHaveBeenCalled()
  })
})

describe('ApiClient - Intake Forms', () => {
  beforeEach(() => vi.clearAllMocks())

  it('createIntakeForm posts to /intake-forms', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { id: 1, slug: 'support-request' } })
    await api.createIntakeForm({
      name: 'Support Request',
      slug: 'support-request',
      fields: [{ id: 'email', label: 'Email', type: 'email', required: true }],
    })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/intake-forms', {
      name: 'Support Request',
      slug: 'support-request',
      fields: [{ id: 'email', label: 'Email', type: 'email', required: true }],
    })
  })

  it('updateIntakeForm sends PATCH to /intake-forms/:id', async () => {
    mockAxiosInstance.patch.mockResolvedValue({ data: { id: 1 } })
    await api.updateIntakeForm(1, { is_active: false })
    expect(mockAxiosInstance.patch).toHaveBeenCalledWith('/intake-forms/1', { is_active: false })
  })

  it('submitPublicIntakeForm posts to /intake/:slug/submit', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { status: 'created', reference: 'abc-123' } })
    await api.submitPublicIntakeForm('support-request', { fields: { email: 'a@b.com' } })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/intake/support-request/submit', {
      fields: { email: 'a@b.com' },
    })
  })
})

describe('ApiClient - Webhook filters', () => {
  beforeEach(() => vi.clearAllMocks())

  it('createWebhook forwards filter_issue_type/filter_assignee_type/filter_assignee_id', async () => {
    mockAxiosInstance.post.mockResolvedValue({ data: { id: 1 } })
    await api.createWebhook({
      name: 'Support bot assignments',
      url: 'https://hooks.example.com/x',
      events: ['issue.assigned'],
      filter_issue_type: 'support',
      filter_assignee_type: 'identity',
      filter_assignee_id: 42,
    })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/webhooks', {
      name: 'Support bot assignments',
      url: 'https://hooks.example.com/x',
      events: ['issue.assigned'],
      filter_issue_type: 'support',
      filter_assignee_type: 'identity',
      filter_assignee_id: 42,
    })
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run src/lib/api.test.ts`
Expected: FAIL — `createIntakeForm`/`updateIntakeForm`/`submitPublicIntakeForm` are not functions; `updateIssue` still calls `.put`.

- [ ] **Step 3: Replace the Issues section of `web/src/lib/api.ts:620-663`**

```ts
  // Issues
  async getIssues(params?: {
    page?: number
    per_page?: number
    organization_id?: number
    entity_id?: number
    project_id?: number
    status?: string
    priority?: string
    issue_type?: string
    assigned_to?: number
    assignee_id?: number
    search?: string
  }) {
    const response = await this.client.get('/issues', { params })
    return response.data
  }

  async getIssue(id: number) {
    const response = await this.client.get(`/issues/${id}`)
    return response.data
  }

  async createIssue(data: {
    title: string
    organization_id: number
    description?: string
    status?: string
    priority?: string
    issue_type?: string
    assignee_id?: number
    assignee_type?: 'identity' | 'org_unit'
    is_incident?: number
    channel?: string
    category?: string
    parent_issue_id?: number
  }) {
    const response = await this.client.post('/issues', data)
    return response.data
  }

  // Note: apps/api/modules/issues/routes/issues.py::update_issue only
  // registers PATCH for /issues/:id (no PUT route exists) — using PUT here
  // 405s every issue update.
  async updateIssue(id: number, data: Partial<{
    title: string
    description: string
    status: string
    priority: string
    issue_type: string
    assignee_id: number
    assignee_type: 'identity' | 'org_unit'
    organization_id: number
    is_incident: number
    channel: string
    category: string
    parent_issue_id: number
  }>) {
    const response = await this.client.patch(`/issues/${id}`, data)
    return response.data
  }

  async deleteIssue(id: number) {
    const response = await this.client.delete(`/issues/${id}`)
    return response.data
  }
```

- [ ] **Step 4: Add intake-forms methods immediately after `unlinkIssueEntity` (`web/src/lib/api.ts:713-716`, before the `// Labels` section)**

```ts
  // Intake Forms (admin) — /api/v1/intake-forms
  async getIntakeForms(params?: { page?: number; per_page?: number; is_active?: boolean }) {
    const response = await this.client.get('/intake-forms', { params })
    return response.data
  }

  async getIntakeForm(id: number) {
    const response = await this.client.get(`/intake-forms/${id}`)
    return response.data
  }

  async createIntakeForm(data: {
    name: string
    slug: string
    description?: string
    fields: Array<{ id: string; label: string; type: string; required: boolean; options?: string[] }>
    issue_type?: string
    default_assignee_type?: 'identity' | 'org_unit'
    default_assignee_id?: number
    organization_id?: number
    is_public?: boolean
    captcha_required?: boolean
    is_active?: boolean
    metadata?: Record<string, unknown>
  }) {
    const response = await this.client.post('/intake-forms', data)
    return response.data
  }

  async updateIntakeForm(id: number, data: Partial<{
    name: string
    description: string
    fields: Array<{ id: string; label: string; type: string; required: boolean; options?: string[] }>
    issue_type: string
    default_assignee_type: 'identity' | 'org_unit'
    default_assignee_id: number
    organization_id: number
    is_public: boolean
    captcha_required: boolean
    is_active: boolean
    metadata: Record<string, unknown>
  }>) {
    const response = await this.client.patch(`/intake-forms/${id}`, data)
    return response.data
  }

  async deleteIntakeForm(id: number) {
    const response = await this.client.delete(`/intake-forms/${id}`)
    return response.data
  }

  // Intake Forms (public, unauthenticated) — /api/v1/intake
  async getPublicIntakeForm(slug: string) {
    const response = await this.client.get(`/intake/${slug}`)
    return response.data
  }

  async submitPublicIntakeForm(slug: string, data: { fields: Record<string, unknown>; altcha?: unknown }) {
    const response = await this.client.post(`/intake/${slug}/submit`, data)
    return response.data
  }
```

- [ ] **Step 5: Extend the webhooks section (`web/src/lib/api.ts:1204-1219`)**

```ts
  async createWebhook(data: {
    name: string
    url: string
    organization_id?: number
    events: string[]
    secret?: string
    enabled?: boolean
    headers?: Record<string, string>
    filter_issue_type?: string
    filter_assignee_type?: 'identity' | 'org_unit'
    filter_assignee_id?: number
    metadata?: Record<string, unknown>
  }) {
    const response = await this.client.post('/webhooks', data)
    return response.data
  }

  async updateWebhook(id: number, data: Partial<{
    name: string
    url: string
    events: string[]
    secret: string
    enabled: boolean
    headers: Record<string, string>
    filter_issue_type: string
    filter_assignee_type: 'identity' | 'org_unit'
    filter_assignee_id: number
    metadata: Record<string, unknown>
  }>) {
    const response = await this.client.put(`/webhooks/${id}`, data)
    return response.data
  }
```

- [ ] **Step 6: Run tests to verify they pass, then typecheck**

Run: `cd web && npx vitest run src/lib/api.test.ts`
Expected: PASS (7 tests)

Run: `cd web && npm run typecheck`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/api.test.ts
git commit -m "fix(issues): PATCH issue updates, add issue_type/assignee fields, intake-forms + webhook-filter API methods"
```

---

## Task 3: Combined assignee picker component

**Files:**
- Modify: `web/src/components/SearchableSelect.tsx`
- Create: `web/src/components/SearchableSelect.test.tsx`
- Create: `web/src/components/AssigneePicker.tsx`
- Create: `web/src/components/AssigneePicker.test.tsx`

**Interfaces:**
- Consumes: `api.getIdentities`, `api.getOrganizations` (existing), `queryKeys.identities.list`, `queryKeys.organizations.dropdown` (existing), `IssueAssigneeType` (Task 1)
- Produces: `AssigneeValue { assignee_type: IssueAssigneeType; assignee_id: number }`, `AssigneePicker` default export with props `{ value?: AssigneeValue | null; onChange: (v: AssigneeValue | null) => void; disabled?: boolean; className?: string }`, rendered with `data-testid="assignee-picker"`. Tasks 5, 6, 7, and 8 all import and render `<AssigneePicker>`.

- [ ] **Step 1: Write a failing test for `SearchableSelect`'s new accessibility props**

Create `web/src/components/SearchableSelect.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import SearchableSelect from './SearchableSelect'

describe('SearchableSelect accessibility', () => {
  it('applies aria-label and combobox role to the input', () => {
    render(
      <SearchableSelect
        options={[{ value: 1, label: 'Option A' }]}
        onChange={vi.fn()}
        ariaLabel="Assignee"
      />
    )
    const input = screen.getByRole('combobox', { name: 'Assignee' })
    expect(input).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/SearchableSelect.test.tsx`
Expected: FAIL — no element with role `combobox` (the `<input>` has no `role`/`aria-label` yet)

- [ ] **Step 3: Add `ariaLabel` + ARIA attributes to `SearchableSelect.tsx`**

Modify the props interface (`web/src/components/SearchableSelect.tsx:8-17`):

```ts
interface SearchableSelectProps {
  options?: Option[]
  value?: string | number
  onChange: (value: string | number, option: Option) => void
  onSearch?: (query: string) => void
  placeholder?: string
  isLoading?: boolean
  disabled?: boolean
  className?: string
  ariaLabel?: string
}
```

Modify the function signature (`web/src/components/SearchableSelect.tsx:19-28`) to destructure `ariaLabel`, and the `<input>` (`web/src/components/SearchableSelect.tsx:119-132`):

```tsx
export default function SearchableSelect({
  options = [],
  value,
  onChange,
  onSearch,
  placeholder = 'Search...',
  isLoading = false,
  disabled = false,
  className = '',
  ariaLabel,
}: SearchableSelectProps) {
  // ...unchanged body...

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={isOpen}
        aria-autocomplete="list"
        value={isOpen ? query : selectedOption?.label || ''}
        onChange={(e) => handleQueryChange(e.target.value)}
        onFocus={() => {
          setIsOpen(true)
          setQuery('')
        }}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        className="w-full rounded-lg border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-white placeholder-slate-400 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500 disabled:opacity-50"
      />
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/SearchableSelect.test.tsx`
Expected: PASS

- [ ] **Step 5: Write failing tests for `AssigneePicker`**

Create `web/src/components/AssigneePicker.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AssigneePicker from './AssigneePicker'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getIdentities: vi.fn(),
    getOrganizations: vi.fn(),
  },
}))

const createQueryClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } })

function renderPicker(onChange = vi.fn()) {
  const queryClient = createQueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <AssigneePicker value={null} onChange={onChange} />
    </QueryClientProvider>
  )
  return { onChange }
}

describe('AssigneePicker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getIdentities).mockResolvedValue({
      items: [{
        id: 42, username: 'jane', full_name: 'Jane Doe', email: 'jane@x.com',
        identity_type: 'employee', auth_provider: 'local', is_active: true,
        is_superuser: false, created_at: '2026-01-01',
      }],
    })
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [{ id: 7, name: 'Platform Team', created_at: '2026-01-01', updated_at: '2026-01-01' }],
    })
  })

  it('resolves an identity selection to {assignee_type: identity, assignee_id}', async () => {
    const { onChange } = renderPicker()
    fireEvent.focus(screen.getByRole('combobox'))
    await waitFor(() => screen.getByText('Jane Doe (Identity)'))
    fireEvent.click(screen.getByText('Jane Doe (Identity)'))
    expect(onChange).toHaveBeenCalledWith({ assignee_type: 'identity', assignee_id: 42 })
  })

  it('resolves an org unit selection to {assignee_type: org_unit, assignee_id}', async () => {
    const { onChange } = renderPicker()
    fireEvent.focus(screen.getByRole('combobox'))
    await waitFor(() => screen.getByText('Platform Team (Org Unit)'))
    fireEvent.click(screen.getByText('Platform Team (Org Unit)'))
    expect(onChange).toHaveBeenCalledWith({ assignee_type: 'org_unit', assignee_id: 7 })
  })

  it('clicking Unassigned clears the value', async () => {
    const { onChange } = renderPicker()
    fireEvent.focus(screen.getByRole('combobox'))
    await waitFor(() => screen.getByText('Unassigned'))
    fireEvent.click(screen.getByText('Unassigned'))
    expect(onChange).toHaveBeenCalledWith(null)
  })
})
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd web && npx vitest run src/components/AssigneePicker.test.tsx`
Expected: FAIL — `Cannot find module './AssigneePicker'`

- [ ] **Step 7: Create `web/src/components/AssigneePicker.tsx`**

```tsx
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
```

- [ ] **Step 8: Run tests to verify they pass, then typecheck**

Run: `cd web && npx vitest run src/components/AssigneePicker.test.tsx`
Expected: PASS (3 tests)

Run: `cd web && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add web/src/components/SearchableSelect.tsx web/src/components/SearchableSelect.test.tsx web/src/components/AssigneePicker.tsx web/src/components/AssigneePicker.test.tsx
git commit -m "feat(issues): add combined identity+org-unit AssigneePicker component"
```

---

## Task 4: Unified Issues list — issue_type badge + filter + search

**Files:**
- Modify: `web/src/pages/Issues.tsx:1-218` (the `Issues` list component only — `CreateIssueModal` is Task 6)
- Create: `web/src/pages/Issues.test.tsx`

**Interfaces:**
- Consumes: `ISSUE_TYPES`, `issueTypeLabel` (Task 1), `api.getIssues` (Task 2)
- Produces: no new exports; `Issues` default export gains an `issue_type` filter and client-side `search`/`issue_type` narrowing. Task 6 continues to import `CreateIssueModal` as a named export from this same file, unchanged.

- [ ] **Step 1: Write failing tests**

Create `web/src/pages/Issues.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Issues from './Issues'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getIssues: vi.fn(),
    getOrganizations: vi.fn().mockResolvedValue({ items: [] }),
    getEntities: vi.fn().mockResolvedValue({ items: [] }),
    getLabels: vi.fn().mockResolvedValue({ items: [] }),
    updateIssue: vi.fn(),
  },
}))

function renderIssues() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Issues />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const items = [
  { id: 1, title: 'Server down', status: 'open', priority: 'high', issue_type: 'operations', created_at: '2026-01-01', updated_at: '2026-01-01' },
  { id: 2, title: 'Customer cannot log in', status: 'open', priority: 'medium', issue_type: 'support', created_at: '2026-01-01', updated_at: '2026-01-01' },
]

describe('Issues list', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getIssues).mockResolvedValue({ items, total: 2, page: 1, pages: 1, per_page: 50 })
  })

  it('renders an issue_type badge per issue and a type filter dropdown', async () => {
    renderIssues()
    await waitFor(() => screen.getByText('Server down'))
    expect(screen.getByTestId('issue-type-filter')).toBeInTheDocument()
    expect(screen.getByText('Operations')).toBeInTheDocument()
    expect(screen.getByText('Support')).toBeInTheDocument()
  })

  it('filters the list client-side by issue_type (server does not support it)', async () => {
    renderIssues()
    await waitFor(() => screen.getByText('Server down'))
    fireEvent.change(screen.getByTestId('issue-type-filter'), { target: { value: 'support' } })
    expect(screen.queryByText('Server down')).toBeNull()
    expect(screen.getByText('Customer cannot log in')).toBeInTheDocument()
  })

  it('filters the list client-side by search text', async () => {
    renderIssues()
    await waitFor(() => screen.getByText('Server down'))
    fireEvent.change(screen.getByPlaceholderText('Search issues...'), { target: { value: 'login' } })
    expect(screen.queryByText('Server down')).toBeNull()
    expect(screen.getByText('Customer cannot log in')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run src/pages/Issues.test.tsx`
Expected: FAIL — no element with `data-testid="issue-type-filter"`; type labels not rendered.

- [ ] **Step 3: Update imports and add `issueTypeFilter` state + client-side filtering (`web/src/pages/Issues.tsx:1-38`)**

```tsx
import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Plus, Search, MessageSquare, Tag, User, AlertTriangle } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { invalidateCache } from '@/lib/invalidateCache'
import { getStatusColor, getPriorityColor } from '@/lib/colorHelpers'
import { ISSUE_TYPES, issueTypeLabel } from '@/lib/constants/issueTypes'
import { Issue, IssueStatus, IssuePriority, IssueType, Organization, Entity, IssueLabel } from '@/types'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'

export default function Issues() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<IssueStatus | ''>('')
  const [priorityFilter, setPriorityFilter] = useState<IssuePriority | ''>('')
  const [issueTypeFilter, setIssueTypeFilter] = useState<IssueType | ''>('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const organizationId = searchParams.get('organization_id')
  const entityId = searchParams.get('entity_id')

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.issues.list({ status: statusFilter, priority: priorityFilter, organizationId, entityId }),
    queryFn: () => api.getIssues({
      status: statusFilter || undefined,
      priority: priorityFilter || undefined,
      organization_id: organizationId ? parseInt(organizationId) : undefined,
      entity_id: entityId ? parseInt(entityId) : undefined,
    }),
  })

  // GET /issues has no server-side `search` or `issue_type` filter
  // (apps/api/modules/issues/routes/issues.py::list_issues only applies
  // status/priority/assignee_id/reporter_id) — filter client-side over the
  // fetched page so the search box and type filter actually narrow results.
  const filteredIssues = useMemo(() => {
    const allItems: Issue[] = data?.items || []
    const q = search.trim().toLowerCase()
    return allItems.filter((issue) => {
      const matchesType = !issueTypeFilter || issue.issue_type === issueTypeFilter
      const matchesSearch =
        !q ||
        issue.title.toLowerCase().includes(q) ||
        (issue.description || '').toLowerCase().includes(q)
      return matchesType && matchesSearch
    })
  }, [data, search, issueTypeFilter])

  const updateStatusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: IssueStatus }) =>
      api.updateIssue(id, { status }),
    onSuccess: async () => {
      await invalidateCache.issues(queryClient)
      toast.success('Issue status updated')
    },
    onError: () => {
      toast.error('Failed to update issue status')
    },
  })
```

- [ ] **Step 4: Add the issue_type filter dropdown to the filters grid (`web/src/pages/Issues.tsx:69-100`)**

```tsx
      {/* Filters */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-slate-400" />
          <Input
            type="text"
            placeholder="Search issues..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as IssueStatus | '')}
        >
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="closed">Closed</option>
        </Select>
        <Select
          value={priorityFilter}
          onChange={(e) => setPriorityFilter(e.target.value as IssuePriority | '')}
        >
          <option value="">All Priorities</option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="critical">Critical</option>
        </Select>
        <Select
          value={issueTypeFilter}
          onChange={(e) => setIssueTypeFilter(e.target.value as IssueType | '')}
          data-testid="issue-type-filter"
        >
          <option value="">All Types</option>
          {ISSUE_TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </Select>
      </div>
```

- [ ] **Step 5: Switch list rendering to `filteredIssues` and add the type badge (`web/src/pages/Issues.tsx:102-199`)**

```tsx
      {/* Issues List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filteredIssues.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">No issues found</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first issue
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {filteredIssues.map((issue: Issue) => (
            <Card
              key={issue.id}
              className="cursor-pointer hover:ring-2 hover:ring-primary-500 transition-all"
              onClick={() => navigate(`/issues/${issue.id}`)}
              data-testid={`issue-card-${issue.id}`}
            >
              <CardContent>
                <div className="flex items-start gap-4">
                  <div className="flex-shrink-0 mt-1">
                    {issue.is_incident ? (
                      <AlertTriangle className="w-5 h-5 text-red-500" />
                    ) : (
                      <MessageSquare className="w-5 h-5 text-primary-400" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <h3 className="text-lg font-semibold text-white mb-1">
                          {issue.title}
                        </h3>
                        {issue.description && (
                          <p className="text-sm text-slate-400 line-clamp-2 mb-3">
                            {issue.description}
                          </p>
                        )}
                        <div className="flex flex-wrap items-center gap-3">
                          <span className="text-xs text-slate-500">#{issue.id}</span>
                          {issue.is_incident === 1 && (
                            <span className="text-xs px-2 py-0.5 rounded bg-red-500/20 text-red-400 border border-red-500/30 font-semibold">
                              INCIDENT
                            </span>
                          )}
                          <span className={`text-xs px-2 py-0.5 rounded border ${getStatusColor(issue.status)}`}>
                            {issue.status.replace('_', ' ')}
                          </span>
                          <span className={`text-xs px-2 py-0.5 rounded border ${getPriorityColor(issue.priority)}`}>
                            {issue.priority}
                          </span>
                          <span className="text-xs px-2 py-0.5 rounded border border-slate-600 bg-slate-700/40 text-slate-300">
                            {issueTypeLabel(issue.issue_type)}
                          </span>
                          {issue.assignee_id && (
                            <span className="flex items-center gap-1 text-xs text-slate-400">
                              <User className="w-3 h-3" />
                              Assigned
                            </span>
                          )}
                          {issue.labels && issue.labels.length > 0 && (
                            <span className="flex items-center gap-1 text-xs text-slate-400">
                              <Tag className="w-3 h-3" />
                              {issue.labels.length} label(s)
                            </span>
                          )}
                        </div>
                      </div>
                      <div onClick={(e) => e.stopPropagation()}>
                        <Select
                          value={issue.status}
                          onChange={(e) =>
                            updateStatusMutation.mutate({
                              id: issue.id,
                              status: e.target.value as IssueStatus,
                            })
                          }
                          className="text-sm"
                        >
                          <option value="open">Open</option>
                          <option value="in_progress">In Progress</option>
                          <option value="closed">Closed</option>
                        </Select>
                      </div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
```

(`Create Issue` button, `CreateIssueModal` render block at the bottom of the file are unchanged by this task — Task 6 replaces `CreateIssueModal`'s internals only.)

- [ ] **Step 6: Run tests to verify they pass, then lint/typecheck**

Run: `cd web && npx vitest run src/pages/Issues.test.tsx`
Expected: PASS (3 tests)

Run: `cd web && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add web/src/pages/Issues.tsx web/src/pages/Issues.test.tsx
git commit -m "feat(issues): unified issue_type badge, filter, and client-side search on the Issues list"
```

---

## Task 5: Issue detail — assignee picker + support section

**Files:**
- Modify: `web/src/pages/IssueDetail.tsx`
- Create: `web/src/pages/IssueDetail.test.tsx`

**Interfaces:**
- Consumes: `AssigneePicker`, `AssigneeValue` (Task 3), `api.getIdentity` (existing), `api.updateIssue` (Task 2)
- Produces: no new exports; `IssueDetail` default export gains a working assignee picker and a conditional support-details card.

- [ ] **Step 1: Write failing tests**

Create `web/src/pages/IssueDetail.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import IssueDetail from './IssueDetail'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getIssue: vi.fn(),
    getIssueComments: vi.fn().mockResolvedValue({ items: [] }),
    getIssueLabels: vi.fn().mockResolvedValue({ items: [] }),
    getIssueEntities: vi.fn().mockResolvedValue({ items: [] }),
    getLabels: vi.fn().mockResolvedValue({ items: [] }),
    getEntities: vi.fn().mockResolvedValue({ items: [] }),
    getIdentities: vi.fn().mockResolvedValue({ items: [] }),
    getIdentity: vi.fn(),
    getOrganizations: vi.fn().mockResolvedValue({ items: [] }),
    getProjects: vi.fn().mockResolvedValue({ items: [] }),
    getMilestones: vi.fn().mockResolvedValue({ items: [] }),
    updateIssue: vi.fn(),
    deleteIssue: vi.fn(),
    createIssueComment: vi.fn(),
    addIssueLabel: vi.fn(),
    removeIssueLabel: vi.fn(),
    linkIssueEntity: vi.fn(),
    unlinkIssueEntity: vi.fn(),
    linkIssueToProject: vi.fn(),
    unlinkIssueFromProject: vi.fn(),
    linkIssueToMilestone: vi.fn(),
    unlinkIssueFromMilestone: vi.fn(),
  },
}))

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

function renderDetail() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/issues/1']}>
        <Routes>
          <Route path="/issues/:id" element={<IssueDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const baseIssue = {
  id: 1,
  title: 'Server down',
  status: 'open' as const,
  priority: 'high' as const,
  issue_type: 'operations' as const,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

describe('IssueDetail', () => {
  beforeEach(() => vi.clearAllMocks())

  it('does not render a support details card for a non-support issue', async () => {
    vi.mocked(api.getIssue).mockResolvedValue(baseIssue)
    renderDetail()
    await waitFor(() => screen.getByText('Server down'))
    expect(screen.queryByText('Support Details')).toBeNull()
  })

  it('renders channel and category for a support issue', async () => {
    vi.mocked(api.getIssue).mockResolvedValue({
      ...baseIssue, issue_type: 'support', channel: 'email', category: 'billing',
    })
    renderDetail()
    await waitFor(() => screen.getByText('Support Details'))
    expect(screen.getByText('email')).toBeInTheDocument()
    expect(screen.getByText('billing')).toBeInTheDocument()
  })

  it('shows an error toast and skips the API call when clearing an assignee', async () => {
    const toast = (await import('react-hot-toast')).default
    vi.mocked(api.getIssue).mockResolvedValue({ ...baseIssue, assignee_id: 42, assignee_type: 'identity' })
    vi.mocked(api.getIdentities).mockResolvedValue({
      items: [{ id: 42, username: 'jane', full_name: 'Jane Doe', email: 'jane@x.com', identity_type: 'employee', auth_provider: 'local', is_active: true, is_superuser: false, created_at: '2026-01-01' }],
    })
    renderDetail()
    await waitFor(() => screen.getByText('Server down'))
    const picker = await screen.findByTestId('assignee-picker')
    picker.querySelector('input')!.focus()
    const unassigned = await screen.findByText('Unassigned')
    unassigned.click()
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(api.updateIssue).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run src/pages/IssueDetail.test.tsx`
Expected: FAIL — no `Support Details` text; assignee `<Select>` renders instead of `data-testid="assignee-picker"`.

- [ ] **Step 3: Update imports, add the `requesterContact` query, and the assignee-clear handler (`web/src/pages/IssueDetail.tsx:1-30, 85-98`)**

```tsx
import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Trash2, MessageSquare, Tag, User, Link as LinkIcon,
  Send, X, Plus, Copy, ListTree
} from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { Entity, Identity, IssueLabel, Issue, IssueStatus, IssuePriority, IssueAssigneeType } from '@/types'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Select from '@/components/Select'
import AssigneePicker, { AssigneeValue } from '@/components/AssigneePicker'
import { CreateIssueModal } from '@/pages/Issues'

interface IssueComment {
  id: number
  body: string
  created_at: string
  author?: {
    full_name?: string
    username: string
  }
}

interface IssueUpdatePayload {
  status?: IssueStatus
  priority?: IssuePriority
  assignee_id?: number
  assignee_type?: IssueAssigneeType
}
```

Insert the new query right after the `allMilestones` query (`web/src/pages/IssueDetail.tsx:95-98`), before `updateMutation`:

```tsx
  const { data: requesterContact } = useQuery({
    queryKey: ['identity', issue?.requester_contact_id],
    queryFn: () => api.getIdentity(issue!.requester_contact_id!),
    enabled: !!issue && issue.issue_type === 'support' && !!issue.requester_contact_id,
  })
```

- [ ] **Step 4: Add the assignee-change handler after `handleAddComment` (`web/src/pages/IssueDetail.tsx:262-267`)**

```tsx
  const handleAssigneeChange = (value: AssigneeValue | null) => {
    if (!value) {
      // PATCH /issues/:id never clears assignee_id once set — see
      // apps/api/modules/issues/routes/issues.py::update_issue ("assignee_id
      // is never cleared by this endpoint"). Surface this instead of
      // silently no-op-ing and reverting the UI on refetch.
      toast.error('Clearing an assignee is not supported yet — pick a new assignee instead')
      return
    }
    updateMutation.mutate({ assignee_id: value.assignee_id, assignee_type: value.assignee_type })
  }
```

- [ ] **Step 5: Replace the Assignee card body (`web/src/pages/IssueDetail.tsx:526-544`)**

```tsx
          {/* Assignee */}
          <Card>
            <CardHeader>
              <h3 className="text-lg font-semibold text-white">Assignee</h3>
            </CardHeader>
            <CardContent>
              <AssigneePicker
                value={
                  issue.assignee_id && issue.assignee_type
                    ? { assignee_type: issue.assignee_type, assignee_id: issue.assignee_id }
                    : null
                }
                onChange={handleAssigneeChange}
              />
            </CardContent>
          </Card>

          {/* Support Details (issue_type=support only) */}
          {issue.issue_type === 'support' && (
            <Card>
              <CardHeader>
                <h3 className="text-lg font-semibold text-white">Support Details</h3>
              </CardHeader>
              <CardContent className="space-y-3">
                {issue.channel && (
                  <div>
                    <dt className="text-xs font-medium text-slate-500">Channel</dt>
                    <dd className="text-sm text-slate-300">{issue.channel}</dd>
                  </div>
                )}
                {issue.category && (
                  <div>
                    <dt className="text-xs font-medium text-slate-500">Category</dt>
                    <dd className="text-sm text-slate-300">{issue.category}</dd>
                  </div>
                )}
                {requesterContact && (
                  <div>
                    <dt className="text-xs font-medium text-slate-500">Requester Contact</dt>
                    <dd className="text-sm text-slate-300">
                      {requesterContact.full_name || requesterContact.username}
                    </dd>
                  </div>
                )}
                {issue.sla_breach_at && (
                  <div>
                    <dt className="text-xs font-medium text-slate-500">SLA Breach</dt>
                    <dd className="text-sm text-slate-300">{new Date(issue.sla_breach_at).toLocaleString()}</dd>
                  </div>
                )}
                {issue.first_response_at && (
                  <div>
                    <dt className="text-xs font-medium text-slate-500">First Response</dt>
                    <dd className="text-sm text-slate-300">{new Date(issue.first_response_at).toLocaleString()}</dd>
                  </div>
                )}
                {issue.resolved_at && (
                  <div>
                    <dt className="text-xs font-medium text-slate-500">Resolved</dt>
                    <dd className="text-sm text-slate-300">{new Date(issue.resolved_at).toLocaleString()}</dd>
                  </div>
                )}
                {!issue.channel && !issue.category && !requesterContact && !issue.sla_breach_at &&
                  !issue.first_response_at && !issue.resolved_at && (
                  <p className="text-slate-500 text-sm">No support details recorded</p>
                )}
              </CardContent>
            </Card>
          )}
```

- [ ] **Step 6: Fix the sub-task modal's `defaultOrganizationId` prop (`web/src/pages/IssueDetail.tsx:767-781`)**

`issue?.organization_id` is always `undefined` (`IssueDTO` never returns it — see Task 1's recon note; the real field is `resource_id`/`resource_type`). Fix while touching this component:

```tsx
      {/* Create Sub-Task Modal */}
      {showCreateSubTask && (
        <CreateIssueModal
          onClose={() => setShowCreateSubTask(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({
              queryKey: ['issue-subtasks', id],
              refetchType: 'all'
            })
            setShowCreateSubTask(false)
          }}
          defaultOrganizationId={issue?.resource_type === 'organization' ? issue.resource_id : undefined}
          parentIssueId={parseInt(id!)}
        />
      )}
```

- [ ] **Step 7: Run tests to verify they pass, then lint/typecheck**

Run: `cd web && npx vitest run src/pages/IssueDetail.test.tsx`
Expected: PASS (3 tests)

Run: `cd web && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add web/src/pages/IssueDetail.tsx web/src/pages/IssueDetail.test.tsx
git commit -m "feat(issues): wire AssigneePicker into issue detail, add support-details section"
```

---

## Task 6: Create/Edit issue form — issue_type + assignee picker + support fields

**Files:**
- Modify: `web/src/pages/Issues.tsx` (`CreateIssueModal`, lines 220-397 of the pre-Task-4 file)
- Modify: `web/src/pages/Issues.test.tsx` (add create-modal tests)
- Create: `web/tests/e2e/issues-unified.spec.ts`

**Interfaces:**
- Consumes: `AssigneePicker`, `AssigneeValue` (Task 3), `ISSUE_TYPES` (Task 1), `api.createIssue`, `api.linkIssueEntity`, `api.addIssueLabel` (existing/Task 2)
- Produces: `CreateIssueModal` (named export, same props as before: `{ onClose, onSuccess, defaultOrganizationId?, defaultEntityId?, parentIssueId? }`) — used unchanged by `Issues.tsx`'s own create button and `IssueDetail.tsx`'s sub-task button.

**Design note:** `FormModalBuilder`'s `FormField` type (checked directly against `@penguintechinc/react-libs`'s shipped `.d.ts`) has no custom/render field type, so it cannot embed the `AssigneePicker` combobox. This task replaces `CreateIssueModal`'s `FormModalBuilder` usage with the same hand-rolled `<Card>`-shell modal pattern already used by `Webhooks.tsx`'s `CreateWebhookModal` and `OrganizationDetail.tsx`'s `EditOrganizationModal` — not a new modal primitive, an existing one.

- [ ] **Step 1: Write failing tests for the new create form (append to `web/src/pages/Issues.test.tsx`)**

```tsx
describe('CreateIssueModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getOrganizations).mockResolvedValue({
      items: [{ id: 5, name: 'Acme Corp', created_at: '2026-01-01', updated_at: '2026-01-01' }],
    })
    vi.mocked(api.getEntities).mockResolvedValue({ items: [] })
    vi.mocked(api.getLabels).mockResolvedValue({ items: [] })
  })

  it('shows support fields only when issue_type is support', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <Issues />
        </MemoryRouter>
      </QueryClientProvider>
    )
    fireEvent.click(screen.getByText('Create Issue'))
    expect(screen.queryByTestId('support-fields')).toBeNull()
    fireEvent.change(screen.getByTestId('issue-type-select'), { target: { value: 'support' } })
    expect(screen.getByTestId('support-fields')).toBeInTheDocument()
  })

  it('submits with organization_id required and issue_type/assignee forwarded', async () => {
    vi.mocked(api.createIssue).mockResolvedValue({ id: 99 })
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <Issues />
        </MemoryRouter>
      </QueryClientProvider>
    )
    fireEvent.click(screen.getByText('Create Issue'))
    fireEvent.change(screen.getByTestId('issue-title-input'), { target: { value: 'New issue' } })
    fireEvent.change(screen.getByTestId('issue-organization-select'), { target: { value: '5' } })
    fireEvent.click(screen.getByTestId('submit-issue-button'))
    await waitFor(() => expect(api.createIssue).toHaveBeenCalled())
    const call = vi.mocked(api.createIssue).mock.calls[0][0]
    expect(call.title).toBe('New issue')
    expect(call.organization_id).toBe(5)
    expect(call.issue_type).toBe('other')
  })
})
```

Add the missing imports at the top of `web/src/pages/Issues.test.tsx` if not already present from Task 4 (`fireEvent`, `waitFor`, `QueryClientProvider`, `QueryClient`, `MemoryRouter`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run src/pages/Issues.test.tsx`
Expected: FAIL — no `data-testid="support-fields"`/`issue-type-select"`/`"issue-organization-select"` yet (still the `FormModalBuilder` version).

- [ ] **Step 3: Replace `CreateIssueModal` (the entire block after the `Issues` component in `web/src/pages/Issues.tsx`)**

```tsx
export interface CreateIssueModalProps {
  onClose: () => void
  onSuccess: () => void
  defaultOrganizationId?: number
  defaultEntityId?: number
  parentIssueId?: number
}

export function CreateIssueModal({ onClose, onSuccess, defaultOrganizationId, defaultEntityId, parentIssueId }: CreateIssueModalProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<IssuePriority>('medium')
  const [issueType, setIssueType] = useState<IssueType>('other')
  const [organizationId, setOrganizationId] = useState(defaultOrganizationId ? String(defaultOrganizationId) : '')
  const [assignee, setAssignee] = useState<AssigneeValue | null>(null)
  const [entityIds, setEntityIds] = useState<number[]>(defaultEntityId ? [defaultEntityId] : [])
  const [labelIds, setLabelIds] = useState<number[]>([])
  const [isIncident, setIsIncident] = useState(false)
  const [channel, setChannel] = useState('')
  const [category, setCategory] = useState('')

  const { data: organizations } = useQuery({
    queryKey: ['organizations-all'],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })
  const { data: entities } = useQuery({
    queryKey: ['entities-all'],
    queryFn: () => api.getEntities({ per_page: 1000 }),
  })
  const { data: labels } = useQuery({
    queryKey: ['labels-all'],
    queryFn: () => api.getLabels({ per_page: 1000 }),
  })

  const createMutation = useMutation({
    mutationFn: async (data: {
      title: string
      description?: string
      priority: string
      issue_type: string
      organization_id: number
      assignee_id?: number
      assignee_type?: 'identity' | 'org_unit'
      is_incident: number
      channel?: string
      category?: string
      parent_issue_id?: number
    }) => {
      const issue = await api.createIssue(data)
      // CreateIssueRequest has no entity_ids/label_ids field (see Task 2
      // recon) — link each selection as a follow-up call against the
      // existing per-item endpoints, same as IssueDetail.tsx's sidebar.
      await Promise.all(entityIds.map((entityId) => api.linkIssueEntity(issue.id, entityId)))
      await Promise.all(labelIds.map((labelId) => api.addIssueLabel(issue.id, labelId)))
      return issue
    },
    onSuccess: () => {
      toast.success(parentIssueId ? 'Sub-task created successfully' : 'Issue created successfully')
      onSuccess()
    },
    onError: () => {
      toast.error(parentIssueId ? 'Failed to create sub-task' : 'Failed to create issue')
    },
  })

  const toggleEntity = (id: number) => {
    setEntityIds((prev) => (prev.includes(id) ? prev.filter((e) => e !== id) : [...prev, id]))
  }

  const toggleLabel = (id: number) => {
    setLabelIds((prev) => (prev.includes(id) ? prev.filter((l) => l !== id) : [...prev, id]))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim() || !organizationId) return
    createMutation.mutate({
      title: title.trim(),
      description: description.trim() || undefined,
      priority,
      issue_type: issueType,
      organization_id: parseInt(organizationId),
      assignee_id: assignee?.assignee_id,
      assignee_type: assignee?.assignee_type,
      is_incident: isIncident ? 1 : 0,
      channel: issueType === 'support' ? (channel.trim() || undefined) : undefined,
      category: issueType === 'support' ? (category.trim() || undefined) : undefined,
      parent_issue_id: parentIssueId,
    })
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">
            {parentIssueId ? 'Create Sub-Task' : 'Create Issue'}
          </h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4" data-testid="create-issue-form">
            <Input
              label="Title"
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Enter issue title"
              data-testid="issue-title-input"
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Enter description (optional)"
                rows={4}
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Select
                label="Priority"
                required
                value={priority}
                onChange={(e) => setPriority(e.target.value as IssuePriority)}
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </Select>
              <Select
                label="Issue Type"
                required
                value={issueType}
                onChange={(e) => setIssueType(e.target.value as IssueType)}
                data-testid="issue-type-select"
              >
                {ISSUE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </Select>
            </div>
            <Select
              label="Organization"
              required
              value={organizationId}
              onChange={(e) => setOrganizationId(e.target.value)}
              data-testid="issue-organization-select"
            >
              <option value="">Select organization</option>
              {organizations?.items?.map((org: Organization) => (
                <option key={org.id} value={org.id}>{org.name}</option>
              ))}
            </Select>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Assignee</label>
              <AssigneePicker value={assignee} onChange={setAssignee} />
            </div>
            {issueType === 'support' && (
              <div className="grid grid-cols-2 gap-4 p-4 bg-slate-800/30 rounded-lg" data-testid="support-fields">
                <Input
                  label="Channel"
                  value={channel}
                  onChange={(e) => setChannel(e.target.value)}
                  placeholder="email, chat, phone..."
                />
                <Input
                  label="Category"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  placeholder="billing, technical..."
                />
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Entities</label>
              <div className="max-h-32 overflow-y-auto space-y-1 border border-slate-700 rounded-lg p-2">
                {entities?.items?.map((entity: Entity) => (
                  <label key={entity.id} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={entityIds.includes(entity.id)}
                      onChange={() => toggleEntity(entity.id)}
                      className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                    />
                    {entity.name}
                  </label>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Labels</label>
              <div className="max-h-32 overflow-y-auto space-y-1 border border-slate-700 rounded-lg p-2">
                {labels?.items?.map((label: IssueLabel) => (
                  <label key={label.id} className="flex items-center gap-2 text-sm cursor-pointer" style={{ color: label.color }}>
                    <input
                      type="checkbox"
                      checked={labelIds.includes(label.id)}
                      onChange={() => toggleLabel(label.id)}
                      className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                    />
                    {label.name}
                  </label>
                ))}
              </div>
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={isIncident}
                onChange={(e) => setIsIncident(e.target.checked)}
                className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
              />
              <span className="text-sm text-slate-300">Mark as Incident</span>
            </label>
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" isLoading={createMutation.isPending} data-testid="submit-issue-button">
                {parentIssueId ? 'Create Sub-Task' : 'Create Issue'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
```

Remove the now-unused `FormModalBuilder, FormField` import and the `useMemo` import (only `useState` remains needed) from the top of `web/src/pages/Issues.tsx`; add `AssigneePicker, { AssigneeValue }` from `@/components/AssigneePicker`.

- [ ] **Step 4: Run tests to verify they pass, then lint/typecheck**

Run: `cd web && npx vitest run src/pages/Issues.test.tsx`
Expected: PASS (all `Issues list` + `CreateIssueModal` tests)

Run: `cd web && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 5: Write the Playwright smoke test**

Create `web/tests/e2e/issues-unified.spec.ts`:

```ts
import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';

const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL || 'admin@localhost';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123';

async function login(page: Page) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel(/email/i).fill(ADMIN_EMAIL);
  await page.getByLabel(/password/i).fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: /sign in|log in/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15000 });
}

test.describe('Unified Issues UI', () => {
  test('issues list loads with the issue type filter visible', async ({ page }) => {
    await login(page);
    await page.goto('/issues', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Issues' })).toBeVisible();
    await expect(page.getByTestId('issue-type-filter')).toBeVisible();
  });

  test('create an issue using the combined assignee picker', async ({ page }) => {
    await login(page);
    await page.goto('/issues', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: /create issue/i }).click();
    await expect(page.getByTestId('create-issue-form')).toBeVisible();

    await page.getByTestId('issue-title-input').fill(`E2E smoke issue ${Date.now()}`);
    await page.getByTestId('issue-type-select').selectOption('support');
    await expect(page.getByTestId('support-fields')).toBeVisible();

    await page.getByTestId('issue-organization-select').selectOption({ index: 1 });

    const assigneeInput = page.getByTestId('assignee-picker').locator('input');
    await assigneeInput.click();
    const firstOption = page.getByTestId('assignee-picker').locator('button').first();
    if (await firstOption.isVisible().catch(() => false)) {
      await firstOption.click();
    }

    await page.getByTestId('submit-issue-button').click();
    await expect(page.getByTestId('create-issue-form')).not.toBeVisible({ timeout: 10000 });
  });
});
```

- [ ] **Step 6: Run the Playwright smoke test**

Run: `cd web && npx playwright test tests/e2e/issues-unified.spec.ts`
Expected: PASS against a running dev server with seeded mock data (`make seed-mock-data`) and `PLAYWRIGHT_BASE_URL`/default `http://localhost:3005` reachable — see `web/tests/e2e/README.md` for the run prerequisites already documented for this suite.

- [ ] **Step 7: Commit**

```bash
git add web/src/pages/Issues.tsx web/src/pages/Issues.test.tsx web/tests/e2e/issues-unified.spec.ts
git commit -m "feat(issues): rebuild create/edit issue form with issue_type, assignee picker, and support fields"
```

This completes the demo-critical path: types → API → assignee picker → unified list/detail/create.

---

## Task 7: Intake-form builder admin UI + public submit stub

**Files:**
- Create: `web/src/pages/IntakeForms.tsx`
- Create: `web/src/pages/IntakeForms.test.tsx`
- Create: `web/src/pages/IntakePublicForm.tsx`
- Create: `web/src/pages/IntakePublicForm.test.tsx`
- Modify: `web/src/modules/issues/index.tsx`
- Create: `web/src/modules/issues/__tests__/module.test.ts`
- Modify: `web/src/App.tsx`

**Interfaces:**
- Consumes: `api.getIntakeForms/createIntakeForm/updateIntakeForm/deleteIntakeForm/getPublicIntakeForm/submitPublicIntakeForm` (Task 2), `AssigneePicker` (Task 3), `ISSUE_TYPES` (Task 1)
- Produces: `IntakeForms` default export (route `issues/intake-forms`, admin-gated), `IntakePublicForm` default export (route `/intake/:slug`, unauthenticated)

- [ ] **Step 1: Write failing tests for `IntakeForms.tsx`**

Create `web/src/pages/IntakeForms.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import IntakeForms from './IntakeForms'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getPortalProfile: vi.fn(),
    getIntakeForms: vi.fn(),
    getOrganizations: vi.fn().mockResolvedValue({ items: [] }),
    getIdentities: vi.fn().mockResolvedValue({ items: [] }),
  },
}))

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <IntakeForms />
    </QueryClientProvider>
  )
}

describe('IntakeForms admin page', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows an admin-required message for non-admins', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({ global_role: 'viewer', tenant_role: null })
    vi.mocked(api.getIntakeForms).mockResolvedValue({ items: [] })
    renderPage()
    await waitFor(() => screen.getByText(/admin access required/i))
  })

  it('lists intake forms for an admin', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({ global_role: 'admin', tenant_role: null })
    vi.mocked(api.getIntakeForms).mockResolvedValue({
      items: [{ id: 1, village_id: 'v1', name: 'Support Request', slug: 'support-request', fields: [], issue_type: 'support', is_public: true, captcha_required: true, is_active: true }],
    })
    renderPage()
    await waitFor(() => screen.getByText('Support Request'))
    expect(screen.getByText('/intake/support-request')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Write failing tests for `IntakePublicForm.tsx`**

Create `web/src/pages/IntakePublicForm.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import IntakePublicForm from './IntakePublicForm'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getPublicIntakeForm: vi.fn(),
    submitPublicIntakeForm: vi.fn(),
  },
}))

function renderPublicForm() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/intake/support-request']}>
        <Routes>
          <Route path="/intake/:slug" element={<IntakePublicForm />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('IntakePublicForm', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders dynamic fields and submits successfully', async () => {
    vi.mocked(api.getPublicIntakeForm).mockResolvedValue({
      name: 'Support Request',
      description: 'Tell us what is wrong',
      fields: [{ id: 'email', label: 'Email', type: 'email', required: true }],
      captcha_required: false,
    })
    vi.mocked(api.submitPublicIntakeForm).mockResolvedValue({ status: 'created', reference: 'abc-123' })

    renderPublicForm()
    await waitFor(() => screen.getByText('Support Request'))
    fireEvent.change(screen.getByLabelText('Email *'), { target: { value: 'a@b.com' } })
    fireEvent.click(screen.getByRole('button', { name: /submit/i }))
    await waitFor(() => screen.getByText('Thank you'))
    expect(api.submitPublicIntakeForm).toHaveBeenCalledWith('support-request', { fields: { email: 'a@b.com' } })
  })

  it('shows a not-available message when the form 404s', async () => {
    vi.mocked(api.getPublicIntakeForm).mockRejectedValue(new Error('not found'))
    renderPublicForm()
    await waitFor(() => screen.getByText('This form is not available.'))
  })
})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd web && npx vitest run src/pages/IntakeForms.test.tsx src/pages/IntakePublicForm.test.tsx`
Expected: FAIL — modules don't exist yet.

- [ ] **Step 4: Create `web/src/pages/IntakeForms.tsx`**

```tsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, FileText, Trash2, Edit } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import AssigneePicker, { AssigneeValue } from '@/components/AssigneePicker'
import { ISSUE_TYPES } from '@/lib/constants/issueTypes'
import type { Organization } from '@/types'

interface IntakeFormFieldSpec {
  id: string
  label: string
  type: 'text' | 'email' | 'textarea' | 'select'
  required: boolean
  options?: string[]
}

interface IntakeForm {
  id: number
  village_id: string
  name: string
  slug: string
  description?: string
  fields: IntakeFormFieldSpec[]
  issue_type: string
  default_assignee_type?: 'identity' | 'org_unit'
  default_assignee_id?: number
  organization_id?: number
  is_public: boolean
  captcha_required: boolean
  is_active: boolean
}

/**
 * Admin builder for configurable intake forms (POST /api/v1/intake-forms)
 * — the CRM-facing entry point that lets a public form submission create a
 * native support Issue without requiring a login. Gated to Admin.
 */
export default function IntakeForms() {
  const [showModal, setShowModal] = useState<'create' | IntakeForm | null>(null)
  const queryClient = useQueryClient()

  const { data: profile } = useQuery({
    queryKey: ['portal-profile'],
    queryFn: () => api.getPortalProfile(),
    staleTime: 60000,
  })
  const isAdmin = profile?.global_role === 'admin' || profile?.tenant_role === 'admin'

  const { data, isLoading } = useQuery({
    queryKey: ['intake-forms'],
    queryFn: () => api.getIntakeForms({ per_page: 100 }),
    enabled: isAdmin,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteIntakeForm(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['intake-forms'], refetchType: 'all' })
      toast.success('Intake form deleted')
    },
    onError: () => toast.error('Failed to delete intake form'),
  })

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">Admin access required</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Intake Forms</h1>
          <p className="mt-2 text-slate-400">
            Public forms that create support issues without requiring a login
          </p>
        </div>
        <Button onClick={() => setShowModal('create')} data-testid="create-intake-form-button">
          <Plus className="w-4 h-4 mr-2" />
          Create Intake Form
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.items?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <FileText className="w-12 h-12 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No intake forms configured</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {data?.items?.map((form: IntakeForm) => (
            <Card key={form.id}>
              <CardContent>
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="text-lg font-semibold text-white">{form.name}</h3>
                    <p className="text-sm text-slate-400 font-mono">/intake/{form.slug}</p>
                    <div className="flex gap-2 mt-2">
                      <span className="text-xs px-2 py-0.5 rounded bg-primary-500/20 text-primary-400">
                        {form.issue_type}
                      </span>
                      {form.is_public && (
                        <span className="text-xs px-2 py-0.5 rounded bg-green-500/20 text-green-400">public</span>
                      )}
                      {form.captcha_required && (
                        <span className="text-xs px-2 py-0.5 rounded bg-yellow-500/20 text-yellow-400">captcha</span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" variant="ghost" onClick={() => setShowModal(form)}>
                      <Edit className="w-4 h-4" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => deleteMutation.mutate(form.id)}>
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {showModal && (
        <IntakeFormModal
          existing={showModal === 'create' ? null : showModal}
          onClose={() => setShowModal(null)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({ queryKey: ['intake-forms'], refetchType: 'all' })
            setShowModal(null)
          }}
        />
      )}
    </div>
  )
}

interface IntakeFormModalProps {
  existing: IntakeForm | null
  onClose: () => void
  onSuccess: () => Promise<void>
}

function IntakeFormModal({ existing, onClose, onSuccess }: IntakeFormModalProps) {
  const [name, setName] = useState(existing?.name || '')
  const [slug, setSlug] = useState(existing?.slug || '')
  const [description, setDescription] = useState(existing?.description || '')
  const [issueType, setIssueType] = useState(existing?.issue_type || 'support')
  const [organizationId, setOrganizationId] = useState(existing?.organization_id ? String(existing.organization_id) : '')
  const [assignee, setAssignee] = useState<AssigneeValue | null>(
    existing?.default_assignee_id && existing?.default_assignee_type
      ? { assignee_type: existing.default_assignee_type, assignee_id: existing.default_assignee_id }
      : null
  )
  const [isPublic, setIsPublic] = useState(existing?.is_public ?? true)
  const [captchaRequired, setCaptchaRequired] = useState(existing?.captcha_required ?? true)
  const [fields, setFields] = useState<IntakeFormFieldSpec[]>(
    existing?.fields?.length ? existing.fields : [{ id: 'email', label: 'Email', type: 'email', required: true }]
  )

  const { data: organizations } = useQuery({
    queryKey: ['organizations-all'],
    queryFn: () => api.getOrganizations({ per_page: 1000 }),
  })

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        name,
        slug,
        description: description || undefined,
        fields,
        issue_type: issueType,
        default_assignee_type: assignee?.assignee_type,
        default_assignee_id: assignee?.assignee_id,
        organization_id: organizationId ? parseInt(organizationId) : undefined,
        is_public: isPublic,
        captcha_required: captchaRequired,
      }
      return existing ? api.updateIntakeForm(existing.id, payload) : api.createIntakeForm(payload)
    },
    onSuccess: async () => {
      toast.success(existing ? 'Intake form updated' : 'Intake form created')
      await onSuccess()
    },
    onError: () => toast.error('Failed to save intake form'),
  })

  const addField = () => setFields((prev) => [...prev, { id: '', label: '', type: 'text', required: false }])
  const updateField = (index: number, patch: Partial<IntakeFormFieldSpec>) =>
    setFields((prev) => prev.map((f, i) => (i === index ? { ...f, ...patch } : f)))
  const removeField = (index: number) => setFields((prev) => prev.filter((_, i) => i !== index))

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !slug.trim() || fields.length === 0) return
    saveMutation.mutate()
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">
            {existing ? 'Edit Intake Form' : 'Create Intake Form'}
          </h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4" data-testid="intake-form-modal">
            <Input label="Name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Support Request" />
            <Input
              label="Slug"
              required
              disabled={!!existing}
              value={slug}
              onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-'))}
              placeholder="support-request"
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                className="block w-full px-4 py-2 text-sm bg-slate-900 border border-slate-700 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Select label="Issue Type" value={issueType} onChange={(e) => setIssueType(e.target.value)}>
                {ISSUE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </Select>
              <Select label="Organization" value={organizationId} onChange={(e) => setOrganizationId(e.target.value)}>
                <option value="">None</option>
                {organizations?.items?.map((org: Organization) => (
                  <option key={org.id} value={org.id}>{org.name}</option>
                ))}
              </Select>
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Default Assignee</label>
              <AssigneePicker value={assignee} onChange={setAssignee} />
            </div>
            <div className="flex gap-6">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500" />
                <span className="text-sm text-slate-300">Public</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={captchaRequired} onChange={(e) => setCaptchaRequired(e.target.checked)} className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500" />
                <span className="text-sm text-slate-300">Require CAPTCHA (Altcha)</span>
              </label>
            </div>
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-slate-300">Fields</label>
                <Button type="button" size="sm" variant="ghost" onClick={addField}>
                  <Plus className="w-4 h-4 mr-1" /> Add Field
                </Button>
              </div>
              <div className="space-y-2">
                {fields.map((field, index) => (
                  <div key={index} className="flex gap-2 items-center bg-slate-800/30 p-2 rounded">
                    <input
                      className="flex-1 px-2 py-1 text-sm bg-slate-900 border border-slate-700 rounded text-white"
                      placeholder="field id (e.g. email)"
                      value={field.id}
                      onChange={(e) => updateField(index, { id: e.target.value })}
                    />
                    <input
                      className="flex-1 px-2 py-1 text-sm bg-slate-900 border border-slate-700 rounded text-white"
                      placeholder="Label"
                      value={field.label}
                      onChange={(e) => updateField(index, { label: e.target.value })}
                    />
                    <select
                      className="px-2 py-1 text-sm bg-slate-900 border border-slate-700 rounded text-white"
                      value={field.type}
                      onChange={(e) => updateField(index, { type: e.target.value as IntakeFormFieldSpec['type'] })}
                    >
                      <option value="text">Text</option>
                      <option value="email">Email</option>
                      <option value="textarea">Textarea</option>
                      <option value="select">Select</option>
                    </select>
                    <label className="flex items-center gap-1 text-xs text-slate-400">
                      <input
                        type="checkbox"
                        checked={field.required}
                        onChange={(e) => updateField(index, { required: e.target.checked })}
                      />
                      req
                    </label>
                    <button type="button" onClick={() => removeField(index)} className="p-1 text-slate-400 hover:text-red-500">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" isLoading={saveMutation.isPending} data-testid="save-intake-form-button">
                {existing ? 'Save Changes' : 'Create Form'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
```

- [ ] **Step 5: Create `web/src/pages/IntakePublicForm.tsx`**

```tsx
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'

interface PublicFormField {
  id: string
  label: string
  type: 'text' | 'email' | 'textarea' | 'select'
  required: boolean
  options?: string[]
}

/**
 * Unauthenticated public submission page for a configured intake form
 * (GET/POST /api/v1/intake/:slug). CAPTCHA (Altcha) widget rendering is a
 * follow-up — forms with captcha_required=true cannot complete a real
 * submission from this page yet (the /submit call requires a solved
 * `altcha` payload the backend verifies; see
 * apps/api/modules/helpdesk/services/altcha.py). Intentionally scoped small
 * per the unified-issues-ui plan: proves the wiring end-to-end for
 * non-CAPTCHA forms, not the full public-facing experience.
 */
export default function IntakePublicForm() {
  const { slug } = useParams<{ slug: string }>()
  const [values, setValues] = useState<Record<string, string>>({})
  const [submitted, setSubmitted] = useState(false)

  const { data: form, isLoading, error } = useQuery({
    queryKey: ['public-intake-form', slug],
    queryFn: () => api.getPublicIntakeForm(slug!),
    enabled: !!slug,
    retry: false,
  })

  const submitMutation = useMutation({
    mutationFn: () => api.submitPublicIntakeForm(slug!, { fields: values }),
    onSuccess: () => setSubmitted(true),
  })

  const handleChange = (fieldId: string, value: string) => {
    setValues((prev) => ({ ...prev, [fieldId]: value }))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    submitMutation.mutate()
  }

  if (isLoading) {
    return <div className="min-h-screen bg-slate-900" />
  }

  if (error || !form) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-900 p-4">
        <Card className="w-full max-w-md">
          <CardContent className="text-center py-12">
            <p className="text-slate-400">This form is not available.</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (submitted) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-900 p-4">
        <Card className="w-full max-w-md">
          <CardContent className="text-center py-12">
            <p className="text-white text-lg font-semibold mb-2">Thank you</p>
            <p className="text-slate-400">Your request has been submitted.</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900 p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <h1 className="text-xl font-semibold text-white">{form.name}</h1>
          {form.description && <p className="text-sm text-slate-400 mt-1">{form.description}</p>}
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4" data-testid="public-intake-form">
            {(form.fields as PublicFormField[]).map((field) => (
              <div key={field.id}>
                <label htmlFor={`field-${field.id}`} className="block text-sm font-medium text-slate-300 mb-1.5">
                  {field.label}{field.required && ' *'}
                </label>
                {field.type === 'textarea' ? (
                  <textarea
                    id={`field-${field.id}`}
                    required={field.required}
                    value={values[field.id] || ''}
                    onChange={(e) => handleChange(field.id, e.target.value)}
                    rows={4}
                    className="block w-full px-4 py-2 text-sm bg-slate-800 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                ) : (
                  <input
                    id={`field-${field.id}`}
                    type={field.type === 'email' ? 'email' : 'text'}
                    required={field.required}
                    value={values[field.id] || ''}
                    onChange={(e) => handleChange(field.id, e.target.value)}
                    className="block w-full px-4 py-2 text-sm bg-slate-800 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                )}
              </div>
            ))}
            {form.captcha_required && (
              <p className="text-xs text-yellow-400">
                This form requires CAPTCHA verification, which is not yet implemented on this page.
              </p>
            )}
            <Button type="submit" isLoading={submitMutation.isPending} disabled={form.captcha_required} className="w-full">
              Submit
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
```

- [ ] **Step 6: Wire the module manifest — `web/src/modules/issues/index.tsx`**

```tsx
import { lazy } from 'react'
import type { RouteObject } from 'react-router-dom'
import type { MenuCategory, MenuItem } from '@penguintechinc/react-libs/components'
import type { FrontendModule } from '../types'
import { AlertCircle, Tag, Flag, FolderKanban, HardDrive } from 'lucide-react'

// eslint-disable react-refresh/only-export-components -- Intentional: exports both lazy components and module manifest
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Issues = lazy(() => import('@/pages/Issues'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const IssueDetail = lazy(() => import('@/pages/IssueDetail'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Projects = lazy(() => import('@/pages/Projects'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const ProjectDetail = lazy(() => import('@/pages/ProjectDetail'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Milestones = lazy(() => import('@/pages/Milestones'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const Labels = lazy(() => import('@/pages/Labels'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const DataStores = lazy(() => import('@/pages/DataStores'))
// eslint-disable-next-line react-refresh/only-export-components -- Intentional: module manifest export
const IntakeForms = lazy(() => import('@/pages/IntakeForms'))
// eslint-enable react-refresh/only-export-components

const navigation: MenuCategory[] = [
  {
    header: 'Tracking',
    collapsible: true,
    items: [
      { name: 'Issues', href: '/issues', icon: AlertCircle },
      { name: 'Labels', href: '/labels', icon: Tag },
      { name: 'Milestones', href: '/milestones', icon: Flag },
      { name: 'Projects', href: '/projects', icon: FolderKanban },
      { name: 'Data Stores', href: '/data-stores', icon: HardDrive },
    ],
  },
]

const adminNav: MenuItem[] = [
  { name: 'Intake Forms', href: '/issues/intake-forms' },
]

const routes: RouteObject[] = [
  { path: 'issues', element: <Issues /> },
  { path: 'issues/intake-forms', element: <IntakeForms /> },
  { path: 'issues/:id', element: <IssueDetail /> },
  { path: 'projects', element: <Projects /> },
  { path: 'projects/:id', element: <ProjectDetail /> },
  { path: 'milestones', element: <Milestones /> },
  { path: 'labels', element: <Labels /> },
  { path: 'data-stores', element: <DataStores /> },
]

const issuesModule: FrontendModule = {
  id: 'issues',
  name: 'Issues & Tracking',
  nav: navigation,
  adminNav,
  routes,
}

export default issuesModule
```

- [ ] **Step 7: Add the public route to `web/src/App.tsx`**

Add near the `Map` lazy import (`web/src/App.tsx:26-28`):

```tsx
// Public intake form submission — lazy-loaded, unauthenticated, kept out
// of the main bundle since it is rarely visited relative to the app shell.
const IntakePublicForm = lazy(() => import('./pages/IntakePublicForm'))
```

Add the route next to `/login`/`/register` (`web/src/App.tsx:95-98`), wrapped in its own `Suspense` (a bare `<Suspense>` cannot be a direct child of `<Route>` in react-router v6 — same reasoning already documented at `web/src/App.tsx:109-112` for the module routes):

```tsx
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/intake/:slug" element={<Suspense fallback={<div />}><IntakePublicForm /></Suspense>} />
        <Route path="/id/:villageId" element={<ProtectedRoute><VillageIdRedirect /></ProtectedRoute>} />
```

- [ ] **Step 8: Create the module manifest test**

Create `web/src/modules/issues/__tests__/module.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import issuesModule from '../index'
import type { FrontendModule } from '../../types'

describe('Issues Module', () => {
  it('should have correct module metadata', () => {
    expect(issuesModule.id).toBe('issues')
    expect(issuesModule.name).toBe('Issues & Tracking')
  })

  it('should have navigation items including Issues', () => {
    expect(issuesModule.nav.length).toBeGreaterThan(0)
    const itemNames = issuesModule.nav[0].items.map((item) => item.name)
    expect(itemNames).toContain('Issues')
  })

  it('should have an Intake Forms admin nav entry', () => {
    expect(issuesModule.adminNav).toBeDefined()
    const adminNames = issuesModule.adminNav?.map((item) => item.name)
    expect(adminNames).toContain('Intake Forms')
  })

  it('should have required routes including the intake-forms admin route', () => {
    const routePaths = issuesModule.routes.map((route) => route.path)
    expect(routePaths).toContain('issues')
    expect(routePaths).toContain('issues/:id')
    expect(routePaths).toContain('issues/intake-forms')
  })

  it('should conform to FrontendModule interface', () => {
    const isValid = (mod: FrontendModule): boolean =>
      typeof mod.id === 'string' &&
      typeof mod.name === 'string' &&
      Array.isArray(mod.nav) &&
      Array.isArray(mod.routes) &&
      (mod.adminNav === undefined || Array.isArray(mod.adminNav))
    expect(isValid(issuesModule)).toBe(true)
  })
})
```

- [ ] **Step 9: Run all new/changed tests, then lint/typecheck**

Run: `cd web && npx vitest run src/pages/IntakeForms.test.tsx src/pages/IntakePublicForm.test.tsx src/modules/issues/__tests__/module.test.ts`
Expected: PASS (all tests)

Run: `cd web && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add web/src/pages/IntakeForms.tsx web/src/pages/IntakeForms.test.tsx web/src/pages/IntakePublicForm.tsx web/src/pages/IntakePublicForm.test.tsx web/src/modules/issues/index.tsx web/src/modules/issues/__tests__/module.test.ts web/src/App.tsx
git commit -m "feat(issues): add intake-form builder admin UI and public submit page"
```

---

## Task 8: Webhook config UI — assignment filters

**Files:**
- Modify: `web/src/pages/Webhooks.tsx`
- Create: `web/src/pages/Webhooks.test.tsx`

**Interfaces:**
- Consumes: `AssigneePicker`, `AssigneeValue` (Task 3), `ISSUE_TYPES` (Task 1), `api.createWebhook`/`api.updateWebhook` (Task 2)
- Produces: no new exports; `Webhooks` default export gains admin gating, an edit modal (previously absent), and `filter_issue_type`/`filter_assignee_type`/`filter_assignee_id` on both create and edit.

- [ ] **Step 1: Write failing tests**

Create `web/src/pages/Webhooks.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import Webhooks from './Webhooks'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getPortalProfile: vi.fn(),
    getWebhooks: vi.fn(),
    getOrganizations: vi.fn().mockResolvedValue({ items: [{ id: 1, name: 'Acme', created_at: '2026-01-01', updated_at: '2026-01-01' }] }),
    getIdentities: vi.fn().mockResolvedValue({ items: [] }),
    createWebhook: vi.fn(),
    updateWebhook: vi.fn(),
    deleteWebhook: vi.fn(),
    testWebhook: vi.fn(),
  },
}))

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <Webhooks />
    </QueryClientProvider>
  )
}

describe('Webhooks admin page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.getPortalProfile).mockResolvedValue({ global_role: 'admin', tenant_role: null })
    vi.mocked(api.getWebhooks).mockResolvedValue({ webhooks: [] })
  })

  it('shows an admin-required message for non-admins', async () => {
    vi.mocked(api.getPortalProfile).mockResolvedValue({ global_role: 'viewer', tenant_role: null })
    renderPage()
    await waitFor(() => screen.getByText(/admin access required/i))
  })

  it('opens the edit modal for an existing webhook with filter fields', async () => {
    vi.mocked(api.getWebhooks).mockResolvedValue({
      webhooks: [{ id: 1, name: 'Support bot', url: 'https://x.example.com', organization_id: 1, events: ['issue.assigned'], enabled: true, filter_issue_type: 'support' }],
    })
    renderPage()
    await waitFor(() => screen.getByText('Support bot'))
    fireEvent.click(screen.getByTitle('Edit webhook'))
    const form = await screen.findByTestId('edit-webhook-form')
    expect(form).toBeInTheDocument()
  })

  it('submits create with filter fields forwarded', async () => {
    vi.mocked(api.createWebhook).mockResolvedValue({ id: 2 })
    renderPage()
    await waitFor(() => screen.getByText('Webhooks'))
    fireEvent.click(screen.getByText('Create Webhook'))
    await waitFor(() => screen.getByLabelText('Name'))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Support bot assignments' } })
    fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://hooks.example.com/x' } })
    fireEvent.click(screen.getByText('Issue Assigned'))
    fireEvent.click(screen.getByText('Create'))
    await waitFor(() => expect(api.createWebhook).toHaveBeenCalled())
    const call = vi.mocked(api.createWebhook).mock.calls[0][0]
    expect(call.name).toBe('Support bot assignments')
    expect(call.events).toContain('issue.assigned')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run src/pages/Webhooks.test.tsx`
Expected: FAIL — no admin gate, no `title="Edit webhook"` button, no `Issue Assigned` event checkbox exists yet.

- [ ] **Step 3: Rewrite `web/src/pages/Webhooks.tsx`**

```tsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Webhook, TestTube, Trash2, CheckCircle, XCircle, Edit } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import Button from '@/components/Button'
import Card, { CardHeader, CardContent } from '@/components/Card'
import Input from '@/components/Input'
import Select from '@/components/Select'
import AssigneePicker, { AssigneeValue } from '@/components/AssigneePicker'
import { ISSUE_TYPES } from '@/lib/constants/issueTypes'
import { Organization } from '@/types'

interface WebhookConfig {
  id: number
  name: string
  url: string
  organization_id: number
  events: string[]
  enabled: boolean
  filter_issue_type?: string
  filter_assignee_type?: 'identity' | 'org_unit'
  filter_assignee_id?: number
}

interface WebhooksResponse {
  webhooks: WebhookConfig[]
}

interface OrganizationResponse {
  items: Organization[]
}

const EVENT_TYPES = [
  { value: 'entity.created', label: 'Entity Created' },
  { value: 'entity.updated', label: 'Entity Updated' },
  { value: 'entity.deleted', label: 'Entity Deleted' },
  { value: 'organization.created', label: 'Organization Created' },
  { value: 'organization.updated', label: 'Organization Updated' },
  { value: 'issue.created', label: 'Issue Created' },
  { value: 'issue.updated', label: 'Issue Updated' },
  { value: 'issue.closed', label: 'Issue Closed' },
  { value: 'issue.assigned', label: 'Issue Assigned' },
]

export default function Webhooks() {
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingWebhook, setEditingWebhook] = useState<WebhookConfig | null>(null)
  const queryClient = useQueryClient()

  const { data: profile } = useQuery({
    queryKey: ['portal-profile'],
    queryFn: () => api.getPortalProfile(),
    staleTime: 60000,
  })
  const isAdmin = profile?.global_role === 'admin' || profile?.tenant_role === 'admin'

  const { data, isLoading } = useQuery({
    queryKey: ['webhooks'],
    queryFn: () => api.getWebhooks() as Promise<WebhooksResponse>,
    enabled: isAdmin,
  })

  const testMutation = useMutation({
    mutationFn: (id: number) => api.testWebhook(id),
    onSuccess: () => toast.success('Test webhook sent'),
    onError: () => toast.error('Failed to send test webhook'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteWebhook(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['webhooks'], refetchType: 'all' })
      toast.success('Webhook deleted')
    },
  })

  if (!isAdmin) {
    return (
      <div className="p-8">
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-slate-400">Admin access required</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">Webhooks</h1>
          <p className="mt-2 text-slate-400">Configure event webhooks and notifications</p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Create Webhook
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-primary-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : data?.webhooks?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <Webhook className="w-12 h-12 text-slate-600 mx-auto mb-4" />
            <p className="text-slate-400">No webhooks configured</p>
            <Button className="mt-4" onClick={() => setShowCreateModal(true)}>
              Create your first webhook
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {data?.webhooks?.map((webhook: WebhookConfig) => (
            <Card key={webhook.id}>
              <CardContent>
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-3 mb-2">
                      {webhook.enabled ? (
                        <CheckCircle className="w-5 h-5 text-green-400" />
                      ) : (
                        <XCircle className="w-5 h-5 text-slate-500" />
                      )}
                      <h3 className="text-lg font-semibold text-white">{webhook.name}</h3>
                    </div>
                    <p className="text-sm text-slate-400 mb-3 font-mono">{webhook.url}</p>
                    <div className="flex flex-wrap gap-2">
                      {webhook.events?.map((event: string) => (
                        <span key={event} className="px-2 py-1 text-xs bg-primary-500/20 text-primary-400 rounded">
                          {event}
                        </span>
                      ))}
                      {webhook.filter_issue_type && (
                        <span className="px-2 py-1 text-xs bg-yellow-500/20 text-yellow-400 rounded">
                          type={webhook.filter_issue_type}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" variant="ghost" title="Edit webhook" onClick={() => setEditingWebhook(webhook)}>
                      <Edit className="w-4 h-4" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => testMutation.mutate(webhook.id)}>
                      <TestTube className="w-4 h-4" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => deleteMutation.mutate(webhook.id)}>
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {showCreateModal && (
        <CreateWebhookModal
          onClose={() => setShowCreateModal(false)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({ queryKey: ['webhooks'], refetchType: 'all' })
            setShowCreateModal(false)
          }}
        />
      )}

      {editingWebhook && (
        <EditWebhookModal
          webhook={editingWebhook}
          onClose={() => setEditingWebhook(null)}
          onSuccess={async () => {
            await queryClient.invalidateQueries({ queryKey: ['webhooks'], refetchType: 'all' })
            setEditingWebhook(null)
          }}
        />
      )}
    </div>
  )
}

interface CreateWebhookModalProps {
  onClose: () => void
  onSuccess: () => Promise<void>
}

function AssignmentFilters({
  filterIssueType, onFilterIssueTypeChange, filterAssignee, onFilterAssigneeChange,
}: {
  filterIssueType: string
  onFilterIssueTypeChange: (v: string) => void
  filterAssignee: AssigneeValue | null
  onFilterAssigneeChange: (v: AssigneeValue | null) => void
}) {
  return (
    <div className="pt-4 border-t border-slate-700">
      <p className="text-sm font-medium text-slate-300 mb-3">
        Assignment Filters (issue.assigned events only)
      </p>
      <div className="space-y-3">
        <Select label="Issue Type" value={filterIssueType} onChange={(e) => onFilterIssueTypeChange(e.target.value)}>
          <option value="">Any issue type</option>
          {ISSUE_TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </Select>
        <div>
          <label className="block text-sm font-medium text-slate-300 mb-1.5">Assignee</label>
          <AssigneePicker value={filterAssignee} onChange={onFilterAssigneeChange} />
        </div>
      </div>
    </div>
  )
}

function CreateWebhookModal({ onClose, onSuccess }: CreateWebhookModalProps) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [orgId, setOrgId] = useState('')
  const [selectedEvents, setSelectedEvents] = useState<string[]>([])
  const [secret, setSecret] = useState('')
  const [filterIssueType, setFilterIssueType] = useState('')
  const [filterAssignee, setFilterAssignee] = useState<AssigneeValue | null>(null)

  const { data: orgs } = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.getOrganizations() as Promise<OrganizationResponse>,
  })

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => api.createWebhook(data as Parameters<typeof api.createWebhook>[0]),
    onSuccess: () => {
      toast.success('Webhook created')
      onSuccess()
    },
  })

  const toggleEvent = (event: string) => {
    setSelectedEvents((prev) => (prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const webhookData: Parameters<typeof api.createWebhook>[0] = {
      name,
      url,
      organization_id: orgId ? parseInt(orgId) : undefined,
      events: selectedEvents,
      secret: secret || undefined,
      enabled: true,
      filter_issue_type: filterIssueType || undefined,
      filter_assignee_type: filterAssignee?.assignee_type,
      filter_assignee_id: filterAssignee?.assignee_id,
    }
    createMutation.mutate(webhookData)
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Create Webhook</h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input label="Name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Production Webhook" />
            <Input label="URL" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/webhook" />
            <Select
              label="Organization"
              value={orgId}
              onChange={(e) => setOrgId(e.target.value)}
              options={[{ value: '', label: 'Select organization' }, ...(orgs?.items || []).map((o: Organization) => ({ value: String(o.id), label: o.name }))]}
            />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Events</label>
              <div className="space-y-2">
                {EVENT_TYPES.map((event) => (
                  <label key={event.value} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedEvents.includes(event.value)}
                      onChange={() => toggleEvent(event.value)}
                      className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                    />
                    <span className="text-sm text-slate-300">{event.label}</span>
                  </label>
                ))}
              </div>
            </div>
            <Input label="Secret (optional)" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Webhook signing secret" />
            <AssignmentFilters
              filterIssueType={filterIssueType}
              onFilterIssueTypeChange={setFilterIssueType}
              filterAssignee={filterAssignee}
              onFilterAssigneeChange={setFilterAssignee}
            />
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" isLoading={createMutation.isPending}>Create</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

interface EditWebhookModalProps {
  webhook: WebhookConfig
  onClose: () => void
  onSuccess: () => Promise<void>
}

function EditWebhookModal({ webhook, onClose, onSuccess }: EditWebhookModalProps) {
  const [name, setName] = useState(webhook.name)
  const [url, setUrl] = useState(webhook.url)
  const [selectedEvents, setSelectedEvents] = useState<string[]>(webhook.events || [])
  const [filterIssueType, setFilterIssueType] = useState(webhook.filter_issue_type || '')
  const [filterAssignee, setFilterAssignee] = useState<AssigneeValue | null>(
    webhook.filter_assignee_id && webhook.filter_assignee_type
      ? { assignee_type: webhook.filter_assignee_type, assignee_id: webhook.filter_assignee_id }
      : null
  )

  const updateMutation = useMutation({
    mutationFn: (data: Parameters<typeof api.updateWebhook>[1]) => api.updateWebhook(webhook.id, data),
    onSuccess: async () => {
      toast.success('Webhook updated')
      await onSuccess()
    },
    onError: () => toast.error('Failed to update webhook'),
  })

  const toggleEvent = (event: string) => {
    setSelectedEvents((prev) => (prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    updateMutation.mutate({
      name,
      url,
      events: selectedEvents,
      filter_issue_type: filterIssueType || undefined,
      filter_assignee_type: filterAssignee?.assignee_type,
      filter_assignee_id: filterAssignee?.assignee_id,
    })
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <CardHeader>
          <h2 className="text-xl font-semibold text-white">Edit Webhook</h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4" data-testid="edit-webhook-form">
            <Input label="Name" required value={name} onChange={(e) => setName(e.target.value)} />
            <Input label="URL" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} />
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">Events</label>
              <div className="space-y-2">
                {EVENT_TYPES.map((event) => (
                  <label key={event.value} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedEvents.includes(event.value)}
                      onChange={() => toggleEvent(event.value)}
                      className="w-4 h-4 bg-slate-900 border-slate-700 rounded text-primary-500"
                    />
                    <span className="text-sm text-slate-300">{event.label}</span>
                  </label>
                ))}
              </div>
            </div>
            <AssignmentFilters
              filterIssueType={filterIssueType}
              onFilterIssueTypeChange={setFilterIssueType}
              filterAssignee={filterAssignee}
              onFilterAssigneeChange={setFilterAssignee}
            />
            <div className="flex justify-end gap-3 mt-6">
              <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
              <Button type="submit" isLoading={updateMutation.isPending} data-testid="save-webhook-button">
                Save Changes
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass, then lint/typecheck**

Run: `cd web && npx vitest run src/pages/Webhooks.test.tsx`
Expected: PASS (3 tests)

Run: `cd web && npm run typecheck && npm run lint`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Webhooks.tsx web/src/pages/Webhooks.test.tsx
git commit -m "feat(webhooks): admin-gate the page, add an edit modal, and add issue_type/assignee assignment filters"
```

---

## Task 9: CRM entity types — `customer_company` + `customer_contact` dropdowns

**Files:**
- Create: `web/src/lib/constants/organizationTypes.ts`
- Create: `web/src/lib/constants/organizationTypes.test.ts`
- Modify: `web/src/pages/Organizations.tsx`
- Create: `web/src/pages/Organizations.test.tsx`
- Modify: `web/src/pages/OrganizationDetail.tsx:1342-1348`
- Modify: `web/src/pages/IAM.tsx:59-67`
- Modify: `web/src/components/CreateIdentityModal.tsx:16-24`

**Interfaces:**
- Consumes: `IDENTITY_TYPES` (Task 1), `OrganizationType` (Task 1)
- Produces: `ORGANIZATION_TYPES: OrganizationTypeOption[]` — the single source of truth `Organizations.tsx` and `OrganizationDetail.tsx` both import instead of each hand-rolling their own array.

- [ ] **Step 1: Write a failing test for the new constant**

Create `web/src/lib/constants/organizationTypes.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { ORGANIZATION_TYPES } from './organizationTypes'

describe('organizationTypes constants', () => {
  it('includes customer_company alongside the existing organization types', () => {
    const values = ORGANIZATION_TYPES.map((t) => t.value)
    expect(values).toEqual(['department', 'organization', 'team', 'collection', 'customer_company', 'other'])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/lib/constants/organizationTypes.test.ts`
Expected: FAIL — `Cannot find module './organizationTypes'`

- [ ] **Step 3: Create `web/src/lib/constants/organizationTypes.ts`**

```ts
import type { OrganizationType } from '@/types'

export interface OrganizationTypeOption {
  value: OrganizationType
  label: string
}

/**
 * Canonical organization-type dropdown options — single source of truth for
 * Organizations.tsx (create) and OrganizationDetail.tsx (edit). Previously
 * only OrganizationDetail.tsx had this list, inline, and Organizations.tsx's
 * create form had no type field at all.
 */
export const ORGANIZATION_TYPES: OrganizationTypeOption[] = [
  { value: 'department', label: 'Department' },
  { value: 'organization', label: 'Organization' },
  { value: 'team', label: 'Team' },
  { value: 'collection', label: 'Collection' },
  { value: 'customer_company', label: 'Customer Company' },
  { value: 'other', label: 'Other' },
]
```

- [ ] **Step 4: Write failing tests for `Organizations.tsx`**

Create `web/src/pages/Organizations.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Organizations from './Organizations'
import api from '@/lib/api'

vi.mock('@/lib/api', () => ({
  default: {
    getOrganizations: vi.fn().mockResolvedValue({ items: [] }),
    createOrganization: vi.fn(),
  },
}))

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Organizations />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('Organizations create form', () => {
  beforeEach(() => vi.clearAllMocks())

  it('offers Customer Company as an organization type', async () => {
    renderPage()
    fireEvent.click(screen.getByText('Create Organization Unit'))
    await waitFor(() => screen.getByText('Customer Company'))
  })

  it('submits organization_type along with name/description', async () => {
    vi.mocked(api.createOrganization).mockResolvedValue({ id: 1 })
    renderPage()
    fireEvent.click(screen.getByText('Create Organization Unit'))
    await waitFor(() => screen.getByLabelText('Name'))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Acme Support' } })
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'customer_company' } })
    fireEvent.click(screen.getByText('Create'))
    await waitFor(() => expect(api.createOrganization).toHaveBeenCalled())
    expect(vi.mocked(api.createOrganization).mock.calls[0][0]).toMatchObject({
      name: 'Acme Support',
      organization_type: 'customer_company',
    })
  })
})
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `cd web && npx vitest run src/pages/Organizations.test.tsx`
Expected: FAIL — no `Type` field exists in `orgFields` yet.

- [ ] **Step 6: Add the `organization_type` field to `web/src/pages/Organizations.tsx`**

Add the import and update `orgFields` (`web/src/pages/Organizations.tsx:1-32`):

```tsx
import { useState, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Plus, Search, Trash2, Edit } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { invalidateCache } from '@/lib/invalidateCache'
import { confirmDelete } from '@/lib/confirmActions'
import type { Organization } from '@/types'
import Button from '@/components/Button'
import Card, { CardContent } from '@/components/Card'
import Input from '@/components/Input'
import { FormModalBuilder, FormField } from '@penguintechinc/react-libs/components'
import { ORGANIZATION_TYPES } from '@/lib/constants/organizationTypes'

// Form fields for organization creation
const orgFields: FormField[] = [
  {
    name: 'name',
    label: 'Name',
    type: 'text',
    required: true,
    placeholder: 'Enter organization name'
  },
  {
    name: 'organization_type',
    label: 'Type',
    type: 'select',
    required: true,
    defaultValue: 'organization',
    options: ORGANIZATION_TYPES,
  },
  {
    name: 'description',
    label: 'Description',
    type: 'textarea',
    placeholder: 'Enter description (optional)',
    rows: 3
  }
]
```

Update `createMutation`/`updateMutation`/`editFields`/`handleCreate`/`handleUpdate` (`web/src/pages/Organizations.tsx:55-143`):

```tsx
  const createMutation = useMutation({
    mutationFn: (data: { name: string; description?: string; organization_type?: string; parent_id?: number }) =>
      api.createOrganization(data),
    onSuccess: async () => {
      await invalidateCache.organizations(queryClient)
      setShowCreateModal(false)
      toast.success('Organization created successfully')
      if (initialParentId) {
        navigate('/organizations', { replace: true })
      }
    },
    onError: (error) => {
      console.error('Create organization error:', error)
      toast.error('Failed to create organization')
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: { name: string; description?: string; organization_type?: string } }) =>
      api.updateOrganization(id, data),
    onSuccess: async () => {
      await invalidateCache.organizations(queryClient)
      setEditingOrg(null)
      toast.success('Organization updated successfully')
    },
    onError: () => {
      toast.error('Failed to update organization')
    },
  })

  const handleDelete = (id: number, name: string) => {
    confirmDelete(name, () => deleteMutation.mutate(id))
  }

  // Edit form fields with current values as defaults
  const editFields: FormField[] = useMemo(() => {
    if (!editingOrg) return orgFields
    return [
      {
        name: 'name',
        label: 'Name',
        type: 'text',
        required: true,
        placeholder: 'Enter organization name',
        defaultValue: editingOrg.name,
      },
      {
        name: 'organization_type',
        label: 'Type',
        type: 'select',
        required: true,
        defaultValue: editingOrg.organization_type || 'organization',
        options: ORGANIZATION_TYPES,
      },
      {
        name: 'description',
        label: 'Description',
        type: 'textarea',
        placeholder: 'Enter description (optional)',
        rows: 3,
        defaultValue: editingOrg.description || '',
      }
    ]
  }, [editingOrg])

  const handleCreate = (formData: Record<string, unknown>) => {
    const data: { name: string; description?: string; organization_type?: string; parent_id?: number } = {
      name: formData.name as string,
      description: (formData.description as string) || undefined,
      organization_type: formData.organization_type as string,
    }
    if (initialParentId) {
      data.parent_id = parseInt(initialParentId)
    }
    createMutation.mutate(data)
  }

  const handleUpdate = (formData: Record<string, unknown>) => {
    if (!editingOrg) return
    updateMutation.mutate({
      id: editingOrg.id,
      data: {
        name: formData.name as string,
        description: (formData.description as string) || undefined,
        organization_type: formData.organization_type as string,
      }
    })
  }
```

- [ ] **Step 7: Dedup `OrganizationDetail.tsx`'s inline `ORGANIZATION_TYPES`**

Replace `web/src/pages/OrganizationDetail.tsx:1342-1348`:

```tsx
  const updateMutation = useMutation({
```

with an import (add near the top of the file's import block) and delete the inline `const ORGANIZATION_TYPES = [...]` declaration entirely:

```tsx
import { ORGANIZATION_TYPES } from '@/lib/constants/organizationTypes'
```

The rest of `EditOrganizationModal` (the hand-rolled `<select>` at lines 1386-1402) is unchanged — it already maps `ORGANIZATION_TYPES.map((type) => <option value={type.value}>{type.label}</option>)`, which works identically against the imported array.

- [ ] **Step 8: Dedup `IAM.tsx`'s inline `IDENTITY_TYPES`**

Replace `web/src/pages/IAM.tsx:59-67`:

```tsx
import { IDENTITY_TYPES } from '@/lib/constants/identityTypes'
```

(Add this import near the top of the file, delete the inline `const IDENTITY_TYPES = [...]` block. Every existing usage — `IDENTITY_TYPES.map(t => ...)`, `IDENTITY_TYPES.find(t => t.value === type)` at `web/src/pages/IAM.tsx:546,585,590,763,912,928,937,1104,1144,1600` — keeps working unchanged since the shared array's shape is a superset of what IAM.tsx used inline.)

- [ ] **Step 9: Dedup `CreateIdentityModal.tsx`'s inline `IDENTITY_TYPES`**

Replace `web/src/components/CreateIdentityModal.tsx:16-24`:

```tsx
import { IDENTITY_TYPES } from '@/lib/constants/identityTypes'
```

(Delete the inline `const IDENTITY_TYPES = [...]` block; add the import near the top with the other imports. The `options: IDENTITY_TYPES` usage at `web/src/components/CreateIdentityModal.tsx:122` is unchanged — `FormField.options` only needs `{value, label}`, and the shared array's extra `icon`/`color` properties are structurally fine to pass through since it's a typed variable, not an object literal.)

- [ ] **Step 10: Run all Task 9 tests, then lint/typecheck**

Run: `cd web && npx vitest run src/lib/constants/organizationTypes.test.ts src/pages/Organizations.test.tsx`
Expected: PASS (3 tests)

Run: `cd web && npm run typecheck && npm run lint`
Expected: no errors — confirms `IAM.tsx`/`CreateIdentityModal.tsx`/`OrganizationDetail.tsx` still compile against the shared constants.

- [ ] **Step 11: Commit**

```bash
git add web/src/lib/constants/organizationTypes.ts web/src/lib/constants/organizationTypes.test.ts web/src/pages/Organizations.tsx web/src/pages/Organizations.test.tsx web/src/pages/OrganizationDetail.tsx web/src/pages/IAM.tsx web/src/components/CreateIdentityModal.tsx
git commit -m "feat(crm): add customer_company/customer_contact dropdown options, dedup type constants"
```

---

## Post-Plan Notes (not tasks — read before executing)

- **Backend Pydantic `identity_type` restriction** (Global Constraints) blocks a real end-to-end `customer_contact` identity creation today. Task 9 ships the correct frontend dropdown regardless; raise the backend fix (`apps/api/models/pydantic/identity.py:16` — widen `IdentityType` to match `apps/api/models/identity.py::IdentityType`) as a follow-up, it is not part of this frontend-only plan.
- **Altcha CAPTCHA widget** on `IntakePublicForm.tsx` (Task 7) is explicitly out of scope — forms with `captcha_required=true` render a submit-disabled notice rather than a working solve flow. Follow-up plan needed before any `captcha_required` intake form can be used by a real customer.
- **`/helpdesk` is untouched** — this plan is additive per the design brief. Retiring the legacy helpdesk ticket surface in favor of the unified Issues UI is a separate, later plan.
