"""Elder Runs worker poll loop (svc-runworker).

Completely decoupled from Elder's database and Redis so it can run either
inside Elder's own Kubernetes cluster or in a remote Kubernetes cluster.
Polls Elder's internal runs API for pending executions, launches hardened
per-execution Kubernetes Jobs via ``K8sRunner``, and posts results back.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
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
    K8sRunner,
    RunExecutionResult,
    RunJobRequest,
    clamp_log_flush_interval,
)

logger = logging.getLogger("apps.runworker")


def _default_health_file() -> str:
    return os.path.join(tempfile.gettempdir(), "runworker.healthy")


def _b64url(data: bytes) -> str:
    """Return unpadded base64url-encoded ASCII text."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _mint_service_worker_jwt(
    secret: str,
    *,
    issuer: str = "elder-api",
    audience: str = "elder-api",
    ttl_seconds: int = 300,
) -> str:
    """Mint a short-lived HS256 ``service_worker`` JWT for ``svc-runworker``.

    Uses only the Python standard library so ``svc-runworker`` needs no extra
    JWT runtime dependency while producing tokens verified by
    ``apps.api.auth.jwt_handler.verify_token``.
    """
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": "svc-runworker",
        "type": "service_worker",
        "tenant": "system",
        "scope": "runs:read runs:write runs:invoke",
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    signing_input = (
        f"{_b64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))}."
        f"{_b64url(json.dumps(payload, separators=(',', ':')).encode('utf-8'))}"
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url(signature)}"


@dataclass(frozen=True)
class RunWorkerConfig:
    """Environment-driven configuration for ``svc-runworker``."""

    elder_api_url: str = "http://hub-api:5000"
    elder_internal_token: str = field(default="", repr=False)
    jwt_secret_key: str = field(default="", repr=False)
    jwt_issuer: str = "elder-api"
    jwt_audience: str = "elder-api"
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

    @classmethod
    def from_env(cls) -> RunWorkerConfig:
        """Load worker configuration from process environment variables."""
        api_url = (os.getenv("ELDER_API_URL") or "http://hub-api:5000").rstrip("/")
        token = os.getenv("ELDER_INTERNAL_TOKEN") or ""
        jwt_secret = os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY") or ""
        jwt_issuer = os.getenv("JWT_ISSUER") or "elder-api"
        jwt_audience = os.getenv("JWT_AUDIENCE") or "elder-api"
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
        return cls(
            elder_api_url=api_url,
            elder_internal_token=token,
            jwt_secret_key=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
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
        )


CONFIGMAP_MAX_CODE_BYTES = 900 * 1024  # Safe margin below K8s 1 MiB etcd limit


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
    )


class RunWorkerService:
    """Async polling service that runs claimed Elder Runs executions in K8s."""

    def __init__(
        self,
        config: RunWorkerConfig,
        *,
        runner: K8sRunner | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.runner = runner or K8sRunner(
            namespace=config.execution_namespace,
            runtime_class_name=config.runtime_class_name or None,
        )
        self._external_client = http_client is not None
        self._client = http_client or httpx.AsyncClient(
            timeout=config.http_timeout_seconds,
        )
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._stop_event = asyncio.Event()

    def _auth_headers(self) -> dict[str, str]:
        """Build internal auth headers without ever logging token values."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.config.elder_internal_token:
            headers["Authorization"] = f"Bearer {self.config.elder_internal_token}"
            headers["X-Elder-Internal-Token"] = self.config.elder_internal_token
        elif self.config.jwt_secret_key:
            token = _mint_service_worker_jwt(
                self.config.jwt_secret_key,
                issuer=self.config.jwt_issuer,
                audience=self.config.jwt_audience,
            )
            headers["Authorization"] = f"Bearer {token}"
        return headers

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
        body: dict[str, Any] = {
            "status": result.status,
            "exit_code": result.exit_code,
            "logs": result.logs,
            "output": result.output,
            "duration_ms": result.duration_ms,
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

    async def _resolve_artifact(self, req: RunJobRequest) -> RunJobRequest:
        """Fetch and verify S3-backed code when ``code_url`` is supplied."""
        if not req.code_url:
            return req

        if self.config.s3_fetch_mode == "init_container":
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

        if len(raw_bytes) > CONFIGMAP_MAX_CODE_BYTES:
            return replace(req, use_init_container=True)

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
                resolved_req = await self._resolve_artifact(req)
                result = await asyncio.to_thread(
                    self.runner.execute,
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
                "max_concurrent": self.config.max_concurrent,
                "token_configured": bool(
                    self.config.elder_internal_token or self.config.jwt_secret_key
                ),
            },
        )
        self.touch_heartbeat()

        try:
            while not self._stop_event.is_set():
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
    service = RunWorkerService(config)
    asyncio.run(service.run_forever())


if __name__ == "__main__":
    main()
