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
| Assignee | `assignee_id` → identities | `assignee_identity_id` **+ `hd_team_id`** |
| Grouping | project · milestone · labels (M2M) · entity links · org (`resource_type`/`resource_id`) | company (via contact) · team · `tags` (JSON) |
| Support extras | — | **SLA policies · CRM (companies/contacts) · email channel · public forms · canned responses** |
| Thread | `issue_comments` (author, content) | `hd_ticket_messages` (internal notes, email threading) |
| Attachments | — | `hd_ticket_attachments` |
| **Tenant scoping** | ❌ **none** — no `tenant_id` column; `GET /issues` applies **no** tenant filter (cross-tenant read leak) | ✅ `tenant_id` on every row; every query filtered; `identity_in_tenant` IDOR guards |
| `village_id` | ✅ | ✅ |
| List response shape | `{items, total, page, per_page, pages}` | `{items, pagination:{…}}` |
| Activity/history | ❌ none | partial (message rows, incl. `system` type) |

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
| License gating | **Scope-only, no per-kind gating.** Issues Enterprise gates removed (#232). Gating is tier-based (quota/seat/SSO/MFA/KMS), not per-route — see §6 |
| `parent_issue_id` sub-tasks | Deferred to Phase B as a `work_items` self-FK |

## 4. Phase A — demo (facade + `/work` UI, no schema change)

### 4.1 New `work` module → `/api/v1/work`
A thin facade that fans out to the existing `issues` and `hd_tickets` PyDAL tables, normalizes to a common shape, and **enforces tenant scoping on every call** (closing the Issues leak on this path).

| Endpoint | Behavior |
|---|---|
| `GET /work/items` | Query both tables (tenant-scoped), normalize, merge, sort, paginate. Filters: `kind`, `status`, `priority`, `assignee`, `search` |
| `GET /work/items/{kind}/{id}` | Dispatch to issue-detail or ticket-detail; return normalized DTO |
| `POST /work/items` | Body carries `kind` + shared + kind-specific fields → dispatch to the right create |
| `PATCH /work/items/{kind}/{id}` | Dispatch to the right update |
| `DELETE /work/items/{kind}/{id}` | Dispatch to the right delete |

**ID namespacing:** every work item is addressed as `{kind}:{id}` (`issue:5`, `ticket:3`) because both tables start at id 1 and would collide in a merged list.

**Auth:** `@login_required` + a new `work:read`/`work:write` scope (or accept `issues:*`/`helpdesk:*` union). Tenant claim required — reject if missing (unlike today's Issues path).

### 4.2 Vocab normalization (server-side, one place)

**Unified status** (superset, display order): `new · open · in_progress · pending · resolved · closed`

| Unified | ← Issue | ← Ticket |
|---|---|---|
| new | — | new |
| open | OPEN | open |
| in_progress | IN_PROGRESS | (n/a) |
| pending | — | pending, on_hold |
| resolved | RESOLVED | resolved |
| closed | CLOSED | closed |

Writes map back to each backend's native vocab (and case — Issues stores UPPERCASE). **Unified priority:** `low · medium · high · urgent · critical` (superset; issues never emit `urgent`).

Normalized DTO (common fields): `uid` (`kind:id`), `kind`, `title` (title/subject), `status`, `priority`, `assignee_id`, `created_at`, `updated_at`, plus a `kind_fields` object carrying the type-specific extras (issue_type/project/milestone for issues; channel/requester_contact/sla for tickets) so the detail UI can render per-kind sections without extra round-trips.

### 4.3 UI — new `/work` nav item
- **List:** merged table with a `kind` badge, unified status/priority filters, search. Row click → unified detail.
- **Detail:** shared header (title/status/priority/assignee/thread) + a per-kind section (issue: type/project/milestone/entity links; ticket: requester/channel/SLA/company).
- **Create/Edit:** one form; a `kind` toggle reveals the kind-specific fields; submits to the facade.
- **Existing `/issues` and `/helpdesk` stay** — nothing removed, so nothing breaks for the demo. `/work` is additive.
- Fix the FE drifts on this path: use PATCH (not PUT), correct label body, valid status vocab.

### 4.4 Phase-A explicitly excludes
No schema change, no data migration, no dropping the old UIs, no cross-kind conversion (issue↔ticket), no unifying SLA/CRM/email onto issues. Those are Phase B.

## 5. Phase B — post-demo (real `work_items` table)

### 5.1 Schema
New tenant-scoped `work_items` (SQLAlchemy model + Alembic migration): shared core columns + `kind` + nullable kind-specific columns.

- **Core:** `id`, `tenant_id` (NOT NULL, FK), `village_id`, `kind`, `title`, `description`, `status`, `priority`, `assignee_identity_id`, `created_at`, `updated_at`, `closed_at`.
- **Issue-kind:** `issue_type`, `is_incident`, `resource_type`, `resource_id`, `organization_id`, `due_date`, `reporter_id`, `closed_by_id`; links via existing `issue_label_assignments`, `issue_entity_links`, `issue_project_links`, `issue_milestone_links` repointed to `work_items.id`.
- **Ticket-kind:** `requester_identity_id`, `requester_contact_id`, `channel`, `category`, `hd_team_id`, `hd_sla_policy_id`, `sla_breach_at`, `first_response_at`, `resolved_at`.
- **New:** `parent_work_item_id` (self-FK, sub-tasks) and a `work_item_events` activity/history table (status changes, assignment, comments/messages unified as events).

### 5.2 Subsystem repointing
Support subsystems (SLA, CRM companies/contacts, email accounts/logs, ticket forms, canned responses, teams) and dev subsystems (projects, milestones, labels, entity links) keep their tables; their FKs move from `hd_tickets.id`/`issues.id` to `work_items.id`. Comments (`issue_comments`) + messages (`hd_ticket_messages`) converge into `work_item_events` (or a `work_item_messages` table) with an `is_internal` flag.

### 5.3 Migration steps
1. Create `work_items` + `work_item_events` (empty), tenant-scoped.
2. Backfill: `issues` → `work_items` (kind=issue; derive `tenant_id` from org→tenant; **backfill fixes the leak**), `hd_tickets` → `work_items` (kind=ticket). Preserve old ids in a `legacy_id`/`legacy_table` pair for link remapping.
3. Repoint child-table FKs via the legacy-id map.
4. **Dual-write** window: facade writes to both old + new while readers cut over.
5. Flip facade reads to `work_items`; retire fan-out.
6. Drop `issues`/`hd_tickets` (and un-migrated cruft) after a bake period.

### 5.4 Fixes folded in (from §2)
Tenant scoping (now a real column), `created_by_id`↔`reporter_id` reconciliation, `apply_sla_policy` await bug, `parent_issue_id` → real self-FK, activity log, FE verb/shape drifts.

## 6. Licensing / quota (context, mostly out of scope)

Gating is **tier-based**, not per-feature-route (which is why the Issues Enterprise gates were removed in #232):

| Tier | Pricing | Objects | Tenants | Admins | SSO | MFA enforce | Encryption key |
|---|---|---|---|---|---|---|---|
| Free | free | 100 cap | 1 | 1 | — | — | local |
| Pro | per-seat (≤150) | 5,000 cap | 1 | multi | + Google | ✓ | local |
| Enterprise | per-object, metered (~1000 batches) | metered | multi | multi | + SAML/OIDC/LDAP | ✓ | external KMS (AWS/Azure/GCP) |

**Implication for this design:** a `work_item` is a metered "object" — it counts toward the Free 100-cap, the Pro 5,000 ceiling, and Enterprise per-object billing. The unified object-count must be accurate/auditable. **The quota/billing system itself is separate work** — this design only notes that work items participate in it; it does not build quota enforcement.

## 7. Testing strategy

- **Phase A:** unit tests for the vocab-normalization mapping (both directions, all statuses/priorities); facade CRUD-dispatch tests (create/edit/delete route to the correct backend by `kind`); tenant-scoping tests (facade rejects missing tenant, never returns cross-tenant rows — regression for the Issues leak); a merged-pagination test; FE smoke test for the `/work` list + create/edit modal.
- **Phase B:** migration round-trip test (backfill preserves every issue/ticket + child links via legacy-id map); dual-write consistency test; `work_item_events` coverage; re-run of all existing issues + helpdesk suites against the unified table.
- 90%+ coverage per house standard; regression test per fixed defect referencing this doc.

## 8. Risks / open questions

- **Full-CRUD-over-two-backends in Phase A** is the riskiest demo choice (create/edit must correctly dispatch + map vocab per kind under time pressure). Mitigation: heavy normalization unit tests before wiring the UI.
- **Merged pagination** across two tables with a unified sort is inherently approximate unless over-fetched then merged — acceptable for demo scale; revisit at Phase B (single table makes it exact).
- **Cross-kind conversion** (promote a ticket to an issue, or vice-versa) is out of scope for both phases — confirm that's acceptable.
- **`work_item_events` vs keeping comments/messages separate** — decide in Phase B detailed design.
