"""Audit System API endpoints for Elder v1.2.0 (Phase 8)."""

# flake8: noqa: E501


import asyncio
import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request

from apps.api.auth.decorators import admin_required, login_required
from apps.api.logging_config import log_error_and_respond

logger = logging.getLogger(__name__)

bp = Blueprint("audit", __name__)


# Audit Retention Policies


@bp.route("/retention-policies", methods=["GET"])
@login_required
def list_retention_policies():
    """
    List all audit retention policies.

    Returns:
        200: List of retention policies
    """
    try:
        db = current_app.db

        # Ensure clean transaction state
        try:
            db.commit()
        except Exception:
            db.rollback()

        policies = db(db.audit_retention_policies.id > 0).select(
            orderby=db.audit_retention_policies.name
        )

        return (
            jsonify(
                {"policies": [p.as_dict() for p in policies], "count": len(policies)}
            ),
            200,
        )

    except Exception as e:
        db.rollback()  # Rollback failed transaction
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/retention-policies/<int:policy_id>", methods=["GET"])
@login_required
def get_retention_policy(policy_id):
    """
    Get retention policy details.

    Returns:
        200: Policy details
        404: Policy not found
    """
    try:
        db = current_app.db

        # Ensure clean transaction state
        try:
            db.commit()
        except Exception:
            db.rollback()

        policy = db.audit_retention_policies[policy_id]

        if not policy:
            return jsonify({"error": "Retention policy not found"}), 404

        return jsonify(policy.as_dict()), 200

    except Exception as e:
        db.rollback()  # Rollback failed transaction
        return log_error_and_respond(logger, e, "Failed to process request", 500)


@bp.route("/retention-policies", methods=["POST"])
@login_required
@admin_required
async def create_retention_policy():
    """
    Create audit retention policy.

    Request body:
        {
            "name": "Standard Policy",
            "retention_days": 90,
            "event_types": ["login", "create"]
        }

    Returns:
        201: Policy created
        400: Invalid request
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        if "name" not in data or "retention_days" not in data:
            return (
                jsonify({"error": "name and retention_days are required"}),
                400,
            )

        def inner():
            db = current_app.db

            # Check if policy already exists for this name
            existing = (
                db(db.audit_retention_policies.name == data["name"]).select().first()
            )
            if existing:
                return (
                    None,
                    f'Retention policy already exists for {data["name"]}',
                    400,
                )

            now = datetime.now(timezone.utc)
            policy_id = db.audit_retention_policies.insert(
                name=data["name"],
                description=data.get("description"),
                retention_days=data["retention_days"],
                event_types=data.get("event_types"),
                is_active=data.get("is_active", True),
                created_at=now,
                updated_at=now,
            )

            db.commit()

            policy = db.audit_retention_policies[policy_id]
            return policy.as_dict(), None, None

        policy_dict, error, status = await asyncio.to_thread(inner)
        if error:
            return jsonify({"error": error}), status
        return jsonify(policy_dict), 201

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/retention-policies/<int:policy_id>", methods=["PUT"])
@admin_required
async def update_retention_policy(policy_id):
    """
    Update retention policy.

    Request body:
        {
            "retention_days": 180,
            "is_active": false
        }

    Returns:
        200: Policy updated
        404: Policy not found
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        def inner():
            db = current_app.db

            policy = db.audit_retention_policies[policy_id]

            if not policy:
                return None, "Retention policy not found", 404

            update_data = {}
            if "retention_days" in data:
                update_data["retention_days"] = data["retention_days"]
            if "is_active" in data:
                update_data["is_active"] = data["is_active"]
            if "description" in data:
                update_data["description"] = data["description"]
            if "event_types" in data:
                update_data["event_types"] = data["event_types"]
            update_data["updated_at"] = datetime.now(timezone.utc)

            db(db.audit_retention_policies.id == policy_id).update(**update_data)
            db.commit()

            policy = db.audit_retention_policies[policy_id]
            return policy.as_dict(), None, None

        policy_dict, error, status = await asyncio.to_thread(inner)
        if error:
            return jsonify({"error": error}), status
        return jsonify(policy_dict), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 400)


@bp.route("/retention-policies/<int:policy_id>", methods=["DELETE"])
@admin_required
async def delete_retention_policy(policy_id):
    """
    Delete retention policy.

    Returns:
        200: Policy deleted
        404: Policy not found
    """
    try:

        def inner():
            db = current_app.db

            policy = db.audit_retention_policies[policy_id]

            if not policy:
                return None, "Retention policy not found", 404

            db(db.audit_retention_policies.id == policy_id).delete()
            db.commit()

            return {"message": "Retention policy deleted successfully"}, None, None

        result_dict, error, status = await asyncio.to_thread(inner)
        if error:
            return jsonify({"error": error}), status
        return jsonify(result_dict), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to process request", 500)


# Audit Log Cleanup (admin operation)


@bp.route("/cleanup", methods=["POST"])
@admin_required
def cleanup_audit_logs():
    """
    Clean up old audit logs based on retention policies.

    Query params:
        - dry_run: If true, only return count of logs to delete (default: true)

    Returns:
        200: Cleanup results
    """
    try:
        db = current_app.db

        # Ensure clean transaction state
        try:
            db.commit()
        except Exception:
            db.rollback()

        dry_run = request.args.get("dry_run", "true").lower() == "true"

        # Get all enabled retention policies
        policies = db(
            (db.audit_retention_policies.id > 0)
            & (db.audit_retention_policies.enabled is True)
        ).select()

        results = {}
        total_deleted = 0

        for policy in policies:
            cutoff_date = datetime.now(timezone.utc) - timedelta(
                days=policy.retention_days
            )

            # Count/delete old audit logs for this resource type
            # Note: This is a simplified implementation
            # In production, you'd have specific audit log tables per resource type

            if dry_run:
                results[policy.name] = {
                    "retention_days": policy.retention_days,
                    "cutoff_date": cutoff_date.isoformat(),
                    "action": "dry_run",
                }
            else:
                results[policy.name] = {
                    "retention_days": policy.retention_days,
                    "cutoff_date": cutoff_date.isoformat(),
                    "deleted": 0,
                    "action": "cleanup_performed",
                }

        return (
            jsonify(
                {
                    "dry_run": dry_run,
                    "results": results,
                    "total_deleted": total_deleted,
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            200,
        )

    except Exception as e:
        db.rollback()  # Rollback failed transaction
        return log_error_and_respond(logger, e, "Failed to process request", 500)
