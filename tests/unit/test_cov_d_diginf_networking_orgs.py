"""Route tests for infrastructure networking, organizations and tree-stats APIs.

Real Quart app, JWTs and Postgres.  Unexpected-failure (500) branches are
driven by making the service layer raise, never by faking the database.
"""

# flake8: noqa: E501

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from apps.api.modules.infrastructure.routes import networking as netmod
from tests.unit._cov_d_helpers import call, in_db, make_token, world  # noqa: F401

NET = "/api/v1/networking"
ORG = "/api/v1/organizations"
ENT = "/api/v1/entities"
RW = ["infrastructure:read", "infrastructure:write"]


def _st(resp):
    """Return the status code of a ``(status, body)`` pair."""
    return resp[0]


async def _entity(app, w, name="ne"):
    """Create an entity in tenant A and return its id."""
    s, d = await call(
        app,
        "post",
        ENT,
        w["tok"]("a", RW),
        json={"name": name, "entity_type": "compute", "organization_id": w["a"]["org"]},
    )
    assert s == 201, d
    return (d.get("data") or d)["id"]


async def _net(app, w, name, **kw):
    """Create a network in tenant A's org and return its id."""
    s, d = await call(
        app,
        "post",
        f"{NET}/networks",
        w["tok"]("a", RW),
        json={
            "organization_id": w["a"]["org"],
            "name": name,
            "network_type": "vlan",
            **kw,
        },
    )
    assert s == 201, d
    return d["id"]


@pytest.mark.asyncio
async def test_networks_crud(app, world):  # noqa: F811
    """Network create/list/get/update/delete including duplicates, overwrite and isolation."""
    w = world
    t = w["tok"]("a", RW)
    tb = w["tok"]("b", RW)
    assert _st(await call(app, "get", f"{NET}/networks")) == 401
    n1 = await _net(app, w, "net-1", cidr="10.0.0.0/24", region="r1")
    s, d = await call(
        app,
        "post",
        f"{NET}/networks",
        t,
        json={
            "organization_id": w["a"]["org"],
            "name": "net-1",
            "network_type": "vlan",
        },
    )
    assert s == 409 and d["existing_id"] == n1
    s, d = await call(
        app,
        "post",
        f"{NET}/networks",
        t,
        json={
            "organization_id": w["a"]["org"],
            "name": "other",
            "network_type": "vlan",
            "cidr": "10.0.0.0/24",
        },
    )
    assert s == 409
    s, d = await call(
        app,
        "post",
        f"{NET}/networks",
        t,
        json={
            "organization_id": w["a"]["org"],
            "name": "net-1",
            "network_type": "subnet",
            "overwrite": True,
            "gateway": "10.0.0.1",
            "vlan_id": 5,
            "mtu": 1500,
        },
    )
    assert s == 200, d
    n2 = await _net(app, w, "net-2", parent_id=n1)
    assert _st(await call(app, "post", f"{NET}/networks", t, json=None)) in (400, 415)
    assert _st(await call(app, "post", f"{NET}/networks", t, json={"name": "x"})) == 400
    assert (
        _st(
            await call(
                app,
                "post",
                f"{NET}/networks",
                t,
                json={
                    "organization_id": w["b"]["org"],
                    "name": "x",
                    "network_type": "vlan",
                },
            )
        )
        == 404
    )
    assert (
        _st(
            await call(
                app,
                "post",
                f"{NET}/networks",
                t,
                json={
                    "organization_id": w["a"]["org"],
                    "name": "x",
                    "network_type": "vlan",
                    "parent_id": 99999999,
                },
            )
        )
        == 404
    )
    # list
    s, d = await call(
        app,
        "get",
        f"{NET}/networks",
        t,
        query_string={
            "organization_id": w["a"]["org"],
            "network_type": "subnet",
            "region": "r1",
            "limit": 5,
        },
    )
    assert s == 200, d
    s, d = await call(app, "get", f"{NET}/networks", t, query_string={"parent_id": n1})
    assert s == 200
    # get
    assert _st(await call(app, "get", f"{NET}/networks/{n1}", t)) == 200
    assert _st(await call(app, "get", f"{NET}/networks/{n1}", tb)) == 404
    # update
    assert _st(await call(app, "put", f"{NET}/networks/{n1}", t, json=None)) in (
        400,
        415,
    )
    assert (
        _st(await call(app, "put", f"{NET}/networks/{n1}", t, json={"mtu": "bad"}))
        == 400
    )
    assert (
        _st(await call(app, "put", f"{NET}/networks/{n1}", tb, json={"name": "h"}))
        == 404
    )
    s, d = await call(
        app,
        "patch",
        f"{NET}/networks/{n1}",
        t,
        json={
            "name": "net-1b",
            "description": "d",
            "gateway": "10.0.0.254",
            "vlan_id": 7,
            "mtu": 9000,
            "is_active": False,
        },
    )
    assert s == 200, d
    # delete
    assert _st(await call(app, "delete", f"{NET}/networks/{n2}", tb)) == 404
    assert _st(await call(app, "delete", f"{NET}/networks/{n2}", t)) == 200
    assert (
        _st(
            await call(
                app, "delete", f"{NET}/networks/{n2}", t, query_string={"hard": "true"}
            )
        )
        == 200
    )
    assert (
        _st(
            await call(
                app, "delete", f"{NET}/networks/{n1}", t, query_string={"hard": "true"}
            )
        )
        == 200
    )
    assert _st(await call(app, "delete", f"{NET}/networks/{n1}", t)) == 404


