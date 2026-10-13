# Plan 03 — CRM entities (customer_contact identity type + customer_company org type)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Represent Ruffled's CRM natively — a customer contact is an **identity** of new type `customer_contact` (its optional details live in the `identities.metadata` bag added in Plan 02), and a customer company is an **organization** of new type `customer_company` (kept distinct from internal org units). This is the type foundation; CRM management UI is a later plan, and the legacy `hd_contacts`/`hd_companies` *data* migration is Phase B.

**Architecture:** `identities.identity_type` is a **Postgres ENUM** (`identitytype`) declared `Enum(IdentityType, values_callable=...)` storing the lowercase `.value`, so a new member needs both the Python enum value AND an `ALTER TYPE ... ADD VALUE` migration. `organizations.type` is a plain `String(64)` column validated only in app code (a pydantic `Literal` + marshmallow `OneOf`), so `customer_company` needs no migration — just the validation lists.

**Tech Stack:** Python 3.13, Quart, penguin-dal, Alembic, pytest; `elder-test:3.13` against `elder-test-postgres`/`elder-test-redis` (55432/56379). Alembic head is **034**; new migration is **035**.

## Global Constraints

- Runtime DB = penguin-dal only; SQLAlchemy/Alembic for schema/migrations only. `python3`; type hints.
- Postgres ENUM add-value: use `op.get_context().autocommit_block()` + `ALTER TYPE identitytype ADD VALUE IF NOT EXISTS 'customer_contact'` (non-transactional; matches the pattern in `alembic/versions/027_add_support_issue_type_urgent_priority.py`). The value is **lowercase** `customer_contact` (identitytype stores `.value` via `values_callable`).
- `create_all()` builds the test schema (enum created fresh with all members), so unit tests pass on the model change alone; the migration is for real deploys — author it and dry-run `alembic upgrade head`.
- **Verify each file before editing** (read the model/schema; grep-based column checks are unreliable). Reset the test DB (`DROP/CREATE elder_test` + `redis-cli FLUSHALL`) before judging a suite run — pre-existing order-dependent flakiness otherwise.
- **Test recipe** (worktree root): `docker start elder-test-postgres elder-test-redis >/dev/null 2>&1; PW=$(docker inspect elder-test-postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2-); docker run --rm --network host -e DATABASE_URL="postgresql://elder_test:${PW}@localhost:55432/elder_test" -e REDIS_URL="redis://localhost:56379/0" -v "$(pwd)":/app -w /app elder-test:3.13 <testpath> -p no:warnings -q --no-header`. Auth: `generate_token(tenant_id=1, scopes=[...])` + `@patch("apps.api.auth.decorators.get_current_user")`.

---

## File Structure

- `apps/api/models/identity.py` — `IdentityType` enum: add `CUSTOMER_CONTACT`.
- `alembic/versions/035_identity_type_customer_contact.py` — ALTER TYPE add value.
- `apps/api/models/pydantic/organization.py` — `OrganizationType` Literal: add `customer_company`.
- `apps/api/schemas/organization.py` — two `OneOf` lists: add `customer_company`.
- `tests/unit/test_crm_entity_types.py` — **new**.

---

## Task 1: `customer_contact` identity type

**Files:** `apps/api/models/identity.py`; `alembic/versions/035_identity_type_customer_contact.py`; `tests/unit/test_crm_entity_types.py`.

**Interfaces:** Produces `IdentityType.CUSTOMER_CONTACT = "customer_contact"`.

- [ ] **Step 1: Failing test** — create an identity with `identity_type="customer_contact"` and read it back.

```python
# tests/unit/test_crm_entity_types.py
import pytest
from datetime import datetime, timezone
from quart import current_app

class TestCrmEntityTypes:
    @pytest.mark.asyncio
    async def test_create_customer_contact_identity(self, app):
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            iid = db.identities.insert(
                username="cust@example.com", email="cust@example.com",
                identity_type="customer_contact", tenant_id=1,
                metadata={"phone": "+1-555-0100"},
                created_at=now, updated_at=now,
            )
            db.commit()
            row = db.identities[iid]
            assert row.identity_type == "customer_contact"
            assert row.metadata["phone"] == "+1-555-0100"
```

- [ ] **Step 2: Run it, verify it fails** — `invalid input value for enum identitytype: "customer_contact"` (member not in the Postgres enum built by `create_all()` from the current Python enum).

- [ ] **Step 3: Add the enum member + migration.** In `identity.py` `IdentityType`, add `CUSTOMER_CONTACT = "customer_contact"`. Create `035_*` (`down_revision="034"`):

```python
def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE identitytype ADD VALUE IF NOT EXISTS 'customer_contact'")

def downgrade():
    pass  # Postgres cannot drop an enum value; no-op (document why)
```

- [ ] **Step 4: Run it, verify it passes.**

- [ ] **Step 5: Commit** — `feat(identities): add customer_contact identity type (CRM)`

---

## Task 2: `customer_company` organization type

`organizations.type` is a `String(64)`; only app-level validation restricts it. Add `customer_company` to the pydantic `Literal` and both marshmallow `OneOf` lists. No migration.

**Files:** `apps/api/models/pydantic/organization.py`; `apps/api/schemas/organization.py`; `tests/unit/test_crm_entity_types.py`.

- [ ] **Step 1: Failing test** — POST an organization with `organization_type="customer_company"` returns success (not a validation 400).

```python
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_customer_company_org(self, mock_get_user, async_client, generate_token, app):
        from unittest.mock import MagicMock, patch
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["organizations:write"])
        resp = await async_client.post("/api/v1/organizations",
            json={"name": "Acme Corp", "organization_type": "customer_company"},
            headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (200, 201), (await resp.get_data()).decode()[:200]
```

(Confirm the real create-org route path + payload shape + required scope by grepping the organizations routes before finalizing this test.)

- [ ] **Step 2: Run it, verify it fails** — validation rejects `customer_company` (marshmallow `OneOf`/pydantic `Literal`).

- [ ] **Step 3: Add `customer_company`** to `OrganizationType = Literal[..., "customer_company"]` (`pydantic/organization.py:18`) and to BOTH `OneOf([...])` lists in `schemas/organization.py` (lines ~17 and ~35).

- [ ] **Step 4: Run it, verify it passes.**

- [ ] **Step 5: Commit** — `feat(organizations): add customer_company org type (CRM)`

---

## Task 3: Verify

- [ ] **Step 1:** Reset DB (`DROP/CREATE elder_test` + `redis-cli FLUSHALL`); run `tests/unit/test_crm_entity_types.py` + `tests/unit/test_api_identities.py` + `tests/unit/test_api_organizations*.py` (grep for the real org test file name) → 0 failed.
- [ ] **Step 2:** `alembic upgrade head` on a fresh DB (001→035) clean; `downgrade -1`/`upgrade head` round-trip clean (035's downgrade is a documented no-op); migration 035 present.
- [ ] **Step 3:** Full `tests/unit` suite on final HEAD (fresh DB) → 0 failed (known order-dependent flaky files, if any, confined + matching the base — do not chase them).

## Self-review notes (author)

- `identity_type` create-path has no pydantic `Literal` to update (validation is at the DB enum layer) — confirmed by grep; the enum member + migration suffice. If a later grep finds a `Literal`/`OneOf` for identity_type, add `customer_contact` there too.
- `customer_company` needs no migration (String column) — only the two validation surfaces. If any other route validates `organization_type` against a hardcoded list, add it there too.
- The `identities.metadata` bag (Plan 02) is the home for contact details (phone/desk/office/etc.) — no per-detail columns.
