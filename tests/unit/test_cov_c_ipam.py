"""HTTP-level behaviour tests for the IPAM prefix, address and VLAN routes.

Requests run through the real Quart app against the Postgres test database
with signed tokens, so validation, overwrite semantics, tenant scoping and the
licence-limit refusal are asserted on responses and stored rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from apps.api.modules.ipam import routes as ipam_routes
from tests.unit._cov_c_helpers import call, mk_identity, mk_org, mk_tenant, sfx, token

pytestmark = pytest.mark.asyncio
BASE = "/api/v1/ipam"
SC = ["ipam:read", "ipam:write"]


@dataclass
class IW:
    """Seeded tenant/org/identity world for IPAM tests."""

    app: Any
    db: Any
    tid: int
    org: int
    org2: int
    foreign_org: int
    tok: str
    foreign: str
    notenant: str


@pytest.fixture(scope="module")
def iw(app) -> IW:
    """Seed a tenant with two orgs plus a foreign tenant org."""
    db = app.db
    tid, other = mk_tenant(db, "ip"), mk_tenant(db, "ipx")
    uid = mk_identity(db, tid, "admin", superuser=True)
    fid = mk_identity(db, other, "admin", superuser=True)
    return IW(
        app,
        db,
        tid,
        mk_org(db, tid),
        mk_org(db, tid),
        mk_org(db, other),
        token(app, uid, tid, SC),
        token(app, fid, other, SC),
        token(app, uid, "", SC),
    )


def cidr() -> str:
    """Return a random-ish unique /24 so rows from other tests never collide."""
    n = int(sfx(), 16)
    return f"10.{(n >> 8) % 250}.{n % 250}.0/24"


async def test_missing_tenant_is_403_everywhere(iw):
    """A token with no tenant claim is refused by every IPAM handler that checks it."""
    cases = [
        ("get", "/prefixes"),
        ("post", "/prefixes"),
        ("get", "/prefixes/1/tree"),
        ("put", "/prefixes/1"),
        ("delete", "/prefixes/1"),
        ("get", "/addresses"),
        ("post", "/addresses"),
        ("get", "/addresses/1"),
        ("put", "/addresses/1"),
        ("delete", "/addresses/1"),
        ("get", "/vlans"),
        ("post", "/vlans"),
        ("put", "/vlans/1"),
        ("delete", "/vlans/1"),
    ]
    bodies = {
        "post/prefixes": {"prefix": "10.0.0.0/8", "organization_id": 1},
        "post/addresses": {"address": "10.0.0.1/8", "prefix_id": 1},
        "post/vlans": {"vid": 5, "name": "v", "organization_id": 1},
    }
    for method, path in cases:
        kw = (
            {"json": bodies.get(f"{method}{path}", {"description": "x"})}
            if method in ("post", "put")
            else {}
        )
        st, js = await call(iw.app, method, BASE + path, iw.notenant, **kw)
        assert st == 403, (method, path, st, js)


async def test_prefix_lifecycle_and_filters(iw):
    """Create, list with every filter, read, tree, update and delete a prefix."""
    parent = cidr()
    st, p = await call(
        iw.app,
        "post",
        f"{BASE}/prefixes",
        iw.tok,
        json={
            "prefix": parent,
            "organization_id": iw.org,
            "description": "parent pool",
            "is_pool": True,
        },
    )
    assert st == 201, p
    st, c = await call(
        iw.app,
        "post",
        f"{BASE}/prefixes",
        iw.tok,
        json={"prefix": cidr(), "organization_id": iw.org, "parent_id": p["id"]},
    )
    assert st == 201
    for q in (
        f"organization_id={iw.org}",
        "status=active",
        f"parent_id={p['id']}",
        "search=parent",
    ):
        st, js = await call(iw.app, "get", f"{BASE}/prefixes?{q}", iw.tok)
        assert st == 200 and js["total"] >= 1, q
    st, js = await call(
        iw.app, "get", f"{BASE}/prefixes?parent_id={p['id']}&status=active", iw.tok
    )
    assert [i["id"] for i in js["items"]] == [c["id"]]
    st, js = await call(iw.app, "get", f"{BASE}/prefixes/{p['id']}/tree", iw.tok)
    assert st == 200 and js["children"][0]["id"] == c["id"]
    assert (await call(iw.app, "get", f"{BASE}/prefixes/{p['id']}", iw.tok))[0] == 200
    assert (await call(iw.app, "get", f"{BASE}/prefixes/{p['id']}", iw.foreign))[
        0
    ] == 404
    assert (await call(iw.app, "get", f"{BASE}/prefixes/999999999/tree", iw.tok))[
        0
    ] == 404
    new_cidr = cidr()
    st, js = await call(
        iw.app,
        "put",
        f"{BASE}/prefixes/{c['id']}",
        iw.tok,
        json={
            "prefix": new_cidr,
            "description": "d2",
            "status": "reserved",
            "parent_id": p["id"],
            "is_pool": True,
            "organization_id": iw.org2,
        },
    )
    assert (
        st == 200
        and js["prefix"] == new_cidr
        and js["status"] == "reserved"
        and js["organization_id"] == iw.org2
    )
    row = iw.db(iw.db.ipam_prefixes.id == c["id"]).select().first()
    assert row.is_pool and row.organization_id == iw.org2
    assert (
        await call(
            iw.app,
            "put",
            f"{BASE}/prefixes/999999999",
            iw.tok,
            json={"description": "x"},
        )
    )[0] == 404
    assert (await call(iw.app, "delete", f"{BASE}/prefixes/{c['id']}", iw.foreign))[
        0
    ] == 404
    assert (await call(iw.app, "delete", f"{BASE}/prefixes/{c['id']}", iw.tok))[0] in (
        200,
        204,
    )
    assert iw.db(iw.db.ipam_prefixes.id == c["id"]).count() == 0


async def test_prefix_create_validation_overwrite_and_limit(iw, monkeypatch):
    """Bad bodies, foreign refs, duplicates (409/overwrite) and licence limits."""
    assert (
        await call(iw.app, "post", f"{BASE}/prefixes", iw.tok, json={"prefix": "bogus"})
    )[0] == 400
    body = {"prefix": cidr(), "organization_id": iw.org}
    assert (
        await call(
            iw.app,
            "post",
            f"{BASE}/prefixes",
            iw.tok,
            json={**body, "organization_id": iw.foreign_org},
        )
    )[0] == 404
    assert (
        await call(
            iw.app,
            "post",
            f"{BASE}/prefixes",
            iw.tok,
            json={**body, "parent_id": 999999999},
        )
    )[0] == 404
    assert (
        await call(
            iw.app,
            "post",
            f"{BASE}/prefixes",
            iw.tok,
            json={**body, "vlan_id": 999999999},
        )
    )[0] == 404
    st, first = await call(iw.app, "post", f"{BASE}/prefixes", iw.tok, json=body)
    assert st == 201
    assert (await call(iw.app, "post", f"{BASE}/prefixes", iw.tok, json=body))[0] == 409
    st, js = await call(
        iw.app,
        "post",
        f"{BASE}/prefixes",
        iw.tok,
        json={**body, "overwrite": True, "description": "replaced"},
    )
    assert st == 200 and js["id"] == first["id"] and js["description"] == "replaced"

    async def deny(kind, tenant_id):
        """Pretend the licence limit has been reached."""
        return ipam_routes.jsonify({"error": "limit reached"}), 402

    monkeypatch.setattr(ipam_routes, "check_limit", deny)
    st, js = await call(
        iw.app,
        "post",
        f"{BASE}/prefixes",
        iw.tok,
        json={"prefix": cidr(), "organization_id": iw.org},
    )
    assert st == 402 and js["error"] == "limit reached"


async def test_address_lifecycle(iw, monkeypatch):
    """Addresses: create/list/filter/read/update/overwrite/conflict/delete and limit refusal."""
    st, pfx = await call(
        iw.app,
        "post",
        f"{BASE}/prefixes",
        iw.tok,
        json={"prefix": cidr(), "organization_id": iw.org},
    )
    pid = pfx["id"]
    addr = f"10.250.{int(sfx(), 16) % 250}.7/24"
    st, a = await call(
        iw.app,
        "post",
        f"{BASE}/addresses",
        iw.tok,
        json={"address": addr, "prefix_id": pid, "dns_name": "h.example.test"},
    )
    assert st == 201, a
    for q in (f"prefix_id={pid}", "status=active", "search=10.250"):
        st, js = await call(iw.app, "get", f"{BASE}/addresses?{q}", iw.tok)
        assert st == 200 and js["total"] >= 1, q
    assert (await call(iw.app, "get", f"{BASE}/addresses/{a['id']}", iw.tok))[0] == 200
    assert (await call(iw.app, "get", f"{BASE}/addresses/{a['id']}", iw.foreign))[
        0
    ] == 404
    assert (
        await call(
            iw.app,
            "post",
            f"{BASE}/addresses",
            iw.tok,
            json={"address": addr, "prefix_id": pid},
        )
    )[0] == 409
    st, js = await call(
        iw.app,
        "post",
        f"{BASE}/addresses",
        iw.tok,
        json={
            "address": addr,
            "prefix_id": pid,
            "overwrite": True,
            "description": "again",
        },
    )
    assert st == 200 and js["id"] == a["id"]
    assert (
        await call(
            iw.app,
            "post",
            f"{BASE}/addresses",
            iw.tok,
            json={"address": addr, "prefix_id": 999999999},
        )
    )[0] == 404
    st, js = await call(
        iw.app,
        "put",
        f"{BASE}/addresses/{a['id']}",
        iw.tok,
        json={
            "address": "10.1.2.3/24",
            "description": "u",
            "status": "reserved",
            "prefix_id": pid,
            "dns_name": "z.example.test",
        },
    )
    assert (
        st == 200
        and js["address"] == "10.1.2.3/24"
        and js["dns_name"] == "z.example.test"
    )
    assert (
        await call(
            iw.app,
            "put",
            f"{BASE}/addresses/999999999",
            iw.tok,
            json={"description": "x"},
        )
    )[0] == 404
    assert (await call(iw.app, "delete", f"{BASE}/addresses/{a['id']}", iw.foreign))[
        0
    ] == 404

    async def deny(kind, tenant_id):
        """Pretend the licence limit has been reached."""
        return ipam_routes.jsonify({"error": "limit"}), 402

    monkeypatch.setattr(ipam_routes, "check_limit", deny)
    st, _ = await call(
        iw.app,
        "post",
        f"{BASE}/addresses",
        iw.tok,
        json={"address": "10.9.9.9/24", "prefix_id": pid},
    )
    assert st == 402
    monkeypatch.undo()
    assert (await call(iw.app, "delete", f"{BASE}/addresses/{a['id']}", iw.tok))[0] in (
        200,
        204,
    )
    assert iw.db(iw.db.ipam_addresses.id == a["id"]).count() == 0


async def test_vlan_lifecycle(iw, monkeypatch):
    """VLANs: create/list/filter/read/update/overwrite/conflict/delete and limit refusal."""
    vid = int(sfx(), 16) % 4000 + 10
    st, v = await call(
        iw.app,
        "post",
        f"{BASE}/vlans",
        iw.tok,
        json={"vid": vid, "name": "net", "organization_id": iw.org},
    )
    assert st == 201, v
    for q in (f"organization_id={iw.org}", "status=active"):
        st, js = await call(iw.app, "get", f"{BASE}/vlans?{q}", iw.tok)
        assert st == 200 and js["total"] >= 1
    assert (await call(iw.app, "get", f"{BASE}/vlans/{v['id']}", iw.tok))[0] == 200
    assert (await call(iw.app, "get", f"{BASE}/vlans/{v['id']}", iw.foreign))[0] == 404
    assert (
        await call(
            iw.app,
            "post",
            f"{BASE}/vlans",
            iw.tok,
            json={"vid": vid, "name": "net", "organization_id": iw.org},
        )
    )[0] == 409
    st, js = await call(
        iw.app,
        "post",
        f"{BASE}/vlans",
        iw.tok,
        json={
            "vid": vid,
            "name": "renamed",
            "organization_id": iw.org,
            "overwrite": True,
        },
    )
    assert st == 200 and js["name"] == "renamed"
    assert (
        await call(
            iw.app,
            "post",
            f"{BASE}/vlans",
            iw.tok,
            json={"vid": 99999, "name": "x", "organization_id": iw.org},
        )
    )[0] == 400
    assert (
        await call(
            iw.app,
            "post",
            f"{BASE}/vlans",
            iw.tok,
            json={"vid": 7, "name": "x", "organization_id": iw.foreign_org},
        )
    )[0] == 404
    st, js = await call(
        iw.app,
        "put",
        f"{BASE}/vlans/{v['id']}",
        iw.tok,
        json={
            "vid": vid + 1,
            "name": "n2",
            "description": "d",
            "status": "reserved",
            "organization_id": iw.org2,
        },
    )
    assert st == 200 and js["vid"] == vid + 1 and js["organization_id"] == iw.org2
    assert (await call(iw.app, "put", f"{BASE}/vlans/{v['id']}", iw.tok))[0] in (
        400,
        415,
    )
    assert (
        await call(
            iw.app,
            "put",
            f"{BASE}/vlans/{v['id']}",
            iw.tok,
            json={"organization_id": "x"},
        )
    )[0] == 400
    assert (
        await call(
            iw.app,
            "put",
            f"{BASE}/vlans/{v['id']}",
            iw.tok,
            json={"organization_id": iw.foreign_org},
        )
    )[0] == 404
    assert (
        await call(iw.app, "put", f"{BASE}/vlans/999999999", iw.tok, json={"name": "x"})
    )[0] == 404
    assert (await call(iw.app, "delete", f"{BASE}/vlans/{v['id']}", iw.foreign))[
        0
    ] == 404

    async def deny(kind, tenant_id):
        """Pretend the licence limit has been reached."""
        return ipam_routes.jsonify({"error": "limit"}), 402

    monkeypatch.setattr(ipam_routes, "check_limit", deny)
    st, _ = await call(
        iw.app,
        "post",
        f"{BASE}/vlans",
        iw.tok,
        json={"vid": 4001, "name": "q", "organization_id": iw.org},
    )
    assert st == 402
    monkeypatch.undo()
    assert (await call(iw.app, "delete", f"{BASE}/vlans/{v['id']}", iw.tok))[0] in (
        200,
        204,
    )


async def test_scoped_helper_none_id(iw):
    """The tenant-scoping helper treats a missing id as not found."""
    assert ipam_routes._scoped(iw.db, iw.db.organizations, None, iw.tid) is None


@pytest.mark.xfail(
    strict=True,
    reason="bug: UpdateIPAMPrefixRequest does not validate CIDR notation, unlike the create request",
)
async def test_prefix_update_rejects_invalid_cidr(iw):
    """Updating a prefix to a non-CIDR string should be a 400 like create."""
    st, p = await call(
        iw.app,
        "post",
        f"{BASE}/prefixes",
        iw.tok,
        json={"prefix": cidr(), "organization_id": iw.org},
    )
    assert (
        await call(
            iw.app,
            "put",
            f"{BASE}/prefixes/{p['id']}",
            iw.tok,
            json={"prefix": "not-a-cidr"},
        )
    )[0] == 400


@pytest.mark.xfail(
    strict=True,
    reason="bug: address create/update requests do not validate IP address syntax",
)
async def test_address_rejects_invalid_ip(iw):
    """A non-IP address string should be rejected with 400."""
    st, pfx = await call(
        iw.app,
        "post",
        f"{BASE}/prefixes",
        iw.tok,
        json={"prefix": cidr(), "organization_id": iw.org},
    )
    st, _ = await call(
        iw.app,
        "post",
        f"{BASE}/addresses",
        iw.tok,
        json={"address": "nope", "prefix_id": pfx["id"]},
    )
    assert st == 400
