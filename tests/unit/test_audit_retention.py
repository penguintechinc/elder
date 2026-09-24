"""
Unit tests for audit log retention auto-enforcement.

Covers AuditService.cleanup_old_logs (retention floor, bounded batching,
per-tenant isolation) and AuditRetentionScheduler.run_once (the automatic
periodic sweep that replaces the manual-admin-trigger-only gap).
"""

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.api.services.audit.scheduler import AuditRetentionScheduler
from apps.api.services.audit.service import AuditService


class _Row(SimpleNamespace):
    """A single fake table row."""


class _Predicate:
    """Minimal PyDAL-query stand-in: wraps a row -> bool predicate."""

    def __init__(self, table: str, fn):
        self.table = table
        self.fn = fn

    def __call__(self, row) -> bool:
        return self.fn(row)

    def __and__(self, other: "_Predicate") -> "_Predicate":
        assert self.table == other.table
        return _Predicate(self.table, lambda row: self.fn(row) and other.fn(row))


class _Field:
    """Minimal PyDAL-field stand-in supporting only what AuditService uses."""

    def __init__(self, table: str, name: str):
        self.table = table
        self.name = name

    def __lt__(self, value) -> _Predicate:
        return _Predicate(self.table, lambda row: getattr(row, self.name) < value)

    def __eq__(self, value) -> _Predicate:  # type: ignore[override]
        return _Predicate(self.table, lambda row: getattr(row, self.name) == value)

    def contains(self, value: dict) -> _Predicate:
        def fn(row) -> bool:
            data = getattr(row, self.name) or {}
            return all(data.get(k) == v for k, v in value.items())

        return _Predicate(self.table, fn)

    def belongs(self, values) -> _Predicate:
        value_set = set(values)
        return _Predicate(self.table, lambda row: getattr(row, self.name) in value_set)


class _Table:
    """Minimal PyDAL-table stand-in backed by a shared, mutable row list."""

    def __init__(self, name: str, rows: list):
        self._name = name
        self.rows = rows  # shared reference; mutated in place on delete
        self.id = _Field(name, "id")
        self.details = _Field(name, "details")
        self.created_at = _Field(name, "created_at")
        self.is_active = _Field(name, "is_active")
        self.data_retention_days = _Field(name, "data_retention_days")

    def __getitem__(self, row_id):
        for row in self.rows:
            if row.id == row_id:
                return row
        return None


class _Set:
    """Result of `db(predicate)` — supports select/delete/count."""

    def __init__(self, table: _Table, matching_rows: list):
        self._table = table
        self._rows = matching_rows

    def select(self, *_fields, limitby=None, **_kwargs):
        rows = self._rows
        if limitby:
            start, end = limitby
            rows = rows[start:end]
        return rows

    def count(self) -> int:
        return len(self._rows)

    def delete(self) -> int:
        for row in self._rows:
            self._table.rows.remove(row)
        return len(self._rows)


class FakeDB:
    """Tiny in-memory PyDAL stand-in covering exactly what retention needs."""

    def __init__(self):
        self._audit_logs: list = []
        self._tenants: list = []
        self.audit_logs = _Table("audit_logs", self._audit_logs)
        self.tenants = _Table("tenants", self._tenants)
        self.commit = MagicMock()

    def __call__(self, predicate: _Predicate) -> _Set:
        if predicate.table == "audit_logs":
            table, rows = self.audit_logs, self._audit_logs
        else:
            table, rows = self.tenants, self._tenants
        matching = [row for row in rows if predicate(row)]
        return _Set(table, matching)

    def add_tenant(self, **kwargs) -> _Row:
        row = _Row(**kwargs)
        self._tenants.append(row)
        return row

    def add_audit_log(self, **kwargs) -> _Row:
        row = _Row(**kwargs)
        self._audit_logs.append(row)
        return row


