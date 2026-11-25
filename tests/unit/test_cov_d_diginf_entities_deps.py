"""Route tests for infrastructure entities and dependencies APIs.

Real Quart app, JWTs and Postgres; cross-tenant ids are asserted to be 404
and tenant-less tokens 403.
"""

# flake8: noqa: E501

import pytest
import pytest_asyncio

from tests.unit._cov_d_helpers import call, in_db, make_token, world  # noqa: F401

ENT = "/api/v1/entities"
DEP = "/api/v1/dependencies"
RW = ["infrastructure:read", "infrastructure:write"]


def _st(resp):
    """Return the status code of a ``(status, body)`` pair."""
    return resp[0]


@pytest_asyncio.fixture
async def nt(app, world):  # noqa: F811
    """A token whose tenant claim is zero (no usable tenant context)."""
    return make_token(app, 0, world["a"]["user"], RW)


async def _ent(app, w, side="a", name="e", **kw):
    """Create an entity via the API and return its id."""
    body = {
        "name": name,
        "entity_type": "compute",
        "organization_id": w[side]["org"],
        **kw,
    }
    s, d = await call(app, "post", ENT, w["tok"](side, RW), json=body)
    assert s == 201, d
    return d["data"]["id"] if "data" in d else d["id"]


@pytest.mark.asyncio
async def test_entity_crud(app, world):  # noqa: F811
    """Entity create/list/filter/get/update/attributes/dependencies/delete."""
    w = world
    t = w["tok"]("a", RW)
    tb = w["tok"]("b", RW)
    assert _st(await call(app, "get", ENT)) == 401
    parent = await _ent(
        app,
        w,
        name="parent-e",
        sub_type="server",
        external_id="ext-1",
        tags=["x"],
        attributes={"k": 1},
    )
    child = await _ent(app, w, name="child-e", parent_id=parent)
    # list + filters
    s, d = await call(app, "get", ENT, t)
    assert s == 200 and d["total"] == 2
    for qs, n in [
        ({"entity_type": "compute"}, 2),
        ({"sub_type": "server"}, 1),
        ({"parent_id": parent}, 1),
        ({"organization_id": w["a"]["org"]}, 2),
        ({"name": "child"}, 1),
        ({"external_id": "ext-1"}, 1),
        ({"entity_type": "other"}, 0),
    ]:
        s, d = await call(app, "get", ENT, t, query_string=qs)
        assert s == 200 and d["total"] == n, (qs, d)
    # empty tenant (no orgs for tenant b entities) -> empty page
    s, d = await call(app, "get", ENT, tb)
    assert s == 200 and d["total"] == 0
    # get
    s, d = await call(app, "get", f"{ENT}/{parent}", t)
    assert s == 200
    assert _st(await call(app, "get", f"{ENT}/{parent}", tb)) == 404
    assert _st(await call(app, "get", f"{ENT}/99999999", t)) == 404
    # create validation / isolation
    base = {"name": "z", "entity_type": "compute"}
    assert (
        _st(
            await call(
                app, "post", ENT, t, json={**base, "organization_id": w["b"]["org"]}
            )
        )
        == 404
    )
    assert (
        _st(await call(app, "post", ENT, t, json={**base, "organization_id": 99999999}))
        == 404
    )
    assert (
        _st(
            await call(
                app,
                "post",
                ENT,
                t,
                json={**base, "organization_id": w["a"]["org"], "parent_id": 99999999},
            )
        )
        == 404
    )
    assert _st(await call(app, "post", ENT, t, json={"name": "z"})) == 400
    # update
    assert (
        _st(await call(app, "put", f"{ENT}/{child}", tb, json={"name": "hijack"}))
        == 404
    )
    assert (
        _st(
            await call(
                app, "put", f"{ENT}/{child}", t, json={"organization_id": w["b"]["org"]}
            )
        )
        == 404
    )
    assert (
        _st(await call(app, "put", f"{ENT}/{child}", t, json={"parent_id": 99999999}))
        == 404
    )
    s, d = await call(
        app,
        "patch",
        f"{ENT}/{child}",
        t,
        json={
            "name": "child2",
            "entity_type": "network",
            "organization_id": w["a"]["org"],
            "parent_id": parent,
            "sub_type": "s",
            "external_id": "e2",
            "attributes": {"a": 2},
            "tags": ["t"],
        },
    )
    assert s == 200, d
    # attributes
    assert (
        _st(await call(app, "patch", f"{ENT}/{child}/attributes", tb, json={"a": 1}))
        == 404
    )
    assert (
        _st(await call(app, "patch", f"{ENT}/{child}/attributes", t, json=[1])) == 400
    )
    s, d = await call(app, "patch", f"{ENT}/{child}/attributes", t, json={"extra": "v"})
    assert s == 200, d
    # dependencies of an entity (+ delete guard)
    assert _st(await call(app, "get", f"{ENT}/99999999/dependencies", t)) == 404
    s, d = await call(app, "get", f"{ENT}/{parent}/dependencies", t)
    assert s == 200
    s, dep = await call(
        app,
        "post",
        DEP,
        t,
        json={
            "source_type": "entity",
            "source_id": child,
            "target_type": "entity",
            "target_id": parent,
            "dependency_type": "runs_on",
        },
    )
    assert s == 201, dep
    for direction in ("outgoing", "incoming", "all"):
        s, d = await call(
            app,
            "get",
            f"{ENT}/{child}/dependencies",
            t,
            query_string={"direction": direction},
        )
        assert s == 200
    s, d = await call(
        app,
        "get",
        f"{ENT}/{child}/dependencies",
        t,
        query_string={"direction": "outgoing"},
    )
    data = d.get("data", d)
    assert len(data["depends_on"]) == 1 and "depended_by" not in data
    assert _st(await call(app, "delete", f"{ENT}/{child}", t)) == 400
    assert _st(await call(app, "delete", f"{ENT}/{child}", tb)) == 404
    assert _st(await call(app, "delete", f"{DEP}/{dep['id']}", t)) == 204


