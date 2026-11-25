"""Unit-test fixtures for outbound webhook delivery.

Delivery vets every target by resolving its hostname, so tests need a
deterministic resolver (no real DNS from CI) and a stub HTTP transport that
records what would have gone over the wire.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

#: Public documentation-style address handed to every "normal" hostname.
STUB_PUBLIC_IP = "93.184.216.34"


def _stub_resolve(host: str, port: int) -> list[str]:
    """Resolve test hostnames without DNS, honestly for local names.

    ``localhost``/``*.internal``/``*.local`` map to loopback (so SSRF checks
    still reject them, as they would in production); everything else maps to
    one fixed public address.
    """
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(
        (".internal", ".local", ".localhost")
    ):
        return ["127.0.0.1"]
    return [STUB_PUBLIC_IP]


@pytest.fixture(autouse=True)
def _deterministic_webhook_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the delivery engine's DNS lookup with the offline resolver."""
    monkeypatch.setattr("shared.webhooks.delivery._resolve_host", _stub_resolve)


class WebhookTransportStub:
    """Recording stand-in for the webhook HTTP transport.

    ``handler`` decides each response (sync or async callable, default 200);
    ``requests`` lists every request the engine actually sent, so a test can
    assert both payload bytes and that nothing was sent at all.
    """

    def __init__(self) -> None:
        """Start with no recorded requests and a 200-OK handler."""
        self.requests: list[httpx.Request] = []
        self.handler: Callable[[httpx.Request], Any] = lambda request: httpx.Response(
            200, text="ok"
        )

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        """Record the request, then delegate to the configured handler."""
        self.requests.append(request)
        outcome = self.handler(request)
        if isinstance(outcome, Awaitable):
            outcome = await outcome
        return outcome

    def respond_with(self, *statuses: int) -> None:
        """Answer successive requests with ``statuses``, repeating the last."""
        queue = list(statuses)

        def handler(request: httpx.Request) -> httpx.Response:
            status = queue.pop(0) if len(queue) > 1 else queue[0]
            return httpx.Response(status, text=f"status {status}")

        self.handler = handler

    def client_factory(self, policy: Any) -> httpx.AsyncClient:
        """Build a client with the policy's real timeouts over the stub."""
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle),
            timeout=httpx.Timeout(
                connect=policy.connect_timeout,
                read=policy.read_timeout,
                write=policy.read_timeout,
                pool=policy.connect_timeout,
            ),
            follow_redirects=False,
            trust_env=False,
        )


@pytest.fixture
def webhook_transport(monkeypatch: pytest.MonkeyPatch) -> WebhookTransportStub:
    """Route the delivery engine's HTTP through a recording stub transport."""
    stub = WebhookTransportStub()
    monkeypatch.setattr("shared.webhooks.delivery._client_factory", stub.client_factory)
    return stub


@pytest.fixture(autouse=True)
def _isolated_webhook_admission() -> Any:
    """Give every test empty pending-delivery accounting and caches.

    Admission counters, the parsed allowlist and the tenant-reference cache
    are process-global; a test that fills a cap (or leaks a never-finished
    task) must not shed deliveries in the next one.
    """
    from apps.api.services.webhooks import service as webhook_service
    from shared.webhooks import delivery

    def reset() -> None:
        """Zero the shared admission state."""
        with delivery._admission_lock:
            delivery._pending_total = 0
            delivery._pending_by_tenant.clear()
        delivery._allowlist_cache = None
        webhook_service._tenant_refs.clear()

    reset()
    yield
    reset()
