"""Hardening tests for graph-adjacent routes: scoping, scope and limits.

Covers ``POST /search/graph``, the dependency listing/per-resource/bulk
routes and the network topology graph with two tenants, plus the per-caller
rate and in-flight limits on graph traversal endpoints.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app

OWN_TENANT = 1
SCOPES = ["infrastructure:read", "infrastructure:write"]


def _user() -> MagicMock:
    """Return a stand-in authenticated user bound to the caller's tenant."""
    return MagicMock(id=1, is_superuser=False, tenant_id=OWN_TENANT)


def _tenant(db) -> int:
    """Create and return a tenant distinct from the caller's tenant."""
    tid = db.tenants.insert(
        name="Sibling Other Tenant", slug=f"sib-{uuid4().hex[:8]}", is_active=True
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


def _edge(
    db,
    source_id: int,
    target_id: int,
    tenant_id: int | None,
    target_type: str = "entity",
    dependency_type: str = "depends_on",
) -> int:
    """Create a dependency from an entity, recorded for ``tenant_id``."""
    dep_id = db.dependencies.insert(
        tenant_id=tenant_id,
        source_type="entity",
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        dependency_type=dependency_type,
    )
    db.commit()
    return dep_id


def _seed(db) -> dict:
    """Seed two tenants whose graphs are bridged by cross-tenant edges.

    own: a1 -> a2. foreign: b1 -> b2. Bridges a1 -> b1 exist both as a
    legacy edge with no recorded tenant and as an edge recorded for the
    foreign tenant; neither may ever be surfaced to the own tenant.
    """
    tag = uuid4().hex[:10]
    own, foreign = f"sibown-{tag}", f"sibforeign-{tag}"
    foreign_tenant = _tenant(db)
    own_org = _org(db, OWN_TENANT, f"{own}-org")
    foreign_org = _org(db, foreign_tenant, f"{foreign}-org")
    a1 = _entity(db, own_org, f"{own}-a1")
    a2 = _entity(db, own_org, f"{own}-a2")
    b1 = _entity(db, foreign_org, f"{foreign}-b1")
    b2 = _entity(db, foreign_org, f"{foreign}-b2")
    return {
        "own": own,
        "foreign": foreign,
        "foreign_tenant": foreign_tenant,
        "own_org": own_org,
        "foreign_org": foreign_org,
        "a1": a1,
        "a2": a2,
        "b1": b1,
        "b2": b2,
        "own_dep": _edge(db, a1, a2, OWN_TENANT),
        "foreign_dep": _edge(db, b1, b2, foreign_tenant),
        "bridge_legacy": _edge(db, a1, b1, None),
        "bridge_foreign": _edge(db, b1, a2, foreign_tenant),
    }


async def _seeded(app) -> dict:
    """Seed the two-tenant fixture inside an app context."""
    async with app.app_context():
        return _seed(current_app.db)


def _auth(generate_token, tenant_id: int = OWN_TENANT, scopes=None) -> dict:
    """Build an Authorization header for the given tenant and scopes."""
    token = generate_token(
        tenant_id=tenant_id, scopes=SCOPES if scopes is None else scopes
    )
    return {"Authorization": f"Bearer {token}"}


class TestSearchGraphIsolation:
    """POST /api/v1/search/graph is scoped, scope-gated and bounded."""

    URL = "/api/v1/search/graph"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_search_graph_foreign_start_entity_is_not_found(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Another tenant's entity cannot be used as the traversal start."""
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        resp = await async_client.post(
            self.URL,
            json={"start_entity_id": seeded["b1"], "max_depth": 3},
            headers=_auth(generate_token),
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 404, body[:200]
        assert seeded["foreign"] not in body

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_search_graph_never_walks_into_other_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Starting from an own entity never returns foreign nodes or edges."""
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        resp = await async_client.post(
            self.URL,
            json={"start_entity_id": seeded["a1"], "max_depth": 5},
            headers=_auth(generate_token),
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 200, body[:200]
        assert seeded["foreign"] not in body
        data = await resp.get_json()
        assert {n["id"] for n in data["nodes"]} == {seeded["a1"], seeded["a2"]}
        ends = {e["source"] for e in data["edges"]} | {
            e["target"] for e in data["edges"]
        }
        assert not ends & {seeded["b1"], seeded["b2"]}
        # Response shape is preserved.
        assert {
            "start_entity_id",
            "max_depth",
            "nodes",
            "edges",
            "node_count",
            "edge_count",
        } <= set(data)
        assert {"id", "name", "entity_type", "sub_type", "depth"} <= set(
            data["nodes"][0]
        )
        assert {"source", "target", "type", "metadata"} <= set(data["edges"][0])

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tenant_id", "scopes", "status"),
        [
            (OWN_TENANT, [], 403),
            (OWN_TENANT, ["ai:read"], 403),
            (0, ["infrastructure:read"], 403),
            (OWN_TENANT, ["infrastructure:read"], 200),
        ],
        ids=["no-scope", "wrong-scope", "no-tenant", "allowed"],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_search_graph_requires_scope_and_tenant(
        self,
        mock_get_user,
        tenant_id,
        scopes,
        status,
        async_client,
        generate_token,
        app,
    ):
        """The read scope and a tenant claim are both required."""
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        resp = await async_client.post(
            self.URL,
            json={"start_entity_id": seeded["a1"]},
            headers=_auth(generate_token, tenant_id, scopes),
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == status, body[:200]
        if status != 200:
            assert seeded["own"] not in body

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_search_graph_unauthenticated(self, mock_get_user, async_client):
        """No token means no traversal."""
        mock_get_user.return_value = None
        resp = await async_client.post(self.URL, json={"start_entity_id": 1})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("requested", [100000, -4, "abc", None, 2])
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_search_graph_depth_clamped(
        self, mock_get_user, requested, async_client, generate_token, app, monkeypatch
    ):
        """An oversized depth is cut to the enforced maximum on a chain."""
        monkeypatch.setattr("apps.api.api.v1.search._GRAPH_SEARCH_MAX_DEPTH", 2)
        mock_get_user.return_value = _user()
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, OWN_TENANT, f"sgchain-{uuid4().hex[:8]}")
            chain = [_entity(db, org_id, f"sgchain-{i}") for i in range(6)]
            for left, right in zip(chain, chain[1:], strict=False):
                _edge(db, left, right, OWN_TENANT)
        payload = {"start_entity_id": chain[0]}
        if requested is not None:
            payload["max_depth"] = requested

        resp = await async_client.post(
            self.URL, json=payload, headers=_auth(generate_token)
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        data = await resp.get_json()
        expected = 1 if requested == -4 else 2
        assert data["max_depth"] == expected
        assert {n["id"] for n in data["nodes"]} == set(chain[: expected + 1])
        assert max(n["depth"] for n in data["nodes"]) == expected

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_search_graph_node_cap_and_bad_input(
        self, mock_get_user, async_client, generate_token, app, monkeypatch
    ):
        """The node cap applies and malformed bodies are rejected with 400."""
        from apps.api.modules.infrastructure.routes import graph as graph_routes

        monkeypatch.setattr(graph_routes, "GRAPH_MAX_NODES", 3)
        mock_get_user.return_value = _user()
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, OWN_TENANT, f"sgstar-{uuid4().hex[:8]}")
            hub = _entity(db, org_id, "sgstar-hub")
            for i in range(6):
                _edge(db, hub, _entity(db, org_id, f"sgstar-{i}"), OWN_TENANT)
        headers = _auth(generate_token)

        resp = await async_client.post(
            self.URL, json={"start_entity_id": hub}, headers=headers
        )
        data = await resp.get_json()
        assert resp.status_code == 200
        assert len(data["nodes"]) == 3
        assert data["truncated"] is True

        for bad in (
            {},
            {"start_entity_id": "abc"},
            {"start_entity_id": -1},
            {"start_entity_id": hub, "dependency_types": "depends_on"},
            {"start_entity_id": hub, "dependency_types": [1, 2]},
        ):
            resp = await async_client.post(self.URL, json=bad, headers=headers)
            assert resp.status_code == 400, bad

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_search_graph_non_entity_edges_follow_edge_tenant(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Edges to non-entity resources are kept only for the caller's tenant."""
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        async with app.app_context():
            db = current_app.db
            _edge(db, seeded["a1"], 111111, OWN_TENANT, "identity", "managed_by")
            _edge(db, seeded["a1"], 222222, seeded["foreign_tenant"], "identity")
            _edge(db, seeded["a1"], 333333, None, "identity")

        resp = await async_client.post(
            self.URL,
            json={"start_entity_id": seeded["a1"]},
            headers=_auth(generate_token),
        )
        assert resp.status_code == 200
        data = await resp.get_json()
        targets = {e["target"] for e in data["edges"]}
        assert 111111 in targets
        assert not targets & {222222, 333333}
        # Non-entity targets are edges only; they never become nodes.
        assert {n["id"] for n in data["nodes"]} == {seeded["a1"], seeded["a2"]}

        resp = await async_client.post(
            self.URL,
            json={"start_entity_id": seeded["a1"], "dependency_types": ["managed_by"]},
            headers=_auth(generate_token),
        )
        data = await resp.get_json()
        assert {e["type"] for e in data["edges"]} == {"managed_by"}
        assert {n["id"] for n in data["nodes"]} == {seeded["a1"]}


class TestGraphWalkEdgeTypes:
    """The centred /graph walk only follows entity-to-entity edges."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_graph_non_entity_edge_is_not_resolved_as_entity(
        self, mock_get_user, async_client, generate_token, app
    ):
        """An entity->identity edge must not pull in the entity sharing that id."""
        mock_get_user.return_value = _user()
        async with app.app_context():
            db = current_app.db
            org_id = _org(db, OWN_TENANT, f"etype-{uuid4().hex[:8]}")
            center = _entity(db, org_id, "etype-center")
            bystander = _entity(db, org_id, "etype-bystander")
            # Target is identity #<bystander id>, not the bystander entity.
            _edge(db, center, bystander, OWN_TENANT, "identity")

        resp = await async_client.get(
            f"/api/v1/graph?entity_id={center}&depth=2", headers=_auth(generate_token)
        )
        assert resp.status_code == 200
        data = await resp.get_json()
        assert {n["id"] for n in data["nodes"]} == {center}
        assert data["edges"] == []


class TestDependencyRoutesIsolation:
    """Dependency listing, per-resource and bulk routes stay in-tenant."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query",
        ["per_page=1000", "source_type=entity&per_page=1000", "target_id={b2}", ""],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_dependencies_list_is_tenant_scoped(
        self, mock_get_user, query, async_client, generate_token, app
    ):
        """No listing or filter combination returns another tenant's rows."""
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        resp = await async_client.get(
            f"/api/v1/dependencies?{query.format(**seeded)}",
            headers=_auth(generate_token),
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        data = await resp.get_json()
        ids = {item["id"] for item in data["items"]}
        assert not ids & {seeded["foreign_dep"], seeded["bridge_foreign"]}
        assert all(item["tenant_id"] == OWN_TENANT for item in data["items"])
        if query == "target_id={b2}":
            assert data["total"] == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_dependencies_list_returns_own_rows(
        self, mock_get_user, async_client, generate_token, app
    ):
        """The caller's own dependency is still listed (filter by its source)."""
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        resp = await async_client.get(
            f"/api/v1/dependencies?source_id={seeded['a1']}&target_id={seeded['a2']}",
            headers=_auth(generate_token),
        )
        data = await resp.get_json()
        assert [item["id"] for item in data["items"]] == [seeded["own_dep"]]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("params", "page", "per_page"),
        [
            ("page=-5&per_page=-1", 1, 50),
            ("page=abc&per_page=abc", 1, 50),
            ("page=0&per_page=999999", 1, 1000),
        ],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_dependencies_list_pagination_bounds(
        self, mock_get_user, params, page, per_page, async_client, generate_token, app
    ):
        """Page and per_page are clamped instead of producing bad offsets."""
        mock_get_user.return_value = _user()
        resp = await async_client.get(
            f"/api/v1/dependencies?{params}", headers=_auth(generate_token)
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        data = await resp.get_json()
        assert (data["page"], data["per_page"]) == (page, per_page)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/dependencies",
            "/api/v1/dependencies/resource/entity/{a1}",
        ],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_dependencies_missing_tenant_claim_is_rejected(
        self, mock_get_user, path, async_client, generate_token, app
    ):
        """A token without a tenant gets 403, never an unscoped listing."""
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        resp = await async_client.get(
            path.format(**seeded), headers=_auth(generate_token, tenant_id=0)
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_resource_dependencies_are_tenant_scoped(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Foreign resources are not found; own results omit foreign edges."""
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        headers = _auth(generate_token)

        resp = await async_client.get(
            f"/api/v1/dependencies/resource/entity/{seeded['b1']}", headers=headers
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == 404, body[:200]
        assert seeded["foreign"] not in body

        resp = await async_client.get(
            f"/api/v1/dependencies/resource/entity/{seeded['a2']}", headers=headers
        )
        assert resp.status_code == 200
        data = await resp.get_json()
        assert [d["id"] for d in data["depended_by"]] == [seeded["own_dep"]]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_bulk_delete_cannot_remove_other_tenant_rows(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Bulk delete only removes the caller's own dependencies."""
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        resp = await async_client.delete(
            "/api/v1/dependencies/bulk",
            json={"ids": [seeded["own_dep"], seeded["foreign_dep"]]},
            headers=_auth(generate_token),
        )
        assert resp.status_code == 200, (await resp.get_data()).decode()[:200]
        assert (await resp.get_json())["deleted"] == 1
        async with app.app_context():
            db = current_app.db
            remaining = db(
                db.dependencies.id.belongs([seeded["own_dep"], seeded["foreign_dep"]])
            ).select()
            assert [row.id for row in remaining] == [seeded["foreign_dep"]]


class TestTopologyGraphIsolation:
    """GET /api/v1/networking/topology/graph checks organization ownership."""

    URL = "/api/v1/networking/topology/graph"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("org_key", "tenant_id", "status"),
        [
            ("foreign_org", OWN_TENANT, 404),
            ("own_org", OWN_TENANT, 200),
            ("own_org", 0, 403),
            ("missing", OWN_TENANT, 404),
        ],
        ids=["foreign-org", "own-org", "no-tenant", "unknown-org"],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_topology_graph_org_must_belong_to_tenant(
        self,
        mock_get_user,
        org_key,
        tenant_id,
        status,
        async_client,
        generate_token,
        app,
    ):
        """Another tenant's organization id is reported as not found."""
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        seeded["missing"] = 2_000_000_000
        async with app.app_context():
            db = current_app.db
            now = datetime.now(UTC)
            db.networking_resources.insert(
                name=f"{seeded['foreign']}-net",
                network_type="vpc",
                organization_id=seeded["foreign_org"],
                created_at=now,
                updated_at=now,
            )
            db.commit()

        resp = await async_client.get(
            f"{self.URL}?organization_id={seeded[org_key]}",
            headers=_auth(generate_token, tenant_id),
        )
        body = (await resp.get_data()).decode()
        assert resp.status_code == status, body[:200]
        assert seeded["foreign"] not in body


class TestGraphCallerLimits:
    """Graph traversal endpoints are limited per tenant:subject."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_graph_rate_limit_per_subject(
        self, mock_get_user, async_client, generate_token, app, monkeypatch
    ):
        """Requests past the per-minute budget get 429 with Retry-After."""
        monkeypatch.setitem(app.config, "RATELIMIT_ENABLED", True)
        monkeypatch.setenv("ELDER_GRAPH_RATE_PER_MINUTE", "3")
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        headers = _auth(generate_token)
        calls = [
            ("get", f"/api/v1/graph?organization_id={seeded['own_org']}", None),
            ("get", f"/api/v1/graph/analyze?organization_id={seeded['own_org']}", None),
            ("post", "/api/v1/search/graph", {"start_entity_id": seeded["a1"]}),
            ("get", f"/api/v1/graph/path?from={seeded['a1']}&to={seeded['a2']}", None),
            ("get", f"/api/v1/graph/map?organization_id={seeded['own_org']}", None),
        ]
        statuses = []
        for method, url, payload in calls:
            if method == "post":
                resp = await async_client.post(url, json=payload, headers=headers)
            else:
                resp = await async_client.get(url, headers=headers)
            statuses.append(resp.status_code)
        # One shared budget across every graph endpoint.
        assert statuses == [200, 200, 200, 429, 429]
        assert resp.headers.get("Retry-After")
        assert (await resp.get_json())["error"] == "RATE_LIMITED"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_graph_inflight_limit_per_subject(
        self, mock_get_user, async_client, generate_token, app, monkeypatch
    ):
        """A caller already at the in-flight cap is refused, then admitted."""
        from apps.api.modules.infrastructure.routes import graph as graph_routes

        monkeypatch.setenv("ELDER_GRAPH_MAX_CONCURRENT_PER_USER", "2")
        mock_get_user.return_value = _user()
        seeded = await _seeded(app)
        headers = _auth(generate_token)
        url = f"/api/v1/graph?organization_id={seeded['own_org']}"
        limiter = graph_routes.graph_limited.inflight
        key = f"{OWN_TENANT}:test-user-123"

        assert limiter.try_acquire(key, 2)
        assert limiter.try_acquire(key, 2)
        try:
            resp = await async_client.get(url, headers=headers)
            assert resp.status_code == 429
            assert (await resp.get_json())["error"] == "TOO_MANY_CONCURRENT"
            # Another caller's budget is unaffected.
            assert limiter.in_flight(f"{seeded['foreign_tenant']}:test-user-123") == 0
        finally:
            limiter.release(key)
            limiter.release(key)

        resp = await async_client.get(url, headers=headers)
        assert resp.status_code == 200
        # The slot is returned after the request completes.
        assert limiter.in_flight(key) == 0
