"""Coverage tests for ``shared.alerting``, ``shared.webhooks`` and ``shared.jobbus``."""

import json
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ResponseError

from shared.alerting import alertmanager as am
from shared.jobbus import JobBus, JobEnvelope, ReclamedMessage
from shared.webhooks import issue_webhooks as iw
from shared.webhooks.delivery import DeliveryResult


# --------------------------------------------------------------------------- alertmanager
class _FakeClient:
    """Async HTTP client double recording posts and optionally raising."""

    def __init__(self, error=None):
        """Store the error (if any) raised from ``post`` or ``raise_for_status``."""
        self.error = error
        self.posted = []

    async def __aenter__(self):
        """Return self as the async context value."""
        return self

    async def __aexit__(self, *exc):
        """Do not suppress exceptions."""
        return False

    async def post(self, url, json=None):
        """Record the call and return a response double."""
        self.posted.append((url, json))
        resp = MagicMock()
        resp.raise_for_status.side_effect = self.error
        return resp


def test_alertmanager_url_resolution(monkeypatch):
    """Explicit URL wins, then env, then the in-cluster default."""
    monkeypatch.setenv("ALERTMANAGER_URL", "http://env:1")
    assert am.AlertmanagerClient("http://x:2").api_url == "http://x:2/api/v2/alerts"
    assert am.AlertmanagerClient().alertmanager_url == "http://env:1"
    monkeypatch.delenv("ALERTMANAGER_URL")
    assert am.AlertmanagerClient().alertmanager_url == "http://alertmanager:9093"


@pytest.mark.asyncio
async def test_send_alert_success_with_optional_fields():
    """The payload carries labels, annotations, ends_at and generator URL."""
    fake = _FakeClient()
    from datetime import UTC, datetime

    start = datetime(2026, 1, 1, tzinfo=UTC)
    with patch.object(am.httpx, "AsyncClient", return_value=fake):
        ok = await am.AlertmanagerClient("http://a").send_alert(
            "N", {"k": "v"}, {"s": "t"}, start, start, "http://gen"
        )
    assert ok is True
    _, body = fake.posted[0]
    alert = body[0]
    assert alert["labels"] == {"alertname": "N", "k": "v"}
    assert (
        alert["endsAt"] == start.isoformat() and alert["generatorURL"] == "http://gen"
    )


@pytest.mark.asyncio
async def test_send_alert_defaults_and_failures():
    """Defaults fill start time; HTTP and unexpected errors both return False."""
    fake = _FakeClient()
    with patch.object(am.httpx, "AsyncClient", return_value=fake):
        assert await am.AlertmanagerClient("http://a").send_alert("N", {}, {})
    alert = fake.posted[0][1][0]
    assert "endsAt" not in alert and alert["generatorURL"] == ""
    import httpx

    for err in (httpx.HTTPError("bad"), RuntimeError("odd")):
        with patch.object(am.httpx, "AsyncClient", return_value=_FakeClient(err)):
            assert (
                await am.AlertmanagerClient("http://a").send_alert("N", {}, {}) is False
            )


@pytest.mark.asyncio
async def test_incident_alert_labels_and_resolution():
    """Priority maps to severity and optional fields appear only when supplied."""
    client = am.AlertmanagerClient("http://a")
    client.send_alert = AsyncMock(return_value=True)
    assert await client.send_incident_alert(
        1, "T", "CRITICAL", "Org", 2, "desc", "bob", ["e1", "e2"], "http://u"
    )
    kw = client.send_alert.call_args.kwargs
    assert (
        kw["labels"]["severity"] == "critical" and kw["labels"]["assigned_to"] == "bob"
    )
    assert (
        kw["annotations"]["entities"] == "e1, e2"
        and kw["annotations"]["url"] == "http://u"
    )
    assert kw["generator_url"] == "http://u"
    await client.send_incident_alert(1, "T", "weird", "Org", 2)
    kw = client.send_alert.call_args.kwargs
    assert kw["labels"]["severity"] == "warning"
    assert kw["annotations"]["description"] == "No description provided"
    assert "assigned_to" not in kw["labels"] and "entities" not in kw["annotations"]
    assert await client.resolve_incident_alert(1, "T", "Org", 2)
    kw = client.send_alert.call_args.kwargs
    assert kw["ends_at"] is not None and kw["labels"]["severity"] == "info"


