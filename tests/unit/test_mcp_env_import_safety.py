"""Import-time safety of hub-mcp env parsing: bad values never crash, defaults apply."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# apps.mcp.server imports the third-party `mcp` package, which is only installed in the
# apps/mcp image (its requirements pin a different opentelemetry than the shared CI env).
_MCP_SKIP_REASON = (
    "mcp package not installed in this env (see #387); runs in the apps/mcp image"
)
_MCP_MODULES = {"apps.mcp.server"}

# (env var, module, attribute expression evaluated on the module, expected default)
_CASES = [
    ("MCP_MAX_REQUEST_BYTES", "apps.mcp.http_app", "MAX_REQUEST_BYTES", "1048576"),
    ("MCP_MAX_BATCH_SIZE", "apps.mcp.http_app", "MAX_BATCH_SIZE", "50"),
    ("MCP_MAX_RESPONSE_BYTES", "apps.mcp.client", "max_response_bytes()", "4194304"),
    ("MCP_PORT", "apps.mcp.server", "mcp.settings.port", "8080"),
]


@pytest.mark.parametrize("bad", ["abc", "", "0", "-5"])
@pytest.mark.parametrize(("var", "module", "expr", "default"), _CASES)
def test_bad_env_does_not_break_import(
    var: str, module: str, expr: str, default: str, bad: str
) -> None:
    """Invalid, empty, zero and negative values import cleanly and yield the default."""
    if module in _MCP_MODULES:
        pytest.importorskip("mcp", reason=_MCP_SKIP_REASON)
    code = f"import {module} as m; print(m.{expr})"
    env = {**os.environ, "PYTHONPATH": str(_ROOT), var: bad}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=_ROOT,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    assert proc.stdout.strip().splitlines()[-1] == default


def test_mcp_skip_only_when_package_absent() -> None:
    """If `mcp` is installed it must import cleanly, so a skip can never mask a broken install."""
    if importlib.util.find_spec("mcp") is None:
        pytest.skip(_MCP_SKIP_REASON)
    import mcp  # noqa: F401, PLC0415 - fail loudly on a present-but-broken package


def test_invalid_value_warns_once_without_leaking_value() -> None:
    """A rejected value logs one warning naming the variable, never the raw value."""
    code = (
        "import logging, io; s = io.StringIO(); "
        "logging.basicConfig(stream=s, level=logging.WARNING); "
        "from apps.mcp import envutil; "
        "envutil.env_int('MCP_X', 7); envutil.env_int('MCP_X', 7); "
        "print(s.getvalue().count('MCP_X'), 'sekret-9' in s.getvalue())"
    )
    env = {**os.environ, "PYTHONPATH": str(_ROOT), "MCP_X": "sekret-9"}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=_ROOT,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    assert proc.stdout.strip() == "1 False"
