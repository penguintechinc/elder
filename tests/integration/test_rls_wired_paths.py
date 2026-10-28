"""Real-PostgreSQL tests for every code path wired into RLS (migration 043).

Each test drives a real route / service through the real penguin-dal under the
NOSUPERUSER application role, so a path that forgot to bind a tenant or enter an
explicit system scope would see zero rows or a policy violation here.
"""

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from rls_support import T1, T2, _run, _seed
from sqlalchemy import text

from shared.database import rls
from shared.database.rls import SystemScopeReason, tenant_context

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("_reset_rls_state"),
]


class _Counter:
    """OTel counter stand-in recording (amount, attributes) calls."""

    def __init__(self):
        self.calls = []

    def add(self, amount, attributes):
        self.calls.append((amount, attributes))


FLOW_SECRET = secrets.token_hex(16)  # per-run, never a committed literal
PAST = datetime.now(UTC) - timedelta(days=3)


@pytest.fixture(scope="module")
def wired(rls_env):
    """Seed the rows the wired routes/services read, as the superuser."""
    ids: dict[str, int] = {}
    with rls_env.admin.begin() as conn:
        conn.execute(text("SET session_replication_role = replica"))  # skip FK parents
        ids["doc_t1"] = _seed(
            conn,
            "doc_documents",
            tenant_id=T1,
            slug="doc-t1",
            title="Doc T1",
            is_public=True,
            status="published",
        )
        _seed(
            conn,
            "doc_documents",
            tenant_id=T2,
            slug="doc-t2",
            title="Doc T2",
            is_public=True,
            status="published",
        )
        _seed(
            conn,
            "doc_documents",
            tenant_id=T1,
            slug="doc-private",
            title="Private",
            is_public=False,
            status="published",
        )

        playbook = _seed(
            conn, "stream_playbooks", tenant_id=T1, name="pb-t1", is_enabled=True
        )
        ids["playbook"] = playbook
        _seed(
            conn,
            "stream_webhooks",
            tenant_id=T1,
            playbook_id=playbook,
            token="tok-t1",
            is_enabled=True,
            validate_signature=False,
            allowed_methods=json.dumps(["POST"]),
        )

        flow = _seed(conn, "iceflows", tenant_id=T2, name="flow-t2", is_enabled=True)
        ids["flow"] = flow
        _seed(
            conn,
            "iceflows_webhooks",
            tenant_id=T2,
            flow_id=flow,
            webhook_id="wh-t2",
            provider="github",
            webhook_secret=FLOW_SECRET,
            is_active=True,
        )
        for order in (1, 2):
            _seed(
                conn,
                "iceflows_stages",
                tenant_id=T2,
                flow_id=flow,
                stage_order=order,
            )
        disabled_flow = _seed(
            conn, "iceflows", tenant_id=T2, name="flow-off", is_enabled=False
        )
        _seed(
            conn,
            "iceflows_webhooks",
            tenant_id=T2,
            flow_id=disabled_flow,
            webhook_id="wh-off",
            provider="github",
            webhook_secret=FLOW_SECRET,
            is_active=True,
        )

        diagram = _seed(conn, "dg_diagrams", tenant_id=T1, title="Diagram T1")
        ids["diagram"] = diagram
        _seed(
            conn,
            "dg_shares",
            tenant_id=T1,
            diagram_id=diagram,
            share_token="share-t1",
            is_public=True,
        )
        _seed(
            conn,
            "dg_diagram_versions",
            tenant_id=T1,
            diagram_id=diagram,
            version_number=1,
            content_json=json.dumps({"nodes": [], "edges": []}),
        )

        for tenant in (T1, T2):
            _seed(
                conn,
                "access_reviews",
                tenant_id=tenant,
                status="scheduled",
                due_date=PAST,
            )
    return ids


@pytest.fixture()
def quart_app(dal):
    """Minimal Quart app mounting the public routes under test."""
    from quart import Quart

    from apps.api.modules.diagrams.routes.shares import bp as shares_bp
    from apps.api.modules.documents.routes.documents import bp as docs_bp
    from apps.api.modules.flows.routes.hooks import bp as flow_hooks_bp
    from apps.api.modules.services_oncall.routes.on_call_webhooks import (
        bp as oncall_bp,
    )
    from apps.api.modules.streams.routes.hooks import bp as stream_hooks_bp

    quart_app = Quart(__name__)
    quart_app.config["TESTING"] = True
    quart_app.db = dal
    quart_app.register_blueprint(docs_bp, url_prefix="/docs")
    quart_app.register_blueprint(stream_hooks_bp, url_prefix="/stream-hooks")
    quart_app.register_blueprint(flow_hooks_bp, url_prefix="/flow-hooks")
    quart_app.register_blueprint(shares_bp, url_prefix="/diagrams")
    quart_app.register_blueprint(oncall_bp, url_prefix="/oncall")
    return quart_app


