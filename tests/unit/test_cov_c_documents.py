"""HTTP-level behaviour tests for the documents module (collections, documents, versions).

Requests go through the real Quart app and Postgres test database with real
signed tokens; the tests assert status codes, response bodies and the rows
that end up in the database, including tenant isolation and visibility rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from apps.api.modules.documents import common as dcommon
from tests.unit._cov_c_helpers import call, mk_identity, mk_tenant, sfx, token

pytestmark = pytest.mark.asyncio

RW = ["documents:read", "documents:write"]


@dataclass
class DW:
    """Seeded tenant, two identities and ready-made tokens."""

    app: Any
    db: Any
    tid: int
    other_tid: int
    uid: int
    uid2: int
    foreign_uid: int
    tok: str
    tok2: str
    ro: str
    nos: str
    foreign: str


@pytest.fixture(scope="module")
def dw(app) -> DW:
    """Seed a tenant with two identities plus a foreign tenant."""
    db = app.db
    tid, other = mk_tenant(db, "doc"), mk_tenant(db, "docx")
    uid, uid2 = mk_identity(db, tid, "admin"), mk_identity(db, tid, "observer")
    fid = mk_identity(db, other, "admin")
    return DW(
        app,
        db,
        tid,
        other,
        uid,
        uid2,
        fid,
        token(app, uid, tid, RW, roles=["editor"]),
        token(app, uid2, tid, RW, roles=["viewer"]),
        token(app, uid, tid, ["documents:read"]),
        token(app, uid, tid, []),
        token(app, fid, other, RW),
    )


async def mk_doc(dw: DW, title: str | None = None, **body: Any) -> dict[str, Any]:
    """Create a document through the API and return its JSON."""
    payload = {
        "title": title or f"Doc {sfx()}",
        "body": "# Hello\n\nworld <script>x</script>",
    }
    payload.update(body)
    st, js = await call(dw.app, "post", "/api/v1/documents", dw.tok, json=payload)
    assert st == 201, js
    return js


async def test_scope_and_auth_rejections(dw):
    """Missing token is 401 and missing scope is 403 across the three routers."""
    assert (await call(dw.app, "get", "/api/v1/documents"))[0] == 401
    for url in (
        "/api/v1/documents",
        "/api/v1/collections",
        "/api/v1/documents/1/versions",
    ):
        assert (await call(dw.app, "get", url, dw.nos))[0] == 403
    assert (await call(dw.app, "post", "/api/v1/documents", dw.ro, json={}))[0] == 403


async def test_no_tenant_claim_gets_403(dw):
    """A token without a tenant claim is refused with 403 on every handler family."""
    tok = token(dw.app, dw.uid, "", RW)
    for method, url in (
        ("get", "/api/v1/documents"),
        ("post", "/api/v1/documents"),
        ("get", "/api/v1/documents/slug"),
        ("patch", "/api/v1/documents/1"),
        ("delete", "/api/v1/documents/1"),
        ("post", "/api/v1/documents/1/publish"),
        ("get", "/api/v1/collections"),
        ("post", "/api/v1/collections"),
        ("get", "/api/v1/collections/1"),
        ("patch", "/api/v1/collections/1"),
        ("delete", "/api/v1/collections/1"),
        ("post", "/api/v1/collections/1/documents/1"),
        ("delete", "/api/v1/collections/1/documents/1"),
        ("get", "/api/v1/documents/1/versions"),
        ("get", "/api/v1/documents/1/versions/1"),
        ("post", "/api/v1/documents/1/versions/1/restore"),
    ):
        kw = {"json": {}} if method in ("post", "patch") else {}
        st, _ = await call(dw.app, method, url, tok, **kw)
        assert st == 403, (method, url, st)


async def test_document_lifecycle(dw):
    """Create, read, list, update, publish, version and delete a document."""
    doc = await mk_doc(dw, category="ops", tags=["a"])
    assert doc["status"] == "draft" and doc["visibility"] == "authenticated"
    slug = doc["slug"]
    st, got = await call(dw.app, "get", f"/api/v1/documents/{slug}", dw.tok)
    assert st == 200 and "<script" not in got["body_html"] and got["body_text"]
    # drafts hidden by default, visible with status filter
    st, js = await call(dw.app, "get", "/api/v1/documents", dw.tok)
    assert slug not in [i["slug"] for i in js["items"]]
    st, js = await call(
        dw.app, "get", "/api/v1/documents?status=draft&category=ops&q=Hello", dw.tok
    )
    assert slug in [i["slug"] for i in js["items"]]
    st, js = await call(
        dw.app, "post", f"/api/v1/documents/{doc['id']}/publish", dw.tok
    )
    assert st == 200 and js["status"] == "published" and js["published_at"]
    st, js = await call(dw.app, "get", "/api/v1/documents?q=Hello", dw.tok)
    assert slug in [i["slug"] for i in js["items"]] and js["pagination"]["total"] >= 1
    # update: title/body/category/tags/visibility; snapshots a version
    new_title = f"Renamed {sfx()}"
    st, js = await call(
        dw.app,
        "patch",
        f"/api/v1/documents/{doc['id']}",
        dw.tok,
        json={
            "title": new_title,
            "body": "new *body*",
            "category": "x",
            "tags": ["z"],
            "visibility": "public",
        },
    )
    assert st == 200 and js["is_public"] is True and js["slug"] != slug
    st, js = await call(
        dw.app, "get", f"/api/v1/documents/{doc['id']}/versions", dw.tok
    )
    assert st == 200 and js["pagination"]["total"] == 1
    st, v = await call(
        dw.app, "get", f"/api/v1/documents/{doc['id']}/versions/1", dw.tok
    )
    assert st == 200 and v["body_markdown"].startswith("# Hello")
    st, r = await call(
        dw.app, "post", f"/api/v1/documents/{doc['id']}/versions/1/restore", dw.tok
    )
    assert (
        st == 200 and r["restored_from_version"] == 1 and r["new_version_number"] == 2
    )
    assert (
        await call(dw.app, "get", f"/api/v1/documents/{doc['id']}/versions/9", dw.tok)
    )[0] == 404
    assert (
        await call(
            dw.app, "post", f"/api/v1/documents/{doc['id']}/versions/9/restore", dw.tok
        )
    )[0] == 404
    st, _ = await call(dw.app, "delete", f"/api/v1/documents/{doc['id']}", dw.tok)
    assert st == 204
    assert dw.db(dw.db.doc_versions.doc_document_id == doc["id"]).count() == 0
    assert (await call(dw.app, "delete", f"/api/v1/documents/{doc['id']}", dw.tok))[
        0
    ] == 404


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"title": "", "body": "x"}, 400),
        ({"title": "T", "body": ""}, 400),
        ({"title": "!!!", "body": "x"}, 400),
        ({"title": "T", "body": "x", "visibility": "bogus"}, 400),
        ({"title": "T", "body": "x", "visibility": "roles"}, 400),
        ({"title": "T", "body": "x", "visibility": "users"}, 400),
        (
            {
                "title": "T",
                "body": "x",
                "visibility": "users",
                "visibility_users": [999999999],
            },
            400,
        ),
    ],
)
async def test_create_document_validation(dw, payload, status):
    """Invalid create payloads are rejected with 400 and nothing is stored."""
    before = dw.db(dw.db.doc_documents.tenant_id == dw.tid).count()
    st, _ = await call(dw.app, "post", "/api/v1/documents", dw.tok, json=payload)
    assert st == status
    assert dw.db(dw.db.doc_documents.tenant_id == dw.tid).count() == before


async def test_create_document_conflicts_and_identity(dw):
    """Creating a document with an existing slug is a 409."""
    doc = await mk_doc(dw)
    st, _ = await call(
        dw.app,
        "post",
        "/api/v1/documents",
        dw.tok,
        json={"title": doc["title"], "body": "b"},
    )
    assert st == 409


async def test_visibility_rules(dw):
    """Role and user visibility gate reads; restricted docs are 404 to outsiders."""
    roles_doc = await mk_doc(dw, visibility="roles", visibility_roles=["editor"])
    users_doc = await mk_doc(dw, visibility="users", visibility_users=[dw.uid])
    pub = await mk_doc(dw, visibility="public")
    st, _ = await call(dw.app, "get", f"/api/v1/documents/{roles_doc['slug']}", dw.tok)
    assert st == 200
    st, _ = await call(dw.app, "get", f"/api/v1/documents/{roles_doc['slug']}", dw.tok2)
    assert st == 404
    assert (
        await call(dw.app, "get", f"/api/v1/documents/{users_doc['slug']}", dw.tok)
    )[0] == 200
    assert (
        await call(dw.app, "get", f"/api/v1/documents/{users_doc['slug']}", dw.tok2)
    )[0] == 404
    # restricted doc: outsiders cannot version, update, publish, delete or restore it
    i = users_doc["id"]
    assert (await call(dw.app, "get", f"/api/v1/documents/{i}/versions", dw.tok2))[
        0
    ] == 404
    assert (await call(dw.app, "get", f"/api/v1/documents/{i}/versions/1", dw.tok2))[
        0
    ] == 404
    assert (
        await call(dw.app, "post", f"/api/v1/documents/{i}/versions/1/restore", dw.tok2)
    )[0] == 404
    assert (
        await call(
            dw.app, "patch", f"/api/v1/documents/{i}", dw.tok2, json={"category": "q"}
        )
    )[0] == 404
    assert (await call(dw.app, "post", f"/api/v1/documents/{i}/publish", dw.tok2))[
        0
    ] == 404
    assert (await call(dw.app, "delete", f"/api/v1/documents/{i}", dw.tok2))[0] == 404
    # list post-filters restricted docs for the outsider
    st, js = await call(
        dw.app, "get", "/api/v1/documents?status=draft&per_page=100", dw.tok2
    )
    slugs = {x["slug"] for x in js["items"]}
    assert users_doc["slug"] not in slugs and pub["slug"] in slugs
    # direct unit checks of fail-closed branches
    row = dw.db(dw.db.doc_documents.id == roles_doc["id"]).select().first()
    from apps.api.modules.documents.routes.documents import _can_read_document

    dw.db(dw.db.doc_documents.id == roles_doc["id"]).update(visibility_roles=[])
    dw.db.commit()
    row = dw.db(dw.db.doc_documents.id == roles_doc["id"]).select().first()
    assert _can_read_document(dw.db, row, dw.tid, dw.uid, ["editor"]) is False
    dw.db(dw.db.doc_documents.id == roles_doc["id"]).update(visibility="mystery")
    dw.db.commit()
    row = dw.db(dw.db.doc_documents.id == roles_doc["id"]).select().first()
    assert _can_read_document(dw.db, row, dw.tid, dw.uid, []) is False
    assert _can_read_document(dw.db, row, dw.tid + 12345, dw.uid, []) is False


async def test_update_document_errors(dw):
    """Update validates visibility, slug collisions, tenant membership and existence."""
    a, b = await mk_doc(dw), await mk_doc(dw)
    url = f"/api/v1/documents/{a['id']}"
    st, _ = await call(dw.app, "patch", url, dw.tok, json={"title": b["title"]})
    assert st == 409
    assert (await call(dw.app, "patch", url, dw.tok, json={"visibility": "nope"}))[
        0
    ] == 400
    st, _ = await call(
        dw.app, "patch", url, dw.tok, json={"visibility_users": [999999999]}
    )
    assert st == 400
    st, js = await call(
        dw.app, "patch", url, dw.tok, json={"title": "  ", "body": "  "}
    )
    assert st == 200 and js["title"] == a["title"]
    assert (
        await call(dw.app, "patch", "/api/v1/documents/999999999", dw.tok, json={})
    )[0] == 404
    assert (await call(dw.app, "patch", url, dw.foreign, json={"category": "x"}))[
        0
    ] == 404
    assert (await call(dw.app, "post", "/api/v1/documents/999999999/publish", dw.tok))[
        0
    ] == 404


async def test_public_endpoints(dw):
    """Public listing and fetch need no auth and only expose published public docs."""
    doc = await mk_doc(dw, visibility="public", category="pubcat")
    await call(dw.app, "post", f"/api/v1/documents/{doc['id']}/publish", dw.tok)
    st, js = await call(
        dw.app, "get", "/api/v1/documents/public?category=pubcat&q=Hello"
    )
    assert st == 200 and doc["slug"] in [i["slug"] for i in js["items"]]
    st, js = await call(dw.app, "get", f"/api/v1/documents/public/{doc['slug']}")
    assert st == 200 and js["slug"] == doc["slug"]


@pytest.mark.xfail(
    strict=True,
    reason="bug: get_public_document returns a nested (response, 404) tuple, giving a 500 instead of 404",
)
async def test_public_document_missing_is_404(dw):
    """A missing or unpublished public slug should answer 404."""
    assert (await call(dw.app, "get", "/api/v1/documents/public/does-not-exist"))[
        0
    ] == 404


async def test_get_document_cross_tenant_is_404(dw):
    """A slug from another tenant is indistinguishable from a missing one."""
    doc = await mk_doc(dw)
    assert (await call(dw.app, "get", f"/api/v1/documents/{doc['slug']}", dw.foreign))[
        0
    ] == 404
    assert (await call(dw.app, "get", "/api/v1/documents/missing-slug", dw.tok))[
        0
    ] == 404
    assert (await call(dw.app, "get", "/api/v1/documents/999999999/versions", dw.tok))[
        0
    ] == 404
    assert (
        await call(dw.app, "get", "/api/v1/documents/999999999/versions/1", dw.tok)
    )[0] == 404
    assert (
        await call(
            dw.app, "post", "/api/v1/documents/999999999/versions/1/restore", dw.tok
        )
    )[0] == 404


async def test_collections_lifecycle(dw):
    """Collections form a tree, attach/detach documents and refuse unsafe deletes."""
    name = f"Root {sfx()}"
    st, root = await call(
        dw.app,
        "post",
        "/api/v1/collections",
        dw.tok,
        json={"name": name, "description": "d"},
    )
    assert st == 201 and root["slug"]
    st, child = await call(
        dw.app,
        "post",
        "/api/v1/collections",
        dw.tok,
        json={"name": f"Kid {sfx()}", "slug": f"kid-{sfx()}", "parent_id": root["id"]},
    )
    assert st == 201 and child["parent_id"] == root["id"]
    st, js = await call(dw.app, "get", "/api/v1/collections", dw.tok)
    node = next(c for c in js["collections"] if c["id"] == root["id"])
    assert [c["id"] for c in node["children"]] == [child["id"]]
    assert (await call(dw.app, "get", f"/api/v1/collections/{root['id']}", dw.tok))[
        0
    ] == 200
    assert (await call(dw.app, "get", "/api/v1/collections/999999999", dw.tok))[
        0
    ] == 404
    assert (await call(dw.app, "get", f"/api/v1/collections/{root['id']}", dw.foreign))[
        0
    ] == 404
    # validation
    assert (
        await call(dw.app, "post", "/api/v1/collections", dw.tok, json={"name": " "})
    )[0] == 400
    assert (
        await call(
            dw.app,
            "post",
            "/api/v1/collections",
            dw.tok,
            json={"name": "x", "parent_id": 999999999},
        )
    )[0] == 404
    # update
    st, js = await call(
        dw.app,
        "patch",
        f"/api/v1/collections/{child['id']}",
        dw.tok,
        json={
            "name": "Renamed",
            "slug": f"r-{sfx()}",
            "description": "n",
            "parent_id": None,
        },
    )
    assert st == 200 and js["parent_id"] is None and js["name"] == "Renamed"
    st, _ = await call(
        dw.app,
        "patch",
        f"/api/v1/collections/{child['id']}",
        dw.tok,
        json={"parent_id": 999999999},
    )
    assert st == 404
    st, js = await call(
        dw.app,
        "patch",
        f"/api/v1/collections/{child['id']}",
        dw.tok,
        json={"parent_id": root["id"]},
    )
    assert st == 200 and js["parent_id"] == root["id"]
    assert (
        await call(dw.app, "patch", "/api/v1/collections/999999999", dw.tok, json={})
    )[0] == 404
    # attach/detach
    doc = await mk_doc(dw)
    url = f"/api/v1/collections/{root['id']}/documents/{doc['id']}"
    assert (await call(dw.app, "post", url, dw.tok))[0] == 200
    assert (await call(dw.app, "post", url, dw.tok))[0] == 409
    assert (
        await call(
            dw.app,
            "post",
            f"/api/v1/collections/999999999/documents/{doc['id']}",
            dw.tok,
        )
    )[0] == 404
    assert (
        await call(
            dw.app,
            "post",
            f"/api/v1/collections/{root['id']}/documents/999999999",
            dw.tok,
        )
    )[0] == 404
    # delete guards
    assert (await call(dw.app, "delete", f"/api/v1/collections/{root['id']}", dw.tok))[
        0
    ] == 400
    assert (await call(dw.app, "delete", url, dw.tok))[0] == 204
    assert (await call(dw.app, "delete", url, dw.tok))[0] == 404
    assert (
        await call(
            dw.app,
            "delete",
            f"/api/v1/collections/999999999/documents/{doc['id']}",
            dw.tok,
        )
    )[0] == 404
    assert (await call(dw.app, "delete", f"/api/v1/collections/{root['id']}", dw.tok))[
        0
    ] == 400  # has child
    assert (await call(dw.app, "delete", f"/api/v1/collections/{child['id']}", dw.tok))[
        0
    ] == 204
    assert (await call(dw.app, "delete", f"/api/v1/collections/{root['id']}", dw.tok))[
        0
    ] == 204
    assert (await call(dw.app, "delete", f"/api/v1/collections/{root['id']}", dw.tok))[
        0
    ] == 404
    # non-empty collection refuses delete
    st, c2 = await call(
        dw.app, "post", "/api/v1/collections", dw.tok, json={"name": f"Full {sfx()}"}
    )
    await call(
        dw.app, "post", f"/api/v1/collections/{c2['id']}/documents/{doc['id']}", dw.tok
    )
    assert (await call(dw.app, "delete", f"/api/v1/collections/{c2['id']}", dw.tok))[
        0
    ] == 400


async def test_common_helpers(dw):
    """slugify normalises titles and visibility_users_in_tenant checks membership."""
    assert dcommon.slugify("  Hello,  World -- Ok! ") == "hello-world-ok"
    assert dcommon.visibility_users_in_tenant(dw.db, None, dw.tid) is True
    assert dcommon.visibility_users_in_tenant(dw.db, [dw.uid], dw.tid) is True
    assert dcommon.visibility_users_in_tenant(dw.db, [dw.foreign_uid], dw.tid) is False
