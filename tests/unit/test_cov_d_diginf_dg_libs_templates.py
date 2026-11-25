"""Route tests for diagram shape libraries and templates.

Real Quart app, JWTs and Postgres.  Ownership and cross-tenant behaviour is
asserted for read, update and delete paths.
"""

# flake8: noqa: E501

import pytest

from tests.unit._cov_d_helpers import call, in_db, now, world  # noqa: F401
from tests.unit.test_cov_d_diginf_dg_core import dw, tok  # noqa: F401

LIB = "/api/v1/diagram-libraries"
TPL = "/api/v1/diagram-templates"


@pytest.mark.asyncio
async def test_libraries(app, dw):  # noqa: F811
    """Library CRUD plus shape add/remove, visibility, ownership and tenant checks."""
    w = dw
    T = w["T"]
    assert (await call(app, "get", LIB))[0] == 401
    assert (await call(app, "post", LIB, T(), json={"name": " "}))[0] == 400
    s, a = await call(app, "post", LIB, T(), json={"name": "Priv", "description": "d"})
    assert s == 201, a
    s, p = await call(app, "post", LIB, T(), json={"name": "Pub", "is_public": True})
    assert s == 201
    aid = a["id"]
    s, d = await call(app, "get", LIB, T(), query_string={"per_page": 5})
    assert s == 200 and d["pagination"]["total"] == 2 and len(d["items"]) == 2
    s, d = await call(app, "get", LIB, T("other"))
    assert [i["id"] for i in d["items"]] == [p["id"]]
    assert (await call(app, "get", LIB, T("b")))[1]["items"] == []
    # get
    assert (await call(app, "get", f"{LIB}/99999999", T()))[0] == 404
    assert (await call(app, "get", f"{LIB}/{aid}", T("other")))[0] == 403
    assert (await call(app, "get", f"{LIB}/{aid}", T("b")))[0] == 403
    # shapes
    assert (await call(app, "post", f"{LIB}/{aid}/shapes", T(), json={"name": " "}))[
        0
    ] == 400
    assert (await call(app, "post", f"{LIB}/99999999/shapes", T(), json={"name": "s"}))[
        0
    ] == 404
    assert (
        await call(app, "post", f"{LIB}/{aid}/shapes", T("other"), json={"name": "s"})
    )[0] == 403
    s, sh = await call(
        app,
        "post",
        f"{LIB}/{aid}/shapes",
        T(),
        json={
            "name": "Box",
            "shape_type": "rect",
            "default_width": 10,
            "default_height": 5,
            "svg_content": "<g/>",
            "shape_meta": {"k": 1},
        },
    )
    assert s == 201, sh
    s, d = await call(app, "get", f"{LIB}/{aid}", T())
    assert s == 200 and [x["name"] for x in d["shapes"]] == ["Box"]
    assert (await call(app, "delete", f"{LIB}/99999999/shapes/{sh['id']}", T()))[
        0
    ] == 404
    assert (await call(app, "delete", f"{LIB}/{aid}/shapes/{sh['id']}", T("other")))[
        0
    ] == 403
    assert (await call(app, "delete", f"{LIB}/{aid}/shapes/99999999", T()))[0] == 404
    assert (await call(app, "delete", f"{LIB}/{aid}/shapes/{sh['id']}", T()))[0] == 204
    # update
    assert (await call(app, "patch", f"{LIB}/99999999", T(), json={"name": "x"}))[
        0
    ] == 404
    assert (await call(app, "patch", f"{LIB}/{aid}", T("other"), json={"name": "x"}))[
        0
    ] == 403
    s, u = await call(
        app,
        "patch",
        f"{LIB}/{aid}",
        T(),
        json={"name": "Priv2", "description": "dd", "is_public": True},
    )
    assert s == 200 and u["name"] == "Priv2" and u["is_public"] is True
    s, u = await call(app, "patch", f"{LIB}/{aid}", T(), json={"name": "   "})
    assert s == 200 and u["name"] == "Priv2"
    assert (await call(app, "get", f"{LIB}/{aid}", T("other")))[0] == 200
    # delete
    assert (await call(app, "delete", f"{LIB}/99999999", T()))[0] == 404
    assert (await call(app, "delete", f"{LIB}/{aid}", T("other")))[0] == 403
    assert (await call(app, "delete", f"{LIB}/{aid}", T()))[0] == 204


@pytest.mark.asyncio
async def test_templates(app, dw):  # noqa: F811
    """Template CRUD with validation, visibility, ownership and tenant checks."""
    w = dw
    T = w["T"]
    assert (await call(app, "get", TPL))[0] == 401
    assert (await call(app, "post", TPL, T(), json={"content": {}}))[0] == 400
    assert (await call(app, "post", TPL, T(), json={"name": "t"}))[0] == 400
    s, a = await call(
        app,
        "post",
        TPL,
        T(),
        json={
            "name": "Priv",
            "content": {"nodes": []},
            "category": "net",
            "thumbnail_url": "http://x",
        },
    )
    assert s == 201, a
    s, p = await call(
        app, "post", TPL, T(), json={"name": "Pub", "content": {}, "is_public": True}
    )
    assert s == 201
    tid = a["id"]
    s, d = await call(app, "get", TPL, T(), query_string={"per_page": 5})
    assert s == 200 and d["pagination"]["total"] == 2
    s, d = await call(app, "get", TPL, T("other"))
    assert [i["id"] for i in d["items"]] == [p["id"]]
    assert (await call(app, "get", TPL, T("b")))[1]["items"] == []
    # get
    assert (await call(app, "get", f"{TPL}/99999999", T()))[0] == 404
    assert (await call(app, "get", f"{TPL}/{tid}", T("other")))[0] == 403
    s, g = await call(app, "get", f"{TPL}/{tid}", T())
    assert s == 200 and g["content"] == {"nodes": []}
    # update
    assert (await call(app, "patch", f"{TPL}/99999999", T(), json={"name": "x"}))[
        0
    ] == 404
    assert (await call(app, "patch", f"{TPL}/{tid}", T("other"), json={"name": "x"}))[
        0
    ] == 403
    s, u = await call(
        app,
        "patch",
        f"{TPL}/{tid}",
        T(),
        json={
            "name": "N2",
            "content": {"nodes": [1]},
            "category": "x",
            "thumbnail_url": "u",
            "is_public": True,
        },
    )
    assert s == 200 and u["name"] == "N2" and u["is_public"] is True
    s, u = await call(app, "patch", f"{TPL}/{tid}", T(), json={"name": " "})
    assert s == 200 and u["name"] == "N2"
    assert (await call(app, "get", f"{TPL}/{tid}", T("other")))[0] == 200
    # delete
    assert (await call(app, "delete", f"{TPL}/99999999", T()))[0] == 404
    assert (await call(app, "delete", f"{TPL}/{tid}", T("other")))[0] == 403
    assert (await call(app, "delete", f"{TPL}/{tid}", T()))[0] == 204
