"""Enhanced audit logging API endpoints for v2.2.0 Enterprise Edition.

Provides compliance reporting and advanced audit log querying
for SOC 2, ISO 27001, HIPAA, and GDPR requirements.
"""

# flake8: noqa: E501


import datetime

from quart import Blueprint, jsonify, request

from apps.api.api.v1.portal_auth import portal_token_required
from apps.api.services.audit import AuditService

bp = Blueprint("audit_enterprise", __name__)


@bp.route("/logs", methods=["GET"])
@portal_token_required
def query_logs():
    """Query audit logs with filters.

    Query params:
        tenant_id: int - Filter by tenant
        resource_type: str - Filter by resource type
        resource_id: int - Filter by resource ID
        action: str - Filter by action
        category: str - Filter by category
        portal_user_id: int - Filter by portal user
        start_date: str - ISO date filter start
        end_date: str - ISO date filter end
        success: bool - Filter by success
        limit: int - Max results (default 100)
        offset: int - Result offset

    Returns:
        Paginated audit logs
    """
    # Get tenant context
    tenant_id = request.args.get("tenant_id", type=int)

    # Non-global admins can only view their tenant's logs
    if request.portal_user.get("global_role") not in ["admin", "support"]:
        tenant_id = request.portal_user.get("tenant_id")

    # Parse date filters
    start_date = None
    end_date = None

    if request.args.get("start_date"):
        try:
            start_date = datetime.datetime.fromisoformat(
                request.args.get("start_date").replace("Z", "+00:00")
            )
        except ValueError:
            return jsonify({"error": "Invalid start_date format"}), 400

    if request.args.get("end_date"):
        try:
            end_date = datetime.datetime.fromisoformat(
                request.args.get("end_date").replace("Z", "+00:00")
            )
        except ValueError:
            return jsonify({"error": "Invalid end_date format"}), 400

    # Parse success filter
    success = None
    if request.args.get("success") is not None:
        success = request.args.get("success").lower() == "true"

    result = AuditService.query_logs(
        tenant_id=tenant_id,
        resource_type=request.args.get("resource_type"),
        resource_id=request.args.get("resource_id", type=int),
        action=request.args.get("action"),
        category=request.args.get("category"),
        portal_user_id=request.args.get("portal_user_id", type=int),
        start_date=start_date,
        end_date=end_date,
        success=success,
        limit=request.args.get("limit", 100, type=int),
        offset=request.args.get("offset", 0, type=int),
    )

    return jsonify(result), 200


@bp.route("/reports/<report_type>", methods=["GET"])
@portal_token_required
def get_compliance_report(report_type):
    """Generate a compliance report.

    Args:
        report_type: Report type
            - user_access: Login/logout events
            - permission_changes: Role and permission modifications
            - data_access: Data read events
            - failed_auth: Failed authentication attempts
            - admin_actions: Admin operations

    Query params:
        tenant_id: int - Tenant ID (admin only)
        start_date: str - Report start date (ISO format)
        end_date: str - Report end date (ISO format)

    Returns:
        Compliance report with summary and events
    """
    # Check permissions - only admins can generate reports
    if (
        request.portal_user.get("global_role") not in ["admin", "support"]
        and request.portal_user.get("tenant_role") != "admin"
    ):
        return jsonify({"error": "Admin permission required"}), 403

    # Get tenant ID
    tenant_id = request.args.get("tenant_id", type=int)
    if not tenant_id:
        tenant_id = request.portal_user.get("tenant_id")

    # Non-global admins can only report on their tenant
    if request.portal_user.get("global_role") not in ["admin", "support"]:
        tenant_id = request.portal_user.get("tenant_id")

    # Parse dates
    try:
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        if not start_date_str or not end_date_str:
            # Default to last 30 days
            end_date = datetime.datetime.now(datetime.timezone.utc)
            start_date = end_date - datetime.timedelta(days=30)
        else:
            start_date = datetime.datetime.fromisoformat(
                start_date_str.replace("Z", "+00:00")
            )
            end_date = datetime.datetime.fromisoformat(
                end_date_str.replace("Z", "+00:00")
            )
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400

    result = AuditService.get_compliance_report(
        tenant_id=tenant_id,
        report_type=report_type,
        start_date=start_date,
        end_date=end_date,
    )

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result), 200


