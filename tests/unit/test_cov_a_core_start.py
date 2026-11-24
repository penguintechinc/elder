"""Unit tests for the unified startup script (``apps/api/start.py``).

The real servers (uvicorn, gRPC ``serve``) and process/signal primitives are
replaced at the boundary; the tests assert the configuration the script hands
to them and how the supervisor loop reacts to dead children and signals.
"""

import signal
from unittest.mock import MagicMock, patch

import pytest

from apps.api import start

MOD = "apps.api.start"


class _StopLoop(Exception):
    """Raised by the patched ``time.sleep`` to break an infinite loop."""


def _proc(alive=True, exitcode=None, pid=1234):
    """Build a fake multiprocessing.Process."""
    proc = MagicMock()
    proc.is_alive.return_value = alive
    proc.exitcode = exitcode
    proc.pid = pid
    return proc


class TestRunFlaskServer:
    """run_flask_server hands host/port/workers from the environment to uvicorn."""

    def test_defaults(self, monkeypatch):
        """Without env overrides uvicorn binds 0.0.0.0:5000 with one worker."""
        for var in ("FLASK_HOST", "FLASK_PORT", "FLASK_WORKERS"):
            monkeypatch.delenv(var, raising=False)
        with patch("uvicorn.run") as run:
            start.run_flask_server()
        args, kwargs = run.call_args
        assert args == ("apps.api.main:create_app",)
        assert kwargs["factory"] is True
        assert (kwargs["host"], kwargs["port"], kwargs["workers"]) == (
            "0.0.0.0",
            5000,
            1,
        )

    def test_env_overrides(self, monkeypatch):
        """FLASK_HOST/PORT/WORKERS are parsed and passed through."""
        monkeypatch.setenv("FLASK_HOST", "127.0.0.1")
        monkeypatch.setenv("FLASK_PORT", "8123")
        monkeypatch.setenv("FLASK_WORKERS", "3")
        with patch("uvicorn.run") as run:
            start.run_flask_server()
        kwargs = run.call_args.kwargs
        assert (kwargs["host"], kwargs["port"], kwargs["workers"]) == (
            "127.0.0.1",
            8123,
            3,
        )

    def test_invalid_port_raises(self, monkeypatch):
        """A non-numeric FLASK_PORT fails loudly instead of starting uvicorn."""
        monkeypatch.setenv("FLASK_PORT", "not-a-port")
        with patch("uvicorn.run") as run, pytest.raises(ValueError):
            start.run_flask_server()
        run.assert_not_called()


class TestRunGrpcServer:
    """run_grpc_server idles when disabled and serves with env config when enabled."""

    @pytest.mark.parametrize("value", [None, "false", "FALSE", "0"])
    def test_disabled_idles_without_serving(self, monkeypatch, value):
        """With gRPC disabled the process only sleeps and never calls serve()."""
        if value is None:
            monkeypatch.delenv("GRPC_ENABLED", raising=False)
        else:
            monkeypatch.setenv("GRPC_ENABLED", value)
        with (
            patch("apps.api.grpc.server.serve") as serve,
            patch(f"{MOD}.time.sleep", side_effect=_StopLoop) as sleep,
            pytest.raises(_StopLoop),
        ):
            start.run_grpc_server()
        sleep.assert_called_once_with(3600)
        serve.assert_not_called()

    def test_enabled_serves_with_defaults(self, monkeypatch):
        """Enabled gRPC builds the app and serves on 0.0.0.0:50051, license required."""
        monkeypatch.setenv("GRPC_ENABLED", "true")
        for var in (
            "GRPC_HOST",
            "GRPC_PORT",
            "GRPC_MAX_WORKERS",
            "GRPC_REQUIRE_LICENSE",
        ):
            monkeypatch.delenv(var, raising=False)
        fake_app = object()
        with (
            patch("apps.api.grpc.server.serve") as serve,
            patch("apps.api.main.create_app", return_value=fake_app),
        ):
            start.run_grpc_server()
        serve.assert_called_once_with(
            app=fake_app,
            host="0.0.0.0",
            port=50051,
            max_workers=10,
            require_license=True,
        )

    def test_enabled_env_overrides(self, monkeypatch):
        """GRPC_* overrides (incl. license opt-out) reach serve()."""
        monkeypatch.setenv("GRPC_ENABLED", "True")
        monkeypatch.setenv("GRPC_HOST", "127.0.0.1")
        monkeypatch.setenv("GRPC_PORT", "6000")
        monkeypatch.setenv("GRPC_MAX_WORKERS", "4")
        monkeypatch.setenv("GRPC_REQUIRE_LICENSE", "false")
        with (
            patch("apps.api.grpc.server.serve") as serve,
            patch("apps.api.main.create_app", return_value="app"),
        ):
            start.run_grpc_server()
        kwargs = serve.call_args.kwargs
        assert kwargs == {
            "app": "app",
            "host": "127.0.0.1",
            "port": 6000,
            "max_workers": 4,
            "require_license": False,
        }


