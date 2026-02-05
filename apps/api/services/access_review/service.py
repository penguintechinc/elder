"""Access Review Service - Enterprise feature for periodic membership reviews.

Provides automated and manual access review workflows:
- Scheduled reviews based on configurable intervals
- Review assignment to group owners
- Member-by-member review decisions (keep/remove/extend)
- Automatic application of decisions with Okta sync
- Compliance reporting and audit trails
"""

import datetime
import logging
import secrets
from typing import Any, Dict, List, Optional

from apps.api.services.audit.service import AuditService

logger = logging.getLogger(__name__)


class AccessReviewService:
    """Service for managing periodic access reviews."""

    # Review statuses
    STATUS_SCHEDULED = "scheduled"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_OVERDUE = "overdue"

    # Review decisions
    DECISION_KEEP = "keep"
    DECISION_REMOVE = "remove"
    DECISION_EXTEND = "extend"

    # Assignment modes
    ASSIGNMENT_MODE_ALL_OWNERS = "all_owners"
    ASSIGNMENT_MODE_PRIMARY_OWNER = "primary_owner"

    def __init__(self, db):
        """Initialize service with database connection."""
        self.db = db

    def _generate_village_id(self) -> str:
        """Generate a unique village ID for reviews."""
        return secrets.token_hex(16)

    def _review_to_dict(self, review) -> Dict[str, Any]:
        """Convert review record to dictionary."""
        return {
            "id": review.id,
            "tenant_id": review.tenant_id,
            "group_id": review.group_id,
            "review_period_start": (
                review.review_period_start.isoformat()
                if review.review_period_start
                else None
            ),
            "review_period_end": (
                review.review_period_end.isoformat()
                if review.review_period_end
                else None
            ),
            "due_date": review.due_date.isoformat() if review.due_date else None,
            "status": review.status,
            "completed_at": (
                review.completed_at.isoformat() if review.completed_at else None
            ),
            "completed_by_id": review.completed_by_id,
            "total_members": review.total_members,
            "members_reviewed": review.members_reviewed,
            "members_kept": review.members_kept,
            "members_removed": review.members_removed,
            "auto_apply_decisions": review.auto_apply_decisions,
            "village_id": review.village_id,
            "created_at": review.created_at.isoformat() if review.created_at else None,
            "updated_at": review.updated_at.isoformat() if review.updated_at else None,
        }

    def _review_item_to_dict(self, item) -> Dict[str, Any]:
        """Convert review item record to dictionary."""
        return {
            "id": item.id,
            "review_id": item.review_id,
            "membership_id": item.membership_id,
            "identity_id": item.identity_id,
            "decision": item.decision,
            "justification": item.justification,
            "new_expiration": (
                item.new_expiration.isoformat() if item.new_expiration else None
            ),
            "reviewed_by_id": item.reviewed_by_id,
            "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }

    # ==================== Review Management ====================

    def create_review(
        self,
        group_id: int,
        period_start: datetime.datetime,
        period_end: datetime.datetime,
        due_date: datetime.datetime,
        tenant_id: int = 1,
        auto_apply: bool = True,
    ) -> Dict[str, Any]:
        """Create a new access review for a group.

        Creates review record and items for all current members.
        Assigns reviewers based on group's assignment mode.

        Args:
            group_id: Group to review
            period_start: Start of review period
            period_end: End of review period
            due_date: Review completion deadline
            tenant_id: Tenant ID
            auto_apply: Whether to auto-apply decisions on completion

        Returns:
            Review record dictionary
        """
        db = self.db

        # Verify group exists and has review enabled
        group = db.identity_groups[group_id]
        if not group:
            raise ValueError(f"Group {group_id} not found")

        # Create review record
        review_id = db.access_reviews.insert(
            tenant_id=tenant_id,
            group_id=group_id,
            review_period_start=period_start,
            review_period_end=period_end,
            due_date=due_date,
            status=self.STATUS_SCHEDULED,
            auto_apply_decisions=auto_apply,
            village_id=self._generate_village_id(),
        )

        # Get all current members
        memberships = db(db.identity_group_memberships.group_id == group_id).select()

        # Create review items for each member
        for membership in memberships:
            db.access_review_items.insert(
                tenant_id=tenant_id,
                review_id=review_id,
                membership_id=membership.id,
                identity_id=membership.identity_id,
            )

        # Update total_members count
        db(db.access_reviews.id == review_id).update(total_members=len(memberships))

        # Assign reviewers based on assignment mode
        self._assign_reviewers(review_id, group)

        # Update review status to in_progress if there are members
        if len(memberships) > 0:
            db(db.access_reviews.id == review_id).update(status=self.STATUS_IN_PROGRESS)

        db.commit()

        # Audit log
        AuditService.log(
            action="create",
            resource_type="access_review",
            resource_id=review_id,
            details={
                "group_id": group_id,
                "total_members": len(memberships),
                "due_date": due_date.isoformat(),
            },
        )

        logger.info(
            f"Created access review {review_id} for group {group_id} "
            f"with {len(memberships)} members"
        )

        return self.get_review(review_id)

    def _assign_reviewers(self, review_id: int, group) -> None:
        """Assign reviewers to a review based on group settings."""
        db = self.db

        assignment_mode = (
            group.review_assignment_mode or self.ASSIGNMENT_MODE_ALL_OWNERS
        )

        reviewers = []

        if assignment_mode == self.ASSIGNMENT_MODE_PRIMARY_OWNER:
            # Only primary owner
            if group.owner_identity_id:
                reviewers.append(group.owner_identity_id)
        else:
            # All owners (default)
            if group.owner_identity_id:
                reviewers.append(group.owner_identity_id)

            # Also include members of owner group if set
            if group.owner_group_id:
                owner_memberships = db(
                    db.identity_group_memberships.group_id == group.owner_group_id
                ).select()
                for membership in owner_memberships:
                    if membership.identity_id not in reviewers:
                        reviewers.append(membership.identity_id)

        # Create assignments
        for reviewer_id in reviewers:
            db.access_review_assignments.insert(
                tenant_id=group.tenant_id or 1,
                review_id=review_id,
                reviewer_identity_id=reviewer_id,
            )

        db.commit()

    def get_review(self, review_id: int) -> Optional[Dict[str, Any]]:
        """Get review details with progress statistics.

        Args:
            review_id: Review ID

        Returns:
            Review record with progress info, or None if not found
        """
        db = self.db

        review = db.access_reviews[review_id]
        if not review:
            return None

        review_data = self._review_to_dict(review)

        # Get group info
        group = db.identity_groups[review.group_id]
        if group:
            review_data["group_name"] = group.name
            review_data["group_description"] = group.description

        # Get assignees
        assignments = db(db.access_review_assignments.review_id == review_id).select()

        review_data["reviewers"] = []
        for assignment in assignments:
            identity = db.identities[assignment.reviewer_identity_id]
            if identity:
                review_data["reviewers"].append(
                    {
                        "identity_id": identity.id,
                        "username": identity.username,
                        "email": identity.email,
                        "completed": assignment.completed,
                        "completed_at": (
                            assignment.completed_at.isoformat()
                            if assignment.completed_at
                            else None
                        ),
                    }
                )

        return review_data

    def list_reviews(
        self,
        tenant_id: int = 1,
        status: Optional[str] = None,
        group_id: Optional[int] = None,
        reviewer_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List access reviews with filters.

        Args:
            tenant_id: Tenant ID
            status: Filter by status
            group_id: Filter by group
            reviewer_id: Filter by assigned reviewer
            limit: Page size
            offset: Page offset

        Returns:
            Paginated list of reviews
        """
        db = self.db

        query = db.access_reviews.tenant_id == tenant_id

        if status:
            query &= db.access_reviews.status == status

        if group_id:
            query &= db.access_reviews.group_id == group_id

        if reviewer_id:
            # Join with assignments
            query &= db.access_review_assignments.review_id == db.access_reviews.id
            query &= db.access_review_assignments.reviewer_identity_id == reviewer_id

        total = db(query).count()

        reviews = db(query).select(
            db.access_reviews.ALL,
            orderby=~db.access_reviews.created_at,
            limitby=(offset, offset + limit),
            distinct=True,
        )

        result = []
        for review in reviews:
            review_data = self._review_to_dict(review)

            # Add group name
            group = db.identity_groups[review.group_id]
            if group:
                review_data["group_name"] = group.name

            result.append(review_data)

        return {"reviews": result, "total": total, "limit": limit, "offset": offset}

    def get_review_items(
        self, review_id: int, include_identity_info: bool = True
    ) -> List[Dict[str, Any]]:
        """Get all review items (members to review).

        Args:
            review_id: Review ID
            include_identity_info: Whether to include identity details

        Returns:
            List of review items with optional identity info
        """
        db = self.db

        items = db(db.access_review_items.review_id == review_id).select(
            orderby=db.access_review_items.id
        )

        result = []
        for item in items:
            item_data = self._review_item_to_dict(item)

            if include_identity_info:
                identity = db.identities[item.identity_id]
                if identity:
                    item_data["username"] = identity.username
                    item_data["email"] = identity.email
                    item_data["full_name"] = identity.full_name

                # Get membership info
                membership = db.identity_group_memberships[item.membership_id]
                if membership:
                    item_data["joined_at"] = (
                        membership.joined_at.isoformat()
                        if membership.joined_at
                        else None
                    )
                    item_data["expires_at"] = (
                        membership.expires_at.isoformat()
                        if membership.expires_at
                        else None
                    )

            result.append(item_data)

        return result

    def submit_review_decision(
        self,
        review_id: int,
        membership_id: int,
        decision: str,
        reviewed_by: int,
        justification: Optional[str] = None,
        new_expiration: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """Submit a review decision for a member.

        Args:
            review_id: Review ID
            membership_id: Membership ID being reviewed
            decision: Decision (keep/remove/extend)
            reviewed_by: Identity ID of reviewer
            justification: Optional justification
            new_expiration: New expiration if extending

        Returns:
            Updated review item
        """
        db = self.db

        # Validate decision
        if decision not in [
            self.DECISION_KEEP,
            self.DECISION_REMOVE,
            self.DECISION_EXTEND,
        ]:
            raise ValueError(f"Invalid decision: {decision}")

        # Validate extend has expiration
        if decision == self.DECISION_EXTEND and not new_expiration:
            raise ValueError("new_expiration required for extend decision")

        # Find the review item
        item = (
            db(
                (db.access_review_items.review_id == review_id)
                & (db.access_review_items.membership_id == membership_id)
            )
            .select()
            .first()
        )

        if not item:
            raise ValueError(
                f"Review item not found for review {review_id}, "
                f"membership {membership_id}"
            )

        # Update the item
        now = datetime.datetime.now(datetime.timezone.utc)
        db(db.access_review_items.id == item.id).update(
            decision=decision,
            justification=justification,
            new_expiration=new_expiration,
            reviewed_by_id=reviewed_by,
            reviewed_at=now,
        )

        # Update review progress
        self._update_review_progress(review_id)

        db.commit()

        # Audit log
        AuditService.log(
            action="update",
            resource_type="access_review_item",
            resource_id=item.id,
            identity_id=reviewed_by,
            details={
                "review_id": review_id,
                "membership_id": membership_id,
                "decision": decision,
                "justification": justification,
            },
        )

        return self._review_item_to_dict(db.access_review_items[item.id])

    def _update_review_progress(self, review_id: int) -> None:
        """Update review progress statistics."""
        db = self.db

        items = db(db.access_review_items.review_id == review_id).select()

        members_reviewed = sum(1 for item in items if item.decision)
        members_kept = sum(1 for item in items if item.decision == self.DECISION_KEEP)
        members_removed = sum(
            1 for item in items if item.decision == self.DECISION_REMOVE
        )

        db(db.access_reviews.id == review_id).update(
            members_reviewed=members_reviewed,
            members_kept=members_kept,
            members_removed=members_removed,
        )

        db.commit()

    def complete_review(self, review_id: int, completed_by: int) -> Dict[str, Any]:
        """Complete a review and optionally apply decisions.

        Args:
            review_id: Review ID
            completed_by: Identity ID completing the review

        Returns:
            Updated review record
        """
        db = self.db

        review = db.access_reviews[review_id]
        if not review:
            raise ValueError(f"Review {review_id} not found")

        if review.status == self.STATUS_COMPLETED:
            raise ValueError(f"Review {review_id} already completed")

        # Verify all members have been reviewed
        items = db(db.access_review_items.review_id == review_id).select()
        unreviewed = [item for item in items if not item.decision]

        if unreviewed:
            raise ValueError(
                f"Cannot complete review: {len(unreviewed)} members not reviewed"
            )

        now = datetime.datetime.now(datetime.timezone.utc)

        # Mark review as completed
        db(db.access_reviews.id == review_id).update(
            status=self.STATUS_COMPLETED,
            completed_at=now,
            completed_by_id=completed_by,
        )

        # Apply decisions if auto_apply is enabled
        if review.auto_apply_decisions:
            self.apply_review_decisions(review_id, completed_by)

        # Schedule next review
        self.schedule_next_review(review.group_id)

        db.commit()

        # Audit log
        AuditService.log(
            action="update",
            resource_type="access_review",
            resource_id=review_id,
            identity_id=completed_by,
            details={
                "status": "completed",
                "group_id": review.group_id,
                "members_kept": review.members_kept,
                "members_removed": review.members_removed,
            },
        )

        logger.info(
            f"Completed access review {review_id}: "
            f"{review.members_kept} kept, {review.members_removed} removed"
        )

        return self.get_review(review_id)

    def apply_review_decisions(self, review_id: int, applied_by: int) -> None:
        """Apply review decisions (remove members, extend expirations).

        Args:
            review_id: Review ID
            applied_by: Identity ID applying decisions
        """
        db = self.db

        # Import here to avoid circular dependency
        from apps.api.services.group_membership.service import GroupMembershipService

        group_service = GroupMembershipService(db)

        review = db.access_reviews[review_id]
        if not review:
            raise ValueError(f"Review {review_id} not found")

        items = db(db.access_review_items.review_id == review_id).select()

        for item in items:
            if item.decision == self.DECISION_REMOVE:
                # Remove member
                try:
                    group_service.remove_member(
                        group_id=review.group_id,
                        identity_id=item.identity_id,
                        removed_by=applied_by,
                    )
                    logger.info(
                        f"Removed identity {item.identity_id} from group "
                        f"{review.group_id} (review decision)"
                    )
                except Exception as e:
                    logger.error(f"Failed to remove identity {item.identity_id}: {e}")

            elif item.decision == self.DECISION_EXTEND and item.new_expiration:
                # Update expiration
                membership = db.identity_group_memberships[item.membership_id]
                if membership:
                    db(db.identity_group_memberships.id == membership.id).update(
                        expires_at=item.new_expiration
                    )
                    logger.info(
                        f"Extended membership {membership.id} to "
                        f"{item.new_expiration.isoformat()}"
                    )

        db.commit()

    def schedule_next_review(self, group_id: int) -> None:
        """Schedule the next review for a group.

        Args:
            group_id: Group ID
        """
        db = self.db

        group = db.identity_groups[group_id]
        if not group or not group.review_enabled:
            return

        now = datetime.datetime.now(datetime.timezone.utc)

        # Calculate next review date
        interval_days = group.review_interval_days or 90
        next_review = now + datetime.timedelta(days=interval_days)

        db(db.identity_groups.id == group_id).update(
            last_review_date=now, next_review_date=next_review
        )

        db.commit()

        logger.info(
            f"Scheduled next review for group {group_id} on "
            f"{next_review.isoformat()}"
        )

    def get_reviews_for_owner(
        self, owner_identity_id: int, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all reviews assigned to an owner.

        Args:
            owner_identity_id: Identity ID of owner
            status: Optional status filter

        Returns:
            List of reviews assigned to owner
        """
        db = self.db

        query = db.access_review_assignments.reviewer_identity_id == owner_identity_id
        query &= db.access_review_assignments.review_id == db.access_reviews.id

        if status:
            query &= db.access_reviews.status == status

        reviews = db(query).select(
            db.access_reviews.ALL,
            orderby=~db.access_reviews.created_at,
            distinct=True,
        )

        result = []
        for review in reviews:
            review_data = self._review_to_dict(review)

            # Add group name
            group = db.identity_groups[review.group_id]
            if group:
                review_data["group_name"] = group.name

            result.append(review_data)

        return result

    def check_overdue_reviews(self) -> List[int]:
        """Check for overdue reviews and update their status.

        Returns:
            List of review IDs that became overdue
        """
        db = self.db

        now = datetime.datetime.now(datetime.timezone.utc)

        # Find reviews that are past due but not completed
        query = (
            db.access_reviews.status.belongs(
                [self.STATUS_SCHEDULED, self.STATUS_IN_PROGRESS]
            )
        ) & (db.access_reviews.due_date < now)

        overdue_reviews = db(query).select()

        overdue_ids = []
        for review in overdue_reviews:
            db(db.access_reviews.id == review.id).update(status=self.STATUS_OVERDUE)
            overdue_ids.append(review.id)

            logger.warning(
                f"Review {review.id} for group {review.group_id} is overdue "
                f"(due: {review.due_date.isoformat()})"
            )

        db.commit()

        return overdue_ids
