"""Isolation guarantees for svc-runworker: backend selection, Job pod spec,
the restriction gateway for Job pods, and worker-process hardening."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from apps.runworker import main as runworker_main
from apps.runworker.gateway import RestrictionGateway
from apps.runworker.k8s_runner import (
    GATEWAY_ACCESS_LABEL,
    GATEWAY_TOKEN_SECRET_KEY,
    GatewayPortPool,
    K8sRunner,
    RunJobRequest,
    build_gateway_secret_manifest,
    build_job_manifest,
)
from apps.runworker.main import (
    DEFAULT_RUNNER_BACKEND,
    RunWorkerConfig,
    RunWorkerService,
    WorkerConfigError,
)
from apps.runworker.sandbox_runner import (
    SandboxIsolationError,
    SandboxRunner,
    evaluate_sandbox_isolation,
    harden_worker_process,
    is_sandboxed_runtime_class,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PY_REQ = RunJobRequest(execution_id="iso-1", runtime="python", code="print(1)")


def _service(**cfg: object) -> RunWorkerService:
    """Build a worker service with mocked Kubernetes clients."""
    return RunWorkerService(
        RunWorkerConfig(**cfg),  # type: ignore[arg-type]
        sandbox_runner=SandboxRunner(),
        k8s_runner=K8sRunner(batch_api=MagicMock(), core_api=MagicMock()),
    )


@pytest.fixture
def worker_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Minimal valid worker environment with no platform signing keys."""
    for key in (
        "JWT_SECRET_KEY",
        "SECRET_KEY",
        "RUNS_RUNNER_BACKEND",
        "RUNS_POD_RUNTIME_CLASS",
        "RUNS_KATA_EXCEPTION_SANDBOX",
        "RUNS_KATA_EXCEPTION_JOBS",
        "RUNS_GATEWAY_PORT_BASE",
        "POD_IP",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ELDER_INTERNAL_TOKEN", "ewk_unit_test_token")
    return monkeypatch


class TestWorkerCredentials:
    """The worker holds only its own token, never platform signing keys."""

    @pytest.mark.parametrize("key", ["JWT_SECRET_KEY", "SECRET_KEY"])
    def test_refuses_to_start_with_signing_key_in_env(
        self, worker_env: pytest.MonkeyPatch, key: str
    ) -> None:
        worker_env.setenv(key, "unit-test-signing-key-material-000000")
        with pytest.raises(WorkerConfigError, match=key):
            RunWorkerConfig.from_env()

    def test_requires_worker_token(self, worker_env: pytest.MonkeyPatch) -> None:
        worker_env.delenv("ELDER_INTERNAL_TOKEN")
        with pytest.raises(WorkerConfigError, match="ELDER_INTERNAL_TOKEN is required"):
            RunWorkerConfig.from_env()

    def test_config_has_no_signing_key_fields(self) -> None:
        fields = set(RunWorkerConfig.__dataclass_fields__)
        assert not {"jwt_secret_key", "jwt_issuer", "jwt_audience"} & fields

    def test_from_env_reads_isolation_settings(
        self, worker_env: pytest.MonkeyPatch
    ) -> None:
        worker_env.setenv("RUNS_POD_RUNTIME_CLASS", "kata")
        worker_env.setenv("RUNS_SANDBOXED_RUNTIME_CLASSES", "kata,kata-clh")
        worker_env.setenv("RUNS_GATEWAY_PORT_BASE", "18080")
        worker_env.setenv("POD_IP", "10.1.2.3")
        worker_env.setenv("RUNS_EXECUTION_SERVICE_ACCOUNT", "elder-run-exec")
        cfg = RunWorkerConfig.from_env()
        assert cfg.runner_backend == DEFAULT_RUNNER_BACKEND == "k8s_job"
        assert cfg.pod_runtime_class == "kata"
        assert cfg.sandboxed_runtime_classes == ("kata", "kata-clh")
        assert (cfg.gateway_bind_host, cfg.gateway_port_base) == ("10.1.2.3", 18080)
        assert cfg.execution_service_account == "elder-run-exec"

    def test_from_env_rejects_bad_gateway_port(
        self, worker_env: pytest.MonkeyPatch
    ) -> None:
        worker_env.setenv("RUNS_GATEWAY_PORT_BASE", "not-a-port")
        with pytest.raises(WorkerConfigError, match="RUNS_GATEWAY_PORT_BASE"):
            RunWorkerConfig.from_env()


class TestSandboxIsolationDecision:
    """``evaluate_sandbox_isolation`` only passes with a real boundary."""

    @pytest.mark.parametrize(
        ("runtime_class", "allowlist", "exception", "expected"),
        [
            ("", (), "", False),
            ("runc", (), "", False),
            ("kata", (), "", True),
            ("KATA-CLH", (), "", True),
            ("gvisor", (), "", False),
            ("gvisor", ("gvisor",), "", True),
            ("kata", ("kata-clh",), "", False),
            ("", (), "approved", True),
            ("", (), "true", False),
            ("runc", (), "yes", False),
        ],
    )
    def test_decision(
        self,
        runtime_class: str,
        allowlist: tuple[str, ...],
        exception: str,
        expected: bool,
    ) -> None:
        allowed, reason = evaluate_sandbox_isolation(
            pod_runtime_class=runtime_class,
            sandboxed_runtime_classes=allowlist,
            kata_exception=exception,
        )
        assert allowed is expected
        assert reason


class TestBackendSelection:
    """Backend selection never puts tenant code in an unsandboxed worker pod."""

    def test_default_backend_is_k8s_job(self) -> None:
        assert RunWorkerConfig().runner_backend == "k8s_job"
        service = _service(runtime_class_name="kata")
        assert service.select_runner(PY_REQ) is service.k8s_runner
        assert service.runner is service.k8s_runner

    def test_auto_never_selects_sandbox_without_sandboxed_runtime(self) -> None:
        service = _service(runner_backend="auto", runtime_class_name="kata")
        assert service.sandbox_allowed is False
        # The local python binary IS available, so only the gate stops it.
        assert service.sandbox_runner.can_execute("python") is True
        assert service.select_runner(PY_REQ) is service.k8s_runner

    @pytest.mark.parametrize("pod_class", ["", "runc", "nvidia"])
    def test_auto_with_unsandboxed_pod_class_uses_jobs(self, pod_class: str) -> None:
        service = _service(
            runner_backend="auto",
            runtime_class_name="kata",
            pod_runtime_class=pod_class,
        )
        assert service.select_runner(PY_REQ) is service.k8s_runner

    def test_auto_selects_sandbox_only_with_sandboxed_runtime(self) -> None:
        service = _service(
            runner_backend="auto", runtime_class_name="kata", pod_runtime_class="kata"
        )
        assert service.select_runner(PY_REQ) is service.sandbox_runner

    @pytest.mark.parametrize("pod_class", ["", "runc"])
    def test_explicit_sandbox_without_signal_refuses_to_start(
        self, pod_class: str
    ) -> None:
        with pytest.raises(SandboxIsolationError, match="sandbox refused"):
            _service(runner_backend="sandbox", pod_runtime_class=pod_class)

    def test_explicit_sandbox_with_kata_pod(self) -> None:
        service = _service(runner_backend="sandbox", pod_runtime_class="kata")
        assert service.select_runner(PY_REQ) is service.sandbox_runner

    def test_sandbox_override_is_explicit_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("ERROR", logger="apps.runworker"):
            service = _service(
                runner_backend="sandbox", kata_exception_sandbox="approved"
            )
        assert service.select_runner(PY_REQ) is service.sandbox_runner
        assert "runworker_kata_exception_active" in caplog.text

    def test_job_backend_requires_runtime_class(self) -> None:
        with pytest.raises(SandboxIsolationError, match="RUNS_RUNTIME_CLASS_NAME"):
            _service(runner_backend="k8s_job")

    def test_job_backend_exception_is_explicit_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("ERROR", logger="apps.runworker"):
            service = _service(runner_backend="k8s_job", kata_exception_jobs="approved")
        assert service.select_runner(PY_REQ) is service.k8s_runner
        assert "runworker_kata_exception_active" in caplog.text

    @pytest.mark.parametrize("runtime_class", ["runc", "kata-qmeu", "gvisor", "  "])
    @pytest.mark.parametrize("backend", ["k8s_job", "auto"])
    def test_job_backend_rejects_unsandboxed_or_misspelt_runtime_class(
        self, backend: str, runtime_class: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A non-empty runtime class is not enough: it must be in the list."""
        with caplog.at_level("ERROR", logger="apps.runworker"):
            with pytest.raises(SandboxIsolationError, match="not a sandboxed"):
                _service(
                    runner_backend=backend,
                    runtime_class_name=runtime_class,
                    sandboxed_runtime_classes=("kata", "kata-qemu"),
                    kata_exception_sandbox="approved",
                )
        assert "runworker_execution_runtime_class_refused" in caplog.text

    def test_job_backend_rejects_runc_without_configured_list(self) -> None:
        with pytest.raises(SandboxIsolationError, match="not a sandboxed"):
            _service(runner_backend="k8s_job", runtime_class_name="runc")

    @pytest.mark.parametrize("runtime_class", ["kata", "kata-qemu"])
    def test_job_backend_accepts_listed_kata_class(
        self, runtime_class: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("ERROR", logger="apps.runworker"):
            service = _service(
                runner_backend="k8s_job",
                runtime_class_name=runtime_class,
                sandboxed_runtime_classes=("kata", "kata-qemu"),
            )
        assert service.select_runner(PY_REQ) is service.k8s_runner
        assert "runworker_kata_exception_active" not in caplog.text

    def test_job_backend_exception_allows_runc_and_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("ERROR", logger="apps.runworker"):
            service = _service(
                runner_backend="k8s_job",
                runtime_class_name="runc",
                sandboxed_runtime_classes=("kata",),
                kata_exception_jobs="approved",
            )
        assert service.select_runner(PY_REQ) is service.k8s_runner
        assert "runworker_kata_exception_active" in caplog.text

    @pytest.mark.parametrize(
        ("name", "allowlist", "expected"),
        [
            ("kata", ("kata",), True),
            ("kata-clh", ("kata",), False),
            ("runc", ("kata",), False),
            ("runc", ("runc-but-listed", "runc"), True),
            ("", ("kata",), False),
            ("kata-fc", (), True),
            ("runc", (), False),
        ],
    )
    def test_runtime_class_membership(
        self, name: str, allowlist: tuple[str, ...], expected: bool
    ) -> None:
        assert is_sandboxed_runtime_class(name, allowlist) is expected

    def test_unknown_backend_is_rejected(self) -> None:
        with pytest.raises(WorkerConfigError, match="Unsupported runner backend"):
            _service(runner_backend="warm", runtime_class_name="kata")

    def test_select_runner_rechecks_gate(self) -> None:
        service = _service(runner_backend="sandbox", pod_runtime_class="kata")
        service.sandbox_allowed = False
        with pytest.raises(SandboxIsolationError):
            service.select_runner(PY_REQ)


class TestJobPodSpec:
    """Execution pods carry no platform credentials and are locked down."""

    def _manifest(self, **kwargs: object) -> dict:
        req = RunJobRequest(
            execution_id="iso-job",
            runtime="python",
            code="print(1)",
            timeout_seconds=42,
            env={
                "JWT_SECRET_KEY": "x",
                "SECRET_KEY": "x",
                "ELDER_INTERNAL_TOKEN": "x",
                "ELDER_API_TOKEN": "x",
                "ELDER_GATEWAY_TOKEN": "x",
                "SAFE_VAR": "ok",
            },
            elder_api_token="platform-issued-execution-token",
        )
        return build_job_manifest(
            req,
            runtime_class_name="kata",
            service_account_name="elder-run-exec",
            **kwargs,  # type: ignore[arg-type]
        )

    def test_pod_has_no_api_token_and_own_service_account(self) -> None:
        pod = self._manifest()["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["serviceAccountName"] == "elder-run-exec"
        assert pod["enableServiceLinks"] is False
        assert pod["runtimeClassName"] == "kata"
        assert pod["hostNetwork"] is False
        assert pod["hostPID"] is False
        assert pod["hostIPC"] is False

    def test_no_secret_env_reaches_the_pod(self) -> None:
        manifest = self._manifest()
        env = {
            e["name"]: e
            for e in manifest["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        for forbidden in (
            "JWT_SECRET_KEY",
            "SECRET_KEY",
            "ELDER_INTERNAL_TOKEN",
            "ELDER_API_TOKEN",
            "ELDER_GATEWAY_TOKEN",
            "ELDER_GATEWAY_URL",
        ):
            assert forbidden not in env
        assert env["SAFE_VAR"]["value"] == "ok"
        assert all("valueFrom" not in e for e in env.values())
        assert "platform-issued-execution-token" not in json.dumps(manifest)

    def test_security_context_and_limits(self) -> None:
        manifest = self._manifest()
        pod = manifest["spec"]["template"]["spec"]
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert pod["securityContext"]["runAsUser"] != 0
        assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        ctx = pod["containers"][0]["securityContext"]
        assert ctx["runAsNonRoot"] is True
        assert ctx["readOnlyRootFilesystem"] is True
        assert ctx["allowPrivilegeEscalation"] is False
        assert ctx["capabilities"] == {"drop": ["ALL"]}
        limits = pod["containers"][0]["resources"]["limits"]
        assert limits["memory"] and limits["cpu"]
        assert all("hostPath" not in v for v in pod["volumes"])

    def test_deadlines(self) -> None:
        spec = self._manifest()["spec"]
        assert spec["activeDeadlineSeconds"] == 42
        assert spec["ttlSecondsAfterFinished"] > 0
        assert spec["backoffLimit"] == 0
        assert spec["template"]["spec"]["restartPolicy"] == "Never"

    def test_gateway_env_only_from_the_runner(self) -> None:
        manifest = self._manifest(
            gateway_url="http://10.1.2.3:18080",
            gateway_token_secret_name="elder-run-gw-iso-job",
        )
        env = {
            e["name"]: e
            for e in manifest["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        assert env["ELDER_GATEWAY_URL"]["value"] == "http://10.1.2.3:18080"
        # The token is referenced from the per-execution Secret, never inlined.
        assert "value" not in env["ELDER_GATEWAY_TOKEN"]
        assert env["ELDER_GATEWAY_TOKEN"]["valueFrom"] == {
            "secretKeyRef": {
                "name": "elder-run-gw-iso-job",
                "key": GATEWAY_TOKEN_SECRET_KEY,
                "optional": False,
            }
        }
        assert "platform-issued-execution-token" not in json.dumps(manifest)

    def test_gateway_secret_manifest(self) -> None:
        req = RunJobRequest(
            execution_id="Iso_Job",
            runtime="python",
            code="print(1)",
            granted_scopes=("entities:read",),
        )
        secret = build_gateway_secret_manifest(
            req,
            gateway_token="per-exec-token",
            namespace="elder-runs",
            owner_job_name="elder-run-iso-job",
            owner_job_uid="uid-123",
        )
        assert secret["kind"] == "Secret"
        assert secret["immutable"] is True
        assert secret["type"] == "Opaque"
        assert secret["metadata"]["name"] == "elder-run-gw-iso-job"
        assert secret["metadata"]["namespace"] == "elder-runs"
        labels = secret["metadata"]["labels"]
        assert labels["elder.penguintech.io/execution-id"] == "iso-job"
        assert labels["app.kubernetes.io/managed-by"] == "svc-runworker"
        assert secret["stringData"] == {GATEWAY_TOKEN_SECRET_KEY: "per-exec-token"}
        owner = secret["metadata"]["ownerReferences"][0]
        assert (owner["kind"], owner["name"], owner["uid"]) == (
            "Job",
            "elder-run-iso-job",
            "uid-123",
        )
        unowned = build_gateway_secret_manifest(req, gateway_token="t")
        assert "ownerReferences" not in unowned["metadata"]

    def test_gateway_label_for_scopes_or_egress(self) -> None:
        plain = build_job_manifest(PY_REQ)
        assert GATEWAY_ACCESS_LABEL not in plain["metadata"]["labels"]
        for extra in (
            {"granted_scopes": ("entities:read",)},
            {"allowed_egress_domains": ("example.com",)},
        ):
            req = RunJobRequest(
                execution_id="iso-l", runtime="python", code="print(1)", **extra
            )
            labels = build_job_manifest(req)["spec"]["template"]["metadata"]["labels"]
            assert labels[GATEWAY_ACCESS_LABEL] == "true"


class TestJobGateway:
    """Job executions reach the Elder API only through the restriction gateway."""

    def _scoped_request(self) -> RunJobRequest:
        return RunJobRequest(
            execution_id="iso-gw",
            runtime="python",
            code="print(1)",
            granted_scopes=("entities:read",),
            elder_api_token="platform-issued-execution-token",
        )

    def test_port_pool_acquire_release_and_exhaustion(self) -> None:
        pool = GatewayPortPool(18080, 2)
        first, second = pool.acquire(), pool.acquire()
        assert {first, second} == {18080, 18081}
        with pytest.raises(RuntimeError, match="No free restriction gateway port"):
            pool.acquire()
        pool.release(first)
        pool.release(first)
        assert pool.acquire() == first

    @pytest.mark.parametrize(("base", "size"), [(80, 5), (18080, 0), (65535, 5)])
    def test_port_pool_rejects_invalid_ranges(self, base: int, size: int) -> None:
        with pytest.raises(ValueError, match="Invalid gateway port pool"):
            GatewayPortPool(base, size)

    def test_scoped_execution_refused_without_gateway_config(self) -> None:
        batch, core = MagicMock(), MagicMock()
        runner = K8sRunner(batch_api=batch, core_api=core, runtime_class_name="kata")
        with pytest.raises(RuntimeError, match="restriction gateway is not configured"):
            runner.execute(self._scoped_request())
        batch.create_namespaced_job.assert_not_called()
        core.create_namespaced_config_map.assert_not_called()

    def _gateway_runner(
        self, batch: MagicMock, core: MagicMock
    ) -> tuple[K8sRunner, GatewayPortPool]:
        """Build a Job runner hosting its gateway on a free loopback port."""
        core.list_namespaced_pod.return_value = MagicMock(items=[])
        pool = GatewayPortPool(_free_port_base(), 1)
        runner = K8sRunner(
            batch_api=batch,
            core_api=core,
            runtime_class_name="kata",
            gateway_bind_host="127.0.0.1",
            gateway_port_pool=pool,
            poll_interval=0.05,
        )
        return runner, pool

    def test_scoped_execution_gets_gateway_and_never_the_real_token(self) -> None:
        batch, core = MagicMock(), MagicMock()
        seen: dict[str, object] = {}
        batch.create_namespaced_job.return_value = MagicMock(
            metadata=MagicMock(uid="job-uid-1")
        )

        def _job_status(name: str, namespace: str) -> MagicMock:
            job_body = batch.create_namespaced_job.call_args.kwargs["body"]
            secret_body = core.create_namespaced_secret.call_args.kwargs["body"]
            env = {
                e["name"]: e
                for e in job_body["spec"]["template"]["spec"]["containers"][0]["env"]
            }
            token = secret_body["stringData"][GATEWAY_TOKEN_SECRET_KEY]
            seen["env"] = env
            seen["token"] = token
            seen["body"] = json.dumps(job_body)
            seen["secret"] = secret_body
            # The gateway is live while the Job runs and rejects bad tokens.
            url = env["ELDER_GATEWAY_URL"]["value"]
            seen["unauth"] = httpx.get(
                f"{url}/api/v1/entities",
                headers={"Authorization": "Bearer wrong"},
                timeout=5,
            ).status_code
            seen["out_of_scope"] = httpx.delete(
                f"{url}/api/v1/entities/1",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            ).status_code
            return MagicMock(status=MagicMock(succeeded=1, failed=0, conditions=[]))

        batch.read_namespaced_job_status.side_effect = _job_status
        runner, pool = self._gateway_runner(batch, core)
        result = runner.execute(self._scoped_request())
        assert result.status == "succeeded"
        env = seen["env"]
        assert isinstance(env, dict)
        url = env["ELDER_GATEWAY_URL"]["value"]
        assert url.startswith("http://127.0.0.1:")
        token = str(seen["token"])
        assert token
        # No token literal anywhere in the Job/Pod spec: Secret reference only.
        assert token not in str(seen["body"])
        assert "value" not in env["ELDER_GATEWAY_TOKEN"]
        ref = env["ELDER_GATEWAY_TOKEN"]["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "elder-run-gw-iso-gw"
        secret = seen["secret"]
        assert isinstance(secret, dict)
        assert secret["metadata"]["name"] == "elder-run-gw-iso-gw"
        assert secret["immutable"] is True
        assert secret["metadata"]["labels"]["elder.penguintech.io/execution-id"] == (
            "iso-gw"
        )
        assert secret["metadata"]["ownerReferences"][0]["uid"] == "job-uid-1"
        assert "platform-issued-execution-token" not in str(seen["body"])
        assert "platform-issued-execution-token" not in json.dumps(secret)
        assert seen["unauth"] == 401
        assert seen["out_of_scope"] == 403
        core.delete_namespaced_secret.assert_called_once_with(
            name="elder-run-gw-iso-gw", namespace=runner.namespace
        )
        # The controller never reads Secrets back (Role has no get/list/watch).
        core.read_namespaced_secret.assert_not_called()
        core.list_namespaced_secret.assert_not_called()
        # Gateway is stopped and its port returned once the execution ends.
        with pytest.raises(httpx.HTTPError):
            httpx.get(url, timeout=2)
        assert pool.acquire()

    @pytest.mark.parametrize(
        ("job_status", "expected"),
        [
            (MagicMock(succeeded=1, failed=0, conditions=[]), "succeeded"),
            (MagicMock(succeeded=0, failed=1, conditions=[]), "failed"),
            (
                MagicMock(
                    succeeded=0,
                    failed=1,
                    conditions=[MagicMock(type="Failed", reason="DeadlineExceeded")],
                ),
                "timeout",
            ),
        ],
    )
    def test_gateway_secret_deleted_on_terminal_states(
        self, job_status: MagicMock, expected: str
    ) -> None:
        batch, core = MagicMock(), MagicMock()
        batch.read_namespaced_job_status.return_value = MagicMock(status=job_status)
        runner, pool = self._gateway_runner(batch, core)
        assert runner.execute(self._scoped_request()).status == expected
        core.create_namespaced_secret.assert_called_once()
        core.delete_namespaced_secret.assert_called_once_with(
            name="elder-run-gw-iso-gw", namespace=runner.namespace
        )
        core.delete_namespaced_config_map.assert_called_once()
        assert pool.acquire()

    @pytest.mark.parametrize("exc_type", [asyncio.CancelledError, RuntimeError])
    def test_gateway_secret_deleted_on_cancel_or_error(
        self, exc_type: type[BaseException]
    ) -> None:
        """Cancellation or an API error mid-poll still removes the Secret."""
        batch, core = MagicMock(), MagicMock()
        batch.read_namespaced_job_status.side_effect = exc_type("cancelled")
        runner, pool = self._gateway_runner(batch, core)
        with pytest.raises(exc_type):
            runner.execute(self._scoped_request())
        core.delete_namespaced_secret.assert_called_once_with(
            name="elder-run-gw-iso-gw", namespace=runner.namespace
        )
        assert pool.acquire()

    def test_gateway_secret_cleanup_attempted_when_create_fails(self) -> None:
        batch, core = MagicMock(), MagicMock()
        core.create_namespaced_secret.side_effect = RuntimeError("secret api down")
        runner, pool = self._gateway_runner(batch, core)
        with pytest.raises(RuntimeError, match="secret api down"):
            runner.execute(self._scoped_request())
        core.delete_namespaced_secret.assert_called_once()
        assert pool.acquire()

    def test_gateway_secret_cleanup_failure_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        batch, core = MagicMock(), MagicMock()
        batch.read_namespaced_job_status.return_value = MagicMock(
            status=MagicMock(succeeded=1, failed=0, conditions=[])
        )
        core.delete_namespaced_secret.side_effect = RuntimeError("forbidden")
        runner, _ = self._gateway_runner(batch, core)
        with caplog.at_level("ERROR", logger="apps.runworker"):
            assert runner.execute(self._scoped_request()).status == "succeeded"
        assert "runworker_gateway_secret_cleanup_failed" in caplog.text

    def test_unscoped_execution_creates_no_secret(self) -> None:
        batch, core = MagicMock(), MagicMock()
        batch.read_namespaced_job_status.return_value = MagicMock(
            status=MagicMock(succeeded=1, failed=0, conditions=[])
        )
        core.list_namespaced_pod.return_value = MagicMock(items=[])
        runner = K8sRunner(batch_api=batch, core_api=core, poll_interval=0.05)
        assert runner.execute(PY_REQ).status == "succeeded"
        core.create_namespaced_secret.assert_not_called()
        core.delete_namespaced_secret.assert_not_called()

    def test_configmap_delete_is_limited_to_the_name_it_created(self) -> None:
        batch, core = MagicMock(), MagicMock()
        batch.read_namespaced_job_status.return_value = MagicMock(
            status=MagicMock(succeeded=1, failed=0, conditions=[])
        )
        core.list_namespaced_pod.return_value = MagicMock(items=[])
        runner = K8sRunner(batch_api=batch, core_api=core, poll_interval=0.05)
        req = RunJobRequest(
            execution_id="../kube-root-ca.crt", runtime="python", code="print(1)"
        )
        runner.execute(req)
        created = core.create_namespaced_config_map.call_args.kwargs["body"]
        deleted = core.delete_namespaced_config_map.call_args.kwargs["name"]
        assert deleted == created["metadata"]["name"]
        assert deleted.startswith("elder-run-cm-")
        core.delete_namespaced_config_map.assert_called_once()

    def test_new_gateway_and_token_per_execution(self) -> None:
        """Each execution gets its own gateway; tokens do not cross over."""
        batch, core = MagicMock(), MagicMock()
        seen: list[tuple[str, str, int, int]] = []
        runner, pool = self._gateway_runner(batch, core)

        def _job_status(name: str, namespace: str) -> MagicMock:
            job_body = batch.create_namespaced_job.call_args.kwargs["body"]
            secret_body = core.create_namespaced_secret.call_args.kwargs["body"]
            env = {
                e["name"]: e
                for e in job_body["spec"]["template"]["spec"]["containers"][0]["env"]
            }
            url = env["ELDER_GATEWAY_URL"]["value"]
            token = secret_body["stringData"][GATEWAY_TOKEN_SECRET_KEY]
            stale = seen[0][1] if seen else "none"
            stale_status = httpx.delete(
                f"{url}/api/v1/entities/1",
                headers={"Authorization": f"Bearer {stale}"},
                timeout=5,
            ).status_code
            own_status = httpx.delete(
                f"{url}/api/v1/entities/1",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            ).status_code
            seen.append(
                (secret_body["metadata"]["name"], token, stale_status, own_status)
            )
            return MagicMock(status=MagicMock(succeeded=1, failed=0, conditions=[]))

        batch.read_namespaced_job_status.side_effect = _job_status
        with patch(
            "apps.runworker.k8s_runner.RestrictionGateway",
            wraps=RestrictionGateway,
        ) as gateway_cls:
            for execution_id in ("iso-gw-a", "iso-gw-b"):
                runner.execute(
                    RunJobRequest(
                        execution_id=execution_id,
                        runtime="python",
                        code="print(1)",
                        granted_scopes=("entities:read",),
                        tenant_id="7",
                    )
                )
        assert gateway_cls.call_count == 2
        first, second = gateway_cls.call_args_list
        assert first.kwargs["execution_id"] == "iso-gw-a"
        assert second.kwargs["execution_id"] == "iso-gw-b"
        assert first.kwargs["tenant_id"] == second.kwargs["tenant_id"] == "7"
        (name_a, token_a, _, own_a), (name_b, token_b, stale_b, own_b) = seen
        assert name_a != name_b
        assert token_a != token_b
        # Authenticated (403 = out of scope) with its own token only.
        assert own_a == 403
        assert own_b == 403
        assert stale_b == 401
        assert pool.acquire()

    def test_gateway_port_released_when_job_creation_fails(self) -> None:
        batch, core = MagicMock(), MagicMock()
        batch.create_namespaced_job.side_effect = RuntimeError("api down")
        pool = GatewayPortPool(_free_port_base(), 1)
        runner = K8sRunner(
            batch_api=batch,
            core_api=core,
            gateway_bind_host="127.0.0.1",
            gateway_port_pool=pool,
        )
        with pytest.raises(RuntimeError, match="api down"):
            runner.execute(self._scoped_request())
        assert pool.acquire()


def _free_port_base() -> int:
    """Return a currently free loopback TCP port for a one-port pool."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_PROBE_PARENT = textwrap.dedent(
    """
    import json, os, sys
    sys.path.insert(0, {repo!r})
    from apps.runworker.k8s_runner import RunJobRequest
    from apps.runworker.sandbox_runner import SandboxRunner, harden_worker_process

    CHILD = '''
    import os
    ppid = os.getppid()
    for name in ("environ", "mem", "maps", "cwd"):
        path = f"/proc/{{ppid}}/{{name}}"
        try:
            if name == "cwd":
                os.readlink(path)
                data = b""
            else:
                with open(path, "rb") as fh:
                    data = fh.read(1 << 20) if name != "mem" else b""
            leaked = b"{marker}" in data
            print(f"{{name}}=READ leaked={{leaked}}")
        except OSError as exc:
            print(f"{{name}}=DENIED {{type(exc).__name__}}")
    print("own_env_has_marker=" + str(any("{marker}" in v for v in os.environ.values())))
    '''
    if {harden!r}:
        result = SandboxRunner(workspace_root={root!r}).execute(
            RunJobRequest(execution_id="probe", runtime="python", code=CHILD)
        )
        print(json.dumps({{"status": result.status, "logs": result.logs}}))
    else:
        import subprocess
        out = subprocess.run(
            [sys.executable, "-c", CHILD], capture_output=True, text=True,
            env={{"PATH": os.environ.get("PATH", "")}},
        )
        print(json.dumps({{"status": "control", "logs": out.stdout}}))
    """
)


class TestWorkerProcessHardening:
    """A same-UID child must not be able to read the worker's secrets."""

    MARKER = "PARENT-ONLY-MARKER-7f3a9c"

    def _run_probe(self, tmp_root: Path, *, harden: bool) -> dict[str, str]:
        """Run a fresh worker-like parent holding a marker secret in its env."""
        script = _PROBE_PARENT.format(
            repo=str(REPO_ROOT), marker=self.MARKER, harden=harden, root=str(tmp_root)
        )
        kwargs: dict[str, object] = {}
        if os.geteuid() == 0:
            # uid 0 bypasses the same-UID check under test; drop to nobody so
            # parent and child are an unprivileged same-UID pair.
            os.chown(tmp_root, 65534, 65534)
            kwargs.update(user=65534, group=65534, extra_groups=[])
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            env={
                "PATH": os.environ.get("PATH", ""),
                "ELDER_INTERNAL_TOKEN": self.MARKER,
                "HOME": str(tmp_root),
                # Same import path as this test process (deps may live in a
                # user site-packages dir that a changed HOME would hide).
                "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            },
            cwd=str(tmp_root),
            **kwargs,  # type: ignore[arg-type]
        )
        assert proc.returncode == 0, proc.stderr[-800:]
        return json.loads(proc.stdout.strip().splitlines()[-1])

    @pytest.fixture
    def probe_root(self) -> Path:
        """World-traversable scratch dir usable after dropping privileges."""
        root = Path(tempfile.mkdtemp(prefix="rw-iso-"))
        os.chmod(root, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        yield root
        shutil.rmtree(root, ignore_errors=True)

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="prctl(PR_SET_DUMPABLE) and /proc are Linux-only",
    )
    def test_child_cannot_read_parent_environ_or_memory(self, probe_root: Path) -> None:
        if not Path("/proc/self/environ").exists():
            pytest.skip("/proc is not mounted in this test container")
        control = self._run_probe(probe_root, harden=False)
        if "environ=READ leaked=True" not in control["logs"]:
            pytest.skip(
                "kernel/container already denies same-UID /proc reads "
                f"(control: {control['logs']!r}); hardening cannot be told apart"
            )
        hardened = self._run_probe(probe_root, harden=True)
        assert hardened["status"] == "succeeded", hardened
        logs = hardened["logs"]
        assert "environ=DENIED" in logs
        assert "mem=DENIED" in logs
        assert "maps=DENIED" in logs
        assert "leaked=True" not in logs
        assert self.MARKER not in logs
        assert "own_env_has_marker=False" in logs

    @pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="prctl is Linux-only"
    )
    def test_harden_worker_process_is_idempotent_in_subprocess(self) -> None:
        code = (
            f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r});"
            "from apps.runworker.sandbox_runner import harden_worker_process, _prctl;"
            "harden_worker_process(); harden_worker_process(); print(_prctl(3))"
        )
        out = subprocess.run(  # noqa: S603
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
        )
        assert out.returncode == 0, out.stderr[-400:]
        assert out.stdout.strip() == "0"

    def test_harden_fails_loudly_when_prctl_unavailable(self) -> None:
        with patch(
            "apps.runworker.sandbox_runner._prctl", side_effect=OSError(1, "denied")
        ):
            with pytest.raises(SandboxIsolationError, match="non-dumpable"):
                harden_worker_process()

    def test_harden_fails_when_flag_does_not_stick(self) -> None:
        with patch("apps.runworker.sandbox_runner._prctl", return_value=1):
            with pytest.raises(SandboxIsolationError, match="still dumpable"):
                harden_worker_process()

    def test_harden_refuses_non_linux(self) -> None:
        with patch("apps.runworker.sandbox_runner.sys.platform", "darwin"):
            with pytest.raises(SandboxIsolationError, match="requires Linux"):
                harden_worker_process()

    def test_execute_refuses_to_spawn_when_hardening_fails(
        self, tmp_path: Path
    ) -> None:
        runner = SandboxRunner(workspace_root=str(tmp_path))
        with (
            patch(
                "apps.runworker.sandbox_runner.harden_worker_process",
                side_effect=SandboxIsolationError("no prctl"),
            ),
            patch("apps.runworker.sandbox_runner.subprocess.Popen") as popen,
        ):
            with pytest.raises(SandboxIsolationError):
                runner.execute(PY_REQ)
        popen.assert_not_called()
        assert list(tmp_path.iterdir()) == []


