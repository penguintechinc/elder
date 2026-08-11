"""Enhanced audit logging service for compliance.

Provides comprehensive audit logging for SOC 2, ISO 27001,
HIPAA, and GDPR compliance requirements.
"""

# flake8: noqa: E501

import datetime
from typing import Optional

from quart import current_app


class AuditService:
    """Enhanced audit logging service."""

    # Event categories
    CATEGORY_AUTH = "authentication"
    CATEGORY_AUTHZ = "authorization"
    CATEGORY_DATA = "data_access"
    CATEGORY_MODIFY = "data_modification"
    CATEGORY_CONFIG = "configuration"
    CATEGORY_ADMIN = "admin_action"
    CATEGORY_API = "api_access"
    CATEGORY_SECURITY = "security"

    # Event actions
    ACTION_LOGIN = "login"
    ACTION_LOGOUT = "logout"
    ACTION_LOGIN_FAILED = "login_failed"
    ACTION_MFA_ENABLED = "mfa_enabled"
    ACTION_MFA_DISABLED = "mfa_disabled"
    ACTION_PASSWORD_CHANGED = "password_changed"
    ACTION_PASSWORD_RESET = "password_reset"
    ACTION_PERMISSION_CHECK = "permission_check"
    ACTION_ACCESS_DENIED = "access_denied"
    ACTION_CREATE = "create"
    ACTION_READ = "read"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_EXPORT = "export"
    ACTION_IMPORT = "import"

    @staticmethod
    def log(
        action: str,
        resource_type: str,
        resource_id: int | None = None,
        identity_id: int | None = None,
        portal_user_id: int | None = None,
        tenant_id: int | None = None,
        details: dict | None = None,
        success: bool = True,
        ip_address: str | None = None,
        user_agent: str | None = None,
        correlation_id: str | None = None,
        category: str | None = None,
        old_values: dict | None = None,
        new_values: dict | None = None,
    ) -> int:
        """Log an audit event.

        Args:
            action: Action performed (create, read, update, delete, etc.)
            resource_type: Type of resource affected
            resource_id: ID of resource affected
            identity_id: Identity performing action (legacy)
            portal_user_id: Portal user performing action (v2.2.0)
            tenant_id: Tenant context
            details: Additional event details
            success: Whether action succeeded
            ip_address: Client IP address
            user_agent: Client user agent
            correlation_id: Request correlation ID
            category: Event category for filtering
            old_values: Previous values (for updates)
            new_values: New values (for updates)

        Returns:
            Audit log entry ID
        """
        db = current_app.db

        # Build details dict
        event_details = details or {}

        if category:
            event_details["category"] = category

        if old_values:
            event_details["old_values"] = old_values

        if new_values:
            event_details["new_values"] = new_values

        if correlation_id:
            event_details["correlation_id"] = correlation_id

        if portal_user_id:
            event_details["portal_user_id"] = portal_user_id

        if tenant_id:
            event_details["tenant_id"] = tenant_id

        # Insert audit log
        log_id = db.audit_logs.insert(
            identity_id=identity_id,
            action_name=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=event_details,
            success=success,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.commit()

        return log_id

    @staticmethod
    def log_auth_event(
        action: str,
        email: str,
        tenant_id: int,
        success: bool = True,
        ip_address: str | None = None,
        user_agent: str | None = None,
        details: dict | None = None,
        portal_user_id: int | None = None,
    ) -> int:
        """Log an authentication event.

        Args:
            action: Auth action (login, logout, login_failed, etc.)
            email: User email
            tenant_id: Tenant ID
            success: Whether auth succeeded
            ip_address: Client IP
            user_agent: Client user agent
            details: Additional details
            portal_user_id: Portal user ID if known

        Returns:
            Audit log ID
        """
        event_details = details or {}
        event_details["email"] = email
        event_details["category"] = AuditService.CATEGORY_AUTH

        return AuditService.log(
            action=action,
            resource_type="portal_user",
            resource_id=portal_user_id,
            portal_user_id=portal_user_id,
            tenant_id=tenant_id,
            details=event_details,
            success=success,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @staticmethod
    def log_data_access(
        resource_type: str,
        resource_id: int,
        portal_user_id: int,
        tenant_id: int,
        fields_accessed: list | None = None,
        ip_address: str | None = None,
    ) -> int:
        """Log a data access event (for sensitive data).

        Args:
            resource_type: Type of resource accessed
            resource_id: Resource ID
            portal_user_id: User accessing data
            tenant_id: Tenant context
            fields_accessed: Specific fields accessed
            ip_address: Client IP

        Returns:
            Audit log ID
        """
        details = {
            "category": AuditService.CATEGORY_DATA,
            "fields_accessed": fields_accessed or [],
        }

        return AuditService.log(
            action=AuditService.ACTION_READ,
            resource_type=resource_type,
            resource_id=resource_id,
            portal_user_id=portal_user_id,
            tenant_id=tenant_id,
            details=details,
            ip_address=ip_address,
        )

    @staticmethod
    def log_modification(
        action: str,
        resource_type: str,
        resource_id: int,
        portal_user_id: int,
        tenant_id: int,
        old_values: dict | None = None,
        new_values: dict | None = None,
        ip_address: str | None = None,
    ) -> int:
        """Log a data modification event.

        Args:
            action: create, update, or delete
            resource_type: Type of resource
            resource_id: Resource ID
            portal_user_id: User making change
            tenant_id: Tenant context
            old_values: Previous values
            new_values: New values
            ip_address: Client IP

        Returns:
            Audit log ID
        """
        return AuditService.log(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            portal_user_id=portal_user_id,
            tenant_id=tenant_id,
            category=AuditService.CATEGORY_MODIFY,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
        )

    @staticmethod
    def query_logs(
        tenant_id: int | None = None,
        resource_type: str | None = None,
        resource_id: int | None = None,
        action: str | None = None,
        category: str | None = None,
        identity_id: int | None = None,
        portal_user_id: int | None = None,
        start_date: datetime.datetime | None = None,
        end_date: datetime.datetime | None = None,
        success: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Query audit logs with filters.

        Args:
            tenant_id: Filter by tenant
            resource_type: Filter by resource type
            resource_id: Filter by resource ID
            action: Filter by action
            category: Filter by category
            identity_id: Filter by identity
            portal_user_id: Filter by portal user
            start_date: Filter by start date
            end_date: Filter by end date
            success: Filter by success status
            limit: Maximum results
            offset: Result offset

        Returns:
            Dict with logs and pagination info
        """
        db = current_app.db

        query = db.audit_logs.id > 0

        if tenant_id:
            query &= db.audit_logs.details.contains({"tenant_id": tenant_id})

        if resource_type:
            query &= db.audit_logs.resource_type == resource_type

        if resource_id:
            query &= db.audit_logs.resource_id == resource_id

        if action:
            query &= db.audit_logs.action_name == action

        if category:
            query &= db.audit_logs.details.contains({"category": category})

        if identity_id:
            query &= db.audit_logs.identity_id == identity_id

        if portal_user_id:
            query &= db.audit_logs.details.contains({"portal_user_id": portal_user_id})

        if start_date:
            query &= db.audit_logs.created_at >= start_date

        if end_date:
            query &= db.audit_logs.created_at <= end_date

        if success is not None:
            query &= db.audit_logs.success == success

        # Get total count
        total = db(query).count()

        # Get logs
        logs = db(query).select(
            orderby=~db.audit_logs.created_at, limitby=(offset, offset + limit)
        )

        return {
            "logs": [AuditService._log_to_dict(log) for log in logs],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    def _log_to_dict(log) -> dict:
        """Convert audit log record to dict."""
        return {
            "id": log.id,
            "identity_id": log.identity_id,
            "action": log.action_name,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "details": log.details,
            "success": log.success,
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }

    @staticmethod
    def get_compliance_report(
        tenant_id: int,
        report_type: str,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
    ) -> dict:
        """Generate a compliance report.

        Args:
            tenant_id: Tenant ID
            report_type: Report type (user_access, permission_changes, etc.)
            start_date: Report start date
            end_date: Report end date

        Returns:
            Compliance report data
        """
        db = current_app.db

        base_query = (db.audit_logs.created_at >= start_date) & (
            db.audit_logs.created_at <= end_date
        )

        if report_type == "user_access":
            # User access report - all login events
            query = base_query & (
                db.audit_logs.action_name.belongs(
                    [
                        AuditService.ACTION_LOGIN,
                        AuditService.ACTION_LOGOUT,
                        AuditService.ACTION_LOGIN_FAILED,
                    ]
                )
            )
            title = "User Access Report"

        elif report_type == "permission_changes":
            # Permission changes - role and permission modifications
            query = (
                base_query
                & (
                    db.audit_logs.resource_type.belongs(
                        ["portal_user", "portal_user_org_assignment", "role"]
                    )
                )
                & (
                    db.audit_logs.action_name.belongs(
                        [
                            AuditService.ACTION_CREATE,
                            AuditService.ACTION_UPDATE,
                            AuditService.ACTION_DELETE,
                        ]
                    )
                )
            )
            title = "Permission Changes Report"

        elif report_type == "data_access":
            # Data access - reads on sensitive resources
            query = base_query & (db.audit_logs.action_name == AuditService.ACTION_READ)
            title = "Data Access Report"

        elif report_type == "failed_auth":
            # Failed authentication attempts
            query = (
                base_query
                & (db.audit_logs.success == False)  # noqa: E712
                & (
                    db.audit_logs.action_name.belongs(
                        [
                            AuditService.ACTION_LOGIN,
                            AuditService.ACTION_LOGIN_FAILED,
                        ]
                    )
                )
            )
            title = "Failed Authentication Report"

        elif report_type == "admin_actions":
            # Admin actions
            query = base_query & db.audit_logs.details.contains(
                {"category": AuditService.CATEGORY_ADMIN}
            )
            title = "Admin Actions Report"

        else:
            return {"error": f"Unknown report type: {report_type}"}

        logs = db(query).select(orderby=~db.audit_logs.created_at)

        # Generate summary statistics
        total_events = len(logs)
        success_count = len([log for log in logs if log.success])
        failure_count = total_events - success_count

        unique_users = len(set(log.identity_id for log in logs if log.identity_id))
        unique_resources = len(
            set((log.resource_type, log.resource_id) for log in logs if log.resource_id)
        )

        return {
            "title": title,
            "report_type": report_type,
            "tenant_id": tenant_id,
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "summary": {
                "total_events": total_events,
                "success_count": success_count,
                "failure_count": failure_count,
                "unique_users": unique_users,
                "unique_resources": unique_resources,
            },
            "events": [AuditService._log_to_dict(log) for log in logs],
            "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    @staticmethod
    def get_retention_policy(tenant_id: int) -> dict:
        """Get audit log retention policy for a tenant.

        Args:
            tenant_id: Tenant ID

        Returns:
            Retention policy settings
        """
        db = current_app.db

        tenant = db.tenants[tenant_id]
        if not tenant:
            return {"error": "Tenant not found"}

        return {
            "tenant_id": tenant_id,
            "data_retention_days": tenant.data_retention_days,
            "subscription_tier": tenant.subscription_tier,
        }

    @staticmethod
    def cleanup_old_logs(tenant_id: int) -> dict:
        """Clean up audit logs older than retention period.

        Args:
            tenant_id: Tenant ID

        Returns:
            Cleanup result
        """
        db = current_app.db

        tenant = db.tenants[tenant_id]
        if not tenant:
            return {"error": "Tenant not found"}

        retention_days = tenant.data_retention_days or 90
        cutoff_date = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            days=retention_days
        )

        # Count logs to delete
        query = db.audit_logs.details.contains({"tenant_id": tenant_id}) & (
            db.audit_logs.created_at < cutoff_date
        )
        count = db(query).count()

        # Delete old logs
        db(query).delete()
        db.commit()

        return {
            "tenant_id": tenant_id,
            "deleted_count": count,
            "retention_days": retention_days,
            "cutoff_date": cutoff_date.isoformat(),
        }
