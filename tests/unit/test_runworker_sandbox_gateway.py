"""Unit tests for the Warm Sandbox Pool, RestrictionGateway, and Elder Helper SDK."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from apps.runworker.elder_sdk_bundle import (
    ELDER_MJS_SDK,
    ELDER_PY_SDK,
    ELDER_RB_SDK,
    sdk_file_for_runtime,
    write_sdk_files,
)
from apps.runworker.gateway import (
    MAX_BODY_BYTES,
    RestrictionGateway,
    has_required_scope,
    map_request_to_scope,
    validate_egress_url,
)
from apps.runworker.k8s_runner import (
    K8sRunner,
    RunJobRequest,
    build_configmap_manifest,
    build_job_manifest,
)
from apps.runworker.main import (
    RunWorkerConfig,
    RunWorkerService,
    _request_from_payload,
)
from apps.runworker.sandbox_runner import SandboxRunner


def _mock_transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


class TestElderSdkBundle:
    """Verify pure-stdlib helper SDKs for Python, Node.js, and Ruby."""

    @pytest.mark.parametrize(
        ("runtime", "expected_file"),
        [
            ("python", "elder.py"),
            ("py", "elder.py"),
            ("nodejs", "elder.mjs"),
            ("node", "elder.mjs"),
            ("ruby", "elder.rb"),
            ("rb", "elder.rb"),
            ("rust", None),
            ("golang", None),
            ("wasm", None),
        ],
    )
    def test_sdk_file_for_runtime(
        self,
        runtime: str,
        expected_file: str | None,
    ) -> None:
        result = sdk_file_for_runtime(runtime)
        if expected_file is None:
            assert result is None
        else:
            assert result is not None
            filename, source = result
            assert filename == expected_file
            assert len(source) > 100

    def test_python_sdk_compiles_and_exports_all_methods(self) -> None:
        compiled = compile(ELDER_PY_SDK, "elder.py", "exec")
        ns: dict[str, object] = {}
        exec(compiled, ns)  # noqa: S102
        for method_name in (
            "get",
            "post",
            "put",
            "patch",
            "delete",
            "update_entity_metadata",
            "trigger_integration_sync",
            "push_integration",
            "trigger_webhook",
        ):
            assert callable(ns.get(method_name)), f"Missing {method_name} in elder.py"

    def test_node_and_ruby_sdks_contain_required_methods_and_env_fallbacks(
        self,
    ) -> None:
        for sdk_source in (ELDER_MJS_SDK, ELDER_RB_SDK):
            assert "ELDER_GATEWAY_URL" in sdk_source
            assert "ELDER_API_URL" in sdk_source
            assert "ELDER_GATEWAY_TOKEN" in sdk_source
            assert "ELDER_API_TOKEN" in sdk_source
            for method_name in (
                "update_entity_metadata",
                "trigger_integration_sync",
                "push_integration",
                "trigger_webhook",
            ):
                assert method_name in sdk_source

    def test_write_sdk_files_creates_helper_in_workspace(self, tmp_path: Path) -> None:
        written = write_sdk_files(tmp_path, "python")
        assert written == tmp_path / "elder.py"
        assert written.read_text(encoding="utf-8") == ELDER_PY_SDK
        assert write_sdk_files(tmp_path, "rust") is None


class TestScopeMappingAndEgressValidation:
    """Verify OIDC scope mapping and SSRF-safe egress validation."""

    @pytest.mark.parametrize(
        ("method", "path", "expected"),
        [
            ("GET", "/api/v1/entities", "entities:read"),
            ("GET", "/api/v1/entities/uuid-1?limit=10", "entities:read"),
            ("PATCH", "/api/v1/entities/uuid-1/metadata", "entities:write"),
            ("POST", "/api/v1/integrations/int-1/sync", "integrations:write"),
            ("PUT", "/api/v1/services/svc-1", "services:write"),
            ("DELETE", "/api/v1/webhooks/wh-1", "webhooks:delete"),
            ("GET", "/api/v1/../secrets/all", None),
            ("GET", "/internal/admin", None),
            ("GET", "/api/v2/entities", None),
        ],
    )
    def test_map_request_to_scope(
        self,
        method: str,
        path: str,
        expected: str | None,
    ) -> None:
        assert map_request_to_scope(method, path) == expected

    def test_has_required_scope_exact_and_wildcards(self) -> None:
        assert has_required_scope("entities:read", ["entities:read"]) is True
        assert has_required_scope("entities:write", ["entities:read"]) is False
        assert has_required_scope("entities:write", ["entities:*"]) is True
        assert has_required_scope("entities:delete", ["entities:admin"]) is True
        assert has_required_scope("entities:read", ["*:read"]) is True
        assert has_required_scope("entities:write", ["*:read"]) is False
        assert has_required_scope("entities:write", []) is False

    @pytest.mark.parametrize(
        ("url", "allowed", "resolved_ips", "expected_reason"),
        [
            ("https://api.example.com/v1", [], ["93.184.216.34"], "egress_not_allowed"),
            (
                "https://evil.com/v1",
                ["api.example.com"],
                ["93.184.216.34"],
                "domain_not_allowlisted",
            ),
            (
                "http://127.0.0.1/admin",
                ["127.0.0.1"],
                ["127.0.0.1"],
                "ip_literal_blocked",
            ),
            (
                "http://169.254.169.254/latest/meta-data/",
                ["169.254.169.254"],
                ["169.254.169.254"],
                "ip_literal_blocked",
            ),
            (
                "http://[::1]/admin",
                ["::1"],
                ["::1"],
                "ip_literal_blocked",
            ),
            (
                "http://localhost/health",
                ["localhost"],
                ["127.0.0.1"],
                "loopback_hostname_blocked",
            ),
            (
                "https://api.example.com/v1",
                ["api.example.com"],
                ["10.0.0.42"],
                "blocked_ip_address",
            ),
            (
                "https://api.example.com/v1",
                ["api.example.com"],
                ["172.16.5.4"],
                "blocked_ip_address",
            ),
            (
                "https://api.example.com/v1",
                ["api.example.com"],
                ["192.168.1.10"],
                "blocked_ip_address",
            ),
            (
                "https://api.example.com/v1",
                ["api.example.com"],
                ["169.254.169.254"],
                "blocked_ip_address",
            ),
            (
                "file:///etc/passwd",
                ["api.example.com"],
                ["93.184.216.34"],
                "unsupported_scheme",
            ),
            (
                "https://user:pass@api.example.com/v1",
                ["api.example.com"],
                ["93.184.216.34"],
                "userinfo_not_allowed",
            ),
            (
                "https://api.example.com/v1/../../secret",
                ["api.example.com"],
                ["93.184.216.34"],
                "invalid_path",
            ),
            (
                "https://api.example.com/v1\\evil",
                ["api.example.com"],
                ["93.184.216.34"],
                "invalid_url",
            ),
        ],
    )
    def test_validate_egress_url_blocks_ssrf(
        self,
        url: str,
        allowed: list[str],
        resolved_ips: list[str],
        expected_reason: str,
    ) -> None:
        ok, reason = validate_egress_url(
            url,
            allowed,
            dns_resolver=lambda _h, _p: resolved_ips,
        )
        assert ok is False
        assert reason == expected_reason

    def test_validate_egress_url_allows_public_allowlisted_domain(self) -> None:
        ok, normalized = validate_egress_url(
            "https://hooks.slack.com/services/abc",
            ["*.slack.com"],
            dns_resolver=lambda _h, _p: ["54.230.1.2"],
        )
        assert ok is True
        assert normalized == "https://hooks.slack.com/services/abc"


class TestRestrictionGateway:
    """Verify live ephemeral HTTP RestrictionGateway behavior."""

    def test_auth_scope_token_injection_and_audit_redaction(self) -> None:
        seen_upstream: list[httpx.Request] = []

        def _upstream(req: httpx.Request) -> httpx.Response:
            seen_upstream.append(req)
            return httpx.Response(200, json={"entity_id": "e-1", "ok": True})

        real_secret = "elder-real-secret-token-xyz-999"
        with RestrictionGateway(
            elder_api_url="http://hub-api:5000",
            elder_api_token=real_secret,
            granted_scopes=("entities:read",),
            execution_id="exec-gw-1",
            http_client=_mock_transport(_upstream),
        ) as gw:
            assert gw.url.startswith("http://127.0.0.1:")
            assert real_secret not in repr(gw)
            assert gw.gateway_token not in repr(gw)

            # Missing auth -> 401
            r_unauth = httpx.get(f"{gw.url}/api/v1/entities/e-1")
            assert r_unauth.status_code == 401

            # Wrong token -> 401
            r_wrong = httpx.get(
                f"{gw.url}/api/v1/entities/e-1",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert r_wrong.status_code == 401

            # Ungranted scope (POST requires entities:write) -> 403 locally
            r_forbidden = httpx.post(
                f"{gw.url}/api/v1/entities/e-1",
                json={"name": "nope"},
                headers={"Authorization": f"Bearer {gw.gateway_token}"},
            )
            assert r_forbidden.status_code == 403
            assert r_forbidden.json()["required_scope"] == "entities:write"
            assert len(seen_upstream) == 0

            # Granted scope (GET requires entities:read) -> forwarded with real token
            r_ok = httpx.get(
                f"{gw.url}/api/v1/entities/e-1",
                headers={"Authorization": f"Bearer {gw.gateway_token}"},
            )
            assert r_ok.status_code == 200
            assert r_ok.json() == {"entity_id": "e-1", "ok": True}
            assert len(seen_upstream) == 1
            assert seen_upstream[0].headers["Authorization"] == f"Bearer {real_secret}"

            # Audit log never leaks tokens
            serialized_audit = json.dumps(gw.audit_log)
            assert real_secret not in serialized_audit
            assert gw.gateway_token not in serialized_audit
            assert len(gw.audit_log) == 4

    def test_rate_limit_and_body_size_cap(self) -> None:
        def _upstream(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        with RestrictionGateway(
            elder_api_url="http://hub-api:5000",
            elder_api_token="tok",
            granted_scopes=("entities:read", "entities:write"),
            max_requests=2,
            max_body_bytes=128,
            http_client=_mock_transport(_upstream),
        ) as gw:
            headers = {"Authorization": f"Bearer {gw.gateway_token}"}
            # Oversized body -> 413 (still consumes request slot)
            r_big = httpx.post(
                f"{gw.url}/api/v1/entities",
                content=b"x" * 200,
                headers=headers,
            )
            assert r_big.status_code == 413

            # Second request succeeds
            r_ok = httpx.get(f"{gw.url}/api/v1/entities", headers=headers)
            assert r_ok.status_code == 200

            # Third request hits cap -> 429
            r_limited = httpx.get(f"{gw.url}/api/v1/entities", headers=headers)
            assert r_limited.status_code == 429
            assert r_limited.json()["error"] == "rate_limit_exceeded"
        assert MAX_BODY_BYTES == 2 * 1024 * 1024

    def test_egress_proxy_strips_elder_token_and_blocks_private_ips(self) -> None:
        seen_egress: list[httpx.Request] = []

        def _upstream(req: httpx.Request) -> httpx.Response:
            seen_egress.append(req)
            return httpx.Response(200, json={"external": True})

        with RestrictionGateway(
            elder_api_url="http://hub-api:5000",
            elder_api_token="super-secret-elder-jwt",
            allowed_egress_domains=("api.public.example",),
            http_client=_mock_transport(_upstream),
            dns_resolver=lambda host, _port: (
                ["93.184.216.34"] if host == "api.public.example" else ["10.0.0.1"]
            ),
        ) as gw:
            headers = {"Authorization": f"Bearer {gw.gateway_token}"}

            # Blocked IMDS IP literal
            r_imds = httpx.get(
                f"{gw.url}/_egress",
                params={"url": "http://169.254.169.254/latest/meta-data/"},
                headers=headers,
            )
            assert r_imds.status_code == 403
            assert r_imds.json()["reason"] == "ip_literal_blocked"

            # Allowed public domain
            r_ok = httpx.get(
                f"{gw.url}/_egress",
                headers={
                    **headers,
                    "X-Elder-Egress-Url": "https://api.public.example/status",
                },
            )
            assert r_ok.status_code == 200
            assert r_ok.json() == {"external": True}
            assert len(seen_egress) == 1
            assert "authorization" not in {k.lower() for k in seen_egress[0].headers}


class TestSandboxRunner:
    """Verify live warm SandboxRunner execution with built-in elder SDK & gateway."""

    def test_live_python_import_elder_against_gateway_and_token_isolation(
        self,
        tmp_path: Path,
    ) -> None:
        seen_calls: list[tuple[str, str, str, object]] = []
        real_api_token = "real-hub-api-jwt-do-not-expose"

        def _hub_api(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode("utf-8")) if req.content else None
            seen_calls.append(
                (req.method, req.url.path, req.headers.get("Authorization", ""), body)
            )
            return httpx.Response(200, json={"ok": True, "path": req.url.path})

        script = """
