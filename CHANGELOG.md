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
  global-admin-only; a `tenant_id` override is 403). Request bodies are now
  validated against strict schemas (wrong types are a 400 that never echoes
  submitted values), and unexpected failures are logged by exception type only
  so a submitted `oidc_client_secret` can never reach logs or responses.
  A PUT racing a DELETE now answers 404 instead of 500.
  - **Behavior change:** only `global_role == "admin"` gets cross-tenant
    access; `global_role == "support"` no longer has a cross-tenant IdP list
    view (it is treated like any tenant caller).
  - **Behavior change:** `GET /api/v1/sso/idp` now requires a tenant or global
    admin (previously any authenticated portal user could list their tenant's
    configs, secrets included).
  - **Response shape change:** `POST`/`PUT /api/v1/sso/idp` return the full
    allowlisted configuration (was `{id, name, tenant_id}` / `{id, updated}`);
    `PUT` keeps `updated: true`. Every IdP response carries the same fields
    (the other protocol's fields are `null`), documented in `openapi/v4.yaml`.
  - **Secrets are never returned:** `oidc_client_secret` is write-only and is
    reported as `has_client_secret` (boolean) instead.
  - `POST` rejects `idp_type` other than `saml`/`oidc`; `tenant_id` on create
    and update is accepted only as the caller's own tenant (global admins may
    choose on create) and a config can never be re-homed.

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
