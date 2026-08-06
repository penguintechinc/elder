# Plan 01 — Issues Foundation (tenant scoping + unified schema)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `issues` into the single unified work-item table — tenant-scoped, with the support-ticket fields, a polymorphic identity/OU assignee, universal `metadata`, and the reconciled column names — so all later plans (CRM, intake forms, webhooks, workers, UI) build on one solid model.

**Architecture:** SQLAlchemy models + Alembic define the physical schema; **penguin-dal (PyDAL) via `current_app.db` does all runtime queries** (never SQLAlchemy at runtime). This plan adds columns + enum values via one Alembic migration, then enforces tenant scoping in the PyDAL route handlers. `issues` is not tenant-scoped today (a cross-tenant read leak) — fixing that is the highest-value change here.

**Tech Stack:** Python 3.13, Quart, penguin-dal (PyDAL over SQLAlchemy), Alembic, pytest. Tests run in the `elder-test:3.13` container against `elder-test-postgres`/`elder-test-redis` (host ports 55432/56379).

## Global Constraints

- **Runtime DB = penguin-dal only** (`current_app.db`); SQLAlchemy + Alembic for schema/migrations only — never runtime queries.
- **`python3` explicitly**, never bare `python`. Type hints on every function; `@dataclass(slots=True)` for data structures.
- **Tenant scoping is universal**: every query filters by the token's tenant (`g.claims["tenant"]`), except global/super-admin users above tenants.
- **Every object carries a unique `village_id` and a `metadata` JSON bag** (issues already have `village_id`; add `metadata`).
- **Enum storage is UPPERCASE member names** in the DB (e.g. `OPEN`); create/update normalize input via `.upper()` (the update path was fixed in #232 — keep it consistent).
- **No Alembic auto-run on app startup** — migrations run via `alembic upgrade head` (manual/K8s Job); `create_all()` in tests is fine.
- **Test recipe** (run from the worktree root):
  ```bash
  docker start elder-test-postgres elder-test-redis
  PW=$(docker inspect elder-test-postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2-)
  docker run --rm --network host \
    -e DATABASE_URL="postgresql://elder_test:${PW}@localhost:55432/elder_test" \
    -e REDIS_URL="redis://localhost:56379/0" \
    -v "$(pwd)":/app -w /app elder-test:3.13 <testpath> -p no:warnings -q --no-header
  ```
- **Auth in tests:** use the `generate_token(tenant_id, scopes=[...])` fixture + `@patch("apps.api.auth.decorators.get_current_user")` (see `tests/unit/test_api_issues.py`).

---

## File Structure

- `apps/api/modules/issues/models/issue.py` — SQLAlchemy `Issue` model + `IssueStatus`/`IssuePriority`/`IssueType` enums. Add support columns, `metadata`, `parent_issue_id`, polymorphic assignee; add `support`/`urgent` enum values.
- `alembic/versions/0NN_issues_unified_foundation.py` — one migration: add columns + backfill `tenant_id` from org.
- `apps/api/modules/issues/routes/issues.py` — PyDAL handlers `list_issues`/`get_issue`/`update_issue`/`create_issue`/`delete_issue`. Add tenant filtering + new fields.
- `apps/api/modules/issues/routes/comments.py`, `labels.py` — add tenant filtering.
- `apps/api/models/dataclasses.py` — `IssueDTO` gains the new fields.
- `apps/api/models/pydantic/issue.py` — `CreateIssueRequest`/`UpdateIssueRequest` gain the new fields.
- `tests/unit/test_api_issues.py` — extend (tenant scoping, support fields, assignee, metadata).
- `tests/unit/test_issues_tenant_isolation.py` — **new**, the tenant-leak regression suite.

---

## Task 1: Tenant scoping on `issues` (schema + backfill)

Add `tenant_id` to `issues` so rows can be scoped. `issues` currently has none; tenant is derived transiently from the org at write time only.

**Files:**
- Modify: `apps/api/modules/issues/models/issue.py` (add `tenant_id` column)
- Create: `alembic/versions/0NN_issues_unified_foundation.py`
- Test: `tests/unit/test_issues_tenant_isolation.py`

**Interfaces:**
- Produces: `issues.tenant_id` (Integer, FK `tenants.id`, indexed, nullable during backfill then enforced).

- [ ] **Step 1: Write the failing test** — `issues` table has a `tenant_id` column.

```python
# tests/unit/test_issues_tenant_isolation.py
import pytest
from quart import current_app

class TestIssuesTenantColumn:
    @pytest.mark.asyncio
    async def test_issues_table_has_tenant_id(self, app):
        async with app.app_context():
            db = current_app.db
            assert "tenant_id" in db.issues.fields, "issues must have a tenant_id column"
```

- [ ] **Step 2: Run it, verify it fails**

Run the test recipe on `tests/unit/test_issues_tenant_isolation.py::TestIssuesTenantColumn::test_issues_table_has_tenant_id`
Expected: FAIL — `tenant_id` not in `db.issues.fields`.

- [ ] **Step 3: Add the column to the model**

In `apps/api/modules/issues/models/issue.py`, in `class Issue`, add after `village_id`/before `resource_type`:

```python
    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,   # nullable for backfill; enforced NOT NULL in a follow-up once populated
        index=True,
    )
```

(Ensure `ForeignKey` is imported.)

- [ ] **Step 4: Run it, verify it passes** (tests build schema via `create_all()`).

Expected: PASS.

- [ ] **Step 5: Write the Alembic migration**

Create `alembic/versions/0NN_issues_unified_foundation.py` (set `down_revision` to the current head — find via `alembic heads`):

```python
"""issues unified foundation: tenant_id + support fields + metadata

Revision ID: 0NN_issues_unified_foundation
"""
from alembic import op
import sqlalchemy as sa

revision = "0NN_issues_unified_foundation"
down_revision = "<CURRENT_HEAD>"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("issues", sa.Column("tenant_id", sa.Integer(), nullable=True, index=True))
    op.create_foreign_key("fk_issues_tenant", "issues", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    # Backfill tenant_id from the owning organization (issues.resource_id when resource_type='organization')
    op.execute("""
        UPDATE issues i SET tenant_id = o.tenant_id
        FROM organizations o
        WHERE i.resource_type = 'organization' AND i.resource_id = o.id AND i.tenant_id IS NULL
    """)
    # Any remaining unmatched → tenant 1 (single-tenant default) so nothing is orphaned
    op.execute("UPDATE issues SET tenant_id = 1 WHERE tenant_id IS NULL")

def downgrade():
    op.drop_constraint("fk_issues_tenant", "issues", type_="foreignkey")
    op.drop_column("issues", "tenant_id")
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/modules/issues/models/issue.py alembic/versions/0NN_issues_unified_foundation.py tests/unit/test_issues_tenant_isolation.py
git commit -m "feat(issues): add tenant_id column + backfill migration"
```

---

## Task 2: Enforce tenant filtering in issue reads (close the leak)

`list_issues` filters `db.issues.id > 0` with the org filter commented out → returns issues across all tenants. Scope every read to `g.claims["tenant"]`.

**Files:**
- Modify: `apps/api/modules/issues/routes/issues.py` (`list_issues` ~105, `get_issue` ~271, `update_issue` ~302, `delete_issue` ~393)
- Test: `tests/unit/test_issues_tenant_isolation.py`

**Interfaces:**
- Consumes: `issues.tenant_id` (Task 1).
- Produces: a module-level helper `_tenant_id() -> int | None` reading `g.claims["tenant"]` (mirror helpdesk `tickets.py:_get_tenant_id`).

- [ ] **Step 1: Write the failing test** — a token for tenant 1 must not see tenant 2's issue.

```python
# tests/unit/test_issues_tenant_isolation.py  (add to the file)
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

class TestIssuesTenantIsolation:
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_excludes_other_tenant(self, mock_get_user, async_client, generate_token, app):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            db.issues.insert(title="T1 issue", status="OPEN", priority="MEDIUM",
                             issue_type="OTHER", is_incident=0, tenant_id=1,
                             created_at=now, updated_at=now)
            db.issues.insert(title="T2 issue", status="OPEN", priority="MEDIUM",
                             issue_type="OTHER", is_incident=0, tenant_id=2,
                             created_at=now, updated_at=now)
            db.commit()
        resp = await async_client.get("/api/v1/issues?per_page=100",
                                      headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        titles = [i["title"] for i in json.loads(await resp.get_data())["items"]]
        assert "T1 issue" in titles
        assert "T2 issue" not in titles, "tenant 1 must not see tenant 2 issues"
```

- [ ] **Step 2: Run it, verify it fails** — currently returns both (leak).

Expected: FAIL — `"T2 issue"` present.

- [ ] **Step 3: Add the tenant helper + filter reads**

In `apps/api/modules/issues/routes/issues.py`, add near the top (after imports):

```python
def _tenant_id() -> int | None:
    """Tenant id from validated JWT claims (populated by before_request)."""
    claims = getattr(g, "claims", {}) or {}
    raw = claims.get("tenant", "")
    if not raw:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None
```

In `list_issues`, replace the base query `query = db.issues.id > 0` (and the commented-out org filter) with:

```python
    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403
    query = db.issues.tenant_id == tenant_id
```

In `get_issue`, `update_issue`, `delete_issue`, change each single-row lookup from `db.issues[id]` / `db.issues.id == id` to also require tenant, e.g.:

```python
    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403
    row = db((db.issues.id == id) & (db.issues.tenant_id == tenant_id)).select().first()
    if not row:
        return jsonify({"error": "Issue not found"}), 404
```

- [ ] **Step 4: Run it, verify it passes**

Expected: PASS. Also run the full `tests/unit/test_api_issues.py` — the existing tests insert with `tenant_id=1` via seeded fixtures; if any insert lacks `tenant_id`, add `tenant_id=1` to those inserts.

- [ ] **Step 5: Set `tenant_id` on create**

In `create_issue`, add `tenant_id=_tenant_id()` to the `db.issues.insert(...)` call (it currently derives the org's tenant only to validate). Add a test that a created issue carries the caller's tenant.

- [ ] **Step 6: Commit**

```bash
git add apps/api/modules/issues/routes/issues.py tests/unit/test_issues_tenant_isolation.py
git commit -m "fix(issues): enforce tenant scoping on all reads/writes (close cross-tenant leak)"
```

---

## Task 3: Add `support` to `IssueType` and `urgent` to `IssuePriority`

**Files:**
- Modify: `apps/api/modules/issues/models/issue.py` (enums)
- Modify: `apps/api/models/pydantic/issue.py` (if status/priority/type are `Literal`s, add the values)
- Test: `tests/unit/test_api_issues.py`

**Interfaces:**
- Produces: `IssueType.SUPPORT = "support"`, `IssuePriority.URGENT = "urgent"`.

- [ ] **Step 1: Write the failing test** — create an issue with `issue_type="support"`, `priority="urgent"`.

```python
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_support_issue_urgent(self, mock_get_user, async_client, generate_token, app):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(name="Org", tenant_id=1, created_at=now, updated_at=now)
            db.commit()
        resp = await async_client.post("/api/v1/issues",
            json={"title": "Cannot log in", "issue_type": "support", "priority": "urgent",
                  "organization_id": org_id},
            headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
        data = json.loads(await resp.get_data())
        assert data["issue_type"] == "SUPPORT"
        assert data["priority"] == "URGENT"
```

- [ ] **Step 2: Run it, verify it fails** — `support`/`urgent` rejected (422) or enum error.

- [ ] **Step 3: Add the enum values**

In `issue.py`:

```python
class IssueType(enum.Enum):
    # ...existing...
    SUPPORT = "support"

class IssuePriority(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"
```

In `apps/api/models/pydantic/issue.py`, if `issue_type`/`priority` use `Literal[...]`, add `"support"`/`"urgent"`.

- [ ] **Step 4: Run it, verify it passes.**

- [ ] **Step 5: Commit**

```bash
git add apps/api/modules/issues/models/issue.py apps/api/models/pydantic/issue.py tests/unit/test_api_issues.py
git commit -m "feat(issues): add support issue_type + urgent priority"
```

---

## Task 4: Polymorphic assignee (`assignee_type` + `assignee_id`)

Support assigning an issue to an **identity or an org unit** (exactly one). Keep the existing `assignee_id` semantics but add `assignee_type` to disambiguate the target table.

**Files:**
- Modify: `apps/api/modules/issues/models/issue.py`, the migration (Task 1 file), `routes/issues.py`, `dataclasses.py` (`IssueDTO`), `pydantic/issue.py`
- Test: `tests/unit/test_api_issues.py`

**Interfaces:**
- Produces: `issues.assignee_type` (String(16), nullable, values `identity`|`org_unit`); `issues.assignee_id` already exists (int) — reuse it as the polymorphic id. When `assignee_type='org_unit'`, `assignee_id` → `organizations.id`; when `identity`, → `identities.id`.

- [ ] **Step 1: Write the failing test** — assign an issue to an org unit.

```python
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_assign_issue_to_org_unit(self, mock_get_user, async_client, generate_token, app):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            ou_id = db.organizations.insert(name="Support Team", type="team", tenant_id=1,
                                            created_at=now, updated_at=now)
            iid = db.issues.insert(title="Assign me", status="OPEN", priority="MEDIUM",
                                   issue_type="SUPPORT", is_incident=0, tenant_id=1,
                                   created_at=now, updated_at=now)
            db.commit()
        resp = await async_client.patch(f"/api/v1/issues/{iid}",
            json={"assignee_type": "org_unit", "assignee_id": ou_id},
            headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        data = json.loads(await resp.get_data())
        assert data["assignee_type"] == "org_unit"
        assert data["assignee_id"] == ou_id
```

- [ ] **Step 2: Run it, verify it fails** — `assignee_type` unknown/not persisted.

- [ ] **Step 3: Add the column + migration + wire through**

Model (`issue.py`): `assignee_type = Column(String(16), nullable=True, index=True)`.
Migration: `op.add_column("issues", sa.Column("assignee_type", sa.String(16), nullable=True))`.
`UpdateIssueRequest`/`CreateIssueRequest` (`pydantic/issue.py`): add `assignee_type: Optional[Literal["identity","org_unit"]] = None`.
`update_issue`/`create_issue` (`routes/issues.py`): when `assignee_type` present, set both `assignee_type` and `assignee_id`; validate the target exists **in tenant** (`identity_in_tenant` for identity; an org-in-tenant check for org_unit). Default `assignee_type='identity'` when only `assignee_id` given (back-compat).
`IssueDTO` (`dataclasses.py`): add `assignee_type: Optional[str]`.

- [ ] **Step 4: Run it, verify it passes.** Add a second test: assign to an identity → `assignee_type == "identity"`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/modules/issues/ alembic/versions/0NN_issues_unified_foundation.py apps/api/models/
git commit -m "feat(issues): polymorphic assignee (identity or org unit)"
```

---

## Task 5: Support fields + `metadata` + `parent_issue_id`

Add the nullable support columns, the universal `metadata` bag, and the sub-task self-FK.

**Files:**
- Modify: `issue.py`, the migration, `routes/issues.py`, `dataclasses.py`, `pydantic/issue.py`
- Test: `tests/unit/test_api_issues.py`

**Interfaces:**
- Produces on `issues`: `channel` (String(20)), `category` (String(100)), `requester_contact_id` (Integer FK identities — set in Plan 03 CRM), `hd_sla_policy_id` (Integer), `sla_breach_at`/`first_response_at`/`resolved_at` (DateTime tz), `issue_metadata` (JSON, mapped to column name `metadata`), `parent_issue_id` (Integer self-FK).

- [ ] **Step 1: Write the failing test** — create a support issue with `channel` + `category` + `metadata`, read them back.

```python
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_support_fields_and_metadata(self, mock_get_user, async_client, generate_token, app):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(name="Org", tenant_id=1, created_at=now, updated_at=now)
            db.commit()
        resp = await async_client.post("/api/v1/issues",
            json={"title": "Email in", "issue_type": "support", "priority": "high",
                  "organization_id": org_id, "channel": "email", "category": "billing",
                  "metadata": {"source_email": "cust@example.com"}},
            headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
        data = json.loads(await resp.get_data())
        assert data["channel"] == "email"
        assert data["category"] == "billing"
        assert data["metadata"]["source_email"] == "cust@example.com"
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Add columns + migration + wire through**

Model — note the reserved attribute name: map the JSON column like organizations do:

```python
    channel = Column(String(20), nullable=True)
    category = Column(String(100), nullable=True)
    requester_contact_id = Column(Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True)
    hd_sla_policy_id = Column(Integer, nullable=True)
    sla_breach_at = Column(DateTime(timezone=True), nullable=True)
    first_response_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    issue_metadata = Column("metadata", JSON, nullable=True)
    parent_issue_id = Column(Integer, ForeignKey("issues.id", ondelete="SET NULL"), nullable=True)
```

Migration: `op.add_column(...)` for each. `Create/UpdateIssueRequest`: add `channel`, `category`, `metadata: Optional[dict]`, `parent_issue_id`. Route create/update: persist them (`metadata` → the `metadata` DAL field). `IssueDTO`: add the fields (expose `issue_metadata` as `metadata`).

- [ ] **Step 4: Run it, verify it passes.**

- [ ] **Step 5: Commit**

```bash
git add apps/api/modules/issues/ alembic/ apps/api/models/ tests/unit/test_api_issues.py
git commit -m "feat(issues): support fields + universal metadata + parent self-FK"
```

---

## Task 6: Reconcile `created_by_id`/`assigned_to_id` vs `reporter_id`/`assignee_id`

Alembic 001 created `issues` with `created_by_id`/`assigned_to_id`; the model/routes use `reporter_id`/`assignee_id`, and no rename migration exists — the physical columns and the ORM names may disagree.

**Files:**
- Modify: the migration (add a rename if the old columns exist); `issue.py`
- Test: `tests/unit/test_api_issues.py` (a create→read that asserts `reporter_id` round-trips)

- [ ] **Step 1: Write the failing test** — create an issue, GET it, assert `reporter_id` equals the caller.

```python
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_reporter_id_roundtrips(self, mock_get_user, async_client, generate_token, app):
        mock_get_user.return_value = MagicMock(id=7, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            org_id = db.organizations.insert(name="Org", tenant_id=1, created_at=now, updated_at=now)
            db.commit()
        resp = await async_client.post("/api/v1/issues",
            json={"title": "R", "priority": "low", "organization_id": org_id},
            headers={"Authorization": f"Bearer {token}"})
        data = json.loads(await resp.get_data())
        assert data["reporter_id"] == 7
```

- [ ] **Step 2: Run it, verify it fails or errors** (column mismatch surfaces as a 500 or a null).

- [ ] **Step 3: Add a guarded rename to the migration**

```python
    # Reconcile legacy column names if the 001 names are still present
    conn = op.get_bind()
    cols = [c["name"] for c in sa.inspect(conn).get_columns("issues")]
    if "created_by_id" in cols and "reporter_id" not in cols:
        op.alter_column("issues", "created_by_id", new_column_name="reporter_id")
    if "assigned_to_id" in cols and "assignee_id" not in cols:
        op.alter_column("issues", "assigned_to_id", new_column_name="assignee_id")
```

- [ ] **Step 4: Run it, verify it passes.**

- [ ] **Step 5: Commit**

```bash
git add alembic/ tests/unit/test_api_issues.py
git commit -m "fix(issues): reconcile created_by_id/assigned_to_id -> reporter_id/assignee_id"
```

---

## Task 7: Tenant-scope comments + labels reads

`comments.py` and `labels.py` list/read without tenant scoping (via the issue). Scope them through the parent issue's tenant.

**Files:**
- Modify: `apps/api/modules/issues/routes/comments.py`, `labels.py`
- Test: `tests/unit/test_issues_tenant_isolation.py`

- [ ] **Step 1: Write the failing test** — a tenant-1 token cannot read comments on a tenant-2 issue (404).

```python
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_comments_scoped_by_issue_tenant(self, mock_get_user, async_client, generate_token, app):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            iid = db.issues.insert(title="T2", status="OPEN", priority="LOW", issue_type="OTHER",
                                   is_incident=0, tenant_id=2, created_at=now, updated_at=now)
            db.commit()
        resp = await async_client.get(f"/api/v1/issues/{iid}/comments",
                                      headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404
```

- [ ] **Step 2: Run it, verify it fails** (returns 200 with the other tenant's comments).

- [ ] **Step 3: Gate the issue lookup by tenant** in `comments.py`/`labels.py` — before listing, resolve the issue with `(db.issues.id == id) & (db.issues.tenant_id == _tenant_id())`; 404 if missing. Import/duplicate the `_tenant_id()` helper (or move it to a shared `common.py` in the module and import from there).

- [ ] **Step 4: Run it, verify it passes.**

- [ ] **Step 5: Commit**

```bash
git add apps/api/modules/issues/routes/comments.py apps/api/modules/issues/routes/labels.py tests/unit/test_issues_tenant_isolation.py
git commit -m "fix(issues): tenant-scope comments + labels via parent issue"
```

---

## Task 8: Run the full issues suite + verify migration applies cleanly

- [ ] **Step 1:** Run the whole issues test set:

```bash
# from worktree root, using the recipe env
docker run ... elder-test:3.13 tests/unit/test_api_issues.py tests/unit/test_issues_tenant_isolation.py tests/unit/test_scope_enforcement.py -p no:warnings -q --no-header
```
Expected: all PASS.

- [ ] **Step 2:** Verify the migration applies on a clean DB:

```bash
docker run ... elder-test:3.13 alembic upgrade head
```
Expected: no error; `issues` has `tenant_id`, `assignee_type`, `channel`, `category`, `metadata`, `parent_issue_id`, SLA columns.

- [ ] **Step 3:** Verify no other suite regressed (run `tests/unit` broadly against a freshly reset `elder_test` — reset per the pollution note before judging failures).

- [ ] **Step 4: Open the PR** into `release/v4.0.X` (assignee = overseer), let CI go green (once Actions recovers), merge.

---

## Self-review notes (author)

- Covers spec §2 tenant-leak, §3 (support/urgent, polymorphic assignee, universal metadata), §4.2 fields, §6-defects (created_by/reporter, parent self-FK). SLA *await* bug and email/auto-close are Plan 06; `requester_contact_id` is populated in Plan 03 (CRM) — the column lands here, the data wiring there.
- `village_id` on issues already exists — not re-added. `metadata` mapped as `Column("metadata", JSON)` to avoid the SQLAlchemy reserved `metadata` attribute (same trick as `organizations.org_metadata`).
- Enum case: DB stores UPPERCASE; create/update `.upper()` (per #232). Tests assert UPPERCASE returns.