class TestMainSupervisor:
    """main() starts both children, supervises them and shuts down on signals."""

    def _run_main(self, monkeypatch, flask, grpc, sleep_effect=_StopLoop):
        """Run main() with fake processes; return (registered handlers, mocks)."""
        handlers = {}
        with (
            patch(f"{MOD}.multiprocessing.set_start_method") as set_start,
            patch(f"{MOD}.multiprocessing.Process", side_effect=[flask, grpc]) as proc,
            patch(
                f"{MOD}.signal.signal",
                side_effect=lambda sig, fn: handlers.__setitem__(sig, fn),
            ),
            patch(f"{MOD}.time.sleep", side_effect=sleep_effect),
        ):
            try:
                start.main()
            finally:
                self.set_start, self.proc = set_start, proc
        return handlers

    def test_starts_both_processes_with_fork(self, monkeypatch):
        """Both servers are started as named child processes using fork."""
        monkeypatch.delenv("GRPC_ENABLED", raising=False)
        flask, grpc = _proc(), _proc()
        with pytest.raises(_StopLoop):
            self._run_main(monkeypatch, flask, grpc)
        self.set_start.assert_called_once_with("fork", force=True)
        targets = [(c.kwargs["target"], c.kwargs["name"]) for c in self.proc.mock_calls]
        assert targets == [
            (start.run_flask_server, "FlaskServer"),
            (start.run_grpc_server, "gRPCServer"),
        ]
        flask.start.assert_called_once_with()
        grpc.start.assert_called_once_with()

    def test_flask_death_exits_1(self, monkeypatch):
        """A dead REST process makes the supervisor exit non-zero."""
        flask, grpc = _proc(alive=False, exitcode=9), _proc()
        with pytest.raises(SystemExit) as exc:
            self._run_main(monkeypatch, flask, grpc)
        assert exc.value.code == 1

    def test_grpc_death_exits_1_when_enabled(self, monkeypatch):
        """A dead gRPC process is fatal when gRPC is enabled."""
        monkeypatch.setenv("GRPC_ENABLED", "true")
        flask, grpc = _proc(), _proc(alive=False, exitcode=2)
        with pytest.raises(SystemExit) as exc:
            self._run_main(monkeypatch, flask, grpc)
        assert exc.value.code == 1

    def test_grpc_death_ignored_when_disabled(self, monkeypatch):
        """A dead gRPC process is ignored when gRPC is disabled (loop keeps going)."""
        monkeypatch.setenv("GRPC_ENABLED", "false")
        flask, grpc = _proc(), _proc(alive=False, exitcode=2)
        with pytest.raises(_StopLoop):
            self._run_main(monkeypatch, flask, grpc)

    def test_registers_sigint_and_sigterm(self, monkeypatch):
        """The same shutdown handler is installed for SIGINT and SIGTERM."""
        handlers = {}
        flask, grpc = _proc(), _proc()
        with (
            patch(f"{MOD}.multiprocessing.set_start_method"),
            patch(f"{MOD}.multiprocessing.Process", side_effect=[flask, grpc]),
            patch(
                f"{MOD}.signal.signal",
                side_effect=lambda sig, fn: handlers.__setitem__(sig, fn),
            ),
            patch(f"{MOD}.time.sleep", side_effect=_StopLoop),
            pytest.raises(_StopLoop),
        ):
            start.main()
        assert set(handlers) == {signal.SIGINT, signal.SIGTERM}
        assert handlers[signal.SIGINT] is handlers[signal.SIGTERM]

    @pytest.mark.parametrize(
        "still_alive_after_join, expect_kill",
        [(False, False), (True, True)],
    )
    def test_shutdown_handler_terminates_then_kills(
        self, monkeypatch, still_alive_after_join, expect_kill
    ):
        """Shutdown terminates live children, kills stragglers, and exits 0."""
        monkeypatch.delenv("GRPC_ENABLED", raising=False)
        handlers = {}
        flask, grpc = _proc(), _proc()
        with (
            patch(f"{MOD}.multiprocessing.set_start_method"),
            patch(f"{MOD}.multiprocessing.Process", side_effect=[flask, grpc]),
            patch(
                f"{MOD}.signal.signal",
                side_effect=lambda sig, fn: handlers.__setitem__(sig, fn),
            ),
            patch(f"{MOD}.time.sleep", side_effect=_StopLoop),
            pytest.raises(_StopLoop),
        ):
            start.main()
        for proc in (flask, grpc):
            proc.is_alive.side_effect = [True, still_alive_after_join]
        with pytest.raises(SystemExit) as exc:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert exc.value.code == 0
        for proc in (flask, grpc):
            proc.terminate.assert_called_once_with()
            proc.join.assert_called_once_with(timeout=5)
            assert proc.kill.called is expect_kill

    def test_shutdown_handler_skips_dead_children(self, monkeypatch):
        """Already-dead children are not terminated again on shutdown."""
        monkeypatch.delenv("GRPC_ENABLED", raising=False)
        handlers = {}
        flask, grpc = _proc(), _proc()
        with (
            patch(f"{MOD}.multiprocessing.set_start_method"),
            patch(f"{MOD}.multiprocessing.Process", side_effect=[flask, grpc]),
            patch(
                f"{MOD}.signal.signal",
                side_effect=lambda sig, fn: handlers.__setitem__(sig, fn),
            ),
            patch(f"{MOD}.time.sleep", side_effect=_StopLoop),
            pytest.raises(_StopLoop),
        ):
            start.main()
        flask.is_alive.return_value = False
        grpc.is_alive.return_value = False
        with pytest.raises(SystemExit) as exc:
            handlers[signal.SIGINT](signal.SIGINT, None)
        assert exc.value.code == 0
        flask.terminate.assert_not_called()
        grpc.terminate.assert_not_called()

    def test_keyboard_interrupt_runs_shutdown(self, monkeypatch):
        """Ctrl-C inside the monitor loop triggers a clean shutdown (exit 0)."""
        monkeypatch.delenv("GRPC_ENABLED", raising=False)
        flask, grpc = _proc(), _proc()
        with pytest.raises(SystemExit) as exc:
            self._run_main(monkeypatch, flask, grpc, sleep_effect=KeyboardInterrupt)
        assert exc.value.code == 0
        flask.terminate.assert_called_once_with()
        grpc.terminate.assert_called_once_with()
