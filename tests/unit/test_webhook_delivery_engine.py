"""Tests for the shared non-blocking webhook delivery engine.

Covers the delivery contract every dispatcher relies on: the event loop is
never blocked, retries back off on 5xx/timeouts and stop at the attempt cap,
4xx is terminal, redirects are refused, private/metadata targets are
rejected before any request is sent, and no URL credential reaches logs,
errors or metric labels.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from shared.webhooks import delivery
from shared.webhooks.delivery import (
    DeliveryPolicy,
    DeliveryRequest,
    WebhookTargetError,
    deliver,
)

FAST = DeliveryPolicy(
    max_attempts=4,
    connect_timeout=1.0,
    read_timeout=1.0,
    attempt_timeout=2.0,
    backoff_base=0.5,
    backoff_cap=30.0,
    max_response_bytes=4096,
)


class SleepRecorder:
    """Awaitable sleep stand-in that records requested delays instantly."""

    def __init__(self) -> None:
        """Start with no recorded delays."""
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        """Record ``delay`` and yield control without waiting."""
        self.delays.append(delay)
        await asyncio.sleep(0)


class LogRecorder:
    """Structlog-shaped logger that keeps every event and its fields."""

    def __init__(self) -> None:
        """Start with no recorded events."""
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _log(self, event: str, **fields: Any) -> None:
        """Store one log call."""
        self.events.append((event, fields))

    debug = info = warning = error = _log


def _request(
    url: str = "https://hooks.example.com/hook", **kwargs: Any
) -> DeliveryRequest:
    """Build a delivery request with a small JSON body."""
    return DeliveryRequest(url=url, body=b'{"a":1}', **kwargs)


@pytest.mark.asyncio
async def test_slow_endpoint_does_not_block_event_loop(webhook_transport):
    """A slow receiver must not delay a concurrent coroutine."""

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.4)
        return httpx.Response(200, text="ok")

    webhook_transport.handler = slow
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    tick_task = asyncio.create_task(ticker())
    result = await deliver(_request(), policy=FAST)
    stop.set()
    await tick_task

    assert result.success is True
    assert result.duration_ms >= 350
    # The ticker kept running throughout the 0.4s delivery, never starved.
    assert len(gaps) >= 15
    assert max(gaps) < 0.2


@pytest.mark.asyncio
async def test_retries_with_backoff_on_5xx_then_succeeds(webhook_transport):
    """503 then 500 are retried with growing backoff; the 200 ends it."""
    webhook_transport.respond_with(503, 500, 200)
    sleeper = SleepRecorder()

    result = await deliver(_request(), policy=FAST, sleep=sleeper)

    assert result.success is True
    assert result.attempts == 3
    assert result.status_code == 200
    assert len(webhook_transport.requests) == 3
    # Equal jitter: attempt n waits within [window/2, window], window=0.5*2^(n-1).
    assert len(sleeper.delays) == 2
    assert 0.25 <= sleeper.delays[0] <= 0.5
    assert 0.5 <= sleeper.delays[1] <= 1.0


@pytest.mark.asyncio
async def test_idempotency_key_is_stable_across_retries(webhook_transport):
    """Every attempt of one delivery carries the same Idempotency-Key."""
    webhook_transport.respond_with(500, 500, 200)

    result = await deliver(_request(), policy=FAST, sleep=SleepRecorder())

    keys = {r.headers["Idempotency-Key"] for r in webhook_transport.requests}
    assert len(webhook_transport.requests) == 3
    assert keys == {result.idempotency_key}
    assert len(result.idempotency_key) >= 16


@pytest.mark.asyncio
async def test_distinct_deliveries_get_distinct_idempotency_keys(webhook_transport):
    """Two separate deliveries must not share an idempotency key."""
    first = await deliver(_request(), policy=FAST)
    second = await deliver(_request(), policy=FAST)
    assert first.idempotency_key != second.idempotency_key


@pytest.mark.asyncio
async def test_retries_on_transport_timeout(webhook_transport):
    """A read timeout is retried and a later success wins."""
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("read timed out", request=request)
        return httpx.Response(200)

    webhook_transport.handler = flaky
    sleeper = SleepRecorder()
    result = await deliver(_request(), policy=FAST, sleep=sleeper)

    assert result.success is True
    assert result.attempts == 2
    assert len(sleeper.delays) == 1


@pytest.mark.asyncio
async def test_attempt_timeout_is_enforced(webhook_transport):
    """A receiver that never answers is cut off at the attempt timeout."""

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    webhook_transport.handler = hang
    policy = DeliveryPolicy(
        max_attempts=2,
        connect_timeout=0.05,
        read_timeout=0.05,
        attempt_timeout=0.1,
        backoff_base=0.01,
        backoff_cap=0.02,
    )
    started = time.monotonic()
    result = await deliver(_request(), policy=policy, sleep=SleepRecorder())
    elapsed = time.monotonic() - started

    assert result.success is False
    assert result.outcome == "timeout"
    assert result.attempts == 2
    assert result.status_code is None
    assert elapsed < 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
async def test_no_retry_on_client_error(webhook_transport, status):
    """A non-retryable 4xx is terminal after exactly one attempt."""
    webhook_transport.respond_with(status)
    sleeper = SleepRecorder()

    result = await deliver(_request(), policy=FAST, sleep=sleeper)

    assert result.success is False
    assert result.attempts == 1
    assert result.status_code == status
    assert result.outcome == "http_error"
    assert len(webhook_transport.requests) == 1
    assert sleeper.delays == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_retryable_statuses_are_retried(webhook_transport, status):
    """408, 429 and 5xx consume the retry budget."""
    webhook_transport.respond_with(status, 200)

    result = await deliver(_request(), policy=FAST, sleep=SleepRecorder())

    assert result.success is True
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_max_attempts_respected(webhook_transport):
    """A permanently failing receiver gets exactly max_attempts requests."""
    webhook_transport.respond_with(500)
    sleeper = SleepRecorder()
    policy = DeliveryPolicy(max_attempts=3, backoff_base=0.5, backoff_cap=30.0)

    result = await deliver(_request(), policy=policy, sleep=sleeper)

    assert result.success is False
    assert result.attempts == 3
    assert result.status_code == 500
    assert len(webhook_transport.requests) == 3
    # No sleep after the final attempt.
    assert len(sleeper.delays) == 2


@pytest.mark.asyncio
async def test_backoff_is_capped(webhook_transport):
    """Backoff never exceeds the cap however many attempts have failed."""
    policy = DeliveryPolicy(max_attempts=10, backoff_base=1.0, backoff_cap=2.0)
    for attempt in range(1, 12):
        assert 0 < delivery.backoff_delay(attempt, policy) <= 2.0


@pytest.mark.asyncio
async def test_retry_after_header_is_honoured_up_to_cap(webhook_transport):
    """A numeric Retry-After lengthens the wait but never past the cap."""
    queue = [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)]
    webhook_transport.handler = lambda request: queue.pop(0)
    sleeper = SleepRecorder()
    policy = DeliveryPolicy(max_attempts=2, backoff_base=0.5, backoff_cap=5.0)

    result = await deliver(_request(), policy=policy, sleep=sleeper)

    assert result.success is True
    assert sleeper.delays == [5.0]


@pytest.mark.asyncio
async def test_redirect_is_not_followed(webhook_transport):
    """A 3xx is a terminal failure; the Location target is never requested."""
    webhook_transport.handler = lambda request: httpx.Response(
        302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
    )
    sleeper = SleepRecorder()

    result = await deliver(_request(), policy=FAST, sleep=sleeper)

    assert result.success is False
    assert result.outcome == "redirect_refused"
    assert result.attempts == 1
    assert len(webhook_transport.requests) == 1
    assert webhook_transport.requests[0].url.host == "93.184.216.34"


@pytest.mark.asyncio
async def test_response_body_is_size_capped(webhook_transport):
    """An oversized response is truncated instead of buffered whole."""
    webhook_transport.handler = lambda request: httpx.Response(
        500, content=b"x" * 1_000_000
    )
    policy = DeliveryPolicy(max_attempts=1, max_response_bytes=256)

    result = await deliver(_request(), policy=policy)

    assert result.status_code == 500
    assert 0 < len(result.response_excerpt) <= 256


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://127.8.9.10:8080/hook",
        "http://10.0.0.5/hook",
        "http://172.16.4.4/hook",
        "http://192.168.1.10/hook",
        "http://100.64.0.1/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/hook",
        "http://[::1]/hook",
        "http://[fe80::1]/hook",
        "http://[fd00:ec2::254]/latest",
        "http://[fc00::1]/hook",
        "http://[::ffff:10.0.0.1]/hook",
        "http://[::ffff:169.254.169.254]/hook",
        "http://[64:ff9b::a00:1]/hook",
        "http://localhost/hook",
        "https://vault.internal/hook",
    ],
)
async def test_private_and_metadata_targets_rejected(webhook_transport, url):
    """Private, loopback, link-local and metadata targets are never contacted."""
    sleeper = SleepRecorder()

    result = await deliver(_request(url), policy=FAST, sleep=sleeper)

    assert result.success is False
    assert result.outcome == "blocked_target"
    assert result.attempts == 1
    assert webhook_transport.requests == []
    assert sleeper.delays == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["ftp://hooks.example.com/x", "file:///etc/passwd", "gopher://hooks.example.com/"],
)
async def test_disallowed_schemes_rejected(webhook_transport, url):
    """Only http and https targets are deliverable."""
    result = await deliver(_request(url), policy=FAST)

    assert result.outcome == "blocked_target"
    assert webhook_transport.requests == []


@pytest.mark.asyncio
async def test_hostname_resolving_to_private_ip_rejected(
    webhook_transport, monkeypatch
):
    """A public-looking name that resolves to RFC 1918 space is refused."""
    monkeypatch.setattr(delivery, "_resolve_host", lambda host, port: ["192.168.50.7"])

    result = await deliver(_request("https://innocent.example.com/h"), policy=FAST)

    assert result.outcome == "blocked_target"
    assert webhook_transport.requests == []


@pytest.mark.asyncio
async def test_mixed_public_and_private_records_rejected(
    webhook_transport, monkeypatch
):
    """One private record among public ones is enough to reject the target."""
    monkeypatch.setattr(
        delivery, "_resolve_host", lambda host, port: ["93.184.216.34", "10.1.2.3"]
    )

    result = await deliver(_request("https://rebind.example.com/h"), policy=FAST)

    assert result.outcome == "blocked_target"
    assert webhook_transport.requests == []


@pytest.mark.asyncio
async def test_unresolvable_host_rejected(webhook_transport, monkeypatch):
    """A name that does not resolve fails closed without a request."""

    def boom(host: str, port: int) -> list[str]:
        raise OSError("name or service not known")

    monkeypatch.setattr(delivery, "_resolve_host", boom)

    result = await deliver(_request("https://nx.example.com/h"), policy=FAST)

    assert result.outcome == "blocked_target"
    assert webhook_transport.requests == []


@pytest.mark.asyncio
async def test_request_is_pinned_to_vetted_ip_with_original_host(webhook_transport):
    """The socket target is the vetted IP; Host and SNI keep the hostname."""
    result = await deliver(
        _request("https://hooks.example.com:8443/p?q=1"), policy=FAST
    )

    assert result.success is True
    sent = webhook_transport.requests[0]
    assert sent.url.host == "93.184.216.34"
    assert sent.url.port == 8443
    assert sent.url.path == "/p"
    assert sent.headers["Host"] == "hooks.example.com:8443"
    assert sent.extensions["sni_hostname"] == "hooks.example.com"
    assert sent.content == b'{"a":1}'


@pytest.mark.asyncio
async def test_caller_cannot_override_host_or_idempotency_key(webhook_transport):
    """Engine-owned headers win over caller-supplied custom headers."""
    request = _request(
        headers={"host": "evil.internal", "idempotency-key": "x", "X-A": "b"}
    )

    result = await deliver(request, policy=FAST)

    sent = webhook_transport.requests[0]
    assert sent.headers["Host"] == "hooks.example.com"
    assert sent.headers["Idempotency-Key"] == result.idempotency_key != "x"
    assert sent.headers["X-A"] == "b"


@pytest.mark.asyncio
async def test_operator_allowlist_permits_private_but_never_loopback(
    webhook_transport, monkeypatch
):
    """The CIDR allowlist opens private ranges only; loopback stays blocked."""
    monkeypatch.setenv(
        delivery.ALLOWED_PRIVATE_CIDRS_ENV, "10.0.0.0/8, not-a-cidr, 127.0.0.0/8"
    )

    allowed = await deliver(_request("http://10.2.3.4/hook"), policy=FAST)
    other_private = await deliver(_request("http://192.168.1.1/hook"), policy=FAST)
    loopback = await deliver(_request("http://127.0.0.1/hook"), policy=FAST)
    metadata = await deliver(_request("http://169.254.169.254/"), policy=FAST)

    assert allowed.success is True
    assert other_private.outcome == "blocked_target"
    assert loopback.outcome == "blocked_target"
    assert metadata.outcome == "blocked_target"
    assert len(webhook_transport.requests) == 1


def test_validate_target_url_sync_guard():
    """The synchronous guard used by threaded dispatchers enforces the same rules."""
    delivery.validate_target_url("https://hooks.example.com/ok")
    for bad in ("http://10.0.0.1/x", "http://localhost/x", "ftp://hooks.example.com/"):
        with pytest.raises(WebhookTargetError):
            delivery.validate_target_url(bad)


@pytest.mark.asyncio
async def test_no_url_credentials_or_secrets_in_logs_or_errors(
    webhook_transport, monkeypatch
):
    """Userinfo, path tokens and query secrets never reach logs or errors."""
    recorder = LogRecorder()
    monkeypatch.setattr(delivery, "logger", recorder)
    url = "https://user:hunter2@hooks.example.com/services/T000/SECRETTOKEN?key=abc123"
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError(f"cannot connect to {url}", request=request)
        return httpx.Response(500, text="nope")

    webhook_transport.handler = handler
    request = _request(url, headers={"X-Elder-Signature": "sig-do-not-log"})
    policy = DeliveryPolicy(max_attempts=3, backoff_base=0.01, backoff_cap=0.01)

    result = await deliver(request, policy=policy, sleep=SleepRecorder())

    assert result.success is False
    assert result.attempts == 3
    assert len(recorder.events) >= 3
    haystack = repr(recorder.events) + repr(result.error) + repr(result)
    for secret in ("hunter2", "SECRETTOKEN", "abc123", "sig-do-not-log", "/services/"):
        assert secret not in haystack
    # The bare hostname is the only target detail that is logged.
    assert any(f.get("target_host") == "hooks.example.com" for _, f in recorder.events)


def test_scrub_error_redacts_urls_and_request_paths():
    """Both scheme URLs and requests' "with url: /path" suffix are redacted."""
    exc = RuntimeError(
        "Max retries exceeded with url: /services/T0/SECRET (see https://u:p@h.example/x)"
    )
    text = delivery.scrub_error(exc)
    assert "SECRET" not in text
    assert "u:p@" not in text
    assert text.startswith("RuntimeError:")


