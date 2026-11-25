"""Route tests for diagram export and storage-provider configuration APIs.

Real Quart app, JWTs and Postgres.  The PostHog flag lookup (an external
boundary) is patched for the raster-export gate.
"""

# flake8: noqa: E501

import json
from unittest.mock import patch

import pytest

from apps.api.modules.diagrams.routes import export as exp
from apps.api.modules.diagrams.routes import storage_providers as sp
from tests.unit._cov_d_helpers import call, fake_secret, in_db, now, world  # noqa: F401
from tests.unit.test_cov_d_diginf_dg_core import dw, tok  # noqa: F401

DG = "/api/v1/diagrams"
ST = "/api/v1/diagram-storage"


def test_build_svg_branches():
    """Cover SVG building: non-dict, malformed nodes/edges, clamping, escaping and edges."""
    assert "Empty diagram" in exp._build_svg_from_content(None)
    assert "Empty diagram" in exp._build_svg_from_content([1])
    svg = exp._build_svg_from_content(
        {
            "nodes": [
                "junk",
                {
                    "id": "a",
                    "x": -5,
                    "y": 9999,
                    "width": 5,
                    "height": 9999,
                    "label": "<b>&",
                },
                {"id": "b", "x": 300, "y": 100},
                {"id": "bad", "x": "nope"},
                {"x": 1},
            ],
            "edges": [
                "junk",
                {"source": "a", "target": "b"},
                {"source": "a", "target": "zzz"},
                {"source": "a", "target": "bad"},
            ],
        }
    )
    assert (
        "&lt;b&gt;&amp;" in svg and svg.count("<line") >= 1 and svg.endswith("</svg>")
    )
    assert "<svg" in exp._build_svg_from_content({})


@pytest.mark.asyncio
async def test_export_routes(app, dw):  # noqa: F811
    """JSON/SVG/raster export with visibility, missing-version and flag paths."""
    w = dw
    T = w["T"]
    s, d = await call(
        app,
        "post",
        DG,
        T(),
        json={
            "title": "E",
            "content": {
                "nodes": [{"id": "a", "label": "A"}, {"id": "b"}],
                "edges": [{"source": "a", "target": "b"}],
            },
        },
    )
    did = d["id"]
    s, e = await call(app, "post", DG, T(), json={"title": "NoVersion"})
    nov = e["id"]
    assert (await call(app, "get", f"{DG}/{did}/export/json"))[0] == 401
    s, body = await call(app, "get", f"{DG}/{did}/export/json", T())
    assert s == 200 and body["nodes"][0]["id"] == "a"
    assert (await call(app, "get", f"{DG}/{nov}/export/json", T()))[0] == 404
    assert (await call(app, "get", f"{DG}/99999999/export/json", T()))[0] == 404
    assert (await call(app, "get", f"{DG}/{did}/export/json", T("other")))[0] == 404
    assert (await call(app, "get", f"{DG}/{did}/export/json", T("b")))[0] == 404
    s, body = await call(app, "get", f"{DG}/{did}/export/svg", T())
    assert s == 200 and b"<svg" in body
    s, body = await call(app, "get", f"{DG}/{nov}/export/svg", T())
    assert s == 200 and b"<svg" in body
    assert (await call(app, "get", f"{DG}/99999999/export/svg", T()))[0] == 404
    assert (await call(app, "get", f"{DG}/{did}/export/svg", T("other")))[0] == 404
    # raster
    assert (await call(app, "get", f"{DG}/{did}/export/gif", T()))[0] == 400
    with patch.object(exp, "flag_enabled", return_value=False):
        s, d = await call(app, "get", f"{DG}/{did}/export/PNG", T())
        assert s == 501 and "not enabled" in d["error"]
    with patch.object(exp, "flag_enabled", return_value=True):
        s, d = await call(app, "get", f"{DG}/{did}/export/pdf", T())
        assert s == 501 and "not yet implemented" in d["error"]


def test_redact_and_format():
    """Secret redaction handles nested dicts/lists and non-dict input."""
    assert sp._redact_secrets("x") == "x"
    out = sp._redact_secrets(
        {
            "endpoint": "e",
            "secret_key": "s",
            "nested": {"password": "p", "ok": 1},
            "items": [{"token": "t"}, "plain"],
        }
    )
    assert out["secret_key"] == "***" and out["nested"] == {"password": "***", "ok": 1}
    assert out["items"] == [{"token": "***"}, "plain"] and out["endpoint"] == "e"


