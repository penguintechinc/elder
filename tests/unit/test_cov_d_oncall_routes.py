"""HTTP route tests for the on-call module (``/api/v1/on-call``).

Requests go through the real Quart app with real JWT verification and the
real Postgres schema.  Mutation routes carry a ``maintainer`` resource-role
decorator whose default ``resource_param`` never matches this module's path
params, so write-path tests use a superuser identity (the decorator's
documented bypass); the unrelated decorator defect is pinned with xfails.
"""

# flake8: noqa: E501

import datetime
from typing import Any

import pytest
import pytest_asyncio

from apps.api.utils.async_utils import run_in_threadpool
from tests.unit._cov_d_helpers import (  # noqa: F401
    call,
    in_db,
    make_token,
    now,
    vid,
    world,
)

BASE = "/api/v1/on-call"
READ = ["services_oncall:read"]
WRITE = ["services_oncall:read", "services_oncall:write"]
UTC = datetime.UTC


@pytest_asyncio.fixture
async def oc(app: Any, world: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    """Extend the two-tenant world with a superuser, a service and an org member."""
    db = world["db"]
    a, b = world["a"], world["b"]
    sfx = world["sfx"]

    def _seed() -> dict[str, Any]:
        ts = now()

        def ident(tenant: int, org: int, name: str, su: bool = False) -> int:
            return db.identities.insert(
                identity_type="human",
                username=f"cd-oc-{name}-{sfx}",
                email=f"cd-oc-{name}-{sfx}@example.com",
                full_name=name,
                tenant_id=tenant,
                organization_id=org,
                auth_provider="local",
                is_active=True,
                is_superuser=su,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="observer",
                created_at=ts,
                updated_at=ts,
            )

        out = {
            "su": ident(a["tenant"], a["org"], "su", True),
            "member": ident(a["tenant"], a["org"], "member"),
            "member2": ident(a["tenant"], a["org"], "member2"),
            "b_member": ident(b["tenant"], b["org"], "bmember"),
        }
        out["svc"] = db.services.insert(
            name=f"svc-{sfx}",
            organization_id=a["org"],
            tenant_id=a["tenant"],
            village_id=vid(),
            is_public=False,
            created_at=ts,
            updated_at=ts,
        )
        out["b_svc"] = db.services.insert(
            name=f"bsvc-{sfx}",
            organization_id=b["org"],
            tenant_id=b["tenant"],
            village_id=vid(),
            is_public=False,
            created_at=ts,
            updated_at=ts,
        )
        db.commit()
        return out

    ids = await in_db(_seed)
    ids["w"] = make_token(app, a["tenant"], ids["su"], WRITE)
    ids["r"] = make_token(app, a["tenant"], a["user"], READ)
    ids["nw"] = make_token(app, a["tenant"], a["user"], WRITE)
    ids["app"] = app
    ids["world"] = world
    return ids


async def _rotation(oc: dict[str, Any], side: str = "a", **kw: Any) -> int:
    """Insert a rotation for the given tenant side and return its id."""
    w = oc["world"]
    s = w[side]

    def _do() -> int:
        ts = now()
        values = dict(
            name=f"rot-{vid()[:6]}",
            is_active=True,
            scope_type="organization",
            organization_id=s["org"],
            schedule_type="manual",
            tenant_id=s["tenant"],
            village_id=vid(),
            created_at=ts,
            updated_at=ts,
        )
        values.update(kw)
        rid = w["db"].on_call_rotations.insert(**values)
        w["db"].commit()
        return rid

    return await run_in_threadpool(_do)


async def _participant(
    oc: dict[str, Any], rid: int, identity: int, idx: int = 0
) -> int:
    """Insert a participant row and return its id."""
    db = oc["world"]["db"]

    def _do() -> int:
        ts = now()
        pid = db.on_call_rotation_participants.insert(
            rotation_id=rid,
            identity_id=identity,
            order_index=idx,
            is_active=True,
            created_at=ts,
            updated_at=ts,
        )
        db.commit()
        return pid

    return await run_in_threadpool(_do)


async def _override(
    oc: dict[str, Any], rid: int, orig: int, repl: int, hours: int = 0
) -> int:
    """Insert an override row starting ``hours`` from now (one hour long)."""
    db = oc["world"]["db"]

    def _do() -> int:
        t = now() + datetime.timedelta(hours=hours)
        oid = db.on_call_overrides.insert(
            rotation_id=rid,
            original_identity_id=orig,
            override_identity_id=repl,
            start_datetime=t,
            end_datetime=t + datetime.timedelta(hours=1),
            reason="r",
            created_at=t,
        )
        db.commit()
        return oid

    return await run_in_threadpool(_do)


async def _shift(
    oc: dict[str, Any], rid: int, identity: int, hours_ago: int = 1
) -> int:
    """Insert a shift covering now (started ``hours_ago`` hours ago)."""
    db = oc["world"]["db"]

    def _do() -> int:
        t = now()
        sid = db.on_call_shifts.insert(
            rotation_id=rid,
            identity_id=identity,
            shift_start=t - datetime.timedelta(hours=hours_ago),
            shift_end=t + datetime.timedelta(hours=1),
            is_override=False,
            created_at=t,
        )
        db.commit()
        return sid

    return await run_in_threadpool(_do)


async def _policy(oc: dict[str, Any], rid: int, level: int = 1, **kw: Any) -> int:
    """Insert an escalation policy and return its id."""
    db = oc["world"]["db"]

    def _do() -> int:
        t = now()
        values = dict(
            rotation_id=rid,
            level=level,
            escalation_type="rotation_participant",
            escalation_delay_minutes=5,
            created_at=t,
            updated_at=t,
        )
        values.update(kw)
        pid = db.on_call_escalation_policies.insert(**values)
        db.commit()
        return pid

    return await run_in_threadpool(_do)


async def _group(oc: dict[str, Any]) -> int:
    """Insert an identity group and return its id."""
    db = oc["world"]["db"]

    def _do() -> int:
        t = now()
        gid = db.identity_groups.insert(
            name=f"grp-{vid()[:8]}",
            village_id=vid(),
            is_active=True,
            review_enabled=False,
            review_auto_apply=True,
            created_at=t,
            updated_at=t,
        )
        db.commit()
        return gid

    return await run_in_threadpool(_do)


# --- auth ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_and_scope_enforced(oc: dict[str, Any]) -> None:
    """Missing token is 401 and a token lacking the scope is 403."""
    app = oc["app"]
    assert (await call(app, "get", BASE + "/rotations"))[0] == 401
    a = oc["world"]["a"]
    bare = make_token(app, a["tenant"], a["user"], ["other:read"])
    assert (await call(app, "get", BASE + "/rotations", bare))[0] == 403
    status, _ = await call(app, "post", BASE + "/rotations", bare, json={"name": "x"})
    assert status == 403


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: resource_role_required default resource_param 'id' never matches rotation_id",
)
async def test_non_superuser_maintainer_can_update(oc: dict[str, Any]) -> None:
    """A non-superuser with write scope should be able to update a rotation."""
    rid = await _rotation(oc)
    status, _ = await call(
        oc["app"], "put", f"{BASE}/rotations/{rid}", oc["nw"], json={"name": "n"}
    )
    assert status == 200


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: resource_role_required default resource_param 'id' never matches rotation_id",
)
async def test_non_superuser_maintainer_can_delete(oc: dict[str, Any]) -> None:
    """A non-superuser with write scope should be able to delete a rotation."""
    rid = await _rotation(oc)
    status, _ = await call(oc["app"], "delete", f"{BASE}/rotations/{rid}", oc["nw"])
    assert status == 204


