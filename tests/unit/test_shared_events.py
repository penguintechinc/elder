"""Unit tests for shared.events.emit_event.

emit_event publishes an event envelope to a Redis Stream and is designed to be
fail-soft: Redis problems are logged, never raised. These tests mock both
``redis`` and Quart's ``current_app`` so no live Redis is required, and assert
the envelope shape, the stream key, and every fail-soft branch.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from shared import events


class _FakeApp:
    def __init__(self, redis_url="redis://localhost:6379/0"):
        self.config = {"REDIS_URL": redis_url}


@pytest.mark.asyncio
async def test_emit_event_publishes_envelope_to_stream():
    fake_redis_client = MagicMock()
    fake_redis_module = MagicMock()
    fake_redis_module.from_url.return_value = fake_redis_client

    with (
        patch.dict("sys.modules", {"redis": fake_redis_module}),
        patch("quart.current_app", _FakeApp("redis://r:6379/2")),
    ):
        await events.emit_event(
            domain="issues",
            action="created",
            resource_id=123,
            tenant_id=7,
            payload={"title": "x"},
        )

    fake_redis_module.from_url.assert_called_once_with("redis://r:6379/2")
    # One xadd to the domain-specific stream key.
    fake_redis_client.xadd.assert_called_once()
    stream_key, fields = fake_redis_client.xadd.call_args[0]
    assert stream_key == "elder:events:issues"
    envelope = json.loads(fields["event"])
    assert envelope["domain"] == "issues"
    assert envelope["action"] == "created"
    assert envelope["resource_id"] == "123"  # coerced to str
    assert envelope["tenant_id"] == 7
    assert envelope["payload"] == {"title": "x"}
    assert "event_id" in envelope and envelope["event_id"]
    assert "emitted_at" in envelope


@pytest.mark.asyncio
async def test_emit_event_defaults_payload_to_empty_dict():
    fake_redis_client = MagicMock()
    fake_redis_module = MagicMock()
    fake_redis_module.from_url.return_value = fake_redis_client

    with (
        patch.dict("sys.modules", {"redis": fake_redis_module}),
        patch("quart.current_app", _FakeApp()),
    ):
        await events.emit_event(domain="discovery", action="updated", resource_id="abc")

    _, fields = fake_redis_client.xadd.call_args[0]
    envelope = json.loads(fields["event"])
    assert envelope["payload"] == {}
    assert envelope["tenant_id"] is None
    assert envelope["resource_id"] == "abc"


@pytest.mark.asyncio
async def test_emit_event_soft_fails_on_connection_error():
    """A Redis ConnectionError is swallowed (logged), never raised."""
    fake_redis_module = MagicMock()

    class _ConnErr(Exception):
        pass

    fake_redis_module.ConnectionError = _ConnErr
    fake_redis_module.from_url.side_effect = _ConnErr("down")

    with (
        patch.dict("sys.modules", {"redis": fake_redis_module}),
        patch("quart.current_app", _FakeApp()),
    ):
        # Must not raise.
        await events.emit_event(domain="d", action="a", resource_id=1)


@pytest.mark.asyncio
async def test_emit_event_soft_fails_on_generic_error():
    """An unexpected error during xadd is swallowed."""
    fake_redis_client = MagicMock()
    fake_redis_client.xadd.side_effect = RuntimeError("boom")
    fake_redis_module = MagicMock()
    fake_redis_module.ConnectionError = type("CE", (Exception,), {})
    fake_redis_module.from_url.return_value = fake_redis_client

    with (
        patch.dict("sys.modules", {"redis": fake_redis_module}),
        patch("quart.current_app", _FakeApp()),
    ):
        await events.emit_event(domain="d", action="a", resource_id=1)
