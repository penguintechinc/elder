"""Behavioural tests for MCP session auth (login/refresh/expiry) and the Elder client."""

import base64
import json
import logging
from typing import Any

import httpx
import pytest

from apps.mcp import auth as auth_mod
from apps.mcp import client as client_mod
from apps.mcp.auth import (
    CallerCredentials,
    ElderAuthError,
    ElderSession,
    _decode_jwt_exp,
)
from apps.mcp.client import (
    ElderAPIError,
    ElderClient,
    ElderResponseTooLargeError,
    mask_secrets,
    public_error_message,
)
from tests.unit._cov_f_helpers import fake_secret

BASE = "http://hub-api.test.invalid"
NOW = 1_800_000_000.0
PASSWORD = fake_secret("mcp", "login", "password")


def _jwt(exp: Any = None, payload: dict[str, Any] | None = None) -> str:
    """Build an unsigned JWT-shaped token at runtime with the given expiry."""
    body = dict(payload or {})
    if exp is not None:
        body["exp"] = exp

    def encode(obj: dict[str, Any]) -> str:
        """Base64url-encode a JSON object without padding."""
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return ".".join([encode({"alg": "none"}), encode(body), "sig"])


class _Hub:
    """A scripted hub-api: maps ``"METHOD path"`` to a queue of responses."""

    def __init__(self, routes: dict[str, list[Any]]):
        """Store the scripted responses."""
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Serve the next scripted response for the request."""
        self.requests.append(request)
        queue = self.routes.get(f"{request.method} {request.url.path}")
        if not queue:
            return httpx.Response(404, json={"message": "not found"})
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def transport(self) -> httpx.MockTransport:
        """Return an httpx transport backed by this hub."""
        return httpx.MockTransport(self)

    def seen(self) -> list[str]:
        """Return ``"METHOD path"`` for each request received."""
        return [f"{r.method} {r.url.path}" for r in self.requests]


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the auth module's clock for deterministic expiry checks."""
    monkeypatch.setattr(auth_mod.time, "time", lambda: NOW)


class TestJwtExpiry:
    """Local expiry decoding."""

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            (_jwt(exp=1234), 1234.0),
            (_jwt(exp=12.5), 12.5),
            (_jwt(exp="soon"), None),
            (_jwt(exp=True), None),
            (_jwt(), None),
            ("opaque-token", None),
            ("a.!!!.c", None),
            ("a." + base64.urlsafe_b64encode(b"not json").decode() + ".c", None),
        ],
    )
    def test_decode_exp(self, token: str, expected: float | None) -> None:
        """Only a numeric exp claim in a well-formed token yields an expiry."""
        assert _decode_jwt_exp(token) == expected


class TestSessionLogin:
    """Password login."""

    @pytest.mark.asyncio
    async def test_login_stores_tokens_and_never_reprs_secrets(self) -> None:
        """Login posts the credentials, keeps both tokens and masks them in repr."""
        access, refresh = _jwt(exp=NOW + 3600), fake_secret("refresh")
        hub = _Hub(
            {
                "POST /api/v1/auth/login": [
                    httpx.Response(
                        200, json={"access_token": access, "refresh_token": refresh}
                    )
                ]
            }
        )
        session = ElderSession(
            BASE + "/", username="svc", password=PASSWORD, transport=hub.transport()
        )
        assert await session.login() == access
        assert json.loads(hub.requests[0].content) == {
            "username": "svc",
            "password": PASSWORD,
        }
        assert (session.access_token, session.refresh_token) == (access, refresh)
        assert session.base_url == BASE
        for secret in (PASSWORD, access, refresh):
            assert secret not in repr(session) and secret not in str(session)

    @pytest.mark.asyncio
    async def test_login_requires_credentials(self) -> None:
        """Password login without username/password fails before any request."""
        hub = _Hub({})
        session = ElderSession(BASE, username="svc", transport=hub.transport())
        with pytest.raises(ElderAuthError, match="ELDER_USERNAME and ELDER_PASSWORD"):
            await session.login()
        assert hub.requests == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 429, 503])
    async def test_login_rejection_raises_with_status(self, status: int) -> None:
        """A rejected login raises an auth error naming the HTTP status."""
        hub = _Hub({"POST /api/v1/auth/login": [httpx.Response(status, text="nope")]})
        session = ElderSession(
            BASE, username="svc", password=PASSWORD, transport=hub.transport()
        )
        with pytest.raises(ElderAuthError, match=f"HTTP {status}") as excinfo:
            await session.login()
        assert PASSWORD not in str(excinfo.value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [{}, {"access_token": ""}, {"access_token": 5}])
    async def test_login_without_token_in_response_fails(self, body: dict) -> None:
        """A 200 without a usable access token is treated as a failed login."""
        hub = _Hub({"POST /api/v1/auth/login": [httpx.Response(200, json=body)]})
        session = ElderSession(
            BASE, username="svc", password=PASSWORD, transport=hub.transport()
        )
        with pytest.raises(ElderAuthError, match="did not include access_token"):
            await session.login()

    @pytest.mark.asyncio
    async def test_login_drops_non_string_refresh_token(self) -> None:
        """A malformed refresh token in the login response is discarded."""
        hub = _Hub(
            {
                "POST /api/v1/auth/login": [
                    httpx.Response(200, json={"access_token": "t", "refresh_token": 9})
                ]
            }
        )
        session = ElderSession(
            BASE, username="svc", password=PASSWORD, transport=hub.transport()
        )
        await session.login()
        assert session.refresh_token is None


