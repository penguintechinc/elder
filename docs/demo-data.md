# Demo Data (`make seed-demo`)

One command builds a coherent, idempotent demo organisation ("Acme Corp") for
README/website screenshots and local demos. It is tenant-scoped, uses fake data
only (reserved `example.com` / `.local` addresses, fake endpoints, no real
keys or secrets) and is safe to re-run: the second run prints
`DEMO SEED RESULT: unchanged`.

```bash
make seed-demo        # canonical; `make seed-mock-data` is an alias
make screenshots      # capture tool (needs a running stack)
```

## Demo login (defined once)

Defined in `scripts/demo_seed/common.py`, mirrored in
`scripts/demo_login.example.env` (a unit test keeps them in sync).

| Variable | Default (local dev only) | Notes |
|----------|--------------------------|-------|
| `DEMO_ADMIN_EMAIL` | `demo-admin@example.com` | reserved domain, no mailbox |
| `DEMO_ADMIN_PASSWORD` | `demo-local-only-1` | **set explicitly** for anything shared; refused when `ELDER_ENV`/`QUART_ENV`/`FLASK_ENV`/`APP_ENV` is `production` |

Login is tenant-less: it resolves to the `system`/`default` tenant, which is where
all demo data lives (so the bootstrap `admin@localhost.local` also sees it).

## What is seeded (3-4 items per feature)

| Feature | Table | Items |
|---------|-------|-------|
| Staff / teams | `identities` / `identity_groups` | 3 staff + admin / 3 teams |
| Organisations | `organizations` | 4 (Acme Corp, Platform Engineering, Globex Industries, Support Team) |
| Compute (LXD containers + VMs) | `entities` | 4 (`lxd_container` x2, `lxd_vm`, `virtual_machine`) |
| Networks / IPAM | `networking_resources` / `ipam_*` | 4 / 3 prefixes, 3 VLANs, 4 addresses |
| Data stores | `data_stores` | 3 |
| Services / dependencies / software | `services` / `dependencies` / `software` | 4 / 4 / 4 |
| SBOM + vulnerabilities | `sbom_scans` / `sbom_components` / `vulnerabilities` | 3 / 4 / 4 (critical, high, medium, low; open, investigating, remediated, accepted) |
| Certificates / license policies | `certificates` / `license_policies` | 3 / 3 |
| Secrets (references only) / API keys | `secrets` / `api_keys` | 3 / 3 |
| Issues (support + feature) | `issues` | 4 (open, in progress, resolved) |
| Projects / milestones / labels | `projects` / `milestones` / `labels` | 3 / 3 / 4 |
| On-call | `on_call_rotations` | 3 |
| Docs / pages / diagrams | `doc_documents` / `pg_pages` / `dg_diagrams` | 3 / 3 / 3 |
| AI resources | `ai_resources` | 4 (2 LLMs, gateway, MCP server; fake endpoints) |
| Run functions | `run_functions` | 3 |
| Webhooks / stream playbooks | `webhooks` / `stream_playbooks` | 3 / 3 |
| Access reviews | `access_reviews` | 3 (scheduled, in progress, overdue) |

**Documented exceptions (graphs, not item lists):** the AWS cloud-discovery
topology and the Kubernetes cluster hierarchy/geo entities
(`scripts/seed_cloud_discovery.py`, `scripts/seed_k8s_geo_demo.py`) create
more entities, networking resources, services, data stores and edges so the
Map/Graph pages render; customer contacts (4) and join rows (memberships,
review items) are also outside the 3-4 rule.

**Skipped:** `backups`, IdP/SSO/SCIM configs, `cloud_accounts` credentials,
helpdesk (moved to Waddles), CSV import jobs, `builtin_secrets` (would hold
secret values) and `iceflows` credentials: nothing here may carry a credential.

## Rules the seed follows

- `village_id` is minted for **every** created row whose table has the column
  (insert hook in `scripts/demo_seed/common.py`, plus a backfill pass for rows
  that predate the run).
- Idempotent: natural-key get-or-create; no duplicates on re-run.
- Only `identities`/`portal_users` hold person-like data, and only on reserved domains.
- Layout: `scripts/seed_demo.py` (entry) + `scripts/demo_seed/*` (per-feature
  modules) layered with the existing `seed_cloud_discovery`, `seed_k8s_geo_demo`
  and `seed_demo_unified` scripts, which still run standalone
  (`make seed-cloud-discovery` etc.). The old Faker/HTTP seeder is `make seed-mock-data-api`.
- Tests: `tests/unit/test_seed_demo_data.py`.
