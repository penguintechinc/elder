"""Unit tests for diagram card preview geometry extraction."""

from types import SimpleNamespace

from apps.api.modules.diagrams.preview import (
    MAX_LABEL_LENGTH,
    MAX_PREVIEW_CONNECTORS,
    MAX_PREVIEW_SHAPES,
    build_diagram_previews,
    empty_preview,
    extract_preview_from_content,
    extract_preview_from_shapes,
)


class TestExtractPreviewFromContent:
    """Tests for React Flow content_json preview extraction."""

    def test_empty_and_invalid_inputs_return_empty_preview(self):
        """Non-dict, missing nodes, and empty nodes all return empty preview."""
        assert extract_preview_from_content(None) == empty_preview()
        assert extract_preview_from_content("not-a-dict") == empty_preview()
        assert extract_preview_from_content({}) == empty_preview()
        assert extract_preview_from_content({"nodes": [], "edges": []}) == (
            empty_preview()
        )

    def test_extracts_nodes_shapes_and_edges(self):
        """Extracts shape types, positions, colors, labels, and valid edges."""
        content = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "cloudProvider",
                    "position": {"x": 50, "y": 100},
                    "width": 160,
                    "height": 70,
                    "data": {"label": "AWS VPC", "color": "#1e40af"},
                },
                {
                    "id": "n2",
                    "type": "shape",
                    "position": {"x": 300, "y": 100},
                    "data": {
                        "label": "Decision",
                        "shape": "diamond",
                        "color": "#f59e0b",
                    },
                },
                {
                    "id": "n3",
                    "type": "shape",
                    "position": {"x": 520, "y": 100},
                    "data": {
                        "label": "Cache",
                        "shape": "circle",
                        "color": "transparent",
                    },
                },
                {
                    "id": "n4",
                    "type": "text",
                    "position": {"x": 50, "y": 20},
                    "data": {"label": "Prod Architecture"},
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "n1",
                    "target": "n2",
                    "style": {"stroke": "#f59e0b"},
                    "markerEnd": {"type": "arrowclosed"},
                },
                {
                    "id": "e2",
                    "source": "n2",
                    "target": "n3",
                },
                {
                    "id": "e-dangling",
                    "source": "n1",
                    "target": "missing-node",
                },
            ],
        }

        preview = extract_preview_from_content(content)

        assert len(preview["shapes"]) == 4
        assert preview["shapes"][0] == {
            "id": "n1",
            "x": 50.0,
            "y": 100.0,
            "width": 160.0,
            "height": 70.0,
            "shape_type": "rounded",
            "label": "AWS VPC",
            "color": "#1e40af",
        }
        assert preview["shapes"][1]["shape_type"] == "diamond"
        assert preview["shapes"][1]["width"] == 110.0
        assert preview["shapes"][2]["shape_type"] == "circle"
        assert preview["shapes"][2]["color"] == "transparent"
        assert preview["shapes"][3]["shape_type"] == "text"

        # Dangling edge is excluded; arrow vs line preserved
        assert len(preview["connectors"]) == 2
        assert preview["connectors"][0] == {
            "id": "e1",
            "source": "n1",
            "target": "n2",
            "connector_type": "arrow",
            "color": "#f59e0b",
        }
        assert preview["connectors"][1]["connector_type"] == "line"
        assert preview["connectors"][1]["color"] is None

    def test_sanitizes_unsafe_colors_and_truncates_labels(self):
        """Rejects non-CSS colors and bounds long labels."""
        long_label = "A" * (MAX_LABEL_LENGTH + 25)
        content = {
            "nodes": [
                {
                    "id": "1",
                    "position": {"x": float("nan"), "y": None},
                    "width": -50,
                    "height": "bad",
                    "data": {
                        "label": long_label,
                        "color": "url(javascript:alert(1))",
                    },
                }
            ],
            "edges": [],
        }

        preview = extract_preview_from_content(content)
        shape = preview["shapes"][0]
        assert shape["color"] is None
        assert len(shape["label"]) == MAX_LABEL_LENGTH
        assert shape["label"].endswith("…")
        assert shape["x"] == 0.0
        assert shape["y"] == 0.0
        assert shape["width"] == 140.0
        assert shape["height"] == 60.0

    def test_caps_shapes_and_connectors(self):
        """Enforces MAX_PREVIEW_SHAPES and MAX_PREVIEW_CONNECTORS caps."""
        nodes = [
            {
                "id": f"n{i}",
                "position": {"x": i * 10, "y": 0},
                "data": {"label": f"N{i}"},
            }
            for i in range(MAX_PREVIEW_SHAPES + 15)
        ]
        edges = [
            {"id": f"e{i}", "source": "n0", "target": "n1"}
            for i in range(MAX_PREVIEW_CONNECTORS + 20)
        ]

        preview = extract_preview_from_content({"nodes": nodes, "edges": edges})
        assert len(preview["shapes"]) == MAX_PREVIEW_SHAPES
        assert len(preview["connectors"]) == MAX_PREVIEW_CONNECTORS


