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
- SCIM and OIDC tenant hardening (regressions: scim-cross-tenant,
  scim-async-decorator, oidc-global-role-carry, oidc-null-tenant,
  oidc-refresh-userinfo-logout-ownership, oidc-login-csrf,
  oidc-email-unverified):
  - SCIM config/token routes (`/sso/scim/config*`) require the path tenant to be
    the caller's own tenant (global admins excepted); another tenant answers
    the same 404 as "SCIM not configured". A tenant admin could previously
    rotate another tenant's SCIM bearer token and then read/deactivate its
    users. SCIM create/update (which 500'd on every call) now work, and SCIM
    never modifies platform-privileged (`global_role`) accounts. Bearer tokens
    are still stored in plaintext (follow-up: store a hash).
  - OIDC logins never produce a session carrying a stored `global_role`; a
    tenant-scoped IdP refuses accounts that have one (403
    `oidc_tenant_idp_global_role_forbidden`). A GLOBAL OIDC IdP only serves a
    tenant whose `settings.oidc_allow_global_idp` is the boolean `true`, for
    existing users only (no JIT); users are never created without a tenant.
  - `/oidc/refresh|userinfo|logout/<idp_id>` only accept configs the caller
    may use (other tenants' and, for non-global admins, global configs: 404).
  - **Behavior change:** `/oidc/authorize` now generates `state`, `nonce` and a
    PKCE challenge server-side (Redis, single use, 10 min) and sets an HttpOnly
    `elder_oidc_binding` cookie the callback requires; a client-supplied
    `state` is ignored, a global IdP needs `tenant_id`, and `idp_id`/
    `redirect_uri` on the callback are optional (must match if sent). A
    tenant-scoped IdP with JIT disabled now still signs in existing users.
  - **Behavior change:** an OIDC login requires `email_verified` to be the
    boolean `true` in the ID token (userinfo is not consulted for it or for the
    email). IdPs that omit the claim (e.g. default Azure AD) are refused with
    403 `oidc_email_not_verified` until a per-IdP opt-out ships (follow-up).
  - Upstream error text is never returned or logged by the OIDC routes.

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
