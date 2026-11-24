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
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from apps.runworker.elder_sdk_bundle import sdk_file_for_runtime
from apps.runworker.gateway import DEFAULT_MAX_REQUESTS, RestrictionGateway
from apps.runworker.runtimes import CONTAINER_TMP_DIR, RuntimeSpec, get_runtime

logger = logging.getLogger(__name__)

MIN_MEMORY_MB = 64
MAX_MEMORY_MB = 2048
DEFAULT_MEMORY_MB = 256

MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 30

MIN_LOG_FLUSH_INTERVAL_SECONDS = 1.0
MAX_LOG_FLUSH_INTERVAL_SECONDS = 300.0
DEFAULT_LOG_FLUSH_INTERVAL_SECONDS = 30.0

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
        "ELDER_SDK_FILENAME",
        "ELDER_SDK_CONTENT",
        "ELDER_GATEWAY_URL",
        "ELDER_GATEWAY_TOKEN",
        "ELDER_API_URL",
        "ELDER_API_TOKEN",
        "ELDER_INTERNAL_TOKEN",
        "JWT_SECRET_KEY",
        "SECRET_KEY",
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
    "sdk_filename = os.environ.get('ELDER_SDK_FILENAME', '').strip()\n"
    "sdk_content = os.environ.get('ELDER_SDK_CONTENT', '')\n"
    "if sdk_filename and sdk_content:\n"
    "    with open(f'/workspace/{sdk_filename}', 'w', encoding='utf-8') as f:\n"
    "        f.write(sdk_content)\n"
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
    granted_scopes: tuple[str, ...] = ()
    allowed_egress_domains: tuple[str, ...] = ()
    elder_api_token: str = field(default="", repr=False)


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


def clamp_log_flush_interval(seconds: float | None) -> float:
    """Clamp periodic live-log push interval (seconds) into [1.0, 300.0]."""
    if seconds is None:
        return DEFAULT_LOG_FLUSH_INTERVAL_SECONDS
    return max(
        MIN_LOG_FLUSH_INTERVAL_SECONDS,
        min(MAX_LOG_FLUSH_INTERVAL_SECONDS, float(seconds)),
    )


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


GATEWAY_ACCESS_LABEL = "elder.penguintech.io/gateway-access"


class GatewayPortPool:
    """Thread-safe pool of the NetworkPolicy-allowlisted gateway ports.

    Execution pods may only reach the controller on these ports, so each
    concurrent execution borrows one for its restriction gateway and returns
    it afterwards.
    """

    def __init__(self, base_port: int, size: int) -> None:
        if not 1024 <= int(base_port) <= 65535 - int(size) or int(size) < 1:
            raise ValueError(
                f"Invalid gateway port pool base={base_port!r} size={size!r}"
            )
        self._lock = threading.Lock()
        self._free: list[int] = list(range(int(base_port), int(base_port) + int(size)))

    def acquire(self) -> int:
        """Borrow a free port; raises when every port is in use."""
        with self._lock:
            if not self._free:
                raise RuntimeError(
                    "No free restriction gateway port; raise the gateway port "
                    "pool size to match RUNS_MAX_CONCURRENT"
                )
            return self._free.pop(0)

    def release(self, port: int) -> None:
        """Return a previously borrowed port to the pool."""
        with self._lock:
            if port not in self._free:
                self._free.append(port)


GATEWAY_SECRET_PREFIX = "elder-run-gw-"
GATEWAY_TOKEN_SECRET_KEY = "token"


def _base_labels(
    request: RunJobRequest,
    spec: RuntimeSpec,
) -> dict[str, str]:
    """Return the labels shared by every object created for one execution."""
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
    if request.granted_scopes or request.allowed_egress_domains:
        # Selects the NetworkPolicy that opens egress to the controller's
        # per-execution restriction gateway and nothing else.
        labels[GATEWAY_ACCESS_LABEL] = "true"
    return labels


