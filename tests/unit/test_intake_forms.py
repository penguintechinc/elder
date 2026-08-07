"""Admin CRUD tests for the IntakeForm model (hd_intake_forms).

Covers create + tenant-scoped list, and global slug-uniqueness enforcement,
mirroring the HdTicketForm admin route test pattern.
"""

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


class TestIntakeFormsAdmin:
    """Admin CRUD for /api/v1/intake-forms."""

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_and_list_form(
        self, mock_get_user, async_client, generate_token, app
    ):
        """Admin can create a form; GET lists it scoped to the caller's tenant."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])
        slug = f"support-{uuid4().hex[:8]}"
        resp = await async_client.post(
            "/api/v1/intake-forms",
            json={
                "name": "Support Request",
                "slug": slug,
                "fields": [
                    {
                        "id": "email",
                        "label": "Email",
                        "type": "email",
                        "required": True,
                    },
                    {
                        "id": "subject",
                        "label": "Subject",
                        "type": "text",
                        "required": True,
                    },
                ],
                "is_public": True,
                "captcha_required": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (200, 201), (await resp.get_data()).decode()[:300]

        listing = await async_client.get(
            "/api/v1/intake-forms", headers={"Authorization": f"Bearer {token}"}
        )
        slugs = [f["slug"] for f in json.loads(await listing.get_data())["items"]]
        assert slug in slugs

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_duplicate_slug_conflict(
        self, mock_get_user, async_client, generate_token, app
    ):
        """A second form with the same globally-unique slug is rejected (409/400)."""
        mock_get_user.return_value = MagicMock(id=1, is_superuser=True)
        token = generate_token(tenant_id=1, scopes=["helpdesk:admin"])
        payload = {
            "name": "Dup Form",
            "slug": f"dup-{uuid4().hex[:8]}",
            "fields": [
                {
                    "id": "email",
                    "label": "Email",
                    "type": "email",
                    "required": True,
                }
            ],
        }
        first = await async_client.post(
            "/api/v1/intake-forms",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code in (200, 201), (await first.get_data()).decode()[:300]

        second = await async_client.post(
            "/api/v1/intake-forms",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code in (400, 409), (
            await second.get_data()
        ).decode()[:300]
