"""Tests for dashboard service - get_dashboard_stats aggregates.

Parity tests against Ruffled's dashboard analytics with golden stat values.

NOTE: Integration tests marked with @pytest.mark.integration require:
- DATABASE_URL environment variable
- Full Quart app setup with all dependencies (quart_cors, etc.)
- Running test PostgreSQL instance

To run integration tests: pytest -m integration

Unit tests (pure algorithm) can run without these dependencies.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
import pytest_asyncio

from apps.api.modules.helpdesk.services.dashboard import get_dashboard_stats


@pytest.mark.integration  # Skip unless -m integration specified
class TestDashboardIntegration:
    """Integration tests for dashboard stats against real test database."""

    @pytest_asyncio.fixture(scope="class")
    async def setup_test_data(self, app, test_database_url):
        """Set up test tickets with various statuses and priorities."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        import time

        from apps.api.utils.async_utils import run_in_threadpool

        async def setup():
            def do_setup():
                db = app.db
                unique_suffix = int(time.time() * 1000000) % 1000000

                # Create test tenant (unique slug per test)
                tenant_id = db.tenants.insert(
                    name=f"Test Helpdesk Tenant {unique_suffix}",
                    slug=f"test-hd-tenant-{unique_suffix}",
                    is_active=True,
                )

                # Create test identity
                identity_id = db.identities.insert(
                    tenant_id=tenant_id,
                    username="test_hd_user",
                    email="test@helpdesk.local",
                    identity_type="human",
                    auth_provider="local",
                    is_active=True,
                    is_superuser=False,
                    # penguin-dal (pyDAL) inserts do NOT apply SQLAlchemy
                    # Python-side defaults; set nullable=False cols explicitly.
                    mfa_enabled=False,
                    must_change_password=False,
                    portal_role="observer",
                )

                now = datetime.now(UTC)
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

                # Create SLA policy for compliance testing
                policy_id = db.hd_sla_policies.insert(
                    tenant_id=tenant_id,
                    name="Test SLA",
                    priority="high",
                    first_response_hours=2,
                    resolution_hours=8,
                    business_hours_only=False,
                    is_active=True,
                )

                # Tickets for count testing
                tickets = []

                # Status: new (1)
                tickets.append(
                    db.hd_tickets.insert(
                        tenant_id=tenant_id,
                        subject="New ticket 1",
                        status="new",
                        priority="medium",
                        channel="web",
                        requester_identity_id=identity_id,
                        created_at=today_start - timedelta(hours=2),
                    )
                )

                # Status: open (2)
                tickets.append(
                    db.hd_tickets.insert(
                        tenant_id=tenant_id,
                        subject="Open ticket 1",
                        status="open",
                        priority="high",
                        channel="email",
                        requester_identity_id=identity_id,
                        created_at=today_start - timedelta(hours=1),
                    )
                )
                tickets.append(
                    db.hd_tickets.insert(
                        tenant_id=tenant_id,
                        subject="Open ticket 2",
                        status="open",
                        priority="low",
                        channel="web",
                        requester_identity_id=identity_id,
                        created_at=today_start - timedelta(hours=3),
                    )
                )

                # Status: pending (1)
                tickets.append(
                    db.hd_tickets.insert(
                        tenant_id=tenant_id,
                        subject="Pending ticket 1",
                        status="pending",
                        priority="medium",
                        channel="web",
                        requester_identity_id=identity_id,
                        created_at=today_start - timedelta(hours=5),
                    )
                )

                # Status: on_hold (1)
                tickets.append(
                    db.hd_tickets.insert(
                        tenant_id=tenant_id,
                        subject="On hold ticket 1",
                        status="on_hold",
                        priority="low",
                        channel="web",
                        requester_identity_id=identity_id,
                        created_at=today_start - timedelta(hours=10),
                    )
                )

                # Status: resolved (2) - for avg resolution and SLA compliance
                resolved_1_created = today_start - timedelta(hours=4)
                resolved_1_resolved = resolved_1_created + timedelta(
                    hours=3
                )  # 3 hours to resolve
                tickets.append(
                    db.hd_tickets.insert(
                        tenant_id=tenant_id,
                        subject="Resolved ticket 1 (compliant)",
                        status="resolved",
                        priority="high",
                        channel="web",
                        requester_identity_id=identity_id,
                        hd_sla_policy_id=policy_id,
                        sla_breach_at=resolved_1_created + timedelta(hours=8),
                        created_at=resolved_1_created,
                        resolved_at=resolved_1_resolved,
                        closed_at=resolved_1_resolved,
                    )
                )

                resolved_2_created = today_start - timedelta(hours=12)
                resolved_2_resolved = resolved_2_created + timedelta(
                    hours=10
                )  # 10 hours to resolve
                tickets.append(
                    db.hd_tickets.insert(
                        tenant_id=tenant_id,
                        subject="Resolved ticket 2 (compliant)",
                        status="resolved",
                        priority="medium",
                        channel="email",
                        requester_identity_id=identity_id,
                        hd_sla_policy_id=policy_id,
                        sla_breach_at=resolved_2_created + timedelta(hours=24),
                        created_at=resolved_2_created,
                        resolved_at=resolved_2_resolved,
                        closed_at=resolved_2_resolved,
                    )
                )

                # Status: closed (1)
                tickets.append(
                    db.hd_tickets.insert(
                        tenant_id=tenant_id,
                        subject="Closed ticket 1",
                        status="closed",
                        priority="low",
                        channel="web",
                        requester_identity_id=identity_id,
                        created_at=today_start - timedelta(hours=24),
                    )
                )

                # Ticket created today (for new_today count)
                tickets.append(
                    db.hd_tickets.insert(
                        tenant_id=tenant_id,
                        subject="Created today",
                        status="new",
                        priority="urgent",
                        channel="api",
                        requester_identity_id=identity_id,
                        created_at=today_start + timedelta(hours=2),
                    )
                )

                # Priority distribution
                # high: 2 (one open, one resolved)
                # medium: 3 (one new, one pending, one resolved)
                # low: 3 (two open, one on_hold, one closed)
                # urgent: 1 (today ticket)

                db.commit()
                return {
                    "tenant_id": tenant_id,
                    "identity_id": identity_id,
                    "policy_id": policy_id,
                    "tickets": tickets,
                    "now": now,
                    "today_start": today_start,
                }

            return await run_in_threadpool(do_setup)

        return await setup()

    @pytest.mark.asyncio
    async def test_get_dashboard_stats_returns_all_keys(self, app, setup_test_data):
        """Test get_dashboard_stats returns all expected keys."""
        data = setup_test_data
        stats = await get_dashboard_stats(app.db, data["tenant_id"])

        expected_keys = {
            "total_tickets",
            "open_tickets",
            "resolved_tickets",
            "new_today",
            "by_status",
            "by_priority",
            "avg_resolution_hours",
            "sla_compliance_percent",
            "timestamp",
        }

        assert set(stats.keys()) == expected_keys
        assert isinstance(stats["timestamp"], str)

    @pytest.mark.asyncio
    async def test_get_dashboard_stats_count_totals(self, app, setup_test_data):
        """Test dashboard stats count totals match test data."""
        data = setup_test_data
        stats = await get_dashboard_stats(app.db, data["tenant_id"])

        # Total: 9 tickets
        # new: 2 (one regular, one today)
        # open: 2
        # pending: 1
        # on_hold: 1
        # resolved: 2
        # closed: 1
        total = 2 + 2 + 1 + 1 + 2 + 1
        assert stats["total_tickets"] == total
        assert stats["open_tickets"] == 2 + 2 + 1 + 1  # new, open, pending, on_hold
        assert stats["resolved_tickets"] == 2
        assert stats["new_today"] == 1

    @pytest.mark.asyncio
    async def test_get_dashboard_stats_by_status_breakdown(self, app, setup_test_data):
        """Test by_status dict contains all statuses with correct counts."""
        data = setup_test_data
        stats = await get_dashboard_stats(app.db, data["tenant_id"])

        assert stats["by_status"]["new"] == 2
        assert stats["by_status"]["open"] == 2
        assert stats["by_status"]["pending"] == 1
        assert stats["by_status"]["on_hold"] == 1
        assert stats["by_status"]["resolved"] == 2
        assert stats["by_status"]["closed"] == 1

    @pytest.mark.asyncio
    async def test_get_dashboard_stats_by_priority_breakdown(
        self, app, setup_test_data
    ):
        """Test by_priority dict contains all priorities with correct counts."""
        data = setup_test_data
        stats = await get_dashboard_stats(app.db, data["tenant_id"])

        # high: 2, medium: 3, low: 3, urgent: 1, critical: 0
        assert stats["by_priority"]["high"] == 2
        assert stats["by_priority"]["medium"] == 3
        assert stats["by_priority"]["low"] == 3
        assert stats["by_priority"]["urgent"] == 1
        assert stats["by_priority"]["critical"] == 0

    @pytest.mark.asyncio
    async def test_get_dashboard_stats_avg_resolution_hours(self, app, setup_test_data):
        """Test avg_resolution_hours calculation.

        Two resolved tickets:
        - Ticket 1: 3 hours (3 * 3600 = 10800 seconds)
        - Ticket 2: 10 hours (10 * 3600 = 36000 seconds)
        Average: (10800 + 36000) / 2 = 23400 seconds = 6.5 hours
        """
        data = setup_test_data
        stats = await get_dashboard_stats(app.db, data["tenant_id"])

        # Expected: 6.5 hours (3 + 10) / 2
        assert stats["avg_resolution_hours"] == 6.5

    @pytest.mark.asyncio
    async def test_get_dashboard_stats_sla_compliance_percent(
        self, app, setup_test_data
    ):
        """Test SLA compliance percentage.

        Only 2 tickets have sla_breach_at set:
        - Ticket 1: resolved_at (1h ago) <= sla_breach_at (+8h) -> compliant
        - Ticket 2: resolved_at (2h ago) <= sla_breach_at (+24h) -> compliant
        Compliance: 2/2 = 100%
        """
        data = setup_test_data
        stats = await get_dashboard_stats(app.db, data["tenant_id"])

        # Both resolved tickets with SLA are compliant
        assert stats["sla_compliance_percent"] == 100.0

    @pytest.mark.asyncio
    async def test_get_dashboard_stats_no_tickets_with_tenant(self, app):
        """Test dashboard stats with no tickets returns zeros."""
        from apps.api.utils.async_utils import run_in_threadpool

        # Create an empty tenant with no tickets
        def create_empty_tenant():
            db = app.db
            empty_tenant_id = db.tenants.insert(
                name="Empty Tenant",
                slug="empty-tenant",
                is_active=True,
            )
            db.commit()
            return empty_tenant_id

        empty_tenant_id = await run_in_threadpool(create_empty_tenant)

        # Get stats for empty tenant
        stats = await get_dashboard_stats(app.db, empty_tenant_id)

        assert stats["total_tickets"] == 0
        assert stats["open_tickets"] == 0
        assert stats["resolved_tickets"] == 0
        assert stats["new_today"] == 0
        assert stats["avg_resolution_hours"] is None
        assert stats["sla_compliance_percent"] == 0.0
        assert all(count == 0 for count in stats["by_status"].values())
        assert all(count == 0 for count in stats["by_priority"].values())
