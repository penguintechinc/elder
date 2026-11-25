"""Unit tests for the search API blueprint.

The real app, auth, scope and tenant checks run; ``SearchService`` and the AI
researcher backends are replaced with mocks so branches can be driven directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

BASE = "/api/v1/search"
MOD = "apps.api.api.v1.search"


def _ident(db, role: str = "admin", superuser: bool = False) -> int:
    """Insert a tenant plus identity with ``role``."""
    tid = db.tenants.insert(
        name="cov-b", slug=f"covb-{uuid4().hex[:10]}", is_active=True
    )
    name = f"covb-{uuid4().hex[:8]}"
    iid = db.identities.insert(
        username=name,
        email=f"{name}@example.test",
        identity_type="human",
        auth_provider="local",
        password_hash=generate_password_hash(uuid4().hex),
        is_active=True,
        is_superuser=superuser,
        mfa_enabled=False,
        must_change_password=False,
        portal_role=role,
        tenant_id=tid,
    )
    db.commit()
    return iid


async def _h(app, iid: int) -> dict[str, str]:
    """Bearer headers for ``iid``."""
    from apps.api.auth.jwt_handler import generate_token

    async with app.app_context():
        tok = generate_token(app.db.identities[iid], "access")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def svc():
    """Patch the search service factory and yield the mock."""
    service = MagicMock()
    with patch(f"{MOD}.get_search_service", return_value=service):
        yield service


@pytest.fixture
def admin(app):
    """Identity id of an admin."""
    return _ident(app.db)


def test_get_search_service_uses_read_replica(app):
    """The factory wraps the read-replica handle."""
    import asyncio

    from apps.api.api.v1 import search

    async def run():
        """Call the factory in an app context."""
        async with app.app_context():
            assert search.get_search_service() is not None

    asyncio.run(run())


@pytest.mark.asyncio
async def test_search_all(app, async_client, admin, svc):
    """search_all parses types and filters and maps errors."""
    h = await _h(app, admin)
    svc.search_all.return_value = {"results": []}
    r = await async_client.get(
        f"{BASE}?q=x&types=entity,issue&filters=%7B%22a%22:1%7D&limit=5&offset=2",
        headers=h,
    )
    assert r.status_code == 200
    kw = svc.search_all.call_args.kwargs
    assert kw["filters"] == {"a": 1} and kw["resource_types"] == ["entity", "issue"]
    assert (
        await async_client.get(f"{BASE}?filters=notjson", headers=h)
    ).status_code == 400
    svc.search_all.side_effect = RuntimeError("boom")
    assert (await async_client.get(BASE, headers=h)).status_code == 500

    viewer = await _h(app, _ident(app.db, "observer"))
    svc.search_all.side_effect = None
    r = await async_client.get(f"{BASE}?types=entity,issue", headers=viewer)
    assert r.status_code == 200
    assert (await async_client.get(BASE)).status_code == 401


@pytest.mark.asyncio
async def test_readable_types_drops_issues_without_scope(app):
    """Issue searches need issues:read in addition to infrastructure:read."""
    from quart import g

    from apps.api.api.v1.search import _readable_types

    async with app.test_request_context("/"):
        g.claims = {"scope": ["infrastructure:read"]}
        assert _readable_types(["entity", "issue"]) == ["entity"]
        g.claims = {"scope": ["issues:read"]}
        assert _readable_types(["entity", "issue"]) == ["entity", "issue"]


@pytest.mark.asyncio
async def test_tenant_required_when_claim_missing(app):
    """A missing tenant claim yields a 403 helper response."""
    from quart import g

    from apps.api.api.v1.search import _tenant_or_403

    async with app.test_request_context("/"):
        g.claims = {"tenant": ""}
        tenant, denied = _tenant_or_403()
        assert tenant is None and denied[1] == 403
        g.claims = {"tenant": "-4"}
        assert _tenant_or_403()[0] is None
        g.claims = {"tenant": "9"}
        assert _tenant_or_403() == (9, None)


@pytest.mark.asyncio
async def test_specific_searches(app, async_client, admin, svc):
    """Entity, organization, issue, popular and suggestion searches."""
    h = await _h(app, admin)
    svc.search_entities.return_value = {"r": 1}
    r = await async_client.get(
        f"{BASE}/entities?q=a&entity_type=compute&sub_type=s&organization_id=3&tags=a,b,&filters=%7B%7D",
        headers=h,
    )
    assert r.status_code == 200 and svc.search_entities.call_args.kwargs["tags"] == [
        "a",
        "b",
    ]
    assert (
        await async_client.get(f"{BASE}/entities?filters=zzz", headers=h)
    ).status_code == 400
    svc.search_entities.side_effect = RuntimeError("x")
    assert (await async_client.get(f"{BASE}/entities", headers=h)).status_code == 500

    svc.search_organizations.return_value = {}
    r = await async_client.get(
        f"{BASE}/organizations?q=a&organization_type=team&parent_id=2", headers=h
    )
    assert r.status_code == 200
    svc.search_organizations.side_effect = RuntimeError("x")
    assert (
        await async_client.get(f"{BASE}/organizations", headers=h)
    ).status_code == 500

    svc.search_issues.return_value = {}
    r = await async_client.get(
        f"{BASE}/issues?q=a&status=open&priority=high&assignee_id=1&organization_id=2&labels=x,y",
        headers=h,
    )
    assert r.status_code == 200 and svc.search_issues.call_args.kwargs["labels"] == [
        "x",
        "y",
    ]
    svc.search_issues.side_effect = RuntimeError("x")
    assert (await async_client.get(f"{BASE}/issues", headers=h)).status_code == 500

    svc.get_popular_searches.return_value = [1]
    r = await async_client.get(f"{BASE}/analytics/popular", headers=h)
    assert (await r.get_json())["count"] == 1
    svc.get_popular_searches.side_effect = RuntimeError("x")
    assert (
        await async_client.get(f"{BASE}/analytics/popular", headers=h)
    ).status_code == 500

    svc.get_search_suggestions.return_value = ["a", "b"]
    r = await async_client.get(f"{BASE}/suggest?q=a&type=entity", headers=h)
    assert (await r.get_json())["count"] == 2
    svc.get_search_suggestions.side_effect = RuntimeError("x")
    assert (await async_client.get(f"{BASE}/suggest", headers=h)).status_code == 500


@pytest.mark.asyncio
async def test_saved_searches(app, async_client, admin, svc):
    """Saved search CRUD maps validation, not-found and generic errors."""
    h = await _h(app, admin)
    S = f"{BASE}/saved"
    svc.list_saved_searches.return_value = [{"id": 1}]
    r = await async_client.get(S, headers=h)
    assert (await r.get_json())["count"] == 1
    svc.list_saved_searches.side_effect = RuntimeError("x")
    assert (await async_client.get(S, headers=h)).status_code == 500

    assert (await async_client.post(S, json={}, headers=h)).status_code == 400
    r = await async_client.post(S, json={"name": "n"}, headers=h)
    assert r.status_code == 400 and "query" in (await r.get_json())["error"]
    svc.create_saved_search.return_value = {"id": 2}
    full = {
        "name": "n",
        "query": "q",
        "resource_type": "entity",
        "filters": {},
        "description": "d",
    }
    assert (await async_client.post(S, json=full, headers=h)).status_code == 201
    svc.create_saved_search.side_effect = RuntimeError("x")
    assert (await async_client.post(S, json=full, headers=h)).status_code == 400

    cases = [
        ("get", f"{S}/1", "get_saved_search", None, 500),
        ("get", f"{S}/1/execute", "execute_saved_search", None, 500),
        ("put", f"{S}/1", "update_saved_search", {"name": "n"}, 400),
        ("delete", f"{S}/1", "delete_saved_search", None, 500),
    ]
    for method, url, attr, body, other in cases:
        kwargs = {"headers": h}
        if body:
            kwargs["json"] = body
        getattr(svc, attr).return_value = {"ok": True}
        assert (
            await getattr(async_client, method)(url, **kwargs)
        ).status_code == 200, attr
        getattr(svc, attr).side_effect = RuntimeError("Search not found")
        assert (
            await getattr(async_client, method)(url, **kwargs)
        ).status_code == 404, attr
        getattr(svc, attr).side_effect = RuntimeError("not owned by user")
        assert (
            await getattr(async_client, method)(url, **kwargs)
        ).status_code == 404, attr
        getattr(svc, attr).side_effect = RuntimeError("kaboom")
        assert (
            await getattr(async_client, method)(url, **kwargs)
        ).status_code == other, attr
    assert (await async_client.put(f"{S}/1", json={}, headers=h)).status_code == 400


@pytest.mark.asyncio
async def test_search_graph_validation(app, async_client, admin):
    """Graph search validates its body before touching the database."""
    h = await _h(app, admin)
    G = f"{BASE}/graph"
    assert (await async_client.post(G, json={}, headers=h)).status_code == 400
    assert (
        await async_client.post(G, json={"start_entity_id": 0}, headers=h)
    ).status_code == 400
    bad_types = [["x"] * 51, "str", [1], ["y" * 65]]
    for dep in bad_types:
        r = await async_client.post(
            G, json={"start_entity_id": 1, "dependency_types": dep}, headers=h
        )
        assert r.status_code == 400
    with patch(f"{MOD}.scoped_entity_search_graph", return_value=None):
        r = await async_client.post(
            G, json={"start_entity_id": 5, "max_depth": 2}, headers=h
        )
        assert r.status_code == 404
    with patch(f"{MOD}.scoped_entity_search_graph", return_value={"nodes": []}):
        r = await async_client.post(
            G,
            json={"start_entity_id": 5, "dependency_types": ["depends_on"]},
            headers=h,
        )
        assert r.status_code == 200
    with patch(f"{MOD}.scoped_entity_search_graph", side_effect=RuntimeError("x")):
        r = await async_client.post(G, json={"start_entity_id": 5}, headers=h)
        assert r.status_code == 500
    with patch(f"{MOD}.get_current_tenant_id", return_value=None):
        r = await async_client.post(G, json={"start_entity_id": 5}, headers=h)
        assert r.status_code == 403


def test_resolve_request_tenant_id(app):
    """Tenant resolution accepts ints/strings and rejects junk."""
    import asyncio
    from types import SimpleNamespace

    from quart import g

    from apps.api.api.v1.search import _resolve_request_tenant_id as f

    async def run():
        """Run the resolver under varied contexts."""
        cases = [
            (SimpleNamespace(tenant_id=5), {}, 5),
            (SimpleNamespace(tenant_id=True), {}, None),
            (SimpleNamespace(tenant_id=None), {"tenant": "8"}, 8),
            (SimpleNamespace(tenant_id=None), {"tenant": ""}, None),
            (SimpleNamespace(tenant_id="abc"), {}, None),
            (SimpleNamespace(tenant_id=-3), {}, None),
            (None, {"tenant": "4"}, 4),
        ]
        for user, claims, want in cases:
            async with app.test_request_context("/"):
                if user is not None:
                    g.current_user = user
                g.claims = claims
                assert f() == want

    asyncio.run(run())


def test_resolve_ai_research_caller(app):
    """Caller extraction falls back to service researcher scopes for superusers."""
    import asyncio
    from types import SimpleNamespace

    from quart import g

    from apps.api.api.v1.search import _resolve_ai_research_caller as f

    async def run():
        """Run the extractor."""
        async with app.test_request_context("/"):
            g.current_user = SimpleNamespace(id=3, username="u", is_superuser=True)
            g.claims = {}
            sub, scopes, uname = f()
            assert sub == "3" and uname == "u" and scopes
        async with app.test_request_context("/"):
            g.current_user = SimpleNamespace(id=3, username="u", is_superuser=False)
            g.claims = {"sub": "9", "scope": ["a:read"]}
            assert f() == ("9", ["a:read"], "u")
        async with app.test_request_context("/"):
            g.claims = {}
            assert f() == ("", [], None)

    asyncio.run(run())


@pytest.mark.asyncio
async def test_ai_research_config(app, async_client, admin):
    """Config endpoint maps researcher errors."""
    from apps.api.services.ai_researcher import AIResearchPermissionError

    h = await _h(app, admin)
    C = f"{BASE}/ai-research/config"
    with patch(
        f"{MOD}.get_ai_researcher_config", new=AsyncMock(return_value={"model": "m"})
    ):
        assert (await async_client.get(C, headers=h)).status_code == 200
    with patch(
        f"{MOD}.get_ai_researcher_config",
        new=AsyncMock(side_effect=AIResearchPermissionError("no")),
    ):
        assert (await async_client.get(C, headers=h)).status_code == 403
    with patch(
        f"{MOD}.get_ai_researcher_config", new=AsyncMock(side_effect=RuntimeError("x"))
    ):
        assert (await async_client.get(C, headers=h)).status_code == 500
    with patch(f"{MOD}._resolve_request_tenant_id", return_value=None):
        assert (await async_client.get(C, headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_ai_research_run(app, async_client, admin):
    """Research endpoint covers admission, validation and error mapping."""
    from apps.api.services.ai_researcher import (
        AIResearchMcpUnavailableError,
        AIResearchPermissionError,
        AIResearchValidationError,
        OllamaUnavailableError,
    )

    h = await _h(app, admin)
    R = f"{BASE}/ai-research"
    ok_rate = AsyncMock(return_value=(True, {}))
    with patch(f"{MOD}.check_subject_rate_limit", new=ok_rate):
        r = await async_client.post(
            R, data="[1]", headers={**h, "Content-Type": "application/json"}
        )
        assert r.status_code == 400
        with patch(
            f"{MOD}.run_ai_research", new=AsyncMock(return_value={"answer": "x"})
        ) as run:
            r = await async_client.post(
                R,
                json={"q": "hi", "model": "m", "history": [], "max_steps": 2},
                headers=h,
            )
            assert r.status_code == 200
            assert (
                run.call_args.kwargs["query"] == "hi"
                and run.call_args.kwargs["max_steps"] == 2
            )
            r = await async_client.post(
                R,
                json={"query": "hi", "model": 5, "history": "x", "max_steps": "a"},
                headers=h,
            )
            assert r.status_code == 200
        for exc, status in (
            (AIResearchPermissionError("no"), 403),
            (AIResearchValidationError("bad"), 400),
            (OllamaUnavailableError("down"), 503),
            (AIResearchMcpUnavailableError("mcp"), 503),
            (RuntimeError("x"), 500),
        ):
            with patch(f"{MOD}.run_ai_research", new=AsyncMock(side_effect=exc)):
                r = await async_client.post(R, json={"query": "x"}, headers=h)
                assert r.status_code == status, exc

    with patch(
        f"{MOD}.check_subject_rate_limit",
        new=AsyncMock(return_value=(False, {"Retry-After": "30"})),
    ):
        r = await async_client.post(R, json={"query": "x"}, headers=h)
        assert r.status_code == 429 and r.headers["Retry-After"] == "30"
    with (
        patch(f"{MOD}.check_subject_rate_limit", new=ok_rate),
        patch(f"{MOD}._ai_research_inflight.try_acquire", return_value=False),
    ):
        r = await async_client.post(R, json={"query": "x"}, headers=h)
        assert r.status_code == 429 and r.headers["Retry-After"] == "5"
    with patch(f"{MOD}._resolve_request_tenant_id", return_value=None):
        assert (await async_client.post(R, json={}, headers=h)).status_code == 403
    with patch(f"{MOD}.get_token_from_header", return_value=None):
        assert (await async_client.post(R, json={}, headers=h)).status_code == 401


@pytest.mark.asyncio
async def test_ai_research_blocks_recursive_service_tokens(app, async_client, admin):
    """service_researcher principals cannot call AI research."""
    h = await _h(app, admin)
    with patch("apps.api.auth.decorators.get_current_user") as gcu:
        user = MagicMock(
            id=1, is_superuser=True, tenant_id=1, token_type="service_researcher"
        )
        gcu.return_value = user
        r = await async_client.post(
            f"{BASE}/ai-research", json={"query": "x"}, headers=h
        )
    assert r.status_code == 403
