"""Hardening tests for graph routes and search pagination bounds.

Covers tenant scoping on every graph route, the traversal depth clamp, the
node/edge caps with their ``truncated`` indicator, the per-level query bound
of the subgraph walk, and the page-size clamp on search endpoints.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app
from sqlalchemy import event

from apps.api.modules.infrastructure.routes import graph as graph_routes

OWN_TENANT = 1
SCOPES = ["infrastructure:read", "issues:read"]


def _user() -> MagicMock:
    """Return a stand-in authenticated user bound to the caller's tenant."""
    return MagicMock(id=1, is_superuser=False, tenant_id=OWN_TENANT)


def _tenant(db) -> int:
    """Create and return a tenant distinct from the caller's tenant."""
    tid = db.tenants.insert(
        name="Bounds Other Tenant", slug=f"bounds-{uuid4().hex[:8]}", is_active=True
    )
    db.commit()
    return tid


def _org(db, tenant_id: int, name: str) -> int:
    """Create an organization owned by ``tenant_id``."""
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=name, tenant_id=tenant_id, created_at=now, updated_at=now
    )
    db.commit()
    return org_id


def _entity(db, org_id: int, name: str) -> int:
    """Create a compute entity in ``org_id``."""
    now = datetime.now(UTC)
    entity_id = db.entities.insert(
        name=name,
        type="compute",
        organization_id=org_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return entity_id


def _edge(db, source_id: int, target_id: int) -> int:
    """Create an entity-to-entity dependency."""
    dep_id = db.dependencies.insert(
        source_type="entity",
        source_id=source_id,
        target_type="entity",
        target_id=target_id,
        dependency_type="depends_on",
    )
    db.commit()
    return dep_id


def _two_tenant_graph(db) -> dict:
    """Seed an own-tenant graph bridged through another tenant's entity.

    own: a1 -> a3 (legitimate edge), a2 isolated from a1 inside the tenant.
    foreign: b1 -> b2, plus cross-tenant edges a1 -> b1 -> a2 that must never
    be traversed or surfaced.
    """
    tag = uuid4().hex[:10]
    own, foreign = f"own-{tag}", f"foreign-{tag}"
    own_org = _org(db, OWN_TENANT, f"{own}-org")
    foreign_org = _org(db, _tenant(db), f"{foreign}-org")
    a1 = _entity(db, own_org, f"{own}-a1")
    a2 = _entity(db, own_org, f"{own}-a2")
    a3 = _entity(db, own_org, f"{own}-a3")
    b1 = _entity(db, foreign_org, f"{foreign}-b1")
    b2 = _entity(db, foreign_org, f"{foreign}-b2")
    _edge(db, a1, a3)
    _edge(db, b1, b2)
    _edge(db, a1, b1)
    _edge(db, b1, a2)
    return {
        "own": own,
        "foreign": foreign,
        "own_org": own_org,
        "foreign_org": foreign_org,
        "a1": a1,
        "a2": a2,
        "b1": b1,
    }


# (route template, own-tenant data expected in the body)
GRAPH_ROUTES = [
    ("/api/v1/graph?organization_id={own_org}", True),
    ("/api/v1/graph?organization_id={foreign_org}", False),
    ("/api/v1/graph?entity_id={a1}&depth=-1", True),
    ("/api/v1/graph?entity_id={a1}&depth=3&include_metadata=true", True),
    ("/api/v1/graph/analyze?organization_id={own_org}", True),
    ("/api/v1/graph/analyze?organization_id={foreign_org}", False),
    ("/api/v1/graph/path?from={a1}&to={a2}", True),
    ("/api/v1/graph/map?organization_id={own_org}", True),
    # The map falls back to the caller's own tenant when the requested
    # organization is not theirs, so only the no-leak assertion applies.
    ("/api/v1/graph/map?organization_id={foreign_org}", None),
]


class TestGraphTenantIsolation:
    """No graph route may return another tenant's entities or edges."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("route", "expects_own"), GRAPH_ROUTES)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_graph_route_never_leaks_other_tenant(
        self, mock_get_user, route, expects_own, async_client, generate_token, app
    ):
        """Foreign-tenant names never appear; own-tenant data still does."""
        mock_get_user.return_value = _user()
        token = generate_token(tenant_id=OWN_TENANT, scopes=SCOPES)
        async with app.app_context():
            seeded = _two_tenant_graph(current_app.db)

        resp = await async_client.get(
            route.format(**seeded), headers={"Authorization": f"Bearer {token}"}
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 200, body[:200]
        assert seeded["foreign"] not in body
        if expects_own is not None:
            assert (seeded["own"] in body) is expects_own, body[:300]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "route", ["/api/v1/graph", "/api/v1/graph/analyze"], ids=["graph", "analyze"]
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_graph_unfiltered_listing_is_tenant_scoped(
        self, mock_get_user, route, async_client, generate_token, app
    ):
        """With no filters at all, only the caller's tenant is read."""
        mock_get_user.return_value = _user()
        token = generate_token(tenant_id=OWN_TENANT, scopes=SCOPES)
        async with app.app_context():
            seeded = _two_tenant_graph(current_app.db)

        resp = await async_client.get(
            route, headers={"Authorization": f"Bearer {token}"}
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 200, body[:200]
        assert seeded["foreign"] not in body
        data = await resp.get_json()
        if route.endswith("analyze"):
            assert data["basic_stats"]["total_entities"] >= 3
        else:
            ids = {n["id"] for n in data["nodes"]}
            edge_ends = {e["from"] for e in data["edges"]} | {
                e["to"] for e in data["edges"]
            }
            assert seeded["b1"] not in ids | edge_ends

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_graph_path_does_not_route_through_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """The only a1->a2 route crosses another tenant, so no path exists."""
        mock_get_user.return_value = _user()
        token = generate_token(tenant_id=OWN_TENANT, scopes=SCOPES)
        async with app.app_context():
            seeded = _two_tenant_graph(current_app.db)

        resp = await async_client.get(
            f"/api/v1/graph/path?from={seeded['a1']}&to={seeded['a2']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert (await resp.get_json())["path_exists"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "route",
        [
            "/api/v1/graph",
            "/api/v1/graph/analyze",
            "/api/v1/graph/path?from=1&to=2",
        ],
        ids=["graph", "analyze", "path"],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_graph_missing_tenant_claim_is_rejected(
        self, mock_get_user, route, async_client, generate_token, app
    ):
        """A token without a usable tenant never falls back to unscoped data."""
        mock_get_user.return_value = _user()
        token = generate_token(tenant_id=0, scopes=SCOPES)
        async with app.app_context():
            seeded = _two_tenant_graph(current_app.db)

        resp = await async_client.get(
            route, headers={"Authorization": f"Bearer {token}"}
        )
        body = (await resp.get_data()).decode()
        assert seeded["own"] not in body
        assert seeded["foreign"] not in body
        assert resp.status_code == 403


class TestGraphDepthClamp:
    """Traversal depth is bounded; -1 means the maximum, not unbounded."""

    @pytest.mark.parametrize(
        ("requested", "expected"),
        [
            (None, (4, True)),
            (-1, (4, True)),
            (-50, (4, True)),
            (0, (0, False)),
            (2, (2, False)),
            (4, (4, False)),
            (5, (4, True)),
            (9999, (4, True)),
        ],
    )
    def test_graph_resolve_depth(self, monkeypatch, requested, expected):
        """Requested depth maps onto the enforced maximum."""
        monkeypatch.setattr(graph_routes, "GRAPH_MAX_DEPTH", 4)
        assert graph_routes._resolve_depth(requested) == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("depth", ["-1", "9999", "abc"])
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_graph_depth_is_clamped_on_route(
        self, mock_get_user, depth, async_client, generate_token, app, monkeypatch
    ):
        """A 6-entity chain is cut at the maximum depth from the centre."""
        monkeypatch.setattr(graph_routes, "GRAPH_MAX_DEPTH", 2)
        mock_get_user.return_value = _user()
        token = generate_token(tenant_id=OWN_TENANT, scopes=SCOPES)
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, OWN_TENANT, f"chain-{uuid4().hex[:8]}")
            chain = [_entity(db, org_id, f"chain-{i}") for i in range(6)]
            for left, right in zip(chain, chain[1:], strict=False):
                _edge(db, left, right)

        resp = await async_client.get(
            f"/api/v1/graph?entity_id={chain[0]}&depth={depth}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        data = await resp.get_json()
        assert {n["id"] for n in data["nodes"]} == set(chain[:3])
        assert data["stats"]["depth"] == 2
        # "abc" falls back to the default depth (2), which needs no clamping.
        assert data["stats"]["depth_clamped"] is (depth != "abc")


class TestGraphPayloadCaps:
    """Node and edge counts are capped and the response says so."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("max_nodes", "max_edges", "nodes", "edges", "truncated"),
        [
            (3, 100, 3, None, True),
            (100, 2, 6, 2, True),
            (100, 100, 6, 5, False),
        ],
        ids=["node-cap", "edge-cap", "under-cap"],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_graph_caps_and_truncated_flag(
        self,
        mock_get_user,
        max_nodes,
        max_edges,
        nodes,
        edges,
        truncated,
        async_client,
        generate_token,
        app,
        monkeypatch,
    ):
        """A 6-node/5-edge star is cut at the caps with truncated=true."""
        monkeypatch.setattr(graph_routes, "GRAPH_MAX_NODES", max_nodes)
        monkeypatch.setattr(graph_routes, "GRAPH_MAX_EDGES", max_edges)
        mock_get_user.return_value = _user()
        token = generate_token(tenant_id=OWN_TENANT, scopes=SCOPES)
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, OWN_TENANT, f"star-{uuid4().hex[:8]}")
            hub = _entity(db, org_id, "star-hub")
            for i in range(5):
                _edge(db, hub, _entity(db, org_id, f"star-leaf-{i}"))

        headers = {"Authorization": f"Bearer {token}"}
        for url in (
            f"/api/v1/graph?organization_id={org_id}",
            f"/api/v1/graph?entity_id={hub}&depth=1",
        ):
            resp = await async_client.get(url, headers=headers)
            assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
            data = await resp.get_json()
            assert len(data["nodes"]) <= max_nodes
            assert len(data["edges"]) <= max_edges
            if "organization_id" in url or not truncated:
                # The centred walk stops early once a cap cuts a level short.
                assert len(data["nodes"]) == nodes, url
                if edges is not None:
                    assert len(data["edges"]) == edges, url
            assert data["stats"]["truncated"] is truncated, url
            # Existing response shape is preserved (additive fields only).
            assert {"nodes", "edges", "stats"} <= set(data)
            assert {"entity_count", "dependency_count"} <= set(data["stats"])

        resp = await async_client.get(
            f"/api/v1/graph/analyze?organization_id={org_id}", headers=headers
        )
        stats = (await resp.get_json())["basic_stats"]
        assert stats["total_entities"] == nodes
        assert stats["truncated"] is truncated

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_graph_map_limit_is_clamped(
        self, mock_get_user, async_client, generate_token, app, monkeypatch
    ):
        """An oversized map limit is reduced to the node cap."""
        monkeypatch.setattr(graph_routes, "GRAPH_MAX_NODES", 2)
        mock_get_user.return_value = _user()
        token = generate_token(tenant_id=OWN_TENANT, scopes=SCOPES)
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, OWN_TENANT, f"map-{uuid4().hex[:8]}")
            for i in range(4):
                _entity(db, org_id, f"map-entity-{i}")

        resp = await async_client.get(
            f"/api/v1/graph/map?organization_id={org_id}&limit=100000000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = await resp.get_json()
        assert len(data["nodes"]) == 2
        assert data["stats"]["truncated"] is True


class TestGraphQueryCountBounded:
    """The subgraph walk issues a fixed number of queries per level."""

    @staticmethod
    def _count_subgraph_queries(db, center_id: int, depth: int) -> tuple[int, int]:
        """Run the walk and return (statements executed, entities found)."""
        engine = getattr(db.engine, "sync_engine", db.engine)
        statements: list[str] = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        org_ids = graph_routes._tenant_org_ids(db, OWN_TENANT)
        center = db.entities[center_id]
        event.listen(engine, "before_cursor_execute", _record)
        try:
            entities, _ = graph_routes._get_entity_subgraph(db, center, depth, org_ids)
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        return len(statements), len(entities)

    @staticmethod
    def _fan_out(db, width: int) -> int:
        """Seed centre -> ``width`` children -> one grandchild each."""
        org_id = _org(db, OWN_TENANT, f"fan-{width}-{uuid4().hex[:8]}")
        center = _entity(db, org_id, f"fan-{width}-center")
        for i in range(width):
            child = _entity(db, org_id, f"fan-{width}-child-{i}")
            _edge(db, center, child)
            _edge(db, _entity(db, org_id, f"fan-{width}-grandchild-{i}"), child)
        return center

    @pytest.mark.asyncio
    async def test_graph_subgraph_query_count_independent_of_entity_count(self, app):
        """10x more entities per level must not issue more queries."""
        depth = 2
        async with app.app_context():
            db = current_app.db
            small = self._fan_out(db, 3)
            large = self._fan_out(db, 30)
            small_queries, small_found = self._count_subgraph_queries(db, small, depth)
            large_queries, large_found = self._count_subgraph_queries(db, large, depth)

        assert small_found == 1 + 3 * 2
        assert large_found == 1 + 30 * 2
        assert small_queries > 0
        assert large_queries == small_queries
        # Two statements per level: the level's edges and its entities.
        assert large_queries <= 2 * depth


SEARCH_ROUTES = [
    ("/api/v1/search?q=x", "search_all", True),
    ("/api/v1/search/entities?q=x", "search_entities", True),
    ("/api/v1/search/organizations?q=x", "search_organizations", True),
    ("/api/v1/search/issues?q=x", "search_issues", True),
    ("/api/v1/search/saved?", "list_saved_searches", False),
    ("/api/v1/search/saved/1/execute?", "execute_saved_search", True),
    ("/api/v1/search/analytics/popular?", "get_popular_searches", False),
    ("/api/v1/search/suggest?q=xy", "get_search_suggestions", False),
]


class TestSearchLimitClamp:
    """Search page size and offset are clamped before reaching the service."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("route", "method", "has_offset"), SEARCH_ROUTES)
    @pytest.mark.parametrize(
        ("limit", "offset", "max_limit", "want_offset"),
        [
            ("100000000", "-5", 200, 0),
            ("-1", "999999999999", None, 1_000_000),
            ("0", "abc", None, 0),
            ("abc", "7", None, 7),
            ("200", "0", 200, 0),
            ("25", "3", 25, 3),
        ],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_search_limit_and_offset_clamped(
        self,
        mock_get_user,
        limit,
        offset,
        max_limit,
        want_offset,
        route,
        method,
        has_offset,
        async_client,
        generate_token,
        app,
    ):
        """No search handler forwards an unbounded limit or a bad offset."""
        mock_get_user.return_value = _user()
        token = generate_token(tenant_id=OWN_TENANT, scopes=SCOPES)
        service = MagicMock()
        for name in {m for _, m, _ in SEARCH_ROUTES}:
            getattr(service, name).return_value = {"results": [], "total": 0}

        with patch("apps.api.api.v1.search.get_search_service", return_value=service):
            resp = await async_client.get(
                f"{route}&limit={limit}&offset={offset}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]

        calls = [c for c in service.method_calls if "limit" in c.kwargs]
        assert len(calls) == 1, service.method_calls
        kwargs = calls[0].kwargs
        assert 1 <= kwargs["limit"] <= 200
        if max_limit is not None:
            assert kwargs["limit"] == max_limit
        else:
            # Unusable values fall back to the endpoint default (10 or 50).
            assert kwargs["limit"] in (10, 50)
        if has_offset:
            assert kwargs["offset"] == want_offset
