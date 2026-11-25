"""Unit coverage for apps/worker/main.py (WorkerService orchestrator).

Redis/JobBus, the DB manager, aiocron, hypercorn and the CSV runners are the
only replaced collaborators; the service's own control flow runs for real.
"""

from __future__ import annotations

import tests.unit.conftest_worker_stubs  # noqa: F401  isort: skip

import asyncio
import importlib
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.worker import main as wmain
from apps.worker.config.settings import settings
from shared.jobbus import JobEnvelope


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A WorkerService built without a database URL."""
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.delenv("POD_NAME", raising=False)
    return wmain.WorkerService()


def _envelope(job_id: str = "j1") -> JobEnvelope:
    """Build a minimal job envelope."""
    return JobEnvelope(
        job_id=job_id,
        job_type="t",
        payload={},
        enqueued_at="2026-01-01T00:00:00Z",
        tenant_id=1,
    )


class StopAfter:
    """Awaitable side-effect that flips ``service.running`` to False."""

    def __init__(self, service: Any) -> None:
        """Remember the service to stop."""
        self.service = service

    async def __call__(self, *_a: Any, **_k: Any) -> None:
        """Stop the service loop when awaited."""
        self.service.running = False


# --------------------------------------------------------------------------- #
# Construction, health endpoints, OTel
# --------------------------------------------------------------------------- #


class TestInit:
    """Construction and health endpoints."""

    def test_pod_name_used_for_consumer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POD_NAME env var is the consumer name."""
        monkeypatch.setattr(settings, "database_url", "")
        monkeypatch.setenv("POD_NAME", "pod-1")
        assert wmain.WorkerService().consumer_name == "pod-1"

    def test_database_initialised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A configured URL builds a DatabaseManager."""
        monkeypatch.setattr(settings, "database_url", "postgresql://u@h/d")
        mgr = MagicMock()
        with patch("shared.database.manager.DatabaseManager", return_value=mgr):
            assert wmain.WorkerService().db_manager is mgr

    def test_database_failure_tolerated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A DB init failure leaves the worker running without a DB."""
        monkeypatch.setattr(settings, "database_url", "postgresql://u@h/d")
        with patch(
            "shared.database.manager.DatabaseManager", side_effect=RuntimeError("no")
        ):
            assert wmain.WorkerService().db_manager is None

    @pytest.mark.asyncio
    async def test_health_endpoints(self, svc: Any) -> None:
        """/healthz and /status reflect running state."""
        client = svc.health_app.test_client()
        resp = await client.get("/healthz")
        assert (await resp.get_json())["status"] == "stopped"
        svc.running = True
        svc.worker_groups = {"b", "a"}
        resp = await client.get("/healthz")
        assert (await resp.get_json())["status"] == "healthy"
        body = await (await client.get("/status")).get_json()
        assert body["worker_groups"] == ["a", "b"]
        assert body["jobbus_connected"] is False
        assert body["database_connected"] is False


def test_otel_metrics_initialised() -> None:
    """_init_otel_metrics returns all instruments."""
    metrics = wmain._init_otel_metrics()
    assert {"job_counter", "job_duration_histogram"} <= set(metrics)


def test_otel_failure_at_import_is_tolerated() -> None:
    """A metrics init error at import leaves _otel_metrics as None."""
    try:
        with patch("shared.observability.get_meter", side_effect=RuntimeError("x")):
            importlib.reload(wmain)
        assert wmain._otel_metrics is None
    finally:
        importlib.reload(wmain)
    assert wmain._otel_metrics is not None


# --------------------------------------------------------------------------- #
# Job bus init + consumer
# --------------------------------------------------------------------------- #


