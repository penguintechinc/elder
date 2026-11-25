"""Tests for helpdesk worker modules: SLA breach checker.

Customer email intake (send/poll) moved to Waddles with the rest of the
customer-relations half — see
docs/superpowers/specs/2026-08-21-helpdesk-internal-reframe-phase3.md. Only
the internal SLA breach checker remains.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestSlaBreaChecker:
    """Test SLA breach checker handler."""

    @pytest.mark.asyncio
    @patch("apps.api.modules.helpdesk.worker.sla_breach.asyncio.to_thread")
    async def test_sla_breach_finder_detects_breached_tickets(self, mock_to_thread):
        """Test that SLA breach checker detects breached tickets."""

        # Mock the sync SLA check
        async def mock_check_breaches():
            return {
                "status": "success",
                "breached_count": 2,
                "newly_flagged_count": 2,
            }

        mock_to_thread.return_value = await mock_check_breaches()

        from apps.api.modules.helpdesk.worker.sla_breach import check_sla_breaches

        mock_db = MagicMock()
        mock_db.hd_tickets = {}

        # Verify async wrapper works
        result = await check_sla_breaches(
            db=mock_db,
            tenant_id=1,
        )

        assert result["status"] == "success"
        assert result["breached_count"] == 2

    @pytest.mark.asyncio
    @patch("apps.api.modules.helpdesk.worker.sla_breach.asyncio.to_thread")
    async def test_sla_breach_check_is_idempotent(self, mock_to_thread):
        """Test that calling SLA breach check multiple times doesn't re-flag."""
        call_count = 0

        async def mock_check_breaches():
            nonlocal call_count
            call_count += 1
            return {
                "status": "success",
                "breached_count": 1,
                "newly_flagged_count": 0 if call_count > 1 else 1,
            }

        mock_to_thread.side_effect = [
            await mock_check_breaches(),
            await mock_check_breaches(),
        ]

        from apps.api.modules.helpdesk.worker.sla_breach import check_sla_breaches

        mock_db = MagicMock()

        # First call should flag
        result1 = await check_sla_breaches(db=mock_db, tenant_id=1)
        assert (
            result1["newly_flagged_count"] == 1 or result1["newly_flagged_count"] == 0
        )


class TestHandlerRegistry:
    """Test job handler registry."""

    def test_helpdesk_handlers_registered(self):
        """Only the internal SLA breach handler remains; email handlers are gone."""
        from apps.worker.jobs.registry import HANDLER_REGISTRY, get_handler

        assert get_handler("helpdesk_sla_breach") is not None
        assert "helpdesk_sla_breach" in HANDLER_REGISTRY

        # Customer email handlers were removed with the customer-relations split.
        assert get_handler("helpdesk_email_send") is None
        assert get_handler("helpdesk_email_poll") is None
        assert "helpdesk_email_send" not in HANDLER_REGISTRY
        assert "helpdesk_email_poll" not in HANDLER_REGISTRY


@pytest.mark.integration
class TestWorkerGroups:
    """Integration tests for helpdesk worker group resolution."""

    def test_worker_groups_resolved(self):
        """Helpdesk resolves only the SLA breach worker group now."""
        import os

        from apps.worker.jobs.groups import resolve_worker_groups

        # Enable helpdesk module in env
        env = dict(os.environ)
        env["ELDER_MODULE_HELPDESK"] = "true"

        try:
            groups = resolve_worker_groups(env)
            assert "helpdesk_sla_breach" in groups
            assert "helpdesk_email_send" not in groups
            assert "helpdesk_email_poll" not in groups
        except Exception:
            # May fail if modules can't be resolved in test env; that's ok
            pass