import json, os, elder

assert "real-hub-api-jwt-do-not-expose" not in json.dumps(dict(os.environ))
assert os.environ.get("ELDER_GATEWAY_URL", "").startswith("http://127.0.0.1:")
assert os.environ.get("ELDER_GATEWAY_TOKEN")
assert "ELDER_API_TOKEN" not in os.environ

r1 = elder.get("/api/v1/entities/ent-1")
r2 = elder.update_entity_metadata("ent-1", {"owner": "sre"})
r3 = elder.trigger_integration_sync("int-9", {"full": True})
r4 = elder.push_integration("int-9", {"records": [1, 2]})
r5 = elder.trigger_webhook("wh-4", {"event": "deployed"})

# Ungranted delete must raise PermissionError (403 from local gateway)
denied = False
try:
    elder.delete("/api/v1/entities/ent-1")
except PermissionError:
    denied = True

print(json.dumps({
    "r1": r1["ok"],
    "r2": r2["ok"],
    "r3": r3["ok"],
    "r4": r4["ok"],
    "r5": r5["ok"],
    "denied": denied,
}))
"""
        runner = SandboxRunner(
            elder_api_url="http://hub-api:5000",
            workspace_root=str(tmp_path),
            gateway_http_client=_mock_transport(_hub_api),
        )
        req = RunJobRequest(
            execution_id="exec-live-sdk",
            runtime="python",
            code=script,
            input_data={"trigger": "test"},
            timeout_seconds=15,
            memory_mb=256,
            granted_scopes=(
                "entities:read",
                "entities:write",
                "integrations:write",
                "webhooks:write",
            ),
            elder_api_token=real_api_token,
        )
        result = runner.execute(req)

        assert (
            result.status == "succeeded"
        ), f"logs={result.logs!r}, err={result.error!r}"
        assert result.exit_code == 0
        assert result.output == {
            "r1": True,
            "r2": True,
            "r3": True,
            "r4": True,
            "r5": True,
            "denied": True,
        }
        assert [c[:2] for c in seen_calls] == [
            ("GET", "/api/v1/entities/ent-1"),
            ("PATCH", "/api/v1/entities/ent-1/metadata"),
            ("POST", "/api/v1/integrations/int-9/sync"),
            ("POST", "/api/v1/integrations/int-9/push"),
            ("POST", "/api/v1/webhooks/wh-4/trigger"),
        ]
        assert all(c[2] == f"Bearer {real_api_token}" for c in seen_calls)
        # Workspace cleaned up in finally
        assert list(tmp_path.iterdir()) == []

    def test_python_without_granted_scopes_raises_permission_error(
        self,
        tmp_path: Path,
    ) -> None:
        script = """
