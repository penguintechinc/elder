"""Helpdesk dashboard analytics endpoints using penguin-dal."""

# flake8: noqa: E501

import logging

from quart import Blueprint, current_app, g, jsonify

from apps.api.auth.decorators import login_required
from apps.api.modules.helpdesk.services.dashboard import get_dashboard_stats
from apps.api.utils.api_responses import ApiResponse
from apps.api.utils.async_utils import run_in_threadpool

logger = logging.getLogger(__name__)

bp = Blueprint("helpdesk_dashboard", __name__)


def _get_tenant_id() -> int:
    """Extract tenant_id from g.claims (populated by before_request)."""
    claims = getattr(g, "claims", {}) or {}
    tenant_str = claims.get("tenant", "")
    if not tenant_str:
        return None
    try:
        return int(tenant_str)
    except (ValueError, TypeError):
        return None


@bp.route("/stats", methods=["GET"])
@login_required
async def dashboard_stats():
    """
    Get ticket metrics and dashboard statistics.

    Returns:
        200: Dashboard statistics
            {
                "total_tickets": int,
                "open_tickets": int,
                "resolved_tickets": int,
                "new_today": int,
                "by_status": {status -> count},
                "by_priority": {priority -> count},
                "avg_resolution_hours": float or null,
                "sla_compliance_percent": float,
                "timestamp": "ISO string"
            }
    """
    db = current_app.db
    tenant_id = _get_tenant_id()

    if not tenant_id:
        return ApiResponse.error("Tenant not found", 403)

    # Run stats calculation in threadpool (get_dashboard_stats is async)
    stats = await get_dashboard_stats(db, tenant_id)

    return jsonify(stats)
