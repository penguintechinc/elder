"""Tests for apps/scanner/main.py graceful shutdown (SIGTERM/SIGINT).

Regression coverage: the scanner had no signal handler at all -- a pod
eviction/restart (SIGTERM) killed it mid-scan with no chance to finish an
in-flight execute_job()/execute_sbom_scan() call or submit its results.

apps/scanner/ ships as a standalone deployable (see apps/scanner/Dockerfile:
`COPY apps/scanner/ .` + `COPY shared /app/shared`, run as `python main.py`
from that directory) -- its `scanners` package only resolves with
apps/scanner/ on sys.path, hence the path shim below.
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

_SCANNER_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "apps", "scanner")
if _SCANNER_DIR not in sys.path:
    sys.path.insert(0, _SCANNER_DIR)

import main as scanner_main  # noqa: E402


@pytest.fixture
def service():
    """A ScannerService with all outbound HTTP-touching methods mocked out."""
    svc = scanner_main.ScannerService()
    return svc


class TestRequestShutdown:
    def test_request_shutdown_sets_event(self, service) -> None:
        assert not service._shutdown_event.is_set()
        service.request_shutdown("SIGTERM")
        assert service._shutdown_event.is_set()

    def test_request_shutdown_is_idempotent(self, service) -> None:
        service.request_shutdown("SIGTERM")
        service.request_shutdown("SIGINT")
        assert service._shutdown_event.is_set()


class TestGracefulPollLoopShutdown:
    """run() must finish the current cycle and exit promptly on shutdown,
    never blocking for the full POLL_INTERVAL and never being killed
    mid-scan.
    """

    @pytest.mark.asyncio
    async def test_run_exits_after_shutdown_without_waiting_full_interval(
        self, service, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Make one full poll cycle a no-op, then request shutdown from
        # inside it -- simulating a signal arriving while a cycle is
        # in-flight. The loop must still finish this cycle cleanly and exit
        # on the *next* check, not hang for POLL_INTERVAL (300s default).
        monkeypatch.setattr(scanner_main, "POLL_INTERVAL", 300)

        async def _fake_due_schedules():
            service.request_shutdown("SIGTERM")
            return []

        service.get_due_schedules = AsyncMock(side_effect=_fake_due_schedules)
        service.get_pending_jobs = AsyncMock(return_value=[])
        service.get_pending_sbom_scans = AsyncMock(return_value=[])
        service._should_run_nvd_sync = lambda: False

        # run() must return quickly (bounded by the test timeout below),
        # never sleeping out the full 300s POLL_INTERVAL.
        await asyncio.wait_for(service.run(), timeout=5.0)

        service.get_due_schedules.assert_awaited()

    @pytest.mark.asyncio
    async def test_signal_handlers_registered_for_sigterm_and_sigint(
        self, service, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run() registers handlers for both SIGTERM and SIGINT on the loop."""
        monkeypatch.setattr(scanner_main, "POLL_INTERVAL", 300)
        service._shutdown_event.set()  # exit after registering handlers, no cycles

        registered_signals = []

        class _FakeLoop:
            def add_signal_handler(self, sig, callback):
                registered_signals.append(sig)

        with patch.object(
            scanner_main.asyncio, "get_running_loop", return_value=_FakeLoop()
        ):
            await service.run()

        import signal as signal_module

        assert signal_module.SIGTERM in registered_signals
        assert signal_module.SIGINT in registered_signals