import json, elder
try:
    elder.get("/api/v1/entities")
    print(json.dumps({"denied": False}))
except PermissionError as exc:
    print(json.dumps({"denied": True, "msg": str(exc)}))
"""
        runner = SandboxRunner(workspace_root=str(tmp_path))
        result = runner.execute(
            RunJobRequest(
                execution_id="exec-no-scopes",
                runtime="python",
                code=script,
                timeout_seconds=10,
            )
        )
        assert result.status == "succeeded", result.logs
        assert result.output["denied"] is True
        assert list(tmp_path.iterdir()) == []

    def test_periodic_on_logs_flush_streams_incremental_output(
        self,
        tmp_path: Path,
    ) -> None:
        script = """
import sys, time, json
print("step-1", flush=True)
time.sleep(0.15)
print("step-2", flush=True)
time.sleep(0.15)
print(json.dumps({"done": True}), flush=True)
"""
        flushed: list[str] = []
        runner = SandboxRunner(workspace_root=str(tmp_path), poll_interval=0.02)
        result = runner.execute(
            RunJobRequest(
                execution_id="exec-logs",
                runtime="python",
                code=script,
                timeout_seconds=10,
            ),
            on_logs=flushed.append,
            log_flush_interval=0.05,
        )
        assert result.status == "succeeded"
        assert result.output == {"done": True}
        assert len(flushed) >= 1
        assert "step-1" in flushed[0]

    def test_timeout_kills_subprocess_and_cleans_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        runner = SandboxRunner(workspace_root=str(tmp_path), poll_interval=0.02)
        result = runner.execute(
            RunJobRequest(
                execution_id="exec-timeout",
                runtime="python",
                code="import time\ntime.sleep(30)\n",
                timeout_seconds=1,
            )
        )
        assert result.status == "timeout"
        assert "timeout" in (result.error or "").lower()
        assert list(tmp_path.iterdir()) == []

    def test_wasm_detection_uses_wasmtime_availability(self) -> None:
        runner = SandboxRunner()
        with patch(
            "apps.runworker.sandbox_runner.shutil.which",
            side_effect=lambda name: (
                "/usr/bin/wasmtime" if name == "wasmtime" else None
            ),
        ):
            assert runner.can_execute("wasm") is True
            assert runner.can_execute("rust", code_url="https://s3/fn.wasm") is True
            assert runner.can_execute("ruby") is False

        with patch("apps.runworker.sandbox_runner.shutil.which", return_value=None):
            assert runner.can_execute("wasm") is False
            assert runner.can_execute("python") is True

    def test_python_handler_function_is_invoked_and_captured(self) -> None:
        runner = SandboxRunner()
        starter_code = (
            "def handler(input_data: dict) -> dict:\n"
            "    name = (input_data or {}).get('name', 'world')\n"
            "    print(f'greeting {name}')\n"
            "    return {'message': f'Hello, {name}!'}\n"
        )
        res = runner.execute(
            RunJobRequest(
                execution_id="exec-handler-sync",
                runtime="python",
                code=starter_code,
                input_data={"name": "Elder"},
                timeout_seconds=10,
            )
        )
        assert res.status == "succeeded"
        assert res.exit_code == 0
        assert "greeting Elder" in res.logs
        assert "Hello, Elder!" in res.logs
        assert res.output == {"message": "Hello, Elder!"}

        async_code = (
            "async def handler(input_data):\n"
            "    return {'async_ok': True, 'got': input_data.get('x')}\n"
        )
        res_async = runner.execute(
            RunJobRequest(
                execution_id="exec-handler-async",
                runtime="python",
                code=async_code,
                input_data={"x": 42},
                timeout_seconds=10,
            )
        )
        assert res_async.status == "succeeded"
        assert res_async.output == {"async_ok": True, "got": 42}


class TestPluggableRunnerAndManifestWiring:
    """Verify RUNS_RUNNER_BACKEND routing, token redaction, and K8s manifests."""

    def test_elder_api_token_redacted_in_repr_and_payload_parsed(self) -> None:
        secret = "eyJhbGciOiJIUzI1NiJ9.super-secret-payload.sig"
        req = _request_from_payload(
            {
                "execution_id": "exec-redacted",
                "runtime": "python",
                "code": "print(1)",
                "granted_scopes": ["entities:read", "webhooks:write"],
                "allowed_egress_domains": ["hooks.slack.com"],
                "elder_api_token": secret,
            }
        )
        assert req.granted_scopes == ("entities:read", "webhooks:write")
        assert req.allowed_egress_domains == ("hooks.slack.com",)
        assert req.elder_api_token == secret
        assert secret not in repr(req)

    @pytest.mark.parametrize(
        ("env_val", "expected"),
        [
            (None, "k8s_job"),
            ("auto", "auto"),
            ("sandbox", "sandbox"),
            ("k8s_job", "k8s_job"),
            ("SANDBOX", "sandbox"),
        ],
    )
    def test_config_from_env_parses_runner_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_val: str | None,
        expected: str,
    ) -> None:
        if env_val is None:
            monkeypatch.delenv("RUNS_RUNNER_BACKEND", raising=False)
        else:
            monkeypatch.setenv("RUNS_RUNNER_BACKEND", env_val)
        monkeypatch.setenv("ELDER_INTERNAL_TOKEN", "ewk_unit_test_token")
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        cfg = RunWorkerConfig.from_env()
        assert cfg.runner_backend == expected

    def test_config_from_env_rejects_unknown_runner_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNS_RUNNER_BACKEND", "invalid_backend")
        monkeypatch.setenv("ELDER_INTERNAL_TOKEN", "ewk_unit_test_token")
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="Unsupported RUNS_RUNNER_BACKEND"):
            RunWorkerConfig.from_env()

    def test_auto_backend_routes_local_python_to_sandbox_and_missing_to_k8s(
        self,
    ) -> None:
        sandbox = SandboxRunner()
        k8s = K8sRunner(batch_api=MagicMock(), core_api=MagicMock())

        service_auto = RunWorkerService(
            RunWorkerConfig(
                runner_backend="auto",
                pod_runtime_class="kata",
                runtime_class_name="kata",
            ),
            sandbox_runner=sandbox,
            k8s_runner=k8s,
        )
        py_req = RunJobRequest(execution_id="e1", runtime="python", code="print(1)")
        rust_req = RunJobRequest(execution_id="e2", runtime="rust", code="fn main(){}")

        with patch("apps.runworker.sandbox_runner.shutil.which", return_value=None):
            assert service_auto.select_runner(py_req) is sandbox
            assert service_auto.select_runner(rust_req) is k8s

        service_sandbox = RunWorkerService(
            RunWorkerConfig(runner_backend="sandbox", pod_runtime_class="kata"),
            sandbox_runner=sandbox,
            k8s_runner=k8s,
        )
        assert service_sandbox.select_runner(rust_req) is sandbox

        service_k8s = RunWorkerService(
            RunWorkerConfig(runner_backend="k8s_job", runtime_class_name="kata"),
            sandbox_runner=sandbox,
            k8s_runner=k8s,
        )
        assert service_k8s.select_runner(py_req) is k8s

    def test_k8s_manifests_mount_sdk_and_label_gateway_access(self) -> None:
        req_privileged = RunJobRequest(
            execution_id="exec-priv",
            runtime="python",
            code="import elder",
            granted_scopes=("entities:read",),
        )
        cm = build_configmap_manifest(req_privileged)
        assert "elder.py" in cm["data"]
        assert cm["data"]["elder.py"] == ELDER_PY_SDK

        job_priv = build_job_manifest(req_privileged)
        assert (
            job_priv["metadata"]["labels"]["elder.penguintech.io/gateway-access"]
            == "true"
        )
        assert (
            job_priv["spec"]["template"]["metadata"]["labels"][
                "elder.penguintech.io/gateway-access"
            ]
            == "true"
        )

        # Unprivileged job has no gateway-access label
        req_unpriv = RunJobRequest(
            execution_id="exec-unpriv",
            runtime="nodejs",
            code="console.log(1)",
        )
        cm_node = build_configmap_manifest(req_unpriv)
        assert "elder.mjs" in cm_node["data"]
        job_unpriv = build_job_manifest(req_unpriv)
        assert (
            "elder.penguintech.io/gateway-access"
            not in job_unpriv["metadata"]["labels"]
        )

        # InitContainer mode passes SDK env vars
        req_init = RunJobRequest(
            execution_id="exec-init-sdk",
            runtime="ruby",
            code="",
            code_url="https://s3.example/fn.rb",
            use_init_container=True,
        )
        job_init = build_job_manifest(req_init)
        init_env = {
            e["name"]: e["value"]
            for e in job_init["spec"]["template"]["spec"]["initContainers"][0]["env"]
        }
        assert init_env["ELDER_SDK_FILENAME"] == "elder.rb"
        assert init_env["ELDER_SDK_CONTENT"] == ELDER_RB_SDK
