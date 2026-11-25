"""Unit tests for diagram preview extraction (pure helpers plus real-DB batch build)."""

# flake8: noqa: E501

from types import SimpleNamespace as NS

import pytest

from apps.api.modules.diagrams import preview as pv
from tests.unit._cov_d_helpers import call, in_db, now, world  # noqa: F401
from tests.unit.test_cov_d_diginf_dg_core import dw, tok  # noqa: F401

DG = "/api/v1/diagrams"


def test_scalar_normalizers():
    """Coordinate, color, type and label coercion helpers handle odd input."""
    assert pv._safe_float(True, 1.0) == 1.0
    assert pv._safe_float("x", 2.0) == 2.0
    assert pv._safe_float(float("inf"), 3.0) == 3.0
    assert pv._safe_float("4.5", 0.0) == 4.5
    assert pv._safe_positive_float(-1, 9.0) == 9.0
    assert pv._safe_color(5) is None
    assert pv._safe_color("   ") is None
    assert pv._safe_color("Transparent") == "transparent"
    assert pv._safe_color("#fff") == "#fff"
    assert pv._safe_color("rgb(1, 2, 3)") == "rgb(1, 2, 3)"
    assert pv._safe_color("url(javascript:x)") is None
    assert pv._normalize_shape_type(5) == "rounded"
    assert pv._normalize_shape_type(" Oval ") == "ellipse"
    assert pv._normalize_shape_type("weird") == "rounded"
    assert pv._normalize_label(None) == ""
    assert pv._normalize_label("  hi  ") == "hi"
    assert len(pv._normalize_label("x" * 1000)) < 1000


def test_extract_from_content_branches():
    """React Flow content: bad shapes, duplicate ids, caps, edge filtering and styles."""
    assert pv.extract_preview_from_content(None) == pv.empty_preview()
    assert pv.extract_preview_from_content({"nodes": []}) == pv.empty_preview()
    assert pv.extract_preview_from_content({"nodes": "x"}) == pv.empty_preview()
    nodes = [
        "junk",
        {
            "id": "a",
            "type": "circle",
            "position": {"x": 1, "y": 2},
            "data": {"label": "A"},
            "style": {"background": "#fff"},
        },
        {"id": "a"},
        {"id": 0},
        {"type": "text"},
        {"id": ""},
    ]
    edges = [
        "junk",
        {
            "source": "a",
            "target": "0",
            "markerEnd": {"type": "arrow"},
            "style": {"stroke": "#000"},
        },
        {"id": "e", "source": "a", "target": "zzz"},
        {"source": "a", "target": "a"},
    ]
    out = pv.extract_preview_from_content({"nodes": nodes, "edges": edges})
    assert {s["id"] for s in out["shapes"]} >= {"a", "0"}
    assert [c["connector_type"] for c in out["connectors"]][0] == "arrow"
    many = [{"id": str(i)} for i in range(pv.MAX_PREVIEW_SHAPES + 20)]
    many_edges = [
        {"id": f"e{i}", "source": "0", "target": "1"}
        for i in range(pv.MAX_PREVIEW_CONNECTORS + 20)
    ]
    out = pv.extract_preview_from_content({"nodes": many, "edges": many_edges})
    assert (
        len(out["shapes"]) == pv.MAX_PREVIEW_SHAPES
        and len(out["connectors"]) == pv.MAX_PREVIEW_CONNECTORS
    )
    assert (
        pv.extract_preview_from_content({"nodes": [{"id": "a"}], "edges": "nope"})[
            "connectors"
        ]
        == []
    )


def test_extract_from_shapes_branches():
    """Relational rows: empty input, duplicates, metadata labels, caps and connector filtering."""
    assert pv.extract_preview_from_shapes([]) == pv.empty_preview()
    rows = [
        NS(
            id=1,
            shape_type="diamond",
            x_position=5,
            y_position=6,
            width=10,
            height=0,
            style_json={"fill": "#abc", "label": "style-label"},
        ),
        NS(id=1),
        NS(id=None, shape_type=None),
        NS(id=2, style_json="bad"),
    ]
    conns = [
        NS(start_shape_id=None, end_shape_id=2),
        NS(start_shape_id=1, end_shape_id=99),
        NS(
            id=7,
            start_shape_id=1,
            end_shape_id=2,
            connector_type="LINE",
            style_json={"stroke": "#111"},
        ),
        NS(start_shape_id=2, end_shape_id=1, connector_type=None, style_json=None),
    ]
    out = pv.extract_preview_from_shapes(rows, conns, {1: NS(label="meta")})
    assert out["shapes"][0]["label"] == "meta" and out["shapes"][0]["height"] == 110.0
    assert [c["connector_type"] for c in out["connectors"]] == ["line", "arrow"]
    big = [NS(id=i) for i in range(pv.MAX_PREVIEW_SHAPES + 5)]
    bigc = [NS(start_shape_id=0, end_shape_id=1)] * (pv.MAX_PREVIEW_CONNECTORS + 5)
    out = pv.extract_preview_from_shapes(big, bigc)
    assert (
        len(out["shapes"]) == pv.MAX_PREVIEW_SHAPES
        and len(out["connectors"]) == pv.MAX_PREVIEW_CONNECTORS
    )


@pytest.mark.asyncio
async def test_build_previews_fallback_to_relational(app, dw):  # noqa: F811
    """Diagrams without versions fall back to shape/connector/metadata rows in one batch."""
    w = dw
    assert pv.build_diagram_previews(app.db, []) == {}
    s, d = await call(app, "post", DG, w["T"](), json={"title": "Seeded"})
    seeded = d["id"]
    s, d = await call(
        app,
        "post",
        DG,
        w["T"](),
        json={"title": "Versioned", "content": {"nodes": [{"id": "n1"}], "edges": []}},
    )
    versioned = d["id"]
    s, d = await call(app, "post", DG, w["T"](), json={"title": "Empty"})
    empty = d["id"]

    def _seed():
        """Insert fixture rows and return what the test needs.
        Used only to set up this test's data."""
        db = app.db
        n = now()
        s1 = db.dg_shapes.insert(
            diagram_id=seeded,
            tenant_id=w["a"]["tenant"],
            shape_type="circle",
            x_position=1,
            y_position=2,
            width=3,
            height=4,
            style_json={"fill": "#123"},
            created_at=n,
            updated_at=n,
        )
        s2 = db.dg_shapes.insert(
            diagram_id=seeded,
            tenant_id=w["a"]["tenant"],
            shape_type="rect",
            created_at=n,
            updated_at=n,
        )
        db.dg_shape_metadata.insert(
            shape_id=s1,
            diagram_id=seeded,
            tenant_id=w["a"]["tenant"],
            label="Lbl",
            created_at=n,
            updated_at=n,
        )
        db.dg_connectors.insert(
            diagram_id=seeded,
            tenant_id=w["a"]["tenant"],
            start_shape_id=s1,
            end_shape_id=s2,
            connector_type="line",
            created_at=n,
            updated_at=n,
        )
        db.commit()
        return pv.build_diagram_previews(db, [seeded, versioned, empty])

    out = await in_db(_seed)
    assert [x["label"] for x in out[seeded]["shapes"]] == ["Lbl", ""]
    assert out[seeded]["connectors"][0]["connector_type"] == "line"
    assert out[versioned]["shapes"][0]["id"] == "n1"
    assert out[empty] == pv.empty_preview()
