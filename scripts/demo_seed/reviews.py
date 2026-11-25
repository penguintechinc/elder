"""Access-review demo data (scheduled, in-progress and overdue reviews)."""

from __future__ import annotations

import datetime as dt

from .common import SeedContext, get_or_create


def seed_access_reviews(ctx: SeedContext, teams: dict[str, int]) -> None:
    """Create one access review per demo team (3 total), with review items.

    Written straight through the DAL rather than ``AccessReviewService``: the
    service logs via ``AuditService`` (needs a live app context) and reads
    ``group.tenant_id`` (a column identity_groups lacks). States shown: scheduled
    (future due, no decisions), in-progress (half the items decided) and overdue.
    Idempotent: a team with any existing review is left untouched.
    """
    db = ctx.db
    now = dt.datetime.now(dt.UTC)
    plans = (
        # (team, period days, due offset days, decide half the items, status)
        ("Platform Engineering", 90, 14, False, "scheduled"),
        ("Security & Compliance", 180, 7, True, "in_progress"),
        ("Site Reliability", 365, -20, False, "in_progress"),
    )
    for team, period_days, due_days, decide, status in plans:
        gid = teams[team]
        if db(db.access_reviews.group_id == gid).select().first():
            ctx.record("access reviews", False)
            continue
        members = db(db.identity_group_memberships.group_id == gid).select()
        review_id = get_or_create(
            ctx,
            "access_reviews",
            {"tenant_id": ctx.tenant_id, "group_id": gid},
            {
                "review_period_start": now - dt.timedelta(days=period_days),
                "review_period_end": now,
                "due_date": now + dt.timedelta(days=due_days),
                "status": status,
                "auto_apply_decisions": False,
                "total_members": len(members),
                "members_reviewed": len(members) // 2 if decide else 0,
                "members_kept": len(members) // 2 if decide else 0,
                "created_at": now,
                "updated_at": now,
            },
            feature="access reviews",
        )
        get_or_create(
            ctx,
            "access_review_assignments",
            {"review_id": review_id, "reviewer_identity_id": ctx.admin_id},
            {
                "tenant_id": ctx.tenant_id,
                "completed": False,
                "created_at": now,
                "updated_at": now,
            },
            feature="access review assignments",
        )
        for idx, member in enumerate(members):
            decided = decide and idx < len(members) // 2
            get_or_create(
                ctx,
                "access_review_items",
                {"review_id": review_id, "membership_id": int(member.id)},
                {
                    "tenant_id": ctx.tenant_id,
                    "identity_id": int(member.identity_id),
                    "decision": "keep" if decided else None,
                    "justification": "Demo decision: access still required"
                    if decided
                    else None,
                    "reviewed_by_id": ctx.admin_id if decided else None,
                    "reviewed_at": now if decided else None,
                    "created_at": now,
                },
                feature="access review items",
            )
