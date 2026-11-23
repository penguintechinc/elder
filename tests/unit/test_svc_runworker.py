"""Unit tests for svc-runworker (Elder Runs isolated K8s function worker)."""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from apps.runworker import healthcheck
from apps.runworker.k8s_runner import (
    MAX_MEMORY_MB,
    MIN_MEMORY_MB,
    K8sRunner,
    RunExecutionResult,
    RunJobRequest,
    build_configmap_manifest,
    build_job_manifest,
    clamp_memory_mb,
    extract_structured_output,
    sanitize_k8s_name,
)
from apps.runworker.main import RunWorkerConfig, RunWorkerService
from apps.runworker.runtimes import RUNTIMES, get_runtime

_DIGEST_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


class TestRuntimes:
    """Verify all 5 supported runtimes have pinned digests and valid commands."""

    @pytest.mark.parametrize(
        "runtime_name",
        ["nodejs", "rust", "python", "golang", "ruby"],
    )
    def test_runtime_pinned_digest_and_command(self, runtime_name: str) -> None:
        spec = get_runtime(runtime_name)
        assert spec.name == runtime_name
        assert _DIGEST_RE.match(spec.image), f"Unpinned image: {spec.image}"
        assert spec.command, "Command must not be empty"
        joined = " ".join(spec.command)
        assert f"/workspace/{spec.filename}" in joined

    def test_exact_five_canonical_runtimes(self) -> None:
        assert set(RUNTIMES.keys()) == {"nodejs", "rust", "python", "golang", "ruby"}

    @pytest.mark.parametrize(
        ("alias", "expected"),
        [
            ("node", "nodejs"),
            ("js", "nodejs"),
            ("py", "python"),
            ("go", "golang"),
            ("rb", "ruby"),
            ("rs", "rust"),
        ],
    )
    def test_runtime_aliases(self, alias: str, expected: str) -> None:
        assert get_runtime(alias).name == expected

    def test_unsupported_runtime_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported runtime"):
            get_runtime("cobol")


class TestManifestBuilders:
    """Verify hardened Job and ConfigMap manifest construction."""

    def test_build_job_manifest_hardening_defaults(self) -> None:
        req = RunJobRequest(
            execution_id="exec-1234-ABCD",
            runtime="python",
            code="print('{\"ok\": true}')",
            input_data={"hello": "world"},
            timeout_seconds=45,
            memory_mb=256,
            env={"USER_FLAG": "1", "PATH": "/evil", "ELDER_RUN_INPUT": "tampered"},
        )
        manifest = build_job_manifest(req, namespace="elder-runs")

        assert manifest["apiVersion"] == "batch/v1"
        assert manifest["kind"] == "Job"
        assert manifest["metadata"]["namespace"] == "elder-runs"

        job_spec = manifest["spec"]
        assert job_spec["backoffLimit"] == 0
        assert job_spec["activeDeadlineSeconds"] == 45
        assert job_spec["ttlSecondsAfterFinished"] == 120

        pod_spec = job_spec["template"]["spec"]
        assert pod_spec["restartPolicy"] == "Never"
        assert pod_spec["automountServiceAccountToken"] is False
        assert "runtimeClassName" not in pod_spec
        assert pod_spec["securityContext"]["runAsNonRoot"] is True
        assert pod_spec["securityContext"]["runAsUser"] == 65532
        assert pod_spec["securityContext"]["runAsGroup"] == 65532
        assert pod_spec["securityContext"]["seccompProfile"] == {
            "type": "RuntimeDefault"
        }

        container = pod_spec["containers"][0]
        sc = container["securityContext"]
        assert sc["runAsNonRoot"] is True
        assert sc["runAsUser"] == 65532
        assert sc["runAsGroup"] == 65532
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["readOnlyRootFilesystem"] is True
        assert sc["capabilities"] == {"drop": ["ALL"]}
        assert sc["seccompProfile"] == {"type": "RuntimeDefault"}

        # Env protections
        env_map = {e["name"]: e["value"] for e in container["env"]}
        assert json.loads(env_map["ELDER_RUN_INPUT"]) == {"hello": "world"}
        assert env_map["USER_FLAG"] == "1"
        assert "PATH" not in env_map

        # Volumes: read-only /workspace ConfigMap + 128Mi /tmp emptyDir
        mounts = {m["mountPath"]: m for m in container["volumeMounts"]}
        assert mounts["/workspace"]["readOnly"] is True
        assert mounts["/tmp"]["name"] == "tmp"
        volumes = {v["name"]: v for v in pod_spec["volumes"]}
        assert volumes["tmp"]["emptyDir"] == {"sizeLimit": "128Mi"}

    def test_build_job_manifest_sets_runtime_class_when_configured(self) -> None:
        req = RunJobRequest(
            execution_id="exec-kata", runtime="rust", code="fn main(){}"
        )
        manifest = build_job_manifest(
            req,
            namespace="elder-runs",
            runtime_class_name="kata",
        )
        assert manifest["spec"]["template"]["spec"]["runtimeClassName"] == "kata"

    @pytest.mark.parametrize(
        ("requested", "expected_limit"),
        [
            (16, MIN_MEMORY_MB),
            (64, 64),
            (512, 512),
            (4096, MAX_MEMORY_MB),
            (99999, MAX_MEMORY_MB),
        ],
    )
    def test_memory_clamping(self, requested: int, expected_limit: int) -> None:
        assert clamp_memory_mb(requested) == expected_limit
        req = RunJobRequest(
            execution_id="exec-mem",
            runtime="nodejs",
            code="console.log(1)",
            memory_mb=requested,
        )
        manifest = build_job_manifest(req)
        resources = manifest["spec"]["template"]["spec"]["containers"][0]["resources"]
        assert resources["limits"]["memory"] == f"{expected_limit}Mi"

    def test_build_configmap_manifest_contains_script_and_input(self) -> None:
        req = RunJobRequest(
            execution_id="exec-cm",
            runtime="golang",
            code="package main",
            input_data={"x": 42},
        )
        cm = build_configmap_manifest(req, namespace="elder-runs")
        assert cm["kind"] == "ConfigMap"
        assert cm["metadata"]["namespace"] == "elder-runs"
        assert cm["data"]["main.go"] == "package main"
        assert json.loads(cm["data"]["input.json"]) == {"x": 42}

    def test_sanitize_k8s_name_dns1123_length(self) -> None:
        name = sanitize_k8s_name("EXEC_" + "A" * 100)
        assert len(name) <= 63
        assert re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", name)


