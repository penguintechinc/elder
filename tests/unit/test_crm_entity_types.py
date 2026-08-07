"""Tests for CRM entity identity types (Plan 03, task 1).

Ruffled CRM customer contacts are represented natively as `identities` rows
with `identity_type="customer_contact"`. Optional contact details (phone,
location, etc.) live in the `identities.metadata` JSON bag added in Plan 02
(see `tests/unit/test_identity_metadata.py`).
"""

import pytest
from datetime import datetime, timezone
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
            iid = db.identities.insert(
                username="cust@example.com", email="cust@example.com",
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
