# Design: Fold Helpdesk Tickets into Issues (one unified Issue model)

**Date:** 2026-08-06
**Status:** Design — pending review
**Owner:** Justin Bowen
**Related:** Elder v4.0 platform-merge (absorbed Ruffled helpdesk). Pre-fixes landed in #232 (Issues ungate + PATCH status-case).

## 1. Goal

There is **no separate "ticket" concept**. A support ticket is **just an Issue** (`issue_type = support`). The absorbed Ruffled helpdesk collapses into Elder's native **`issues`** model: one table, one API, one backlog. Support-only fields become nullable columns on `issues`; helpdesk's duplicate concepts (teams, tags, threads, attachments) merge into Elder-native tables.

**Guiding principle (per review):** *leverage Elder-native objects/resources/tables wherever possible; merge similar databases and concepts rather than carrying Ruffled's parallel ones.*

Delivered in **two phases**:

- **Phase A (demo-critical):** a presentation merge — one unified Issues surface with **full CRUD** that shows helpdesk tickets **as** support-issues, over the two *existing* backends. **No schema change.** Low-risk, shippable for the imminent demo.
- **Phase B (post-demo):** migrate `hd_tickets` **into** `issues`, add the support columns, converge the duplicate concepts, and build the two net-new features (intake forms, assignment webhooks). Retire the `hd_*` tables.

## 2. Current state (why this is non-trivial)

Both modules run on **penguin-dal** (PyDAL) over `current_app.db`; SQLAlchemy models define the schema. They diverge:

| Aspect | **Issues** (`issues`) — the survivor | **Tickets** (`hd_tickets`) — folds in |
|---|---|---|
| Title | `title` | `subject` |
| Status | `open · in_progress · resolved · closed` (stored UPPERCASE) | `new · open · pending · on_hold · resolved · closed` (lowercase) |
| Priority | `low · medium · high · critical` | `low · medium · high · urgent · critical` |
| Type | `issue_type` (10 values) — **add `support`** | free-text `category` + `channel` |
| Reporter | `reporter_id` → identities | `requester_identity_id` **or** `requester_contact_id` (external) |
| Assignee | `assignee_id` → identities (individual only) | `assignee_identity_id` + `hd_team_id` |
| Grouping | project · milestone · labels (M2M) · entity links · org | company (via contact) · team · `tags` (JSON) |
| Support extras | — | **SLA · CRM · email channel · public forms · canned responses** |
| Thread | `issue_comments` | `hd_ticket_messages` (internal notes, email threading) |
| Attachments | — | `hd_ticket_attachments` |
| **Tenant scoping** | ❌ **none** (no `tenant_id`; `GET /issues` unfiltered → cross-tenant read leak) | ✅ fully scoped + IDOR guards |
| Activity/history | ❌ none | partial (message rows) |

**Pre-existing defects folded into this work (not silently carried):**

| Defect | Location | Phase |
|---|---|---|
| Issues has no tenant scoping (read leak) — now the everything-table, so critical | `issues.py` | **A** facade enforces → **B** column |
| `created_by_id`/`assigned_to_id` (migration 001) vs `reporter_id`/`assignee_id` (model/routes) never reconciled | `alembic 001` vs `issue.py` | **B** |
| `apply_sla_policy` called un-awaited + passed a Row not id → SLA never applied | `helpdesk/tickets.py:219`, `ticket_forms.py:668` | **B** |
| FE↔BE drifts: `updateIssue` PUT (BE PATCH only); label POST body; TicketList status filter uses invalid `in_progress` | `web/src/lib/api.ts`, `TicketList.tsx` | **A** |
| `parent_issue_id` declared but unimplemented (FE subtasks `[]`) | `pydantic/issue.py` | **B** self-FK or drop |
| No activity/history log | — | **B** |
| `update_issue` 500 on lowercase status | issues.py | ✅ **fixed #232** |

## 3. Decisions (from brainstorming, 2026-08-06)

