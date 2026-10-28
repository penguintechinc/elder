# PostgreSQL Row-Level Security (Tenant Isolation)

Defense in depth under the app-layer tenant scoping (`apps/api/utils/tenant_scoping.py`).
Migrations `042_tenant_id_drift_repair` + `043_rls_tenant_isolation`, runtime `shared/database/rls.py`.
PostgreSQL only; SQLite/MySQL paths are untouched.

## Status: delivered vs enforced

**Delivered by this change:** policies (migration 043), the per-transaction tenant-context plumbing,
explicit system scopes, the startup gates, tests. **Enforcement is not on anywhere yet.** It needs both:

1. migration **043 applied** — the API only runs `create_all()` at startup; `run_migrations()` has no caller and
   no chart ships an alembic job, so no environment has it applied today;
2. an application role that is **NOSUPERUSER, NOBYPASSRLS and not the table owner** — every environment connects
   as the DB bootstrap superuser today.

Neither is provisioned by the charts/compose files yet (separate follow-up PR). Until both hold the code is inert:
the database enforces nothing, and `check_rls_posture` says so at startup (ERROR log naming the reason,
fatal with `ELDER_RLS_STRICT=true`).

| Environment | Migration 043 | Runtime role | Enforced |
|---|---|---|---|
| dev / docker-compose, local alpha | not applied | bootstrap superuser | no |
| beta / gamma / prod (Helm) | not applied (no alembic job) | bootstrap superuser | no |
| test suite (`tests/integration/test_rls_*`) | applied to throwaway DBs | created NOSUPERUSER role | yes — the only place today |

## How it works

```
JWT tenant claim ──> populate_claims ──> ContextVar ──┐   websocket ticket tenant ──┐   job envelope ──┐
                                                      v                             v                  v
            SQLAlchemy Engine "begin" listener  (every PG transaction, any DAL/engine)
              SELECT set_config('app.tenant_id', <id|''>, true),
                     set_config('app.rls_scope', <'system'|''>, true)      -- is_local = transaction-local
                                                      v
   policy elder_tenant_isolation (USING + WITH CHECK):
     tenant_id = (SELECT elder_current_tenant_id())  OR  (SELECT elder_rls_system_scope())
```

- **Transaction-local**: PostgreSQL reverts both GUCs at COMMIT/ROLLBACK, so a pooled connection never carries
  one tenant into another request; the listener also re-stamps (resets) them at every `BEGIN`.
- **Fail closed**: unset/empty/non-numeric GUC => `elder_current_tenant_id()` is NULL => zero rows, writes rejected.
  Transactions that begin with no tenant and no scope are counted (`elder.rls.unbound_transactions`).
- **`ENABLE` + `FORCE`** on every covered table, so the table *owner* is constrained too (superusers and
  `BYPASSRLS` roles are never constrained).
- Tenant comes only from the verified JWT (API), the single-use websocket ticket minted from a verified JWT, or
  the job envelope (worker / flows-invoker). Never from request input.
