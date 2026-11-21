# Third-Party Licenses

Elder is licensed AGPL-3.0 (see `LICENSE.md`). This file tracks the licenses of
third-party (OSS) dependencies bundled at build/runtime, so it's clear at a
glance that nothing incompatible has been pulled in.

## Generating the full report

The authoritative, up-to-date list is generated, not hand-maintained here —
run it whenever dependencies change:

```bash
# Python (apps/, shared/) — requires the dev venv (make setup-python)
.venv/bin/pip-licenses --format=markdown > /tmp/elder-python-licenses.md

# Web (web/) — requires web/node_modules (npm ci)
cd web && npx --yes license-checker@25.0.1 --production --csv > /tmp/elder-web-licenses.csv
```

`make license-check-deps` runs both against the AGPL-3.0-compatible allowlist
and fails the build if a dependency's license isn't on it — see the `Makefile`
`# ── OSS License Compliance ──` section and `.github/workflows/ci.yml`'s
`license-check-deps` job.

## Placeholder

This file is a placeholder until the first generated report is committed
alongside it (tracked separately from this repo-hygiene pass — the CI job
above will surface the actual dependency license inventory on first run).
