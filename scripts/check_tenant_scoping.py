#!/usr/bin/env python3
"""Regression gate for gh-237: flags unscoped ``db.<table>[<id>]`` lookups.

PyDAL's bracket lookup (``db.organizations[org_id]``) resolves a row by
primary key with NO tenant filter -- exactly the shape of the cross-tenant
IDOR fixed in gh-237 (``organization_tree.py``, ``graph.py``). A caller who
can supply a bare integer id and reach one of these lookups can read
another tenant's row by guessing/incrementing the id.

This is a ratchet, not a hard zero: fixing all pre-existing sites across
the codebase is a separate, larger effort (gh-237 full sweep, ~339 sites).
This script only prevents the count from GROWING -- new call sites must use
``apps.api.utils.tenant_scoping.get_tenant_scoped()`` instead (mirrors
``scripts/lint-debt.sh``'s ratchet convention exactly: count, compare to a
checked-in baseline, fail only on regression).

Usage:
    scripts/check_tenant_scoping.py              # fail if count rose above baseline
    scripts/check_tenant_scoping.py --update     # rewrite the baseline from current count
    scripts/check_tenant_scoping.py --root DIR   # scan a different root (tests)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_NAME = ".tenant-scoping-baseline"

# Route files that resolve tenant-owned resources by numeric id -- the
# surface most likely to receive a caller-controlled numeric id straight
# from the URL path or a query parameter.
SCAN_GLOBS = [
    "apps/api/modules/*/routes/*.py",
    "apps/api/modules/*/routes/**/*.py",
    "apps/api/api/v1/*.py",
]

# `db.<table>[<expr>]` bracket lookup -- PyDAL's unscoped-by-primary-key
# accessor. Requires the leading `db.` so it does not flag
# `tenant_scoping.py`'s own internal `table[record_id]` (already tenant
# verified by the caller-supplied `tenant_id` before that line runs).
BRACKET_LOOKUP = re.compile(r"\bdb\.\w+\[")

# Lines carrying this marker are an intentional, reviewed exception (mirrors
# ruff's `# noqa`) -- e.g. a lookup already gated by an outer tenant check
# that isn't expressible as a single-line scope.
ALLOW_MARKER = "# tenant-scope-exempt"


def find_files(root: Path) -> list[Path]:
    """Collect route files matched by SCAN_GLOBS, deduplicated and sorted."""
    files: set[Path] = set()
    for pattern in SCAN_GLOBS:
        files.update(root.glob(pattern))
    return sorted(f for f in files if f.is_file())


def count_unscoped(files: list[Path]) -> int:
    """Count lines matching the unscoped bracket-lookup pattern, minus exemptions."""
    total = 0
    for f in files:
        for line in f.read_text().splitlines():
            if ALLOW_MARKER in line:
                continue
            if BRACKET_LOOKUP.search(line):
                total += 1
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="rewrite the baseline")
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT, help="repo root to scan (tests only)"
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    baseline_file = root / BASELINE_NAME

    files = find_files(root)
    if not files:
        print(
            "No route files found -- the scan is pointed at nothing. Failing.",
            file=sys.stderr,
        )
        return 1

    current = count_unscoped(files)
    print(
        f"tenant-scoping gate: {current} unscoped bracket lookups "
        f"across {len(files)} files examined"
    )

    if args.update:
        baseline_file.write_text(
            "# gh-237 ratchet baseline: known unscoped `db.<table>[<id>]` lookups.\n"
            "# scripts/check_tenant_scoping.py fails if this count rises.\n"
            "# Fix a lookup with apps.api.utils.tenant_scoping.get_tenant_scoped()\n"
            "# and regenerate with: scripts/check_tenant_scoping.py --update\n"
            f"count={current}\n"
        )
        print(f"Wrote {baseline_file} (count={current})")
        return 0

    if not baseline_file.exists():
        print(
            f"{baseline_file} missing -- run: scripts/check_tenant_scoping.py --update",
            file=sys.stderr,
        )
        return 1

    baseline: int | None = None
    for line in baseline_file.read_text().splitlines():
        if line.startswith("count="):
            baseline = int(line.split("=", 1)[1])
    if baseline is None:
        print(
            f"{baseline_file} has no count= line -- refusing to compare.",
            file=sys.stderr,
        )
        return 1

    if current > baseline:
        print(
            f"REGRESSION: {current} unscoped lookups found, baseline is {baseline}. "
            "New `db.<table>[<id>]` call sites must use get_tenant_scoped() "
            "(apps/api/utils/tenant_scoping.py) or be marked "
            f"'{ALLOW_MARKER}' with justification.",
            file=sys.stderr,
        )
        return 1

    if current < baseline:
        print(
            f"Tenant-scoping debt went down ({baseline} -> {current}). Lock it in: "
            f"scripts/check_tenant_scoping.py --update && git add {BASELINE_NAME}"
        )
        return 0

    print("tenant-scoping gate: unchanged (no regressions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