class TestJobbusInit:
    """_init_jobbus."""

    @pytest.mark.asyncio
    async def test_success(self, svc: Any) -> None:
        """Groups are resolved and ensured; a failing group is tolerated."""
        bus = MagicMock()
        bus.ensure_group = AsyncMock(side_effect=[None, RuntimeError("x")])
        with (
            patch.object(wmain.redis.asyncio, "from_url", return_value=MagicMock()),
            patch.object(wmain, "JobBus", return_value=bus),
            patch.object(wmain, "resolve_worker_groups", return_value={"a", "b"}),
        ):
            await svc._init_jobbus()
        assert svc.jobbus is bus
        assert bus.ensure_group.await_count == 2

    @pytest.mark.asyncio
    async def test_no_groups(self, svc: Any) -> None:
        """No enabled groups returns early."""
        with (
            patch.object(wmain.redis.asyncio, "from_url", return_value=MagicMock()),
            patch.object(wmain, "JobBus", return_value=MagicMock()),
            patch.object(wmain, "resolve_worker_groups", return_value=set()),
        ):
            await svc._init_jobbus()
        assert svc.worker_groups == set()

    @pytest.mark.asyncio
    async def test_connection_failure_swallowed(self, svc: Any) -> None:
        """A Redis failure is logged and the worker continues."""
        with patch.object(
            wmain.redis.asyncio, "from_url", side_effect=RuntimeError("down")
        ):
            await svc._init_jobbus()
        assert svc.jobbus is None


class TestConsumerLoop:
    """_consumer_loop."""

    @pytest.mark.asyncio
    async def test_no_jobbus(self, svc: Any) -> None:
        """Without a JobBus the loop exits immediately."""
        await svc._consumer_loop()

    @pytest.mark.asyncio
    async def test_reads_and_processes(self, svc: Any) -> None:
        """Messages are processed; empty reads and read errors are skipped."""
        svc.running = True
        svc.worker_groups = {"g"}
        svc.jobbus = MagicMock()
        stopper = StopAfter(svc)

        async def read(*_a: Any, **_k: Any) -> Any:
            """Return messages once, then empty, then error, then stop."""
            read.calls += 1
            if read.calls == 1:
                return [("1-0", _envelope())]
            if read.calls == 2:
                return []
            if read.calls == 3:
                raise RuntimeError("redis")
            await stopper()
            return []

        read.calls = 0
        svc.jobbus.read = read
        svc._process_job = AsyncMock()
        await svc._consumer_loop()
        svc._process_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_outer_error_sleeps(self, svc: Any) -> None:
        """An error outside the per-group handler backs off before retrying."""

        class BadGroups:
            """Iterable that fails when iterated."""

            def __iter__(self) -> Any:
                """Raise to hit the outer handler."""
                raise RuntimeError("iter")

            def __bool__(self) -> bool:
                """Truthy so the service treats groups as configured."""
                return True

        svc.running = True
        svc.jobbus = MagicMock()
        svc.worker_groups = BadGroups()  # type: ignore[assignment]
        with patch.object(wmain.asyncio, "sleep", StopAfter(svc)):
            # sorted(self.worker_groups) in the startup log also iterates; guard it
            with patch.object(wmain, "sorted", lambda x: [], create=True):
                await svc._consumer_loop()
        assert svc.running is False


