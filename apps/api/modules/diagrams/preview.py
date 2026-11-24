"""Lightweight, XSS-safe preview geometry builder for diagram list cards.

Extracts a bounded, sanitized ``{"shapes": [...], "connectors": [...]}``
summary from either the latest ``dg_diagram_versions.content_json`` (React
Flow ``{nodes, edges}``) or the relational ``dg_shapes`` / ``dg_connectors``
tables (used by seeded and shape-API diagrams).
"""

from __future__ import annotations

import math
import re
from typing import Any

MAX_PREVIEW_SHAPES = 60
MAX_PREVIEW_CONNECTORS = 120
MAX_LABEL_LENGTH = 40

_SAFE_HEX_COLOR_RE = re.compile(
    r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"
)
_SAFE_RGB_COLOR_RE = re.compile(
    r"^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\)$"
)

_SHAPE_TYPE_MAP: dict[str, str] = {
    "rectangle": "rectangle",
    "rect": "rectangle",
    "box": "rectangle",
    "square": "rectangle",
    "rounded": "rounded",
    "circle": "circle",
    "ellipse": "ellipse",
    "oval": "ellipse",
    "diamond": "diamond",
    "rhombus": "diamond",
    "text": "text",
}

_DEFAULT_DIMENSIONS: dict[str, tuple[float, float]] = {
    "circle": (80.0, 80.0),
    "diamond": (110.0, 110.0),
    "text": (120.0, 36.0),
}


def empty_preview() -> dict[str, list[dict[str, Any]]]:
    """Return an empty preview payload."""
    return {"shapes": [], "connectors": []}


def _safe_float(value: Any, default: float) -> float:
    """Coerce a numeric coordinate to a finite float, falling back to default."""
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _safe_positive_float(value: Any, default: float) -> float:
    """Coerce a dimension to a finite positive float, falling back to default."""
    parsed = _safe_float(value, default)
    return parsed if parsed > 0 else default


def _safe_color(value: Any) -> str | None:
    """Allow only safe CSS hex, rgb/rgba, or 'transparent' color strings."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.lower() == "transparent":
        return "transparent"
    if _SAFE_HEX_COLOR_RE.match(cleaned) or _SAFE_RGB_COLOR_RE.match(cleaned):
        return cleaned
    return None


def _normalize_shape_type(raw: Any) -> str:
    """Normalize a shape/node type into a known preview primitive."""
    if not isinstance(raw, str):
        return "rounded"
    return _SHAPE_TYPE_MAP.get(raw.strip().lower(), "rounded")


def _normalize_label(raw: Any) -> str:
    """Coerce and truncate a shape label to a safe bounded plain string."""
    if raw is None:
        return ""
    text = str(raw).strip()
    if len(text) > MAX_LABEL_LENGTH:
        return text[: MAX_LABEL_LENGTH - 1].rstrip() + "…"
    return text


def extract_preview_from_content(
    content_json: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Extract preview shapes and connectors from React Flow ``content_json``.

    Args:
        content_json: Dict containing ``nodes`` and ``edges`` lists.

    Returns:
        Dict with ``shapes`` and ``connectors`` lists bounded to preview caps.
    """
    if not isinstance(content_json, dict):
        return empty_preview()

    raw_nodes = content_json.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return empty_preview()

    shapes: list[dict[str, Any]] = []
    shape_ids: set[str] = set()

    for idx, node in enumerate(raw_nodes):
        if len(shapes) >= MAX_PREVIEW_SHAPES:
            break
        if not isinstance(node, dict):
            continue

        node_id = str(node.get("id") if node.get("id") is not None else idx)
        if not node_id or node_id in shape_ids:
            continue

        position = node.get("position")
        pos_dict = position if isinstance(position, dict) else {}
        x = _safe_float(pos_dict.get("x"), float(idx * 180))
        y = _safe_float(pos_dict.get("y"), 0.0)

        data = node.get("data")
        data_dict = data if isinstance(data, dict) else {}
        node_type = str(node.get("type") or "").strip().lower()

        if node_type == "shape":
            shape_type = _normalize_shape_type(data_dict.get("shape") or "rectangle")
        elif node_type == "text":
            shape_type = "text"
        else:
            shape_type = "rounded"

        default_w, default_h = _DEFAULT_DIMENSIONS.get(shape_type, (140.0, 60.0))
        measured = node.get("measured")
        measured_dict = measured if isinstance(measured, dict) else {}
        node_style = node.get("style")
        style_dict = node_style if isinstance(node_style, dict) else {}

        width = _safe_positive_float(
            node.get("width") or measured_dict.get("width") or style_dict.get("width"),
            default_w,
        )
        height = _safe_positive_float(
            node.get("height")
            or measured_dict.get("height")
            or style_dict.get("height"),
            default_h,
        )

        label = _normalize_label(
            data_dict.get("label")
            or data_dict.get("name")
            or data_dict.get("title")
            or ""
        )
        color = _safe_color(
            data_dict.get("color")
            or style_dict.get("backgroundColor")
            or style_dict.get("fill")
        )

        shapes.append(
            {
                "id": node_id,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "shape_type": shape_type,
                "label": label,
                "color": color,
            }
        )
        shape_ids.add(node_id)

    connectors: list[dict[str, Any]] = []
    raw_edges = content_json.get("edges")
    if isinstance(raw_edges, list):
        for idx, edge in enumerate(raw_edges):
            if len(connectors) >= MAX_PREVIEW_CONNECTORS:
                break
            if not isinstance(edge, dict):
                continue

            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source not in shape_ids or target not in shape_ids:
                continue

            edge_id = str(edge.get("id") or f"e-{idx}")
            edge_style = edge.get("style")
            style_dict = edge_style if isinstance(edge_style, dict) else {}
            connector_type = "arrow" if edge.get("markerEnd") else "line"
            color = _safe_color(style_dict.get("stroke"))

            connectors.append(
                {
                    "id": edge_id,
                    "source": source,
                    "target": target,
                    "connector_type": connector_type,
                    "color": color,
                }
            )

    return {"shapes": shapes, "connectors": connectors}


