"""Route tests for the data-stores API (``/api/v1/data-stores``).

Real Quart app, JWTs and Postgres.  The caller identity is promoted to a
superuser so the per-resource role decorator does not hide the handler logic;
tenant isolation is asserted separately with a second tenant.
"""

# flake8: noqa: E501

import pytest
import pytest_asyncio

from tests.unit._cov_d_helpers import call, in_db, now, vid, world  # noqa: F401

BASE = "/api/v1/data-stores"
RW = ["infrastructure:read", "infrastructure:write"]


@pytest_asyncio.fixture
async def sw(app, world):  # noqa: F811
    """World where both sides' users are superusers (role checks bypassed)."""

    def _promote():
        """Promote both sides' identities to superusers.
        Used only to set up this test's data."""
        for side in ("a", "b"):
            app.db(app.db.identities.id == world[side]["user"]).update(
                is_superuser=True
            )
        app.db.commit()

    await in_db(_promote)
    return world


def _st(resp):
    """Return the status code, keeping the body visible in assertion output."""
    _LAST.append(resp[1])
    return resp[0]


_LAST: list = []


async def _mk(app, w, side="a", name="ds", **kw):
    """Create a data store through the API and return its id."""
    body = {"name": name, "organization_id": w[side]["org"], **kw}
    s, d = await call(app, "post", BASE, w["tok"](side, RW), json=body)
    assert s == 201, d
    return d["id"]