# --- rotations: list / get ---------------------------------------------------------


@pytest.mark.asyncio
async def test_list_rotations_filters_and_isolation(oc: dict[str, Any]) -> None:
    """List honours every filter, paginates and never shows another tenant's rotation."""
    app = oc["app"]
    r1 = await _rotation(oc, name="alpha", is_active=True)
    r2 = await _rotation(
        oc,
        name="beta",
        is_active=False,
        scope_type="service",
        organization_id=None,
        service_id=oc["svc"],
        schedule_type="cron",
        schedule_cron="0 0 * * *",
    )
    foreign = await _rotation(oc, "b", name="foreign")

    status, data = await call(app, "get", BASE + "/rotations", oc["r"])
    ids = {i["id"] for i in data["items"]}
    assert status == 200
    assert {r1, r2} <= ids and foreign not in ids
    assert data["total"] == len(ids)

    a_org = oc["world"]["a"]["org"]
    for qs, expected in [
        (f"org_id={a_org}", {r1}),
        (f"service_id={oc['svc']}", {r2}),
        ("scope_type=service", {r2}),
        ("schedule_type=cron", {r2}),
        ("is_active=false", {r2}),
        ("is_active=true", {r1}),
    ]:
        status, data = await call(app, "get", f"{BASE}/rotations?{qs}", oc["r"])
        assert status == 200, qs
        assert {i["id"] for i in data["items"]} >= expected, qs
        assert (expected - {i["id"] for i in data["items"]}) == set(), qs

    status, data = await call(
        app, "get", BASE + "/rotations?per_page=1&page=1", oc["r"]
    )
    assert status == 200 and len(data["items"]) == 1 and data["pages"] >= 2


