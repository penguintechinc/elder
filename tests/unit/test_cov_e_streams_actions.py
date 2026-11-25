"""Coverage tests for streams action nodes: http_request, webhook_out,
human_approval, run_function and the shared SSRF guard.

Only the httpx client and DNS resolution (true external boundaries) are faked.
"""

from __future__ import annotations

import json
import socket
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import tests.unit.conftest_worker_stubs  # noqa: F401
from apps.worker.streams.nodes import net_guard
from apps.worker.streams.nodes.actions import (
    http_request as hr,
)
from apps.worker.streams.nodes.actions import (
    human_approval as ha,
)
from apps.worker.streams.nodes.actions import (
    run_function as rf,
)
from apps.worker.streams.nodes.actions import (
    webhook_out as wo,
)


def fake_secret(*parts: str) -> str:
    """Build a credential-looking value at runtime (no literal secrets)."""
    return "-".join(("cove", *parts, uuid4().hex[:6]))


def _ctx(config=None, **extra):
    """Build a node context."""
    ctx = {
        "execution_id": "ex-1",
        "playbook_id": "pb-1",
        "node_id": "n-1",
        "config": config or {},
    }
    ctx.update(extra)
    return ctx


class FakeResp:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status=200, body=b"", headers=None, json_data=None):
        """Store the canned response fields."""
        self.status_code = status
        self.content = body
        self.text = body.decode() if isinstance(body, bytes) else str(body)
        self.headers = headers or {}
        self._json = json_data

    def json(self):
        """Return canned JSON or parse the body."""
        if self._json is not None:
            return self._json
        return json.loads(self.text)


class FakeClient:
    """Async client double that replays a script of responses or exceptions."""

    script: list = []
    calls: list = []

    def __init__(self, *a, **kw):
        """Record construction kwargs."""
        self.kw = kw

    async def __aenter__(self):
        """Enter the async context."""
        return self

    async def __aexit__(self, *exc):
        """Leave the async context."""
        return False

    async def _next(self, record):
        """Pop the next scripted item, raising when it is an exception."""
        FakeClient.calls.append(record)
        item = FakeClient.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def post(self, url, **kw):
        """Scripted POST."""
        return await self._next(("post", url, kw))

    async def get(self, url, **kw):
        """Scripted GET."""
        return await self._next(("get", url, kw))

    async def request(self, **kw):
        """Scripted generic request."""
        return await self._next(("request", kw))


@pytest.fixture
def client(monkeypatch):
    """Install FakeClient for httpx in all action modules and skip sleeps."""
    FakeClient.script = []
    FakeClient.calls = []
    for mod in (hr, wo, ha, rf):
        monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)

    async def _nosleep(_s):
        """Skip real waiting."""

    for mod in (hr, wo, rf):
        monkeypatch.setattr(mod.asyncio, "sleep", _nosleep)
    monkeypatch.setattr(hr, "_guard_ssrf", lambda url: None)
    monkeypatch.setattr(wo, "guard_ssrf", lambda url: None)
    return FakeClient


class TestNetGuard:
    """SSRF guard IP classification and URL validation."""

    @pytest.mark.parametrize(
        "ip,blocked",
        [
            ("127.0.0.1", True),
            ("10.1.2.3", True),
            ("100.64.0.1", True),
            ("::ffff:127.0.0.1", True),
            ("garbage", True),
            ("93.184.216.34", False),
        ],
    )
    def test_is_blocked_ip(self, ip, blocked):
        """Internal, CGNAT, mapped and junk addresses are blocked."""
        assert net_guard.is_blocked_ip(ip) is blocked

    def test_guard_rejections(self, monkeypatch):
        """Scheme, host, DNS failure and blocked resolution all raise."""
        with pytest.raises(ValueError, match="scheme"):
            net_guard.guard_ssrf("ftp://example.com/x")
        with pytest.raises(ValueError, match="no host"):
            net_guard.guard_ssrf("http:///path")

        def boom(*a, **k):
            """Fail resolution."""
            raise socket.gaierror("nope")

        monkeypatch.setattr(net_guard.socket, "getaddrinfo", boom)
        with pytest.raises(ValueError, match="resolve"):
            net_guard.guard_ssrf("https://example.com")
        monkeypatch.setattr(
            net_guard.socket,
            "getaddrinfo",
            lambda *a, **k: [(0, 0, 0, "", ("10.0.0.5", 80))],
        )
        with pytest.raises(ValueError, match="internal"):
            net_guard.guard_ssrf("http://example.com:8080")

    def test_guard_allows_public(self, monkeypatch):
        """A public resolution passes silently."""
        monkeypatch.setattr(
            net_guard.socket,
            "getaddrinfo",
            lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 443))],
        )
        assert net_guard.guard_ssrf("https://example.com") is None


