# Cloud-Scan Relationship Linkage — Design

**Date:** 2026-07-25
**Status:** Draft — awaiting review
**Scope:** Auto-create relationship edges between resources discovered by Elder's cloud scans, for **all** providers (AWS, GCP, Azure, Kubernetes). Customer requirement.

---

## 1. Problem

Elder's cloud discovery imports resources but does not build the graph *between* them. A scan of an AWS account yields isolated EC2, VPC, subnet, and volume rows with almost no edges connecting them. The customer needs the linkage — the graph is the product, not the inventory.

### Current state (audited 2026-07-25)

| Provider | Inter-resource edges today | Containment→root | Edge tests |
|---|---|---|---|
| **K8s** (best) | Ingress→Service (`routes_to`), PVC→PV (`bound_to`), namespace membership | `discovered_from` | none |
| **AWS** | EC2→VPC only | VPC→root | mocked unit only |
| **GCP** | none | bucket→root, VPC→root | none |
| **Azure** | none — treated as "unknown provider" | none (`parent_id=None`) | none |

Three root causes, common to every provider:

1. **Capture gap.** Discovery clients emit flat resource dicts and drop cross-references. AWS keeps `vpc_id`/`subnet_id` but discards security groups, EBS attachments, RDS subnet groups, ELB targets (`aws_discovery.py:345-357, 430-437, 605-616`). GCP/Azure capture almost nothing — GCP's `discover_compute` ignores `network_interfaces` (`gcp_discovery.py:104-108`); Azure reduces a VNet's subnets to an integer count (`azure_discovery.py:170`) and its DB/serverless enumerators are empty stubs.
2. **Linking is provider-gated and fragile.** Real edges fire only in K8s/AWS branches of `service.py`, gated on metadata keys the other providers never emit, and resolve targets by **display name in emission order** — which collides and silently drops. Azure hits none of these branches at all (`service.py:362-378, 386-390, 1181-1217`).
3. **No edge/E2E test coverage** on any provider. `scripts/aws/e2e-free-tier-assets.sh` only provisions/tears down AWS assets; it never calls Elder or asserts anything.

### What already works (build on it)

The edge **engine** exists and is idempotent (SELECT-first):

- `_create_dependency_link(source_type, source_id, target_type, target_id, dep_type, meta)` → `dependencies` table (`service.py:880-914`).
- `_upsert_network_entity_mapping(network_id, entity_id, relationship_type)` → `network_entity_mappings` (`service.py:591-614`).

`dependencies.source_type`/`target_type` are free-form `String(64)` with plain-integer, non-FK ids (`dependency.py:27-30`), so edges **span tables freely** — the code already emits `service→entity`, `data_store→entity`, `networking_resource→service`. `network_entity_mappings` is the narrower FK-enforced network↔entity table.

### Read side — two disjoint graph stores (audited 2026-07-25)

Writing an edge is not enough; it has to land where the UI reads. The read path is split:

- **`dependencies`** feeds the main graph (`graph.py:96-101`), `/graph/analyze`, `/graph/path`, the global **Map** (`graph.py:700-714`), the org **Relationship** graph (`organizations_pydal.py:447-452`), the **Dependencies** page, and each entity's "related resources" (`entities.py:337-357`). This is the general-purpose edge store.
- **`network_entity_mappings`** feeds **only** the Networking→Topology tab (`networking.py:349-368`, `service.py:475-527`, read with `include_entities=true` from `NetworkTopologyGraph.tsx:42`).

**Critical gotcha:** `/graph/map` materializes nodes only for `organization, entity, identity, project, milestone, issue` and **silently drops any edge whose endpoint node type isn't in that allowlist** (`graph.py:17-24, 461-463, 502-615`). A `dependencies` edge pointing at a `networking_resource`/`data_store`/`service`/`software` row therefore renders **no node and no edge** on the map. The main `/graph` query is `entity↔entity` only. So cross-table cloud edges are invisible in today's general graph UIs until the read-side node allowlist + node materialization are widened. This makes read-side work part of the feature, not optional polish.

---

## 2. Decisions (approved)

| Decision | Choice | Rationale |
|---|---|---|
| Ingestion path | **Discovery path only** (`apps/worker/discovery/`) | A user-configured cloud scan flows *only* through discovery (`POST /discovery/jobs`). The AWS/GCP connector path is orphaned — env-var-only, no API/UI, no Azure — and redundant for cloud accounts. Linkage there would be exercised by no customer scan. |
| Edge depth | **Full topology** — network + attachments + routing + containment | Containment-only ("in this VPC") can't answer "what's in this subnet" or "what can reach this DB." Those need attachment + traffic edges. |
| Delivery | **Stacked PRs**, shared core first, one provider per PR | Matches branch-per-change; each provider independently reviewable/tweakable. |

