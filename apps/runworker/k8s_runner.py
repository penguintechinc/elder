"""Hardened Kubernetes batch/v1 Job runner for Elder Runs functions.

Executes user code inside a single-shot, rootless, read-only-rootfs
Kubernetes Job with an ephemeral ConfigMap carrying the script and JSON
input, then captures logs, extracts structured JSON output (if present),
and deletes the ConfigMap.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from apps.runworker.runtimes import CONTAINER_TMP_DIR, RuntimeSpec, get_runtime

logger = logging.getLogger(__name__)

MIN_MEMORY_MB = 64
MAX_MEMORY_MB = 2048
DEFAULT_MEMORY_MB = 256

MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 30

DEFAULT_TTL_SECONDS = 120
DEFAULT_TMP_SIZE_LIMIT = "128Mi"
NONROOT_UID = 65532
NONROOT_GID = 65532

_PROTECTED_ENV_KEYS = frozenset(
    {
        "ELDER_RUN_INPUT",
        "ELDER_EXECUTION_ID",
        "ELDER_ARTIFACT_URL",
        "ELDER_ARTIFACT_SHA256",
        "ELDER_ARTIFACT_FILENAME",
        "HOME",
        "TMPDIR",
        "PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
    }
)
_K8S_NAME_RE = re.compile(r"[^a-z0-9-]+")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_WORKSPACE_SIZE_LIMIT = "64Mi"

_INIT_FETCH_SCRIPT = (
    "import hashlib, os, urllib.request\n"
    "url = os.environ['ELDER_ARTIFACT_URL']\n"
    "expected = os.environ.get('ELDER_ARTIFACT_SHA256', '').strip()\n"
    "filename = os.environ['ELDER_ARTIFACT_FILENAME']\n"
    "input_json = os.environ.get('ELDER_RUN_INPUT', '{}')\n"
    "with urllib.request.urlopen(url, timeout=30) as resp:\n"
    "    data = resp.read()\n"
    "if expected:\n"
    "    actual = hashlib.sha256(data).hexdigest()\n"
    "    if actual != expected:\n"
    "        raise SystemExit(f'SHA-256 mismatch: expected {expected}, got {actual}')\n"
    "with open(f'/workspace/{filename}', 'wb') as f:\n"
    "    f.write(data)\n"
    "with open('/workspace/input.json', 'w', encoding='utf-8') as f:\n"
    "    f.write(input_json)\n"
)


@dataclass(frozen=True)
class RunJobRequest:
    """Parameters for a single serverless function execution."""

    execution_id: str
    runtime: str
    code: str
    input_data: Any = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    memory_mb: int = DEFAULT_MEMORY_MB
    env: dict[str, str] = field(default_factory=dict)
    function_id: str | None = None
    tenant_id: str | None = None
    code_url: str | None = field(default=None, repr=False)
    code_sha256: str | None = None
    storage_backend: str = "database"
    use_init_container: bool = False


@dataclass(frozen=True)
class RunExecutionResult:
    """Outcome reported back to the Elder API after a Job finishes."""

    status: str  # "succeeded" | "failed" | "timeout"
    exit_code: int | None
    logs: str
    output: Any | None
    duration_ms: int
    error: str | None = None


def clamp_memory_mb(memory_mb: int | None) -> int:
    """Clamp requested memory (MiB) into the safe [64, 2048] range."""
    if memory_mb is None:
        return DEFAULT_MEMORY_MB
    return max(MIN_MEMORY_MB, min(MAX_MEMORY_MB, int(memory_mb)))


def clamp_timeout_seconds(timeout_seconds: int | None) -> int:
    """Clamp requested timeout (seconds) into the safe [1, 300] range."""
    if timeout_seconds is None:
        return DEFAULT_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, int(timeout_seconds)))


def sanitize_k8s_name(execution_id: str, prefix: str = "elder-run-") -> str:
    """Build a valid DNS-1123 Kubernetes resource name (<= 63 chars)."""
    raw = _K8S_NAME_RE.sub("-", (execution_id or "").lower()).strip("-")
    if not raw:
        raw = uuid.uuid4().hex[:12]
    max_body = 63 - len(prefix)
    name = f"{prefix}{raw[:max_body]}".rstrip("-")
    return name


def extract_structured_output(logs: str) -> Any | None:
    """Parse structured JSON from the last non-empty line of pod logs.

    Returns the decoded JSON value if the last non-empty line is valid JSON,
    otherwise ``None``.
    """
    if not logs:
        return None
    for line in reversed(logs.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None
    return None


def _base_labels(
    request: RunJobRequest,
    spec: RuntimeSpec,
) -> dict[str, str]:
    labels = {
        "app.kubernetes.io/name": "elder-run",
        "app.kubernetes.io/component": "function-execution",
        "app.kubernetes.io/managed-by": "svc-runworker",
        "elder.penguintech.io/runtime": spec.name,
    }
    exec_label = _K8S_NAME_RE.sub("-", (request.execution_id or "").lower()).strip("-")[
        :63
    ]
    if exec_label:
        labels["elder.penguintech.io/execution-id"] = exec_label
    return labels


def build_configmap_manifest(
    request: RunJobRequest,
    *,
    namespace: str = "elder-runs",
    configmap_name: str | None = None,
) -> dict[str, Any]:
    """Build the ephemeral ConfigMap holding the user script and input JSON."""
    spec = get_runtime(request.runtime)
    name = configmap_name or sanitize_k8s_name(
        request.execution_id, prefix="elder-run-cm-"
    )
    input_payload = json.dumps(
        request.input_data if request.input_data is not None else {},
        separators=(",", ":"),
    )
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": _base_labels(request, spec),
        },
        "data": {
            spec.filename: request.code,
            "input.json": input_payload,
        },
    }


def build_job_manifest(
    request: RunJobRequest,
    *,
    namespace: str = "elder-runs",
    runtime_class_name: str | None = None,
    job_name: str | None = None,
    configmap_name: str | None = None,
) -> dict[str, Any]:
    """Build a hardened ``batch/v1`` Job manifest for a function execution."""
    spec = get_runtime(request.runtime)
    name = job_name or sanitize_k8s_name(request.execution_id, prefix="elder-run-")
    cm_name = configmap_name or sanitize_k8s_name(
        request.execution_id, prefix="elder-run-cm-"
    )

    mem_limit = clamp_memory_mb(request.memory_mb)
    mem_request = max(MIN_MEMORY_MB, mem_limit // 2)
    cpu_limit_m = min(2000, max(250, mem_limit * 2))
    cpu_request_m = max(100, cpu_limit_m // 4)
    timeout = clamp_timeout_seconds(request.timeout_seconds)

    input_json = json.dumps(
        request.input_data if request.input_data is not None else {},
        separators=(",", ":"),
    )

    env_list: list[dict[str, str]] = [
        {"name": "ELDER_RUN_INPUT", "value": input_json},
        {"name": "ELDER_EXECUTION_ID", "value": request.execution_id},
        {"name": "TMPDIR", "value": CONTAINER_TMP_DIR},
    ]
    seen_env = {entry["name"] for entry in env_list}

    for key, val in spec.extra_env:
        if key not in seen_env:
            env_list.append({"name": key, "value": val})
            seen_env.add(key)

    for key, val in (request.env or {}).items():
        if key in _PROTECTED_ENV_KEYS or key in seen_env or not _ENV_KEY_RE.match(key):
            continue
        env_list.append({"name": key, "value": str(val)})
        seen_env.add(key)

    labels = _base_labels(request, spec)

    use_init_fetch = bool(request.use_init_container and request.code_url)
    workspace_volume: dict[str, Any] = (
        {
            "name": "workspace",
            "emptyDir": {
                "sizeLimit": DEFAULT_WORKSPACE_SIZE_LIMIT,
            },
        }
        if use_init_fetch
        else {
            "name": "workspace",
            "configMap": {
                "name": cm_name,
                "defaultMode": 0o444,
            },
        }
    )

    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": NONROOT_UID,
            "runAsGroup": NONROOT_GID,
            "fsGroup": NONROOT_GID,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "runner",
                "image": spec.image,
                "command": list(spec.command),
                "workingDir": "/workspace",
                "env": env_list,
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": NONROOT_UID,
                    "runAsGroup": NONROOT_GID,
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "resources": {
                    "requests": {
                        "memory": f"{mem_request}Mi",
                        "cpu": f"{cpu_request_m}m",
                    },
                    "limits": {
                        "memory": f"{mem_limit}Mi",
                        "cpu": f"{cpu_limit_m}m",
                    },
                },
                "volumeMounts": [
                    {
                        "name": "workspace",
                        "mountPath": "/workspace",
                        "readOnly": True,
                    },
                    {
                        "name": "tmp",
                        "mountPath": CONTAINER_TMP_DIR,
                    },
                ],
            }
        ],
        "volumes": [
            workspace_volume,
            {
                "name": "tmp",
                "emptyDir": {
                    "sizeLimit": DEFAULT_TMP_SIZE_LIMIT,
                },
            },
        ],
    }

    if use_init_fetch:
        fetch_image = get_runtime("python").image
        pod_spec["initContainers"] = [
            {
                "name": "fetch-artifact",
                "image": fetch_image,
                "command": ["python3", "-c", _INIT_FETCH_SCRIPT],
                "env": [
                    {"name": "ELDER_ARTIFACT_URL", "value": request.code_url},
                    {
                        "name": "ELDER_ARTIFACT_SHA256",
                        "value": request.code_sha256 or "",
                    },
                    {"name": "ELDER_ARTIFACT_FILENAME", "value": spec.filename},
                    {"name": "ELDER_RUN_INPUT", "value": input_json},
                ],
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": NONROOT_UID,
                    "runAsGroup": NONROOT_GID,
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "resources": {
                    "requests": {"memory": "64Mi", "cpu": "50m"},
                    "limits": {"memory": "128Mi", "cpu": "250m"},
                },
                "volumeMounts": [
                    {
                        "name": "workspace",
                        "mountPath": "/workspace",
                        "readOnly": False,
                    },
                ],
            }
        ]

    if runtime_class_name:
        pod_spec["runtimeClassName"] = runtime_class_name

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": timeout,
            "ttlSecondsAfterFinished": DEFAULT_TTL_SECONDS,
            "template": {
                "metadata": {
                    "labels": labels,
                },
                "spec": pod_spec,
            },
        },
    }


class K8sRunner:
    """Executes Elder Runs functions as isolated Kubernetes Jobs."""

    def __init__(
        self,
        *,
        namespace: str = "elder-runs",
        runtime_class_name: str | None = None,
        batch_api: Any | None = None,
        core_api: Any | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self.namespace = namespace
        self.runtime_class_name = runtime_class_name or None
        self._batch_api = batch_api
        self._core_api = core_api
        self.poll_interval = max(0.05, float(poll_interval))

    def _ensure_clients(self) -> tuple[Any, Any]:
        """Lazily load Kubernetes config and API clients."""
        if self._batch_api is not None and self._core_api is not None:
            return self._batch_api, self._core_api

        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        if self._batch_api is None:
            self._batch_api = client.BatchV1Api()
        if self._core_api is None:
            self._core_api = client.CoreV1Api()
        return self._batch_api, self._core_api

    def execute(self, request: RunJobRequest) -> RunExecutionResult:
        """Create ConfigMap + Job, wait for completion, collect logs, and clean up."""
        started = time.monotonic()
        batch_api, core_api = self._ensure_clients()

        job_name = sanitize_k8s_name(request.execution_id, prefix="elder-run-")
        cm_name = sanitize_k8s_name(request.execution_id, prefix="elder-run-cm-")

        use_init_fetch = bool(request.use_init_container and request.code_url)
        job_manifest = build_job_manifest(
            request,
            namespace=self.namespace,
            runtime_class_name=self.runtime_class_name,
            job_name=job_name,
            configmap_name=cm_name,
        )

        created_cm = False
        if not use_init_fetch:
            cm_manifest = build_configmap_manifest(
                request,
                namespace=self.namespace,
                configmap_name=cm_name,
            )
            core_api.create_namespaced_config_map(
                namespace=self.namespace,
                body=cm_manifest,
            )
            created_cm = True

        try:
            batch_api.create_namespaced_job(
                namespace=self.namespace,
                body=job_manifest,
            )

            timeout = clamp_timeout_seconds(request.timeout_seconds)
            deadline = started + timeout + 15.0
            status = "failed"
            error_msg: str | None = None

            while True:
                job_obj = batch_api.read_namespaced_job_status(
                    name=job_name,
                    namespace=self.namespace,
                )
                job_status = getattr(job_obj, "status", None)
                succeeded = getattr(job_status, "succeeded", None) or 0
                failed = getattr(job_status, "failed", None) or 0
                conditions = getattr(job_status, "conditions", None) or []

                if succeeded >= 1:
                    status = "succeeded"
                    break

                deadline_exceeded = any(
                    getattr(cond, "type", None) == "Failed"
                    and getattr(cond, "reason", None) == "DeadlineExceeded"
                    for cond in conditions
                )
                if deadline_exceeded:
                    status = "timeout"
                    error_msg = f"Execution exceeded activeDeadlineSeconds ({timeout}s)"
                    break

                if failed >= 1:
                    status = "failed"
                    break

                if time.monotonic() >= deadline:
                    status = "timeout"
                    error_msg = f"Worker poll deadline exceeded ({timeout}s)"
                    break

                time.sleep(self.poll_interval)

            logs, exit_code = self._collect_pod_logs_and_exit_code(core_api, job_name)
            output = extract_structured_output(logs)
            duration_ms = int((time.monotonic() - started) * 1000)

            return RunExecutionResult(
                status=status,
                exit_code=exit_code,
                logs=logs,
                output=output,
                duration_ms=duration_ms,
                error=error_msg,
            )
        finally:
            if created_cm:
                self._cleanup_configmap(core_api, cm_name)

    def _collect_pod_logs_and_exit_code(
        self,
        core_api: Any,
        job_name: str,
    ) -> tuple[str, int | None]:
        """Fetch logs and container exit code from the Job's pod."""
        pods_resp = core_api.list_namespaced_pod(
            namespace=self.namespace,
            label_selector=f"job-name={job_name}",
        )
        items = getattr(pods_resp, "items", None) or []
        if not items:
            return "", None

        pod = items[0]
        pod_name = getattr(getattr(pod, "metadata", None), "name", None)
        exit_code: int | None = None

        container_statuses = (
            getattr(getattr(pod, "status", None), "container_statuses", None) or []
        )
        for cs in container_statuses:
            terminated = getattr(getattr(cs, "state", None), "terminated", None)
            if terminated is not None:
                exit_code = getattr(terminated, "exit_code", None)
                break

        logs = ""
        if pod_name:
            try:
                logs = (
                    core_api.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=self.namespace,
                    )
                    or ""
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "runworker_pod_log_read_failed",
                    extra={"job_name": job_name, "error": str(exc)},
                )

        return logs, exit_code

    def _cleanup_configmap(self, core_api: Any, cm_name: str) -> None:
        """Delete the ephemeral ConfigMap, ignoring not-found errors."""
        try:
            core_api.delete_namespaced_config_map(
                name=cm_name,
                namespace=self.namespace,
            )
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) == 404:
                return
            logger.warning(
                "runworker_configmap_cleanup_failed",
                extra={"configmap": cm_name, "error": str(exc)},
            )