@pytest.mark.asyncio
async def test_topology_mappings_graph(app, world):  # noqa: F811
    """Topology connections, entity mappings and the topology graph."""
    w = world
    t = w["tok"]("a", RW)
    tb = w["tok"]("b", RW)
    n1 = await _net(app, w, "tn-1")
    n2 = await _net(app, w, "tn-2")
    e1 = await _entity(app, w)
    # connections
    assert _st(
        await call(app, "post", f"{NET}/topology/connections", t, json=None)
    ) in (400, 415)
    assert (
        _st(
            await call(
                app,
                "post",
                f"{NET}/topology/connections",
                t,
                json={"source_network_id": n1},
            )
        )
        == 400
    )
    assert (
        _st(
            await call(
                app,
                "post",
                f"{NET}/topology/connections",
                t,
                json={
                    "source_network_id": "x",
                    "target_network_id": n2,
                    "connection_type": "peer",
                },
            )
        )
        == 400
    )
    assert (
        _st(
            await call(
                app,
                "post",
                f"{NET}/topology/connections",
                t,
                json={
                    "source_network_id": True,
                    "target_network_id": n2,
                    "connection_type": "peer",
                },
            )
        )
        == 400
    )
    assert (
        _st(
            await call(
                app,
                "post",
                f"{NET}/topology/connections",
                t,
                json={
                    "source_network_id": n1,
                    "target_network_id": 99999999,
                    "connection_type": "peer",
                },
            )
        )
        == 404
    )
    assert (
        _st(
            await call(
                app,
                "post",
                f"{NET}/topology/connections",
                tb,
                json={
                    "source_network_id": n1,
                    "target_network_id": n2,
                    "connection_type": "peer",
                },
            )
        )
        == 404
    )
    s, c = await call(
        app,
        "post",
        f"{NET}/topology/connections",
        t,
        json={
            "source_network_id": n1,
            "target_network_id": n2,
            "connection_type": "peer",
            "bandwidth": "1G",
            "latency": "5ms",
            "metadata": {"a": 1},
        },
    )
    assert s == 201, c
    cid = c["id"]
    s, d = await call(
        app,
        "get",
        f"{NET}/topology/connections",
        t,
        query_string={"network_id": n1, "connection_type": "peer"},
    )
    assert s == 200 and len(d["connections"]) == 1
    assert (await call(app, "get", f"{NET}/topology/connections", tb))[1][
        "connections"
    ] == []
    assert _st(await call(app, "get", f"{NET}/topology/connections/{cid}", t)) == 200
    assert _st(await call(app, "get", f"{NET}/topology/connections/{cid}", tb)) == 404
    # mappings
    assert _st(await call(app, "post", f"{NET}/mappings", t, json=None)) in (400, 415)
    assert (
        _st(await call(app, "post", f"{NET}/mappings", t, json={"network_id": n1}))
        == 400
    )
    assert (
        _st(
            await call(
                app,
                "post",
                f"{NET}/mappings",
                t,
                json={"network_id": 0, "entity_id": e1, "relationship_type": "member"},
            )
        )
        == 400
    )
    assert (
        _st(
            await call(
                app,
                "post",
                f"{NET}/mappings",
                t,
                json={
                    "network_id": n1,
                    "entity_id": 99999999,
                    "relationship_type": "member",
                },
            )
        )
        == 404
    )
    assert (
        _st(
            await call(
                app,
                "post",
                f"{NET}/mappings",
                t,
                json={
                    "network_id": 99999999,
                    "entity_id": e1,
                    "relationship_type": "member",
                },
            )
        )
        == 404
    )
    s, m = await call(
        app,
        "post",
        f"{NET}/mappings",
        t,
        json={
            "network_id": n1,
            "entity_id": e1,
            "relationship_type": "member",
            "metadata": {"ip": "x"},
        },
    )
    assert s == 201, m
    mid = m["id"]
    s, d = await call(
        app,
        "get",
        f"{NET}/mappings",
        t,
        query_string={"network_id": n1, "entity_id": e1, "relationship_type": "member"},
    )
    assert s == 200 and len(d["mappings"]) == 1
    assert (await call(app, "get", f"{NET}/mappings", tb))[1]["mappings"] == []
    assert _st(await call(app, "get", f"{NET}/mappings/{mid}", t)) == 200
    assert _st(await call(app, "get", f"{NET}/mappings/{mid}", tb)) == 404
    # graph
    org = w["a"]["org"]
    assert _st(await call(app, "get", f"{NET}/topology/graph", t)) == 400
    assert (
        _st(
            await call(
                app,
                "get",
                f"{NET}/topology/graph",
                tb,
                query_string={"organization_id": org},
            )
        )
        == 404
    )
    s, g = await call(
        app, "get", f"{NET}/topology/graph", t, query_string={"organization_id": org}
    )
    assert s == 200 and len(g["nodes"]) == 2 and len(g["edges"]) == 1
    s, g = await call(
        app,
        "get",
        f"{NET}/topology/graph",
        t,
        query_string={"organization_id": org, "include_entities": "true"},
    )
    assert s == 200
    # deletes
    assert _st(await call(app, "delete", f"{NET}/mappings/{mid}", tb)) == 404
    assert _st(await call(app, "delete", f"{NET}/mappings/{mid}", t)) == 200
    assert (
        _st(await call(app, "delete", f"{NET}/topology/connections/{cid}", tb)) == 404
    )
    assert _st(await call(app, "delete", f"{NET}/topology/connections/{cid}", t)) == 200


