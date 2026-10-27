# Changelog

All notable changes to Elder are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses `vMajor.Minor.Patch` version tags (see `scripts/version/update-version.sh`).

## [Unreleased]

### Security
- SSO IdP configuration CRUD (`/api/v1/sso/idp*`) now enforces tenant
  ownership (regression: idp-config-cross-tenant). Previously any tenant
  admin could list, rewrite (SAML certificate / OIDC issuer+client) or delete
  another tenant's or the global IdP config, enabling forged SAML assertions
  and OIDC login takeover. Non-global callers are confined to their own
  tenant's configs (other-tenant ids answer 404; global configs are
  global-admin-only; a `tenant_id` override is 403). Responses no longer
  include `oidc_client_secret` (reported as `has_client_secret`); the list
  endpoint is now admin-only.

### Changed
- Release-gate hardening: recovered `.github/CODEOWNERS`, added coverage
  gating (`--cov-fail-under=90` / vitest coverage thresholds), hardened
  shell scripts with `set -euo pipefail`, enabled SBOM + provenance on
  release container builds, added `SECURITY.md` / `security.txt`, and added
  an OSS dependency license compliance gate (`make license-check-deps`).

## Prior Releases

This file was seeded retroactively from existing git tags/releases; entries
below are version markers, not full release notes. See
[GitHub Releases](https://github.com/penguintechinc/elder/releases) for the
generated body of each tag going forward.

| Version | Date       |
|---------|------------|
| v3.2.2  | 2026-05-21 |
| v3.2.1  | 2026-04-21 |
| v3.1.6  | 2026-04-07 |
| v3.1.5  | 2026-03-26 |
| v3.1.4  | 2026-03-11 |
| v3.1.1  | 2026-03-03 |
| v3.1.0  | 2026-02-25 |
| v3.0.9  | 2026-02-05 |
| v3.0.8  | 2026-01-28 |
| v3.0.7  | 2026-01-27 |

Full tag history: `git tag --sort=-creatordate`.
