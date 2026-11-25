"""HTTP-level behaviour tests for issue labels, comments, milestones and projects.

Requests run through the real Quart app and Postgres test database with
signed tokens; tenant isolation, ownership rules and persistence are asserted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from apps.api.modules.issues.routes import common
from tests.unit._cov_c_helpers import call, mk_identity, mk_org, mk_tenant, sfx, token

pytestmark = pytest.mark.asyncio
SC = ["issues:read", "issues:write"]


@dataclass
class IA:
    """Seeded tenants, identities and tokens."""

    app: Any
    db: Any
    tid: int
    org: int
    org2: int
    foreign_org: int
    uid: int
    uid2: int
    issue: int
    tok: str
    other_user: str
    ro: str
    foreign: str
    notenant: str


@pytest.fixture(scope="module")
def ia(app) -> IA:
    """Seed one tenant with an admin, a plain commenter and an issue, plus a foreign tenant."""
    db = app.db
    tid, other = mk_tenant(db, "ia"), mk_tenant(db, "iax")
    uid = mk_identity(db, tid, "admin", superuser=True)
    uid2 = mk_identity(db, tid, "observer")
    ro = mk_identity(db, tid, "observer")
    fid = mk_identity(db, other, "admin", superuser=True)
    org = mk_org(db, tid)
    now = datetime.now(UTC)
    issue = db.issues.insert(
        title="seed",
        description="d",
        status="OPEN",
        priority="MEDIUM",
        issue_type="OTHER",
        resource_type="organization",
        resource_id=org,
        is_incident=0,
        tenant_id=tid,
        created_at=now,
        updated_at=now,
    )
    db.commit()
    return IA(
        app,
        db,
        tid,
        org,
        mk_org(db, tid),
        mk_org(db, other),
        uid,
        uid2,
        issue,
        token(app, uid, tid, SC),
        token(app, uid2, tid, SC),
        token(app, ro, tid, ["issues:read"]),
        token(app, fid, other, SC),
        token(app, uid, "", SC),
    )


async def test_tenant_and_scope_guards(ia):
    """Tenant-less callers are 403 on tenant-scoped handlers; read-only callers cannot write."""
    cases = [
        ("get", "/api/v1/milestones"),
        ("post", "/api/v1/milestones"),
        ("get", "/api/v1/milestones/1"),
        ("put", "/api/v1/milestones/1"),
        ("delete", "/api/v1/milestones/1"),
        ("get", "/api/v1/milestones/1/issues"),
        ("get", "/api/v1/projects"),
        ("post", "/api/v1/projects"),
        ("get", "/api/v1/projects/1"),
        ("put", "/api/v1/projects/1"),
        ("delete", "/api/v1/projects/1"),
        ("get", "/api/v1/issues/1/comments"),
        ("post", "/api/v1/issues/1/comments"),
        ("delete", "/api/v1/issues/1/comments/1"),
        ("patch", "/api/v1/issues/1/comments/1"),
    ]
    for method, url in cases:
        body = {"content": "x"} if "comments" in url else {"title": "x", "name": "x"}
        kw = {"json": body} if method in ("post", "put", "patch") else {}
        st, _ = await call(ia.app, method, url, ia.notenant, **kw)
        assert st == 403, (method, url, st)
    for url in ("/api/v1/labels", "/api/v1/milestones", "/api/v1/projects"):
        assert (await call(ia.app, "post", url, ia.ro, json={"name": "x"}))[0] == 403


async def test_labels_lifecycle(ia):
    """Labels are global: create/list/search/get/update/delete with duplicate protection."""
    name = f"lbl-{sfx()}"
    st, lab = await call(
        ia.app,
        "post",
        "/api/v1/labels",
        ia.tok,
        json={"name": name, "description": "d", "color": "#ff0000"},
    )
    assert st == 201 and lab["color"] == "#ff0000"
    assert (await call(ia.app, "post", "/api/v1/labels", ia.tok, json={"name": name}))[
        0
    ] == 409
    st, js = await call(
        ia.app, "get", f"/api/v1/labels?search={name}&per_page=5", ia.tok
    )
    assert st == 200 and js["total"] == 1
    st, js = await call(
        ia.app, "get", "/api/v1/labels?search=zzz-no-such-label", ia.tok
    )
    assert js["total"] == 0 and js["pages"] == 0
    assert (await call(ia.app, "get", f"/api/v1/labels/{lab['id']}", ia.tok))[0] == 200
    assert (await call(ia.app, "get", "/api/v1/labels/999999999", ia.tok))[0] == 404
    other = (
        await call(
            ia.app, "post", "/api/v1/labels", ia.tok, json={"name": f"lbl-{sfx()}"}
        )
    )[1]
    assert (
        await call(
            ia.app,
            "put",
            f"/api/v1/labels/{lab['id']}",
            ia.tok,
            json={"name": other["name"]},
        )
    )[0] == 409
    assert (
        await call(
            ia.app, "put", "/api/v1/labels/999999999", ia.tok, json={"name": "x"}
        )
    )[0] == 404
    assert (await call(ia.app, "delete", "/api/v1/labels/999999999", ia.tok))[0] == 404


XF_TP = "bug: handler assigns through a TableProxy (db.<table>[id] = ...) which penguin-dal rejects, so the write returns 500"


@pytest.mark.xfail(strict=True, reason=XF_TP)
async def test_label_update_and_delete_persist(ia):
    """Updating and deleting a label should persist."""
    st, lab = await call(
        ia.app, "post", "/api/v1/labels", ia.tok, json={"name": f"lbl-{sfx()}"}
    )
    st, js = await call(
        ia.app,
        "put",
        f"/api/v1/labels/{lab['id']}",
        ia.tok,
        json={"name": f"new-{sfx()}", "description": "n", "color": "#00ff00"},
    )
    assert st == 200 and js["color"] == "#00ff00"
    assert (await call(ia.app, "delete", f"/api/v1/labels/{lab['id']}", ia.tok))[
        0
    ] == 204


async def test_comments_lifecycle(ia):
    """Comments are tenant-scoped to the issue, authored by the caller, and author/superuser gated."""
    url = f"/api/v1/issues/{ia.issue}/comments"
    st, c = await call(ia.app, "post", url, ia.other_user, json={"content": "first"})
    assert st == 201 and c["content"] == "first"
    st, js = await call(ia.app, "get", url, ia.tok)
    assert st == 200 and js["total"] >= 1
    assert (await call(ia.app, "post", url, ia.tok, json={}))[0] == 400
    assert (
        await call(
            ia.app,
            "post",
            "/api/v1/issues/999999999/comments",
            ia.tok,
            json={"content": "x"},
        )
    )[0] == 404
    assert (await call(ia.app, "get", "/api/v1/issues/999999999/comments", ia.tok))[
        0
    ] == 404
    assert (await call(ia.app, "get", url, ia.foreign))[0] == 404
    one = f"{url}/{c['id']}"
    # a non-author, non-superuser is refused; missing ids and foreign issues are 404
    mine = (await call(ia.app, "post", url, ia.tok, json={"content": "admin comment"}))[
        1
    ]
    assert (
        await call(
            ia.app,
            "patch",
            f"{url}/{mine['id']}",
            ia.other_user,
            json={"content": "hijack"},
        )
    )[0] == 403
    assert (await call(ia.app, "delete", f"{url}/{mine['id']}", ia.other_user))[
        0
    ] == 403
    assert (
        await call(ia.app, "patch", f"{url}/999999999", ia.tok, json={"content": "x"})
    )[0] == 404
    assert (await call(ia.app, "delete", f"{url}/999999999", ia.tok))[0] == 404
    assert (
        await call(
            ia.app,
            "patch",
            "/api/v1/issues/999999999/comments/1",
            ia.tok,
            json={"content": "x"},
        )
    )[0] == 404
    assert (
        await call(ia.app, "delete", "/api/v1/issues/999999999/comments/1", ia.tok)
    )[0] == 404
    assert (
        await call(
            ia.app, "patch", f"{url}/{c['id']}", ia.foreign, json={"content": "x"}
        )
    )[0] == 404


@pytest.mark.xfail(strict=True, reason=XF_TP)
async def test_comment_update_and_delete_persist(ia):
    """The author edits and deletes their own comment; a superuser may edit and delete anyone's."""
    url = f"/api/v1/issues/{ia.issue}/comments"
    st, c = await call(ia.app, "post", url, ia.other_user, json={"content": "first"})
    st, js = await call(
        ia.app, "patch", f"{url}/{c['id']}", ia.other_user, json={"content": "edited"}
    )
    assert st == 200 and js["content"] == "edited"
    assert (
        await call(
            ia.app, "patch", f"{url}/{c['id']}", ia.tok, json={"content": "by admin"}
        )
    )[0] == 200
    assert (await call(ia.app, "delete", f"{url}/{c['id']}", ia.tok))[0] == 204


