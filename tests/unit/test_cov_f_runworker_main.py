"""Tests for the runworker poll loop: config, JWT minting, API calls, dispatch and lifecycle."""

import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from apps.runworker import main as mod
from apps.runworker.k8s_runner import K8sRunner, RunExecutionResult, RunJobRequest
from apps.runworker.main import (
    RunWorkerConfig,
    RunWorkerService,
    _mint_service_worker_jwt,
    _parse_str_tuple,
    _request_from_payload,
)
from apps.runworker.sandbox_runner import SandboxRunner
from tests.unit._cov_f_helpers import fake_secret

ENV_KEYS = [
    "ELDER_API_URL", "ELDER_INTERNAL_TOKEN", "JWT_SECRET_KEY", "SECRET_KEY", "JWT_ISSUER",
    "JWT_AUDIENCE", "RUNS_EXECUTION_NAMESPACE", "RUNS_WORKER_CLUSTER", "RUNS_RUNTIME_CLASS_NAME",
    "RUNS_POLL_INTERVAL_SECONDS", "RUNS_MAX_CONCURRENT", "POD_NAME", "RUNS_WORKER_ID",
    "RUNS_HEALTH_FILE", "RUNS_HTTP_TIMEOUT_SECONDS", "RUNS_S3_FETCH_MODE",
    "RUNS_LOG_FLUSH_INTERVAL_SECONDS", "RUNS_RUNNER_BACKEND", "RUNS_HEARTBEAT_INTERVAL_SECONDS",
    "ELDER_VERSION", "APP_VERSION",
]  # fmt: skip


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every worker env var so each test starts from defaults."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


class FakeRunner:
    """Runner double returning a canned result and optionally pushing logs."""

    def __init__(
        self,
        result: RunExecutionResult | None = None,
        *,
        error: Exception | None = None,
        logs: str = "",
    ) -> None:
        """Configure the outcome."""
        self.result = result or RunExecutionResult("succeeded", 0, "log", {"a": 1}, 5)
        self.error = error
        self.logs = logs
        self.requests: list[RunJobRequest] = []

    def execute(
        self, req: RunJobRequest, *, on_logs: Any = None, log_flush_interval: float = 0
    ) -> RunExecutionResult:
        """Record the request, push logs if configured, and return the result."""
        self.requests.append(req)
        if self.logs and on_logs:
            on_logs(self.logs)
        if self.error:
            raise self.error
        return self.result


def _cfg(**kw: Any) -> RunWorkerConfig:
    """Build a config pointing at an invalid host with a stable worker id."""
    base: dict[str, Any] = {
        "elder_api_url": "http://hub.invalid",
        "worker_id": "w-1",
        "health_file": "/nonexistent-dir/x",
    }
    base.update(kw)
    return RunWorkerConfig(**base)


class Recorder:
    """httpx mock transport handler recording requests and replying per route."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        """Store route responses keyed by URL path suffix."""
        self.routes = routes or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and return the matching canned response."""
        self.requests.append(request)
        for suffix, reply in self.routes.items():
            if request.url.path.endswith(suffix) or str(request.url).endswith(suffix):
                if isinstance(reply, Exception):
                    raise reply
                return reply
        return httpx.Response(200, json={"ok": True})

    def client(self) -> httpx.AsyncClient:
        """Build an AsyncClient on this handler."""
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def _svc(
    cfg: RunWorkerConfig | None = None, rec: Recorder | None = None, **kw: Any
) -> tuple[RunWorkerService, Recorder]:
    """Build a service with a recording HTTP client and fake runners."""
    rec = rec or Recorder()
    kw.setdefault("sandbox_runner", SandboxRunner())
    kw.setdefault("k8s_runner", K8sRunner(batch_api=MagicMock(), core_api=MagicMock()))
    return RunWorkerService(cfg or _cfg(), http_client=rec.client(), **kw), rec


