"""Tests for tenant scoping on the `issues` table (issues foundation, task 1)."""

import pytest
from quart import current_app


class TestIssuesTenantColumn:
    """Verify the `issues` table carries a `tenant_id` column."""

    @pytest.mark.asyncio
    async def test_issues_table_has_tenant_id(self, app):
        """`issues` must have a `tenant_id` column for tenant scoping.

        The installed penguin-dal `TableProxy` exposes columns via attribute
        access (`db.issues.tenant_id`) and the underlying SQLAlchemy `Table`
        via the `.table` property -- it has no pydal-style `.fields` list, so
        column presence is checked against `db.issues.table.columns`.
        """
        async with app.app_context():
            db = current_app.db
            assert (
                "tenant_id" in db.issues.table.columns
            ), "issues must have a tenant_id column"
