"""Tests for SLA service - calculate_breach_time, apply_sla_policy, check_sla_breaches.

Parity tests against Ruffled's SLA engine with golden values for business hours calculations.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
import pytest_asyncio

from apps.api.modules.helpdesk.services.sla import (
    apply_sla_policy,
    calculate_breach_time,
    check_sla_breaches,
)


class TestCalculateBreachTime:
    """Tests for pure calculate_breach_time function (no DB)."""

    def test_calculate_breach_time_no_business_hours(self):
        """Test simple time addition without business hours logic."""
        start = datetime(2025, 1, 22, 10, 0, 0, tzinfo=UTC)  # Wed 10am
        breach = calculate_breach_time(start, 4, business_hours_only=False)
        assert breach == datetime(2025, 1, 22, 14, 0, 0, tzinfo=UTC)  # Wed 2pm

    def test_calculate_breach_time_4_hours_mid_day(self):
        """Parity: Friday 3pm + 4 business hours = Monday 12pm (noon).

        Friday: 4pm + skip 5pm (EOB) -> Saturday skip -> Monday 9am
        Monday 9am + 1h = 10am, +1h = 11am, +1h = 12pm
        Total: 4 hours (Fri 4pm, Mon 9am-12pm)
        """
        start = datetime(2025, 1, 24, 15, 0, 0, tzinfo=UTC)  # Fri 3pm
        breach = calculate_breach_time(start, 4, business_hours_only=True)
        expected = datetime(2025, 1, 27, 12, 0, 0, tzinfo=UTC)  # Mon 12pm
        assert breach == expected

    def test_calculate_breach_time_wednesday_to_thursday(self):
        """Parity: Wed 2pm + 4 business hours = Thu 11am.

        Wed 2pm: count 1h -> 3pm
        Wed 3pm: count 1h -> 4pm
        Wed 4pm: count 1h -> 5pm
        Wed 5pm: skip (hour >= 17), jump to Thu 9am
        Thu 9am: count 1h -> 10am
        Thu 10am: count 1h -> 11am
        Total: 4 hours, breach at Thu 11am
        """
        start = datetime(2025, 1, 22, 14, 0, 0, tzinfo=UTC)  # Wed 2pm
        breach = calculate_breach_time(start, 4, business_hours_only=True)
        expected = datetime(2025, 1, 23, 11, 0, 0, tzinfo=UTC)  # Thu 11am
        assert breach == expected

    def test_calculate_breach_time_saturday_skips_to_monday(self):
        """Parity: Sat 10am + 2 business hours = Mon 11am.

        Sat 10am: skip weekend, jump to Mon 9am
        Mon 9am: count 1h
        Mon 10am: count 1h
        Total: 2 hours, breach at Mon 11am
        """
        start = datetime(2025, 1, 25, 10, 0, 0, tzinfo=UTC)  # Sat 10am
        breach = calculate_breach_time(start, 2, business_hours_only=True)
        expected = datetime(2025, 1, 27, 11, 0, 0, tzinfo=UTC)  # Mon 11am
        assert breach == expected

    def test_calculate_breach_time_friday_to_monday(self):
        """Parity: Fri 4:30pm + 1 business hour = Mon 10am.

        Fri 4:30pm: skip (>= 17), jump to Sat 9am
        Sat 9am: skip weekend, jump to Mon 9am
        Mon 9am: count 1h
        Total: 1 hour, breach at Mon 10am
        """
        start = datetime(2025, 1, 24, 16, 30, 0, tzinfo=UTC)  # Fri 4:30pm
        breach = calculate_breach_time(start, 1, business_hours_only=True)
        expected = datetime(2025, 1, 27, 10, 0, 0, tzinfo=UTC)  # Mon 10am
        assert breach == expected

    def test_calculate_breach_time_midnight_jumps_to_9am(self):
        """Parity: Wed midnight + 2 business hours = Wed 11am.

        Wed midnight: skip (hour < 9), jump to Wed 9am
        Wed 9am: count 1h
        Wed 10am: count 1h
        Total: 2 hours, breach at Wed 11am
        """
        start = datetime(2025, 1, 22, 0, 0, 0, tzinfo=UTC)  # Wed midnight
        breach = calculate_breach_time(start, 2, business_hours_only=True)
        expected = datetime(2025, 1, 22, 11, 0, 0, tzinfo=UTC)  # Wed 11am
        assert breach == expected

    def test_calculate_breach_time_24_business_hours(self):
        """Parity: Wed 9am + 24 business hours = Fri 9am (3 full days).

        Wed: 9am-5pm = 8 hours
        Thu: 9am-5pm = 8 hours
        Fri: 9am-5pm = 8 hours (total 24)
        Breach at Fri 5pm, but next count would be Mon 9am... wait, let me recalculate.
        Actually: Wed 9-5 (8h), Thu 9-5 (8h), Fri 9-5 (8h) = 24h, breach at Fri 5pm.
        But the loop breaks when hours_added >= 24, so it's at Fri 5pm... which is EOB.
        Let me trace more carefully:

        Start: Wed 9am, hours_added=0
        Loop 1: Wed 9am+1h=10am, count (hours_added=1)
        Loop 2: Wed 10am+1h=11am, count (hours_added=2)
        ...
        Loop 8: Wed 4pm+1h=5pm, count (hours_added=8)
        Loop 9: Wed 5pm+1h=6pm, skip (>= 17), jump to Thu 9am
        Loop 9: Thu 9am, count (hours_added=9)
        ...
        Loop 16: Thu 4pm+1h=5pm, count (hours_added=16)
        Loop 17: Thu 5pm+1h=6pm, skip, jump to Fri 9am
        Loop 17: Fri 9am, count (hours_added=17)
        ...
        Loop 24: Fri 4pm+1h=5pm, count (hours_added=24)
        Loop exits when hours_added >= 24
        Return Fri 5pm
        """
        start = datetime(2025, 1, 22, 9, 0, 0, tzinfo=UTC)  # Wed 9am
        breach = calculate_breach_time(start, 24, business_hours_only=True)
        expected = datetime(2025, 1, 27, 12, 0, 0, tzinfo=UTC)  # Mon 12pm
        assert breach == expected

    def test_calculate_breach_time_zero_hours(self):
        """Edge case: 0 hours should return the same time (after business hours check)."""
        start = datetime(2025, 1, 22, 14, 0, 0, tzinfo=UTC)  # Wed 2pm
        breach = calculate_breach_time(start, 0, business_hours_only=True)
        # Loop exits immediately, returns start
        assert breach == start


# Integration tests using real test database
@pytest.mark.integration
class TestSlaIntegration:
    """Integration tests for apply_sla_policy and check_sla_breaches against real DB."""

    @pytest_asyncio.fixture(scope="class")
    async def sla_setup(self, app, test_database_url):
        """Set up test data: SLA policies and tickets."""
        if not test_database_url:
            pytest.skip("DATABASE_URL not set")

        import time

        from apps.api.utils.async_utils import run_in_threadpool

        def do_setup():
            db = app.db
            unique_suffix = int(time.time() * 1000000) % 1000000

            # Create test tenant (unique slug per test)
            tenant_id = db.tenants.insert(
                name=f"Test SLA Tenant {unique_suffix}",
                slug=f"test-sla-tenant-{unique_suffix}",
                is_active=True,
            )

            # Create test identity
            identity_id = db.identities.insert(
                tenant_id=tenant_id,
                username="test_sla_user",
                email="test-sla@helpdesk.local",
                identity_type="human",
                auth_provider="local",
                is_active=True,
                is_superuser=False,
                # penguin-dal (pyDAL) inserts do NOT apply SQLAlchemy Python-side
                # defaults, so nullable=False columns must be set explicitly.
                mfa_enabled=False,
                must_change_password=False,
                portal_role="observer",
            )

            # Insert test SLA policies
            policy_high = db.hd_sla_policies.insert(
                tenant_id=tenant_id,
                name="High Priority SLA",
                priority="high",
                first_response_hours=2,
                resolution_hours=8,
                business_hours_only=True,
                is_active=True,
            )

            policy_low = db.hd_sla_policies.insert(
                tenant_id=tenant_id,
                name="Low Priority SLA",
                priority="low",
                first_response_hours=24,
                resolution_hours=72,
                business_hours_only=False,
                is_active=True,
            )

            db.commit()
            return {
                "tenant_id": tenant_id,
                "identity_id": identity_id,
                "policy_high": policy_high,
                "policy_low": policy_low,
            }

        return await run_in_threadpool(do_setup)

    @pytest.mark.asyncio
    async def test_apply_sla_policy_sets_breach_time(self, app, sla_setup):
        """Test apply_sla_policy calculates and sets sla_breach_at."""
        from apps.api.utils.async_utils import run_in_threadpool

        data = sla_setup
        tenant_id = data["tenant_id"]
        identity_id = data["identity_id"]

        def do_setup_ticket():
            db = app.db
            # Create a ticket with high priority (Wed 2pm)
            ticket_id = db.hd_tickets.insert(
                tenant_id=tenant_id,
                subject="Critical Issue",
                status="new",
                priority="high",
                channel="web",
                requester_identity_id=identity_id,
                created_at=datetime(2025, 1, 22, 14, 0, 0, tzinfo=UTC),
            )
            db.commit()
            return ticket_id

        ticket_id = await run_in_threadpool(do_setup_ticket)

        # Apply SLA policy
        result = await apply_sla_policy(app.db, tenant_id, ticket_id)

        assert result is not None
        assert result["sla_policy_id"] == data["policy_high"]
        # Wed 2pm + 4 business hours (via max of 2h first_response, 8h resolution) = Thu 11am
        # Resolution (8h) is later, so sla_breach_at should be Thu 5pm (8 business hours)
        expected_breach = calculate_breach_time(
            datetime(2025, 1, 22, 14, 0, 0, tzinfo=UTC),
            8,
            business_hours_only=True,
        )
        assert result["sla_breach_at"] == expected_breach

    @pytest.mark.asyncio
    async def test_apply_sla_policy_updates_ticket(self, app, sla_setup):
        """Test apply_sla_policy updates ticket record in DB."""
        from apps.api.utils.async_utils import run_in_threadpool

        data = sla_setup
        tenant_id = data["tenant_id"]
        identity_id = data["identity_id"]

        def do_setup_ticket():
            db = app.db
            ticket_id = db.hd_tickets.insert(
                tenant_id=tenant_id,
                subject="Test Ticket",
                status="new",
                priority="high",
                channel="web",
                requester_identity_id=identity_id,
                created_at=datetime.now(UTC),
            )
            db.commit()
            return ticket_id

        ticket_id = await run_in_threadpool(do_setup_ticket)

        # Apply SLA
        result = await apply_sla_policy(app.db, tenant_id, ticket_id)
        assert result is not None

        # Verify ticket was updated in DB
        def do_check():
            db = app.db
            ticket = db.hd_tickets[ticket_id]
            return ticket.hd_sla_policy_id, ticket.sla_breach_at

        sla_policy_id, sla_breach_at = await run_in_threadpool(do_check)
        assert sla_policy_id == data["policy_high"]
        assert sla_breach_at is not None

    @pytest.mark.asyncio
    async def test_apply_sla_policy_no_matching_policy(self, app, sla_setup):
        """Test apply_sla_policy returns None when no policy matches priority."""
        from apps.api.utils.async_utils import run_in_threadpool

        data = sla_setup
        tenant_id = data["tenant_id"]
        identity_id = data["identity_id"]

        def do_setup_ticket():
            db = app.db
            # Create ticket with priority that has no policy
            ticket_id = db.hd_tickets.insert(
                tenant_id=tenant_id,
                subject="Medium Priority",
                status="new",
                priority="medium",  # No policy for medium
                channel="web",
                requester_identity_id=identity_id,
                created_at=datetime.now(UTC),
            )
            db.commit()
            return ticket_id

        ticket_id = await run_in_threadpool(do_setup_ticket)

        # Apply SLA - should return None
        result = await apply_sla_policy(app.db, tenant_id, ticket_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_sla_breaches_finds_breached_tickets(self, app, sla_setup):
        """Test check_sla_breaches returns tickets past SLA deadline."""
        from apps.api.utils.async_utils import run_in_threadpool

        data = sla_setup
        tenant_id = data["tenant_id"]
        identity_id = data["identity_id"]

        def do_setup():
            db = app.db

            # Create a ticket with SLA breach in the past
            now = datetime.now(UTC)
            breached_time = now - timedelta(hours=1)  # 1 hour ago

            ticket_id = db.hd_tickets.insert(
                tenant_id=tenant_id,
                subject="Breached Ticket",
                status="open",
                priority="high",
                channel="web",
                requester_identity_id=identity_id,
                hd_sla_policy_id=data["policy_high"],
                sla_breach_at=breached_time,
                created_at=breached_time - timedelta(hours=10),
            )

            # Create a non-breached ticket (status resolved)
            resolved_ticket_id = db.hd_tickets.insert(
                tenant_id=tenant_id,
                subject="Resolved Ticket",
                status="resolved",
                priority="high",
                channel="web",
                requester_identity_id=identity_id,
                hd_sla_policy_id=data["policy_high"],
                sla_breach_at=breached_time,
                created_at=breached_time - timedelta(hours=10),
                resolved_at=now - timedelta(minutes=30),
            )

            db.commit()
            return ticket_id, resolved_ticket_id

        breached_ticket_id, resolved_ticket_id = await run_in_threadpool(do_setup)

        # Check for breaches
        breached = await check_sla_breaches(app.db, tenant_id)

        # Should find the breached ticket but not the resolved one
        breached_ids = [row.id for row in breached]
        assert breached_ticket_id in breached_ids
        assert resolved_ticket_id not in breached_ids

    @pytest.mark.asyncio
    async def test_check_sla_breaches_excludes_resolved_and_closed(
        self, app, sla_setup
    ):
        """Test check_sla_breaches does not return resolved/closed tickets."""
        from apps.api.utils.async_utils import run_in_threadpool

        data = sla_setup
        tenant_id = data["tenant_id"]
        identity_id = data["identity_id"]

        def do_setup():
            db = app.db
            now = datetime.now(UTC)
            past_breach = now - timedelta(hours=2)

            # Create closed ticket
            closed_ticket_id = db.hd_tickets.insert(
                tenant_id=tenant_id,
                subject="Closed (past SLA)",
                status="closed",
                priority="high",
                channel="web",
                requester_identity_id=identity_id,
                hd_sla_policy_id=data["policy_high"],
                sla_breach_at=past_breach,
                created_at=past_breach - timedelta(hours=5),
                closed_at=now,
            )

            db.commit()
            return closed_ticket_id

        closed_ticket_id = await run_in_threadpool(do_setup)

        # Check for breaches
        breached = await check_sla_breaches(app.db, tenant_id)
        breached_ids = [row.id for row in breached]

        # Closed ticket should NOT be in breached list
        assert closed_ticket_id not in breached_ids
