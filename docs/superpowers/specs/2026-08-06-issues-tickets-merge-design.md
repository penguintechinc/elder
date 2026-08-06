# Design: Merge Issues + Helpdesk Tickets into a Unified Work Item

**Date:** 2026-08-06
**Status:** Design — pending review
**Owner:** Justin Bowen
**Related:** Elder v4.0 platform-merge (absorbed Ruffled helpdesk). Pre-fixes landed in #232 (Issues ungate + PATCH status-case).

## 1. Goal

Unify Elder's **Issues** module and the absorbed Ruffled **helpdesk Tickets** module into a single "Work Item" concept — one list, one detail, one create/edit flow — so users work a single backlog instead of two disjoint surfaces.

Delivered in **two phases**:

- **Phase A (demo-critical):** a presentation-layer merge — a unified `/work` surface with **full CRUD** over the two *existing* backends, **no schema change**. Low-risk, shippable for the imminent demo.
- **Phase B (post-demo):** the real `work_items` unified table that replaces the two-backend seam, with data migration and subsystem repointing.

## 2. Current state (why this is non-trivial)

Both modules run routes on **penguin-dal** (PyDAL) over `current_app.db`; SQLAlchemy models define the physical schema. But they diverge sharply:

| Aspect | **Issues** (`issues`) | **Tickets** (`hd_tickets`) |
|---|---|---|
| Title field | `title` | `subject` |
| Status | `open · in_progress · resolved · closed` (DB stores UPPERCASE member names) | `new · open · pending · on_hold · resolved · closed` (lowercase strings) |
| Priority | `low · medium · high · critical` | `low · medium · high · urgent · critical` |
| Type / channel | `issue_type` (bug/feature/security/…10) | free-text `category` + `channel` (web/email/api) |
| Reporter | `reporter_id` → identities | `requester_identity_id` **or** `requester_contact_id` (external CRM) |
| **Assignee** | `assignee_id` → identities **(individual only)** | `assignee_identity_id` (individual) **+ `hd_team_id` (team)** |
| Grouping | project · milestone · labels (M2M) · entity links · org (`resource_type`/`resource_id`) | company (via contact) · team · `tags` (JSON) |
| Support extras | — | **SLA policies · CRM (companies/contacts) · email channel · public forms · canned responses** |
| Thread | `issue_comments` (author, content) | `hd_ticket_messages` (internal notes, email threading) |
| Attachments | — | `hd_ticket_attachments` |
| **Tenant scoping** | ❌ **none** — no `tenant_id` column; `GET /issues` applies **no** tenant filter (cross-tenant read leak) | ✅ `tenant_id` on every row; every query filtered; `identity_in_tenant` IDOR guards |
| `village_id` | ✅ | ✅ |
| List response shape | `{items, total, page, per_page, pages}` | `{items, pagination:{…}}` |
| Activity/history | ❌ none | partial (message rows, incl. `system` type) |

**Assignee gap (called out per review):** tickets can be assigned to a **team** (`hd_team_id`) and/or an **individual**; issues can only be assigned to an individual. The unified model carries **both** an org-unit/team assignee and an optional individual assignee (§4), so issues gain team assignment they lack today.

**Known pre-existing defects to fold into the merge (not silently carried):**

| Defect | Location | Phase to fix |
|---|---|---|
| Issues has no tenant scoping (read leak) | `issues.py` list/get | **A** (facade enforces) → **B** (column) |
| `created_by_id`/`assigned_to_id` (migration 001) vs `reporter_id`/`assignee_id` (model/routes) — never reconciled | `alembic 001` vs `issue.py` | **B** |
| `apply_sla_policy` called un-awaited + passed a Row not an id → SLA never applied on create | `helpdesk/routes/tickets.py:219`, `ticket_forms.py:668` | **B** (or earlier) |
| FE ↔ BE verb/shape drifts: `updateIssue` calls PUT (BE only has PATCH); label POST body mismatch; TicketList status filter uses invalid `in_progress` | `web/src/lib/api.ts`, `TicketList.tsx` | **A** |
| `parent_issue_id` declared (pydantic) but unimplemented; FE subtasks hard-coded `[]` | `pydantic/issue.py`, `IssueDetail.tsx:68` | **B** (self-FK) or drop |
| No activity/history log on either side | — | **B** (`work_item_events`) |

