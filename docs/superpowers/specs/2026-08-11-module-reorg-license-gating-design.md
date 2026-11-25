# Module Reorg + License Gating — Design Spec

**Status:** Draft for review (2026-08-11). No code until the derived plan is reviewed.

**Goal:** Reorganize Elder's 15 flat modules into four named groups (Core / CRM / Workflow / KB), add a **scale/structure license-gating system**, and fully retire the legacy `/helpdesk` backend by absorbing its remaining capabilities into the unified `issues` model.

**Architecture:** Three subsystems delivered as one phased program — (A) module grouping + group ENV toggles, (B) a license-enforcement layer that hard-blocks over-limit actions, (C) `/helpdesk` capability absorption + deletion. Quart/Python + penguin-dal + SQLAlchemy/Alembic backend; React 18 + Vite frontend.

**Tech stack:** existing Elder stack — Quart, penguin-dal, SQLAlchemy models + Alembic migrations, `penguin_licensing` client, Redis (counters/cache), React + TanStack Query, `apps/worker` aiocron.

---

## Global Constraints

- **Paywall gates scale + structure, not features** — every tier gets ALL modules with full features. No module is ever tier-locked or feature-crippled. (Canonical: `general.md` License Tiers, revised 2026-08-11.)
- **Tier matrix (defaults; license-server can override any number):**

| Dimension | Free | Professional | Enterprise |
|---|---|---|---|
| Modules (all, full) | ✅ | ✅ | ✅ |
| Non-admin members | ∞ | ∞ | ∞ |
| Global admins | 1 | 1 | ∞ |
| Tenant admins | 0 | 10 | ∞ |
| Tenants | 1 | 1 | ∞ (multi-tenancy) |
| Teams | 1 | ∞ | ∞ |
| Object quota | 1,000 | ∞ | ∞ |
| Backend nodes / service type | 1 | 1 | multiple / HA (license-set, default ∞) |
| Google SSO | — | ✅ | ✅ |
| SAML/OIDC | — | — | ✅ |
| WaddleAI | — | ✅ hosted-API-only | ✅ |
| BYOK AI (Anthropic/OpenAI/Ollama) | — | — | ✅ |
| Whitelabel | — | — | ✅ |
| External KMS | — | — | ✅ |

