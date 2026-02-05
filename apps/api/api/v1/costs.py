"""Cost tracking API endpoints."""

# flake8: noqa: E501

from flask import Blueprint, g, jsonify, request

bp = Blueprint("costs", __name__)


def _get_cost_service():
    """Get CostService instance."""
    from apps.api.services.costs.cost_service import CostService
    return CostService(g.db)


@bp.route("/<resource_type>/<int:resource_id>", methods=["GET"])
def get_resource_costs(resource_type, resource_id):
    """Get cost data for a specific resource."""
    valid_types = ["entity", "service", "data_store", "networking_resource", "certificate"]
    if resource_type not in valid_types:
        return jsonify({"error": f"Invalid resource_type. Must be one of: {valid_types}"}), 400

    service = _get_cost_service()
    costs = service.get_resource_costs(resource_type, resource_id)

    if not costs:
        return jsonify({"data": None, "message": "No cost data found"}), 200

    return jsonify({"data": costs}), 200


@bp.route("/<resource_type>/<int:resource_id>", methods=["POST"])
def update_resource_costs(resource_type, resource_id):
    """Create or update cost entry for a resource."""
    valid_types = ["entity", "service", "data_store", "networking_resource", "certificate"]
    if resource_type not in valid_types:
        return jsonify({"error": f"Invalid resource_type. Must be one of: {valid_types}"}), 400

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    service = _get_cost_service()
    try:
        cost_id = service.update_resource_costs(resource_type, resource_id, data)
        g.db.commit()
        return jsonify({"data": {"id": cost_id}, "message": "Cost data updated"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/sync-jobs", methods=["GET"])
def list_sync_jobs():
    """List cost sync jobs."""
    db = g.db
    jobs = db(db.cost_sync_jobs.id > 0).select(
        orderby=db.cost_sync_jobs.name
    )
    return jsonify({"data": [j.as_dict() for j in jobs]}), 200


@bp.route("/sync-jobs", methods=["POST"])
def create_sync_job():
    """Create a cost sync job."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    required = ["name", "provider", "organization_id", "config_json"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400

    db = g.db
    try:
        job_id = db.cost_sync_jobs.insert(
            name=data["name"],
            provider=data["provider"],
            organization_id=data["organization_id"],
            config_json=data["config_json"],
            schedule_interval=data.get("schedule_interval", 86400),
            enabled=data.get("enabled", True),
        )
        db.commit()
        return jsonify({"data": {"id": job_id}, "message": "Sync job created"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/sync-jobs/<int:job_id>/run", methods=["POST"])
def run_sync_job(job_id):
    """Trigger a manual cost sync."""
    service = _get_cost_service()
    try:
        result = service.sync_costs_from_provider(job_id)
        return jsonify({"data": result, "message": "Sync completed"}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
