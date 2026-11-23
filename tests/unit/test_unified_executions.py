"""Unit tests for the unified GET /api/v1/streams/executions audit log endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from quart import Quart, g

from apps.api.modules.streams.routes.streams import bp as streams_bp


class _Expr:
    """Minimal query expression supporting ==, belongs(), &, |, and ~orderby."""

    def __init__(self, predicate):
        self._predicate = predicate

    def __call__(self, row: Any) -> bool:
        return bool(self._predicate(row))

    def __and__(self, other: _Expr) -> _Expr:
        return _Expr(lambda row: self(row) and other(row))

    def __or__(self, other: _Expr) -> _Expr:
        return _Expr(lambda row: self(row) or other(row))


class _OrderSpec:
    """Minimal orderby specifier supporting ~field for descending sort."""

    def __init__(self, field_name: str, descending: bool = False):
        self.field_name = field_name
        self.descending = descending


class _Field:
    """Minimal table column descriptor mimicking penguin-dal / PyDAL expressions."""

    def __init__(self, name: str):
        self.name = name

    def __eq__(self, value: Any) -> _Expr:  # type: ignore[override]
        return _Expr(lambda row: getattr(row, self.name, None) == value)

    def belongs(self, values: Any) -> _Expr:
        allowed = set(values)
        return _Expr(lambda row: getattr(row, self.name, None) in allowed)

    def __invert__(self) -> _OrderSpec:
        return _OrderSpec(self.name, descending=True)


class _FakeTable:
    """In-memory table with PyDAL-compatible field descriptors."""

    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows: list[SimpleNamespace] = [SimpleNamespace(**r) for r in (rows or [])]

    def __getattr__(self, name: str) -> _Field:
        return _Field(name)


class _FakeSet:
    """Result of db(query) supporting .select() and .count()."""

    def __init__(self, table: _FakeTable, expr: _Expr):
        self._table = table
        self._expr = expr

    def _matched(self) -> list[SimpleNamespace]:
        return [row for row in self._table.rows if self._expr(row)]

    def select(
        self, *_fields: Any, orderby: Any = None, limitby: Any = None
    ) -> list[SimpleNamespace]:
        rows = self._matched()
        if isinstance(orderby, _OrderSpec):
            rows.sort(
                key=lambda r: (
                    getattr(r, orderby.field_name, None)
                    or datetime.min.replace(tzinfo=UTC)
                ),
                reverse=orderby.descending,
            )
        if limitby is not None:
            start, end = limitby
            rows = rows[start:end]
        return rows

    def count(self) -> int:
        return len(self._matched())


class _TableBoundExpr(_Expr):
    """Expression that remembers its owning _FakeTable for db(expr) dispatch."""

    def __init__(self, table: _FakeTable, predicate):
        super().__init__(predicate)
        self.table = table

    def __and__(self, other: _Expr) -> _TableBoundExpr:
        return _TableBoundExpr(self.table, lambda row: self(row) and other(row))

    def __or__(self, other: _Expr) -> _TableBoundExpr:
        return _TableBoundExpr(self.table, lambda row: self(row) or other(row))


class _BoundField(_Field):
    """Field bound to a specific _FakeTable instance."""

    def __init__(self, table: _FakeTable, name: str):
        super().__init__(name)
        self.table = table

    def __eq__(self, value: Any) -> _TableBoundExpr:  # type: ignore[override]
        return _TableBoundExpr(
            self.table, lambda row: getattr(row, self.name, None) == value
        )

    def belongs(self, values: Any) -> _TableBoundExpr:
        allowed = set(values)
        return _TableBoundExpr(
            self.table, lambda row: getattr(row, self.name, None) in allowed
        )


class _BoundFakeTable(_FakeTable):
    """In-memory table producing table-bound query expressions."""

    def __getattr__(self, name: str) -> _BoundField:
        return _BoundField(self, name)


class FakeDB:
    """Queryable in-memory database stub supporting optional partial schemas."""

    def __init__(self, **tables: list[dict[str, Any]]):
        for name, rows in tables.items():
            setattr(self, name, _BoundFakeTable(rows))

    def __call__(self, expr: _TableBoundExpr) -> _FakeSet:
        return _FakeSet(expr.table, expr)


def _build_app(db: FakeDB, tenant_id: int = 10, identity_id: int = 100) -> Quart:
    """Create a minimal Quart test app with streams blueprint and authenticated claims."""
    app = Quart(__name__)
    app.db = db  # type: ignore[attr-defined]

    @app.before_request
    async def _inject_claims():
        g.claims = {
            "sub": f"user-{identity_id}",
            "user_id": identity_id,
            "identity_id": identity_id,
            "tenant": str(tenant_id),
            "scope": ["streams:read", "streams:write"],
        }
        g.user = g.claims
        g.user_id = identity_id
        g.tenant_id = tenant_id

    app.register_blueprint(streams_bp, url_prefix="/api/v1/streams")
    return app


@pytest.fixture(autouse=True)
def _mock_authenticated_user():
    """Patch get_current_user in auth decorators so unit tests run without JWT setup."""
    fake_user = SimpleNamespace(
        id=100,
        username="tester",
        is_superuser=False,
    )
    with patch("apps.api.auth.decorators.get_current_user", return_value=fake_user):
        yield


def _seed_full_db() -> FakeDB:
    """Seed tenant 10 and tenant 20 records across playbooks, CodeRuns, and webhooks."""
    now = datetime(2026, 11, 23, 18, 0, 0, tzinfo=UTC)
    return FakeDB(
        stream_playbooks=[
            {
                "id": 1,
                "tenant_id": 10,
                "name": "Incident Triage",
                "owner_identity_id": 100,
                "is_public": True,
            },
            {
                "id": 2,
                "tenant_id": 10,
                "name": "Nightly Sync",
                "owner_identity_id": 100,
                "is_public": False,
            },
            {
                "id": 99,
                "tenant_id": 20,
                "name": "Other Tenant Playbook",
                "owner_identity_id": 200,
                "is_public": True,
            },
        ],
        stream_shares=[],
        stream_executions=[
            {
                "id": 11,
                "tenant_id": 10,
                "playbook_id": 1,
                "execution_id": "pb-exec-001",
                "status": "completed",
                "trigger_type": "manual",
                "input_json": {"a": 1},
                "output_json": {"ok": True},
                "error_message": None,
                "started_at": now - timedelta(minutes=10),
                "completed_at": now - timedelta(minutes=9),
                "duration_ms": 60000,
            },
            {
                "id": 12,
                "tenant_id": 10,
                "playbook_id": 2,
                "execution_id": "pb-exec-002",
                "status": "failed",
                "trigger_type": "schedule",
                "input_json": None,
                "output_json": None,
                "error_message": "Upstream timeout",
                "started_at": now - timedelta(minutes=2),
                "completed_at": now - timedelta(minutes=1),
                "duration_ms": 1500,
            },
            {
                "id": 19,
                "tenant_id": 20,
                "playbook_id": 99,
                "execution_id": "pb-other-tenant",
                "status": "completed",
                "trigger_type": "manual",
                "input_json": None,
                "output_json": None,
                "error_message": None,
                "started_at": now,
                "completed_at": now,
                "duration_ms": 100,
            },
        ],
        run_functions=[
            {
                "id": 50,
                "tenant_id": 10,
                "name": "Enrich Alert Payload",
                "runtime": "python",
            },
            {
                "id": 51,
                "tenant_id": 20,
                "name": "Other Tenant Function",
                "runtime": "rust",
            },
        ],
        run_executions=[
            {
                "id": 21,
                "tenant_id": 10,
                "execution_id": "cr-exec-001",
                "function_id": 50,
                "status": "completed",
                "trigger_type": "stream",
                "started_at": now - timedelta(minutes=5),
                "completed_at": now - timedelta(minutes=4, seconds=58),
                "created_at": now - timedelta(minutes=5),
                "duration_ms": 2000,
                "error_message": None,
            },
            {
                "id": 22,
                "tenant_id": 10,
                "execution_id": "cr-exec-002",
                "function_id": 50,
                "status": "timed_out",
                "trigger_type": "manual",
                "started_at": now - timedelta(minutes=1),
                "completed_at": now,
                "created_at": now - timedelta(minutes=1),
                "duration_ms": 300000,
                "error_message": "Exceeded 300s deadline",
            },
            {
                "id": 29,
                "tenant_id": 20,
                "execution_id": "cr-other-tenant",
                "function_id": 51,
                "status": "completed",
                "trigger_type": "manual",
                "started_at": now,
                "completed_at": now,
                "created_at": now,
                "duration_ms": 50,
                "error_message": None,
            },
        ],
        webhooks=[
            {
                "id": 70,
                "tenant_id": 10,
                "name": "PagerDuty Escalation",
                "url": "https://events.pagerduty.example/hook",
            },
            {
                "id": 71,
                "tenant_id": 20,
                "name": "Other Tenant Webhook",
                "url": "https://other.example/hook",
            },
        ],
        webhook_deliveries=[
            {
                "id": 31,
                "webhook_id": 70,
                "event_type": "issue.assigned",
                "status": "success",
                "http_status": 200,
                "error_message": None,
                "delivered_at": now - timedelta(minutes=3),
                "created_at": now - timedelta(minutes=3),
            },
            {
                "id": 32,
                "webhook_id": 70,
                "event_type": "incident.created",
                "status": "failed",
                "http_status": 502,
                "error_message": "Bad Gateway",
                "delivered_at": now - timedelta(seconds=30),
                "created_at": now - timedelta(seconds=30),
            },
            {
                "id": 39,
                "webhook_id": 71,
                "event_type": "issue.assigned",
                "status": "success",
                "http_status": 200,
                "error_message": None,
                "delivered_at": now,
                "created_at": now,
            },
        ],
    )


class TestUnifiedExecutionsEndpoint:
    """Verify centralized, filterable GET /api/v1/streams/executions behavior."""

    @pytest.mark.asyncio
    async def test_aggregates_all_sources_newest_first_with_tenant_isolation(
        self,
    ) -> None:
        app = _build_app(_seed_full_db(), tenant_id=10, identity_id=100)
        client = app.test_client()

        resp = await client.get("/api/v1/streams/executions")
        assert resp.status_code == 200
        body = await resp.get_json()

        assert body["total"] == 6
        assert body["page"] == 1
        assert body["per_page"] == 50
        assert body["pages"] == 1
        assert body["counts_by_source"] == {"playbook": 2, "coderun": 2, "webhook": 2}
        assert body["executions"] == body["items"] == body["data"]

        # Newest-first ordering: wh-32 (-30s), cr-exec-002 (-1m), pb-exec-002 (-2m),
        # wh-31 (-3m), cr-exec-001 (-5m), pb-exec-001 (-10m)
        exec_ids = [item["execution_id"] for item in body["executions"]]
        assert exec_ids == [
            "wh-32",
            "cr-exec-002",
            "pb-exec-002",
            "wh-31",
            "cr-exec-001",
            "pb-exec-001",
        ]

        # Ensure other tenant rows never leaked
        assert "pb-other-tenant" not in exec_ids
        assert "cr-other-tenant" not in exec_ids
        assert "wh-39" not in exec_ids

        # Verify normalized fields per source_type
        by_id = {item["execution_id"]: item for item in body["executions"]}

        pb = by_id["pb-exec-001"]
        assert pb["source_type"] == "playbook"
        assert pb["source_name"] == "Incident Triage"
        assert pb["stream_name"] == "Incident Triage"
        assert pb["playbook_id"] == 1
        assert pb["duration_ms"] == 60000
        assert pb["trigger_type"] == "manual"

        cr = by_id["cr-exec-001"]
        assert cr["source_type"] == "coderun"
        assert cr["source_name"] == "Enrich Alert Payload"
        assert cr["function_id"] == 50
        assert cr["runtime"] == "python"
        assert cr["duration_ms"] == 2000
        assert cr["trigger_type"] == "stream"

        wh = by_id["wh-32"]
        assert wh["source_type"] == "webhook"
        assert wh["source_name"] == "PagerDuty Escalation"
        assert wh["webhook_id"] == 70
        assert wh["event_type"] == "incident.created"
        assert wh["http_status"] == 502
        assert wh["error_message"] == "Bad Gateway"

    @pytest.mark.asyncio
    async def test_source_filter_and_validation(self) -> None:
        app = _build_app(_seed_full_db())
        client = app.test_client()

        for src in ("playbook", "coderun", "webhook"):
            resp = await client.get(f"/api/v1/streams/executions?source={src}")
            assert resp.status_code == 200
            body = await resp.get_json()
            assert body["total"] == 2
            assert all(item["source_type"] == src for item in body["executions"])
            assert body["counts_by_source"] == {
                "playbook": 2,
                "coderun": 2,
                "webhook": 2,
            }

        bad_resp = await client.get("/api/v1/streams/executions?source=unknown")
        assert bad_resp.status_code == 400

    @pytest.mark.asyncio
    async def test_stream_id_defaults_to_playbook_only_for_backward_compat(
        self,
    ) -> None:
        app = _build_app(_seed_full_db())
        client = app.test_client()

        resp = await client.get("/api/v1/streams/executions?stream_id=1")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["total"] == 1
        assert body["executions"][0]["execution_id"] == "pb-exec-001"
        assert body["executions"][0]["source_type"] == "playbook"
        assert body["counts_by_source"] == {"playbook": 1, "coderun": 0, "webhook": 0}

    @pytest.mark.asyncio
    async def test_status_and_search_filters(self) -> None:
        app = _build_app(_seed_full_db())
        client = app.test_client()

        # Status filter across sources (pb-exec-002 and wh-32 are both 'failed')
        resp_failed = await client.get("/api/v1/streams/executions?status=failed")
        assert resp_failed.status_code == 200
        body_failed = await resp_failed.get_json()
        assert body_failed["total"] == 2
        assert {e["execution_id"] for e in body_failed["executions"]} == {
            "pb-exec-002",
            "wh-32",
        }
        assert body_failed["counts_by_source"] == {
            "playbook": 1,
            "coderun": 0,
            "webhook": 1,
        }

        # Search by runtime / function name
        resp_py = await client.get("/api/v1/streams/executions?search=python")
        assert resp_py.status_code == 200
        body_py = await resp_py.get_json()
        assert body_py["total"] == 2
        assert all(e["source_type"] == "coderun" for e in body_py["executions"])

        # Search by webhook event type
        resp_evt = await client.get("/api/v1/streams/executions?search=issue.assigned")
        assert resp_evt.status_code == 200
        body_evt = await resp_evt.get_json()
        assert body_evt["total"] == 1
        assert body_evt["executions"][0]["execution_id"] == "wh-31"

        # Combined status + search
        resp_combo = await client.get(
            "/api/v1/streams/executions?status=failed&search=gateway"
        )
        assert resp_combo.status_code == 200
        body_combo = await resp_combo.get_json()
        assert body_combo["total"] == 1
        assert body_combo["executions"][0]["execution_id"] == "wh-32"

    @pytest.mark.asyncio
    async def test_pagination(self) -> None:
        app = _build_app(_seed_full_db())
        client = app.test_client()

        resp1 = await client.get("/api/v1/streams/executions?page=1&per_page=2")
        assert resp1.status_code == 200
        b1 = await resp1.get_json()
        assert b1["total"] == 6
        assert b1["pages"] == 3
        assert [e["execution_id"] for e in b1["executions"]] == ["wh-32", "cr-exec-002"]

        resp2 = await client.get("/api/v1/streams/executions?page=2&per_page=2")
        assert resp2.status_code == 200
        b2 = await resp2.get_json()
        assert [e["execution_id"] for e in b2["executions"]] == ["pb-exec-002", "wh-31"]

    @pytest.mark.asyncio
    async def test_partial_schema_without_runs_or_webhooks_tables(self) -> None:
        now = datetime(2026, 11, 23, 18, 0, 0, tzinfo=UTC)
        partial_db = FakeDB(
            stream_playbooks=[
                {
                    "id": 1,
                    "tenant_id": 10,
                    "name": "Solo Playbook",
                    "owner_identity_id": 100,
                    "is_public": True,
                }
            ],
            stream_executions=[
                {
                    "id": 101,
                    "tenant_id": 10,
                    "playbook_id": 1,
                    "execution_id": "pb-solo",
                    "status": "completed",
                    "trigger_type": "manual",
                    "input_json": None,
                    "output_json": None,
                    "error_message": None,
                    "started_at": now,
                    "completed_at": now,
                    "duration_ms": 120,
                }
            ],
        )
        app = _build_app(partial_db)
        client = app.test_client()

        resp = await client.get("/api/v1/streams/executions")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["total"] == 1
        assert body["executions"][0]["execution_id"] == "pb-solo"
        assert body["executions"][0]["source_type"] == "playbook"
        assert body["counts_by_source"] == {"playbook": 1, "coderun": 0, "webhook": 0}