@pytest.mark.xfail(
    strict=True,
    reason="bug: delete_entity uses `del db.entities[id]` on a table proxy that does not support item deletion (500)",
)
@pytest.mark.asyncio
async def test_entity_delete(app, world):  # noqa: F811
    """Deleting an entity without dependencies returns 204."""
    w = world
    t = w["tok"]("a", RW)
    eid = await _ent(app, w, name="del-e")
    assert _st(await call(app, "delete", f"{ENT}/{eid}", t)) == 204
    assert _st(await call(app, "get", f"{ENT}/{eid}", t)) == 404


@pytest.mark.asyncio
async def test_entity_no_tenant(app, world, nt):  # noqa: F811
    """A token without a usable tenant claim is refused on every entity route."""
    for m, u, kw in [
        ("get", ENT, {}),
        (
            "post",
            ENT,
            {"json": {"name": "n", "entity_type": "c", "organization_id": 1}},
        ),
        ("get", f"{ENT}/1", {}),
        ("put", f"{ENT}/1", {"json": {"name": "n"}}),
        ("delete", f"{ENT}/1", {}),
        ("get", f"{ENT}/1/dependencies", {}),
        ("patch", f"{ENT}/1/attributes", {"json": {}}),
    ]:
        s, _ = await call(app, m, u, nt, **kw)
        assert s in (401, 403), (m, u, s)


async def _org2(app, w, side="a"):
    """Create a second organization in the given tenant and return its id."""

    def _mk():
        """Insert the fixture rows and return their id.
        Used only to set up this test's data."""
        n = now_()
        oid = app.db.organizations.insert(
            name=f"org2-{w['sfx']}-{side}",
            tenant_id=w[side]["tenant"],
            created_at=n,
            updated_at=n,
        )
        app.db.commit()
        return oid

    return await in_db(_mk)


def now_():
    """Return the current UTC time."""
    from datetime import UTC, datetime

    return datetime.now(UTC)


