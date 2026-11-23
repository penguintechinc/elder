#!/usr/bin/env python3
"""Native Python healthcheck for ``svc-runworker`` (no curl/wget required).

Verifies:
1. Core runworker modules import cleanly.
2. ``ELDER_API_URL`` is configured.
3. The heartbeat file (``RUNS_HEALTH_FILE``, default ``/tmp/runworker.healthy``)
   exists and has been updated recently.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path


def check_imports() -> bool:
    """Verify core runworker modules can be imported."""
    try:
        from apps.runworker import k8s_runner, main, runtimes  # noqa: F401

        return True
    except ImportError as exc:
        sys.stderr.write(f"Import error: {exc}\n")
        return False


def check_config() -> bool:
    """Verify required worker configuration is present."""
    if not os.getenv("ELDER_API_URL"):
        sys.stderr.write("ELDER_API_URL not configured\n")
        return False
    return True


def check_heartbeat() -> bool:
    """Verify the poll loop heartbeat file is fresh."""
    default_health = os.path.join(tempfile.gettempdir(), "runworker.healthy")
    health_file = Path(os.getenv("RUNS_HEALTH_FILE", default_health))
    poll_interval = max(1.0, float(os.getenv("RUNS_POLL_INTERVAL_SECONDS", "3")))
    max_age = max(60.0, poll_interval * 10.0)

    if not health_file.exists():
        sys.stderr.write(f"Heartbeat file missing: {health_file}\n")
        return False

    try:
        raw = health_file.read_text(encoding="utf-8").strip()
        ts = float(raw)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"Heartbeat unreadable: {exc}\n")
        return False

    age = time.time() - ts
    if age > max_age:
        sys.stderr.write(f"Heartbeat stale ({age:.1f}s > {max_age:.1f}s)\n")
        return False
    return True


def main() -> int:
    """Run all health checks and exit 0 when healthy."""
    checks = (
        ("imports", check_imports),
        ("config", check_config),
        ("heartbeat", check_heartbeat),
    )
    ok = True
    for name, fn in checks:
        try:
            if not fn():
                sys.stderr.write(f"Check failed: {name}\n")
                ok = False
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"Check error ({name}): {exc}\n")
            ok = False

    if ok:
        sys.stdout.write("OK\n")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
