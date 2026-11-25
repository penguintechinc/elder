"""Tests for ElderAPIClient's retry/backoff policy.

Regression coverage: `_request` used to be wrapped in
`@backoff.on_exception(backoff.expo, aiohttp.ClientError, ...)` with no
`giveup` predicate, so `response.raise_for_status()` raising
`ClientResponseError` for a 401/403/429 got retried just like a transient
5xx -- burning the retry budget on a rejection that will never succeed.
Only 5xx responses and non-HTTP failures (connection errors, timeouts)
should be retried; 4xx must give up on the first attempt.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from apps.worker.utils.elder_client import ElderAPIClient, _is_transient_client_error


def _client_response_error(status: int) -> aiohttp.ClientResponseError:
    """Build a ClientResponseError the way response.raise_for_status() does."""
    request_info = aiohttp.RequestInfo(
        url=aiohttp.client.URL("http://elder-api.local/api/v1/organizations"),
        method="GET",
        headers=aiohttp.client.CIMultiDict(),
        real_url=aiohttp.client.URL("http://elder-api.local/api/v1/organizations"),
    )
    return aiohttp.ClientResponseError(
        request_info=request_info, history=(), status=status
    )


class TestIsTransientClientError:
    """Unit coverage for the giveup predicate itself."""

    @pytest.mark.parametrize("status", [401, 403, 404, 409, 422, 429])
    def test_4xx_is_not_transient(self, status: int) -> None:
        """4xx (including auth/rate-limit) must never be retried."""
        assert _is_transient_client_error(_client_response_error(status)) is False

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_5xx_is_transient(self, status: int) -> None:
        """5xx server errors are worth retrying."""
        assert _is_transient_client_error(_client_response_error(status)) is True

    def test_non_response_client_error_is_transient(self) -> None:
        """Connection-level errors (no HTTP status at all) are transient."""
        exc = aiohttp.ClientConnectionError("connection reset")
        assert _is_transient_client_error(exc) is True


class _FakeResponse:
    """Minimal stand-in for aiohttp's ClientResponse."""

    def __init__(self, status: int) -> None:
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise _client_response_error(self.status)

    async def json(self) -> dict:
        return {"ok": True}


class _FakeRequestContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, *exc_info) -> bool:
        return False


class _FakeSession:
    """Records each call and returns the next queued status code."""

    def __init__(self, statuses: list[int]) -> None:
        self._statuses = list(statuses)
        self.call_count = 0

    def request(self, method: str, url: str, **kwargs) -> _FakeRequestContext:
        self.call_count += 1
        status = self._statuses.pop(0)
        return _FakeRequestContext(_FakeResponse(status))


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip backoff's real exponential-wait sleep so retry tests run fast."""

    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)


class TestRequestRetryPolicy:
    """End-to-end: ElderAPIClient._request() through the backoff decorator."""

    @pytest.mark.asyncio
    async def test_401_gives_up_after_first_attempt(self) -> None:
        """A 401 must not be retried -- it's terminal, not transient."""
        client = ElderAPIClient(base_url="http://elder-api.local", api_key="k")
        fake_session = _FakeSession([401, 401, 401])
        client.session = fake_session

        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await client._request("GET", "/organizations")

        assert exc_info.value.status == 401
        assert fake_session.call_count == 1

    @pytest.mark.asyncio
    async def test_500_is_retried_until_success(self) -> None:
        """A transient 500 is retried and succeeds once the server recovers."""
        client = ElderAPIClient(base_url="http://elder-api.local", api_key="k")
        fake_session = _FakeSession([500, 500, 200])
        client.session = fake_session

        result = await client._request("GET", "/organizations")

        assert result == {"ok": True}
        assert fake_session.call_count == 3

    @pytest.mark.asyncio
    async def test_429_gives_up_after_first_attempt(self) -> None:
        """A 429 (rate limit) must not be retried -- retrying amplifies the limit."""
        client = ElderAPIClient(base_url="http://elder-api.local", api_key="k")
        fake_session = _FakeSession([429, 200])
        client.session = fake_session

        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await client._request("GET", "/organizations")

        assert exc_info.value.status == 429
        assert fake_session.call_count == 1