class TestProcessJob:
    """_process_job and _process_job_body."""

    @pytest.mark.asyncio
    async def test_process_job_records_metrics(self, svc: Any) -> None:
        """The wrapper delegates and records duration."""
        svc._process_job_body = AsyncMock(return_value="success")
        await svc._process_job("g", "1-0", _envelope())
        svc._process_job_body.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_job_without_metrics(self, svc: Any) -> None:
        """A missing metrics dict is tolerated."""
        svc._process_job_body = AsyncMock(return_value="error")
        with patch.object(wmain, "_otel_metrics", None):
            await svc._process_job("g", "1-0", _envelope())

    @pytest.mark.asyncio
    async def test_duplicate(self, svc: Any) -> None:
        """Duplicates are acked and skipped."""
        svc.jobbus = MagicMock()
        svc.jobbus.is_duplicate = AsyncMock(return_value=True)
        svc.jobbus.ack = AsyncMock()
        assert await svc._process_job_body("g", "1", _envelope()) == "duplicate"
        svc.jobbus.ack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_handler(self, svc: Any) -> None:
        """A group without a handler is acked to avoid replay."""
        svc.jobbus = MagicMock()
        svc.jobbus.is_duplicate = AsyncMock(return_value=False)
        svc.jobbus.ack = AsyncMock()
        with patch.object(wmain, "get_handler", return_value=None):
            assert await svc._process_job_body("g", "1", _envelope()) == "no_handler"
        svc.jobbus.ack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success(self, svc: Any) -> None:
        """A handler result is published, marked processed and acked."""
        svc.jobbus = MagicMock()
        svc.jobbus.is_duplicate = AsyncMock(return_value=False)
        svc.jobbus.mark_processed = AsyncMock()
        svc.jobbus.publish_result = AsyncMock()
        svc.jobbus.ack = AsyncMock()
        handler = AsyncMock(return_value=None)
        with patch.object(wmain, "get_handler", return_value=handler):
            assert await svc._process_job_body("g", "1", _envelope()) == "success"
        assert svc.jobbus.publish_result.await_args.args[3] == {}

    @pytest.mark.asyncio
    async def test_handler_error_not_acked(self, svc: Any) -> None:
        """Handler exceptions leave the message pending."""
        svc.jobbus = MagicMock()
        svc.jobbus.is_duplicate = AsyncMock(return_value=False)
        svc.jobbus.ack = AsyncMock()
        handler = AsyncMock(side_effect=RuntimeError("bad"))
        with patch.object(wmain, "get_handler", return_value=handler):
            assert await svc._process_job_body("g", "1", _envelope()) == "error"
        svc.jobbus.ack.assert_not_awaited()


class TestSweeper:
    """_sweeper_task."""

    @pytest.mark.asyncio
    async def test_no_jobbus(self, svc: Any) -> None:
        """No JobBus means nothing to sweep."""
        await svc._sweeper_task()

    @pytest.mark.asyncio
    async def test_reclaims_and_errors(self, svc: Any) -> None:
        """Reclaimed messages are re-dispatched; failures do not abort."""
        svc.worker_groups = {"empty", "full", "bad"}
        reclaimed = [SimpleNamespace(msg_id="1-0", envelope=_envelope())]

        async def reclaim(group: str, *_a: Any, **_k: Any) -> Any:
            """Return per-group results."""
            if group == "bad":
                raise RuntimeError("x")
            return reclaimed if group == "full" else []

        svc.jobbus = MagicMock()
        svc.jobbus.reclaim_stale = reclaim
        svc._process_job = AsyncMock()
        await svc._sweeper_task()
        svc._process_job.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Heartbeat
# --------------------------------------------------------------------------- #


