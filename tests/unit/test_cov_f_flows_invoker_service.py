"""Behavioural tests for the flows-invoker consumer service and its health probe."""

import signal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.flows_invoker import healthcheck
from apps.flows_invoker import main as mod
from apps.flows_invoker.main import FLOWS_GROUP, FlowsInvokerService


def _envelope(payload: dict[str, Any] | None, tenant_id: Any = 7, job_id: str = "j-1"):
    """Build a job-bus envelope as delivered to the consumer."""
    return SimpleNamespace(payload=payload, tenant_id=tenant_id, job_id=job_id)


def _service() -> FlowsInvokerService:
    """Return a service with a job-bus double and a fake write database."""
    service = FlowsInvokerService()
    service.consumer_name = "pod-a"
    service.jobbus = AsyncMock()
    service.jobbus.is_duplicate.return_value = False
    service.db_manager = SimpleNamespace(write=object())
    return service


class TestInitialisation:
    """Dependency wiring."""

    def test_consumer_name_prefers_pod_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The consumer is named after the pod, falling back to the hostname."""
        monkeypatch.setenv("POD_NAME", "flows-invoker-0")
        assert FlowsInvokerService().consumer_name == "flows-invoker-0"
        monkeypatch.delenv("POD_NAME")
        monkeypatch.setattr(mod.socket, "gethostname", lambda: "host-x")
        assert FlowsInvokerService().consumer_name == "host-x"

    def test_init_db_requires_database_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Starting without a database URL fails loudly."""
        monkeypatch.setattr(mod.settings, "database_url", None)
        with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
            FlowsInvokerService()._init_db()

    def test_init_db_builds_manager_from_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The database manager receives primary, replica and pool settings."""
        monkeypatch.setattr(mod.settings, "database_url", "postgresql://primary/db")
        monkeypatch.setattr(mod.settings, "database_read_url", "postgresql://ro/db")
        monkeypatch.setattr(mod.settings, "db_pool_size", 3)
        service = FlowsInvokerService()
        with patch("shared.database.manager.DatabaseManager") as manager:
            service._init_db()
        manager.assert_called_once_with(
            primary_url="postgresql://primary/db",
            replica_url="postgresql://ro/db",
            pool_size=3,
        )
        assert service.db_manager is manager.return_value

    @pytest.mark.asyncio
    async def test_init_jobbus_creates_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The job bus is built on the configured Redis and the flows group ensured."""
        monkeypatch.setattr(mod.settings, "redis_url", "redis://cache.invalid/0")
        service = FlowsInvokerService()
        bus = AsyncMock()
        with (
            patch.object(mod.redis.asyncio, "from_url") as from_url,
            patch.object(mod, "JobBus", return_value=bus) as bus_cls,
        ):
            await service._init_jobbus()
        from_url.assert_called_once_with("redis://cache.invalid/0")
        bus_cls.assert_called_once_with(
            from_url.return_value, max_deliveries=5, group="workers"
        )
        bus.ensure_group.assert_awaited_once_with(FLOWS_GROUP)
        assert service.jobbus is bus

    @pytest.mark.asyncio
    async def test_start_aborts_when_jobbus_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If Redis cannot be reached the service does not enter its loops."""
        service = FlowsInvokerService()
        monkeypatch.setattr(service, "_init_db", lambda: None)
        service._consumer_loop = AsyncMock()
        with patch.object(
            mod.redis.asyncio, "from_url", side_effect=ConnectionError("refused")
        ):
            await service.start()
        assert service.jobbus is None and service.running is False
        service._consumer_loop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_runs_consumer_and_sweeper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A healthy start installs signal handlers and runs both loops."""
        service = _service()
        monkeypatch.setattr(service, "_init_db", lambda: None)
        service._init_jobbus = AsyncMock()
        service._install_signal_handlers = MagicMock()
        service._consumer_loop = AsyncMock()
        service._sweeper_loop = AsyncMock()
        await service.start()
        assert service.running is True
        service._install_signal_handlers.assert_called_once_with()
        service._consumer_loop.assert_awaited_once()
        service._sweeper_loop.assert_awaited_once()

    def test_signal_handlers_request_graceful_stop(self) -> None:
        """SIGTERM and SIGINT are wired to a handler that stops the loops."""
        service = _service()
        service.running = True
        loop = MagicMock()
        with patch.object(mod.asyncio, "get_event_loop", return_value=loop):
            service._install_signal_handlers()
        signals = [call.args[0] for call in loop.add_signal_handler.call_args_list]
        assert signals == [signal.SIGTERM, signal.SIGINT]
        loop.add_signal_handler.call_args.args[1]()
        assert service.running is False

    def test_main_runs_the_service(self) -> None:
        """The entrypoint runs the service's start coroutine to completion."""
        with (
            patch.object(mod.asyncio, "run") as run,
            patch.object(mod.logging, "basicConfig"),
        ):
            mod.main()
        coroutine = run.call_args.args[0]
        assert coroutine.cr_code.co_name == "start"
        coroutine.close()


