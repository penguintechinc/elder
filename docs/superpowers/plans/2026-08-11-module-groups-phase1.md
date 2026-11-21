# Module Reorg — Phase 1: Grouping + Toggles + Helper Relocation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group Elder's 15 modules into Core/CRM/Workflow/KB with a `group` attribute + `ELDER_GROUP_*` env toggles + grouped sidebar nav, and relocate the `identity_in_tenant` helper out of `helpdesk` into `apps/api/common/` (removing the cross-module dependency that blocks `/helpdesk` deletion).

**Architecture:** Additive `group` field on the backend `ModuleManifest` and frontend `FrontendModule`/`ModuleInfo`; a group-toggle layer in `resolve_enabled` (precedence: per-module > group > `ELDER_MODULES_ENABLED`); nav bucketed by group in `registry.ts::navFor`; a new `apps/api/common/identity.py` de-duplicating the helper. No module directories move.

**Tech Stack:** Python 3.13 / Quart / penguin-dal; React 18 + TS + Vite; pytest (container `elder-test:3.13`), jest.

## Global Constraints

- **Grouping is a registry/nav attribute — NO module directory moves.**
- **Group mapping (locked):** Core = `infrastructure, ipam, discovery, sbom, secrets, services_oncall, access_reviews, webhooks_alerting`; CRM = `helpdesk`; Workflow = `issues, streams, flows`; KB = `documents, pages, diagrams`. Group keys are lowercase `core|crm|workflow|kb`.
- **Toggle precedence:** `ELDER_MODULE_<NAME>` (highest) > `ELDER_GROUP_<GROUP>` > `ELDER_MODULES_ENABLED` base.
- **License model:** grouping is presentation/deployment only — **no group is tier-locked** (see `general.md` License Tiers; the reorg spec).
- **Everything hook-gated:** commits run the real pre-commit hooks (ruff, gitleaks, etc.); run tests in the `elder-test:3.13` container (host python has broken opentelemetry). `ruff` pinned `v0.8.4`.
- **Tests:** unit only in this phase; reset the `elder_test` DB before judging a suite run.

---

### Task 1: `group` field on `ModuleManifest` + assign every module a group

**Files:**
- Modify: `apps/api/modules/registry.py` (add field to `ModuleManifest`, ~line 45-56)
- Modify: `apps/api/modules/__init__.py:313-567` (add `group=...` to all 15 entries)
- Test: `tests/unit/test_module_groups.py` (new)

**Interfaces:**
- Produces: `ModuleManifest.group: str` — consumed by Tasks 2, 3.

- [ ] **Step 1: Write failing test** `tests/unit/test_module_groups.py`:
```python
"""Phase 1: every module belongs to exactly one valid group, per the locked mapping."""
from apps.api.modules import MODULES

VALID_GROUPS = {"core", "crm", "workflow", "kb"}
EXPECTED = {
    "infrastructure": "core", "ipam": "core", "discovery": "core", "sbom": "core",
    "secrets": "core", "services_oncall": "core", "access_reviews": "core",
    "webhooks_alerting": "core",
    "helpdesk": "crm",
    "issues": "workflow", "streams": "workflow", "flows": "workflow",
    "documents": "kb", "pages": "kb", "diagrams": "kb",
}

def test_every_module_has_a_valid_group():
    for m in MODULES:
        assert m.group in VALID_GROUPS, f"{m.name} has invalid group {m.group!r}"

def test_group_mapping_matches_spec():
    got = {m.name: m.group for m in MODULES}
    assert got == EXPECTED
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: 'ModuleManifest' object has no attribute 'group'`):
```
docker run --rm --network host -e DATABASE_URL=postgresql://elder_test:elder_test_password@localhost:55432/elder_test -e REDIS_URL=redis://localhost:56379/0 -e PYTHONHASHSEED=0 -v $PWD:/app -w /app --entrypoint python3 elder-test:3.13 -m pytest tests/unit/test_module_groups.py -q 2>&1 | grep -iE "passed|failed|error"
```

- [ ] **Step 3: Add the field.** In `registry.py`, add to the `ModuleManifest` dataclass (after `nav_id: str`, keep it before the defaulted `default_enabled`): `group: str` and update the docstring Attributes list with one line. It must be a required (non-default) field placed **before** `default_enabled: bool = True` (frozen dataclass field ordering).

- [ ] **Step 4: Assign groups.** In `apps/api/modules/__init__.py`, add `group="<key>"` to each of the 15 `ModuleManifest(...)` entries per the EXPECTED mapping above (place it next to `nav_id=`).

- [ ] **Step 5: Run — expect PASS.** Same command as Step 2.

- [ ] **Step 6: Commit** `feat(modules): add group attribute to ModuleManifest (Core/CRM/Workflow/KB)`.

---

### Task 2: `ELDER_GROUP_*` toggle layer in `resolve_enabled`

**Files:**
- Modify: `apps/api/modules/registry.py:59-136` (`resolve_enabled`)
- Test: `tests/unit/test_module_group_toggle.py` (new)

**Interfaces:**
- Consumes: `ModuleManifest.group` (Task 1).