**Out of scope:** the connector path for cloud providers; building an Azure connector; linkage for non-cloud connector sources (LDAP, Okta, Google Workspace) — those keep their own path. Unifying discovery-vs-connector dedup keys.

---

## 3. Architecture — a shared two-pass linker

Replace the fragile inline, name-based, emission-ordered linking with an explicit two-pass model in the shared `service.py`. Each provider only declares *what* it wants linked; the engine resolves and persists edges.

```
Discovery client (per provider)          Persistence engine (shared service.py)
──────────────────────────────          ───────────────────────────────────────
discover_*()  ──►  resource dict         PASS 1  persist each resource
  + external_id (native cloud id)  ─────►   • write row (entities / domain table)
  + relationships: [                       • set external_id on the row
      {target_ext_id, edge_type,           • register scan-index:
       target_kind_hint}, ...              (provider, external_id) → (type_str, row_id)
    ]
                                         PASS 2  resolve + write edges
                                           for each captured relationship:
                                             • resolve target_ext_id → (type_str, id)
                                               via scan-index, then DB fallback
                                             • write dependencies edge
                                               (or network_entity_mapping if net↔entity)
                                             • unresolved → log WARNING + count
                                         return {resources, edges, unresolved} in job result
```

### 3.1 Capture contract (per-provider, the only per-provider work)

Every resource dict gains two normalized fields:

- `external_id: str` — the resource's own native cloud id (`i-abc`, `vpc-123`, an ARN, an Azure resource id, a GCP self-link, a K8s uid/name).
- `relationships: list[dict]` — zero or more `{ "target_external_id": str, "edge_type": str, "target_kind": str }`. `edge_type` is from the canonical vocabulary (§4). `target_kind` is a hint (`"networking_resource"`, `"entity"`, `"service"`, `"data_store"`) used to disambiguate resolution; the resolver treats it as advisory, not authoritative.

Providers do **not** call the edge helpers directly — they only populate these fields. This keeps all resolution/ordering logic in one place and makes a provider's contribution a pure, unit-testable transform.

### 3.2 Persist pass (shared)

- Every insert/upsert sets `external_id` on the row (migration §5 adds the column where missing).
- As each row is persisted, register `(provider, external_id) → (type_string, row_id)` in an in-memory `scan_index` dict scoped to the job.
- Existing dedup keys are unchanged (name-tuples) to avoid behavioral drift; `external_id` is written additively. (Divergence from connector dedup is noted but out of scope.)

### 3.3 Link pass (shared)

- Runs after **all** resources for the job are persisted — emission order no longer matters.
- Resolution order for each `target_external_id`:
  1. `scan_index` lookup (target discovered in this same scan) — O(1), no DB hit.
  2. DB fallback: query the hinted table (then a bounded set of edge-eligible tables) by `external_id` + `organization_id` — covers targets persisted by an **earlier** scan (incremental discovery).
  3. Unresolved → `logger.warning(...)` with the dangling ref, increment `unresolved_edges`. **Never silent** (the current failure mode).
- Write via the existing idempotent helpers, following the storage rule in §3.5: every edge → `dependencies` (source of truth); network-membership edges additionally → `network_entity_mappings` (topology-tab projection). Edge direction: **source = dependent/child, target = depended-on/parent** (matches existing `routes_to`, `bound_to`).
- `dependencies.tenant_id` (NOT NULL) is set via the existing `_tenant_for_org(organization_id)` helper.

### 3.4 Job-result reporting

`discovery_history` / job result gains `edges_created` and `unresolved_edges` counts. This is the hook E2E asserts on (`unresolved_edges == 0` for the standard asset set) and how an operator sees linkage health.

### 3.5 Storage & visibility rule

Because the read path is split (§1), the linker uses one source of truth plus one narrow projection, and the general graph's node allowlist must be widened.

1. **`dependencies` is the single source of truth for every edge.** All topology edges (attachments, routing, roles, network membership, containment) are written here with the §4 `dependency_type` and the domain type-string vocabulary (`entity`, `networking_resource`, `data_store`, `service`, `software`, `identity`). This is what the main graph, Map, Relationship graph, Dependencies page, and entity detail read.
2. **Network-membership edges are dual-written to `network_entity_mappings`.** Only the entity↔networking_resource edges (`in_network`, `in_subnet`) are *also* upserted into `network_entity_mappings` (FK-valid: `entity_id`→entities, `network_id`→networking_resources), so the Networking→Topology tab renders them. `dependencies` remains authoritative; this table is a projection. Edges whose source is itself a `networking_resource` (subnet→VPC) cannot use this table (no entity endpoint) and live in `dependencies` only.
3. **Read-side node allowlist widened.** Extend `/graph/map` (and the main `/graph`) node materialization to include `networking_resource`, `data_store`, `service`, and `software` node types (`graph.py:17-24, 502-615`), so cross-table `dependencies` edges render instead of being dropped (`graph.py:461-463`). Without this, most cloud edges are stored but invisible. This is a bounded read-side change (node-type map + serializer), scoped into PR1.

