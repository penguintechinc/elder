"""HTTP-level behaviour tests for the issues routes (CRUD, labels, links, resources, project/milestone links).

Requests run through the real Quart app and Postgres test database with
signed tokens. Tenant isolation, validation, duplicate protection and the
stored rows are asserted for every handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from apps.api.modules.issues.routes import issues as ir
from tests.unit._cov_c_helpers import (
    call,
    mk_entity,
    mk_identity,
    mk_org,
    mk_tenant,
    sfx,
    token,
)

pytestmark = pytest.mark.asyncio
SC = ["issues:read", "issues:write"]
I = "/api/v1/issues"


@dataclass
class IC:
    """Seeded tenant world for the issues tests."""

    app: Any
    db: Any
    tid: int
    org: int
    org2: int
    foreign_org: int
    uid: int
    uid2: int
    foreign_uid: int
    entity: int
    foreign_entity: int
    project: int
    milestone: int
    tok: str
    ro: str
    foreign: str
    notenant: str


@pytest.fixture(scope="module")
def ic(app) -> IC:
    """Seed one tenant (orgs, identities, entity, project, milestone) and one foreign tenant."""
    db = app.db
    tid, other = mk_tenant(db, "ic"), mk_tenant(db, "icx")
    uid, uid2 = (
        mk_identity(db, tid, "admin", superuser=True),
        mk_identity(db, tid, "observer"),
    )
    ro = mk_identity(db, tid, "observer")
    fid = mk_identity(db, other, "admin", superuser=True)
    org, org2, forg = mk_org(db, tid), mk_org(db, tid), mk_org(db, other)
    ent, fent = mk_entity(db, org), mk_entity(db, forg)
    now = datetime.now(UTC)
    proj = db.projects.insert(
        name=f"p-{sfx()}",
        organization_id=org,
        tenant_id=tid,
        status="active",
        created_at=now,
        updated_at=now,
    )
    ms = db.milestones.insert(
        title=f"m-{sfx()}",
        organization_id=org,
        tenant_id=tid,
        status="open",
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return IC(
        app,
        db,
        tid,
        org,
        org2,
        forg,
        uid,
        uid2,
        fid,
        ent,
        fent,
        proj,
        ms,
        token(app, uid, tid, SC),
        token(app, ro, tid, ["issues:read"]),
        token(app, fid, other, SC),
        token(app, uid, "", SC),
    )


async def mk_issue(ic: IC, **extra: Any) -> dict[str, Any]:
    """Create an issue through the API."""
    st, js = await call(
        ic.app,
        "post",
        I,
        ic.tok,
        json={"title": f"Issue {sfx()}", "organization_id": ic.org, **extra},
    )
    assert st == 201, js
    return js


async def test_tenant_and_scope_guards(ic):
    """Tenant-less callers get 403 on every tenant-scoped handler; read-only callers cannot write."""
    cases = [
        ("get", ""),
        ("post", ""),
        ("get", "/1"),
        ("patch", "/1"),
        ("delete", "/1"),
        ("get", "/1/comments"),
        ("post", "/1/comments"),
        ("delete", "/1/comments/1"),
        ("get", "/1/labels"),
        ("post", "/1/labels"),
        ("delete", "/1/labels/1"),
        ("get", "/1/links"),
        ("post", "/1/links"),
        ("delete", "/1/links/1"),
        ("delete", "/1/links/by-entity/1"),
        ("get", "/1/resources"),
        ("post", "/1/resources"),
        ("delete", "/1/resources/1"),
        ("post", "/1/projects"),
        ("delete", "/1/projects/1"),
        ("post", "/1/milestones"),
        ("delete", "/1/milestones/1"),
    ]
    bodies = {
        "/1/comments": {"content": "x"},
        "/1/labels": {"label_id": 1},
        "/1/links": {"entity_id": 1},
        "/1/resources": {"resource_type": "x", "resource_id": 1},
        "/1/projects": {"project_id": 1},
        "/1/milestones": {"milestone_id": 1},
        "": {"title": "t", "organization_id": 1},
        "/1": {"title": "t"},
    }
    for method, path in cases:
        kw = {"json": bodies.get(path, {})} if method in ("post", "patch") else {}
        st, _ = await call(ic.app, method, I + path, ic.notenant, **kw)
        assert st == 403, (method, path, st)
    assert (
        await call(
            ic.app, "post", I, ic.ro, json={"title": "t", "organization_id": ic.org}
        )
    )[0] == 403
    assert (await call(ic.app, "get", I))[0] == 401


async def test_issue_create_list_get_patch(ic):
    """Create validates references and dates; list filters; patch edits fields and webhooks fire quietly."""
    bad = [
        {"organization_id": ic.foreign_org},
        {"assignee_type": "identity"},
        {"assignee_id": 999999999},
        {"assignee_id": 999999999, "assignee_type": "org_unit"},
        {"start_date": "2030-02-01T00:00:00Z", "end_date": "2030-01-01T00:00:00Z"},
    ]
    for extra in bad:
        body = {"title": "t", "organization_id": ic.org, **extra}
        if "organization_id" in extra:
            body["organization_id"] = extra["organization_id"]
        st, _ = await call(ic.app, "post", I, ic.tok, json=body)
        assert st in (400, 404), extra
    assert (
        await call(
            ic.app, "post", I, ic.tok, json={"title": "", "organization_id": ic.org}
        )
    )[0] == 400
    iss = await mk_issue(
        ic,
        description="d",
        status="open",
        priority="high",
        issue_type="bug",
        assignee_id=ic.uid2,
        is_incident=1,
        channel="email",
        category="billing",
        metadata={"k": "v"},
        start_date="2030-01-01T00:00:00Z",
        end_date="2030-02-01T00:00:00Z",
    )
    assert (
        iss["status"] == "open"
        and iss["priority"] == "high"
        and iss["issue_type"] == "bug"
    )
    org_assigned = await mk_issue(ic, assignee_id=ic.org2, assignee_type="org_unit")
    assert org_assigned["assignee_type"] == "org_unit"
    for q in (
        "status=open",
        "priority=HIGH",
        f"assignee_id={ic.uid2}",
        f"reporter_id={ic.uid}",
        "page=1&per_page=5",
    ):
        st, js = await call(ic.app, "get", f"{I}?{q}", ic.tok)
        assert st == 200 and js["total"] >= 1, q
    assert (await call(ic.app, "get", f"{I}/{iss['id']}", ic.tok))[0] == 200
    assert (await call(ic.app, "get", f"{I}/{iss['id']}", ic.foreign))[0] == 404
    assert (await call(ic.app, "get", f"{I}/999999999", ic.tok))[0] == 404
    # patch
    st, js = await call(
        ic.app,
        "patch",
        f"{I}/{iss['id']}",
        ic.tok,
        json={
            "title": "New title",
            "description": "dd",
            "status": "Closed",
            "priority": "low",
            "issue_type": "bug",
            "assignee_id": ic.uid,
            "organization_id": ic.org2,
            "is_incident": 0,
            "channel": "chat",
            "category": "tech",
            "metadata": {"a": 1},
            "parent_issue_id": org_assigned["id"],
            "start_date": "2030-03-01T00:00:00Z",
            "end_date": "2030-04-01T00:00:00Z",
        },
    )
    assert st == 200 and js["status"] == "closed" and js["title"] == "New title"
    row = ic.db(ic.db.issues.id == iss["id"]).select().first()
    assert (
        row.closed_at is not None
        and row.resource_id == ic.org2
        and row.assignee_id == ic.uid
    )
    st, js = await call(
        ic.app,
        "patch",
        f"{I}/{iss['id']}",
        ic.tok,
        json={"assignee_id": ic.uid, "start_date": None, "end_date": None},
    )
    assert st == 200
    st, js = await call(ic.app, "patch", f"{I}/{iss['id']}", ic.tok, json={})
    assert st == 200
    assert (
        await call(
            ic.app,
            "patch",
            f"{I}/{iss['id']}",
            ic.tok,
            json={"assignee_type": "identity"},
        )
    )[0] == 400
    assert (
        await call(
            ic.app, "patch", f"{I}/{iss['id']}", ic.tok, json={"assignee_id": 999999999}
        )
    )[0] == 404
    assert (
        await call(
            ic.app,
            "patch",
            f"{I}/{iss['id']}",
            ic.tok,
            json={"organization_id": ic.foreign_org},
        )
    )[0] == 404
    assert (
        await call(
            ic.app,
            "patch",
            f"{I}/{iss['id']}",
            ic.tok,
            json={
                "start_date": "2031-01-01T00:00:00Z",
                "end_date": "2030-01-01T00:00:00Z",
            },
        )
    )[0] == 400
    assert (
        await call(
            ic.app, "patch", "/api/v1/issues/999999999", ic.tok, json={"title": "x"}
        )
    )[0] == 404
    assert (
        await call(ic.app, "patch", f"{I}/{iss['id']}", ic.foreign, json={"title": "x"})
    )[0] == 404
    # delete
    assert (await call(ic.app, "delete", f"{I}/{org_assigned['id']}", ic.foreign))[
        0
    ] == 404
    assert (await call(ic.app, "delete", f"{I}/999999999", ic.tok))[0] == 404
    assert (await call(ic.app, "delete", f"{I}/{org_assigned['id']}", ic.tok))[0] == 204
    assert ic.db(ic.db.issues.id == org_assigned["id"]).count() == 0


async def test_create_blocked_by_licence_limit(ic, monkeypatch):
    """A tripped licence limit short-circuits issue creation with the limit's own response."""

    async def deny(kind, tenant_id):
        """Pretend the object limit has been reached."""
        return ir.jsonify({"error": "limit reached"}), 402

    monkeypatch.setattr(ir, "check_limit", deny)
    st, js = await call(
        ic.app, "post", I, ic.tok, json={"title": "t", "organization_id": ic.org}
    )
    assert st == 402 and js["error"] == "limit reached"