@pytest.mark.asyncio
async def test_singleton_and_module_level_send():
    """The module helper reuses a lazily created singleton client."""
    with patch.object(am, "_alertmanager_client", None):
        first = am.get_alertmanager_client()
        assert am.get_alertmanager_client() is first
        first.send_incident_alert = AsyncMock(return_value=True)
        assert await am.send_incident_alert(1, "T", "high", "O", 2, web_url="u")
        assert first.send_incident_alert.call_args.kwargs["web_url"] == "u"


# --------------------------------------------------------------------------- issue webhooks
def _cfg(config, cid=1):
    """Return an alert-configuration row double."""
    return types.SimpleNamespace(id=cid, config=config)


def _db(rows):
    """Return a DAL double whose query yields ``rows``."""
    db = MagicMock()
    db.return_value.select.return_value = rows
    return db


@pytest.mark.asyncio
async def test_issue_webhooks_skips_without_org_or_configs():
    """No organization or no enabled webhooks yields an empty result."""
    assert await iw.send_issue_created_webhooks(_db([]), 1, "t", "ops", 0, None) == []
    assert await iw.send_issue_created_webhooks(_db([]), 1, "t", "ops", 0, 5) == []


@pytest.mark.asyncio
async def test_issue_webhooks_delivery_outcomes():
    """Success, HTTP failure, transport error and missing URL each produce the right result."""
    rows = [
        _cfg({"url": "http://ok", "headers": {"X-A": "1"}, "timeout": 3}, 1),
        _cfg({"url": "http://bad"}, 2),
        _cfg({"url": "http://err"}, 3),
        _cfg({}, 4),
        _cfg(None, 5),
    ]
    ok = DeliveryResult(
        success=True,
        attempts=1,
        outcome="success",
        idempotency_key="k1",
        status_code=204,
    )
    bad = DeliveryResult(
        success=False,
        attempts=3,
        outcome="http_error",
        idempotency_key="k2",
        status_code=500,
        error="HTTP 500",
        response_excerpt="x" * 600,
    )
    with patch.object(
        iw._delivery,
        "deliver",
        new=AsyncMock(side_effect=[ok, bad, ConnectionError("down")]),
    ) as deliver:
        out = await iw.send_issue_created_webhooks(
            _db(rows), 7, "Title", "security", 1, 9, web_url_base="http://web"
        )
    assert [r["config_id"] for r in out] == [1, 2, 3]
    assert out[0]["success"] is True and out[0]["response"] is None
    assert out[0]["status_code"] == 204 and out[0]["target_host"] == "ok"
    assert out[1]["success"] is False and len(out[1]["response"]) == 500
    assert out[1]["attempts"] == 3 and out[1]["error"] == "HTTP 500"
    assert out[2]["success"] is False and out[2]["error"] == "ConnectionError: down"
    first = deliver.await_args_list[0].args[0]
    body = json.loads(first.body)
    assert body["issue"]["url"] == "http://web/issues/7"
    assert body["issue"]["is_incident"] is True and first.kind == "issue_created"
    assert first.headers["X-A"] == "1" and first.url == "http://ok"


def test_webhooks_package_exports_sender():
    """The package re-exports the issue-created sender."""
    import shared.webhooks as pkg

    assert callable(pkg.send_issue_created_webhooks)


# --------------------------------------------------------------------------- jobbus
def _bus(**kw):
    """Return a JobBus wired to an AsyncMock Redis client with a MULTI/EXEC pipeline double."""
    bus = JobBus(AsyncMock(), **kw)
    pipe = MagicMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.execute = AsyncMock(return_value=[])
    bus.redis.pipeline = MagicMock(return_value=pipe)
    bus.pipe = pipe
    return bus


