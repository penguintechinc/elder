"""Unit coverage for worker job registry, group resolution and CSV batch runner.

Covers apps/worker/jobs/registry.py, apps/worker/jobs/groups.py and
apps/worker/csv_import_batch.py. Only true boundaries (DB, S3 storage, the
playbook executor) are replaced with test doubles.
"""

from __future__ import annotations

import tests.unit.conftest_worker_stubs  # noqa: F401  isort: skip

import asyncio
import contextlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.worker import csv_import_batch as cib
from apps.worker.jobs import groups as groups_mod
from apps.worker.jobs import registry
from shared.jobbus import JobEnvelope

# --------------------------------------------------------------------------- #
# Shared test doubles
# --------------------------------------------------------------------------- #


class Rows(list):
    """List of rows that also exposes DAL-style ``first()``."""

    def first(self) -> Any:
        """Return the first row or None when empty."""
        return self[0] if self else None


class FakeQuery:
    """DAL query double: select pops queued results, update records kwargs."""

    def __init__(self, db: FakeDB) -> None:
        """Bind the query to its owning fake database."""
        self.db = db

    def select(self, *args: Any, **kwargs: Any) -> Rows:
        """Pop the next queued select result (empty when the queue is dry)."""
        self.db.select_calls.append(kwargs)
        if self.db.select_queue:
            return Rows(self.db.select_queue.pop(0))
        return Rows()

    def update(self, **kwargs: Any) -> int:
        """Record an update and return the configured affected-row count."""
        self.db.updates.append(kwargs)
        return self.db.update_result


class FakeDB:
    """Callable DAL double with auto-created table mocks."""

    def __init__(
        self,
        select_queue: list[list[Any]] | None = None,
        has_tenant_modules: bool = True,
        update_result: int = 1,
    ) -> None:
        """Create the fake DB with an ordered queue of select results."""
        self.select_queue = list(select_queue or [])
        self.has_tenant_modules = has_tenant_modules
        self.update_result = update_result
        self.updates: list[dict[str, Any]] = []
        self.select_calls: list[dict[str, Any]] = []
        self.tables: dict[str, MagicMock] = {}
        self.commit = MagicMock()
        self.rollback = MagicMock()
        self.close = MagicMock()

    def __call__(self, *_args: Any, **_kwargs: Any) -> FakeQuery:
        """Return a query double regardless of the expression."""
        return FakeQuery(self)

    def __getattr__(self, name: str) -> Any:
        """Auto-create table mocks, hiding tenant_modules when configured."""
        if name.startswith("__"):
            raise AttributeError(name)
        if name == "tenant_modules" and not self.has_tenant_modules:
            raise AttributeError(name)
        return self.tables.setdefault(name, MagicMock())


def _envelope(
    payload: dict[str, Any] | None = None,
    tenant_id: int | None = 7,
    job_id: str = "job-1",
) -> JobEnvelope:
    """Build a JobEnvelope for handler tests."""
    return JobEnvelope(
        job_id=job_id,
        job_type="t",
        payload=payload if payload is not None else {},
        enqueued_at="2026-01-01T00:00:00Z",
        tenant_id=tenant_id,
    )


