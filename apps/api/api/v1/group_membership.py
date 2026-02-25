"""Group Membership Management API endpoints.

Enterprise feature for group ownership, access requests, and provider write-back.
"""

# flake8: noqa: E501


import logging

from flask import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.services.group_membership import GroupMembershipService
from apps.api.licensing_fallback import license_required

logger = logging.getLogger(__name__)

bp = Blueprint("group_membership", __name__)


def get_service():
    """Get GroupMembershipService instance."""
    return GroupMembershipService(current_app.db)


# ===========================
# Group Endpoints
# ===========================


@bp.route("/groups", methods=["GET"])
@login_required
@license_required("enterprise")
def list_groups():
    """
    List all groups with ownership and member info.

    Query params:
        - include_members: Include member counts (default: false)
        - include_pending: Include pending request counts (default: false)
        - limit: Results per page (default: 50)
        - offset: Pagination offset

    Returns:
        200: List of groups
    """
    try:
        service = get_service()

        include_members = request.args.get("include_members", "false").lower() == "true"
        include_pending = request.args.get("include_pending", "false").lower() == "true"
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        result = service.list_groups(
            include_members=include_members,
            include_pending=include_pending,
            limit=limit,
            offset=offset,
        )

        return jsonify(result), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to list groups", 500)


@bp.route("/groups/<int:group_id>", methods=["GET"])
@login_required
@license_required("enterprise")
def get_group(group_id):
    """
    Get group details with ownership and member info.

    Returns:
        200: Group details
        404: Group not found
    """
    try:
        service = get_service()

        group = service.get_group(group_id)
        if not group:
            return jsonify({"error": "Group not found"}), 404

        return jsonify(group), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to get group", 500)


@bp.route("/groups/<int:group_id>", methods=["PATCH"])
@login_required
@license_required("enterprise")
def update_group(group_id):
    """
    Update group ownership and settings.

    Request body (all optional):
        {
            "owner_identity_id": 123,
            "owner_group_id": 456,
            "approval_mode": "any|all|threshold",
            "approval_threshold": 2,
            "provider": "internal|ldap|okta",
            "provider_group_id": "cn=group,dc=example,dc=com",
            "sync_enabled": true
        }

    Returns:
        200: Updated group
        403: Not authorized
        404: Group not found
    """
    try:
        service = get_service()

        # Check if user is owner or admin
        current_user_id = getattr(g.current_user, "identity_id", None)
        if current_user_id and not service.is_group_owner(current_user_id, group_id):
            # Check if admin
            if not getattr(g.current_user, "is_admin", False):
                return jsonify({"error": "Not authorized to update this group"}), 403

        data = request.get_json() or {}

        group = service.update_group(
            group_id=group_id,
            owner_identity_id=data.get("owner_identity_id"),
            owner_group_id=data.get("owner_group_id"),
            approval_mode=data.get("approval_mode"),
            approval_threshold=data.get("approval_threshold"),
            provider=data.get("provider"),
            provider_group_id=data.get("provider_group_id"),
            sync_enabled=data.get("sync_enabled"),
            updated_by=current_user_id,
        )

        if not group:
            return jsonify({"error": "Group not found"}), 404

        return jsonify(group), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to update group", 500)


# ===========================
# Access Request Endpoints
# ===========================


@bp.route("/groups/<int:group_id>/requests", methods=["POST"])
@login_required
@license_required("enterprise")
def create_access_request(group_id):
    """
    Create an access request for a group.

    Request body:
        {
            "reason": "I need access for project X",
            "expires_at": "2024-12-31T23:59:59Z"  // optional
        }

    Returns:
        201: Access request created
        400: Already a member or pending request exists
        404: Group not found
    """
    try:
        service = get_service()

        # Get requester identity ID
        current_user_id = getattr(g.current_user, "identity_id", None)
        if not current_user_id:
            return jsonify({"error": "Identity not found for current user"}), 400

        data = request.get_json() or {}

        # Parse expires_at if provided
        expires_at = None
        if data.get("expires_at"):
            from datetime import datetime

            expires_at = datetime.fromisoformat(
                data["expires_at"].replace("Z", "+00:00")
            )

        result = service.create_access_request(
            group_id=group_id,
            requester_id=current_user_id,
            reason=data.get("reason"),
            expires_at=expires_at,
        )

        return jsonify(result), 201

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to create access request", 500)