async def test_milestones_lifecycle(ia):
    """Milestones: create/list/filter/get/update/delete with org ownership checks."""
    assert (await call(ia.app, "post", "/api/v1/milestones", ia.tok))[0] == 400
    assert (
        await call(
            ia.app,
            "post",
            "/api/v1/milestones",
            ia.tok,
            json={"organization_id": ia.org},
        )
    )[0] == 400
    assert (
        await call(ia.app, "post", "/api/v1/milestones", ia.tok, json={"title": "t"})
    )[0] == 400
    assert (
        await call(
            ia.app,
            "post",
            "/api/v1/milestones",
            ia.tok,
            json={"title": "t", "organization_id": ia.foreign_org},
        )
    )[0] == 404
    title = f"ms-{sfx()}"
    st, m = await call(
        ia.app,
        "post",
        "/api/v1/milestones",
        ia.tok,
        json={
            "title": title,
            "organization_id": ia.org,
            "description": "d",
            "status": "open",
        },
    )
    assert st == 201
    for q in (
        f"organization_id={ia.org}",
        "project_id=1",
        "status=open",
        f"search={title}",
    ):
        st, js = await call(ia.app, "get", f"/api/v1/milestones?{q}", ia.tok)
        assert st == 200, q
    assert (await call(ia.app, "get", f"/api/v1/milestones/{m['id']}", ia.tok))[
        0
    ] == 200
    assert (await call(ia.app, "get", f"/api/v1/milestones/{m['id']}", ia.foreign))[
        0
    ] == 404
    assert (await call(ia.app, "get", "/api/v1/milestones/999999999", ia.tok))[0] == 404
    assert (await call(ia.app, "put", f"/api/v1/milestones/{m['id']}", ia.tok))[
        0
    ] == 400
    assert (
        await call(
            ia.app,
            "put",
            f"/api/v1/milestones/{m['id']}",
            ia.tok,
            json={"organization_id": ia.foreign_org},
        )
    )[0] == 404
    assert (
        await call(
            ia.app, "put", "/api/v1/milestones/999999999", ia.tok, json={"title": "x"}
        )
    )[0] == 404
    st, js = await call(
        ia.app,
        "put",
        f"/api/v1/milestones/{m['id']}",
        ia.tok,
        json={
            "title": "renamed",
            "description": "n",
            "status": "closed",
            "project_id": None,
            "due_date": "2031-01-02",
            "organization_id": ia.org2,
        },
    )
    assert (
        st == 200
        and js["title"] == "renamed"
        and js["status"] == "closed"
        and js["organization_id"] == ia.org2
    )
    st, js = await call(
        ia.app,
        "put",
        f"/api/v1/milestones/{m['id']}",
        ia.tok,
        json={"closed_at": "2031-01-03T00:00:00Z"},
    )
    assert st == 200
    # issues linked to the milestone
    assert (await call(ia.app, "get", f"/api/v1/milestones/{m['id']}/issues", ia.tok))[
        1
    ] == {"issues": []}
    ia.db.issue_milestone_links.insert(
        issue_id=ia.issue, milestone_id=m["id"], created_at=datetime.now(UTC)
    )
    ia.db.commit()
    st, js = await call(ia.app, "get", f"/api/v1/milestones/{m['id']}/issues", ia.tok)
    assert st == 200 and [i["id"] for i in js["issues"]] == [ia.issue]
    assert (await call(ia.app, "get", "/api/v1/milestones/999999999/issues", ia.tok))[
        0
    ] == 404
    assert (await call(ia.app, "delete", f"/api/v1/milestones/{m['id']}", ia.foreign))[
        0
    ] == 404
    assert (await call(ia.app, "delete", "/api/v1/milestones/999999999", ia.tok))[
        0
    ] == 404


