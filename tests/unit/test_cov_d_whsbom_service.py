"""Coverage tests for ``apps/api/services/webhooks/service.py``.

Runs against the real Postgres schema through the Quart app context. Only the
outbound HTTP transport (``webhook_transport`` fixture) and the redis counter
used for village-id minting are faked.
"""

# flake8: noqa: E501

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from quart import current_app
from sqlalchemy.exc import IntegrityError

from apps.api.services.webhooks import service as svc_mod
from apps.api.services.webhooks.service import (
    WebhookService,
    _as_utc,
    _is_village_id_conflict,
    _normalize_alert_destination_type,
    _village_id_seq,
    delivery_idempotency_key,
    generate_signature,
    resolve_tenant_ref,
    tenant_ref,
    tenant_ref_nowait,
)
from shared.webhooks import delivery as _delivery
from tests.unit import _cov_d_helpers as _cov_helpers

#: Re-export the shared two-tenant fixture for pytest discovery in this module.
world = _cov_helpers.world

URL = "https://hooks.example.com/cov"


def _name() -> str:
    """Return a unique webhook/rule name."""
    return "cov-" + uuid.uuid4().hex[:8]


def _mk(service: WebhookService, tenant: int, **kw) -> dict:
    """Create a webhook with sensible defaults for the given tenant."""
    args = {"name": _name(), "url": URL, "events": ["issue.assigned"]}
    args.update(kw)
    return service.create_webhook(tenant_id=tenant, **args)


async def _drain_background() -> None:
    """Wait until every fire-and-forget delivery task has finished."""
    for _ in range(300):
        if not _delivery._background_tasks:
            return
        await asyncio.sleep(0.02)


# ------------------------------------------------------------- pure helpers


def test_tenant_ref_paths(world) -> None:
    """Tenant references are cached, fall back when unknown, and stay bounded."""
    db = world["db"]
    tid = world["a"]["tenant"]
    fallback = tenant_ref_nowait(987654)
    assert fallback == svc_mod._fallback_tenant_ref(987654)
    first = tenant_ref(db, tid)
    assert tenant_ref(db, tid) == first
    assert tenant_ref_nowait(tid) == first
    assert tenant_ref(db, 987655) == svc_mod._fallback_tenant_ref(987655)

    class _Boom:
        """DB stand-in whose lookup always fails."""

        def __call__(self, *a, **k):
            """Raise to simulate an unavailable database."""
            raise RuntimeError("db down")

        tenants = SimpleNamespace(id=1)

    assert tenant_ref(_Boom(), 424242) == svc_mod._fallback_tenant_ref(424242)
    svc_mod._tenant_refs.update({i: "x" for i in range(svc_mod._TENANT_REF_CACHE_MAX)})
    tenant_ref(db, tid) if tid not in svc_mod._tenant_refs else None
    svc_mod._tenant_refs.pop(tid, None)
    assert tenant_ref(db, tid) == first
    assert len(svc_mod._tenant_refs) < svc_mod._TENANT_REF_CACHE_MAX


@pytest.mark.asyncio
async def test_resolve_tenant_ref_async(world) -> None:
    """Async resolution hits the cache after the first lookup."""
    tid = world["a"]["tenant"]
    a = await resolve_tenant_ref(world["db"], tid)
    b = await resolve_tenant_ref(world["db"], tid)
    assert a == b


def test_small_helpers() -> None:
    """Time normalisation, idempotency keys, signatures and vid parsing."""
    naive = datetime(2024, 1, 1)
    assert _as_utc(naive).tzinfo is UTC
    aware = datetime(2024, 1, 1, tzinfo=UTC)
    assert _as_utc(aware) is aware
    assert isinstance(_as_utc("junk"), datetime)
    assert delivery_idempotency_key(1, 2) == delivery_idempotency_key(1, 2)
    assert delivery_idempotency_key(1, 2) != delivery_idempotency_key(1, 3)
    assert len(generate_signature("k", "body")) == 64
    assert _village_id_seq(None) is None
    assert _village_id_seq("nodash") is None
    assert _village_id_seq("test-zzzz") is None
    assert _village_id_seq("00000001-000000000000000a") == 10
    assert WebhookService(MagicMock())._generate_signature(
        "k", "b"
    ) == generate_signature("k", "b")


