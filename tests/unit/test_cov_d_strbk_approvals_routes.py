"""HTTP route tests for the streams approval-gate and approval endpoints.

Real Quart app, JWT verification and Postgres.  Covers gate CRUD, the
worker-facing request-approval pause, approve / reject decisions with
multi-approver thresholds, status, history (human vs AI) and tenant isolation.
"""

# flake8: noqa: E501
# ruff: noqa: F811

import uuid

import pytest

from apps.api.modules.streams.routes import approvals as ap
from tests.unit._cov_d_helpers import call, in_db, now, world  # noqa: F401
from tests.unit.test_cov_d_strbk_streams_routes import (  # noqa: F401
    _insert_stream,
    sw,
)

BASE = "/api/v1/streams"


def _mk_exec(db, sw_, sid, status="paused_for_approval", by=None, triggered_by=None):
    """Insert an execution and return its string id."""
    ts = now()
    eid = "ex-" + uuid.uuid4().hex[:10]
    db.stream_executions.insert(
        tenant_id=sw_["tenant"],
        playbook_id=sid,
        execution_id=eid,
        status=status,
        trigger_type="manual",
        triggered_by_identity_id=by,
        triggered_by=triggered_by,
        input_json={},
        started_at=ts,
        created_at=ts,
        updated_at=ts,
    )
    db.commit()
    return eid


def _mk_gate(db, sw_, sid, approvers, min_approvers=1, node="g1", enabled=True):
    """Insert an approval gate and return its row id."""
    ts = now()
    gid = db.stream_approval_gates.insert(
        tenant_id=sw_["tenant"],
        gate_id=str(uuid.uuid4()),
        playbook_id=sid,
        node_id=node,
        name="Gate " + node,
        description=None,
        require_approval=True,
        min_approvers=min_approvers,
        approvers=approvers,
        approver_groups=None,
        timeout_minutes=None,
        is_enabled=enabled,
        created_at=ts,
        updated_at=ts,
    )
    db.commit()
    return gid