@bp.route("/groups/<int:group_id>/requests", methods=["GET"])
@login_required
@license_required("enterprise")
def list_group_requests(group_id):
    """
    List access requests for a group (owners only).

    Query params:
        - status: Filter by status (pending, approved, denied)
        - limit: Results per page (default: 50)
        - offset: Pagination offset

    Returns:
        200: List of requests
        403: Not an owner
    """
    try:
        service = get_service()

        # Check if user is owner
        current_user_id = getattr(g.current_user, "identity_id", None)
        if current_user_id and not service.is_group_owner(current_user_id, group_id):
            if not getattr(g.current_user, "is_admin", False):
                return jsonify({"error": "Not authorized to view requests"}), 403

        status = request.args.get("status")
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        result = service.list_requests(
            group_id=group_id,
            status=status,
            limit=limit,
            offset=offset,
        )

        return jsonify(result), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to list requests", 500)


@bp.route("/requests/pending", methods=["GET"])
@login_required
@license_required("enterprise")
def list_pending_requests():
    """
    List all pending requests for groups owned by current user.

    Query params:
        - limit: Results per page (default: 50)
        - offset: Pagination offset

    Returns:
        200: List of pending requests
    """
    try:
        service = get_service()

        current_user_id = getattr(g.current_user, "identity_id", None)
        if not current_user_id:
            return jsonify({"requests": [], "total": 0}), 200

        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        result = service.get_pending_requests_for_owner(
            owner_identity_id=current_user_id,
            limit=limit,
            offset=offset,
        )

        return jsonify(result), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to list pending requests", 500)