class TestJobHandling:
    """Per-job execution, idempotency and tenant scoping."""

    @pytest.mark.asyncio
    async def test_handle_runs_pipeline_with_tenant_scope(self) -> None:
        """The pipeline runs against the write DB with integer tenant and promotion ids."""
        service = _service()
        seen: dict[str, Any] = {}

        def pipeline(db: Any, tenant: int, promotion: int, by: Any, execution: Any):
            """Record the arguments the service passes to the pipeline."""
            seen.update(db=db, args=(tenant, promotion, by, execution))
            return {"status": "succeeded"}

        with patch.object(mod, "execute_promotion_pipeline", pipeline):
            result = await service._handle(
                _envelope(
                    {
                        "promotion_id": "12",
                        "started_by_identity_id": 4,
                        "execution_id": "ex-1",
                    },
                    tenant_id="7",
                )
            )
        assert result == {"status": "succeeded"}
        assert seen["db"] is service.db_manager.write
        assert seen["args"] == (7, 12, 4, "ex-1")

    @pytest.mark.asyncio
    async def test_handle_falls_back_to_payload_tenant(self) -> None:
        """When the envelope has no tenant, the payload's tenant id is used."""
        service = _service()
        with patch.object(
            mod, "execute_promotion_pipeline", lambda _db, t, p, *_: {"t": t, "p": p}
        ):
            result = await service._handle(
                _envelope({"tenant_id": 3, "promotion_id": 9}, tenant_id=None)
            )
        assert result == {"t": 3, "p": 9}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("payload", "tenant"),
        [({"promotion_id": 1}, None), ({}, 7), (None, 7)],
        ids=["no-tenant", "no-promotion", "empty-payload"],
    )
    async def test_handle_rejects_unscoped_jobs(
        self, payload: dict[str, Any] | None, tenant: Any
    ) -> None:
        """A job without tenant or promotion never reaches the pipeline."""
        service = _service()
        with (
            patch.object(mod, "execute_promotion_pipeline") as pipeline,
            pytest.raises(ValueError, match="missing tenant_id/promotion_id"),
        ):
            await service._handle(_envelope(payload, tenant_id=tenant))
        pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_success_marks_publishes_and_acks(self) -> None:
        """A successful job is marked processed, its result published, then acked."""
        service = _service()
        service._handle = AsyncMock(return_value={"status": "succeeded"})
        await service._process("1-0", _envelope({}, job_id="j-9"))
        service.jobbus.mark_processed.assert_awaited_once_with("j-9")
        service.jobbus.publish_result.assert_awaited_once_with(
            FLOWS_GROUP, "j-9", "success", {"status": "succeeded"}
        )
        service.jobbus.ack.assert_awaited_once_with(FLOWS_GROUP, "1-0")

    @pytest.mark.asyncio
    async def test_process_publishes_empty_result_for_none(self) -> None:
        """A pipeline returning nothing still publishes an empty success result."""
        service = _service()
        service._handle = AsyncMock(return_value=None)
        await service._process("1-0", _envelope({}))
        assert service.jobbus.publish_result.call_args.args[3] == {}

    @pytest.mark.asyncio
    async def test_duplicate_job_is_acked_without_running(self) -> None:
        """An already-processed job is acknowledged and never re-executed."""
        service = _service()
        service.jobbus.is_duplicate.return_value = True
        service._handle = AsyncMock()
        await service._process("1-0", _envelope({}))
        service._handle.assert_not_awaited()
        service.jobbus.ack.assert_awaited_once_with(FLOWS_GROUP, "1-0")
        service.jobbus.mark_processed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_job_is_left_pending_for_redelivery(self) -> None:
        """A failing job is not acked or marked, so the sweeper can redeliver it."""
        service = _service()
        service._handle = AsyncMock(side_effect=RuntimeError("pipeline crashed"))
        await service._process("1-0", _envelope({}))
        service.jobbus.ack.assert_not_awaited()
        service.jobbus.mark_processed.assert_not_awaited()
        service.jobbus.publish_result.assert_not_awaited()