class TestJwtAndConfig:
    """JWT minting and environment parsing."""

    def test_b64url_unpadded(self) -> None:
        """Encoding strips padding and uses the URL-safe alphabet."""
        assert mod._b64url(b"\xfb\xff") == "-_8"

    def test_default_health_file(self) -> None:
        """The default liveness file lives in the temp dir."""
        assert mod._default_health_file().endswith("runworker.healthy")

    def test_mint_jwt_is_verifiable(self) -> None:
        """The token has valid HS256 signature and expected claims."""
        secret = fake_secret("jwt", "key")
        token = _mint_service_worker_jwt(
            secret, issuer="iss", audience="aud", ttl_seconds=60
        )
        head, payload, sig = token.split(".")
        expected = hmac.new(
            secret.encode(), f"{head}.{payload}".encode(), hashlib.sha256
        ).digest()
        assert sig == mod._b64url(expected)
        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        assert claims["iss"] == "iss" and claims["aud"] == "aud"
        assert claims["exp"] - claims["iat"] == 60
        assert claims["type"] == "service_worker" and claims["tenant"] == "system"

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without env vars sensible defaults apply."""
        monkeypatch.setattr(mod.socket, "gethostname", lambda: "host-a")
        cfg = RunWorkerConfig.from_env()
        assert cfg.elder_api_url == "http://hub-api:5000"
        assert cfg.worker_id == "host-a" and cfg.runner_backend == "auto"
        assert cfg.s3_fetch_mode == "worker" and cfg.poll_interval_seconds == 3.0
        assert cfg.max_concurrent == 5 and cfg.heartbeat_interval_seconds == 30.0
        assert cfg.log_flush_interval_seconds == mod.DEFAULT_LOG_FLUSH_INTERVAL_SECONDS

    def test_from_env_overrides_and_clamps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit env values are honoured and clamped to safe minimums."""
        token = fake_secret("internal")
        env = {
            "ELDER_API_URL": "http://api.invalid/", "ELDER_INTERNAL_TOKEN": token, "SECRET_KEY": "s",
            "JWT_ISSUER": "i", "JWT_AUDIENCE": "a", "RUNS_EXECUTION_NAMESPACE": "ns",
            "RUNS_WORKER_CLUSTER": " c1 ", "RUNS_RUNTIME_CLASS_NAME": " gvisor ",
            "RUNS_POLL_INTERVAL_SECONDS": "0.1", "RUNS_MAX_CONCURRENT": "0", "POD_NAME": "pod-9",
            "RUNS_HEALTH_FILE": "/tmp/hf", "RUNS_HTTP_TIMEOUT_SECONDS": "1",
            "RUNS_S3_FETCH_MODE": " INIT_CONTAINER ", "RUNS_LOG_FLUSH_INTERVAL_SECONDS": "5",
            "RUNS_RUNNER_BACKEND": "Sandbox", "RUNS_HEARTBEAT_INTERVAL_SECONDS": "1",
            "ELDER_VERSION": " 1.2.3 ",
        }  # fmt: skip
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        cfg = RunWorkerConfig.from_env()
        assert (
            cfg.elder_api_url == "http://api.invalid"
            and cfg.elder_internal_token == token
        )
        assert cfg.jwt_secret_key == "s" and cfg.worker_cluster == "c1"
        assert cfg.runtime_class_name == "gvisor" and cfg.poll_interval_seconds == 0.5
        assert cfg.max_concurrent == 1 and cfg.worker_id == "pod-9"
        assert cfg.http_timeout_seconds == 5.0 and cfg.s3_fetch_mode == "init_container"
        assert cfg.log_flush_interval_seconds == 5.0 and cfg.runner_backend == "sandbox"
        assert cfg.heartbeat_interval_seconds == 5.0 and cfg.worker_version == "1.2.3"
        assert token not in repr(cfg)

    def test_from_env_invalid_choices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unknown modes fall back to defaults and bad heartbeat values are ignored."""
        monkeypatch.setenv("RUNS_S3_FETCH_MODE", "weird")
        monkeypatch.setenv("RUNS_RUNNER_BACKEND", "weird")
        monkeypatch.setenv("RUNS_HEARTBEAT_INTERVAL_SECONDS", "abc")
        monkeypatch.setenv("JWT_SECRET_KEY", "k")
        monkeypatch.setenv("RUNS_WORKER_ID", "wid")
        monkeypatch.setenv("APP_VERSION", "9")
        cfg = RunWorkerConfig.from_env()
        assert (cfg.s3_fetch_mode, cfg.runner_backend) == ("worker", "auto")
        assert cfg.heartbeat_interval_seconds == 30.0
        assert cfg.worker_id == "wid" and cfg.worker_version == "9"


class TestPayloadParsing:
    """Pending-execution payload conversion."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, ()),
            ("a, b  c", ("a", "b", "c")),
            ([" x ", "", "y"], ("x", "y")),
            (("p",), ("p",)),
            ({"q"}, ("q",)),
            (5, ()),
        ],
    )
    def test_parse_str_tuple(self, raw: Any, expected: tuple[str, ...]) -> None:
        """Strings, collections and junk normalise to a clean tuple."""
        assert _parse_str_tuple(raw) == expected

    def test_request_from_full_payload(self) -> None:
        """All supported fields map into the request."""
        req = _request_from_payload(
            {
                "id": "e1", "runtime": "py", "code": "x", "input": {"a": 1}, "timeout_seconds": 7,
                "memory_mb": 99, "env_vars": {"K": 2}, "function_id": "f", "tenant_id": "t",
                "code_url": "https://s/a", "code_sha256": "ab", "storage_backend": "s3",
                "granted_scopes": "a:read b:write", "allowed_egress_domains": ["x.com"],
                "elder_api_token": "tok",
            }
        )  # fmt: skip
        assert req.execution_id == "e1" and req.input_data == {"a": 1}
        assert req.env == {"K": "2"} and req.function_id == "f" and req.tenant_id == "t"
        assert (
            req.granted_scopes == ("a:read", "b:write") and req.storage_backend == "s3"
        )
        assert req.allowed_egress_domains == ("x.com",) and req.elder_api_token == "tok"
        assert "tok" not in repr(req)

    def test_request_defaults(self) -> None:
        """Minimal payloads get defaults; input_data is the fallback key."""
        req = _request_from_payload(
            {"execution_id": "e2", "input_data": [1], "env": "notadict"}
        )
        assert req.input_data == [1] and req.env == {} and req.code_url is None
        assert req.timeout_seconds == mod.DEFAULT_TIMEOUT_SECONDS
        assert (
            req.memory_mb == mod.DEFAULT_MEMORY_MB and req.storage_backend == "database"
        )
        assert req.function_id is None and req.tenant_id is None

    def test_missing_execution_id(self) -> None:
        """Payloads without an id are rejected."""
        with pytest.raises(ValueError, match="execution_id"):
            _request_from_payload({"runtime": "python"})