def test_village_conflict_detection() -> None:
    """Only village_id unique violations count as mint collisions."""
    diag = SimpleNamespace(constraint_name="uq_webhooks_village_id")
    assert _is_village_id_conflict(IntegrityError("s", {}, SimpleNamespace(diag=diag)))
    other = SimpleNamespace(diag=SimpleNamespace(constraint_name="other"))
    assert not _is_village_id_conflict(IntegrityError("s", {}, other))
    assert _is_village_id_conflict(
        IntegrityError("ix_webhooks_village_id dup", {}, None)
    )
    assert not _is_village_id_conflict(IntegrityError("something else", {}, None))


def test_normalize_alert_destination() -> None:
    """Destination types are validated and upper-cased."""
    assert _normalize_alert_destination_type(" slack ") == "SLACK"
    for bad in (None, "", "  ", "fax", 5):
        with pytest.raises(Exception, match="Invalid destination_type"):
            _normalize_alert_destination_type(bad)


def test_insert_retry_on_village_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """A village_id collision re-mints and raises the counter; others propagate."""
    monkeypatch.setattr(
        "apps.api.utils.village_id.mint_village_id",
        lambda *a, **k: "00000001-0000000000000001",
    )
    conflict = IntegrityError(
        "s",
        {},
        SimpleNamespace(diag=SimpleNamespace(constraint_name="uq_webhooks_village_id")),
    )
    other = IntegrityError(
        "s", {}, SimpleNamespace(diag=SimpleNamespace(constraint_name="fk_other"))
    )

    class _Rows(list):
        """Row container."""

    class _Db:
        """Minimal DB double driving the insert/retry loop."""

        def __init__(self, errors):
            """Queue the errors to raise on successive inserts."""
            self.errors = list(errors)
            self.webhooks = SimpleNamespace(
                tenant_id=1,
                village_id=1,
                insert=self._insert,
            )

        def _insert(self, **kw):
            """Raise queued errors, then succeed."""
            if self.errors:
                raise self.errors.pop(0)
            return 7

        def __call__(self, q):
            """Return a select stub listing existing village ids."""
            return SimpleNamespace(
                select=lambda *a: _Rows(
                    [
                        SimpleNamespace(village_id="00000001-00000000000000ff"),
                        SimpleNamespace(village_id="bad"),
                    ]
                )
            )

    redis = MagicMock()
    redis.get.return_value = b"3"
    assert (
        svc_mod._insert_webhook_with_unique_village_id(_Db([conflict]), 1, redis, {})
        == 7
    )
    assert redis.set.call_args.args[1] == 255
    redis.get.return_value = None
    assert (
        svc_mod._insert_webhook_with_unique_village_id(_Db([conflict]), 1, None, {})
        == 7
    )
    with pytest.raises(IntegrityError):
        svc_mod._insert_webhook_with_unique_village_id(_Db([other]), 1, redis, {})
    with pytest.raises(IntegrityError):
        svc_mod._insert_webhook_with_unique_village_id(
            _Db([conflict] * 5), 1, redis, {}
        )


# -------------------------------------------------------------- webhook CRUD