- [ ] **Step 1: Write failing test:**
```python
"""Phase 1: ELDER_GROUP_* toggles; per-module override beats group beats base."""
from apps.api.modules.registry import resolve_enabled

def _names(env): return {m.name for m in resolve_enabled(env)}

def test_group_false_disables_all_modules_in_group():
    env = {"ELDER_MODULES_ENABLED": "all", "ELDER_GROUP_CRM": "false"}
    assert "helpdesk" not in _names(env)

def test_group_true_enables_group_modules():
    env = {"ELDER_MODULES_ENABLED": "issues", "ELDER_GROUP_KB": "true"}
    got = _names(env)
    assert {"documents", "pages", "diagrams"}.issubset(got)

def test_per_module_override_beats_group():
    # group off, but the module is explicitly on -> module wins
    env = {"ELDER_MODULES_ENABLED": "all", "ELDER_GROUP_CRM": "false",
           "ELDER_MODULE_HELPDESK": "true"}
    assert "helpdesk" in _names(env)
```

- [ ] **Step 2: Run — expect FAIL** (group vars ignored → helpdesk still present in test 1).

- [ ] **Step 3: Implement.** In `resolve_enabled`, AFTER the `ELDER_MODULES_ENABLED` base set is built and BEFORE the existing per-module override loop, insert a group-override pass:
```python
    # Apply group-level overrides (ELDER_GROUP_<GROUP>=true|false).
    # Precedence: per-module override (below) > group toggle > ELDER_MODULES_ENABLED.
    groups = {m.group for m in MODULES}
    for group in groups:
        env_key = f"ELDER_GROUP_{group.upper()}"
        if env_key in env:
            on = env[env_key].lower() in ("true", "1", "yes")
            for module in MODULES:
                if module.group == group:
                    if on:
                        enabled_names.add(module.name)
                    else:
                        enabled_names.discard(module.name)
```
(The existing per-module loop that follows already runs last, so it wins — no change needed there.)

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `feat(modules): ELDER_GROUP_* enablement toggles (per-module > group > base)`.

---

### Task 3: expose `group` on the `/api/v1/modules` response

**Files:**
- Modify: `apps/api/api/v1/modules.py:17-103` (`list_modules`, the per-module dict)
- Test: `tests/unit/test_api_modules_group.py` (new) — mirror the auth/setup pattern of an existing `tests/unit/test_api_*.py` (mock `get_current_user`, `generate_token`, `async_client`).

**Interfaces:**
- Produces: each `/modules` item gains `"group"` — consumed by frontend Task 5.

- [ ] **Step 1: Write failing test** asserting `GET /api/v1/modules` returns 200 and every item in `data["modules"]` has a `"group"` in `{"core","crm","workflow","kb"}`, and that the `helpdesk` item's group is `"crm"`.

- [ ] **Step 2: Run — expect FAIL** (KeyError/assert on missing `group`).

- [ ] **Step 3: Implement.** Add `"group": manifest.group,` to the dict appended in `modules_list` (next to `"nav_id": manifest.nav_id,`).

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `feat(api): include module group in /modules response`.

---

### Task 4: frontend types + manifests carry `group`

**Files:**
- Modify: `web/src/modules/types.ts` (add `group` to `FrontendModule` and `ModuleInfo`)
- Modify: each frontend manifest `web/src/modules/<name>/index.ts` (add `group`)
- Test: `web/src/modules/__tests__/registry.groups.test.ts` (new; jest)

**Interfaces:**
- Produces: `FrontendModule.group` — consumed by Task 5's `navFor`.

- [ ] **Step 1: Write failing jest test** importing `MODULES` from `../registry` asserting every `FrontendModule` has a `group` in the valid set and that `issuesModule.group === 'workflow'`, `diagramsModule.group === 'kb'`.

- [ ] **Step 2: Run — expect FAIL** (`npm --prefix web test -- registry.groups`).

- [ ] **Step 3: Implement.**
  - `types.ts`: add `/** Group bucket: core|crm|workflow|kb (matches backend). */ group: string` to `FrontendModule`; add `group?: string` to `ModuleInfo`.
  - Add `group: '<key>'` to each of the 13 registered manifests' default export object (the registered ones: infrastructure, ipam, sbom, services_oncall, issues, discovery, secrets, webhooks_alerting, access_reviews, diagrams, documents, pages, streams) per the locked mapping. (`flows`/`helpdesk` have no frontend manifest — skip.)

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit** `feat(web): add group to FrontendModule + module manifests`.

---

### Task 5: grouped sidebar — bucket `navFor` output by group

**Files:**
- Modify: `web/src/modules/registry.ts` (`navFor`)
- Test: `web/src/modules/__tests__/navFor.groups.test.ts` (new; jest)

**Interfaces:**
- Consumes: `FrontendModule.group` (Task 4). `MenuCategory` has an optional `header` — a group header is emitted as a `MenuCategory` with `header` set and `items: []` acting as a section label, immediately before that group's categories.