class TestExtractPreviewFromShapes:
    """Tests for relational dg_shapes + dg_connectors preview extraction."""

    def test_extracts_seeded_shapes_and_connectors(self):
        """Extracts demo-seeded dg_shapes and dg_connectors cleanly."""
        shapes = [
            SimpleNamespace(
                id=10,
                diagram_id=1,
                shape_type="rectangle",
                x_position=80,
                y_position=120,
                width=160,
                height=70,
                style_json={"label": "Storefront", "color": "#1f2937"},
            ),
            SimpleNamespace(
                id=11,
                diagram_id=1,
                shape_type="rectangle",
                x_position=300,
                y_position=120,
                width=160,
                height=70,
                style_json={"label": "Orders API"},
            ),
        ]
        connectors = [
            SimpleNamespace(
                id=100,
                diagram_id=1,
                start_shape_id=10,
                end_shape_id=11,
                connector_type="arrow",
                style_json=None,
            ),
            SimpleNamespace(
                id=101,
                diagram_id=1,
                start_shape_id=10,
                end_shape_id=999,
                connector_type="arrow",
                style_json=None,
            ),
        ]
        metadata = {
            11: SimpleNamespace(shape_id=11, label="Orders API v2"),
        }

        preview = extract_preview_from_shapes(shapes, connectors, metadata)
        assert len(preview["shapes"]) == 2
        assert preview["shapes"][0]["label"] == "Storefront"
        assert preview["shapes"][0]["color"] == "#1f2937"
        assert preview["shapes"][1]["label"] == "Orders API v2"
        assert len(preview["connectors"]) == 1
        assert preview["connectors"][0] == {
            "id": "100",
            "source": "10",
            "target": "11",
            "connector_type": "arrow",
            "color": None,
        }


class _FakeField:
    def __init__(self, table_name: str, field_name: str):
        self.table_name = table_name
        self.field_name = field_name

    def belongs(self, values):
        return (self.table_name, self.field_name, set(values))

    def __invert__(self):
        return (self.field_name, True)


class _FakeTable:
    def __init__(self, name: str, fields: list[str]):
        self._name = name
        for f in fields:
            setattr(self, f, _FakeField(name, f))


class _FakeQueryResult:
    def __init__(self, rows: list[SimpleNamespace]):
        self._rows = rows

    def select(self, *args, orderby=None, **kwargs):
        rows = list(self._rows)
        if isinstance(orderby, tuple):
            field_name, desc = orderby
            rows.sort(key=lambda r: getattr(r, field_name), reverse=desc)
        elif isinstance(orderby, _FakeField):
            rows.sort(key=lambda r: getattr(r, orderby.field_name))
        return rows


class _FakeDB:
    """Minimal PyDAL-compatible mock for batch preview queries."""

    def __init__(self, versions=None, shapes=None, metadata=None, connectors=None):
        self._data = {
            "dg_diagram_versions": versions or [],
            "dg_shapes": shapes or [],
            "dg_shape_metadata": metadata or [],
            "dg_connectors": connectors or [],
        }
        self.dg_diagram_versions = _FakeTable(
            "dg_diagram_versions", ["diagram_id", "version_number", "content_json"]
        )
        self.dg_shapes = _FakeTable("dg_shapes", ["id", "diagram_id"])
        self.dg_shape_metadata = _FakeTable("dg_shape_metadata", ["shape_id"])
        self.dg_connectors = _FakeTable("dg_connectors", ["id", "diagram_id"])

    def __call__(self, query):
        table_name, field_name, allowed = query
        rows = [r for r in self._data[table_name] if getattr(r, field_name) in allowed]
        return _FakeQueryResult(rows)


class TestBuildDiagramPreviews:
    """Tests for batch preview resolution across versions and relational shapes."""

    def test_empty_diagram_ids_returns_empty_dict(self):
        """Empty ID list returns empty dict without querying."""
        db = _FakeDB()
        assert build_diagram_previews(db, []) == {}

    def test_prefers_latest_version_and_falls_back_to_shapes(self):
        """Uses latest version for diagram 1, dg_shapes for diagram 2, empty for 3."""
        db = _FakeDB(
            versions=[
                SimpleNamespace(
                    diagram_id=1,
                    version_number=1,
                    content_json={
                        "nodes": [
                            {
                                "id": "old",
                                "position": {"x": 0, "y": 0},
                                "data": {"label": "Old"},
                            }
                        ],
                        "edges": [],
                    },
                ),
                SimpleNamespace(
                    diagram_id=1,
                    version_number=2,
                    content_json={
                        "nodes": [
                            {
                                "id": "new",
                                "position": {"x": 10, "y": 20},
                                "data": {"label": "New"},
                            }
                        ],
                        "edges": [],
                    },
                ),
            ],
            shapes=[
                SimpleNamespace(
                    id=50,
                    diagram_id=2,
                    shape_type="rectangle",
                    x_position=80,
                    y_position=120,
                    width=160,
                    height=70,
                    style_json={"label": "Storefront"},
                ),
                SimpleNamespace(
                    id=51,
                    diagram_id=2,
                    shape_type="rectangle",
                    x_position=300,
                    y_position=120,
                    width=160,
                    height=70,
                    style_json={"label": "Orders API"},
                ),
            ],
            connectors=[
                SimpleNamespace(
                    id=70,
                    diagram_id=2,
                    start_shape_id=50,
                    end_shape_id=51,
                    connector_type="arrow",
                    style_json=None,
                )
            ],
        )

        previews = build_diagram_previews(db, [1, 2, 3])

        # Diagram 1 gets version 2 (latest)
        assert [s["label"] for s in previews[1]["shapes"]] == ["New"]

        # Diagram 2 falls back to dg_shapes + dg_connectors
        assert [s["label"] for s in previews[2]["shapes"]] == [
            "Storefront",
            "Orders API",
        ]
        assert len(previews[2]["connectors"]) == 1

        # Diagram 3 genuinely has 0 shapes
        assert previews[3] == empty_preview()