@pytest.mark.asyncio
async def test_webhook_crud_and_validation(app, world) -> None:
    """Create/list/update/delete honour validation, filters and tenant scope."""
    tid = world["a"]["tenant"]
    other = world["b"]["tenant"]
    async with app.app_context():
        s = WebhookService(current_app.db)
        for kwargs, msg in [
            ({"url": "ftp://x"}, "must start with"),
            ({"events": []}, "non-empty list"),
            ({"events": "issue.assigned"}, "non-empty list"),
            ({"filter_issue_type": "nope"}, "Invalid filter_issue_type"),
            ({"filter_assignee_type": "identity"}, "set together"),
            (
                {"filter_assignee_type": "robot", "filter_assignee_id": 1},
                "Invalid filter_assignee_type",
            ),
        ]:
            with pytest.raises(Exception, match=msg):
                _mk(s, tid, **kwargs)

        hook = _mk(s, tid, secret="s" * 8, headers={"X-A": "1"}, metadata={"k": 1})
        _mk(s, tid, is_active=False)
        _mk(s, other)
        assert hook["secret"] == "***masked***" and hook["metadata"] == {"k": 1}
        assert len(s.list_webhooks(tid)) == 2
        assert len(s.list_webhooks(tid, enabled=True)) == 1
        assert len(s.list_webhooks(tid, enabled=False)) == 1
        with pytest.raises(Exception, match="not found"):
            s.get_webhook(hook["id"], other)

        upd = s.update_webhook(
            hook["id"],
            tid,
            name="renamed",
            url="http://hooks.example.com/new",
            events=["user.created"],
            secret="t" * 8,
            headers={"X-B": "2"},
            is_active=False,
            filter_issue_type="bug",
            filter_assignee_type="org_unit",
            filter_assignee_id=3,
            metadata={"m": 2},
        )
        assert upd["name"] == "renamed" and upd["filter_issue_type"] == "BUG"
        assert upd["filter_assignee_id"] == 3 and upd["is_active"] is False
        cleared = s.update_webhook(
            hook["id"],
            tid,
            filter_issue_type=None,
            filter_assignee_type=None,
            filter_assignee_id=None,
        )
        assert (
            cleared["filter_issue_type"] is None
            and cleared["filter_assignee_type"] is None
        )
        untouched = s.update_webhook(hook["id"], tid)
        assert untouched["name"] == "renamed"
        for kwargs, msg in [
            ({"url": "gopher://x"}, "must start with"),
            ({"events": "x"}, "must be a list"),
            ({"filter_issue_type": "nope"}, "Invalid filter_issue_type"),
            ({"filter_assignee_type": "robot"}, "Invalid filter_assignee_type"),
        ]:
            with pytest.raises(Exception, match=msg):
                s.update_webhook(hook["id"], tid, **kwargs)
        with pytest.raises(Exception, match="not found"):
            s.update_webhook(hook["id"], other, name="x")
        with pytest.raises(Exception, match="not found"):
            s.delete_webhook(hook["id"], other)
        assert s.delete_webhook(hook["id"], tid)["message"].startswith(
            "Webhook deleted"
        )
        with pytest.raises(Exception, match="not found"):
            s.get_webhook(hook["id"], tid)


# ------------------------------------------------------------------ delivery


@pytest.mark.asyncio
async def test_prepare_delivery_errors(app, world) -> None:
    """Deliveries to missing, disabled or unsubscribed webhooks are refused."""
    tid = world["a"]["tenant"]
    async with app.app_context():
        s = WebhookService(current_app.db)
        live = _mk(s, tid)
        off = _mk(s, tid, is_active=False)
        with pytest.raises(Exception, match="not found"):
            s._prepare_delivery(10**9, tid, "issue.assigned", {})
        with pytest.raises(Exception, match="disabled"):
            s._prepare_delivery(off["id"], tid, "issue.assigned", {})
        with pytest.raises(Exception, match="does not subscribe"):
            s._prepare_delivery(live["id"], tid, "other.event", {})


