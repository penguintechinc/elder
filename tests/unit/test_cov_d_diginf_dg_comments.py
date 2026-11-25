"""Route tests for diagram comments and replies.

Real Quart app, JWTs and Postgres; author/editor rules and tenant isolation
are asserted for every mutation.
"""

# flake8: noqa: E501

import pytest

from tests.unit._cov_d_helpers import call, in_db, now, world  # noqa: F401
from tests.unit.test_cov_d_diginf_dg_core import dw, tok  # noqa: F401

DG = "/api/v1/diagrams"


async def _diagram(app, w, **kw):
    """Create a diagram owned by tenant A's primary user and return its id."""
    s, d = await call(app, "post", DG, w["T"](), json={"title": "C", **kw})
    assert s == 201, d
    return d["id"]


@pytest.mark.asyncio
async def test_comments_flow(app, dw):  # noqa: F811
    """Create/list/reply/update/delete with author, editor, reader and foreign callers."""
    w = dw
    T = w["T"]
    d = await _diagram(app, w)
    pub = await _diagram(app, w, is_public=True)
    url = f"{DG}/{d}/comments"
    assert (await call(app, "get", url))[0] == 401
    # validation / permission
    assert (await call(app, "post", url, T(), json={"text_content": " "}))[0] == 400
    assert (
        await call(
            app, "post", f"{DG}/99999999/comments", T(), json={"text_content": "x"}
        )
    )[0] == 404
    assert (await call(app, "post", url, T("other"), json={"text_content": "x"}))[
        0
    ] == 403
    assert (await call(app, "post", url, T("b"), json={"text_content": "x"}))[0] == 403
    s, c = await call(
        app,
        "post",
        url,
        T(),
        json={"text_content": "hello", "x_position": 1.5, "y_position": 2.5},
    )
    assert s == 201 and c["replies"] == []
    cid = c["id"]
    # public diagram lets 'other' read+comment
    s, oc = await call(
        app,
        "post",
        f"{DG}/{pub}/comments",
        T("other"),
        json={"text_content": "from-other"},
    )
    assert s == 201
    # replies
    rurl = f"{url}/{cid}/replies"
    assert (await call(app, "post", rurl, T(), json={"text_content": " "}))[0] == 400
    assert (
        await call(
            app,
            "post",
            f"{DG}/99999999/comments/{cid}/replies",
            T(),
            json={"text_content": "x"},
        )
    )[0] == 404
    assert (await call(app, "post", rurl, T("other"), json={"text_content": "x"}))[
        0
    ] == 403
    assert (
        await call(
            app, "post", f"{url}/99999999/replies", T(), json={"text_content": "x"}
        )
    )[0] == 404
    s, r = await call(app, "post", rurl, T(), json={"text_content": "reply"})
    assert s == 201 and r["comment_id"] == cid
    # list
    s, lst = await call(app, "get", url, T(), query_string={"per_page": 5})
    assert (
        s == 200
        and lst["pagination"]["total"] == 1
        and lst["items"][0]["replies"][0]["text_content"] == "reply"
    )
    # update: other (reader of public, not author, not editor) forbidden
    assert (await call(app, "patch", f"{DG}/99999999/comments/{cid}", T(), json={}))[
        0
    ] == 404
    assert (await call(app, "patch", f"{url}/99999999", T(), json={}))[0] == 404
    assert (
        await call(
            app,
            "patch",
            f"{DG}/{pub}/comments/{oc['id']}",
            T("a"),
            json={"is_resolved": True},
        )
    )[0] == 200  # owner is an editor
    assert (
        await call(app, "patch", f"{url}/{cid}", T("other"), json={"text_content": "x"})
    )[0] == 403
    assert (
        await call(app, "patch", f"{url}/{cid}", T("b"), json={"text_content": "x"})
    )[0] == 404
    s, u = await call(
        app,
        "patch",
        f"{url}/{cid}",
        T(),
        json={"text_content": "edited", "is_resolved": True},
    )
    assert s == 200 and u["text_content"] == "edited" and u["is_resolved"] is True
    s, u = await call(app, "patch", f"{url}/{cid}", T(), json={"text_content": "  "})
    assert s == 200 and u["text_content"] == "edited"
    # author (other) can update own comment on public diagram
    s, u = await call(
        app,
        "patch",
        f"{DG}/{pub}/comments/{oc['id']}",
        T("other"),
        json={"text_content": "mine"},
    )
    assert s == 200 and u["text_content"] == "mine"
    # a different non-author, non-editor reader is forbidden: owner's comment on public diagram
    s, oc2 = await call(
        app, "post", f"{DG}/{pub}/comments", T("a"), json={"text_content": "owner-says"}
    )
    assert (
        await call(
            app,
            "patch",
            f"{DG}/{pub}/comments/{oc2['id']}",
            T("other"),
            json={"text_content": "x"},
        )
    )[0] == 403
    assert (await call(app, "delete", f"{DG}/{pub}/comments/{oc2['id']}", T("other")))[
        0
    ] == 403
    # delete
    assert (await call(app, "delete", f"{DG}/99999999/comments/{cid}", T()))[0] == 404
    assert (await call(app, "delete", f"{url}/99999999", T()))[0] == 404
    assert (await call(app, "delete", f"{url}/{cid}", T("b")))[0] == 404
    assert (await call(app, "delete", f"{DG}/{pub}/comments/{oc['id']}", T("other")))[
        0
    ] == 204
    assert (await call(app, "delete", f"{url}/{cid}", T()))[0] == 204


@pytest.mark.xfail(
    strict=True,
    reason="bug: list_comments unpacks a None result for a missing/unreadable diagram, returning 500 instead of 404/403",
)
@pytest.mark.asyncio
async def test_list_comments_missing_or_forbidden(app, dw):  # noqa: F811
    """Listing comments of a missing or unreadable diagram should be a 404/403, not a 500."""
    w = dw
    d = await _diagram(app, w)
    assert (await call(app, "get", f"{DG}/99999999/comments", w["T"]()))[0] == 404
    assert (await call(app, "get", f"{DG}/{d}/comments", w["T"]("other")))[0] == 403
