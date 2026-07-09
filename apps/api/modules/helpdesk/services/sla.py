"""SLA service for calculating and managing SLA policies and breaches.

Handles:
- Calculating SLA breach times based on business hours and policies
- Applying SLA policies to tickets based on priority matching
- Finding tickets with breached or approaching SLA deadlines
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog

from apps.api.utils.async_utils import run_in_threadpool

log = structlog.get_logger()


def calculate_breach_time(
    start: datetime,
    hours: int,
    business_hours_only: bool = False,
) -> datetime:
    """Calculate SLA breach time from start time and hours.

    Implements M-F 9am-5pm UTC business hours logic when business_hours_only=True.
    Skips weekends and hours outside business window.

    Parity: Port of Ruffled's calculate_breach_time (lines 54-98).

    Args:
        start: Ticket creation datetime (UTC).
        hours: Number of hours until breach.
        business_hours_only: If True, calculate only during M-F 9am-5pm UTC.

    Returns:
        datetime: Calculated breach time in UTC.

    Example:
        >>> start = datetime(2025, 1, 24, 15, 0, 0, tzinfo=timezone.utc)  # Fri 3pm
        >>> breach = calculate_breach_time(start, 4, business_hours_only=True)
        >>> # Returns Monday 1pm (4 business hours: Fri 4pm-5pm (1h), Mon 9am-1pm (3h))
    """
    if not business_hours_only:
        return start + timedelta(hours=hours)

    # Business hours: M-F 9am-5pm UTC
    breach_time = start
    hours_added = 0

    while hours_added < hours:
        breach_time += timedelta(hours=1)

        # Skip weekends (Saturday=5, Sunday=6)
        if breach_time.weekday() >= 5:
            # Jump to Monday 9am
            days_until_monday = 7 - breach_time.weekday()
            breach_time = breach_time.replace(hour=9, minute=0, second=0) + timedelta(days=days_until_monday)
            continue

        # Skip outside business hours (before 9am or at/after 5pm)
        if breach_time.hour < 9 or breach_time.hour >= 17:
            if breach_time.hour >= 17:
                # Jump to next day 9am
                breach_time = breach_time.replace(hour=9, minute=0, second=0) + timedelta(days=1)
            else:
                # Jump to 9am today
                breach_time = breach_time.replace(hour=9, minute=0, second=0)
            continue

        hours_added += 1

    return breach_time


async def apply_sla_policy(db: Any, tenant_id: int, ticket_id: int) -> Optional[dict]:
    """Apply SLA policy to a ticket based on priority matching.

    Queries the ticket, finds matching active SLA policy by priority and tenant,
    calculates first_response and resolution breach times, updates ticket.

    Args:
        db: penguin-dal database instance.
        tenant_id: Tenant ID for scoped policy lookup.
        ticket_id: Ticket ID to apply SLA to.

    Returns:
        dict with sla_policy_id and sla_breach_at, or None if no policy found.

    Raises:
        Exception: On database errors.
    """

    def do_apply():
        # Get ticket
        ticket = db.hd_tickets[ticket_id]
        if not ticket:
            log.warning("ticket_not_found", ticket_id=ticket_id, tenant_id=tenant_id)
            return None

        # Query active SLA policy by tenant + priority
        policies = db(
            (db.hd_sla_policies.tenant_id == tenant_id)
            & (db.hd_sla_policies.priority == ticket.priority)
            & (db.hd_sla_policies.is_active == True)  # noqa: E712
        ).select(limitby=(0, 1))

        if not policies:
            log.info("no_sla_policy_found", priority=ticket.priority, tenant_id=tenant_id)
            return None

        policy = policies[0]

        # Calculate breach times
        first_response_breach = calculate_breach_time(
            ticket.created_at,
            policy.first_response_hours,
            policy.business_hours_only,
        )
        resolution_breach = calculate_breach_time(
            ticket.created_at,
            policy.resolution_hours,
            policy.business_hours_only,
        )

        # Use the later breach time (resolution is typically later)
        sla_breach_at = max(first_response_breach, resolution_breach)

        # Update ticket
        db(db.hd_tickets.id == ticket_id).update(
            hd_sla_policy_id=policy.id,
            sla_breach_at=sla_breach_at,
        )

        log.info(
            "sla_applied",
            ticket_id=ticket_id,
            policy_id=policy.id,
            sla_breach_at=sla_breach_at,
        )

        return {"sla_policy_id": policy.id, "sla_breach_at": sla_breach_at}

    return await run_in_threadpool(do_apply)


async def check_sla_breaches(db: Any, tenant_id: int) -> list:
    """Find tickets with breached SLA deadlines.

    Queries for tickets where:
    - sla_breach_at is not null and < now
    - status is not resolved/closed

    Returns list of breached ticket records.

    Args:
        db: penguin-dal database instance.
        tenant_id: Tenant ID for scoped query.

    Returns:
        list: Breached ticket rows with id, subject, sla_breach_at, status, priority.
    """

    def do_check():
        now = datetime.now(timezone.utc)

        # Query tickets where sla_breach_at < now and status not resolved/closed
        breached = db(
            (db.hd_tickets.tenant_id == tenant_id)
            & (db.hd_tickets.sla_breach_at != None)  # noqa: E711
            & (db.hd_tickets.sla_breach_at < now)
            & ~(db.hd_tickets.status.belongs(["resolved", "closed"]))
        ).select(orderby=~db.hd_tickets.sla_breach_at)

        log.info("sla_breaches_checked", tenant_id=tenant_id, count=len(breached))
        return breached

    return await run_in_threadpool(do_check)
