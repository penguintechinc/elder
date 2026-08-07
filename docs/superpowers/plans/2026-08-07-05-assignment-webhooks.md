# Plan 05 — Assignment webhooks (`issue.assigned`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fire a new native webhook event, `issue.assigned`, on every issue-assignee change (create-with-assignee, PATCH assignee change, intake-form default-assign), matched per-tenant against additive `issue_type`/assignee filters on each webhook config, delivered HMAC-signed and non-blocking, with a `webhook_deliveries` audit row per attempt.

**Repo reality this plan starts from (read before touching code):** the native `webhooks`/`webhook_deliveries` system is currently **broken and unwired** — `WebhookService` (`apps/api/services/webhooks/service.py`) reads/writes columns that do not exist on the real schema (`events_json`/`headers_json`/`description`/`enabled` on `webhooks`; `payload_json`/`attempts`/`success`/`status_code`/`duration_ms`/`error_message` on `webhook_deliveries`). The authoritative schema is `alembic/versions/011_create_base_tables.py:641-655` (webhooks: `id`, `organization_id` nullable FK, `name`, `url`, `secret`, `is_active`, `events` JSON, `headers` JSON, `last_triggered_at`, timestamps — no `village_id`/`tenant_id`) and `:783-797` (webhook_deliveries: `id`, `webhook_id`, `event_type`, `status`, `http_status`, `request_payload` JSON, `response_body`, `error_message`, `attempt_count`, `delivered_at`, `created_at` — no `updated_at`). The SQLAlchemy model (`apps/api/modules/webhooks_alerting/models/webhooks.py`) drifts a third way again. `broadcast_event()`'s `self.db.webhooks.enabled is True` is also a dead filter (Python identity comparison on a pydal `Field`, always false). Task 1 and Task 2 below reconcile the model and service **toward the migration's already-correct column names** — the migration is schema truth; the Python code is what's wrong.

**A second, separate webhook path already fires `issue.created` today**: `shared/webhooks/issue_webhooks.py::send_issue_created_webhooks`, which queries `alert_configurations` (not `webhooks`), has no HMAC, and is called fire-and-forget from `apps/api/modules/issues/routes/issues.py:358-370`. **This plan does not touch it.** Converging the two webhook systems into one is a real problem (two independently-configured webhook surfaces for the same product) but is out of scope here and is a tracked follow-up, not part of Task 1-9.

**Architecture:** Reconcile the native `webhooks` table's Python-side model/service to match its real (already-deployed) schema, then extend it with `tenant_id`, `village_id`, three additive assignment-event filter columns, and a `metadata` JSON bag. A new pure-matching module (`apps/api/services/webhooks/assignment.py`) builds an `AssignmentEvent` from any of the three call sites that can change `issues.assignee_id`, finds every active tenant webhook subscribed to `issue.assigned` whose filters match, and delivers an HMAC-signed payload to each — non-blocking (`asyncio.create_task` wrapping `asyncio.to_thread`-offloaded `requests.post`), recording one `webhook_deliveries` row per attempt. Fire-and-forget: a delivery failure never fails the issue create/update/intake-submit that triggered it.

**Tech Stack:** Python 3.13, Quart, penguin-dal (runtime queries), SQLAlchemy + Alembic (schema/migrations only), `requests` (synchronous, offloaded via `asyncio.to_thread`), pytest + pytest-asyncio; `elder-test:3.13` against `elder-test-postgres`/`elder-test-redis` (55432/56379).

## Global Constraints

