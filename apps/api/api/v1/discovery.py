"""Cloud Auto-Discovery API endpoints for Elder v1.2.0 (Phase 5)."""

# flake8: noqa: E501


import logging

from flask import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import admin_required, login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.services.discovery import DiscoveryService

logger = logging.getLogger(__name__)

bp = Blueprint("discovery", __name__)


def get_discovery_service(read_only=False):
    """Get DiscoveryService instance with current database.

    Args:
        read_only: If True, uses read replica connection for queries.
    """
    db = current_app.db_read if read_only else current_app.db
    return DiscoveryService(db)


# Discovery Jobs endpoints


@bp.route("/jobs", methods=["GET"])
@login_required
def list_discovery_jobs():
    """
    List all discovery jobs.

    Query params:
        - provider: Filter by cloud provider (aws, gcp, azure, kubernetes)
        - enabled: Filter by enabled status
        - organization_id: Filter by organization

    Returns:
        200: List of discovery jobs
    """
    try:
        service = get_discovery_service(read_only=True)

        provider = request.args.get("provider")
        enabled = request.args.get("enabled")
        organization_id = request.args.get("organization_id", type=int)

        # Convert enabled string to boolean
        enabled_bool = None
        if enabled is not None:
            enabled_bool = enabled.lower() == "true"

        jobs = service.list_jobs(
            provider=provider, enabled=enabled_bool, organization_id=organization_id
        )

        return jsonify({"jobs": jobs, "count": len(jobs)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/jobs", methods=["POST"])
@login_required
@admin_required
def create_discovery_job():
    """
    Create a new discovery job.

    Request body:
        {
            "name": "AWS Production Discovery",
            "provider": "aws",
            "config": {
                "region": "us-east-1",
                "access_key_id": "...",
                "secret_access_key": "...",
                "services": ["ec2", "rds", "s3"]
            },
            "organization_id": 1,
            "schedule_interval": 3600,
            "description": "Discover AWS production resources"
        }

    Returns:
        201: Job created
        400: Invalid request
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["name", "provider", "config", "organization_id"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_discovery_service()
        job = service.create_job(
            name=data["name"],
            provider=data["provider"],
            config=data["config"],
            organization_id=data["organization_id"],
            schedule_interval=data.get("schedule_interval"),
            description=data.get("description"),
        )

        return jsonify(job), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/jobs/<int:job_id>", methods=["GET"])
@login_required
def get_discovery_job(job_id):
    """
    Get discovery job details.

    Returns:
        200: Job details
        404: Job not found
    """
    try:
        service = get_discovery_service(read_only=True)
        job = service.get_job(job_id)
        return jsonify(job), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/jobs/<int:job_id>", methods=["PUT"])
@admin_required
def update_discovery_job(job_id):
    """
    Update discovery job configuration.

    Request body (all optional):
        {
            "name": "Updated Name",
            "config": {...},
            "schedule_interval": 7200,
            "description": "Updated description",
            "enabled": false
        }

    Returns:
        200: Job updated
        404: Job not found
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_discovery_service()
        job = service.update_job(
            job_id=job_id,
            name=data.get("name"),
            config=data.get("config"),
            schedule_interval=data.get("schedule_interval"),
            description=data.get("description"),
            enabled=data.get("enabled"),
        )

        return jsonify(job), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/jobs/<int:job_id>", methods=["DELETE"])
@admin_required
def delete_discovery_job(job_id):
    """
    Delete discovery job.

    Returns:
        200: Job deleted
        404: Job not found
    """
    try:
        service = get_discovery_service()
        result = service.delete_job(job_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/jobs/<int:job_id>/test", methods=["POST"])
@login_required
def test_discovery_job(job_id):
    """
    Test discovery job connectivity.

    Returns:
        200: Test result
        404: Job not found
    """
    try:
        service = get_discovery_service()
        result = service.test_job(job_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/jobs/<int:job_id>/run", methods=["POST"])
@admin_required
def run_discovery_job(job_id):
    """
    Manually trigger a discovery job.

    DEPRECATED: Synchronous discovery execution in the API is deprecated.
    The worker service now handles cloud discovery jobs by polling the DB.
    This endpoint sets next_run_at=now() so the worker picks it up.
    Pass ?legacy=true to force synchronous execution (emergency fallback).

    Sunset target: v4.0.0

    Returns:
        202: Job queued for worker execution
        404: Job not found
    """
    try:
        service = get_discovery_service()

        # Legacy synchronous execution (emergency fallback only)
        legacy = request.args.get("legacy", "").lower() == "true"
        if legacy:
            logger.warning(
                "DEPRECATED: Synchronous discovery execution via API. "
                "Use the worker service instead. Sunset target: v4.0.0"
            )
            result = service.run_discovery(job_id)
            status_code = 202 if result.get("success") else 500
            return jsonify(result), status_code

        # Default: queue for worker by setting next_run_at = now
        result = service.queue_job_for_worker(job_id)
        return jsonify(result), 202

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/jobs/<int:job_id>/history", methods=["GET"])
@login_required
def get_discovery_job_history(job_id):
    """
    Get discovery job execution history.

    Query params:
        - limit: Number of history entries (default: 50)

    Returns:
        200: Job history
    """
    try:
        service = get_discovery_service(read_only=True)

        limit = request.args.get("limit", 50, type=int)

        history = service.get_discovery_history(job_id=job_id, limit=limit)

        return jsonify({"history": history, "count": len(history)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/history", methods=["GET"])
@login_required
def get_all_discovery_history():
    """
    Get all discovery execution history.

    Query params:
        - limit: Number of history entries (default: 50)

    Returns:
        200: Discovery history
    """
    try:
        service = get_discovery_service(read_only=True)

        limit = request.args.get("limit", 50, type=int)

        history = service.get_discovery_history(limit=limit)

        return jsonify({"history": history, "count": len(history)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Scanner service endpoints


@bp.route("/jobs/pending", methods=["GET"])
def get_pending_jobs():
    """
    Get pending scan jobs for the scanner service.

    This endpoint is called by the scanner container to fetch jobs ready to run.
    Jobs are considered pending if they have schedule_interval=0 (one-time run)
    and haven't been run yet, or scheduled jobs that are due.

    Returns:
        200: List of pending jobs
    """
    try:
        service = get_discovery_service(read_only=True)
        jobs = service.get_pending_jobs()
        return jsonify({"jobs": jobs}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to get pending jobs", 500)


@bp.route("/jobs/<int:job_id>/start", methods=["POST"])
def start_job(job_id):
    """
    Mark a job as running.

    Called by the scanner service when it picks up a job.

    Returns:
        200: Job marked as running
        404: Job not found
    """
    try:
        service = get_discovery_service()
        result = service.mark_job_running(job_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Job not found", 404)
        return log_error_and_respond(logger, e, "Failed to start job", 500)


@bp.route("/jobs/<int:job_id>/complete", methods=["POST"])
def complete_job(job_id):
    """
    Submit job results and mark as completed.

    Called by the scanner service when a job finishes.

    Request body:
        {
            "success": true,
            "results": {...},
            "error_message": null
        }

    Returns:
        200: Results recorded
        404: Job not found
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_discovery_service()
        result = service.complete_job(
            job_id=job_id,
            success=data.get("success", False),
            results=data.get("results", {}),
            error_message=data.get("error_message"),
        )
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Job not found", 404)
        return log_error_and_respond(logger, e, "Failed to complete job", 500)