def test_envelope_roundtrip_and_trace_context():
    """Envelopes survive JSON round-trips and extract the propagated context."""
    env = JobEnvelope("j", "t", {"a": 1}, "now", 3, "k", {"traceparent": "x"})
    back = JobEnvelope.from_json(env.to_json())
    assert back == env
    minimal = JobEnvelope.from_json(json.dumps({"job_id": "j", "job_type": "t"}))
    assert minimal.payload == {} and minimal.trace_context == {}
    assert minimal.extract_trace_context() is not None
    assert env.extract_trace_context() is not None


def test_key_helpers():
    """Stream and key names follow the documented layout."""
    bus = _bus()
    assert bus.stream_key("g") == "elder:jobs:g"
    assert bus.results_key("g") == "elder:results:g"
    assert bus.dead_key("g") == "elder:jobs:g:dead"
    assert bus.idempotency_key("j") == "elder:jobs:processed:j"


@pytest.mark.asyncio
async def test_ensure_group_outcomes():
    """BUSYGROUP is ignored, other errors propagate."""
    bus = _bus()
    await bus.ensure_group("g")
    bus.redis.xgroup_create.side_effect = ResponseError("BUSYGROUP exists")
    await bus.ensure_group("g")
    bus.redis.xgroup_create.side_effect = ResponseError("other")
    with pytest.raises(ResponseError):
        await bus.ensure_group("g")


@pytest.mark.asyncio
async def test_enqueue_plain_and_duplicate():
    """Enqueue writes one envelope; a duplicate hit short-circuits without writing."""
    bus = _bus()
    job_id = await bus.enqueue("g", "type", {"p": 1}, "ts", tenant_id=2)
    key, fields = bus.redis.xadd.call_args.args
    assert key == "elder:jobs:g"
    assert json.loads(fields["data"])["job_id"] == job_id
    bus.redis.xadd.reset_mock()
    bus.redis.exists.return_value = 1
    await bus.enqueue("g", "type", {}, "ts", idempotency_key="k")
    bus.redis.xadd.assert_not_called()
    bus.redis.exists.return_value = 0
    await bus.enqueue("g", "type", {}, "ts", idempotency_key="k")
    bus.redis.xadd.assert_called_once()


@pytest.mark.xfail(
    strict=True, reason="bug: idempotency lookup uses a freshly generated id"
)
@pytest.mark.asyncio
async def test_enqueue_same_idempotency_key_twice_enqueues_once():
    """Two enqueues with one idempotency key should write a single stream entry."""
    seen: set[str] = set()
    bus = _bus()
    bus.redis.exists.side_effect = lambda k: int(k in seen)
    first = await bus.enqueue("g", "t", {}, "ts", idempotency_key="same")
    seen.add(bus.idempotency_key(first))
    await bus.enqueue("g", "t", {}, "ts", idempotency_key="same")
    assert bus.redis.xadd.call_count == 1


@pytest.mark.asyncio
async def test_read_handles_bytes_str_empty_and_errors():
    """Bytes and str payloads are decoded; empty results and NOGROUP return []; other errors raise."""
    bus = _bus()
    env = JobEnvelope("j", "t", {}, "ts").to_json()
    bus.redis.xreadgroup.return_value = [
        ("s", [(b"1-0", {b"data": env.encode()}), ("2-0", {"data": env})])
    ]
    got = await bus.read("g", "c")
    assert [m[0] for m in got] == ["1-0", "2-0"] and got[0][1].job_id == "j"
    bus.redis.xreadgroup.return_value = []
    assert await bus.read("g", "c") == []
    bus.redis.xreadgroup.side_effect = ResponseError("NOGROUP x")
    assert await bus.read("g", "c") == []
    bus.redis.xreadgroup.side_effect = ResponseError("boom")
    with pytest.raises(ResponseError):
        await bus.read("g", "c")