@pytest.mark.asyncio
async def test_deliver_event_and_record_paths(app, world, webhook_transport) -> None:
    """Event dispatch records success, failures, and bookkeeping errors."""
    tid = world["a"]["tenant"]
    async with app.app_context():
        s = WebhookService(current_app.db)
        hook = _mk(s, tid, secret="z" * 8, headers={"X-Extra": "1"})
        row = current_app.db.webhooks[hook["id"]]
        ok = await s.deliver_event(
            row, "issue.assigned", {"a": 1}, kind="issue_assigned"
        )
        assert ok["success"] is True and ok["status_code"] == 200
        sent = webhook_transport.requests[-1].headers
        assert sent["X-Extra"] == "1" and "X-Elder-Signature" in sent

        webhook_transport.respond_with(400)
        bad = await s.deliver_event(
            row, "issue.assigned", {"a": 2}, kind="issue_assigned"
        )
        assert bad["success"] is False and "error" in bad

        private = _mk(s, tid, url="http://localhost/x")
        prow = current_app.db.webhooks[private["id"]]
        res = await s.deliver_event(prow, "issue.assigned", {}, kind="issue_assigned")
        assert res["success"] is False

        class _NoInsert(WebhookService):
            """Service whose outbox insert fails."""

            def _insert_event_delivery(self, *a, **k):
                """Raise to simulate a database failure."""
                raise RuntimeError("insert failed")

        failed = await _NoInsert(current_app.db).deliver_event(
            row, "issue.assigned", {}, kind="k"
        )
        assert failed["success"] is False

        class _NoRecord(WebhookService):
            """Service whose result persistence fails."""

            def _record_delivery_result(self, *a, **k):
                """Raise to simulate a database failure."""
                raise RuntimeError("record failed")

        webhook_transport.respond_with(200)
        out = await _NoRecord(current_app.db).deliver_event(
            row, "issue.assigned", {}, kind="k"
        )
        assert out["success"] is True


@pytest.mark.asyncio
async def test_attempt_delivery_engine_exception(app, world, monkeypatch) -> None:
    """An unexpected engine error becomes a failed delivery result."""
    tid = world["a"]["tenant"]
    async with app.app_context():
        s = WebhookService(current_app.db)
        hook = _mk(s, tid)
        row = current_app.db.webhooks[hook["id"]]
        did = s._insert_event_delivery(row.id, "issue.assigned", {})

        async def boom(*a, **k):
            """Fail like a broken engine."""
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(svc_mod._delivery, "deliver", boom)
        out = await s._attempt_delivery(did, row, {})
        assert out["success"] is False and out["attempts"] == 1


@pytest.mark.asyncio
async def test_deliver_redeliver_history_and_test(
    app, world, webhook_transport
) -> None:
    """Native delivery, retries recorded as progress, redelivery, history, test send."""
    tid = world["a"]["tenant"]
    other = world["b"]["tenant"]
    async with app.app_context():
        s = WebhookService(current_app.db)
        hook = _mk(s, tid, events=["a.b"])
        webhook_transport.respond_with(503, 503, 200)
        res = await s.deliver_webhook(hook["id"], tid, "a.b", {"x": 1}, max_attempts=3)
        assert res["success"] is True and res["attempts"] == 3

        webhook_transport.respond_with(404)
        failed = await s.deliver_webhook(
            hook["id"], tid, "a.b", {"x": 2}, max_attempts=1
        )
        assert failed["success"] is False
        history = s.get_webhook_deliveries(hook["id"], tid)
        assert {h["status"] for h in history} == {"success", "failed"}
        only_failed = s.get_webhook_deliveries(
            hook["id"], tid, status="failed", limit=5
        )
        assert len(only_failed) == 1 and only_failed[0]["error_message"]
        with pytest.raises(Exception, match="not found"):
            s.get_webhook_deliveries(hook["id"], other)

        webhook_transport.respond_with(200)
        redone = await s.redeliver_webhook(hook["id"], tid, failed["delivery_id"])
        assert redone["success"] is True
        with pytest.raises(Exception, match="not found"):
            await s.redeliver_webhook(10**9, tid, failed["delivery_id"])
        with pytest.raises(Exception, match="Delivery .* not found"):
            await s.redeliver_webhook(hook["id"], tid, 10**9)
        other_hook = _mk(s, tid)
        with pytest.raises(Exception, match="does not belong"):
            await s.redeliver_webhook(other_hook["id"], tid, failed["delivery_id"])

        t = await s.test_webhook(hook["id"], tid)
        assert t["success"] is True
        with pytest.raises(Exception, match="not found"):
            await s.test_webhook(10**9, tid)
        empty_events = _mk(s, tid, events=["x.y"])
        current_app.db(current_app.db.webhooks.id == empty_events["id"]).update(
            events=None
        )
        current_app.db.commit()
        with pytest.raises(Exception, match="does not subscribe"):
            await s.test_webhook(empty_events["id"], tid)


