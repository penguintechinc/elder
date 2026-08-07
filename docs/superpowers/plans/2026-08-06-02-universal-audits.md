# Plan 02 — Universal audits: tenant scoping (P0) + village_id/metadata

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the P0 cross-tenant IDOR in `projects`/`milestones` (both entirely un-tenant-scoped today), and advance the two universal per-object rules — every object carries a unique `village_id` and a `metadata` JSON bag — for the tables this merge builds on (`identities`, `issue_comments`).

**Architecture:** Same stack/patterns as Plan 01 (merged): SQLAlchemy+Alembic for schema, penguin-dal at runtime, tenant from `g.claims["tenant"]`. `projects`/`milestones` get `tenant_id` + enforced scoping exactly like `issues` did (Plan 01 Task 1/2). `identities` and `issue_comments` gain the universal columns.

**Tech Stack:** Python 3.13, Quart, penguin-dal, Alembic, pytest; `elder-test:3.13` container against `elder-test-postgres`/`elder-test-redis` (55432/56379). Alembic head is currently **030**; new migrations start at **031**.

## Global Constraints

- Runtime DB = penguin-dal only (`current_app.db`); SQLAlchemy/Alembic for schema/migrations only. `python3`; type hints; `@dataclass(slots=True)` for data structures.
- Tenant from `g.claims["tenant"]`; use the shared helper `_tenant_id()` in `apps/api/modules/issues/routes/common.py` (from Plan 01). Every object tenant-scoped; sole exception = global/super-admin users above tenants.
- Every object gets a unique `village_id` (VillageIDMixin, `generate_village_id(tenant_id, redis_client)`) and a `metadata` JSON bag (map SQLAlchemy attr to DB column `metadata` like `Organization.org_metadata` → never a Python attr literally named `metadata`).
- Enum storage UPPERCASE; `.upper()` normalize on write. No Alembic auto-run on startup; `create_all()` builds test schema.
- **Verify each table's real current columns by reading the model before adding** — a `grep -A40 __tablename__` check is unreliable (columns can sit far from the tablename line).
- **Test recipe** (worktree root): `docker start elder-test-postgres elder-test-redis >/dev/null 2>&1; PW=$(docker inspect elder-test-postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2-); docker run --rm --network host -e DATABASE_URL="postgresql://elder_test:${PW}@localhost:55432/elder_test" -e REDIS_URL="redis://localhost:56379/0" -v "$(pwd)":/app -w /app elder-test:3.13 <testpath> -p no:warnings -q --no-header`. Auth: `generate_token(tenant_id=N, scopes=[...])` + `@patch("apps.api.auth.decorators.get_current_user")`.
- **Known pre-existing test flakiness:** some unrelated suites (documents/helpdesk_sla/dashboard) are order-dependent (fail in isolation, pass in full-run, identical on the release base) — not caused by this work; judge your tests, don't chase those.

---

## File Structure

- `apps/api/modules/issues/models/project.py` — `Project`, `Milestone` models: add `TenantScopedMixin` (or explicit `tenant_id`).
- `apps/api/modules/issues/models/issue.py` — `IssueComment` model: add `village_id`, `metadata`, `tenant_id`.
- `apps/api/models/identity.py` — `Identity`: add `metadata` JSON column.
- `alembic/versions/031_*` … — one migration per task (031 projects, 032 milestones, 033 identity metadata, 034 issue_comments cols), `down_revision` chaining from 030.
- `apps/api/modules/issues/routes/projects.py`, `milestones.py` — enforce tenant scoping on every `db.projects[id]`/`db.milestones[id]` lookup.
- `tests/unit/test_projects_milestones_tenant_isolation.py` — **new**.

---

## Task 1: Tenant-scope `projects` (P0 IDOR)

`projects` has no `tenant_id`; `projects.py` reads `db.projects[id]` unscoped → any tenant can read/update/delete another tenant's project.

**Files:** Modify `apps/api/modules/issues/models/project.py` (Project), create `alembic/versions/031_projects_tenant_id.py`, modify `apps/api/modules/issues/routes/projects.py`; test `tests/unit/test_projects_milestones_tenant_isolation.py`.

**Interfaces:** Produces `projects.tenant_id` (Integer FK tenants.id, indexed). Reuse `_tenant_id()` from `issues/routes/common.py`.

- [ ] **Step 1: Failing test** — tenant-1 token gets 404 reading a tenant-2 project.

```python
# tests/unit/test_projects_milestones_tenant_isolation.py
import json, pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from quart import current_app

class TestProjectsTenantIsolation:
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_project_rejects_other_tenant(self, mock_get_user, async_client, generate_token, app):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db; now = datetime.now(timezone.utc)
            pid = db.projects.insert(name="T2 proj", tenant_id=2, created_at=now, updated_at=now)
            db.commit()
        resp = await async_client.get(f"/api/v1/projects/{pid}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404
```

