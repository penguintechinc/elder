"""Elder Runs worker poll loop (svc-runworker).

Completely decoupled from Elder's database and Redis so it can run either
inside Elder's own Kubernetes cluster or in a remote Kubernetes cluster.
Polls Elder's internal runs API for pending executions, launches hardened
per-execution Kubernetes Jobs via ``K8sRunner``, and posts results back.

Trust model: the worker authenticates to ``hub-api`` only with its own
provisioned, revocable, runs-scoped worker token. It never holds the platform
token-signing key and refuses to start if one is present in its environment.
Tenant code runs in per-execution Job pods by default; in-pod execution is
opt-in and gated on a VM-isolated pod runtime (see ``sandbox_runner``).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import signal
import socket
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

from apps.runworker.k8s_runner import (
    DEFAULT_LOG_FLUSH_INTERVAL_SECONDS,
    DEFAULT_MEMORY_MB,
    DEFAULT_TIMEOUT_SECONDS,
    GatewayPortPool,
    K8sRunner,
    RunExecutionResult,
    RunJobRequest,
    clamp_log_flush_interval,
)
from apps.runworker.sandbox_runner import (
    KATA_EXCEPTION_APPROVED,
    SandboxIsolationError,
    SandboxRunner,
    evaluate_sandbox_isolation,
    harden_worker_process,
    is_sandboxed_runtime_class,
)

logger = logging.getLogger("apps.runworker")


def _default_health_file() -> str:
    return os.path.join(tempfile.gettempdir(), "runworker.healthy")


RUNNER_BACKENDS: tuple[str, ...] = ("k8s_job", "sandbox", "auto")
DEFAULT_RUNNER_BACKEND = "k8s_job"

# Platform signing material must never be reachable from a process that
# schedules or runs tenant code; the worker refuses to start if it is present.
FORBIDDEN_ENV_KEYS: tuple[str, ...] = ("JWT_SECRET_KEY", "SECRET_KEY")
WORKER_TOKEN_ENV = "ELDER_INTERNAL_TOKEN"  # noqa: S105 - env var name, not a secret


class WorkerConfigError(RuntimeError):
    """Raised when the worker environment is unsafe or incomplete to start."""


@dataclass(frozen=True)
class RunWorkerConfig:
    """Environment-driven configuration for ``svc-runworker``."""

    elder_api_url: str = "http://hub-api:5000"
    elder_internal_token: str = field(default="", repr=False)
    execution_namespace: str = "elder-runs"
    worker_cluster: str = ""
    runtime_class_name: str = ""
    poll_interval_seconds: float = 3.0
    max_concurrent: int = 5
    worker_id: str = ""
    health_file: str = field(default_factory=_default_health_file)
    http_timeout_seconds: float = 30.0
    s3_fetch_mode: str = "worker"  # "worker" (zero-egress pods) | "init_container"
    log_flush_interval_seconds: float = DEFAULT_LOG_FLUSH_INTERVAL_SECONDS
    runner_backend: str = DEFAULT_RUNNER_BACKEND  # "k8s_job" | "sandbox" | "auto"
    pod_runtime_class: str = ""  # runtime class of THIS worker pod (chart-set)
    sandboxed_runtime_classes: tuple[str, ...] = ()
    kata_exception_sandbox: str = ""  # "approved" permits in-pod runs without Kata
    kata_exception_jobs: str = ""  # "approved" permits Job pods without Kata
    execution_service_account: str = ""
    gateway_bind_host: str = ""  # controller pod IP reachable by execution pods
    gateway_port_base: int = 0  # 0 disables the Job-backend restriction gateway
    heartbeat_interval_seconds: float = 30.0
    worker_version: str = ""

    @classmethod
    def from_env(cls) -> RunWorkerConfig:
        """Load worker configuration from process environment variables."""
        api_url = (os.getenv("ELDER_API_URL") or "http://hub-api:5000").rstrip("/")
        present = [key for key in FORBIDDEN_ENV_KEYS if os.getenv(key)]
        if present:
            raise WorkerConfigError(
                "svc-runworker must not be given platform signing keys; remove "
                f"{', '.join(present)} from its environment and authenticate "
                f"with a provisioned worker token ({WORKER_TOKEN_ENV})"
            )
        token = (os.getenv(WORKER_TOKEN_ENV) or "").strip()
        if not token:
            raise WorkerConfigError(
                f"{WORKER_TOKEN_ENV} is required: onboard this worker via "
                "POST /api/v1/admin/workers and mount the issued token from a Secret"
            )
        namespace = os.getenv("RUNS_EXECUTION_NAMESPACE") or "elder-runs"
        cluster = (os.getenv("RUNS_WORKER_CLUSTER") or "").strip()
        runtime_class = (os.getenv("RUNS_RUNTIME_CLASS_NAME") or "").strip()
        poll_interval = max(0.5, float(os.getenv("RUNS_POLL_INTERVAL_SECONDS") or "3"))
        max_concurrent = max(1, int(os.getenv("RUNS_MAX_CONCURRENT") or "5"))
        worker_id = (
            os.getenv("POD_NAME") or os.getenv("RUNS_WORKER_ID") or socket.gethostname()
        )
        health_file = os.getenv("RUNS_HEALTH_FILE") or _default_health_file()
        http_timeout = max(5.0, float(os.getenv("RUNS_HTTP_TIMEOUT_SECONDS") or "30"))
        raw_fetch_mode = (os.getenv("RUNS_S3_FETCH_MODE") or "worker").strip().lower()
        s3_fetch_mode = (
            raw_fetch_mode
            if raw_fetch_mode in ("worker", "init_container")
            else "worker"
        )
        raw_flush_interval = (
            os.getenv("RUNS_LOG_FLUSH_INTERVAL_SECONDS") or ""
        ).strip()
        log_flush_interval = clamp_log_flush_interval(
            float(raw_flush_interval) if raw_flush_interval else None
        )
        runner_backend = (
            (os.getenv("RUNS_RUNNER_BACKEND") or DEFAULT_RUNNER_BACKEND).strip().lower()
        )
        if runner_backend not in RUNNER_BACKENDS:
            raise WorkerConfigError(
                f"Unsupported RUNS_RUNNER_BACKEND {runner_backend!r}; "
                f"expected one of {', '.join(RUNNER_BACKENDS)}"
            )
        raw_port_base = (os.getenv("RUNS_GATEWAY_PORT_BASE") or "0").strip()
        try:
            gateway_port_base = int(raw_port_base)
        except ValueError as exc:
            raise WorkerConfigError(
                f"RUNS_GATEWAY_PORT_BASE must be an integer, got {raw_port_base!r}"
            ) from exc
        raw_hb_interval = (os.getenv("RUNS_HEARTBEAT_INTERVAL_SECONDS") or "30").strip()
        try:
            heartbeat_interval = max(5.0, float(raw_hb_interval))
        except ValueError:
            heartbeat_interval = 30.0
        worker_version = (
            os.getenv("ELDER_VERSION") or os.getenv("APP_VERSION") or ""
        ).strip()
        return cls(
            elder_api_url=api_url,
            elder_internal_token=token,
            execution_namespace=namespace,
            worker_cluster=cluster,
            runtime_class_name=runtime_class,
            poll_interval_seconds=poll_interval,
            max_concurrent=max_concurrent,
            worker_id=worker_id,
            health_file=health_file,
            http_timeout_seconds=http_timeout,
            s3_fetch_mode=s3_fetch_mode,
            log_flush_interval_seconds=log_flush_interval,
            runner_backend=runner_backend,
            pod_runtime_class=(os.getenv("RUNS_POD_RUNTIME_CLASS") or "").strip(),
            sandboxed_runtime_classes=_parse_str_tuple(
                os.getenv("RUNS_SANDBOXED_RUNTIME_CLASSES")
            ),
            kata_exception_sandbox=(
                os.getenv("RUNS_KATA_EXCEPTION_SANDBOX") or ""
            ).strip(),
            kata_exception_jobs=(os.getenv("RUNS_KATA_EXCEPTION_JOBS") or "").strip(),
            execution_service_account=(
                os.getenv("RUNS_EXECUTION_SERVICE_ACCOUNT") or ""
            ).strip(),
            gateway_bind_host=(os.getenv("POD_IP") or "").strip(),
            gateway_port_base=gateway_port_base,
            heartbeat_interval_seconds=heartbeat_interval,
            worker_version=worker_version,
        )


CONFIGMAP_MAX_CODE_BYTES = 900 * 1024  # Safe margin below K8s 1 MiB etcd limit


def _parse_str_tuple(raw: Any) -> tuple[str, ...]:
    """Normalize a list/tuple/set or delimited string into a clean tuple of strings."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
        return tuple(parts)
    if isinstance(raw, (list, tuple, set)):
        return tuple(str(v).strip() for v in raw if str(v).strip())
    return ()