@bp.route("/retention", methods=["GET"])
@portal_token_required
def get_retention_policy():
    """Get audit log retention policy for a tenant.

    Query params:
        tenant_id: int - Tenant ID (admin only)

    Returns:
        Retention policy settings
    """
    tenant_id = request.args.get("tenant_id", type=int)
    if not tenant_id:
        tenant_id = request.portal_user.get("tenant_id")

    # Non-global admins can only view their tenant
    if request.portal_user.get("global_role") not in ["admin", "support"]:
        tenant_id = request.portal_user.get("tenant_id")

    result = AuditService.get_retention_policy(tenant_id)

    if "error" in result:
        return jsonify(result), 404

    return jsonify(result), 200


@bp.route("/cleanup", methods=["POST"])
@portal_token_required
async def cleanup_old_logs():
    """Clean up audit logs older than retention period.

    Requires admin permission.

    Request body:
        tenant_id: int - Tenant ID (admin only)

    Returns:
        Cleanup result with count of deleted logs
    """
    # Check permissions - only admins can cleanup
    if (
        request.portal_user.get("global_role") != "admin"
        and request.portal_user.get("tenant_role") != "admin"
    ):
        return jsonify({"error": "Admin permission required"}), 403

    data = await request.get_json() or {}
    tenant_id = data.get("tenant_id")

    if not tenant_id:
        tenant_id = request.portal_user.get("tenant_id")

    # Non-global admins can only cleanup their tenant
    if request.portal_user.get("global_role") != "admin":
        tenant_id = request.portal_user.get("tenant_id")

    result = AuditService.cleanup_old_logs(tenant_id)

    if "error" in result:
        return jsonify(result), 404

    return jsonify(result), 200


@bp.route("/export", methods=["GET"])
@portal_token_required
def export_logs():
    """Export audit logs for compliance archival.

    Query params:
        tenant_id: int - Tenant ID
        start_date: str - Export start date
        end_date: str - Export end date
        format: str - Export format (json or csv)

    Returns:
        Exported audit logs
    """
    # Check permissions
    if (
        request.portal_user.get("global_role") not in ["admin", "support"]
        and request.portal_user.get("tenant_role") != "admin"
    ):
        return jsonify({"error": "Admin permission required"}), 403

    tenant_id = request.args.get("tenant_id", type=int)
    if not tenant_id:
        tenant_id = request.portal_user.get("tenant_id")

    # Parse dates
    try:
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")

        if start_date_str:
            start_date = datetime.datetime.fromisoformat(
                start_date_str.replace("Z", "+00:00")
            )
        else:
            start_date = None

        if end_date_str:
            end_date = datetime.datetime.fromisoformat(
                end_date_str.replace("Z", "+00:00")
            )
        else:
            end_date = None
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400

    # Query all logs for export (no pagination)
    result = AuditService.query_logs(
        tenant_id=tenant_id,
        start_date=start_date,
        end_date=end_date,
        limit=10000,  # Max export size
        offset=0,
    )

    export_format = request.args.get("format", "json")

    if export_format == "csv":
        # Convert to CSV
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(
            [
                "id",
                "action",
                "resource_type",
                "resource_id",
                "success",
                "ip_address",
                "created_at",
            ]
        )

        # Rows
        for log in result["logs"]:
            writer.writerow(
                [
                    log["id"],
                    log["action"],
                    log["resource_type"],
                    log["resource_id"],
                    log["success"],
                    log["ip_address"],
                    log["created_at"],
                ]
            )

        return (
            output.getvalue(),
            200,
            {
                "Content-Type": "text/csv",
                "Content-Disposition": f"attachment; filename=audit_logs_{tenant_id}.csv",
            },
        )

    return jsonify(result), 200
