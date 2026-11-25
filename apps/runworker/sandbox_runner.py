"""Warm Sandbox Pool Runner for Elder Runs inside ``svc-runworker`` pods.

Executes CodeRuns directly inside warm ``svc-runworker`` pods (designed for
gVisor / Kata shared worker nodes, eliminating per-execution K8s Job and etcd
churn):

- Creates an isolated temp workspace ``/tmp/elder-run-<exec_id>-<rand>``
  with ``0o700`` permissions containing the user script, ``input.json``, and
  the built-in zero-dependency ``elder`` helper SDK.
- Starts an ephemeral per-execution ``RestrictionGateway`` on ``127.0.0.1:0``
  whenever ``granted_scopes`` or ``allowed_egress_domains`` is non-empty so
  the real ``elder_api_token`` never enters the untrusted CodeRun process.
- Runs the script in a subprocess with a stripped environment, POSIX
  ``resource.setrlimit`` limits (CPU, memory/AS, open files, processes where
  supported), hard timeout process-group kill, and optional Wasm/WASI
  execution via ``wasmtime``.
- Streams incremental stdout/stderr to ``on_logs`` on ``log_flush_interval``.
- Cleans up the workspace and shuts down the gateway in ``finally``.

Isolation model: the subprocess shares the worker's UID, so rlimits and a
stripped environment are *not* a trust boundary. This backend is therefore
opt-in and only permitted when the pod itself runs under a VM-isolated
runtime class (see ``evaluate_sandbox_isolation``). As defence in depth the
worker process is made non-dumpable before any spawn so same-UID children are
denied ``/proc/<pid>`` introspection and ptrace of the worker.
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from apps.runworker.elder_sdk_bundle import write_sdk_files
from apps.runworker.gateway import DEFAULT_MAX_REQUESTS, RestrictionGateway
from apps.runworker.k8s_runner import (
    DEFAULT_LOG_FLUSH_INTERVAL_SECONDS,
    RunExecutionResult,
    RunJobRequest,
    clamp_memory_mb,
    clamp_timeout_seconds,
    extract_structured_output,
)
from apps.runworker.runtimes import RUNTIME_ALIASES

logger = logging.getLogger("apps.runworker.sandbox_runner")

MAX_LOG_BYTES = 2 * 1024 * 1024  # 2 MiB stdout+stderr capture ceiling
DEFAULT_NOFILE_LIMIT = 128
DEFAULT_NPROC_LIMIT = 64

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WASM_MAGIC = b"\x00asm"

_PROTECTED_SANDBOX_ENV_KEYS = frozenset(
    {
        "ELDER_RUN_INPUT",
        "ELDER_EXECUTION_ID",
        "ELDER_ARTIFACT_URL",
        "ELDER_ARTIFACT_SHA256",
        "ELDER_ARTIFACT_FILENAME",
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
        "PYTHONPATH",
        "NODE_PATH",
        "RUBYLIB",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
    }
)

_SANDBOX_RUNTIME_ALIASES: dict[str, str] = {
    **RUNTIME_ALIASES,
    "wasi": "wasm",
    "wasmtime": "wasm",
}

_RUNTIME_FILENAMES: dict[str, str] = {
    "python": "main.py",
    "nodejs": "main.js",
    "ruby": "main.rb",
    "golang": "main.go",
    "rust": "main.rs",
    "wasm": "main.wasm",
}


KATA_EXCEPTION_APPROVED = "approved"
DEFAULT_SANDBOXED_RUNTIME_CLASS_PREFIXES: tuple[str, ...] = ("kata",)

_PR_GET_DUMPABLE = 3
_PR_SET_DUMPABLE = 4


def _validate_argv(cmd: Any) -> None:
    """Reject anything other than a non-empty list of NUL-free strings.

    Defence in depth before ``Popen``: guarantees an argv vector is passed
    (never a shell string) and that argv[0] is an absolute executable path
    or the fixed ``sh`` used for the rust compile-then-exec wrapper.
    """
    if not isinstance(cmd, list) or not cmd:
        raise ValueError("sandbox command must be a non-empty argv list")
    for arg in cmd:
        if not isinstance(arg, str) or "\x00" in arg:
            raise ValueError("sandbox argv elements must be NUL-free strings")
    if cmd[0] != "sh" and not os.path.isabs(cmd[0]):
        raise ValueError("sandbox executable must be an absolute path")


class SandboxIsolationError(RuntimeError):
    """Raised when the in-pod sandbox is requested without an isolation boundary."""


def is_sandboxed_runtime_class(
    runtime_class: str,
    sandboxed_runtime_classes: tuple[str, ...] = (),
) -> bool:
    """Return whether ``runtime_class`` names a VM-isolated runtime class.

    Single membership test shared by the worker-pod check and the
    execution-pod check. The allowlist comes from the chart value
    ``svc-runworker.sandboxedRuntimeClasses``; a merely non-empty name is
    never enough, so ``runc`` or a misspelt class cannot pass as isolated.
    Without an allowlist only ``kata*`` class names are accepted.
    """
    name = (runtime_class or "").strip().lower()
    if not name:
        return False
    allowlist = tuple(c.strip().lower() for c in sandboxed_runtime_classes if c.strip())
    if allowlist:
        return name in allowlist
    return name.startswith(DEFAULT_SANDBOXED_RUNTIME_CLASS_PREFIXES)


def evaluate_sandbox_isolation(
    *,
    pod_runtime_class: str,
    sandboxed_runtime_classes: tuple[str, ...] = (),
    kata_exception: str = "",
) -> tuple[bool, str]:
    """Decide whether in-pod execution is permitted for this worker pod.

    Returns ``(allowed, reason)``. In-pod execution is allowed only when the
    pod's runtime class is a VM-isolated one (an explicit allowlist entry, or
    a ``kata*`` class by default), or when the operator set the documented
    Kata exception. Anything else is refused so tenant code never shares a
    kernel-level trust boundary with the worker by accident.
    """
    runtime_class = (pod_runtime_class or "").strip().lower()
    if is_sandboxed_runtime_class(runtime_class, sandboxed_runtime_classes):
        return True, f"pod runtime class {runtime_class!r} is sandboxed"
    if (kata_exception or "").strip().lower() == KATA_EXCEPTION_APPROVED:
        return True, "KATA EXCEPTION (approved) override is set"
    return (
        False,
        "worker pod is not running under a sandboxed runtime class "
        f"(RUNS_POD_RUNTIME_CLASS={runtime_class or '<unset>'!r})",
    )


def _prctl(option: int, arg: int = 0) -> int:
    """Call ``prctl(2)`` through libc, raising ``OSError`` on failure."""
    libc = ctypes.CDLL(None, use_errno=True)
    result = int(libc.prctl(option, arg, 0, 0, 0))
    if result < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"prctl({option}) failed: {os.strerror(errno)}")
    return result


def harden_worker_process() -> None:
    """Mark the worker process non-dumpable so children cannot introspect it.

    A non-dumpable process denies same-UID readers access to its
    ``/proc/<pid>/environ`` and memory and cannot be ptrace-attached. Raises
    ``SandboxIsolationError`` when the kernel does not support it, because
    in-pod execution must not proceed without this guard.
    """
    if not sys.platform.startswith("linux"):
        raise SandboxIsolationError(
            "In-pod sandbox requires Linux prctl(PR_SET_DUMPABLE) support"
        )
    try:
        if _prctl(_PR_GET_DUMPABLE) != 0:
            _prctl(_PR_SET_DUMPABLE, 0)
        dumpable = _prctl(_PR_GET_DUMPABLE)
    except (OSError, AttributeError) as exc:
        raise SandboxIsolationError(
            f"Could not mark the worker process non-dumpable: {exc}"
        ) from exc
    if dumpable != 0:
        raise SandboxIsolationError("Worker process is still dumpable after prctl")


def normalize_sandbox_runtime(runtime: str) -> str:
    """Resolve a runtime name or alias to its canonical sandbox runtime key."""
    normalized = (runtime or "").strip().lower()
    return _SANDBOX_RUNTIME_ALIASES.get(normalized, normalized)


def _is_wasm_request(
    runtime: str,
    *,
    code_url: str | None = None,
    code: str | bytes = "",
) -> bool:
    """Return True when the request targets Wasm/WASI execution."""
    if normalize_sandbox_runtime(runtime) == "wasm":
        return True
    if code_url and code_url.lower().split("?", 1)[0].endswith(".wasm"):
        return True
    if isinstance(code, (bytes, bytearray)) and bytes(code).startswith(_WASM_MAGIC):
        return True
    return False


def resolve_local_runtime_binary(runtime: str) -> str | None:
    """Return the local executable path for ``runtime``, or ``None`` if unavailable."""
    canonical = normalize_sandbox_runtime(runtime)
    if canonical == "python":
        return sys.executable or shutil.which("python3")
    if canonical == "nodejs":
        return shutil.which("node")
    if canonical == "ruby":
        return shutil.which("ruby")
    if canonical == "golang":
        return shutil.which("go")
    if canonical == "rust":
        return shutil.which("rustc")
    if canonical == "wasm":
        return shutil.which("wasmtime")
    return None


def _decode_wasm_bytes(code: str | bytes) -> bytes:
    """Decode raw or base64-encoded WebAssembly binary bytes."""
    if isinstance(code, (bytes, bytearray)):
        return bytes(code)
    text = (code or "").strip()
    if text.startswith("base64:"):
        return base64.b64decode(text[7:])
    try:
        decoded = base64.b64decode(text, validate=True)
        if decoded.startswith(_WASM_MAGIC):
            return decoded
    except Exception:  # noqa: BLE001
        pass
    return text.encode("latin-1", errors="ignore")


def _build_rlimit_preexec(
    *,
    timeout_seconds: int,
    memory_mb: int,
    nofile_limit: int = DEFAULT_NOFILE_LIMIT,
    nproc_limit: int = DEFAULT_NPROC_LIMIT,
) -> Callable[[], None]:
    """Build a POSIX ``preexec_fn`` that applies strict ``resource.setrlimit`` caps."""
    mem_bytes = clamp_memory_mb(memory_mb) * 1024 * 1024
    cpu_soft = max(1, int(timeout_seconds) + 1)
    cpu_hard = cpu_soft + 1

    def _apply_limits() -> None:
        def _safe_set(res_id: int, soft: int, hard: int) -> None:
            try:
                cur_soft, cur_hard = resource.getrlimit(res_id)
                if cur_hard != resource.RLIM_INFINITY:
                    hard = min(hard, cur_hard)
                    soft = min(soft, hard)
                if cur_soft != resource.RLIM_INFINITY:
                    soft = (
                        min(soft, cur_soft)
                        if res_id == resource.RLIMIT_NOFILE
                        else soft
                    )
                resource.setrlimit(res_id, (soft, hard))
            except (OSError, ValueError):
                pass

        _safe_set(resource.RLIMIT_CPU, cpu_soft, cpu_hard)
        _safe_set(resource.RLIMIT_AS, mem_bytes, mem_bytes)
        _safe_set(resource.RLIMIT_NOFILE, nofile_limit, nofile_limit)
        if hasattr(resource, "RLIMIT_NPROC"):
            try:
                cur_soft, cur_hard = resource.getrlimit(resource.RLIMIT_NPROC)
                # Only apply RLIMIT_NPROC if we are not lowering below an
                # already-tight limit; on Linux RLIMIT_NPROC is per-UID across
                # all host processes, so apply best-effort without breaking
                # subprocess startup when many sibling processes share the UID.
                target_nproc = max(nproc_limit, 64)
                if cur_hard == resource.RLIM_INFINITY or target_nproc <= cur_hard:
                    if cur_soft == resource.RLIM_INFINITY or target_nproc <= cur_soft:
                        resource.setrlimit(
                            resource.RLIMIT_NPROC,
                            (target_nproc, target_nproc),
                        )
            except (OSError, ValueError):
                pass

    return _apply_limits


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Terminate an entire subprocess group safely."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


class SandboxRunner:
    """Executes Elder Runs functions directly in warm worker sandboxes."""

    def __init__(
        self,
        *,
        elder_api_url: str = "http://hub-api:5000",
        max_gateway_requests: int = DEFAULT_MAX_REQUESTS,
        poll_interval: float = 0.05,
        workspace_root: str | None = None,
        gateway_http_client: httpx.Client | None = None,
        dns_resolver: Callable[[str, int], list[str]] | None = None,
    ) -> None:
        self.elder_api_url = (elder_api_url or "http://hub-api:5000").rstrip("/")
        self.max_gateway_requests = max(1, int(max_gateway_requests))
        self.poll_interval = max(0.01, float(poll_interval))
        self.workspace_root = workspace_root
        self._gateway_http_client = gateway_http_client
        self._dns_resolver = dns_resolver

    def can_execute(
        self,
        runtime: str,
        *,
        code_url: str | None = None,
        code: str | bytes = "",
    ) -> bool:
        """Return True if this warm worker pod has the binary needed for ``runtime``."""
        if _is_wasm_request(runtime, code_url=code_url, code=code):
            return resolve_local_runtime_binary("wasm") is not None
        return resolve_local_runtime_binary(runtime) is not None

    def _build_env(
        self,
        request: RunJobRequest,
        *,
        canonical_runtime: str,
        binary_path: str,
        workspace: Path,
        tmp_dir: Path,
        input_json: str,
        gateway: RestrictionGateway | None,
    ) -> dict[str, str]:
        bin_dir = str(Path(binary_path).parent)
        safe_path = f"{bin_dir}:/usr/local/bin:/usr/bin:/bin"
        env: dict[str, str] = {
            "PATH": safe_path,
            "HOME": str(tmp_dir),
            "TMPDIR": str(tmp_dir),
            "ELDER_RUN_INPUT": input_json,
            "ELDER_EXECUTION_ID": request.execution_id,
        }

        if canonical_runtime == "python":
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONPATH"] = str(workspace)
        elif canonical_runtime == "nodejs":
            env["NODE_OPTIONS"] = "--no-warnings"
            env["NODE_PATH"] = str(workspace)
        elif canonical_runtime == "ruby":
            env["RUBYLIB"] = str(workspace)
        elif canonical_runtime == "golang":
            env["GOCACHE"] = str(tmp_dir / "go-build")
            env["GOPATH"] = str(tmp_dir / "go")
            env["GOTMPDIR"] = str(tmp_dir)
        elif canonical_runtime == "rust":
            env["CARGO_HOME"] = str(tmp_dir / "cargo")

        if gateway is not None:
            env["ELDER_GATEWAY_URL"] = gateway.url
            env["ELDER_GATEWAY_TOKEN"] = gateway.gateway_token

        for key, val in (request.env or {}).items():
            if (
                key in _PROTECTED_SANDBOX_ENV_KEYS
                or key in env
                or not _ENV_KEY_RE.match(key)
            ):
                continue
            env[key] = str(val)

        return env

    _PYTHON_BOOTSTRAP = """import asyncio, inspect, json, os, runpy, sys