@pytest.mark.asyncio
async def test_get_rotation_with_participants_and_current(oc: dict[str, Any]) -> None:
    """Get returns the rotation, its participants and the current on-call shift."""
    app = oc["app"]
    rid = await _rotation(oc)
    await _participant(oc, rid, oc["member"], 0)
    await _shift(oc, rid, oc["member"])
    status, data = await call(app, "get", f"{BASE}/rotations/{rid}", oc["r"])
    assert status == 200
    assert data["rotation"]["id"] == rid
    assert data["participants"][0]["identity_id"] == oc["member"]
    assert data["current_oncall"]["identity_id"] == oc["member"]

    empty = await _rotation(oc)
    status, data = await call(app, "get", f"{BASE}/rotations/{empty}", oc["r"])
    assert (
        status == 200 and data["participants"] == [] and data["current_oncall"] is None
    )


@pytest.mark.asyncio
async def test_get_rotation_orphan_shift_identity_and_foreign(
    oc: dict[str, Any],
) -> None:
    """A shift for a foreign-tenant identity is hidden; foreign rotations are 404."""
    app = oc["app"]
    rid = await _rotation(oc)
    await _shift(oc, rid, oc["b_member"])
    status, data = await call(app, "get", f"{BASE}/rotations/{rid}", oc["r"])
    assert status == 200 and data["current_oncall"] is None

    foreign = await _rotation(oc, "b")
    assert (await call(app, "get", f"{BASE}/rotations/{foreign}", oc["r"]))[0] == 404


# --- rotations: create ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rotation_validation_errors(oc: dict[str, Any]) -> None:
    """Create rejects bad bodies, bad scope/parent and bad schedule parameters."""
    app = oc["app"]
    org = oc["world"]["a"]["org"]
    url = BASE + "/rotations"

    assert (await call(app, "post", url, oc["w"], json={}))[0] == 400
    assert (await call(app, "post", url, oc["w"], json={"name": "x"}))[0] == 400

    def body(**kw: Any) -> dict[str, Any]:
        base = {
            "name": "n",
            "scope_type": "organization",
            "organization_id": org,
            "schedule_type": "manual",
        }
        base.update(kw)
        return base

    cases = [
        ({"organization_id": None}, 404),
        ({"scope_type": "bogus"}, 400),
        ({"schedule_type": "weekly"}, 400),
        (
            {
                "schedule_type": "weekly",
                "rotation_length_days": 7,
                "rotation_start_date": "not-a-date",
            },
            400,
        ),
        ({"schedule_type": "cron"}, 400),
        ({"schedule_type": "cron", "schedule_cron": "nope nope"}, 400),
        ({"schedule_type": "follow_the_sun"}, 400),
        ({"organization_id": oc["world"]["b"]["org"]}, 404),
    ]
    for override, want in cases:
        payload = body(**override)
        status, _ = await call(app, "post", url, oc["w"], json=payload)
        assert status == want, override

    missing_org = body()
    del missing_org["organization_id"]
    assert (await call(app, "post", url, oc["w"], json=missing_org))[0] == 400

    status, _ = await call(
        app,
        "post",
        url,
        oc["w"],
        json={"name": "n", "scope_type": "service", "schedule_type": "manual"},
    )
    assert status == 400
    status, _ = await call(
        app,
        "post",
        url,
        oc["w"],
        json={
            "name": "n",
            "scope_type": "service",
            "service_id": oc["b_svc"],
            "schedule_type": "manual",
        },
    )
    assert status == 404