@pytest.mark.asyncio
async def test_ack_publish_result_and_idempotency_markers():
    """Ack, result publication and idempotency marking hit the expected Redis commands."""
    bus = _bus()
    await bus.ack("g", "1-0")
    bus.redis.xack.assert_called_once_with("elder:jobs:g", "workers", "1-0")
    await bus.publish_result("g", "j", "ok", {"r": 1})
    await bus.publish_result("g", "j", "ok")
    last = json.loads(bus.redis.xadd.call_args.args[1]["data"])
    assert last["result"] == {}
    bus.redis.exists.return_value = 1
    assert await bus.is_duplicate("j") is True
    await bus.mark_processed("j", ttl=5)
    bus.redis.setex.assert_called_once_with("elder:jobs:processed:j", 5, "1")


@pytest.mark.asyncio
async def test_reclaim_stale_routes_and_returns():
    """Messages under the delivery limit are returned; over the limit go to the dead stream."""
    bus = _bus(max_deliveries=2)
    env = JobEnvelope("j", "t", {}, "ts").to_json()
    bus.redis.xautoclaim.return_value = [
        "0-0",
        [(b"1-0", {b"data": env.encode()}), ("2-0", {"data": env})],
    ]
    bus.redis.incr.side_effect = [1, 3]
    out = await bus.reclaim_stale("g", "c")
    assert len(out) == 1 and isinstance(out[0], ReclamedMessage)
    assert out[0].msg_id == "1-0" and out[0].delivery_count == 1
    dead_key, dead = bus.pipe.xadd.call_args.args
    assert dead_key == "elder:jobs:g:dead"
    assert "max_deliveries_exceeded (3)" in json.loads(dead["data"])["dlq_reason"]
    bus.pipe.xack.assert_called_once()
    bus.pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_reclaim_stale_edge_results_and_errors():
    """Empty or short results and NOGROUP return []; unrelated errors propagate."""
    bus = _bus()
    bus.redis.xautoclaim.return_value = None
    assert await bus.reclaim_stale("g", "c") == []
    bus.redis.xautoclaim.return_value = ["0-0"]
    assert await bus.reclaim_stale("g", "c") == []
    bus.redis.xautoclaim.side_effect = ResponseError("NOGROUP")
    assert await bus.reclaim_stale("g", "c") == []
    bus.redis.xautoclaim.side_effect = ResponseError("boom")
    with pytest.raises(ResponseError):
        await bus.reclaim_stale("g", "c")


@pytest.mark.asyncio
async def test_delivery_count_helpers():
    """Counts are parsed, absent keys are zero, lookup errors degrade to zero."""
    bus = _bus()
    bus.redis.get.return_value = b"4"
    assert await bus._get_delivery_count("g", "1-0") == 4
    bus.redis.get.return_value = None
    assert await bus._get_delivery_count("g", "1-0") == 0
    bus.redis.get.side_effect = RuntimeError("x")
    assert await bus._get_delivery_count("g", "1-0") == 0
    bus.redis.incr.return_value = 2
    assert await bus._increment_delivery_count("g", "1-0") == 2
    bus.redis.expire.assert_called_once_with(
        "elder:jobs:delivery:g:1-0", bus.idempotency_ttl
    )


@pytest.mark.asyncio
async def test_reclaim_stale_swapped_payload_encodings():
    """Bytes payloads are decoded on the dead-letter path and str payloads pass on the return path."""
    bus = _bus(max_deliveries=2)
    env = JobEnvelope("j", "t", {}, "ts").to_json()
    bus.redis.xautoclaim.return_value = [
        "0-0",
        [("1-0", {"data": env}), (b"2-0", {b"data": env.encode()})],
    ]
    bus.redis.incr.side_effect = [1, 3]
    out = await bus.reclaim_stale("g", "c")
    assert [m.msg_id for m in out] == ["1-0"]
    bus.pipe.xadd.assert_called_once()
