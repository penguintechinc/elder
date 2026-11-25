#!/usr/bin/env bash
# Security audit gate — the single source of truth for the scanners run by
# `make audit` locally AND by the `security` job in .github/workflows/ci.yml.
# Every scanner fails the run on findings; nothing here is soft-fail.
#
#   trivy    fs CVE + secret + misconfig scan, HIGH/CRITICAL fail.
#            Accepted exceptions: .trivyignore (misconfig IDs, each justified)
#            and trivy-secret.yaml (path+regex scoped secret allow rules).
#   semgrep  p/security-audit + p/secrets + p/owasp-top-ten, ERROR fail.
#   gitleaks working-tree secret scan. Accepted exceptions: .gitleaksignore
#            (per-finding fingerprints, reviewed doc placeholders only).
#
# CodeQL runs only in CI (no local equivalent). Ruff/mypy/eslint gates live in
# `make lint` / scripts/lint-debt.sh, not here.
#
# Usage: scripts/audit.sh [trivy|semgrep|gitleaks]...   (default: all)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRIVY_IMAGE="aquasec/trivy:0.69.3"
SEMGREP_IMAGE="semgrep/semgrep:1.107.0"
GITLEAKS_IMAGE="zricethezav/gitleaks:v8.21.2"

run_trivy() {
	echo "==> trivy fs (HIGH,CRITICAL)"
	docker run --rm -v "${ROOT}:/project" -w /project "${TRIVY_IMAGE}" \
		fs /project \
		--exit-code 1 \
		--severity HIGH,CRITICAL \
		--ignorefile /project/.trivyignore \
		--secret-config /project/trivy-secret.yaml \
		--skip-dirs /project/web/node_modules \
		--skip-dirs /project/.venv \
		--skip-dirs /project/target \
		--quiet
}

run_semgrep() {
	echo "==> semgrep (ERROR severity)"
	docker run --rm -v "${ROOT}:/src" -w /src "${SEMGREP_IMAGE}" \
		semgrep scan \
		--config p/security-audit \
		--config p/secrets \
		--config p/owasp-top-ten \
		--severity ERROR \
		--exclude web/node_modules --exclude .venv --exclude target \
		--metrics off \
		--error \
		.
}

run_gitleaks() {
	echo "==> gitleaks (working tree)"
	docker run --rm -v "${ROOT}:/repo" -w /repo "${GITLEAKS_IMAGE}" \
		detect --source . --no-git --redact --gitleaks-ignore-path .gitleaksignore
}

scanners=("$@")
[ "${#scanners[@]}" -gt 0 ] || scanners=(trivy semgrep gitleaks)

failed=()
for s in "${scanners[@]}"; do
	case "$s" in
	trivy | semgrep | gitleaks) "run_${s}" || failed+=("$s") ;;
	*)
		echo "unknown scanner: $s" >&2
		exit 2
		;;
	esac
done

if [ "${#failed[@]}" -gt 0 ]; then
	echo "AUDIT FAILED: ${failed[*]}" >&2
	exit 1
fi
echo "Audit passed: ${scanners[*]}"
