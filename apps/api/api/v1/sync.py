"""Sync configuration and management API endpoints.

Provides API endpoints for managing two-way synchronization with external
project management platforms (GitHub, GitLab, Jira, Trello, OpenProject).
"""

# flake8: noqa: E501


from datetime import datetime

from flask import Blueprint, current_app, g, jsonify, request
from flask_cors import cross_origin

from apps.api.auth.decorators import admin_required, login_required
from apps.api.utils.async_utils import run_in_threadpool

bp = Blueprint("sync", __name__, url_prefix="/api/v1/sync")


@bp.route("/configs", methods=["GET"])
@cross_origin()
@login_required
def list_sync_configs():
    """List all sync configurations."""
    db = current_app.db

    configs = db(db.sync_configs).select().as_list()

    return jsonify({"configs": configs}), 200


@bp.route("/configs", methods=["POST"])
@cross_origin()
@login_required
@admin_required
async def create_sync_config():
    """Create a new sync configuration."""
    db = current_app.db
    data = request.json

    # Validate required fields
    required = ["name", "platform"]
    if not all(field in data for field in required):
        return jsonify({"error": "Missing required fields"}), 400

    def inner():
        config_id = db.sync_configs.insert(
            name=data["name"],
            platform=data["platform"],
            enabled=data.get("enabled", True),
            sync_interval=data.get("sync_interval", 300),
            batch_fallback_enabled=data.get("batch_fallback_enabled", True),
            batch_size=data.get("batch_size", 100),
            two_way_create=data.get("two_way_create", False),
            webhook_enabled=data.get("webhook_enabled", True),
            webhook_secret=data.get("webhook_secret"),
            config_json=data.get("config_json", {}),
        )
        db.commit()
        config = db.sync_configs[config_id].as_dict()
        return config, None, None

    config, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return jsonify({"config": config}), 201


@bp.route("/configs/<int:config_id>", methods=["GET"])
@cross_origin()
@login_required
def get_sync_config(config_id):
    """Get sync configuration details."""
    db = current_app.db

    config = db.sync_configs[config_id]

    if not config:
        return jsonify({"error": "Config not found"}), 404

    return jsonify({"config": config.as_dict()}), 200


@bp.route("/configs/<int:config_id>", methods=["PATCH"])
@cross_origin()
@admin_required
async def update_sync_config(config_id):
    """Update sync configuration."""
    db = current_app.db
    data = request.json

    def inner():
        config = db.sync_configs[config_id]
        if not config:
            return None, "Config not found", 404

        allowed_fields = [
            "enabled",
            "sync_interval",
            "batch_fallback_enabled",
            "batch_size",
            "two_way_create",
            "webhook_enabled",
            "webhook_secret",
            "config_json",
        ]

        update_data = {k: v for k, v in data.items() if k in allowed_fields}
        db(db.sync_configs.id == config_id).update(**update_data)
        db.commit()

        updated_config = db.sync_configs[config_id].as_dict()
        return updated_config, None, None

    config, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return jsonify({"config": config}), 200


@bp.route("/configs/<int:config_id>", methods=["DELETE"])
@cross_origin()
@admin_required
async def delete_sync_config(config_id):
    """Delete sync configuration."""
    db = current_app.db

    def inner():
        config = db.sync_configs[config_id]
        if not config:
            return None, "Config not found", 404

        db(db.sync_configs.id == config_id).delete()
        db.commit()
        return {"message": "Config deleted"}, None, None

    result, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), 200


@bp.route("/history", methods=["GET"])
@cross_origin()
@login_required
def list_sync_history():
    """List sync history with pagination."""
    db = current_app.db

    # Pagination
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    offset = (page - 1) * per_page

    # Filters
    config_id = request.args.get("config_id", type=int)
    sync_type = request.args.get("sync_type")

    query = db.sync_history

    if config_id:
        query = query(db.sync_history.sync_config_id == config_id)

    if sync_type:
        query = query(db.sync_history.sync_type == sync_type)

    # Get total count
    total = query.count()

    # Get paginated results
    history = query.select(
        orderby=~db.sync_history.started_at, limitby=(offset, offset + per_page)
    ).as_list()

    return (
        jsonify(
            {
                "history": history,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": (total + per_page - 1) // per_page,
                },
            }
        ),
        200,
    )


@bp.route("/conflicts", methods=["GET"])
@cross_origin()
@login_required
def list_sync_conflicts():
    """List unresolved sync conflicts."""
    db = current_app.db

    # Filters
    resolved = request.args.get("resolved", "false").lower() == "true"

    conflicts = (
        db(db.sync_conflicts.resolved == resolved)
        .select(orderby=~db.sync_conflicts.created_at)
        .as_list()
    )

    return jsonify({"conflicts": conflicts}), 200


@bp.route("/conflicts/<int:conflict_id>/resolve", methods=["POST"])
@cross_origin()
@admin_required
async def resolve_conflict(conflict_id):
    """Resolve a sync conflict manually."""
    db = current_app.db
    data = request.json
    user_id = g.current_user.id

    def inner():
        conflict = db.sync_conflicts[conflict_id]
        if not conflict:
            return None, "Conflict not found", 404

        strategy = data.get("resolution_strategy", "manual")
        db(db.sync_conflicts.id == conflict_id).update(
            resolved=True,
            resolved_at=datetime.now(),
            resolved_by_id=user_id,
            resolution_strategy=strategy,
        )
        db.commit()

        updated_conflict = db.sync_conflicts[conflict_id].as_dict()
        return updated_conflict, None, None

    conflict, error, status = await run_in_threadpool(inner)
    if error:
        return jsonify({"error": error}), status
    return jsonify({"conflict": conflict}), 200


@bp.route("/mappings", methods=["GET"])
@cross_origin()
@login_required
def list_sync_mappings():
    """List sync mappings."""
    db = current_app.db

    # Filters
    config_id = request.args.get("config_id", type=int)
    elder_type = request.args.get("elder_type")

    query = db.sync_mappings

    if config_id:
        query = query(db.sync_mappings.sync_config_id == config_id)

    if elder_type:
        query = query(db.sync_mappings.elder_type == elder_type)

    mappings = query.select().as_list()

    return jsonify({"mappings": mappings}), 200


@bp.route("/status", methods=["GET"])
@cross_origin()
@login_required
def sync_status():
    """Get overall sync status summary."""
    db = current_app.db

    # Count configs
    total_configs = db(db.sync_configs).count()
    enabled_configs = db(db.sync_configs.enabled is True).count()

    # Count recent syncs (last 24 hours)
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(hours=24)

    recent_syncs = db(db.sync_history.started_at > cutoff).count()
    recent_failures = db(
        (db.sync_history.started_at > cutoff) & (db.sync_history.success is False)
    ).count()

    # Count unresolved conflicts
    unresolved_conflicts = db(db.sync_conflicts.resolved is False).count()

    # Count mappings
    total_mappings = db(db.sync_mappings).count()

    return (
        jsonify(
            {
                "configs": {
                    "total": total_configs,
                    "enabled": enabled_configs,
                },
                "recent_activity": {
                    "syncs_24h": recent_syncs,
                    "failures_24h": recent_failures,
                },
                "conflicts": {
                    "unresolved": unresolved_conflicts,
                },
                "mappings": {
                    "total": total_mappings,
                },
            }
        ),
        200,
    )
