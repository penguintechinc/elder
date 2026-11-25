"""Extra route tests for the infrastructure graph, organizations and entities APIs.

Real Quart app, JWTs and Postgres; rows for projects, issues, software and so
on are seeded through the DB so the map endpoint is exercised end to end.
"""

# flake8: noqa: E501

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.api.modules.infrastructure.routes import graph as gr
from apps.api.modules.infrastructure.routes import organizations_pydal as op
from tests.unit._cov_d_helpers import call, in_db, make_token, vid, world  # noqa: F401

GR = "/api/v1/graph"
ORG = "/api/v1/organizations"
ENT = "/api/v1/entities"
DEP = "/api/v1/dependencies"
RW = ["infrastructure:read", "infrastructure:write"]


def _st(resp):
    """Return the status code of a ``(status, body)`` pair."""
    return resp[0]


async def _entity(app, w, name, **kw):
    """Create an entity in tenant A through the API and return its id."""
    s, d = await call(
        app,
        "post",
        ENT,
        w["tok"]("a", RW),
        json={
            "name": name,
            "entity_type": "compute",
            "organization_id": w["a"]["org"],
            **kw,
        },
    )
    assert s == 201, d
    return (d.get("data") or d)["id"]


async def _dep(app, w, a, b, kind="calls"):
    """Create an entity-to-entity dependency and return its id."""
    s, d = await call(
        app,
        "post",
        DEP,
        w["tok"]("a", RW),
        json={
            "source_type": "entity",
            "source_id": a,
            "target_type": "entity",
            "target_id": b,
            "dependency_type": kind,
        },
    )
    assert s == 201, d
    return d["id"]


@pytest.mark.asyncio
async def test_graph_routes(app, world):  # noqa: F811
    """Graph, analyze and path endpoints: filters, subgraph, cycles, truncation, isolation."""
    w = world
    t = w["tok"]("a", RW)
    tb = w["tok"]("b", RW)
    nt = make_token(app, 0, w["a"]["user"], RW)
    e1, e2, e3, e4 = [
        await _entity(app, w, f"g{i}", attributes={"k": i}) for i in range(4)
    ]
    await _dep(app, w, e1, e2)
    await _dep(app, w, e2, e3, "runs_on")
    await _dep(app, w, e3, e1, "depends_on")
    assert _st(await call(app, "get", GR)) == 401
    for u in (GR, f"{GR}/analyze", f"{GR}/path?from=1&to=2"):
        assert _st(await call(app, "get", u, nt)) == 403, u
    with patch("quart.g", SimpleNamespace(current_user=None, claims={})):
        assert _st(await call(app, "get", f"{GR}/map", t)) == 403
    # base graph
    s, g = await call(
        app,
        "get",
        GR,
        t,
        query_string={
            "include_metadata": "true",
            "entity_type": "compute",
            "organization_id": w["a"]["org"],
        },
    )
    assert (
        s == 200
        and g["stats"]["entity_count"] == 4
        and g["stats"]["dependency_count"] == 3
    )
    assert any("metadata" in n for n in g["nodes"])
    s, g = await call(app, "get", GR, tb)
    assert s == 200 and g["stats"]["entity_count"] == 0
    s, g = await call(
        app, "get", GR, t, query_string={"organization_id": w["b"]["org"]}
    )
    assert s == 200 and g["nodes"] == []
    s, g = await call(app, "get", GR, t, query_string={"entity_id": e1, "depth": 1})
    assert s == 200 and g["stats"]["depth"] == 1
    s, g = await call(app, "get", GR, t, query_string={"entity_id": e1, "depth": -1})
    assert s == 200 and g["stats"]["depth_clamped"] is True
    assert (
        _st(await call(app, "get", GR, t, query_string={"entity_id": 99999999})) == 404
    )
    assert _st(await call(app, "get", GR, tb, query_string={"entity_id": e1})) == 404
    # analyze
    s, a = await call(app, "get", f"{GR}/analyze", t)
    assert (
        s == 200
        and a["basic_stats"]["total_entities"] == 4
        and a["issues"]["circular_dependencies"] >= 1
        and a["issues"]["isolated_entities"] == 1
    )
    s, a = await call(app, "get", f"{GR}/analyze", tb)
    assert s == 200 and a["basic_stats"]["total_entities"] == 0
    s, a = await call(
        app, "get", f"{GR}/analyze", t, query_string={"organization_id": w["b"]["org"]}
    )
    assert s == 200 and a["basic_stats"]["total_entities"] == 0
    # path
    assert _st(await call(app, "get", f"{GR}/path", t)) == 400
    assert (
        _st(
            await call(
                app, "get", f"{GR}/path", t, query_string={"from": 99999999, "to": e1}
            )
        )
        == 404
    )
    assert (
        _st(
            await call(
                app, "get", f"{GR}/path", t, query_string={"from": e1, "to": 99999999}
            )
        )
        == 404
    )
    s, p = await call(app, "get", f"{GR}/path", t, query_string={"from": e1, "to": e3})
    assert s == 200 and p["path_exists"] is True and p["path_length"] == 2
    s, p = await call(app, "get", f"{GR}/path", t, query_string={"from": e1, "to": e4})
    assert s == 200 and p["path_exists"] is False and "truncated" not in p
    with patch.object(gr, "GRAPH_MAX_NODES", 2):
        s, p = await call(
            app, "get", f"{GR}/path", t, query_string={"from": e1, "to": e4}
        )
        assert s == 200 and p["truncated"] is True
        s, g = await call(app, "get", GR, t)
        assert s == 200 and g["stats"]["truncated"] is True
    with patch.object(gr, "GRAPH_MAX_EDGES", 1):
        s, g = await call(app, "get", GR, t)
        assert s == 200 and g["stats"]["truncated"] is True
    with patch.object(gr.nx, "simple_cycles", side_effect=RuntimeError("x")):
        s, a = await call(app, "get", f"{GR}/analyze", t)
        assert s == 200 and a["issues"]["circular_dependencies"] == 0


