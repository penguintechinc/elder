"""Diagram export endpoints (JSON, SVG, raster formats).

Provides diagram export in multiple formats:
- JSON: Raw diagram content
- SVG: Rendered as SVG markup (dependency-free)
- PNG/PDF: Raster exports (feature-flagged, 501 if disabled)
"""

# flake8: noqa: E501

import html
import logging
from datetime import datetime, timezone

from quart import Blueprint, current_app, g, jsonify, request, send_file

from apps.api.auth.decorators import login_required, require_scope
from apps.api.common.flags.posthog_client import flag_enabled
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

from .diagrams import _can_read_diagram, _get_tenant_id

logger = logging.getLogger(__name__)

export_bp = Blueprint("diagram_export", __name__)


def _build_svg_from_content(content_json):
    """Build SVG markup from diagram content (dependency-free).

    Args:
        content_json: Diagram content with nodes and edges

    Returns:
        SVG markup as string
    """
    if not isinstance(content_json, dict):
        return _svg_empty()

    nodes = content_json.get("nodes") or []
    edges = content_json.get("edges") or []

    # SVG parameters
    svg_width = 1200
    svg_height = 800
    margin = 40
    node_width = 120
    node_height = 60

    # Build SVG elements
    svg_elements = []

    # Add nodes
    for node in nodes:
        try:
            if not isinstance(node, dict):
                continue

            node_id = node.get("id", "")
            x = node.get("x", 100)
            y = node.get("y", 100)
            width = node.get("width", node_width)
            height = node.get("height", node_height)
            label = html.escape(str(node.get("label", node_id)))

            # Clamp to reasonable bounds to avoid massive SVGs
            x = max(0, min(x, svg_width - margin))
            y = max(0, min(y, svg_height - margin))
            width = max(40, min(width, 300))
            height = max(30, min(height, 200))

            # Add node rect
            svg_elements.append(
                f'  <rect x="{x}" y="{y}" width="{width}" height="{height}" '
                f'fill="#e8f4f8" stroke="#0088cc" stroke-width="2"/>'
            )

            # Add node text label
            text_x = x + width / 2
            text_y = y + height / 2 + 5
            svg_elements.append(
                f'  <text x="{text_x}" y="{text_y}" text-anchor="middle" '
                f'font-family="Arial,sans-serif" font-size="12" fill="#333">'
                f"{label}</text>"
            )
        except Exception as e:
            # Skip malformed nodes; never crash
            logger.debug(f"Skipped malformed node: {e}")
            continue

    # Add edges (lines between nodes)
    node_centers = {}
    for node in nodes:
        try:
            if not isinstance(node, dict):
                continue
            node_id = node.get("id")
            if node_id:
                x = node.get("x", 100)
                y = node.get("y", 100)
                width = node.get("width", node_width)
                height = node.get("height", node_height)
                node_centers[node_id] = (x + width / 2, y + height / 2)
        except Exception:
            pass

    for edge in edges:
        try:
            if not isinstance(edge, dict):
                continue

            source_id = edge.get("source")
            target_id = edge.get("target")

            if source_id in node_centers and target_id in node_centers:
                x1, y1 = node_centers[source_id]
                x2, y2 = node_centers[target_id]

                # Add edge line
                svg_elements.append(
                    f'  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                    f'stroke="#666" stroke-width="2"/>'
                )
        except Exception as e:
            # Skip malformed edges
            logger.debug(f"Skipped malformed edge: {e}")
            continue

    # Wrap in SVG container
    svg_content = (
        f'<svg width="{svg_width}" height="{svg_height}" '
        f'viewBox="0 0 {svg_width} {svg_height}" '
        f'xmlns="http://www.w3.org/2000/svg">\n'
        f'  <rect width="{svg_width}" height="{svg_height}" fill="#fff"/>\n'
    )
    svg_content += "\n".join(svg_elements)
    svg_content += "\n</svg>"

    return svg_content


def _svg_empty():
    """Return an empty SVG template."""
    return (
        '<svg width="1200" height="800" viewBox="0 0 1200 800" '
        'xmlns="http://www.w3.org/2000/svg">'
        '<rect width="1200" height="800" fill="#fff"/>'
        '<text x="600" y="400" text-anchor="middle" font-family="Arial" '
        'font-size="14" fill="#999">Empty diagram</text></svg>'
    )