class TestServiceBasics:
    """Runner selection, auth and heartbeat."""

    @pytest.mark.asyncio
    async def test_runner_wiring(self) -> None:
        """The default runner follows the configured backend."""
        sandbox, k8s = (
            SandboxRunner(),
            K8sRunner(batch_api=MagicMock(), core_api=MagicMock()),
        )
        explicit = FakeRunner()
        svc_sb, _ = _svc(
            _cfg(runner_backend="sandbox"), sandbox_runner=sandbox, k8s_runner=k8s
        )
        assert svc_sb.runner is sandbox
        svc_k8s, _ = _svc(
            _cfg(runner_backend="k8s_job"), sandbox_runner=sandbox, k8s_runner=k8s
        )
        assert svc_k8s.runner is k8s
        svc_x, _ = _svc(runner=explicit)
        assert svc_x.runner is explicit

    @pytest.mark.asyncio
    async def test_default_runners_and_client_created(self) -> None:
        """Runners and the HTTP client are built when not injected."""
        svc = RunWorkerService(_cfg(runtime_class_name="kata"))
        assert isinstance(svc.sandbox_runner, SandboxRunner)
        assert svc.k8s_runner.runtime_class_name == "kata"
        await svc._client.aclose()

    @pytest.mark.asyncio
    async def test_select_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit and configured backends win; auto prefers a capable sandbox."""
        req = _request_from_payload({"id": "e", "runtime": "python"})
        explicit = FakeRunner()
        assert _svc(runner=explicit)[0].select_runner(req) is explicit
        svc, _ = _svc(_cfg(runner_backend="sandbox"))
        assert svc.select_runner(req) is svc.sandbox_runner
        svc, _ = _svc(_cfg(runner_backend="k8s_job"))
        assert svc.select_runner(req) is svc.k8s_runner
        svc, _ = _svc(_cfg(runner_backend="auto"))
        monkeypatch.setattr(svc.sandbox_runner, "can_execute", lambda *a, **k: True)
        assert svc.select_runner(req) is svc.sandbox_runner
        monkeypatch.setattr(svc.sandbox_runner, "can_execute", lambda *a, **k: False)
        assert svc.select_runner(req) is svc.k8s_runner

    @pytest.mark.asyncio
    async def test_auth_headers(self) -> None:
        """Internal token wins, JWT is minted from the secret, otherwise only Accept."""
        tok = fake_secret("internal")
        h = _svc(_cfg(elder_internal_token=tok))[0]._auth_headers()
        assert (
            h["Authorization"] == f"Bearer {tok}" and h["X-Elder-Internal-Token"] == tok
        )
        h = _svc(_cfg(jwt_secret_key="k"))[0]._auth_headers()
        assert h["Authorization"].count(".") == 2 and "X-Elder-Internal-Token" not in h
        assert _svc()[0]._auth_headers() == {"Accept": "application/json"}

    @pytest.mark.asyncio
    async def test_touch_heartbeat(self, tmp_path: Path) -> None:
        """The heartbeat file gets a fresh timestamp; write errors are tolerated."""
        target = tmp_path / "sub" / "hb"
        _svc(_cfg(health_file=str(target)))[0].touch_heartbeat()
        assert target.read_text().strip().isdigit()
        blocker = tmp_path / "file"
        blocker.write_text("x")
        _svc(_cfg(health_file=str(blocker / "hb")))[0].touch_heartbeat()

    @pytest.mark.asyncio
    async def test_send_heartbeat_variants(self) -> None:
        """Heartbeats return the payload, wrap non-dicts, and swallow failures."""
        svc, rec = _svc(_cfg(worker_version="2.0", worker_cluster="c9"))
        assert await svc.send_heartbeat() == {"ok": True}
        body = json.loads(rec.requests[0].content)
        assert body["version"] == "2.0" and body["cluster"] == "c9"
        assert body["worker_id"] == "w-1" and svc._last_heartbeat_sent > 0

        svc, rec = _svc(rec=Recorder({"heartbeat": httpx.Response(200, json=[1])}))
        assert await svc.send_heartbeat() == {"ok": True}
        assert "version" not in json.loads(rec.requests[0].content)
        assert json.loads(rec.requests[0].content)["cluster"] == "in-cluster"

        svc, _ = _svc(rec=Recorder({"heartbeat": httpx.Response(500)}))
        assert await svc.send_heartbeat() is None


class TestApiCalls:
    """poll/flush/report HTTP interactions."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ([{"a": 1}, "junk"], [{"a": 1}]),
            ({"items": [{"b": 2}, 3]}, [{"b": 2}]),
            ({"executions": [{"c": 3}]}, [{"c": 3}]),
            ({"jobs": [{"d": 4}]}, [{"d": 4}]),
            ({"data": [{"e": 5}]}, [{"e": 5}]),
            ({"other": 1}, []),
            ("str", []),
        ],
    )
    async def test_poll_pending_shapes(self, payload: Any, expected: list[Any]) -> None:
        """Every supported response envelope is unwrapped to dict items."""
        svc, rec = _svc(
            _cfg(worker_cluster="c1"),
            Recorder({"pending": httpx.Response(200, json=payload)}),
        )
        assert await svc.poll_pending() == expected
        params = dict(rec.requests[0].url.params)
        assert params == {"worker_id": "w-1", "cluster": "c1", "worker_cluster": "c1"}

    @pytest.mark.asyncio
    async def test_poll_pending_without_cluster_and_error(self) -> None:
        """No cluster params are sent by default; HTTP errors propagate."""
        svc, rec = _svc(rec=Recorder({"pending": httpx.Response(200, json=[])}))
        await svc.poll_pending()
        assert dict(rec.requests[0].url.params) == {"worker_id": "w-1"}
        svc, _ = _svc(rec=Recorder({"pending": httpx.Response(503)}))
        with pytest.raises(httpx.HTTPStatusError):
            await svc.poll_pending()

    @pytest.mark.asyncio
    async def test_flush_logs(self) -> None:
        """Logs are posted to the execution endpoint with the worker id."""
        svc, rec = _svc()
        assert await svc.flush_logs("e1", "hello") is True
        req = rec.requests[0]
        assert req.url.path == "/api/v1/runs/internal/executions/e1/logs"
        assert json.loads(req.content) == {"logs": "hello", "worker_id": "w-1"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "sent"),
        [
            ("succeeded", "completed"),
            ("success", "completed"),
            ("timeout", "timed_out"),
            ("failed", "failed"),
        ],
    )
    async def test_report_result_status_mapping(self, status: str, sent: str) -> None:
        """Runner statuses map to the API's vocabulary."""
        svc, rec = _svc()
        res = RunExecutionResult(status, 1, "out", {"k": 1}, 12, "err")
        assert await svc.report_result("e1", res) is True
        body = json.loads(rec.requests[0].content)
        assert (
            body["status"] == sent
            and body["stdout_log"] == "out"
            and body["error"] == "err"
        )
        assert body["duration_ms"] == 12 and body["output"] == {"k": 1}
        assert rec.requests[0].url.path.endswith("/e1/result")


