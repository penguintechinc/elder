"""Unit tests for ElderClient and AsyncElderClient using httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from elder_sdk import (
    ApiKeyAuth,
    AsyncElderClient,
    AuthConfig,
    BearerAuth,
    ElderAuthError,
    ElderClient,
    ElderNotFoundError,
    ElderRateLimitError,
    ElderServerError,
    ElderTransportError,
    ElderValidationError,
)
from elder_sdk.client import (
    _extract_page_items,
    _has_more_pages,
)


def test_client_construction_and_secret_masking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELDER_API_URL", "https://env.elder.local")
    monkeypatch.setenv("ELDER_TOKEN", "env_secret_jwt_token_value")
    monkeypatch.delenv("ELDER_API_KEY", raising=False)

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"status": "healthy"})
    )
    with ElderClient.from_env(transport=transport) as client:
        assert client.base_url == "https://env.elder.local"
        assert "env_secret_jwt_token_value" not in repr(client)
        assert "env_secret_jwt_token_value" not in str(client)
        assert client.healthz() == {"status": "healthy"}

    cfg = AuthConfig(
        base_url="https://cfg.elder.local",
        auth=ApiKeyAuth("elder_key_secret_99"),
    )
    c2 = ElderClient(
        config=cfg,
        default_headers={"X-Client": "sdk-test"},
        transport=transport,
    )
    assert c2.config is cfg
    c2.close()

    with pytest.raises(ValueError, match="max_retries"):
        ElderClient(base_url="https://elder.example.com", max_retries=-1)
    with pytest.raises(ValueError, match="backoff_factor"):
        ElderClient(base_url="https://elder.example.com", backoff_factor=-0.1)


def test_http_verbs_and_response_decoding() -> None:
    seen: list[tuple[str, str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                request.url.query.decode(),
                request.headers.get("X-API-Key"),
            )
        )
        if request.url.path == "/api/v1/csv":
            return httpx.Response(
                200, text="a,b\n1,2\n", headers={"Content-Type": "text/csv"}
            )
        if request.url.path == "/api/v1/bin":
            return httpx.Response(
                200,
                content=b"\x00\x01\x02",
                headers={"Content-Type": "application/octet-stream"},
            )
        if request.url.path == "/api/v1/fallback-json":
            return httpx.Response(
                200,
                content=b'{"fallback": true}',
                headers={"Content-Type": "application/octet-stream"},
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"method": request.method})

    with ElderClient(
        base_url="https://elder.example.com",
        api_key="dummy-key-placeholder",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.get("api/v1/items", params={"a": "1", "drop": None}) == {
            "method": "GET"
        }
        assert client.post("https://elder.example.com/api/v1/items", json={"x": 1}) == {
            "method": "POST"
        }
        assert client.put("/api/v1/items/1", json={"x": 2}) == {"method": "PUT"}
        assert client.patch("/api/v1/items/1", json={"x": 3}) == {"method": "PATCH"}
        assert client.delete("/api/v1/items/1") is None
        assert client.get("/api/v1/csv") == "a,b\n1,2\n"
        assert client.get("/api/v1/bin") == b"\x00\x01\x02"
        assert client.get("/api/v1/fallback-json") == {"fallback": True}

        with pytest.raises(ValueError, match="Request path"):
            client.get("   ")

    assert seen[0] == ("GET", "/api/v1/items", "a=1", "dummy-key-placeholder")


def test_retry_policy_sync_and_no_retry_on_client_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("elder_sdk.client.time.sleep", sleeps.append)

    attempts = 0

    def transient_then_ok(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("reset", request=request)
        if attempts == 2:
            return httpx.Response(
                429, json={"error": "rate limit"}, headers={"Retry-After": "1.5"}
            )
        if attempts == 3:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"ok": True})

    with ElderClient(
        base_url="https://elder.example.com",
        max_retries=3,
        backoff_factor=0.5,
        transport=httpx.MockTransport(transient_then_ok),
    ) as client:
        assert client.get("/api/v1/status") == {"ok": True}
    assert attempts == 4
    assert sleeps == [0.5, 1.5, 2.0]

    # Exhausted transport error
    def always_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with ElderClient(
        base_url="https://elder.example.com",
        max_retries=1,
        backoff_factor=0.0,
        transport=httpx.MockTransport(always_timeout),
    ) as client:
        with pytest.raises(ElderTransportError):
            client.get("/api/v1/status")

    # Exhausted 429 & 502/504
    calls_429 = 0

    def always_429(_request: httpx.Request) -> httpx.Response:
        nonlocal calls_429
        calls_429 += 1
        return httpx.Response(429, json={"error": "too many"})

    with ElderClient(
        base_url="https://elder.example.com",
        max_retries=2,
        backoff_factor=0.0,
        transport=httpx.MockTransport(always_429),
    ) as client:
        with pytest.raises(ElderRateLimitError):
            client.get("/api/v1/issues")
    assert calls_429 == 3

    # NEVER retry 400, 401, 403, 404, 422, 500
    for code, exc_cls in [
        (400, ElderValidationError),
        (401, ElderAuthError),
        (403, ElderAuthError),
        (404, ElderNotFoundError),
        (422, ElderValidationError),
        (500, ElderServerError),
    ]:
        count = 0

        def fail_once(_request: httpx.Request, status: int = code) -> httpx.Response:
            nonlocal count
            count += 1
            return httpx.Response(status, json={"error": f"err-{status}"})

        with ElderClient(
            base_url="https://elder.example.com",
            max_retries=3,
            backoff_factor=0.0,
            transport=httpx.MockTransport(fail_once),
        ) as client:
            with pytest.raises(exc_cls):
                client.get("/api/v1/issues")
        assert count == 1


async def test_async_client_retries_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("elder_sdk.client.asyncio.sleep", fake_sleep)

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("blip", request=request)
        if attempts == 2:
            return httpx.Response(502, json={"error": "bad gateway"})
        if attempts == 3:
            return httpx.Response(504, json={"error": "timeout"})
        return httpx.Response(200, json={"status": "operational"})

    async with AsyncElderClient.from_env(
        max_retries=3,
        backoff_factor=0.1,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert await client.get("/api/v1/status") == {"status": "operational"}
    assert attempts == 4
    assert sleeps == [0.1, 0.2, 0.4]

    async def fail_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async with AsyncElderClient(
        base_url="https://elder.example.com",
        max_retries=1,
        backoff_factor=0.0,
        transport=httpx.MockTransport(fail_transport),
    ) as client:
        with pytest.raises(ElderTransportError):
            await client.post("/api/v1/issues", json={})

    # Never retry 401/403/400/404/422
    calls = 0

    def auth_fail(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, json={"error": "forbidden"})

    async with AsyncElderClient(
        base_url="https://elder.example.com",
        max_retries=3,
        backoff_factor=0.0,
        transport=httpx.MockTransport(auth_fail),
    ) as client:
        with pytest.raises(ElderAuthError):
            await client.delete("/api/v1/issues/1")
    assert calls == 1


def test_pagination_variants_sync_and_helpers() -> None:
    assert _extract_page_items("not-a-list", None) == []
    assert _extract_page_items({"custom": [1, 2]}, "missing") == []
    assert _extract_page_items({"arbitrary": [3, 4]}, None) == [3, 4]
    assert _extract_page_items({"x": 1}, None) == []
    assert _has_more_pages({}, [], 1, 10, 0) is False
    assert _has_more_pages({"total": 5}, [1, 2], 1, 2, 2) is True
    assert _has_more_pages({"total": 2}, [1, 2], 1, 2, 2) is False
    assert _has_more_pages([1, 2], [1, 2], 1, 2, 2) is True
    assert _has_more_pages([1], [1], 1, 2, 1) is False

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        if request.url.path == "/api/v1/issues":
            return httpx.Response(
                200,
                json={
                    "items": [{"id": page * 10 + 1}, {"id": page * 10 + 2}],
                    "page": page,
                    "per_page": 2,
                    "pages": 2,
                    "total": 4,
                },
            )
        return httpx.Response(
            200,
            json={
                "history": [{"id": page}],
                "pagination": {"page": page, "per_page": 1, "pages": 3},
            },
        )

    with ElderClient(
        base_url="https://elder.example.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        all_issues = list(client.paginate("/api/v1/issues", per_page=2))
        assert [i["id"] for i in all_issues] == [11, 12, 21, 22]

        capped = list(client.paginate("/api/v1/sync/history", per_page=1, max_pages=2))
        assert [h["id"] for h in capped] == [1, 2]


async def test_async_pagination_and_convenience_methods() -> None:
    calls: list[tuple[str, str, object, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        calls.append(
            (
                request.method,
                request.url.path,
                body,
                request.headers.get("Authorization"),
            )
        )
        p = request.url.path
        if p == "/api/v1/auth/login":
            return httpx.Response(
                200,
                json={"access_token": "acc-1", "refresh_token": "ref-1"},
            )
        if p == "/api/v1/portal-auth/login":
            return httpx.Response(200, json={"mfa_required": True})
        if p == "/api/v1/auth/refresh":
            return httpx.Response(200, json={"access_token": "acc-2"})
        if p == "/api/v1/id/00000001-00000000000000aa":
            return httpx.Response(
                200, json={"resource_type": "entity", "resource_id": 7}
            )
        if p == "/api/v1/licensing/status":
            return httpx.Response(200, json={"tier": "enterprise", "status": "active"})
        if p == "/api/v1/search/ai-research":
            return httpx.Response(200, json={"answer": "All DBs encrypted"})
        if p == "/healthz":
            return httpx.Response(200, json={"status": "healthy"})
        if p == "/api/v1/entities":
            page = int(request.url.params["page"])
            items = [{"id": 1}, {"id": 2}] if page == 1 else [{"id": 3}]
            return httpx.Response(200, json={"items": items, "total": 3})
        return httpx.Response(200, json={"ok": True})

    async with AsyncElderClient(
        base_url="https://elder.example.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ElderAuthError):
            await client.login("", "pw")
        with pytest.raises(ElderAuthError):
            await client.login("a@b.com", "")
        with pytest.raises(ElderAuthError):
            await client.refresh_token()

        login_res = await client.login("admin@example.com", "pw123")
        assert login_res["access_token"] == "acc-1"
        assert isinstance(client.config.auth, BearerAuth)
        assert client.config.auth.token == "acc-1"

        mfa_res = await client.login(
            "admin@example.com", "pw123", portal=True, tenant="acme"
        )
        assert mfa_res == {"mfa_required": True}
        assert client.config.auth.token == "acc-1"

        ref_res = await client.refresh_token()
        assert ref_res["access_token"] == "acc-2"
        assert client.config.auth.token == "acc-2"

        vid_res = await client.resolve_village_id("00000001-00000000000000aa")
        assert vid_res["resource_id"] == 7

        lic_res = await client.licensing_status()
        assert lic_res["tier"] == "enterprise"

        ai_res = await client.ai_research(
            "check encryption",
            model="gemma4:e4b",
            history=[{"role": "user", "content": "hi"}],
            max_steps=3,
        )
        assert ai_res["answer"] == "All DBs encrypted"
        assert await client.healthz() == {"status": "healthy"}

        # Dynamic fallback to catalog-only service (not in 48 typed facades)
        await client.diagram_collections.list()
        assert "diagram_collections" in dir(client)
        with pytest.raises(AttributeError):
            _ = client._private
        with pytest.raises(AttributeError):
            _ = client.does_not_exist

        paged = [item async for item in client.paginate("/api/v1/entities", per_page=2)]
        assert [i["id"] for i in paged] == [1, 2, 3]
        capped = [
            item
            async for item in client.paginate(
                "/api/v1/entities", per_page=2, max_pages=1
            )
        ]
        assert len(capped) == 2

        await client.put("/api/v1/x", json={"a": 1})
        await client.patch("/api/v1/x", json={"a": 2})
        await client.close()

    assert calls[0][2] == {"username": "admin@example.com", "password": "pw123"}
    assert calls[1][2] == {
        "email": "admin@example.com",
        "password": "pw123",
        "tenant": "acme",
    }
    assert calls[3][3] == "Bearer acc-2"


def test_sync_convenience_methods_and_attr_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(
                200, json={"access_token": "sync-acc", "refresh_token": "sync-ref"}
            )
        if request.url.path == "/api/v1/auth/refresh":
            return httpx.Response(200, json={"access_token": "sync-acc-2"})
        if request.url.path == "/api/v1/id/00000001-00000000000000bb":
            return httpx.Response(
                200, json={"resource_type": "issue", "resource_id": 9}
            )
        if request.url.path == "/api/v1/licensing/status":
            return httpx.Response(200, json={"tier": "free"})
        if request.url.path == "/api/v1/search/ai-research":
            return httpx.Response(200, json={"answer": "ok"})
        return httpx.Response(200, json={"ok": True})

    with ElderClient(
        base_url="https://elder.example.com",
        transport=httpx.MockTransport(handler),
    ) as client:
        client.login("admin@example.com", "pw")
        client.refresh_token()
        resolved = client.resolve_village_id("00000001-00000000000000bb")
        assert resolved["resource_id"] == 9
        assert client.licensing_status()["tier"] == "free"
        assert client.ai_research("hello")["answer"] == "ok"
        assert client.integrations_internal.get_pending_integrations() == {"ok": True}
        assert "integrations_internal" in dir(client)
        with pytest.raises(AttributeError):
            _ = client._secret
        with pytest.raises(AttributeError):
            _ = client.nonexistent_service
