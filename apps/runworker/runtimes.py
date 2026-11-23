"""Pinned container runtime definitions for Elder Runs function execution.

Each supported language maps to a rootless-compatible official base image
pinned to an immutable multi-arch SHA256 digest and a command that executes
the mounted user script at ``/workspace/<filename>`` with a read-only root
filesystem and writable ``/tmp``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeSpec:
    """Immutable container runtime specification for a single language."""

    name: str
    image: str
    filename: str
    command: tuple[str, ...]
    extra_env: tuple[tuple[str, str], ...] = ()


# Isolated per-pod emptyDir mount path inside ephemeral function containers.
CONTAINER_TMP_DIR = "/tmp"  # nosec B108


# All images are pinned to immutable multi-arch manifest list SHA256 digests.
RUNTIMES: dict[str, RuntimeSpec] = {
    "nodejs": RuntimeSpec(
        name="nodejs",
        image="node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee",
        filename="main.js",
        command=("node", "/workspace/main.js"),
        extra_env=(
            ("HOME", CONTAINER_TMP_DIR),
            ("NODE_OPTIONS", "--no-warnings"),
        ),
    ),
    "rust": RuntimeSpec(
        name="rust",
        image="rust:1-slim-bookworm@sha256:7c7d4eb4d05b7781187a327809466cfddf29753f6defdc3d19a0e291951b7a37",
        filename="main.rs",
        command=(
            "sh",
            "-c",
            "rustc /workspace/main.rs -o /tmp/elder_run_bin && exec /tmp/elder_run_bin",
        ),
        extra_env=(
            ("HOME", CONTAINER_TMP_DIR),
            ("CARGO_HOME", f"{CONTAINER_TMP_DIR}/cargo"),
            ("TMPDIR", CONTAINER_TMP_DIR),
        ),
    ),
    "python": RuntimeSpec(
        name="python",
        image="python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72",
        filename="main.py",
        command=("python3", "-B", "/workspace/main.py"),
        extra_env=(
            ("HOME", CONTAINER_TMP_DIR),
            ("PYTHONDONTWRITEBYTECODE", "1"),
            ("PYTHONUNBUFFERED", "1"),
        ),
    ),
    "golang": RuntimeSpec(
        name="golang",
        image="golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437",
        filename="main.go",
        command=(
            "sh",
            "-c",
            "cp /workspace/main.go /tmp/main.go && exec go run /tmp/main.go",
        ),
        extra_env=(
            ("HOME", CONTAINER_TMP_DIR),
            ("GOCACHE", f"{CONTAINER_TMP_DIR}/go-build"),
            ("GOPATH", f"{CONTAINER_TMP_DIR}/go"),
            ("GOTMPDIR", CONTAINER_TMP_DIR),
        ),
    ),
    "ruby": RuntimeSpec(
        name="ruby",
        image="ruby:3.4-slim-bookworm@sha256:5a5585d0c13f42ed8503d56d4160dc2c19689cefc1a1acaff1ff91e82eef3577",
        filename="main.rb",
        command=("ruby", "/workspace/main.rb"),
        extra_env=(("HOME", CONTAINER_TMP_DIR),),
    ),
}

RUNTIME_ALIASES: dict[str, str] = {
    "node": "nodejs",
    "js": "nodejs",
    "javascript": "nodejs",
    "py": "python",
    "python3": "python",
    "go": "golang",
    "rb": "ruby",
    "rs": "rust",
}


def get_runtime(name: str) -> RuntimeSpec:
    """Resolve a runtime name or alias to its pinned ``RuntimeSpec``.

    Raises:
        ValueError: If the runtime is empty or unsupported.
    """
    normalized = (name or "").strip().lower()
    canonical = RUNTIME_ALIASES.get(normalized, normalized)
    if canonical not in RUNTIMES:
        supported = ", ".join(sorted(RUNTIMES))
        raise ValueError(
            f"Unsupported runtime {name!r}; supported runtimes: {supported}"
        )
    return RUNTIMES[canonical]