@bp.route("/requests/<int:request_id>/approve", methods=["POST"])
@login_required
@license_required("enterprise")
def approve_request(request_id):
    """
    Approve an access request.

    Request body:
        {
            "comment": "Approved for project X"  // optional
        }

    Returns:
        200: Request approved
        400: Request not pending
        403: Not authorized
        404: Request not found
    """
    try:
        service = get_service()

        current_user_id = getattr(g.current_user, "identity_id", None)
        if not current_user_id:
            return jsonify({"error": "Identity not found for current user"}), 400

        data = request.get_json() or {}

        result = service.approve_request(
            request_id=request_id,
            approver_id=current_user_id,
            comment=data.get("comment"),
        )

        return jsonify(result), 200

    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            return jsonify({"error": error_msg}), 404
        elif "not authorized" in error_msg.lower():
            return jsonify({"error": error_msg}), 403
        return jsonify({"error": error_msg}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to approve request", 500)


@bp.route("/requests/<int:request_id>/deny", methods=["POST"])
@login_required
@license_required("enterprise")
def deny_request(request_id):
    """
    Deny an access request.

    Request body:
        {
            "comment": "Denied: insufficient justification"  // optional
        }

    Returns:
        200: Request denied
        400: Request not pending
        403: Not authorized
        404: Request not found
    """
    try:
        service = get_service()

        current_user_id = getattr(g.current_user, "identity_id", None)
        if not current_user_id:
            return jsonify({"error": "Identity not found for current user"}), 400

        data = request.get_json() or {}

        result = service.deny_request(
            request_id=request_id,
            denier_id=current_user_id,
            comment=data.get("comment"),
        )

        return jsonify(result), 200

    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            return jsonify({"error": error_msg}), 404
        elif "not authorized" in error_msg.lower():
            return jsonify({"error": error_msg}), 403
        return jsonify({"error": error_msg}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to deny request", 500)


@bp.route("/requests/<int:request_id>", methods=["DELETE"])
@login_required
@license_required("enterprise")
def cancel_request(request_id):
    """
    Cancel own access request.

    Returns:
        200: Request cancelled
        400: Request not pending or not yours
        404: Request not found
    """
    try:
        service = get_service()

        current_user_id = getattr(g.current_user, "identity_id", None)
        if not current_user_id:
            return jsonify({"error": "Identity not found for current user"}), 400

        result = service.cancel_request(
            request_id=request_id,
            canceller_id=current_user_id,
        )

        return jsonify(result), 200

    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            return jsonify({"error": error_msg}), 404
        return jsonify({"error": error_msg}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to cancel request", 500)


@bp.route("/requests/bulk-approve", methods=["POST"])
@login_required
@license_required("enterprise")
def bulk_approve_requests():
    """
    Bulk approve multiple requests.

    Request body:
        {
            "request_ids": [1, 2, 3],
            "comment": "Bulk approved"  // optional
        }

    Returns:
        200: Results of bulk operation
    """
    try:
        service = get_service()

        current_user_id = getattr(g.current_user, "identity_id", None)
        if not current_user_id:
            return jsonify({"error": "Identity not found for current user"}), 400

        data = request.get_json()
        if not data or "request_ids" not in data:
            return jsonify({"error": "request_ids is required"}), 400

        result = service.bulk_approve_requests(
            request_ids=data["request_ids"],
            approver_id=current_user_id,
            comment=data.get("comment"),
        )

        return jsonify(result), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to bulk approve", 500)


# ===========================
# Member Management Endpoints
# ===========================


@bp.route("/groups/<int:group_id>/members", methods=["GET"])
@login_required
@license_required("enterprise")
def list_group_members(group_id):
    """
    List members of a group.

    Query params:
        - limit: Results per page (default: 100)
        - offset: Pagination offset

    Returns:
        200: List of members
        404: Group not found
    """
    try:
        service = get_service()

        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)

        result = service.get_group_members(
            group_id=group_id,
            limit=limit,
            offset=offset,
        )

        return jsonify(result), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to list members", 500)


@bp.route("/groups/<int:group_id>/members", methods=["POST"])
@login_required
@license_required("enterprise")
def add_group_member(group_id):
    """
    Directly add a member to a group (admin/owner only).

    Request body:
        {
            "identity_id": 123,
            "expires_at": "2024-12-31T23:59:59Z",  // optional
            "provider_member_id": "cn=user,dc=example,dc=com"  // optional
        }

    Returns:
        201: Member added
        400: Already a member
        403: Not authorized
    """
    try:
        service = get_service()

        current_user_id = getattr(g.current_user, "identity_id", None)
        if current_user_id and not service.is_group_owner(current_user_id, group_id):
            if not getattr(g.current_user, "is_admin", False):
                return jsonify({"error": "Not authorized to add members"}), 403

        data = request.get_json()
        if not data or "identity_id" not in data:
            return jsonify({"error": "identity_id is required"}), 400

        # Parse expires_at if provided
        expires_at = None
        if data.get("expires_at"):
            from datetime import datetime

            expires_at = datetime.fromisoformat(
                data["expires_at"].replace("Z", "+00:00")
            )

        result = service.add_member(
            group_id=group_id,
            identity_id=data["identity_id"],
            added_by=current_user_id,
            expires_at=expires_at,
            provider_member_id=data.get("provider_member_id"),
        )

        return jsonify(result), 201

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to add member", 500)


@bp.route("/groups/<int:group_id>/members/<int:identity_id>", methods=["DELETE"])
@login_required
@license_required("enterprise")
def remove_group_member(group_id, identity_id):
    """
    Remove a member from a group.

    Returns:
        200: Member removed
        400: Not a member
        403: Not authorized
    """
    try:
        service = get_service()

        current_user_id = getattr(g.current_user, "identity_id", None)

        # Allow self-removal or owner/admin removal
        is_self = current_user_id == identity_id
        is_owner = current_user_id and service.is_group_owner(current_user_id, group_id)
        is_admin = getattr(g.current_user, "is_admin", False)

        if not (is_self or is_owner or is_admin):
            return jsonify({"error": "Not authorized to remove members"}), 403

        result = service.remove_member(
            group_id=group_id,
            identity_id=identity_id,
            removed_by=current_user_id,
        )

        return jsonify(result), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to remove member", 500)
