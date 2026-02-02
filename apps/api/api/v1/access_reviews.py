"""Access Review API endpoints.

Enterprise feature for periodic access reviews of group memberships.
"""

import datetime
import logging

from flask import Blueprint, current_app, g, jsonify, request

from apps.api.auth.decorators import login_required
from apps.api.logging_config import log_error_and_respond
from apps.api.services.access_review import AccessReviewService
from shared.licensing import license_required

logger = logging.getLogger(__name__)

bp = Blueprint("access_reviews", __name__)


def get_service():
    """Get AccessReviewService instance."""
    return AccessReviewService(current_app.db)


# ===========================
# Access Review Endpoints
# ===========================


@bp.route("/access-reviews", methods=["GET"])
@login_required
@license_required("enterprise")
def list_reviews():
    """
    List access reviews with filters.

    Query params:
        - status: Filter by status (scheduled/in_progress/completed/overdue)
        - group_id: Filter by group ID
        - limit: Results per page (default: 50)
        - offset: Pagination offset

    Returns:
        200: List of reviews with pagination
    """
    try:
        service = get_service()

        status = request.args.get("status")
        group_id = request.args.get("group_id", type=int)
        limit = request.args.get("limit", 50, type=int)
        offset = request.args.get("offset", 0, type=int)

        result = service.list_reviews(
            tenant_id=1,
            status=status,
            group_id=group_id,
            limit=limit,
            offset=offset,
        )

        return jsonify(result), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to list reviews", 500)


@bp.route("/access-reviews/<int:review_id>", methods=["GET"])
@login_required
@license_required("enterprise")
def get_review(review_id):
    """
    Get review details with progress statistics.

    Returns:
        200: Review details
        404: Review not found
    """
    try:
        service = get_service()

        review = service.get_review(review_id)
        if not review:
            return jsonify({"error": "Review not found"}), 404

        return jsonify(review), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to get review", 500)


@bp.route("/access-reviews/<int:review_id>/items", methods=["GET"])
@login_required
@license_required("enterprise")
def get_review_items(review_id):
    """
    Get all members to review in a review.

    Returns:
        200: List of review items with identity info
        404: Review not found
    """
    try:
        service = get_service()

        # Verify review exists
        review = service.get_review(review_id)
        if not review:
            return jsonify({"error": "Review not found"}), 404

        items = service.get_review_items(review_id, include_identity_info=True)

        return jsonify({"items": items}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to get review items", 500)


@bp.route("/access-reviews/<int:review_id>/decisions", methods=["POST"])
@login_required
@license_required("enterprise")
def submit_decisions(review_id):
    """
    Submit review decision for member(s).

    Request body:
        {
            "membership_id": 123,
            "decision": "keep" | "remove" | "extend",
            "justification": "Optional reason",
            "new_expiration": "2024-12-31T00:00:00Z"  # Required for extend
        }

    Returns:
        200: Decision recorded
        400: Invalid request
        404: Review not found
    """
    try:
        service = get_service()
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        membership_id = data.get("membership_id")
        decision = data.get("decision")
        justification = data.get("justification")
        new_expiration_str = data.get("new_expiration")

        if not membership_id or not decision:
            return jsonify({"error": "membership_id and decision required"}), 400

        # Parse expiration if provided
        new_expiration = None
        if new_expiration_str:
            try:
                new_expiration = datetime.datetime.fromisoformat(
                    new_expiration_str.replace("Z", "+00:00")
                )
            except ValueError:
                return jsonify({"error": "Invalid new_expiration format"}), 400

        # Submit decision
        item = service.submit_review_decision(
            review_id=review_id,
            membership_id=membership_id,
            decision=decision,
            reviewed_by=g.user.id,
            justification=justification,
            new_expiration=new_expiration,
        )

        return jsonify(item), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to submit decision", 500)


@bp.route("/access-reviews/<int:review_id>/complete", methods=["POST"])
@login_required
@license_required("enterprise")
def complete_review(review_id):
    """
    Complete and apply review decisions.

    All members must have decisions before completing.
    If auto_apply is enabled, decisions are applied immediately.

    Returns:
        200: Review completed
        400: Review cannot be completed (unreviewed members)
        404: Review not found
    """
    try:
        service = get_service()

        review = service.complete_review(review_id=review_id, completed_by=g.user.id)

        return jsonify(review), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to complete review", 500)


@bp.route("/access-reviews/my-reviews", methods=["GET"])
@login_required
@license_required("enterprise")
def get_my_reviews():
    """
    Get reviews assigned to current user.

    Query params:
        - status: Filter by status

    Returns:
        200: List of assigned reviews
    """
    try:
        service = get_service()

        status = request.args.get("status")

        reviews = service.get_reviews_for_owner(
            owner_identity_id=g.user.id, status=status
        )

        return jsonify({"reviews": reviews}), 200

    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to get my reviews", 500)


@bp.route("/access-reviews", methods=["POST"])
@login_required
@license_required("enterprise")
def create_review():
    """
    Create an ad-hoc access review (admin only).

    Request body:
        {
            "group_id": 123,
            "period_start": "2024-01-01T00:00:00Z",
            "period_end": "2024-03-31T23:59:59Z",
            "due_date": "2024-04-14T23:59:59Z",
            "auto_apply": true
        }

    Returns:
        201: Review created
        400: Invalid request
    """
    try:
        service = get_service()
        data = request.get_json()

        if not data:
            return jsonify({"error": "Request body required"}), 400

        group_id = data.get("group_id")
        period_start_str = data.get("period_start")
        period_end_str = data.get("period_end")
        due_date_str = data.get("due_date")
        auto_apply = data.get("auto_apply", True)

        if not all([group_id, period_start_str, period_end_str, due_date_str]):
            return (
                jsonify(
                    {
                        "error": "group_id, period_start, period_end, "
                        "and due_date required"
                    }
                ),
                400,
            )

        # Parse dates
        try:
            period_start = datetime.datetime.fromisoformat(
                period_start_str.replace("Z", "+00:00")
            )
            period_end = datetime.datetime.fromisoformat(
                period_end_str.replace("Z", "+00:00")
            )
            due_date = datetime.datetime.fromisoformat(
                due_date_str.replace("Z", "+00:00")
            )
        except ValueError:
            return jsonify({"error": "Invalid date format"}), 400

        # Create review
        review = service.create_review(
            group_id=group_id,
            period_start=period_start,
            period_end=period_end,
            due_date=due_date,
            tenant_id=1,
            auto_apply=auto_apply,
        )

        return jsonify(review), 201

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return log_error_and_respond(logger, e, "Failed to create review", 500)
