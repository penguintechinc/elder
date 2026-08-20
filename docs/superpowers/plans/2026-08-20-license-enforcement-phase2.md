# Module Reorg — Phase 2: License Enforcement System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** A scale/structure license-enforcement system that hard-blocks over-limit actions (users/admins, teams, tenants, nodes, objects) per the tier model, gated by an `elder.license-enforcement` PostHog flag that **counts in observe-only before it ever blocks**.

**Architecture:** `apps/api/common/licensing/` package — `limits.py` (tier→`LimitSet` resolver, license-overridable), `counters.py` (per-tenant/global counts), `enforce.py` (`@enforce_limit` guard → 402 + upgrade payload, observe-only via flag). New `service_nodes` heartbeat table for node counting. Enforcement wired at tenant/user/team/object create paths + node registration.

**Tech Stack:** Quart / penguin-dal / SQLAlchemy+Alembic; `penguin_licensing` client (`validate().tier` + `.limits`); Redis (count caches); `apps/api/common/flags/posthog_client.flag_enabled`.

## Global Constraints (tier limits — defaults, license-overridable)

| Dimension | Free | Professional | Enterprise |
|---|---|---|---|
| Global admins | 1 | 1 | ∞ |
| Tenant admins | 0 | 10 | ∞ |
| Teams (per tenant) | 1 | ∞ | ∞ |
| Tenants | 1 | 1 | ∞ |
| Objects (per tenant) | 1000 | ∞ | ∞ |
| Nodes per service type | 1 | 1 | ∞ |
| Non-admin members | ∞ | ∞ | ∞ |

- `∞` = `None` in `LimitSet` (no limit).
- **Enforcement = hard block (HTTP 402 + upgrade payload)** ONLY when the `elder.license-enforcement` flag is ON; when OFF (default), count + log `license_limit_would_block` at WARN and **allow** (observe-only).
- Tier from `app.extensions["license_client"].validate().tier` (`community`/`professional`/`enterprise`; treat `community`==Free); `None` client → community fallback. `.limits` dict overrides any default (e.g. Enterprise `max_nodes`).
- Admin model is dual-track (see `identity.py`): **global admin** = `Identity.is_superuser OR Identity.portal_role=='admin'` (and `PortalUser.global_role` in {'admin','superadmin'}); **tenant admin** = `PortalUser.tenant_role=='admin'` + `Identity.portal_role=='admin'` scoped to a non-default tenant — approximate; document the heuristic.
- All new code: type hints (mypy), `@dataclass(slots=True)`, hooks-gated commits (ruff v0.8.4), container tests (`elder-test:3.13`), reset `elder_test` before judging.

---

### Task 1: `LimitSet` resolver — `apps/api/common/licensing/limits.py`

**Files:** Create `apps/api/common/licensing/__init__.py`, `apps/api/common/licensing/limits.py`; Test `tests/unit/test_license_limits.py`.

- [ ] Test: `resolve_limits(None)` → community `LimitSet` (max_global_admins=1, max_tenant_admins=0, max_teams=1, max_tenants=1, max_objects=1000, max_nodes_per_type=1). A fake client with `tier='enterprise'` → all `None`. A client whose `.limits={'max_objects':5000}` overrides that one field.
- [ ] Implement `@dataclass(slots=True) class LimitSet` (fields above, all `int | None`); `TIER_DEFAULTS: dict[str, LimitSet]` for `community`/`professional`/`enterprise` per the table; `resolve_limits(license_client) -> tuple[str, LimitSet]` — get tier via `client.validate().tier` (fallback `"community"` on None/exception), start from `TIER_DEFAULTS[tier]`, then apply any matching keys from `validate().limits` via `dataclasses.replace`. Cache per-process 60s keyed on tier (module-level `LazyLock`/simple TTL) — acceptable since tier rarely changes.
- [ ] Docstrings (why community==Free). Run test → PASS. Commit `feat(licensing): tier->LimitSet resolver`.

---

### Task 2: `service_nodes` heartbeat table + registration/count

