"""Integration tests for the audit-retention purge against real Postgres.

`AuditService.cleanup_old_logs` (used by both the manual admin endpoint and
`AuditRetentionScheduler`) scopes its purge query with
`db.audit_logs.details.contains({"tenant_id": tenant_id})`. `details` is a
plain SQLAlchemy `JSON` (not `JSONB`) column, and penguin-dal's generic
`FieldProxy.contains()` degrades to `ILIKE` for every field type it accepts
-- which Postgres rejects on a native `json` column (`operator does not
exist: json ~~* unknown`). `AuditRetentionScheduler.run_once()` catches and
logs that exception per tenant per sweep, so the purge silently never ran
against Postgres in production; the feature was inert.

Every prior test for this path (`tests/unit/test_audit_retention.py`) used a
hand-rolled in-memory DB double whose own `.contains()` stand-in never
generates real SQL, so it could not catch this. These tests exercise the
real penguin-dal -> SQLAlchemy -> Postgres path and require `DATABASE_URL`
to point at a live test Postgres instance (see `tests/conftest.py`).
"""

import datetime
import uuid

import pytest
from sqlalchemy.exc import ProgrammingError

from apps.api.services.audit.service import AuditService


class TestAuditRetentionPurgePostgres:
    """`AuditService.cleanup_old_logs` against a real Postgres `audit_logs`."""

    @pytest.fixture
    def tenant_id(self, app):
        """Create a throwaway tenant, and purge any audit logs it leaves behind."""
        db = app.db
        new_id = db.tenants.insert(
            name="Audit Retention PG Test",
            slug=f"audit-retention-pg-{uuid.uuid4().hex[:12]}",
            is_active=True,
            data_retention_days=90,
        )

        yield new_id

        db(AuditService._tenant_details_query(db, new_id)).delete()
        db(db.tenants.id == new_id).delete()

    def test_purge_deletes_only_aged_rows_for_tenant(self, app, tenant_id):
        """The purge deletes rows past the retention window and keeps recent ones."""
        db = app.db
        now = datetime.datetime.now(datetime.UTC)

        aged_id = db.audit_logs.insert(
            details={"tenant_id": tenant_id},
            created_at=now - datetime.timedelta(days=100),
            action_name="test.aged",
            success=True,
        )
        recent_id = db.audit_logs.insert(
            details={"tenant_id": tenant_id},
            created_at=now - datetime.timedelta(days=5),
            action_name="test.recent",
            success=True,
        )

        result = AuditService.cleanup_old_logs(tenant_id, db=db)

        assert "error" not in result
        assert result["deleted_count"] == 1
        assert result["tenant_id"] == tenant_id

        remaining = db(db.audit_logs.id.belongs([aged_id, recent_id])).select(
            db.audit_logs.id
        )
        assert {row.id for row in remaining} == {recent_id}

    def test_purge_does_not_delete_other_tenants_rows(self, app, tenant_id):
        """Per-tenant isolation holds against the real JSON-keyed predicate."""
        db = app.db
        now = datetime.datetime.now(datetime.UTC)

        other_tenant_id = db.tenants.insert(
            name="Other Tenant",
            slug=f"audit-retention-pg-other-{uuid.uuid4().hex[:12]}",
            is_active=True,
            data_retention_days=90,
        )
        try:
            own_aged_id = db.audit_logs.insert(
                details={"tenant_id": tenant_id},
                created_at=now - datetime.timedelta(days=100),
                action_name="test.aged",
                success=True,
            )
            other_aged_id = db.audit_logs.insert(
                details={"tenant_id": other_tenant_id},
                created_at=now - datetime.timedelta(days=100),
                action_name="test.aged",
                success=True,
            )

            result = AuditService.cleanup_old_logs(tenant_id, db=db)

            assert result["deleted_count"] == 1

            remaining = db(
                db.audit_logs.id.belongs([own_aged_id, other_aged_id])
            ).select(db.audit_logs.id)
            assert {row.id for row in remaining} == {other_aged_id}
        finally:
            db(AuditService._tenant_details_query(db, other_tenant_id)).delete()
            db(db.tenants.id == other_tenant_id).delete()

    def test_json_contains_fallback_is_invalid_sql_on_postgres(self, app, tenant_id):
        """Regression guard for the original defect.

        `details` is a plain `json` column, so penguin-dal's generic
        `FieldProxy.contains()` degrades to `ILIKE` regardless of field type,
        which Postgres rejects for `json`. `_tenant_details_query` must never
        go back to calling `.contains()` for a real penguin-dal `FieldProxy`
        -- if this test stops raising, that regression has been reintroduced.
        """
        db = app.db

        with pytest.raises(ProgrammingError, match="json"):
            db(db.audit_logs.details.contains({"tenant_id": tenant_id})).select(
                db.audit_logs.id
            )