**Net effect on visibility:** every cloud edge appears in the Dependencies page, entity "related resources", org Relationship graph, and global Map; network-membership edges *additionally* appear in the Networking Topology tab.

---

## 4. Canonical edge vocabulary

Reuse existing `dependency_type` strings where present; add the rest. All lowercase snake_case.

| edge_type | Meaning | Existing? |
|---|---|---|
| `in_network` | resource lives in a VPC/VNet/network | new (AWS uses `connected_to` today — migrate) |
| `in_subnet` | resource lives in a subnet | new |
| `uses_security_group` | resource references a SG/NSG/firewall | new |
| `attached_to` | disk/volume attached to a compute instance | new |
| `routes_to` | LB/ingress forwards to a target | ✅ `service.py:956` |
| `assumes_role` | compute assumes an IAM role / GCP service account | new |
| `in_resource_group` | Azure resource → resource group | new |
| `bound_to` | PVC → PV | ✅ `service.py:1005` |
| `runs_on` | K8s Pod → Node | new |
| `runs_in` | container image → Pod | new |
| `manages` | Deployment → Pod | new |
| `discovered_from` | provenance → provider root | ✅ (keep) |

---

## 5. Data model change (one additive migration)

`entities.external_id` already exists (`entity.py:36`, `String(255)`, nullable). Add the same column to the edge-eligible domain tables that lack it:

- `networking_resources` (`infrastructure.py:26-43`)
- `data_stores` (`infrastructure.py:74-103`)
- `services` (`assets.py:24-47`)
- `software` (`assets.py:50-70`)
- `certificates` (`security.py:136`)

All `external_id String(255) NULL`, plus a non-unique index on `(organization_id, external_id)` for the DB-fallback resolution query. `identities.auth_provider_id` already serves as its native key; reuse it rather than adding a duplicate. Alembic migration is additive and idempotent (nullable, no backfill required) — safe under the "no auto-migrate on startup" rule; runs as the normal manual/K8s-job step.

---

## 6. Per-provider capture catalog (the topology)

Each entry = a `relationships` edge the provider must emit. "Capture work" flags where the client currently drops the source data and must be extended.

### AWS (`aws_discovery.py`)
| Source | edge_type | Target | Capture work |
|---|---|---|---|
| EC2 instance | `in_network` | VPC | vpc_id already captured |
| EC2 instance | `in_subnet` | subnet | subnet_id already captured |
| EC2 instance | `uses_security_group` | SG | **add** — read `SecurityGroups` |
| EC2 instance | `assumes_role` | IAM role | **add** — read `IamInstanceProfile` |
| EBS volume | `attached_to` | EC2 instance | **add** — read `Attachments[].InstanceId` |
| subnet | `in_network` | VPC | **add** — enumerate subnets w/ VpcId |
| security group | `in_network` | VPC | **add** |
| RDS instance | `in_network` / `uses_security_group` | VPC / SG | **add** — DBSubnetGroup.VpcId, VpcSecurityGroups |
| ELB/ALB | `in_network`, `routes_to` | VPC, target instances | **add** — target groups/targets |
| Lambda | `in_network`, `uses_security_group`, `assumes_role` | VPC, SG, role | **add** — VpcConfig, Role |

### GCP (`gcp_discovery.py`)
| Source | edge_type | Target | Capture work |
|---|---|---|---|
| GCE instance | `in_network` | VPC network | **add** — `network_interfaces[].network` |
| GCE instance | `in_subnet` | subnetwork | **add** — `network_interfaces[].subnetwork` |
| GCE instance | `assumes_role` | service account | **add** — `serviceAccounts[]` |
| persistent disk | `attached_to` | instance | **add** — enumerate disks + users |
| subnet | `in_network` | VPC | **add** |
| Cloud SQL | `in_network` | VPC | **add** — replace stub (`gcp_discovery.py:171-175`) |
| forwarding rule / backend | `routes_to` | instance group | **add** — enumerate LBs |