class TestStructuredOutput:
    """Verify JSON extraction from pod logs."""

    def test_extracts_last_non_empty_json_line(self) -> None:
        logs = 'starting run...\n{"result": 42, "ok": true}\n\n'
        assert extract_structured_output(logs) == {"result": 42, "ok": True}

    def test_returns_none_for_plain_text_last_line(self) -> None:
        assert extract_structured_output('{"a": 1}\ndone!\n') is None
        assert extract_structured_output("") is None


class TestK8sRunner:
    """Verify K8sRunner lifecycle and ConfigMap cleanup with mocked K8s APIs."""

    def test_execute_success_and_cleanup(self) -> None:
        batch_api = MagicMock()
        core_api = MagicMock()

        batch_api.read_namespaced_job_status.return_value = SimpleNamespace(
            status=SimpleNamespace(succeeded=1, failed=0, conditions=[])
        )
        pod = SimpleNamespace(
            metadata=SimpleNamespace(name="elder-run-exec-1-xyz"),
            status=SimpleNamespace(
                container_statuses=[
                    SimpleNamespace(
                        state=SimpleNamespace(terminated=SimpleNamespace(exit_code=0))
                    )
                ]
            ),
        )
        core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])
        core_api.read_namespaced_pod_log.return_value = 'info log\n{"sum": 3}\n'

        runner = K8sRunner(
            namespace="elder-runs",
            batch_api=batch_api,
            core_api=core_api,
            poll_interval=0.05,
        )
        result = runner.execute(
            RunJobRequest(
                execution_id="exec-1",
                runtime="python",
                code="print('{\"sum\": 3}')",
            )
        )

        assert result.status == "succeeded"
        assert result.exit_code == 0
        assert result.output == {"sum": 3}
        assert "info log" in result.logs
        core_api.create_namespaced_config_map.assert_called_once()
        batch_api.create_namespaced_job.assert_called_once()
        core_api.delete_namespaced_config_map.assert_called_once()

    def test_execute_timeout_cleans_up_configmap(self) -> None:
        batch_api = MagicMock()
        core_api = MagicMock()

        batch_api.read_namespaced_job_status.return_value = SimpleNamespace(
            status=SimpleNamespace(
                succeeded=0,
                failed=1,
                conditions=[SimpleNamespace(type="Failed", reason="DeadlineExceeded")],
            )
        )
        core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[])

        runner = K8sRunner(
            namespace="elder-runs",
            batch_api=batch_api,
            core_api=core_api,
            poll_interval=0.05,
        )
        result = runner.execute(
            RunJobRequest(
                execution_id="exec-timeout",
                runtime="ruby",
                code="sleep 999",
                timeout_seconds=5,
            )
        )

        assert result.status == "timeout"
        assert result.error is not None
        assert "activeDeadlineSeconds" in result.error
        core_api.delete_namespaced_config_map.assert_called_once()


