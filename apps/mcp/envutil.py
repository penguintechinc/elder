"""Import-safe environment parsing helpers shared by the hub-mcp modules.

Invalid or non-positive values never raise (a bad env var must not crash the
service at import); they fall back to the default and warn once per variable.
The raw value is never logged because it may be mistyped secret material.
"""

from __future__ import annotations

import logging
import os

_log = logging.getLogger("hub-mcp.env")
_warned: set[str] = set()


def _warn_once(name: str, default: object) -> None:
    """Log a single warning per variable name when a value is rejected."""
    if name not in _warned:
        _warned.add(name)
        _log.warning(
            "invalid %s (want a positive number); using default %s", name, default
        )


def env_int(name: str, default: int) -> int:
    """Read a positive integer env var, falling back to ``default`` (warn once if invalid)."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        _warn_once(name, default)
        return default
    return value


def env_float(name: str, default: float) -> float:
    """Read a positive finite float env var, falling back to ``default`` (warn once if invalid)."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not value > 0 or value == float("inf"):
        _warn_once(name, default)
        return default
    return value