def extract_preview_from_shapes(
    shape_rows: list[Any],
    connector_rows: list[Any] | None = None,
    metadata_by_shape_id: dict[Any, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Extract preview geometry from ``dg_shapes`` and ``dg_connectors`` rows.

    Args:
        shape_rows: Iterable of ``dg_shapes`` rows for a single diagram.
        connector_rows: Optional iterable of ``dg_connectors`` rows.
        metadata_by_shape_id: Optional mapping of ``shape_id`` -> ``dg_shape_metadata`` row.

    Returns:
        Dict with ``shapes`` and ``connectors`` lists bounded to preview caps.
    """
    if not shape_rows:
        return empty_preview()

    meta_map = metadata_by_shape_id or {}
    shapes: list[dict[str, Any]] = []
    shape_ids: set[str] = set()

    for idx, row in enumerate(shape_rows):
        if len(shapes) >= MAX_PREVIEW_SHAPES:
            break

        raw_id = getattr(row, "id", None)
        shape_id = str(raw_id if raw_id is not None else idx)
        if shape_id in shape_ids:
            continue

        shape_type = _normalize_shape_type(
            getattr(row, "shape_type", None) or "rectangle"
        )
        default_w, default_h = _DEFAULT_DIMENSIONS.get(shape_type, (140.0, 60.0))

        x = _safe_float(getattr(row, "x_position", None), float(idx * 180))
        y = _safe_float(getattr(row, "y_position", None), 0.0)
        width = _safe_positive_float(getattr(row, "width", None), default_w)
        height = _safe_positive_float(getattr(row, "height", None), default_h)

        style = getattr(row, "style_json", None)
        style_dict = style if isinstance(style, dict) else {}

        meta_row = meta_map.get(raw_id)
        meta_label = getattr(meta_row, "label", None) if meta_row is not None else None
        label = _normalize_label(meta_label or style_dict.get("label") or "")
        color = _safe_color(
            style_dict.get("color")
            or style_dict.get("fill")
            or style_dict.get("backgroundColor")
        )

        shapes.append(
            {
                "id": shape_id,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "shape_type": shape_type,
                "label": label,
                "color": color,
            }
        )
        shape_ids.add(shape_id)

    connectors: list[dict[str, Any]] = []
    for idx, conn in enumerate(connector_rows or []):
        if len(connectors) >= MAX_PREVIEW_CONNECTORS:
            break

        start_id = getattr(conn, "start_shape_id", None)
        end_id = getattr(conn, "end_shape_id", None)
        if start_id is None or end_id is None:
            continue

        source = str(start_id)
        target = str(end_id)
        if source not in shape_ids or target not in shape_ids:
            continue

        conn_id = str(getattr(conn, "id", None) or f"c-{idx}")
        raw_type = str(getattr(conn, "connector_type", None) or "arrow").strip().lower()
        connector_type = "line" if raw_type == "line" else "arrow"

        style = getattr(conn, "style_json", None)
        style_dict = style if isinstance(style, dict) else {}
        color = _safe_color(style_dict.get("stroke") or style_dict.get("color"))

        connectors.append(
            {
                "id": conn_id,
                "source": source,
                "target": target,
                "connector_type": connector_type,
                "color": color,
            }
        )

    return {"shapes": shapes, "connectors": connectors}


def build_diagram_previews(
    db: Any,
    diagram_ids: list[int],
) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """Batch-build preview geometry for a list of diagram IDs (zero N+1 queries).

    Prefers the latest ``dg_diagram_versions.content_json`` when it contains
    nodes, and falls back to ``dg_shapes`` + ``dg_shape_metadata`` +
    ``dg_connectors`` for diagrams stored in relational shape tables (such as
    seeded demo diagrams).

    Args:
        db: PyDAL database instance.
        diagram_ids: List of diagram primary keys to build previews for.

    Returns:
        Mapping of ``diagram_id`` -> ``{"shapes": [...], "connectors": [...]}``.
    """
    if not diagram_ids:
        return {}

    previews: dict[int, dict[str, list[dict[str, Any]]]] = {
        did: empty_preview() for did in diagram_ids
    }

    # 1. Batch-fetch latest version content per diagram
    version_rows = db(db.dg_diagram_versions.diagram_id.belongs(diagram_ids)).select(
        db.dg_diagram_versions.diagram_id,
        db.dg_diagram_versions.version_number,
        db.dg_diagram_versions.content_json,
        orderby=~db.dg_diagram_versions.version_number,
    )

    seen_versions: set[int] = set()
    for vrow in version_rows:
        did = vrow.diagram_id
        if did in seen_versions:
            continue
        seen_versions.add(did)
        preview = extract_preview_from_content(vrow.content_json)
        if preview["shapes"]:
            previews[did] = preview

    # 2. Fall back to relational dg_shapes / dg_connectors for diagrams without
    #    versioned React Flow nodes (e.g. seeded diagrams)
    fallback_ids = [did for did in diagram_ids if not previews[did]["shapes"]]
    if not fallback_ids:
        return previews

    shape_rows = db(db.dg_shapes.diagram_id.belongs(fallback_ids)).select(
        orderby=db.dg_shapes.id,
    )
    if not shape_rows:
        return previews

    shapes_by_diagram: dict[int, list[Any]] = {}
    shape_ids_all: list[int] = []
    for srow in shape_rows:
        shapes_by_diagram.setdefault(srow.diagram_id, []).append(srow)
        shape_ids_all.append(srow.id)

    diagrams_with_shapes = list(shapes_by_diagram.keys())

    # Batch-fetch shape metadata labels if any exist
    meta_by_shape_id: dict[Any, Any] = {}
    if shape_ids_all and hasattr(db, "dg_shape_metadata"):
        meta_rows = db(db.dg_shape_metadata.shape_id.belongs(shape_ids_all)).select()
        for mrow in meta_rows:
            meta_by_shape_id[mrow.shape_id] = mrow

    # Batch-fetch connectors for diagrams that have shapes
    connectors_by_diagram: dict[int, list[Any]] = {}
    if diagrams_with_shapes and hasattr(db, "dg_connectors"):
        conn_rows = db(
            db.dg_connectors.diagram_id.belongs(diagrams_with_shapes)
        ).select(orderby=db.dg_connectors.id)
        for crow in conn_rows:
            connectors_by_diagram.setdefault(crow.diagram_id, []).append(crow)

    for did, d_shapes in shapes_by_diagram.items():
        previews[did] = extract_preview_from_shapes(
            d_shapes,
            connectors_by_diagram.get(did, []),
            meta_by_shape_id,
        )

    return previews