@pytest.mark.asyncio
async def test_storage_providers(app, dw):  # noqa: F811
    """Provider CRUD with validation, admin-only system default, redaction and test endpoint."""
    w = dw
    T = w["T"]
    cfg = {
        "endpoint": "http://minio",
        "bucket": "b",
        "access_key": fake_secret("ak"),
        "secret_key": fake_secret("sk"),
    }
    assert (await call(app, "get", ST))[0] == 401
    assert (
        await call(
            app, "post", ST, T(), json={"provider_type": "s3", "config_json": cfg}
        )
    )[0] == 400
    assert (
        await call(
            app,
            "post",
            ST,
            T(),
            json={"name": "n", "provider_type": "ftp", "config_json": cfg},
        )
    )[0] == 400
    assert (
        await call(app, "post", ST, T(), json={"name": "n", "provider_type": "s3"})
    )[0] == 400
    assert (
        await call(
            app,
            "post",
            ST,
            T(),
            json={"name": "n", "provider_type": "s3", "config_json": "x"},
        )
    )[0] == 400
    s, p = await call(
        app,
        "post",
        ST,
        T(),
        json={
            "name": "Mine",
            "provider_type": "S3",
            "config_json": cfg,
            "storage_config": {"prefix": "x", "api_token": "t"},
            "is_system_default": True,
        },
    )
    assert s == 201, p
    assert (
        p["is_system_default"] is True
        and p["config_json"]["secret_key"] == "***"
        and p["storage_config"]["api_token"] == "***"
    )
    pid = p["id"]
    s, q = await call(
        app,
        "post",
        ST,
        T("other", roles=()),
        json={
            "name": "Theirs",
            "provider_type": "gcs",
            "config_json": {"bucket": "b"},
            "is_system_default": True,
            "is_active": False,
        },
    )
    assert s == 201 and q["is_system_default"] is False and q["is_active"] is False
    qid = q["id"]
    # list: owner sees own; 'other' sees own + system default
    s, d = await call(app, "get", ST, T("other"), query_string={"per_page": 10})
    assert s == 200 and {i["id"] for i in d["items"]} == {pid, qid}
    s, d = await call(app, "get", ST, T())
    assert {i["id"] for i in d["items"]} == {pid}
    assert (await call(app, "get", ST, T("b")))[1]["items"] == []
    # get
    assert (await call(app, "get", f"{ST}/99999999", T()))[0] == 404
    assert (await call(app, "get", f"{ST}/{qid}", T()))[0] == 403
    assert (await call(app, "get", f"{ST}/{pid}", T("b")))[0] == 403
    assert (await call(app, "get", f"{ST}/{pid}", T("other")))[0] == 200
    # update
    assert (await call(app, "patch", f"{ST}/99999999", T(), json={"name": "x"}))[
        0
    ] == 404
    assert (await call(app, "patch", f"{ST}/{pid}", T("other"), json={"name": "x"}))[
        0
    ] == 403
    assert (await call(app, "patch", f"{ST}/{pid}", T("b"), json={"name": "x"}))[
        0
    ] == 403
    s, u = await call(
        app,
        "patch",
        f"{ST}/{pid}",
        T(),
        json={
            "name": "Mine2",
            "config_json": cfg,
            "storage_config": {},
            "is_active": False,
            "is_system_default": False,
        },
    )
    assert (
        s == 200
        and u["name"] == "Mine2"
        and u["is_active"] is False
        and u["is_system_default"] is False
    )
    s, u = await call(
        app,
        "patch",
        f"{ST}/{qid}",
        T("other", roles=()),
        json={"name": " ", "is_system_default": True},
    )
    assert s == 200 and u["is_system_default"] is False and u["name"] == "Theirs"
    # test endpoint
    assert (await call(app, "post", f"{ST}/99999999/test", T()))[0] == 404
    assert (await call(app, "post", f"{ST}/{pid}/test", T("other")))[0] == 403
    s, t = await call(app, "post", f"{ST}/{pid}/test", T())
    assert s == 200 and t == {"ok": True, "missing": []}
    s, t = await call(app, "post", f"{ST}/{qid}/test", T("other", roles=()))
    assert s == 200 and t["ok"] is False and t["missing"] == ["credentials"]
    # delete
    assert (await call(app, "delete", f"{ST}/99999999", T()))[0] == 404
    assert (await call(app, "delete", f"{ST}/{pid}", T("other")))[0] == 403
    assert (await call(app, "delete", f"{ST}/{pid}", T("b")))[0] == 403
    assert (await call(app, "delete", f"{ST}/{pid}", T()))[0] == 204
    assert (await call(app, "delete", f"{ST}/{qid}", T("other", roles=())))[0] == 204
