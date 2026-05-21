"""Backup & Data Management API endpoints for Elder v1.2.0 (Phase 10)."""

# flake8: noqa: E501


import logging
import os
import tempfile

from quart import Blueprint, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from apps.api.auth.decorators import admin_required, login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.services.backup import BackupService

logger = logging.getLogger(__name__)

bp = Blueprint("backup", __name__)


def get_backup_service():
    """Get BackupService instance with current database."""
    return BackupService(current_app.db)


# ===========================
# Backup Job Endpoints
# ===========================


@bp.route("/jobs", methods=["GET"])
@admin_required
def list_backup_jobs():
    """
    List all backup jobs.

    Query params:
        - enabled: Filter by enabled status

    Returns:
        200: List of backup jobs
    """
    try:
        service = get_backup_service()

        enabled = request.args.get("enabled")

        # Convert enabled string to boolean
        enabled_bool = None
        if enabled is not None:
            enabled_bool = enabled.lower() == "true"

        jobs = service.list_backup_jobs(enabled=enabled_bool)

        return jsonify({"jobs": jobs, "count": len(jobs)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/jobs", methods=["POST"])
@login_required
@admin_required
async def create_backup_job():
    """
    Create a new backup job.

    Request body:
        {
            "name": "Daily Full Backup",
            "schedule": "0 2 * * *",
            "retention_days": 30,
            "enabled": true,
            "description": "Daily backup at 2 AM",
            "include_tables": ["entities", "organizations"],
            "exclude_tables": ["audit_logs"]
        }

    Returns:
        201: Job created
        400: Invalid request
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        if "name" not in data:
            return jsonify({"error": "name is required"}), 400

        service = get_backup_service()

        job = service.create_backup_job(
            name=data["name"],
            schedule=data.get("schedule"),
            retention_days=data.get("retention_days", 30),
            enabled=data.get("enabled", True),
            description=data.get("description"),
            include_tables=data.get("include_tables"),
            exclude_tables=data.get("exclude_tables"),
            s3_enabled=data.get("s3_enabled", False),
            s3_endpoint=data.get("s3_endpoint"),
            s3_bucket=data.get("s3_bucket"),
            s3_region=data.get("s3_region"),
            s3_access_key=data.get("s3_access_key"),
            s3_secret_key=data.get("s3_secret_key"),
            s3_prefix=data.get("s3_prefix"),
        )

        return jsonify(job), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/jobs/<int:job_id>", methods=["GET"])
@admin_required
def get_backup_job(job_id):
    """
    Get backup job details.

    Returns:
        200: Job details
        404: Job not found
    """
    try:
        service = get_backup_service()
        job = service.get_backup_job(job_id)
        return jsonify(job), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/jobs/<int:job_id>", methods=["PUT"])
@admin_required
async def update_backup_job(job_id):
    """
    Update backup job configuration.

    Request body (all optional):
        {
            "name": "Updated name",
            "schedule": "0 3 * * *",
            "retention_days": 60,
            "enabled": false,
            "description": "Updated description"
        }

    Returns:
        200: Job updated
        404: Job not found
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        service = get_backup_service()

        job = service.update_backup_job(
            job_id=job_id,
            name=data.get("name"),
            schedule=data.get("schedule"),
            retention_days=data.get("retention_days"),
            enabled=data.get("enabled"),
            description=data.get("description"),
        )

        return jsonify(job), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/jobs/<int:job_id>", methods=["DELETE"])
@admin_required
def delete_backup_job(job_id):
    """
    Delete backup job.

    Returns:
        200: Job deleted
        404: Job not found
    """
    try:
        service = get_backup_service()
        result = service.delete_backup_job(job_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/jobs/<int:job_id>/run", methods=["POST"])
@admin_required
def run_backup_job(job_id):
    """
    Manually trigger a backup job.

    Returns:
        202: Backup started
        404: Job not found
    """
    try:
        service = get_backup_service()
        result = service.run_backup_job(job_id)

        status_code = 202 if result.get("success") else 500
        return jsonify(result), status_code

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Backup Management Endpoints
# ===========================


@bp.route("", methods=["GET"])
@admin_required
def list_backups():
    """
    List all backups.

    Query params:
        - job_id: Filter by backup job
        - limit: Number of backups (default: 50)

    Returns:
        200: List of backups
    """
    try:
        service = get_backup_service()

        job_id = request.args.get("job_id", type=int)
        limit = request.args.get("limit", 50, type=int)

        backups = service.list_backups(job_id=job_id, limit=limit)

        return jsonify({"backups": backups, "count": len(backups)}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:backup_id>", methods=["GET"])
@admin_required
def get_backup(backup_id):
    """
    Get backup details.

    Returns:
        200: Backup details
        404: Backup not found
    """
    try:
        service = get_backup_service()
        backup = service.get_backup(backup_id)
        return jsonify(backup), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:backup_id>/download", methods=["GET"])
@admin_required
def download_backup(backup_id):
    """
    Download backup file.

    Returns:
        200: Backup file
        404: Backup not found
    """
    try:
        service = get_backup_service()
        filepath = service.get_backup_file_path(backup_id)

        return send_file(
            filepath, as_attachment=True, download_name=os.path.basename(filepath)
        )

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/<int:backup_id>", methods=["DELETE"])
@admin_required
def delete_backup(backup_id):
    """
    Delete backup file.

    Returns:
        200: Backup deleted
        404: Backup not found
    """
    try:
        service = get_backup_service()
        result = service.delete_backup(backup_id)
        return jsonify(result), 200

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Restore Operation Endpoints
# ===========================


@bp.route("/<int:backup_id>/restore", methods=["POST"])
@admin_required
async def restore_backup(backup_id):
    """
    Restore from backup.

    Request body:
        {
            "dry_run": false,
            "restore_options": {
                "tables": ["entities", "organizations"],
                "clear_existing": false,
                "regenerate_ids": true
            }
        }

    Returns:
        202: Restore started
        400: Invalid request
        404: Backup not found
    """
    try:
        data = await request.get_json() or {}

        service = get_backup_service()

        result = service.restore_backup(
            backup_id=backup_id,
            dry_run=data.get("dry_run", False),
            restore_options=data.get("restore_options"),
        )

        return jsonify(result), 202

    except Exception as e:
        if "not found" in str(e).lower():
            return log_error_and_respond(logger, e, "Failed to process request", 404)
        return log_error_and_respond(logger, e, "Failed to process request", 400)


# ===========================
# Export Operation Endpoints
# ===========================


@bp.route("/export", methods=["POST"])
@admin_required
async def export_data():
    """
    Export data to various formats.

    Request body:
        {
            "format": "json",
            "resource_types": ["entity", "organization", "issue"],
            "filters": {...}
        }

    Returns:
        202: Export started
        400: Invalid request
    """
    try:
        data = await request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        required = ["format", "resource_types"]
        missing = [f for f in required if f not in data]
        if missing:
            return (
                jsonify({"error": f'Missing required fields: {", ".join(missing)}'}),
                400,
            )

        service = get_backup_service()

        result = service.export_data(
            format=data["format"],
            resource_types=data["resource_types"],
            filters=data.get("filters"),
        )

        return jsonify(result), 202

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/export/<path:filename>/download", methods=["GET"])
@admin_required
def download_export(filename):
    """
    Download exported data.

    Returns:
        200: Export file
        404: Export not found
    """
    try:
        # Secure the filename
        filename = secure_filename(filename)
        filepath = os.path.join(tempfile.gettempdir(), filename)

        if not os.path.exists(filepath):
            return jsonify({"error": "Export file not found"}), 404

        return send_file(filepath, as_attachment=True, download_name=filename)

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# ===========================
# Import Operation Endpoints
# ===========================


@bp.route("/import", methods=["POST"])
@admin_required
def import_data():
    """
    Import data from file.

    Request: multipart/form-data with file upload
        - file: Data file (json, json.gz)
        - dry_run: Test import without committing (optional, default: false)

    Returns:
        202: Import started
        400: Invalid file or format
    """
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]

        if file.filename == "":
            return jsonify({"error": "Empty filename"}), 400

        # Secure the filename
        filename = secure_filename(file.filename)

        # Save to temporary location
        temp_path = os.path.join(tempfile.gettempdir(), filename)
        file.save(temp_path)

        # Get dry_run parameter
        dry_run = request.form.get("dry_run", "false").lower() == "true"

        service = get_backup_service()

        result = service.import_data(filepath=temp_path, dry_run=dry_run)

        # Clean up temp file if not dry run
        if not dry_run and os.path.exists(temp_path):
            os.remove(temp_path)

        return jsonify(result), 202

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


# ===========================
# Storage Statistics Endpoints
# ===========================


@bp.route("/stats", methods=["GET"])
@admin_required
def get_backup_stats():
    """
    Get backup and storage statistics.

    Returns:
        200: Storage statistics
    """
    try:
        service = get_backup_service()
        stats = service.get_backup_stats()
        return jsonify(stats), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)