@export_bp.route("/<int:diagram_id>/export/json", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def export_json(diagram_id: int):
    """Export diagram as JSON (latest version).

    Returns the latest diagram version's content_json as a downloadable
    application/json file.

    Requires: diagrams:read scope + read access to diagram

    Returns:
        200: JSON file attachment
        404: Diagram not found or not accessible
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def get_version_json():
        # Get diagram
        diagram_row = db(db.dg_diagrams.id == diagram_id).select().first()
        if not diagram_row:
            return None, "not_found"

        # Check read permission (return 404 for both not found and not readable)
        if not _can_read_diagram(db, diagram_row, tenant_id, identity_id):
            return None, "not_found"

        # Get latest version
        version_row = (
            db(db.dg_diagram_versions.diagram_id == diagram_id)
            .select(orderby=~db.dg_diagram_versions.version_number)
            .first()
        )
        if not version_row:
            return None, "no_version"

        return version_row.content_json, "ok"

    content, status = await run_in_threadpool(get_version_json)

    if status == "not_found":
        return ApiResponse.error("Diagram not found", 404)
    if status == "no_version":
        return ApiResponse.error("No diagram version found", 404)

    # Return as JSON file attachment
    import io
    import json

    json_str = json.dumps(content, indent=2)
    json_bytes = io.BytesIO(json_str.encode("utf-8"))

    return await send_file(
        json_bytes,
        mimetype="application/json",
        as_attachment=True,
        attachment_filename=f"diagram-{diagram_id}.json",
    )


@export_bp.route("/<int:diagram_id>/export/svg", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def export_svg(diagram_id: int):
    """Export diagram as SVG (latest version, dependency-free rendering).

    Renders diagram nodes+edges to SVG markup with no external dependencies.
    Defensive: skips malformed nodes/edges, never returns 500.

    Requires: diagrams:read scope + read access to diagram

    Returns:
        200: SVG image attachment
        404: Diagram not found or not accessible
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    claims = getattr(g, "claims", {}) or {}
    identity_id = claims.get("identity_id")

    def get_version_svg():
        # Get diagram
        diagram_row = db(db.dg_diagrams.id == diagram_id).select().first()
        if not diagram_row:
            return None, "not_found"

        # Check read permission (return 404 for both not found and not readable)
        if not _can_read_diagram(db, diagram_row, tenant_id, identity_id):
            return None, "not_found"

        # Get latest version
        version_row = (
            db(db.dg_diagram_versions.diagram_id == diagram_id)
            .select(orderby=~db.dg_diagram_versions.version_number)
            .first()
        )
        if not version_row:
            content = {}
        else:
            content = version_row.content_json or {}

        return content, "ok"

    content, status = await run_in_threadpool(get_version_svg)

    if status == "not_found":
        return ApiResponse.error("Diagram not found", 404)

    # Build SVG
    svg_str = _build_svg_from_content(content)

    import io

    svg_bytes = io.BytesIO(svg_str.encode("utf-8"))

    return await send_file(
        svg_bytes,
        mimetype="image/svg+xml",
        as_attachment=True,
        attachment_filename=f"diagram-{diagram_id}.svg",
    )


@export_bp.route("/<int:diagram_id>/export/<fmt>", methods=["GET"])
@login_required
@require_scope("diagrams:read")
async def export_raster(diagram_id: int, fmt: str):
    """Export diagram as raster format (PNG, PDF).

    PNG and PDF exports are feature-flagged and return 501 if disabled.
    Valid formats: png, pdf

    Requires: diagrams:read scope + read access to diagram

    Returns:
        501: Feature not enabled (if flag off)
        400: Invalid format
    """
    fmt_lower = fmt.lower()

    # Validate format
    if fmt_lower not in ("png", "pdf"):
        return ApiResponse.error(
            f"Unknown export format: {fmt_lower}. Valid: png, pdf", 400
        )

    # Check raster export flag
    distinct_id = f"diagram-{diagram_id}"
    raster_enabled = flag_enabled(
        "elder.diagrams.export-raster", distinct_id, default=False
    )

    if not raster_enabled:
        return ApiResponse.error(f"Raster export ({fmt_lower}) is not enabled", 501)

    # Feature-flagged but not yet implemented
    return ApiResponse.error(f"Raster export ({fmt_lower}) not yet implemented", 501)