class TestLoops:
    """Consumer and sweeper loops."""

    @pytest.fixture
    def naps(self, monkeypatch: pytest.MonkeyPatch) -> list[float]:
        """Replace the loop sleeps with a recorder so tests never wait."""
        delays: list[float] = []

        async def _sleep(delay: float) -> None:
            """Record the requested delay."""
            delays.append(delay)

        monkeypatch.setattr(mod.asyncio, "sleep", _sleep)
        return delays

    @pytest.mark.asyncio
    async def test_consumer_processes_batches_until_stopped(
        self, naps: list[float]
    ) -> None:
        """Each read batch is processed in order; the loop exits once stopped."""
        service = _service()
        service.running = True
        first, second = _envelope({}, job_id="a"), _envelope({}, job_id="b")
        processed: list[tuple[str, str]] = []

        async def process(msg_id: str, envelope: Any) -> None:
            """Record the processed message."""
            processed.append((msg_id, envelope.job_id))

        async def read(*_args: Any, **_kwargs: Any) -> list:
            """Return one batch, then stop the service."""
            service.running = False
            return [("1-0", first), ("2-0", second)]

        service._process = process
        service.jobbus.read = AsyncMock(side_effect=read)
        await service._consumer_loop()
        assert processed == [("1-0", "a"), ("2-0", "b")]
        service.jobbus.read.assert_awaited_once_with(
            FLOWS_GROUP, "pod-a", count=5, block_ms=5000
        )
        assert naps == []

    @pytest.mark.asyncio
    async def test_consumer_backs_off_after_redis_error(
        self, naps: list[float]
    ) -> None:
        """A Redis failure is survived with a one-second backoff before retrying."""
        service = _service()
        service.running = True
        attempts = 0

        async def read(*_args: Any, **_kwargs: Any) -> list:
            """Fail once, then stop the service on the retry."""
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("redis down")
            service.running = False
            return []

        service.jobbus.read = AsyncMock(side_effect=read)
        await service._consumer_loop()
        assert attempts == 2 and naps == [1]

    @pytest.mark.asyncio
    async def test_sweeper_reprocesses_reclaimed_messages(
        self, naps: list[float]
    ) -> None:
        """Stale pending messages are reclaimed every minute and processed again."""
        service = _service()
        service.running = True
        stale = SimpleNamespace(msg_id="9-0", envelope=_envelope({}, job_id="old"))

        async def reclaim(*_args: Any, **_kwargs: Any) -> list:
            """Return one stale message, then stop the service."""
            service.running = False
            return [stale]

        service.jobbus.reclaim_stale = AsyncMock(side_effect=reclaim)
        service._process = AsyncMock()
        await service._sweeper_loop()
        assert naps == [60]
        service.jobbus.reclaim_stale.assert_awaited_once_with(
            FLOWS_GROUP, "pod-a", min_idle_ms=60000
        )
        service._process.assert_awaited_once_with("9-0", stale.envelope)

    @pytest.mark.asyncio
    async def test_sweeper_survives_reclaim_failure(self, naps: list[float]) -> None:
        """A reclaim failure is logged and the sweeper keeps running until stopped."""
        service = _service()
        service.running = True
        calls = 0

        async def reclaim(*_args: Any, **_kwargs: Any) -> list:
            """Fail on the first sweep and stop on the second."""
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("redis down")
            service.running = False
            return []

        service.jobbus.reclaim_stale = AsyncMock(side_effect=reclaim)
        await service._sweeper_loop()
        assert calls == 2 and naps == [60, 60]


class TestHealthcheck:
    """Container health probe."""

    def test_healthy_container(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture
    ) -> None:
        """With config present and a writable workspace the probe prints OK and exits 0."""
        monkeypatch.setenv("REDIS_URL", "redis://cache.invalid/0")
        monkeypatch.setenv("DATABASE_URL", "postgresql://db.invalid/x")
        monkeypatch.setenv("FLOWS_WORKSPACE_ROOT", str(tmp_path / "ws"))
        assert healthcheck.main() == 0
        assert capsys.readouterr().out.strip() == "OK"
        assert list((tmp_path / "ws").iterdir()) == []

    @pytest.mark.parametrize(
        ("missing", "message"),
        [
            ("REDIS_URL", "REDIS_URL not configured"),
            ("DATABASE_URL", "DATABASE_URL not configured"),
        ],
    )
    def test_missing_config_is_unhealthy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
        capsys: pytest.CaptureFixture,
        missing: str,
        message: str,
    ) -> None:
        """A missing connection setting fails the probe and names the setting."""
        monkeypatch.setenv("REDIS_URL", "redis://cache.invalid/0")
        monkeypatch.setenv("DATABASE_URL", "postgresql://db.invalid/x")
        monkeypatch.setenv("FLOWS_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.delenv(missing)
        assert healthcheck.main() == 1
        err = capsys.readouterr().err
        assert message in err and "Check failed: config" in err

    def test_unwritable_workspace_is_unhealthy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture
    ) -> None:
        """A workspace root that cannot be created fails the probe."""
        blocker = tmp_path / "file"
        blocker.write_text("x")
        monkeypatch.setenv("FLOWS_WORKSPACE_ROOT", str(blocker / "ws"))
        assert healthcheck.check_workspace_root() is False
        assert "Workspace root check failed" in capsys.readouterr().err

    def test_import_failure_is_unhealthy(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """A missing core module is reported as an import error."""
        import builtins

        real_import = builtins.__import__

        def failing_import(name: str, *args: Any, **kwargs: Any) -> Any:
            """Fail only the job-bus import the probe depends on."""
            if name == "shared.jobbus":
                raise ImportError("No module named 'shared.jobbus'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", failing_import)
        assert healthcheck.check_imports() is False
        assert "Import error" in capsys.readouterr().err

    def test_crashing_check_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """A check that raises is reported as unhealthy instead of crashing the probe."""
        monkeypatch.setattr(healthcheck, "check_imports", lambda: True)
        monkeypatch.setattr(healthcheck, "check_workspace_root", lambda: True)

        def boom() -> bool:
            """Simulate a check that blows up."""
            raise OSError("disk gone")

        monkeypatch.setattr(healthcheck, "check_config", boom)
        assert healthcheck.main() == 1
        assert "Check error (config): disk gone" in capsys.readouterr().err