@pytest.fixture
def worker_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Give the worker settings a database URL for the duration of a test."""
    from apps.worker.config.settings import settings

    monkeypatch.setattr(settings, "database_url", "postgresql://u@h/db")
    monkeypatch.setattr(settings, "database_read_url", None, raising=False)
    return settings


# --------------------------------------------------------------------------- #
# groups.py
# --------------------------------------------------------------------------- #


class TestGroups:
    """resolve_worker_groups behaviour."""

    def test_collects_groups_from_manifests(self) -> None:
        """Groups from every enabled manifest are unioned."""
        manifests = [
            SimpleNamespace(worker_task_groups=["a", "b"]),
            SimpleNamespace(worker_task_groups=["b", "c"]),
        ]
        with patch("apps.api.modules.registry.resolve_enabled", return_value=manifests):
            assert groups_mod.resolve_worker_groups({}) == {"a", "b", "c"}

    def test_resolution_error_is_reraised(self) -> None:
        """A ValueError from module resolution propagates."""
        with patch(
            "apps.api.modules.registry.resolve_enabled",
            side_effect=ValueError("unknown module"),
        ):
            with pytest.raises(ValueError, match="unknown module"):
                groups_mod.resolve_worker_groups({})


# --------------------------------------------------------------------------- #
# registry.py
# --------------------------------------------------------------------------- #


class TestRegistryBasics:
    """get_handler and the SBOM stub."""

    def test_get_handler_known_and_unknown(self) -> None:
        """Known groups resolve to handlers; unknown yield None."""
        assert registry.get_handler("discovery") is registry.handle_discovery
        assert registry.get_handler("streams") is registry.handle_streams
        assert registry.get_handler("nope") is None

    @pytest.mark.asyncio
    async def test_sbom_scan_stub_raises(self) -> None:
        """The SBOM handler fails loudly instead of silently succeeding."""
        with pytest.raises(NotImplementedError):
            await registry.handle_sbom_scan(_envelope())


class TestHandleDiscovery:
    """handle_discovery dispatch."""

    @pytest.mark.asyncio
    async def test_missing_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No DATABASE_URL raises ValueError."""
        from apps.worker.config.settings import settings

        monkeypatch.setattr(settings, "database_url", "")
        with pytest.raises(ValueError, match="DATABASE_URL"):
            await registry.handle_discovery(_envelope())

    @pytest.mark.asyncio
    async def test_success(self, worker_settings: Any) -> None:
        """The executor runs in a thread and the DB manager is closed."""
        mgr = MagicMock()
        executor = MagicMock()
        executor.run_pending.return_value = 3
        with (
            patch("shared.database.manager.DatabaseManager", return_value=mgr),
            patch(
                "apps.worker.discovery.executor.DiscoveryExecutor",
                return_value=executor,
            ),
        ):
            result = await registry.handle_discovery(_envelope())
        assert result == {"status": "success", "jobs_executed": 3, "job_id": "job-1"}
        mgr.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_reraises(self, worker_settings: Any) -> None:
        """Executor failures are logged and re-raised."""
        mgr = MagicMock()
        executor = MagicMock()
        executor.run_pending.side_effect = RuntimeError("boom")
        with (
            patch("shared.database.manager.DatabaseManager", return_value=mgr),
            patch(
                "apps.worker.discovery.executor.DiscoveryExecutor",
                return_value=executor,
            ),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await registry.handle_discovery(_envelope())
        mgr.close.assert_called_once()


class TestHandleSla:
    """handle_helpdesk_sla_breach dispatch."""

    @pytest.mark.asyncio
    async def test_missing_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No DATABASE_URL raises ValueError."""
        from apps.worker.config.settings import settings

        monkeypatch.setattr(settings, "database_url", "")
        with pytest.raises(ValueError, match="DATABASE_URL"):
            await registry.handle_helpdesk_sla_breach(_envelope())

    @pytest.mark.asyncio
    async def test_missing_tenant(self, worker_settings: Any) -> None:
        """An envelope without tenant_id is rejected."""
        with pytest.raises(ValueError, match="tenant_id"):
            await registry.handle_helpdesk_sla_breach(_envelope(tenant_id=None))

    @pytest.mark.asyncio
    async def test_success(self, worker_settings: Any) -> None:
        """The breach check result is returned and the manager closed."""
        mgr = MagicMock()
        check = AsyncMock(return_value={"breached_count": 2})
        with (
            patch("shared.database.manager.DatabaseManager", return_value=mgr),
            patch(
                "apps.api.modules.helpdesk.worker.sla_breach.check_sla_breaches",
                check,
            ),
        ):
            result = await registry.handle_helpdesk_sla_breach(_envelope())
        assert result == {"breached_count": 2}
        check.assert_awaited_once()
        mgr.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_reraises(self, worker_settings: Any) -> None:
        """Check failures propagate and still close the manager."""
        mgr = MagicMock()
        check = AsyncMock(side_effect=RuntimeError("sla down"))
        with (
            patch("shared.database.manager.DatabaseManager", return_value=mgr),
            patch(
                "apps.api.modules.helpdesk.worker.sla_breach.check_sla_breaches",
                check,
            ),
        ):
            with pytest.raises(RuntimeError, match="sla down"):
                await registry.handle_helpdesk_sla_breach(_envelope())
        mgr.close.assert_called_once()


def _node_row(node_id: str = "n1") -> SimpleNamespace:
    """Normalized stream_nodes row."""
    return SimpleNamespace(
        node_id=node_id,
        node_type="http",
        node_category="action",
        label="Call",
        config=None,
    )


def _edge_row() -> SimpleNamespace:
    """Normalized stream_edges row."""
    return SimpleNamespace(
        source_node_id="n1",
        target_node_id="n2",
        source_handle="out",
        target_handle="in",
    )


def _node_result(
    node_id: str = "n1", node_type: str = "", error: str | None = None
) -> Any:
    """Per-node executor result double."""
    res = MagicMock()
    res.node_id = node_id
    res.node_type = node_type
    res.status = SimpleNamespace(value="success")
    res.to_dict.return_value = {"outputs": {"k": 1}}
    res.error = error
    res.started_at = datetime.now(UTC)
    res.completed_at = datetime.now(UTC)
    res.execution_time_ms = 5.0
    return res


def _exec_result(success: bool = True, node_results: dict | None = None) -> Any:
    """Playbook execution result double."""
    res = MagicMock()
    res.success = success
    res.error = None if success else "bad"
    res.execution_time_ms = 12.5
    res.completed_nodes = 1
    res.failed_nodes = 0 if success else 1
    res.to_dict.return_value = {"ok": success}
    res.node_results = node_results if node_results is not None else {}
    return res


@contextlib.contextmanager
def _streams_env(db: FakeDB, exec_result: Any = None, manifests: Any = None):
    """Patch the streams handler collaborators; yield the executor class mock."""
    manifests = manifests or [
        SimpleNamespace(name="mod_a", default_enabled=True),
        SimpleNamespace(name="mod_b", default_enabled=False),
    ]
    executor_cls = MagicMock()
    executor_cls.return_value.execute = AsyncMock(
        return_value=exec_result if exec_result is not None else _exec_result()
    )
    with (
        patch("penguin_dal.DAL", return_value=db),
        patch("apps.api.modules.registry.resolve_enabled", return_value=manifests),
        patch("apps.worker.streams.executor.node_registry.discover_nodes"),
        patch("apps.worker.streams.connectors.registry.discover_connectors"),
        patch(
            "apps.worker.streams.executor.playbook_executor.PlaybookExecutor",
            executor_cls,
        ),
    ):
        yield executor_cls


def _streams_payload(**extra: Any) -> dict[str, Any]:
    """Standard streams job payload."""
    base = {"execution_id": "ex-1", "playbook_id": 4}
    base.update(extra)
    return base


class TestHandleStreams:
    """handle_streams execution flow."""

    @pytest.mark.asyncio
    async def test_missing_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No DATABASE_URL raises ValueError."""
        from apps.worker.config.settings import settings

        monkeypatch.setattr(settings, "database_url", "")
        with pytest.raises(ValueError, match="DATABASE_URL"):
            await registry.handle_streams(_envelope(_streams_payload()))

    @pytest.mark.asyncio
    async def test_missing_payload_fields(self, worker_settings: Any) -> None:
        """Missing execution/playbook/tenant ids are rejected."""
        with pytest.raises(ValueError, match="Missing execution_id"):
            await registry.handle_streams(_envelope({}, tenant_id=None))

    @pytest.mark.asyncio
    async def test_non_dict_payload_treated_as_empty(
        self, worker_settings: Any
    ) -> None:
        """A non-dict payload is tolerated for logging, then fails validation."""
        env = _envelope(tenant_id=None)
        env.payload = ["x"]  # type: ignore[assignment]
        with pytest.raises(AttributeError):
            await registry.handle_streams(env)

    @pytest.mark.asyncio
    async def test_execution_not_found(self, worker_settings: Any) -> None:
        """An unknown execution aborts with ValueError."""
        db = FakeDB(select_queue=[[]])
        with _streams_env(db):
            with pytest.raises(ValueError, match="Execution ex-1 not found"):
                await registry.handle_streams(_envelope(_streams_payload()))

    @pytest.mark.asyncio
    async def test_terminal_execution_skipped(self, worker_settings: Any) -> None:
        """A terminal execution is a no-op returning status skipped."""
        db = FakeDB(select_queue=[[SimpleNamespace(status="success", id=1)]])
        with _streams_env(db) as executor_cls:
            result = await registry.handle_streams(_envelope(_streams_payload()))
        assert result["status"] == "skipped"
        executor_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_playbook_not_found(self, worker_settings: Any) -> None:
        """A missing playbook aborts with ValueError."""
        execution = SimpleNamespace(status="queued", id=1, input_json=None)
        db = FakeDB(select_queue=[[execution], []])
        with _streams_env(db):
            with pytest.raises(ValueError, match="Playbook 4 not found"):
                await registry.handle_streams(_envelope(_streams_payload()))

    @pytest.mark.asyncio
    async def test_success_with_normalized_nodes(self, worker_settings: Any) -> None:
        """Normalized nodes/edges run, results persist, token stays in memory."""
        execution = SimpleNamespace(status="pending", id=1, input_json={"a": 1})
        toggles = [
            SimpleNamespace(module_name="mod_a", enabled=False),
            SimpleNamespace(module_name="unknown", enabled=True),
        ]
        db = FakeDB(
            select_queue=[
                [execution],
                [SimpleNamespace(id=4)],
                [_node_row()],
                [_edge_row()],
                toggles,
            ]
        )
        node_results = {
            "n1": _node_result("n1", node_type="http"),
            "n2": _node_result("n2"),
            "weird_id": _node_result("weird_id"),
            "plain": _node_result("plain"),
        }
        with _streams_env(db, _exec_result(True, node_results)) as executor_cls:
            result = await registry.handle_streams(
                _envelope(_streams_payload(auth_token="tok"))
            )
        assert result["status"] == "success"
        assert result["success"] is True
        playbook_data = executor_cls.return_value.execute.await_args.args[0]
        cfg = playbook_data["config"]
        assert cfg["_elder_auth_token"] == "tok"
        assert cfg["enabled_modules"] == {"mod_a": False, "mod_b": False}
        assert playbook_data["nodes"][0]["data"]["config"] == {}
        assert playbook_data["edges"][0]["sourceHandle"] == "out"
        assert db.stream_node_executions.insert.call_count == 4
        types = [
            c.kwargs["node_type"]
            for c in db.stream_node_executions.insert.call_args_list
        ]
        assert types == ["http", "n2", "weird", "plain"]

    @pytest.mark.asyncio
    async def test_failed_result_and_no_tenant_modules_table(
        self, worker_settings: Any
    ) -> None:
        """A failed run is recorded as failed; no tenant_modules is tolerated."""
        execution = SimpleNamespace(status="queued", id=1, input_json=None)
        db = FakeDB(
            select_queue=[[execution], [SimpleNamespace(id=4)], [_node_row()], []],
            has_tenant_modules=False,
        )
        with _streams_env(db, _exec_result(False)):
            result = await registry.handle_streams(_envelope(_streams_payload()))
        assert result["status"] == "failed"
        assert any(u.get("status") == "failed" for u in db.updates)

    @pytest.mark.asyncio
    async def test_canvas_json_fallback(self, worker_settings: Any) -> None:
        """Empty stream_nodes fall back to the latest version canvas."""
        execution = SimpleNamespace(status="queued", id=1, input_json=None)
        canvas = {
            "nodes": [
                "junk",
                {
                    "id": "a",
                    "data": {
                        "nodeType": "http",
                        "label": "L",
                        "category": "c",
                        "config": {"x": 1},
                    },
                },
                {
                    "id": "b",
                    "type": "delay",
                    "label": "B",
                    "category": "k",
                    "config": {"y": 2},
                },
                {},
            ],
            "edges": [
                "junk",
                {"source": "a", "target": "b"},
            ],
        }
        version = SimpleNamespace(canvas_json=canvas, nodes_json=None, edges_json=None)
        db = FakeDB(
            select_queue=[
                [execution],
                [SimpleNamespace(id=4)],
                [],
                [],
                [version],
                [],
            ]
        )
        with _streams_env(db) as executor_cls:
            await registry.handle_streams(_envelope(_streams_payload()))
        data = executor_cls.return_value.execute.await_args.args[0]
        assert [n["id"] for n in data["nodes"]] == ["a", "b", ""]
        assert data["nodes"][0]["type"] == "http"
        assert data["nodes"][1]["type"] == "delay"
        assert data["edges"] == [
            {"source": "a", "target": "b", "sourceHandle": None, "targetHandle": None}
        ]

    @pytest.mark.asyncio
    async def test_version_json_columns_and_existing_edges(
        self, worker_settings: Any
    ) -> None:
        """nodes_json/edges_json are used when canvas is empty; edges not replaced."""
        execution = SimpleNamespace(status="queued", id=1, input_json=None)
        version = SimpleNamespace(
            canvas_json=None,
            nodes_json=[{"id": "a", "nodeType": "x"}],
            edges_json=[{"source": "a", "target": "b"}],
        )
        db = FakeDB(
            select_queue=[
                [execution],
                [SimpleNamespace(id=4)],
                [],
                [_edge_row()],
                [version],
                [],
            ]
        )
        with _streams_env(db) as executor_cls:
            await registry.handle_streams(_envelope(_streams_payload()))
        data = executor_cls.return_value.execute.await_args.args[0]
        assert data["nodes"][0]["type"] == "x"
        assert data["edges"][0]["source"] == "n1"

    @pytest.mark.asyncio
    async def test_version_absent(self, worker_settings: Any) -> None:
        """No version rows leaves nodes empty and still runs."""
        execution = SimpleNamespace(status="queued", id=1, input_json=None)
        db = FakeDB(select_queue=[[execution], [SimpleNamespace(id=4)], [], [], [], []])
        with _streams_env(db) as executor_cls:
            await registry.handle_streams(_envelope(_streams_payload()))
        assert executor_cls.return_value.execute.await_args.args[0]["nodes"] == []

    @pytest.mark.asyncio
    async def test_version_edges_empty_raw(self, worker_settings: Any) -> None:
        """A version with nodes but no raw edges leaves edges empty."""
        execution = SimpleNamespace(status="queued", id=1, input_json=None)
        version = SimpleNamespace(
            canvas_json={"nodes": [{"id": "a"}]}, nodes_json=None, edges_json=None
        )
        db = FakeDB(
            select_queue=[[execution], [SimpleNamespace(id=4)], [], [], [version], []]
        )
        with _streams_env(db) as executor_cls:
            await registry.handle_streams(_envelope(_streams_payload()))
        assert executor_cls.return_value.execute.await_args.args[0]["edges"] == []

    @pytest.mark.asyncio
    async def test_executor_failure_reraises(self, worker_settings: Any) -> None:
        """Executor exceptions are logged and re-raised for XAUTOCLAIM retry."""
        execution = SimpleNamespace(status="queued", id=1, input_json=None)
        db = FakeDB(
            select_queue=[[execution], [SimpleNamespace(id=4)], [_node_row()], []]
        )
        with _streams_env(db) as executor_cls:
            executor_cls.return_value.execute = AsyncMock(side_effect=RuntimeError("x"))
            with pytest.raises(RuntimeError, match="x"):
                await registry.handle_streams(_envelope(_streams_payload()))


# --------------------------------------------------------------------------- #
# csv_import_batch.py
# --------------------------------------------------------------------------- #


class FakeEngine:
    """SQLAlchemy engine double for the PostgreSQL claim path."""

    def __init__(self, rows: list[dict[str, Any]], first: dict | None = None) -> None:
        """Hold rows returned for the all()/first() result mappings."""
        self.dialect = SimpleNamespace(name="postgresql")
        self.rows = rows
        self.first = first
        self.params: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def begin(self):
        """Yield a connection double that records bound params."""
        outer = self

        class Conn:
            """Connection double."""

            def execute(self, _sql: Any, params: dict[str, Any]) -> Any:
                """Record params and return a mappings() result."""
                outer.params.append(params)
                result = MagicMock()
                result.mappings.return_value.all.return_value = outer.rows
                result.mappings.return_value.first.return_value = outer.first
                return result

        yield Conn()


def _job_row(pk: int = 1, job_id: str = "j1") -> SimpleNamespace:
    """csv_import_jobs row for the generic-dialect path."""
    return SimpleNamespace(
        id=pk,
        job_id=job_id,
        tenant_id=3,
        resource="entities",
        filename="a.csv",
        storage_backend="s3",
        storage_key="k/a.csv",
        file_sha256="abc",
        file_size_bytes=10,
    )


class TestCsvClaims:
    """claim_pending_jobs / claim_job_by_id."""

    def test_normalize_url(self) -> None:
        """postgres:// is rewritten to postgresql://."""
        assert cib._normalize_database_url("postgres://u@h/d") == "postgresql://u@h/d"
        assert cib._normalize_database_url("postgresql://u@h/d") == "postgresql://u@h/d"
        assert cib._normalize_database_url("sqlite://x") == "sqlite://x"

    def test_reset_guard(self) -> None:
        """reset_unconfigured_log_state clears the one-shot flag."""
        cib._s3_unconfigured_logged = True
        cib.reset_unconfigured_log_state()
        assert cib._s3_unconfigured_logged is False

    def test_claim_pending_postgres(self) -> None:
        """The PostgreSQL path returns dict rows and clamps the limit."""
        engine = FakeEngine([{"id": 1, "job_id": "a"}])
        db = SimpleNamespace(engine=engine)
        out = cib.claim_pending_jobs(db, limit=0, worker_id="w")
        assert out == [{"id": 1, "job_id": "a"}]
        assert engine.params[0]["limit"] == 1

    def test_claim_pending_generic(self) -> None:
        """Generic path claims rows via conditional update up to the limit."""
        rows = [_job_row(1, "a"), _job_row(2, "b"), _job_row(3, "c")]
        db = FakeDB(select_queue=[rows])
        db.engine = None
        out = cib.claim_pending_jobs(db, limit=2, worker_id="w")
        assert [r["job_id"] for r in out] == ["a", "b"]
        assert db.commit.call_count == 2

    def test_claim_pending_generic_lost_race(self) -> None:
        """Rows another worker already claimed are skipped."""
        db = FakeDB(select_queue=[[_job_row()]], update_result=0)
        out = cib.claim_pending_jobs(db, limit=5, worker_id="w")
        assert out == []

    def test_claim_by_id_postgres_hit_and_miss(self) -> None:
        """PostgreSQL path returns the row or None."""
        hit = SimpleNamespace(engine=FakeEngine([], first={"id": 1, "job_id": "a"}))
        assert cib.claim_job_by_id(hit, "a", worker_id="w") == {"id": 1, "job_id": "a"}
        miss = SimpleNamespace(engine=FakeEngine([], first=None))
        assert cib.claim_job_by_id(miss, "a", worker_id="w") is None

    def test_claim_by_id_generic(self) -> None:
        """Generic path updates then reads the row back."""
        db = FakeDB(select_queue=[[_job_row(9, "zz")]])
        out = cib.claim_job_by_id(db, "zz", worker_id="w")
        assert out is not None and out["id"] == 9

    def test_claim_by_id_generic_not_updated(self) -> None:
        """No row transitioned means None."""
        db = FakeDB(update_result=0)
        assert cib.claim_job_by_id(db, "zz", worker_id="w") is None

    def test_claim_by_id_generic_row_vanished(self) -> None:
        """A claimed row that cannot be re-read yields None."""
        db = FakeDB(select_queue=[[]])
        assert cib.claim_job_by_id(db, "zz", worker_id="w") is None


class TestProcessClaimedJob:
    """process_claimed_job terminal-state handling."""

    def _job(self, **over: Any) -> dict[str, Any]:
        """Claimed job dict."""
        base = {
            "id": 1,
            "job_id": "j1",
            "tenant_id": "3",
            "resource": "entities",
            "storage_key": "k",
            "file_sha256": "abc",
        }
        base.update(over)
        return base

    def test_completed(self) -> None:
        """A clean import is marked completed and the object deleted."""
        db, storage = FakeDB(), MagicMock()
        result = SimpleNamespace(
            errors=[], total_rows=2, imported_count=2, skipped_count=0
        )
        with patch.object(cib, "process_csv_text", return_value=result):
            assert cib.process_claimed_job(db, storage, self._job()) == "completed"
        assert db.updates[0]["status"] == "completed"
        storage.delete_csv.assert_called_once_with("k")

    def test_validation_failed(self) -> None:
        """Row errors mark the job failed with the validation message."""
        db, storage = FakeDB(), MagicMock()
        result = SimpleNamespace(
            errors=[{"row": 1}], total_rows=1, imported_count=0, skipped_count=1
        )
        with patch.object(cib, "process_csv_text", return_value=result):
            assert cib.process_claimed_job(db, storage, self._job()) == "failed"
        assert db.updates[0]["error_message"] == "CSV validation failed"

    def test_missing_storage_key(self) -> None:
        """A job without a storage key fails and does not call delete."""
        db, storage = FakeDB(), MagicMock()
        out = cib.process_claimed_job(db, storage, self._job(storage_key=None))
        assert out == "failed"
        storage.delete_csv.assert_not_called()

    def test_exception_with_rollback_error(self) -> None:
        """Failures roll back (even if rollback raises) and mark failed."""
        db, storage = FakeDB(), MagicMock()
        db.rollback.side_effect = RuntimeError("rb")
        storage.get_csv.side_effect = RuntimeError("s3 down")
        assert cib.process_claimed_job(db, storage, self._job()) == "failed"
        assert "s3 down" in db.updates[-1]["error_message"]


class TestRunners:
    """run_once / run_batch_once / process_job_by_id / main."""

    def test_run_once_counts(self) -> None:
        """Completed and failed jobs are tallied in the summary."""
        with (
            patch.object(
                cib, "claim_pending_jobs", return_value=[{"id": 1}, {"id": 2}]
            ),
            patch.object(
                cib, "process_claimed_job", side_effect=["completed", "failed"]
            ),
        ):
            out = cib.run_once(FakeDB(), MagicMock(), worker_id="w")
        assert out == {"claimed": 2, "completed": 1, "failed": 1}

    def test_run_once_empty_uses_env_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No jobs yields a zero summary; worker id falls back to POD_NAME."""
        monkeypatch.setenv("POD_NAME", "pod-x")
        with patch.object(cib, "claim_pending_jobs", return_value=[]) as claim:
            out = cib.run_once(FakeDB(), MagicMock())
        assert out == {"claimed": 0, "completed": 0, "failed": 0}
        assert claim.call_args.kwargs["worker_id"] == "pod-x"

    def test_batch_once_unconfigured(self) -> None:
        """Unconfigured S3 skips cleanly, logging once."""
        cib.reset_unconfigured_log_state()
        storage = SimpleNamespace(is_configured=False)
        assert cib.run_batch_once(storage=storage) == 0
        assert cib._s3_unconfigured_logged is True
        assert cib.run_batch_once(storage=storage) == 0
        cib.reset_unconfigured_log_state()

    def test_batch_once_require_s3(self) -> None:
        """require_s3 turns the skip into a failure code."""
        storage = SimpleNamespace(is_configured=False)
        assert cib.run_batch_once(storage=storage, require_s3=True) == 1

    def test_batch_once_default_storage(self) -> None:
        """Without storage the module-level factory is consulted."""
        with patch.object(
            cib,
            "get_csv_import_storage",
            return_value=SimpleNamespace(is_configured=False),
        ):
            assert cib.run_batch_once() == 0
        cib.reset_unconfigured_log_state()

    def test_batch_once_missing_database_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No supplied DB and no DATABASE_URL fails with code 1."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        storage = SimpleNamespace(is_configured=True)
        assert cib.run_batch_once(storage=storage) == 1

    def test_batch_once_supplied_db_not_closed(self) -> None:
        """A caller-owned DB is not closed."""
        db = FakeDB()
        storage = SimpleNamespace(is_configured=True)
        with patch.object(cib, "run_once") as run:
            assert cib.run_batch_once(db=db, storage=storage, worker_id="w") == 0
        run.assert_called_once()
        db.close.assert_not_called()

    def test_batch_once_owned_db_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An internally built DAL is closed, tolerating close errors."""
        monkeypatch.setenv("DATABASE_URL", "postgres://u@h/d")
        dal = MagicMock()
        dal.close.side_effect = RuntimeError("c")
        storage = SimpleNamespace(is_configured=True)
        with (
            patch.object(cib, "DAL", return_value=dal) as dal_cls,
            patch.object(cib, "run_once"),
        ):
            assert cib.run_batch_once(storage=storage) == 0
        assert dal_cls.call_args.args[0] == "postgresql://u@h/d"
        dal.close.assert_called_once()

    def test_process_by_id_unconfigured(self) -> None:
        """Unconfigured S3 returns None and logs once."""
        cib.reset_unconfigured_log_state()
        storage = SimpleNamespace(is_configured=False)
        assert cib.process_job_by_id("j", storage=storage) is None
        assert cib.process_job_by_id("j", storage=storage) is None
        cib.reset_unconfigured_log_state()

    def test_process_by_id_default_storage(self) -> None:
        """The module storage factory is used when none is supplied."""
        with patch.object(
            cib,
            "get_csv_import_storage",
            return_value=SimpleNamespace(is_configured=False),
        ):
            assert cib.process_job_by_id("j") is None
        cib.reset_unconfigured_log_state()

    def test_process_by_id_missing_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No DB and no DATABASE_URL returns None."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        storage = SimpleNamespace(is_configured=True)
        assert cib.process_job_by_id("j", storage=storage) is None

    def test_process_by_id_not_claimed(self) -> None:
        """A job claimed elsewhere yields None."""
        storage = SimpleNamespace(is_configured=True)
        with patch.object(cib, "claim_job_by_id", return_value=None):
            assert cib.process_job_by_id("j", db=FakeDB(), storage=storage) is None

    def test_process_by_id_processes(self) -> None:
        """A claimed job is processed with the batch size."""
        storage = SimpleNamespace(is_configured=True)
        with (
            patch.object(cib, "claim_job_by_id", return_value={"id": 1}),
            patch.object(cib, "process_claimed_job", return_value="completed") as proc,
        ):
            out = cib.process_job_by_id(
                "j", db=FakeDB(), storage=storage, batch_size=5, worker_id="w"
            )
        assert out == "completed"
        assert proc.call_args.kwargs["batch_size"] == 5

    def test_process_by_id_owned_db_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An internally built DAL is closed even when close raises."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
        dal = MagicMock()
        dal.close.side_effect = RuntimeError("c")
        storage = SimpleNamespace(is_configured=True)
        with (
            patch.object(cib, "DAL", return_value=dal),
            patch.object(cib, "claim_job_by_id", return_value=None),
        ):
            assert cib.process_job_by_id("j", storage=storage) is None
        dal.close.assert_called_once()

    def test_main_parses_args(self) -> None:
        """The CLI forwards parsed arguments to run_batch_once."""
        with (
            patch("apps.worker.utils.logger.configure_logging"),
            patch.object(cib, "run_batch_once", return_value=0) as run,
        ):
            rc = cib.main(
                [
                    "--limit",
                    "3",
                    "--batch-size",
                    "9",
                    "--worker-id",
                    "w",
                    "--require-s3",
                ]
            )
        assert rc == 0
        assert run.call_args.kwargs == {
            "limit": 3,
            "batch_size": 9,
            "worker_id": "w",
            "require_s3": True,
        }


def test_utc_now_is_aware() -> None:
    """_utc_now returns a timezone-aware datetime."""
    assert cib._utc_now().tzinfo is not None
    assert asyncio  # keep import used for event-loop based tests above
