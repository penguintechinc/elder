"""Route tests for diagram CRUD, versions, shares and collections.

Real Quart app, JWTs carrying ``identity_id`` and real Postgres.  Cross-tenant
and non-owner access is asserted to be 404, never leaking existence.
"""

# flake8: noqa: E501

from datetime import UTC, datetime, timedelta

import jwt
import pytest
import pytest_asyncio

from tests.unit._cov_d_helpers import call, in_db, now, world  # noqa: F401

DG = "/api/v1/diagrams"
COL = "/api/v1/diagram-collections"
RW = ["diagrams:read", "diagrams:write"]
RO = ["diagrams:read"]


def tok(app, tenant, ident, scopes=RW, with_ident=True, roles=("admin",)):
    """Mint a diagrams JWT; ``with_ident`` controls the ``identity_id`` claim."""
    n = datetime.now(UTC)
    p = {
        "sub": str(ident),
        "type": "access",
        "iat": n,
        "exp": n + timedelta(hours=1),
        "scope": list(scopes),
        "tenant": str(tenant),
        "roles": list(roles),
        "teams": [],
    }
    if with_ident:
        p["identity_id"] = ident
    return jwt.encode(
        p,
        app.config.get("JWT_SECRET_KEY") or app.config.get("SECRET_KEY"),
        algorithm="HS256",
    )


