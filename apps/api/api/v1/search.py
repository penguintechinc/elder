"""Advanced Search API endpoints for Elder v1.2.0 (Phase 10)."""

# flake8: noqa: E501

import json
import logging
from typing import Any

from quart import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required, require_scope
from apps.api.auth.jwt_handler import (
    get_service_researcher_read_scopes,
    get_token_from_header,
)
from apps.api.logging_config import log_error_and_respond
from apps.api.modules.infrastructure.routes.graph import (
    graph_limited,
    scoped_entity_search_graph,
)
from apps.api.services.ai_researcher import (
    AIResearchMcpUnavailableError,
    AIResearchPermissionError,
    AIResearchValidationError,
    OllamaUnavailableError,
    get_ai_researcher_config,
    run_ai_research,
)
from apps.api.services.search import SearchService
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool
from apps.api.utils.inflight_limiter import InflightLimiter
from apps.api.utils.pagination import (
    clamp_int,
    env_bounded_int,
    parse_limit,
    parse_offset,
)
from apps.api.utils.rate_limiter import check_subject_rate_limit
from apps.api.utils.tenant_scoping import caller_has_scope, get_current_tenant_id
from apps.mcp.envutil import env_int

logger = logging.getLogger(__name__)

bp = Blueprint("search", __name__)

# Hard ceiling on traversal depth for POST /search/graph (shared with /graph).
_GRAPH_SEARCH_MAX_DEPTH = env_bounded_int("ELDER_GRAPH_MAX_DEPTH", 6, 25)

# Per-replica in-flight cap for /ai-research (see _ai_research_admit).
_ai_research_inflight = InflightLimiter()


def get_search_service():
    """Get SearchService instance with read replica for search queries."""
    return SearchService(current_app.db_read)


def _readable_types(requested: list[str]) -> list[str]:
    """Drop resource families the caller has no read scope for.

    Entities and organizations are gated by the route's
    ``infrastructure:read`` scope; issues additionally need ``issues:read``.
    """
    if caller_has_scope("issues:read"):
        return requested
    return [t for t in requested if t != "issue"]


def _tenant_or_403() -> tuple[int | None, Any]:
    """Resolve the caller's tenant from the verified token claims only.

    Returns ``(tenant_id, None)`` or ``(None, 403 response)`` when the token
    carries no usable tenant -- a missing tenant never means "all tenants".
    """
    tenant_id = get_current_tenant_id()
    if not tenant_id or tenant_id <= 0:
        return None, ApiResponse.forbidden("Tenant context required")
    return tenant_id, None


# ===========================
# Universal Search Endpoints
# ===========================


