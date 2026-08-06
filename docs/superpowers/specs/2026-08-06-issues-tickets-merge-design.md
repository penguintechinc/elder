# Design: Fold Helpdesk Tickets into Issues (one unified Issue model)

**Date:** 2026-08-06
**Status:** Design — pending review
**Owner:** Justin Bowen
**Related:** Elder v4.0 platform-merge (absorbed Ruffled helpdesk). Pre-fixes landed in #232 (Issues ungate + PATCH status-case).

## 1. Goal

There is **no separate "ticket" concept**. A support ticket is **just an Issue** (`issue_type = support`). The absorbed Ruffled helpdesk collapses into Elder's native **`issues`** model. Support-only fields become nullable columns on `issues`; helpdesk's duplicate concepts (teams, companies, contacts, tags, threads, attachments) merge into Elder-native tables.

**Guiding principle:** *leverage Elder-native objects/resources/tables wherever possible; merge duplicate concepts rather than carrying Ruffled's parallel ones.* **Every object carries a unique `village_id`.**

Delivered in **two phases** (re-scoped per review — the demo now includes intake forms, assignment webhooks, and OU-assignment):

- **Phase A (demo-critical):** **build the real unified model + features.** Extend `issues` (support columns, polymorphic identity/OU assignee, village_ids), add the new identity/org types, build **intake forms** (Altcha) and **assignment webhooks**, and a unified full-CRUD Issues UI. Legacy `hd_tickets` are shown **read-through a facade** (normalized as support-issues) so the demo is one backlog **without** the risky data migration.
- **Phase B (post-demo):** the **data migration** — `hd_tickets` → `issues`, `hd_contacts` → `identities`, `hd_companies` → `organizations`, `hd_teams` → `organizations` — then retire the `hd_*` tables and collapse the facade to plain `/api/v1/issues`.

## 2. Current state (why this is non-trivial)

Both modules run on **penguin-dal** (PyDAL) over `current_app.db`; SQLAlchemy models define the schema.

| Aspect | **Issues** (`issues`) — survivor | **Tickets** (`hd_tickets`) — folds in |
|---|---|---|
| Title | `title` | `subject` |
| Status | `open · in_progress · resolved · closed` (stored UPPERCASE) | `new · open · pending · on_hold · resolved · closed` (lowercase) |
| Priority | `low · medium · high · critical` | `low · medium · high · urgent · critical` |
| Type | `issue_type` (10) — **add `support`** | free-text `category` + `channel` |
| Reporter | `reporter_id` → identities | `requester_identity_id` **or** `requester_contact_id` |
| Assignee | `assignee_id` → identities | `assignee_identity_id` + `hd_team_id` |
| Grouping | project · milestone · labels · entity links · org | company (via contact) · team · `tags` (JSON) |
| Support extras | — | **SLA · CRM · email channel · public forms · canned responses** |
| Thread | `issue_comments` | `hd_ticket_messages` |
| Attachments | — | `hd_ticket_attachments` |
| **Tenant scoping** | ❌ **none** (`GET /issues` unfiltered → cross-tenant read leak) | ✅ fully scoped |
| `village_id` | ✅ (issues) | ✅ (tickets) |

**Pre-existing defects folded in (not silently carried):**

| Defect | Location | Phase |
|---|---|---|
| Issues tenant leak — now the everything-table, critical | `issues.py` | **A** |
| `created_by_id`/`assigned_to_id` (mig 001) vs `reporter_id`/`assignee_id` never reconciled | `alembic 001` | **A** (schema touched anyway) |
| `apply_sla_policy` un-awaited + passed Row not id → SLA never applied | `helpdesk/tickets.py:219`, `ticket_forms.py:668` | **A** |
| FE↔BE drifts: `updateIssue` PUT (BE PATCH); label POST body; TicketList `in_progress` filter | `web/src/lib/api.ts`, `TicketList.tsx` | **A** |
| `parent_issue_id` declared, unimplemented | `pydantic/issue.py` | **A** self-FK |
| No activity/history log | — | **A** |
| `update_issue` 500 on lowercase status | issues.py | ✅ **fixed #232** |

## 3. Decisions (from brainstorming, 2026-08-06)

