"""Tests for the Kubernetes Job runner: manifests, clamps, polling loop and cleanup."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from apps.runworker import k8s_runner as mod
from apps.runworker.k8s_runner import (
    K8sRunner,
    RunJobRequest,
    build_configmap_manifest,
    build_job_manifest,
    clamp_log_flush_interval,
    clamp_memory_mb,
    clamp_timeout_seconds,
    extract_structured_output,
    sanitize_k8s_name,
)


def _req(**kw: Any) -> RunJobRequest:
    """Build a RunJobRequest with defaults suitable for manifest tests."""
    base: dict[str, Any] = {
        "execution_id": "Exec_ABC-123",
        "runtime": "python",
        "code": "print(1)",
        "input_data": {"a": 1},
    }
    base.update(kw)
    return RunJobRequest(**base)


class TestHelpers:
    """Clamping, naming and output parsing."""

    def test_clamps(self) -> None:
        """Values are clamped to their safe ranges; None yields defaults."""
        assert clamp_memory_mb(None) == mod.DEFAULT_MEMORY_MB
        assert clamp_memory_mb(1) == mod.MIN_MEMORY_MB
        assert clamp_memory_mb(10**6) == mod.MAX_MEMORY_MB
        assert clamp_memory_mb(512) == 512
        assert clamp_timeout_seconds(None) == mod.DEFAULT_TIMEOUT_SECONDS
        assert clamp_timeout_seconds(0) == 1
        assert clamp_timeout_seconds(10**6) == 300
        assert clamp_log_flush_interval(None) == mod.DEFAULT_LOG_FLUSH_INTERVAL_SECONDS
        assert clamp_log_flush_interval(0.1) == 1.0
        assert clamp_log_flush_interval(9999) == 300.0
        assert clamp_log_flush_interval(12) == 12.0

    def test_sanitize_name(self) -> None:
        """Names are DNS-safe, prefixed and length-bounded."""
        assert sanitize_k8s_name("Exec_ABC-123") == "elder-run-exec-abc-123"
        long_name = sanitize_k8s_name("a" * 200)
        assert len(long_name) <= 63 and long_name.startswith("elder-run-")
        assert not sanitize_k8s_name("a" * 53 + "-" * 5 + "b").endswith("-")

    def test_sanitize_empty_uses_random_suffix(self) -> None:
        """An empty id still yields a valid generated name."""
        a, b = sanitize_k8s_name(""), sanitize_k8s_name("___")
        assert a.startswith("elder-run-") and len(a) > len("elder-run-")
        assert a != b

    @pytest.mark.parametrize(
        ("logs", "expected"),
        [
            ("", None),
            ('hello\n{"a": 1}\n\n', {"a": 1}),
            ('{"a": 1}\nnot json', None),
            ("\n  \n", None),
            ("7", 7),
        ],
    )
    def test_extract_structured_output(self, logs: str, expected: Any) -> None:
        """Only the last non-empty line is parsed as JSON."""
        assert extract_structured_output(logs) == expected


class TestManifests:
    """ConfigMap and Job manifests."""

    def test_configmap_includes_code_input_and_sdk(self) -> None:
        """The ConfigMap carries script, input and the bundled SDK."""
        cm = build_configmap_manifest(_req(granted_scopes=("a:read",)))
        assert cm["data"]["main.py"] == "print(1)"
        assert cm["data"]["input.json"] == '{"a":1}'
        assert "elder.py" in cm["data"]
        labels = cm["metadata"]["labels"]
        assert labels[mod.GATEWAY_ACCESS_LABEL] == "true"
        assert labels["elder.penguintech.io/execution-id"] == "exec-abc-123"
        assert cm["metadata"]["name"] == "elder-run-cm-exec-abc-123"

    def test_configmap_without_sdk_or_input(self) -> None:
        """Runtimes without an SDK get no extra file; None input becomes {}."""
        cm = build_configmap_manifest(
            _req(runtime="rust", input_data=None, execution_id="___"),
            namespace="ns",
            configmap_name="cm1",
        )
        assert set(cm["data"]) == {"main.rs", "input.json"}
        assert cm["data"]["input.json"] == "{}"
        assert cm["metadata"]["name"] == "cm1"
        assert "elder.penguintech.io/execution-id" not in cm["metadata"]["labels"]

    def test_job_manifest_hardening_and_env_filtering(self) -> None:
        """The Job is hardened and untrusted env cannot override protected keys."""
        job = build_job_manifest(
            _req(
                env={
                    "GOOD": "1",
                    "HOME": "/evil",
                    "ELDER_API_TOKEN": "x",
                    "bad key": "y",
                    "TMPDIR": "/z",
                    "PYTHONUNBUFFERED": "0",
                }
            ),
            runtime_class_name="gvisor",
        )
        pod = job["spec"]["template"]["spec"]
        ctr = pod["containers"][0]
        env = {e["name"]: e["value"] for e in ctr["env"]}
        assert env["GOOD"] == "1"
        assert env["TMPDIR"] == "/tmp"
        assert env["HOME"] == "/tmp"
        assert "ELDER_API_TOKEN" not in env and "bad key" not in env
        assert env["PYTHONUNBUFFERED"] == "1"
        assert pod["runtimeClassName"] == "gvisor"
        assert pod["automountServiceAccountToken"] is False
        assert ctr["securityContext"]["readOnlyRootFilesystem"] is True
        assert pod["volumes"][0]["configMap"]["name"] == "elder-run-cm-exec-abc-123"
        assert "initContainers" not in pod
        assert job["spec"]["activeDeadlineSeconds"] == mod.DEFAULT_TIMEOUT_SECONDS

    def test_runtime_env_does_not_duplicate_base_env(self) -> None:
        """Runtime extra env that repeats a base variable is not duplicated."""
        job = build_job_manifest(_req(runtime="rust"))
        env = job["spec"]["template"]["spec"]["containers"][0]["env"]
        names = [e["name"] for e in env]
        assert names.count("TMPDIR") == 1 and "CARGO_HOME" in names

    def test_job_resources_scale_with_memory(self) -> None:
        """CPU/memory requests derive from the clamped memory limit."""
        job = build_job_manifest(_req(memory_mb=2048, timeout_seconds=5))
        res = job["spec"]["template"]["spec"]["containers"][0]["resources"]
        assert res["limits"] == {"memory": "2048Mi", "cpu": "2000m"}
        assert res["requests"] == {"memory": "1024Mi", "cpu": "500m"}
        assert job["spec"]["activeDeadlineSeconds"] == 5

    def test_job_with_init_container_and_sdk(self) -> None:
        """Artifact fetch mode uses an emptyDir workspace plus an init container."""
        job = build_job_manifest(
            _req(
                use_init_container=True,
                code_url="https://s3.invalid/a",
                code_sha256="ab",
            ),
            job_name="j",
            configmap_name="c",
        )
        pod = job["spec"]["template"]["spec"]
        assert "emptyDir" in pod["volumes"][0]
        init = pod["initContainers"][0]
        env = {e["name"]: e["value"] for e in init["env"]}
        assert env["ELDER_ARTIFACT_URL"] == "https://s3.invalid/a"
        assert env["ELDER_ARTIFACT_SHA256"] == "ab"
        assert env["ELDER_SDK_FILENAME"] == "elder.py"
        assert job["metadata"]["name"] == "j"

    def test_job_init_container_without_sdk(self) -> None:
        """Runtimes lacking an SDK skip the SDK env entries."""
        job = build_job_manifest(
            _req(runtime="go", use_init_container=True, code_url="https://s3.invalid/a")
        )
        init = job["spec"]["template"]["spec"]["initContainers"][0]
        names = {e["name"] for e in init["env"]}
        assert "ELDER_SDK_FILENAME" not in names
        assert {e["name"]: e["value"] for e in init["env"]}[
            "ELDER_ARTIFACT_SHA256"
        ] == ""

    def test_init_container_needs_code_url(self) -> None:
        """use_init_container without a code_url falls back to the ConfigMap."""
        job = build_job_manifest(_req(use_init_container=True))
        assert "initContainers" not in job["spec"]["template"]["spec"]

    def test_unknown_runtime_raises(self) -> None:
        """Unsupported runtimes fail loudly."""
        with pytest.raises(ValueError):
            build_job_manifest(_req(runtime="cobol"))


def _job(
    succeeded: int = 0, failed: int = 0, conditions: list[Any] | None = None
) -> Any:
    """Build a fake Job status object."""
    return SimpleNamespace(
        status=SimpleNamespace(
            succeeded=succeeded, failed=failed, conditions=conditions
        )
    )


def _pod(
    name: str | None = "pod-1", exit_code: int | None = None, waiting: str | None = None
) -> Any:
    """Build a fake pod with one container status."""
    state = SimpleNamespace(
        waiting=SimpleNamespace(reason=waiting) if waiting else None,
        terminated=SimpleNamespace(exit_code=exit_code)
        if exit_code is not None
        else None,
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(container_statuses=[SimpleNamespace(state=state)]),
    )


def _apis(
    job_statuses: list[Any], pods: list[Any], log: Any = 'out\n{"r": 2}'
) -> tuple[MagicMock, MagicMock]:
    """Build batch/core API doubles returning the given job statuses and pods."""
    batch, core = MagicMock(), MagicMock()
    batch.read_namespaced_job_status.side_effect = job_statuses
    core.list_namespaced_pod.return_value = SimpleNamespace(items=pods)
    core.read_namespaced_pod_log.return_value = log
    return batch, core


class TestRunner:
    """K8sRunner.execute and helpers with mocked clients."""

    def test_success_with_configmap_cleanup(self) -> None:
        """A succeeded Job returns logs, output and exit code, and deletes the ConfigMap."""
        batch, core = _apis([_job(succeeded=1)], [_pod(exit_code=0)])
        runner = K8sRunner(batch_api=batch, core_api=core, poll_interval=0.0)
        res = runner.execute(_req())
        assert (res.status, res.exit_code, res.output, res.error) == (
            "succeeded",
            0,
            {"r": 2},
            None,
        )
        core.create_namespaced_config_map.assert_called_once()
        core.delete_namespaced_config_map.assert_called_once()
        batch.create_namespaced_job.assert_called_once()

    def test_init_fetch_skips_configmap(self) -> None:
        """Artifact fetch mode never creates or deletes a ConfigMap."""
        batch, core = _apis([_job(succeeded=1)], [_pod()])
        res = K8sRunner(batch_api=batch, core_api=core).execute(
            _req(use_init_container=True, code_url="https://s3.invalid/a")
        )
        assert res.status == "succeeded"
        core.create_namespaced_config_map.assert_not_called()
        core.delete_namespaced_config_map.assert_not_called()

    def test_deadline_exceeded_condition(self) -> None:
        """A DeadlineExceeded condition maps to timeout."""
        cond = SimpleNamespace(type="Failed", reason="DeadlineExceeded")
        other = SimpleNamespace(type="Complete", reason="x")
        batch, core = _apis([_job(conditions=[other, cond])], [_pod(exit_code=137)])
        res = K8sRunner(batch_api=batch, core_api=core).execute(_req(timeout_seconds=9))
        assert res.status == "timeout" and "9s" in (res.error or "")
        assert res.exit_code == 137

    def test_failed_job(self) -> None:
        """A failed Job maps to status failed."""
        batch, core = _apis([_job(failed=1)], [_pod(exit_code=1)])
        res = K8sRunner(batch_api=batch, core_api=core).execute(_req())
        assert (res.status, res.exit_code) == ("failed", 1)

    def test_poll_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exceeding the worker-side deadline reports a timeout."""
        ticks = [0.0]

        def clock() -> float:
            """Return 0 once (the start) then a time far past the deadline."""
            return ticks.pop(0) if ticks else 1000.0

        monkeypatch.setattr(mod.time, "monotonic", clock)
        batch, core = _apis([_job()], [])
        res = K8sRunner(batch_api=batch, core_api=core).execute(_req(timeout_seconds=1))
        assert res.status == "timeout" and "poll deadline" in (res.error or "")
        assert res.logs == "" and res.exit_code is None

    def test_live_log_flush(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running jobs push changed logs, once, via the callback."""
        real_sleep = mod.time.sleep
        monkeypatch.setattr(mod.time, "sleep", lambda _s: real_sleep(0.02))
        batch, core = _apis([_job(), _job(), _job(succeeded=1)], [_pod(exit_code=0)])
        seen: list[str] = []
        runner = K8sRunner(batch_api=batch, core_api=core, poll_interval=0.0)
        res = runner.execute(_req(), on_logs=seen.append, log_flush_interval=0.0)
        assert res.status == "succeeded"
        assert seen == ['out\n{"r": 2}']

    def test_log_callback_errors_are_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing on_logs callback does not abort the run."""
        real_sleep = mod.time.sleep
        monkeypatch.setattr(mod.time, "sleep", lambda _s: real_sleep(0.02))
        batch, core = _apis([_job(), _job(succeeded=1)], [_pod(exit_code=0)])

        def cb(_logs: str) -> None:
            """Raise to simulate a broken sink."""
            raise RuntimeError("sink down")

        res = K8sRunner(batch_api=batch, core_api=core).execute(
            _req(), on_logs=cb, log_flush_interval=0.0
        )
        assert res.status == "succeeded"

    def test_create_job_failure_still_cleans_up(self) -> None:
        """If Job creation fails the ConfigMap is still deleted."""
        batch, core = _apis([], [])
        batch.create_namespaced_job.side_effect = RuntimeError("api down")
        with pytest.raises(RuntimeError):
            K8sRunner(batch_api=batch, core_api=core).execute(_req())
        core.delete_namespaced_config_map.assert_called_once()

    def test_runtime_class_and_namespace_passed(self) -> None:
        """Namespace and runtimeClass flow into the created Job."""
        batch, core = _apis([_job(succeeded=1)], [])
        K8sRunner(
            namespace="ns1", runtime_class_name="kata", batch_api=batch, core_api=core
        ).execute(_req())
        kwargs = batch.create_namespaced_job.call_args.kwargs
        assert kwargs["namespace"] == "ns1"
        assert kwargs["body"]["spec"]["template"]["spec"]["runtimeClassName"] == "kata"


class TestPodLogs:
    """Best-effort and final log collection."""

    def _runner(self) -> K8sRunner:
        """Return a runner with clients preset."""
        return K8sRunner(batch_api=MagicMock(), core_api=MagicMock())

    def test_best_effort_list_error(self) -> None:
        """Listing failures yield empty logs."""
        core = MagicMock()
        core.list_namespaced_pod.side_effect = RuntimeError("x")
        assert self._runner()._read_pod_logs_best_effort(core, "j") == ""

    @pytest.mark.parametrize(
        "pods",
        [[], [SimpleNamespace(metadata=SimpleNamespace(name=None), status=None)]],
    )
    def test_best_effort_no_pod_or_name(self, pods: list[Any]) -> None:
        """No pod or an unnamed pod yields empty logs."""
        core = MagicMock()
        core.list_namespaced_pod.return_value = SimpleNamespace(items=pods)
        assert self._runner()._read_pod_logs_best_effort(core, "j") == ""

    @pytest.mark.parametrize("reason", ["ContainerCreating", "PodInitializing"])
    def test_best_effort_waiting(self, reason: str) -> None:
        """Containers that have not started yield empty logs."""
        core = MagicMock()
        core.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_pod(waiting=reason)]
        )
        assert self._runner()._read_pod_logs_best_effort(core, "j") == ""
        core.read_namespaced_pod_log.assert_not_called()

    def test_best_effort_reads_and_handles_error(self) -> None:
        """Logs are returned when readable; read errors and None become empty."""
        core = MagicMock()
        core.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_pod(waiting="Other")]
        )
        core.read_namespaced_pod_log.return_value = "text"
        assert self._runner()._read_pod_logs_best_effort(core, "j") == "text"
        core.read_namespaced_pod_log.return_value = None
        assert self._runner()._read_pod_logs_best_effort(core, "j") == ""
        core.read_namespaced_pod_log.side_effect = RuntimeError("x")
        assert self._runner()._read_pod_logs_best_effort(core, "j") == ""

    def test_collect_no_pods(self) -> None:
        """No pods means empty logs and unknown exit code."""
        core = MagicMock()
        core.list_namespaced_pod.return_value = SimpleNamespace(items=[])
        assert self._runner()._collect_pod_logs_and_exit_code(core, "j") == ("", None)

    def test_collect_unnamed_pod_and_read_failure(self) -> None:
        """An unnamed pod skips the log read; read failures are tolerated."""
        core = MagicMock()
        core.list_namespaced_pod.return_value = SimpleNamespace(items=[_pod(name=None)])
        assert self._runner()._collect_pod_logs_and_exit_code(core, "j") == ("", None)
        core.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_pod(exit_code=3)]
        )
        core.read_namespaced_pod_log.side_effect = RuntimeError("gone")
        assert self._runner()._collect_pod_logs_and_exit_code(core, "j") == ("", 3)

    def test_collect_reads_logs_and_exit_code(self) -> None:
        """Logs and the terminated exit code are returned together."""
        core = MagicMock()
        core.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_pod(exit_code=0)]
        )
        core.read_namespaced_pod_log.return_value = None
        assert self._runner()._collect_pod_logs_and_exit_code(core, "j") == ("", 0)


