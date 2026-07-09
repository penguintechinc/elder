"""Tests for documents module (CRUD, collections, versions, wiki-links).

Integration tests for documents API endpoints.
Uses real JWT token-based authentication and real database.
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from uuid import uuid4
from quart import current_app


@pytest.mark.asyncio
class TestDocumentsCRUD:
    """Test document CRUD operations."""

    async def test_create_document_draft(self, async_client, generate_token, app):
        """Create a draft document."""
        token = generate_token(tenant_id=1, scopes=["documents:write"])

        async with app.app_context():
            # Create identity record for author
            db = current_app.db
            now = datetime.now(timezone.utc)
            identity_id = db.identities.insert(
                identity_type="human",
                username=f"test_user_{uuid4().hex[:8]}",
                email="test@example.com",
                tenant_id=1,
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                mfa_enabled=False,
                must_change_password=False,
                portal_role="observer",
                created_at=now,
                updated_at=now,
            )
            db.commit()

            with patch(
                "apps.api.modules.documents.routes.documents.current_app"
            ) as mock_app:
                with patch(
                    "shared.utils.village_id.generate_village_id"
                ) as mock_village_id:
                    mock_app.db = current_app.db
                    mock_app.redis_client = MagicMock()
                    mock_village_id.return_value = f"test-vid-{uuid4().hex[:8]}"

                    response = await async_client.post(
                        "/api/v1/documents",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "title": "Getting Started",
                            "body": "# Welcome\n\nThis is a guide.",
                            "category": "intro",
                            "tags": ["beginner", "tutorial"],
                        },
                    )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["title"] == "Getting Started"
        assert data["slug"] == "getting-started"
        assert data["status"] == "draft"


@pytest.mark.asyncio
class TestPublicDocuments:
    """Test public document endpoints (no auth)."""

    async def test_public_list_no_auth(self, async_client):
        """List public documents without authentication."""
        response = await async_client.get("/api/v1/documents/public")

        # Should return 200 even without auth
        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "items" in data
        assert "pagination" in data
