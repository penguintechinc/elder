"""Crash-proof environment variable parsers for numeric tuning knobs.

Timeouts, retry caps and concurrency limits are read from the environment,
often at import time. A typo in a deployment value must never take the
process down, so every parser here falls back to a safe default and clamps
the result into a caller-supplied range instead of raising.
"""

from __future__ import annotations

import math
import os

import structlog

logger = structlog.get_logger(__name__)


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """Read ``name`` as a float clamped to ``[minimum, maximum]``.

    Returns ``default`` when the variable is unset, empty, non-numeric or not
    finite (``nan``/``inf``), logging the variable name only (never its value,
    which may be an operator's pasted secret).
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning("env_value_invalid", variable=name, fallback=default)
        return default
    if not math.isfinite(value):
        logger.warning("env_value_invalid", variable=name, fallback=default)
        return default
    return min(max(value, minimum), maximum)


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read ``name`` as an integer clamped to ``[minimum, maximum]``.

    Returns ``default`` when the variable is unset, empty or not a base-10
    integer; never raises, so it is safe to call at module import time.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip(), 10)
    except ValueError:
        logger.warning("env_value_invalid", variable=name, fallback=default)
        return default
    return min(max(value, minimum), maximum)