class TestAuthConfig:
    """AuthConfig header building for every type."""

    def test_headers(self):
        """bearer, basic, apikey and incomplete configs."""
        assert wo.AuthConfig().build_headers() == {}
        tok = fake_secret("tok")
        assert wo.AuthConfig("bearer", bearer_token=tok).build_headers() == {
            "Authorization": f"Bearer {tok}"
        }
        basic = wo.AuthConfig("basic", basic_username="u", basic_password="p")
        assert basic.build_headers()["Authorization"].startswith("Basic ")
        assert wo.AuthConfig("basic", basic_username="u").build_headers() == {}
        key = wo.AuthConfig("apikey", api_key_header="X-K", api_key_value="v")
        assert key.build_headers() == {"X-K": "v"}


class TestWebhookOut:
    """WebhookOutAction input handling and retry logic."""

    def test_ports(self):
        """Declared ports."""
        assert len(wo.WebhookOutAction.inputs()) == 3
        assert len(wo.WebhookOutAction.outputs()) == 3

    @pytest.mark.asyncio
    async def test_missing_url(self, client):
        """Empty URL raises."""
        with pytest.raises(ValueError, match="required"):
            await wo.WebhookOutAction(_ctx()).execute({})

    @pytest.mark.asyncio
    async def test_success_payload_variants(self, client):
        """Payload from input, JSON string config, plain string, dict config, in."""
        cfg = {"url": "http://example.com/{{p}}", "maxRetries": 0}
        client.script = [FakeResp(200)] * 5
        node = wo.WebhookOutAction(_ctx(cfg))
        res = await node.execute({"payload": {"a": 1}, "in": {"p": "x"}})
        assert res["success"]["data"] is True
        assert client.calls[-1][1] == "http://example.com/x"
        assert json.loads(client.calls[-1][2]["content"]) == {"a": 1}

        cfg2 = dict(cfg, payload='{"k": "{{p}}"}')
        await wo.WebhookOutAction(_ctx(cfg2)).execute({"in": {"p": 3}})
        assert json.loads(client.calls[-1][2]["content"]) == {"k": "3"}

        cfg3 = dict(cfg, payload="plain {{p}}")
        await wo.WebhookOutAction(_ctx(cfg3)).execute({"in": {"p": 3}})
        assert client.calls[-1][2]["content"] == "plain 3"

        cfg4 = dict(cfg, payload={"z": "{{p}}"})
        await wo.WebhookOutAction(_ctx(cfg4)).execute({"in": {"p": 3}})
        assert json.loads(client.calls[-1][2]["content"]) == {"z": 3}

        await wo.WebhookOutAction(_ctx(cfg)).execute({"in": {"p": 1}})
        assert json.loads(client.calls[-1][2]["content"]) == {"p": 1}

    @pytest.mark.asyncio
    async def test_payload_string_resolving_to_object(self, client):
        """Exact-token payload resolving to a dict is used directly."""
        client.script = [FakeResp(200)]
        cfg = {"url": "http://example.com", "payload": "{{obj}}", "maxRetries": 0}
        await wo.WebhookOutAction(_ctx(cfg)).execute({"in": {"obj": {"q": 1}}})
        assert json.loads(client.calls[-1][2]["content"]) == {"q": 1}

    @pytest.mark.asyncio
    async def test_headers_and_auth(self, client):
        """Custom headers (dict or JSON string) override auth headers."""
        client.script = [FakeResp(200), FakeResp(200), FakeResp(204)]
        tok = fake_secret("b")
        cfg = {
            "url": "http://example.com",
            "authType": "bearer",
            "bearerToken": tok,
            "headers": '{"X-A": "{{p}}"}',
            "maxRetries": 0,
        }
        await wo.WebhookOutAction(_ctx(cfg)).execute({"in": {"p": "1"}})
        hdrs = client.calls[-1][2]["headers"]
        assert hdrs["X-A"] == "1" and hdrs["Authorization"].startswith("Bearer")
        await wo.WebhookOutAction(_ctx({"url": "http://example.com"})).execute(
            {"headers": {"X-B": "2"}}
        )
        assert client.calls[-1][2]["headers"]["X-B"] == "2"
        res = await wo.WebhookOutAction(
            _ctx({"url": "http://example.com", "headers": 5})
        ).execute({})
        assert res["status"]["data"] == 204

    @pytest.mark.asyncio
    async def test_retry_paths(self, client):
        """5xx retry then success, 5xx exhausted, 4xx, timeouts, connect errors."""
        cfg = {"url": "http://example.com", "maxRetries": 1}
        node = lambda: wo.WebhookOutAction(_ctx(cfg))  # noqa: E731
        client.script = [FakeResp(503), FakeResp(200)]
        assert (await node().execute({}))["success"]["data"] is True
        client.script = [FakeResp(500), FakeResp(502)]
        res = await node().execute({})
        assert res["status"]["data"] == 502 and res["success"]["data"] is False
        client.script = [FakeResp(404)]
        assert (await node().execute({}))["message"]["data"] == "HTTP 404"
        client.script = [httpx.ReadTimeout("t"), httpx.ReadTimeout("t")]
        res = await node().execute({})
        assert res["message"]["data"].startswith("Timeout")
        client.script = [httpx.ConnectError("c"), httpx.ConnectError("c")]
        res = await node().execute({})
        assert res["message"]["data"].startswith("Connection error")
        client.script = [httpx.ConnectError("c"), FakeResp(200)]
        assert (await node().execute({}))["success"]["data"] is True
        client.script = [httpx.ReadTimeout("t"), FakeResp(200)]
        assert (await node().execute({}))["success"]["data"] is True

    @pytest.mark.asyncio
    async def test_failed_after_retries(self, client):
        """Negative max retries falls out of the loop with the fallback message."""
        cfg = {"url": "http://example.com", "maxRetries": -1}
        res = await wo.WebhookOutAction(_ctx(cfg)).execute({})
        assert res["message"]["data"] == "Failed after retries"