@pytest_asyncio.fixture
async def dw(app, world):  # noqa: F811
    """World plus a second identity ('other') in tenant A and token factory ``T``."""
    db = app.db

    def _mk():
        """Insert the fixture rows and return their id.
        Used only to set up this test's data."""
        n = now()
        i = db.identities.insert(
            identity_type="human",
            username=f"o-{world['sfx']}",
            email=f"o-{world['sfx']}@example.com",
            full_name="Other",
            tenant_id=world["a"]["tenant"],
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
        return i

    world["other"] = await in_db(_mk)

    def T(who="a", scopes=RW, with_ident=True, roles=("admin",)):
        """Return a token for owner 'a', 'other' (tenant A) or 'b' (tenant B)."""
        if who == "a":
            return tok(
                app, world["a"]["tenant"], world["a"]["user"], scopes, with_ident, roles
            )
        if who == "other":
            return tok(
                app, world["a"]["tenant"], world["other"], scopes, with_ident, roles
            )
        return tok(
            app, world["b"]["tenant"], world["b"]["user"], scopes, with_ident, roles
        )

    world["T"] = T
    return world


async def _mk(app, w, who="a", **kw):
    """Create a diagram via the API and return its id."""
    s, d = await call(app, "post", DG, w["T"](who), json={"title": "D", **kw})
    assert s == 201, d
    return d["id"]


@pytest.mark.asyncio
async def test_diagram_crud(app, dw):
    """Create/list/get/update/delete with filters, visibility and isolation."""
    w = dw
    T = w["T"]
    assert (await call(app, "get", DG))[0] == 401
    assert (await call(app, "get", DG, T("a", ["x:y"])))[0] == 403
    assert (await call(app, "post", DG, T("a", RO), json={"title": "x"}))[0] == 403
    # validation
    assert (await call(app, "post", DG, T(), json={"title": "  "}))[0] == 400
    d1 = await _mk(
        app,
        w,
        title="Alpha",
        content={"nodes": [{"id": "n1"}], "edges": []},
        tags=["t"],
        status="active",
        description="desc",
    )
    d2 = await _mk(app, w, title="Beta", is_template=True, is_public=True)
    s, d = await call(app, "get", DG, T())
    assert s == 200 and d["pagination"]["total"] == 2
    for qs, n in [
        ({"status": "active"}, 1),
        ({"q": "Alp"}, 1),
        ({"is_template": "true"}, 1),
        ({"is_template": "false"}, 1),
        ({"page": 1, "per_page": 1}, 2),
    ]:
        s, d = await call(app, "get", DG, T(), query_string=qs)
        assert s == 200 and d["pagination"]["total"] == n, (qs, d)
    # 'other' (same tenant) only sees the public one in the listing
    s, d = await call(app, "get", DG, T("other"))
    assert [i["id"] for i in d["items"]] == [d2]
    assert (await call(app, "get", DG, T("b")))[1]["pagination"]["total"] == 0
    # get
    s, d = await call(app, "get", f"{DG}/{d1}", T())
    assert s == 200 and d["content"]["nodes"] == [{"id": "n1"}]
    s, d = await call(app, "get", f"{DG}/{d2}", T())
    assert d["content"] == {"nodes": [], "edges": []}
    assert (await call(app, "get", f"{DG}/{d1}", T("other")))[0] == 404
    assert (await call(app, "get", f"{DG}/{d2}", T("other")))[0] == 200
    assert (await call(app, "get", f"{DG}/{d1}", T("b")))[0] == 404
    assert (await call(app, "get", f"{DG}/99999999", T()))[0] == 404
    # update
    assert (await call(app, "patch", f"{DG}/{d1}", T("other"), json={"title": "h"}))[
        0
    ] == 404
    assert (await call(app, "patch", f"{DG}/{d2}", T("other"), json={"title": "h"}))[
        0
    ] == 404
    assert (await call(app, "patch", f"{DG}/99999999", T(), json={"title": "h"}))[
        0
    ] == 404
    s, d = await call(
        app,
        "patch",
        f"{DG}/{d1}",
        T(),
        json={
            "title": "A2",
            "description": "n",
            "status": "archived",
            "tags": ["x"],
            "is_public": True,
            "is_template": True,
            "thumbnail_url": "http://x/y.png",
        },
    )
    assert s == 200 and d["title"] == "A2" and d["is_public"] is True
    # delete
    assert (await call(app, "delete", f"{DG}/{d1}", T("other")))[0] == 404
    assert (await call(app, "delete", f"{DG}/99999999", T()))[0] == 404
    assert (await call(app, "delete", f"{DG}/{d1}", T()))[0] == 204
    assert (await call(app, "get", f"{DG}/{d1}", T()))[0] == 404


@pytest.mark.asyncio
async def test_versions(app, dw):
    """Version save/list/get/restore with validation and permission checks."""
    w = dw
    T = w["T"]
    d = await _mk(app, w)
    assert (await call(app, "post", f"{DG}/{d}/versions", T(), json={}))[0] == 400
    assert (
        await call(app, "post", f"{DG}/99999999/versions", T(), json={"content": {}})
    )[0] == 404
    assert (
        await call(app, "post", f"{DG}/{d}/versions", T("other"), json={"content": {}})
    )[0] == 404
    s, v = await call(
        app,
        "post",
        f"{DG}/{d}/versions",
        T(),
        json={"content": {"nodes": [1]}, "change_summary": "first"},
    )
    assert s == 201 and v["version_number"] == 1
    s, v = await call(
        app, "post", f"{DG}/{d}/versions", T(), json={"content": {"nodes": [2]}}
    )
    assert v["version_number"] == 2
    s, d_ = await call(
        app, "get", f"{DG}/{d}/versions", T(), query_string={"per_page": 1}
    )
    assert s == 200 and d_["pagination"]["total"] == 2 and len(d_["versions"]) == 1
    assert (await call(app, "get", f"{DG}/99999999/versions", T()))[0] == 404
    assert (await call(app, "get", f"{DG}/{d}/versions", T("other")))[0] == 404
    s, v = await call(app, "get", f"{DG}/{d}/versions/1", T())
    assert s == 200 and v["content"] == {"nodes": [1]}
    assert (await call(app, "get", f"{DG}/{d}/versions/9", T()))[0] == 404
    assert (await call(app, "get", f"{DG}/99999999/versions/1", T()))[0] == 404
    assert (await call(app, "get", f"{DG}/{d}/versions/1", T("other")))[0] == 404
    # restore
    assert (await call(app, "post", f"{DG}/99999999/versions/1/restore", T()))[0] == 404
    assert (await call(app, "post", f"{DG}/{d}/versions/1/restore", T("other")))[
        0
    ] == 404
    assert (await call(app, "post", f"{DG}/{d}/versions/9/restore", T()))[0] == 404
    s, r = await call(app, "post", f"{DG}/{d}/versions/1/restore", T())
    assert s == 200 and r["new_version_number"] == 3 and r["restored_from_version"] == 1
    s, v = await call(app, "get", f"{DG}/{d}", T())
    assert v["content"] == {"nodes": [1]}


@pytest.mark.asyncio
async def test_shares(app, dw):
    """Share create/list/revoke and the public share-token endpoint."""
    w = dw
    T = w["T"]
    d = await _mk(app, w, content={"nodes": [], "edges": []})
    assert (await call(app, "post", f"{DG}/99999999/shares", T(), json={}))[0] == 404
    assert (await call(app, "post", f"{DG}/{d}/shares", T("other"), json={}))[0] == 404
    s, sh = await call(
        app,
        "post",
        f"{DG}/{d}/shares",
        T(),
        json={"shared_with_identity_id": w["other"], "permission": "editor"},
    )
    assert s == 201 and sh["share_token"] is None
    s, pub = await call(
        app,
        "post",
        f"{DG}/{d}/shares",
        T(),
        json={
            "is_public": True,
            "permission": "viewer",
            "expires_at": (now() + timedelta(days=1)).isoformat(),
        },
    )
    assert s == 201 and pub["share_token"] and pub["expires_at"]
    # editor share lets 'other' edit and see shares
    assert (await call(app, "get", f"{DG}/{d}", T("other")))[0] == 200
    assert (
        await call(app, "patch", f"{DG}/{d}", T("other"), json={"title": "by-editor"})
    )[0] == 200
    s, lst = await call(
        app, "get", f"{DG}/{d}/shares", T("other"), query_string={"per_page": 5}
    )
    assert s == 200 and lst["pagination"]["total"] == 2
    assert (await call(app, "get", f"{DG}/99999999/shares", T()))[0] == 404
    assert (await call(app, "get", f"{DG}/{d}/shares", T("b")))[0] == 404
    d_priv = await _mk(app, w, title="priv")
    assert (await call(app, "get", f"{DG}/{d_priv}/shares", T("other")))[0] == 404
    # public token
    s, body = await call(app, "get", f"{DG}/shared/{pub['share_token']}")
    assert s == 200 and body["id"] == d
    assert (await call(app, "get", f"{DG}/shared/nope-token"))[0] == 404
    # expired share
    s, exp = await call(
        app,
        "post",
        f"{DG}/{d}/shares",
        T(),
        json={"is_public": True, "expires_at": (now() - timedelta(days=1)).isoformat()},
    )
    assert s == 201
    assert (await call(app, "get", f"{DG}/shared/{exp['share_token']}"))[0] == 404
    # revoke
    assert (await call(app, "delete", f"{DG}/{d}/shares/99999999", T()))[0] == 404
    assert (await call(app, "delete", f"{DG}/{d}/shares/{pub['id']}", T("b")))[0] == 404
    assert (await call(app, "delete", f"{DG}/{d}/shares/{sh['id']}", T("other")))[
        0
    ] == 204
    assert (await call(app, "delete", f"{DG}/{d}/shares/{pub['id']}", T()))[0] == 204


@pytest.mark.asyncio
async def test_public_share_of_deleted_diagram(app, dw):
    """A public share whose diagram vanished resolves to 404."""
    w = dw
    d = await _mk(app, w)
    s, sh = await call(
        app, "post", f"{DG}/{d}/shares", w["T"](), json={"is_public": True}
    )
    assert s == 201

    def _drop():
        """Delete the diagram row directly.
        Used only to set up this test's data."""
        app.db(app.db.dg_diagrams.id == d).delete()
        app.db.commit()

    try:
        await in_db(_drop)
    except Exception:
        pytest.skip("diagram row is protected by a foreign key from shares")
    assert (await call(app, "get", f"{DG}/shared/{sh['share_token']}"))[0] == 404


@pytest.mark.asyncio
async def test_collections(app, dw):
    """Collection CRUD, item management, visibility and isolation."""
    w = dw
    T = w["T"]
    assert (await call(app, "get", COL))[0] == 401
    assert (await call(app, "post", COL, T(), json={"name": " "}))[0] == 400
    s, c = await call(
        app,
        "post",
        COL,
        T(),
        json={"name": "C1", "thumbnail_url": "http://x", "share_mode": "private"},
    )
    assert s == 201, c
    cid = c["id"]
    s, c2 = await call(app, "post", COL, T(), json={"name": "Pub", "is_public": True})
    assert s == 201
    s, d = await call(app, "get", COL, T(), query_string={"per_page": 5})
    assert s == 200 and d["pagination"]["total"] == 2
    s, d = await call(app, "get", COL, T("other"))
    assert [i["id"] for i in d["items"]] == [c2["id"]]
    assert (await call(app, "get", COL, T("b")))[1]["pagination"]["total"] == 0
    # get
    assert (await call(app, "get", f"{COL}/99999999", T()))[0] == 404
    assert (await call(app, "get", f"{COL}/{cid}", T("other")))[0] == 404
    assert (await call(app, "get", f"{COL}/{cid}", T("b")))[0] == 404
    # items
    dg = await _mk(app, w, title="InCol")
    dpriv = await _mk(app, w, title="PrivOwnerOnly")
    assert (await call(app, "post", f"{COL}/{cid}/items", T(), json={}))[0] == 400
    assert (
        await call(app, "post", f"{COL}/99999999/items", T(), json={"diagram_id": dg})
    )[0] == 404
    assert (
        await call(
            app, "post", f"{COL}/{cid}/items", T("other"), json={"diagram_id": dg}
        )
    )[0] == 404
    assert (
        await call(
            app, "post", f"{COL}/{cid}/items", T(), json={"diagram_id": 99999999}
        )
    )[0] == 404
    s, it = await call(app, "post", f"{COL}/{cid}/items", T(), json={"diagram_id": dg})
    assert s == 201 and it["order_index"] == 0
    assert (
        await call(app, "post", f"{COL}/{cid}/items", T(), json={"diagram_id": dg})
    )[0] == 409
    s, it = await call(
        app, "post", f"{COL}/{cid}/items", T(), json={"diagram_id": dpriv}
    )
    assert s == 201 and it["order_index"] == 1
    s, d = await call(app, "get", f"{COL}/{cid}", T())
    assert s == 200 and [i["diagram_id"] for i in d["items"]] == [dg, dpriv]
    # 'other' owns a collection and tries to add a diagram they cannot read
    s, oc = await call(app, "post", COL, T("other"), json={"name": "OC"})
    assert (
        await call(
            app,
            "post",
            f"{COL}/{oc['id']}/items",
            T("other"),
            json={"diagram_id": dpriv},
        )
    )[0] == 404
    # remove item
    assert (await call(app, "delete", f"{COL}/99999999/items/{dg}", T()))[0] == 404
    assert (await call(app, "delete", f"{COL}/{cid}/items/{dg}", T("other")))[0] == 404
    assert (await call(app, "delete", f"{COL}/{cid}/items/99999999", T()))[0] == 404
    assert (await call(app, "delete", f"{COL}/{cid}/items/{dg}", T()))[0] == 204
    # update
    assert (await call(app, "patch", f"{COL}/99999999", T(), json={"name": "x"}))[
        0
    ] == 404
    assert (await call(app, "patch", f"{COL}/{cid}", T("other"), json={"name": "x"}))[
        0
    ] == 404
    s, u = await call(
        app,
        "patch",
        f"{COL}/{cid}",
        T(),
        json={
            "name": "C1b",
            "thumbnail_url": "u",
            "is_public": True,
            "share_mode": "link",
        },
    )
    assert s == 200 and u["name"] == "C1b" and u["is_public"] is True
    # delete
    assert (await call(app, "delete", f"{COL}/99999999", T()))[0] == 404
    assert (await call(app, "delete", f"{COL}/{cid}", T("other")))[0] == 404
    assert (await call(app, "delete", f"{COL}/{cid}", T()))[0] == 204
    assert (await call(app, "get", f"{COL}/{cid}", T()))[0] == 404


@pytest.mark.asyncio
async def test_collection_shared_visibility(app, dw):
    """A collection shared to an identity becomes readable by that identity."""
    w = dw
    T = w["T"]
    s, c = await call(app, "post", COL, T(), json={"name": "Shared"})
    cid = c["id"]
    assert (await call(app, "get", f"{COL}/{cid}", T("other")))[0] == 404

    def _share():
        """Insert a share row granting access to the second identity.
        Used only to set up this test's data."""
        n = now()
        app.db.dg_collection_shares.insert(
            tenant_id=w["a"]["tenant"],
            collection_id=cid,
            shared_with_identity_id=w["other"],
            permission="viewer",
            created_at=n,
            updated_at=n,
        )
        app.db.commit()

    await in_db(_share)
    assert (await call(app, "get", f"{COL}/{cid}", T("other")))[0] == 200