class TestHeartbeat:
    """_service_node_heartbeat."""

    @pytest.mark.asyncio
    async def test_no_db(self, svc: Any) -> None:
        """No DB manager is a no-op."""
        await svc._service_node_heartbeat()

    @pytest.mark.asyncio
    async def test_already_registered(self, svc: Any) -> None:
        """An existing registration only refreshes the heartbeat."""
        svc.db_manager = MagicMock()
        with (
            patch("apps.api.models.service_node.heartbeat_node", return_value=True),
            patch("apps.api.models.service_node.register_node") as reg,
            patch("apps.api.common.licensing.enforce.check_limit", AsyncMock()),
        ):
            await svc._service_node_heartbeat()
        reg.assert_not_called()

    @pytest.mark.asyncio
    async def test_registers_new_node(
        self, svc: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A first heartbeat checks the node limit then registers."""
        monkeypatch.setenv("HOSTNAME", "host-a")
        svc.db_manager = MagicMock()
        check = AsyncMock(return_value=None)
        with (
            patch("apps.api.models.service_node.heartbeat_node", return_value=False),
            patch("apps.api.models.service_node.register_node") as reg,
            patch("apps.api.common.licensing.enforce.check_limit", check),
        ):
            await svc._service_node_heartbeat()
        check.assert_awaited_once_with("node", None)
        assert reg.call_args.args[2] == "host-a"

    @pytest.mark.asyncio
    async def test_failure_swallowed(self, svc: Any) -> None:
        """Errors never escape the heartbeat."""
        svc.db_manager = MagicMock()
        with patch(
            "apps.api.models.service_node.heartbeat_node", side_effect=RuntimeError("x")
        ):
            await svc._service_node_heartbeat()


# --------------------------------------------------------------------------- #
# CSV import helpers and loops
# --------------------------------------------------------------------------- #


class TestCsvHelpers:
    """CSV interval + thread-offloaded runners."""

    def test_interval_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A valid env value wins and is clamped to >=1."""
        monkeypatch.setenv("CSV_IMPORT_POLL_INTERVAL_SECONDS", "0")
        assert wmain.WorkerService._get_csv_import_poll_interval() == 1
        monkeypatch.setenv("CSV_IMPORT_POLL_INTERVAL_SECONDS", "42")
        assert wmain.WorkerService._get_csv_import_poll_interval() == 42

    def test_interval_invalid_and_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid or blank values fall back to the settings value."""
        expected = max(1, int(settings.csv_import_poll_interval_seconds))
        monkeypatch.setenv("CSV_IMPORT_POLL_INTERVAL_SECONDS", "abc")
        assert wmain.WorkerService._get_csv_import_poll_interval() == expected
        monkeypatch.setenv("CSV_IMPORT_POLL_INTERVAL_SECONDS", "  ")
        assert wmain.WorkerService._get_csv_import_poll_interval() == expected
        monkeypatch.delenv("CSV_IMPORT_POLL_INTERVAL_SECONDS")
        assert wmain.WorkerService._get_csv_import_poll_interval() == expected

    @pytest.mark.asyncio
    async def test_batch_once(self, svc: Any) -> None:
        """The batch runner is invoked with the DB writer when present."""
        svc.db_manager = SimpleNamespace(write="W")
        with patch.object(wmain, "run_batch_once", return_value=0) as run:
            assert await svc._run_csv_import_batch_once() == 0
        assert run.call_args.kwargs["db"] == "W"

    @pytest.mark.asyncio
    async def test_batch_once_error(self, svc: Any) -> None:
        """A batch failure is converted to the error code 1."""
        with patch.object(wmain, "run_batch_once", side_effect=RuntimeError("x")):
            assert await svc._run_csv_import_batch_once() == 1

    @pytest.mark.asyncio
    async def test_process_job_id(self, svc: Any) -> None:
        """A queued job id is processed off-loop."""
        svc.db_manager = SimpleNamespace(write="W")
        with patch.object(wmain, "process_job_by_id", return_value="completed"):
            assert await svc._process_csv_import_job_id("j") == "completed"

    @pytest.mark.asyncio
    async def test_process_job_id_error(self, svc: Any) -> None:
        """Processing errors return None."""
        with patch.object(wmain, "process_job_by_id", side_effect=RuntimeError("x")):
            assert await svc._process_csv_import_job_id("j") is None


class TestCsvQueueLoop:
    """_csv_import_queue_loop."""

    @pytest.mark.asyncio
    async def test_no_redis(self, svc: Any) -> None:
        """Without Redis the consumer does not start."""
        await svc._csv_import_queue_loop()

    @pytest.mark.asyncio
    async def test_storage_unconfigured(self, svc: Any) -> None:
        """Unconfigured S3 disables the consumer."""
        svc.redis = MagicMock()
        with patch.object(
            wmain,
            "get_csv_import_storage",
            return_value=SimpleNamespace(is_configured=False),
        ):
            await svc._csv_import_queue_loop()

    @pytest.mark.asyncio
    async def test_consumes_items(self, svc: Any) -> None:
        """Items are decoded, blanks skipped, errors back off, loop ends."""
        svc.running = True
        svc.redis = MagicMock()
        stopper = StopAfter(svc)
        items = [
            None,
            ("q", b"job-a"),
            ("q", "   "),
            ("q", "job-b"),
            RuntimeError("redis"),
        ]

        async def brpop(*_a: Any, **_k: Any) -> Any:
            """Yield canned items, then stop the loop."""
            if items:
                nxt = items.pop(0)
                if isinstance(nxt, Exception):
                    raise nxt
                return nxt
            await stopper()
            return None

        svc.redis.brpop = brpop
        svc._process_csv_import_job_id = AsyncMock()
        with (
            patch.object(
                wmain,
                "get_csv_import_storage",
                return_value=SimpleNamespace(is_configured=True),
            ),
            patch.object(wmain.asyncio, "sleep", AsyncMock()),
        ):
            await svc._csv_import_queue_loop()
        ids = [c.args[0] for c in svc._process_csv_import_job_id.await_args_list]
        assert ids == ["job-a", "job-b"]

    @pytest.mark.asyncio
    async def test_cancellation_propagates(self, svc: Any) -> None:
        """Cancellation inside the loop is re-raised."""
        svc.running = True
        svc.redis = MagicMock()
        svc.redis.brpop = AsyncMock(side_effect=asyncio.CancelledError())
        with patch.object(
            wmain,
            "get_csv_import_storage",
            return_value=SimpleNamespace(is_configured=True),
        ):
            with pytest.raises(asyncio.CancelledError):
                await svc._csv_import_queue_loop()


class TestCsvLoop:
    """_csv_import_loop."""

    @pytest.mark.asyncio
    async def test_runs_until_stopped(self, svc: Any) -> None:
        """The loop runs the batch and sleeps until running turns False."""
        svc.running = True
        svc._run_csv_import_batch_once = AsyncMock(return_value=0)
        with patch.object(wmain.asyncio, "sleep", StopAfter(svc)):
            await svc._csv_import_loop()
        svc._run_csv_import_batch_once.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stops_after_batch(self, svc: Any) -> None:
        """If the service stops during the batch, no sleep happens."""
        svc.running = True

        async def batch() -> int:
            """Stop the service mid-batch."""
            svc.running = False
            return 0

        svc._run_csv_import_batch_once = batch
        await svc._csv_import_loop()

    @pytest.mark.asyncio
    async def test_cancel_reraised(self, svc: Any) -> None:
        """Cancellation is propagated."""
        svc.running = True
        svc._run_csv_import_batch_once = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await svc._csv_import_loop()


# --------------------------------------------------------------------------- #
# Scheduled syncs
# --------------------------------------------------------------------------- #


class TestScheduled:
    """_setup_scheduled_syncs and the cron callbacks it registers."""

    @staticmethod
    def _capture() -> tuple[Any, dict[str, Any]]:
        """Patch aiocron.crontab to capture decorated coroutines by name."""
        captured: dict[str, Any] = {}

        def crontab(_expr: str) -> Any:
            """Return a decorator that records the function."""

            def deco(fn: Any) -> Any:
                """Record and return fn."""
                captured[fn.__name__] = fn
                return fn

            return deco

        return patch.object(wmain.aiocron, "crontab", crontab), captured

    @pytest.mark.asyncio
    async def test_heartbeat_only(self, svc: Any) -> None:
        """Without a JobBus only the heartbeat is scheduled."""
        ctx, captured = self._capture()
        svc._service_node_heartbeat = AsyncMock()
        with ctx:
            svc._setup_scheduled_syncs()
        assert list(captured) == ["service_node_heartbeat"]
        await captured["service_node_heartbeat"]()
        svc._service_node_heartbeat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sweeper_and_sla_scheduler(
        self, svc: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With groups, the sweeper and SLA scheduler are registered and work."""
        monkeypatch.setattr(settings, "database_url", "postgresql://u@h/d")
        svc.jobbus = MagicMock()
        svc.jobbus.enqueue = AsyncMock()
        svc.worker_groups = {"helpdesk_sla_breach"}
        svc._sweeper_task = AsyncMock()
        ctx, captured = self._capture()
        with ctx:
            svc._setup_scheduled_syncs()
        assert {"xautoclaim_sweeper", "helpdesk_sla_breach_scheduler"} <= set(captured)
        await captured["xautoclaim_sweeper"]()
        svc._sweeper_task.assert_awaited_once()

        mgr = MagicMock()
        mgr.write.return_value.select.return_value = [
            SimpleNamespace(id=1),
            SimpleNamespace(id=2),
        ]
        with patch("shared.database.manager.DatabaseManager", return_value=mgr):
            await captured["helpdesk_sla_breach_scheduler"]()
        assert svc.jobbus.enqueue.await_count == 2
        kwargs = svc.jobbus.enqueue.await_args_list[0].kwargs
        assert kwargs["idempotency_key"] == "hd_sla_breach_1"
        mgr.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_sla_scheduler_error_swallowed(
        self, svc: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scheduler errors are logged, not raised."""
        monkeypatch.setattr(settings, "database_url", "postgresql://u@h/d")
        svc.jobbus = MagicMock()
        svc.worker_groups = {"helpdesk_sla_breach"}
        ctx, captured = self._capture()
        with ctx:
            svc._setup_scheduled_syncs()
        with patch(
            "shared.database.manager.DatabaseManager", side_effect=RuntimeError("x")
        ):
            await captured["helpdesk_sla_breach_scheduler"]()


# --------------------------------------------------------------------------- #
# start / stop / health server / main
# --------------------------------------------------------------------------- #


class TestLifecycle:
    """start, stop, run_health_server and main."""

    @pytest.mark.asyncio
    async def test_start_launches_tasks_and_stop_cancels(self, svc: Any) -> None:
        """start wires consumers; stop cancels tasks and closes resources."""

        async def fake_init() -> None:
            """Install a JobBus and Redis as _init_jobbus would."""
            svc.jobbus = MagicMock()
            svc.redis = MagicMock()
            svc.redis.close = AsyncMock()
            svc.worker_groups = {"g"}

        async def forever(*_a: Any, **_k: Any) -> None:
            """Block until cancelled."""
            await asyncio.sleep(3600)

        svc._init_jobbus = fake_init
        svc._service_node_heartbeat = AsyncMock()
        svc._setup_scheduled_syncs = MagicMock()
        svc._consumer_loop = forever
        svc._csv_import_queue_loop = forever
        svc._csv_import_loop = forever
        svc.db_manager = MagicMock()
        svc.sync_tasks = [asyncio.create_task(forever())]
        with patch.object(
            wmain,
            "get_csv_import_storage",
            return_value=SimpleNamespace(is_configured=True),
        ):
            await svc.start()
        assert svc.running is True
        assert svc.consumer_task and svc.csv_import_queue_task and svc.csv_import_task
        await svc.stop()
        assert svc.running is False
        assert svc.consumer_task.cancelled()
        svc.redis.close.assert_awaited_once()
        svc.db_manager.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_minimal(self, svc: Any) -> None:
        """Without Redis/JobBus only the CSV sweep task starts."""

        async def forever() -> None:
            """Block until cancelled."""
            await asyncio.sleep(3600)

        svc._init_jobbus = AsyncMock()
        svc._service_node_heartbeat = AsyncMock()
        svc._setup_scheduled_syncs = MagicMock()
        svc._csv_import_loop = forever
        with patch.object(
            wmain,
            "get_csv_import_storage",
            return_value=SimpleNamespace(is_configured=True),
        ):
            await svc.start()
        assert svc.consumer_task is None and svc.csv_import_queue_task is None
        await svc.stop()

    @pytest.mark.asyncio
    async def test_stop_with_nothing_running(self, svc: Any) -> None:
        """stop is safe when nothing was started."""
        await svc.stop()
        assert svc.running is False

    @pytest.mark.asyncio
    async def test_stop_skips_done_tasks(self, svc: Any) -> None:
        """Completed tasks are not cancelled again."""

        async def noop() -> None:
            """Finish immediately."""

        tasks = [asyncio.create_task(noop()) for _ in range(3)]
        await asyncio.gather(*tasks)
        svc.consumer_task, svc.csv_import_queue_task, svc.csv_import_task = tasks
        await svc.stop()

    def test_run_health_server(self, svc: Any) -> None:
        """The health server is launched on a thread via hypercorn."""
        served: list[Any] = []
        real_thread = threading.Thread

        async def fake_serve(app: Any, config: Any, **_k: Any) -> None:
            """Record the served app and config."""
            served.append((app, config))

        class FakeThread:
            """Thread double that runs the target on a real thread at start."""

            def __init__(self, target: Any, daemon: bool = False) -> None:
                """Remember the target."""
                self.target = target

            def start(self) -> None:
                """Run the target to completion on a helper thread."""
                t = real_thread(target=self.target)
                t.start()
                t.join(10)

        with (
            patch("hypercorn.asyncio.serve", fake_serve),
            patch("threading.Thread", FakeThread),
        ):
            svc.run_health_server()
        assert served and served[0][0] is svc.health_app
        assert served[0][1].bind == [f"0.0.0.0:{settings.health_check_port}"]


class FakeService:
    """WorkerService double for main()."""

    instances: list[FakeService] = []

    def __init__(self) -> None:
        """Track the instance."""
        self.running = False
        self.stopped = False
        self.fail = False
        FakeService.instances.append(self)

    def run_health_server(self) -> None:
        """No-op health server."""

    async def start(self) -> None:
        """Mark running, or fail when requested."""
        if self.fail:
            raise RuntimeError("fatal")
        self.running = True

    async def stop(self) -> None:
        """Record the stop."""
        self.stopped = True
        self.running = False


class TestMain:
    """main() entry point."""

    @pytest.mark.asyncio
    async def test_runs_until_stopped(self) -> None:
        """main starts the service and exits once running turns False."""
        FakeService.instances = []
        handlers: dict[Any, Any] = {}
        real_sleep = asyncio.sleep

        async def fast_sleep(_s: float) -> None:
            """Stop the service on first sleep."""
            FakeService.instances[0].running = False

        with (
            patch.object(wmain, "WorkerService", FakeService),
            patch.object(
                wmain.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn)
            ),
            patch.object(wmain.asyncio, "sleep", fast_sleep),
        ):
            await wmain.main()
            inst = FakeService.instances[0]
            assert inst.stopped is True
            # signal handler schedules a stop task
            inst.stopped = False
            handlers[wmain.signal.SIGTERM](wmain.signal.SIGTERM, None)
            await real_sleep(0)
            await real_sleep(0)
        assert inst.stopped is True

    @pytest.mark.asyncio
    async def test_fatal_error_exits(self) -> None:
        """A startup failure logs and exits with status 1 after stopping."""
        FakeService.instances = []

        class Failing(FakeService):
            """Service whose start always fails."""

            def __init__(self) -> None:
                """Configure failure."""
                super().__init__()
                self.fail = True

        with (
            patch.object(wmain, "WorkerService", Failing),
            patch.object(wmain.signal, "signal", lambda *_a: None),
        ):
            with pytest.raises(SystemExit) as exc:
                await wmain.main()
        assert exc.value.code == 1
        assert FakeService.instances[0].stopped is True