def _admin_count(rls_env, sql: str, **params) -> int:
    rows, _ = _run(rls_env.admin, sql, **params)
    return rows[0][0]


class TestPublicRoutes:
    """Unauthenticated routes enter an explicit system scope and still work."""

    def test_control_unscoped_dal_sees_no_public_documents(self, wired, dal):
        assert dal(dal.doc_documents.is_public == True).count() == 0  # noqa: E712

    @pytest.mark.asyncio
    async def test_public_document_list_spans_tenants_and_hides_private(
        self, wired, quart_app
    ):
        response = await quart_app.test_client().get("/docs/public")
        assert response.status_code == 200
        slugs = {item["slug"] for item in (await response.get_json())["items"]}
        assert {"doc-t1", "doc-t2"} <= slugs
        assert "doc-private" not in slugs

    @pytest.mark.asyncio
    async def test_public_document_get_by_slug(self, wired, quart_app):
        response = await quart_app.test_client().get("/docs/public/doc-t2")
        assert response.status_code == 200
        assert (await response.get_json())["title"] == "Doc T2"

    @pytest.mark.asyncio
    async def test_shared_diagram_view_reads_and_records_analytics_for_its_tenant(
        self, wired, quart_app, rls_env
    ):
        before = _admin_count(
            rls_env,
            "SELECT count(*) FROM dg_share_analytics WHERE tenant_id = :t",
            t=T1,
        )
        response = await quart_app.test_client().get("/diagrams/shared/share-t1")
        assert response.status_code == 200
        assert (await response.get_json())["title"] == "Diagram T1"
        after = _admin_count(
            rls_env,
            "SELECT count(*) FROM dg_share_analytics WHERE tenant_id = :t",
            t=T1,
        )
        assert after == before + 1
        assert (
            await quart_app.test_client().get("/diagrams/shared/missing")
        ).status_code == 404

    @pytest.mark.asyncio
    async def test_stream_webhook_test_route(self, wired, quart_app):
        response = await quart_app.test_client().get("/stream-hooks/tok-t1/test")
        assert response.status_code == 200
        assert (await response.get_json())["webhook"]["is_enabled"] is True
        assert (
            await quart_app.test_client().get("/stream-hooks/nope/test")
        ).status_code == 404

    @pytest.mark.asyncio
    async def test_stream_webhook_trigger_writes_execution_for_playbook_tenant(
        self, wired, quart_app, rls_env
    ):
        response = await quart_app.test_client().post(
            "/stream-hooks/tok-t1", json={"a": 1}
        )
        assert response.status_code == 202
        rows, _ = _run(
            rls_env.admin,
            "SELECT tenant_id FROM stream_executions WHERE playbook_id = :p",
            p=wired["playbook"],
        )
        assert rows and {r[0] for r in rows} == {T1}
        assert (
            await quart_app.test_client().post("/stream-hooks/nope")
        ).status_code == 404

    @pytest.mark.asyncio
    async def test_flow_webhook_lookup_and_signature_gate(
        self, wired, quart_app, rls_env
    ):
        """Lookup runs under explicit scope; a bad signature never reaches any write."""
        headers = {"Content-Type": "application/json"}
        bad = await quart_app.test_client().post(
            "/flow-hooks/wh-t2",
            data=b"{}",
            headers={**headers, "X-Hub-Signature-256": "sha256=00"},
        )
        assert bad.status_code == 401
        assert _admin_count(rls_env, "SELECT count(*) FROM iceflows_promotions") == 0
        unknown = await quart_app.test_client().post("/flow-hooks/unknown", data=b"{}")
        assert unknown.status_code == 404
        disabled = await quart_app.test_client().post("/flow-hooks/wh-off", data=b"{}")
        assert disabled.status_code == 404  # disabled pipeline: uniform 404

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason=(
            "pre-existing release bug: the flows webhook route reads "
            "iceflows_webhooks.trigger_stage_order/trigger_count, columns no model or "
            "migration defines; the RLS wiring before it (system-scope lookup, "
            "set_tenant) is exercised by the 401/404 test above"
        ),
    )
    @pytest.mark.asyncio
    async def test_flow_webhook_valid_signature_creates_tenant_pinned_promotion(
        self, wired, quart_app, rls_env
    ):
        body = json.dumps({"commit_sha": "abc"}).encode()
        good = (
            "sha256=" + hmac.new(FLOW_SECRET.encode(), body, hashlib.sha256).hexdigest()
        )
        ok = await quart_app.test_client().post(
            "/flow-hooks/wh-t2",
            data=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": good},
        )
        assert ok.status_code == 202
        rows, _ = _run(rls_env.admin, "SELECT tenant_id FROM iceflows_promotions")
        assert [r[0] for r in rows] == [T2]

    @pytest.mark.asyncio
    async def test_alertmanager_webhook_runs_under_explicit_counted_scope(
        self, wired, quart_app, monkeypatch
    ):
        """The unauthenticated webhook enters PUBLIC_TOKEN scope (counted, not audited).

        ``ApiResponse.success`` already returns ``(response, status)`` so the route's
        ``(ApiResponse.success(...), 202)`` is a nested tuple Quart rejects — a
        pre-existing bug unrelated to RLS; patched here so the DB path is judged alone.
        """
        from quart import jsonify

        from apps.api.modules.services_oncall.routes import on_call_webhooks

        monkeypatch.setattr(
            on_call_webhooks.ApiResponse,
            "success",
            staticmethod(lambda data, status_code=200: jsonify(data)),
        )
        audited, counter = [], _Counter()
        rls.register_audit_sink(audited.append)
        monkeypatch.setitem(rls._counters, "elder.rls.system_scope.entered", counter)
        payload = {"alerts": [{"status": "firing", "labels": {"service": "nope"}}]}
        response = await quart_app.test_client().post(
            "/oncall/alertmanager/webhook", json=payload
        )
        assert response.status_code == 202
        assert audited == []  # anonymous scopes are never durably audited
        assert counter.calls == [(1, {"reason": "PUBLIC_TOKEN"})]

    @pytest.mark.asyncio
    async def test_public_token_flood_writes_no_audit_rows_but_is_counted(
        self, wired, quart_app, rls_env, monkeypatch
    ):
        """25 anonymous hits (bogus + real tokens) -> 0 audit_logs rows, 25 counted entries."""
        from apps.api.services.audit.rls_sink import write_system_scope_audit

        before = _admin_count(rls_env, "SELECT count(*) FROM audit_logs")
        counter = _Counter()
        rls.register_audit_sink(write_system_scope_audit)  # the real durable sink
        monkeypatch.setitem(rls._counters, "elder.rls.system_scope.entered", counter)
        client = quart_app.test_client()
        for i in range(25):
            token = "tok-t1" if i % 5 == 0 else f"bogus-{i}"
            await client.get(f"/stream-hooks/{token}/test")
        assert _admin_count(rls_env, "SELECT count(*) FROM audit_logs") == before
        assert len(counter.calls) == 25
        assert {attrs["reason"] for _, attrs in counter.calls} == {"PUBLIC_TOKEN"}

    @pytest.mark.asyncio
    async def test_privileged_scope_is_still_durably_audited_off_the_event_loop(
        self, wired, quart_app, rls_env
    ):
        from apps.api.api.v1.tenant_modules import _module_scope
        from apps.api.services.audit.rls_sink import write_system_scope_audit

        before = _admin_count(
            rls_env,
            "SELECT count(*) FROM audit_logs WHERE action_name = 'rls_system_scope'",
        )
        rls.register_audit_sink(write_system_scope_audit)
        async with quart_app.app_context():
            from quart import g

            g.current_user = SimpleNamespace(id=9)
            with tenant_context(T1):
                async with _module_scope(T1, T2, "tenant_modules.list"):
                    pass
        after = _admin_count(
            rls_env,
            "SELECT count(*) FROM audit_logs WHERE action_name = 'rls_system_scope'",
        )
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_flow_webhook_binds_the_webhooks_tenant_only_after_the_signature_passes(
        self, wired, quart_app, monkeypatch
    ):
        """Pins hooks.set_tenant independently of the route's pre-existing later bug."""
        from apps.api.modules.flows.routes import hooks

        bound = []
        real = hooks.set_tenant
        monkeypatch.setattr(
            hooks,
            "set_tenant",
            lambda tenant_id: bound.append(tenant_id) or real(tenant_id),
        )
        body = json.dumps({"commit_sha": "abc"}).encode()
        good = (
            "sha256=" + hmac.new(FLOW_SECRET.encode(), body, hashlib.sha256).hexdigest()
        )
        headers = {"Content-Type": "application/json"}
        client = quart_app.test_client()
        await client.post(
            "/flow-hooks/wh-t2",
            data=body,
            headers={**headers, "X-Hub-Signature-256": "sha256=00"},
        )
        assert bound == []
        await client.post(
            "/flow-hooks/wh-t2",
            data=body,
            headers={**headers, "X-Hub-Signature-256": good},
        )
        assert bound == [T2]