- [ ] **Step 1: Write failing jest test:** for an enabled set spanning ≥2 groups, assert `navFor(enabled)` returns categories ordered Core→CRM→Workflow→KB, and that a header category (`header` matching the group label, `items.length === 0`) precedes each non-empty group's categories.

- [ ] **Step 2: Run — expect FAIL** (current `navFor` is flat, registry order).

- [ ] **Step 3: Implement.** Replace `navFor` body: iterate groups in fixed order `[['core','Core'],['crm','CRM'],['workflow','Workflow'],['kb','Knowledge Base']]`; for each, collect categories from enabled modules whose `group` matches (registry order within the group); if any, push a header category `{ header: <label>, key: 'group-'+key, items: [] }` then the collected categories. Keep `routesFor`/`adminNavFor`/`capabilitiesFor` unchanged.
```typescript
const GROUP_ORDER: [string, string][] = [
  ['core', 'Core'], ['crm', 'CRM'], ['workflow', 'Workflow'], ['kb', 'Knowledge Base'],
]
export function navFor(enabledModuleIds: Set<string>): MenuCategory[] {
  const out: MenuCategory[] = []
  for (const [key, label] of GROUP_ORDER) {
    const cats: MenuCategory[] = []
    for (const module of MODULES) {
      if (module.group === key && enabledModuleIds.has(module.id)) cats.push(...module.nav)
    }
    if (cats.length) {
      out.push({ header: label, key: `group-${key}`, items: [] })
      out.push(...cats)
    }
  }
  return out
}
```

- [ ] **Step 4: Run — expect PASS.** Also run the existing module/nav tests to ensure no regression.

- [ ] **Step 5: Commit** `feat(web): group sidebar nav under Core/CRM/Workflow/KB headers`.

---

### Task 6: relocate `identity_in_tenant` to `apps/api/common/identity.py` (remove cross-module dep + dedup)

**Files:**
- Create: `apps/api/common/identity.py`
- Modify: `apps/api/modules/issues/routes/issues.py:25`, `apps/api/modules/webhooks_alerting/routes/webhooks.py:12` (import from new home)
- Modify: `apps/api/modules/helpdesk/routes/{contacts.py:11,intake_forms.py:23,tickets.py:12}` (import from new home)
- Modify: `apps/api/modules/documents/common.py:16` (replace local dup with import + re-export; keep `visibility_users_in_tenant`/`slugify`)
- Delete: `apps/api/modules/helpdesk/common.py` (contained only `identity_in_tenant`)
- Test: `tests/unit/test_common_identity_in_tenant.py` (new)

**Interfaces:**
- Produces: `from apps.api.common.identity import identity_in_tenant`.

- [ ] **Step 1: Write failing test** — a two-tenant test: insert two identities in different tenants; assert `identity_in_tenant(db, id_a, tenant_a)` is True, `identity_in_tenant(db, id_a, tenant_b)` is False, `identity_in_tenant(db, None, tenant_a)` is True — importing from `apps.api.common.identity`. Also assert `apps.api.modules.helpdesk` is NOT imported by `apps.api.modules.issues.routes.issues` (grep-style: `import importlib, inspect; assert 'helpdesk' not in inspect.getsource(issues_module)` for the import line) — the anti-cross-module-dep regression.

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: apps.api.common.identity`).

- [ ] **Step 3: Create** `apps/api/common/identity.py` with the `identity_in_tenant` function verbatim (docstring generalized: "…request body — assignee, requester, team member, contact, document visibility, …").

- [ ] **Step 4: Repoint imports.** Change the 5 route-file imports (issues, webhooks_alerting, helpdesk contacts/intake_forms/tickets) from `apps.api.modules.helpdesk.common` → `apps.api.common.identity`. In `documents/common.py`, replace the local `def identity_in_tenant` with `from apps.api.common.identity import identity_in_tenant` (keep `visibility_users_in_tenant` calling it, and `slugify`, and the `sanitize_html` re-export). Delete `apps/api/modules/helpdesk/common.py`.

- [ ] **Step 5: Run — expect PASS.** Then run the broader affected suites to prove no import breakage:
```
... -m pytest tests/unit/test_api_issues.py tests/unit/test_api_webhooks.py tests/unit/test_notification_rules_tenant_isolation.py tests/unit/test_common_identity_in_tenant.py -q
```

- [ ] **Step 6: Commit** `refactor(common): relocate identity_in_tenant to apps/api/common (drop helpdesk cross-dep)`.

---

## Self-Review

- **Spec coverage:** grouping (T1/T4), group ENV toggles (T2), `/modules` group (T3), grouped nav (T5), helper relocation (T6) — all Phase 1 spec items covered.
- **Type consistency:** `group: str` (backend, required) / `group: string` (frontend) used consistently; group keys `core|crm|workflow|kb` fixed across all tasks.
- **No placeholders:** every task has concrete test code + exact edits.
- **Ordering:** T6 is independent of T1-5 and could run in parallel, but keep sequential for a clean review; T2 depends on T1; T3 depends on T1; T5 depends on T4.

## Out of Scope (later phases)
License-enforcement system (Phase 2), `/helpdesk` capability absorption + deletion (Phase 3+), CRM view composition, node/object counting.