@pytest.mark.asyncio
async def test_create_rotation_variants(oc: dict[str, Any]) -> None:
    """Every schedule type can be created; org scope auto-enrols active humans."""
    app = oc["app"]
    db = oc["world"]["db"]
    org = oc["world"]["a"]["org"]
    url = BASE + "/rotations"

    status, data = await call(
        app,
        "post",
        url,
        oc["w"],
        json={
            "name": "weekly",
            "scope_type": "organization",
            "organization_id": org,
            "schedule_type": "weekly",
            "rotation_length_days": 7,
            "rotation_start_date": "2024-01-01T00:00:00",
            "description": "d",
        },
    )
    assert status == 201 and data["schedule_type"] == "weekly"
    rid = data["id"]
    count = await run_in_threadpool(
        lambda: db(db.on_call_rotation_participants.rotation_id == rid).count()
    )
    assert count >= 3  # su, member, member2 (+ the tenant's own user has no org)

    status, data = await call(
        app,
        "post",
        url,
        oc["w"],
        json={
            "name": "cron",
            "scope_type": "service",
            "service_id": oc["svc"],
            "schedule_type": "cron",
            "schedule_cron": "0 9 * * 1",
            "is_active": False,
        },
    )
    assert (
        status == 201
        and data["handoff_timezone"] == "UTC"
        and data["is_active"] is False
    )

    status, data = await call(
        app,
        "post",
        url,
        oc["w"],
        json={
            "name": "fts",
            "scope_type": "organization",
            "organization_id": org,
            "schedule_type": "follow_the_sun",
            "handoff_timezone": "UTC",
            "shift_config": {"timezones": []},
        },
    )
    assert status == 201 and data["shift_split"] is False

    status, data = await call(
        app,
        "post",
        url,
        oc["w"],
        json={
            "name": "manual",
            "scope_type": "organization",
            "organization_id": org,
            "schedule_type": "manual",
        },
    )
    assert status == 201


# --- rotations: update / delete ---------------------------------------------------------


@pytest.mark.asyncio
async def test_update_rotation(oc: dict[str, Any]) -> None:
    """Update validates input, ignores unknown keys and persists changes."""
    app = oc["app"]
    rid = await _rotation(
        oc,
        schedule_type="weekly",
        rotation_length_days=7,
        rotation_start_date=datetime.date(2024, 1, 1),
    )
    url = f"{BASE}/rotations/{rid}"

    assert (await call(app, "put", url, oc["w"], json={}))[0] == 400
    assert (await call(app, "put", url, oc["w"], json={"schedule_cron": "bad bad"}))[
        0
    ] == 400
    foreign = await _rotation(oc, "b")
    assert (
        await call(
            app, "put", f"{BASE}/rotations/{foreign}", oc["w"], json={"name": "x"}
        )
    )[0] == 404

    status, data = await call(app, "put", url, oc["w"], json={"unknown": 1})
    assert status == 200 and data["id"] == rid

    status, data = await call(
        app,
        "put",
        url,
        oc["w"],
        json={
            "name": "renamed",
            "schedule_cron": "0 1 * * *",
            "rotation_start_date": "2024-02-01",
            "is_active": False,
        },
    )
    assert status == 200 and data["name"] == "renamed" and data["is_active"] is False


@pytest.mark.asyncio
async def test_delete_rotation(oc: dict[str, Any]) -> None:
    """Delete removes the rotation once, then 404s; foreign rotations are 404."""
    app = oc["app"]
    rid = await _rotation(oc)
    assert (await call(app, "delete", f"{BASE}/rotations/{rid}", oc["w"]))[0] == 204
    assert (await call(app, "delete", f"{BASE}/rotations/{rid}", oc["w"]))[0] == 404
    foreign = await _rotation(oc, "b")
    assert (await call(app, "delete", f"{BASE}/rotations/{foreign}", oc["w"]))[0] == 404