**Files:** Create `apps/api/models/service_node.py` (ServiceNode: id, service_type str, pod_id str unique, heartbeat_ts datetime, created_at); Alembic migration `039_service_nodes.py` (mirror 038 idempotent pattern, revises 038); register in `apps.api.modules`/models import; startup hook + heartbeat in `apps/worker/main.py` (aiocron) and API startup. Test `tests/unit/test_service_nodes.py`.

- [ ] Test: insert 2 active + 1 stale (old heartbeat) `service_nodes` rows for `service_type='scanner'`; `count_active_nodes(db, 'scanner', stale_after_s=90)` returns 2.
- [ ] Implement: `ServiceNode` model + migration (String(64) service_type index, String(128) pod_id unique, DateTime heartbeat_ts). `register_node(db, service_type, pod_id)` upsert; `heartbeat_node(db, pod_id)` update ts; `count_active_nodes(db, service_type, stale_after_s=90)` = rows with `heartbeat_ts > now - stale`. Wire: API `main.py` startup registers `service_type=os.getenv("ELDER_SERVICE_TYPE","main")`, pod_id `os.getenv("HOSTNAME")`; worker aiocron `@aiocron.crontab("* * * * *")` heartbeats. (Registration is best-effort; wrap in try/except, never crash startup.)
- [ ] Run → PASS. Commit `feat(licensing): service_nodes heartbeat table + node counting`.

---

### Task 3: counters — `apps/api/common/licensing/counters.py`

**Files:** Create `counters.py`; Test `tests/unit/test_license_counters.py`.