def build_configmap_manifest(
    request: RunJobRequest,
    *,
    namespace: str = "elder-runs",
    configmap_name: str | None = None,
) -> dict[str, Any]:
    """Build the ephemeral ConfigMap holding the user script, input JSON, and SDK."""
    spec = get_runtime(request.runtime)
    name = configmap_name or sanitize_k8s_name(
        request.execution_id, prefix="elder-run-cm-"
    )
    input_payload = json.dumps(
        request.input_data if request.input_data is not None else {},
        separators=(",", ":"),
    )
    data: dict[str, str] = {
        spec.filename: request.code,
        "input.json": input_payload,
    }
    sdk_entry = sdk_file_for_runtime(spec.name)
    if sdk_entry is not None:
        sdk_filename, sdk_source = sdk_entry
        data[sdk_filename] = sdk_source
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": _base_labels(request, spec),
        },
        "data": data,
    }


def build_gateway_secret_manifest(
    request: RunJobRequest,
    *,
    gateway_token: str,
    namespace: str = "elder-runs",
    secret_name: str | None = None,
    owner_job_name: str | None = None,
    owner_job_uid: str | None = None,
) -> dict[str, Any]:
    """Build the per-execution Secret holding the restriction gateway token.

    One immutable Secret per execution, labelled with the execution id. When
    the owning Job's UID is known it is set as owner so Kubernetes garbage
    collection removes the Secret with the Job even if the worker dies before
    its own cleanup runs.
    """
    spec = get_runtime(request.runtime)
    metadata: dict[str, Any] = {
        "name": secret_name
        or sanitize_k8s_name(request.execution_id, prefix=GATEWAY_SECRET_PREFIX),
        "namespace": namespace,
        "labels": _base_labels(request, spec),
    }
    if owner_job_name and owner_job_uid:
        metadata["ownerReferences"] = [
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "name": owner_job_name,
                "uid": owner_job_uid,
                "controller": False,
                "blockOwnerDeletion": False,
            }
        ]
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": metadata,
        "type": "Opaque",
        "immutable": True,
        "stringData": {GATEWAY_TOKEN_SECRET_KEY: gateway_token},
    }