- [ ] **Step 2: Run it, verify it fails** (currently returns 200 with the other tenant's project). If `db.projects.insert(tenant_id=...)` errors that the column is unknown, that itself proves the column is missing — add it (Step 3) then this insert works.

- [ ] **Step 3: Add `tenant_id` to the model + migration.** In `project.py`, add `TenantScopedMixin` to `Project`'s bases (confirm the mixin exists in `apps/api/models/base.py`; it adds `tenant_id` Integer FK tenants.id NOT NULL) OR add an explicit nullable `tenant_id` column mirroring `issues` (Plan 01 migration 026). Migration `031`: add column + FK + index; **backfill** `projects.tenant_id` from the linking org where derivable (projects link to issues/orgs — if no direct org link, backfill via any linked issue's tenant, else default 1); `down_revision="030"`.

- [ ] **Step 4: Enforce scoping in `projects.py`.** Replace every `db.projects[id]` / unscoped `db(db.projects.id==id)` (lines ~164/191/245/264/299/303) with `db((db.projects.id==id) & (db.projects.tenant_id==_tenant_id())).select().first()`, 404 if missing, 403 if `_tenant_id()` is None. Set `tenant_id=_tenant_id()` on create. List endpoint filters by tenant.

- [ ] **Step 5: Run it, verify it passes.** Add positive test: same-tenant project still 200.

- [ ] **Step 6: Commit** — `git commit -m "fix(projects): add tenant_id + enforce tenant scoping (close IDOR)"`

---

## Task 2: Tenant-scope `milestones` (P0 IDOR, incl. `/<id>/issues`)

`milestones` has no `tenant_id`; `milestones.py` reads `db.milestones[id]` unscoped, and `GET /milestones/<id>/issues` returns any tenant's issues.

**Files:** Modify `project.py` (Milestone), create `alembic/versions/032_milestones_tenant_id.py`, modify `apps/api/modules/issues/routes/milestones.py`; extend the test file.

**Interfaces:** Produces `milestones.tenant_id`.

- [ ] **Step 1: Failing tests** — tenant-1 token: (a) 404 on a tenant-2 milestone GET; (b) `GET /milestones/<t2-id>/issues` does NOT return tenant-2 issues.

```python
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_milestone_issues_excludes_other_tenant(self, mock_get_user, async_client, generate_token, app):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=False)
        token = generate_token(tenant_id=1, scopes=["issues:read"])
        async with app.app_context():
            db = current_app.db; now = datetime.now(timezone.utc)
            mid = db.milestones.insert(name="T2 ms", tenant_id=2, created_at=now, updated_at=now)
            db.commit()
        resp = await async_client.get(f"/api/v1/milestones/{mid}/issues", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Add `tenant_id` to `Milestone` + migration `032`** (`down_revision="031"`), backfill + FK + index, same approach as Task 1.

- [ ] **Step 4: Enforce scoping in `milestones.py`** — every `db.milestones[id]` lookup (lines ~171/198/256 and the `get_milestone_issues` handler ~338-378) resolves tenant-scoped; `get_milestone_issues` must 404 on a foreign/missing milestone AND additionally filter the returned issues by `db.issues.tenant_id==_tenant_id()`. Set `tenant_id` on create.

- [ ] **Step 5: Run it, verify it passes.** Positive test: same-tenant milestone + its issues return 200 and only this tenant's issues.

- [ ] **Step 6: Commit** — `fix(milestones): add tenant_id + scope reads incl /<id>/issues (close IDOR)`

---

## Task 3: Add `metadata` to `identities`

`identities` has `village_id`+`tenant_id` but no `metadata` bag. Plan 03 (CRM) needs it for customer-contact optional details (phone, location, etc.).

**Files:** Modify `apps/api/models/identity.py`; create `alembic/versions/033_identity_metadata.py`; test `tests/unit/test_identity_metadata.py` (new).

**Interfaces:** Produces `identities.identity_metadata = Column("metadata", JSON, nullable=True)`.

- [ ] **Step 1: Failing test** — assert `"metadata" in db.identities.table.columns` (penguin-dal `TableProxy.table` → SQLAlchemy Table; membership by column name). (Recall from Plan 01: `db.<t>.fields` does NOT exist on penguin_dal's TableProxy — use `.table.columns`.)

```python
    async def test_identities_has_metadata(self, app):
        from quart import current_app
        async with app.app_context():
            assert "metadata" in current_app.db.identities.table.columns
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Add the column** — in `identity.py` `Identity`: `identity_metadata = Column("metadata", JSON, nullable=True)` (import `JSON`; do NOT name the Python attr `metadata`). Migration `033` (`down_revision="032"`): `op.add_column("identities", sa.Column("metadata", sa.JSON(), nullable=True))`, guarded by column-existence.