| Decision | Choice |
|---|---|
| Model | **One `issues` table.** Support ticket = `issue_type='support'`. No `work_items`, no `kind` discriminator |
| Timing | Presentation merge **in the demo**; migration **after** |
| Demo scope | **Full CRUD** through the unified surface |
| Architecture | **Backend facade** (Phase A) normalizes both backends server-side; Phase B removes the facade's fan-out once `hd_tickets` is migrated |
| Assignee | **Single polymorphic assignee** — an **identity** *or* an **organizational unit** (both nullable is N/A; one assignee, one of two target types). Applies to every issue |
| Person refs use **identities** (not "users") | assignees/reporters may be **non-human** identities (service accounts, bots); `identities` already covers human + non-human + external-system principals |
| Org units | Reuse Elder **`organizations`** (`type ∈ department/organization/team/collection/other`, tenant-scoped, hierarchical). Helpdesk `hd_teams` is a **duplicate** → retire into `organizations` |
| Native reuse | Merge duplicate helpdesk concepts into native tables (§5) |
| Missing fields | Any helpdesk ticket field absent from issues is **added to `issues`** (nullable) |
| Intake forms | Generalize `hd_ticket_forms` → **issue intake forms**; default **private**, optional public; public requires **Altcha** captcha (§6) |
| Assignment webhooks | Extend native **`webhooks`** module: fire on `issue.assigned`, multiple configs filtered by `issue_type` + assignee (§7) |
| License gating | Scope-only; tier model (quota/seat/SSO/MFA/KMS) — §8. Issues Enterprise gates removed (#232) |
| **Village IDs** | **Every object gets a unique `village_id`** (`VillageIDMixin`, `generate_village_id(tenant_id, redis)`) — issues, comments, attachments, intake forms, webhook configs, and any workflow/workflow-item. Tables lacking it today (`issue_comments`, `hd_ticket_messages`, `hd_ticket_attachments`, `hd_ticket_forms`, native `webhooks`) **get it added**; migration backfills existing rows |

## 4. Target model — the extended Issue

`issues` gains the support columns (nullable; populated when `issue_type='support'`) and a polymorphic assignee. **App:** all = every issue; sup = support-origin only.

### 4.1 Assignee (polymorphic, all issues)

| Field | Type | ← Issue | ← Ticket | Notes |
|---|---|---|---|---|
| `assignee_type` | enum(`identity`,`org_unit`) | (derive: `identity` if `assignee_id` set) | `org_unit` if `hd_team_id` set else `identity` | which target table |
| `assignee_id` | int (poly) | `assignee_id` → identities | `assignee_identity_id` → identities, **or** `hd_team_id` → organizations (via §5 migration) | the assignee |
| `reporter_type` | enum(`identity`,`contact`) | `identity` | `identity` or `contact` | who reported/requested |
| `reporter_id` | int (poly) | `reporter_id` → identities | `requester_identity_id` → identities **or** `requester_contact_id` → contacts | |

- **Identity** → `identities` (human **or** non-human). **Org unit** → `organizations` (any type; a "team" is `type='team'`).
- Ticket's current dual (team + individual) **collapses** to one polymorphic assignee; migration rule: prefer the individual assignee if set, else the team (→ org_unit). Flag if simultaneous team+person must be preserved (would need a secondary field).
- UI picker = **one search box across identities + organizational units** (§8.2).

### 4.2 Core + support fields on `issues`

| Field | Type | App | Source / disposition |
|---|---|---|---|
| `tenant_id` | int FK tenants | all | **ADD** (backfill via org→tenant) — fixes the leak |
| `title` | string(500) | all | issue `title`; ticket `subject`→title |
| `issue_type` | enum + **`support`** | all | add `support` value |
| `channel` | enum(web/email/api) | sup | **ADD** from ticket `channel` (provenance for support-origin) |
| `category` | string(100) | sup | **ADD** from ticket `category` (support sub-category; distinct from issue_type) |
| `requester_contact_id` | int FK contacts | sup | **ADD** from ticket `requester_contact_id` (external requester) |
| `hd_sla_policy_id` | int FK sla_policies | sup | **ADD** from ticket |
| `sla_breach_at`/`first_response_at`/`resolved_at` | datetime tz | sup | **ADD** from ticket |
| `village_id`, `closed_at`, `closed_by_id`, `due_date`, `is_incident`, `resource_type`/`resource_id`, `organization_id`, `created_at`/`updated_at` | — | all/issue | already on issues |

### 4.3 Unified status / priority vocab

**Status** (display order): `new · open · in_progress · pending · resolved · closed`

| Unified | ← Issue (UPPERCASE) | ← Ticket (lowercase) |
|---|---|---|
| new | — | new |
| open | OPEN | open |
| in_progress | IN_PROGRESS | — |
| pending | — | pending, on_hold |
| resolved | RESOLVED | resolved |
| closed | CLOSED | closed |

Writes map to each backend's native value **and case** (case bug fixed #232). **Priority:** `low·medium·high·urgent·critical` (issues gain `urgent`).

## 5. Concept convergence (reuse Elder-native, merge duplicates)

| Helpdesk (Ruffled) | Elder-native target | Disposition |
|---|---|---|
| `hd_teams` (+ `hd_team_members`) | **`organizations`** (`type='team'`) + org membership | **Retire** → organizations [decided] |
| `hd_companies` | `organizations` (customer/account org unit) | **Candidate** — confirm (customer-account vs internal-org semantics) in Phase-B detail |
| `hd_contacts` | `identities` (already carries optional `identity_id`) | **Candidate** — external non-login contacts as identities vs a lightweight `contacts` table — confirm |
| `tags` (JSON on ticket) | issue **`labels`** (native M2M) | **Converge** → labels; migrate tag strings to labels |
| `hd_ticket_messages` | **`issue_comments`** (add `is_internal`, email-threading cols) | **Merge** → issue_comments |
| `hd_ticket_attachments` | *(no native table)* → generalize to **`issue_attachments`** | **Generalize** (there is no native attachments table today) |
| `hd_sla_policies`, `hd_canned_responses`, `hd_email_accounts` | *(no native equivalent)* | **Keep**, repoint FKs to `issues` |
| `hd_ticket_forms` | **issue intake forms** (§6) | **Generalize** + Altcha |
| `webhooks` (native) | native webhooks module | **Extend** for assignment events (§7) |

## 6. Intake forms (net-new, generalizes `hd_ticket_forms`)

Configurable intake screens that create Issues on submit.

- **Form config:** `village_id` (unique), name, `slug`, ordered field definitions (JSON), the `issue_type` it files as (default `support`), default assignee (identity/OU), `is_active`.
- **Visibility:** **default private** (auth required); optionally **public** (unauthenticated endpoint at `/api/v1/intake/{slug}`).
- **Captcha:** public forms **require Altcha** (altcha.org — open-source, self-hostable, privacy-friendly proof-of-work; replaces the old forms' Turnstile/reCAPTCHA which don't fit the open-source posture). Config: `captcha_provider='altcha'`, server verifies the Altcha solution before creating the issue. Private forms: no captcha.
- **Submit flow:** validate fields → (if public) verify Altcha → upsert requester contact (external) → create Issue (`issue_type` per form, support fields populated) → apply default assignee → fire `issue.assigned` webhook if assigned (§7).
- Reuses helpdesk's public-form plumbing (slug routing, field JSON) but files a native Issue, not an `hd_ticket`.

## 7. Assignment webhooks (extend native `webhooks` module)

Native `webhooks` table already stores `{events: JSON, organization_id, event_type, url, …}`. Extend it:

- **New event:** `issue.assigned` — emitted whenever an issue's assignee changes (create-with-assignee, PATCH assignee, form default-assign, reassignment).
- **Filters (per webhook, additive):** `issue_type` (e.g. `support`) **and/or** assignee (identity id or org-unit id). Stored on the webhook config (extend the row or its filter JSON). The `webhooks` table also **gains a `village_id`** (it lacks one today).
- **Multiple configs:** each webhook independently filtered — e.g. *(a)* any issue assigned to **OU-X** → OU-X's webhook; *(b)* `issue_type=support` assigned to the **support-bot identity** → the support-bot webhook.
- **Dispatch:** on assignment, evaluate all tenant webhooks whose filters match → POST a signed payload (issue id, type, status, assignee {type,id}, actor, timestamp). Reuse existing delivery/retry/signing.
- **Matching semantics:** a webhook with only an OU filter matches any issue assigned to that OU regardless of type; one with `issue_type=support` + support-bot matches only those. Empty filter = all assignments in the tenant.

## 8. Phase A — demo (facade + unified Issues UI, no schema change)

### 8.1 Facade `/api/v1/work` (transitional)
Reads `issues` + `hd_tickets`, **presents everything as Issues** (tickets normalized to `issue_type=support`), **tenant-scoped on every call** (closes the leak on this path). Full CRUD dispatches to the correct backend by origin. ID namespacing `issue:{id}` / `ticket:{id}` while two tables exist. (In Phase B, once `hd_tickets` is migrated, the facade collapses to plain `/api/v1/issues`.)

| Endpoint | Behavior |
|---|---|
| `GET /work/items` | Merge both (tenant-scoped), normalize to §4, filter (`issue_type`, status, priority, assignee=identity/OU), paginate |
| `GET/POST/PATCH/DELETE /work/items[/{origin}/{id}]` | Dispatch by origin; create defaults new items to the `issues` backend |

### 8.2 UI — unified Issues surface
- **List:** all issues incl. support (badge for `issue_type=support`), unified status/priority filters, assignee filter (identity **or** OU), search.
- **Detail:** shared header (title/status/priority/**assignee via combined identity+OU search**/thread) + support section (requester/channel/SLA) when `issue_type=support`.
- **Create/Edit:** one form; assignee picker = **one search box over identities + organizational units**; support fields shown when type=support.
- Fix FE drifts (PATCH not PUT, label body, valid status vocab). Existing `/issues` and `/helpdesk` stay (additive).

### 8.3 Phase-A excludes
No schema change, no data migration, no `hd_*` retirement. **Org-unit assignment persists only for tickets in A** (issues have no OU-assignee column yet) — see §11 open question. **Intake forms + assignment webhooks are Phase B** (net-new backend) unless explicitly pulled into the demo.

## 9. Phase B — post-demo (migrate into `issues`)

1. **Add columns** to `issues`: `tenant_id`, polymorphic assignee (`assignee_type`/`assignee_id`), `channel`, `category`, `requester_contact_id`, SLA fields, `parent_issue_id` (self-FK); add `support` to `issue_type` and `urgent` to priority. Add `issue_attachments` (**with `village_id`**); extend `issue_comments` (`is_internal`, email cols, **add `village_id`**). Add `village_id` (VillageIDMixin) to every object lacking it — intake forms, webhook configs, comments, attachments.
   - **Backfill `village_id`** for all pre-existing rows that lack one (`issue_comments`, migrated messages, attachments, forms, webhooks) via `generate_village_id(tenant_id, redis)` — every object ends up uniquely addressable.
2. **Migrate `hd_teams` → `organizations`** (`type=team`) + membership; resolve `hd_companies`/`hd_contacts` convergence (§5).
3. **Backfill `hd_tickets` → `issues`** (`issue_type=support`; `tenant_id` direct; map status/priority/case; `subject`→`title`; `hd_team_id`→OU assignee; tags→labels; messages→`issue_comments`; attachments→`issue_attachments`). Keep `legacy_id` for FK remap. **Also backfill `tenant_id` for existing issues** via org→tenant (fixes the leak).
4. Repoint `hd_sla_policies`/`hd_canned_responses`/`hd_email_accounts`/forms FKs to `issues`.
5. Build **intake forms** (§6) + **assignment webhooks** (§7).
6. **Dual-write** window; then flip reads to `issues`, retire the facade fan-out; drop `hd_tickets` (+ retired `hd_*`) after a bake period.
7. Fold in the §2 defects (tenant enforcement, `created_by_id`↔`reporter_id`, SLA await, parent self-FK, activity log).

## 10. Licensing / quota (context, mostly out of scope)

Tier-based (not per-route — why #232 removed the Issues gates):

| Tier | Pricing | Objects | Tenants | Admins | SSO | MFA enforce | Encryption key |
|---|---|---|---|---|---|---|---|
| Free | free | 100 cap | 1 | 1 | — | — | local |
| Pro | per-seat (≤150) | 5,000 cap | 1 | multi | + Google | ✓ | local |
| Enterprise | per-object metered (~1000 batches) | metered | multi | multi | + SAML/OIDC/LDAP | ✓ | external KMS (AWS/Azure/GCP) |

An issue (incl. support) is a metered **object** — counts toward Free 100-cap, Pro 5,000 ceiling, Enterprise per-object billing. Counting must be accurate/auditable. **Quota/billing enforcement is separate work** — noted, not built here.

## 11. Testing strategy

- **Phase A:** status/priority normalization (both directions, all values); assignee-shape tests (identity vs OU); facade CRUD-dispatch by origin; **tenant-scoping regression** (facade rejects missing tenant, never returns cross-tenant rows); merged pagination; FE smoke (unified list + create/edit with combined assignee picker).
- **Phase B:** migration round-trip (every ticket→issue + child links + `hd_teams`→`organizations` via legacy map); OU-assignment on issues (new); intake-form submit (private auth + public **Altcha verify**, issue created with support fields); assignment-webhook matching matrix (OU-only filter, type+assignee filter, empty filter, non-match); dual-write consistency; re-run all issues + helpdesk suites against `issues`.
- 90%+ coverage; regression test per fixed defect referencing this doc.

## 12. Risks / open questions

- **Full-CRUD-over-two-backends in Phase A** is the riskiest demo choice — mitigate with heavy normalization/dispatch unit tests before the UI.
- **OU-assignment asymmetry in Phase A:** issues have no OU-assignee column, so issue OU-assignment is Phase B. Phase A: hide/disable the OU option for native issues, or a small schema touch now? **Recommend hide-in-A.**
- **CRM convergence** (`hd_companies`→`organizations`, `hd_contacts`→`identities`) has real semantic tension (internal org model vs external customer accounts/contacts) — confirm in Phase-B detailed design.
- **Ticket dual-assignment collapse** (team + person → single polymorphic assignee): confirm losing simultaneous team+person is acceptable, else add a secondary assignee field.
- **Altcha** assumed as the public-form captcha — confirm.
- **Intake forms + assignment webhooks in the demo?** Default Phase B; pull into A only if you want them shown.
- **Cross-kind conversion** is moot now (everything is an Issue) ✓.
