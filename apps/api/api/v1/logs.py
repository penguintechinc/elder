"""System Logs API endpoints for admin log viewing."""

# flake8: noqa: E501

import os

import structlog
from quart import Blueprint, g, jsonify, request

from apps.api.auth.decorators import admin_required, login_required
from apps.api.logging_config import DEFAULT_LOG_FILE, FALLBACK_LOG_FILE
from apps.api.utils.api_responses import ApiResponse

logger = structlog.get_logger(__name__)

bp = Blueprint("logs", __name__)


def _get_log_file_path():
    """Get the active log file path."""
    log_file = os.getenv("LOG_FILE", DEFAULT_LOG_FILE)
    if os.path.exists(log_file):
        return log_file
    if os.path.exists(FALLBACK_LOG_FILE):
        return FALLBACK_LOG_FILE
    return None


@bp.route("", methods=["GET"])
@login_required
@admin_required
def get_logs():
    """
    Get last 100 log lines (global admin only).

    Returns:
        200: List of log lines and total count
        404: Log file not found
        500: Unable to read logs
    """
    log_file = _get_log_file_path()
    if not log_file:
        return ApiResponse.error("Log file not found", 404)

    try:
        with open(log_file) as f:
            lines = f.readlines()
            last_100 = lines[-100:] if len(lines) > 100 else lines

        admin_email = getattr(g.current_user, "email", "unknown")
        logger.info("logs_retrieved", admin=admin_email)

        return (
            jsonify(
                {
                    "lines": [line.rstrip() for line in last_100],
                    "total": len(lines),
                    "log_file": log_file,
                }
            ),
            200,
        )
    except Exception as e:
        logger.error("logs_read_error", error=str(e))
        return ApiResponse.internal_error("Unable to read logs")


@bp.route("/search", methods=["GET"])
@login_required
@admin_required
def search_logs():
    """
    Search logs, return last 100 matches (global admin only).

    Query Parameters:
        q: Search string (required)

    Returns:
        200: List of matching log lines
        400: Missing search query
        404: Log file not found
        500: Unable to search logs
    """
    query = request.args.get("q", "").strip()
    if not query:
        return ApiResponse.bad_request("Search query required")

    log_file = _get_log_file_path()
    if not log_file:
        return ApiResponse.error("Log file not found", 404)

    try:
        with open(log_file) as f:
            # Case-insensitive search
            matches = [line.rstrip() for line in f if query.lower() in line.lower()]
            last_100 = matches[-100:] if len(matches) > 100 else matches

        admin_email = getattr(g.current_user, "email", "unknown")
        logger.info(
            "logs_searched",
            admin=admin_email,
            query=query,
            matches=len(matches),
        )

        return (
            jsonify(
                {
                    "lines": last_100,
                    "total_matches": len(matches),
                    "query": query,
                    "log_file": log_file,
                }
            ),
            200,
        )
    except Exception as e:
        logger.error("logs_search_error", error=str(e))
        return ApiResponse.internal_error("Unable to search logs")
