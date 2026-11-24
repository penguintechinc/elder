"""Two-tenant isolation tests for search, entities and dependencies.

The universal search service (``GET /search``, ``/search/entities``,
``/search/organizations``, ``/search/issues``, ``/search/analytics/popular``,
``/search/suggest``, ``/search/saved/<id>/execute``), the entity list
(``GET /entities``) and the dependency routes did not apply the caller's
tenant to their queries, so an authenticated caller could read or link rows
owned by other tenants. Every route touched by the fix is covered here with a
caller in tenant 1 and a second, foreign tenant: own rows stay visible,
foreign rows never appear, foreign ids map to 404, a token without a tenant
gets 403, and the read scopes are enforced.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from quart import current_app

OWN_TENANT = 1
ALL_SCOPES = [
    "infrastructure:read",
    "infrastructure:write",
    "issues:read",
]


def _tag() -> str:
    """Return a short unique marker so assertions ignore unrelated rows."""
    return uuid4().hex[:10]


def _tenant(db) -> int:
    """Create and return a tenant distinct from tenant 1."""
    tid = db.tenants.insert(
        name="Other Tenant", slug=f"other-{uuid4().hex[:8]}", is_active=True
    )
    db.commit()
    return tid


def _org(db, tenant_id: int, name: str) -> int:
    """Insert an organization owned by ``tenant_id``."""
    now = datetime.now(UTC)
    org_id = db.organizations.insert(
        name=name, description=name, tenant_id=tenant_id, created_at=now, updated_at=now
    )
    db.commit()
    return org_id


def _entity(db, org_id: int, name: str) -> int:
    """Insert an entity under ``org_id``."""
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


def _issue(db, tenant_id: int, title: str) -> int:
    """Insert an issue owned by ``tenant_id``."""
    now = datetime.now(UTC)
    issue_id = db.issues.insert(
        title=title,
        status="OPEN",
        priority="LOW",
        issue_type="OTHER",
        is_incident=0,
        resource_type="organization",
        resource_id=1,
        tenant_id=tenant_id,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return issue_id


def _identity(db, tenant_id: int) -> int:
    """Insert a human identity in ``tenant_id``."""
    username = f"u-{uuid4().hex[:10]}"
    identity_id = db.identities.insert(
        identity_type="human",
        username=username,
        email=f"{username}@example.com",
        tenant_id=tenant_id,
        auth_provider="local",
        is_active=True,
        is_superuser=False,
        mfa_enabled=False,
        must_change_password=False,
        portal_role="observer",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.commit()
    return identity_id


def _saved_search(db, identity_id: int, name: str, query: str) -> int:
    """Insert a saved search owned by ``identity_id``."""
    sid = db.saved_searches.insert(
        identity_id=identity_id,
        name=name,
        query=query,
        filters=None,
        created_at=datetime.now(UTC),
    )
    db.commit()
    return sid


def _dependency(db, tenant_id: int, source_id: int, target_id: int) -> int:
    """Insert an entity->entity dependency owned by ``tenant_id``."""
    dep_id = db.dependencies.insert(
        tenant_id=tenant_id,
        source_type="entity",
        source_id=source_id,
        target_type="entity",
        target_id=target_id,
        dependency_type="depends_on",
    )
    db.commit()
    return dep_id


@pytest_asyncio.fixture
async def world(app):
    """Seed matching rows in tenant 1 and in a foreign tenant.

    Both tenants get an org, two entities, an issue, an identity with a
    saved search and a dependency, all named with a shared marker so a
    single search term hits both tenants' rows.
    """
    tag = _tag()
    async with app.app_context():
        db = current_app.db
        foreign = _tenant(db)
        w = {"tag": tag, "foreign_tenant": foreign}
        for side, tid in (("own", OWN_TENANT), ("foreign", foreign)):
            org = _org(db, tid, f"{tag}-{side}-org")
            e1 = _entity(db, org, f"{tag}-{side}-entity-a")
            e2 = _entity(db, org, f"{tag}-{side}-entity-b")
            ident = _identity(db, tid)
            w[side] = {
                "org": org,
                "e1": e1,
                "e2": e2,
                "issue": _issue(db, tid, f"{tag}-{side}-issue"),
                "identity": ident,
                "saved": _saved_search(
                    db, ident, f"{tag}-{side}-saved", f"{tag}-{side}-term"
                ),
                "dep": _dependency(db, tid, e1, e2),
            }
    return w


def _hdr(generate_token, tenant_id=OWN_TENANT, scopes=None) -> dict:
    """Bearer header for a token in ``tenant_id`` with ``scopes``."""
    token = generate_token(
        tenant_id=tenant_id, scopes=ALL_SCOPES if scopes is None else scopes
    )
    return {"Authorization": f"Bearer {token}"}


async def _body(resp) -> str:
    """Return the response body as text."""
    return (await resp.get_data()).decode()


def _user(identity_id: int = 1) -> MagicMock:
    """Non-superuser current user stub."""
    return MagicMock(id=identity_id, is_superuser=False)


SEARCH_READ_ROUTES = [
    "/api/v1/search?q={tag}",
    "/api/v1/search/entities?q={tag}",
    "/api/v1/search/organizations?q={tag}",
    "/api/v1/search/issues?q={tag}",
    "/api/v1/search/suggest?q={tag}",
    "/api/v1/search/analytics/popular?limit=500",
]


class TestSearchTenantIsolation:
    """Search results, suggestions and analytics never cross tenants."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", SEARCH_READ_ROUTES)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_only_own_tenant_rows_returned(
        self, mock_user, route, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        tag = world["tag"]
        resp = await async_client.get(
            route.format(tag=tag), headers=_hdr(generate_token)
        )
        body = await _body(resp)
        assert resp.status_code == 200, body[:300]
        assert f"{tag}-foreign" not in body, body[:500]
        assert f"{tag}-own" in body, body[:500]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_search_all_counts_only_own_tenant(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.get(
            f"/api/v1/search?q={world['tag']}", headers=_hdr(generate_token)
        )
        data = await resp.get_json()
        assert data["entities_count"] == 2
        assert data["organizations_count"] == 1
        assert data["issues_count"] == 1
        assert data["total_count"] == 4

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_entity_search_ignores_foreign_organization_filter(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.get(
            f"/api/v1/search/entities?organization_id={world['foreign']['org']}",
            headers=_hdr(generate_token),
        )
        data = await resp.get_json()
        assert resp.status_code == 200
        assert data["total_count"] == 0 and data["entities"] == []

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_popular_searches_do_not_reveal_other_tenants_terms(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.get(
            "/api/v1/search/analytics/popular?limit=1000",
            headers=_hdr(generate_token),
        )
        data = await resp.get_json()
        assert resp.status_code == 200
        queries = {p["query"] for p in data["popular_searches"]}
        names = {p["name"] for p in data["popular_searches"]}
        assert f"{world['tag']}-foreign-term" not in queries
        assert f"{world['tag']}-foreign-saved" not in names
        assert f"{world['tag']}-own-term" in queries

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stype", ["entity", "organization", "issue", None])
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_suggestions_do_not_reveal_other_tenants_names(
        self, mock_user, stype, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        qs = f"q={world['tag']}" + (f"&type={stype}" if stype else "") + "&limit=50"
        resp = await async_client.get(
            f"/api/v1/search/suggest?{qs}", headers=_hdr(generate_token)
        )
        data = await resp.get_json()
        assert resp.status_code == 200
        texts = [s["text"] for s in data["suggestions"]]
        assert texts, "own-tenant suggestions missing"
        assert all("-foreign-" not in t for t in texts), texts

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_execute_saved_search_is_tenant_scoped(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user(world["own"]["identity"])
        async with async_client.app.app_context():
            db = current_app.db
            sid = _saved_search(db, world["own"]["identity"], "x", world["tag"])
        resp = await async_client.get(
            f"/api/v1/search/saved/{sid}/execute", headers=_hdr(generate_token)
        )
        body = await _body(resp)
        assert resp.status_code == 200, body[:300]
        assert f"{world['tag']}-foreign" not in body
        assert f"{world['tag']}-own" in body

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", SEARCH_READ_ROUTES)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_token_without_tenant_is_forbidden(
        self, mock_user, route, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.get(
            route.format(tag=world["tag"]), headers=_hdr(generate_token, tenant_id=0)
        )
        assert resp.status_code == 403, (await _body(resp))[:300]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("route", SEARCH_READ_ROUTES)
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_read_scope_required(
        self, mock_user, route, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.get(
            route.format(tag=world["tag"]), headers=_hdr(generate_token, scopes=[])
        )
        assert resp.status_code == 403, (await _body(resp))[:300]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_issue_search_requires_issues_read(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.get(
            f"/api/v1/search/issues?q={world['tag']}",
            headers=_hdr(generate_token, scopes=["infrastructure:read"]),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_search_all_omits_issues_without_issues_read(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.get(
            f"/api/v1/search?q={world['tag']}",
            headers=_hdr(generate_token, scopes=["infrastructure:read"]),
        )
        data = await resp.get_json()
        assert resp.status_code == 200
        assert "issues" not in data
        assert data["entities_count"] == 2


class TestEntityRoutesTenantIsolation:
    """Every route in entities.py stays inside the caller's tenant."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_returns_only_own_tenant(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.get(
            f"/api/v1/entities?name={world['tag']}&per_page=1000",
            headers=_hdr(generate_token),
        )
        data = await resp.get_json()
        assert resp.status_code == 200
        ids = {i["id"] for i in data["items"]}
        assert ids == {world["own"]["e1"], world["own"]["e2"]}
        assert data["total"] == 2

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_with_foreign_org_filter_is_empty(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.get(
            f"/api/v1/entities?organization_id={world['foreign']['org']}",
            headers=_hdr(generate_token),
        )
        data = await resp.get_json()
        assert resp.status_code == 200 and data["total"] == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,suffix,payload",
        [
            ("get", "", None),
            ("patch", "", {"name": "pwned"}),
            ("put", "", {"name": "pwned"}),
            ("delete", "", None),
            ("get", "/dependencies", None),
            ("patch", "/attributes", {"k": "v"}),
        ],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_foreign_entity_is_404(
        self, mock_user, method, suffix, payload, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        fid = world["foreign"]["e1"]
        kwargs = {"headers": _hdr(generate_token)}
        if payload is not None:
            kwargs["json"] = payload
        resp = await getattr(async_client, method)(
            f"/api/v1/entities/{fid}{suffix}", **kwargs
        )
        assert resp.status_code == 404, (await _body(resp))[:300]
        async with async_client.app.app_context():
            row = current_app.db.entities[fid]  # tenant-scope-exempt: test
            assert row is not None and row.name != "pwned"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["organization_id", "parent_id"])
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_with_foreign_reference_is_404(
        self, mock_user, field, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        name = f"{world['tag']}-created-{field}"
        body = {
            "name": name,
            "entity_type": "compute",
            "organization_id": world["own"]["org"],
        }
        body[field] = (
            world["foreign"]["org"]
            if field == "organization_id"
            else world["foreign"]["e1"]
        )
        resp = await async_client.post(
            "/api/v1/entities", json=body, headers=_hdr(generate_token)
        )
        assert resp.status_code == 404, (await _body(resp))[:300]
        async with async_client.app.app_context():
            db = current_app.db
            assert db(db.entities.name == name).count() == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_in_own_org_succeeds(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        body = {
            "name": f"{world['tag']}-own-created",
            "entity_type": "compute",
            "organization_id": world["own"]["org"],
            "parent_id": world["own"]["e1"],
        }
        resp = await async_client.post(
            "/api/v1/entities", json=body, headers=_hdr(generate_token)
        )
        assert resp.status_code == 201, (await _body(resp))[:300]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["organization_id", "parent_id"])
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_cannot_move_into_foreign_tenant(
        self, mock_user, field, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        own = world["own"]["e1"]
        target = (
            world["foreign"]["org"]
            if field == "organization_id"
            else world["foreign"]["e1"]
        )
        resp = await async_client.patch(
            f"/api/v1/entities/{own}",
            json={field: target},
            headers=_hdr(generate_token),
        )
        assert resp.status_code == 404, (await _body(resp))[:300]
        async with async_client.app.app_context():
            row = current_app.db.entities[own]  # tenant-scope-exempt: test
            assert row.organization_id == world["own"]["org"]
            assert row.parent_id != world["foreign"]["e1"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", ""),
            ("get", "/{e}"),
            ("patch", "/{e}"),
            ("delete", "/{e}"),
            ("get", "/{e}/dependencies"),
            ("patch", "/{e}/attributes"),
            ("post", ""),
        ],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_token_without_tenant_is_forbidden(
        self, mock_user, method, path, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        kwargs = {"headers": _hdr(generate_token, tenant_id=0)}
        if method in ("patch", "post"):
            kwargs["json"] = {
                "name": "x",
                "entity_type": "compute",
                "organization_id": world["own"]["org"],
            }
        resp = await getattr(async_client, method)(
            "/api/v1/entities" + path.format(e=world["own"]["e1"]), **kwargs
        )
        assert resp.status_code == 403, (await _body(resp))[:300]


class TestDependencyRoutesTenantIsolation:
    """Dependencies can neither read nor link another tenant's resources."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_returns_only_own_tenant(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.get(
            "/api/v1/dependencies?per_page=1000", headers=_hdr(generate_token)
        )
        data = await resp.get_json()
        ids = {i["id"] for i in data["items"]}
        assert world["own"]["dep"] in ids
        assert world["foreign"]["dep"] not in ids

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "source,target",
        [("own", "foreign"), ("foreign", "own"), ("foreign", "foreign")],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_with_foreign_endpoint_is_404(
        self, mock_user, source, target, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        body = {
            "source_type": "entity",
            "source_id": world[source]["e2"],
            "target_type": "entity",
            "target_id": world[target]["e1"],
            "dependency_type": "linked",
        }
        resp = await async_client.post(
            "/api/v1/dependencies", json=body, headers=_hdr(generate_token)
        )
        assert resp.status_code == 404, (await _body(resp))[:300]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_within_own_tenant_succeeds(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        body = {
            "source_type": "entity",
            "source_id": world["own"]["e2"],
            "target_type": "organization",
            "target_id": world["own"]["org"],
            "dependency_type": "linked",
        }
        resp = await async_client.post(
            "/api/v1/dependencies", json=body, headers=_hdr(generate_token)
        )
        assert resp.status_code == 201, (await _body(resp))[:300]
        assert (await resp.get_json())["tenant_id"] == OWN_TENANT

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["source", "target"])
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_bulk_create_with_foreign_endpoint_is_404_and_atomic(
        self, mock_user, bad, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        good = {
            "source_type": "entity",
            "source_id": world["own"]["e2"],
            "target_type": "organization",
            "target_id": world["own"]["org"],
            "dependency_type": f"bulk-{world['tag']}",
        }
        foreign = dict(good)
        foreign[f"{bad}_type"] = "entity"
        foreign[f"{bad}_id"] = world["foreign"]["e1"]
        resp = await async_client.post(
            "/api/v1/dependencies/bulk",
            json={"dependencies": [good, foreign]},
            headers=_hdr(generate_token),
        )
        assert resp.status_code == 404, (await _body(resp))[:300]
        async with async_client.app.app_context():
            db = current_app.db
            n = db(db.dependencies.dependency_type == f"bulk-{world['tag']}").count()
            assert n == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_bulk_delete_ignores_foreign_ids(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.open(
            "/api/v1/dependencies/bulk",
            method="DELETE",
            json={"ids": [world["foreign"]["dep"]]},
            headers=_hdr(generate_token),
        )
        assert resp.status_code == 200
        assert (await resp.get_json())["deleted"] == 0
        async with async_client.app.app_context():
            db = current_app.db
            assert db(db.dependencies.id == world["foreign"]["dep"]).count() == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["source_id", "target_id"])
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_cannot_repoint_to_foreign_resource(
        self, mock_user, field, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        dep = world["own"]["dep"]
        resp = await async_client.patch(
            f"/api/v1/dependencies/{dep}",
            json={field: world["foreign"]["e1"]},
            headers=_hdr(generate_token),
        )
        assert resp.status_code == 404, (await _body(resp))[:300]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/{d}"),
            ("patch", "/{d}"),
            ("delete", "/{d}"),
            ("get", "/resource/entity/{e}"),
            ("get", "/resource/organization/{o}"),
        ],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_foreign_ids_are_404(
        self, mock_user, method, path, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        f = world["foreign"]
        kwargs = {"headers": _hdr(generate_token)}
        if method == "patch":
            kwargs["json"] = {"dependency_type": "pwned"}
        resp = await getattr(async_client, method)(
            "/api/v1/dependencies" + path.format(d=f["dep"], e=f["e1"], o=f["org"]),
            **kwargs,
        )
        assert resp.status_code == 404, (await _body(resp))[:300]

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_resource_dependencies_hide_foreign_edges(
        self, mock_user, async_client, generate_token, world
    ):
        """A foreign-tenant edge pointing at an own entity is not listed."""
        mock_user.return_value = _user()
        async with async_client.app.app_context():
            db = current_app.db
            stray = _dependency(
                db, world["foreign_tenant"], world["foreign"]["e1"], world["own"]["e1"]
            )
        for url in (
            f"/api/v1/dependencies/resource/entity/{world['own']['e1']}",
            f"/api/v1/entities/{world['own']['e1']}/dependencies",
        ):
            resp = await async_client.get(url, headers=_hdr(generate_token))
            body = await resp.get_json()
            assert resp.status_code == 200
            data = body.get("data", body)
            ids = {d["id"] for d in data.get("depended_by", [])}
            assert stray not in ids, url

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", ""),
            ("post", ""),
            ("get", "/{d}"),
            ("patch", "/{d}"),
            ("delete", "/{d}"),
            ("post", "/bulk"),
            ("delete", "/bulk"),
            ("get", "/resource/entity/{e}"),
        ],
    )
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_token_without_tenant_is_forbidden(
        self, mock_user, method, path, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        own = world["own"]
        dep = {
            "source_type": "entity",
            "source_id": own["e1"],
            "target_type": "entity",
            "target_id": own["e2"],
            "dependency_type": "x",
        }
        payloads = {
            ("post", ""): dep,
            ("patch", "/{d}"): {"dependency_type": "x"},
            ("post", "/bulk"): {"dependencies": [dep]},
            ("delete", "/bulk"): {"ids": [own["dep"]]},
        }
        kwargs = {"headers": _hdr(generate_token, tenant_id=0)}
        if (method, path) in payloads:
            kwargs["json"] = payloads[(method, path)]
        url = "/api/v1/dependencies" + path.format(d=own["dep"], e=own["e1"])
        if method == "delete" and "json" in kwargs:
            resp = await async_client.open(url, method="DELETE", **kwargs)
        else:
            resp = await getattr(async_client, method)(url, **kwargs)
        assert resp.status_code == 403, (await _body(resp))[:300]


class TestSearchGraphTenantIsolation:
    """``POST /search/graph`` only traverses the caller's tenant."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_own_graph_returned_without_foreign_nodes(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.post(
            "/api/v1/search/graph",
            json={"start_entity_id": world["own"]["e1"]},
            headers=_hdr(generate_token),
        )
        body = await _body(resp)
        assert resp.status_code == 200, body[:300]
        data = await resp.get_json()
        ids = {n["id"] for n in data["nodes"]}
        assert ids == {world["own"]["e1"], world["own"]["e2"]}
        assert f"{world['tag']}-foreign" not in body

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_foreign_start_entity_is_404(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.post(
            "/api/v1/search/graph",
            json={"start_entity_id": world["foreign"]["e1"]},
            headers=_hdr(generate_token),
        )
        assert resp.status_code == 404
        assert f"{world['tag']}-foreign" not in await _body(resp)

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_requires_infrastructure_read(
        self, mock_user, async_client, generate_token, world
    ):
        mock_user.return_value = _user()
        resp = await async_client.post(
            "/api/v1/search/graph",
            json={"start_entity_id": world["own"]["e1"]},
            headers=_hdr(generate_token, scopes=["issues:read"]),
        )
        assert resp.status_code == 403
