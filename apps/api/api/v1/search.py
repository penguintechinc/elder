"""Advanced Search API endpoints for Elder v1.2.0 (Phase 10)."""

# flake8: noqa: E501


import json
import logging

from flask import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.services.search import SearchService

logger = logging.getLogger(__name__)

bp = Blueprint("search", __name__)


def get_search_service():
    """Get SearchService instance with read replica for search queries."""
    return SearchService(current_app.db_read)


# ===========================
# Universal Search Endpoints
# ===========================


@bp.route("", methods=["GET"])
@login_required
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
    """
    try:
        service = get_search_service()

        query = request.args.get("q", "")
        types_str = request.args.get("types", "entity,organization,issue")
        filters_str = request.args.get("filters")
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        # Parse types
        resource_types = [t.strip() for t in types_str.split(",") if t.strip()]

        # Parse filters
        filters = None
        if filters_str:
            filters = json.loads(filters_str)

        results = service.search_all(
            query=query,
            resource_types=resource_types,
            filters=filters,
            limit=limit,
            offset=offset,
        )

        return jsonify(results), 200

    except json.JSONDecodeError:
        return jsonify({"error": "Invalid filters JSON"}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Entity Search Endpoints
# ===========================


@bp.route("/entities", methods=["GET"])
@login_required
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
    """
    try:
        service = get_search_service()

        query = request.args.get("q")
        entity_type = request.args.get("entity_type")
        sub_type = request.args.get("sub_type")
        organization_id = request.args.get("organization_id", type=int)
        tags_str = request.args.get("tags")
        filters_str = request.args.get("filters")
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

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
        return jsonify({"error": "Invalid filters JSON"}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Organization Search Endpoints
# ===========================


@bp.route("/organizations", methods=["GET"])
@login_required
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
    """
    try:
        service = get_search_service()

        query = request.args.get("q")
        organization_type = request.args.get("organization_type")
        parent_id = request.args.get("parent_id", type=int)
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        results = service.search_organizations(
            query=query,
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
    """
    try:
        service = get_search_service()

        query = request.args.get("q")
        status = request.args.get("status")
        priority = request.args.get("priority")
        assignee_id = request.args.get("assignee_id", type=int)
        organization_id = request.args.get("organization_id", type=int)
        labels_str = request.args.get("labels")
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        # Parse labels
        labels = None
        if labels_str:
            labels = [label.strip() for label in labels_str.split(",") if label.strip()]

        results = service.search_issues(
            query=query,
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
def search_graph():
    """
    Graph-based search for entities and dependencies.

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
    """
    try:
        data = request.get_json()

        if not data or "start_entity_id" not in data:
            return jsonify({"error": "start_entity_id is required"}), 400

        service = get_search_service()

        results = service.search_graph(
            start_entity_id=data["start_entity_id"],
            max_depth=data.get("max_depth", 3),
            dependency_types=data.get("dependency_types"),
            entity_filters=data.get("entity_filters"),
        )

        return jsonify(results), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


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

        limit = request.args.get("limit", 50, type=int)

        searches = service.list_saved_searches(user_id=g.current_user.id, limit=limit)

        return jsonify({"searches": searches, "count": len(searches)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/saved", methods=["POST"])
@login_required
def create_saved_search():
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
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "query", "resource_type"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
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
        404: Search not found
    """
    try:
        service = get_search_service()

        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        results = service.execute_saved_search(
            search_id=search_id, user_id=g.current_user.id, limit=limit, offset=offset
        )

        return jsonify(results), 200

    except Exception as e:
        if "not found" in str(e).lower() or "not owned" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/saved/<int:search_id>", methods=["PUT"])
@login_required
def update_saved_search(search_id):
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
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

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
def get_popular_searches():
    """
    Get most popular/frequent searches.

    Query params:
        - limit: Maximum results (default: 10)

    Returns:
        200: Popular search terms and patterns
    """
    try:
        service = get_search_service()

        limit = request.args.get("limit", 10, type=int)

        popular = service.get_popular_searches(limit=limit)

        return jsonify({"popular_searches": popular, "count": len(popular)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/suggest", methods=["GET"])
@login_required
def search_suggestions():
    """
    Get search suggestions/autocomplete.

    Query params:
        - q: Partial query string
        - type: Resource type for suggestions
        - limit: Maximum suggestions (default: 10)

    Returns:
        200: Search suggestions
    """
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
        limit = request.args.get("limit", 10, type=int)

        suggestions = service.get_search_suggestions(
            partial_query=query, resource_type=resource_type, limit=limit
        )

        return jsonify({"suggestions": suggestions, "count": len(suggestions)}), 200

    except Exception as e:
        db.rollback()  # Rollback on error
        return log_error_and_respond(logger, e, "Failed to process request", 500)