@pytest.mark.asyncio
async def test_networking_no_tenant_and_500(app, world):  # noqa: F811
    """Tenant-less tokens get 403 and service failures map to a generic 500."""
    w = world
    nt = make_token(app, 0, w["a"]["user"], RW)
    t = w["tok"]("a", RW)
    reqs = [
        ("get", f"{NET}/networks", {}),
        ("get", f"{NET}/networks/1", {}),
        (
            "post",
            f"{NET}/networks",
            {"json": {"organization_id": 1, "name": "n", "network_type": "v"}},
        ),
        ("put", f"{NET}/networks/1", {"json": {"name": "n"}}),
        ("delete", f"{NET}/networks/1", {}),
        ("get", f"{NET}/topology/connections", {}),
        ("get", f"{NET}/topology/connections/1", {}),
        (
            "post",
            f"{NET}/topology/connections",
            {
                "json": {
                    "source_network_id": 1,
                    "target_network_id": 2,
                    "connection_type": "p",
                }
            },
        ),
        ("delete", f"{NET}/topology/connections/1", {}),
        ("get", f"{NET}/mappings", {}),
        ("get", f"{NET}/mappings/1", {}),
        (
            "post",
            f"{NET}/mappings",
            {"json": {"network_id": 1, "entity_id": 2, "relationship_type": "m"}},
        ),
        ("delete", f"{NET}/mappings/1", {}),
        ("get", f"{NET}/topology/graph?organization_id=1", {}),
    ]
    for m, u, kw in reqs:
        s, _ = await call(app, m, u, nt, **kw)
        assert s in (401, 403), (m, u, s)
    boom = RuntimeError("boom")
    with patch.object(netmod, "NetworkingService") as svc:
        inst = svc.return_value
        for name in dir(inst):
            pass
        for meth in (
            "list_networks",
            "get_network",
            "create_or_update_network",
            "update_network",
            "delete_network",
            "list_topology_connections",
            "get_topology_connection",
            "create_topology_connection",
            "delete_topology_connection",
            "list_entity_mappings",
            "get_entity_mapping",
            "map_entity_to_network",
            "delete_entity_mapping",
            "get_network_topology_graph",
        ):
            getattr(inst, meth).side_effect = boom
        for m, u, kw in reqs:
            if "graph" in u:
                u = f"{NET}/topology/graph?organization_id={w['a']['org']}"
            s, d = await call(app, m, u, t, **kw)
            assert s == 500, (m, u, s)
            assert "boom" not in json.dumps(
                d if isinstance(d, (dict, list)) else str(d)
            )