# --- participants -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_participants_crud(oc: dict[str, Any]) -> None:
    """Participants can be listed, added, updated and removed within a tenant."""
    app = oc["app"]
    rid = await _rotation(oc)
    url = f"{BASE}/rotations/{rid}/participants"

    assert (await call(app, "get", f"{BASE}/rotations/987654/participants", oc["r"]))[
        0
    ] == 404
    status, data = await call(app, "get", url, oc["r"])
    assert status == 200 and data["items"] == []

    assert (
        await call(
            app,
            "post",
            f"{BASE}/rotations/987654/participants",
            oc["w"],
            json={"identity_id": 1, "order_index": 0},
        )
    )[0] == 404
    assert (await call(app, "post", url, oc["w"], json={}))[0] == 400
    assert (await call(app, "post", url, oc["w"], json={"identity_id": oc["member"]}))[
        0
    ] == 400
    status, _ = await call(
        app,
        "post",
        url,
        oc["w"],
        json={"identity_id": oc["b_member"], "order_index": 1},
    )
    assert status == 404

    status, created = await call(
        app,
        "post",
        url,
        oc["w"],
        json={
            "identity_id": oc["member"],
            "order_index": 1,
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "notification_email": "pager@example.com",
            "notification_phone": "555-0100",
            "notification_slack": "#oncall",
        },
    )
    assert status == 201 and created["notification_slack"] == "#oncall"
    pid = created["id"]
    status, minimal = await call(
        app,
        "post",
        url,
        oc["w"],
        json={"identity_id": oc["member2"], "order_index": 2, "is_active": False},
    )
    assert status == 201 and minimal["is_active"] is False

    status, data = await call(app, "get", url + "?per_page=10", oc["r"])
    assert status == 200 and data["total"] == 2
    assert data["items"][0]["identity_name"]

    purl = f"{url}/{pid}"
    assert (
        await call(
            app,
            "put",
            f"{BASE}/rotations/987654/participants/{pid}",
            oc["w"],
            json={"order_index": 3},
        )
    )[0] == 404
    assert (await call(app, "put", f"{url}/987654", oc["w"], json={"order_index": 3}))[
        0
    ] == 404
    assert (await call(app, "put", purl, oc["w"], json={}))[0] == 400
    status, data = await call(
        app,
        "put",
        purl,
        oc["w"],
        json={
            "order_index": 5,
            "is_active": False,
            "notification_phone": "555-0111",
            "start_date": "2024-02-01",
            "end_date": "2024-11-30",
        },
    )
    assert status == 200 and data["order_index"] == 5 and data["end_date"]
    status, data = await call(app, "put", purl, oc["w"], json={"unknown": 1})
    assert status == 200

    assert (
        await call(
            app, "delete", f"{BASE}/rotations/987654/participants/{pid}", oc["w"]
        )
    )[0] == 404
    assert (await call(app, "delete", purl, oc["w"]))[0] == 204
    assert (await call(app, "delete", purl, oc["w"]))[0] == 404


@pytest.mark.asyncio
async def test_participant_foreign_tenant_is_404(oc: dict[str, Any]) -> None:
    """Participants of another tenant's rotation cannot be touched by id."""
    app = oc["app"]
    mine = await _rotation(oc)
    foreign = await _rotation(oc, "b")
    fpid = await _participant(oc, foreign, oc["b_member"])
    url = f"{BASE}/rotations/{mine}/participants/{fpid}"
    assert (await call(app, "put", url, oc["w"], json={"order_index": 9}))[0] == 404
    assert (await call(app, "delete", url, oc["w"]))[0] == 404


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: required-field check is truthiness-based, so order_index 0 is rejected",
)
async def test_add_participant_at_position_zero(oc: dict[str, Any]) -> None:
    """The first position (order_index 0) should be a valid participant slot."""
    rid = await _rotation(oc)
    status, _ = await call(
        oc["app"],
        "post",
        f"{BASE}/rotations/{rid}/participants",
        oc["w"],
        json={"identity_id": oc["member"], "order_index": 0},
    )
    assert status == 201


