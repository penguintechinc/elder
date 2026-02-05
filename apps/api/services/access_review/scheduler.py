"""Access Review Scheduler.

Creates periodic access reviews based on group review intervals.
Checks for overdue reviews.
"""

import datetime
import logging
import threading
import time
from typing import Optional

from apps.api.services.access_review.service import AccessReviewService

logger = logging.getLogger(__name__)


class AccessReviewScheduler:
    """Background scheduler for access review automation."""

    def __init__(self, db):
        """Initialize scheduler.

        Args:
            db: PyDAL database instance
        """
        self.db = db
        self.service = AccessReviewService(db)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._check_interval = 3600  # 1 hour in seconds
        self._overdue_check_interval = 21600  # 6 hours in seconds
        self._last_overdue_check = 0

    def start(self):
        """Start the scheduler thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Scheduler already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Access review scheduler started")

    def stop(self):
        """Stop the scheduler thread."""
        if not self._thread or not self._thread.is_alive():
            return

        self._stop_event.set()
        self._thread.join(timeout=5)
        logger.info("Access review scheduler stopped")

    def _run(self):
        """Main scheduler loop."""
        while not self._stop_event.is_set():
            try:
                # Check for pending reviews every hour
                self._create_pending_reviews()

                # Check for overdue reviews every 6 hours
                now = time.time()
                if now - self._last_overdue_check >= self._overdue_check_interval:
                    self._check_overdue_reviews()
                    self._last_overdue_check = now

            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}", exc_info=True)

            # Wait for next check (or until stopped)
            self._stop_event.wait(self._check_interval)

    def _create_pending_reviews(self):
        """Create reviews for groups with next_review_date <= now."""
        try:
            now = datetime.datetime.now(datetime.timezone.utc)

            # Find groups that need reviews
            groups = self.db(
                (self.db.identity_groups.review_enabled == True)  # noqa: E712
                & (self.db.identity_groups.next_review_date <= now)
                & (self.db.identity_groups.is_active == True)  # noqa: E712
            ).select()

            for group in groups:
                try:
                    # Calculate review period
                    period_start = group.last_review_date or group.created_at
                    period_end = now
                    due_days = group.review_due_days or 14
                    due_date = now + datetime.timedelta(days=due_days)

                    # Create review
                    review = self.service.create_review(
                        group_id=group.id,
                        period_start=period_start,
                        period_end=period_end,
                        due_date=due_date,
                        tenant_id=group.tenant_id or 1,
                        auto_apply=group.review_auto_apply,
                    )

                    logger.info(
                        f"Created scheduled review {review['id']} for "
                        f"group {group.id} ({group.name})"
                    )

                except Exception as e:
                    logger.error(
                        f"Failed to create review for group {group.id}: {e}",
                        exc_info=True,
                    )

        except Exception as e:
            logger.error(f"Error creating pending reviews: {e}", exc_info=True)

    def _check_overdue_reviews(self):
        """Check for overdue reviews and update status."""
        try:
            overdue_ids = self.service.check_overdue_reviews()
            if overdue_ids:
                logger.info(f"Marked {len(overdue_ids)} reviews as overdue")
        except Exception as e:
            logger.error(f"Error checking overdue reviews: {e}", exc_info=True)


# Global scheduler instance
_scheduler: Optional[AccessReviewScheduler] = None


def init_scheduler(db):
    """Initialize and start the global scheduler.

    Args:
        db: PyDAL database instance
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("Scheduler already initialized")
        return

    _scheduler = AccessReviewScheduler(db)
    _scheduler.start()


def get_scheduler() -> Optional[AccessReviewScheduler]:
    """Get the global scheduler instance.

    Returns:
        Scheduler instance or None if not initialized
    """
    return _scheduler


def stop_scheduler():
    """Stop the global scheduler."""
    global _scheduler

    if _scheduler:
        _scheduler.stop()
        _scheduler = None