def _request_from_payload(item: dict[str, Any]) -> RunJobRequest:
    """Convert an API pending-execution payload into a ``RunJobRequest``."""
    execution_id = str(item.get("execution_id") or item.get("id") or "")
    if not execution_id:
        raise ValueError("Pending execution payload is missing execution_id/id")
    runtime = str(item.get("runtime") or "")
    code = str(item.get("code") or "")
    code_url = str(item["code_url"]) if item.get("code_url") else None
    code_sha256 = str(item["code_sha256"]) if item.get("code_sha256") else None
    storage_backend = str(item.get("storage_backend") or "database")
    input_data = item.get("input") if "input" in item else item.get("input_data")
    timeout_seconds = int(item.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    memory_mb = int(item.get("memory_mb") or DEFAULT_MEMORY_MB)
    env = item.get("env") or item.get("env_vars")
    env_dict = env if isinstance(env, dict) else {}
    granted_scopes = _parse_str_tuple(item.get("granted_scopes"))
    allowed_egress_domains = _parse_str_tuple(item.get("allowed_egress_domains"))
    elder_api_token = str(item.get("elder_api_token") or "")
    return RunJobRequest(
        execution_id=execution_id,
        runtime=runtime,
        code=code,
        input_data=input_data,
        timeout_seconds=timeout_seconds,
        memory_mb=memory_mb,
        env={str(k): str(v) for k, v in env_dict.items()},
        function_id=str(item["function_id"]) if item.get("function_id") else None,
        tenant_id=str(item["tenant_id"]) if item.get("tenant_id") else None,
        code_url=code_url,
        code_sha256=code_sha256,
        storage_backend=storage_backend,
        granted_scopes=granted_scopes,
        allowed_egress_domains=allowed_egress_domains,
        elder_api_token=elder_api_token,
    )


class RunWorkerService:
    """Async polling service that runs claimed Elder Runs executions."""

    def __init__(
        self,
        config: RunWorkerConfig,
        *,
        runner: Any | None = None,
        sandbox_runner: SandboxRunner | None = None,
        k8s_runner: K8sRunner | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._explicit_runner = runner
        self.backend = (config.runner_backend or DEFAULT_RUNNER_BACKEND).strip().lower()
        if self.backend not in RUNNER_BACKENDS:
            raise WorkerConfigError(
                f"Unsupported runner backend {self.backend!r}; "
                f"expected one of {', '.join(RUNNER_BACKENDS)}"
            )
        self.sandbox_allowed, self.sandbox_reason = evaluate_sandbox_isolation(
            pod_runtime_class=config.pod_runtime_class,
            sandboxed_runtime_classes=config.sandboxed_runtime_classes,
            kata_exception=config.kata_exception_sandbox,
        )
        if runner is None:
            self._enforce_isolation_policy()
        self.sandbox_runner = sandbox_runner or SandboxRunner(
            elder_api_url=config.elder_api_url,
        )
        self.k8s_runner = k8s_runner or K8sRunner(
            namespace=config.execution_namespace,
            runtime_class_name=config.runtime_class_name or None,
            service_account_name=config.execution_service_account or None,
            elder_api_url=config.elder_api_url,
            gateway_bind_host=config.gateway_bind_host or None,
            gateway_port_pool=(
                GatewayPortPool(config.gateway_port_base, config.max_concurrent)
                if config.gateway_port_base and config.gateway_bind_host
                else None
            ),
        )
        if runner is not None:
            self.runner = runner
        elif self.backend == "sandbox":
            self.runner = self.sandbox_runner
        else:
            self.runner = self.k8s_runner
        self._external_client = http_client is not None
        self._client = http_client or httpx.AsyncClient(
            timeout=config.http_timeout_seconds,
        )
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._stop_event = asyncio.Event()
        self._last_heartbeat_sent: float = 0.0

    def _enforce_isolation_policy(self) -> None:
        """Refuse to start in a configuration that lacks an isolation boundary.

        In-pod execution needs a VM-isolated worker pod; Job execution needs a
        VM-isolated runtime class for the execution pods. Either requirement
        can only be waived by the documented, loudly logged Kata exception.
        """
        if self.backend == "sandbox" and not self.sandbox_allowed:
            raise SandboxIsolationError(
                "RUNS_RUNNER_BACKEND=sandbox refused: "
                f"{self.sandbox_reason}. Use the k8s_job backend, run the "
                "worker pod under a Kata runtime class, or record a Kata "
                "exception (RUNS_KATA_EXCEPTION_SANDBOX)"
            )
        if self.backend in ("sandbox", "auto") and self.sandbox_allowed:
            if "KATA EXCEPTION" in self.sandbox_reason:
                logger.error(
                    "runworker_kata_exception_active",
                    extra={
                        "scope": "in_pod_sandbox",
                        "detail": "tenant code runs in the worker pod WITHOUT "
                        "a VM isolation boundary (KATA EXCEPTION approved)",
                    },
                )
        if self.backend in ("k8s_job", "auto"):
            runtime_class = (self.config.runtime_class_name or "").strip()
            if not is_sandboxed_runtime_class(
                runtime_class, self.config.sandboxed_runtime_classes
            ):
                if (
                    self.config.kata_exception_jobs.strip().lower()
                    != KATA_EXCEPTION_APPROVED
                ):
                    logger.error(
                        "runworker_execution_runtime_class_refused",
                        extra={
                            "runtime_class": runtime_class or "<unset>",
                            "detail": "execution pod runtime class is not in "
                            "the sandboxed runtime class list; refusing to "
                            "run tenant code without a VM isolation boundary",
                        },
                    )
                    raise SandboxIsolationError(
                        "RUNS_RUNTIME_CLASS_NAME="
                        f"{runtime_class or '<unset>'!r} is not a sandboxed "
                        "runtime class (RUNS_SANDBOXED_RUNTIME_CLASSES): "
                        "execution pods must run under a Kata runtime class, "
                        "or record a Kata exception (RUNS_KATA_EXCEPTION_JOBS)"
                    )
                logger.error(
                    "runworker_kata_exception_active",
                    extra={
                        "scope": "execution_pods",
                        "runtime_class": runtime_class or "<unset>",
                        "detail": "execution pods run WITHOUT a VM isolation "
                        "boundary (KATA EXCEPTION approved)",
                    },
                )

    def select_runner(self, req: RunJobRequest) -> Any:
        """Select the runner backend for ``req``.

        ``auto`` only considers the in-pod sandbox when this worker pod has a
        confirmed isolation boundary; otherwise every execution gets its own
        Job pod.
        """
        if self._explicit_runner is not None:
            return self._explicit_runner
        if self.backend == "sandbox":
            if not self.sandbox_allowed:
                raise SandboxIsolationError(
                    f"In-pod sandbox refused: {self.sandbox_reason}"
                )
            return self.sandbox_runner
        if self.backend == "k8s_job":
            return self.k8s_runner
        if self.sandbox_allowed and self.sandbox_runner.can_execute(
            req.runtime,
            code_url=req.code_url,
            code=req.code,
        ):
            return self.sandbox_runner
        return self.k8s_runner

    def _auth_headers(self) -> dict[str, str]:
        """Build worker-token auth headers without ever logging token values."""
        token = self.config.elder_internal_token
        if not token:
            raise WorkerConfigError(
                f"{WORKER_TOKEN_ENV} is not configured; refusing to call the "
                "Elder API unauthenticated"
            )
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Elder-Internal-Token": token,
        }

    def touch_heartbeat(self) -> None:
        """Write current epoch timestamp to the file-based liveness probe path."""
        try:
            path = Path(self.config.health_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{int(time.time())}\n", encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "runworker_heartbeat_write_failed",
                extra={"path": self.config.health_file, "error": str(exc)},
            )

    async def send_heartbeat(self) -> dict[str, Any] | None:
        """Send a best-effort fleet heartbeat to hub-api; never raises."""
        self._last_heartbeat_sent = time.monotonic()
        url = f"{self.config.elder_api_url}/api/v1/admin/workers/heartbeat"
        body: dict[str, Any] = {
            "worker_id": self.config.worker_id,
            "worker_type": "runworker",
            "hostname": socket.gethostname(),
            "cluster": self.config.worker_cluster or "in-cluster",
            "capabilities": {
                "runner_backend": self.config.runner_backend,
                "max_concurrent": self.config.max_concurrent,
                "execution_namespace": self.config.execution_namespace,
                "runtime_class_name": self.config.runtime_class_name or None,
                "s3_fetch_mode": self.config.s3_fetch_mode,
            },
        }
        if self.config.worker_version:
            body["version"] = self.config.worker_version

        try:
            response = await self._client.post(
                url,
                json=body,
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {"ok": True}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "runworker_heartbeat_post_failed",
                extra={
                    "worker_id": self.config.worker_id,
                    "error": str(exc),
                },
            )
            return None

    async def poll_pending(self) -> list[dict[str, Any]]:
        """Claim pending executions from the Elder API."""
        url = f"{self.config.elder_api_url}/api/v1/runs/internal/pending"
        params: dict[str, str] = {"worker_id": self.config.worker_id}
        if self.config.worker_cluster:
            params["cluster"] = self.config.worker_cluster
            params["worker_cluster"] = self.config.worker_cluster

        response = await self._client.get(
            url,
            params=params,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        payload = response.json()

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("items", "executions", "jobs", "data"):
                val = payload.get(key)
                if isinstance(val, list):
                    return [item for item in val if isinstance(item, dict)]
        return []

    async def flush_logs(
        self,
        execution_id: str,
        logs: str,
    ) -> bool:
        """Push in-progress pod logs for a running execution to the Elder API."""
        url = (
            f"{self.config.elder_api_url}"
            f"/api/v1/runs/internal/executions/{execution_id}/logs"
        )
        body: dict[str, Any] = {
            "logs": logs,
            "worker_id": self.config.worker_id,
        }
        response = await self._client.post(
            url,
            json=body,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return True

    async def report_result(
        self,
        execution_id: str,
        result: RunExecutionResult,
    ) -> bool:
        """Report an execution outcome back to the Elder API."""
        url = (
            f"{self.config.elder_api_url}"
            f"/api/v1/runs/internal/executions/{execution_id}/result"
        )
        status_map = {
            "succeeded": "completed",
            "success": "completed",
            "timeout": "timed_out",
        }
        normalized_status = status_map.get(result.status, result.status)
        body: dict[str, Any] = {
            "status": normalized_status,
            "exit_code": result.exit_code,
            "stdout_log": result.logs,
            "logs": result.logs,
            "output": result.output,
            "duration_ms": result.duration_ms,
            "error_message": result.error,
            "error": result.error,
            "worker_id": self.config.worker_id,
        }
        response = await self._client.post(
            url,
            json=body,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return True

    async def _resolve_artifact(
        self,
        req: RunJobRequest,
        *,
        target_runner: Any | None = None,
    ) -> RunJobRequest:
        """Fetch and verify S3-backed code when ``code_url`` is supplied."""
        if not req.code_url:
            return req

        is_sandbox = isinstance(target_runner, SandboxRunner)
        if not is_sandbox and self.config.s3_fetch_mode == "init_container":
            return replace(req, use_init_container=True)

        response = await self._client.get(req.code_url)
        response.raise_for_status()
        raw_bytes = response.content
        if req.code_sha256:
            actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
            if actual_sha256 != req.code_sha256:
                raise ValueError(
                    f"Artifact SHA-256 mismatch for execution {req.execution_id}: "
                    f"expected {req.code_sha256}, got {actual_sha256}"
                )

        if not is_sandbox and len(raw_bytes) > CONFIGMAP_MAX_CODE_BYTES:
            return replace(req, use_init_container=True)

        if raw_bytes.startswith(b"\x00asm") or req.code_url.lower().split("?", 1)[
            0
        ].endswith(".wasm"):
            encoded = f"base64:{base64.b64encode(raw_bytes).decode('ascii')}"
            return replace(req, code=encoded, use_init_container=False)

        return replace(req, code=raw_bytes.decode("utf-8"), use_init_container=False)

    async def process_execution(self, item: dict[str, Any]) -> RunExecutionResult:
        """Execute a single claimed job under the concurrency semaphore and report."""
        req = _request_from_payload(item)
        async with self._semaphore:
            started = time.monotonic()
            loop = asyncio.get_running_loop()

            def _on_logs(logs_text: str) -> None:
                fut = asyncio.run_coroutine_threadsafe(
                    self.flush_logs(req.execution_id, logs_text),
                    loop,
                )
                try:
                    fut.result(timeout=min(10.0, self.config.http_timeout_seconds))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "runworker_live_log_flush_failed",
                        extra={
                            "execution_id": req.execution_id,
                            "error": str(exc),
                        },
                    )

            try:
                selected_runner = self.select_runner(req)
                backend_label = type(selected_runner).__name__
                try:
                    await self.flush_logs(
                        req.execution_id,
                        f"[elder] Worker {self.config.worker_id} starting {req.runtime} "
                        f"execution ({backend_label}) — warming up...\n",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "runworker_startup_log_flush_failed",
                        extra={
                            "execution_id": req.execution_id,
                            "error": str(exc),
                        },
                    )
                resolved_req = await self._resolve_artifact(
                    req,
                    target_runner=selected_runner,
                )
                result = await asyncio.to_thread(
                    selected_runner.execute,
                    resolved_req,
                    on_logs=_on_logs,
                    log_flush_interval=self.config.log_flush_interval_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "runworker_execution_error",
                    extra={"execution_id": req.execution_id, "runtime": req.runtime},
                )
                result = RunExecutionResult(
                    status="failed",
                    exit_code=None,
                    logs="",
                    output=None,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=str(exc),
                )

            try:
                await self.report_result(req.execution_id, result)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "runworker_report_failed",
                    extra={
                        "execution_id": req.execution_id,
                        "status": result.status,
                        "error": str(exc),
                    },
                )
            return result

    async def poll_once(self) -> int:
        """Run one poll-and-execute cycle; returns number of executions processed."""
        self.touch_heartbeat()
        items = await self.poll_pending()
        if not items:
            return 0

        await asyncio.gather(
            *(self.process_execution(item) for item in items),
            return_exceptions=False,
        )
        return len(items)

    def request_stop(self) -> None:
        """Signal the poll loop to exit cleanly after the current iteration."""
        self._stop_event.set()

    async def run_forever(self) -> None:
        """Run the async poll loop until signalled to stop."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except NotImplementedError:
                pass

        logger.info(
            "runworker_started",
            extra={
                "worker_id": self.config.worker_id,
                "api_url": self.config.elder_api_url,
                "execution_namespace": self.config.execution_namespace,
                "cluster": self.config.worker_cluster or "default",
                "runtime_class": self.config.runtime_class_name or "default",
                "runner_backend": self.config.runner_backend,
                "max_concurrent": self.config.max_concurrent,
                "token_configured": bool(
                    self.config.elder_internal_token or self.config.jwt_secret_key
                ),
            },
        )
        self.touch_heartbeat()
        await self.send_heartbeat()

        try:
            while not self._stop_event.is_set():
                if (
                    time.monotonic() - self._last_heartbeat_sent
                    >= self.config.heartbeat_interval_seconds
                ):
                    await self.send_heartbeat()
                try:
                    await self.poll_once()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "runworker_poll_cycle_failed",
                        extra={"error": str(exc)},
                    )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.config.poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            if not self._external_client:
                await self._client.aclose()
            logger.info("runworker_stopped", extra={"worker_id": self.config.worker_id})


def main() -> None:
    """Entrypoint for ``python3 -m apps.runworker.main``."""
    log_level = (os.getenv("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = RunWorkerConfig.from_env()
    # The token now lives only in this process's memory: drop it from the
    # environment so it is never inherited by anything this process spawns.
    os.environ.pop(WORKER_TOKEN_ENV, None)
    service = RunWorkerService(config)
    try:
        harden_worker_process()
    except SandboxIsolationError:
        if service.backend != "k8s_job":
            raise
        logger.warning("runworker_process_hardening_unavailable")
    asyncio.run(service.run_forever())


if __name__ == "__main__":
    main()
