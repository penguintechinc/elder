"""REST API endpoints for networking resources and topology."""

# flake8: noqa: E501


import logging

from flask import Blueprint, jsonify, request
from penguin_libs.pydantic.flask_integration import ValidationErrorResponse
from apps.api.models.pydantic.network import CreateNetworkRequest, UpdateNetworkRequest
from pydantic import ValidationError

from apps.api.auth.decorators import login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.services.networking import NetworkingService

logger = logging.getLogger(__name__)

bp = Blueprint("networking", __name__, url_prefix="/api/v1/networking")


# Networking Resources Endpoints


@bp.route("/networks", methods=["GET"])
@login_required
def list_networks():
    """List networking resources with filters."""
    try:
        service = NetworkingService()

        organization_id = request.args.get("organization_id", type=int)
        network_type = request.args.get("network_type")
        parent_id = request.args.get("parent_id", type=int)
        region = request.args.get("region")
        is_active = request.args.get("is_active", "true").lower() == "true"
        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)

        result = service.list_networks(
            organization_id=organization_id,
            network_type=network_type,
            parent_id=parent_id,
            region=region,
            is_active=is_active,
            limit=limit,
            offset=offset,
        )

        return jsonify(result), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/networks/<int:network_id>", methods=["GET"])
@login_required
def get_network(network_id):
    """Get networking resource by ID."""
    try:
        service = NetworkingService()
        network = service.get_network(network_id)
        return jsonify(network), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/networks", methods=["POST"])
@login_required
def create_network():
    """Create a new networking resource."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        body = CreateNetworkRequest.model_validate(data)
        service = NetworkingService()

        network = service.create_network(
            name=body.name,
            network_type=body.network_type,
            organization_id=body.organization_id,
            description=body.description,
            gateway=body.gateway,
            vlan_id=body.vlan_id,
            mtu=body.mtu,
            cidr=body.cidr,
            region=body.region,
            location=body.location,
            is_active=body.is_active,
        )

        return jsonify(network), 201

    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)
    except Exception as e:
        logger.error(f"Create network error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/networks/<int:network_id>", methods=["PUT", "PATCH"])
@login_required
def update_network(network_id):
    """Update networking resource."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        body = UpdateNetworkRequest.model_validate(data)
        service = NetworkingService()

        network = service.update_network(
            network_id=network_id,
            name=body.name,
            description=body.description,
            gateway=body.gateway,
            vlan_id=body.vlan_id,
            mtu=body.mtu,
            is_active=body.is_active,
        )

        return jsonify(network), 200

    except ValidationError as e:
        return ValidationErrorResponse.from_pydantic_error(e)
    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Update network error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/networks/<int:network_id>", methods=["DELETE"])
@login_required
def delete_network(network_id):
    """Delete networking resource."""
    try:
        hard_delete = request.args.get("hard", "false").lower() == "true"

        service = NetworkingService()
        result = service.delete_network(network_id, hard_delete=hard_delete)

        return jsonify(result), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Delete network error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Network Topology Endpoints


@bp.route("/topology/connections", methods=["GET"])
@login_required
def list_topology_connections():
    """List topology connections."""
    try:
        service = NetworkingService()

        network_id = request.args.get("network_id", type=int)
        connection_type = request.args.get("connection_type")

        connections = service.list_topology_connections(
            network_id=network_id,
            connection_type=connection_type,
        )

        return jsonify({"connections": connections}), 200

    except Exception as e:
        logger.error(f"List topology connections error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/topology/connections/<int:connection_id>", methods=["GET"])
@login_required
def get_topology_connection(connection_id):
    """Get topology connection by ID."""
    try:
        service = NetworkingService()
        connection = service.get_topology_connection(connection_id)
        return jsonify(connection), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Get topology connection error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/topology/connections", methods=["POST"])
@login_required
def create_topology_connection():
    """Create a network topology connection."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required_fields = ["source_network_id", "target_network_id", "connection_type"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            return (
                jsonify({"error": f"Missing required fields: {', '.join(missing)}"}),
                400,
            )

        service = NetworkingService()

        connection = service.create_topology_connection(
            source_network_id=data["source_network_id"],
            target_network_id=data["target_network_id"],
            connection_type=data["connection_type"],
            bandwidth=data.get("bandwidth"),
            latency=data.get("latency"),
            metadata=data.get("metadata"),
        )

        return jsonify(connection), 201

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Create topology connection error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/topology/connections/<int:connection_id>", methods=["DELETE"])
@login_required
def delete_topology_connection(connection_id):
    """Delete a topology connection."""
    try:
        service = NetworkingService()
        result = service.delete_topology_connection(connection_id)
        return jsonify(result), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Delete topology connection error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Entity-Network Mapping Endpoints


@bp.route("/mappings", methods=["GET"])
@login_required
def list_entity_mappings():
    """List entity-network mappings."""
    try:
        service = NetworkingService()

        network_id = request.args.get("network_id", type=int)
        entity_id = request.args.get("entity_id", type=int)
        relationship_type = request.args.get("relationship_type")

        mappings = service.list_entity_mappings(
            network_id=network_id,
            entity_id=entity_id,
            relationship_type=relationship_type,
        )

        return jsonify({"mappings": mappings}), 200

    except Exception as e:
        logger.error(f"List entity mappings error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/mappings/<int:mapping_id>", methods=["GET"])
@login_required
def get_entity_mapping(mapping_id):
    """Get entity-network mapping by ID."""
    try:
        service = NetworkingService()
        mapping = service.get_entity_mapping(mapping_id)
        return jsonify(mapping), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Get entity mapping error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/mappings", methods=["POST"])
@login_required
def create_entity_mapping():
    """Map an entity to a network."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required_fields = ["network_id", "entity_id", "relationship_type"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            return (
                jsonify({"error": f"Missing required fields: {', '.join(missing)}"}),
                400,
            )

        service = NetworkingService()

        mapping = service.map_entity_to_network(
            network_id=data["network_id"],
            entity_id=data["entity_id"],
            relationship_type=data["relationship_type"],
            metadata=data.get("metadata"),
        )

        return jsonify(mapping), 201

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Create entity mapping error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/mappings/<int:mapping_id>", methods=["DELETE"])
@login_required
def delete_entity_mapping(mapping_id):
    """Delete an entity-network mapping."""
    try:
        service = NetworkingService()
        result = service.delete_entity_mapping(mapping_id)
        return jsonify(result), 200

    except ValueError as e:
        return log_error_and_respond(logger, e, "Failed to process request", 404)
    except Exception as e:
        logger.error(f"Delete entity mapping error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Topology Visualization Endpoint


@bp.route("/topology/graph", methods=["GET"])
@login_required
def get_topology_graph():
    """Get network topology as a graph for visualization."""
    try:
        organization_id = request.args.get("organization_id", type=int)
        include_entities = (
            request.args.get("include_entities", "false").lower() == "true"
        )

        if not organization_id:
            return jsonify({"error": "organization_id parameter required"}), 400

        service = NetworkingService()
        graph = service.get_network_topology_graph(
            organization_id=organization_id,
            include_entities=include_entities,
        )

        return jsonify(graph), 200

    except Exception as e:
        logger.error(f"Get topology graph error: {str(e)}")
        return log_error_and_respond(logger, e, "Failed to process request", 500)