class TestEntrypoint:
    """``main()`` scrubs the token from the environment and hardens the process."""

    def _run_main(self, worker_env: pytest.MonkeyPatch, harden: MagicMock) -> None:
        async def _noop(self: RunWorkerService) -> None:
            return None

        worker_env.setattr(runworker_main.RunWorkerService, "run_forever", _noop)
        worker_env.setattr(runworker_main, "harden_worker_process", harden)
        runworker_main.main()

    def test_main_scrubs_token_env_and_hardens(
        self, worker_env: pytest.MonkeyPatch
    ) -> None:
        worker_env.setenv("RUNS_RUNTIME_CLASS_NAME", "kata")
        harden = MagicMock()
        self._run_main(worker_env, harden)
        assert "ELDER_INTERNAL_TOKEN" not in os.environ
        harden.assert_called_once_with()

    def test_main_tolerates_missing_hardening_only_for_job_backend(
        self, worker_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        worker_env.setenv("RUNS_RUNTIME_CLASS_NAME", "kata")
        harden = MagicMock(side_effect=SandboxIsolationError("no prctl"))
        with caplog.at_level("WARNING", logger="apps.runworker"):
            self._run_main(worker_env, harden)
        assert "runworker_process_hardening_unavailable" in caplog.text

    def test_main_fails_when_sandbox_cannot_be_hardened(
        self, worker_env: pytest.MonkeyPatch
    ) -> None:
        worker_env.setenv("RUNS_RUNNER_BACKEND", "sandbox")
        worker_env.setenv("RUNS_POD_RUNTIME_CLASS", "kata")
        harden = MagicMock(side_effect=SandboxIsolationError("no prctl"))
        with pytest.raises(SandboxIsolationError, match="no prctl"):
            self._run_main(worker_env, harden)

    def test_main_refuses_unsandboxed_sandbox_backend(
        self, worker_env: pytest.MonkeyPatch
    ) -> None:
        worker_env.setenv("RUNS_RUNNER_BACKEND", "sandbox")
        with pytest.raises(SandboxIsolationError, match="sandbox refused"):
            self._run_main(worker_env, MagicMock())


class TestGatewayBinding:
    """The gateway advertises the address it was bound to."""

    def test_default_binding_is_loopback(self) -> None:
        with RestrictionGateway(elder_api_url="http://hub-api:5000") as gateway:
            assert gateway.url == f"http://127.0.0.1:{gateway.port}"

    def test_ipv6_binding_url_is_bracketed(self) -> None:
        import socket

        if not socket.has_ipv6:
            pytest.skip("IPv6 is not available in this environment")
        try:
            gateway = RestrictionGateway(
                elder_api_url="http://hub-api:5000", bind_host="::1"
            ).start()
        except OSError as exc:
            pytest.skip(f"cannot bind ::1 in this environment: {exc}")
        try:
            assert gateway.url == f"http://[::1]:{gateway.port}"
        finally:
            gateway.stop()

    def test_token_is_bound_to_execution_and_tenant(self) -> None:
        """A token from execution A is rejected by the gateway of execution B."""
        with (
            RestrictionGateway(
                elder_api_url="http://hub-api:5000",
                granted_scopes=("entities:read",),
                execution_id="exec-a",
                tenant_id="1",
            ) as gw_a,
            RestrictionGateway(
                elder_api_url="http://hub-api:5000",
                granted_scopes=("entities:read",),
                execution_id="exec-b",
                tenant_id="2",
            ) as gw_b,
        ):
            assert gw_a.gateway_token != gw_b.gateway_token
            assert gw_a.port != gw_b.port

            def _status(gateway: RestrictionGateway, token: str) -> int:
                return httpx.delete(
                    f"{gateway.url}/api/v1/entities/1",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=5,
                ).status_code

            assert _status(gw_a, gw_a.gateway_token) == 403
            assert _status(gw_b, gw_b.gateway_token) == 403
            assert _status(gw_b, gw_a.gateway_token) == 401
            assert _status(gw_a, gw_b.gateway_token) == 401

    def test_token_binding_rejects_a_changed_execution_identity(self) -> None:
        """The binding is re-derived from the gateway's own execution identity."""
        gateway = RestrictionGateway(
            elder_api_url="http://hub-api:5000", execution_id="exec-a", tenant_id="1"
        )
        handler = MagicMock()
        handler.headers = {"Authorization": f"Bearer {gateway.gateway_token}"}
        assert gateway._verify_auth(handler) is True
        gateway.execution_id = "exec-b"
        assert gateway._verify_auth(handler) is False
        gateway.execution_id = "exec-a"
        gateway.tenant_id = "2"
        assert gateway._verify_auth(handler) is False
