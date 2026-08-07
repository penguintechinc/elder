# Plan 04 — Issue intake forms (public submit → native support issue)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Configurable intake forms that create native **Issues** (`issue_type=support`) on submit. Admin CRUD for forms; each form has a globally-unique slug, a JSON field spec, an `issue_type`, an optional default assignee, and a public/private flag. Public forms expose an unauthenticated `GET /api/v1/intake/<slug>` + `POST /api/v1/intake/<slug>/submit`; public submits require an **Altcha** proof-of-work solution. Submissions are validated with a **dynamically-built Pydantic model** (all Pydantic types). A submit upserts the requester as a `customer_contact` **identity** (keyed on email; details in `identities.metadata`) and creates a native support Issue.

**Scope note:** File-upload/attachment storage (PVC/S3) is a **separate follow-up**, NOT in this plan — no reusable storage abstraction exists in the repo (the diagrams "storage provider" is config-only; the only real S3 client is the backup service). Forms here support non-file field types; a `file` field type is rejected with a clear "not yet supported" message. Tracked for a later plan.

**Architecture:** Builds on the merged Plans 01–03 — `issues` is tenant-scoped with `issue_type=support`; `identities` has `identity_type=customer_contact` + a `metadata` JSON bag. Intake forms live in the **helpdesk/CRM module** (helpdesk is becoming CRM). New `intake_forms` table; new blueprint with admin routes + unauthenticated public routes. Alembic head is **035**; new migration is **036**.

**Tech Stack:** Python 3.13, Quart, penguin-dal, Alembic, pydantic (incl. `pydantic.create_model` — used nowhere else yet), pytest; `elder-test:3.13` against `elder-test-postgres`/`elder-test-redis` (55432/56379).

## Global Constraints

- Runtime DB = penguin-dal (`current_app.db`) only; SQLAlchemy/Alembic for schema/migrations only. `python3`; type hints; `@dataclass(slots=True)` where applicable.
- Every object gets `village_id` (VillageIDMixin, `generate_village_id(tenant_id, redis_client)` — raises `ValueError` if redis None; use `getattr(current_app,"redis_client",None)` and a `f"test-{uuid4().hex[:8]}"` fallback like `helpdesk/routes/ticket_forms.py`) and a `metadata` JSON bag (map SQLAlchemy attr to DB column `metadata`; never a literal `metadata` attr).
- Tenant from `g.claims["tenant"]` on admin routes (use `_get_tenant_id()` pattern). Public routes have NO auth — the form row's `tenant_id` is authoritative for what gets created.
- **Slug is GLOBALLY unique** (`UniqueConstraint("slug")`) — public URLs have no tenant component (mirror `HdTicketForm`).
- penguin-dal `insert()` does NOT apply SQLAlchemy `Column(default=...)` — pass all NOT-NULL fields explicitly.
- `customer_contact` identity: `username` is unique NOT NULL → use the lowercased email as username; set `identity_type="customer_contact"`, `auth_provider="local"`, `is_active=True`, `portal_role="observer"`, `must_change_password=False`, `tenant_id`, `email`, `metadata={...}`, `village_id`.
- **Test recipe** (worktree root): `docker start elder-test-postgres elder-test-redis >/dev/null 2>&1; docker exec elder-test-redis redis-cli FLUSHALL >/dev/null 2>&1; docker exec elder-test-postgres psql -U elder_test -d postgres -c "DROP DATABASE IF EXISTS elder_test; CREATE DATABASE elder_test OWNER elder_test;"; PW=$(docker inspect elder-test-postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_PASSWORD=' | cut -d= -f2-); docker run --rm --network host -e DATABASE_URL="postgresql://elder_test:${PW}@localhost:55432/elder_test" -e REDIS_URL="redis://localhost:56379/0" -v "$(pwd)":/app -w /app elder-test:3.13 <testpath> -p no:warnings -q --no-header`. Auth (admin tests): `generate_token(tenant_id=1, scopes=[...])` + `@patch("apps.api.auth.decorators.get_current_user")`.
- Known order-dependent flaky files (documents/helpdesk_sla/dashboard/seed_cloud_discovery) — reset DB before judging; confine, don't chase.

---

## File Structure

