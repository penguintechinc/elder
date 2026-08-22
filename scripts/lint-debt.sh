#!/usr/bin/env bash
# Ratchet gate for linters that cannot pass cleanly yet.
#
# Elder carries known debt in mypy, shellcheck, prettier and eslint. Wrapping
# those in `|| true` (what `make lint` used to do) means the gate can never
# fail, so the debt grows silently behind a permanently green target.
#
# This script counts the findings, prints the denominator, and fails if any
# count rose above the checked-in baseline. Fixing findings and lowering the
# baseline is the way debt goes down; nothing lets it go up.
#
# Usage:
#   scripts/lint-debt.sh            # compare against .lint-baseline, fail on regression
#   scripts/lint-debt.sh --update   # rewrite .lint-baseline from current counts
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BASELINE_FILE=".lint-baseline"
VENV_PY="${VENV_PY:-.venv/bin/python3}"
UPDATE=0
[[ "${1:-}" == "--update" ]] && UPDATE=1

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'

count_mypy() {
    local out
    # A writable cache dir: .mypy_cache is often left root-owned by container runs.
    out="$(MYPY_CACHE_DIR="${TMPDIR:-/tmp}/elder-mypy-cache" \
        "$VENV_PY" -m mypy apps/ shared/ \
        --ignore-missing-imports --explicit-package-bases \
        --exclude apps/api/grpc/generated 2>&1 || true)"
    grep -c 'error:' <<<"$out" || true
}

# Count only git-tracked scripts. A bare `find` also walks .worktrees/ and other
# untracked checkouts, so the denominator differed between a dev box (79) and CI
# (40) — and a shrinking denominator reads as "IMPROVED" when nothing improved.
shell_files() { git ls-files '*.sh'; }

count_shellcheck() {
    local fails=0
    while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        shellcheck "$f" >/dev/null 2>&1 || fails=$((fails + 1))
    done < <(shell_files)
    echo "$fails"
}

count_prettier() {
    # prettier writes its [warn] lines to STDERR — 2>/dev/null silently counts zero.
    local out
    out="$(cd web; npx prettier --check . 2>&1 || true)"
    if ! grep -q 'Checking formatting\|All matched files use Prettier\|Code style issues' <<<"$out"; then
        echo "PRETTIER_DID_NOT_RUN"
        return
    fi
    grep -c '^\[warn\] ' <<<"$out" || true
}

count_eslint() {
    local out rc n
    out="$(cd web; npx eslint . --ext ts,tsx --report-unused-disable-directives 2>&1)" && rc=0 || rc=$?
    # eslint exits 0 (clean) or 1 (findings); anything else means it failed to run.
    if (( rc > 1 )); then
        echo "ESLINT_DID_NOT_RUN"
        return
    fi
    # eslint exits 0 even with warnings present, so parse the summary either way.
    # Summary line looks like: "✖ 2 problems (0 errors, 2 warnings)"
    n="$(grep -oE '[0-9]+ problems?' <<<"$out" | head -1 | grep -oE '[0-9]+' || true)"
    if [[ -z "$n" ]]; then
        # No summary line: clean run (rc 0) counts as zero, anything else is a non-run.
        if (( rc == 0 )); then echo 0; else echo "ESLINT_DID_NOT_RUN"; fi
        return
    fi
    echo "$n"
}

# Denominators, so "0 findings" can never mean "nothing was examined".
total_shell="$(shell_files | wc -l | tr -d ' ')"
if [[ "$total_shell" -eq 0 ]]; then
    echo "${RED}No shell scripts found — the scan is pointed at nothing. Failing.${RESET}" >&2
    exit 1
fi

echo "${BLUE}Counting known lint debt (${total_shell} shell scripts examined)...${RESET}"

declare -A CURRENT
CURRENT[mypy]="$(count_mypy)"
CURRENT[shellcheck]="$(count_shellcheck)"
CURRENT[prettier]="$(count_prettier)"
CURRENT[eslint]="$(count_eslint)"

# A count that is not a number means the linter never ran. Absence of findings is
# not evidence of cleanliness — fail loudly rather than banking a fake zero.
for k in mypy shellcheck prettier eslint; do
    if ! [[ "${CURRENT[$k]}" =~ ^[0-9]+$ ]]; then
        echo "${RED}${k} did not run (got: ${CURRENT[$k]}). Refusing to report a count.${RESET}" >&2
        exit 1
    fi
done

if [[ "$UPDATE" -eq 1 ]]; then
    {
        echo "# Known lint debt. Lower these as findings are fixed; scripts/lint-debt.sh"
        echo "# fails if any count rises. Regenerate with: scripts/lint-debt.sh --update"
        for k in mypy shellcheck prettier eslint; do
            echo "${k}=${CURRENT[$k]}"
        done
        echo "shellcheck_total=${total_shell}"
    } > "$BASELINE_FILE"
    echo "${GREEN}Wrote $BASELINE_FILE${RESET}"
    cat "$BASELINE_FILE"
    exit 0
fi

if [[ ! -f "$BASELINE_FILE" ]]; then
    echo "${RED}$BASELINE_FILE missing — run: scripts/lint-debt.sh --update${RESET}" >&2
    exit 1
fi

declare -A BASE
while IFS='=' read -r key value; do
    [[ "$key" =~ ^# ]] && continue
    [[ -z "$key" ]] && continue
    BASE[$key]="$value"
done < "$BASELINE_FILE"

# A denominator that shrank means the scan narrowed, not that the code improved.
base_total="${BASE[shellcheck_total]:-}"
if [[ -n "$base_total" ]] && (( total_shell < base_total )); then
    echo "${RED}Only ${total_shell} shell scripts examined, baseline expected ${base_total}.${RESET}" >&2
    echo "${RED}The scan narrowed — a lower finding count here is not an improvement.${RESET}" >&2
    exit 1
fi

regressed=0
improved=0
printf '\n  %-12s %8s %10s   %s\n' "LINTER" "CURRENT" "BASELINE" "STATUS"
for k in mypy shellcheck prettier eslint; do
    cur="${CURRENT[$k]}"
    base="${BASE[$k]:-}"
    if [[ -z "$base" ]]; then
        printf '  %-12s %8s %10s   %s\n' "$k" "$cur" "-" "${RED}NO BASELINE${RESET}"
        regressed=1
    elif (( cur > base )); then
        printf '  %-12s %8s %10s   %s\n' "$k" "$cur" "$base" "${RED}REGRESSED +$((cur - base))${RESET}"
        regressed=1
    elif (( cur < base )); then
        printf '  %-12s %8s %10s   %s\n' "$k" "$cur" "$base" "${GREEN}IMPROVED -$((base - cur))${RESET}"
        improved=1
    else
        printf '  %-12s %8s %10s   %s\n' "$k" "$cur" "$base" "${YELLOW}unchanged${RESET}"
    fi
done
echo

if (( regressed )); then
    echo "${RED}Lint debt increased. Fix the new findings, or justify and update .lint-baseline.${RESET}" >&2
    exit 1
fi

if (( improved )); then
    # Improvement must never block work — just make the stale baseline impossible to miss.
    echo "${GREEN}Lint debt went down. Lock it in so it cannot creep back:${RESET}"
    echo "${GREEN}    scripts/lint-debt.sh --update && git add .lint-baseline${RESET}"
    exit 0
fi

echo "${GREEN}Lint debt unchanged (no regressions).${RESET}"