- **Policy performance**: the helper calls are scalar sub-selects, so PostgreSQL evaluates each once per query
  (InitPlan) instead of once per row. 1.5M-row table, PG16: `count(*)` 330 ms -> 36 ms, paginated list
  0.33 -> 0.24 ms, by-id 0.031 -> 0.021 ms. Queries that carry their own `tenant_id = ?` predicate still use
  index/bitmap scans (asserted by `test_policy_helpers_are_init_plans_and_index_scans_remain_possible`).
  **Follow-up issue (not done here):** on `create_all()` databases about two thirds of the covered tables
  (65 per the review's catalog scan, 67 per a model-metadata scan) have no `tenant_id`-leading index, so
  tenant-filtered queries on them are seq scans; add indexes table by table (no indexes added in this PR).
- **Websockets**: Quart skips `before_request` for websockets, so `populate_claims` never binds a tenant for them.
  `diagrams/routes/collab.py` binds the ticket tenant itself (the only websocket route; a test fails if a second
  appears without being audited). An unusable ticket tenant refuses the connection.

## Database role requirement (deployment)

Run migrations/DDL as the owner (or superuser); run the application as a separate plain role. `alembic/env.py`
puts the migration connection in system scope so data backfills are not filtered by the policies.

- Startup logs `rls_not_enforced` (ERROR) with counts when enforcement is incomplete: role superuser/BYPASSRLS,
  migration not applied (`0 of N tables protected`), or some tables unprotected.
- `ELDER_RLS_STRICT=true` makes any of those a **startup failure** in the API, worker and flows-invoker
  (including "migration not applied"). Recommended once roles and the migration job are provisioned.

## System scope (explicit cross-tenant access)

No blanket bypass role. Cross-tenant work is `with system_scope(reason, purpose, actor=...)` (`async with
system_scope_async(...)` in coroutines). Every entry is a structured log + OTel counter
`elder.rls.system_scope.entered{reason}`.

| Reason | Used by | Durable `audit_logs` row |
|---|---|---|
| `SUPER_ADMIN` | global-admin/superuser acting on another tenant (`tenants` detail/stats, `tenant_modules`); requires `actor` and a working sink, refused otherwise; the async form writes off the event loop | yes |
| `PUBLIC_TOKEN` | unauthenticated, token-addressed routes: shared diagrams, public documents, stream/flow webhooks, AlertManager webhook | no (log at debug + counter) — an anonymous caller must not be able to flood `audit_logs` |
| `BACKGROUND_SWEEP` | access-review overdue sweep, discovery job -> tenant lookup | no (log + counter) |
| `MAINTENANCE` | the four direct-DAL seed scripts | no (log + counter) |
| `AUTH_BOOTSTRAP` | reserved for tranche 2 (login/SSO/SCIM pre-auth lookups) | no |
| `KILL_SWITCH` | `elder.disable-rls` flag is ON | no |

## Kill-switch semantics

Core-platform-mechanism convention: PostHog flag **`elder.disable-rls`** — unseen/OFF = RLS on, ON = legacy
behaviour (every un-scoped transaction runs in `KILL_SWITCH` system scope, policies stay installed). Polled in a
background thread (30 s) so a slow flag backend never blocks the DB path. **A flag outage never turns RLS off:**
the state starts OFF, a first-evaluation failure leaves it OFF, and an error while OFF stays OFF. An error while
ON keeps the last-known ON (deliberate graceful degradation: an outage must not flip a deliberately disabled
control back mid-incident). The API evaluates the flag at startup; worker/flows images ship no PostHog client
(always RLS-on, fail-safe).

## Coverage

Tranche 1 (migration 043): every table with a direct `tenant_id` column (82) except the deferred ones.
`tests/unit/test_rls_table_registry.py` fails if a model gains `tenant_id` without a decision, and if the runtime
list (`shared/database/rls_tables.py`) diverges from the frozen copy in the migration.

Alembic-built vs `create_all()`: migration **042** adds the missing `tenant_id` (nullable, FK, indexed, backfilled
from the parent group/review) to the five access-review/group-access tables that alembic never gave it.
Migration 043 **fails** (does not warn) when an expected table is missing or lacks `tenant_id`, except the
documented allowlist (`references`: only `create_all()` builds it — the startup posture check flags it if it
appears unprotected). `tests/integration/test_rls_migration.py` builds both schemas and asserts the protected
sets match.

| Not covered | Why |
|---|---|
| `identities`, `portal_users`, `idp_configurations`, `scim_configurations` (deferred) | login/SSO/SCIM look up by unauthenticated input before a tenant is known; wrap in `system_scope(AUTH_BOOTSTRAP)` first |
| `identity_groups` | the model has no `tenant_id` (alembic-built DBs do); groups are globally named and the create route sets no tenant — needs a data-model decision |
| `entities`, `secrets`, `networking_resources` + 13 more org-scoped tables | no `tenant_id` column (scoped via `organization_id`); need a join policy |
| gRPC data RPCs (`organizations`, `dependencies`, entity/graph RPCs) | no JWT auth or tenant context. Once RLS is enforced they fail closed with `UNAUTHENTICATED` + log + counter (`apps/api/grpc/rls_guard.py`); a pass-through today. No auth was invented |

## Applying migration 043 safely

- **NULL-tenant rows** (`dependencies`, `issue_comments`, `issues`, `milestones`, `organizations`, `projects`,
  `webhooks` allow NULL) become invisible once enforced. 043 pre-flights per-table NULL counts and **aborts**
  listing them. Remedy: assign owners, or re-run with `ELDER_RLS_NULL_TENANT_BACKFILL=default` to assign them to the
  tenant with slug `default` (must exist). The backfill is idempotent and deliberately not undone by `downgrade`.
  Data-model follow-up: make those columns `NOT NULL`.
- **Locks**: ENABLE/FORCE/policy DDL takes `ACCESS EXCLUSIVE`. 043 runs each table in its own short transaction
  (outside the migration transaction) under `SET LOCAL lock_timeout` with bounded retry
  (`ELDER_RLS_LOCK_TIMEOUT_MS`=5000, `ELDER_RLS_LOCK_RETRIES`=5, `ELDER_RLS_LOCK_BACKOFF_S`=2), so locks are never
  held across tables; a timeout aborts naming the table and a re-run resumes. Still run it in a low-traffic
  maintenance window.
- Downgrade: per table `NO FORCE` + `DISABLE` + `DROP POLICY`, then drops the helper functions. Migration 042's
  downgrade is a deliberate no-op (the added columns are kept).

## Residual limits (reproduced in review; accepted/documented)

- **Not a defence against SQL injection with stacked statements**: GUC-based RLS trusts the SQL author; an attacker
  who can run `set_config` can pick a tenant. It stops application bugs (missing `WHERE tenant_id`, IDOR).
- **Job envelopes are unsigned JSON in Redis**: whoever can write the job stream chooses the tenant. Redis access is
  part of the trust boundary; signing envelopes is a separate follow-up. Same for websocket tickets in Redis.
- **Backups**: `pg_dump` by a FORCE-RLS non-superuser fails ("query would be affected by row-level security
  policy"); `--enable-row-security` dumps 0 rows. Backups need a separate `BYPASSRLS` role.
- **Owner can disable RLS**: a table-owner application role can `ALTER TABLE ... DISABLE ROW LEVEL SECURITY`. Use a
  distinct owner role for migrations.
- **`TRUNCATE` is not covered** by policies (needs the TRUNCATE privilege; do not grant it to the app role).
- **Existence oracles**: cross-tenant FK references are accepted, and FK / UNIQUE / `ON CONFLICT DO NOTHING` can
  reveal that a row exists in another tenant.
- **Views**: a superuser-owned view bypasses RLS unless created `WITH (security_invoker = true)` (none exist today).
- **Mid-transaction changes**: a transaction keeps the tenant it had at `BEGIN`; changing the context mid-transaction
  has no effect until the next one.
- **Task scope inheritance**: an `asyncio` task created inside a scope keeps that scope after the block exits (context
  is copied at creation). A raw `ThreadPoolExecutor` + `set_tenant` would persist across jobs (no such caller exists;
  use `tenant_context` / `run_in_threadpool`).
- **Logs** carry numeric internal tenant/actor references (Elder has no `public_id` yet); never secrets.

## Test environment notes

`tests/unit/test_jobbus.py` hardcodes `redis://localhost:56379/5` (without Redis there, 19 fail + 19 error on any
branch). Combined `tests/unit` + `tests/integration` in one pytest process shows 5 pre-existing
`test_refs_integration` failures; CI runs them separately.