@pytest.mark.asyncio
async def test_map_endpoint_all_resource_types(app, world):  # noqa: F811
    """The map includes every resource type, hierarchy and dependency edges, with filters."""
    w = world
    t = w["tok"]("a", RW)
    tb = w["tok"]("b", RW)
    root = w["a"]["org"]
    tid = w["a"]["tenant"]

    def _seed():
        """Insert fixture rows and return what the test needs.
        Used only to set up this test's data."""
        db = app.db
        n = datetime.now(UTC)
        child = db.organizations.insert(
            name=f"mc-{w['sfx']}",
            tenant_id=tid,
            parent_id=root,
            created_at=n,
            updated_at=n,
        )
        ent = db.entities.insert(
            name="m-e", type="compute", organization_id=root, external_id="m1"
        )
        ent2 = db.entities.insert(
            name="m-e2",
            type="network",
            organization_id=root,
            parent_id=ent,
            external_id="m2",
        )
        proj = db.projects.insert(
            name="p",
            status="active",
            organization_id=root,
            tenant_id=tid,
            village_id=vid(),
            created_at=n,
            updated_at=n,
        )
        ms = db.milestones.insert(
            title="m",
            status="open",
            organization_id=root,
            tenant_id=tid,
            village_id=vid(),
            created_at=n,
            updated_at=n,
        )
        iss = db.issues.insert(
            tenant_id=tid,
            resource_type="organization",
            resource_id=root,
            title="i",
            status="OPEN",
            priority="LOW",
            issue_type="OPERATIONS",
            is_incident=0,
            organization_id=root,
            village_id=vid(),
            created_at=n,
            updated_at=n,
        )
        nr = db.networking_resources.insert(
            name="vpc", network_type="vpc", organization_id=root, external_id="v1"
        )
        ds = db.data_stores.insert(
            name="d",
            organization_id=root,
            tenant_id=tid,
            storage_type="database",
            village_id=vid(),
            created_at=n,
            updated_at=n,
        )
        svc = db.services.insert(
            name="s",
            organization_id=root,
            tenant_id=tid,
            is_public=False,
            deployment_method="k8s",
            external_id="s1",
        )
        sw = db.software.insert(
            name="sw",
            organization_id=root,
            tenant_id=tid,
            software_type="app",
            is_active=True,
            external_id="sw1",
            village_id=vid(),
            created_at=n,
            updated_at=n,
        )
        db.dependencies.insert(
            source_type="entity",
            source_id=ent,
            target_type="data_store",
            target_id=ds,
            dependency_type="uses",
            tenant_id=tid,
        )
        db.commit()
        return {"child": child, "ent": ent}

    ids = await in_db(_seed)
    s, m = await call(app, "get", f"{GR}/map", t)
    assert s == 200, m
    kinds = {n["resource_type"] for n in m["nodes"]}
    assert {
        "organization",
        "entity",
        "identity",
        "project",
        "milestone",
        "issue",
        "networking_resource",
        "data_store",
        "service",
        "software",
    } <= kinds
    edge_types = {e["type"] for e in m["edges"]}
    assert {"parent_of", "contains", "uses"} <= edge_types
    # org filter incl. children, entity type filter, resource_types subset
    s, m = await call(
        app,
        "get",
        f"{GR}/map",
        t,
        query_string={"organization_id": root, "entity_types": "compute", "limit": 100},
    )
    assert s == 200 and all(
        n.get("type") != "network" for n in m["nodes"] if n["resource_type"] == "entity"
    )
    s, m = await call(
        app,
        "get",
        f"{GR}/map",
        t,
        query_string={
            "resource_types": "entity,project",
            "organization_id": root,
            "include_hierarchical": "false",
            "include_dependencies": "false",
        },
    )
    assert s == 200 and m["edges"] == []
    s, m = await call(
        app,
        "get",
        f"{GR}/map",
        t,
        query_string={
            "resource_types": "data_store,service,software",
            "organization_id": root,
        },
    )
    assert s == 200 and {n["resource_type"] for n in m["nodes"]} == {
        "data_store",
        "service",
        "software",
    }
    s, m = await call(
        app,
        "get",
        f"{GR}/map",
        t,
        query_string={
            "resource_types": "data_store,service,software",
            "organization_id": 99999999,
        },
    )
    assert s == 200 and m["nodes"] == []
    s, m = await call(app, "get", f"{GR}/map", t, query_string={"limit": 1})
    assert s == 200 and m["stats"]["truncated"] is True and len(m["nodes"]) == 1
    # another tenant's org id never scopes into foreign data
    s, m = await call(
        app, "get", f"{GR}/map", tb, query_string={"organization_id": root}
    )
    assert s == 200 and all(
        n["resource_type"] in ("organization", "identity") for n in m["nodes"]
    )
    assert all(
        n["resource_id"] != ids["ent"]
        for n in m["nodes"]
        if n["resource_type"] == "entity"
    )