## 3. Decisions (from brainstorming, 2026-08-06)

| Decision | Choice |
|---|---|
| Target end state | **One unified Work Item** — single table + API + UI with a `kind` discriminator (`issue`/`ticket`) |
| Timing | Presentation merge **in the demo**; real table migration **after** |
| Demo scope | **Full CRUD** (list + detail + create + edit) through the unified surface |
| Architecture | **Backend facade** (`/api/v1/work`) normalizes both backends server-side — not frontend fan-out |
| **Assignee** | **Team/org-unit assignee + optional individual assignee** (both nullable). Tickets already do this; issues gain team assignment |
| License gating | **Scope-only, no per-kind gating.** Issues Enterprise gates removed (#232). Gating is tier-based (quota/seat/SSO/MFA/KMS), not per-route — see §7 |
| `parent_issue_id` sub-tasks | Deferred to Phase B as a `work_items` self-FK |

## 4. Unified Work Item — canonical field set

This is the authoritative merged field list. Phase A's facade DTO and Phase B's `work_items` columns are both derived from it. **App = applies to** (`all`, `issue`, `ticket`).

### 4.1 Core fields (all kinds)

| Unified field | Type | App | ← Issue source | ← Ticket source | Notes |
|---|---|---|---|---|---|
| `uid` | string | all | `"issue:"+id` | `"ticket:"+id` | Phase A addressing; Phase B = single native id |
| `id` | int PK | all | `id` | `id` | Phase B single sequence; legacy id kept for migration |
| `kind` | enum(`issue`,`ticket`) | all | const `issue` | const `ticket` | discriminator |
| `tenant_id` | int FK tenants | all | **NEW** (backfill via org→tenant) | `tenant_id` | closes the Issues leak |
| `village_id` | string(32) | all | `village_id` | `village_id` | cross-object ref |
| `title` | string(500) | all | `title` | `subject` | renamed |
| `description` | text | all | `description` | ∅ (tickets have none → first message body, else null) | |
| `status` | enum (unified §4.4) | all | `status` | `status` | normalized both ways |
| `priority` | enum (unified §4.4) | all | `priority` | `priority` | normalized |
| `assignee_team_id` | int FK teams | all | **NEW** (issues gain this) | `hd_team_id` | **org-unit/team assignee** |
| `assignee_identity_id` | int FK identities | all | `assignee_id` | `assignee_identity_id` | individual assignee (optional; may co-exist with team) |
| `reporter_identity_id` | int FK identities | all | `reporter_id` | `requester_identity_id` | internal reporter/requester |
| `reporter_contact_id` | int FK contacts | all | ∅ (null) | `requester_contact_id` | external requester (CRM); ticket-only in practice |
| `closed_at` | datetime tz | all | `closed_at` | `closed_at` | |
| `closed_by_id` | int FK identities | all | `closed_by_id` | **NEW** (null) | |
| `created_at`/`updated_at` | datetime tz | all | ✓ | ✓ | |

**Assignee semantics:** `assignee_team_id` and `assignee_identity_id` are independent and both nullable — a work item may be assigned to a team, to an individual, or to an individual *within* a team (team triage → person). Requires a shared **teams** table: Phase B generalizes helpdesk `hd_teams` into a shared `teams` (or org-unit) table both kinds reference; Phase A can persist a team assignee for **tickets** (existing `hd_team_id`) but **not for issues** (no column) — issue team-assignment is a Phase-B capability (noted §5.4, §8).

### 4.2 Issue-kind fields

| Unified field | Type | ← Issue source | Notes |
|---|---|---|---|
| `issue_type` | enum(10: bug/feature/security/…) | `issue_type` | |
| `is_incident` | bool | `is_incident` | |
| `due_date` | datetime tz | `due_date` | |
| `resource_type` / `resource_id` | poly ref | ✓ | entity/org soft association |
| `organization_id` | int FK organizations | `organization_id` | |
| labels | M2M `issue_label_assignments` | ✓ | repointed to work_items in B |
| entity links | `issue_entity_links` | ✓ | related/blocks/blocked_by/fixes |
| project / milestone links | `issue_project_links` / `issue_milestone_links` | ✓ | |

### 4.3 Ticket-kind fields

| Unified field | Type | ← Ticket source | Notes |
|---|---|---|---|
| `channel` | enum(web/email/api) | `channel` | |
| `category` | string(100) free-text | `category` | distinct from `issue_type`; both retained per-kind |
| `tags` | JSON array | `tags` | issues use labels instead — see §4.5 |
| `hd_sla_policy_id` | int FK | `hd_sla_policy_id` | SLA |
| `sla_breach_at` / `first_response_at` / `resolved_at` | datetime tz | ✓ | SLA timestamps |
| company | via `reporter_contact_id → hd_contacts.hd_company_id` | ✓ | no direct FK on the work item |

### 4.4 Unified status / priority vocab

**Status** (display order): `new · open · in_progress · pending · resolved · closed`

| Unified | ← Issue (UPPERCASE stored) | ← Ticket (lowercase) |
|---|---|---|
| new | — | new |
| open | OPEN | open |
| in_progress | IN_PROGRESS | — |
| pending | — | pending, on_hold |
| resolved | RESOLVED | resolved |
| closed | CLOSED | closed |

Writes map back to each backend's native value **and case** (Issues stores uppercase member names — the case bug fixed in #232 makes lowercase-in safe). **Priority:** `low · medium · high · urgent · critical` (superset; issues never emit `urgent`).

### 4.5 Fields needing a convergence decision (Phase B)

| Tension | Options | Recommendation |
|---|---|---|
| `tags` (ticket JSON) vs `labels` (issue M2M) | keep both / converge to labels | Converge to labels (M2M) for both in B; migrate ticket tags → labels |
| `category` (ticket free-text) vs `issue_type` (issue enum) | keep both / merge | Keep both — different semantics (support category vs engineering type) |
| `description` for tickets | null / synthesize from first message | Null in A; in B backfill from first inbound message if present |

## 5. Phase A — demo (facade + `/work` UI, no schema change)

### 5.1 New `work` module → `/api/v1/work`
A thin facade that fans out to the existing `issues` and `hd_tickets` PyDAL tables, normalizes to the §4 field set, and **enforces tenant scoping on every call** (closing the Issues leak on this path).

| Endpoint | Behavior |
|---|---|
| `GET /work/items` | Query both tables (tenant-scoped), normalize to §4, merge, sort, paginate. Filters: `kind`, `status`, `priority`, `assignee` (team or identity), `search` |
| `GET /work/items/{kind}/{id}` | Dispatch to issue-detail or ticket-detail; return normalized DTO (§4) |
| `POST /work/items` | Body carries `kind` + §4 fields → dispatch to the right create |
| `PATCH /work/items/{kind}/{id}` | Dispatch to the right update |
| `DELETE /work/items/{kind}/{id}` | Dispatch to the right delete |

**Auth:** `@login_required` + `work:read`/`work:write` scope (accept the `issues:*`/`helpdesk:*` union during transition). Tenant claim **required** — reject if missing (unlike today's Issues path).

**Normalized DTO** = the §4 fields: core fields flat, plus a `kind_fields` object carrying the §4.2/§4.3 kind-specific extras so the detail UI renders per-kind sections without extra round-trips. `assignee` is expressed as `{team_id?, identity_id?}`.

### 5.2 UI — new `/work` nav item
- **List:** merged table with a `kind` badge, unified status/priority filters, an assignee filter that accepts a **team or an individual**, and search. Row click → unified detail.
- **Detail:** shared header (title/status/priority/**assignee: team and/or person**/thread) + a per-kind section (issue: type/project/milestone/entity links; ticket: requester/channel/SLA/company).
- **Create/Edit:** one form; a `kind` toggle reveals kind-specific fields; assignee picker offers **teams and people**; submits to the facade.
- **Existing `/issues` and `/helpdesk` stay** — additive, nothing removed → nothing breaks for the demo.
- Fix the FE drifts on this path: PATCH not PUT, correct label body, valid status vocab.

### 5.3 Phase-A explicitly excludes
No schema change, no data migration, no dropping old UIs, no cross-kind conversion, no unifying SLA/CRM/email onto issues, and **no team-assignment for issues** (no column yet — Phase B).

## 6. Phase B — post-demo (real `work_items` table)

### 6.1 Schema
New tenant-scoped `work_items` (SQLAlchemy model + Alembic migration) whose columns are exactly the §4 field set: core columns + `kind` + nullable kind-specific columns. Adds:
- `assignee_team_id` on **both** kinds (issues finally get team assignment), referencing a **shared `teams` table** generalized from `hd_teams`.
- `parent_work_item_id` (self-FK, sub-tasks) and a `work_item_events` activity/history table (status change, (re)assignment, comments/messages unified as events, `is_internal` flag).

### 6.2 Subsystem repointing
Support subsystems (SLA, CRM companies/contacts, email accounts/logs, ticket forms, canned responses, teams) and dev subsystems (projects, milestones, labels, entity links) keep their tables; their FKs move from `hd_tickets.id`/`issues.id` to `work_items.id`. `hd_teams` → shared `teams`. Comments (`issue_comments`) + messages (`hd_ticket_messages`) converge into `work_item_events` (or `work_item_messages`) with `is_internal`. Resolve the §4.5 tag/label convergence.

### 6.3 Migration steps
1. Create `work_items` + `work_item_events` + shared `teams` (empty), tenant-scoped.
2. Backfill: `issues` → `work_items` (kind=issue; derive `tenant_id` from org→tenant — **fixes the leak**), `hd_tickets` → `work_items` (kind=ticket). Keep `legacy_id`/`legacy_table` for link remapping; migrate `hd_teams` rows into `teams`.
3. Repoint child-table FKs via the legacy-id map (including ticket `hd_team_id` → `assignee_team_id`).
4. **Dual-write** window: facade writes both old + new while readers cut over.
5. Flip facade reads to `work_items`; retire fan-out.
6. Drop `issues`/`hd_tickets` after a bake period.

### 6.4 Fixes folded in (from §2)
Tenant column, `created_by_id`↔`reporter_id` reconciliation, `apply_sla_policy` await bug, `parent_issue_id` → real self-FK, activity log, FE verb/shape drifts, issue team-assignment.

## 7. Licensing / quota (context, mostly out of scope)

Gating is **tier-based**, not per-feature-route (why the Issues Enterprise gates were removed in #232):

| Tier | Pricing | Objects | Tenants | Admins | SSO | MFA enforce | Encryption key |
|---|---|---|---|---|---|---|---|
| Free | free | 100 cap | 1 | 1 | — | — | local |
| Pro | per-seat (≤150) | 5,000 cap | 1 | multi | + Google | ✓ | local |
| Enterprise | per-object, metered (~1000 batches) | metered | multi | multi | + SAML/OIDC/LDAP | ✓ | external KMS (AWS/Azure/GCP) |

**Implication:** a `work_item` is a metered "object" — it counts toward the Free 100-cap, the Pro 5,000 ceiling, and Enterprise per-object billing, so the unified count must be accurate/auditable. **The quota/billing system itself is separate work** — this design only notes that work items participate in it.

## 8. Testing strategy

- **Phase A:** unit tests for §4.4 vocab normalization (both directions, all statuses/priorities) and for the §4 assignee shape (team-only, person-only, both); facade CRUD-dispatch tests (route to correct backend by `kind`); tenant-scoping tests (reject missing tenant, never return cross-tenant rows — regression for the Issues leak); merged-pagination test; FE smoke test for `/work` list + create/edit (incl. team-assignee picker).
- **Phase B:** migration round-trip test (backfill preserves every issue/ticket + child links + `hd_teams`→`teams` via legacy-id map); issue team-assignment test (new capability); dual-write consistency; `work_item_events` coverage; re-run all existing issues + helpdesk suites against the unified table.
- 90%+ coverage; regression test per fixed defect referencing this doc.

## 9. Risks / open questions

- **Full-CRUD-over-two-backends in Phase A** is the riskiest demo choice (create/edit must dispatch + map vocab per kind under time pressure). Mitigation: heavy normalization unit tests before wiring the UI.
- **Team-assignment asymmetry in Phase A:** tickets can be team-assigned, issues can't (no column). The `/work` UI must disable/hide the team picker for `kind=issue` in Phase A, or accept it's Phase-B-only — **confirm which.**
- **Merged pagination** across two tables with a unified sort is approximate unless over-fetched then merged — fine for demo scale; exact after Phase B (single table).
- **Cross-kind conversion** (promote ticket→issue or vice-versa) is out of scope for both phases — confirm acceptable.
- **`work_item_events` vs keeping comments/messages separate**, and the §4.5 tag/label convergence — decide in Phase B detailed design.