def _req(**kw: Any) -> RunJobRequest:
    """Build a request that carries an artifact URL by default."""
    base: dict[str, Any] = {
        "execution_id": "e1",
        "runtime": "python",
        "code": "",
        "code_url": "https://s3.invalid/code.py",
    }
    base.update(kw)
    return RunJobRequest(**base)


class TestResolveArtifact:
    """Artifact download, verification and delivery mode."""

    @pytest.mark.asyncio
    async def test_no_code_url_is_passthrough(self) -> None:
        """Requests without an artifact are returned unchanged."""
        svc, rec = _svc()
        req = _req(code_url=None, code="x")
        assert await svc._resolve_artifact(req) is req and rec.requests == []

    @pytest.mark.asyncio
    async def test_init_container_mode_defers_fetch(self) -> None:
        """init_container mode lets the pod fetch (non-sandbox runners only)."""
        svc, rec = _svc(_cfg(s3_fetch_mode="init_container"))
        out = await svc._resolve_artifact(_req(), target_runner=svc.k8s_runner)
        assert out.use_init_container is True and rec.requests == []
        out2 = await svc._resolve_artifact(_req(), target_runner=svc.sandbox_runner)
        assert out2.use_init_container is False

    @pytest.mark.asyncio
    async def test_text_download_with_sha(self) -> None:
        """Downloaded source is verified and decoded to text."""
        body = b"print('hi')"
        svc, _ = _svc(rec=Recorder({"code.py": httpx.Response(200, content=body)}))
        out = await svc._resolve_artifact(
            _req(code_sha256=hashlib.sha256(body).hexdigest())
        )
        assert out.code == "print('hi')" and out.use_init_container is False

    @pytest.mark.asyncio
    async def test_sha_mismatch(self) -> None:
        """A tampered artifact is rejected."""
        svc, _ = _svc(rec=Recorder({"code.py": httpx.Response(200, content=b"x")}))
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            await svc._resolve_artifact(_req(code_sha256="0" * 64))

    @pytest.mark.asyncio
    async def test_oversize_for_configmap_uses_init_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Large artifacts bound for K8s switch to the init-container path; the sandbox is unaffected."""
        monkeypatch.setattr(mod, "CONFIGMAP_MAX_CODE_BYTES", 3)
        svc, _ = _svc(rec=Recorder({"code.py": httpx.Response(200, content=b"abcdef")}))
        big = await svc._resolve_artifact(_req(), target_runner=svc.k8s_runner)
        assert big.use_init_container is True
        small = await svc._resolve_artifact(_req(), target_runner=svc.sandbox_runner)
        assert small.code == "abcdef" and small.use_init_container is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("url", "content"),
        [
            ("https://s3.invalid/m.bin", b"\x00asm\x01"),
            ("https://s3.invalid/m.WASM?x=1", b"plain"),
        ],
    )
    async def test_wasm_artifacts_are_base64(self, url: str, content: bytes) -> None:
        """Wasm artifacts are delivered base64-encoded by magic bytes or extension."""
        svc, _ = _svc(
            rec=Recorder(
                {
                    url.split("?")[0].rsplit("/", 1)[1]: httpx.Response(
                        200, content=content
                    )
                }
            )
        )
        out = await svc._resolve_artifact(
            _req(code_url=url), target_runner=svc.sandbox_runner
        )
        assert out.code == "base64:" + base64.b64encode(content).decode()

    @pytest.mark.asyncio
    async def test_download_error(self) -> None:
        """HTTP errors while fetching propagate."""
        svc, _ = _svc(rec=Recorder({"code.py": httpx.Response(404)}))
        with pytest.raises(httpx.HTTPStatusError):
            await svc._resolve_artifact(_req())


class TestProcessing:
    """Execution dispatch and result reporting."""

    @pytest.mark.asyncio
    async def test_success_reports_result_and_flushes_logs(self) -> None:
        """A run posts a warm-up log, live logs, then the final result."""
        runner = FakeRunner(logs="live")
        svc, rec = _svc(runner=runner)
        res = await svc.process_execution(
            {"id": "e1", "runtime": "python", "code": "x"}
        )
        assert res.status == "succeeded" and runner.requests[0].code == "x"
        paths = [r.url.path.rsplit("/", 1)[1] for r in rec.requests]
        assert paths == ["logs", "logs", "result"]
        assert "warming up" in json.loads(rec.requests[0].content)["logs"]
        assert json.loads(rec.requests[1].content)["logs"] == "live"

    @pytest.mark.asyncio
    async def test_runner_exception_becomes_failed_result(self) -> None:
        """Runner crashes are reported as failed executions."""
        svc, rec = _svc(runner=FakeRunner(error=RuntimeError("kaput")))
        res = await svc.process_execution({"id": "e1", "runtime": "python"})
        assert res.status == "failed" and res.error == "kaput"
        assert json.loads(rec.requests[-1].content)["status"] == "failed"

    @pytest.mark.asyncio
    async def test_log_flush_failures_are_tolerated(self) -> None:
        """Failed startup and live log posts never fail the run."""
        rec = Recorder({"/logs": httpx.Response(500)})
        svc, _ = _svc(rec=rec, runner=FakeRunner(logs="live"))
        res = await svc.process_execution({"id": "e1", "runtime": "python"})
        assert res.status == "succeeded"

    @pytest.mark.asyncio
    async def test_report_failure_is_swallowed(self) -> None:
        """If the result cannot be reported the result is still returned."""
        rec = Recorder({"/result": httpx.Response(500)})
        svc, _ = _svc(rec=rec, runner=FakeRunner())
        res = await svc.process_execution({"id": "e1", "runtime": "python"})
        assert res.status == "succeeded"

    @pytest.mark.asyncio
    async def test_artifact_resolution_failure(self) -> None:
        """A bad artifact turns into a failed execution without running code."""
        runner = FakeRunner()
        rec = Recorder({"code.py": httpx.Response(200, content=b"x")})
        svc, _ = _svc(rec=rec, runner=runner)
        res = await svc.process_execution(
            {
                "id": "e1",
                "runtime": "python",
                "code_url": "https://s3.invalid/code.py",
                "code_sha256": "0" * 64,
            }
        )
        assert res.status == "failed" and "mismatch" in (res.error or "")
        assert runner.requests == []

    @pytest.mark.asyncio
    async def test_bad_payload_raises(self) -> None:
        """Payloads without an id surface a ValueError to the caller."""
        svc, _ = _svc(runner=FakeRunner())
        with pytest.raises(ValueError):
            await svc.process_execution({})

    @pytest.mark.asyncio
    async def test_poll_once(self, tmp_path: Path) -> None:
        """One cycle touches the heartbeat, claims work and runs each item."""
        hb = tmp_path / "hb"
        rec = Recorder(
            {
                "pending": httpx.Response(
                    200,
                    json=[
                        {"id": "a", "runtime": "python"},
                        {"id": "b", "runtime": "python"},
                    ],
                )
            }
        )
        runner = FakeRunner()
        svc, _ = _svc(_cfg(health_file=str(hb)), rec, runner=runner)
        assert await svc.poll_once() == 2
        assert hb.exists() and len(runner.requests) == 2
        svc2, _ = _svc(
            _cfg(health_file=str(hb)),
            Recorder({"pending": httpx.Response(200, json=[])}),
            runner=runner,
        )
        assert await svc2.poll_once() == 0


class TestRunForever:
    """Main loop lifecycle."""

    @pytest.mark.asyncio
    async def test_loop_runs_heartbeats_and_stops(self, tmp_path: Path) -> None:
        """The loop heartbeats, survives poll errors, and exits on request_stop."""
        svc, _ = _svc(
            _cfg(
                health_file=str(tmp_path / "hb"),
                poll_interval_seconds=0.01,
                heartbeat_interval_seconds=0.0,
                elder_internal_token="t",
            )
        )
        svc.send_heartbeat = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]
        calls = {"n": 0}

        async def poll_once() -> int:
            """Fail once, then request a stop."""
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("api down")
            svc.request_stop()
            return 0

        svc.poll_once = poll_once  # type: ignore[method-assign]
        await asyncio.wait_for(svc.run_forever(), timeout=10)
        assert calls["n"] == 2 and svc.send_heartbeat.await_count >= 2
        assert not svc._client.is_closed

    @pytest.mark.asyncio
    async def test_owned_client_closed_and_no_signal_support(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An internally created client is closed; platforms without signal handlers still run."""
        svc = RunWorkerService(
            _cfg(poll_interval_seconds=0.01, heartbeat_interval_seconds=1000)
        )
        svc.send_heartbeat = AsyncMock(return_value=None)  # type: ignore[method-assign]
        svc.poll_once = AsyncMock(side_effect=lambda: svc.request_stop() or 0)  # type: ignore[method-assign]
        loop = asyncio.get_running_loop()

        def no_signals(*_a: Any, **_kw: Any) -> None:
            """Simulate a platform lacking add_signal_handler."""
            raise NotImplementedError

        monkeypatch.setattr(loop, "add_signal_handler", no_signals)
        await asyncio.wait_for(svc.run_forever(), timeout=10)
        assert svc._client.is_closed