script_path = sys.argv[1]
ns = runpy.run_path(script_path, run_name="__main__")
fn = ns.get("handler")
if callable(fn):
    raw_input = os.environ.get("ELDER_RUN_INPUT", "{}")
    try:
        input_data = json.loads(raw_input) if raw_input else {}
    except Exception:
        input_data = {}
    sig = inspect.signature(fn)
    params = [
        p
        for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    res = fn() if len(params) == 0 else fn(input_data)
    if inspect.isawaitable(res):
        res = asyncio.run(res)
    if res is not None:
        print(json.dumps(res, default=str), flush=True)
"""

    _NODE_BOOTSTRAP = """import { pathToFileURL } from "node:url";

const scriptPath = process.argv[2];
const mod = await import(pathToFileURL(scriptPath).href);
const fn =
  typeof mod.handler === "function"
    ? mod.handler
    : typeof mod.default === "function"
      ? mod.default
      : null;
if (fn) {
  let inputData = {};
  try {
    inputData = process.env.ELDER_RUN_INPUT
      ? JSON.parse(process.env.ELDER_RUN_INPUT)
      : {};
  } catch {
    inputData = {};
  }
  const res = await fn(inputData);
  if (res !== undefined) {
    console.log(JSON.stringify(res));
  }
}
"""

    _RUBY_BOOTSTRAP = """require "json"

script_path = ARGV[0]
load script_path
if defined?(handler) == "method"
  raw = ENV.fetch("ELDER_RUN_INPUT", "{}")
  input_data = begin
    raw.empty? ? {} : JSON.parse(raw)
  rescue StandardError
    {}
  end
  m = method(:handler)
  res = m.arity == 0 ? handler : handler(input_data)
  puts JSON.generate(res) unless res.nil?
end
"""

    def _build_command(
        self,
        *,
        canonical_runtime: str,
        binary_path: str,
        script_path: Path,
        workspace: Path,
        tmp_dir: Path,
        env: dict[str, str],
    ) -> list[str]:
        if canonical_runtime == "python":
            bootstrap = tmp_dir / "_elder_bootstrap.py"
            bootstrap.write_text(self._PYTHON_BOOTSTRAP, encoding="utf-8")
            return [binary_path, "-u", "-B", str(bootstrap), str(script_path)]
        if canonical_runtime == "nodejs":
            bootstrap = tmp_dir / "_elder_bootstrap.mjs"
            bootstrap.write_text(self._NODE_BOOTSTRAP, encoding="utf-8")
            return [binary_path, str(bootstrap), str(script_path)]
        if canonical_runtime == "ruby":
            bootstrap = tmp_dir / "_elder_bootstrap.rb"
            bootstrap.write_text(self._RUBY_BOOTSTRAP, encoding="utf-8")
            return [binary_path, "-I", str(workspace), str(bootstrap), str(script_path)]
        if canonical_runtime == "golang":
            return [binary_path, "run", str(script_path)]
        if canonical_runtime == "rust":
            out_bin = tmp_dir / "elder_run_bin"
            # Paths are passed as positional parameters ($1..$3), never
            # interpolated into the shell program text.
            return [
                "sh",
                "-c",
                '"$1" "$2" -o "$3" && exec "$3"',
                "sh",
                binary_path,
                str(script_path),
                str(out_bin),
            ]
        if canonical_runtime == "wasm":
            cmd = [
                binary_path,
                "run",
                "--dir",
                str(workspace),
                "--env",
                f"ELDER_RUN_INPUT={env['ELDER_RUN_INPUT']}",
                "--env",
                f"ELDER_EXECUTION_ID={env['ELDER_EXECUTION_ID']}",
            ]
            if "ELDER_GATEWAY_URL" in env:
                cmd.extend(["--env", f"ELDER_GATEWAY_URL={env['ELDER_GATEWAY_URL']}"])
            if "ELDER_GATEWAY_TOKEN" in env:
                cmd.extend(
                    ["--env", f"ELDER_GATEWAY_TOKEN={env['ELDER_GATEWAY_TOKEN']}"]
                )
            cmd.append(str(script_path))
            return cmd
        raise ValueError(f"Unsupported sandbox runtime: {canonical_runtime!r}")

    def execute(
        self,
        request: RunJobRequest,
        *,
        on_logs: Callable[[str], None] | None = None,
        log_flush_interval: float = DEFAULT_LOG_FLUSH_INTERVAL_SECONDS,
    ) -> RunExecutionResult:
        """Execute ``request`` inside a warm isolated temp workspace subprocess."""
        started = time.monotonic()
        is_wasm = _is_wasm_request(
            request.runtime,
            code_url=request.code_url,
            code=request.code,
        )
        canonical = "wasm" if is_wasm else normalize_sandbox_runtime(request.runtime)
        binary_path = resolve_local_runtime_binary(canonical)
        if not binary_path:
            raise RuntimeError(
                f"Local runtime binary for {canonical!r} is not installed in this worker"
            )

        safe_exec = (
            _SAFE_ID_RE.sub("-", (request.execution_id or "run"))[:32].strip("-")
            or "run"
        )
        workspace = Path(
            tempfile.mkdtemp(
                prefix=f"elder-run-{safe_exec}-",
                dir=self.workspace_root,
            )
        )
        gateway: RestrictionGateway | None = None

        try:
            os.chmod(workspace, 0o700)
            tmp_dir = workspace / "tmp"
            tmp_dir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(tmp_dir, 0o700)

            filename = _RUNTIME_FILENAMES.get(canonical, "main.txt")
            script_path = workspace / filename
            if canonical == "wasm":
                script_path.write_bytes(_decode_wasm_bytes(request.code))
            else:
                script_text = (
                    request.code.decode("utf-8", errors="replace")
                    if isinstance(request.code, (bytes, bytearray))
                    else str(request.code)
                )
                script_path.write_text(script_text, encoding="utf-8")

            input_json = json.dumps(
                request.input_data if request.input_data is not None else {},
                separators=(",", ":"),
            )
            (workspace / "input.json").write_text(input_json, encoding="utf-8")

            # Mount built-in zero-dependency Elder helper SDK automatically.
            write_sdk_files(workspace, canonical)

            needs_gateway = bool(
                request.granted_scopes or request.allowed_egress_domains
            )
            if needs_gateway:
                gateway = RestrictionGateway(
                    elder_api_url=self.elder_api_url,
                    elder_api_token=request.elder_api_token,
                    granted_scopes=request.granted_scopes,
                    allowed_egress_domains=request.allowed_egress_domains,
                    execution_id=request.execution_id,
                    tenant_id=request.tenant_id or "",
                    max_requests=self.max_gateway_requests,
                    http_client=self._gateway_http_client,
                    dns_resolver=self._dns_resolver,
                ).start()

            env = self._build_env(
                request,
                canonical_runtime=canonical,
                binary_path=binary_path,
                workspace=workspace,
                tmp_dir=tmp_dir,
                input_json=input_json,
                gateway=gateway,
            )
            cmd = self._build_command(
                canonical_runtime=canonical,
                binary_path=binary_path,
                script_path=script_path,
                workspace=workspace,
                tmp_dir=tmp_dir,
                env=env,
            )

            timeout = clamp_timeout_seconds(request.timeout_seconds)
            memory_mb = clamp_memory_mb(request.memory_mb)
            preexec = _build_rlimit_preexec(
                timeout_seconds=timeout,
                memory_mb=memory_mb,
            )

            # Must hold before every spawn: refuses to run rather than start
            # tenant code that could introspect this process.
            harden_worker_process()

            _validate_argv(cmd)
            # nosemgrep: python.django.security.injection.command.subprocess-injection.subprocess-injection -- argv list (no shell=True), validated by _validate_argv; executable from resolve_local_runtime_binary()
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                cwd=str(workspace),
                env=env,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=preexec,
            )

            log_chunks: list[bytes] = []
            log_bytes = 0
            log_lock = threading.Lock()

            def _reader() -> None:
                nonlocal log_bytes
                assert proc.stdout is not None
                try:
                    while True:
                        chunk = proc.stdout.read1(4096)  # type: ignore[attr-defined]
                        if not chunk:
                            break
                        with log_lock:
                            remaining = MAX_LOG_BYTES - log_bytes
                            if remaining > 0:
                                piece = chunk[:remaining]
                                log_chunks.append(piece)
                                log_bytes += len(piece)
                except OSError:
                    pass
                finally:
                    try:
                        proc.stdout.close()
                    except OSError:
                        pass

            reader_thread = threading.Thread(
                target=_reader,
                name=f"elder-log-{safe_exec[:12]}",
                daemon=True,
            )
            reader_thread.start()

            deadline = started + timeout
            flush_interval = max(0.01, float(log_flush_interval))
            last_log_flush = started
            last_flushed_logs = ""
            timed_out = False
            error_msg: str | None = None

            while True:
                rc = proc.poll()
                now = time.monotonic()

                if rc is not None:
                    break

                if now >= deadline:
                    timed_out = True
                    error_msg = f"Execution exceeded timeout ({timeout}s)"
                    _kill_process_group(proc)
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        pass
                    break

                if on_logs is not None and (now - last_log_flush) >= flush_interval:
                    last_log_flush = now
                    with log_lock:
                        current_logs = b"".join(log_chunks).decode(
                            "utf-8", errors="replace"
                        )
                    if current_logs and current_logs != last_flushed_logs:
                        last_flushed_logs = current_logs
                        try:
                            on_logs(current_logs)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "runworker_sandbox_log_flush_callback_failed",
                                extra={
                                    "execution_id": request.execution_id,
                                    "error": str(exc),
                                },
                            )

                time.sleep(self.poll_interval)

            reader_thread.join(timeout=2.0)
            with log_lock:
                logs = b"".join(log_chunks).decode("utf-8", errors="replace")

            exit_code = proc.returncode
            if timed_out:
                status = "timeout"
            elif exit_code == 0:
                status = "succeeded"
            else:
                status = "failed"

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
            if gateway is not None:
                try:
                    gateway.stop()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "runworker_gateway_stop_failed",
                        extra={
                            "execution_id": request.execution_id,
                            "error": str(exc),
                        },
                    )
            shutil.rmtree(workspace, ignore_errors=True)
