"""Hardened command execution for the Flows invoker.

Defense-in-depth for running user-defined stage commands, using only the
standard library (no external sandbox runtime — keeps deployment simple). Each
layer is independent:

- **No shell**: commands run as an argv list (``shlex.split``); shell
  metacharacters (``;``, ``|``, ``$()`` …) are literal, so there is no
  injection surface.
- **Binary allowlist**: argv[0] must be a known CI tool OR a script that
  resolves *inside* the workspace — a config value can't run ``/bin/rm``.
- **POSIX rlimits** (``preexec_fn``): CPU, address space, process count
  (fork-bomb guard), open files, output file size, no core dumps.
- **Process-group kill on timeout**: ``start_new_session`` + ``killpg`` so a
  runaway and all its children die together.
- **Minimal env**: a curated allowlist — ``DATABASE_URL``, secrets and
  ``LD_PRELOAD``/``LD_LIBRARY_PATH`` never reach the child.
- **Output cap**: stdout is truncated to bound memory/DB/log growth.

This runs inside an already-isolated, non-root, read-only-rootfs container
(see the K8s manifests), so these process-level controls compose with
container-level and network-level controls.
"""

from __future__ import annotations

import os
import resource
import shlex
import signal
import subprocess  # nosec B404 - the whole point of this module: hardened exec
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Default CI toolchain binaries permitted as argv[0]. Override with the
# FLOWS_ALLOWED_BINARIES env var (comma-separated) for a stricter or broader
# set without a code change.
DEFAULT_ALLOWED_BINARIES = frozenset(
    {
        "bash",
        "sh",
        "make",
        "python",
        "python3",
        "pytest",
        "tox",
        "poetry",
        "pip",
        "node",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "jest",
        "go",
        "cargo",
        "rustc",
        "mvn",
        "gradle",
        "dotnet",
        "rake",
        "rspec",
        "phpunit",
        "composer",
    }
)

# Env keys that must never be forwarded to a child (loader hijacking / shell
# init injection), even if a stage supplies them.
_BLOCKED_ENV_PREFIXES = ("LD_", "DYLD_")
_BLOCKED_ENV_KEYS = frozenset({"BASH_ENV", "ENV", "IFS", "PATH"})

# Bytes of stdout retained (rest truncated).
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024


class SandboxError(Exception):
    """Raised when a command is rejected before execution (policy violation)."""


@dataclass(slots=True)
class ResourceLimits:
    """POSIX resource limits applied to the child via setrlimit."""

    cpu_seconds: int = 900
    address_space_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB
    # NOTE: RLIMIT_NPROC counts processes per real UID KERNEL-WIDE — on a
    # shared node every container running as uid 1000 counts against it. The
    # default must sit well above ambient node usage while still stopping a
    # fork bomb (which spawns tens of thousands).
    max_processes: int = 4096
    open_files: int = 1024
    file_size_bytes: int = 512 * 1024 * 1024  # 512 MiB

    @classmethod
    def from_env(cls) -> ResourceLimits:
        """Build limits, allowing env overrides for ops tuning."""

        def _int(name: str, default: int) -> int:
            raw = os.environ.get(name)
            try:
                return int(raw) if raw else default
            except ValueError:
                return default

        return cls(
            cpu_seconds=_int("FLOWS_LIMIT_CPU_SECONDS", 900),
            address_space_bytes=_int("FLOWS_LIMIT_AS_BYTES", 2 * 1024 * 1024 * 1024),
            max_processes=_int("FLOWS_LIMIT_NPROC", 4096),
            open_files=_int("FLOWS_LIMIT_NOFILE", 1024),
            file_size_bytes=_int("FLOWS_LIMIT_FSIZE_BYTES", 512 * 1024 * 1024),
        )


@dataclass(slots=True)
class CommandResult:
    """Outcome of a sandboxed command."""

    returncode: int
    stdout: str
    timed_out: bool
    duration_seconds: float
    truncated: bool
    argv: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def allowed_binaries() -> frozenset:
    """Resolve the binary allowlist (env override or the default set)."""
    raw = os.environ.get("FLOWS_ALLOWED_BINARIES")
    if raw:
        return frozenset(b.strip() for b in raw.split(",") if b.strip())
    return DEFAULT_ALLOWED_BINARIES


