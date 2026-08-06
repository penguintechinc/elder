"""
Unit tests for Helpdesk CRM endpoints (Companies and Contacts).

Uses real JWT token-based authentication and real database.
Module enablement via ELDER_MODULE_HELPDESK=true in conftest.
"""

import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from uuid import uuid4
from quart import current_app


@pytest_asyncio.fixture(autouse=True)
async def _clean_crm_tables(app):
    """Isolate each CRM test on the shared test DB by truncating the CRM
    tables first (these tests seed per-test, so no class-scoped seed is lost)."""
    async with app.app_context():
        db = current_app.db
        db(db.hd_contacts.id > 0).delete()
        db(db.hd_companies.id > 0).delete()
        db.commit()
    yield


class TestHelpDeskCompaniesAPI:
    """Test Helpdesk Companies CRM endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_companies_empty(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/companies with empty list."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_companies.tenant_id == 1).delete()
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/companies",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "items" in data
        assert data["items"] == []
        assert data["pagination"]["total"] == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_company(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/companies."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {
            "name": "Acme Corp",
            "domain": "acme.com",
            "industry": "Technology",
            "size": "enterprise",
            "website": "https://acme.com",
            "notes": "Major client",
        }

        async with app.app_context():
            # Mirror Wave 1 tickets create test: provide redis_client + mint
            # village_id deterministically (test app has no real redis).
            with patch(
                "apps.api.modules.helpdesk.routes.companies.current_app"
            ) as mock_app:
                with patch(
                    "shared.utils.village_id.generate_village_id"
                ) as mock_village_id:
                    mock_app.db = current_app.db
                    mock_app.redis_client = MagicMock()
                    mock_village_id.return_value = f"test-vid-{uuid4().hex[:8]}"

                    response = await async_client.post(
                        "/api/v1/helpdesk/companies",
                        json=payload,
                        headers={"Authorization": f"Bearer {token}"},
                    )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["name"] == "Acme Corp"
        assert data["domain"] == "acme.com"
        assert data["industry"] == "Technology"
        assert data["size"] == "enterprise"
        assert data["website"] == "https://acme.com"
        assert data["notes"] == "Major client"
        assert data["village_id"] is not None
        assert "id" in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_company_missing_name(
        self, mock_get_user, async_client, generate_token
    ):
        """Test POST /api/v1/helpdesk/companies with missing name."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {"domain": "example.com"}

        response = await async_client.post(
            "/api/v1/helpdesk/companies",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_company(self, mock_get_user, async_client, generate_token, app):
        """Test GET /api/v1/helpdesk/companies/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            # Insert test company
            company_id = db.hd_companies.insert(
                tenant_id=1,
                village_id=f"co-{uuid4().hex[:8]}",
                name="Test Company",
                domain="test.com",
                industry="Tech",
                size="smb",
                website="https://test.com",
                notes="Test notes",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.get(
            f"/api/v1/helpdesk/companies/{company_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["name"] == "Test Company"
        assert data["domain"] == "test.com"
        assert data["id"] == company_id

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_company_not_found(
        self, mock_get_user, async_client, generate_token
    ):
        """Test GET /api/v1/helpdesk/companies/:id with non-existent ID."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        response = await async_client.get(
            "/api/v1/helpdesk/companies/99999",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_company(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test PATCH /api/v1/helpdesk/companies/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            company_id = db.hd_companies.insert(
                tenant_id=1,
                village_id=f"co-{uuid4().hex[:8]}",
                name="Old Name",
                domain="old.com",
                industry="Tech",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        payload = {
            "name": "New Name",
            "industry": "Finance",
        }

        response = await async_client.patch(
            f"/api/v1/helpdesk/companies/{company_id}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["name"] == "New Name"
        assert data["industry"] == "Finance"
        assert data["domain"] == "old.com"  # Unchanged

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_company(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test DELETE /api/v1/helpdesk/companies/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            company_id = db.hd_companies.insert(
                tenant_id=1,
                village_id=f"co-{uuid4().hex[:8]}",
                name="Delete Me",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.delete(
            f"/api/v1/helpdesk/companies/{company_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

        # Verify deleted
        async with app.app_context():
            db = current_app.db
            company = db(db.hd_companies.id == company_id).select().first()
            assert company is None

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_companies_filter_by_name(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/companies with name filter."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            # Insert multiple companies
            db.hd_companies.insert(
                tenant_id=1,
                village_id=f"co-{uuid4().hex[:8]}",
                name="Acme Corp",
                created_at=now,
                updated_at=now,
            )
            db.hd_companies.insert(
                tenant_id=1,
                village_id=f"co-{uuid4().hex[:8]}",
                name="Beta Inc",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/companies?name=Acme",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "Acme Corp"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_queries_scoped_to_tenant_companies(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test that company queries are scoped to tenant via JWT claims."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token_tenant1 = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            # Clean up to ensure empty start
            db(db.hd_companies.tenant_id == 1).delete()
            db.commit()

        # Tenant 1 should see empty list
        response = await async_client.get(
            "/api/v1/helpdesk/companies",
            headers={"Authorization": f"Bearer {token_tenant1}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert len(data["items"]) == 0


class TestHelpDeskContactsAPI:
    """Test Helpdesk Contacts CRM endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_contacts_empty(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/contacts with empty list."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_contacts.tenant_id == 1).delete()
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/contacts",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "items" in data
        assert data["items"] == []
        assert data["pagination"]["total"] == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_contact(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/helpdesk/contacts."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            # Create test company
            company_id = db.hd_companies.insert(
                tenant_id=1,
                village_id=f"co-{uuid4().hex[:8]}",
                name="Test Company",
                created_at=now,
                updated_at=now,
            )
            db.commit()

            payload = {
                "email": "john@example.com",
                "first_name": "John",
                "last_name": "Doe",
                "phone": "+1-555-0123",
                "job_title": "Manager",
                "company_id": company_id,
                "notes": "Primary contact",
            }

            with patch(
                "apps.api.modules.helpdesk.routes.contacts.current_app"
            ) as mock_app:
                with patch(
                    "shared.utils.village_id.generate_village_id"
                ) as mock_village_id:
                    mock_app.db = current_app.db
                    mock_app.redis_client = MagicMock()
                    mock_village_id.return_value = f"test-vid-{uuid4().hex[:8]}"

                    response = await async_client.post(
                        "/api/v1/helpdesk/contacts",
                        json=payload,
                        headers={"Authorization": f"Bearer {token}"},
                    )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["email"] == "john@example.com"
        assert data["first_name"] == "John"
        assert data["last_name"] == "Doe"
        assert data["company_id"] == company_id
        assert data["village_id"] is not None
        assert "id" in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_contact_missing_email(
        self, mock_get_user, async_client, generate_token
    ):
        """Test POST /api/v1/helpdesk/contacts with missing email."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {"first_name": "John", "last_name": "Doe"}

        response = await async_client.post(
            "/api/v1/helpdesk/contacts",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_contact(self, mock_get_user, async_client, generate_token, app):
        """Test GET /api/v1/helpdesk/contacts/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            contact_id = db.hd_contacts.insert(
                tenant_id=1,
                village_id=f"ct-{uuid4().hex[:8]}",
                email="jane@example.com",
                first_name="Jane",
                last_name="Smith",
                phone="+1-555-0456",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.get(
            f"/api/v1/helpdesk/contacts/{contact_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["email"] == "jane@example.com"
        assert data["first_name"] == "Jane"
        assert data["id"] == contact_id

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_contact(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test PATCH /api/v1/helpdesk/contacts/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            contact_id = db.hd_contacts.insert(
                tenant_id=1,
                village_id=f"ct-{uuid4().hex[:8]}",
                email="old@example.com",
                first_name="Old",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        payload = {
            "email": "new@example.com",
            "first_name": "New",
            "job_title": "Developer",
        }

        response = await async_client.patch(
            f"/api/v1/helpdesk/contacts/{contact_id}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["email"] == "new@example.com"
        assert data["first_name"] == "New"
        assert data["job_title"] == "Developer"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_contact(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test DELETE /api/v1/helpdesk/contacts/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            contact_id = db.hd_contacts.insert(
                tenant_id=1,
                village_id=f"ct-{uuid4().hex[:8]}",
                email="delete@example.com",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.delete(
            f"/api/v1/helpdesk/contacts/{contact_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

        # Verify deleted
        async with app.app_context():
            db = current_app.db
            contact = db(db.hd_contacts.id == contact_id).select().first()
            assert contact is None

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_contacts_filter_by_email(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/helpdesk/contacts with email filter."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            db.hd_contacts.insert(
                tenant_id=1,
                village_id=f"ct-{uuid4().hex[:8]}",
                email="alice@example.com",
                created_at=now,
                updated_at=now,
            )
            db.hd_contacts.insert(
                tenant_id=1,
                village_id=f"ct-{uuid4().hex[:8]}",
                email="bob@example.com",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.get(
            "/api/v1/helpdesk/contacts?email=alice",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert len(data["items"]) == 1
        assert data["items"][0]["email"] == "alice@example.com"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_queries_scoped_to_tenant_contacts(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test that contact queries are scoped to tenant via JWT claims."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token_tenant1 = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            # Clean up to ensure empty start
            db(db.hd_contacts.tenant_id == 1).delete()
            db.commit()

        # Tenant 1 should see empty list
        response = await async_client.get(
            "/api/v1/helpdesk/contacts",
            headers={"Authorization": f"Bearer {token_tenant1}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert len(data["items"]) == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_contact_company_linkage(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test contact-to-company linkage."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)

            # Create company
            company_id = db.hd_companies.insert(
                tenant_id=1,
                village_id=f"co-{uuid4().hex[:8]}",
                name="Tech Corp",
                created_at=now,
                updated_at=now,
            )

            # Create contact linked to company
            contact_id = db.hd_contacts.insert(
                tenant_id=1,
                village_id=f"ct-{uuid4().hex[:8]}",
                email="contact@techcorp.com",
                first_name="John",
                hd_company_id=company_id,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        # Fetch contact and verify company linkage
        response = await async_client.get(
            f"/api/v1/helpdesk/contacts/{contact_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["company_id"] == company_id

        # List contacts filtered by company
        response = await async_client.get(
            f"/api/v1/helpdesk/contacts?company_id={company_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert len(data["items"]) == 1
        assert data["items"][0]["company_id"] == company_id