class TestCrossTenantAdminScope:
    """Global-admin style routes get an audited SUPER_ADMIN scope for other tenants."""

    @pytest.mark.asyncio
    async def test_tenants_route_helper_elevates_only_for_other_tenants(
        self, wired, quart_app, dal
    ):
        from apps.api.api.v1.tenants import _tenant_scope

        audited = []
        rls.register_audit_sink(audited.append)
        async with quart_app.test_request_context("/"):
            from quart import request

            request.portal_user = {"tenant_id": T1, "sub": "5"}
            with tenant_context(T1):
                with _tenant_scope(T1, "tenants.get_tenant"):
                    own = dal(dal.organizations.tenant_id == T1).count()
                assert audited == []
                assert dal(dal.organizations.tenant_id == T2).count() == 0
                with _tenant_scope(T2, "tenants.get_tenant"):
                    other = dal(dal.organizations.tenant_id == T2).count()
        assert (own, other) == (2, 2)
        assert [(s.reason, s.purpose, s.actor) for s in audited] == [
            (SystemScopeReason.SUPER_ADMIN, "tenants.get_tenant", "portal_user:5")
        ]

    @pytest.mark.asyncio
    async def test_tenant_modules_helper_elevates_only_for_other_tenants(
        self, wired, quart_app, dal
    ):
        from quart import g

        from apps.api.api.v1.tenant_modules import _module_scope

        audited = []
        rls.register_audit_sink(audited.append)
        async with quart_app.app_context():
            g.current_user = SimpleNamespace(id=7)
            with tenant_context(T1):
                async with _module_scope(T1, T1, "tenant_modules.list"):
                    assert audited == []
                async with _module_scope(T1, T2, "tenant_modules.set"):
                    assert dal(dal.organizations.tenant_id == T2).count() == 2
        assert [(s.purpose, s.actor) for s in audited] == [
            ("tenant_modules.set", "identity:7")
        ]


