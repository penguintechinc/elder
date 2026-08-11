"""Cross-reference resolution and backlink endpoints.

Provides registry-driven resolution of references by village_id or ref format
(module:type:id), with tenant isolation and breakage detection.
"""

# flake8: noqa: E501

import logging
from typing import Optional

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.common.refs.registry import get_type, resolve_by_village_id, resolve_ref
from apps.api.common.refs.service import backlinks_for
from apps.api.utils.async_utils import run_in_threadpool
from shared.utils.village_id import parse_village_id

bp = Blueprint("refs", __name__)
logger = logging.getLogger(__name__)


def _get_tenant_id() -> int | None:
    """Extract and validate tenant ID from authenticated JWT claims.

    Reads from g.claims populated by before_request bridge in main.py.
    Tenant claim is a string like "42", must be converted to int safely.

    Returns:
        Tenant ID as int, or None if not authenticated / tenant claim invalid.
    """
    claims = getattr(g, "claims", {}) or {}
    tenant_str = claims.get("tenant", "")

    # Fail-closed: tenant must be present and convertible to int
    if not tenant_str:
        return None

    try:
        return int(tenant_str)
    except (ValueError, TypeError):
        return None


def _build_resolve_response(
    db,
    resource_type: str,
    resource_id,
    village_id: str | None = None,
    tenant_id: int | None = None,
) -> dict:
    """Build a resolve response for a found resource.

    Args:
        db: PyDAL database instance
        resource_type: Type identifier
        resource_id: Resource ID value
        village_id: Optional village_id to include
        tenant_id: Tenant ID for tenant-scoped resolution

    Returns:
        Dict with type, id, village_id, title, url, broken=false
    """
    resolvable = get_type(resource_type)
    if not resolvable:
        return {"error": "unknown_type", "broken": True}

    # Resolve the reference to get title (tenant-scoped)
    ref_data = resolve_ref(
        db, resolvable.module, resource_type, resource_id, tenant_id=tenant_id
    )
    if not ref_data:
        return {
            "type": resource_type,
            "id": resource_id,
            "village_id": village_id or "",
            "title": None,
            "url": resolvable.url_pattern.format(id=resource_id),
            "broken": True,
        }

    # Build URL
    url = resolvable.url_pattern.format(id=resource_id)

    return {
        "type": resource_type,
        "id": resource_id,
        "village_id": village_id or "",
        "title": ref_data.get("title"),
        "url": url,
        "broken": False,
    }


@bp.route("/refs/resolve", methods=["GET"])
async def resolve_reference():
    """Resolve a reference by village_id or module:type:id.

    Query Parameters:
        - village_id: 25-char village ID (e.g., "0000002a-000000000000f3c1")
        - ref: module:type:id format (e.g., "infrastructure:entity:123")

    Returns:
        200: {type, id, village_id, title, url, broken}
        400: Missing or invalid parameters
        401: Unauthenticated (no valid JWT claims)
        403: Forbidden (tenant mismatch, invalid tenant claim)
    """
    # Fail-closed: require authenticated caller with valid tenant claim
    tenant_id = _get_tenant_id()
    if tenant_id is None:
        return jsonify({"error": "Unauthorized"}), 401

    db = current_app.db
    village_id = request.args.get("village_id")
    ref = request.args.get("ref")

    if not village_id and not ref:
        return (
            jsonify(
                {
                    "error": "missing_parameters",
                    "message": "Provide either ?village_id or ?ref",
                }
            ),
            400,
        )

    def search():
        if village_id:
            # Validate village_id format
            from shared.utils.village_id import is_valid_village_id

            if not is_valid_village_id(village_id):
                return {"error": "invalid_village_id_format"}

            # Parse village_id to check tenant — MUST match caller's tenant
            parsed = parse_village_id(village_id)
            if parsed.tenant_id != tenant_id:
                return {"error": "tenant_mismatch", "code": 403}

            # Resolve by village_id (tenant-scoped)
            result = resolve_by_village_id(db, village_id, tenant_id=tenant_id)
            if not result:
                return {
                    "village_id": village_id,
                    "type": None,
                    "id": None,
                    "broken": True,
                }

            return _build_resolve_response(
                db,
                result["type"],
                result["id"],
                village_id=village_id,
                tenant_id=parsed.tenant_id,
            )

        elif ref:
            # Parse module:type:id format
            parts = ref.split(":")
            if len(parts) != 3:
                return {"error": "invalid_ref_format"}

            module, type_name, resource_id = parts
            resolvable = get_type(type_name)
            if not resolvable or resolvable.module != module:
                return {"error": "unknown_type"}

            return _build_resolve_response(
                db, type_name, resource_id, tenant_id=tenant_id
            )

        return {"error": "no_ref"}

    result = await run_in_threadpool(search)

    # Handle error codes
    if isinstance(result, dict):
        if result.get("error") == "tenant_mismatch":
            return jsonify({"error": "Tenant mismatch"}), 403
        if result.get("error") in ("invalid_village_id_format", "invalid_ref_format"):
            return jsonify({"error": result["error"]}), 400
        if result.get("error") in ("unknown_type", "no_ref", "missing_parameters"):
            return jsonify({"error": result["error"]}), 400

    return jsonify(result), 200


@bp.route("/refs/backlinks", methods=["GET"])
async def get_backlinks():
    """Get all references pointing to a target resource.

    Query Parameters:
        - target: module:type:id format (e.g., "infrastructure:entity:123")

    Returns:
        200: [{source_module, source_type, source_id, title, url, broken, ref_type}, ...]
        400: Missing or invalid parameters
        401: Unauthenticated (no valid JWT claims)
        403: Forbidden (invalid tenant claim)
    """
    # Fail-closed: require authenticated caller with valid tenant claim
    tenant_id = _get_tenant_id()
    if tenant_id is None:
        return jsonify({"error": "Unauthorized"}), 401

    db = current_app.db
    target = request.args.get("target")

    if not target:
        return (
            jsonify({"error": "missing_target_parameter"}),
            400,
        )

    def search():
        # Parse module:type:id format
        parts = target.split(":")
        if len(parts) != 3:
            return {"error": "invalid_target_format"}

        module, type_name, resource_id = parts
        resolvable = get_type(type_name)
        if not resolvable or resolvable.module != module:
            return {"error": "unknown_type"}

        # Get backlinks for this target (tenant-scoped)
        refs = backlinks_for(db, module, type_name, resource_id, tenant_id)

        results = []
        for ref in refs:
            # Resolve source title (tenant-scoped)
            source_data = resolve_ref(
                db,
                ref.source_module,
                ref.source_type,
                ref.source_id,
                tenant_id=tenant_id,
            )

            results.append(
                {
                    "source_module": ref.source_module,
                    "source_type": ref.source_type,
                    "source_id": ref.source_id,
                    "title": source_data.get("title") if source_data else None,
                    "url": (
                        get_type(ref.source_type).url_pattern.format(id=ref.source_id)
                        if get_type(ref.source_type)
                        else None
                    ),
                    "broken": source_data is None,
                    "ref_type": ref.ref_type,
                }
            )

        return results

    result = await run_in_threadpool(search)

    # Handle error codes
    if isinstance(result, dict):
        if result.get("error") == "invalid_target_format":
            return jsonify({"error": result["error"]}), 400
        if result.get("error") == "unknown_type":
            return jsonify({"error": result["error"]}), 400

    return jsonify({"backlinks": result}), 200