| Decision | Choice |
|---|---|
| Model | **One `issues` table.** Support ticket = `issue_type='support'`. No `work_items`, no `kind` |
| Demo scope | Full CRUD **+ intake forms + assignment webhooks + OU-assignment** — all in the demo |
| Phasing | **A = build model + features** (schema changes included); **B = `hd_tickets`/CRM data migration** + retire `hd_*` |
| Assignee | **Single polymorphic assignee — an identity *or* an org unit, exactly one (never both).** Every issue |
| Person refs | **`identities`** (not "users") — assignees/reporters may be non-human (service accounts, bots) |
| Org units | Reuse **`organizations`** (tenant-scoped, hierarchical, `type`). `hd_teams` → `organizations` (`type=team`) |
| **CRM: contacts** | `hd_contacts` **merge into `identities`** with a new `identity_type = customer_contact` |
| **CRM: companies** | `hd_companies` → `organizations` with a **new `organization_type = customer_company`** (kept distinct from internal orgs) |
| Native reuse | Merge duplicate helpdesk concepts into native tables (§5) |
| Missing fields | Any helpdesk field absent from issues is **added to `issues`** (nullable) |
| Intake forms | Generalize `hd_ticket_forms` → **issue intake forms**; default **private**, optional public; public requires **Altcha** captcha (§6). **Phase A** |
| Assignment webhooks | Extend native **`webhooks`**: fire on `issue.assigned`, multiple configs filtered by `issue_type` + assignee (§7). **Phase A** |
| Village IDs | **Universal — every Elder object** (identities, entities, resources, issues, comments, attachments, forms, webhooks, …) gets a unique `village_id`. `identities` **already has one** (explicit col). Tables genuinely lacking it — `issue_comments`, `hd_ticket_messages`, `hd_ticket_attachments`, `hd_ticket_forms`, `webhooks` — get it added + backfilled; audit repo-wide for any others |
| License gating | Scope-only; tier model (quota/seat/SSO/MFA/KMS) — §10. Issues Enterprise gates removed (#232) |

## 4. Target model — the extended Issue

### 4.1 Assignee (single polymorphic, every issue)

| Field | Type | ← Issue | ← Ticket |
|---|---|---|---|
| `assignee_type` | enum(`identity`,`org_unit`), nullable | `identity` if set | `org_unit` if `hd_team_id` else `identity` |
| `assignee_id` | int (poly), nullable | `assignee_id` → identities | `assignee_identity_id` → identities **or** `hd_team_id` → organizations |
| `reporter_type` | enum(`identity`,`contact→identity`) | `identity` | `identity` or `contact` |
| `reporter_id` | int (poly) | `reporter_id` | `requester_identity_id` or `requester_contact_id` (→ identity after CRM merge) |

- **Exactly one** assignee target — an **identity** (human/non-human) **or** an **org unit** (`organizations`; a team is `type='team'`), never both. Ticket rows with both team+individual collapse to **the individual** (drop the team affiliation, or capture via org membership) — migration rule, §9.
- UI picker = **one search box across identities + organizational units** (§8.2).

### 4.2 Fields added to `issues` (nullable; populated when relevant)

| Field | Type | App | Source / action |
|---|---|---|---|
| `tenant_id` | int FK tenants | all | **ADD** + backfill (org→tenant) — fixes the leak |
| `assignee_type`/`assignee_id` | poly | all | **ADD** (§4.1) — OU assignment now |
| `issue_type` | enum + **`support`** | all | **ADD value** `support` |
| priority | enum + **`urgent`** | all | **ADD value** `urgent` |
| `channel` | enum(web/email/api) | support | **ADD** from ticket |
| `category` | string(100) | support | **ADD** from ticket |
| `requester_contact_id` | (→ identity) | support | via CRM merge (§5) |
| `hd_sla_policy_id`, `sla_breach_at`, `first_response_at`, `resolved_at` | | support | **ADD** from ticket |
| `parent_issue_id` | int self-FK | all | **ADD** (sub-tasks) |
| `created_by_id`/`assigned_to_id` reconciliation | | all | align with `reporter_id`/`assignee_id` |

### 4.3 Status / priority normalization

**Status** display order `new · open · in_progress · pending · resolved · closed`; map issue UPPERCASE ↔ ticket lowercase (case bug fixed #232): ticket `new`→open (or keep `new`), `on_hold`/`pending`→pending, others 1:1. **Priority** gains `urgent`.

### 4.4 New enum values / village_id

- `IdentityType += customer_contact`; `OrganizationType += customer_company`.
- `village_id` **added** to `issue_comments`, `hd_ticket_messages`, attachments, intake forms, `webhooks` (+ backfill). `identities` **already has `village_id`** (explicit column) — no change. **Universal rule:** *every* Elder object carries a unique `village_id`; audit for and add it to any table still missing one.

## 5. Concept convergence (reuse Elder-native, merge duplicates)

| Helpdesk (Ruffled) | Elder-native target | Disposition |
|---|---|---|
| `hd_teams` (+ `hd_team_members`) | `organizations` (`type=team`) + org membership | **Retire** → organizations |
| `hd_companies` | `organizations` **`type=customer_company`** (new) | **Merge**, kept distinct from internal orgs |
| `hd_contacts` | `identities` **`identity_type=customer_contact`** (new) | **Merge** into identities, **keyed on email**: if the email already belongs to a user/identity, **reuse that identity** (don't duplicate); else create `customer_contact` with `username=email`. Optional contact details (phone, desk/office location, job title, notes) go into an identity **`metadata` JSON** bag — **add a `metadata` column to `identities`** (it has none; model it like `organizations.org_metadata`) as "optional information" |
| `hd_ticket_attachments` storage | Elder **diagrams storage-provider abstraction** (S3/GCS/MinIO already there) | Attachments stored per §6.1: **PVC** if single-node, else **S3-compatible** (default MinIO; support S3/GCS/Azure Blob), server-side encrypted |
| `tags` (JSON) | issue **`labels`** (native M2M) | **Converge** → labels |
| `hd_ticket_messages` | `issue_comments` (+ `is_internal`, email cols, `village_id`) | **Merge** |
| `hd_ticket_attachments` | new **`issue_attachments`** (with `village_id`) | **Generalize** (no native attachments today) |
| `hd_sla_policies`, `hd_canned_responses`, `hd_email_accounts` | *(no native equivalent)* | **Keep**, repoint FKs to `issues` |
| `hd_ticket_forms` | **issue intake forms** (§6) | **Generalize** + Altcha + `village_id` |
| native `webhooks` | native webhooks module | **Extend** for assignment events (§7) + `village_id` |

## 6. Intake forms (Phase A; generalizes `hd_ticket_forms`)

- **Config:** `village_id`, name, `slug`, ordered field defs (JSON), the `issue_type` it files as (default `support`), default assignee (identity/OU), `is_active`.
- **Field types = every Pydantic-supported type** (str, int, float, bool, `EmailStr`, `HttpUrl`, `date`/`datetime`, `Enum`/Literal choices, `list[...]`, nested models, constrained types, file upload, etc.). A form's field defs **compile to a dynamic Pydantic model**; submissions are **validated with Pydantic** server-side (same validation stack as the rest of the API → consistent errors + OpenAPI-able).
- **Visibility:** **default private** (auth); optionally **public** (unauth endpoint `/api/v1/intake/{slug}`).
- **Captcha:** public forms **require Altcha** (altcha.org — open-source, self-hostable PoW; replaces old Turnstile/reCAPTCHA). `captcha_provider='altcha'`; server verifies the solution before creating the issue. Private forms: none.
- **Submit:** Pydantic-validate → (public) verify Altcha → upsert requester **identity** (`customer_contact`, keyed on email) → create Issue (`issue_type` per form; support fields set; `village_id`) → store any uploads (§6.1) → apply default assignee → fire `issue.assigned` webhook if assigned (§7).

### 6.1 Uploads / attachment storage
Configurable backend (reuse the diagrams module's S3/GCS/MinIO storage-provider abstraction):
- **Single-node:** a **PVC** (local persistent volume) is sufficient.
- **Multi-node:** drop to an **S3-compatible** object store — **default MinIO**, support **AWS S3 / GCS / Azure Blob**. Server-side encryption on (per `security.md` at-rest).
- `issue_attachments` rows store `village_id`, the backend + object key/path, filename, content-type, size. Applies to both form uploads and in-issue attachments.

## 7. Assignment webhooks (Phase A; extend native `webhooks`)

Native `webhooks` stores `{events: JSON, organization_id, event_type, url, …}`; gains `village_id` + filter fields.

- **New event:** `issue.assigned` — on any assignee change (create-with-assignee, PATCH assignee, form default-assign, reassignment).
- **Filters (per webhook, additive):** `issue_type` (e.g. `support`) and/or assignee (identity id or org-unit id).
- **Multiple configs:** e.g. *(a)* any issue assigned to **OU-X** → OU-X webhook; *(b)* `issue_type=support` assigned to the **support-bot identity** → support-bot webhook. Empty filter = all tenant assignments.
- **Dispatch:** on assignment, match all tenant webhooks → POST signed payload (issue id, type, status, `assignee {type,id}`, actor, ts). Reuse existing delivery/retry/signing.

## 8. Phase A — the build (demo)

Schema + features land here. Legacy `hd_tickets` are **not** migrated yet — shown via the facade.

### 8.1 Backend (sequence: village_id + tenant scoping FIRST)
- **First / promptly — `village_id` everywhere:** add `village_id` to every table missing it (`issue_comments`, `hd_ticket_messages`, attachments, forms, `webhooks`) + backfill via `generate_village_id`. (`identities` already has one.) Land this early in Phase A as its own migration + PR, ahead of the feature work.
- **`issues` migration:** add §4.2 columns (tenant_id, polymorphic assignee, support fields, parent self-FK); add `support`/`urgent` enum values; reconcile `created_by_id`↔`reporter_id`.
- **Enforce tenant scoping** on all issue reads/writes (close the leak); backfill `issues.tenant_id`.
- **New types:** `identity_type=customer_contact`, `organization_type=customer_company`.
- **Intake forms** (§6) + **assignment webhooks** (§7) + `issue_attachments`; extend `issue_comments`.
- **Facade `/api/v1/work`** (transitional): merges native `issues` + legacy `hd_tickets` (normalized to `issue_type=support`), tenant-scoped, full CRUD by origin; new items create native issues. Collapses to `/api/v1/issues` in Phase B.
- Fix SLA await bug; FE verb/shape drifts.

### 8.2 UI — unified Issues surface
- **List:** all issues incl. support (badge), unified status/priority/assignee(identity **or** OU) filters, search.
- **Detail:** shared header (title/status/priority/**assignee via combined identity+OU search box**/thread) + support section when `issue_type=support`.
- **Create/Edit:** one form; assignee picker = **one search box over identities + org units**; support fields shown for support type. Intake-form builder UI (field config, private/public, Altcha). Webhook config UI (filters).
- Existing `/issues` and `/helpdesk` stay (additive) until Phase B.

## 9. Phase B — data migration (post-demo)

1. **Migrate CRM:** `hd_companies` → `organizations` (`type=customer_company`); `hd_contacts` → `identities` (`type=customer_contact`, username=email); `hd_teams` → `organizations` (`type=team`) + membership. Keep `legacy_id` maps.
2. **Migrate `hd_tickets` → `issues`** (`issue_type=support`; tenant direct; `subject`→`title`; status/priority normalized; `hd_team_id`→OU assignee or individual per §4.1; `tags`→labels; messages→`issue_comments`; attachments→`issue_attachments`; requester_contact→migrated identity; **village_id** preserved/backfilled).
3. Repoint `hd_sla_policies`/`hd_canned_responses`/`hd_email_accounts`/forms FKs to `issues`.
4. **Dual-write** window; flip reads to `issues`; retire facade fan-out; **drop `hd_*`** after bake.
5. Backfill `village_id` for any remaining rows lacking one.

## 10. Licensing / quota (context, mostly out of scope)

| Tier | Pricing | Objects | Tenants | Admins | SSO | MFA enforce | Encryption key |
|---|---|---|---|---|---|---|---|
| Free | free | 100 cap | 1 | 1 | — | — | local |
| Pro | per-seat (≤150) | 5,000 cap | 1 | multi | + Google | ✓ | local |
| Enterprise | per-object metered (~1000 batches) | metered | multi | multi | + SAML/OIDC/LDAP | ✓ | external KMS (AWS/Azure/GCP) |

An issue (incl. support) is a metered **object**. Counting must be accurate/auditable. **Quota/billing enforcement is separate work** — noted, not built here.

## 11. Testing strategy

- **Phase A:** status/priority normalization (both directions); assignee-shape (identity vs OU, never both); tenant-scoping **regression** (no cross-tenant rows; reject missing tenant); facade CRUD-dispatch by origin; **intake-form submit** (private auth + public **Altcha verify** → issue created, support fields, village_id); **assignment-webhook matching matrix** (OU-only, type+assignee, empty, non-match); new-type validation; village_id uniqueness on every created object; FE smoke (unified list + create/edit + combined assignee picker + form builder).
- **Phase B:** migration round-trip (every ticket/contact/company/team → target + child links via legacy map); OU-assignment persisted on migrated issues; dual-write consistency; re-run all issues + helpdesk suites against `issues`.
- 90%+ coverage; regression test per fixed defect referencing this doc.

## 12. Risks / open questions

- **Phase A is now a large build under demo time pressure** (schema migration + forms + webhooks + OU-assignment + facade). Highest risk in the plan — sequence so each piece is independently shippable + tested; the `issues` schema migration lands first.
- **`identities` `village_id`** — **decided: add it** (+ backfill all identities), per the universal village_id rule. Note the ripple: it's a core-table migration touching every identity row, not just customer contacts.
**Resolved (2026-08-06):**
- `identities` village_id → **already present** (explicit column) — no migration needed. `identities` **gains a `metadata` JSON column** (it has none) to hold optional contact info (phone, desk/office location, job title, notes).
- Contact↔identity → **key on email; reuse the existing identity if the email already belongs to a user**, else create the `customer_contact` (§5).
- Ticket team+person → **collapse to the person** (§4.1).
- Legacy `hd_ticket` assignment via the facade → **fires `issue.assigned`** (facade's ticket-update emits it).
