"""Tests for CRM entity identity types (Plan 03, task 1).

Ruffled CRM customer contacts are represented natively as `identities` rows
with `identity_type="customer_contact"`. Optional contact details (phone,
location, etc.) live in the `identities.metadata` JSON bag added in Plan 02
(see `tests/unit/test_identity_metadata.py`).
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


class TestCrmEntityTypes:
    """Verify the `customer_contact` identity type round-trips."""

    @pytest.mark.asyncio
    async def test_create_customer_contact_identity(self, app):
        async with app.app_context():
            db = current_app.db
            now = datetime.now(timezone.utc)
            # penguin-dal's insert() does not apply SQLAlchemy Column
            # `default=` values (see other db.identities.insert() call
            # sites, e.g. tests/unit/test_api_diagram_collab.py) -- every
            # NOT NULL column without a server_default must be passed
            # explicitly.
            unique_email = f"cust-{uuid4().hex[:8]}@example.com"
            iid = db.identities.insert(
                username=unique_email, email=unique_email,
                identity_type="customer_contact", tenant_id=1,
                auth_provider="local", is_active=True, is_superuser=False,
                mfa_enabled=False, must_change_password=False,
                portal_role="observer",
                metadata={"phone": "+1-555-0100"},
                created_at=now, updated_at=now,
            )
            db.commit()
            row = db.identities[iid]
            assert row.identity_type == "customer_contact"
            assert row.metadata["phone"] == "+1-555-0100"

    @pytest.mark.asyncio
    @patch("apps.api.auth.decorators.get_current_user")
    async def test_create_customer_company_org(self, mock_get_user, async_client):
        """POST /api/v1/organizations accepts organization_type="customer_company".

        The live create-org route is
        apps.api.modules.infrastructure.routes.organizations_pydal.create_organization
        (registered via the "infrastructure" module manifest) -- NOT
        apps/api/api/v1/organizations.py, which is unregistered dead code.
        It is guarded by @require_scope("infrastructure:write"), bypassed
        here via an is_superuser mock, mirroring
        tests/unit/test_api_organizations.py::test_create_organization.
        tenant_id must be set explicitly on the mock: the route falls back
        to `getattr(g.current_user, "tenant_id", 1)`, and an unconfigured
        MagicMock attribute is truthy (not the int default), which would
        otherwise break the insert.
        """
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.tenant_id = 1
        mock_user.is_superuser = True
        mock_get_user.return_value = mock_user

        resp = await async_client.post(
            "/api/v1/organizations",
            json={"name": "Acme Corp", "organization_type": "customer_company"},
            headers={"Authorization": "Bearer fake-token"},
        )
        assert resp.status_code == 201, (await resp.get_data()).decode()[:200]
        data = json.loads(await resp.get_data())
        assert data["type"] == "customer_company"

    def test_customer_company_org_type_validation_surfaces(self):
        """`customer_company` is accepted by both declared org-type validators.

        The live create-org route's pydantic body model
        (CreateOrganizationRequest.organization_type: str) does not actually
        enforce OrganizationType/OneOf today -- confirmed by re-running
        test_create_customer_company_org above with this task's source edits
        reverted; it still returns 201. This test instead exercises the two
        validation surfaces this task is responsible for keeping in sync
        (models/pydantic/organization.py's OrganizationType Literal and both
        marshmallow OneOf lists in schemas/organization.py), so it actually
        fails before those edits and passes after.
        """
        from typing import get_args

        from apps.api.models.pydantic.organization import OrganizationType
        from apps.api.schemas.organization import (
            OrganizationCreateSchema,
            OrganizationUpdateSchema,
        )

        assert "customer_company" in get_args(OrganizationType)

        create_errors = OrganizationCreateSchema().validate(
            {
                "name": "Acme Corp",
                "tenant_id": 1,
                "organization_type": "customer_company",
            }
        )
        assert create_errors == {}

        update_errors = OrganizationUpdateSchema().validate(
            {"organization_type": "customer_company"}
        )
        assert update_errors == {}