- [ ] **Step 4: Run it, verify it passes.**

- [ ] **Step 5: Commit** — `feat(identities): add metadata JSON bag (universal metadata rule)`

---

## Task 4: Add `village_id` + `metadata` + `tenant_id` to `issue_comments`

`issue_comments` lacks all three (columns: id, issue_id, author_id, content, timestamps). Per the universal rules + tenant scoping.

**Files:** Modify `apps/api/modules/issues/models/issue.py` (`IssueComment`); create `alembic/versions/034_issue_comments_universal_cols.py`; modify `apps/api/modules/issues/routes/comments.py` (+ the duplicate comment-create in `issues.py`) to set `village_id`/`tenant_id` on create; test in `tests/unit/test_issues_tenant_isolation.py`.

**Interfaces:** Produces on `issue_comments`: `village_id` (via VillageIDMixin or explicit), `comment_metadata = Column("metadata", JSON)`, `tenant_id` (Integer FK tenants.id).

- [ ] **Step 1: Failing test** — a created comment carries a `village_id` and the row's `tenant_id` matches the parent issue's tenant.

```python
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_comment_gets_village_id_and_tenant(self, mock_get_user, async_client, generate_token, app):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["issues:write"])
        async with app.app_context():
            db = current_app.db; now = datetime.now(timezone.utc)
            iid = db.issues.insert(title="I", status="OPEN", priority="LOW", issue_type="OTHER",
                                   is_incident=0, tenant_id=1, resource_type="organization", resource_id=1,
                                   created_at=now, updated_at=now); db.commit()
        resp = await async_client.post(f"/api/v1/issues/{iid}/comments", json={"content":"hi"},
                                       headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (200, 201)
        cid = json.loads(await resp.get_data())["id"]
        async with app.app_context():
            row = current_app.db.issue_comments[cid]
            assert row.village_id and row.tenant_id == 1
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Add columns + migration + create-path wiring.** Model: add `village_id`, `comment_metadata = Column("metadata", JSON)`, `tenant_id` FK. Migration `034` (`down_revision="033"`) adds the three + backfills `tenant_id` from the parent issue and generates `village_id` for existing rows (via `generate_village_id`, or leave null + let a follow-up backfill if redis-in-migration is impractical — prefer generating). In the comment-create handler(s), set `tenant_id` from the parent issue and `village_id = generate_village_id(tenant_id, current_app.redis...)` (follow the ticket-create pattern in `helpdesk/routes/tickets.py`).

- [ ] **Step 4: Run it, verify it passes.**

- [ ] **Step 5: Commit** — `feat(issue_comments): add village_id + metadata + tenant_id (universal rules)`

---

## Task 5: Verify — full suite + clean Alembic replay

- [ ] **Step 1:** Full unit suite on final HEAD (fresh DB reset first to avoid the known order-dependent flakiness): report pass/skip/fail. The new tenant-isolation + metadata tests must pass; unrelated order-dependent failures (documents/helpdesk_sla/dashboard) are pre-existing — confirm they match the release base rather than chasing them.
- [ ] **Step 2:** `alembic upgrade head` on a fresh DB (001→034), `downgrade -1`/`upgrade head` round-trip clean; migrations 031–034 present.
- [ ] **Step 3:** Confirm `projects`/`milestones`/`identities`/`issue_comments` have the new columns after replay; `grep -n "db.projects\[\|db.milestones\[" apps/api/modules/issues/routes/*.py` shows every remaining lookup tenant-scoped.
- [ ] **Step 4:** (No PR here — the finishing step opens it after the whole-branch review.)

## Self-review notes (author)

- Closes the Plan-01-discovered P0 (projects + milestones un-tenant-scoped, incl. milestone `/<id>/issues`).
- Advances universal `village_id`/`metadata` for the surviving core tables the merge builds on (`identities` for CRM, `issue_comments`). The soon-to-be-retired `hd_ticket_messages`/`attachments`/`forms` are intentionally NOT modified here — they converge into `issue_comments`/new `issue_attachments`/intake-forms (Plans 03-04, created WITH the columns); `webhooks` gains its `village_id`/`metadata` in Plan 05 when it's extended. A full repo-wide village_id/metadata sweep of every remaining table is tracked as ongoing hygiene, not blocking this merge.
- Migrations 031–034 chain from Plan 01's 030.