class TestSessionRefresh:
    """Token refresh and expiry handling."""

    @pytest.mark.asyncio
    async def test_refresh_uses_refresh_token(self) -> None:
        """Refresh presents the refresh token as a bearer and stores the new token."""
        refresh, new = fake_secret("refresh"), _jwt(exp=NOW + 3600)
        hub = _Hub(
            {
                "POST /api/v1/auth/refresh": [
                    httpx.Response(200, json={"access_token": new})
                ]
            }
        )
        session = ElderSession(
            BASE, access_token="old", refresh_token=refresh, transport=hub.transport()
        )
        assert await session.refresh() == new
        assert hub.requests[0].headers["Authorization"] == f"Bearer {refresh}"
        assert session.access_token == new

    @pytest.mark.asyncio
    async def test_refresh_falls_back_to_login(self) -> None:
        """A rejected refresh token falls back to password login when possible."""
        new = _jwt(exp=NOW + 3600)
        hub = _Hub(
            {
                "POST /api/v1/auth/refresh": [httpx.Response(401, text="expired")],
                "POST /api/v1/auth/login": [
                    httpx.Response(200, json={"access_token": new})
                ],
            }
        )
        session = ElderSession(
            BASE,
            username="svc",
            password=PASSWORD,
            refresh_token="stale",
            transport=hub.transport(),
        )
        assert await session.refresh() == new
        assert hub.seen() == ["POST /api/v1/auth/refresh", "POST /api/v1/auth/login"]

    @pytest.mark.asyncio
    async def test_refresh_without_refresh_token_logs_in_or_fails(self) -> None:
        """Without a refresh token, credentials are used; without either it fails."""
        new = _jwt(exp=NOW + 3600)
        hub = _Hub(
            {
                "POST /api/v1/auth/login": [
                    httpx.Response(200, json={"access_token": new})
                ]
            }
        )
        with_creds = ElderSession(
            BASE, username="svc", password=PASSWORD, transport=hub.transport()
        )
        assert await with_creds.refresh() == new
        with pytest.raises(ElderAuthError, match="Cannot refresh token"):
            await ElderSession(BASE, access_token="t").refresh()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("response", "message"),
        [
            (httpx.Response(403, text="revoked"), "refresh failed with HTTP 403"),
            (httpx.Response(200, json={}), "did not include access_token"),
        ],
    )
    async def test_refresh_failures_without_credentials(
        self, response: httpx.Response, message: str
    ) -> None:
        """A failed refresh with no password fallback raises a clear auth error."""
        hub = _Hub({"POST /api/v1/auth/refresh": [response]})
        session = ElderSession(BASE, refresh_token="r", transport=hub.transport())
        with pytest.raises(ElderAuthError, match=message):
            await session.refresh()

    @pytest.mark.asyncio
    async def test_expiring_token_is_refreshed_before_use(self) -> None:
        """A token inside the 30s leeway is refreshed before being handed out."""
        new = _jwt(exp=NOW + 3600)
        hub = _Hub(
            {
                "POST /api/v1/auth/refresh": [
                    httpx.Response(200, json={"access_token": new})
                ]
            }
        )
        session = ElderSession(
            BASE,
            access_token=_jwt(exp=NOW + 10),
            refresh_token="r",
            transport=hub.transport(),
        )
        assert await session.auth_headers() == {"Authorization": f"Bearer {new}"}

    @pytest.mark.asyncio
    async def test_expired_static_token_fails_closed(self) -> None:
        """An expired static token with no way to refresh is refused."""
        session = ElderSession(BASE, access_token=_jwt(exp=NOW - 1))
        with pytest.raises(ElderAuthError, match="Static ELDER_API_TOKEN is expired"):
            await session.get_token()

    @pytest.mark.asyncio
    async def test_caller_tokens_are_not_expired_locally(self) -> None:
        """Caller-supplied tokens are passed through; hub-api decides their validity."""
        expired = _jwt(exp=NOW - 500)
        session = ElderSession.from_caller(
            BASE, CallerCredentials(access_token=expired)
        )
        assert await session.auth_headers() == {"Authorization": f"Bearer {expired}"}
        assert await session.handle_unauthorized() is None

    @pytest.mark.asyncio
    async def test_auth_header_selection(self) -> None:
        """API keys use X-API-Key; passwords log in lazily; nothing configured fails."""
        key = fake_secret("mcp", "apikey")
        assert await ElderSession.from_api_key(BASE, f"  {key} ").auth_headers() == {
            "X-API-Key": key
        }
        token = _jwt(exp=NOW + 3600)
        hub = _Hub(
            {
                "POST /api/v1/auth/login": [
                    httpx.Response(200, json={"access_token": token})
                ]
            }
        )
        lazy = ElderSession(
            BASE, username="svc", password=PASSWORD, transport=hub.transport()
        )
        assert await lazy.auth_headers() == {"Authorization": f"Bearer {token}"}
        assert await lazy.get_token() == token
        with pytest.raises(ElderAuthError, match="No Elder credentials configured"):
            await ElderSession(BASE).auth_headers()

    @pytest.mark.asyncio
    async def test_get_token_logs_in_when_no_token_yet(self) -> None:
        """With credentials but no token, get_token performs the login."""
        token = _jwt(exp=NOW + 3600)
        hub = _Hub(
            {
                "POST /api/v1/auth/login": [
                    httpx.Response(200, json={"access_token": token})
                ]
            }
        )
        session = ElderSession(
            BASE, username="svc", password=PASSWORD, transport=hub.transport()
        )
        assert await session.get_token() == token

    @pytest.mark.parametrize("key", ["", "   "])
    def test_empty_api_key_rejected(self, key: str) -> None:
        """A blank API key is refused at construction."""
        with pytest.raises(ElderAuthError, match="API key must not be empty"):
            ElderSession.from_api_key(BASE, key)

    def test_caller_session_requires_a_credential(self) -> None:
        """A caller with neither token nor API key cannot build a session."""
        with pytest.raises(ElderAuthError, match="Caller credentials are required"):
            ElderSession.from_caller(BASE, CallerCredentials(request_id="r-1"))

    def test_from_env_reads_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Environment credentials populate the session; blanks become None."""
        key = fake_secret("env", "key")
        monkeypatch.setenv("ELDER_API_KEY", key)
        monkeypatch.setenv("ELDER_API_TOKEN", "")
        monkeypatch.setenv("ELDER_USERNAME", "svc")
        monkeypatch.setenv("ELDER_PASSWORD", PASSWORD)
        session = ElderSession.from_env()
        assert (session.api_key, session.access_token, session.username) == (
            key,
            None,
            "svc",
        )
        client = ElderClient(access_token="override", api_key="k2")
        assert (client.session.access_token, client.session.api_key) == (
            "override",
            "k2",
        )
        assert key not in repr(client)


class TestClientRequests:
    """Upstream request handling."""

    def _client(self, hub: _Hub, **session_kwargs: Any) -> ElderClient:
        """Return a client whose session and requests share the scripted hub."""
        session = ElderSession(BASE, transport=hub.transport(), **session_kwargs)
        return ElderClient(session=session)

    @pytest.mark.asyncio
    async def test_401_triggers_refresh_and_single_retry(self) -> None:
        """A 401 refreshes the token and replays the request once with the new token."""
        new = _jwt(exp=NOW + 3600)
        hub = _Hub(
            {
                "GET /api/v1/entities": [
                    httpx.Response(401, json={"message": "expired"}),
                    httpx.Response(200, json={"items": []}),
                ],
                "POST /api/v1/auth/refresh": [
                    httpx.Response(200, json={"access_token": new})
                ],
            }
        )
        async with self._client(hub, access_token="old", refresh_token="r") as client:
            assert await client.get("/api/v1/entities", params={"a": None, "b": 1}) == {
                "items": []
            }
        assert hub.seen() == [
            "GET /api/v1/entities",
            "POST /api/v1/auth/refresh",
            "GET /api/v1/entities",
        ]
        assert hub.requests[0].headers["Authorization"] == "Bearer old"
        assert hub.requests[2].headers["Authorization"] == f"Bearer {new}"
        assert hub.requests[2].url.query == b"b=1"

    @pytest.mark.asyncio
    async def test_401_without_refresh_path_is_reported(self) -> None:
        """A 401 that cannot be refreshed surfaces as an API error, with no retry."""
        hub = _Hub({"GET /x": [httpx.Response(401, json={"error": "bad token"})]})
        async with self._client(hub, access_token="static") as client:
            with pytest.raises(ElderAPIError) as excinfo:
                await client.get("/x")
        assert (excinfo.value.status_code, excinfo.value.message) == (401, "bad token")
        assert len(hub.requests) == 1

    @pytest.mark.asyncio
    async def test_transport_failure_on_retry_is_masked(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A transport error on the post-refresh retry becomes a generic error."""
        secret = fake_secret("leaky", "token")
        hub = _Hub(
            {
                "GET /x": [
                    httpx.Response(401, json={}),
                    httpx.ConnectError(f"failed token={secret}"),
                ],
                "POST /api/v1/auth/refresh": [
                    httpx.Response(200, json={"access_token": "new"})
                ],
            }
        )
        with caplog.at_level(logging.WARNING, logger="hub-mcp.client"):
            async with self._client(hub, access_token="old", refresh_token="r") as c:
                with pytest.raises(ElderAPIError) as excinfo:
                    await c.get("/x")
        assert excinfo.value.status_code == 0
        assert excinfo.value.message == "Upstream request failed"
        assert secret not in caplog.text
        assert "mcp_upstream_transport_error" in caplog.text
        assert "error_type=ConnectError" in caplog.text

    @pytest.mark.asyncio
    async def test_transport_failure_is_generic_and_masked(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Connection failures never leak upstream details or secrets to the caller."""
        secret = fake_secret("dsn", "password")
        hub = _Hub({"GET /x": [httpx.ReadTimeout(f"timeout password={secret}")]})
        with caplog.at_level(logging.WARNING, logger="hub-mcp.client"):
            async with self._client(hub, api_key="k") as client:
                with pytest.raises(ElderAPIError) as excinfo:
                    await client.get("/x")
        assert str(excinfo.value) == "Elder API error 0: Upstream request failed"
        assert excinfo.value.__cause__ is None
        assert secret not in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("response", "message", "detail"),
        [
            (
                httpx.Response(404, json={"detail": "no such entity"}),
                "no such entity",
                {"detail": "no such entity"},
            ),
            (httpx.Response(500, json={}), "Internal Server Error", {}),
            (
                httpx.Response(400, json=["bad", "input"]),
                "['bad', 'input']",
                ["bad", "input"],
            ),
            (
                httpx.Response(502, text="  upstream down  "),
                "upstream down",
                "upstream down",
            ),
            (httpx.Response(503, text=""), "Service Unavailable", ""),
        ],
        ids=["json-detail", "empty-object", "json-list", "plain-text", "empty-body"],
    )
    async def test_error_bodies_are_summarised(
        self, response: httpx.Response, message: str, detail: Any
    ) -> None:
        """Error bodies of any shape produce a message and structured detail."""
        hub = _Hub({"GET /x": [response]})
        async with self._client(hub, api_key="k") as client:
            with pytest.raises(ElderAPIError) as excinfo:
                await client.get("/x")
        assert (excinfo.value.message, excinfo.value.detail) == (message, detail)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            (httpx.Response(204), {"status": "ok", "status_code": 204}),
            (httpx.Response(200, content=b""), {"status": "ok", "status_code": 200}),
            (httpx.Response(200, text="plain ok"), {"raw": "plain ok"}),
            (httpx.Response(200, json=[1, 2]), [1, 2]),
        ],
        ids=["no-content", "empty-200", "non-json", "json-list"],
    )
    async def test_success_bodies(
        self, response: httpx.Response, expected: Any
    ) -> None:
        """Empty, non-JSON and JSON success bodies are all returned predictably."""
        hub = _Hub({"DELETE /x": [response]})
        async with self._client(hub, api_key="k") as client:
            assert await client.delete("/x") == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("declared", [True, False], ids=["declared", "streamed"])
    async def test_oversized_response_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, declared: bool
    ) -> None:
        """Responses over the byte cap are refused whether or not length is declared."""
        monkeypatch.setenv("MCP_MAX_RESPONSE_BYTES", "64")
        body = b"x" * 500

        async def chunks() -> Any:
            """Stream the body without a content-length header."""
            yield body[:100]
            yield body[100:]

        response = (
            httpx.Response(200, content=body)
            if declared
            else httpx.Response(200, content=chunks())
        )
        hub = _Hub({"GET /big": [response]})
        async with self._client(hub, api_key="k") as client:
            with pytest.raises(ElderResponseTooLargeError) as excinfo:
                await client.get("/big")
        assert excinfo.value.status_code == 502
        assert "64-byte limit" in excinfo.value.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verb", ["post", "put", "patch"])
    async def test_write_verbs_send_json_and_params(self, verb: str) -> None:
        """Write helpers forward the JSON body and drop None-valued params."""
        hub = _Hub({f"{verb.upper()} /w": [httpx.Response(200, json={"ok": 1})]})
        async with self._client(hub, api_key="k") as client:
            result = await getattr(client, verb)(
                "/w", json={"a": 1}, params={"dry": "true", "skip": None}
            )
        request = hub.requests[0]
        assert result == {"ok": 1}
        assert json.loads(request.content) == {"a": 1}
        assert request.url.query == b"dry=true"
        assert request.headers["X-API-Key"] == "k"

    @pytest.mark.asyncio
    async def test_caller_trace_headers_propagate_but_auth_wins(self) -> None:
        """Caller request/trace ids are forwarded; the session's auth header is used."""
        hub = _Hub({"GET /x": [httpx.Response(200, json={})]})
        creds = CallerCredentials(
            access_token="caller",
            request_id="req-1",
            traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
            tracestate="vendor=1",
        )
        session = ElderSession.from_caller(BASE, creds, transport=hub.transport())
        async with ElderClient(session=session, caller_credentials=creds) as client:
            await client.get("/x")
        headers = hub.requests[0].headers
        assert headers["X-Request-ID"] == "req-1"
        assert headers["traceparent"] == creds.traceparent
        assert headers["tracestate"] == "vendor=1"
        assert headers["Authorization"] == "Bearer caller"
        assert "caller" not in repr(creds)

    @pytest.mark.asyncio
    async def test_shared_http_client_is_not_closed(self) -> None:
        """A client handed a shared pool leaves it open; an owned one is closed."""
        hub = _Hub({"GET /x": [httpx.Response(200, json={})]})
        shared = client_mod.create_pooled_http_client(BASE, transport=hub.transport())
        borrowed = ElderClient(
            session=ElderSession(BASE, api_key="k"), http_client=shared
        )
        await borrowed.get("/x")
        await borrowed.close()
        assert shared.is_closed is False
        await shared.aclose()
        owned = ElderClient(
            session=ElderSession(BASE, api_key="k", transport=hub.transport())
        )
        await owned.get("/x")
        pool = owned._client
        await owned.close()
        assert pool.is_closed is True and owned._client is None

    @pytest.mark.xfail(
        strict=True,
        reason="bug: apps/mcp/client.py:65 httpx.AsyncClient copies the cookies argument "
        "into a plain Cookies jar, so _NoCookies is discarded and an upstream "
        "Set-Cookie is stored in the pooled client and replayed on later requests",
    )
    @pytest.mark.asyncio
    async def test_upstream_cookies_are_never_stored_or_replayed(self) -> None:
        """Set-Cookie from hub-api is ignored so sessions cannot bleed across callers."""
        hub = _Hub(
            {
                "GET /x": [
                    httpx.Response(
                        200, json={}, headers={"Set-Cookie": "sid=abc; Path=/"}
                    )
                ]
            }
        )
        async with self._client(hub, api_key="k") as client:
            await client.get("/x")
            await client.get("/x")
        assert "cookie" not in hub.requests[1].headers


class TestMessages:
    """Error message helpers."""

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (0, "Upstream request failed"),
            (-1, "Upstream request failed"),
            (404, "Not Found"),
            (429, "Too Many Requests"),
            (799, "Upstream request failed"),
        ],
    )
    def test_public_error_message(self, status: int, expected: str) -> None:
        """Only standard reason phrases are exposed; anything else is generic."""
        assert public_error_message(status) == expected

    def test_mask_secrets(self) -> None:
        """Bearer tokens, API keys and credential query values are masked."""
        secret = fake_secret("masked", "value")
        text = (
            f"Authorization: Bearer {secret} x-api-key: {secret} "
            f"url?token={secret}&password={secret}"
        )
        masked = mask_secrets(text)
        assert secret not in masked and masked.count("****") == 4