class TestCleanupAndClients:
    """ConfigMap cleanup and lazy client construction."""

    def test_cleanup_ignores_404(self) -> None:
        """A 404 on delete is silently ignored."""
        core = MagicMock()
        core.delete_namespaced_config_map.side_effect = SimpleNamespace(
            status=404
        )  # not an exception
        err = Exception("nf")
        err.status = 404  # type: ignore[attr-defined]
        core.delete_namespaced_config_map.side_effect = err
        mod.logger.warning = MagicMock()
        K8sRunner(batch_api=MagicMock(), core_api=core)._cleanup_configmap(core, "cm")
        mod.logger.warning.assert_not_called()

    def test_cleanup_logs_other_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Other delete failures are logged but not raised."""
        warn = MagicMock()
        monkeypatch.setattr(mod.logger, "warning", warn)
        core = MagicMock()
        core.delete_namespaced_config_map.side_effect = RuntimeError("denied")
        K8sRunner(batch_api=MagicMock(), core_api=core)._cleanup_configmap(core, "cm")
        warn.assert_called_once()

    def test_ensure_clients_returns_injected(self) -> None:
        """Injected clients are used as-is."""
        b, c = MagicMock(), MagicMock()
        assert K8sRunner(batch_api=b, core_api=c)._ensure_clients() == (b, c)

    @pytest.mark.parametrize("incluster_ok", [True, False])
    def test_ensure_clients_lazy_load(
        self, monkeypatch: pytest.MonkeyPatch, incluster_ok: bool
    ) -> None:
        """Missing clients are built after loading in-cluster or kubeconfig config."""
        import kubernetes

        class FakeConfigError(Exception):
            """Stand-in for kubernetes ConfigException."""

        calls: list[str] = []

        def incluster() -> None:
            """Fail or succeed in-cluster loading."""
            calls.append("incluster")
            if not incluster_ok:
                raise FakeConfigError()

        monkeypatch.setattr(kubernetes.config, "ConfigException", FakeConfigError)
        monkeypatch.setattr(kubernetes.config, "load_incluster_config", incluster)
        monkeypatch.setattr(
            kubernetes.config, "load_kube_config", lambda: calls.append("kube")
        )
        monkeypatch.setattr(kubernetes.client, "BatchV1Api", lambda: "batch")
        monkeypatch.setattr(kubernetes.client, "CoreV1Api", lambda: "core")
        runner = K8sRunner()
        assert runner._ensure_clients() == ("batch", "core")
        assert calls == (["incluster"] if incluster_ok else ["incluster", "kube"])

    def test_ensure_clients_fills_only_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An injected batch client is preserved while core is built."""
        import kubernetes

        monkeypatch.setattr(kubernetes.config, "load_incluster_config", lambda: None)
        monkeypatch.setattr(kubernetes.client, "BatchV1Api", lambda: "NEW-batch")
        monkeypatch.setattr(kubernetes.client, "CoreV1Api", lambda: "NEW-core")
        runner = K8sRunner(batch_api="mine")
        assert runner._ensure_clients() == ("mine", "NEW-core")
        runner2 = K8sRunner(core_api="mine-core")
        assert runner2._ensure_clients() == ("NEW-batch", "mine-core")
