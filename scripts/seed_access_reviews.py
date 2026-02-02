#!/usr/bin/env python3
"""Seed mock access reviews for development testing.

Creates 3-4 sample reviews with different statuses:
- Scheduled (not started)
- In-progress (some decisions made)
- Completed
- Overdue

Usage:
    python scripts/seed_access_reviews.py
"""

import datetime
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from apps.api.main import create_app
from apps.api.services.access_review.service import AccessReviewService


def seed_access_reviews():
    """Create sample access reviews for testing."""
    print("Seeding access reviews...")

    # Create Flask app and get database
    app = create_app()

    with app.app_context():
        db = app.db
        service = AccessReviewService(db)

        now = datetime.datetime.now(datetime.timezone.utc)

        # Get existing groups (need groups with members)
        groups = db(
            (db.identity_groups.is_active == True)  # noqa: E712
        ).select(limitby=(0, 4))

        if len(groups) == 0:
            print("No groups found. Please create groups first.")
            return

        print(f"Found {len(groups)} groups")

        # Review 1: Scheduled (future due date, not started)
        if len(groups) > 0:
            group1 = groups[0]
            review1 = service.create_review(
                group_id=group1.id,
                period_start=now - datetime.timedelta(days=90),
                period_end=now,
                due_date=now + datetime.timedelta(days=14),
                tenant_id=1,
                auto_apply=True,
            )
            print(f"Created scheduled review {review1['id']} for group '{group1.name}'")

        # Review 2: In-progress (some decisions made)
        if len(groups) > 1:
            group2 = groups[1]
            review2 = service.create_review(
                group_id=group2.id,
                period_start=now - datetime.timedelta(days=180),
                period_end=now,
                due_date=now + datetime.timedelta(days=7),
                tenant_id=1,
                auto_apply=True,
            )

            # Make decisions on half the items
            items = service.get_review_items(review2["id"], include_identity_info=False)
            if items:
                # Get first reviewer (owner)
                if review2.get("reviewers"):
                    reviewer_id = review2["reviewers"][0]["identity_id"]
                else:
                    # Fallback to admin user
                    admin = db(db.identities.username == "admin").select().first()
                    reviewer_id = admin.id if admin else 1

                for i, item in enumerate(items[: len(items) // 2]):
                    decision = "keep" if i % 2 == 0 else "remove"
                    service.submit_review_decision(
                        review_id=review2["id"],
                        membership_id=item["membership_id"],
                        decision=decision,
                        reviewed_by=reviewer_id,
                        justification=f"Mock {decision} decision for testing",
                    )

            print(
                f"Created in-progress review {review2['id']} for group '{group2.name}' "
                f"with {len(items)//2} decisions"
            )

        # Review 3: Completed (all decisions made and applied)
        if len(groups) > 2:
            group3 = groups[2]
            review3 = service.create_review(
                group_id=group3.id,
                period_start=now - datetime.timedelta(days=270),
                period_end=now - datetime.timedelta(days=180),
                due_date=now - datetime.timedelta(days=160),
                tenant_id=1,
                auto_apply=False,  # Manual apply to prevent actual member removal
            )

            # Make decisions on all items
            items = service.get_review_items(review3["id"], include_identity_info=False)
            if items:
                if review3.get("reviewers"):
                    reviewer_id = review3["reviewers"][0]["identity_id"]
                else:
                    admin = db(db.identities.username == "admin").select().first()
                    reviewer_id = admin.id if admin else 1

                for item in items:
                    service.submit_review_decision(
                        review_id=review3["id"],
                        membership_id=item["membership_id"],
                        decision="keep",  # Keep all to avoid disrupting test data
                        reviewed_by=reviewer_id,
                        justification="Mock keep decision - completed review",
                    )

                # Complete the review (but don't apply decisions)
                try:
                    service.complete_review(review3["id"], completed_by=reviewer_id)
                    print(
                        f"Created completed review {review3['id']} for group "
                        f"'{group3.name}'"
                    )
                except Exception as e:
                    print(f"Warning: Could not complete review: {e}")

        # Review 4: Overdue (past due date, not completed)
        if len(groups) > 3:
            group4 = groups[3]
            review4 = service.create_review(
                group_id=group4.id,
                period_start=now - datetime.timedelta(days=365),
                period_end=now - datetime.timedelta(days=275),
                due_date=now - datetime.timedelta(days=260),  # Past due
                tenant_id=1,
                auto_apply=True,
            )

            # Mark as overdue
            db(db.access_reviews.id == review4["id"]).update(
                status=AccessReviewService.STATUS_OVERDUE
            )
            db.commit()

            print(f"Created overdue review {review4['id']} for group '{group4.name}'")

        print("\nAccess review seeding complete!")
        print(
            f"Created {min(4, len(groups))} reviews with different statuses "
            "(scheduled, in-progress, completed, overdue)"
        )


if __name__ == "__main__":
    try:
        seed_access_reviews()
    except Exception as e:
        print(f"Error seeding access reviews: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