@pytest.mark.asyncio
async def test_deliver_webhook_shed(app, world, monkeypatch) -> None:
    """A full pending-delivery cap sheds the caller with DeliveryShedError."""
    tid = world["a"]["tenant"]
    monkeypatch.setenv("WEBHOOK_MAX_PENDING", "1")
    async with app.app_context():
        s = WebhookService(current_app.db)
        hook = _mk(s, tid)
        tenant = await resolve_tenant_ref(current_app.db, tid)
        held = _delivery.reserve_slots_or_raise(tenant)[0]
        try:
            with pytest.raises(_delivery.DeliveryShedError):
                await s.deliver_webhook(hook["id"], tid, "issue.assigned", {})
            with pytest.raises(_delivery.DeliveryShedError):
                await s.redeliver_webhook(hook["id"], tid, 1)
        finally:
            _delivery.release_slot(held)


# ------------------------------------------------------ notification rules


@pytest.mark.asyncio
async def test_notification_rule_crud(app, world) -> None:
    """Rules are tenant-scoped, validated, and updated in place."""
    a, b = world["a"], world["b"]
    async with app.app_context():
        s = WebhookService(current_app.db)
        assert s.list_notification_rules(10**9) == []
        for kwargs, msg in [
            ({"channel": "sms"}, "Invalid channel"),
            ({"events": []}, "non-empty list"),
            ({"config": {}}, "non-empty dictionary"),
            ({"organization_id": b["org"]}, "not found in tenant"),
        ]:
            args = {
                "name": _name(),
                "channel": "slack",
                "events": ["x.y"],
                "config": {"webhook_url": URL},
                "organization_id": a["org"],
                "tenant_id": a["tenant"],
            }
            args.update(kwargs)
            with pytest.raises(Exception, match=msg):
                s.create_notification_rule(**args)

        rule = s.create_notification_rule(
            _name(),
            "slack",
            ["x.y"],
            {"webhook_url": URL},
            a["org"],
            description="d",
            tenant_id=a["tenant"],
        )
        legacy = s.create_notification_rule(
            _name(), "email", ["x.y"], {"recipients": ["r"]}, a["org"]
        )
        assert rule["enabled"] is True and rule["village_id"]
        assert len(s.list_notification_rules(a["tenant"])) == 2
        assert len(s.list_notification_rules(a["tenant"], a["org"], "slack")) == 1
        assert s.list_notification_rules(a["tenant"], b["org"]) == []
        assert s.get_notification_rule(rule["id"], a["tenant"])["name"] == rule["name"]
        with pytest.raises(Exception, match="not found"):
            s.get_notification_rule(rule["id"], b["tenant"])
        with pytest.raises(Exception, match="not found"):
            s.get_notification_rule(10**9, a["tenant"])

        upd = s.update_notification_rule(
            rule["id"],
            name="n2",
            events=["z"],
            config={"webhook_url": URL},
            description="dd",
            enabled=False,
            tenant_id=a["tenant"],
        )
        assert (
            upd["name"] == "n2" and upd["enabled"] is False and upd["events"] == ["z"]
        )
        with pytest.raises(Exception, match="Events must be a list"):
            s.update_notification_rule(rule["id"], events="x", tenant_id=a["tenant"])
        with pytest.raises(Exception, match="Config must be a dictionary"):
            s.update_notification_rule(rule["id"], config="x", tenant_id=a["tenant"])
        with pytest.raises(Exception, match="not found"):
            s.update_notification_rule(rule["id"], name="x", tenant_id=b["tenant"])
        s.update_notification_rule(legacy["id"], name="legacy")
        assert s.delete_notification_rule(rule["id"], a["tenant"])["message"]
        s.delete_notification_rule(legacy["id"])
        with pytest.raises(Exception, match="not found"):
            s.delete_notification_rule(rule["id"], a["tenant"])


