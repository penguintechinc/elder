#!/usr/bin/env bash
# Release consistency gate (check only — never bumps anything).
#
#   1. .version is vMajor.Minor.Patch[.build] (build = epoch64).
#   2. k8s/helm/elder/Chart.yaml `appVersion` equals the full .version (the
#      chart's default image tag, which docker-build.yml publishes), and the
#      chart `version` equals its Major.Minor.Patch. A stale appVersion would
#      silently deploy an older build.
#   3. CHANGELOG.md has an `## [Unreleased]` section, and with --release
#      (release -> main PRs, which cut a version) a `## [Major.Minor.Patch]`
#      section for the version being released.
#
# Usage: scripts/release-check.sh [--release]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

release=false
[ "${1:-}" = "--release" ] && release=true

fail=0
err() {
	echo "ERROR: $*" >&2
	fail=1
}

version="$(tr -d '[:space:]' <.version)"
if [[ ! "$version" =~ ^v?([0-9]+\.[0-9]+\.[0-9]+)(\.[0-9]+)?$ ]]; then
	echo "ERROR: .version '$version' is not vMajor.Minor.Patch[.build]" >&2
	exit 1
fi
semver="${BASH_REMATCH[1]}"

chart=k8s/helm/elder/Chart.yaml
chart_version="$(sed -n 's/^version:[[:space:]]*"\{0,1\}\([^"[:space:]]*\)"\{0,1\}.*/\1/p' "$chart")"
app_version="$(sed -n 's/^appVersion:[[:space:]]*"\{0,1\}\([^"[:space:]]*\)"\{0,1\}.*/\1/p' "$chart")"
[ "$chart_version" = "$semver" ] ||
	err "$chart version '$chart_version' != .version $semver — bump it"
[ "$app_version" = "${version#v}" ] ||
	err "$chart appVersion '$app_version' != .version ${version#v} — bump it"

grep -q '^## \[Unreleased\]' CHANGELOG.md ||
	err "CHANGELOG.md has no '## [Unreleased]' section"
if $release; then
	grep -qE "^## \[v?${semver//./\\.}\]" CHANGELOG.md ||
		err "CHANGELOG.md has no '## [$semver]' section for this release"
fi

[ "$fail" -eq 0 ] || exit 1
echo "release-check OK: .version=$version chart=$chart_version appVersion=$app_version release=$release"