# --- overrides ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overrides_validation_list_update_delete(oc: dict[str, Any]) -> None:
    """Override validation, overlap rejection, listing, update and delete."""
    app = oc["app"]
    rid = await _rotation(oc)
    url = f"{BASE}/rotations/{rid}/overrides"
    m1, m2 = oc["member"], oc["member2"]

    assert (await call(app, "get", f"{BASE}/rotations/987654/overrides", oc["r"]))[
        0
    ] == 404
    status, data = await call(app, "get", url, oc["r"])
    assert status == 200 and data["items"] == []

    good = {
        "original_identity_id": m1,
        "override_identity_id": m2,
        "start_datetime": "2030-01-01T00:00:00Z",
        "end_datetime": "2030-01-02T00:00:00Z",
        "reason": "trip",
        "created_by_id": oc["su"],
    }
    assert (
        await call(
            app, "post", f"{BASE}/rotations/987654/overrides", oc["w"], json=good
        )
    )[0] == 404
    assert (await call(app, "post", url, oc["w"], json={}))[0] == 400
    assert (await call(app, "post", url, oc["w"], json={"original_identity_id": m1}))[
        0
    ] == 400
    assert (
        await call(
            app,
            "post",
            url,
            oc["w"],
            json={**good, "original_identity_id": oc["b_member"]},
        )
    )[0] == 404
    assert (
        await call(
            app,
            "post",
            url,
            oc["w"],
            json={**good, "override_identity_id": oc["b_member"]},
        )
    )[0] == 404
    assert (
        await call(
            app, "post", url, oc["w"], json={**good, "start_datetime": "garbage"}
        )
    )[0] == 400
    assert (
        await call(app, "post", url, oc["w"], json={**good, "end_datetime": "garbage"})
    )[0] == 400
    assert (
        await call(
            app,
            "post",
            url,
            oc["w"],
            json={**good, "end_datetime": good["start_datetime"]},
        )
    )[0] == 400

    oid = await _override(oc, rid, m1, m2, hours=0)
    t = now()
    overlap = {
        **good,
        "start_datetime": (t - datetime.timedelta(minutes=5)).isoformat(),
        "end_datetime": (t + datetime.timedelta(minutes=5)).isoformat(),
    }
    assert (await call(app, "post", url, oc["w"], json=overlap))[0] == 409

    status, data = await call(app, "get", url, oc["r"])
    assert status == 200 and data["total"] == 1
    assert data["items"][0]["override_identity_name"]

    ourl = f"{BASE}/rotations/overrides/{oid}"
    assert (
        await call(
            app,
            "put",
            f"{BASE}/rotations/overrides/987654",
            oc["w"],
            json={"reason": "x"},
        )
    )[0] == 404
    assert (await call(app, "put", ourl, oc["w"], json={}))[0] == 400
    status, data = await call(
        app,
        "put",
        ourl,
        oc["w"],
        json={
            "reason": "changed",
            "start_datetime": "2030-01-01T01:00:00Z",
            "end_datetime": "2030-01-02T01:00:00Z",
        },
    )
    assert status == 200 and data["reason"] == "changed"
    assert (await call(app, "put", ourl, oc["w"], json={"unknown": 1}))[0] == 200

    assert (await call(app, "delete", ourl, oc["w"]))[0] == 204
    assert (await call(app, "delete", ourl, oc["w"]))[0] == 404


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: create_override inserts updated_at but on_call_overrides has no such column",
)
async def test_create_override_succeeds(oc: dict[str, Any]) -> None:
    """A valid override should be created."""
    rid = await _rotation(oc)
    status, data = await call(
        oc["app"],
        "post",
        f"{BASE}/rotations/{rid}/overrides",
        oc["w"],
        json={
            "original_identity_id": oc["member"],
            "override_identity_id": oc["member2"],
            "start_datetime": "2030-01-01T00:00:00Z",
            "end_datetime": "2030-01-02T00:00:00Z",
            "reason": "trip",
            "created_by_id": oc["su"],
        },
    )
    assert status == 201 and data["reason"] == "trip"


@pytest.mark.asyncio
async def test_override_foreign_tenant_is_404(oc: dict[str, Any]) -> None:
    """Overrides on another tenant's rotation cannot be updated or deleted."""
    app = oc["app"]
    foreign = await _rotation(oc, "b")
    oid = await _override(oc, foreign, oc["b_member"], oc["b_member"])
    ourl = f"{BASE}/rotations/overrides/{oid}"
    assert (await call(app, "put", ourl, oc["w"], json={"reason": "x"}))[0] == 404
    assert (await call(app, "delete", ourl, oc["w"]))[0] == 404


# --- current on-call / history ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_oncall_for_org_and_service(oc: dict[str, Any]) -> None:
    """Current on-call lists active rotations that have a live shift."""
    app = oc["app"]
    org = oc["world"]["a"]["org"]
    live = await _rotation(oc, name="live")
    await _shift(oc, live, oc["member"])
    await _rotation(oc, name="quiet")
    svc_rot = await _rotation(
        oc, name="svc", scope_type="service", organization_id=None, service_id=oc["svc"]
    )
    await _shift(oc, svc_rot, oc["member2"])

    status, data = await call(
        app, "get", f"{BASE}/rotations/current/organization/{org}", oc["r"]
    )
    assert status == 200 and data["organization_id"] == org
    assert [r["rotation_id"] for r in data["rotations"]] == [live]
    assert (
        await call(app, "get", f"{BASE}/rotations/current/organization/987654", oc["r"])
    )[0] == 404

    status, data = await call(
        app, "get", f"{BASE}/rotations/current/service/{oc['svc']}", oc["r"]
    )
    assert status == 200 and [r["rotation_id"] for r in data["rotations"]] == [svc_rot]
    assert (
        await call(app, "get", f"{BASE}/rotations/current/service/987654", oc["r"])
    )[0] == 404


