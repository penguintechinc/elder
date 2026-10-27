"""Unit tests: background entry points bind the right RLS context (no database).

The real-database behaviour of these paths is covered in
tests/integration/test_rls_wired_paths.py; here we pin *which* tenant / scope each
entry point establishes before touching data.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.database import rls
from shared.database.rls import SystemScopeReason


@pytest.fixture(autouse=True)
def _clean():
    rls.clear_tenant()
    yield
    rls.clear_tenant()


def _worker():
    from apps.worker.main import WorkerService

    service = WorkerService.__new__(WorkerService)
    service.jobbus = MagicMock()
    service.jobbus.is_duplicate = AsyncMock(return_value=False)
    for name in ("ack", "mark_processed", "publish_result"):
        setattr(service.jobbus, name, AsyncMock())
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant_id,expected", [(42, 42), (None, None), (0, None)])
async def test_worker_dispatch_binds_envelope_tenant_only_while_handler_runs(
    tenant_id, expected
):
    seen = []

    async def handler(envelope):
        seen.append(rls.current_tenant_id())
        return {"ok": True}

    envelope = SimpleNamespace(
        job_id="j1", job_type="t", tenant_id=tenant_id, payload={}
    )
    with patch("apps.worker.main.get_handler", return_value=handler):
        status = await _worker()._process_job_body("grp", "1-0", envelope)
    assert status == "success"
    assert seen == [expected]
    assert rls.current_tenant_id() is None


@pytest.mark.asyncio
async def test_flows_invoker_binds_job_tenant_inside_worker_thread():
    from apps.flows_invoker.main import FlowsInvokerService

    seen = []

    def fake_pipeline(db, tenant_id, promotion_id, started_by, execution_id):
        seen.append((rls.current_tenant_id(), tenant_id))
        return {"done": True}

    service = FlowsInvokerService()
    service.db_manager = SimpleNamespace(write=object())
    envelope = SimpleNamespace(tenant_id=7, payload={"promotion_id": 3})
    with patch("apps.flows_invoker.main.execute_promotion_pipeline", fake_pipeline):
        assert await service._handle(envelope) == {"done": True}
    assert seen == [(7, 7)]
    assert rls.current_tenant_id() is None


@pytest.mark.asyncio
async def test_flows_invoker_rejects_job_without_tenant_before_any_binding():
    from apps.flows_invoker.main import FlowsInvokerService

    service = FlowsInvokerService()
    with pytest.raises(ValueError, match="tenant_id"):
        await service._handle(
            SimpleNamespace(tenant_id=None, payload={"promotion_id": 1})
        )


class _Expr:
    """Stand-in for a DAL field/query: every operator yields another expression."""

    def __eq__(self, other):
        return self

    def __le__(self, other):
        return self

    def __and__(self, other):
        return self


def _scheduler(groups):
    from apps.api.services.access_review.scheduler import AccessReviewScheduler

    db = MagicMock()
    db.identity_groups = SimpleNamespace(
        review_enabled=_Expr(), next_review_date=_Expr(), is_active=_Expr()
    )
    db.return_value.select.return_value = groups
    return AccessReviewScheduler(db)


def _group(tenant_id):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=1,
        name="g",
        tenant_id=tenant_id,
        last_review_date=now,
        created_at=now,
        review_due_days=7,
        review_auto_apply=False,
    )


@pytest.mark.parametrize("group_tenant,expected", [(5, 5), (None, 1)])
def test_access_review_creation_is_pinned_to_the_groups_tenant(group_tenant, expected):
    scheduler = _scheduler([_group(group_tenant)])
    seen = []

    def create_review(**kwargs):
        seen.append((rls.current_tenant_id(), kwargs["tenant_id"]))
        return {"id": 1}

    scheduler.service.create_review = create_review
    scheduler._create_pending_reviews()
    assert seen == [(expected, expected)]
    assert rls.current_tenant_id() is None


def test_access_review_overdue_sweep_runs_in_audited_background_scope():
    scheduler = _scheduler([])
    seen = []

    def check_overdue():
        seen.append(rls.current_system_scope())
        return [1, 2]

    scheduler.service.check_overdue_reviews = check_overdue
    scheduler._check_overdue_reviews()
    assert [(s.reason, s.purpose) for s in seen] == [
        (SystemScopeReason.BACKGROUND_SWEEP, "access_review.overdue_sweep")
    ]
    assert rls.current_system_scope() is None


def test_discovery_job_tenant_resolution_uses_background_scope():
    from apps.worker.discovery.service import DiscoveryService

    service = DiscoveryService.__new__(DiscoveryService)
    scopes = []

    def tenant_for_org(org_id):
        scopes.append(rls.current_system_scope())
        return 11

    service._tenant_for_org = tenant_for_org
    assert service._resolve_job_tenant(99) == 11
    assert [s.purpose for s in scopes] == ["discovery.resolve_job_tenant"]
    assert rls.current_system_scope() is None