@pytest.mark.asyncio
async def test_map_without_tenant_orgs(app, world):  # noqa: F811
    """A tenant that owns no organizations gets no org-scoped nodes."""
    w = world

    def _mk():
        """Insert the fixture rows and return their id.
        Used only to set up this test's data."""
        db = app.db
        n = datetime.now(UTC)
        tid = db.tenants.insert(
            name=f"empty-{w['sfx']}", slug=f"empty-{w['sfx']}", is_active=True
        )
        ident = db.identities.insert(
            identity_type="human",
            username=f"e-{w['sfx']}",
            email=f"e-{w['sfx']}@example.com",
            full_name="E",
            tenant_id=tid,
            auth_provider="local",
            is_active=True,
            is_superuser=False,
            mfa_enabled=False,
            must_change_password=False,
            portal_role="observer",
            created_at=n,
            updated_at=n,
        )
        db.commit()
        return tid, ident

    tid, ident = await in_db(_mk)
    t = make_token(app, tid, ident, RW)
    s, m = await call(app, "get", f"{GR}/map", t)
    assert s == 200 and {n["resource_type"] for n in m["nodes"]} <= {"identity"}
    s, m = await call(app, "get", ENT, t)
    assert s == 200 and m["total"] == 0 and m["items"] == []


def test_graph_helpers():
    """Pure helpers: depth clamping, tenant org ids, entity query scoping, styles."""
    assert gr._resolve_depth(None) == (gr.GRAPH_MAX_DEPTH, True)
    assert gr._resolve_depth(-3) == (gr.GRAPH_MAX_DEPTH, True)
    assert gr._resolve_depth(gr.GRAPH_MAX_DEPTH + 5) == (gr.GRAPH_MAX_DEPTH, True)
    assert gr._resolve_depth(1) == (1, False)
    assert gr._tenant_org_ids(None, None) == []
    assert gr._entity_edges(None, []) == ([], False)
    db = SimpleNamespace(
        entities=SimpleNamespace(
            organization_id=SimpleNamespace(
                belongs=lambda ids: ("belongs", ids), __eq__=None
            )
        )
    )
    assert gr._tenant_entity_query(db, [1, 2], 9) is None
    assert gr._tenant_entity_query(db, [1, 2]) == ("belongs", [1, 2])
    assert gr._get_node_style("compute")
    assert gr._get_node_style("zzz-unknown")
    assert gr._get_edge_style("zzz-unknown")
    assert gr._get_node_style_by_resource("zzz-unknown", None)