@pytest.mark.asyncio
class TestGates:
    """Approval gate CRUD and the request-approval pause."""

    async def test_gate_crud_and_denials(self, app, sw):
        """Create (with validation) and list gates under access control."""
        sid = sw["sid"]
        url = f"{BASE}/{sid}/approval-gates"
        st, body = await call(app, "get", url, sw["tok"]())
        assert st == 200 and body == {"gates": [], "count": 0}
        assert (await call(app, "post", url, sw["tok"]("editor"), json={"name": "x"}))[
            0
        ] == 400
        st, body = await call(
            app,
            "post",
            url,
            sw["tok"]("editor"),
            json={
                "node_id": "n1",
                "name": "Prod",
                "description": "d",
                "min_approvers": 2,
                "approvers": [sw["owner"]],
                "approver_groups": [1],
                "timeout_minutes": 5,
                "is_enabled": False,
            },
        )
        assert st == 201 and body["min_approvers"] == 2 and body["is_enabled"] is False
        st, body = await call(
            app, "post", url, sw["tok"](), json={"node_id": "n2", "name": "Defaults"}
        )
        assert (
            st == 201 and body["require_approval"] is True and body["approvers"] == []
        )
        st, body = await call(app, "get", url, sw["tok"]("viewer"))
        assert st == 200 and body["count"] == 2 and body["gates"][0]["created_at"]
        assert (await call(app, "get", url, sw["tok"]("outsider")))[0] == 403
        assert (await call(app, "get", url, sw["tok"]("buser")))[0] == 404
        assert (
            await call(app, "get", f"{BASE}/999999999/approval-gates", sw["tok"]())
        )[0] == 404
        assert (
            await call(
                app,
                "post",
                url,
                sw["tok"]("viewer"),
                json={"node_id": "a", "name": "b"},
            )
        )[0] == 403
        assert (await call(app, "post", url, sw["tok"]("buser"), json={}))[0] == 404
        assert (
            await call(
                app, "post", f"{BASE}/999999999/approval-gates", sw["tok"](), json={}
            )
        )[0] == 404

    async def test_request_approval_validation(self, app, sw):
        """Bad approver ids / counts / timeouts are 400s."""
        db = sw["db"]
        eid = await in_db(lambda: _mk_exec(db, sw, sw["sid"], status="running"))
        url = f"{BASE}/{sw['sid']}/executions/{eid}/request-approval"
        for payload in (
            {"approver_ids": True},
            {"approver_ids": -4},
            {"approver_ids": 1.5},
            {"approver_ids": ["x"]},
            {"approver_ids": [True]},
            {"approver_ids": [0]},
            {"required_approvals": "abc"},
            {"required_approvals": 0},
            {"timeout_minutes": "abc"},
            {"timeout_minutes": 0},
        ):
            st, _ = await call(app, "post", url, sw["tok"](), json=payload)
            assert st == 400, payload

    async def test_request_approval_flow(self, app, sw):
        """Pausing creates the gate, resolves approvers and updates on repeat."""
        db = sw["db"]
        sid = sw["sid"]
        eid = await in_db(
            lambda: _mk_exec(db, sw, sid, status="running", by=sw["editor"])
        )
        url = f"{BASE}/{sid}/executions/{eid}/request-approval"
        # approvers default to the requester
        st, body = await call(
            app,
            "post",
            url,
            sw["tok"](),
            json={"node_id": "ha", "title": "T", "instructions": "i"},
        )
        assert (
            st == 200
            and body["approver_ids"] == [sw["editor"]]
            and body["status"] == "paused_for_approval"
        )
        # explicit approvers (string form, dedup) update the existing gate
        st, body = await call(
            app,
            "post",
            url,
            sw["tok"]("editor"),
            json={
                "node_id": "ha",
                "approver_ids": f"{sw['viewer']}, {sw['viewer']},{sw['owner']}",
                "required_approvals": "2",
                "timeout_minutes": None,
            },
        )
        assert (
            st == 200
            and body["approver_ids"] == [sw["viewer"], sw["owner"]]
            and body["required_approvals"] == 2
        )
        # a different node falls back to the latest gate's approvers
        st, body = await call(
            app,
            "post",
            url,
            sw["tok"](),
            json={"node_id": "other", "approver_ids": None},
        )
        assert body["approver_ids"] == [sw["viewer"], sw["owner"]]
        # execution without a requester falls back to the caller
        eid2 = await in_db(lambda: _mk_exec(db, sw, sid, status="running"))
        db_sid = await in_db(lambda: _insert_stream(db, sw["tenant"], sw["owner"]))
        eid3 = await in_db(lambda: _mk_exec(db, sw, db_sid, status="running"))
        st, body = await call(
            app,
            "post",
            f"{BASE}/{db_sid}/executions/{eid3}/request-approval",
            sw["tok"](),
            json={},
        )
        assert (
            st == 200
            and body["approver_ids"] == [sw["owner"]]
            and body["node_id"] == "human_approval"
        )
        # errors
        assert (
            await call(
                app,
                "post",
                f"{BASE}/{sid}/executions/nope/request-approval",
                sw["tok"](),
                json={},
            )
        )[0] == 404
        assert (
            await call(
                app,
                "post",
                f"{BASE}/999999999/executions/{eid}/request-approval",
                sw["tok"](),
                json={},
            )
        )[0] == 404
        assert (await call(app, "post", url, sw["tok"]("outsider"), json={}))[0] == 403
        assert (await call(app, "post", url, sw["tok"]("buser"), json={}))[0] == 404
        assert eid2