@pytest.fixture(autouse=True)
def _clear_retention_env(monkeypatch):
    """Ensure each test starts from documented defaults, not leaked env."""
    for name in (
        "AUDIT_RETENTION_MIN_DAYS",
        "AUDIT_RETENTION_BATCH_SIZE",
        "AUDIT_RETENTION_MAX_BATCHES",
        "AUDIT_RETENTION_AUTOENFORCE_ENABLED",
        "AUDIT_RETENTION_CHECK_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


class TestAuditServiceRetentionCleanup:
    """AuditService.cleanup_old_logs: floor, batching, isolation."""

    def test_purges_past_window_keeps_within_window(self):
        db = FakeDB()
        db.add_tenant(id=1, data_retention_days=90, is_active=True)
        now = datetime.datetime.now(datetime.UTC)

        db.add_audit_log(
            id=1, details={"tenant_id": 1}, created_at=now - datetime.timedelta(days=91)
        )
        db.add_audit_log(
            id=2, details={"tenant_id": 1}, created_at=now - datetime.timedelta(days=10)
        )

        result = AuditService.cleanup_old_logs(1, db=db)

        assert result["deleted_count"] == 1
        assert result["retention_days"] == 90
        assert {row.id for row in db._audit_logs} == {2}

    def test_enforces_minimum_retention_floor(self, monkeypatch):
        monkeypatch.setenv("AUDIT_RETENTION_MIN_DAYS", "30")
        db = FakeDB()
        # Tenant configured well below the compliance floor.
        db.add_tenant(id=1, data_retention_days=5, is_active=True)
        now = datetime.datetime.now(datetime.UTC)

        # 10 days old: past the tenant's 5-day setting but within the 30-day
        # floor -- must survive.
        db.add_audit_log(
            id=1, details={"tenant_id": 1}, created_at=now - datetime.timedelta(days=10)
        )

        result = AuditService.cleanup_old_logs(1, db=db)

        assert result["retention_days"] == 30
        assert result["configured_retention_days"] == 5
        assert result["deleted_count"] == 0
        assert len(db._audit_logs) == 1

    def test_per_tenant_isolation(self):
        db = FakeDB()
        db.add_tenant(id=1, data_retention_days=30, is_active=True)
        db.add_tenant(id=2, data_retention_days=30, is_active=True)
        now = datetime.datetime.now(datetime.UTC)

        db.add_audit_log(
            id=1, details={"tenant_id": 1}, created_at=now - datetime.timedelta(days=40)
        )
        db.add_audit_log(
            id=2, details={"tenant_id": 2}, created_at=now - datetime.timedelta(days=40)
        )

        result = AuditService.cleanup_old_logs(1, db=db)

        assert result["deleted_count"] == 1
        remaining = {(row.id, row.details["tenant_id"]) for row in db._audit_logs}
        assert remaining == {(2, 2)}

    def test_bounded_batch_delete_runs_multiple_batches(self):
        db = FakeDB()
        db.add_tenant(id=1, data_retention_days=30, is_active=True)
        now = datetime.datetime.now(datetime.UTC)

        for i in range(5):
            db.add_audit_log(
                id=i + 1,
                details={"tenant_id": 1},
                created_at=now - datetime.timedelta(days=40),
            )

        result = AuditService.cleanup_old_logs(1, db=db, batch_size=2, max_batches=10)

        assert result["deleted_count"] == 5
        assert result["batches_run"] == 3  # 2 + 2 + 1
        assert result["truncated"] is False
        assert len(db._audit_logs) == 0

    def test_batch_cap_is_safety_bounded_and_idempotent(self):
        db = FakeDB()
        db.add_tenant(id=1, data_retention_days=30, is_active=True)
        now = datetime.datetime.now(datetime.UTC)

        for i in range(5):
            db.add_audit_log(
                id=i + 1,
                details={"tenant_id": 1},
                created_at=now - datetime.timedelta(days=40),
            )

        # Cap at 2 batches of 2 -> only 4 of 5 purged on the first call.
        first = AuditService.cleanup_old_logs(1, db=db, batch_size=2, max_batches=2)
        assert first["deleted_count"] == 4
        assert first["batches_run"] == 2
        assert first["truncated"] is True
        assert len(db._audit_logs) == 1

        # Idempotent: calling again finishes the remainder without error
        # or double-deleting anything.
        second = AuditService.cleanup_old_logs(1, db=db, batch_size=2, max_batches=2)
        assert second["deleted_count"] == 1
        assert second["truncated"] is False
        assert len(db._audit_logs) == 0

    def test_missing_tenant_returns_error(self):
        db = FakeDB()
        result = AuditService.cleanup_old_logs(999, db=db)
        assert result == {"error": "Tenant not found"}

    def test_defaults_used_when_tenant_has_no_retention_configured(self):
        db = FakeDB()
        db.add_tenant(id=1, data_retention_days=None, is_active=True)
        now = datetime.datetime.now(datetime.UTC)

        db.add_audit_log(
            id=1,
            details={"tenant_id": 1},
            created_at=now
            - datetime.timedelta(days=AuditService.DEFAULT_RETENTION_DAYS + 1),
        )

        result = AuditService.cleanup_old_logs(1, db=db)

        assert result["retention_days"] == AuditService.DEFAULT_RETENTION_DAYS
        assert result["deleted_count"] == 1


class TestAuditRetentionScheduler:
    """AuditRetentionScheduler: periodic sweep, isolation, feature flag."""

    def test_run_once_purges_active_tenants_with_isolation(self):
        db = FakeDB()
        db.add_tenant(id=1, data_retention_days=30, is_active=True)
        db.add_tenant(id=2, data_retention_days=30, is_active=True)
        db.add_tenant(id=3, data_retention_days=30, is_active=False)
        now = datetime.datetime.now(datetime.UTC)

        db.add_audit_log(
            id=1, details={"tenant_id": 1}, created_at=now - datetime.timedelta(days=40)
        )
        db.add_audit_log(
            id=2, details={"tenant_id": 2}, created_at=now - datetime.timedelta(days=10)
        )
        db.add_audit_log(
            id=3, details={"tenant_id": 3}, created_at=now - datetime.timedelta(days=40)
        )

        scheduler = AuditRetentionScheduler(db)
        summary = scheduler.run_once()

        # Only the two active tenants are swept.
        assert summary["tenants_checked"] == 2
        assert summary["tenants_purged"] == 1
        assert summary["total_deleted"] == 1

        remaining_ids = {row.id for row in db._audit_logs}
        # Tenant 1's stale record purged; tenant 2's recent record kept;
        # tenant 3 (inactive) never touched.
        assert remaining_ids == {2, 3}

    def test_disabled_by_env_var_does_not_start_thread(self, monkeypatch):
        monkeypatch.setenv("AUDIT_RETENTION_AUTOENFORCE_ENABLED", "false")
        db = FakeDB()
        scheduler = AuditRetentionScheduler(db)

        scheduler.start()

        assert scheduler.enabled is False
        assert scheduler._thread is None

    def test_enabled_by_default(self):
        db = FakeDB()
        scheduler = AuditRetentionScheduler(db)

        assert scheduler.enabled is True

    def test_one_tenant_failure_does_not_block_others(self, monkeypatch):
        db = FakeDB()
        db.add_tenant(id=1, data_retention_days=30, is_active=True)
        db.add_tenant(id=2, data_retention_days=30, is_active=True)
        now = datetime.datetime.now(datetime.UTC)

        db.add_audit_log(
            id=2, details={"tenant_id": 2}, created_at=now - datetime.timedelta(days=40)
        )

        original_cleanup = AuditService.cleanup_old_logs

        def flaky_cleanup(tenant_id, **kwargs):
            if tenant_id == 1:
                raise RuntimeError("simulated DB error for tenant 1")
            return original_cleanup(tenant_id, **kwargs)

        monkeypatch.setattr(
            AuditService, "cleanup_old_logs", staticmethod(flaky_cleanup)
        )

        scheduler = AuditRetentionScheduler(db)
        summary = scheduler.run_once()

        # Tenant 1 failed but tenant 2 still got purged.
        assert summary["tenants_checked"] == 2
        assert summary["total_deleted"] == 1
        assert len(db._audit_logs) == 0