def parse_command(command: str) -> list[str]:
    """Split a command string into an argv list (no shell interpretation)."""
    if not command or not command.strip():
        raise SandboxError("empty command")
    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise SandboxError(f"unparseable command: {e}")
    if not argv:
        raise SandboxError("empty command")
    return argv


def resolve_executable(
    argv0: str, workspace: str, extra_allowed: frozenset | None = None
) -> None:
    """Validate argv[0] against the allowlist / workspace containment.

    Raises SandboxError if argv[0] is neither an allowlisted binary name nor a
    script path that stays inside ``workspace``. ``extra_allowed`` lets trusted
    internal callers (e.g. git operations) permit a binary that is deliberately
    NOT in the user-facing allowlist.
    """
    if argv0.startswith("-"):
        raise SandboxError(f"argument-injection: argv[0] {argv0!r} starts with '-'")

    if "/" in argv0:
        # Treat as a path — must resolve inside the workspace (no escape).
        ws_real = os.path.realpath(workspace)
        target = os.path.realpath(os.path.join(workspace, argv0))
        if target != ws_real and not target.startswith(ws_real + os.sep):
            raise SandboxError(f"path escapes workspace: {argv0!r} -> {target!r}")
        return

    permitted = allowed_binaries()
    if extra_allowed:
        permitted = permitted | extra_allowed
    if argv0 not in permitted:
        raise SandboxError(
            f"binary not allowlisted: {argv0!r} (set FLOWS_ALLOWED_BINARIES to permit)"
        )


def build_env(workspace: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a minimal, curated environment (parent env is NOT inherited)."""
    env: dict[str, str] = {
        "PATH": os.environ.get(
            "PATH", "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "HOME": workspace,
        "TMPDIR": workspace,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "CI": "true",
        "GIT_TERMINAL_PROMPT": "0",
    }
    for key, value in (extra or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key in _BLOCKED_ENV_KEYS or key.startswith(_BLOCKED_ENV_PREFIXES):
            continue
        env[key] = value
    return env


def _limit_preexec(limits: ResourceLimits):
    """Return a preexec_fn that applies rlimits in the child before exec."""

    def _apply() -> None:  # pragma: no cover - runs in forked child
        resource.setrlimit(
            resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds)
        )
        if limits.address_space_bytes:
            resource.setrlimit(
                resource.RLIMIT_AS,
                (limits.address_space_bytes, limits.address_space_bytes),
            )
        resource.setrlimit(
            resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes)
        )
        resource.setrlimit(
            resource.RLIMIT_NOFILE, (limits.open_files, limits.open_files)
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (limits.file_size_bytes, limits.file_size_bytes),
        )
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return _apply


def run_command(
    argv: list[str],
    workspace: str,
    *,
    timeout: int = 600,
    env: dict[str, str] | None = None,
    limits: ResourceLimits | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    extra_allowed: frozenset | None = None,
) -> CommandResult:
    """Run ``argv`` inside ``workspace`` under the full sandbox policy.

    Validates the binary, applies rlimits + a minimal env, enforces a wall-clock
    timeout with process-group kill, and returns captured (truncated) output.
    Raises SandboxError on a policy rejection (before any process starts).
    """
    if not argv or not all(isinstance(a, str) for a in argv):
        raise SandboxError("argv must be a non-empty list of strings")
    if not os.path.isdir(workspace):
        raise SandboxError(f"workspace does not exist: {workspace!r}")

    resolve_executable(argv[0], workspace, extra_allowed)
    limits = limits or ResourceLimits.from_env()
    child_env = env if env is not None else build_env(workspace)

    started = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603  # nosec B603 - argv list, no shell, allowlist-validated
        argv,
        cwd=workspace,
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=_limit_preexec(limits),
        text=True,
        errors="replace",
    )
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, _ = proc.communicate()
    duration = time.monotonic() - started

    out = out or ""
    truncated = False
    if len(out.encode("utf-8", "replace")) > max_output_bytes:
        out = out.encode("utf-8", "replace")[:max_output_bytes].decode(
            "utf-8", "replace"
        )
        out += "\n...[output truncated]..."
        truncated = True

    return CommandResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=out,
        timed_out=timed_out,
        duration_seconds=round(duration, 3),
        truncated=truncated,
        argv=list(argv),
    )