def test_positive_ints_helper():
    """_positive_ints accepts positive ints only and rejects bools/strings/zero."""
    assert netmod._positive_ints({"a": 1, "b": 2}, ("a", "b")) is True
    assert netmod._positive_ints({"a": True}, ("a",)) is False
    assert netmod._positive_ints({"a": "1"}, ("a",)) is False
    assert netmod._positive_ints({"a": 0}, ("a",)) is False
    assert netmod._positive_ints({}, ("a",)) is False


@pytest.mark.asyncio
async def test_organizations_crud(app, world):  # noqa: F811
    """Organization create/list/get/update/delete/children/graph with isolation."""
    w = world
    t = w["tok"]("a", RW)
    tb = w["tok"]("b", RW)
    root = w["a"]["org"]
    assert _st(await call(app, "get", ORG)) == 401
    s, d = await call(
        app,
        "post",
        ORG,
        t,
        json={
            "name": f"child-{w['sfx']}",
            "parent_id": root,
            "description": "c",
            "organization_type": "department",
        },
    )
    assert s == 201, d
    child = d["id"]
    s, d = await call(
        app, "post", ORG, t, json={"name": f"grand-{w['sfx']}", "parent_id": child}
    )
    assert s == 201, d
    grand = d["id"]
    assert _st(await call(app, "post", ORG, t, json={})) == 400
    # list + filters
    s, d = await call(app, "get", ORG, t, query_string={"parent_id": root})
    assert s == 200 and d["total"] >= 1
    s, d = await call(app, "get", ORG, t, query_string={"name": "child"})
    assert s == 200 and d["total"] >= 1
    s, d = await call(app, "get", ORG, t, query_string={"search": "grand"})
    assert s == 200 and d["total"] >= 1
    # get
    assert _st(await call(app, "get", f"{ORG}/{child}", t)) == 200
    assert _st(await call(app, "get", f"{ORG}/{child}", tb)) == 404
    assert _st(await call(app, "get", f"{ORG}/99999999", t)) == 404
    # update
    assert (
        _st(await call(app, "put", f"{ORG}/{child}", tb, json={"description": "h"}))
        == 404
    )
    assert _st(await call(app, "put", f"{ORG}/{child}", t, json={})) == 400
    s, d = await call(
        app,
        "patch",
        f"{ORG}/{child}",
        t,
        json={"description": "new", "organization_type": "team"},
    )
    assert s == 200 and d["description"] == "new"
    # children
    s, d = await call(app, "get", f"{ORG}/{root}/children", t)
    assert s == 200 and [o["id"] for o in d] == [child]
    s, d = await call(
        app, "get", f"{ORG}/{root}/children", t, query_string={"recursive": "true"}
    )
    assert s == 200 and {o["id"] for o in d} == {child, grand}
    assert _st(await call(app, "get", f"{ORG}/{root}/children", tb)) == 404
    # graph
    assert _st(await call(app, "get", f"{ORG}/{root}/graph", tb)) == 404
    assert _st(await call(app, "get", f"{ORG}/99999999/graph", t)) == 404
    s, d = await call(app, "get", f"{ORG}/{child}/graph", t, query_string={"depth": 2})
    assert s == 200 and d["center_node"] == f"org-{child}"
    labels = {(e["from"], e["to"]) for e in d["edges"]}
    assert (f"org-{root}", f"org-{child}") in labels and (
        f"org-{child}",
        f"org-{grand}",
    ) in labels
    # delete
    assert _st(await call(app, "delete", f"{ORG}/{child}", t)) == 400
    assert _st(await call(app, "delete", f"{ORG}/{grand}", tb)) == 404
    assert _st(await call(app, "delete", f"{ORG}/{grand}", t)) == 204
    assert _st(await call(app, "delete", f"{ORG}/{child}", t)) == 204


@pytest.mark.xfail(
    strict=True,
    reason="bug: hard-deleting a network that still has children surfaces a foreign-key violation as a 500",
)
@pytest.mark.asyncio
async def test_network_hard_delete_with_children(app, world):  # noqa: F811
    """Hard-deleting a parent network with children should be a client error, not a 500."""
    w = world
    t = w["tok"]("a", RW)
    n1 = await _net(app, w, "hp-1")
    await _net(app, w, "hp-2", parent_id=n1)
    s, _ = await call(
        app, "delete", f"{NET}/networks/{n1}", t, query_string={"hard": "true"}
    )
    assert 400 <= s < 500