- **Enforcement = hard block** — over-limit action refused (HTTP 402) with an upgrade prompt; never a soft warning.
- **"node" = backend container instance** (scanner/main/worker replicas), NOT a graph node.
- **Everything is an Issue** — a support ticket is `issue_type='support'` (continues the #13 unified model).
- **Alembic is the schema authority** — every new table/column ships an idempotent migration; `create_all()` only fills missing tables.
- **Every feature behind a PostHog flag**, defaulted OFF; `{product}.{feature}` key convention.
- **`--dev` flag** continues to unlock all tiers for single-user eval on PenguinTech domains.
- Modules stay ENV-toggleable (`ELDER_MODULE_*`, `ELDER_MODULES_ENABLED`) + tenant-toggle (`tenant_modules`); the new group toggle is additive.

---

## Current State (verified 2026-08-10)

- **15 modules**, structurally equal, in `apps/api/modules/__init__.py::MODULES`. Only `access_reviews` carries a `license_feature`. `effective = licensed AND tenant_enabled`; `module_licensed()` fails open.
- **Nav is flat** — `web/src/modules/registry.ts` (13 manifests; `flows` + `helpdesk` are backend-only). No group concept anywhere.
- **Licensing:** `penguin_licensing` client → `Tenant.subscription_tier` (free-text) + `storage_quota_gb`. **No user/team/tenant/node/object counting or enforcement exists in Elder's code** — this system is net-new.
- **`/helpdesk` owns 13 `hd_` tables** (`apps/api/modules/helpdesk/models/helpdesk.py`) + routes. Intake already writes native `issues`; tickets map to `issue_type='support'`. Cross-module dependency: `issues` + `webhooks_alerting` import `helpdesk.common.identity_in_tenant`.

---

## Part A — Module Grouping + Group Toggles

**Groups (a presentation + toggle layer over the existing flat registry — NOT a code move of module dirs):**

| Group | Modules |
|---|---|
| **Core** | infrastructure, ipam, discovery, sbom, secrets, services_oncall, access_reviews, webhooks_alerting |
| **CRM** | contacts + companies (ex-helpdesk → OUs/identities), support-issues view (`issue_type=support`), intake forms |
| **Workflow** | issues (all), streams, flows |
| **KB** | documents, pages, diagrams |

- Add a `group` attribute to each module's registry entry (backend `MODULES` + frontend manifest `FrontendModule`). Purely additive — no directory restructure (avoids churn + import breakage).
- **Group ENV toggle:** `ELDER_GROUP_<CORE|CRM|WORKFLOW|KB>=true|false`, resolved in `modules/registry.py` — a group=false disables all its modules unless a per-module `ELDER_MODULE_*` override re-enables. Precedence: per-module override > group toggle > `ELDER_MODULES_ENABLED`.
- **Frontend nav:** `useModules.ts` groups the enabled modules' nav under their group header (sidebar sections). `MenuCategory` gains an optional `group` tag; ungrouped modules fall back to a default section.
- CRM is a **view composition**, not a new module: it surfaces the `support`-typed issues + the contact/company entities. `issues` physically stays in Workflow.

**Deliverable:** grouped sidebar + group ENV toggles, all 15 modules still individually functional.

---

## Part B — License-Enforcement System (net-new)

**Components:**

1. **Tier + limits resolver** (`shared/licensing/limits.py`): resolve `(tier, limits)` from `penguin_licensing` validation; `limits` is a dataclass of the tier matrix defaults, overridable by license-server values. Cached (Redis, 60s, `elder:lic:{tenant}`), graceful-degrade to last-known/free on outage. `--dev` + PenguinTech-domain bypass respected.
2. **Counters** (`shared/licensing/counters.py`): tenant-scoped counts for users (by role class: global-admin / tenant-admin / member), teams (OU type=team), tenants, and objects (village_id-bearing rows — ties to `[[elder-universal-village-id]]`). Read-through Redis cache; source of truth is the DB.
3. **Node counter**: backend replica count per service type — read from the deployment (K8s downward API / a registered-nodes table heartbeat). Enforced at pod-registration/startup, not per-request.
4. **Hard-block guard** (`penguin-aaa`-adjacent decorator/middleware): `@enforce_limit(kind)` on the create paths that grow a metered dimension — refuse with **402 + upgrade payload** when at/over cap. Applied at: user-invite/create, team(OU)-create, tenant-create, and object-create for Free (the 1001st object).
5. **Enforcement points (initial):** user creation (global/tenant-admin caps + member ∞), team creation (Free=1), tenant creation (Free/Pro=1), Free object quota (1000). SSO/AI/whitelabel/KMS keep their existing capability-tier checks.

**Data flow:** create request → tenant middleware → `@enforce_limit` reads counter vs resolved limit → 402 if over, else proceed → on success, counter cache invalidated.

**Feature flag:** `elder.license-enforcement`, default OFF; when OFF, counting still runs (observability) but never blocks — lets us validate counts before flipping the wall on.

---

## Part C — `/helpdesk` Retirement

**Absorption map (each row is a sub-project):**

| hd_ capability | lands in | notes |
|---|---|---|
| hd_tickets | issues (`issue_type=support`) | ✅ model done; migrate residual rows |
| hd_ticket_messages | issue comments | ✅ mostly; migrate residual |
| hd_intake_forms / submissions | intake_forms → native issues | ✅ done |
| hd_ticket_attachments | **new `issue_attachments`** (VillageIDMixin, tenant-scoped, object store ref) | build |
| hd_sla_policies | **issue SLA** (per-OU policy, timers on issues) — worker aiocron sweep | build (overlaps Plan 06) |
| hd_canned_responses | **canned_responses** attached to issue comments | build |
| hd_email_accounts / hd_email_logs | **email-intake worker** (Plan 06) — inbound email → support issue | build (Plan 06) |
| hd_companies | OUs `type=company` | data migration + convention already seeded |
| hd_contacts | identities `type=customer_contact` (namespaced) | data migration |
| hd_teams / hd_team_members | OUs `type=team` + membership | data migration |
| `helpdesk.common.identity_in_tenant` | relocate → `shared/` or `apps/api/common/` util | quick; unblocks import removal |
| **delete** 13 `hd_` tables + `modules/helpdesk/` | — | final step, after all above + data migrated |

- **Data migration** per absorbed table: idempotent Alembic data-migration (or a K8s Job script) copying rows into the unified targets with village_id minting, preserving created_at + linkage.
- **Deletion gate:** helpdesk deletion only after (a) every capability absorbed + tested, (b) `identity_in_tenant` relocated + importers updated, (c) data migrated + verified, (d) no remaining `modules.helpdesk` / `hd_` import anywhere.

---

## Phasing (program sequence)

1. **Phase 1 — Grouping + toggles + helper relocation** (foundational, low-risk; unblocks nav + removes the deletion blocker early).
2. **Phase 2 — License-enforcement scaffolding** (resolver + counters + guard, flag OFF / observe-only).
3. **Phase 3 — Absorptions** (parallelizable sub-projects): `issue_attachments` → canned responses → issue SLA → email intake (Plan 06) → CRM-entity migration.
4. **Phase 4 — Flip enforcement ON** (after counters validated in observe-only).
5. **Phase 5 — Delete `/helpdesk`** (tables + routes + module), after Phase 3 complete + data migrated.

Each phase is independently shippable and becomes its own implementation plan (via `writing-plans`). Phase 1 is the first plan.

---

## Testing Strategy

- Unit + integration (90%+) against the `elder-test:3.13` container (host python has broken otel — see test recipe). Each metered guard gets a regression test: at-limit → 402, under-limit → 200, and observe-only-mode → never blocks.
- Data-migration tests: seed legacy `hd_` rows → run migration → assert unified rows + village_id + no data loss; idempotent re-run.
- Screenshot refresh for the grouped nav (UI-affecting).
- Enforcement flag default OFF; counters validated in observe-only before flip.

## Out of Scope (this spec)

- Billing/checkout integration (the 402 links to an upgrade prompt; payment flow is separate).
- WaddleAI/BYOK AI runtime wiring (tier gate only; AI plumbing is its own work).
- Physical relocation of module source directories (grouping is a registry/nav attribute).

## Open Questions / Risks

- **Node counting mechanism** — K8s downward API vs a heartbeat/registration table? (Phase 2 decision.)
- **Object-quota definition** — confirm "object" = any village_id-bearing row (ties to universal village_id rollout, which is itself incomplete for some tables).
- **CRM-entity migration** on live tenants — companies/contacts/teams → OUs/identities must preserve existing references; needs a mapping table during transition.
- Grouping placement of `webhooks_alerting` (Core vs Workflow) — currently Core; revisit if it feels misplaced in the nav.