@pytest.mark.asyncio
async def test_notification_channels(app, world, webhook_transport) -> None:
    """Email, Slack, Teams and PagerDuty channels send or fail cleanly."""
    a = world["a"]
    async with app.app_context():
        s = WebhookService(current_app.db)

        def _rule(channel: str, config: dict) -> dict:
            """Create a rule for a channel and return its id."""
            return s.create_notification_rule(
                _name(), channel, ["x.y"], config, a["org"], tenant_id=a["tenant"]
            )

        email = _rule("email", {"recipients": ["r"]})
        slack = _rule("slack", {"webhook_url": URL})
        teams = _rule("teams", {"webhook_url": URL})
        pd = _rule("pagerduty", {"routing_key": "k"})
        slack_bad = _rule("slack", {"other": 1})
        pd_bad = _rule("pagerduty", {"other": 1})

        assert (await s.test_notification_rule(email["id"], a["tenant"]))[
            "channel"
        ] == "email"
        assert (await s.test_notification_rule(slack["id"], a["tenant"]))[
            "success"
        ] is True
        assert (await s.test_notification_rule(teams["id"]))["success"] is True
        webhook_transport.respond_with(202)
        assert (await s.test_notification_rule(pd["id"], a["tenant"]))[
            "success"
        ] is True
        webhook_transport.respond_with(500)
        res = await s.test_notification_rule(slack["id"], a["tenant"])
        assert res["success"] is False and res["status_code"] == 500
        blocked = _rule("teams", {"webhook_url": "http://localhost/x"})
        res = await s.test_notification_rule(blocked["id"], a["tenant"])
        assert res["success"] is False and "error" in res
        assert "required" in (await s.test_notification_rule(slack_bad["id"]))["error"]
        assert "required" in (await s.test_notification_rule(pd_bad["id"]))["error"]
        fake = SimpleNamespace(channel="fax", config_json={"a": 1})
        out = await s._send_notification(fake, {})
        assert out["success"] is False and "Unsupported" in out["error"]
        with pytest.raises(Exception, match="not found"):
            await s.test_notification_rule(slack["id"], world["b"]["tenant"])


# ------------------------------------------------------- alert configurations


@pytest.mark.asyncio
async def test_alert_configuration_crud(app, world) -> None:
    """Alert configurations validate input and stay tenant-scoped."""
    a, b = world["a"], world["b"]
    async with app.app_context():
        s = WebhookService(current_app.db)
        assert s.list_alert_configurations(10**9) == []
        base = {
            "tenant_id": a["tenant"],
            "organization_id": a["org"],
            "destination_type": "slack",
            "name": _name(),
            "config": {"webhook_url": URL},
        }
        for override, msg in [
            ({"destination_type": "fax"}, "Invalid destination_type"),
            ({"name": "  "}, "name is required"),
            ({"name": 3}, "name is required"),
            ({"config": {}}, "non-empty dictionary"),
            ({"severity_filter": "high"}, "must be a list"),
            ({"organization_id": b["org"]}, "not found in tenant"),
        ]:
            with pytest.raises(Exception, match=msg):
                s.create_alert_configuration(**{**base, **override})
        cfg = s.create_alert_configuration(
            **base, severity_filter=["high"], enabled=False
        )
        s.create_alert_configuration(
            **{**base, "destination_type": "email", "name": _name()}
        )
        assert cfg["enabled"] == 0 and cfg["destination_type"] == "slack"
        assert cfg["severity_filter"] == ["high"]
        assert len(s.list_alert_configurations(a["tenant"])) == 2
        assert len(s.list_alert_configurations(a["tenant"], a["org"], "slack")) == 1
        assert s.get_alert_configuration(cfg["id"], a["tenant"])["id"] == cfg["id"]
        with pytest.raises(Exception, match="not found"):
            s.get_alert_configuration(cfg["id"], b["tenant"])
        with pytest.raises(Exception, match="not found"):
            s.get_alert_configuration(10**9, a["tenant"])

        upd = s.update_alert_configuration(
            cfg["id"],
            a["tenant"],
            name=" n2 ",
            enabled=True,
            config={"webhook_url": URL},
            severity_filter=None,
        )
        assert (
            upd["name"] == "n2"
            and upd["enabled"] == 1
            and upd["severity_filter"] is None
        )
        s.update_alert_configuration(cfg["id"], a["tenant"])
        for kwargs, msg in [
            ({"name": " "}, "cannot be empty"),
            ({"config": {}}, "non-empty dictionary"),
            ({"severity_filter": "x"}, "must be a list"),
        ]:
            with pytest.raises(Exception, match=msg):
                s.update_alert_configuration(cfg["id"], a["tenant"], **kwargs)
        assert s.delete_alert_configuration(cfg["id"], a["tenant"])["message"]
        with pytest.raises(Exception, match="not found"):
            s.delete_alert_configuration(cfg["id"], a["tenant"])