@pytest.mark.asyncio
class TestDecisions:
    """Approve / reject / status / my-approvals / history."""

    async def test_approve_threshold_and_guards(self, app, sw):
        """Approvals resume only at the threshold; guards reject bad callers."""
        db = sw["db"]
        sid = sw["sid"]
        await in_db(
            lambda: _mk_gate(db, sw, sid, [sw["owner"], sw["editor"]], min_approvers=2)
        )
        eid = await in_db(lambda: _mk_exec(db, sw, sid, by=sw["owner"]))
        url = f"{BASE}/executions/{eid}/approve"
        assert (await call(app, "post", url, sw["tok"]("viewer"), json={}))[
            0
        ] == 403  # readable, not an approver
        assert (await call(app, "post", url, sw["tok"]("outsider"), json={}))[
            0
        ] == 403  # cannot read stream
        assert (await call(app, "post", url, sw["tok"]("buser"), json={}))[0] == 404
        st, body = await call(app, "post", url, sw["tok"](), json={"comment": "ok"})
        assert (
            st == 200
            and body["resumed"] is False
            and body["approvals"] == 1
            and body["required"] == 2
        )
        st, body = await call(app, "post", url, sw["tok"](), json={})
        assert st == 400 and "Already" in body["error"]
        st, body = await call(app, "post", url, sw["tok"]("editor"), json=None)
        assert st == 200 and body["resumed"] is True
        st, body = await call(app, "post", url, sw["tok"]("editor"), json={})
        assert st == 400 and "not paused" in body["error"]
        assert (
            await call(
                app, "post", f"{BASE}/executions/nope/approve", sw["tok"](), json={}
            )
        )[0] == 404

    async def test_approve_missing_playbook_is_404(self, app, sw):
        """An execution whose playbook is gone (other tenant) reads as 404."""
        db = sw["db"]
        sid = await in_db(lambda: _insert_stream(db, sw["btenant"], sw["buser"]))
        eid = await in_db(lambda: _mk_exec(db, sw, sid))
        for action, payload in (("approve", {}), ("reject", {"comment": "no"})):
            st, _ = await call(
                app,
                "post",
                f"{BASE}/executions/{eid}/{action}",
                sw["tok"](),
                json=payload,
            )
            assert st == 404

    async def test_reject(self, app, sw):
        """Reject requires a comment and approver rights, then fails the execution."""
        db = sw["db"]
        sid = sw["sid"]
        await in_db(lambda: _mk_gate(db, sw, sid, [sw["owner"]]))
        eid = await in_db(lambda: _mk_exec(db, sw, sid))
        url = f"{BASE}/executions/{eid}/reject"
        assert (await call(app, "post", url, sw["tok"](), json={"comment": "  "}))[
            0
        ] == 400
        assert (
            await call(app, "post", url, sw["tok"]("viewer"), json={"comment": "no"})
        )[0] == 403
        assert (
            await call(app, "post", url, sw["tok"]("outsider"), json={"comment": "no"})
        )[0] == 403
        st, body = await call(
            app, "post", url, sw["tok"](), json={"comment": "bad plan"}
        )
        assert st == 200 and body["message"] == "Execution rejected"
        st, body = await call(app, "post", url, sw["tok"](), json={"comment": "again"})
        assert st == 400 and "not paused" in body["error"]
        assert (
            await call(
                app,
                "post",
                f"{BASE}/executions/nope/reject",
                sw["tok"](),
                json={"comment": "x"},
            )
        )[0] == 404
        st, body = await call(app, "get", f"{BASE}/executions/{eid}", sw["tok"]())
        assert (
            body["execution"]["status"] == "failed"
            and "bad plan" in body["execution"]["error_message"]
        )
        # double-decision guard on a second paused execution
        eid2 = await in_db(lambda: _mk_exec(db, sw, sid))
        url2 = f"{BASE}/executions/{eid2}/reject"
        assert (await call(app, "post", url2, sw["tok"](), json={"comment": "x"}))[
            0
        ] == 200
        await in_db(
            lambda: (
                db(db.stream_executions.execution_id == eid2).update(
                    status="paused_for_approval"
                ),
                db.commit(),
            )
        )
        st, body = await call(app, "post", url2, sw["tok"](), json={"comment": "x"})
        assert st == 400 and "Already" in body["error"]

    async def test_status_my_approvals_and_history(self, app, sw):
        """Status, pending list and audit history including AI decisions."""
        db = sw["db"]
        sid = sw["sid"]
        gid = await in_db(
            lambda: _mk_gate(db, sw, sid, [sw["owner"], sw["editor"]], min_approvers=3)
        )
        await in_db(
            lambda: _mk_gate(db, sw, sid, [sw["viewer"]], node="off", enabled=False)
        )
        e_user = await in_db(lambda: _mk_exec(db, sw, sid, by=sw["owner"]))
        e_anon = await in_db(lambda: _mk_exec(db, sw, sid, triggered_by="svc-bot"))
        e_none = await in_db(lambda: _mk_exec(db, sw, sid))
        # other-tenant paused execution on a foreign stream is skipped, orphan too
        bsid = await in_db(lambda: _insert_stream(db, sw["btenant"], sw["buser"]))
        await in_db(lambda: _mk_exec(db, sw, bsid))
        priv = await in_db(lambda: _insert_stream(db, sw["tenant"], sw["viewer"]))
        e_priv = await in_db(lambda: _mk_exec(db, sw, priv))
        await in_db(lambda: _mk_gate(db, sw, priv, [sw["owner"]], node="p"))

        st, body = await call(app, "get", f"{BASE}/my-approvals", sw["tok"]())
        assert st == 200
        ids = {p["execution_id"]: p for p in body["pending_approvals"]}
        assert {e_user, e_anon, e_none} <= set(ids) and e_priv not in ids
        assert (
            ids[e_anon]["requested_by"] == "svc-bot"
            and ids[e_none]["requested_by"] == "Unknown"
        )
        assert ids[e_user]["requested_by"].startswith("cd-user-a")
        # once decided it disappears from my list
        assert (
            await call(
                app,
                "post",
                f"{BASE}/executions/{e_user}/approve",
                sw["tok"](),
                json={"comment": "c"},
            )
        )[0] == 200
        st, body = await call(app, "get", f"{BASE}/my-approvals", sw["tok"]())
        assert e_user not in {p["execution_id"] for p in body["pending_approvals"]}
        # a user who approves nothing gets an empty list
        st, body = await call(app, "get", f"{BASE}/my-approvals", sw["tok"]("outsider"))
        assert body == {"pending_approvals": [], "count": 0}

        st, body = await call(
            app, "get", f"{BASE}/executions/{e_user}/approval-status", sw["tok"]()
        )
        assert (
            st == 200
            and body["count"] == 1
            and body["approvals"][0]["gate_name"].startswith("Gate")
        )
        assert body["approvals"][0]["approver"]["name"].startswith("cd-user-a")
        assert (
            await call(
                app,
                "get",
                f"{BASE}/executions/{e_user}/approval-status",
                sw["tok"]("outsider"),
            )
        )[0] == 404
        assert (
            await call(
                app, "get", f"{BASE}/executions/nope/approval-status", sw["tok"]()
            )
        )[0] == 404

        ts = now()

        def add_ai():
            """Insert a gate-less AI decision and a decision whose approver is gone."""
            db.stream_execution_approvals.insert(
                tenant_id=sw["tenant"],
                approval_id=str(uuid.uuid4()),
                execution_id=e_user,
                gate_id=None,
                approver_identity_id=None,
                decision="approve",
                comment="[AI] looks fine",
                created_at=ts,
                updated_at=ts,
            )
            db.stream_execution_approvals.insert(
                tenant_id=sw["tenant"],
                approval_id=str(uuid.uuid4()),
                execution_id="orphan-exec",
                gate_id=gid,
                approver_identity_id=sw["owner"],
                decision="reject",
                comment=None,
                created_at=ts,
                updated_at=ts,
            )
            db.stream_execution_approvals.insert(
                tenant_id=sw["tenant"],
                approval_id=str(uuid.uuid4()),
                execution_id=e_priv,
                gate_id=gid,
                approver_identity_id=sw["owner"],
                decision="approve",
                comment="p",
                created_at=ts,
                updated_at=ts,
            )
            db.commit()

        await in_db(add_ai)
        st, body = await call(
            app, "get", f"{BASE}/executions/{e_user}/approval-status", sw["tok"]()
        )
        assert {a["approver"]["name"] for a in body["approvals"]} >= {"Unknown"}
        assert any(a["gate_name"] == "Unknown Gate" for a in body["approvals"])
        st, body = await call(
            app, "get", f"{BASE}/approvals/history?limit=200", sw["tok"]()
        )
        assert st == 200
        kinds = {h["approver_type"] for h in body["history"]}
        assert kinds == {"ai", "human"}
        assert any(h["playbook_name"] == "Unknown Playbook" for h in body["history"])
        # the private stream's decision is hidden from the owner-of-nothing outsider
        st, body = await call(
            app, "get", f"{BASE}/approvals/history?limit=abc", sw["tok"]("outsider")
        )
        assert st == 200 and all(h["execution_id"] != e_priv for h in body["history"])
        st, body = await call(
            app, "get", f"{BASE}/approvals/history?limit=0", sw["tok"]()
        )
        assert body["count"] == 1


class TestParseApproverIds:
    """Pure parsing of approver id inputs."""

    def test_parse(self):
        """Lists, strings, ints and empties parse; bad values raise."""
        p = ap._parse_approver_ids
        assert p(None) == [] and p("") == []
        assert p(5) == [5]
        assert p([1, "2", 1]) == [1, 2]
        assert p((3,)) == [3]
        assert p("4, 5,,") == [4, 5]
        for bad in (True, 0, -1, 1.5, ["a"], [True], [0], {"a": 1}):
            with pytest.raises(ValueError):
                p(bad)