@pytest.mark.asyncio
async def test_organizations_edge_paths(app, world):  # noqa: F811
    """Organization routes: team limit, object limit, insert failures, error mapping, depth cut."""
    w = world
    t = w["tok"]("a", RW)
    root = w["a"]["org"]
    # team-type create goes through the team limit check; a block is returned verbatim
    s, d = await call(
        app,
        "post",
        ORG,
        t,
        json={"name": f"team-{w['sfx']}", "organization_type": "team"},
    )
    assert s == 201, d
    from apps.api.utils.api_responses import ApiResponse

    async def blocked(*_a, **_k):
        """Return a limit-exceeded response like the license gate would."""
        return ApiResponse.error("limit reached", 402)

    with patch.object(op, "check_limit", blocked):
        s, d = await call(
            app,
            "post",
            ORG,
            t,
            json={"name": f"t2-{w['sfx']}", "organization_type": "team"},
        )
        assert s == 402
        s, d = await call(app, "post", ORG, t, json={"name": f"t3-{w['sfx']}"})
        assert s == 402
    # insert failures
    with patch.object(op, "insert_record", return_value=None):
        assert (
            _st(await call(app, "post", ORG, t, json={"name": f"f1-{w['sfx']}"})) == 500
        )
    with patch.object(op, "insert_record", side_effect=RuntimeError("boom")):
        s, d = await call(app, "post", ORG, t, json={"name": f"f2-{w['sfx']}"})
        assert s == 500 and "boom" not in json.dumps(
            d if isinstance(d, (dict, list)) else str(d)
        )
    # row vanished after insert -> fallback DTO still returned
    with patch.object(op, "get_by_id", return_value=None):
        s, d = await call(
            app, "post", ORG, t, json={"name": f"f3-{w['sfx']}", "description": "d"}
        )
        assert s == 201 and d["name"] == f"f3-{w['sfx']}"
    # lookup / update / delete failures
    with patch.object(op, "_get_org_scoped_for_caller", side_effect=RuntimeError("x")):
        assert _st(await call(app, "get", f"{ORG}/{root}", t)) == 404
    with patch.object(op, "commit_db", side_effect=RuntimeError("x")):
        assert (
            _st(await call(app, "put", f"{ORG}/{root}", t, json={"description": "z"}))
            == 500
        )
        s, c = await call(app, "post", ORG, t, json={"name": f"f4-{w['sfx']}"})
        assert s == 500
    s, c = await call(app, "post", ORG, t, json={"name": f"del-{w['sfx']}"})
    with patch.object(op, "commit_db", side_effect=RuntimeError("x")):
        assert _st(await call(app, "delete", f"{ORG}/{c['id']}", t)) == 500
    # graph: tenant-less user, lookup error, depth cut-off
    fake = SimpleNamespace(current_user=None, claims={})
    with patch.object(op, "g", fake):
        assert _st(await call(app, "get", f"{ORG}/{root}/graph", t)) == 403
    with patch.object(op, "get_tenant_scoped", side_effect=RuntimeError("x")):
        assert _st(await call(app, "get", f"{ORG}/{root}/graph", t)) == 404
    s, ch = await call(
        app, "post", ORG, t, json={"name": f"gc-{w['sfx']}", "parent_id": root}
    )
    s, gc = await call(
        app, "post", ORG, t, json={"name": f"gg-{w['sfx']}", "parent_id": ch["id"]}
    )
    s, g1 = await call(app, "get", f"{ORG}/{root}/graph", t, query_string={"depth": 1})
    assert s == 200 and f"org-{gc['id']}" not in {n["id"] for n in g1["nodes"]}
    s, g3 = await call(app, "get", f"{ORG}/{root}/graph", t, query_string={"depth": 99})
    assert (
        s == 200
        and g3["depth"] == 10
        and f"org-{gc['id']}" in {n["id"] for n in g3["nodes"]}
    )


