"""
Unit tests for Access Review Service.

Tests core functionality of access review creation, decision submission,
completion, and scheduling.
"""

import datetime
import pytest
from unittest.mock import MagicMock, patch

from apps.api.services.access_review.service import AccessReviewService


class TestAccessReviewService:
    """Test AccessReviewService core methods."""

    @pytest.fixture
    def mock_db(self):
        """Create mock PyDAL database."""
        db = MagicMock()

        # Mock tables
        db.access_reviews = MagicMock()
        db.access_review_items = MagicMock()
        db.access_review_assignments = MagicMock()
        db.identity_groups = MagicMock()
        db.identity_group_memberships = MagicMock()
        db.identities = MagicMock()

        # Mock commit
        db.commit = MagicMock()

        return db

    @pytest.fixture
    def service(self, mock_db):
        """Create AccessReviewService instance."""
        return AccessReviewService(mock_db)

    def test_create_review_creates_items_for_members(self, service, mock_db):
        """Test that create_review creates items for all group members."""
        now = datetime.datetime.now(datetime.timezone.utc)

        # Mock group
        mock_group = MagicMock()
        mock_group.id = 1
        mock_group.name = "Test Group"
        mock_group.tenant_id = 1
        mock_group.review_assignment_mode = "all_owners"
        mock_group.owner_identity_id = 10
        mock_db.identity_groups.__getitem__.return_value = mock_group

        # Mock memberships
        mock_membership1 = MagicMock()
        mock_membership1.id = 101
        mock_membership1.identity_id = 1
        mock_membership1.group_id = 1

        mock_membership2 = MagicMock()
        mock_membership2.id = 102
        mock_membership2.identity_id = 2
        mock_membership2.group_id = 1

        mock_db().select.return_value = [mock_membership1, mock_membership2]

        # Mock insert returns
        mock_db.access_reviews.insert.return_value = 500
        mock_db.access_review_items.insert.return_value = 600

        with patch.object(service, "get_review") as mock_get_review:
            mock_get_review.return_value = {
                "id": 500,
                "group_id": 1,
                "total_members": 2,
            }

            # Create review
            review = service.create_review(
                group_id=1,
                period_start=now - datetime.timedelta(days=90),
                period_end=now,
                due_date=now + datetime.timedelta(days=14),
            )

            # Verify review created
            assert mock_db.access_reviews.insert.called
            assert review["id"] == 500

            # Verify items created (called twice for 2 members)
            assert mock_db.access_review_items.insert.call_count == 2

    def test_submit_review_decision_updates_progress(self, service, mock_db):
        """Test that submitting decisions updates review progress."""
        now = datetime.datetime.now(datetime.timezone.utc)

        # Mock review item
        mock_item = MagicMock()
        mock_item.id = 700
        mock_item.review_id = 500
        mock_item.membership_id = 101
        mock_db().select.return_value.first.return_value = mock_item

        # Mock review for progress update
        mock_review_item1 = MagicMock()
        mock_review_item1.decision = "keep"
        mock_review_item2 = MagicMock()
        mock_review_item2.decision = None

        mock_db().select.return_value = [mock_review_item1, mock_review_item2]

        with patch.object(service, "_review_item_to_dict") as mock_to_dict:
            mock_to_dict.return_value = {"id": 700, "decision": "keep"}

            # Submit decision
            result = service.submit_review_decision(
                review_id=500,
                membership_id=101,
                decision="keep",
                reviewed_by=10,
                justification="Test justification",
            )

            # Verify item updated
            assert mock_db().update.called
            assert result["id"] == 700

    def test_complete_review_validates_all_reviewed(self, service, mock_db):
        """Test that complete_review validates all members reviewed."""
        # Mock review
        mock_review = MagicMock()
        mock_review.id = 500
        mock_review.status = "in_progress"
        mock_review.auto_apply_decisions = True
        mock_review.group_id = 1
        mock_db.access_reviews.__getitem__.return_value = mock_review

        # Mock unreviewed items
        mock_item1 = MagicMock()
        mock_item1.decision = "keep"

        mock_item2 = MagicMock()
        mock_item2.decision = None  # Unreviewed!

        mock_db().select.return_value = [mock_item1, mock_item2]

        # Should raise error for unreviewed items
        with pytest.raises(ValueError, match="not reviewed"):
            service.complete_review(review_id=500, completed_by=10)

    def test_complete_review_applies_decisions(self, service, mock_db):
        """Test that complete_review applies decisions when all reviewed."""
        # Mock review
        mock_review = MagicMock()
        mock_review.id = 500
        mock_review.status = "in_progress"
        mock_review.auto_apply_decisions = True
        mock_review.group_id = 1
        mock_db.access_reviews.__getitem__.return_value = mock_review

        # Mock all items reviewed
        mock_item1 = MagicMock()
        mock_item1.decision = "keep"
        mock_item2 = MagicMock()
        mock_item2.decision = "remove"

        mock_db().select.return_value = [mock_item1, mock_item2]

        with patch.object(service, "apply_review_decisions") as mock_apply:
            with patch.object(service, "schedule_next_review") as mock_schedule:
                with patch.object(service, "get_review") as mock_get_review:
                    mock_get_review.return_value = {"id": 500, "status": "completed"}

                    # Complete review
                    result = service.complete_review(review_id=500, completed_by=10)

                    # Verify decisions applied
                    assert mock_apply.called
                    assert mock_schedule.called
                    assert result["id"] == 500

    def test_apply_review_decisions_removes_members(self, service, mock_db):
        """Test that apply_review_decisions calls GroupMembershipService."""
        # Mock review
        mock_review = MagicMock()
        mock_review.id = 500
        mock_review.group_id = 1
        mock_db.access_reviews.__getitem__.return_value = mock_review

        # Mock items with remove decision
        mock_item = MagicMock()
        mock_item.decision = "remove"
        mock_item.identity_id = 5

        mock_db().select.return_value = [mock_item]

        with patch(
            "apps.api.services.access_review.service.GroupMembershipService"
        ) as MockGroupService:
            mock_group_service = MockGroupService.return_value
            mock_group_service.remove_member = MagicMock()

            # Apply decisions
            service.apply_review_decisions(review_id=500, applied_by=10)

            # Verify remove_member called
            assert mock_group_service.remove_member.called
            call_args = mock_group_service.remove_member.call_args
            assert call_args[1]["group_id"] == 1
            assert call_args[1]["identity_id"] == 5

    def test_apply_review_decisions_extends_expiration(self, service, mock_db):
        """Test that apply_review_decisions updates expiration for extend."""
        # Mock review
        mock_review = MagicMock()
        mock_review.id = 500
        mock_review.group_id = 1
        mock_db.access_reviews.__getitem__.return_value = mock_review

        # Mock items with extend decision
        new_expiration = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            days=90
        )
        mock_item = MagicMock()
        mock_item.decision = "extend"
        mock_item.new_expiration = new_expiration
        mock_item.membership_id = 101

        # Mock membership
        mock_membership = MagicMock()
        mock_membership.id = 101
        mock_db.identity_group_memberships.__getitem__.return_value = mock_membership

        mock_db().select.return_value = [mock_item]

        with patch(
            "apps.api.services.access_review.service.GroupMembershipService"
        ):
            # Apply decisions
            service.apply_review_decisions(review_id=500, applied_by=10)

            # Verify expiration updated
            assert mock_db().update.called

    def test_schedule_next_review_calculates_date(self, service, mock_db):
        """Test that schedule_next_review calculates next review date."""
        # Mock group with review enabled
        mock_group = MagicMock()
        mock_group.id = 1
        mock_group.review_enabled = True
        mock_group.review_interval_days = 90
        mock_db.identity_groups.__getitem__.return_value = mock_group

        # Schedule next review
        service.schedule_next_review(group_id=1)

        # Verify last_review_date and next_review_date updated
        assert mock_db().update.called

    def test_check_overdue_reviews_marks_overdue(self, service, mock_db):
        """Test that check_overdue_reviews updates status."""
        now = datetime.datetime.now(datetime.timezone.utc)

        # Mock overdue review
        mock_review = MagicMock()
        mock_review.id = 500
        mock_review.status = "in_progress"
        mock_review.due_date = now - datetime.timedelta(days=1)
        mock_review.group_id = 1

        mock_db().select.return_value = [mock_review]

        # Check overdue
        overdue_ids = service.check_overdue_reviews()

        # Verify status updated
        assert mock_db().update.called
        assert 500 in overdue_ids

    def test_get_reviews_for_owner_filters_correctly(self, service, mock_db):
        """Test that get_reviews_for_owner returns assigned reviews."""
        # Mock review
        mock_review = MagicMock()
        mock_review.id = 500
        mock_review.group_id = 1

        mock_group = MagicMock()
        mock_group.name = "Test Group"
        mock_db.identity_groups.__getitem__.return_value = mock_group

        mock_db().select.return_value = [mock_review]

        with patch.object(service, "_review_to_dict") as mock_to_dict:
            mock_to_dict.return_value = {"id": 500, "group_id": 1}

            # Get reviews for owner
            reviews = service.get_reviews_for_owner(owner_identity_id=10)

            # Verify query constructed correctly
            assert isinstance(reviews, list)