class TestEntrypoint:
    """The module-level main()."""

    @pytest.mark.parametrize(
        ("level", "expected"), [("debug", 10), ("bogus", 20), (None, 20)]
    )
    def test_main_configures_logging_and_runs(
        self, monkeypatch: pytest.MonkeyPatch, level: str | None, expected: int
    ) -> None:
        """main() sets the log level, builds the service and runs the loop."""
        if level is not None:
            monkeypatch.setenv("LOG_LEVEL", level)
        else:
            monkeypatch.delenv("LOG_LEVEL", raising=False)
        basic = MagicMock()
        monkeypatch.setattr(mod.logging, "basicConfig", basic)
        built: list[Any] = []

        class FakeService:
            """Service stand-in exposing run_forever."""

            def __init__(self, config: RunWorkerConfig) -> None:
                """Record the config."""
                built.append(config)

            async def run_forever(self) -> None:
                """No-op loop."""

        monkeypatch.setattr(mod, "RunWorkerService", FakeService)
        ran: list[bool] = []

        def fake_run(coro: Any) -> None:
            """Drive the coroutine to completion without a new event loop."""
            ran.append(True)
            coro.close()

        monkeypatch.setattr(mod.asyncio, "run", fake_run)
        mod.main()
        assert basic.call_args.kwargs["level"] == expected
        assert built and ran == [True]

    def test_dunder_main_invokes_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Executing the module as a script calls main() once."""
        import runpy

        ran: list[bool] = []

        def fake_run(coro: Any) -> None:
            """Close the coroutine instead of running a loop."""
            ran.append(True)
            coro.close()

        monkeypatch.setattr(asyncio, "run", fake_run)
        monkeypatch.setattr(mod.logging, "basicConfig", MagicMock())
        runpy.run_module("apps.runworker.main", run_name="__main__")
        assert ran == [True]