def build_job_manifest(
    request: RunJobRequest,
    *,
    namespace: str = "elder-runs",
    runtime_class_name: str | None = None,
    job_name: str | None = None,
    configmap_name: str | None = None,
    service_account_name: str | None = None,
    gateway_url: str | None = None,
    gateway_token_secret_name: str | None = None,
) -> dict[str, Any]:
    """Build a hardened ``batch/v1`` Job manifest for a function execution.

    The pod never receives platform credentials: no API token is mounted,
    service links are disabled, and the only credential it may carry is the
    random per-execution restriction gateway token. That token is never
    written into the Job or Pod spec: it is referenced from a per-execution
    Secret, so reading Job/Pod objects or their events does not reveal it.
    """
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

    env_list: list[dict[str, Any]] = [
        {"name": "ELDER_RUN_INPUT", "value": input_json},
        {"name": "ELDER_EXECUTION_ID", "value": request.execution_id},
        {"name": "TMPDIR", "value": CONTAINER_TMP_DIR},
    ]
    if gateway_url and gateway_token_secret_name:
        env_list.append({"name": "ELDER_GATEWAY_URL", "value": gateway_url})
        env_list.append(
            {
                "name": "ELDER_GATEWAY_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": gateway_token_secret_name,
                        "key": GATEWAY_TOKEN_SECRET_KEY,
                        "optional": False,
                    }
                },
            }
        )
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
        "enableServiceLinks": False,
        "hostNetwork": False,
        "hostPID": False,
        "hostIPC": False,
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
        init_env: list[dict[str, str]] = [
            {"name": "ELDER_ARTIFACT_URL", "value": request.code_url or ""},
            {
                "name": "ELDER_ARTIFACT_SHA256",
                "value": request.code_sha256 or "",
            },
            {"name": "ELDER_ARTIFACT_FILENAME", "value": spec.filename},
            {"name": "ELDER_RUN_INPUT", "value": input_json},
        ]
        sdk_entry = sdk_file_for_runtime(spec.name)
        if sdk_entry is not None:
            sdk_filename, sdk_source = sdk_entry
            init_env.append({"name": "ELDER_SDK_FILENAME", "value": sdk_filename})
            init_env.append({"name": "ELDER_SDK_CONTENT", "value": sdk_source})
        pod_spec["initContainers"] = [
            {
                "name": "fetch-artifact",
                "image": fetch_image,
                "command": ["python3", "-c", _INIT_FETCH_SCRIPT],
                "env": init_env,
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
    if service_account_name:
        pod_spec["serviceAccountName"] = service_account_name

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
        service_account_name: str | None = None,
        elder_api_url: str = "http://hub-api:5000",
        gateway_bind_host: str | None = None,
        gateway_port_pool: GatewayPortPool | None = None,
        max_gateway_requests: int = DEFAULT_MAX_REQUESTS,
    ) -> None:
        self.namespace = namespace
        self.runtime_class_name = runtime_class_name or None
        self.service_account_name = service_account_name or None
        self.elder_api_url = (elder_api_url or "http://hub-api:5000").rstrip("/")
        self.gateway_bind_host = (gateway_bind_host or "").strip() or None
        self.gateway_port_pool = gateway_port_pool
        self.max_gateway_requests = max(1, int(max_gateway_requests))
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

    def execute(
        self,
        request: RunJobRequest,
        *,
        on_logs: Callable[[str], None] | None = None,
        log_flush_interval: float = DEFAULT_LOG_FLUSH_INTERVAL_SECONDS,
    ) -> RunExecutionResult:
        """Create ConfigMap + Job, wait for completion, collect logs, and clean up.

        The gateway token Secret and the code ConfigMap are removed on every
        exit path (success, failure, timeout, cancellation or error).
        """
        started = time.monotonic()
        batch_api, core_api = self._ensure_clients()

        job_name = sanitize_k8s_name(request.execution_id, prefix="elder-run-")
        cm_name = sanitize_k8s_name(request.execution_id, prefix="elder-run-cm-")

        use_init_fetch = bool(request.use_init_container and request.code_url)
        secret_name = sanitize_k8s_name(
            request.execution_id, prefix=GATEWAY_SECRET_PREFIX
        )
        gateway, gateway_port = self._start_gateway(request)
        created_cm = False
        created_secret = False

        try:
            job_manifest = build_job_manifest(
                request,
                namespace=self.namespace,
                runtime_class_name=self.runtime_class_name,
                job_name=job_name,
                configmap_name=cm_name,
                service_account_name=self.service_account_name,
                gateway_url=gateway.url if gateway is not None else None,
                gateway_token_secret_name=secret_name if gateway is not None else None,
            )

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

            created_job = batch_api.create_namespaced_job(
                namespace=self.namespace,
                body=job_manifest,
            )

            if gateway is not None:
                # Created after the Job so the Secret can name it as owner;
                # the pod cannot start its container until the Secret exists.
                job_uid = getattr(getattr(created_job, "metadata", None), "uid", None)
                if not isinstance(job_uid, str) or not job_uid:
                    job_uid = None
                    logger.warning(
                        "runworker_gateway_secret_no_owner",
                        extra={
                            "job_name": job_name,
                            "detail": "Job UID unavailable; Secret relies on "
                            "explicit cleanup only",
                        },
                    )
                # Marked before the call: a create that fails client-side may
                # still have been applied, so cleanup is always attempted.
                created_secret = True
                core_api.create_namespaced_secret(
                    namespace=self.namespace,
                    body=build_gateway_secret_manifest(
                        request,
                        gateway_token=gateway.gateway_token,
                        namespace=self.namespace,
                        secret_name=secret_name,
                        owner_job_name=job_name,
                        owner_job_uid=job_uid,
                    ),
                )

            timeout = clamp_timeout_seconds(request.timeout_seconds)
            deadline = started + timeout + 15.0
            flush_interval = max(0.01, float(log_flush_interval))
            last_log_flush = started
            last_flushed_logs = ""
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

                now = time.monotonic()
                if now >= deadline:
                    status = "timeout"
                    error_msg = f"Worker poll deadline exceeded ({timeout}s)"
                    break

                if on_logs is not None and (now - last_log_flush) >= flush_interval:
                    last_log_flush = now
                    current_logs = self._read_pod_logs_best_effort(core_api, job_name)
                    if current_logs and current_logs != last_flushed_logs:
                        last_flushed_logs = current_logs
                        try:
                            on_logs(current_logs)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "runworker_live_log_flush_callback_failed",
                                extra={"job_name": job_name, "error": str(exc)},
                            )

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
            self._stop_gateway(gateway, gateway_port, request.execution_id)
            if created_secret:
                self._cleanup_gateway_secret(core_api, secret_name)
            if created_cm:
                self._cleanup_configmap(core_api, cm_name)

    def _start_gateway(
        self, request: RunJobRequest
    ) -> tuple[RestrictionGateway | None, int | None]:
        """Start the per-execution restriction gateway when the request needs one.

        Executions with granted scopes or an egress allowlist reach the Elder
        API and the internet only through this proxy. If the controller was
        not configured to host it, the execution is refused rather than run
        with a silently missing capability.
        """
        if not (request.granted_scopes or request.allowed_egress_domains):
            return None, None
        if not self.gateway_bind_host or self.gateway_port_pool is None:
            raise RuntimeError(
                "Execution requests Elder API scopes or an egress allowlist but "
                "the restriction gateway is not configured for the Job backend "
                "(set POD_IP and RUNS_GATEWAY_PORT_BASE)"
            )
        port = self.gateway_port_pool.acquire()
        try:
            gateway = RestrictionGateway(
                elder_api_url=self.elder_api_url,
                elder_api_token=request.elder_api_token,
                granted_scopes=request.granted_scopes,
                allowed_egress_domains=request.allowed_egress_domains,
                execution_id=request.execution_id,
                tenant_id=request.tenant_id or "",
                max_requests=self.max_gateway_requests,
                bind_host=self.gateway_bind_host,
                bind_port=port,
            ).start()
        except Exception:
            self.gateway_port_pool.release(port)
            raise
        return gateway, port

    def _stop_gateway(
        self,
        gateway: RestrictionGateway | None,
        port: int | None,
        execution_id: str,
    ) -> None:
        """Stop the per-execution gateway and return its port to the pool."""
        if gateway is not None:
            try:
                gateway.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "runworker_gateway_stop_failed",
                    extra={"execution_id": execution_id, "error": str(exc)},
                )
        if port is not None and self.gateway_port_pool is not None:
            self.gateway_port_pool.release(port)

    def _read_pod_logs_best_effort(self, core_api: Any, job_name: str) -> str:
        """Read current pod logs for an in-progress Job, ignoring transient states."""
        try:
            pods_resp = core_api.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f"job-name={job_name}",
            )
        except Exception:  # noqa: BLE001
            return ""

        items = getattr(pods_resp, "items", None) or []
        if not items:
            return ""

        pod = items[0]
        pod_name = getattr(getattr(pod, "metadata", None), "name", None)
        if not pod_name:
            return ""

        container_statuses = (
            getattr(getattr(pod, "status", None), "container_statuses", None) or []
        )
        for cs in container_statuses:
            waiting = getattr(getattr(cs, "state", None), "waiting", None)
            if waiting is not None and getattr(waiting, "reason", None) in (
                "ContainerCreating",
                "PodInitializing",
            ):
                return ""

        try:
            return (
                core_api.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=self.namespace,
                )
                or ""
            )
        except Exception:  # noqa: BLE001
            return ""

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

    def _cleanup_gateway_secret(self, core_api: Any, secret_name: str) -> None:
        """Delete the per-execution gateway token Secret, ignoring not-found.

        Only ever called with the name this runner derived for the execution
        it created, so the delete permission is never used on other Secrets.
        A failed delete is logged as an error: the owning Job's garbage
        collection is then the remaining removal path.
        """
        try:
            core_api.delete_namespaced_secret(
                name=secret_name,
                namespace=self.namespace,
            )
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) == 404:
                return
            logger.error(
                "runworker_gateway_secret_cleanup_failed",
                extra={"secret": secret_name, "error": str(exc)},
            )
