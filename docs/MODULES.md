# Modules & the WIRED Model

Elder is a **modular monolith**: one deployable platform made of 15 modules that are all installed in every build and switched on or off at deploy time.

Modules are grouped into the five **WIRED** pillars. The pillars are the same five nouns everywhere — the sidebar sections, the module groups, and the `ELDER_GROUP_*` env toggles.

```
  W  Workstreams    things that run on a trigger      streams · flows · webhooks & alerting
  I  Issues         things that need doing            issues · projects · internal tickets
  R  Relationships  edges, discovered and attested    discovery · access reviews
  E  Entities       things you inventory              CMDB · IPAM · SBOM · services · secrets
  D  Documents      things you write down             documents · pages · diagrams
```

## The 15 modules

| Module | Title | Group | Depends on | License feature | Table prefix |
|--------|-------|-------|------------|-----------------|--------------|
| `streams` | Streams (Workflows) | `workstreams` | — | — | `stream_` |
| `flows` | Flows (CI/CD) | `workstreams` | — | — | `iceflows_` |
| `webhooks_alerting` | Webhooks & Alerting | `workstreams` | — | — | — |
| `issues` | Issues & Project Tracking | `issues` | — | — | — |
| `helpdesk` | Helpdesk (Internal Ticketing) | `issues` | — | — | `hd_` |
| `discovery` | Discovery & Sync | `relationships` | `infrastructure` | — | — |
| `access_reviews` | Access Reviews | `relationships` | — | `access-reviews` | — |
| `infrastructure` | Infrastructure CMDB | `entities` | — | — | — |
| `ipam` | IP Address Management | `entities` | — | — | — |
| `sbom` | Software Bill of Materials | `entities` | — | — | — |
| `services_oncall` | Service Catalog & On-Call | `entities` | — | — | — |
| `secrets` | Secrets Management | `entities` | — | — | — |
| `documents` | Documents & Knowledge Base | `documents` | — | — | `doc_` |
| `pages` | Pages & Documentation | `documents` | `documents` | — | `pg_` |
| `diagrams` | Diagrams & Drawing | `documents` | — | — | `dg_` |

> ⚠️ **`flows` and `helpdesk` are API-only today.** Both register blueprints and worker task groups, but neither has an entry in `web/src/modules/` — the React app ships 13 module manifests against the backend's 15, so enabling either adds REST endpoints and background work but no sidebar entry or page.

Manifests live in [`apps/api/modules/__init__.py`](../apps/api/modules/__init__.py); the `ModuleManifest` dataclass and resolution logic are in [`apps/api/modules/registry.py`](../apps/api/modules/registry.py). The frontend mirror is [`web/src/modules/registry.ts`](../web/src/modules/registry.ts).

## Why each module sits where it does

- **`discovery` is a Relationship, not an Entity.** It ingests from AWS, GCP, Kubernetes, Okta, LDAP, vCenter and friends, but its distinguishing output is *edges* — cloud scans auto-create dependency links between the resources they find. The entities themselves are owned by `infrastructure`.
- **`access_reviews` is a Relationship too.** An access review attests an identity→resource edge; it is the human-verified counterpart to the machine-built edges from `discovery`.
- **`webhooks_alerting` is a Workstream.** It is event-triggered automation — same shape as `streams` (event/schedule-driven workflows) and `flows` (CI/CD), just with an outbound HTTP delivery at the end.
- **The graph UI lives in Entities.** Dependencies, the topology map, and the relationship graph are served by the `infrastructure` module, which owns both the objects and the edges between them. The Relationships group holds the modules that *produce* edges, not the ones that draw them.
- **`helpdesk` is internal-only.** Customer relationships, public intake forms, and community support belong to **Waddles**. Elder's helpdesk covers employees and contractors, and a support request is just an Issue with `issue_type=support`.

## Enabling and disabling modules

Three layers, highest precedence first:

| Precedence | Variable | Example | Effect |
|-----------|----------|---------|--------|
| 1 (highest) | `ELDER_MODULE_<NAME>` | `ELDER_MODULE_HELPDESK=false` | Forces one module on or off |
| 2 | `ELDER_GROUP_<GROUP>` | `ELDER_GROUP_WORKSTREAMS=false` | Forces every module in one WIRED group on or off |
| 3 (base) | `ELDER_MODULES_ENABLED` | `ELDER_MODULES_ENABLED=all` | Base set: `all`, or a comma-separated module list |

The five group toggles are `ELDER_GROUP_WORKSTREAMS`, `ELDER_GROUP_ISSUES`, `ELDER_GROUP_RELATIONSHIPS`, `ELDER_GROUP_ENTITIES`, `ELDER_GROUP_DOCUMENTS`. Values are parsed as `true`/`1`/`yes` → on, anything else → off.

A per-module override always wins over its group:

```bash
ELDER_MODULES_ENABLED=all
ELDER_GROUP_ISSUES=false        # issues + helpdesk off...
ELDER_MODULE_HELPDESK=true      # ...except helpdesk
```

**Dependencies are validated at resolution time** — enabling `pages` without `documents`, or `discovery` without `infrastructure`, is rejected rather than silently half-mounted.

### In Kubernetes

Per-module flags render into the `elder-modules` ConfigMap from `.Values.modules` in [`k8s/helm/elder`](../k8s/helm/elder):

```yaml
modules:
  helpdesk: false
  accessReviews: true
```

camelCase keys become `ELDER_MODULE_<UPPER_SNAKE>` env vars.

## Reading module state at runtime

`GET /api/v1/modules` returns every installed module with:

| Field | Meaning |
|-------|---------|
| `name` | Module identifier |
| `title` | Human-readable name |
| `group` | WIRED group (`workstreams`, `issues`, `relationships`, `entities`, `documents`) |
| `nav_id` | Sidebar navigation category ID |
| `scopes` | OIDC scopes the module declares |
| `installed` | Present in this build (always `true` today) |
| `licensed` | License entitlement satisfied |
| `tenant_enabled` | Enabled for the calling tenant |
| `effective` | The resolved answer — the UI renders only these |

The web UI buckets `effective: true` modules by `group` and renders **one collapsible
sidebar section per pillar**, in acronym order: Workstreams, Issues, Relationships,
Entities, Documents. A pillar with no enabled modules is omitted entirely.

Module-authored sub-headers are intentionally not shown. Modules each declare their own
nav categories and those headers collide — `sbom` and `services_oncall` both ship a
"Software & Services", and a single-item module renders its name twice (header, then the
identical item). One section per pillar removes both classes of duplicate by
construction. The items are still contributed by module manifests and gated per tenant,
so toggling a module still adds or removes exactly its own entries.

## Licensing

**Grouping is presentation and deployment only — no WIRED group is tier-locked.** License gating is per-module via `ModuleManifest.license_feature` (today only `access_reviews` sets one) and per-object via the tenant/global limit counters. See [licensing/](licensing/).

## History

The four ad-hoc buckets `core`, `crm`, `workflow`, and `kb` were replaced by the WIRED pillars in v4.0.0. The `crm` group was retired earlier when the customer-relations half of `helpdesk` moved to Waddles. See [RELEASE_NOTES.md](RELEASE_NOTES.md).