@pytest.mark.asyncio
async def test_shift_history_unknown_rotation_is_404(oc: dict[str, Any]) -> None:
    """History for an unknown rotation is a 404."""
    assert (await call(oc["app"], "get", f"{BASE}/rotations/987654/history", oc["r"]))[
        0
    ] == 404


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: shift history selects db.on_call_shifts.ALL, which penguin-dal does not support",
)
@pytest.mark.parametrize(
    "qs",
    [
        "",
        "start_date=2000-01-01T00:00:00%2B00:00",
        "end_date=2999-01-01T00:00:00%2B00:00",
        "start_date=garbage&end_date=garbage",
        "identity_id=1",
    ],
)
async def test_shift_history_lists_shifts(oc: dict[str, Any], qs: str) -> None:
    """History should list shifts and honour the date and identity filters."""
    rid = await _rotation(oc)
    await _shift(oc, rid, oc["member"])
    status, data = await call(
        oc["app"], "get", f"{BASE}/rotations/{rid}/history?{qs}", oc["r"]
    )
    assert status == 200 and data["total"] == 1


# --- escalations --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_escalations(oc: dict[str, Any]) -> None:
    """Escalation listing enriches identity and group targets."""
    app = oc["app"]
    rid = await _rotation(oc)
    gid = await _group(oc)
    await _policy(oc, rid, 1, escalation_type="identity", identity_id=oc["member"])
    await _policy(oc, rid, 2, escalation_type="group", group_id=gid)
    await _policy(oc, rid, 3)
    url = f"{BASE}/rotations/{rid}/escalations"
    assert (await call(app, "get", f"{BASE}/rotations/987654/escalations", oc["r"]))[
        0
    ] == 404
    status, data = await call(app, "get", url, oc["r"])
    assert status == 200 and data["total"] == 3
    first, second, third = data["items"]
    assert first["identity_name"] and second["group_name"]
    assert "identity_name" not in third and "group_name" not in third


@pytest.mark.asyncio
async def test_create_update_delete_escalation(oc: dict[str, Any]) -> None:
    """Escalation policies validate targets and round-trip through update/delete."""
    app = oc["app"]
    rid = await _rotation(oc)
    gid = await _group(oc)
    url = f"{BASE}/rotations/{rid}/escalations"

    assert (
        await call(
            app,
            "post",
            f"{BASE}/rotations/987654/escalations",
            oc["w"],
            json={"level": 1, "escalation_type": "identity"},
        )
    )[0] == 404
    assert (await call(app, "post", url, oc["w"], json={}))[0] == 400
    assert (await call(app, "post", url, oc["w"], json={"level": 1}))[0] == 400
    assert (
        await call(
            app, "post", url, oc["w"], json={"level": 1, "escalation_type": "weird"}
        )
    )[0] == 400
    assert (
        await call(
            app, "post", url, oc["w"], json={"level": 1, "escalation_type": "identity"}
        )
    )[0] == 400
    assert (
        await call(
            app, "post", url, oc["w"], json={"level": 1, "escalation_type": "group"}
        )
    )[0] == 400
    assert (
        await call(
            app,
            "post",
            url,
            oc["w"],
            json={
                "level": 1,
                "escalation_type": "identity",
                "identity_id": oc["b_member"],
            },
        )
    )[0] == 404
    assert (
        await call(
            app,
            "post",
            url,
            oc["w"],
            json={"level": 1, "escalation_type": "group", "group_id": 987654},
        )
    )[0] == 404

    status, ident = await call(
        app,
        "post",
        url,
        oc["w"],
        json={
            "level": 1,
            "escalation_type": "identity",
            "identity_id": oc["member"],
            "escalation_delay_minutes": 3,
            "notification_channels": ["email"],
        },
    )
    assert (
        status == 201
        and ident["identity_name"]
        and ident["notification_channels"] == ["email"]
    )
    status, grp = await call(
        app,
        "post",
        url,
        oc["w"],
        json={"level": 2, "escalation_type": "group", "group_id": gid},
    )
    assert status == 201 and grp["group_name"] and grp["escalation_delay_minutes"] == 15
    status, part = await call(
        app,
        "post",
        url,
        oc["w"],
        json={"level": 3, "escalation_type": "rotation_participant"},
    )
    assert status == 201

    for pol, key in [(ident, "identity_name"), (grp, "group_name"), (part, None)]:
        purl = f"{BASE}/rotations/escalations/{pol['id']}"
        status, data = await call(
            app,
            "put",
            purl,
            oc["w"],
            json={
                "level": 9,
                "escalation_delay_minutes": 1,
                "notification_channels": ["sms"],
            },
        )
        assert status == 200 and data["level"] == 9
        if key:
            assert data[key]
    purl = f"{BASE}/rotations/escalations/{ident['id']}"
    assert (
        await call(
            app,
            "put",
            f"{BASE}/rotations/escalations/987654",
            oc["w"],
            json={"level": 1},
        )
    )[0] == 404
    assert (await call(app, "put", purl, oc["w"], json={}))[0] == 400
    assert (await call(app, "put", purl, oc["w"], json={"unknown": 1}))[0] == 200
    assert (await call(app, "delete", purl, oc["w"]))[0] == 204
    assert (await call(app, "delete", purl, oc["w"]))[0] == 404