async def test_comments_via_issues_router(ic):
    """List/create/delete comments with author and tenant checks (issues router serves these)."""
    iss = await mk_issue(ic)
    url = f"{I}/{iss['id']}/comments"
    assert (await call(ic.app, "get", url, ic.tok))[1]["total"] == 0
    st, c = await call(ic.app, "post", url, ic.tok, json={"content": "hello"})
    assert st == 201
    assert (await call(ic.app, "post", url, ic.tok, json={}))[0] == 400
    assert (await call(ic.app, "get", url, ic.tok))[1]["total"] == 1
    assert (await call(ic.app, "get", url, ic.foreign))[0] == 404
    assert (
        await call(
            ic.app, "post", f"{I}/999999999/comments", ic.tok, json={"content": "x"}
        )
    )[0] == 404
    assert (await call(ic.app, "get", f"{I}/999999999/comments", ic.tok))[0] == 404
    assert (await call(ic.app, "delete", f"{url}/999999999", ic.tok))[0] == 404
    assert (await call(ic.app, "delete", f"{I}/999999999/comments/1", ic.tok))[0] == 404
    # author-only deletion for a plain identity
    plain = token(ic.app, ic.uid2, ic.tid, SC)
    assert (await call(ic.app, "delete", f"{url}/{c['id']}", plain))[0] == 403