- `apps/api/modules/helpdesk/models/helpdesk.py` — add `IntakeForm` model (`hd_intake_forms`, or reuse the module's `hd_` prefix). VillageID + TenantScoped + Timestamp + `metadata`.
- `apps/api/modules/helpdesk/routes/intake_forms.py` — **new**: admin CRUD blueprint (`/api/v1/intake-forms`, scope `helpdesk:admin`) + public blueprint routes (`/api/v1/intake/<slug>`, `/api/v1/intake/<slug>/submit`, no auth).
- `apps/api/modules/helpdesk/services/form_validation.py` — **new**: `build_form_model(fields)` + `validate_submission(fields, data)` (dynamic Pydantic).
- `apps/api/modules/helpdesk/services/altcha.py` — **new**: `create_challenge()` + `verify_solution(payload) -> bool` (PoW + HMAC, `CAPTCHA_SECRET`).
- `apps/api/modules/helpdesk/services/intake_submit.py` — **new**: `upsert_customer_contact(db, tenant_id, email, details, redis)` + `create_support_issue_from_form(db, form, validated, contact_id, redis)`.
- `apps/api/modules/__init__.py` — register the new blueprint(s) in `_helpdesk_blueprints` + add the models module to the helpdesk manifest `models_import` (the model is in the same `helpdesk.py` already imported — no change needed unless new file).
- `alembic/versions/036_intake_forms.py` — the `hd_intake_forms` table.
- `tests/unit/test_intake_forms.py`, `tests/unit/test_form_validation.py`, `tests/unit/test_altcha.py` — new.

---

## Task 1: `IntakeForm` model + admin CRUD

**Files:** `helpdesk/models/helpdesk.py`; `alembic/versions/036_intake_forms.py`; `helpdesk/routes/intake_forms.py` (admin routes only this task); register in `modules/__init__.py` `_helpdesk_blueprints`; `tests/unit/test_intake_forms.py`.

**Interfaces:** `hd_intake_forms` columns: `id`, `tenant_id` (FK tenants, NOT NULL), `village_id` (unique), `metadata` (JSON), `name` (255 NOT NULL), `slug` (255 NOT NULL, **globally unique**), `description` (Text), `fields` (JSON NOT NULL, the field spec array), `issue_type` (String(30) default `"support"`), `default_assignee_type` (String(16) nullable), `default_assignee_id` (Integer nullable), `is_public` (Bool default False), `captcha_required` (Bool default False), `is_active` (Bool default True), timestamps. Admin blueprint `intake_forms.bp` mounted `/api/v1/intake-forms`, scope `helpdesk:admin`.

- [ ] **Step 1: Failing test** — admin creates a form; GET lists it (tenant-scoped).

```python
# tests/unit/test_intake_forms.py
import json, pytest
from unittest.mock import MagicMock, patch

class TestIntakeFormsAdmin:
    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_and_list_form(self, mock_get_user, async_client, generate_token, app):
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])
        resp = await async_client.post("/api/v1/intake-forms",
            json={"name": "Support Request", "slug": "support-request",
                  "fields": [{"id": "email", "label": "Email", "type": "email", "required": True},
                             {"id": "subject", "label": "Subject", "type": "text", "required": True}],
                  "is_public": True, "captcha_required": True},
            headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (200, 201), (await resp.get_data()).decode()[:300]
        listing = await async_client.get("/api/v1/intake-forms", headers={"Authorization": f"Bearer {token}"})
        slugs = [f["slug"] for f in json.loads(await listing.get_data())["items"]]
        assert "support-request" in slugs
```

- [ ] **Step 2: Run it, verify it fails** (route/table absent).

- [ ] **Step 3: Add model + migration + admin routes + register.** Model per Interfaces (read `HdTicketForm` for the pattern, add VillageID/metadata). Migration 036 (`down_revision="035"`) creates `hd_intake_forms` with the columns + globally-unique slug constraint + explicit indexes; guard idempotent. Admin routes (list/create/get/update/delete) tenant-scoped (`_get_tenant_id()`), `@require_scope("helpdesk:admin")`, `fields` stored via `json.dumps`, slug global-uniqueness check on create; village_id minted on create. Register `intake_forms.bp` at `/api/v1/intake-forms` in `_helpdesk_blueprints`.

- [ ] **Step 4: Run it, verify it passes.** Add: create with a duplicate slug → 409/400.

- [ ] **Step 5: Commit** — `feat(crm): intake form model + admin CRUD`

---

## Task 2: Dynamic Pydantic submission validation

**Files:** `helpdesk/services/form_validation.py`; `tests/unit/test_form_validation.py`.

**Interfaces:** `build_form_model(fields: list[dict]) -> type[BaseModel]` — maps each spec's `type` to a Python/pydantic type (`text/textarea/string`→`str`, `email`→`EmailStr`, `number/int`→`int`, `float`→`float`, `bool/checkbox`→`bool`, `date`→`date`, `select`→`str` (or `Literal` if `options` present), `url`→`HttpUrl`; unknown/`file`→ raise `ValueError("unsupported field type: file")`), required→required else `Optional[...] = None`; field name = spec `id`. `validate_submission(fields, data) -> tuple[dict|None, list[str]]` returns `(validated_dict, [])` or `(None, error_messages)`.

- [ ] **Step 1: Failing test** — valid submission passes, missing required + bad email fail with messages, `file` type rejected.

```python
# tests/unit/test_form_validation.py
from apps.api.modules.helpdesk.services.form_validation import build_form_model, validate_submission
import pytest

FIELDS = [{"id":"email","label":"Email","type":"email","required":True},
          {"id":"age","label":"Age","type":"number","required":False}]

def test_valid_submission():
    ok, errs = validate_submission(FIELDS, {"email":"a@b.com","age":30})
    assert errs == [] and ok["email"] == "a@b.com" and ok["age"] == 30

def test_missing_required_and_bad_email():
    ok, errs = validate_submission(FIELDS, {"age":"notanumber"})
    assert ok is None and errs  # email missing + age not int

def test_file_type_unsupported():
    with pytest.raises(ValueError):
        build_form_model([{"id":"f","label":"F","type":"file","required":False}])
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement `form_validation.py`** using `pydantic.create_model` + `EmailStr`/`HttpUrl`/`date`. `validate_submission` runs `model.model_validate(data)`, catches `ValidationError`, flattens `e.errors()` to human-readable strings.

- [ ] **Step 4: Run it, verify it passes.**

- [ ] **Step 5: Commit** — `feat(crm): dynamic Pydantic validation for intake submissions`

---

## Task 3: Altcha challenge + verifier

**Files:** `helpdesk/services/altcha.py`; `tests/unit/test_altcha.py`.

**Interfaces:** `create_challenge(difficulty:int=50000) -> dict` returns `{algorithm:"SHA-256", challenge, salt, maxnumber, signature}` where `challenge = sha256(salt + str(number))` for a random `number` in `[0, difficulty)`, `signature = hmac_sha256(CAPTCHA_SECRET, challenge)`. `verify_solution(payload: dict|str) -> bool` — payload is the Altcha widget's returned object (or its base64 JSON): checks `sha256(salt + str(number)) == challenge` AND `hmac_sha256(CAPTCHA_SECRET, challenge) == signature`. (This is the standard Altcha protocol; the existing `auth.py:/captcha-challenge` is a placeholder that doesn't embed a solvable number — this service supersedes it; do NOT depend on that endpoint.)

- [ ] **Step 1: Failing test** — a challenge can be solved + verified; a tampered/forged solution fails.

```python
# tests/unit/test_altcha.py
import hashlib, base64, json, os
os.environ.setdefault("CAPTCHA_SECRET", "test-secret")
from apps.api.modules.helpdesk.services.altcha import create_challenge, verify_solution

def _solve(ch):
    for n in range(ch["maxnumber"] + 1):
        if hashlib.sha256(f"{ch['salt']}{n}".encode()).hexdigest() == ch["challenge"]:
            return {"algorithm": ch["algorithm"], "challenge": ch["challenge"],
                    "number": n, "salt": ch["salt"], "signature": ch["signature"]}
    raise AssertionError("unsolvable")

def test_solve_and_verify():
    ch = create_challenge(difficulty=2000)
    assert verify_solution(_solve(ch)) is True

def test_forged_fails():
    ch = create_challenge(difficulty=2000)
    bad = _solve(ch); bad["number"] = bad["number"] + 1
    assert verify_solution(bad) is False
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement `altcha.py`** (stdlib `hashlib`/`hmac`/`os`/`secrets`/`base64`/`json`). `create_challenge` picks a random `number < difficulty`, `challenge=sha256(salt+number)`, `signature=hmac(secret, challenge)`, `maxnumber=difficulty`. `verify_solution` accepts a dict or a base64-encoded JSON string, recomputes + compares (constant-time `hmac.compare_digest`).

- [ ] **Step 4: Run it, verify it passes.**

- [ ] **Step 5: Commit** — `feat(crm): Altcha challenge + solution verifier`

---

## Task 4: Public intake endpoints (submit → native support issue)

**Files:** `helpdesk/routes/intake_forms.py` (add public routes); `helpdesk/services/intake_submit.py`; `tests/unit/test_intake_forms.py`.

**Interfaces:**
- `GET /api/v1/intake/<slug>` (no auth) — resolve `is_active` form by slug; 404 if missing/not public? (public forms only via this route: require `is_public==True` else 404). Return `{name, description, fields (safe), captcha_required, altcha_challenge?}` (include a fresh `create_challenge()` when `captcha_required`).
- `POST /api/v1/intake/<slug>/submit` (no auth) — resolve public active form; if `captcha_required`, `verify_solution(body["altcha"])` else 400; `validate_submission(form.fields, body["fields"])` → 400 with messages on fail; `upsert_customer_contact(...)` keyed on lowercased `fields["email"]`; `create_support_issue_from_form(...)`; return `{issue_village_id, status:"created"}` (do NOT leak internal ids/tenant).
- `intake_submit.upsert_customer_contact(db, tenant_id, email, details, redis) -> int` — find `db((db.identities.tenant_id==tenant_id) & (db.identities.username==email)).select().first()`; reuse if present, else insert a `customer_contact` identity (per Global Constraints casing) with `metadata=details`; return identity id.
- `intake_submit.create_support_issue_from_form(db, form, validated, contact_id, redis) -> str` — inline `db.issues.insert(...)`: `title = validated.get("subject") or form.name`, `description` = a rendered summary of the submitted fields, `status="OPEN"`, `priority=(validated.get("priority") or "medium").upper()`, `issue_type="SUPPORT"`, `channel="web"`, `tenant_id=form.tenant_id`, `reporter_id=contact_id`, `resource_type="organization"`, `resource_id=<form's org — see note>`, `village_id=<minted>`, timestamps. If `form.default_assignee_type`/`id` set, set `assignee_type`/`assignee_id`. Return the issue's `village_id`.

**Note on org:** the form needs an owning org for `resource_id`. Add an `organization_id` column to `hd_intake_forms` (Task 1) OR default to the tenant's root org. **Decide in Task 1: add `organization_id` (nullable FK organizations) to the form model** so a submit has a target org; if null, look up the tenant's first org.

- [ ] **Step 1: Failing test** — end-to-end public submit creates a support issue + a customer_contact identity.

```python
    @pytest.mark.asyncio
    async def test_public_submit_creates_issue_and_contact(self, async_client, app, generate_token):
        from unittest.mock import MagicMock, patch
        # seed: a public form (tenant 1) + an org
        with patch("apps.api.auth.decorators.get_current_user", return_value=MagicMock(id=1, is_superuser=True)):
            token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])
            await async_client.post("/api/v1/intake-forms", json={"name":"Help","slug":"help",
                "fields":[{"id":"email","label":"Email","type":"email","required":True},
                          {"id":"subject","label":"Subject","type":"text","required":True}],
                "is_public":True, "captcha_required":False},
                headers={"Authorization": f"Bearer {token}"})
        resp = await async_client.post("/api/v1/intake/help/submit",
            json={"fields": {"email":"cust@example.com","subject":"Cannot log in"}})
        assert resp.status_code in (200,201), (await resp.get_data()).decode()[:300]
        async with app.app_context():
            db = current_app.db
            assert db((db.identities.username=="cust@example.com") & (db.identities.identity_type=="customer_contact")).count() == 1
            assert db((db.issues.issue_type=="SUPPORT") & (db.issues.title=="Cannot log in")).count() >= 1
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement the public routes + `intake_submit.py`** per Interfaces. Wire the public blueprint (no auth decorators) into `_helpdesk_blueprints` at `/api/v1/intake`. Handle: form not found/not public → 404; captcha fail → 400; validation fail → 400 with messages; missing email when a contact is needed → 400.

- [ ] **Step 4: Run it, verify it passes.** Add: captcha_required form rejects a submit with no/invalid altcha (400); private form (is_public False) → 404 on the public route.

- [ ] **Step 5: Commit** — `feat(crm): public intake submit -> customer_contact + native support issue`

---

## Task 5: Verify

- [ ] **Step 1:** Reset DB; run `tests/unit/test_intake_forms.py test_form_validation.py test_altcha.py` → 0 failed.
- [ ] **Step 2:** `alembic upgrade head` fresh (→ 036), `downgrade -1`/`upgrade head` round-trip clean; 036 present.
- [ ] **Step 3:** Full `tests/unit` on final HEAD (fresh DB) → 0 failed (known-flaky files confined only).
- [ ] **Step 4:** Confirm the public submit path sets `tenant_id`/`village_id` on both the new identity and the new issue, and that a `file`-type field is rejected with a clear message.

## Self-review notes (author)

- Builds on Plans 01–03 (support issue_type, customer_contact identity, identities.metadata) — do not re-add them.
- File-upload storage deferred (separate subsystem; no reusable abstraction). `file` field type is explicitly rejected, not silently ignored — flagged as the follow-up.
- Altcha service supersedes the placeholder `/captcha-challenge` in `auth.py` (which doesn't embed a solvable number); a later cleanup can point that endpoint at this service.
- Public submit is unauthenticated by design (mirrors `ticket_forms.py` public routes) — the form row's `tenant_id`/`is_public` are the authoritative gate; slug is globally unique; captcha gates abuse. Never leak internal ids/tenant in the public response.
- `intake_submit` reuses penguin-dal inline inserts (no reusable issue-create service exists) — mirror `create_issue`'s field set, adapted for an anonymous reporter (contact identity, not `g.current_user`).