### Azure (`azure_discovery.py`) — biggest lift
First, wire Azure as a known provider (currently "unknown"): add Azure branches to `_detect_provider_type` (`service.py:362-378`), `_ensure_provider_root_entity` (`:386-390`), `_ensure_intermediate_networking` (`:446-543`), `_resource_type_to_domain` (`:1181-1217`). Then capture:
| Source | edge_type | Target | Capture work |
|---|---|---|---|
| VM | `in_network` / `in_subnet` | VNet / subnet | **add** — NIC → ipConfigurations |
| VM | `uses_security_group` | NSG | **add** |
| VM / resource | `in_resource_group` | resource group | **add** |
| disk | `attached_to` | VM | **add** — enumerate disks |
| VNet | (subnet) `in_network` | VNet | **add** — real subnets, not count |
| SQL DB | `in_network` | server/VNet | **add** — replace stub |
| load balancer | `routes_to` | backend pool members | **add** |

### Kubernetes (`k8s_discovery.py` + `service.py`) — close parity gaps
Keep existing Ingress→Service, PVC→PV, namespace membership. Add:
| Source | edge_type | Target | Capture work |
|---|---|---|---|
| Pod | `runs_on` | Node | node_name already in metadata (`k8s_discovery.py:198`) — needs Node entity + edge |
| container image | `runs_in` | Pod | image currently links to root only (`service.py:1410`) |
| Deployment | `manages` | Pod | **add `discover_deployments`** — advertised (`k8s_discovery.py:83`) but not implemented |

---

## 7. Error handling

- Capture: an unparseable/absent cross-reference field is skipped for that one edge with a debug log — never aborts the resource or the scan.
- Resolution: unresolved target → WARNING + counter, scan continues.
- Edge write: `_create_dependency_link` / `_upsert_network_entity_mapping` already wrap inserts in try/except-with-warning; keep, but the unresolved **counter** is the signal E2E checks, not log scraping.
- A provider that emits *no* `relationships` (pre-migration behavior) produces zero new edges — the engine is a no-op, so PR1 lands safely ahead of any provider change.

---

## 8. Testing

- **Engine unit tests (PR1):** scan-index resolution, DB-fallback resolution, cross-table edges (`entity`→`networking_resource`), unresolved counting, idempotent re-scan (no duplicate edges), tenant_id set correctly. Also backfill the **currently-absent** K8s edge tests (`_create_dependency_link`, `_upsert_network_entity_mapping`, ingress/PVC helpers).
- **Per-provider capture unit tests:** feed a mocked API page, assert the resource dict's `external_id` + `relationships` are correct. (Extends the existing `test_aws_discovery.py` pattern.)
- **E2E (per provider):** extend the provisioning scripts (`scripts/aws/e2e-free-tier-assets.sh` + new GCP/Azure equivalents) into a full loop: provision real assets → create+run a discovery job via API → poll history → query the graph for the **expected edge set** → assert `unresolved_edges == 0` → tear down. This is the "relations in AWS show up in Elder" verification that is currently missing entirely.
- Coverage stays ≥90%.

---

## 9. Delivery — stacked PRs into `release/v4.0.X`

| PR | Branch | Contents |
|---|---|---|
| 1 | `feature/discovery-linker-core` | Two-pass engine, capture contract, `external_id` population, scan-index + DB-fallback resolver, dual-write rule (§3.5), unresolved counting, migration (§5), **read-side node-allowlist widening** (`graph.py` — render `networking_resource`/`data_store`/`service`/`software` nodes), engine + K8s-edge unit tests. No provider *capture* change yet, so no new edges appear until PR2+. |
| 2 | `feature/discovery-linkage-aws` | AWS capture catalog (§6) + unit tests + real-asset E2E. |
| 3 | `feature/discovery-linkage-gcp` | GCP capture + tests + E2E. |
| 4 | `feature/discovery-linkage-azure` | Azure provider wiring + capture + tests + E2E. |
| 5 | `feature/discovery-linkage-k8s-parity` | Deployments discovery, Pod→Node / image→Pod / Deployment→Pod edges + tests. |

Each branches off the previous until it merges, then rebases onto the release branch; merged bottom-up. Each PR notes its stack position and closes its tracking issue.

---

## 10. Acceptance criteria

- After scanning the standard free-tier asset set for a provider, every expected edge in §6 is present in `dependencies` (and network-membership edges also in `network_entity_mappings`), and `unresolved_edges == 0`.
- The edges are **visible**, not just stored: cloud edges render in the Dependencies page / entity "related resources" / org Relationship graph / global Map; network-membership edges render in the Networking→Topology tab. (Guards against the "written but dropped by the node allowlist" failure mode — §1, §3.5.)
- Re-running the same scan creates no duplicate edges (idempotent).
- Azure resources are no longer "unknown provider" — they get a root, domain routing, and edges.
- No regression in resource counts; ≥90% coverage; CI green.