async def test_labels_on_issues(ic):
    """Global label listing/creation and per-issue assign/unassign with duplicate protection."""
    iss = await mk_issue(ic)
    name = f"lab-{sfx()}"
    st, lab = await call(
        ic.app,
        "post",
        f"{I}/labels",
        ic.tok,
        json={"name": name, "color": "#112233", "description": "d"},
    )
    assert st == 201
    assert (
        await call(
            ic.app,
            "post",
            f"{I}/labels",
            ic.tok,
            json={"name": name, "color": "#112233"},
        )
    )[0] == 409
    assert (await call(ic.app, "post", f"{I}/labels", ic.tok, json={"name": "x"}))[
        0
    ] == 400
    st, js = await call(ic.app, "get", f"{I}/labels", ic.tok)
    assert st == 200 and js["total"] >= 1
    url = f"{I}/{iss['id']}/labels"
    assert (await call(ic.app, "post", url, ic.tok, json={"label_id": lab["id"]}))[
        0
    ] == 200
    assert (await call(ic.app, "post", url, ic.tok, json={"label_id": lab["id"]}))[
        0
    ] == 409
    assert (await call(ic.app, "post", url, ic.tok, json={"label_id": 999999999}))[
        0
    ] == 404
    assert (
        await call(
            ic.app,
            "post",
            f"{I}/999999999/labels",
            ic.tok,
            json={"label_id": lab["id"]},
        )
    )[0] == 404
    assert (await call(ic.app, "post", url, ic.tok, json={"label_id": 0}))[0] == 400
    assert (await call(ic.app, "get", f"{I}/999999999/labels", ic.tok))[0] == 404
    assert (await call(ic.app, "delete", f"{url}/{lab['id']}", ic.foreign))[0] == 404
    assert (await call(ic.app, "delete", f"{url}/{lab['id']}", ic.tok))[0] == 204
    assert (await call(ic.app, "delete", f"{url}/{lab['id']}", ic.tok))[0] == 404
    assert (await call(ic.app, "delete", f"{I}/999999999/labels/1", ic.tok))[0] == 404