@pytest.mark.asyncio
async def test_dependency_crud(app, world):  # noqa: F811
    """Dependency create/list/get/update/delete, bulk ops, resource view and isolation."""
    w = world
    t = w["tok"]("a", RW)
    tb = w["tok"]("b", RW)
    org_a = w["a"]["org"]
    org2 = await _org2(app, w)
    ident = w["a"]["user"]
    assert _st(await call(app, "get", DEP)) == 401
    e1 = await _ent(app, w, name="d1")
    e2 = await _ent(app, w, name="d2")

    def mk(**kw):
        """Build a dependency body between e1 and e2 with overrides."""
        return {
            "source_type": "entity",
            "source_id": e1,
            "target_type": "entity",
            "target_id": e2,
            "dependency_type": "uses",
            **kw,
        }

    # validation
    assert _st(await call(app, "post", DEP, t, json={"source_type": "entity"})) == 400
    assert _st(await call(app, "post", DEP, t, json=mk(source_type="bogus"))) == 400
    assert _st(await call(app, "post", DEP, t, json=mk(target_type="bogus"))) == 400
    assert _st(await call(app, "post", DEP, t, json=mk(target_id=e1))) == 400
    assert _st(await call(app, "post", DEP, t, json=mk(source_id=99999999))) == 404
    assert _st(await call(app, "post", DEP, t, json=mk(target_id=99999999))) == 404
    assert _st(await call(app, "post", DEP, tb, json=mk())) == 404
    s, dep = await call(app, "post", DEP, t, json=mk(metadata={"m": 1}))
    assert s == 201, dep
    did = dep["id"]
    s, d = await call(app, "post", DEP, t, json=mk())
    assert s == 409 and d["dependency_id"] == did
    # other resource types as endpoints
    s, d2 = await call(
        app,
        "post",
        DEP,
        t,
        json={
            "source_type": "organization",
            "source_id": org_a,
            "target_type": "organization",
            "target_id": org2,
            "dependency_type": "owns",
        },
    )
    assert s == 201, d2
    s, d3 = await call(
        app,
        "post",
        DEP,
        t,
        json={
            "source_type": "identity",
            "source_id": ident,
            "target_type": "entity",
            "target_id": e1,
            "dependency_type": "manages",
        },
    )
    assert s == 201, d3
    # list + filters
    s, d = await call(app, "get", DEP, t)
    assert s == 200 and d["total"] == 3
    for qs, n in [
        ({"source_type": "entity"}, 1),
        ({"source_id": e1}, 1),
        ({"target_type": "organization"}, 1),
        ({"target_id": e2}, 1),
        ({"dependency_type": "owns"}, 1),
        ({"page": 1, "per_page": 1}, 3),
    ]:
        s, d = await call(app, "get", DEP, t, query_string=qs)
        assert s == 200 and d["total"] == n, (qs, d)
    assert (await call(app, "get", DEP, tb))[1]["total"] == 0
    # get
    assert _st(await call(app, "get", f"{DEP}/{did}", t)) == 200
    assert _st(await call(app, "get", f"{DEP}/{did}", tb)) == 404
    assert _st(await call(app, "get", f"{DEP}/99999999", t)) == 404
    # update
    assert (
        _st(await call(app, "put", f"{DEP}/{did}", tb, json={"dependency_type": "x"}))
        == 404
    )
    assert (
        _st(await call(app, "put", f"{DEP}/{did}", t, json={"source_type": "bogus"}))
        == 400
    )
    assert (
        _st(await call(app, "put", f"{DEP}/{did}", t, json={"target_type": "bogus"}))
        == 400
    )
    assert (
        _st(await call(app, "put", f"{DEP}/{did}", t, json={"source_id": 99999999}))
        == 404
    )
    assert (
        _st(await call(app, "patch", f"{DEP}/{did}", t, json={"target_id": 99999999}))
        == 404
    )
    s, d = await call(
        app,
        "put",
        f"{DEP}/{did}",
        t,
        json={"dependency_type": "deps", "metadata": {"z": 2}},
    )
    assert s == 200 and d["dependency_type"] == "deps"
    e3 = await _ent(app, w, name="d3")
    s, d = await call(
        app,
        "patch",
        f"{DEP}/{did}",
        t,
        json={
            "source_type": "entity",
            "source_id": e3,
            "target_type": "entity",
            "target_id": e2,
        },
    )
    assert s == 200 and d["source_id"] == e3
    # resource view
    assert _st(await call(app, "get", f"{DEP}/resource/bogus/1", t)) == 400
    assert _st(await call(app, "get", f"{DEP}/resource/entity/99999999", t)) == 404
    assert _st(await call(app, "get", f"{DEP}/resource/entity/{e2}", tb)) == 404
    for direction in ("all", "outgoing", "incoming"):
        s, d = await call(
            app,
            "get",
            f"{DEP}/resource/entity/{e2}",
            t,
            query_string={"direction": direction},
        )
        assert s == 200
    s, d = await call(app, "get", f"{DEP}/resource/identity/{ident}", t)
    assert s == 200 and d["resource_name"]

    # bulk create
    def item(src, tgt, **kw):
        """Build a bulk dependency body from src to tgt with overrides."""
        return {
            "source_type": "entity",
            "source_id": src,
            "target_type": "entity",
            "target_id": tgt,
            "dependency_type": "bulk",
            **kw,
        }

    assert _st(await call(app, "post", f"{DEP}/bulk", t, json=None)) in (400, 415, 500)
    assert (
        _st(await call(app, "post", f"{DEP}/bulk", t, json={"dependencies": []})) == 400
    )
    assert (
        _st(
            await call(
                app,
                "post",
                f"{DEP}/bulk",
                t,
                json={"dependencies": [item(e1, e2, source_type="bogus")]},
            )
        )
        == 400
    )
    assert (
        _st(
            await call(
                app,
                "post",
                f"{DEP}/bulk",
                t,
                json={"dependencies": [item(e1, 99999999)]},
            )
        )
        == 404
    )
    assert (
        _st(
            await call(
                app, "post", f"{DEP}/bulk", tb, json={"dependencies": [item(e1, e2)]}
            )
        )
        == 404
    )
    s, d = await call(
        app,
        "post",
        f"{DEP}/bulk",
        t,
        json={"dependencies": [item(e1, e3), item(e3, e1)]},
    )
    assert s == 201 and len(d) == 2
    ids = [x["id"] for x in d]
    # bulk delete
    assert _st(await call(app, "delete", f"{DEP}/bulk", t, json={"ids": []})) == 400
    s, d = await call(app, "delete", f"{DEP}/bulk", tb, json={"ids": ids})
    assert s == 200 and d["deleted"] == 0
    s, d = await call(app, "delete", f"{DEP}/bulk", t, json={"ids": ids})
    assert s == 200 and d["deleted"] == 2 and d["requested"] == 2
    # delete
    assert _st(await call(app, "delete", f"{DEP}/{did}", tb)) == 404
    assert _st(await call(app, "delete", f"{DEP}/{did}", t)) == 204
    assert _st(await call(app, "delete", f"{DEP}/{did}", t)) == 404