# ------------------------------------------------------------ outbox recovery


def _old_delivery(
    db, webhook_id: int, *, age: timedelta, status="pending", attempts=0
) -> int:
    """Insert an aged unfinished outbox row and return its id."""
    did = db.webhook_deliveries.insert(
        webhook_id=webhook_id,
        event_type="issue.assigned",
        request_payload={"event": "issue.assigned"},
        status=status,
        attempt_count=attempts,
        created_at=datetime.now(UTC) - age,
    )
    db.commit()
    return int(did)


@pytest.mark.asyncio
async def test_recover_pending_deliveries(
    app, world, webhook_transport, monkeypatch
) -> None:
    """Recovery resumes, expires, fails and skips orphaned deliveries."""
    tid = world["a"]["tenant"]
    monkeypatch.setenv("WEBHOOK_RECOVERY_MIN_AGE_SECONDS", "0")
    monkeypatch.setenv("WEBHOOK_RECOVERY_OBSERVE_SECONDS", "0")
    monkeypatch.setenv("WEBHOOK_RECOVERY_MAX_AGE_SECONDS", "3600")
    async with app.app_context():
        db = current_app.db
        s = WebhookService(db)

        async def no_sleep(_s: float) -> None:
            """Skip the observation wait."""

        monkeypatch.setenv("WEBHOOK_RECOVERY_BATCH", "0")
        assert (await s.recover_pending_deliveries(sleep=no_sleep))["candidates"] == 0
        monkeypatch.setenv("WEBHOOK_RECOVERY_BATCH", "1000")

        live = _mk(s, tid)
        gone = _mk(s, tid, is_active=False)
        resume = _old_delivery(db, live["id"], age=timedelta(minutes=10))
        resume_n = _old_delivery(
            db, live["id"], age=timedelta(minutes=10), status="retrying", attempts=2
        )
        expired = _old_delivery(db, live["id"], age=timedelta(hours=5))
        dead = _old_delivery(db, gone["id"], age=timedelta(minutes=10))
        out = await s.recover_pending_deliveries(sleep=no_sleep)
        assert out["candidates"] >= 4
        assert out["resumed"] >= 2 and out["expired"] >= 1 and out["failed"] >= 1
        states = {
            r.id: r.status
            for r in db(
                db.webhook_deliveries.id.belongs([resume, resume_n, expired, dead])
            ).select()
        }
        assert states[resume] == "success" and states[resume_n] == "success"
        assert states[expired] == "expired" and states[dead] == "failed"
        again = await s.recover_pending_deliveries(sleep=no_sleep)
        assert again["candidates"] == 0


