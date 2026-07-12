"""Dashboard analytics service for helpdesk metrics.

Provides aggregated statistics for tickets:
- Counts by status and priority
- Total/open/resolved/today counts
- Average resolution time
- SLA compliance percentage
"""

from datetime import datetime, timezone
from typing import Any

import structlog

from apps.api.utils.async_utils import run_in_threadpool

log = structlog.get_logger()

# Valid statuses and priorities per schema
VALID_STATUSES = ["new", "open", "pending", "on_hold", "resolved", "closed"]
VALID_PRIORITIES = ["low", "medium", "high", "urgent", "critical"]
OPEN_STATUSES = ["new", "open", "pending", "on_hold"]


async def get_dashboard_stats(db: Any, tenant_id: int) -> dict:
    """Get aggregated ticket dashboard statistics.

    Returns counts, averages, and percentages for helpdesk dashboard.

    Parity: Port of Ruffled's dashboard.py /stats endpoint (lines 20-171).

    Args:
        db: penguin-dal database instance.
        tenant_id: Tenant ID for scoped query.

    Returns:
        dict with keys:
            - total_tickets: int
            - open_tickets: int (statuses: new, open, pending, on_hold)
            - resolved_tickets: int (status == resolved)
            - new_today: int (created today)
            - by_status: dict[status -> count]
            - by_priority: dict[priority -> count]
            - avg_resolution_hours: float or None (rounded to 2 decimals)
            - sla_compliance_percent: float (rounded to 2 decimals)
            - timestamp: ISO string (UTC now)

    Raises:
        Exception: On database errors.
    """

    def do_get_stats():
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Total tickets
        total_result = db(db.hd_tickets.tenant_id == tenant_id).count()
        total_tickets = total_result

        # Tickets by status
        status_counts = {}
        for status in VALID_STATUSES:
            count = db(
                (db.hd_tickets.tenant_id == tenant_id)
                & (db.hd_tickets.status == status)
            ).count()
            status_counts[status] = count

        # Tickets by priority
        priority_counts = {}
        for priority in VALID_PRIORITIES:
            count = db(
                (db.hd_tickets.tenant_id == tenant_id)
                & (db.hd_tickets.priority == priority)
            ).count()
            priority_counts[priority] = count

        # Open tickets (not resolved/closed)
        open_tickets = db(
            (db.hd_tickets.tenant_id == tenant_id)
            & (db.hd_tickets.status.belongs(OPEN_STATUSES))
        ).count()

        # New tickets created today
        new_today = db(
            (db.hd_tickets.tenant_id == tenant_id)
            & (db.hd_tickets.created_at >= today_start)
        ).count()

        # Resolved tickets
        resolved_tickets = db(
            (db.hd_tickets.tenant_id == tenant_id)
            & (db.hd_tickets.status == "resolved")
        ).count()

        # Average resolution time (only tickets with resolved_at)
        resolved_with_times = db(
            (db.hd_tickets.tenant_id == tenant_id)
            & (db.hd_tickets.resolved_at != None)  # noqa: E711
            & (db.hd_tickets.status == "resolved")
        ).select()

        avg_resolution_hours = None
        if resolved_with_times:
            total_seconds = 0
            for ticket in resolved_with_times:
                delta = ticket.resolved_at - ticket.created_at
                total_seconds += delta.total_seconds()
            avg_resolution_seconds = total_seconds / len(resolved_with_times)
            avg_resolution_hours = round(avg_resolution_seconds / 3600, 2)

        # SLA compliance: resolved before sla_breach_at / total with SLA policy
        sla_total_tickets = db(
            (db.hd_tickets.tenant_id == tenant_id)
            & (db.hd_tickets.sla_breach_at != None)  # noqa: E711
        ).count()

        sla_compliant = 0
        if sla_total_tickets > 0:
            # penguin-dal cannot compare two columns in a WHERE clause
            # (resolved_at <= sla_breach_at binds the RHS field as a value),
            # so fetch the resolved-with-SLA rows and compare in Python.
            sla_rows = db(
                (db.hd_tickets.tenant_id == tenant_id)
                & (db.hd_tickets.sla_breach_at != None)  # noqa: E711
                & (db.hd_tickets.resolved_at != None)  # noqa: E711
                & (db.hd_tickets.status.belongs(["resolved", "closed"]))
            ).select()
            sla_compliant = sum(1 for t in sla_rows if t.resolved_at <= t.sla_breach_at)

        sla_compliance_percent = (
            round((sla_compliant / sla_total_tickets) * 100, 2)
            if sla_total_tickets > 0
            else 0.0
        )

        result = {
            "total_tickets": total_tickets,
            "open_tickets": open_tickets,
            "resolved_tickets": resolved_tickets,
            "new_today": new_today,
            "by_status": status_counts,
            "by_priority": priority_counts,
            "avg_resolution_hours": avg_resolution_hours,
            "sla_compliance_percent": sla_compliance_percent,
            "timestamp": now.isoformat(),
        }

        log.info(
            "dashboard_stats_calculated",
            tenant_id=tenant_id,
            total=total_tickets,
            open=open_tickets,
            sla_compliance=sla_compliance_percent,
        )

        return result

    return await run_in_threadpool(do_get_stats)