@bp.route("", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
def search_all():
    """
    Advanced search across all resources.

    Query params:
        - q: Search query
        - types: Resource types to search (comma-separated: entity,organization,issue)
        - filters: JSON-encoded filter object
        - limit: Results per page (default: 50)
        - offset: Pagination offset

    Returns:
        200: Search results
        400: Invalid query
        403: Missing scope or tenant context
    """
    tenant_id, denied = _tenant_or_403()
    if denied:
        return denied
    try:
        service = get_search_service()

        query = request.args.get("q", "")
        types_str = request.args.get("types", "entity,organization,issue")
        filters_str = request.args.get("filters")
        limit = parse_limit(request.args, 50)
        offset = parse_offset(request.args)

        # Parse types
        resource_types = _readable_types(
            [t.strip() for t in types_str.split(",") if t.strip()]
        )

        # Parse filters
        filters = None
        if filters_str:
            filters = json.loads(filters_str)

        results = service.search_all(
            query=query,
            tenant_id=tenant_id,
            resource_types=resource_types,
            filters=filters,
            limit=limit,
            offset=offset,
        )

        return jsonify(results), 200

    except json.JSONDecodeError:
        return ApiResponse.bad_request("Invalid filters JSON")
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Entity Search Endpoints
# ===========================


@bp.route("/entities", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
def search_entities():
    """
    Search entities with advanced filters.

    Query params:
        - q: Search query
        - entity_type: Filter by entity type
        - sub_type: Filter by entity sub-type
        - organization_id: Filter by organization
        - tags: Filter by tags (comma-separated)
        - filters: JSON filter for entity attributes
        - limit: Results per page (default: 50)
        - offset: Pagination offset

    Returns:
        200: Entity search results
        403: Missing scope or tenant context
    """
    tenant_id, denied = _tenant_or_403()
    if denied:
        return denied
    try:
        service = get_search_service()

        query = request.args.get("q")
        entity_type = request.args.get("entity_type")
        sub_type = request.args.get("sub_type")
        organization_id = request.args.get("organization_id", type=int)
        tags_str = request.args.get("tags")
        filters_str = request.args.get("filters")
        limit = parse_limit(request.args, 50)
        offset = parse_offset(request.args)

        # Parse tags
        tags = None
        if tags_str:
            tags = [t.strip() for t in tags_str.split(",") if t.strip()]

        # Parse filters
        filters = None
        if filters_str:
            filters = json.loads(filters_str)

        results = service.search_entities(
            query=query,
            tenant_id=tenant_id,
            entity_type=entity_type,
            sub_type=sub_type,
            organization_id=organization_id,
            tags=tags,
            filters=filters,
            limit=limit,
            offset=offset,
        )

        return jsonify(results), 200

    except json.JSONDecodeError:
        return ApiResponse.bad_request("Invalid filters JSON")
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Organization Search Endpoints
# ===========================


@bp.route("/organizations", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
def search_organizations():
    """
    Search organizations.

    Query params:
        - q: Search query
        - organization_type: Filter by type
        - parent_id: Filter by parent organization
        - limit: Results per page (default: 50)
        - offset: Pagination offset

    Returns:
        200: Organization search results
        403: Missing scope or tenant context
    """
    tenant_id, denied = _tenant_or_403()
    if denied:
        return denied
    try:
        service = get_search_service()

        query = request.args.get("q")
        organization_type = request.args.get("organization_type")
        parent_id = request.args.get("parent_id", type=int)
        limit = parse_limit(request.args, 50)
        offset = parse_offset(request.args)

        results = service.search_organizations(
            query=query,
            tenant_id=tenant_id,
            organization_type=organization_type,
            parent_id=parent_id,
            limit=limit,
            offset=offset,
        )

        return jsonify(results), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Issue Search Endpoints
# ===========================


@bp.route("/issues", methods=["GET"])
@login_required
@require_scope("issues:read")
def search_issues():
    """
    Search issues with advanced filters.

    Query params:
        - q: Search query
        - status: Filter by status
        - priority: Filter by priority
        - assignee_id: Filter by assignee
        - organization_id: Filter by organization
        - labels: Filter by labels (comma-separated)
        - limit: Results per page (default: 50)
        - offset: Pagination offset

    Returns:
        200: Issue search results
        403: Missing scope or tenant context
    """
    tenant_id, denied = _tenant_or_403()
    if denied:
        return denied
    try:
        service = get_search_service()

        query = request.args.get("q")
        status = request.args.get("status")
        priority = request.args.get("priority")
        assignee_id = request.args.get("assignee_id", type=int)
        organization_id = request.args.get("organization_id", type=int)
        labels_str = request.args.get("labels")
        limit = parse_limit(request.args, 50)
        offset = parse_offset(request.args)

        # Parse labels
        labels = None
        if labels_str:
            labels = [label.strip() for label in labels_str.split(",") if label.strip()]

        results = service.search_issues(
            query=query,
            tenant_id=tenant_id,
            status=status,
            priority=priority,
            assignee_id=assignee_id,
            organization_id=organization_id,
            labels=labels,
            limit=limit,
            offset=offset,
        )

        return jsonify(results), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Graph Search Endpoints
# ===========================


@bp.route("/graph", methods=["POST"])
@login_required
@require_scope("infrastructure:read")
@graph_limited
async def search_graph():
    """
    Graph-based search for entities and dependencies.

    Scoped to the caller's tenant: the start entity must belong to it and
    the traversal never leaves it. Depth, nodes and edges are capped.

    Request body:
        {
            "start_entity_id": 1,
            "max_depth": 3,
            "dependency_types": ["depends_on", "connects_to"],
            "entity_filters": {...}
        }

    Returns:
        200: Graph search results
        400: Invalid request
        403: Missing scope or tenant claim
        404: Start entity not found
    """
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        return jsonify({"error": "Missing tenant claim in token"}), 403

    data = await request.get_json(silent=True)
    if not isinstance(data, dict) or "start_entity_id" not in data:
        return ApiResponse.bad_request("start_entity_id is required")

    start_entity_id = clamp_int(data["start_entity_id"], 0, 0, 2**63 - 1)
    if not start_entity_id:
        return ApiResponse.bad_request("start_entity_id must be a positive integer")

    raw_types = data.get("dependency_types")
    if raw_types is not None and (
        not isinstance(raw_types, list)
        or len(raw_types) > 50
        or not all(isinstance(t, str) and len(t) <= 64 for t in raw_types)
    ):
        return ApiResponse.bad_request("dependency_types must be a list of strings")

    max_depth = clamp_int(data.get("max_depth"), 3, 1, _GRAPH_SEARCH_MAX_DEPTH)
    db = current_app.db_read

    try:
        results = await run_in_threadpool(
            scoped_entity_search_graph,
            db,
            tenant_id,
            start_entity_id,
            max_depth,
            raw_types,
        )
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)

    if results is None:
        return jsonify({"error": "Entity not found"}), 404

    return jsonify(results), 200


# ===========================
# Saved Search Endpoints
# ===========================


@bp.route("/saved", methods=["GET"])
@login_required
def list_saved_searches():
    """
    List user's saved searches.

    Query params:
        - limit: Maximum results (default: 50)

    Returns:
        200: List of saved searches
    """
    try:
        service = get_search_service()

        limit = parse_limit(request.args, 50)

        searches = service.list_saved_searches(user_id=g.current_user.id, limit=limit)

        return jsonify({"searches": searches, "count": len(searches)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/saved", methods=["POST"])
@login_required
async def create_saved_search():
    """
    Save a search query.

    Request body:
        {
            "name": "Critical entities in production",
            "query": "entity_type:compute AND tags:production",
            "resource_type": "entity",
            "filters": {...},
            "description": "Find all critical compute entities"
        }

    Returns:
        201: Saved search created
        400: Invalid request
    """
    try:
        data = await request.get_json()

        if not data:
            return ApiResponse.bad_request("Request body required")

        required = ["name", "query", "resource_type"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f"Missing required fields: {', '.join(missing)}"}),
                400,
            )

        service = get_search_service()

        search = service.create_saved_search(
            user_id=g.current_user.id,
            name=data["name"],
            query=data["query"],
            resource_type=data["resource_type"],
            filters=data.get("filters"),
            description=data.get("description"),
        )

        return jsonify(search), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/saved/<int:search_id>", methods=["GET"])
@login_required
def get_saved_search(search_id):
    """
    Get saved search details.

    Returns:
        200: Saved search details
        404: Search not found
    """
    try:
        service = get_search_service()

        search = service.get_saved_search(
            search_id=search_id, user_id=g.current_user.id
        )

        return jsonify(search), 200

    except Exception as e:
        if "not found" in str(e).lower() or "not owned" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/saved/<int:search_id>/execute", methods=["GET"])
@login_required
def execute_saved_search(search_id):
    """
    Execute a saved search.

    Query params:
        - limit: Maximum results (default: 50)
        - offset: Pagination offset

    Returns:
        200: Search results
        403: Missing tenant context
        404: Search not found
    """
    tenant_id, denied = _tenant_or_403()
    if denied:
        return denied
    try:
        service = get_search_service()

        limit = parse_limit(request.args, 50)
        offset = parse_offset(request.args)

        # Results are tenant-scoped and limited to families the caller can read.
        readable = ["issue"] if caller_has_scope("issues:read") else []
        if caller_has_scope("infrastructure:read"):
            readable = ["entity", "organization", *readable]

        results = service.execute_saved_search(
            search_id=search_id,
            user_id=g.current_user.id,
            tenant_id=tenant_id,
            resource_types=readable,
            limit=limit,
            offset=offset,
        )

        return jsonify(results), 200

    except Exception as e:
        if "not found" in str(e).lower() or "not owned" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/saved/<int:search_id>", methods=["PUT"])
@login_required
async def update_saved_search(search_id):
    """
    Update saved search.

    Request body (all optional):
        {
            "name": "Updated name",
            "query": "new query",
            "filters": {...},
            "description": "Updated description"
        }

    Returns:
        200: Search updated
        404: Search not found
    """
    try:
        data = await request.get_json()

        if not data:
            return ApiResponse.bad_request("Request body required")

        service = get_search_service()

        search = service.update_saved_search(
            search_id=search_id,
            user_id=g.current_user.id,
            name=data.get("name"),
            query=data.get("query"),
            filters=data.get("filters"),
            description=data.get("description"),
        )

        return jsonify(search), 200

    except Exception as e:
        if "not found" in str(e).lower() or "not owned" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/saved/<int:search_id>", methods=["DELETE"])
@login_required
def delete_saved_search(search_id):
    """
    Delete saved search.

    Returns:
        200: Search deleted
        404: Search not found
    """
    try:
        service = get_search_service()

        result = service.delete_saved_search(
            search_id=search_id, user_id=g.current_user.id
        )

        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower() or "not owned" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Search Analytics Endpoints
# ===========================


@bp.route("/analytics/popular", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
def get_popular_searches():
    """
    Get most popular/frequent searches.

    Query params:
        - limit: Maximum results (default: 10)

    Returns:
        200: Popular search terms and patterns (caller's tenant only)
        403: Missing scope or tenant context
    """
    tenant_id, denied = _tenant_or_403()
    if denied:
        return denied
    try:
        service = get_search_service()

        limit = parse_limit(request.args, 10)

        popular = service.get_popular_searches(tenant_id=tenant_id, limit=limit)

        return jsonify({"popular_searches": popular, "count": len(popular)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/suggest", methods=["GET"])
@login_required
@require_scope("infrastructure:read")
def search_suggestions():
    """
    Get search suggestions/autocomplete.

    Query params:
        - q: Partial query string
        - type: Resource type for suggestions
        - limit: Maximum suggestions (default: 10)

    Returns:
        200: Search suggestions (caller's tenant only)
        403: Missing scope or tenant context
    """
    tenant_id, denied = _tenant_or_403()
    if denied:
        return denied
    db = current_app.db
    # Ensure clean transaction state
    try:
        db.commit()
    except Exception:
        db.rollback()

    try:
        service = get_search_service()

        query = request.args.get("q", "")
        resource_type = request.args.get("type")
        limit = parse_limit(request.args, 10)

        suggestions = service.get_search_suggestions(
            partial_query=query,
            tenant_id=tenant_id,
            resource_type=resource_type,
            limit=limit,
            include_issues=caller_has_scope("issues:read"),
        )

        return jsonify({"suggestions": suggestions, "count": len(suggestions)}), 200

    except Exception as e:
        db.rollback()  # Rollback on error
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# AI Researcher Endpoints
# ===========================


def _resolve_request_tenant_id() -> int | None:
    """Resolve authenticated tenant ID from current_user or verified JWT claims."""
    user = getattr(g, "current_user", None)
    raw_tenant = getattr(user, "tenant_id", None) if user is not None else None
    if raw_tenant is None:
        raw_tenant = (getattr(g, "claims", {}) or {}).get("tenant")
    if isinstance(raw_tenant, bool) or raw_tenant is None or raw_tenant == "":
        return None
    try:
        parsed = int(str(raw_tenant).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_ai_research_caller() -> tuple[str, Any, str | None]:
    """Extract caller_sub, caller_scopes, and caller_username from request context."""
    user = getattr(g, "current_user", None)
    claims = getattr(g, "claims", {}) or {}
    caller_sub = str(
        claims.get("sub") or (getattr(user, "id", "") if user is not None else "") or ""
    ).strip()
    caller_username = getattr(user, "username", None) if user is not None else None
    caller_scopes = claims.get("scope", [])
    if (
        not caller_scopes
        and user is not None
        and getattr(user, "is_superuser", False) in (True, 1)
    ):
        caller_scopes = get_service_researcher_read_scopes()
    return caller_sub, caller_scopes, caller_username


@bp.route("/ai-research/config", methods=["GET"])
@login_required
@require_scope("ai:read")
async def get_ai_research_config_endpoint():
    """Return AI Researcher model configuration, Ollama health, and enabled MCP tool count."""
    tenant_id = _resolve_request_tenant_id()
    if tenant_id is None:
        return (
            jsonify(
                {"error": "TENANT_REQUIRED", "message": "Tenant context is required"}
            ),
            403,
        )
    _, caller_scopes, _ = _resolve_ai_research_caller()
    try:
        config = await get_ai_researcher_config(
            current_app, tenant_id, caller_scopes=caller_scopes
        )
        return jsonify(config), 200
    except AIResearchPermissionError as exc:
        return jsonify({"error": "FORBIDDEN", "message": str(exc)}), 403
    except Exception as exc:
        return log_error_and_respond(
            logger, exc, "Failed to load AI Researcher configuration", 500
        )


@bp.route("/ai-research", methods=["POST"])
@login_required
@require_scope("ai:read")
async def run_ai_research_endpoint():
    """Execute a multi-step read-only AI Researcher query backed by Ollama + Elder MCP."""
    user = getattr(g, "current_user", None)
    if getattr(user, "token_type", None) == "service_researcher":
        return (
            jsonify(
                {
                    "error": "FORBIDDEN",
                    "message": "service_researcher tokens cannot invoke AI research recursively",
                }
            ),
            403,
        )

    tenant_id = _resolve_request_tenant_id()
    if tenant_id is None:
        return (
            jsonify(
                {"error": "TENANT_REQUIRED", "message": "Tenant context is required"}
            ),
            403,
        )

    caller_sub, caller_scopes, caller_username = _resolve_ai_research_caller()
    # Forward the caller's own validated credential (header or HTTP-only cookie).
    caller_token = get_token_from_header()
    if not caller_token:
        return (
            jsonify({"error": "UNAUTHORIZED", "message": "Authentication required"}),
            401,
        )

    # Per-user limits keyed by the validated token identity (never the client IP).
    limit_key = f"{tenant_id}:{caller_sub}"
    refusal = await _ai_research_admit(limit_key)
    if refusal is not None:
        return refusal
    try:
        return await _run_ai_research_admitted(
            tenant_id,
            caller_sub,
            caller_scopes,
            caller_token,
            caller_username,
        )
    finally:
        _ai_research_inflight.release(limit_key)


async def _ai_research_admit(limit_key: str):
    """Apply the per-user rate and in-flight caps; return a 429 tuple or None if admitted.

    On admission an in-flight slot is held and the caller must release it.
    """
    max_inflight = env_int("AI_RESEARCH_MAX_CONCURRENT_PER_USER", 2)
    per_minute = env_int("AI_RESEARCH_RATE_PER_MINUTE", 10)
    allowed, headers = await check_subject_rate_limit(
        current_app,
        subject=limit_key,
        limit_per_minute=per_minute,
        key_prefix="elder:ai_research",
    )
    if not allowed:
        resp = jsonify(
            {"error": "RATE_LIMITED", "message": "Too many AI research requests"}
        )
        resp.status_code = 429
        resp.headers["Retry-After"] = headers.get("Retry-After", "60")
        return resp
    if not _ai_research_inflight.try_acquire(limit_key, max_inflight):
        resp = jsonify(
            {
                "error": "TOO_MANY_CONCURRENT",
                "message": "Too many concurrent AI research requests",
            }
        )
        resp.status_code = 429
        resp.headers["Retry-After"] = "5"
        return resp
    return None


async def _run_ai_research_admitted(
    tenant_id: int,
    caller_sub: str,
    caller_scopes: Any,
    caller_token: str,
    caller_username: str | None,
):
    """Parse the request body and run the researcher (caller holds an in-flight slot)."""
    try:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return (
                jsonify(
                    {
                        "error": "INVALID_REQUEST",
                        "message": "Request body must be a JSON object",
                    }
                ),
                400,
            )

        query = payload.get("query") or payload.get("q") or ""
        model = payload.get("model")
        history = payload.get("history")
        max_steps = payload.get("max_steps")

        result = await run_ai_research(
            current_app,
            tenant_id,
            query=str(query),
            caller_sub=caller_sub,
            caller_scopes=caller_scopes,
            caller_token=caller_token,
            caller_username=caller_username,
            model=str(model) if isinstance(model, str) else None,
            history=history if isinstance(history, list) else None,
            max_steps=int(max_steps) if isinstance(max_steps, int) else None,
        )
        return jsonify(result), 200
    except AIResearchPermissionError as exc:
        return jsonify({"error": "FORBIDDEN", "message": str(exc)}), 403
    except AIResearchValidationError as exc:
        return jsonify({"error": "INVALID_REQUEST", "message": str(exc)}), 400
    except OllamaUnavailableError as exc:
        return jsonify({"error": "OLLAMA_UNAVAILABLE", "message": str(exc)}), 503
    except AIResearchMcpUnavailableError as exc:
        # Detail (may expose internal hosts) is logged server-side, never returned.
        logger.warning("ai_research_mcp_unavailable", extra={"error": str(exc)[:300]})
        return (
            jsonify(
                {
                    "error": "MCP_UNAVAILABLE",
                    "message": "The Elder MCP service is currently unavailable",
                }
            ),
            503,
        )
    except Exception as exc:
        return log_error_and_respond(logger, exc, "AI Researcher execution failed", 500)
