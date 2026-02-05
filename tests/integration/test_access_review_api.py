"""
Integration tests for Access Review API endpoints.

Tests complete access review workflows with real database interactions
but mocked authentication.
"""

import datetime
import json
from unittest.mock import patch

import pytest


class TestAccessReviewAPI:
    """Test Access Review API endpoints."""

    @pytest.fixture
    def setup_test_data(self, app):
        """Create test groups and identities."""
        with app.app_context():
            from apps.api.database import get_db

            db = get_db()

            # Create test identity for owner
            owner_id = db.identities.insert(
                username="test_owner",
                email="owner@test.com",
                full_name="Test Owner",
                password_hash="fake_hash",
            )

            # Create test group
            group_id = db.identity_groups.insert(
                name="Test Group for Reviews",
                description="Integration test group",
                is_active=True,
                owner_identity_id=owner_id,
                review_enabled=True,
                review_interval_days=90,
                review_due_days=14,
                review_auto_apply=True,
            )

            # Create test members
            member1_id = db.identities.insert(
                username="member1",
                email="member1@test.com",
                full_name="Member One",
                password_hash="fake_hash",
            )

            member2_id = db.identities.insert(
                username="member2",
                email="member2@test.com",
                full_name="Member Two",
                password_hash="fake_hash",
            )

            # Add members to group
            db.identity_group_memberships.insert(
                group_id=group_id, identity_id=member1_id
            )
            db.identity_group_memberships.insert(
                group_id=group_id, identity_id=member2_id
            )

            db.commit()

            return {
                "owner_id": owner_id,
                "group_id": group_id,
                "member1_id": member1_id,
                "member2_id": member2_id,
            }

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.licensing.get_license_client")
    def test_create_access_review(self, mock_license, mock_jwt, client, app, setup_test_data):
        """Test POST /api/v1/access-reviews - Create review."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}

        # Mock license validation
        mock_license_instance = mock_license.return_value
        mock_license_instance.validate.return_value.tier = "enterprise"

        now = datetime.datetime.now(datetime.timezone.utc)
        payload = {
            "group_id": setup_test_data["group_id"],
            "period_start": (now - datetime.timedelta(days=90)).isoformat(),
            "period_end": now.isoformat(),
            "due_date": (now + datetime.timedelta(days=14)).isoformat(),
            "auto_apply": True,
        }

        response = client.post(
            "/api/v1/access-reviews",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 201
        data = json.loads(response.data)
        assert data["group_id"] == setup_test_data["group_id"]
        assert data["total_members"] == 2  # 2 members in group
        assert data["status"] in ["scheduled", "in_progress"]

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.licensing.get_license_client")
    def test_list_access_reviews(self, mock_license, mock_jwt, client, app, setup_test_data):
        """Test GET /api/v1/access-reviews - List reviews."""
        mock_jwt.return_value = {"user_id": 1, "username": "admin"}
        mock_license_instance = mock_license.return_value
        mock_license_instance.validate.return_value.tier = "enterprise"

        # Create a review first
        with app.app_context():
            from apps.api.database import get_db
            from apps.api.services.access_review.service import AccessReviewService

            db = get_db()
            service = AccessReviewService(db)

            now = datetime.datetime.now(datetime.timezone.utc)
            service.create_review(
                group_id=setup_test_data["group_id"],
                period_start=now - datetime.timedelta(days=90),
                period_end=now,
                due_date=now + datetime.timedelta(days=14),
            )

        response = client.get(
            "/api/v1/access-reviews",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "reviews" in data
        assert len(data["reviews"]) >= 1

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.licensing.get_license_client")
    def test_get_review_details(self, mock_license, mock_jwt, client, app, setup_test_data):
        """Test GET /api/v1/access-reviews/:id - Get review details."""
        mock_jwt.return_value = {"user_id": setup_test_data["owner_id"], "username": "test_owner"}
        mock_license_instance = mock_license.return_value
        mock_license_instance.validate.return_value.tier = "enterprise"

        # Create a review
        with app.app_context():
            from apps.api.database import get_db
            from apps.api.services.access_review.service import AccessReviewService

            db = get_db()
            service = AccessReviewService(db)

            now = datetime.datetime.now(datetime.timezone.utc)
            review = service.create_review(
                group_id=setup_test_data["group_id"],
                period_start=now - datetime.timedelta(days=90),
                period_end=now,
                due_date=now + datetime.timedelta(days=14),
            )
            review_id = review["id"]

        response = client.get(
            f"/api/v1/access-reviews/{review_id}",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["id"] == review_id
        assert data["group_id"] == setup_test_data["group_id"]
        assert "reviewers" in data

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.licensing.get_license_client")
    def test_get_review_items(self, mock_license, mock_jwt, client, app, setup_test_data):
        """Test GET /api/v1/access-reviews/:id/items - Get review items."""
        mock_jwt.return_value = {"user_id": setup_test_data["owner_id"], "username": "test_owner"}
        mock_license_instance = mock_license.return_value
        mock_license_instance.validate.return_value.tier = "enterprise"

        # Create a review
        with app.app_context():
            from apps.api.database import get_db
            from apps.api.services.access_review.service import AccessReviewService

            db = get_db()
            service = AccessReviewService(db)

            now = datetime.datetime.now(datetime.timezone.utc)
            review = service.create_review(
                group_id=setup_test_data["group_id"],
                period_start=now - datetime.timedelta(days=90),
                period_end=now,
                due_date=now + datetime.timedelta(days=14),
            )
            review_id = review["id"]

        response = client.get(
            f"/api/v1/access-reviews/{review_id}/items",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "items" in data
        assert len(data["items"]) == 2  # 2 members

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.licensing.get_license_client")
    def test_submit_review_decision(self, mock_license, mock_jwt, client, app, setup_test_data):
        """Test POST /api/v1/access-reviews/:id/decisions - Submit decision."""
        mock_jwt.return_value = {"user_id": setup_test_data["owner_id"], "username": "test_owner"}
        mock_license_instance = mock_license.return_value
        mock_license_instance.validate.return_value.tier = "enterprise"

        # Create a review
        with app.app_context():
            from apps.api.database import get_db
            from apps.api.services.access_review.service import AccessReviewService

            db = get_db()
            service = AccessReviewService(db)

            now = datetime.datetime.now(datetime.timezone.utc)
            review = service.create_review(
                group_id=setup_test_data["group_id"],
                period_start=now - datetime.timedelta(days=90),
                period_end=now,
                due_date=now + datetime.timedelta(days=14),
            )
            review_id = review["id"]

            # Get membership ID
            items = service.get_review_items(review_id, include_identity_info=False)
            membership_id = items[0]["membership_id"]

        payload = {
            "membership_id": membership_id,
            "decision": "keep",
            "justification": "Active contributor",
        }

        response = client.post(
            f"/api/v1/access-reviews/{review_id}/decisions",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["decision"] == "keep"
        assert data["justification"] == "Active contributor"

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.licensing.get_license_client")
    def test_complete_review_workflow(self, mock_license, mock_jwt, client, app, setup_test_data):
        """Test complete review workflow: create, review all, complete."""
        mock_jwt.return_value = {"user_id": setup_test_data["owner_id"], "username": "test_owner"}
        mock_license_instance = mock_license.return_value
        mock_license_instance.validate.return_value.tier = "enterprise"

        # Create a review
        with app.app_context():
            from apps.api.database import get_db
            from apps.api.services.access_review.service import AccessReviewService

            db = get_db()
            service = AccessReviewService(db)

            now = datetime.datetime.now(datetime.timezone.utc)
            review = service.create_review(
                group_id=setup_test_data["group_id"],
                period_start=now - datetime.timedelta(days=90),
                period_end=now,
                due_date=now + datetime.timedelta(days=14),
                auto_apply=False,  # Don't auto-apply to avoid member removal
            )
            review_id = review["id"]

            # Get all items
            items = service.get_review_items(review_id, include_identity_info=False)

        # Submit decisions for all members
        for item in items:
            payload = {
                "membership_id": item["membership_id"],
                "decision": "keep",
                "justification": "Integration test",
            }

            response = client.post(
                f"/api/v1/access-reviews/{review_id}/decisions",
                data=json.dumps(payload),
                content_type="application/json",
                headers={"Authorization": "Bearer fake-token"},
            )
            assert response.status_code == 200

        # Complete review
        response = client.post(
            f"/api/v1/access-reviews/{review_id}/complete",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "completed"

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.licensing.get_license_client")
    def test_get_my_reviews(self, mock_license, mock_jwt, client, app, setup_test_data):
        """Test GET /api/v1/access-reviews/my-reviews - Get assigned reviews."""
        mock_jwt.return_value = {"user_id": setup_test_data["owner_id"], "username": "test_owner"}
        mock_license_instance = mock_license.return_value
        mock_license_instance.validate.return_value.tier = "enterprise"

        # Create a review (will be assigned to owner)
        with app.app_context():
            from apps.api.database import get_db
            from apps.api.services.access_review.service import AccessReviewService

            db = get_db()
            service = AccessReviewService(db)

            now = datetime.datetime.now(datetime.timezone.utc)
            service.create_review(
                group_id=setup_test_data["group_id"],
                period_start=now - datetime.timedelta(days=90),
                period_end=now,
                due_date=now + datetime.timedelta(days=14),
            )

        response = client.get(
            "/api/v1/access-reviews/my-reviews",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert "reviews" in data
        assert len(data["reviews"]) >= 1

    @patch("apps.api.auth.decorators.verify_jwt")
    @patch("shared.licensing.get_license_client")
    def test_cannot_complete_unreviewed(self, mock_license, mock_jwt, client, app, setup_test_data):
        """Test that completing review fails if not all members reviewed."""
        mock_jwt.return_value = {"user_id": setup_test_data["owner_id"], "username": "test_owner"}
        mock_license_instance = mock_license.return_value
        mock_license_instance.validate.return_value.tier = "enterprise"

        # Create a review
        with app.app_context():
            from apps.api.database import get_db
            from apps.api.services.access_review.service import AccessReviewService

            db = get_db()
            service = AccessReviewService(db)

            now = datetime.datetime.now(datetime.timezone.utc)
            review = service.create_review(
                group_id=setup_test_data["group_id"],
                period_start=now - datetime.timedelta(days=90),
                period_end=now,
                due_date=now + datetime.timedelta(days=14),
            )
            review_id = review["id"]

        # Try to complete without reviewing all members
        response = client.post(
            f"/api/v1/access-reviews/{review_id}/complete",
            headers={"Authorization": "Bearer fake-token"},
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert "not reviewed" in data["error"].lower()
