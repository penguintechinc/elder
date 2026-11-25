"""Coverage tests for the base connector SyncResult and context manager."""

import pytest

from apps.worker.connectors.base import BaseConnector, SyncResult


class _Dummy(BaseConnector):
    """Minimal concrete connector recording lifecycle calls."""

    def __init__(self):
        """Create the connector with a call log."""
        super().__init__("dummy")
        self.calls = []

    async def connect(self):
        """Record connect."""
        self.calls.append("connect")

    async def disconnect(self):
        """Record disconnect."""
        self.calls.append("disconnect")

    async def sync(self):
        """Return an empty result."""
        return SyncResult("dummy")

    async def health_check(self):
        """Report healthy."""
        return True


class TestSyncResult:
    """SyncResult aggregation helpers."""

    def test_defaults_and_totals(self):
        """Errors default to a list and operations are summed."""
        r = SyncResult("x", 1, 2, 3, 4)
        assert r.errors == [] and r.total_operations == 10 and not r.has_errors

    def test_errors_and_dict(self):
        """Provided errors flip has_errors and appear in the dict."""
        r = SyncResult("x", errors=["boom"])
        d = r.to_dict()
        assert r.has_errors and d["errors"] == ["boom"] and d["connector"] == "x"
        assert d["has_errors"] is True and d["total_operations"] == 0


class TestContextManager:
    """Async context manager protocol."""

    @pytest.mark.asyncio
    async def test_enter_exit(self):
        """Entering connects and exiting disconnects, even on error."""
        c = _Dummy()
        with pytest.raises(ValueError):
            async with c as got:
                assert got is c
                raise ValueError("x")
        assert c.calls == ["connect", "disconnect"]
        assert await c.sync() and await c.health_check()