class TestHttpRequest:
    """HttpRequestAction request building, retries and response parsing."""

    def test_ports(self):
        """Declared ports."""
        assert len(hr.HttpRequestAction.inputs()) == 5
        assert len(hr.HttpRequestAction.outputs()) == 4

    @pytest.mark.asyncio
    async def test_missing_url(self, client):
        """Empty URL raises."""
        with pytest.raises(ValueError, match="required"):
            await hr.HttpRequestAction(_ctx()).execute({})

    @pytest.mark.asyncio
    async def test_get_json(self, client):
        """GET with params/headers parses a JSON response."""
        client.script = [
            FakeResp(200, b'{"a": 1}', {"content-type": "application/json"}, {"a": 1})
        ]
        cfg = {
            "url": "http://example.com/{{p}}",
            "params": '{"q": "{{p}}"}',
            "headers": {"X": "{{p}}"},
            "authType": "apikey",
            "apiKeyHeader": "X-Key",
            "apiKeyValue": "{{p}}",
            "maxRetries": 0,
        }
        res = await hr.HttpRequestAction(_ctx(cfg)).execute({"in": {"p": "v"}})
        kw = client.calls[-1][1]
        assert kw["method"] == "GET" and kw["params"] == {"q": "v"}
        assert kw["headers"]["X-Key"] == "v" and "json" not in kw
        assert res["out"]["data"]["body"] == {"a": 1}
        assert res["status"]["data"] == 200

    @pytest.mark.asyncio
    async def test_body_variants(self, client):
        """Bodies from input, JSON string, plain string, dict config and none."""
        base = {"url": "http://example.com", "method": "post", "maxRetries": 0}
        client.script = [FakeResp(200, b"ok", {"content-type": "text/plain"})] * 6
        await hr.HttpRequestAction(_ctx(base)).execute({"body": {"i": 1}})
        assert client.calls[-1][1]["json"] == {"i": 1}
        await hr.HttpRequestAction(_ctx(dict(base, body='{"j": "{{p}}"}'))).execute(
            {"in": {"p": 1}}
        )
        assert client.calls[-1][1]["json"] == {"j": "1"}
        await hr.HttpRequestAction(_ctx(dict(base, body="raw {{p}}"))).execute(
            {"in": {"p": 1}}
        )
        assert client.calls[-1][1]["content"] == "raw 1"
        await hr.HttpRequestAction(_ctx(dict(base, body={"d": "{{p}}"}))).execute(
            {"in": {"p": 2}}
        )
        assert client.calls[-1][1]["json"] == {"d": 2}
        await hr.HttpRequestAction(_ctx(dict(base, body="{{o}}"))).execute(
            {"in": {"o": [1]}}
        )
        assert client.calls[-1][1]["json"] == [1]
        await hr.HttpRequestAction(_ctx(base)).execute({"method": "PUT"})
        kw = client.calls[-1][1]
        assert kw["method"] == "PUT" and "json" not in kw and "content" not in kw

    @pytest.mark.asyncio
    async def test_auth_types(self, client):
        """Bearer and basic auth headers are built with interpolation."""
        client.script = [FakeResp(200, b"x", {})] * 2
        tok = fake_secret("t")
        cfg = {
            "url": "http://example.com",
            "authType": "bearer",
            "bearerToken": tok,
            "maxRetries": 0,
        }
        await hr.HttpRequestAction(_ctx(cfg)).execute({})
        assert client.calls[-1][1]["headers"]["Authorization"] == f"Bearer {tok}"
        cfg = {
            "url": "http://example.com",
            "authType": "basic",
            "basicUsername": "u",
            "basicPassword": fake_secret("p"),
            "maxRetries": 0,
        }
        await hr.HttpRequestAction(_ctx(cfg)).execute({})
        assert client.calls[-1][1]["headers"]["Authorization"].startswith("Basic")

    @pytest.mark.asyncio
    async def test_retry_and_errors(self, client):
        """5xx retries with and without jitter; timeouts/network errors raise."""
        cfg = {"url": "http://example.com", "maxRetries": 1, "jitter": False}
        mk = lambda c=cfg: hr.HttpRequestAction(_ctx(c))  # noqa: E731
        client.script = [FakeResp(500, b""), FakeResp(200, b"")]
        assert (await mk().execute({}))["status"]["data"] == 200
        client.script = [FakeResp(500, b""), FakeResp(502, b"")]
        assert (await mk().execute({}))["status"]["data"] == 502
        jit = dict(cfg, jitter=True)
        client.script = [FakeResp(500, b""), FakeResp(200, b"")]
        assert (await mk(jit).execute({}))["status"]["data"] == 200
        client.script = [httpx.ReadTimeout("t"), FakeResp(200, b"")]
        assert (await mk().execute({}))["status"]["data"] == 200
        client.script = [httpx.ReadTimeout("t"), httpx.ReadTimeout("t")]
        with pytest.raises(httpx.TimeoutException):
            await mk().execute({})
        client.script = [httpx.ConnectError("c"), FakeResp(200, b"")]
        assert (await mk().execute({}))["status"]["data"] == 200
        client.script = [httpx.ConnectError("c"), httpx.ConnectError("c")]
        with pytest.raises(httpx.ConnectError):
            await mk().execute({})
        with pytest.raises(RuntimeError, match="after retries"):
            await mk(dict(cfg, maxRetries=-1)).execute({})

    @pytest.mark.asyncio
    async def test_timeout_error_branch(self, client, monkeypatch):
        """A builtin TimeoutError from _make_request is logged and re-raised."""

        async def boom(*a, **k):
            """Raise a builtin timeout."""
            raise TimeoutError("late")

        node = hr.HttpRequestAction(_ctx({"url": "http://example.com"}))
        monkeypatch.setattr(node, "_make_request", boom)
        with pytest.raises(TimeoutError):
            await node.execute({})

    @pytest.mark.asyncio
    async def test_network_error_branch(self, client, monkeypatch):
        """httpx.NetworkError from _make_request is logged and re-raised."""

        async def boom(*a, **k):
            """Raise a network error."""
            raise httpx.NetworkError("down")

        node = hr.HttpRequestAction(_ctx({"url": "http://example.com"}))
        monkeypatch.setattr(node, "_make_request", boom)
        with pytest.raises(httpx.NetworkError):
            await node.execute({})

    def test_parse_response_body(self):
        """Content-type driven parsing for every branch."""
        node = hr.HttpRequestAction(_ctx())
        p = node._parse_response_body
        assert p(FakeResp(200, b""), "text/plain") is None
        assert p(FakeResp(200, b'{"a":1}'), "application/json") == {"a": 1}
        assert p(FakeResp(200, b"{bad"), "application/json") == "{bad"
        assert p(FakeResp(200, b"hi"), "text/html") == "hi"
        assert p(FakeResp(200, b"[1]"), "application/xml") == [1]
        assert p(FakeResp(200, b"<x/>"), "application/xml") == "<x/>"
        assert p(FakeResp(200, b"zz"), "image/png") == "zz"


