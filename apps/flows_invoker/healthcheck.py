#!/usr/bin/env python3
"""Health check script for the Elder Flows Invoker service.

No HTTP server runs in this container (it is a pure Redis Streams
consumer), so health is checked via a native Python process invocation
rather than an HTTP probe. Verifies that:

1. Core invoker modules can be imported
2. REDIS_URL / DATABASE_URL are configured
3. The sandbox workspace root is writable

Exit codes:
    0 - Healthy
    1 - Unhealthy
"""

import os
import sys


def check_imports() -> bool:
    """Verify core modules can be imported."""
    try:
        from apps.flows_invoker import executor, sandbox  # noqa: F401
        from apps.worker.config.settings import settings  # noqa: F401
        from shared.jobbus import JobBus  # noqa: F401

        return True
    except ImportError as e:
        print(f"Import error: {e}", file=sys.stderr)
        return False


def check_config() -> bool:
    """Verify required connection settings are configured."""
    ok = True
    if not os.getenv("REDIS_URL"):
        print("REDIS_URL not configured", file=sys.stderr)
        ok = False
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL not configured", file=sys.stderr)
        ok = False
    return ok


def check_workspace_root() -> bool:
    """Verify the sandbox workspace root is writable."""
    workspace_root = os.getenv("FLOWS_WORKSPACE_ROOT", "/workspace")
    try:
        os.makedirs(workspace_root, exist_ok=True)
        test_file = os.path.join(workspace_root, ".health_check")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"Workspace root check failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    """Run all health checks."""
    checks = [
        ("imports", check_imports),
        ("config", check_config),
        ("workspace_root", check_workspace_root),
    ]

    all_passed = True
    for name, check in checks:
        try:
            if not check():
                print(f"Check failed: {name}", file=sys.stderr)
                all_passed = False
        except Exception as e:  # noqa: BLE001
            print(f"Check error ({name}): {e}", file=sys.stderr)
            all_passed = False

    if all_passed:
        print("OK")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
