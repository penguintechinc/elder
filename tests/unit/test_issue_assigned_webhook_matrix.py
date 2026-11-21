"""Cross-cutting matching/tenant-isolation/signature matrix for
issue.assigned (Plan 05, spec §7). Exercises send_issue_assigned_webhooks
directly against multiple simultaneous webhook configs — the scenario none
of Tasks 6-8's single-webhook wiring tests cover on their own. Firing on
each of the three call sites (create_issue, update_issue, intake
default-assign) and the non-firing cases (no assignee, unchanged assignee)
already have dedicated coverage in test_issues_assigned_webhook_create.py,
test_issues_assigned_webhook_update.py, and test_intake_assigned_webhook.py
— not repeated here.

webhooks.tenant_id is a real foreign key into tenants (see
test_webhook_assignment_matching.py's _make_tenant), so every test mints
its own tenant(s) via _make_tenant rather than a hardcoded literal — a bare
tenant_id=1 would only work by accident of another test's insert order
within the shared session, and fails outright when this file runs alone
against a freshly reset database. webhook_deliveries/webhooks lookups are
likewise scoped to each test's own webhook_id or tenant_id, since neither
table is truncated between test functions within a session.
"""

from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from quart import current_app


def _make_tenant(db, name: str) -> int:
    """Create a dedicated tenant row and return its id.

    Mirrors test_webhook_assignment_matching.py / test_webhook_assignment_dispatch.py's
    _make_tenant — webhooks.tenant_id is a real FK into tenants, so each test
    needs its own valid, guaranteed-unused tenant row.
    """
    tid = db.tenants.insert(
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid4().hex[:8]}",
        is_active=True,
    )
    db.commit()
    return tid


def _insert_webhook(db, tenant_id, **overrides):
    now = datetime.now(UTC)
    defaults = dict(
        tenant_id=tenant_id,
        village_id=f"{tenant_id:08x}-{uuid4().hex[:16]}",
        name="Matrix webhook",
        url="https://hooks.example.com/matrix",
        events=["issue.assigned"],
        is_active=True,
        secret="matrix-secret",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    webhook_id = db.webhooks.insert(**defaults)
    db.commit()
    return webhook_id


def _event(tenant_id, **overrides):
    from apps.api.services.webhooks.assignment import AssignmentEvent

    defaults = dict(
        issue_id=1,
        village_id="00000009-0000000000000001",
        issue_type="SUPPORT",
        status="OPEN",
        assignee_type="identity",
        assignee_id=42,
        tenant_id=tenant_id,
        actor_id=7,
    )
    defaults.update(overrides)
    return AssignmentEvent(**defaults)


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_org_unit_only_filter_matches_org_unit_assignment(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        tenant_id = _make_tenant(db, "Matrix OU Filter")
        _insert_webhook(
            db, tenant_id, filter_assignee_type="org_unit", filter_assignee_id=10
        )

        results = await send_issue_assigned_webhooks(
            db, _event(tenant_id, assignee_type="org_unit", assignee_id=10)
        )
        assert len(results) == 1 and results[0]["success"] is True


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_issue_type_and_assignee_combo_filter(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        tenant_id = _make_tenant(db, "Matrix Combo Filter")
        _insert_webhook(
            db,
            tenant_id,
            filter_issue_type="SUPPORT",
            filter_assignee_type="identity",
            filter_assignee_id=42,
        )

        # Matches both filters -> delivered
        results = await send_issue_assigned_webhooks(db, _event(tenant_id))
        assert len(results) == 1

        # issue_type mismatches -> not delivered
        mock_post.reset_mock()
        results = await send_issue_assigned_webhooks(
            db, _event(tenant_id, issue_type="BUG")
        )
        assert results == []
        mock_post.assert_not_called()


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_empty_filter_matches_all_assignments_in_tenant(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        tenant_id = _make_tenant(db, "Matrix Empty Filter")
        _insert_webhook(db, tenant_id)  # no filters set

        results_a = await send_issue_assigned_webhooks(
            db,
            _event(
                tenant_id, issue_type="BUG", assignee_type="org_unit", assignee_id=999
            ),
        )
        results_b = await send_issue_assigned_webhooks(
            db,
            _event(
                tenant_id,
                issue_type="SUPPORT",
                assignee_type="identity",
                assignee_id=1,
            ),
        )
        assert len(results_a) == 1
        assert len(results_b) == 1


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_multiple_configs_each_fire_independently(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        tenant_id = _make_tenant(db, "Matrix Multi Config")
        # (a) any issue assigned to org-unit 10 -> webhook A
        _insert_webhook(
            db, tenant_id, filter_assignee_type="org_unit", filter_assignee_id=10
        )
        # (b) issue_type=SUPPORT assigned to identity 42 -> webhook B
        _insert_webhook(
            db,
            tenant_id,
            filter_issue_type="SUPPORT",
            filter_assignee_type="identity",
            filter_assignee_id=42,
        )

        # An assignment matching ONLY (b) fires exactly one webhook.
        results = await send_issue_assigned_webhooks(
            db,
            _event(
                tenant_id,
                issue_type="SUPPORT",
                assignee_type="identity",
                assignee_id=42,
            ),
        )
        assert len(results) == 1


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_tenant_isolation_webhook_never_fires_cross_tenant(mock_post, app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        tenant_a = _make_tenant(db, "Matrix Isolation A")
        tenant_b = _make_tenant(db, "Matrix Isolation B")
        # Webhook lives in tenant B.
        _insert_webhook(db, tenant_b)

        # Assignment event is for tenant A — must never match tenant B's webhook.
        results = await send_issue_assigned_webhooks(db, _event(tenant_a))

        assert results == []
        mock_post.assert_not_called()


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_signature_matches_expected_hmac(mock_post, app):
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks
        from apps.api.services.webhooks.service import generate_signature

        db = current_app.db
        tenant_id = _make_tenant(db, "Matrix Signature")
        _insert_webhook(db, tenant_id, secret="known-secret")

        await send_issue_assigned_webhooks(db, _event(tenant_id))

        sent_kwargs = mock_post.call_args.kwargs
        sent_signature = sent_kwargs["headers"]["X-Elder-Signature"]

        # Recompute over the ACTUAL bytes posted (mock_post's `data=` kwarg —
        # _dispatch_one signs and sends the same serialized string, never
        # `json=`), not just assert the header is present. Mirrors
        # test_webhook_assignment_dispatch.py's recompute-and-compare pattern.
        sent_body = sent_kwargs["data"]
        assert isinstance(sent_body, bytes)
        expected_signature = generate_signature(
            "known-secret", sent_body.decode("utf-8")
        )
        assert sent_signature == expected_signature


@pytest.mark.asyncio
@patch("apps.api.services.webhooks.assignment.requests.post")
async def test_inactive_webhook_never_fires(mock_post, app):
    async with app.app_context():
        from apps.api.services.webhooks.assignment import send_issue_assigned_webhooks

        db = current_app.db
        tenant_id = _make_tenant(db, "Matrix Inactive")
        _insert_webhook(db, tenant_id, is_active=False)

        results = await send_issue_assigned_webhooks(db, _event(tenant_id))

        assert results == []
        mock_post.assert_not_called()