@pytest.mark.asyncio
async def test_dependency_no_tenant(app, world, nt):  # noqa: F811
    """Tenant-less tokens are refused on every dependency route."""
    for m, u, kw in [
        ("get", DEP, {}),
        (
            "post",
            DEP,
            {
                "json": {
                    "source_type": "entity",
                    "source_id": 1,
                    "target_type": "entity",
                    "target_id": 2,
                    "dependency_type": "x",
                }
            },
        ),
        ("get", f"{DEP}/1", {}),
        ("put", f"{DEP}/1", {"json": {"dependency_type": "x"}}),
        ("delete", f"{DEP}/1", {}),
        ("post", f"{DEP}/bulk", {"json": {"dependencies": []}}),
        ("delete", f"{DEP}/bulk", {"json": {"ids": [1]}}),
        ("get", f"{DEP}/resource/entity/1", {}),
    ]:
        s, _ = await call(app, m, u, nt, **kw)
        assert s in (401, 403), (m, u, s)


def test_get_tenant_resource_branches():
    """Cover ownership resolution: unknown type, parent project, and fail-closed paths."""
    from types import SimpleNamespace as NS

    from apps.api.modules.infrastructure.routes import dependencies as dp

    assert dp.get_tenant_resource(None, "entity", 1, None) is None
    assert dp._lookup_resource(NS(), "bogus", 1) is None
    assert dp._lookup_resource(NS(), "entity", 1) is None  # table missing on db
    row = NS(tenant_id=None, organization_id=None, project_id=5)
    proj_ok = NS(tenant_id=7)
    db = NS(projects={5: proj_ok}, entities={1: row})
    assert dp.get_tenant_resource(db, "entity", 1, 7) is row
    assert dp.get_tenant_resource(db, "entity", 1, 8) is None
    db2 = NS(projects={5: None}, entities={1: row})
    assert dp.get_tenant_resource(db2, "entity", 1, 7) is None
    row2 = NS(tenant_id=None, organization_id=None, project_id=None)
    assert dp.get_tenant_resource(NS(entities={1: row2}), "entity", 1, 7) is None
    assert dp._owned_by_tenant(None, row2, 7) is None