@pytest.mark.xfail(
    strict=True,
    reason="bug: org graph and tree-stats read entity.entity_type but the entities column is named type (500)",
)
@pytest.mark.asyncio
async def test_org_graph_entities_and_deps(app, world):  # noqa: F811
    """The org graph includes contained entities and entity-to-entity dependencies."""
    w = world
    t = w["tok"]("a", RW)
    e1 = await _entity(app, w, "g1")
    e2 = await _entity(app, w, "g2")
    s, dep = await call(
        app,
        "post",
        "/api/v1/dependencies",
        t,
        json={
            "source_type": "entity",
            "source_id": e1,
            "target_type": "entity",
            "target_id": e2,
            "dependency_type": "calls",
        },
    )
    assert s == 201, dep
    s, d = await call(app, "get", f"{ORG}/{w['a']['org']}/graph", t)
    assert s == 200
    assert {n["id"] for n in d["nodes"]} >= {f"entity-{e1}", f"entity-{e2}"}
    assert any(e["label"] == "calls" for e in d["edges"])


@pytest.mark.asyncio
async def test_org_graph_cross_tenant_parent(app, world):  # noqa: F811
    """A parent in another tenant halts the climb and never appears in the graph."""
    w = world

    def _mk():
        """Insert the fixture rows and return their id.
        Used only to set up this test's data."""
        n = datetime.now(UTC)
        db = app.db
        oid = db.organizations.insert(
            name=f"xp-{w['sfx']}",
            tenant_id=w["a"]["tenant"],
            parent_id=w["b"]["org"],
            created_at=n,
            updated_at=n,
        )
        db.commit()
        return oid

    oid = await in_db(_mk)
    s, d = await call(app, "get", f"{ORG}/{oid}/graph", w["tok"]("a", RW))
    assert s == 200
    assert all(n["id"] != f"org-{w['b']['org']}" for n in d["nodes"])


@pytest.mark.asyncio
async def test_tree_stats(app, world):  # noqa: F811
    """Tree-stats aggregates descendants and refuses foreign or missing orgs."""
    w = world
    t = w["tok"]("a", RW)
    tb = w["tok"]("b", RW)
    root = w["a"]["org"]
    s, d = await call(
        app, "post", ORG, t, json={"name": f"ts-{w['sfx']}", "parent_id": root}
    )
    assert s == 201, d
    assert (
        _st(await call(app, "get", f"/api/v1/organizations/{root}/tree-stats")) == 401
    )
    s, d = await call(app, "get", f"/api/v1/organizations/{root}/tree-stats", t)
    assert s == 200 and d["total_sub_organizations"] == 1 and d["total_entities"] == 0
    assert d["breakdown"] == {
        "by_entity_type": {},
        "by_issue_priority": {},
        "by_project_status": {},
    }
    assert (
        _st(await call(app, "get", f"/api/v1/organizations/{root}/tree-stats", tb))
        == 404
    )
    assert (
        _st(await call(app, "get", "/api/v1/organizations/99999999/tree-stats", t))
        == 404
    )
    from apps.api.modules.infrastructure.routes import organization_tree as ot

    with patch.object(ot, "get_tenant_scoped", side_effect=RuntimeError("boom")):
        s, d = await call(app, "get", f"/api/v1/organizations/{root}/tree-stats", t)
    assert s == 500 and "boom" not in json.dumps(d)


@pytest.mark.xfail(
    strict=True,
    reason="bug: tree-stats reads entity.entity_type but the entities column is named type (500)",
)
@pytest.mark.asyncio
async def test_tree_stats_with_entities(app, world):  # noqa: F811
    """Tree-stats counts entities by type once the org owns entities."""
    w = world
    await _entity(app, w, "ts-e")
    s, d = await call(
        app,
        "get",
        f"/api/v1/organizations/{w['a']['org']}/tree-stats",
        w["tok"]("a", RW),
    )
    assert s == 200 and d["breakdown"]["by_entity_type"] == {"compute": 1}


def test_descendant_cycle_guard():
    """The descendant walker stops on cycles and returns each org once."""
    from types import SimpleNamespace as NS

    from apps.api.modules.infrastructure.routes import organization_tree as ot

    children = {1: [NS(id=2)], 2: [NS(id=1)]}

    class _Q:
        """Query stub exposing ``select`` for a fixed parent id."""

        def __init__(self, pid):
            """Remember the parent id."""
            self.pid = pid

        def select(self):
            """Return the stub children."""
            return children.get(self.pid, [])

    class _Col:
        """Column stub whose equality yields a query for that parent."""

        def __eq__(self, other):
            """Return a query keyed by the compared id."""
            return _Q(other)

    class _Db:
        """Database stub: calling it with a query returns that query."""

        organizations = NS(parent_id=_Col())

        def __call__(self, q):
            """Return the query unchanged."""
            return q

    assert ot._get_all_descendant_orgs(_Db(), 1) == [1, 2]