@pytest.mark.asyncio
async def test_metrics_use_bounded_labels_only(webhook_transport, monkeypatch):
    """Attempt/latency/outcome metrics carry enum labels, never URL or host."""
    recorded: list[tuple[str, float, dict[str, Any]]] = []

    class Instrument:
        """Minimal counter/histogram stand-in recording its attributes."""

        def __init__(self, name: str) -> None:
            """Remember which instrument this is."""
            self.name = name

        def add(self, value: float, attributes: dict[str, Any]) -> None:
            """Record a counter increment."""
            recorded.append((self.name, value, attributes))

        record = add

    for attr in (
        "_attempt_counter",
        "_success_counter",
        "_failure_counter",
        "_latency_histogram",
    ):
        monkeypatch.setattr(delivery, attr, Instrument(attr))
    webhook_transport.respond_with(503, 200)

    await deliver(
        _request(kind="tenant-supplied-garbage"), policy=FAST, sleep=SleepRecorder()
    )
    webhook_transport.respond_with(400)
    await deliver(_request(kind="native"), policy=FAST, sleep=SleepRecorder())

    names = [name for name, _, _ in recorded]
    assert names.count("_attempt_counter") == 3
    assert names.count("_latency_histogram") == 3
    assert names.count("_success_counter") == 1
    assert names.count("_failure_counter") == 1
    for _, _, attributes in recorded:
        assert set(attributes) <= {"kind", "outcome", "status_class"}
        assert attributes["kind"] in {"native", "other"}
        assert "example.com" not in repr(attributes)
    assert {"kind": "other", "outcome": "http_error", "status_class": "5xx"} in [
        a for n, _, a in recorded if n == "_attempt_counter"
    ]