- Runtime DB access is penguin-dal (`current_app.db`) only; SQLAlchemy + Alembic are schema/migration-only (`backend-database.md`).
- Every new/changed object is **tenant-scoped**: `webhooks` gains `tenant_id`, and every CRUD route + the assignment-matching query filters by the caller's/event's `tenant_id`. Never trust a client-supplied `tenant_id` — derive it from `g.claims["tenant"]` (see `_tenant_id()` helpers already used throughout `apps/api/modules/issues/routes/`).
- Every new/changed object carries a unique **village_id** (`VillageIDMixin`) and a **metadata** JSON bag mapped to DB column `metadata` (Python attribute `webhook_metadata`, same pattern as `Issue.issue_metadata` / `Organization.org_metadata` — `metadata` is reserved on `Base`).
- **JSON-column read/write asymmetry (critical, verified against this exact codebase):** `apps/api/modules/helpdesk/routes/intake_forms.py:285` does `json.loads(form_row.metadata) if form_row.metadata else None` on a JSON column read via penguin-dal, while `apps/api/modules/helpdesk/services/intake_submit.py:132` writes the same kind of column as a raw dict (`metadata=details`). A pydal SELECT returns a JSON column as a raw JSON **string**, not a pre-parsed dict/list, even though `.insert()`/`.update()` accept (and correctly persist) a raw Python dict/list. Every read of `webhooks.events`, `webhooks.headers`, `webhooks.metadata`, or `webhook_deliveries.request_payload` in this plan's code **must** go through `json.loads(x) if x else <default>`; every write passes the raw dict/list directly. Getting this backwards produces a `TypeError` (`json.loads` on a dict) or silently-wrong behavior (treating a JSON string as if it were already a list), not a clean failure — verify this in Task 1's own tests before building on it.
- Dispatch is **non-blocking** from async routes: `asyncio.create_task(send_issue_assigned_webhooks(db, event))` fired immediately after the assignment-changing DB write commits; `send_issue_assigned_webhooks` itself offloads every blocking DB/HTTP call via `asyncio.to_thread`. Never call `asyncio.create_task` from inside a `run_in_threadpool`-executed sync function — no running event loop there.
- Every delivery is **HMAC-signed** (`sha256`, header `X-Elder-Signature`) via a single shared `generate_signature()` function (Task 2) — never a second, divergent signing implementation.
- A **fire-and-forget dispatch must never fail the operation that triggered it** — every exception in matching or delivery is caught and logged, never re-raised into the caller's request/response path.
- Type hints on every function; `@dataclass(slots=True)` for new data structures (matches `shared/utils/village_id.py`'s `VillageId`).
- `# noqa: E712` on any literal `== True`/`== False` pydal comparison (repo's own `.flake8` doesn't blanket-ignore E712; `apps/api/api/v1/tenants.py:518` is the existing precedent for the noqa).
- **Test/verification recipe** (run from the worktree root; do not run the full suite, do not background it):
  ```bash
  docker start elder-test-postgres elder-test-redis >/dev/null 2>&1
  docker exec elder-test-redis redis-cli FLUSHALL >/dev/null 2>&1
  docker exec elder-test-postgres psql -U elder_test -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='elder_test' AND pid<>pg_backend_pid();" \
    -c "DROP DATABASE IF EXISTS elder_test;" \
    -c "CREATE DATABASE elder_test OWNER elder_test;"
  PW=$(docker inspect elder-test-postgres --format '{{range .Config.Env}}{{println .}}{{end}}' \
       | grep '^POSTGRES_PASSWORD=' | cut -d= -f2-)
  docker run --rm --network host \
    -e DATABASE_URL="postgresql://elder_test:${PW}@localhost:55432/elder_test" \
    -e REDIS_URL="redis://localhost:56379/0" \
    -v "$(pwd)":/app -w /app elder-test:3.13 \
    <testpath> -p no:warnings -q --no-header
  ```
  Then, on the exact files touched by the task: `flake8 <files>` and `black --check <files>` (both must pass — CI runs `black --check`, a black-only failure blocks merge). No full-suite run; no `run_in_background`.
- Coverage target 90%+ on new/changed code (repo-wide standard); each task's own tests are the mechanism, not a separate pass.

---

## File Structure

**New:**
- `alembic/versions/037_webhook_assignment_events.py` — schema migration
- `apps/api/services/webhooks/assignment.py` — `AssignmentEvent`, matching, dispatch
- `tests/unit/test_webhooks_schema.py`
- `tests/unit/test_webhook_service.py`
- `tests/unit/test_api_webhooks.py`
- `tests/unit/test_webhook_assignment_matching.py`
- `tests/unit/test_webhook_assignment_dispatch.py`
- `tests/unit/test_issues_assigned_webhook_create.py`
- `tests/unit/test_issues_assigned_webhook_update.py`
- `tests/unit/test_intake_assigned_webhook.py`
- `tests/unit/test_issue_assigned_webhook_matrix.py`

**Modified:**
- `apps/api/modules/webhooks_alerting/models/webhooks.py` — `Webhook`/`WebhookDelivery` reconciled to schema truth + new columns
- `apps/api/services/webhooks/service.py` — column-name reconciliation, tenant scoping, `generate_signature()` extraction, village_id minting
- `apps/api/modules/webhooks_alerting/routes/webhooks.py` — tenant-scoped CRUD, new filter/metadata fields
- `apps/api/modules/issues/routes/issues.py` — fire `issue.assigned` from `create_issue`/`update_issue`
- `apps/api/modules/helpdesk/services/intake_submit.py` — `create_support_issue_from_form` returns the issue row (was: village_id string)
- `apps/api/modules/helpdesk/routes/intake_forms.py` — fire `issue.assigned` from `submit_public_intake_form`

---

## Task 1: Schema reconciliation — migration 037 + SQLAlchemy models

**Files:**
- Create: `alembic/versions/037_webhook_assignment_events.py`
- Modify: `apps/api/modules/webhooks_alerting/models/webhooks.py`
- Test: `tests/unit/test_webhooks_schema.py`

**Interfaces:**
- Produces: `webhooks` table gains `village_id` (String(32), unique via `uq_webhooks_village_id`), `tenant_id` (Integer, FK `tenants.id` ON DELETE CASCADE, indexed `ix_webhooks_tenant_id`), `filter_issue_type` (String(30), nullable), `filter_assignee_type` (String(16), nullable), `filter_assignee_id` (Integer, nullable), `metadata` (JSON, nullable). `Webhook`/`WebhookDelivery` SQLAlchemy models reconciled to these + the pre-existing migration-011 columns (`is_active`, `events`, `headers` on webhooks; `status`, `http_status`, `request_payload`, `error_message`, `attempt_count`, `delivered_at` on webhook_deliveries — no `success`/`duration_ms`/`attempts`/`payload_json` anywhere).

- [ ] **Step 1: Write the failing schema test**

```python
# tests/unit/test_webhooks_schema.py
"""Schema tests for the reconciled `webhooks`/`webhook_deliveries` tables
(Plan 05: assignment webhooks). These exercise columns Task 1 adds/renames —
things test_schema_smoke.py's table-count floor can't catch.
"""

from datetime import datetime, timezone

import pytest
from quart import current_app
from sqlalchemy.exc import IntegrityError


@pytest.mark.asyncio
async def test_webhooks_has_new_columns(app):
    async with app.app_context():
        db = current_app.db
        now = datetime.now(timezone.utc)
        webhook_id = db.webhooks.insert(
            tenant_id=1,
            village_id="00000001-0000000000000abc",
            name="Test Webhook",
            url="https://example.com/hook",
            events=["issue.assigned"],
            is_active=True,
            filter_issue_type="SUPPORT",
            filter_assignee_type="identity",
            filter_assignee_id=42,
            metadata={"team": "support"},
            created_at=now,
            updated_at=now,
        )
        db.commit()

        row = db.webhooks[webhook_id]
        assert row.village_id == "00000001-0000000000000abc"
        assert row.tenant_id == 1
        assert row.filter_issue_type == "SUPPORT"
        assert row.filter_assignee_type == "identity"
        assert row.filter_assignee_id == 42


@pytest.mark.asyncio
async def test_webhooks_village_id_unique(app):
    async with app.app_context():
        db = current_app.db
        now = datetime.now(timezone.utc)
        db.webhooks.insert(
            tenant_id=1,
            village_id="00000001-0000000000000def",
            name="Webhook A",
            url="https://example.com/a",
            events=["issue.assigned"],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        with pytest.raises(IntegrityError):
            db.webhooks.insert(
                tenant_id=1,
                village_id="00000001-0000000000000def",
                name="Webhook B",
                url="https://example.com/b",
                events=["issue.assigned"],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()
        db.rollback()


@pytest.mark.asyncio
async def test_webhook_deliveries_has_reconciled_columns(app):
    async with app.app_context():
        db = current_app.db
        now = datetime.now(timezone.utc)
        webhook_id = db.webhooks.insert(
            tenant_id=1,
            village_id="00000001-0000000000000fff",
            name="Delivery Test Webhook",
            url="https://example.com/hook",
            events=["issue.assigned"],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.commit()

        delivery_id = db.webhook_deliveries.insert(
            webhook_id=webhook_id,
            event_type="issue.assigned",
            status="success",
            http_status=200,
            request_payload={"event": "issue.assigned"},
            attempt_count=1,
            delivered_at=now,
            created_at=now,
        )
        db.commit()

        delivery = db.webhook_deliveries[delivery_id]
        assert delivery.status == "success"
        assert delivery.http_status == 200
        assert delivery.attempt_count == 1
        assert delivery.request_payload is not None
```

- [ ] **Step 2: Run it, verify it fails** (missing columns — `db.webhooks.insert()` raises on unknown fields, or the model doesn't yet expose them).

Run:
```bash
<verification recipe above> tests/unit/test_webhooks_schema.py -p no:warnings -q --no-header
```
Expected: FAIL (unknown column / attribute error on `village_id`, `tenant_id`, `filter_issue_type`, etc.)

- [ ] **Step 3: Write the migration**

```python
# alembic/versions/037_webhook_assignment_events.py
"""Add tenant_id, village_id, assignment-event filters, and a metadata bag to
`webhooks` (Plan 05: assignment webhooks). Migration 011 already defines the
correct `is_active`/`events`/`headers` columns on `webhooks` and
`status`/`http_status`/`request_payload`/`attempt_count`/`error_message` on
`webhook_deliveries` — those are schema truth. The Python-side model and
WebhookService currently reference a third, non-existent set of column names
(`events_json`, `enabled`, `payload_json`, `success`, ...); that drift is
reconciled in the model/service, not here — this migration only extends the
already-correct `webhooks` table.

Revision ID: 037
Revises: 036
Create Date: 2026-08-07
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade():
    """Add tenant_id/village_id/filter_*/metadata to webhooks; backfill any pre-existing rows (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("webhooks")}

    if "village_id" not in existing_columns:
        op.add_column(
            "webhooks", sa.Column("village_id", sa.String(32), nullable=True)
        )
        op.create_unique_constraint(
            "uq_webhooks_village_id", "webhooks", ["village_id"]
        )

    if "tenant_id" not in existing_columns:
        op.add_column("webhooks", sa.Column("tenant_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_webhooks_tenant_id",
            "webhooks",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_webhooks_tenant_id", "webhooks", ["tenant_id"])

    if "filter_issue_type" not in existing_columns:
        op.add_column(
            "webhooks", sa.Column("filter_issue_type", sa.String(30), nullable=True)
        )

    if "filter_assignee_type" not in existing_columns:
        op.add_column(
            "webhooks", sa.Column("filter_assignee_type", sa.String(16), nullable=True)
        )

    if "filter_assignee_id" not in existing_columns:
        op.add_column(
            "webhooks", sa.Column("filter_assignee_id", sa.Integer(), nullable=True)
        )

    if "metadata" not in existing_columns:
        op.add_column("webhooks", sa.Column("metadata", sa.JSON(), nullable=True))

    # Backfill: the native webhooks feature has been unwired/broken since
    # inception (see plan intro), so in practice zero rows exist against any
    # real deployment — this loop exists only so a stray manually-inserted
    # row is never left with a NULL village_id/tenant_id after this
    # migration runs.
    rows = conn.execute(
        sa.text("SELECT id, organization_id FROM webhooks WHERE village_id IS NULL")
    ).fetchall()
    for row in rows:
        tenant_id = None
        if row.organization_id:
            org_row = conn.execute(
                sa.text("SELECT tenant_id FROM organizations WHERE id = :oid"),
                {"oid": row.organization_id},
            ).fetchone()
            if org_row and org_row.tenant_id:
                tenant_id = org_row.tenant_id
        village_id = f"{(tenant_id or 0):08x}-{uuid.uuid4().hex[:16]}"
        conn.execute(
            sa.text(
                "UPDATE webhooks SET village_id = :vid, tenant_id = :tid WHERE id = :id"
            ),
            {"vid": village_id, "tid": tenant_id, "id": row.id},
        )


def downgrade():
    """Drop the columns added by upgrade() (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("webhooks")}

    if "metadata" in existing_columns:
        op.drop_column("webhooks", "metadata")
    if "filter_assignee_id" in existing_columns:
        op.drop_column("webhooks", "filter_assignee_id")
    if "filter_assignee_type" in existing_columns:
        op.drop_column("webhooks", "filter_assignee_type")
    if "filter_issue_type" in existing_columns:
        op.drop_column("webhooks", "filter_issue_type")
    if "tenant_id" in existing_columns:
        op.drop_index("ix_webhooks_tenant_id", table_name="webhooks")
        op.drop_constraint("fk_webhooks_tenant_id", "webhooks", type_="foreignkey")
        op.drop_column("webhooks", "tenant_id")
    if "village_id" in existing_columns:
        op.drop_constraint("uq_webhooks_village_id", "webhooks", type_="unique")
        op.drop_column("webhooks", "village_id")
```

- [ ] **Step 4: Reconcile the SQLAlchemy models**

```python
# apps/api/modules/webhooks_alerting/models/webhooks.py
# flake8: noqa: E501
"""Webhook and notification rule models."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)

from apps.api.models.base import Base, IDMixin, TimestampMixin, VillageIDMixin


class Webhook(Base, IDMixin, VillageIDMixin, TimestampMixin):
    """Tenant-scoped outbound webhook configuration.

    Reconciled to the real (already-deployed) migration-011 schema
    (`is_active`/`events`/`headers`, not the `enabled`/`events_json`/
    `headers_json` this model previously drifted to) and extended (037) with
    tenant_id, village_id, additive assignment-event filters, and a
    universal metadata bag.
    """

    __tablename__ = "webhooks"

    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Tenant this webhook belongs to",
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    name = Column(String(255), nullable=False)
    url = Column(String(1024), nullable=False)
    secret = Column(String(512), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    events = Column(JSON, nullable=True, comment="Subscribed event types, e.g. ['issue.assigned']")
    headers = Column(JSON, nullable=True, comment="Custom headers sent with every delivery")
    filter_issue_type = Column(
        String(30),
        nullable=True,
        comment="Optional issue_type filter for issue.assigned (e.g. SUPPORT); unset matches any",
    )
    filter_assignee_type = Column(
        String(16),
        nullable=True,
        comment="Optional assignee_type filter for issue.assigned ('identity' or 'org_unit')",
    )
    filter_assignee_id = Column(
        Integer,
        nullable=True,
        comment="Optional assignee_id filter for issue.assigned",
    )
    # `metadata` is reserved on SQLAlchemy declarative models (Base.metadata),
    # so the Python attribute is named webhook_metadata while the DB column
    # stays `metadata` (same pattern as Issue.issue_metadata).
    webhook_metadata = Column(
        "metadata", JSON, nullable=True, comment="Universal free-form JSON metadata bag"
    )
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)


class WebhookDelivery(Base, IDMixin):
    """Webhook delivery audit record — one row per delivery attempt.

    Reconciled to the real migration-011 schema: `status`/`http_status`/
    `request_payload`/`error_message`/`attempt_count`, no `success` boolean,
    no `duration_ms`, no `updated_at` (this table is insert-then-update-in-
    place, never re-timestamped as "updated").
    """

    __tablename__ = "webhook_deliveries"

    webhook_id = Column(
        Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False
    )
    event_type = Column(String(255), nullable=True)
    status = Column(String(64), nullable=True, comment="'success' or 'failed'")
    http_status = Column(Integer, nullable=True)
    request_payload = Column(JSON, nullable=True)
    response_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class NotificationRule(Base, IDMixin, TimestampMixin):
    """Notification rules for various channels.

    NOTE: this model also drifts from its migration-011 schema (`event_types`/
    `conditions`/`channels`/`is_active`, not `events`/`config_json`/`enabled`)
    but notification_rules is not on any path this plan touches (assignment
    webhooks are delivered exclusively through `webhooks`/`webhook_deliveries`)
    — left unchanged here; reconciling it is a separate, tracked follow-up.
    """

    __tablename__ = "notification_rules"

    name = Column(String(255), nullable=False)
    channel = Column(String(50), nullable=False)
    events = Column(JSON, nullable=False)
    config_json = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
```

- [ ] **Step 5: Run it, verify it passes**

```bash
<verification recipe> tests/unit/test_webhooks_schema.py -p no:warnings -q --no-header
```
Expected: PASS (3 passed)

- [ ] **Step 6: Lint + format**

```bash
flake8 alembic/versions/037_webhook_assignment_events.py apps/api/modules/webhooks_alerting/models/webhooks.py tests/unit/test_webhooks_schema.py
black --check alembic/versions/037_webhook_assignment_events.py apps/api/modules/webhooks_alerting/models/webhooks.py tests/unit/test_webhooks_schema.py
```

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/037_webhook_assignment_events.py apps/api/modules/webhooks_alerting/models/webhooks.py tests/unit/test_webhooks_schema.py
git commit -m "feat(webhooks): add tenant_id/village_id/assignment filters/metadata to webhooks (037)"
```

---

## Task 2: WebhookService reconciliation

**Files:**
- Modify: `apps/api/services/webhooks/service.py`
- Test: `tests/unit/test_webhook_service.py`

**Interfaces:**
- Consumes: `Webhook`/`WebhookDelivery` schema from Task 1 (`tenant_id`, `village_id`, `is_active`, `events`, `headers`, `filter_issue_type`, `filter_assignee_type`, `filter_assignee_id`, `metadata`; `webhook_deliveries.status`/`http_status`/`request_payload`/`attempt_count`/`error_message`/`delivered_at`).
- Produces (used by Task 3 routes and Task 4/5 assignment module): module-level `generate_signature(secret: str, payload: str) -> str`; `WebhookService(db)` with `list_webhooks(tenant_id, enabled=None)`, `get_webhook(webhook_id, tenant_id)`, `create_webhook(tenant_id, name, url, events, redis_client=None, organization_id=None, secret=None, headers=None, filter_issue_type=None, filter_assignee_type=None, filter_assignee_id=None, metadata=None) -> Dict`, `update_webhook(webhook_id, tenant_id, ...) -> Dict`, `delete_webhook(webhook_id, tenant_id) -> Dict`, `deliver_webhook(webhook_id, tenant_id, event_type, payload) -> Dict`, `redeliver_webhook(webhook_id, tenant_id, delivery_id) -> Dict`, `get_webhook_deliveries(webhook_id, tenant_id, limit=50, status=None) -> List[Dict]`, `test_webhook(webhook_id, tenant_id) -> Dict`. All raise `Exception("... not found")` on missing/cross-tenant rows (existing convention — routes catch and 404 on `"not found" in str(e).lower()`).

- [ ] **Step 1: Write the failing service test**

```python
# tests/unit/test_webhook_service.py
"""Unit/integration tests for the reconciled WebhookService (Plan 05).

Covers: tenant-scoped CRUD against the real schema, village_id minting on
create, HMAC signature generation, and delivery recording. requests.post is
mocked — no real network calls.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


@pytest.mark.asyncio
async def test_create_webhook_mints_village_id_and_scopes_tenant(app):
    async with app.app_context():
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        webhook = service.create_webhook(
            tenant_id=1,
            name="Support assignments",
            url="https://hooks.example.com/support",
            events=["issue.assigned"],
            redis_client=None,
            filter_issue_type="support",
            filter_assignee_type="identity",
            filter_assignee_id=99,
        )

        assert webhook["tenant_id"] == 1
        assert webhook["village_id"] is not None
        assert webhook["filter_issue_type"] == "SUPPORT"
        assert webhook["filter_assignee_type"] == "identity"
        assert webhook["filter_assignee_id"] == 99
        assert webhook["secret"] is None


@pytest.mark.asyncio
async def test_create_webhook_requires_assignee_filter_pair(app):
    async with app.app_context():
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)

        with pytest.raises(Exception, match="must be set together"):
            service.create_webhook(
                tenant_id=1,
                name="Bad filter",
                url="https://hooks.example.com/bad",
                events=["issue.assigned"],
                filter_assignee_id=5,  # filter_assignee_type missing
            )


@pytest.mark.asyncio
async def test_get_webhook_cross_tenant_not_found(app):
    async with app.app_context():
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)
        webhook = service.create_webhook(
            tenant_id=1,
            name="Tenant 1 webhook",
            url="https://hooks.example.com/t1",
            events=["issue.assigned"],
        )

        with pytest.raises(Exception, match="not found"):
            service.get_webhook(webhook["id"], tenant_id=2)


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.service.requests.post")
async def test_deliver_webhook_signs_and_records_delivery(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        from apps.api.services.webhooks.service import WebhookService

        db = current_app.db
        service = WebhookService(db)
        webhook = service.create_webhook(
            tenant_id=1,
            name="Signed webhook",
            url="https://hooks.example.com/signed",
            events=["issue.assigned"],
            secret="s3cr3t",
        )

        result = service.deliver_webhook(
            webhook["id"], tenant_id=1, event_type="issue.assigned", payload={"x": 1}
        )

        assert result["success"] is True
        sent_headers = mock_post.call_args.kwargs["headers"]
        assert "X-Elder-Signature" in sent_headers

        deliveries = service.get_webhook_deliveries(webhook["id"], tenant_id=1)
        assert len(deliveries) == 1
        assert deliveries[0]["status"] == "success"
        assert deliveries[0]["http_status"] == 200
```

- [ ] **Step 2: Run it, verify it fails**

```bash
<verification recipe> tests/unit/test_webhook_service.py -p no:warnings -q --no-header
```
Expected: FAIL (`create_webhook` doesn't accept `tenant_id`/`filter_*` kwargs yet; `events_json` AttributeError from the current broken code)

- [ ] **Step 3: Rewrite `service.py`'s webhook-related methods**

Replace the imports and add the module-level signature function + village_id minting helpers (near the top of the file, after the existing imports):

```python
# apps/api/services/webhooks/service.py — top of file
"""Webhook & Notification Service for Elder v1.2.0 (Phase 9)."""

# flake8: noqa: E501


import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests
from penguin_dal import DAL
from sqlalchemy.exc import IntegrityError

#: Matches IssueType member names (apps/api/modules/issues/models/issue.py).
#: filter_issue_type is stored uppercase (matching how issues.issue_type
#: itself is stored — see create_issue's `.upper()`), so it's validated
#: against this same set.
_VALID_ISSUE_TYPES_UPPER = {
    "OPERATIONS",
    "CODE",
    "CONFIG",
    "SECURITY",
    "ARCHITECTURE",
    "PROCESS",
    "APPROVAL",
    "FEATURE",
    "BUG",
    "SUPPORT",
    "OTHER",
}

#: Matches issues.assignee_type's two valid values (see
#: apps/api/modules/issues/routes/issues.py::_resolve_assignee_type).
_VALID_ASSIGNEE_TYPES = {"identity", "org_unit"}

#: Postgres's constraint name for webhooks.village_id's unique constraint
#: (see alembic/versions/037_webhook_assignment_events.py). Used to scope
#: the mint-collision retry to village_id ONLY.
_VILLAGE_ID_UNIQUE_CONSTRAINT = "uq_webhooks_village_id"

_MAX_VILLAGE_ID_MINT_ATTEMPTS = 3


def generate_signature(secret: str, payload: str) -> str:
    """Generate an HMAC-SHA256 signature for a webhook payload string.

    The single shared signing implementation for every native webhook
    delivery — WebhookService's own delivery path (below) and the
    issue.assigned dispatcher (apps/api/services/webhooks/assignment.py)
    both call this, so no code path can ever sign differently than another.
    """
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _is_village_id_conflict(exc: IntegrityError) -> bool:
    """Return True only if `exc` is the webhooks village_id unique-constraint violation.

    Mirrors apps/api/modules/helpdesk/routes/intake_forms.py's identical
    helper for hd_intake_forms.
    """
    orig = getattr(exc, "orig", None)
    constraint_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if constraint_name is not None:
        return constraint_name == _VILLAGE_ID_UNIQUE_CONSTRAINT
    return _VILLAGE_ID_UNIQUE_CONSTRAINT in str(exc)


def _village_id_seq(village_id: Optional[str]) -> Optional[int]:
    """Parse the object-seq (trailing 16 hex chars) out of a village_id.

    Returns None for anything that doesn't match the `TTTTTTTT-OOOO...`
    format (notably the `test-<uuid>` fallback minted with no Redis) rather
    than raising.
    """
    if not village_id or "-" not in village_id:
        return None
    _, seq_hex = village_id.split("-", 1)
    try:
        return int(seq_hex, 16)
    except ValueError:
        return None


def _raise_village_id_counter_to_table_max(
    db: DAL, tenant_id: int, redis_client: Any
) -> None:
    """Raise the tenant's Redis village_id counter to >= the highest
    object-seq already persisted in `webhooks` for this tenant.

    Mirrors apps/api/modules/helpdesk/routes/intake_forms.py's identical
    helper for hd_intake_forms — see that docstring for the full O(1)
    recovery rationale. The counter key is shared across every table that
    mints village_ids for this tenant, so this only ever raises it.
    """
    counter_key = f"elder:vid:{tenant_id:08x}"

    rows = db(db.webhooks.tenant_id == tenant_id).select(db.webhooks.village_id)
    table_max = 0
    for row in rows:
        seq = _village_id_seq(row.village_id)
        if seq is not None:
            table_max = max(table_max, seq)

    current = int(redis_client.get(counter_key) or 0)
    redis_client.set(counter_key, max(current, table_max))


def _insert_webhook_with_unique_village_id(
    db: DAL, tenant_id: int, redis_client: Any, insert_data: Dict[str, Any]
) -> int:
    """Insert `insert_data` into webhooks with a collision-safe village_id.

    Mirrors apps/api/modules/helpdesk/routes/intake_forms.py's
    _insert_form_with_unique_village_id for hd_intake_forms — same
    mint-retry strategy against the same shared per-tenant Redis counter.
    """
    from shared.utils.village_id import generate_village_id

    last_error: Optional[IntegrityError] = None
    for _ in range(_MAX_VILLAGE_ID_MINT_ATTEMPTS):
        if redis_client:
            village_id = generate_village_id(tenant_id, redis_client)
        else:
            village_id = f"test-{uuid4().hex[:8]}"

        try:
            return db.webhooks.insert(village_id=village_id, **insert_data)
        except IntegrityError as exc:
            if not _is_village_id_conflict(exc):
                raise
            last_error = exc
            if redis_client:
                _raise_village_id_counter_to_table_max(db, tenant_id, redis_client)

    assert last_error is not None  # loop always executes >= 1 iteration
    raise last_error
```

Then replace the `WebhookService` class's webhook-related methods (leave `list_notification_rules`/`get_notification_rule`/`create_notification_rule`/`update_notification_rule`/`delete_notification_rule`/`test_notification_rule`/`_send_notification`/`_send_email_notification`/`_send_slack_notification`/`_send_teams_notification`/`_send_pagerduty_notification` untouched — notification_rules is out of scope, see Task 1's model docstring):

```python
class WebhookService:
    """Service for managing webhooks and notification rules."""

    def __init__(self, db: DAL):
        """
        Initialize WebhookService.

        Args:
            db: penguin-dal database instance
        """
        self.db = db

    # ===========================
    # Webhook Management Methods
    # ===========================

    def list_webhooks(
        self, tenant_id: int, enabled: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """
        List all webhooks for a tenant, optionally filtered by active status.

        Args:
            tenant_id: Owning tenant (from the caller's validated JWT)
            enabled: Filter by is_active status

        Returns:
            List of webhook dictionaries
        """
        query = self.db.webhooks.tenant_id == tenant_id

        if enabled is not None:
            query &= self.db.webhooks.is_active == enabled

        webhooks = self.db(query).select(orderby=self.db.webhooks.created_at)

        return [self._sanitize_webhook(w) for w in webhooks]

    def get_webhook(self, webhook_id: int, tenant_id: int) -> Dict[str, Any]:
        """
        Get webhook details by id, scoped to tenant_id.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)

        Returns:
            Webhook dictionary

        Raises:
            Exception: If webhook not found in this tenant
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        return self._sanitize_webhook(webhook)

    def create_webhook(
        self,
        tenant_id: int,
        name: str,
        url: str,
        events: List[str],
        redis_client: Optional[Any] = None,
        organization_id: Optional[int] = None,
        secret: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        filter_issue_type: Optional[str] = None,
        filter_assignee_type: Optional[str] = None,
        filter_assignee_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new tenant-scoped webhook.

        Args:
            tenant_id: Owning tenant (from the caller's validated JWT, never client input)
            name: Webhook name
            url: Target URL for webhook deliveries
            events: List of event types to subscribe to (e.g. ["issue.assigned"])
            redis_client: Redis client for village_id minting (falls back to a
                test-safe random id when None, matching hd_intake_forms)
            organization_id: Optional owning organization
            secret: Optional shared secret for HMAC signatures
            headers: Optional custom headers sent with every delivery
            filter_issue_type: Optional issue_type filter for issue.assigned
                (e.g. "support"); stored/compared uppercase
            filter_assignee_type: Optional assignee_type filter ("identity" or
                "org_unit"); required together with filter_assignee_id
            filter_assignee_id: Optional assignee_id filter; required together
                with filter_assignee_type
            metadata: Optional free-form JSON metadata bag

        Returns:
            Created webhook dictionary (see _sanitize_webhook for the exact shape)
        """
        if not url.startswith(("http://", "https://")):
            raise Exception("Webhook URL must start with http:// or https://")

        if not events or not isinstance(events, list):
            raise Exception("Events must be a non-empty list")

        if filter_issue_type is not None:
            filter_issue_type = filter_issue_type.upper()
            if filter_issue_type not in _VALID_ISSUE_TYPES_UPPER:
                raise Exception(
                    f"Invalid filter_issue_type. Must be one of: {', '.join(sorted(_VALID_ISSUE_TYPES_UPPER))}"
                )

        if (filter_assignee_type is None) != (filter_assignee_id is None):
            raise Exception(
                "filter_assignee_type and filter_assignee_id must be set together"
            )

        if (
            filter_assignee_type is not None
            and filter_assignee_type not in _VALID_ASSIGNEE_TYPES
        ):
            raise Exception(
                f"Invalid filter_assignee_type. Must be one of: {', '.join(sorted(_VALID_ASSIGNEE_TYPES))}"
            )

        now = datetime.now(timezone.utc)
        insert_data = {
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "name": name,
            "url": url,
            "events": events,
            "secret": secret,
            "headers": headers,
            "is_active": True,
            "filter_issue_type": filter_issue_type,
            "filter_assignee_type": filter_assignee_type,
            "filter_assignee_id": filter_assignee_id,
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
        }

        webhook_id = _insert_webhook_with_unique_village_id(
            self.db, tenant_id, redis_client, insert_data
        )
        self.db.commit()

        webhook = self.db.webhooks[webhook_id]
        return self._sanitize_webhook(webhook)

    def update_webhook(
        self,
        webhook_id: int,
        tenant_id: int,
        name: Optional[str] = None,
        url: Optional[str] = None,
        events: Optional[List[str]] = None,
        secret: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        is_active: Optional[bool] = None,
        filter_issue_type: Optional[str] = None,
        filter_assignee_type: Optional[str] = None,
        filter_assignee_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update a tenant-scoped webhook configuration.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)
            (remaining args: see create_webhook — same semantics, all optional)

        Returns:
            Updated webhook dictionary

        Raises:
            Exception: If webhook not found in this tenant
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        update_data: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}

        if name is not None:
            update_data["name"] = name

        if url is not None:
            if not url.startswith(("http://", "https://")):
                raise Exception("Webhook URL must start with http:// or https://")
            update_data["url"] = url

        if events is not None:
            if not isinstance(events, list):
                raise Exception("Events must be a list")
            update_data["events"] = events

        if secret is not None:
            update_data["secret"] = secret

        if headers is not None:
            update_data["headers"] = headers

        if is_active is not None:
            update_data["is_active"] = is_active

        if filter_issue_type is not None:
            resolved = filter_issue_type.upper()
            if resolved not in _VALID_ISSUE_TYPES_UPPER:
                raise Exception(
                    f"Invalid filter_issue_type. Must be one of: {', '.join(sorted(_VALID_ISSUE_TYPES_UPPER))}"
                )
            update_data["filter_issue_type"] = resolved

        if filter_assignee_type is not None:
            if filter_assignee_type not in _VALID_ASSIGNEE_TYPES:
                raise Exception(
                    f"Invalid filter_assignee_type. Must be one of: {', '.join(sorted(_VALID_ASSIGNEE_TYPES))}"
                )
            update_data["filter_assignee_type"] = filter_assignee_type

        if filter_assignee_id is not None:
            update_data["filter_assignee_id"] = filter_assignee_id

        if metadata is not None:
            update_data["metadata"] = metadata

        self.db(
            (self.db.webhooks.id == webhook_id)
            & (self.db.webhooks.tenant_id == tenant_id)
        ).update(**update_data)
        self.db.commit()

        webhook = self.db.webhooks[webhook_id]
        return self._sanitize_webhook(webhook)

    def delete_webhook(self, webhook_id: int, tenant_id: int) -> Dict[str, str]:
        """
        Delete a tenant-scoped webhook and its delivery history.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)

        Returns:
            Success message

        Raises:
            Exception: If webhook not found in this tenant
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        self.db(self.db.webhook_deliveries.webhook_id == webhook_id).delete()
        self.db(
            (self.db.webhooks.id == webhook_id)
            & (self.db.webhooks.tenant_id == tenant_id)
        ).delete()
        self.db.commit()

        return {"message": "Webhook deleted successfully"}

    # ===========================
    # Webhook Delivery Methods
    # ===========================

    def deliver_webhook(
        self, webhook_id: int, tenant_id: int, event_type: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Deliver a webhook event.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)
            event_type: Event type (e.g., "issue.assigned")
            payload: Event payload

        Returns:
            Delivery result dictionary
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        if not webhook.is_active:
            raise Exception(f"Webhook {webhook_id} is disabled")

        # JSON columns come back from a pydal SELECT as raw strings, not
        # pre-parsed lists/dicts — see the plan's Global Constraints note.
        events = json.loads(webhook.events) if webhook.events else []
        if event_type not in events:
            raise Exception(
                f"Webhook {webhook_id} does not subscribe to event {event_type}"
            )

        delivery_payload = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }

        now = datetime.now(timezone.utc)
        delivery_id = self.db.webhook_deliveries.insert(
            webhook_id=webhook_id,
            event_type=event_type,
            request_payload=delivery_payload,
            attempt_count=0,
            created_at=now,
        )
        self.db.commit()

        return self._attempt_delivery(delivery_id, webhook, delivery_payload)

    def _attempt_delivery(
        self, delivery_id: int, webhook: Any, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Attempt to deliver a webhook, HMAC-signed if a secret is configured.

        Args:
            delivery_id: Delivery id (row already inserted by the caller)
            webhook: Webhook row
            payload: Delivery payload

        Returns:
            Delivery result
        """
        try:
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Elder-Webhook/1.2.0",
            }

            if webhook.headers:
                headers.update(json.loads(webhook.headers))

            if webhook.secret:
                signature = generate_signature(
                    webhook.secret, json.dumps(payload, sort_keys=True)
                )
                headers["X-Elder-Signature"] = signature

            response = requests.post(
                webhook.url, json=payload, headers=headers, timeout=30
            )

            success = 200 <= response.status_code < 300

            self.db(self.db.webhook_deliveries.id == delivery_id).update(
                attempt_count=self.db.webhook_deliveries.attempt_count + 1,
                status="success" if success else "failed",
                http_status=response.status_code,
                response_body=response.text[:1000],
                delivered_at=datetime.now(timezone.utc) if success else None,
            )
            self.db.commit()

            return {
                "delivery_id": delivery_id,
                "success": success,
                "status_code": response.status_code,
            }

        except Exception as e:
            self.db(self.db.webhook_deliveries.id == delivery_id).update(
                attempt_count=self.db.webhook_deliveries.attempt_count + 1,
                status="failed",
                error_message=str(e)[:500],
            )
            self.db.commit()

            return {"delivery_id": delivery_id, "success": False, "error": str(e)}

    def redeliver_webhook(
        self, webhook_id: int, tenant_id: int, delivery_id: int
    ) -> Dict[str, Any]:
        """
        Retry a failed webhook delivery.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)
            delivery_id: Delivery id

        Returns:
            Redelivery result

        Raises:
            Exception: If webhook or delivery not found
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        delivery = self.db.webhook_deliveries[delivery_id]
        if not delivery:
            raise Exception(f"Delivery {delivery_id} not found")

        if delivery.webhook_id != webhook_id:
            raise Exception(
                f"Delivery {delivery_id} does not belong to webhook {webhook_id}"
            )

        payload = (
            json.loads(delivery.request_payload) if delivery.request_payload else {}
        )

        return self._attempt_delivery(delivery_id, webhook, payload)

    def get_webhook_deliveries(
        self,
        webhook_id: int,
        tenant_id: int,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get webhook delivery history.

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)
            limit: Maximum number of deliveries to return
            status: Filter by delivery status ("success" or "failed")

        Returns:
            List of delivery dictionaries

        Raises:
            Exception: If webhook not found in this tenant
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )
        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        query = self.db.webhook_deliveries.webhook_id == webhook_id

        if status is not None:
            query &= self.db.webhook_deliveries.status == status

        deliveries = self.db(query).select(
            orderby=~self.db.webhook_deliveries.created_at, limitby=(0, limit)
        )

        return [
            {
                "id": d.id,
                "webhook_id": d.webhook_id,
                "event_type": d.event_type,
                "status": d.status,
                "http_status": d.http_status,
                "request_payload": (
                    json.loads(d.request_payload) if d.request_payload else None
                ),
                "response_body": d.response_body,
                "error_message": d.error_message,
                "attempt_count": d.attempt_count,
                "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in deliveries
        ]

    def test_webhook(self, webhook_id: int, tenant_id: int) -> Dict[str, Any]:
        """
        Send a test event to webhook (uses its first subscribed event type).

        Args:
            webhook_id: Webhook id
            tenant_id: Owning tenant (from the caller's validated JWT)

        Returns:
            Test delivery result

        Raises:
            Exception: If webhook not found in this tenant
        """
        webhook = (
            self.db(
                (self.db.webhooks.id == webhook_id)
                & (self.db.webhooks.tenant_id == tenant_id)
            )
            .select()
            .first()
        )

        if not webhook:
            raise Exception(f"Webhook {webhook_id} not found")

        test_payload = {
            "test": True,
            "webhook_id": webhook_id,
            "message": "This is a test webhook delivery from Elder",
        }

        events = json.loads(webhook.events) if webhook.events else []
        event_type = events[0] if events else "test.event"

        return self.deliver_webhook(webhook_id, tenant_id, event_type, test_payload)
```

`broadcast_event` keeps its existing `organization_id`-scoped signature (unchanged, out of scope for tenant-scoping), but its webhook-half must be fixed since it references the renamed column — replace only that half:

```python
    def broadcast_event(
        self, event_type: str, payload: Dict[str, Any], organization_id: int
    ) -> Dict[str, Any]:
        """
        Broadcast an event to all applicable webhooks and notification rules.

        Args:
            event_type: Event type (e.g., "entity.created")
            payload: Event payload
            organization_id: Organization ID

        Returns:
            Broadcast result with counts
        """
        # Webhook half: reconciled to the real `is_active` column (was
        # `enabled`, compared with the always-false `is True` identity
        # check — see this plan's intro). The notification_rules half below
        # still references stale columns (events_json/enabled vs the real
        # event_types/channels/is_active) — that table's drift is a
        # separate, out-of-scope follow-up (see Task 1's model docstring);
        # left unchanged here.
        webhooks = self.db(
            (self.db.webhooks.organization_id == organization_id)
            & (self.db.webhooks.is_active == True)  # noqa: E712
        ).select()

        webhook_results = []
        for webhook in webhooks:
            events = json.loads(webhook.events) if webhook.events else []
            if event_type in events:
                try:
                    result = self.deliver_webhook(
                        webhook.id, webhook.tenant_id, event_type, payload
                    )
                    webhook_results.append(result)
                except Exception as e:
                    webhook_results.append(
                        {"webhook_id": webhook.id, "success": False, "error": str(e)}
                    )

        rules = self.db(
            (self.db.notification_rules.organization_id == organization_id)
            & (self.db.notification_rules.enabled is True)
        ).select()

        notification_results = []
        for rule in rules:
            events = json.loads(rule.events_json)
            if event_type in events:
                try:
                    result = self._send_notification(rule, payload)
                    notification_results.append(result)
                except Exception as e:
                    notification_results.append(
                        {"rule_id": rule.id, "success": False, "error": str(e)}
                    )

        return {
            "event_type": event_type,
            "webhooks_triggered": len(webhook_results),
            "webhooks_successful": sum(1 for r in webhook_results if r.get("success")),
            "notifications_triggered": len(notification_results),
            "notifications_successful": sum(
                1 for r in notification_results if r.get("success")
            ),
            "webhook_results": webhook_results,
            "notification_results": notification_results,
        }
```

Finally, replace the old `_generate_signature`/`_sanitize_webhook` helper methods:

```python
    # ===========================
    # Helper Methods
    # ===========================

    def _generate_signature(self, secret: str, payload: str) -> str:
        """Instance-method wrapper around the module-level generate_signature
        (back-compat for any existing caller of the instance method)."""
        return generate_signature(secret, payload)

    def _sanitize_webhook(self, webhook: Any) -> Dict[str, Any]:
        """
        Build the exact public webhook response shape from a pydal Row.

        Explicit field allowlist — never a raw `as_dict()`/`__dict__`
        passthrough (see security.md Output Validation) — so a future
        column addition to `webhooks` never silently leaks into every
        webhook API response. JSON columns (events/headers/metadata) come
        back from a pydal SELECT as raw strings, not pre-parsed
        dicts/lists — see this plan's Global Constraints. The secret is
        never returned in full, only a masked placeholder indicating
        whether one is configured.

        Args:
            webhook: pydal Row for a webhooks record

        Returns:
            Sanitized webhook dictionary
        """
        return {
            "id": webhook.id,
            "village_id": webhook.village_id,
            "tenant_id": webhook.tenant_id,
            "organization_id": webhook.organization_id,
            "name": webhook.name,
            "url": webhook.url,
            "secret": "***masked***" if webhook.secret else None,
            "is_active": webhook.is_active,
            "events": json.loads(webhook.events) if webhook.events else [],
            "headers": json.loads(webhook.headers) if webhook.headers else {},
            "filter_issue_type": webhook.filter_issue_type,
            "filter_assignee_type": webhook.filter_assignee_type,
            "filter_assignee_id": webhook.filter_assignee_id,
            "metadata": json.loads(webhook.metadata) if webhook.metadata else None,
            "last_triggered_at": (
                webhook.last_triggered_at.isoformat()
                if webhook.last_triggered_at
                else None
            ),
            "created_at": webhook.created_at.isoformat() if webhook.created_at else None,
            "updated_at": webhook.updated_at.isoformat() if webhook.updated_at else None,
        }
```

- [ ] **Step 4: Run it, verify it passes**

```bash
<verification recipe> tests/unit/test_webhook_service.py -p no:warnings -q --no-header
```
Expected: PASS (4 passed)

- [ ] **Step 5: Lint + format**

```bash
flake8 apps/api/services/webhooks/service.py tests/unit/test_webhook_service.py
black --check apps/api/services/webhooks/service.py tests/unit/test_webhook_service.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/services/webhooks/service.py tests/unit/test_webhook_service.py
git commit -m "fix(webhooks): reconcile WebhookService to real schema, add tenant scoping + village_id minting"
```

---

## Task 3: Webhooks CRUD routes — tenant scoping + filter/metadata fields

**Files:**
- Modify: `apps/api/modules/webhooks_alerting/routes/webhooks.py`
- Test: `tests/unit/test_api_webhooks.py`

**Interfaces:**
- Consumes: `WebhookService` from Task 2 (tenant-scoped signatures).
- Produces: `POST/GET/PUT/DELETE /api/v1/webhooks`, `POST /api/v1/webhooks/<id>/test`, `GET /api/v1/webhooks/<id>/deliveries`, `POST /api/v1/webhooks/<id>/deliveries/<delivery_id>/redeliver` — all tenant-scoped via a local `_tenant_id()` helper (same pattern as `apps/api/modules/issues/routes/common.py`).

- [ ] **Step 1: Write the failing route test**

```python
# tests/unit/test_api_webhooks.py
"""HTTP-level tests for tenant-scoped webhook CRUD (Plan 05)."""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_create_and_get_webhook(mock_get_user, async_client, generate_token):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(
        tenant_id=1, scopes=["webhooks_alerting:admin", "webhooks_alerting:read"]
    )

    resp = await async_client.post(
        "/api/v1/webhooks",
        json={
            "name": "Support assignments",
            "url": "https://hooks.example.com/support",
            "events": ["issue.assigned"],
            "filter_issue_type": "support",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]
    created = json.loads(await resp.get_data())
    assert created["village_id"] is not None
    assert created["filter_issue_type"] == "SUPPORT"

    get_resp = await async_client.get(
        f"/api/v1/webhooks/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 200


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_webhook_not_visible_cross_tenant(mock_get_user, async_client, generate_token):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token_t1 = generate_token(tenant_id=1, scopes=["webhooks_alerting:admin"])
    token_t2 = generate_token(tenant_id=2, scopes=["webhooks_alerting:read"])

    resp = await async_client.post(
        "/api/v1/webhooks",
        json={
            "name": "Tenant 1 only",
            "url": "https://hooks.example.com/t1",
            "events": ["issue.assigned"],
        },
        headers={"Authorization": f"Bearer {token_t1}"},
    )
    webhook_id = json.loads(await resp.get_data())["id"]

    get_resp = await async_client.get(
        f"/api/v1/webhooks/{webhook_id}",
        headers={"Authorization": f"Bearer {token_t2}"},
    )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
@patch("apps.api.auth.decorators.get_current_user")
async def test_create_webhook_missing_required_field(
    mock_get_user, async_client, generate_token
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=["webhooks_alerting:admin"])

    resp = await async_client.post(
        "/api/v1/webhooks",
        json={"name": "Missing url and events"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run it, verify it fails**

```bash
<verification recipe> tests/unit/test_api_webhooks.py -p no:warnings -q --no-header
```
Expected: FAIL (route still calls the old `organization_id`-only `WebhookService` signature)

- [ ] **Step 3: Rewrite the webhook-related routes**

```python
# apps/api/modules/webhooks_alerting/routes/webhooks.py
"""Webhook & Notification System API endpoints for Elder v1.2.0 (Phase 9)."""

# flake8: noqa: E501


import logging
from typing import Optional

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import admin_required, login_required, require_scope
from apps.api.logging_config import log_error_and_respond
from apps.api.services.webhooks import WebhookService

logger = logging.getLogger(__name__)

bp = Blueprint("webhooks", __name__)


def _tenant_id() -> Optional[int]:
    """Tenant id from validated JWT claims (populated by before_request).

    Duplicated locally rather than imported cross-module — matches the
    existing per-module convention (see intake_forms.py's _get_tenant_id,
    issues/routes/common.py's _tenant_id) rather than introducing a
    cross-module import for an 8-line helper.
    """
    claims = getattr(g, "claims", {}) or {}
    raw = claims.get("tenant", "")
    if not raw:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def get_webhook_service():
    """Get WebhookService instance with current database."""
    return WebhookService(current_app.db)


# ===========================
# Webhook Endpoints
# ===========================


@bp.route("", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def list_webhooks():
    """
    List all webhooks for the caller's tenant.

    Query params:
        - enabled: Filter by active status

    Returns:
        200: List of webhooks
        403: Tenant not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()

        enabled = request.args.get("enabled")
        enabled_bool = None
        if enabled is not None:
            enabled_bool = enabled.lower() == "true"

        webhooks = service.list_webhooks(tenant_id=tenant_id, enabled=enabled_bool)

        return jsonify({"webhooks": webhooks, "count": len(webhooks)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("", methods=["POST"])
@login_required
@require_scope("webhooks_alerting:admin")
@admin_required
async def create_webhook():
    """
    Create a new webhook for the caller's tenant.

    Request body:
        {
            "name": "Support bot assignments",
            "url": "https://hooks.example.com/...",
            "events": ["issue.assigned"],
            "secret": "shared-secret-for-hmac",
            "organization_id": 1,
            "headers": {"X-Custom-Header": "value"},
            "filter_issue_type": "support",
            "filter_assignee_type": "identity",
            "filter_assignee_id": 42,
            "metadata": {"team": "support"}
        }

    Returns:
        201: Webhook created
        400: Invalid request
        403: Tenant not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "url", "events"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        redis_client = getattr(current_app, "redis_client", None)

        service = get_webhook_service()
        webhook = service.create_webhook(
            tenant_id=tenant_id,
            name=data["name"],
            url=data["url"],
            events=data["events"],
            redis_client=redis_client,
            organization_id=data.get("organization_id"),
            secret=data.get("secret"),
            headers=data.get("headers"),
            filter_issue_type=data.get("filter_issue_type"),
            filter_assignee_type=data.get("filter_assignee_type"),
            filter_assignee_id=data.get("filter_assignee_id"),
            metadata=data.get("metadata"),
        )

        return jsonify(webhook), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/<int:webhook_id>", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def get_webhook(webhook_id):
    """
    Get webhook details (must belong to the caller's tenant).

    Returns:
        200: Webhook details
        403: Tenant not found
        404: Webhook not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        webhook = service.get_webhook(webhook_id, tenant_id)
        return jsonify(webhook), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>", methods=["PUT"])
@admin_required
@require_scope("webhooks_alerting:admin")
async def update_webhook(webhook_id):
    """
    Update webhook configuration (must belong to the caller's tenant).

    Request body (all optional):
        {
            "name": "Updated name",
            "url": "https://new-url.com",
            "events": ["issue.assigned"],
            "secret": "new-secret",
            "headers": {"X-New-Header": "value"},
            "is_active": false,
            "filter_issue_type": "support",
            "filter_assignee_type": "org_unit",
            "filter_assignee_id": 7,
            "metadata": {"team": "support"}
        }

    Returns:
        200: Webhook updated
        403: Tenant not found
        404: Webhook not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_webhook_service()
        webhook = service.update_webhook(
            webhook_id=webhook_id,
            tenant_id=tenant_id,
            name=data.get("name"),
            url=data.get("url"),
            events=data.get("events"),
            secret=data.get("secret"),
            headers=data.get("headers"),
            is_active=data.get("is_active"),
            filter_issue_type=data.get("filter_issue_type"),
            filter_assignee_type=data.get("filter_assignee_type"),
            filter_assignee_id=data.get("filter_assignee_id"),
            metadata=data.get("metadata"),
        )

        return jsonify(webhook), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/<int:webhook_id>", methods=["DELETE"])
@admin_required
@require_scope("webhooks_alerting:admin")
def delete_webhook(webhook_id):
    """
    Delete a webhook (must belong to the caller's tenant).

    Returns:
        200: Webhook deleted
        403: Tenant not found
        404: Webhook not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = service.delete_webhook(webhook_id, tenant_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>/test", methods=["POST"])
@login_required
@require_scope("webhooks_alerting:write")
def test_webhook(webhook_id):
    """
    Send a test event to webhook (must belong to the caller's tenant).

    Returns:
        200: Test sent successfully
        400: Test failed
        403: Tenant not found
        404: Webhook not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = service.test_webhook(webhook_id, tenant_id)

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>/deliveries", methods=["GET"])
@login_required
@require_scope("webhooks_alerting:read")
def get_webhook_deliveries(webhook_id):
    """
    Get webhook delivery history (must belong to the caller's tenant).

    Query params:
        - limit: Number of deliveries (default: 50)
        - status: Filter by delivery status ("success" or "failed")

    Returns:
        200: Delivery history
        403: Tenant not found
        404: Webhook not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()

        limit = request.args.get("limit", 50, type=int)
        status = request.args.get("status")

        deliveries = service.get_webhook_deliveries(
            webhook_id=webhook_id, tenant_id=tenant_id, limit=limit, status=status
        )

        return jsonify({"deliveries": deliveries, "count": len(deliveries)}), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:webhook_id>/deliveries/<int:delivery_id>/redeliver", methods=["POST"])
@admin_required
@require_scope("webhooks_alerting:admin")
def redeliver_webhook(webhook_id, delivery_id):
    """
    Retry a failed webhook delivery (must belong to the caller's tenant).

    Returns:
        200: Redelivery initiated
        403: Tenant not found
        404: Webhook or delivery not found
    """
    try:
        tenant_id = _tenant_id()
        if not tenant_id:
            return jsonify({"error": "Tenant not found"}), 403

        service = get_webhook_service()
        result = service.redeliver_webhook(webhook_id, tenant_id, delivery_id)

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)
```

The notification-rule endpoints (`/notification-rules...`) and `/broadcast` stay byte-for-byte unchanged — leave them exactly as they are in the current file (append the code above's block in place of the old webhook-endpoint block, keep everything from `# Notification Rule Endpoints` onward as-is).

- [ ] **Step 4: Run it, verify it passes**

```bash
<verification recipe> tests/unit/test_api_webhooks.py -p no:warnings -q --no-header
```
Expected: PASS (3 passed)

- [ ] **Step 5: Lint + format**

```bash
flake8 apps/api/modules/webhooks_alerting/routes/webhooks.py tests/unit/test_api_webhooks.py
black --check apps/api/modules/webhooks_alerting/routes/webhooks.py tests/unit/test_api_webhooks.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/modules/webhooks_alerting/routes/webhooks.py tests/unit/test_api_webhooks.py
git commit -m "feat(webhooks): tenant-scope CRUD routes, add assignment filter + metadata fields"
```

---

## Task 4: Assignment matching module

**Files:**
- Create: `apps/api/services/webhooks/assignment.py` (matching half only this task — `AssignmentEvent`, `webhook_matches_assignment`, `find_matching_webhooks`, `build_assignment_payload`; dispatch/delivery is Task 5)
- Test: `tests/unit/test_webhook_assignment_matching.py`

**Interfaces:**
- Consumes: `webhooks` schema from Task 1 (`tenant_id`, `is_active`, `events`, `filter_issue_type`, `filter_assignee_type`, `filter_assignee_id`).
- Produces (used by Task 5 and Tasks 6-9): `@dataclass(slots=True) AssignmentEvent(issue_id, village_id, issue_type, status, assignee_type, assignee_id, tenant_id, actor_id)`; `webhook_matches_assignment(webhook: Any, event: AssignmentEvent) -> bool` (pure — no DB/network); `find_matching_webhooks(db: Any, event: AssignmentEvent) -> List[Any]`; `build_assignment_payload(event: AssignmentEvent) -> Dict[str, Any]`; module constant `ASSIGNMENT_EVENT_TYPE = "issue.assigned"`.

- [ ] **Step 1: Write the failing pure-matching test** (no DB needed — uses a tiny stand-in object for `webhook`)

```python
# tests/unit/test_webhook_assignment_matching.py
"""Pure unit tests for issue.assigned webhook matching (Plan 05). No DB,
no network — webhook_matches_assignment takes a plain object with the four
filter attributes, so a lightweight stand-in is enough.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class _FakeWebhook:
    filter_issue_type: Optional[str] = None
    filter_assignee_type: Optional[str] = None
    filter_assignee_id: Optional[int] = None


def _event(**overrides):
    from apps.api.services.webhooks.assignment import AssignmentEvent

    defaults = dict(
        issue_id=1,
        village_id="00000001-0000000000000001",
        issue_type="SUPPORT",
        status="OPEN",
        assignee_type="identity",
        assignee_id=42,
        tenant_id=1,
        actor_id=7,
    )
    defaults.update(overrides)
    return AssignmentEvent(**defaults)


def test_empty_filter_matches_any_assignment():
    from apps.api.services.webhooks.assignment import webhook_matches_assignment

    webhook = _FakeWebhook()
    assert webhook_matches_assignment(webhook, _event()) is True
    assert (
        webhook_matches_assignment(
            webhook, _event(issue_type="BUG", assignee_type="org_unit", assignee_id=99)
        )
        is True
    )


def test_issue_type_filter_only():
    from apps.api.services.webhooks.assignment import webhook_matches_assignment

    webhook = _FakeWebhook(filter_issue_type="SUPPORT")
    assert webhook_matches_assignment(webhook, _event(issue_type="SUPPORT")) is True
    assert webhook_matches_assignment(webhook, _event(issue_type="BUG")) is False


def test_assignee_filter_only():
    from apps.api.services.webhooks.assignment import webhook_matches_assignment

    webhook = _FakeWebhook(filter_assignee_type="org_unit", filter_assignee_id=5)
    assert (
        webhook_matches_assignment(
            webhook, _event(assignee_type="org_unit", assignee_id=5)
        )
        is True
    )
    assert (
        webhook_matches_assignment(
            webhook, _event(assignee_type="org_unit", assignee_id=6)
        )
        is False
    )
    assert (
        webhook_matches_assignment(
            webhook, _event(assignee_type="identity", assignee_id=5)
        )
        is False
    )


def test_both_filters_are_additive_and():
    from apps.api.services.webhooks.assignment import webhook_matches_assignment

    webhook = _FakeWebhook(
        filter_issue_type="SUPPORT", filter_assignee_type="identity", filter_assignee_id=42
    )
    # Both match -> True
    assert (
        webhook_matches_assignment(
            webhook, _event(issue_type="SUPPORT", assignee_type="identity", assignee_id=42)
        )
        is True
    )
    # issue_type matches but assignee doesn't -> False
    assert (
        webhook_matches_assignment(
            webhook, _event(issue_type="SUPPORT", assignee_type="identity", assignee_id=43)
        )
        is False
    )
    # assignee matches but issue_type doesn't -> False
    assert (
        webhook_matches_assignment(
            webhook, _event(issue_type="BUG", assignee_type="identity", assignee_id=42)
        )
        is False
    )


def test_build_assignment_payload_shape():
    from apps.api.services.webhooks.assignment import build_assignment_payload

    payload = build_assignment_payload(_event())
    assert payload["event"] == "issue.assigned"
    assert payload["issue"]["id"] == 1
    assert payload["issue"]["village_id"] == "00000001-0000000000000001"
    assert payload["issue"]["issue_type"] == "SUPPORT"
    assert payload["issue"]["status"] == "OPEN"
    assert payload["issue"]["assignee"] == {"type": "identity", "id": 42}
    assert payload["actor"] == 7
    assert payload["tenant_id"] == 1
    assert "ts" in payload
```

- [ ] **Step 2: Run it, verify it fails**

```bash
<verification recipe> tests/unit/test_webhook_assignment_matching.py -p no:warnings -q --no-header
```
Expected: FAIL (`ModuleNotFoundError: apps.api.services.webhooks.assignment`)

- [ ] **Step 3: Write `assignment.py`'s matching half**

```python
# apps/api/services/webhooks/assignment.py
"""Issue-assignment webhook dispatch: matches a per-issue assignment event
against every active, filter-matching webhook in its tenant and delivers
`issue.assigned`, HMAC-signed and non-blocking.

Three call sites can change an issue's assignee and each builds an
AssignmentEvent and fires send_issue_assigned_webhooks (Task 5) after its DB
write commits: apps/api/modules/issues/routes/issues.py::create_issue,
apps/api/modules/issues/routes/issues.py::update_issue (only when the
assignee actually changes), and
apps/api/modules/helpdesk/routes/intake_forms.py::submit_public_intake_form
(when the form has a default assignee configured).
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ASSIGNMENT_EVENT_TYPE = "issue.assigned"


@dataclass(slots=True)
class AssignmentEvent:
    """A single issue-assignment occurrence to match against tenant webhooks.

    Built by each of the three assignment call sites and passed to
    send_issue_assigned_webhooks for matching + delivery.
    """

    issue_id: int
    village_id: Optional[str]
    issue_type: str
    status: str
    assignee_type: Optional[str]
    assignee_id: Optional[int]
    tenant_id: int
    actor_id: Optional[int]


def webhook_matches_assignment(webhook: Any, event: AssignmentEvent) -> bool:
    """Return True if `webhook`'s filters (if any) match `event`.

    Filters are additive (AND, not OR): an unset filter (None) matches
    anything; a set filter must match exactly. A webhook with no filters set
    at all matches every assignment in its tenant. Pure function — no DB or
    network access, so it's fully unit-testable against a plain stand-in
    object exposing the three filter_* attributes.
    """
    if (
        webhook.filter_issue_type is not None
        and webhook.filter_issue_type != event.issue_type
    ):
        return False
    if (
        webhook.filter_assignee_type is not None
        and webhook.filter_assignee_type != event.assignee_type
    ):
        return False
    if (
        webhook.filter_assignee_id is not None
        and webhook.filter_assignee_id != event.assignee_id
    ):
        return False
    return True


def find_matching_webhooks(db: Any, event: AssignmentEvent) -> List[Any]:
    """Return every active, tenant-scoped webhook subscribed to issue.assigned
    whose filters match `event`. Synchronous/blocking — callers offload this
    via asyncio.to_thread (see send_issue_assigned_webhooks).
    """
    candidates = db(
        (db.webhooks.tenant_id == event.tenant_id)
        & (db.webhooks.is_active == True)  # noqa: E712
    ).select()

    matched = []
    for webhook in candidates:
        # JSON columns come back from a pydal SELECT as raw strings, not
        # pre-parsed lists — see the plan's Global Constraints.
        events = json.loads(webhook.events) if webhook.events else []
        if ASSIGNMENT_EVENT_TYPE not in events:
            continue
        if webhook_matches_assignment(webhook, event):
            matched.append(webhook)
    return matched


def build_assignment_payload(event: AssignmentEvent) -> Dict[str, Any]:
    """Build the issue.assigned webhook payload body (spec §7)."""
    return {
        "event": ASSIGNMENT_EVENT_TYPE,
        "issue": {
            "id": event.issue_id,
            "village_id": event.village_id,
            "issue_type": event.issue_type,
            "status": event.status,
            "assignee": {"type": event.assignee_type, "id": event.assignee_id},
        },
        "actor": event.actor_id,
        "tenant_id": event.tenant_id,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 4: Run it, verify it passes**

```bash
<verification recipe> tests/unit/test_webhook_assignment_matching.py -p no:warnings -q --no-header
```
Expected: PASS (5 passed)

- [ ] **Step 5: Lint + format** (drop the not-yet-used `asyncio`/`requests`/`generate_signature` imports first if landing this task alone — see note above; keep them if Task 5 lands in the same commit)

```bash
flake8 apps/api/services/webhooks/assignment.py tests/unit/test_webhook_assignment_matching.py
black --check apps/api/services/webhooks/assignment.py tests/unit/test_webhook_assignment_matching.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/services/webhooks/assignment.py tests/unit/test_webhook_assignment_matching.py
git commit -m "feat(webhooks): add issue.assigned matching (AssignmentEvent + filter matching)"
```

---

## Task 5: Assignment dispatch service

**Files:**
- Modify: `apps/api/services/webhooks/assignment.py` (add dispatch functions below Task 4's matching functions)
- Test: `tests/unit/test_webhook_assignment_dispatch.py`

**Interfaces:**
- Consumes: `AssignmentEvent`, `find_matching_webhooks`, `build_assignment_payload`, `ASSIGNMENT_EVENT_TYPE` from Task 4; `generate_signature` from Task 2; `webhook_deliveries` schema from Task 1.
- Produces (used by Tasks 6-9): `async def send_issue_assigned_webhooks(db: Any, event: AssignmentEvent) -> List[Dict[str, Any]]` — matches + delivers to every matching webhook, non-blocking, never raises. Callers wrap it in `asyncio.create_task(...)` from an async route, immediately after the assignment-changing DB write commits.

- [ ] **Step 1: Write the failing dispatch test** (mock `requests.post` at `apps.api.services.webhooks.assignment.requests.post` — note this is a *different* mock target than Task 2's `apps.api.services.webhooks.service.requests.post`, since dispatch lives in its own module)

```python
# tests/unit/test_webhook_assignment_dispatch.py
"""Integration tests for issue.assigned delivery (Plan 05). requests.post is
mocked at apps.api.services.webhooks.assignment.requests.post (dispatch's
own module) — not apps.api.services.webhooks.service, which is a different
mock target used by WebhookService's own delivery path.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


def _insert_webhook(db, **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        tenant_id=1,
        village_id=f"00000001-{overrides.pop('_vid_suffix', '0000000000000001')}",
        name="Test webhook",
        url="https://hooks.example.com/test",
        events=["issue.assigned"],
        is_active=True,
        secret="s3cr3t",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    webhook_id = db.webhooks.insert(**defaults)
    db.commit()
    return webhook_id


def _event(tenant_id=1, **overrides):
    from apps.api.services.webhooks.assignment import AssignmentEvent

    defaults = dict(
        issue_id=1,
        village_id="00000002-0000000000000001",
        issue_type="SUPPORT",
        status="OPEN",
        assignee_type="identity",
        assignee_id=42,
        tenant_id=tenant_id,
        actor_id=7,
    )
    defaults.update(overrides)
    return AssignmentEvent(**defaults)


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_send_delivers_to_matching_webhook_signed(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        _insert_webhook(db, _vid_suffix="00000000000000a1")

        results = await send_issue_assigned_webhooks(db, _event())

        assert len(results) == 1
        assert results[0]["success"] is True
        sent_headers = mock_post.call_args.kwargs["headers"]
        assert "X-Elder-Signature" in sent_headers

        delivery = db(db.webhook_deliveries.event_type == "issue.assigned").select().first()
        assert delivery is not None
        assert delivery.status == "success"
        assert delivery.http_status == 200


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_send_skips_non_matching_webhook(mock_post, app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        _insert_webhook(
            db, _vid_suffix="00000000000000a2", events=["issue.created"]
        )  # not subscribed to issue.assigned

        results = await send_issue_assigned_webhooks(db, _event())

        assert results == []
        mock_post.assert_not_called()


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_send_never_raises_on_delivery_failure(mock_post, app):
    mock_post.side_effect = Exception("connection refused")

    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        _insert_webhook(db, _vid_suffix="00000000000000a3")

        results = await send_issue_assigned_webhooks(db, _event())  # must not raise

        assert len(results) == 1
        assert results[0]["success"] is False

        delivery = db(db.webhook_deliveries.event_type == "issue.assigned").select().first()
        assert delivery.status == "failed"
        assert delivery.error_message is not None
```

- [ ] **Step 2: Run it, verify it fails**

```bash
<verification recipe> tests/unit/test_webhook_assignment_dispatch.py -p no:warnings -q --no-header
```
Expected: FAIL (`send_issue_assigned_webhooks` not defined)

- [ ] **Step 3: Append the dispatch functions to `assignment.py`**

Task 4's matching-only code didn't need `asyncio`, `requests`, or `generate_signature` — the dispatch functions below do. Add `import asyncio` as a new first line, and add a new import group after the existing `from typing import ...` line:

```python
# apps/api/services/webhooks/assignment.py — top of file, updated
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from apps.api.services.webhooks.service import generate_signature

logger = logging.getLogger(__name__)

ASSIGNMENT_EVENT_TYPE = "issue.assigned"
```

(This replaces Task 4's top-of-file import block in full — the rest of the file, `AssignmentEvent` through `build_assignment_payload`, is unchanged.)

Then append the dispatch functions below `build_assignment_payload`:

```python
# apps/api/services/webhooks/assignment.py — appended below build_assignment_payload


def _dispatch_one(db: Any, webhook: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Deliver `payload` to a single webhook synchronously and record the attempt.

    Runs entirely inside asyncio.to_thread (see send_issue_assigned_webhooks)
    — blocking I/O here never touches the event loop. Never raises: any
    failure (network error, non-2xx response) is captured on the
    webhook_deliveries row and returned in the result dict instead of
    propagating, so one webhook's failure can never break another's
    delivery or the caller's fire-and-forget task.
    """
    payload_str = json.dumps(payload, sort_keys=True)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Elder-Webhook/1.2.0",
    }
    if webhook.headers:
        headers.update(json.loads(webhook.headers))
    if webhook.secret:
        headers["X-Elder-Signature"] = generate_signature(webhook.secret, payload_str)

    now = datetime.now(timezone.utc)
    delivery_id = db.webhook_deliveries.insert(
        webhook_id=webhook.id,
        event_type=ASSIGNMENT_EVENT_TYPE,
        request_payload=payload,
        attempt_count=1,
        created_at=now,
    )
    db.commit()

    try:
        response = requests.post(webhook.url, json=payload, headers=headers, timeout=30)
        success = 200 <= response.status_code < 300
        db(db.webhook_deliveries.id == delivery_id).update(
            status="success" if success else "failed",
            http_status=response.status_code,
            response_body=response.text[:1000],
            delivered_at=datetime.now(timezone.utc) if success else None,
        )
        db.commit()
        return {
            "webhook_id": webhook.id,
            "delivery_id": delivery_id,
            "success": success,
            "http_status": response.status_code,
        }
    except Exception as exc:
        db(db.webhook_deliveries.id == delivery_id).update(
            status="failed",
            error_message=str(exc)[:500],
        )
        db.commit()
        logger.warning(
            "issue_assigned_webhook_delivery_failed",
            extra={"webhook_id": webhook.id, "error": str(exc)[:200]},
        )
        return {
            "webhook_id": webhook.id,
            "delivery_id": delivery_id,
            "success": False,
            "error": str(exc),
        }


async def send_issue_assigned_webhooks(
    db: Any, event: AssignmentEvent
) -> List[Dict[str, Any]]:
    """Match `event` against every active webhook in its tenant and deliver
    issue.assigned to each match, HMAC-signed, non-blocking.

    Fire-and-forget: callers wrap this in asyncio.create_task from an async
    route handler right after the assignment-changing DB write commits.
    Every blocking call (DB queries + requests.post) runs inside
    asyncio.to_thread so this never blocks the event loop; a failure in
    matching or in any single delivery is caught and logged rather than
    raised, so it can never fail the issue create/update/intake-submit it
    was triggered by.
    """
    try:
        matched = await asyncio.to_thread(find_matching_webhooks, db, event)
    except Exception as exc:  # pragma: no cover - defensive; matching is pure/cheap
        logger.warning(
            "issue_assigned_webhook_match_failed", extra={"error": str(exc)[:200]}
        )
        return []

    if not matched:
        return []

    payload = build_assignment_payload(event)
    results = []
    for webhook in matched:
        try:
            result = await asyncio.to_thread(_dispatch_one, db, webhook, payload)
        except Exception as exc:  # pragma: no cover - _dispatch_one already catches
            logger.warning(
                "issue_assigned_webhook_dispatch_failed",
                extra={"webhook_id": webhook.id, "error": str(exc)[:200]},
            )
            result = {"webhook_id": webhook.id, "success": False, "error": str(exc)}
        results.append(result)
    return results
```

- [ ] **Step 4: Run it, verify it passes**

```bash
<verification recipe> tests/unit/test_webhook_assignment_dispatch.py -p no:warnings -q --no-header
```
Expected: PASS (3 passed)

- [ ] **Step 5: Lint + format**

```bash
flake8 apps/api/services/webhooks/assignment.py tests/unit/test_webhook_assignment_dispatch.py
black --check apps/api/services/webhooks/assignment.py tests/unit/test_webhook_assignment_dispatch.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/services/webhooks/assignment.py tests/unit/test_webhook_assignment_dispatch.py
git commit -m "feat(webhooks): non-blocking HMAC-signed issue.assigned dispatch"
```

---

## Task 6: Wire `issue.assigned` into `create_issue`

**Files:**
- Modify: `apps/api/modules/issues/routes/issues.py:328-373` (the `create_issue` handler)
- Test: `tests/unit/test_issues_assigned_webhook_create.py`

**Interfaces:**
- Consumes: `AssignmentEvent`, `send_issue_assigned_webhooks` from Task 5.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_issues_assigned_webhook_create.py
"""issue.assigned must fire when an issue is created with an assignee, and
must NOT fire when created without one."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


def _insert_webhook(db, tenant_id=1):
    now = datetime.now(timezone.utc)
    webhook_id = db.webhooks.insert(
        tenant_id=tenant_id,
        village_id="00000001-0000000000000b01",
        name="Assign watcher",
        url="https://hooks.example.com/assign",
        events=["issue.assigned"],
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return webhook_id


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_create_issue_with_assignee_fires_webhook(
    mock_get_user, mock_post, async_client, generate_token, app
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        now = datetime.now(timezone.utc)
        org_id = db.organizations.insert(
            name="Org", tenant_id=1, created_at=now, updated_at=now
        )
        db.commit()
        _insert_webhook(db)

    resp = await async_client.post(
        "/api/v1/issues",
        json={
            "title": "New ticket",
            "organization_id": org_id,
            "assignee_id": 1,
            "assignee_type": "identity",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]

    # asyncio.create_task fire-and-forget: give the event loop a tick.
    import asyncio

    await asyncio.sleep(0.1)

    async with app.app_context():
        db = current_app.db
        delivery = (
            db(db.webhook_deliveries.event_type == "issue.assigned").select().first()
        )
        assert delivery is not None


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_create_issue_without_assignee_does_not_fire(
    mock_get_user, mock_post, async_client, generate_token, app
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        now = datetime.now(timezone.utc)
        org_id = db.organizations.insert(
            name="Org2", tenant_id=1, created_at=now, updated_at=now
        )
        db.commit()
        _insert_webhook(db)

    resp = await async_client.post(
        "/api/v1/issues",
        json={"title": "Unassigned ticket", "organization_id": org_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201

    import asyncio

    await asyncio.sleep(0.1)
    mock_post.assert_not_called()
```

- [ ] **Step 2: Run it, verify it fails**

```bash
<verification recipe> tests/unit/test_issues_assigned_webhook_create.py -p no:warnings -q --no-header
```
Expected: FAIL (no delivery row is ever created — `create_issue` doesn't fire the event yet)

- [ ] **Step 3: Wire the dispatch into `create_issue`**

Add the import near the top of `apps/api/modules/issues/routes/issues.py` (alongside the existing `from shared.webhooks import send_issue_created_webhooks`):

```python
from apps.api.services.webhooks.assignment import AssignmentEvent, send_issue_assigned_webhooks
```

Then, in `create_issue` (`apps/api/modules/issues/routes/issues.py:356-370`), add the new dispatch immediately after the existing `issue.created` dispatch:

```python
    issue = await run_in_threadpool(create)

    # Send issue created webhooks asynchronously (fire and forget)
    if issue.resource_id and issue.resource_type == "organization":
        asyncio.create_task(
            send_issue_created_webhooks(
                db=db,
                issue_id=issue.id,
                issue_title=issue.title,
                issue_type=issue.issue_type,
                is_incident=issue.is_incident if hasattr(issue, "is_incident") else 0,
                organization_id=issue.resource_id,
                web_url_base=current_app.config.get("WEB_URL", "http://localhost:3000"),
            )
        )

    # Send issue.assigned webhooks asynchronously (fire and forget) when the
    # issue was created with an assignee already set.
    if issue.assignee_id is not None:
        asyncio.create_task(
            send_issue_assigned_webhooks(
                db,
                AssignmentEvent(
                    issue_id=issue.id,
                    village_id=issue.village_id,
                    issue_type=issue.issue_type,
                    status=issue.status,
                    assignee_type=issue.assignee_type,
                    assignee_id=issue.assignee_id,
                    tenant_id=tenant_id,
                    actor_id=current_user_id,
                ),
            )
        )

    issue_dto = from_pydal_row(issue, IssueDTO)
    return jsonify(asdict(issue_dto)), 201
```

(`tenant_id` and `current_user_id` are both already in scope at this point in `create_issue` — `tenant_id` from line 290, `current_user_id` from line 326.)

- [ ] **Step 4: Run it, verify it passes**

```bash
<verification recipe> tests/unit/test_issues_assigned_webhook_create.py -p no:warnings -q --no-header
```
Expected: PASS (2 passed)

- [ ] **Step 5: Lint + format**

```bash
flake8 apps/api/modules/issues/routes/issues.py tests/unit/test_issues_assigned_webhook_create.py
black --check apps/api/modules/issues/routes/issues.py tests/unit/test_issues_assigned_webhook_create.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/modules/issues/routes/issues.py tests/unit/test_issues_assigned_webhook_create.py
git commit -m "feat(webhooks): fire issue.assigned from create_issue when assignee is set"
```

---

## Task 7: Wire `issue.assigned` into `update_issue` (assignee-change diff)

**Files:**
- Modify: `apps/api/modules/issues/routes/issues.py:413-538` (the `update_issue` handler)
- Test: `tests/unit/test_issues_assigned_webhook_update.py`

**Interfaces:**
- Consumes: `AssignmentEvent`, `send_issue_assigned_webhooks` from Task 5 (already imported into this file by Task 6).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_issues_assigned_webhook_update.py
"""issue.assigned must fire on PATCH only when the assignee actually
changes — not when the same assignee is resent, and not when assignee_id is
absent from the body."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


def _setup(db, tenant_id=1, initial_assignee_id=None):
    now = datetime.now(timezone.utc)
    org_id = db.organizations.insert(
        name="Org", tenant_id=tenant_id, created_at=now, updated_at=now
    )
    issue_id = db.issues.insert(
        title="Ticket",
        status="OPEN",
        priority="MEDIUM",
        issue_type="OTHER",
        reporter_id=None,
        assignee_id=initial_assignee_id,
        assignee_type="identity" if initial_assignee_id else None,
        resource_type="organization",
        resource_id=org_id,
        is_incident=0,
        tenant_id=tenant_id,
        created_at=now,
        updated_at=now,
    )
    webhook_id = db.webhooks.insert(
        tenant_id=tenant_id,
        village_id="00000001-0000000000000c01",
        name="Assign watcher",
        url="https://hooks.example.com/assign",
        events=["issue.assigned"],
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return issue_id, webhook_id


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_patch_new_assignee_fires_webhook(
    mock_get_user, mock_post, async_client, generate_token, app
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        issue_id, _ = _setup(db, initial_assignee_id=None)

    resp = await async_client.patch(
        f"/api/v1/issues/{issue_id}",
        json={"assignee_id": 5, "assignee_type": "identity"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    await asyncio.sleep(0.1)

    async with app.app_context():
        db = current_app.db
        delivery = (
            db(db.webhook_deliveries.event_type == "issue.assigned").select().first()
        )
        assert delivery is not None


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_patch_same_assignee_does_not_fire(
    mock_get_user, mock_post, async_client, generate_token, app
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        issue_id, _ = _setup(db, initial_assignee_id=5)

    resp = await async_client.patch(
        f"/api/v1/issues/{issue_id}",
        json={"assignee_id": 5, "assignee_type": "identity"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    await asyncio.sleep(0.1)
    mock_post.assert_not_called()


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
@patch("apps.api.auth.decorators.get_current_user")
async def test_patch_without_assignee_field_does_not_fire(
    mock_get_user, mock_post, async_client, generate_token, app
):
    mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
    token = generate_token(tenant_id=1, scopes=["issues:write"])

    async with app.app_context():
        db = current_app.db
        issue_id, _ = _setup(db, initial_assignee_id=5)

    resp = await async_client.patch(
        f"/api/v1/issues/{issue_id}",
        json={"title": "Renamed, no assignee touch"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    await asyncio.sleep(0.1)
    mock_post.assert_not_called()
```

- [ ] **Step 2: Run it, verify it fails**

```bash
<verification recipe> tests/unit/test_issues_assigned_webhook_update.py -p no:warnings -q --no-header
```
Expected: FAIL (test 1 fails — no delivery row created yet)

- [ ] **Step 3: Wire the dispatch into `update_issue`**

Capture `current_user_id` near the top of `update_issue` (`apps/api/modules/issues/routes/issues.py:445-449`, right after the tenant check):

```python
    db = current_app.db

    tenant_id = _tenant_id()
    if not tenant_id:
        return jsonify({"error": "Tenant not found"}), 403

    current_user_id = g.current_user.id
```

Inside the `update()` closure (`apps/api/modules/issues/routes/issues.py:483-530`), capture the pre-update assignee, compute `assignee_changed`, and return it as a fourth tuple element on every return path:

```python
    def update():
        # Check if issue exists and belongs to caller's tenant
        issue = (
            db((db.issues.id == id) & (db.issues.tenant_id == tenant_id))
            .select()
            .first()
        )
        if not issue:
            return None, "Issue not found", 404, False

        old_assignee_id = issue.assignee_id
        old_assignee_type = issue.assignee_type

        # Build update fields
        update_fields = {}

        if body.title is not None:
            update_fields["title"] = body.title
        if body.description is not None:
            update_fields["description"] = body.description
        if body.status is not None:
            update_fields["status"] = body.status.upper()
            # Set closed_at if closing
            if body.status.upper() in ("CLOSED", "RESOLVED"):
                update_fields["closed_at"] = datetime.now(timezone.utc)
        if body.priority is not None:
            update_fields["priority"] = body.priority.upper()
        if body.assignee_id is not None:
            update_fields["assignee_id"] = body.assignee_id
            update_fields["assignee_type"] = assignee_type_resolved
        if body.organization_id is not None:
            update_fields["resource_id"] = body.organization_id
            update_fields["resource_type"] = "organization"
        if body.is_incident is not None:
            update_fields["is_incident"] = body.is_incident
        if body.channel is not None:
            update_fields["channel"] = body.channel
        if body.category is not None:
            update_fields["category"] = body.category
        if body.metadata is not None:
            update_fields["metadata"] = body.metadata
        if body.parent_issue_id is not None:
            update_fields["parent_issue_id"] = body.parent_issue_id

        # Update issue (re-scoped to tenant for defense-in-depth)
        db((db.issues.id == id) & (db.issues.tenant_id == tenant_id)).update(
            **update_fields
        )
        db.commit()

        assignee_changed = body.assignee_id is not None and (
            old_assignee_id != body.assignee_id
            or old_assignee_type != assignee_type_resolved
        )

        return get_tenant_scoped_issue(db, id, tenant_id), None, None, assignee_changed

    result, error, status, assignee_changed = await run_in_threadpool(update)

    if error:
        return jsonify({"error": error}), status

    if assignee_changed:
        asyncio.create_task(
            send_issue_assigned_webhooks(
                db,
                AssignmentEvent(
                    issue_id=result.id,
                    village_id=result.village_id,
                    issue_type=result.issue_type,
                    status=result.status,
                    assignee_type=result.assignee_type,
                    assignee_id=result.assignee_id,
                    tenant_id=tenant_id,
                    actor_id=current_user_id,
                ),
            )
        )

    issue_dto = from_pydal_row(result, IssueDTO)
    return jsonify(asdict(issue_dto)), 200
```

- [ ] **Step 4: Run it, verify it passes**

```bash
<verification recipe> tests/unit/test_issues_assigned_webhook_update.py -p no:warnings -q --no-header
```
Expected: PASS (3 passed)

- [ ] **Step 5: Lint + format**

```bash
flake8 apps/api/modules/issues/routes/issues.py tests/unit/test_issues_assigned_webhook_update.py
black --check apps/api/modules/issues/routes/issues.py tests/unit/test_issues_assigned_webhook_update.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/modules/issues/routes/issues.py tests/unit/test_issues_assigned_webhook_update.py
git commit -m "feat(webhooks): fire issue.assigned from update_issue on assignee change"
```

---

## Task 8: Wire `issue.assigned` into intake-form default-assign

**Files:**
- Modify: `apps/api/modules/helpdesk/services/intake_submit.py:173-254` (`create_support_issue_from_form` — return type changes from village_id string to the issue row)
- Modify: `apps/api/modules/helpdesk/routes/intake_forms.py:733-831` (`submit_public_intake_form`)
- Test: `tests/unit/test_intake_assigned_webhook.py`

**Interfaces:**
- Consumes: `AssignmentEvent`, `send_issue_assigned_webhooks` from Task 5.
- Produces (breaking change to an internal function — its only caller is updated in the same task): `create_support_issue_from_form(...)` now returns the created issue's full pydal Row (was: `str` village_id). `intake_forms.py`'s only caller (`submit_public_intake_form`) is updated accordingly; no other caller exists (verified: `grep -rn create_support_issue_from_form` finds exactly one production call site).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_intake_assigned_webhook.py
"""issue.assigned must fire from a public intake-form submission when the
form has a default assignee configured, using the resolved customer_contact
as actor (there is no authenticated user on this path)."""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


def _setup_form_and_webhook(db, tenant_id=1, default_assignee_id=None):
    now = datetime.now(timezone.utc)
    org_id = db.organizations.insert(
        name="Org", tenant_id=tenant_id, created_at=now, updated_at=now
    )
    form_id = db.hd_intake_forms.insert(
        tenant_id=tenant_id,
        village_id="00000001-0000000000000d01",
        name="Support Form",
        slug="support-form-assign-test",
        fields=json.dumps(
            [{"id": "email", "label": "Email", "type": "email", "required": True}]
        ),
        issue_type="support",
        default_assignee_type="identity" if default_assignee_id else None,
        default_assignee_id=default_assignee_id,
        organization_id=org_id,
        is_public=True,
        captcha_required=False,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.webhooks.insert(
        tenant_id=tenant_id,
        village_id="00000001-0000000000000d02",
        name="Assign watcher",
        url="https://hooks.example.com/assign",
        events=["issue.assigned"],
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return form_id


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_submit_with_default_assignee_fires_webhook(mock_post, async_client, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")

    async with app.app_context():
        db = current_app.db
        _setup_form_and_webhook(db, default_assignee_id=5)

    resp = await async_client.post(
        "/api/v1/intake/support-form-assign-test/submit",
        json={"fields": {"email": "user@example.com"}},
    )
    assert resp.status_code == 201, (await resp.get_data()).decode()[:300]

    await asyncio.sleep(0.1)

    async with app.app_context():
        db = current_app.db
        delivery = (
            db(db.webhook_deliveries.event_type == "issue.assigned").select().first()
        )
        assert delivery is not None


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_submit_without_default_assignee_does_not_fire(mock_post, async_client, app):
    async with app.app_context():
        db = current_app.db
        _setup_form_and_webhook(db, default_assignee_id=None)

        # Use a distinct slug — form slugs are globally unique.
        db(db.hd_intake_forms.slug == "support-form-assign-test").update(
            slug="support-form-no-assign-test"
        )
        db.commit()

    resp = await async_client.post(
        "/api/v1/intake/support-form-no-assign-test/submit",
        json={"fields": {"email": "user2@example.com"}},
    )
    assert resp.status_code == 201

    await asyncio.sleep(0.1)
    mock_post.assert_not_called()
```

- [ ] **Step 2: Run it, verify it fails**

```bash
<verification recipe> tests/unit/test_intake_assigned_webhook.py -p no:warnings -q --no-header
```
Expected: FAIL (test 1 fails — no delivery row created yet)

- [ ] **Step 3: Change `create_support_issue_from_form`'s return type**

In `apps/api/modules/helpdesk/services/intake_submit.py`, change the end of `create_support_issue_from_form` (lines 251-254) and its docstring's `Returns:` line:

```python
    issue_id = db.issues.insert(**insert_data)
    db.commit()

    return db(db.issues.id == issue_id).select().first()
```

And update the docstring (originally "Returns: The created issue's village_id."):

```python
    Returns:
        The created issue row (all columns, including village_id, id,
        assignee_id, assignee_type, issue_type, status, tenant_id — the
        caller uses these to decide whether to fire an issue.assigned
        webhook, since this function itself cannot: it runs inside
        run_in_threadpool, off the event loop, where asyncio.create_task
        has no running loop to attach to).
```

- [ ] **Step 4: Wire the dispatch into `submit_public_intake_form`**

Add `import asyncio` to the top of `apps/api/modules/helpdesk/routes/intake_forms.py` (alongside the existing `import json`), and add the `AssignmentEvent`/`send_issue_assigned_webhooks` import alongside the existing `intake_submit` import:

```python
import asyncio
import json
import logging
```

```python
from apps.api.modules.helpdesk.services.intake_submit import (
    ContactResolutionError,
    create_support_issue_from_form,
    upsert_customer_contact,
)
from apps.api.services.webhooks.assignment import AssignmentEvent, send_issue_assigned_webhooks
```

Then replace the end of `submit_public_intake_form` (`apps/api/modules/helpdesk/routes/intake_forms.py:813-831`):

```python
    def submit():
        contact_id = upsert_customer_contact(
            db, tenant_id, email, details, redis_client
        )
        return create_support_issue_from_form(
            db, form_row, validated, contact_id, redis_client
        )

    try:
        issue = await run_in_threadpool(submit)
    except ContactResolutionError as exc:
        logger.error(f"Intake form contact resolution failed for slug={slug}: {exc}")
        return ApiResponse.conflict("Unable to resolve contact; please retry")
    except ValueError as exc:
        logger.error(f"Intake form submit failed for slug={slug}: {exc}")
        return ApiResponse.error("Unable to create support issue", 400)

    if issue.assignee_id is not None:
        # No authenticated user exists on this public path — the resolved
        # customer_contact (also the issue's reporter_id) is the closest
        # analog to "actor" for a system-generated default-assign.
        asyncio.create_task(
            send_issue_assigned_webhooks(
                db,
                AssignmentEvent(
                    issue_id=issue.id,
                    village_id=issue.village_id,
                    issue_type=issue.issue_type,
                    status=issue.status,
                    assignee_type=issue.assignee_type,
                    assignee_id=issue.assignee_id,
                    tenant_id=issue.tenant_id,
                    actor_id=issue.reporter_id,
                ),
            )
        )

    return jsonify({"status": "created", "reference": issue.village_id}), 201
```

- [ ] **Step 5: Run it, verify it passes**

```bash
<verification recipe> tests/unit/test_intake_assigned_webhook.py -p no:warnings -q --no-header
```
Expected: PASS (2 passed)

Also re-run `tests/unit/test_intake_forms.py` (unmodified file, but its behavior depends on `create_support_issue_from_form`'s return type change):

```bash
<verification recipe> tests/unit/test_intake_forms.py -p no:warnings -q --no-header
```
Expected: PASS, unchanged pass count (that file only asserts on the HTTP response's `reference` field and the resulting DB row looked up by village_id — never on `create_support_issue_from_form`'s direct return value — so the type change is invisible to it).

- [ ] **Step 6: Lint + format**

```bash
flake8 apps/api/modules/helpdesk/services/intake_submit.py apps/api/modules/helpdesk/routes/intake_forms.py tests/unit/test_intake_assigned_webhook.py
black --check apps/api/modules/helpdesk/services/intake_submit.py apps/api/modules/helpdesk/routes/intake_forms.py tests/unit/test_intake_assigned_webhook.py
```

- [ ] **Step 7: Commit**

```bash
git add apps/api/modules/helpdesk/services/intake_submit.py apps/api/modules/helpdesk/routes/intake_forms.py tests/unit/test_intake_assigned_webhook.py
git commit -m "feat(webhooks): fire issue.assigned from intake-form default-assign"
```

---

## Task 9: Integration test matrix — filters, tenant isolation, signature correctness

**Files:**
- Create: `tests/unit/test_issue_assigned_webhook_matrix.py`

**Interfaces:**
- Consumes: `AssignmentEvent`, `send_issue_assigned_webhooks` from Task 5. This task adds no production code — it is a dedicated cross-cutting test file exercising the matching/dispatch service directly (bypassing HTTP and the three wiring points, which already have their own tests from Tasks 6-8) against combinations the per-task tests don't each individually cover: multiple simultaneous webhook configs per tenant, tenant isolation, and signature correctness end-to-end.

- [ ] **Step 1: Write the matrix tests**

```python
# tests/unit/test_issue_assigned_webhook_matrix.py
"""Cross-cutting matching/tenant-isolation/signature matrix for
issue.assigned (Plan 05, spec §7). Exercises send_issue_assigned_webhooks
directly against multiple simultaneous webhook configs — the scenario none
of Tasks 6-8's single-webhook wiring tests cover on their own.
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from quart import current_app


def _insert_webhook(db, tenant_id, vid_suffix, **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        tenant_id=tenant_id,
        village_id=f"{tenant_id:08x}-{vid_suffix}",
        name=f"Webhook {vid_suffix}",
        url=f"https://hooks.example.com/{vid_suffix}",
        events=["issue.assigned"],
        is_active=True,
        secret="matrix-secret",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    webhook_id = db.webhooks.insert(**defaults)
    db.commit()
    return webhook_id


def _event(**overrides):
    from apps.api.services.webhooks.assignment import AssignmentEvent

    defaults = dict(
        issue_id=1,
        village_id="00000009-0000000000000001",
        issue_type="SUPPORT",
        status="OPEN",
        assignee_type="identity",
        assignee_id=42,
        tenant_id=1,
        actor_id=7,
    )
    defaults.update(overrides)
    return AssignmentEvent(**defaults)


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_org_unit_only_filter_matches_org_unit_assignment(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        _insert_webhook(
            db, 1, "0000000000000e01", filter_assignee_type="org_unit", filter_assignee_id=10
        )

        results = await send_issue_assigned_webhooks(
            db, _event(assignee_type="org_unit", assignee_id=10)
        )
        assert len(results) == 1 and results[0]["success"] is True


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_issue_type_and_assignee_combo_filter(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        _insert_webhook(
            db,
            1,
            "0000000000000e02",
            filter_issue_type="SUPPORT",
            filter_assignee_type="identity",
            filter_assignee_id=42,
        )

        # Matches both filters -> delivered
        results = await send_issue_assigned_webhooks(db, _event())
        assert len(results) == 1

        # issue_type mismatches -> not delivered
        mock_post.reset_mock()
        results = await send_issue_assigned_webhooks(db, _event(issue_type="BUG"))
        assert results == []
        mock_post.assert_not_called()


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_empty_filter_matches_all_assignments_in_tenant(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        _insert_webhook(db, 1, "0000000000000e03")  # no filters set

        results_a = await send_issue_assigned_webhooks(
            db, _event(issue_type="BUG", assignee_type="org_unit", assignee_id=999)
        )
        results_b = await send_issue_assigned_webhooks(
            db, _event(issue_type="SUPPORT", assignee_type="identity", assignee_id=1)
        )
        assert len(results_a) == 1
        assert len(results_b) == 1


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_multiple_configs_each_fire_independently(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        # (a) any issue assigned to org-unit 10 -> webhook A
        _insert_webhook(
            db, 1, "0000000000000e04", filter_assignee_type="org_unit", filter_assignee_id=10
        )
        # (b) issue_type=support assigned to identity 42 -> webhook B
        _insert_webhook(
            db,
            1,
            "0000000000000e05",
            filter_issue_type="SUPPORT",
            filter_assignee_type="identity",
            filter_assignee_id=42,
        )

        # An assignment matching ONLY (b) fires exactly one webhook.
        results = await send_issue_assigned_webhooks(
            db, _event(issue_type="SUPPORT", assignee_type="identity", assignee_id=42)
        )
        assert len(results) == 1


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_tenant_isolation_webhook_never_fires_cross_tenant(mock_post, app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        # Webhook lives in tenant 2.
        _insert_webhook(db, 2, "0000000000000e06")

        # Assignment event is for tenant 1 — must never match tenant 2's webhook.
        results = await send_issue_assigned_webhooks(db, _event(tenant_id=1))

        assert results == []
        mock_post.assert_not_called()


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_signature_matches_expected_hmac(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    async with app.app_context():
        from apps.api.services.webhooks.assignment import (
            build_assignment_payload,
            send_issue_assigned_webhooks,
        )

        db = current_app.db
        _insert_webhook(db, 1, "0000000000000e07", secret="known-secret")

        event = _event()
        await send_issue_assigned_webhooks(db, event)

        sent_kwargs = mock_post.call_args.kwargs
        sent_payload = sent_kwargs["json"]
        sent_signature = sent_kwargs["headers"]["X-Elder-Signature"]

        expected_signature = hmac.new(
            b"known-secret",
            json.dumps(sent_payload, sort_keys=True).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert sent_signature == expected_signature


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_inactive_webhook_never_fires(mock_post, app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        _insert_webhook(db, 1, "0000000000000e08", is_active=False)

        results = await send_issue_assigned_webhooks(db, _event())

        assert results == []
        mock_post.assert_not_called()
```

- [ ] **Step 2: Run it, verify it currently passes** (this task is pure test coverage over already-implemented Task 5 behavior — nothing new to implement; if any case fails, it's a real gap in Task 4/5's matching or dispatch logic that must be fixed before proceeding)

```bash
<verification recipe> tests/unit/test_issue_assigned_webhook_matrix.py -p no:warnings -q --no-header
```
Expected: PASS (7 passed). If any test fails, fix the matching/dispatch code in `apps/api/services/webhooks/assignment.py` (Tasks 4/5) — do not weaken the test to make it pass.

- [ ] **Step 3: Lint + format**

```bash
flake8 tests/unit/test_issue_assigned_webhook_matrix.py
black --check tests/unit/test_issue_assigned_webhook_matrix.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_issue_assigned_webhook_matrix.py
git commit -m "test(webhooks): issue.assigned filter/tenant-isolation/signature matrix"
```

---

## Follow-ups (explicitly out of scope for this plan)

- **Converge the two webhook systems.** `shared/webhooks/issue_webhooks.py::send_issue_created_webhooks` (queries `alert_configurations`, no HMAC, blocking `requests.post` on the event loop) and the native `webhooks`/`webhook_deliveries` system this plan builds on are two independently-configured webhook surfaces for the same product. Not touched here.
- **`notification_rules` schema drift.** The model (`events`/`config_json`/`enabled`) still doesn't match its migration-011 schema (`event_types`/`conditions`/`channels`/`is_active`) — `broadcast_event`'s notification-rules half is still broken after this plan. Not on any path this plan's feature (assignment webhooks) touches.
