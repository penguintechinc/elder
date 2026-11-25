#!/usr/bin/env python3
"""Regression gate for gh-237: flags unscoped tenant-owned-resource lookups.

Two shapes of the same bug, both counted together:

1. PyDAL's bracket lookup (``db.organizations[org_id]``) resolves a row by
   primary key with NO tenant filter -- exactly the shape of the
   cross-tenant IDOR fixed in gh-237 (``organization_tree.py``, ``graph.py``).
2. A call into a tenant-scoped resolver/helper (at minimum the
   ``apps/api/common/refs`` resolver family: ``resolve_by_village_id()``,
   ``resolve_ref()``) that omits the ``tenant_id=`` keyword. This is the same
   unscoped-lookup shape one layer removed -- the resolver itself fails
   closed on ``tenant_id=None``, but a caller that never passes it still
   can't resolve anything useful, and a caller that *does* pass a bare id
   straight through (``resolve_by_village_id(db, village_id)``, no
   ``tenant_id``) is exactly how ``lookup_village_id.py`` (gh-237 follow-up)
   let an unauthenticated caller enumerate other tenants' resources --
   check (1) alone can't see this because it never touches ``db.<table>[``.

Both caught by a bare ``db.<table>[<id>]`` or missing ``tenant_id=`` check
are a REGRESSION if new (not a pre-existing site already in the baseline).

3. A list/search query that starts from every row (``db.<table>.id > 0``,
   including ``self.db.`` in services) inside a function that never applies
   a tenant or organization predicate. Checks (1) and (2) only see
   single-row lookups, so whole-table list/search/suggest queries (e.g. the
   universal search service, ``GET /entities``) passed the gate while
   returning every tenant's rows. This shape is counted separately
   (``list_count=`` in the baseline) so fixing one category can never buy
   headroom for a regression in the other. The scan also covers
   ``apps/api/services/`` because route handlers often delegate the query.

This is a ratchet, not a hard zero: fixing all pre-existing sites across
the codebase is a separate, larger effort (gh-237 full sweep, ~339 sites).
This script only prevents the count from GROWING -- new call sites must use
``apps.api.utils.tenant_scoping.get_tenant_scoped()`` or pass ``tenant_id=``
to a resolver instead (mirrors ``scripts/lint-debt.sh``'s ratchet convention
exactly: count, compare to a checked-in baseline, fail only on regression).

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
    # Service modules already brought to zero unscoped lookups; listing them
    # here keeps them there. Add further services as they are cleaned up.
    "apps/api/services/networking/*.py",
]

# Service modules run queries on behalf of routes -- scanned for the
# whole-table list shape (check 3) only.
LIST_SCAN_GLOBS = SCAN_GLOBS + [
    "apps/api/services/*.py",
    "apps/api/services/**/*.py",
]

# `db.<table>.id > 0` -- a query seeded with "every row" (check 3).
ALL_ROWS_QUERY = re.compile(r"\bdb\.\w+\.id\s*>\s*0\b")

# Any of these inside the enclosing function counts as a tenant predicate.
TENANT_PREDICATE = re.compile(r"tenant|org_ids|organization_id\.belongs", re.I)

FUNC_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s")

# `db.<table>[<expr>]` bracket lookup -- PyDAL's unscoped-by-primary-key
# accessor. Requires the leading `db.` so it does not flag
# `tenant_scoping.py`'s own internal `table[record_id]` (already tenant
# verified by the caller-supplied `tenant_id` before that line runs).
BRACKET_LOOKUP = re.compile(r"\bdb\.\w+\[")

# Tenant-scoped resolver/helper family -- a call site that doesn't pass
# `tenant_id=` has the same unscoped shape as a bare bracket lookup, just
# behind a helper instead of `db.<table>[`. Minimum set per gh-237 follow-up;
# extend as new tenant-scoped resolvers are added to apps/api/common/.
TENANT_SCOPED_RESOLVERS = ("resolve_by_village_id", "resolve_ref")

RESOLVER_CALL_START = re.compile(
    r"\b(?:" + "|".join(re.escape(name) for name in TENANT_SCOPED_RESOLVERS) + r")\s*\("
)

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


def count_unscoped_bracket_lookups(files: list[Path]) -> int:
    """Count lines matching the unscoped bracket-lookup pattern, minus exemptions."""
    total = 0
    for f in files:
        for line in f.read_text().splitlines():
            if ALLOW_MARKER in line:
                continue
            if BRACKET_LOOKUP.search(line):
                total += 1
    return total


def _collect_call_text(lines: list[str], start_idx: int) -> str:
    """Join ``lines[start_idx:]`` until the call expression's parens balance.

    Approximate -- counts ``(``/``)`` per line with no string-literal
    awareness. Acceptable for a ratchet-style scan of route files: matches
    this script's existing bracket-lookup regex in simplicity, and a false
    negative here only means a pre-existing site isn't caught yet (same
    ratchet trade-off as the bracket check).
    """
    depth = 0
    collected: list[str] = []
    for line in lines[start_idx:]:
        collected.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            break
    return "\n".join(collected)


def count_unscoped_resolver_calls(files: list[Path]) -> int:
    """Count calls to TENANT_SCOPED_RESOLVERS missing a ``tenant_id=`` keyword."""
    total = 0
    for f in files:
        lines = f.read_text().splitlines()
        for idx, line in enumerate(lines):
            if ALLOW_MARKER in line:
                continue
            if not RESOLVER_CALL_START.search(line):
                continue
            call_text = _collect_call_text(lines, idx)
            if ALLOW_MARKER in call_text:
                continue
            if re.search(r"\btenant_id\s*=", call_text):
                continue
            total += 1
    return total


def _enclosing_function(lines: list[str], idx: int) -> str:
    """Return the source of the function enclosing ``lines[idx]`` (or "" at module level)."""
    line_indent = len(lines[idx]) - len(lines[idx].lstrip())
    start = None
    def_indent = 0
    for j in range(idx, -1, -1):
        m = FUNC_DEF.match(lines[j])
        if m and len(m.group(1)) < line_indent:
            start, def_indent = j, len(m.group(1))
            break
    if start is None:
        return ""
    end = len(lines)
    for k in range(idx + 1, len(lines)):
        text = lines[k]
        if text.strip() and (len(text) - len(text.lstrip())) <= def_indent:
            if not text.lstrip().startswith((")", "]", "}")):
                end = k
                break
    return "\n".join(lines[start:end])


def count_unscoped_list_queries(files: list[Path]) -> int:
    """Count ``db.<t>.id > 0`` queries in functions with no tenant/org predicate.

    Heuristic: the enclosing function's source (signature, body, comments)
    must mention a tenant or organization-set predicate; otherwise the
    whole-table query is counted. Global, non-tenant tables (e.g. the
    tenants catalogue itself) carry the ``# tenant-scope-exempt`` marker.
    """
    total = 0
    for f in files:
        lines = f.read_text().splitlines()
        for idx, line in enumerate(lines):
            if ALLOW_MARKER in line or not ALL_ROWS_QUERY.search(line):
                continue
            if TENANT_PREDICATE.search(_enclosing_function(lines, idx)):
                continue
            total += 1
    return total


def list_offenders(files: list[Path]) -> list[str]:
    """Return ``path:line`` for each check-3 offender (printed to aid fixing)."""
    out: list[str] = []
    for f in files:
        lines = f.read_text().splitlines()
        for idx, line in enumerate(lines):
            if ALLOW_MARKER in line or not ALL_ROWS_QUERY.search(line):
                continue
            if not TENANT_PREDICATE.search(_enclosing_function(lines, idx)):
                out.append(f"{f}:{idx + 1}")
    return out


def find_list_files(root: Path) -> list[Path]:
    """Collect route + service files matched by LIST_SCAN_GLOBS."""
    files: set[Path] = set()
    for pattern in LIST_SCAN_GLOBS:
        files.update(root.glob(pattern))
    return sorted(f for f in files if f.is_file())


def count_unscoped(files: list[Path]) -> int:
    """Total unscoped lookups: bracket lookups + resolver calls missing tenant_id."""
    return count_unscoped_bracket_lookups(files) + count_unscoped_resolver_calls(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="rewrite the baseline")
    parser.add_argument(
        "--root", type=Path, default=DEFAULT_ROOT, help="repo root to scan (tests only)"
    )
    parser.add_argument(
        "--list", action="store_true", help="print whole-table list-query offenders"
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

    bracket_count = count_unscoped_bracket_lookups(files)
    resolver_count = count_unscoped_resolver_calls(files)
    current = bracket_count + resolver_count
    list_files = find_list_files(root)
    list_current = count_unscoped_list_queries(list_files)
    print(
        f"tenant-scoping gate: {current} unscoped lookups "
        f"({bracket_count} bracket lookups + {resolver_count} resolver calls "
        f"missing tenant_id=) across {len(files)} files examined; "
        f"{list_current} whole-table list queries without a tenant/org "
        f"predicate across {len(list_files)} route+service files examined"
    )
    if args.list:
        for item in list_offenders(list_files):
            print(f"  list-query: {item.removeprefix(str(root) + '/')}")

    if args.update:
        baseline_file.write_text(
            "# gh-237 ratchet baseline: known unscoped tenant-owned-resource\n"
            "# lookups -- bare `db.<table>[<id>]` bracket lookups, and calls into\n"
            "# the tenant-scoped resolver family (apps/api/common/refs) missing\n"
            "# `tenant_id=`.\n"
            "# scripts/check_tenant_scoping.py fails if this count rises.\n"
            "# Fix a bracket lookup with apps.api.utils.tenant_scoping.get_tenant_scoped(),\n"
            "# or pass tenant_id= to a resolver call.\n"
            "# list_count = whole-table `db.<t>.id > 0` queries in functions with no\n"
            "# tenant/org predicate (routes + services); ratcheted separately.\n"
            "# Regenerate with: scripts/check_tenant_scoping.py --update\n"
            f"count={current}\n"
            f"list_count={list_current}\n"
        )
        print(f"Wrote {baseline_file} (count={current}, list_count={list_current})")
        return 0

    if not baseline_file.exists():
        print(
            f"{baseline_file} missing -- run: scripts/check_tenant_scoping.py --update",
            file=sys.stderr,
        )
        return 1

    baseline: int | None = None
    list_baseline: int | None = None
    for line in baseline_file.read_text().splitlines():
        if line.startswith("count="):
            baseline = int(line.split("=", 1)[1])
        elif line.startswith("list_count="):
            list_baseline = int(line.split("=", 1)[1])
    if baseline is None:
        print(
            f"{baseline_file} has no count= line -- refusing to compare.",
            file=sys.stderr,
        )
        return 1
    if list_baseline is None:
        # Older baselines predate check 3: strictest reading, zero allowed.
        list_baseline = 0

    if list_current > list_baseline:
        print(
            f"REGRESSION: {list_current} whole-table list queries without a "
            f"tenant/org predicate, baseline is {list_baseline}. Scope the query "
            "to the caller's tenant (or its organizations), or mark a genuinely "
            f"global table '{ALLOW_MARKER}'. Run with --list to see offenders.",
            file=sys.stderr,
        )
        return 1

    if current > baseline:
        print(
            f"REGRESSION: {current} unscoped lookups found, baseline is {baseline}. "
            "New `db.<table>[<id>]` call sites must use get_tenant_scoped() "
            "(apps/api/utils/tenant_scoping.py); new calls into "
            f"{', '.join(TENANT_SCOPED_RESOLVERS)} must pass tenant_id=. "
            f"Or mark the line '{ALLOW_MARKER}' with justification.",
            file=sys.stderr,
        )
        return 1

    if current < baseline or list_current < list_baseline:
        print(
            f"Tenant-scoping debt went down (lookups {baseline} -> {current}, "
            f"list queries {list_baseline} -> {list_current}). Lock it in: "
            f"scripts/check_tenant_scoping.py --update && git add {BASELINE_NAME}"
        )
        return 0

    print("tenant-scoping gate: unchanged (no regressions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