@pytest.mark.asyncio
async def test_concurrency_is_bounded(webhook_transport, monkeypatch):
    """No more than WEBHOOK_MAX_CONCURRENCY deliveries are in flight at once."""
    monkeypatch.setenv("WEBHOOK_MAX_CONCURRENCY", "2")
    delivery._semaphores.clear()
    state = {"in_flight": 0, "peak": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        await asyncio.sleep(0.05)
        state["in_flight"] -= 1
        return httpx.Response(200)

    webhook_transport.handler = handler
    try:
        results = await asyncio.gather(
            *(deliver(_request(), policy=FAST) for _ in range(8))
        )
    finally:
        delivery._semaphores.clear()

    assert all(r.success for r in results)
    assert state["peak"] == 2


@pytest.mark.asyncio
async def test_background_failure_is_contained_and_logged(monkeypatch):
    """A crashing background task is logged, not raised into the scheduler."""
    recorder = LogRecorder()
    monkeypatch.setattr(delivery, "logger", recorder)

    async def crash() -> None:
        raise RuntimeError("boom https://u:pw@hooks.example.com/t")

    task = delivery.schedule_background(crash(), name="t")
    await delivery.drain_background(timeout=2)
    await asyncio.sleep(0)

    assert task.done()
    assert task not in delivery._background_tasks
    events = [e for e, _ in recorder.events]
    assert "webhook_background_task_failed" in events
    assert "pw@" not in repr(recorder.events)


def test_default_policy_survives_garbage_environment(monkeypatch):
    """Bad WEBHOOK_* values fall back to defaults and are clamped, never raise."""
    monkeypatch.setenv("WEBHOOK_MAX_ATTEMPTS", "lots")
    monkeypatch.setenv("WEBHOOK_CONNECT_TIMEOUT_SECONDS", "nan")
    monkeypatch.setenv("WEBHOOK_READ_TIMEOUT_SECONDS", "999999")
    monkeypatch.setenv("WEBHOOK_BACKOFF_BASE_SECONDS", "")

    policy = delivery.default_policy()

    assert policy.max_attempts == 4
    assert policy.connect_timeout == 3.0
    assert policy.read_timeout == 120.0
    assert policy.backoff_base == 0.5
    assert delivery.default_policy(max_attempts=1).max_attempts == 1
    assert delivery.default_policy(max_attempts=99).max_attempts == 10