class TestRunWorkerPollAndReport:
    """Verify async poll & report cycle with mocked httpx and K8sRunner."""

    def test_poll_once_executes_and_reports_results(self, tmp_path) -> None:
        reported: list[tuple[str, dict]] = []
        poll_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if (
                request.method == "GET"
                and request.url.path == "/api/v1/runs/internal/pending"
            ):
                poll_requests.append(request)
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "execution_id": "exec-abc",
                                "runtime": "python",
                                "code": "print('{\"ok\": true}')",
                                "input": {"k": "v"},
                                "timeout_seconds": 15,
                                "memory_mb": 128,
                            },
                            {
                                "execution_id": "exec-def",
                                "runtime": "nodejs",
                                "code": "console.log(JSON.stringify({n: 2}))",
                                "input": {},
                            },
                        ]
                    },
                )
            if request.method == "POST" and request.url.path.startswith(
                "/api/v1/runs/internal/executions/"
            ):
                reported.append((request.url.path, json.loads(request.content)))
                return httpx.Response(200, json={"accepted": True})
            return httpx.Response(404)

        mock_runner = MagicMock(spec=K8sRunner)
        mock_runner.execute.side_effect = lambda req: RunExecutionResult(
            status="succeeded",
            exit_code=0,
            logs='{"ok": true}\n',
            output={"ok": True, "exec": req.execution_id},
            duration_ms=42,
        )

        health_file = tmp_path / "runworker.healthy"
        cfg = RunWorkerConfig(
            elder_api_url="https://elder.example.test",
            elder_internal_token="super-secret-token-value",
            execution_namespace="elder-runs",
            worker_cluster="dal2-remote",
            max_concurrent=2,
            worker_id="runworker-pod-1",
            health_file=str(health_file),
        )
        # Token must never leak in repr
        assert "super-secret-token-value" not in repr(cfg)

        async def _run() -> int:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                service = RunWorkerService(cfg, runner=mock_runner, http_client=client)
                return await service.poll_once()

        count = asyncio.run(_run())
        assert count == 2
        assert len(poll_requests) == 1
        assert poll_requests[0].url.params["worker_id"] == "runworker-pod-1"
        assert poll_requests[0].url.params["cluster"] == "dal2-remote"
        assert (
            poll_requests[0].headers["authorization"]
            == "Bearer super-secret-token-value"
        )

        assert len(reported) == 2
        reported_by_path = dict(reported)
        assert (
            reported_by_path["/api/v1/runs/internal/executions/exec-abc/result"][
                "status"
            ]
            == "succeeded"
        )
        assert (
            reported_by_path["/api/v1/runs/internal/executions/exec-def/result"][
                "output"
            ]["exec"]
            == "exec-def"
        )
        assert health_file.exists()

    def test_runner_exception_reports_failed_status(self, tmp_path) -> None:
        reported: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "execution_id": "exec-boom",
                            "runtime": "python",
                            "code": "raise RuntimeError()",
                        }
                    ],
                )
            reported.append(json.loads(request.content))
            return httpx.Response(200, json={"accepted": True})

        mock_runner = MagicMock(spec=K8sRunner)
        mock_runner.execute.side_effect = RuntimeError("k8s api unreachable")

        cfg = RunWorkerConfig(
            elder_api_url="http://hub-api:5000",
            health_file=str(tmp_path / "healthy"),
        )

        async def _run() -> int:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                service = RunWorkerService(cfg, runner=mock_runner, http_client=client)
                return await service.poll_once()

        assert asyncio.run(_run()) == 1
        assert len(reported) == 1
        assert reported[0]["status"] == "failed"
        assert "k8s api unreachable" in reported[0]["error"]

    def test_healthcheck_passes_with_fresh_heartbeat(
        self, tmp_path, monkeypatch
    ) -> None:
        hb = tmp_path / "runworker.healthy"
        cfg = RunWorkerConfig(health_file=str(hb))
        service = RunWorkerService(cfg, runner=MagicMock(spec=K8sRunner))
        service.touch_heartbeat()

        monkeypatch.setenv("ELDER_API_URL", "http://hub-api:5000")
        monkeypatch.setenv("RUNS_HEALTH_FILE", str(hb))
        assert healthcheck.main() == 0

    def test_auth_headers_mints_service_worker_jwt_when_secret_configured(self) -> None:
        import jwt as pyjwt

        secret = "unit-test-runworker-jwt-secret-key-32b"
        cfg = RunWorkerConfig(
            elder_api_url="http://hub-api:5000",
            jwt_secret_key=secret,
        )
        assert secret not in repr(cfg)

        service = RunWorkerService(cfg, runner=MagicMock(spec=K8sRunner))
        headers = service._auth_headers()
        assert headers["Authorization"].startswith("Bearer ")
        token = headers["Authorization"].split(" ", 1)[1]

        decoded = pyjwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="elder-api",
            issuer="elder-api",
        )
        assert decoded["sub"] == "svc-runworker"
        assert decoded["type"] == "service_worker"
        assert decoded["tenant"] == "system"
        assert "runs:invoke" in decoded["scope"].split()
        assert 0 < decoded["exp"] - decoded["iat"] <= 600