@pytest.mark.asyncio
async def test_recovery_edges(app, world, webhook_transport, monkeypatch) -> None:
    """Lost claims are skipped, shed rows stay retrying, row errors are counted."""
    tid = world["a"]["tenant"]
    async with app.app_context():
        db = current_app.db
        s = WebhookService(db)
        hook = _mk(s, tid)
        raced = _old_delivery(db, hook["id"], age=timedelta(minutes=10), attempts=1)
        assert await s._resume_delivery(raced, 99) == "skipped"
        assert s._transition_delivery(raced, 1, 0.0, status="retrying") is True

        missing = _old_delivery(db, hook["id"], age=timedelta(minutes=10))
        db(db.webhook_deliveries.id == missing).update(webhook_id=hook["id"])
        db.commit()

        shed_row = _old_delivery(db, hook["id"], age=timedelta(minutes=10))
        monkeypatch.setattr(_delivery, "reserve_slot", lambda *a, **k: None)
        assert await s._resume_delivery(shed_row, 0) == "shed"
        assert db.webhook_deliveries[shed_row].status == "retrying"
        monkeypatch.undo()

        orphan = _old_delivery(db, hook["id"], age=timedelta(minutes=10))
        monkeypatch.setenv("WEBHOOK_RECOVERY_MIN_AGE_SECONDS", "0")
        monkeypatch.setenv("WEBHOOK_RECOVERY_OBSERVE_SECONDS", "0")

        async def boom(*a, **k):
            """Fail the claim-and-send step."""
            raise RuntimeError("resume failed")

        monkeypatch.setattr(WebhookService, "_resume_delivery", boom)

        async def no_sleep(_s: float) -> None:
            """Skip the observation wait."""

        out = await s.recover_pending_deliveries(sleep=no_sleep)
        assert out["failed"] >= 1 and orphan

        def bad_find(self, *a, **k):
            """Fail candidate discovery."""
            raise RuntimeError("find failed")

        monkeypatch.setattr(WebhookService, "_find_resumable_deliveries", bad_find)
        assert (await s.recover_pending_deliveries(sleep=no_sleep))["candidates"] == 0


# ------------------------------------------------------------------ broadcast


@pytest.mark.asyncio
async def test_broadcast_event(app, world, webhook_transport, monkeypatch) -> None:
    """Broadcast queues matching webhooks and rules, tolerates per-item failures."""
    a, b = world["a"], world["b"]
    async with app.app_context():
        db = current_app.db
        s = WebhookService(db)
        hook = _mk(s, a["tenant"], organization_id=a["org"], events=["thing.made"])
        _mk(s, a["tenant"], organization_id=a["org"], events=["other"])
        _mk(s, b["tenant"], organization_id=a["org"], events=["thing.made"])
        s.create_notification_rule(
            _name(),
            "slack",
            ["thing.made"],
            {"webhook_url": URL},
            a["org"],
            tenant_id=a["tenant"],
        )
        s.create_notification_rule(
            _name(),
            "email",
            ["unrelated"],
            {"recipients": []},
            a["org"],
            tenant_id=a["tenant"],
        )
        res = await s.broadcast_event("thing.made", {"p": 1}, a["org"], a["tenant"])
        await _drain_background()
        assert res["webhooks_triggered"] == 1 and res["webhooks_queued"] == 1
        assert res["notifications_queued"] == 1
        assert res["webhook_results"][0]["webhook_id"] == hook["id"]
        empty = await s.broadcast_event("nothing", {}, a["org"], a["tenant"])
        assert (
            empty["webhooks_triggered"] == 0 and empty["notifications_triggered"] == 0
        )

        async def boom(*args, **kw):
            """Fail the delivery preparation step."""
            raise RuntimeError("prep failed")

        monkeypatch.setattr(WebhookService, "_prepare_delivery", lambda *a, **k: 1 / 0)
        res = await s.broadcast_event("thing.made", {}, a["org"], a["tenant"])
        assert res["webhook_results"][0]["queued"] is False
        monkeypatch.undo()

        calls = {"n": 0}

        def failing_schedule(coro, **kw):
            """Close the coroutine and fail scheduling."""
            coro.close()
            calls["n"] += 1
            raise RuntimeError("schedule failed")

        monkeypatch.setattr(_delivery, "schedule_background", failing_schedule)
        res = await s.broadcast_event("thing.made", {}, a["org"], a["tenant"])
        assert res["notification_results"][0]["queued"] is False
        assert calls["n"] >= 2