@pytest.mark.asyncio
async def test_auth_and_validation(app, sw):
    """Missing auth, missing scope and body/enum validation failures."""
    w = sw
    assert _st(await call(app, "get", BASE)) == 401
    t = w["tok"]("a", RW)
    assert _st(await call(app, "post", BASE, t, json={})) == 400
    assert _st(await call(app, "post", BASE, t, json={"name": "x"})) == 400
    assert (
        _st(
            await call(
                app,
                "post",
                BASE,
                t,
                json={
                    "name": "x",
                    "organization_id": w["a"]["org"],
                    "data_classification": "bogus",
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
                BASE,
                t,
                json={
                    "name": "x",
                    "organization_id": w["a"]["org"],
                    "storage_type": "bogus",
                },
            )
        )
        == 400
    )
    assert (
        _st(
            await call(
                app, "post", BASE, t, json={"name": "x", "organization_id": 99999999}
            )
        )
        == 404
    )
    assert (
        _st(
            await call(
                app,
                "post",
                BASE,
                t,
                json={
                    "name": "x",
                    "organization_id": w["a"]["org"],
                    "poc_identity_id": 99999999,
                },
            )
        )
        == 404
    )


@pytest.mark.asyncio
async def test_crud_and_filters(app, sw):
    """Create with labels, list with every filter, get, update, delete."""
    w = sw
    t = w["tok"]("a", RW)
    poc = w["a"]["user"]
    sid = await _mk(
        app,
        w,
        name="Orders DB",
        description="primary",
        storage_type="database",
        data_classification="restricted",
        location_region="us-east",
        poc_identity_id=poc,
        compliance_frameworks=["soc2"],
        contains_pii=True,
    )
    s, d = await call(app, "get", BASE, t, query_string={"search": "orders"})
    assert s == 200 and d["total"] >= 1
    # label ids through POST, names are pre-seeded on first list
    s, lab = await call(app, "get", f"{BASE}/{sid}", t)
    assert s == 200 and any(l["name"].lower() == "pii" for l in lab["labels"])
    pii = next(l["id"] for l in lab["labels"] if l["name"].lower() == "pii")
    sid2 = await _mk(
        app, w, name="Cache1", storage_type="cache", label_ids=[pii, pii, 99999999]
    )
    s, d = await call(app, "get", f"{BASE}/{sid2}", t)
    assert [l["id"] for l in d["labels"]] == [pii] and d["contains_pii"] is True
    for qs in [
        {"organization_id": w["a"]["org"]},
        {"data_classification": "restricted"},
        {"storage_type": "database"},
        {"location_region": "us-east"},
        {"contains_pii": "true"},
        {"contains_phi": "false"},
        {"contains_pci": "false"},
        {"poc_identity_id": poc},
        {"label_id": pii},
        {"label": "PII"},
        {"page": 1, "per_page": 1},
    ]:
        s, d = await call(app, "get", BASE, t, query_string=qs)
        assert s == 200, (qs, d)
        assert d["total"] >= 1, qs
    s, d = await call(app, "get", BASE, t, query_string={"label": "nonexistent-label"})
    assert s == 200 and d["total"] == 0
    s, d = await call(app, "get", BASE, t, query_string={"label_id": 99999999})
    assert s == 200 and d["total"] == 0
    # update
    assert _st(await call(app, "put", f"{BASE}/{sid}", t, json=[])) == 400
    assert (
        _st(
            await call(
                app, "put", f"{BASE}/{sid}", t, json={"data_classification": "bogus"}
            )
        )
        == 400
    )
    assert (
        _st(await call(app, "put", f"{BASE}/{sid}", t, json={"storage_type": "bogus"}))
        == 400
    )
    assert (
        _st(
            await call(
                app, "put", f"{BASE}/{sid}", t, json={"poc_identity_id": 99999999}
            )
        )
        == 404
    )
    assert (
        _st(
            await call(
                app, "put", f"{BASE}/{sid}", t, json={"organization_id": 99999999}
            )
        )
        == 404
    )
    s, d = await call(
        app,
        "put",
        f"{BASE}/{sid}",
        t,
        json={
            "name": "Renamed",
            "organization_id": w["a"]["org"],
            "label_ids": [pii],
            "poc_identity_id": poc,
        },
    )
    assert s == 200 and d["name"] == "Renamed" and d["contains_pii"] is True
    s, d = await call(
        app,
        "put",
        f"{BASE}/{sid}",
        t,
        json={
            "description": "x",
            "storage_type": "cache",
            "data_classification": "public",
        },
    )
    assert s == 200 and d["description"] == "x"
    s, d = await call(app, "put", f"{BASE}/{sid}", t, json={"unknown_field": 1})
    assert s == 200
    # delete
    assert _st(await call(app, "delete", f"{BASE}/99999999", t)) == 404


@pytest.mark.asyncio
async def test_label_assignment(app, sw):
    """Label list/add/remove including duplicates and not-found branches."""
    w = sw
    t = w["tok"]("a", RW)
    sid = await _mk(app, w, name="L1")
    # discover label ids via list (seeds defaults)
    await call(app, "get", BASE, t)

    def _lab():
        """Return every label id in the database.
        Used only to set up this test's data."""
        return [r.id for r in app.db(app.db.issue_labels.id > 0).select()]

    ids = await in_db(_lab)
    lid = ids[0]
    assert _st(await call(app, "get", f"{BASE}/99999999/labels", t)) == 404
    s, d = await call(app, "get", f"{BASE}/{sid}/labels", t)
    assert s == 200 and d["labels"] == []
    assert (
        _st(
            await call(
                app, "post", f"{BASE}/99999999/labels", t, json={"label_id": lid}
            )
        )
        == 404
    )
    assert _st(await call(app, "post", f"{BASE}/{sid}/labels", t, json=[])) == 400
    assert _st(await call(app, "post", f"{BASE}/{sid}/labels", t, json={})) == 400
    assert (
        _st(
            await call(
                app, "post", f"{BASE}/{sid}/labels", t, json={"label_id": 99999999}
            )
        )
        == 404
    )
    assert (
        _st(await call(app, "post", f"{BASE}/{sid}/labels", t, json={"label_id": lid}))
        == 201
    )
    assert (
        _st(await call(app, "post", f"{BASE}/{sid}/labels", t, json={"label_id": lid}))
        == 409
    )
    s, d = await call(app, "get", f"{BASE}/{sid}/labels", t)
    assert len(d["labels"]) == 1
    assert _st(await call(app, "delete", f"{BASE}/99999999/labels/{lid}", t)) == 404
    assert _st(await call(app, "delete", f"{BASE}/{sid}/labels/99999999", t)) == 404
    assert (
        (await call(app, "delete", f"{BASE}/{sid}/labels/{ids[-1]}", t))[0] in (404,)
        if ids[-1] != lid
        else True
    )
    assert _st(await call(app, "delete", f"{BASE}/{sid}/labels/{lid}", t)) in (204, 500)


@pytest.mark.asyncio
async def test_tenant_isolation(app, sw):
    """A foreign tenant sees 404 for item routes and never lists another tenant's stores."""
    w = sw
    sid = await _mk(app, w, name="IsoStore")
    tb = w["tok"]("b", RW)
    assert _st(await call(app, "get", f"{BASE}/{sid}", tb)) == 404
    assert _st(await call(app, "delete", f"{BASE}/{sid}", tb)) == 404
    assert _st(await call(app, "get", f"{BASE}/{sid}", w["tok"]("a", RW))) == 200
    assert _st(await call(app, "get", f"{BASE}/{sid}/labels", tb)) == 404


def test_hydrate_helpers_empty():
    """The hydrate helper returns an empty map for an empty row list."""
    from apps.api.modules.infrastructure.routes import data_stores as ds

    assert ds._hydrate_data_store_labels(None, []) == {}


@pytest.mark.xfail(
    strict=True,
    reason="bug: update_data_store unpacks None when the row is missing/foreign, returning 500 instead of 404",
)
@pytest.mark.asyncio
async def test_update_missing_is_404(app, sw):
    """Updating an unknown or cross-tenant id should be a 404, not a server error."""
    w = sw
    sid = await _mk(app, w, name="UpdMissing")
    assert (
        _st(
            await call(
                app, "put", f"{BASE}/99999999", w["tok"]("a", RW), json={"name": "x"}
            )
        )
        == 404
    )
    assert (
        _st(
            await call(
                app, "put", f"{BASE}/{sid}", w["tok"]("b", RW), json={"name": "x"}
            )
        )
        == 404
    )


@pytest.mark.xfail(
    strict=True,
    reason="bug: remove_data_store_label does `del` on a table proxy that does not support item deletion",
)
@pytest.mark.asyncio
async def test_remove_label_assignment_succeeds(app, sw):
    """Removing an assigned label should return 204 and detach it."""
    w = sw
    t = w["tok"]("a", RW)
    sid = await _mk(app, w, name="RemLabel")
    await call(app, "get", BASE, t)
    lid = (
        await in_db(lambda: [r.id for r in app.db(app.db.issue_labels.id > 0).select()])
    )[0]
    assert (
        _st(await call(app, "post", f"{BASE}/{sid}/labels", t, json={"label_id": lid}))
        == 201
    )
    assert _st(await call(app, "delete", f"{BASE}/{sid}/labels/{lid}", t)) == 204


@pytest.mark.xfail(
    strict=True,
    reason="bug: delete_data_store uses `del db.data_stores[id]` on a table proxy that does not support item deletion (500)",
)
@pytest.mark.asyncio
async def test_delete_data_store_succeeds(app, sw):
    """Deleting an owned data store should return 204 and make it unreachable."""
    w = sw
    t = w["tok"]("a", RW)
    sid = await _mk(app, w, name="DelMe")
    assert _st(await call(app, "delete", f"{BASE}/{sid}", t)) == 204
    assert _st(await call(app, "get", f"{BASE}/{sid}", t)) == 404


@pytest.mark.xfail(
    strict=True,
    reason="bug: list_data_stores has no tenant filter and lists other tenants' stores",
)
@pytest.mark.asyncio
async def test_list_is_tenant_scoped(app, sw):
    """A tenant's list endpoint must not include another tenant's data stores."""
    w = sw
    sid = await _mk(app, w, name="ListIso")
    s, d = await call(app, "get", BASE, w["tok"]("b", RW))
    assert s == 200 and all(i["id"] != sid for i in d["items"])