@pytest.mark.asyncio
async def test_escalation_foreign_tenant_is_404(oc: dict[str, Any]) -> None:
    """Escalation policies on another tenant's rotation are not reachable."""
    app = oc["app"]
    foreign = await _rotation(oc, "b")
    pid = await _policy(oc, foreign)
    purl = f"{BASE}/rotations/escalations/{pid}"
    assert (await call(app, "put", purl, oc["w"], json={"level": 2}))[0] == 404
    assert (await call(app, "delete", purl, oc["w"]))[0] == 404


# --- webhook --------------------------------------------------------------------------------------


HOOK = BASE + "/webhooks/alertmanager/webhook"


@pytest.mark.asyncio
async def test_webhook_rejects_bad_bodies(oc: dict[str, Any]) -> None:
    """The webhook needs a JSON object with at least one alert."""
    app = oc["app"]
    assert (await call(app, "post", HOOK, json={}))[0] == 400
    assert (await call(app, "post", HOOK, json={"alerts": []}))[0] == 400


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: webhook returns a nested (response, 200), 202 tuple that Quart rejects",
)
async def test_webhook_skips_unmatched_alerts(oc: dict[str, Any]) -> None:
    """Resolved, label-less and unknown-service alerts are ignored and accepted."""
    svc_name = f"svc-{oc['world']['sfx']}"
    alerts = [
        {"status": "resolved", "labels": {"service": svc_name}},
        {"status": "firing", "labels": {}},
        {"status": "firing", "labels": {"service": "no-such-service"}},
        {"status": "firing"},
    ]
    status, data = await call(oc["app"], "post", HOOK, json={"alerts": alerts})
    assert status == 202 and data["alerts_processed"] == 4


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: webhook rotation filter uses `is True` and the 202 response is a nested tuple",
)
async def test_webhook_firing_alert_for_known_service(oc: dict[str, Any]) -> None:
    """A firing alert for a service with a rotation should be accepted."""
    svc_name = f"svc-{oc['world']['sfx']}"
    status, _ = await call(
        oc["app"],
        "post",
        HOOK,
        json={"alerts": [{"status": "firing", "labels": {"service": svc_name}}]},
    )
    assert status == 202


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="bug: webhook inserts updated_at into on_call_notifications, which has no such column",
)
async def test_webhook_records_notification_for_oncall(
    oc: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the broken rotation query bypassed, the on-call person is notified.

    Only the rotation lookup (which builds a bare-bool query) is replaced by an
    equivalent correct query; notification recording runs for real.
    """
    from apps.api.modules.services_oncall.routes import on_call_webhooks as hooks

    db = oc["world"]["db"]
    a = oc["world"]["a"]
    idle = await _rotation(
        oc,
        name="idle",
        scope_type="service",
        organization_id=None,
        service_id=oc["svc"],
    )
    rid = await _rotation(
        oc,
        name="hook",
        scope_type="service",
        organization_id=None,
        service_id=oc["svc"],
    )
    await _shift(oc, rid, oc["member"])
    real = hooks.run_in_threadpool

    async def shim(fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Swap the rotation lookup for a correct equivalent; pass others through."""
        if fn.__name__ == "get_rotations":
            return await real(
                lambda: db(db.on_call_rotations.service_id == oc["svc"]).select()
            )
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(hooks, "run_in_threadpool", shim)
    status, _ = await call(
        oc["app"],
        "post",
        HOOK,
        json={
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "service": f"svc-{oc['world']['sfx']}",
                        "alertname": "Boom",
                    },
                    "annotations": {"summary": "it broke"},
                }
            ]
        },
    )
    rows = await real(lambda: db(db.on_call_notifications.rotation_id == rid).select())
    assert len(rows) == 1 and rows[0].subject == "Alert: Boom"
    assert rows[0].identity_id == oc["member"]
    none = await real(lambda: db(db.on_call_notifications.rotation_id == idle).count())
    assert none == 0 and a["tenant"]
    assert status == 202
