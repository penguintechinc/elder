"""Tests for the `identities.metadata` JSON bag (universal-audits, task 3).

Every object in the universal-audits plan gets a `metadata` JSON bag;
`identities` had `village_id`/`tenant_id` but no `metadata`. Plan 03 (CRM)
needs it to hold customer-contact optional details (phone, location, etc.).
`metadata` is reserved on SQLAlchemy declarative models, so it's mapped via
the differently-named Python attribute `identity_metadata`, exactly like
`Vulnerability.extra_metadata` in `apps/api/models/security.py`.
"""

import pytest
from quart import current_app


class TestIdentityMetadataColumn:
    """Verify the `identities` table carries a `metadata` column."""

    @pytest.mark.asyncio
    async def test_identities_has_metadata(self, app):
        async with app.app_context():
            db = current_app.db
            assert (
                "metadata" in db.identities.table.columns
            ), "identities must have a metadata column"