@pytest.mark.xfail(
    strict=True,
    reason="bug: list_organizations has no tenant filter and lists other tenants' organizations",
)
@pytest.mark.asyncio
async def test_org_list_is_tenant_scoped(app, world):  # noqa: F811
    """A tenant's organization listing must not include another tenant's organizations."""
    w = world
    s, d = await call(
        app, "get", ORG, w["tok"]("b", RW), query_string={"per_page": 1000}
    )
    assert s == 200 and all(i["id"] != w["a"]["org"] for i in d["items"])


@pytest.mark.asyncio
async def test_entity_partial_updates(app, world):  # noqa: F811
    """Single-field updates leave every other column untouched (covers each optional branch)."""
    w = world
    t = w["tok"]("a", RW)
    eid = await _entity(
        app,
        w,
        "partial",
        sub_type="s0",
        external_id="x0",
        tags=["a"],
        attributes={"a": 1},
    )
    parent = await _entity(app, w, "parent")
    for body in [
        {"name": "n1"},
        {"entity_type": "network"},
        {"sub_type": "s1"},
        {"external_id": "x1"},
        {"attributes": {"b": 2}},
        {"tags": ["z"]},
        {"parent_id": parent},
        {"organization_id": w["a"]["org"]},
        {},
    ]:
        s, d = await call(app, "patch", f"{ENT}/{eid}", t, json=body)
        assert s == 200, (body, d)
    s, d = await call(app, "get", f"{ENT}/{eid}", t)
    data = d.get("data") or d
    assert data["name"] == "n1" and data["parent_id"] == parent


@pytest.mark.asyncio
async def test_org_children_without_user_tenant(app, world):  # noqa: F811
    """With no tenant on the caller, the children lookups run unscoped (direct and recursive)."""
    w = world
    t = w["tok"]("a", RW)
    root = w["a"]["org"]
    s, ch = await call(
        app, "post", ORG, t, json={"name": f"nc-{w['sfx']}", "parent_id": root}
    )
    s, gc = await call(
        app, "post", ORG, t, json={"name": f"ng-{w['sfx']}", "parent_id": ch["id"]}
    )
    with patch.object(op, "g", SimpleNamespace()):
        s, d = await call(app, "get", f"{ORG}/{root}/children", t)
        assert s == 200 and [o["id"] for o in d] == [ch["id"]]
        s, d = await call(
            app, "get", f"{ORG}/{root}/children", t, query_string={"recursive": "true"}
        )
        assert s == 200 and {o["id"] for o in d} == {ch["id"], gc["id"]}
        assert _st(await call(app, "get", f"{ORG}/99999999/children", t)) == 404


@pytest.mark.asyncio
async def test_org_graph_handles_parent_cycles(app, world):  # noqa: F811
    """A parent/child cycle in the data never loops forever and nodes are not duplicated."""
    w = world
    t = w["tok"]("a", RW)
    root = w["a"]["org"]
    s, ch = await call(
        app, "post", ORG, t, json={"name": f"cy-{w['sfx']}", "parent_id": root}
    )

    def _loop():
        """Turn the parent link into a cycle.
        Used only to set up this test's data."""
        app.db(app.db.organizations.id == root).update(parent_id=ch["id"])
        app.db.commit()

    await in_db(_loop)
    s, d = await call(app, "get", f"{ORG}/{root}/graph", t, query_string={"depth": 3})
    assert s == 200
    ids = [n["id"] for n in d["nodes"]]
    assert len(ids) == len(set(ids)) == 2