- [ ] Test (seed a tenant with identities/portal_users/organizations/issues): `count_users(db, tenant_id)` (identities+portal_users), `count_global_admins(db, tenant_id)`, `count_tenant_admins(db, tenant_id)`, `count_teams(db, tenant_id)` (organizations type='team'), `count_tenants(db)`, `count_objects(db, tenant_id, redis)` (sum across village_id tables, cached).
- [ ] Implement each counter with penguin-dal queries per the admin heuristic (Global Constraints). `count_objects`: iterate a module-level `OBJECT_TABLES: tuple[str,...]` (the 32 village_id tables from the extraction that have a `tenant_id` column — verify each has tenant_id; skip those that don't and note them), `SUM(db(db[t].tenant_id==tenant_id).count())`; cache in Redis `elder:objcount:{tenant_id:08x}` 60s (separate key from `elder:vid:*`). `count_nodes` re-exports Task 2's `count_active_nodes`.
- [ ] Run → PASS. Commit `feat(licensing): per-tenant/global limit counters`.

---

### Task 4: `@enforce_limit` guard + observe-only flag — `apps/api/common/licensing/enforce.py`

**Files:** Create `enforce.py`; Test `tests/unit/test_license_enforce.py`.

- [ ] Test: with the flag ON, an at-limit `kind='tenant'` (count>=limit) → the guard raises/returns a 402 with `{"error":"limit_reached","limit":"tenant","tier":...,"upgrade":true}`; under limit → passes. With the flag OFF (observe-only) → at-limit does NOT block (returns None/allows) but logs `license_limit_would_block`. Limit `None` (∞) → never blocks.
- [ ] Implement `async def check_limit(kind: str, tenant_id: int | None) -> tuple|None`: resolve `(tier, limits)` (Task 1), pick the relevant limit + counter (Task 3) for `kind` in `{"tenant","global_admin","tenant_admin","team","object","node"}`, compare. If over-limit: read `flag_enabled("elder.license-enforcement", distinct_id=str(tenant_id or "global"), default=False)`; if ON → return a `(payload, 402)`; if OFF → `logger.warning("license_limit_would_block", kind=kind, tenant_id=..., count=..., limit=...)` and return None. Provide a decorator `@enforce_limit(kind)` for Quart routes that calls `check_limit` and short-circuits with the 402 tuple, deriving tenant from `g.claims`.
- [ ] Run → PASS. Commit `feat(licensing): enforce_limit guard (402, observe-only flag)`.

---

### Task 5: wire tenant + node enforcement

**Files:** Modify `apps/api/api/v1/tenants.py:183` (`create_tenant`); node registration already gated at startup (Task 2) — add `check_limit("node", None)` before `register_node`, observe-only. Test additions.

- [ ] Test: at tenant limit + flag ON → `POST /tenants` returns 402; flag OFF → 201 + would-block log. Node over-limit at startup logs would-block.
- [ ] Implement: `@enforce_limit("tenant")` (or inline `check_limit`) in `create_tenant` before the insert; in Task 2's `register_node`, call `check_limit("node", None)` first (observe-only, log only — do not refuse a pod from starting even when ON; node is a soft scale gate, log at WARN). Commit `feat(licensing): enforce tenant + node limits (observe-only)`.

---

### Task 6: wire user/admin enforcement

**Files:** Modify `apps/api/api/v1/identities.py:147` (`create_identity`), `apps/api/api/v1/portal_auth.py:93` (`register`) + `PortalAuthService.create_portal_user`. Test additions.

- [ ] Test: creating a 2nd global admin on Free (flag ON) → 402; creating a member → allowed; observe-only logs.
- [ ] Implement: before insert, determine the role being created (superuser/portal_role='admin' → global_admin; tenant_role/portal_role admin on a tenant → tenant_admin; else member/unlimited). Call `check_limit("global_admin"|"tenant_admin", tenant_id)` for the relevant kind (members are ∞ → skip). Same in the portal register path. Commit `feat(licensing): enforce global/tenant admin limits (observe-only)`.

---

### Task 7: wire team enforcement

**Files:** Locate the organization-create path (grep `db.organizations.insert`); if a `POST /organizations`-style endpoint exists, gate creation of `type=='team'`; else gate at the shared org-create service. Test additions.

- [ ] Test: at team limit on Free (flag ON) creating a `type='team'` organization → 402; observe-only logs.
- [ ] Implement: `check_limit("team", tenant_id)` guarding org creates where `type=='team'`. If no single create endpoint exists, add the check in the lowest-level shared org-create helper used by the team-creating flows (document which). Commit `feat(licensing): enforce team limit (observe-only)`.

---

### Task 8: wire object-quota enforcement (Free 1000)

**Files:** A reusable guard applied to the primary object-create endpoints across modules. Test additions.

- [ ] Test: at object limit on Free (flag ON) creating an issue/entity → 402; Pro/Enterprise (limit None) never blocks; observe-only logs.
- [ ] Implement: add `check_limit("object", tenant_id)` at the create POST of each primary object module — at minimum: issues (`issues.py::create_issue`), infrastructure entities (`entities` create), organizations, ipam (prefix/address/vlan), documents, pages, diagrams, streams, webhooks. Use a shared helper so each site is one line. `OBJECT_TABLES` (Task 3) is the source of truth for what counts; this task wires the create sites. **Log (via `log()`/PR body) which object-create sites are covered vs deferred** — full 32-site coverage is the completion goal; wire the high-volume primaries here, list the remainder. Commit `feat(licensing): enforce Free object quota at primary create paths (observe-only)`.

---

### Task 9: integration + observe-only validation

- [ ] Add an integration test proving end-to-end: seed a Free tenant at each limit; with flag OFF, all creates succeed + emit `license_limit_would_block`; with flag ON, each returns 402. Enterprise tier (limits None) never blocks.
- [ ] Run the FULL unit suite (`tests/unit`) on a reset DB — 0 failures (catches import/fixture breakage the per-file runs miss). Commit `test(licensing): observe-only + hard-block integration coverage`.

---

## Self-Review
- Spec Part B covered: resolver (T1), node table (T2), counters (T3), guard+flag (T4), enforcement points tenant/node (T5), users/admins (T6), teams (T7), objects (T8), integration (T9).
- Observe-only default OFF everywhere; flag flip (Phase 4) is what turns blocking on — never blocks in this phase's default.
- Admin-granularity is heuristic (documented) pending the RBAC model cleanup; object coverage is primaries + a documented remainder list.

## Out of scope
Phase 4 (flip enforcement ON after validation), `/helpdesk` absorption (Phase 3), RBAC model cleanup for true tenant-admin roles.
