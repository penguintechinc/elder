# PostgreSQL Row-Level Security (Tenant Isolation)

Defense in depth under the app-layer tenant scoping
(`apps/api/utils/tenant_scoping.py`). Migration `042_rls_tenant_isolation`,
runtime `shared/database/rls.py`. PostgreSQL only; SQLite/MySQL paths are untouched.

## How it works

```
JWT tenant claim ──> populate_claims ──> ContextVar ──┐   worker/flows: job envelope ──┐
                                                      v                                v
            SQLAlchemy Engine "begin" listener  (every PG transaction, any DAL/engine)
              SELECT set_config('app.tenant_id', <id|''>, true),
                     set_config('app.rls_scope', <'system'|''>, true)      -- is_local = transaction-local
                                                      v
   policy elder_tenant_isolation (USING + WITH CHECK):
     tenant_id = elder_current_tenant_id()  OR  elder_rls_system_scope()
```

- **Transaction-local**: PostgreSQL reverts both GUCs at COMMIT/ROLLBACK, so a pooled
  connection can never carry one tenant into another request. The listener also re-stamps
  (resets) them at every `BEGIN`.
- **Fail closed**: unset/empty/non-numeric GUC => `elder_current_tenant_id()` is NULL =>
  zero rows, writes rejected. NULL-tenant (legacy/unowned) rows are invisible outside system scope.
- **`ENABLE` + `FORCE`** on every covered table, so the table *owner* is constrained too.
- Tenant comes only from the verified JWT (API) or the job envelope (worker/flows-invoker) — never request input.

## Database role requirement (deployment)

RLS never constrains a **superuser** or a role with **`BYPASSRLS`**. The application must connect as
a plain role (`NOSUPERUSER NOBYPASSRLS`). Migrations/DDL run as the owner/superuser; `alembic/env.py`
puts the migration connection in system scope for data backfills.

- Startup logs `rls_role_bypasses_policies` (ERROR) when policies exist but the role bypasses them.
- `ELDER_RLS_STRICT=true` turns that into a startup failure (recommended once roles are fixed).
- The dev/test Postgres image's `POSTGRES_USER` is a superuser: policies exist there but are not enforced.

## System scope (explicit cross-tenant access)

No blanket bypass role. Cross-tenant work is `with system_scope(reason, purpose, actor=...)`:
structured audit log + OTel counter `elder.rls.system_scope.entered{reason}` on every entry.

| Reason | Used by |
|---|---|
| `SUPER_ADMIN` | global-admin/superuser acting on another tenant (`tenants` detail/stats, `tenant_modules`); requires `actor` and a working audit sink (row in `audit_logs`), refused otherwise |
| `PUBLIC_TOKEN` | unauthenticated, token-addressed routes: shared diagrams, public documents, stream/flow webhooks, AlertManager webhook |
| `BACKGROUND_SWEEP` | access-review overdue sweep, discovery job -> tenant lookup |
| `AUTH_BOOTSTRAP` | reserved for tranche 2 (login/SSO/SCIM pre-auth lookups) |
| `KILL_SWITCH` | `elder.disable-rls` flag is ON |

Known limit: GUC-based RLS trusts the SQL author. It stops application bugs (missing `WHERE tenant_id`,
IDOR) but not SQL injection with stacked statements, which could `set_config` a different tenant.

## Kill-switch

Core-platform-mechanism convention: PostHog flag **`elder.disable-rls`** — unseen/OFF = RLS on, ON = legacy
behaviour (every un-scoped transaction runs in `KILL_SWITCH` system scope, policies stay installed). The flag is
polled in a background thread (30 s) so a slow flag backend never blocks the DB path; evaluation errors keep the
last-known value. API process only (the worker/flows images ship no PostHog client).

## Coverage

Tranche 1 (migration 042): every table with a direct `tenant_id` column (82) except the deferred ones below.
`tests/unit/test_rls_table_registry.py` fails if a model gains `tenant_id` without a decision.

Deferred to tranche 2:

| Tables | Why |
|---|---|
| `identities`, `portal_users`, `idp_configurations`, `scim_configurations` | login/SSO/SCIM look up by unauthenticated input before a tenant is known; wrap in `system_scope(AUTH_BOOTSTRAP)` first |
| `entities`, `secrets`, `networking_resources` + 13 more org-scoped tables | no `tenant_id` column (scoped through `organization_id`); need a join policy |
| gRPC servicer (`organizations`, `dependencies`) | data RPCs have no JWT auth or tenant context; fail closed under RLS until a JWT interceptor lands |

Alembic-built databases drift from the models (some tables lack `tenant_id`): the migration skips and logs
a WARNING naming each unprotected table instead of failing the upgrade.
