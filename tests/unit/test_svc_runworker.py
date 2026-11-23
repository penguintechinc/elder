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
    DEFAULT_LOG_FLUSH_INTERVAL_SECONDS,
    MAX_LOG_FLUSH_INTERVAL_SECONDS,
    MAX_MEMORY_MB,
    MAX_TIMEOUT_SECONDS,
    MIN_LOG_FLUSH_INTERVAL_SECONDS,
    MIN_MEMORY_MB,
    MIN_TIMEOUT_SECONDS,
    K8sRunner,
    RunExecutionResult,
    RunJobRequest,
    build_configmap_manifest,
    build_job_manifest,
    clamp_log_flush_interval,
    clamp_memory_mb,
    clamp_timeout_seconds,
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

    @pytest.mark.parametrize(
        ("requested", "expected_limit"),
        [
            (0, MIN_TIMEOUT_SECONDS),
            (-5, MIN_TIMEOUT_SECONDS),
            (1, 1),
            (60, 60),
            (300, 300),
            (301, MAX_TIMEOUT_SECONDS),
            (900, MAX_TIMEOUT_SECONDS),
            (3600, MAX_TIMEOUT_SECONDS),
        ],
    )
    def test_timeout_clamping_max_300s(
        self, requested: int, expected_limit: int
    ) -> None:
        assert MAX_TIMEOUT_SECONDS == 300
        assert clamp_timeout_seconds(requested) == expected_limit
        req = RunJobRequest(
            execution_id="exec-timeout-clamp",
            runtime="python",
            code="print(1)",
            timeout_seconds=requested,
        )
        manifest = build_job_manifest(req)
        assert manifest["spec"]["activeDeadlineSeconds"] == expected_limit

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

    def test_execute_flushes_live_logs_periodically_and_skips_container_creating(
        self,
    ) -> None:
        batch_api = MagicMock()
        core_api = MagicMock()

        running_status = SimpleNamespace(
            status=SimpleNamespace(succeeded=0, failed=0, conditions=[])
        )
        succeeded_status = SimpleNamespace(
            status=SimpleNamespace(succeeded=1, failed=0, conditions=[])
        )
        batch_api.read_namespaced_job_status.side_effect = [
            running_status,
            running_status,
            running_status,
            running_status,
            running_status,
            succeeded_status,
        ]

        creating_pod = SimpleNamespace(
            metadata=SimpleNamespace(name="elder-run-exec-live-pod"),
            status=SimpleNamespace(
                container_statuses=[
                    SimpleNamespace(
                        state=SimpleNamespace(
                            waiting=SimpleNamespace(reason="ContainerCreating")
                        )
                    )
                ]
            ),
        )
        running_pod = SimpleNamespace(
            metadata=SimpleNamespace(name="elder-run-exec-live-pod"),
            status=SimpleNamespace(container_statuses=[]),
        )
        finished_pod = SimpleNamespace(
            metadata=SimpleNamespace(name="elder-run-exec-live-pod"),
            status=SimpleNamespace(
                container_statuses=[
                    SimpleNamespace(
                        state=SimpleNamespace(terminated=SimpleNamespace(exit_code=0))
                    )
                ]
            ),
        )
        core_api.list_namespaced_pod.side_effect = [
            SimpleNamespace(items=[creating_pod]),
            SimpleNamespace(items=[running_pod]),
            SimpleNamespace(items=[running_pod]),
            SimpleNamespace(items=[running_pod]),
            SimpleNamespace(items=[finished_pod]),
        ]
        # 1st running read -> line 1; 2nd -> unchanged (no flush); 3rd -> line 1+2; final -> full
        core_api.read_namespaced_pod_log.side_effect = [
            "step 1\n",
            "step 1\n",
            "step 1\nstep 2\n",
            'step 1\nstep 2\n{"done": true}\n',
        ]

        flushed: list[str] = []
        runner = K8sRunner(
            namespace="elder-runs",
            batch_api=batch_api,
            core_api=core_api,
            poll_interval=0.05,
        )
        result = runner.execute(
            RunJobRequest(
                execution_id="exec-live",
                runtime="python",
                code="print('step 1')",
            ),
            on_logs=flushed.append,
            log_flush_interval=0.01,
        )

        assert result.status == "succeeded"
        assert result.exit_code == 0
        assert result.output == {"done": True}
        assert flushed == ["step 1\n", "step 1\nstep 2\n"]
        core_api.delete_namespaced_config_map.assert_called_once()

    def test_execute_ignores_on_logs_callback_exception(self) -> None:
        batch_api = MagicMock()
        core_api = MagicMock()

        running_status = SimpleNamespace(
            status=SimpleNamespace(succeeded=0, failed=0, conditions=[])
        )
        succeeded_status = SimpleNamespace(
            status=SimpleNamespace(succeeded=1, failed=0, conditions=[])
        )
        batch_api.read_namespaced_job_status.side_effect = [
            running_status,
            succeeded_status,
        ]
        pod = SimpleNamespace(
            metadata=SimpleNamespace(name="elder-run-exec-cb-err"),
            status=SimpleNamespace(
                container_statuses=[
                    SimpleNamespace(
                        state=SimpleNamespace(terminated=SimpleNamespace(exit_code=0))
                    )
                ]
            ),
        )
        core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])
        core_api.read_namespaced_pod_log.return_value = "live line\n"

        def _failing_cb(_logs: str) -> None:
            raise RuntimeError("transient flush error")

        runner = K8sRunner(
            namespace="elder-runs",
            batch_api=batch_api,
            core_api=core_api,
            poll_interval=0.05,
        )
        result = runner.execute(
            RunJobRequest(
                execution_id="exec-cb-err",
                runtime="python",
                code="print('live line')",
            ),
            on_logs=_failing_cb,
            log_flush_interval=0.01,
        )

        assert result.status == "succeeded"
        assert result.logs == "live line\n"

    @pytest.mark.parametrize(
        ("requested", "expected"),
        [
            (None, DEFAULT_LOG_FLUSH_INTERVAL_SECONDS),
            (-5, MIN_LOG_FLUSH_INTERVAL_SECONDS),
            (0, MIN_LOG_FLUSH_INTERVAL_SECONDS),
            (0.2, MIN_LOG_FLUSH_INTERVAL_SECONDS),
            (1, 1.0),
            (30, 30.0),
            (300, 300.0),
            (301, MAX_LOG_FLUSH_INTERVAL_SECONDS),
            (999, MAX_LOG_FLUSH_INTERVAL_SECONDS),
        ],
    )
    def test_log_flush_interval_clamping(
        self, requested: float | None, expected: float
    ) -> None:
        assert DEFAULT_LOG_FLUSH_INTERVAL_SECONDS == 30.0
        assert MIN_LOG_FLUSH_INTERVAL_SECONDS == 1.0
        assert MAX_LOG_FLUSH_INTERVAL_SECONDS == 300.0
        assert clamp_log_flush_interval(requested) == expected

    @pytest.mark.parametrize(
        ("env_value", "expected"),
        [
            (None, 30.0),
            ("", 30.0),
            ("0", 1.0),
            ("-10", 1.0),
            ("0.5", 1.0),
            ("15", 15.0),
            ("300", 300.0),
            ("301", 300.0),
            ("600", 300.0),
        ],
    )
    def test_config_from_env_clamps_log_flush_interval(
        self, monkeypatch, env_value: str | None, expected: float
    ) -> None:
        if env_value is None:
            monkeypatch.delenv("RUNS_LOG_FLUSH_INTERVAL_SECONDS", raising=False)
        else:
            monkeypatch.setenv("RUNS_LOG_FLUSH_INTERVAL_SECONDS", env_value)
        cfg = RunWorkerConfig.from_env()
        assert cfg.log_flush_interval_seconds == expected


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
        mock_runner.execute.side_effect = lambda req, **_kw: RunExecutionResult(
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

    def test_worker_fetches_presigned_s3_artifact_and_verifies_sha256(
        self, tmp_path
    ) -> None:
        import hashlib

        script_code = "print('{\"from_s3\": true}')\n"
        expected_sha = hashlib.sha256(script_code.encode("utf-8")).hexdigest()
        presigned_url = (
            "https://s3.example.test/elder-runs/runs/tenant-1/fn/v1/main.py?sig=secret"
        )
        executed_requests: list[RunJobRequest] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/runs/internal/pending":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "execution_id": "exec-s3-1",
                                "runtime": "python",
                                "code": "",
                                "storage_backend": "s3",
                                "code_url": presigned_url,
                                "code_sha256": expected_sha,
                                "input_data": {"a": 1},
                            }
                        ]
                    },
                )
            if str(request.url) == presigned_url:
                return httpx.Response(200, text=script_code)
            if request.method == "POST":
                return httpx.Response(200, json={"accepted": True})
            return httpx.Response(404)

        mock_runner = MagicMock(spec=K8sRunner)

        def _capture_execute(req: RunJobRequest, **_kw) -> RunExecutionResult:
            executed_requests.append(req)
            return RunExecutionResult(
                status="succeeded",
                exit_code=0,
                logs='{"from_s3": true}\n',
                output={"from_s3": True},
                duration_ms=15,
            )

        mock_runner.execute.side_effect = _capture_execute

        cfg = RunWorkerConfig(
            elder_api_url="https://elder.example.test",
            health_file=str(tmp_path / "healthy"),
            s3_fetch_mode="worker",
        )

        async def _run() -> int:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                service = RunWorkerService(cfg, runner=mock_runner, http_client=client)
                return await service.poll_once()

        assert asyncio.run(_run()) == 1
        assert len(executed_requests) == 1
        assert executed_requests[0].code == script_code
        assert executed_requests[0].use_init_container is False
        assert "sig=secret" not in repr(executed_requests[0])

    def test_worker_reports_failed_on_s3_sha256_mismatch(self, tmp_path) -> None:
        presigned_url = "https://s3.example.test/elder-runs/runs/tampered.py?sig=1"
        reported: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/runs/internal/pending":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "execution_id": "exec-s3-bad",
                                "runtime": "python",
                                "code": "",
                                "storage_backend": "s3",
                                "code_url": presigned_url,
                                "code_sha256": "f" * 64,
                            }
                        ]
                    },
                )
            if str(request.url) == presigned_url:
                return httpx.Response(200, text="tampered()")
            reported.append(json.loads(request.content))
            return httpx.Response(200, json={"accepted": True})

        mock_runner = MagicMock(spec=K8sRunner)
        cfg = RunWorkerConfig(
            elder_api_url="https://elder.example.test",
            health_file=str(tmp_path / "healthy"),
        )

        async def _run() -> int:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                service = RunWorkerService(cfg, runner=mock_runner, http_client=client)
                return await service.poll_once()

        assert asyncio.run(_run()) == 1
        mock_runner.execute.assert_not_called()
        assert len(reported) == 1
        assert reported[0]["status"] == "failed"
        assert "SHA-256 mismatch" in reported[0]["error"]

    def test_init_container_manifest_uses_emptydir_and_hides_presigned_url(
        self,
    ) -> None:
        req = RunJobRequest(
            execution_id="exec-init-1",
            runtime="nodejs",
            code="",
            code_url="https://s3.example.test/bucket/main.js?X-Amz-Signature=hidden",
            code_sha256="a" * 64,
            storage_backend="s3",
            use_init_container=True,
        )
        manifest = build_job_manifest(req, namespace="elder-runs")
        pod_spec = manifest["spec"]["template"]["spec"]

        assert len(pod_spec["initContainers"]) == 1
        init_c = pod_spec["initContainers"][0]
        assert init_c["name"] == "fetch-artifact"
        assert init_c["securityContext"]["runAsNonRoot"] is True
        assert init_c["securityContext"]["readOnlyRootFilesystem"] is True
        assert init_c["securityContext"]["allowPrivilegeEscalation"] is False

        runner_c = pod_spec["containers"][0]
        runner_env_names = {e["name"] for e in runner_c["env"]}
        assert "ELDER_ARTIFACT_URL" not in runner_env_names
        assert runner_c["volumeMounts"][0]["readOnly"] is True

        workspace_vol = pod_spec["volumes"][0]
        assert "emptyDir" in workspace_vol
        assert "configMap" not in workspace_vol

    def test_process_execution_pushes_live_logs_before_final_result(
        self, tmp_path
    ) -> None:
        posted: list[tuple[str, dict]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                posted.append((request.url.path, json.loads(request.content)))
                return httpx.Response(200, json={"updated": True})
            return httpx.Response(404)

        mock_runner = MagicMock(spec=K8sRunner)

        def _fake_execute(
            req: RunJobRequest,
            *,
            on_logs=None,
            log_flush_interval=30.0,
        ) -> RunExecutionResult:
            assert log_flush_interval == 30.0
            assert on_logs is not None
            on_logs("chunk 1\n")
            on_logs("chunk 1\nchunk 2\n")
            return RunExecutionResult(
                status="succeeded",
                exit_code=0,
                logs="chunk 1\nchunk 2\nfinal\n",
                output=None,
                duration_ms=120,
            )

        mock_runner.execute.side_effect = _fake_execute

        cfg = RunWorkerConfig(
            elder_api_url="https://elder.example.test",
            elder_internal_token="tok-live",
            worker_id="runworker-live-1",
            health_file=str(tmp_path / "healthy"),
        )

        async def _run() -> RunExecutionResult:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                service = RunWorkerService(cfg, runner=mock_runner, http_client=client)
                return await service.process_execution(
                    {
                        "execution_id": "exec-stream-1",
                        "runtime": "python",
                        "code": "print('hi')",
                    }
                )

        res = asyncio.run(_run())
        assert res.status == "succeeded"
        assert [p for p, _ in posted] == [
            "/api/v1/runs/internal/executions/exec-stream-1/logs",
            "/api/v1/runs/internal/executions/exec-stream-1/logs",
            "/api/v1/runs/internal/executions/exec-stream-1/result",
        ]
        assert posted[0][1] == {"logs": "chunk 1\n", "worker_id": "runworker-live-1"}
        assert posted[1][1] == {
            "logs": "chunk 1\nchunk 2\n",
            "worker_id": "runworker-live-1",
        }
        assert posted[2][1]["status"] == "succeeded"
