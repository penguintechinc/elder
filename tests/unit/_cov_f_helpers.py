"""Shared fakes for the slice-F coverage tests (worker connectors, flows invoker, MCP).

Only true external boundaries are faked here: the Elder REST client the
connectors write to and the provider HTTP session they read from.  The
connectors' own mapping code always runs for real.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import requests


def fake_secret(*parts: str) -> str:
    """Build a credential-looking value at runtime so no literal secret is committed."""
    return "-".join(("covf", *parts))


def make_elder(
    orgs: list[dict[str, Any]] | None = None,
    entities: list[dict[str, Any]] | None = None,
) -> AsyncMock:
    """Return an Elder API client double with realistic list/create responses."""
    elder = AsyncMock()
    elder.list_organizations.return_value = {"items": list(orgs or [])}
    elder.list_entities.return_value = {"items": list(entities or [])}
    elder.create_organization.return_value = {"id": 501}
    elder.create_entity.return_value = {"id": 9001}
    elder.update_entity.return_value = {"id": 9001}
    return elder


def created_entities(elder: AsyncMock) -> list[Any]:
    """Return the Entity payloads passed to ``create_entity`` in call order."""
    return [call.args[0] for call in elder.create_entity.call_args_list]


def updated_entities(elder: AsyncMock) -> list[tuple[Any, Any]]:
    """Return ``(entity_id, Entity)`` pairs passed to ``update_entity``."""
    return [(call.args[0], call.args[1]) for call in elder.update_entity.call_args_list]


def by_name(entities: list[Any]) -> dict[str, Any]:
    """Index Entity payloads by their name for order-independent assertions."""
    return {entity.name: entity for entity in entities}


def spy_logger(connector: Any) -> MagicMock:
    """Replace a connector's logger with a recording mock and return it."""
    logger = MagicMock()
    connector.logger = logger
    return logger


def logged_text(logger: MagicMock) -> str:
    """Flatten every recorded log call (message and fields) into one string."""
    return repr(logger.mock_calls)


def assert_not_leaked(secret: str, *haystacks: Any) -> None:
    """Assert a secret value appears in none of the given objects' reprs."""
    for haystack in haystacks:
        assert secret not in repr(haystack)


class FakeResponse:
    """Minimal ``requests.Response`` stand-in with real ``raise_for_status`` semantics."""

    def __init__(self, payload: Any = None, status_code: int = 200, text: str = ""):
        """Store the JSON payload and HTTP status to replay."""
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        """Return the payload, or raise like a non-JSON body when it is an exception."""
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self) -> None:
        """Raise ``requests.HTTPError`` for 4xx/5xx exactly like the real client."""
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error", response=self)


class RouteSession:
    """A ``requests.Session`` double that serves canned payloads per URL path.

    ``routes`` maps a path suffix to a payload, a ``FakeResponse`` or an
    exception instance (raised on the call).  Every request is recorded so
    tests can assert pagination, timeouts and headers.
    """

    def __init__(self, base_url: str, routes: dict[str, Any]):
        """Remember the base URL to strip and the routing table."""
        self.base_url = base_url
        self.routes = routes
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.headers: dict[str, str] = {}
        self.verify: Any = True
        self.cert: Any = None
        self.closed = False

    def _serve(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        """Resolve a request against the routing table."""
        path = url[len(self.base_url) :] if url.startswith(self.base_url) else url
        self.calls.append((f"{method} {path}", kwargs))
        if path not in self.routes:
            return FakeResponse({"error": "not found"}, status_code=404)
        outcome = self.routes[path]
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, FakeResponse):
            return outcome
        return FakeResponse(outcome)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        """Serve a GET."""
        return self._serve("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Serve a POST."""
        return self._serve("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        """Serve an arbitrary-method request."""
        return self._serve(method.upper(), url, **kwargs)

    def close(self) -> None:
        """Record that the session was closed."""
        self.closed = True

    def paths(self) -> list[str]:
        """Return the recorded ``"METHOD path"`` strings in call order."""
        return [call[0] for call in self.calls]


class AsyncRouteClient:
    """An async HTTP client double (httpx/aiohttp style) serving canned responses.

    ``routes`` maps a path to a JSON payload, a ``FakeResponse`` or an
    exception instance.  A list value is consumed one item per call, which
    lets a test script retries (e.g. 429 then 200) and pagination.
    """

    def __init__(self, routes: dict[str, Any]):
        """Remember the routing table."""
        self.routes = routes
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def _serve(self, method: str, path: str, **kwargs: Any) -> FakeResponse:
        """Resolve one request."""
        self.calls.append((f"{method} {path}", kwargs))
        if path not in self.routes:
            return FakeResponse({"error": "not found"}, status_code=404)
        outcome = self.routes[path]
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, FakeResponse):
            return outcome
        return FakeResponse(outcome)

    async def get(self, path: str, **kwargs: Any) -> FakeResponse:
        """Serve a GET."""
        return await self._serve("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> FakeResponse:
        """Serve a POST."""
        return await self._serve("POST", path, **kwargs)

    async def request(self, method: str, path: str, **kwargs: Any) -> FakeResponse:
        """Serve an arbitrary-method request."""
        return await self._serve(method.upper(), path, **kwargs)

    async def aclose(self) -> None:
        """Record that the client was closed."""
        self.closed = True

    def paths(self) -> list[str]:
        """Return the recorded ``"METHOD path"`` strings in call order."""
        return [call[0] for call in self.calls]


class HttpxResponse:
    """An ``httpx.Response`` stand-in whose ``raise_for_status`` raises real httpx errors."""

    def __init__(
        self,
        payload: Any = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "",
    ):
        """Store the body, status and headers to replay."""
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self) -> Any:
        """Return the JSON body, or raise when the body is not JSON."""
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self) -> None:
        """Raise ``httpx.HTTPStatusError`` for 4xx/5xx like the real response."""
        import httpx

        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )


class HttpxScript:
    """An ``httpx.AsyncClient`` double keyed by ``"METHOD url"``.

    A list value is consumed one response per call (retries, pagination);
    an exception instance is raised.  Unknown requests return 404.
    """

    def __init__(self, routes: dict[str, Any]):
        """Remember the scripted outcomes."""
        self.routes = routes
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def _serve(self, method: str, url: str, **kwargs: Any) -> HttpxResponse:
        """Resolve one request against the script."""
        key = f"{method} {url}"
        self.calls.append((key, kwargs))
        if key not in self.routes:
            return HttpxResponse({"errorSummary": "Not found"}, 404, text="Not found")
        outcome = self.routes[key]
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, HttpxResponse):
            return outcome
        return HttpxResponse(outcome)

    async def get(self, url: str, **kwargs: Any) -> HttpxResponse:
        """Serve a GET."""
        return await self._serve("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> HttpxResponse:
        """Serve a POST."""
        return await self._serve("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> HttpxResponse:
        """Serve a PUT."""
        return await self._serve("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> HttpxResponse:
        """Serve a PATCH."""
        return await self._serve("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> HttpxResponse:
        """Serve a DELETE."""
        return await self._serve("DELETE", url, **kwargs)

    async def aclose(self) -> None:
        """Record that the client was closed."""
        self.closed = True

    def keys(self) -> list[str]:
        """Return the recorded ``"METHOD url"`` strings in call order."""
        return [call[0] for call in self.calls]