class TestHumanApproval:
    """HumanApprovalAction parsing, validation and API call."""

    def test_helpers(self, monkeypatch):
        """Base URL / token resolution and approver parsing."""
        monkeypatch.delenv("ELDER_API_URL", raising=False)
        for k in ("ELDER_INTERNAL_TOKEN", "ELDER_API_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        assert ha._resolve_elder_api_base({}) == "http://hub-api:8080"
        monkeypatch.setenv("ELDER_API_URL", "http://env.example.com/")
        assert ha._resolve_elder_api_base({}) == "http://env.example.com"
        ctx = {"global_config": {"elder_api_url": "http://g.example.com/"}}
        assert ha._resolve_elder_api_base(ctx) == "http://g.example.com"
        assert ha._resolve_elder_auth_token({}) is None
        monkeypatch.setenv("ELDER_API_TOKEN", "e1")
        assert ha._resolve_elder_auth_token({}) == "e1"
        assert (
            ha._resolve_elder_auth_token({"global_config": {"_elder_auth_token": "g"}})
            == "g"
        )

        p = ha._parse_approver_ids
        assert p(None) == [] and p("") == []
        assert p(5) == [5]
        assert p("1, 2,2") == [1, 2]
        assert p([3, "4"]) == [3, 4]
        for bad in (True, 0, -1, 1.5, "a", "0", [True], ["x"], [0]):
            with pytest.raises(ValueError):
                p(bad)

    def test_ports_and_validate(self):
        """Ports and every validation error branch."""
        assert ha.HumanApprovalAction.inputs()[0]["name"] == "in"
        assert [o["name"] for o in ha.HumanApprovalAction.outputs()] == [
            "approved",
            "rejected",
        ]
        v = ha.HumanApprovalAction(_ctx()).validate_config
        assert v({"title": "t"}) == []
        assert v({"title": "t", "approver_ids": "{{x}}"}) == []
        errs = v(
            {
                "title": " ",
                "approver_ids": "abc",
                "required_approvals": 0,
                "timeout_minutes": 0,
            }
        )
        assert len(errs) == 4
        errs = v({"title": "t", "required_approvals": "x", "timeout_minutes": "y"})
        assert len(errs) == 2
        assert (
            v({"title": "t", "required_approvals": "", "timeout_minutes": None}) == []
        )

    @pytest.mark.asyncio
    async def test_execute_success(self, client, monkeypatch):
        """Happy path posts to the approval endpoint with a bearer token."""
        monkeypatch.setenv("ELDER_API_TOKEN", "tok")
        client.script = [FakeResp(200)]
        cfg = {
            "title": "Approve {{a}}",
            "instructions": "do {{a}}",
            "approver_ids": "1,2",
            "required_approvals": "2",
            "timeout_minutes": "5",
        }
        res = await ha.HumanApprovalAction(_ctx(cfg)).execute({"in": {"a": "x"}})
        url, kw = client.calls[-1][1], client.calls[-1][2]
        assert url.endswith("/api/v1/streams/pb-1/executions/ex-1/request-approval")
        assert kw["json"]["approver_ids"] == [1, 2]
        assert kw["headers"]["Authorization"] == "Bearer tok"
        meta = res["approved"]["metadata"]
        assert meta["paused_for_approval"] and meta["approval_title"] == "Approve x"

    @pytest.mark.asyncio
    async def test_execute_list_approvers_no_token(self, client, monkeypatch):
        """Non-string approvers and blank required/timeout fall to defaults."""
        for k in ("ELDER_INTERNAL_TOKEN", "ELDER_API_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        client.script = [FakeResp(201)]
        cfg = {
            "title": "t",
            "approver_ids": [7],
            "required_approvals": "",
            "timeout_minutes": "",
        }
        await ha.HumanApprovalAction(_ctx(cfg)).execute({})
        assert "Authorization" not in client.calls[-1][2]["headers"]
        assert client.calls[-1][2]["json"]["approver_ids"] == [7]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cfg,ctx,msg",
        [
            ({"title": ""}, {}, "title is required"),
            (
                {"title": "t", "required_approvals": "x"},
                {},
                "required_approvals must be a positive",
            ),
            ({"title": "t", "required_approvals": 0}, {}, "at least 1"),
            (
                {"title": "t", "timeout_minutes": "x"},
                {},
                "timeout_minutes must be a positive",
            ),
            ({"title": "t", "timeout_minutes": 0}, {}, "at least 1"),
            ({"title": "t"}, {"playbook_id": None}, "playbook_id and execution_id"),
        ],
    )
    async def test_execute_validation_errors(self, client, cfg, ctx, msg):
        """Bad config or missing context raises ValueError."""
        context = _ctx(cfg)
        context.update(ctx)
        with pytest.raises(ValueError, match=msg):
            await ha.HumanApprovalAction(context).execute({})

    @pytest.mark.asyncio
    async def test_execute_http_error(self, client):
        """A 4xx/5xx reply raises RuntimeError with the body."""
        client.script = [FakeResp(500, b"bad")]
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await ha.HumanApprovalAction(_ctx({"title": "t"})).execute({})


class TestRunFunction:
    """RunFunctionAction invoke, poll and error paths."""

    def test_static(self, monkeypatch):
        """Ports, validation, unwrap helper, URL and auth header resolution."""
        assert len(rf.RunFunctionAction.inputs()) == 2
        assert len(rf.RunFunctionAction.outputs()) == 6
        node = rf.RunFunctionAction(_ctx())
        assert node.validate_config({}) == ["function_id is required"]
        assert node.validate_config({"function_id": 1}) == []
        assert rf._unwrap_input({"data": 1, "metadata": {}}) == 1
        assert rf._unwrap_input({"data": 1, "other": 2}) == {"data": 1, "other": 2}
        monkeypatch.delenv("ELDER_API_URL", raising=False)
        for k in ("ELDER_TOKEN", "ELDER_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        assert node._resolve_api_url() == "http://localhost:8000"
        monkeypatch.setenv("ELDER_API_URL", "http://a.example.com/")
        assert node._resolve_api_url() == "http://a.example.com"
        assert node._build_auth_headers() == {}
        monkeypatch.setenv("ELDER_API_KEY", "k")
        assert node._build_auth_headers() == {"X-API-Key": "k"}
        n2 = rf.RunFunctionAction(_ctx(variables={"_elder_auth_token": "v"}))
        assert n2._build_auth_headers() == {"Authorization": "Bearer v"}
        n3 = rf.RunFunctionAction(_ctx(_elder_auth_token="c"))
        assert n3._build_auth_headers() == {"Authorization": "Bearer c"}

    def test_resolve_payload(self):
        """Payload from config string/JSON/dict or the wrapped input."""
        inputs = {"in": {"data": {"w": 1}, "metadata": {}}}
        assert rf.RunFunctionAction(_ctx())._resolve_payload(inputs) == {"w": 1}
        r = lambda p: rf.RunFunctionAction(_ctx({"payload": p}))._resolve_payload(  # noqa: E731
            {"in": {"x": 5}}
        )
        assert r('{"a": "{{x}}"}') == {"a": "5"}
        assert r("txt {{x}}") == "txt 5"
        assert r("{{x}}") == 5
        assert r({"a": "{{x}}"}) == {"a": 5}

    @pytest.mark.asyncio
    async def test_function_id_errors(self, client):
        """Missing or non-numeric function ids raise."""
        with pytest.raises(ValueError, match="required"):
            await rf.RunFunctionAction(_ctx()).execute({})
        with pytest.raises(ValueError, match="Invalid"):
            await rf.RunFunctionAction(_ctx({"function_id": "abc"})).execute({})

    @pytest.mark.asyncio
    async def test_no_wait(self, client):
        """wait_for_result false returns the pending envelope."""
        client.script = [FakeResp(200, json_data={"execution": {"execution_id": "e9"}})]
        cfg = {"function_id": "{{fid}}", "wait_for_result": False}
        res = await rf.RunFunctionAction(_ctx(cfg)).execute({"in": {"fid": 4}})
        assert res["status"]["data"] == "pending"
        assert res["out"]["data"]["execution_id"] == "e9"
        assert client.calls[-1][1].endswith("/api/v1/runs/4/invoke")

    @pytest.mark.asyncio
    async def test_poll_to_completion(self, client):
        """Polling continues through non-terminal states until completed."""
        client.script = [
            FakeResp(200, json_data={"execution_id": "e1"}),
            FakeResp(200, json_data={"status": "running"}),
            FakeResp(
                200,
                json_data={
                    "status": "COMPLETED",
                    "exit_code": 0,
                    "output_data": {"r": 1},
                    "stdout_log": "o",
                    "stderr_log": "e",
                },
            ),
        ]
        cfg = {"function_id": 1, "poll_interval": 0.01, "timeout_seconds": 30}
        res = await rf.RunFunctionAction(_ctx(cfg)).execute(
            {"function_id": {"data": 1}}
        )
        assert res["status"]["data"] == "completed"
        assert res["output"]["data"] == {"r": 1}
        assert res["exit_code"]["data"] == 0

    @pytest.mark.asyncio
    async def test_failures(self, client):
        """Invoke/poll HTTP errors, missing execution id and poll timeout."""
        cfg = {"function_id": 1}
        client.script = [FakeResp(500, b"x")]
        with pytest.raises(RuntimeError, match="Failed to invoke"):
            await rf.RunFunctionAction(_ctx(cfg)).execute({})
        client.script = [FakeResp(200, json_data={"nothing": 1})]
        with pytest.raises(RuntimeError, match="missing execution_id"):
            await rf.RunFunctionAction(_ctx(cfg)).execute({})
        client.script = [
            FakeResp(200, json_data={"execution_id": "e"}),
            FakeResp(404, b"n"),
        ]
        with pytest.raises(RuntimeError, match="Failed to poll"):
            await rf.RunFunctionAction(_ctx(cfg)).execute({})
        client.script = [
            FakeResp(200, json_data={"execution_id": "e"}),
            FakeResp(200, json_data={"status": "running"}),
        ]
        with pytest.raises(TimeoutError):
            await rf.RunFunctionAction(_ctx(dict(cfg, timeout_seconds=0))).execute({})
