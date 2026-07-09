"""
Unit tests for Helpdesk Email Accounts and Ticket Forms API endpoints.

Uses real JWT token-based authentication and real database.
Module enablement via conftest enable_helpdesk_module fixture.
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from quart import current_app


class TestHelpDeskEmailAccountsAPI:
    """Test Helpdesk Email Accounts API endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_email_accounts_empty(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/email-accounts with empty list."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_email_accounts.tenant_id == 1).delete()
            db.commit()

        response = await async_client.get(
            "/api/v1/email-accounts",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "items" in data
        assert data["items"] == []
        assert data["pagination"]["total"] == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_email_account_smtp_imap(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/email-accounts with SMTP/IMAP provider."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_email_accounts.tenant_id == 1).delete()
            db.commit()

        payload = {
            "email_address": "support@example.com",
            "display_name": "Support",
            "provider": "smtp_imap",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_mode": "starttls",
            "smtp_username": "user@gmail.com",
            "smtp_password_ref": "penguin-sal-smtp-password",
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "imap_username": "user@gmail.com",
            "imap_password_ref": "penguin-sal-imap-password",
            "is_default": True,
        }

        response = await async_client.post(
            "/api/v1/email-accounts",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["email_address"] == "support@example.com"
        assert data["display_name"] == "Support"
        assert data["provider"] == "smtp_imap"
        assert data["is_default"] is True
        assert data["is_active"] is True
        assert "id" in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_email_account_gmail_api(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/email-accounts with Gmail API provider."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {
            "email_address": "gmail-support@example.com",
            "display_name": "Gmail Support",
            "provider": "gmail_api",
            "gmail_credentials_ref": "penguin-sal-gmail-credentials",
            "gmail_token_ref": "penguin-sal-gmail-token",
            "is_default": False,
        }

        response = await async_client.post(
            "/api/v1/email-accounts",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["email_address"] == "gmail-support@example.com"
        assert data["provider"] == "gmail_api"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_email_account_missing_email(
        self, mock_get_user, async_client, generate_token
    ):
        """Test POST /api/v1/email-accounts without email_address."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        payload = {"provider": "smtp_imap"}

        response = await async_client.post(
            "/api/v1/email-accounts",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_email_account(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/email-accounts/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_email_accounts.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            account_id = db.hd_email_accounts.insert(
                tenant_id=1,
                email_address="test@example.com",
                display_name="Test Account",
                provider="smtp_imap",
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_mode="starttls",
                smtp_username="user",
                smtp_password_ref="secret-ref-1",
                imap_host="imap.example.com",
                imap_port=993,
                imap_username="user",
                imap_password_ref="secret-ref-2",
                is_default=False,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.get(
            f"/api/v1/email-accounts/{account_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["id"] == account_id
        assert data["email_address"] == "test@example.com"
        assert data["provider"] == "smtp_imap"
        # Ensure secret refs are returned but not log them
        assert data["smtp_password_ref"] == "secret-ref-1"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_email_account_not_found(
        self, mock_get_user, async_client, generate_token
    ):
        """Test GET /api/v1/email-accounts/:id with non-existent account."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        response = await async_client.get(
            "/api/v1/email-accounts/9999",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_email_account(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test PATCH /api/v1/email-accounts/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_email_accounts.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            account_id = db.hd_email_accounts.insert(
                tenant_id=1,
                email_address="test@example.com",
                display_name="Original Name",
                provider="smtp_imap",
                is_default=False,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        payload = {
            "display_name": "Updated Name",
            "is_default": True,
            "is_active": False,
        }

        response = await async_client.patch(
            f"/api/v1/email-accounts/{account_id}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["display_name"] == "Updated Name"
        assert data["is_default"] is True
        assert data["is_active"] is False

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_email_account(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test DELETE /api/v1/email-accounts/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_email_accounts.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            account_id = db.hd_email_accounts.insert(
                tenant_id=1,
                email_address="test@example.com",
                display_name="Test",
                provider="smtp_imap",
                is_default=False,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.delete(
            f"/api/v1/email-accounts/{account_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_test_connection(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/email-accounts/:id/test-connection."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_email_accounts.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            account_id = db.hd_email_accounts.insert(
                tenant_id=1,
                email_address="test@example.com",
                display_name="Test",
                provider="smtp_imap",
                smtp_host="smtp.example.com",
                smtp_username="user",
                imap_host="imap.example.com",
                imap_username="user",
                is_default=False,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.post(
            f"/api/v1/email-accounts/{account_id}/test-connection",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["account_id"] == account_id
        assert "smtp_configured" in data
        assert "imap_configured" in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_email_account_list_pagination(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test that email accounts list respects pagination."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token_tenant1 = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_email_accounts.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)

            # Create multiple accounts for tenant 1
            for i in range(3):
                db.hd_email_accounts.insert(
                    tenant_id=1,
                    email_address=f"account{i}@example.com",
                    display_name=f"Account {i}",
                    provider="smtp_imap",
                    is_default=False,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            db.commit()

        response = await async_client.get(
            "/api/v1/email-accounts?page=1&per_page=2",
            headers={"Authorization": f"Bearer {token_tenant1}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["pagination"]["total"] == 3
        assert len(data["items"]) == 2


class TestHelpDeskTicketFormsAPI:
    """Test Helpdesk Ticket Forms API endpoints."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_list_forms_empty(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test GET /api/v1/ticket-forms with empty list."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_ticket_forms.tenant_id == 1).delete()
            db.commit()

        response = await async_client.get(
            "/api/v1/ticket-forms",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert "items" in data
        assert data["items"] == []
        assert data["pagination"]["total"] == 0

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_form(self, mock_get_user, async_client, generate_token, app):
        """Test POST /api/v1/ticket-forms."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_ticket_forms.tenant_id == 1).delete()
            db.commit()

        payload = {
            "name": "Contact Support",
            "slug": "contact-support",
            "description": "General support form",
            "captcha_provider": "turnstile",
            "captcha_site_key": "pk_test_123456",
            "captcha_secret_ref": "penguin-sal-turnstile-secret",
            "fields": [
                {
                    "id": "subject",
                    "label": "Subject",
                    "type": "text",
                    "required": True,
                },
                {
                    "id": "description",
                    "label": "Description",
                    "type": "textarea",
                    "required": True,
                },
            ],
        }

        response = await async_client.post(
            "/api/v1/ticket-forms",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["name"] == "Contact Support"
        assert data["slug"] == "contact-support"
        assert data["captcha_provider"] == "turnstile"
        assert data["captcha_site_key"] == "pk_test_123456"
        assert len(data["fields"]) == 2
        assert "id" in data

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_form_duplicate_slug(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test POST /api/v1/ticket-forms with duplicate slug (409)."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_ticket_forms.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            db.hd_ticket_forms.insert(
                tenant_id=1,
                name="First Form",
                slug="duplicate-slug",
                is_default=False,
                is_active=True,
                captcha_provider="none",
                fields="{}",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        payload = {
            "name": "Second Form",
            "slug": "duplicate-slug",
        }

        response = await async_client.post(
            "/api/v1/ticket-forms",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 409

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_get_form(self, mock_get_user, async_client, generate_token, app):
        """Test GET /api/v1/ticket-forms/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_ticket_forms.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            form_id = db.hd_ticket_forms.insert(
                tenant_id=1,
                name="Test Form",
                slug="test-form",
                description="Test description",
                is_default=False,
                is_active=True,
                captcha_provider="recaptcha",
                captcha_site_key="pk_recaptcha_123",
                captcha_secret_ref="secret-ref",
                fields=json.dumps([{"id": "subject", "label": "Subject"}]),
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.get(
            f"/api/v1/ticket-forms/{form_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["id"] == form_id
        assert data["name"] == "Test Form"
        assert data["captcha_provider"] == "recaptcha"
        assert len(data["fields"]) == 1

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_update_form(self, mock_get_user, async_client, generate_token, app):
        """Test PATCH /api/v1/ticket-forms/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_ticket_forms.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            form_id = db.hd_ticket_forms.insert(
                tenant_id=1,
                name="Original Name",
                slug="original-slug",
                is_default=False,
                is_active=True,
                captcha_provider="none",
                fields="{}",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        payload = {
            "name": "Updated Name",
            "is_active": False,
            "captcha_provider": "turnstile",
            "captcha_site_key": "new_key",
        }

        response = await async_client.patch(
            f"/api/v1/ticket-forms/{form_id}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["name"] == "Updated Name"
        assert data["is_active"] is False
        assert data["captcha_provider"] == "turnstile"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_delete_form(self, mock_get_user, async_client, generate_token, app):
        """Test DELETE /api/v1/ticket-forms/:id."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token = generate_token(tenant_id=1, scopes=["helpdesk:write"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_ticket_forms.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            form_id = db.hd_ticket_forms.insert(
                tenant_id=1,
                name="Test Form",
                slug="test-form",
                is_default=False,
                is_active=True,
                captcha_provider="none",
                fields="{}",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.delete(
            f"/api/v1/ticket-forms/{form_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_get_public_form(self, async_client, app):
        """Test GET /api/v1/ticket-forms/public/:slug (public, no auth)."""
        async with app.app_context():
            db = current_app.db
            db((db.hd_ticket_forms.slug == "public-form")).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            db.hd_ticket_forms.insert(
                tenant_id=1,
                name="Public Form",
                slug="public-form",
                description="Public form description",
                is_default=False,
                is_active=True,
                captcha_provider="turnstile",
                captcha_site_key="pk_public_key",
                captcha_secret_ref="secret-ref-not-exposed",
                fields=json.dumps([{"id": "subject", "label": "Subject"}]),
                created_at=now,
                updated_at=now,
            )
            db.commit()

        response = await async_client.get("/api/v1/ticket-forms/public/public-form")

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["slug"] == "public-form"
        assert data["captcha_site_key"] == "pk_public_key"
        # Secret ref should NOT be exposed in public endpoint
        assert "captcha_secret_ref" not in data
        assert len(data["fields"]) == 1

    @pytest.mark.asyncio
    async def test_get_public_form_not_found(self, async_client):
        """Test GET /api/v1/ticket-forms/public/:slug for non-existent form."""
        response = await async_client.get(
            "/api/v1/ticket-forms/public/nonexistent-slug"
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_submit_public_form_without_captcha(self, async_client, app):
        """Test POST /api/v1/ticket-forms/public/:slug/submit without CAPTCHA token."""
        async with app.app_context():
            db = current_app.db
            db((db.hd_ticket_forms.slug == "form-with-captcha")).delete()
            db(db.hd_tickets.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            db.hd_ticket_forms.insert(
                tenant_id=1,
                name="Captcha Form",
                slug="form-with-captcha",
                is_default=False,
                is_active=True,
                captcha_provider="turnstile",
                captcha_site_key="pk_test",
                captcha_secret_ref="secret-ref",
                fields=json.dumps(
                    [
                        {"id": "subject", "label": "Subject", "required": True},
                    ]
                ),
                created_at=now,
                updated_at=now,
            )
            db.commit()

        payload = {
            "fields": {
                "subject": "Test issue",
            }
        }

        response = await async_client.post(
            "/api/v1/ticket-forms/public/form-with-captcha/submit",
            json=payload,
        )

        # Should fail because CAPTCHA token is required
        assert response.status_code == 400
        data = json.loads(await response.get_data())
        assert "CAPTCHA" in data.get("error", "")

    @pytest.mark.asyncio
    async def test_submit_public_form_missing_required_field(self, async_client, app):
        """Test POST /api/v1/ticket-forms/public/:slug/submit with missing required field."""
        async with app.app_context():
            db = current_app.db
            db((db.hd_ticket_forms.slug == "required-field-form")).delete()
            db.commit()

            now = datetime.now(timezone.utc)
            db.hd_ticket_forms.insert(
                tenant_id=1,
                name="Required Field Form",
                slug="required-field-form",
                is_default=False,
                is_active=True,
                captcha_provider="none",
                fields=json.dumps(
                    [
                        {"id": "subject", "label": "Subject", "required": True},
                        {"id": "description", "label": "Description", "required": True},
                    ]
                ),
                created_at=now,
                updated_at=now,
            )
            db.commit()

        payload = {
            "fields": {
                "subject": "Test",
                # description is missing but required
            }
        }

        response = await async_client.post(
            "/api/v1/ticket-forms/public/required-field-form/submit",
            json=payload,
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_submit_public_form_creates_ticket(self, async_client, app):
        """Test POST /api/v1/ticket-forms/public/:slug/submit creates a ticket."""
        async with app.app_context():
            db = current_app.db
            db((db.hd_ticket_forms.slug == "submit-form")).delete()
            db(db.hd_tickets.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)

            form_id = db.hd_ticket_forms.insert(
                tenant_id=1,
                name="Submit Form",
                slug="submit-form",
                is_default=False,
                is_active=True,
                captcha_provider="none",
                fields=json.dumps(
                    [
                        {"id": "subject", "label": "Subject", "required": True},
                        {"id": "description", "label": "Description", "required": True},
                    ]
                ),
                created_at=now,
                updated_at=now,
            )
            db.commit()

        payload = {
            "fields": {
                "subject": "Test issue from form",
                "description": "This is a test",
                "priority": "high",
                "email": "Guest.User@Example.com",
                "first_name": "Guest",
                "last_name": "User",
            }
        }

        response = await async_client.post(
            "/api/v1/ticket-forms/public/submit-form/submit",
            json=payload,
        )

        assert response.status_code == 201
        data = json.loads(await response.get_data())
        assert data["subject"] == "Test issue from form"
        assert data["status"] == "new"
        assert data["village_id"] is not None
        assert "id" in data

        # Anonymous submission must NOT be attributed to any internal identity;
        # a tenant-scoped CRM contact is created (email normalized) and linked.
        async with app.app_context():
            db = current_app.db
            ticket = db(db.hd_tickets.id == data["id"]).select().first()
            assert ticket.requester_identity_id is None
            assert ticket.requester_contact_id is not None
            contact = (
                db(db.hd_contacts.id == ticket.requester_contact_id).select().first()
            )
            assert contact.email == "guest.user@example.com"
            assert contact.tenant_id == 1

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_ticket_forms_list_filtered(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Test that ticket forms list can be filtered by is_active."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        token_tenant1 = generate_token(tenant_id=1, scopes=["helpdesk:read"])

        async with app.app_context():
            db = current_app.db
            db(db.hd_ticket_forms.tenant_id == 1).delete()
            db.commit()

            now = datetime.now(timezone.utc)

            # Create active form
            db.hd_ticket_forms.insert(
                tenant_id=1,
                name="Active Form",
                slug="active-form",
                is_default=False,
                is_active=True,
                captcha_provider="none",
                fields="{}",
                created_at=now,
                updated_at=now,
            )
            # Create inactive form
            db.hd_ticket_forms.insert(
                tenant_id=1,
                name="Inactive Form",
                slug="inactive-form",
                is_default=False,
                is_active=False,
                captcha_provider="none",
                fields="{}",
                created_at=now,
                updated_at=now,
            )
            db.commit()

        # Filter by is_active=true
        response = await async_client.get(
            "/api/v1/ticket-forms?is_active=true",
            headers={"Authorization": f"Bearer {token_tenant1}"},
        )

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert len(data["items"]) == 1
        assert data["items"][0]["is_active"] is True