class TestBackgroundPaths:
    """Schedulers and workers bind a tenant or enter an explicit sweep scope."""

    def test_access_review_overdue_sweep_marks_every_tenants_reviews(
        self, wired, dal, rls_env
    ):
        from apps.api.services.access_review.scheduler import AccessReviewScheduler

        assert (
            AccessReviewScheduler(dal).service.check_overdue_reviews() == []
        )  # control: unscoped
        AccessReviewScheduler(dal)._check_overdue_reviews()
        rows, _ = _run(
            rls_env.admin,
            "SELECT tenant_id, status FROM access_reviews ORDER BY tenant_id",
        )
        assert rows == [(T1, "overdue"), (T2, "overdue")]

    def test_discovery_tenant_resolution_needs_explicit_scope(
        self, wired, dal, rls_env
    ):
        from apps.worker.discovery.service import DiscoveryService

        org_t2, _ = _run(
            rls_env.admin,
            "SELECT id FROM organizations WHERE tenant_id = :t LIMIT 1",
            t=T2,
        )
        org_id = org_t2[0][0]
        service = DiscoveryService(dal)
        with pytest.raises(ValueError, match="Cannot resolve tenant"):
            service._tenant_for_org(org_id)  # unscoped worker sees nothing
        assert service._resolve_job_tenant(org_id) == T2
        with tenant_context(T2):
            assert service._tenant_for_org(org_id) == T2
        with tenant_context(T1):
            with pytest.raises(ValueError):
                service._tenant_for_org(org_id)