def seed_link(ic: IC, issue_id: int, entity_id: int) -> int:
    """Insert an issue->entity link row directly with its required link type."""
    lid = ic.db.issue_entity_links.insert(
        issue_id=issue_id,
        entity_id=entity_id,
        link_type="RELATED",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    ic.db.commit()
    return lid


async def test_entity_links(ic):
    """Entity links: list, duplicate refusal and tenant checks."""
    iss = await mk_issue(ic)
    url = f"{I}/{iss['id']}/links"
    assert (await call(ic.app, "get", url, ic.tok))[1]["total"] == 0
    seed_link(ic, iss["id"], ic.entity)
    assert (await call(ic.app, "post", url, ic.tok, json={"entity_id": ic.entity}))[
        0
    ] == 409
    assert (
        await call(ic.app, "post", url, ic.tok, json={"entity_id": ic.foreign_entity})
    )[0] == 404
    assert (
        await call(
            ic.app,
            "post",
            f"{I}/999999999/links",
            ic.tok,
            json={"entity_id": ic.entity},
        )
    )[0] == 404
    assert (await call(ic.app, "post", url, ic.tok, json={}))[0] == 400
    st, js = await call(ic.app, "get", url, ic.tok)
    assert st == 200 and js["items"][0]["entity_name"]
    assert (await call(ic.app, "get", f"{I}/999999999/links", ic.tok))[0] == 404
    assert (await call(ic.app, "delete", f"{url}/999999999", ic.tok))[0] == 404
    assert (await call(ic.app, "delete", f"{I}/999999999/links/1", ic.tok))[0] == 404
    assert (
        await call(ic.app, "delete", f"{url}/by-entity/{ic.foreign_entity}", ic.tok)
    )[0] == 404
    assert (await call(ic.app, "delete", f"{I}/999999999/links/by-entity/1", ic.tok))[
        0
    ] == 404


@pytest.mark.xfail(
    strict=True,
    reason="bug: create_issue_entity_link inserts issue_entity_links without the NOT NULL link_type, so creation returns 500",
)
async def test_entity_link_create(ic):
    """Linking an entity to an issue should return 201 with the entity name."""
    iss = await mk_issue(ic)
    st, link = await call(
        ic.app, "post", f"{I}/{iss['id']}/links", ic.tok, json={"entity_id": ic.entity}
    )
    assert st == 201 and link["entity_id"] == ic.entity


@pytest.mark.xfail(
    strict=True,
    reason="bug: GET /issues/<id>/labels selects db.issue_labels.ALL, which penguin-dal does not support (500)",
)
async def test_issue_labels_listing(ic):
    """Listing an issue's labels should return the assigned labels."""
    iss = await mk_issue(ic)
    st, lab = await call(
        ic.app,
        "post",
        f"{I}/labels",
        ic.tok,
        json={"name": f"l-{sfx()}", "color": "#000000"},
    )
    await call(
        ic.app, "post", f"{I}/{iss['id']}/labels", ic.tok, json={"label_id": lab["id"]}
    )
    st, js = await call(ic.app, "get", f"{I}/{iss['id']}/labels", ic.tok)
    assert st == 200 and js["total"] == 1


async def test_resource_links(ic):
    """Resource links dedupe on (type, id) and are listed per issue."""
    iss = await mk_issue(ic)
    url = f"{I}/{iss['id']}/resources"
    assert (await call(ic.app, "get", url, ic.tok))[1] == {"resources": []}
    st, r = await call(
        ic.app, "post", url, ic.tok, json={"resource_type": " vault ", "resource_id": 5}
    )
    assert st == 201
    st, again = await call(
        ic.app, "post", url, ic.tok, json={"resource_type": "vault", "resource_id": 5}
    )
    assert st == 200 and again["id"] == r["id"]
    assert (
        await call(
            ic.app, "post", url, ic.tok, json={"resource_type": "   ", "resource_id": 5}
        )
    )[0] == 400
    assert (
        await call(
            ic.app,
            "post",
            url,
            ic.tok,
            json={"resource_type": "vault", "resource_id": 0},
        )
    )[0] == 400
    assert (
        await call(
            ic.app,
            "post",
            f"{I}/999999999/resources",
            ic.tok,
            json={"resource_type": "x", "resource_id": 1},
        )
    )[0] == 404
    assert len((await call(ic.app, "get", url, ic.tok))[1]["resources"]) == 1
    assert (await call(ic.app, "get", f"{I}/999999999/resources", ic.tok))[0] == 404
    assert (await call(ic.app, "delete", f"{url}/999999999", ic.tok))[0] == 404
    assert (await call(ic.app, "delete", f"{I}/999999999/resources/1", ic.tok))[
        0
    ] == 404


async def test_project_and_milestone_links(ic):
    """Issue->project and issue->milestone links reject duplicates and cross-tenant targets."""
    iss = await mk_issue(ic)
    for kind, key, target in (
        ("projects", "project_id", ic.project),
        ("milestones", "milestone_id", ic.milestone),
    ):
        url = f"{I}/{iss['id']}/{kind}"
        assert (await call(ic.app, "post", url, ic.tok, json={key: target}))[0] == 201
        assert (await call(ic.app, "post", url, ic.tok, json={key: target}))[0] == 400
        assert (await call(ic.app, "post", url, ic.tok, json={key: 999999999}))[
            0
        ] == 404
        assert (
            await call(
                ic.app, "post", f"{I}/999999999/{kind}", ic.tok, json={key: target}
            )
        )[0] == 404
        assert (await call(ic.app, "post", url, ic.tok, json={}))[0] == 400
        assert (await call(ic.app, "post", url, ic.foreign, json={key: target}))[
            0
        ] == 404
        assert (await call(ic.app, "delete", f"{url}/999999999", ic.tok))[0] == 404
        assert (await call(ic.app, "delete", f"{I}/999999999/{kind}/1", ic.tok))[
            0
        ] == 404
        assert (await call(ic.app, "delete", f"{url}/{target}", ic.foreign))[0] == 404


async def test_link_deletes_persist(ic):
    """Entity, resource, project and milestone link deletes should remove the row."""
    iss = await mk_issue(ic)
    lid = seed_link(ic, iss["id"], ic.entity)
    assert (await call(ic.app, "delete", f"{I}/{iss['id']}/links/{lid}", ic.tok))[
        0
    ] == 204
    seed_link(ic, iss["id"], ic.entity)
    assert (
        await call(
            ic.app, "delete", f"{I}/{iss['id']}/links/by-entity/{ic.entity}", ic.tok
        )
    )[0] == 204
    st, res = await call(
        ic.app,
        "post",
        f"{I}/{iss['id']}/resources",
        ic.tok,
        json={"resource_type": "v", "resource_id": 1},
    )
    assert (
        await call(ic.app, "delete", f"{I}/{iss['id']}/resources/{res['id']}", ic.tok)
    )[0] == 204
    await call(
        ic.app,
        "post",
        f"{I}/{iss['id']}/projects",
        ic.tok,
        json={"project_id": ic.project},
    )
    assert (
        await call(ic.app, "delete", f"{I}/{iss['id']}/projects/{ic.project}", ic.tok)
    )[0] == 204
    await call(
        ic.app,
        "post",
        f"{I}/{iss['id']}/milestones",
        ic.tok,
        json={"milestone_id": ic.milestone},
    )
    assert (
        await call(
            ic.app, "delete", f"{I}/{iss['id']}/milestones/{ic.milestone}", ic.tok
        )
    )[0] == 204


async def test_pure_helpers(ic):
    """Datetime normalisation, casing and assignee resolution helpers."""
    naive = datetime(2030, 1, 1)
    assert ir._to_utc(None) is None and ir._to_utc(naive).tzinfo is not None
    aware = datetime(2030, 1, 1, tzinfo=UTC)
    assert ir._to_utc(aware) == aware
    assert ir._org_unit_in_tenant(ic.db, None, ic.tid) is True
    assert ir._org_unit_in_tenant(ic.db, ic.org, ic.tid) is True
    assert ir._org_unit_in_tenant(ic.db, ic.foreign_org, ic.tid) is False
    assert ir._resolve_assignee_type(ic.db, ic.tid, None, None) == (None, True)
    assert ir._resolve_assignee_type(ic.db, ic.tid, ic.uid, None) == ("identity", True)
    assert ir._resolve_assignee_type(ic.db, ic.tid, ic.org, "org_unit") == (
        "org_unit",
        True,
    )
    assert ir._resolve_assignee_type(ic.db, ic.tid, ic.foreign_org, "org_unit") == (
        "org_unit",
        False,
    )
    assert isinstance(ir._mint_village_id(ic.tid, None), str)
    dto = SimpleNamespace()
    del dto