async def test_projects_lifecycle(ia):
    """Projects: create/list/filter/get/update/delete with org ownership checks."""
    assert (await call(ia.app, "post", "/api/v1/projects", ia.tok))[0] == 400
    assert (
        await call(
            ia.app, "post", "/api/v1/projects", ia.tok, json={"organization_id": ia.org}
        )
    )[0] == 400
    assert (await call(ia.app, "post", "/api/v1/projects", ia.tok, json={"name": "p"}))[
        0
    ] == 400
    assert (
        await call(
            ia.app,
            "post",
            "/api/v1/projects",
            ia.tok,
            json={"name": "p", "organization_id": ia.foreign_org},
        )
    )[0] == 404
    name = f"pr-{sfx()}"
    st, p = await call(
        ia.app,
        "post",
        "/api/v1/projects",
        ia.tok,
        json={"name": name, "organization_id": ia.org, "description": "d"},
    )
    assert st == 201
    for q in (f"organization_id={ia.org}", "status=active", f"search={name}"):
        assert (await call(ia.app, "get", f"/api/v1/projects?{q}", ia.tok))[0] == 200, q
    assert (await call(ia.app, "get", f"/api/v1/projects/{p['id']}", ia.tok))[0] == 200
    assert (await call(ia.app, "get", f"/api/v1/projects/{p['id']}", ia.foreign))[
        0
    ] == 404
    assert (await call(ia.app, "get", "/api/v1/projects/999999999", ia.tok))[0] == 404
    assert (await call(ia.app, "put", f"/api/v1/projects/{p['id']}", ia.tok))[0] == 400
    assert (
        await call(
            ia.app,
            "put",
            f"/api/v1/projects/{p['id']}",
            ia.tok,
            json={"organization_id": ia.foreign_org},
        )
    )[0] == 404
    assert (
        await call(
            ia.app, "put", "/api/v1/projects/999999999", ia.tok, json={"name": "x"}
        )
    )[0] == 404
    st, js = await call(
        ia.app,
        "put",
        f"/api/v1/projects/{p['id']}",
        ia.tok,
        json={
            "name": "renamed",
            "description": "n",
            "status": "archived",
            "organization_id": ia.org2,
        },
    )
    assert st == 200 and js["name"] == "renamed" and js["organization_id"] == ia.org2
    assert (await call(ia.app, "delete", f"/api/v1/projects/{p['id']}", ia.foreign))[
        0
    ] == 404
    assert (await call(ia.app, "delete", "/api/v1/projects/999999999", ia.tok))[
        0
    ] == 404
    assert (await call(ia.app, "delete", f"/api/v1/projects/{p['id']}", ia.tok))[
        0
    ] == 204
    assert (await call(ia.app, "delete", f"/api/v1/projects/{p['id']}", ia.tok))[
        0
    ] == 404


async def test_common_tenant_helpers(ia):
    """The shared issue helpers resolve in-tenant issues only."""
    assert common.get_tenant_scoped_issue(ia.db, ia.issue, ia.tid).id == ia.issue
    assert common.get_tenant_scoped_issue(ia.db, ia.issue, ia.tid + 1000) is None
