"""Tests for tenant scoping on notification_rules (cross-tenant IDOR fix).

Regression: notification_rules list and get were not tenant-scoped,
allowing any tenant to read another tenant's rules.
"""

from uuid import uuid4

import pytest
from quart import current_app


def _foreign_tenant(db) -> int:
    """Create and return a second tenant distinct from tenant 1."""
    tid = db.tenants.insert(
        name="Other Tenant", slug=f"other-{uuid4().hex[:8]}", is_active=True
    )
    db.commit()
    return tid


def _create_org_in_tenant(db, tenant_id: int, name: str = "Test Org") -> int:
    """Create an organization in the given tenant."""
    org_id = db.organizations.insert(
        name=name,
        slug=f"org-{uuid4().hex[:8]}",
        tenant_id=tenant_id,
    )
    db.commit()
    return org_id


def _create_notification_rule_in_org(db, org_id: int, name: str = "Test Rule") -> int:
    """Create a notification rule in the given organization."""
    rule_id = db.notification_rules.insert(
        name=name,
        channel="email",
        events=["issue.created"],
        config_json={"recipients": ["test@example.com"]},
        enabled=True,
        organization_id=org_id,
    )
    db.commit()
    return rule_id


class TestNotificationRulesTenantIsolation:
    """Verify notification_rules reads are scoped to the caller's tenant."""

    @pytest.mark.asyncio
    async def test_list_excludes_other_tenant_rules(self, app):
        """list_notification_rules must not return rules from other tenants.

        Regression: notification_rules cross-tenant IDOR.
        """
        async with app.app_context():
            from apps.api.services.webhooks.service import WebhookService

            db = current_app.db
            service = WebhookService(db)

            # Create tenant 2 with its own org and rule
            t2_id = _foreign_tenant(db)
            t2_org_id = _create_org_in_tenant(db, t2_id, name="T2 Org")
            t2_rule_id = _create_notification_rule_in_org(db, t2_org_id, name="T2 Rule")

            # Tenant 1's org and rule
            t1_org_id = _create_org_in_tenant(db, 1, name="T1 Org")
            t1_rule_id = _create_notification_rule_in_org(db, t1_org_id, name="T1 Rule")

            # List as tenant 1 — must NOT include tenant 2's rule
            rules_t1 = service.list_notification_rules(tenant_id=1)
            rule_names = [r["name"] for r in rules_t1]

            assert "T1 Rule" in rule_names, "Tenant 1 must see its own rule"
            assert "T2 Rule" not in rule_names, "Tenant 1 must not see Tenant 2's rule"

            # List as tenant 2 — must NOT include tenant 1's rule
            rules_t2 = service.list_notification_rules(tenant_id=t2_id)
            rule_names_t2 = [r["name"] for r in rules_t2]

            assert "T2 Rule" in rule_names_t2, "Tenant 2 must see its own rule"
            assert (
                "T1 Rule" not in rule_names_t2
            ), "Tenant 2 must not see Tenant 1's rule"

    @pytest.mark.asyncio
    async def test_get_cross_tenant_not_found(self, app):
        """get_notification_rule must return not-found for cross-tenant access.

        Regression: notification_rules cross-tenant IDOR.
        """
        async with app.app_context():
            from apps.api.services.webhooks.service import WebhookService

            db = current_app.db
            service = WebhookService(db)

            # Create tenant 2's rule
            t2_id = _foreign_tenant(db)
            t2_org_id = _create_org_in_tenant(db, t2_id, name="T2 Org")
            t2_rule_id = _create_notification_rule_in_org(db, t2_org_id, name="T2 Rule")

            # Tenant 1 tries to get tenant 2's rule — must fail with not-found
            with pytest.raises(Exception, match="not found"):
                service.get_notification_rule(t2_rule_id, tenant_id=1)

            # Tenant 2 can get its own rule
            rule = service.get_notification_rule(t2_rule_id, tenant_id=t2_id)
            assert rule["name"] == "T2 Rule"

    @pytest.mark.asyncio
    async def test_list_filtered_by_organization_still_tenant_scoped(self, app):
        """list with organization_id filter must still respect tenant isolation.

        Even if the caller specifies an organization_id, only rules from
        organizations that belong to the caller's tenant are returned.
        """
        async with app.app_context():
            from apps.api.services.webhooks.service import WebhookService

            db = current_app.db
            service = WebhookService(db)

            # Create tenant 2's org and rule
            t2_id = _foreign_tenant(db)
            t2_org_id = _create_org_in_tenant(db, t2_id, name="T2 Org")
            t2_rule_id = _create_notification_rule_in_org(db, t2_org_id, name="T2 Rule")

            # Tenant 1 tries to filter by tenant 2's org_id — should get empty
            rules = service.list_notification_rules(
                tenant_id=1, organization_id=t2_org_id
            )
            assert len(rules) == 0, "Tenant 1 must not see rules from Tenant 2's orgs"
