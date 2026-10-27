# svc-workflows — Design Spec

**Status:** Draft for owner review (2026-10-27). Design only. No code, scaffolding, or Dockerfiles until the derived plan is reviewed.

**Goal:** Replace Elder's in-process workflow and CI/CD engines (Streams, Flows) with `svc-workflows`: a multi-container Rust service that runs tenant-authored flowbundles inside WASM sandboxes, behind a gated gateway, melded with Elder's own data, scalable past 100k events/second.

**Differentiator:** not a standalone automation engine. The point is "melding" with Elder's assets, issues, schedules, identities, diagrams, and documents (D9, 5A).

**Architecture:** Elder API (Python/Quart) stays the control plane (definitions, authz, identity, summaries) and is OUT of the per-event hot path. The data plane is one Cargo workspace producing several images split along trust boundaries (gateway, engine, builder), scaled per role, dividing work over Valkey Streams, deployed as shared-nothing cells.

**Tech stack:** Rust (tokio/axum, wasmtime component model + WASI 0.2), gRPC, partitioned Valkey/Redis Streams, shared `penguin-libs` Rust crates (git-pinned), React Flow canvas (`@xyflow/react` 12.3.5, in `web/package.json`), Helm only.

---

## 0. Reading guide

| Marker | Meaning |
|---|---|
| **DECISION** | Made by the product owner. Fixed |
| **AUTHOR'S CALL — owner to confirm** | Chosen by the author so the doc is plannable. Builder may proceed, not treat as settled |
| **ANSWERED** | Resolved by the coordinator/owner after the first draft; stated as fact |
| **VERIFIED** | Read by the author. Elder = `origin/release/v4.0.X` @ `bbd5ba7`; Waddles = `ae935412` |
| **RECON** | From the recon brief; not re-read |
| **UNVERIFIED** | Not verified. Builder checks first. All capacity numbers and toolchain-maturity claims not marked VERIFIED are UNVERIFIED |

Paths are relative to the Elder repo unless prefixed `waddles:` or `penguin-libs:`. Sections 5A (melding), 5B (scale), 5C (languages, compile, cold start), 5D (marketplace) follow section 5 and keep other numbers stable. "Flowbundle" = Elder's term for Waddles' "app bundle".

---

## 1. Intent & why

### 1.1 Intent

- One service presentation: Lucidchart (diagrams) + n8n/Zapier (event in, process, action out) + GitHub Actions (CI/CD workflows, reusable `uses:` functions).
- Elder's data is the differentiator: look up and act on Elder resources; data events launch flows; a diagram converts to a flow.
- All tenant logic runs as WASM; platform code never executes user code natively. All traffic in or out passes a gateway we gate.
- Works fully with no other PenguinTech product present. Scales horizontally and vertically, per role, past 100k events/s without redesign.

### 1.2 Why now

| Problem today | Evidence |
|---|---|
| Two engines, ~15k prod LOC, no WASM, no Rust | RECON; no `Cargo.toml` (VERIFIED) |
| Users cannot extend safely: `stream_custom_modules.code_blob` is a table nothing executes | `streams.py` (VERIFIED by grep) |
| Credentials plaintext, writes return 501 (cites #171) | `routes/credentials.py:100-106,152-161` (VERIFIED) |
| No per-tenant concurrency limits, dashboards, or OTel in `flows_invoker`/`apps/mcp` | RECON |
| No production history: cheapest moment to replace | D10 |

### 1.3 What exists today (VERIFIED unless marked)

| Piece | Where / notes |
|---|---|
| Streams | `apps/api/modules/streams/` (~3.4k LOC RECON), `apps/worker/streams/` (~5.7k RECON). 16+ `stream_*` tables; triggers: manual `routes/streams.py:1006`, HMAC webhook `routes/hooks.py:23,48`; 21 node classes in `nodes/{actions,conditionals,transforms}`; `expression.py:113` AST whitelist |
| Connectors | `apps/worker/streams/connectors/manifests/`: 31 YAML. 13 PenguinTech-product (`darwin`, `waddlebot`, `waddleai`, `nest`, `killkrill`, `marchproxy`, `squawk`, `tobogganing`, `skauswatch`, `articdbm`, `iceshelves`, `elder`, `waddleperf`) + 18 DB. HTTP-manifest style; platform-wide `env_var` auth |
| Flows | `apps/api/modules/flows/` (~3.4k RECON), `apps/flows_invoker/` (~1.8k RECON). 11 `iceflows*` tables; triggers `routes/promotions.py:465`, `routes/hooks.py:29,51,74` (GitHub HMAC, GitLab token header); `executor.py:389`; Darwin review gate `review.py` fail-closed when required; Flows writes `stream_executions` directly (`calls.py:79-195`) |
| Job bus | `shared/jobbus/core.py`: `elder:jobs:<group>`, one JSON `data` field, idempotency key, XAUTOCLAIM, DLQ at 5 |
| UI | `web/src/pages/{StreamsList,PlaybookEditor,ExecutionsList,ExecutionDetail,ApprovalCenter}.tsx` (~920 LOC RECON); Flows has no UI; Runs absent |
| Diagrams | `apps/api/modules/diagrams/`: 18 `dg_` tables; `dg_shapes.elder_entity_id` numeric soft ref; `routes/collab.py` WebSocket + Redis pub/sub |
| Reference registry | `apps/api/models/references.py`; `apps/api/common/refs/registry.py`; `apps/api/api/v1/refs.py:95,191`. Registers tenant, organization, entity, identity, software, service, ipam_*, issue, project, milestone, document, page. NOT diagram, stream, flow |
| Licensing | `apps/api/common/licensing/*`; flag `elder.license-enforcement`; `@enforce_limit` observe-only |
| Egress carry-over | `net_guard.py:21` (private + CGNAT blocked); `gitops.py:62` scheme allowlist; `sandbox.py` (25 allowed binaries incl. `bash`, `pip`, `npm`; CPU 900 s, AS 2 GiB, nproc 4096, fsize 512 MiB; env scrub). `networkpolicy-flows-invoker.yaml` is plain NetworkPolicy; CGNAT exclusion RECON: no |

Issues (open): #168 Phase 4b (~50% RECON); #171 Phase 6 compliance/KMS (the credential gate cites it); #165 Phase 2 gaps (registry lacks diagrams/streams/flows). No `svc-workflows` issue or Streams/Flows spec exists.

---

## 2. Goals / non-goals / anti-goals

### 2.1 Goals

| # | Goal | Verified by |
|---|---|---|
| G1 | Tenant logic runs only as WASM components with no ambient authority | Negative tests: no socket, file, env |
| G2 | One gateway is the only way in or out; every crossing authorized | Network-policy test; denial metrics non-zero |
| G3 | Signed, versioned flowbundles and reusable `uses:` functions | Unsigned/tampered/unpinned refused |
| G4 | Canvas edits the graph that runs; diagrams and flows cross-link and convert | Round trip canvas → run → overlay |
| G5 | Carry forward Streams/Flows behavior; delete old code before v4.0 GA | Fixture suite; deletion gate 7.8 |
| G6 | Credentials never enter the sandbox; encrypted at rest; closes #171 | Secret scan of guest memory/logs/spans finds none |
| G7 | Independence: works with no other product | CI runs the suite with every other product absent |
| G8 | OTel logs + metrics + traces; usage metering | Telemetry gate; counts printed |
| G9 | Fail closed when the control plane is unreachable | Chaos test |
| G10 | **Melding with Elder data is the primary differentiator** | Scenarios 5A.8 (a)-(e) |
| G11 | **Scale to >100k events/s sustained aggregate without redesign** (D11), horizontally and vertically per role | Load gate 5B.11 |
| G12 | Flowbundles in Rust, JS/TS, Python at v4.0.x; Go, C#, Ruby at v4.1.x; compiled by us (D12, D13) | Conformance suite 5C.5 |
| G13 | Git `bundles/` marketplace sources (D15) | Scenarios k-m (5D) |

### 2.2 Non-goals

N1 A shared service for other products (D7). N2 Rewriting Elder's control plane. N3 A general container scheduler (runner jobs only for steps WASM cannot do). N4 Per-tenant Postgres RLS: ANSWERED, in progress on `feature/rls-tenant-isolation-v2` (no PR yet); dependency; gate = "RLS landed on `release/v4.0.X`"; Phase 0 proceeds on app-layer scoping (`apps/api/utils/tenant_scoping.py`, VERIFIED). N5 Changing the license tier model. N6 A paid/public storefront (5D is git sources only). N7 Native DB-protocol egress (R9). N8 The Runs module (Q16). N9 User-supplied prebuilt `.wasm` (D13).

### 2.3 Anti-goals

- A1 A user module reaching network, filesystem, or credentials directly.
- A2 A cross-product dependency (compile, runtime, deploy, data).
- A3 A silent fallback or stub (an unimplemented host call returns loud `not_implemented`).
- A4 A gate that cannot fail (`\|\| true`, zero-denominator pass, unmounted check).
- A5 A dual stack surviving to GA.
- A6 Secrets or PII in spans, labels, logs, bundles, or the sandbox.
- A7 Guest-supplied tenant, run, or identity fields.
- A8 Reproducing the Waddles shortfalls (5B.7, 5C.7).
- A9 `svc-workflows` as a second source of truth for Elder data.
- A10 Raw PII, or any direct database access, in the data plane.
- A11 The control plane (Quart/Postgres) in the per-event hot path.
- A12 Silent loss under overload (every shed is counted).
- A13 A build with network access while executing user code, or an unscanned/unsigned artifact loading.
- A14 Credentials, ingress, and untrusted-code execution sharing one image (D16).

---

## 3. Constraints

| Rule | Source | Implication |
|---|---|---|
| Data plane, security-sensitive, agents = Rust; control plane = Python 3.13 + Quart | `critical-rules.md`; `general.md` | Platform is Rust; Elder API stays Quart |
| No Go services; never Go for security-sensitive | Go Phase-Out | PenguinTech's own services only; Go is a USER language here (5C.6) |
| No PRC-origin deps, images, models, Actions | `general.md` | Crates, base images, toolchains, bundle dependencies |
| Immutable pins with verification | `critical-rules.md` | `Cargo.lock` + vendoring; git crates by SHA; images by digest; `wasmtime` exact |
| Rootless, stateless | Rootless Containers | Non-root, read-only rootfs; `.cwasm` cache is a disposable `emptyDir`. No STATEFUL EXCEPTION expected for svc-workflows roles; Valkey itself is stateful infra (how Elder deploys it: UNVERIFIED) |
| OTel logs + metrics + traces, `OTEL_*` env only | Observability | Plus penguin logging (8.1) |
| Flag per feature, default OFF; no stubs; flag-on real-path E2E | Feature Flags; `devops.md` | 7.5 |
| PII tokenization; opaque IDs; int enum codes; `village_id` on every object | Entity Identity & Typing; project rule | 5A.2 |
| Tenant hard boundary | `security.md` | Bound to every run, verified every hop |
| Service-to-service gRPC, short-lived signed JWTs, SPIFFE-ready | `backend.md`; `security.md` | 5.7 |
| REST gets OpenAPI 3.x; gRPC versioned | `backend.md` | Ingress HTTP spec; `workflows.v1` |
| Decision-gating caches seeded + lazy-loaded | `general.md` Red Flags | Grants, trigger, run-as caches |
| 90% coverage; default-deny Cilium; enforced Tetragon; PSA `restricted` | rules | 6.4 |
| Helm only; pinned build container | `devops.md` | First Rust crate in this repo (VERIFIED) |

---

## 4. Decisions (owner's)

| # | Decision |
|---|---|
| D1 | Move Elder's workflow and CI/CD functionality into its own image/container, `svc-workflows`: a Rust system layer that runs flows inside WASM environments |
| D2 | "Lucidchart + n8n/Zapier + GitHub Actions in one service presentation." The existing diagrams layer (IceCharts; 18 `dg_` tables, React Flow canvas, real-time collab over WebSocket + Redis) must be usable and cross-used within workflows. Closer to GitHub Actions than Ansible: CI/CD workflows and reusable "functions" |
| D3 | Flowbundles for different services covering ingest, processing, and actions — signed, versioned units. A reusable function is like a GitHub Action `uses:` |
| D4 | A unified gateway layer for ingress/egress out of the system, which we gate. It is the only way in or out |
| D5 | Take notes from the Waddles data plane (section 8) |
| D6 | WASM is for any "logic", so users can create custom modules with lowered risk to the system. Bundles are user/tenant-authored, untrusted code |
| D7 | Independence: all PenguinTech products are independent. Other products may trigger EXISTING workflows by webhook or API and may offer optional integration, but nothing depends on them. Works fully with none present; degrades gracefully if one is absent or down. Serves Elder only |
| D8 | (2026-10-27) Reuse the shared `penguin-libs` Rust crates Waddles uses; do not reimplement; do not depend on the Waddles repo (call E) |
| D9 | (2026-10-27) "The reason workflows was merged into Elder was to allow the 'melding' of assets data, issues / schedules, diagrams, and workflows together — meaning I should be able to easily look up and use the other resources and assets of Elder (i.e. quickly add a 'schedule event' as an action, kick off a workflow which sends an email to a known hostname and user, and launch workflows when an asset is added... also quickly convert a diagram into a workflow if needed." |
| D10 | (2026-10-27) "We never made it to prod with IceCharts product before the merger here." No real tenants, playbooks, run history, credentials, or webhook URLs exist to preserve. Elder v3.x never contained Streams/Flows (VERIFIED: tag `v3.2.2` has none of `apps/api/modules/streams`, `apps/api/modules/flows`, `apps/flows_invoker`; first commits 2026-07-10 `53ca62b`, `c09986a`); v4.0 has no tag yet. UNVERIFIED: that no internal deployment holds data that matters (owner says none) |
| D11 | (2026-10-27) "...we should be able to scale over 100k eps if needed to (a la would this work at a large enterprise like Uber?)." A design ceiling reached without redesign, not the day-one baseline. Scope and SLOs: Q1 |
| D12 | (2026-10-27) Flowbundles may be written in Python, C#, Node.js, Rust, Go, or Ruby, compiled to WASM components (component model, WASI 0.2, pinned wasmtime). **Sequencing (owner):** v4.0.x ships Rust, JS/TS, Python, first-class and conformance-gated. Go, C#, Ruby are a v4.1.x target: still committed scope, gated by a per-language spike + conformance + security gate. The v4.0 build pipeline is language-pluggable so 4.1.x languages are additive |
| D13 | (2026-10-27) "We provide a means to do the compilation so we can secure them similar to Waddles": build-as-a-service (untrusted build in an ephemeral K8s Job with build + publisher containers, no network during build, publish to the artifact bucket, approve, Ed25519 sign, verify before load). User-supplied prebuilt `.wasm` is NOT accepted for tenant bundles |
| D14 | (2026-10-27) Slow cold start of interpreter-embedded languages (Python, JS, later Ruby) is documented as a known characteristic, not a blocker; speed it up where safe. Snapshots "caused us some headache on Waddles", so they are not a baseline (5C.7) |
| D15 | (2026-10-27) A "marketplace" is simply a `bundles/` folder in a git repo in a format we expect (manifest, folder structure, signature), as just specified for Waddles (5D) |
| D16 | (2026-10-27) svc-workflows is a MULTI-CONTAINER service, not a single container. It scales horizontally AND vertically, per role. Roles are separated (by run mode and/or image) and communicate and divide work over Valkey Streams/queues, as Waddles does (5.3, 5.8) |

---

## 5. Architecture

### 5.1 Overview (one cell)

```
  Browser (canvas)           Other products / customers (webhook, bulk API)
        | REST                        | HTTPS
        v                             v
 +----------------+        +------------------------+
 | Elder API      |        | gateway: ingress       |  image svc-workflows-gateway
 | CONTROL PLANE  |        | verify, limit, mint,   |
 | definitions,   |        | dedupe                 |
 | authz, vault,  |        +-----------+------------+
 | summaries      |                    | XADD (run stream, partitioned)
 +--+-------+-----+                    v
    | gRPC  | outbox      +-------------------------------+     gateway: scheduler
    | async v events ---->| Valkey Streams (cluster, AOF, |<--- events, cron, timers
    | batched             | HA); lease + epoch fencing    |     (mints, no internet)
    |                     +---------------+---------------+
    |                                     | consumer groups
    |                    +----------------v---------+  UDS   +-------------------+
    |                    | engine pod: engine       |<------>| executor (wasmtime|
    |                    | gate, host-call broker   |        | holds no secrets) |
    |   elder.* gRPC     +---+--------------+-------+        +-------------------+
    +<-------------------+   | mTLS gRPC     | action streams
                             v               v
                  gateway: egress  <---- async actions
                  allowlist, pin, vault injection --> Internet / customer APIs

  gateway: controller -> K8s Jobs: runner (git/test), builder + publisher (compile, 5C)
  History sink: batches -> object storage.   Router: tenant -> cell (5B.4)
```

- Control plane = Elder API; data plane = `svc-workflows`. AUTHOR'S CALL A (revised: run/step history moves to the data plane's sink, AA).
- External traffic enters/leaves through gateway ingress/egress. `elder.*` calls take the engine's gated Elder adapter to Elder's internal API. AUTHOR'S CALL Q (Q11).

### 5.2 Control plane (Elder API)

Owns: definitions (graphs, flowbundles, triggers, schedules; schema changes need no shims, D10); authz (OIDC scopes, tenant, teams); identity and PII; credential vault (6.6); license and flags; signing (private key never in the data plane); run summaries and indexes (written in batches, never per event); the `/api/v1/streams/*` and `/api/v1/flows/*` facade the UI keeps using.

### 5.3 Roles, images, deployments — AUTHOR'S CALL I (replaces "one image, several modes"), AQ

One Cargo workspace, several images split along **trust boundaries** (Waddles runs ingest, process, action, bundle_executor, and egress_proxy as separate services). A run mode within an image exists only where roles share a trust level.

| Deployment (SPIFFE `spiffe://penguintech.io/<env>/svc-workflows-…`) | Image / mode | Holds | Network | Scales on |
|---|---|---|---|---|
| `gateway-ingress` | gateway / `ingress` | Per-source auth refs; `gateway-in` producer key | Public listener; Valkey (XADD only); control plane | RPS, connections, 429 rate |
| `gateway-egress` | gateway / `egress` | Vault access; credentials in memory per call; assertion key | Internet allowlist; vault; consumes action streams; mTLS from engine | In-flight egress, pool saturation, action-stream lag |
| `gateway-scheduler` | gateway / `scheduler` | `scheduler` producer key; trigger cache | Control plane; Valkey; no public listener, no internet | Event/timer lag |
| `gateway-controller` | gateway / `controller` | K8s permission: create/watch Jobs in one namespace | K8s API; object storage; control plane | Job backlog |
| `engine` pod | engine: containers `engine` + `executor` | `engine`: Valkey consume credentials, run-as Elder tokens. `executor`: nothing | `engine`: Valkey, gateway-egress, Elder internal API. `executor`: UDS only | Run-stream lag, executor saturation |
| `builder` Job | builder-`<lang>` (untrusted) + publisher (trusted) | build: nothing. publisher: bucket write | build: none. publisher: bucket + control plane | Build queue |
| `runner` Job | Allowlisted runner images | Nothing | gateway-egress only | Per-tenant quota |

Why split by trust boundary: (1) least privilege: credentials and external connectivity live only in the gateway image; the engine image, which executes untrusted code, has no credentials, no ingress, no K8s Job code, a minimal binary, and a tight Tetragon allowlist and NetworkPolicy, and a code path that is absent from an image cannot be leveraged; (2) different resource profiles and scaling signals per role; (3) independent rollout and drain per role; (4) smaller images, smaller attack surface.

- **Ingress vs egress (recommendation):** separate Deployments of the gateway image with different modes, separate NetworkPolicies/ServiceAccounts/Tetragon policies. Trade-off: separate images would drop credential-injection code from the internet-facing binary at higher CI cost (Q6).
- **Scheduler and controller:** gateway-image modes (platform code, no user code, no credentials), their own Deployments. The scheduler holds a minting key, so it never shares an image with the executor (Q6).
- **Executor container (call AQ):** "credential-less" is precise: the engine process necessarily holds internal Valkey consume and Elder identity; a guest escape must not reach them. So wasmtime runs in a separate `executor` container: no secrets, no ServiceAccount token, UDS peer-credential check to `engine`. Pod-level network policy is shared, which is weaker than Waddles' separate Deployments over mTLS; a separate Deployment is the option if network isolation is required, at a network hop per host call (Q6).
- Builder Jobs are ephemeral, not services. The controller creates them; build containers are the most hostile (they execute bundle build code).
- No role keeps durable local state. Per-role scaling: 5.9. Helm, CI, SPIFFE, Tetragon: 7.6.

| Alternative | For | Against |
|---|---|---|
| Single image, all modes | Simplest CI/ops | Credentials and the untrusted-code executor in one image: larger blast radius. Rejected for the gateway/engine boundary |
| Two images (gateway + engine) | Minimum split | Builder code and its toolchains would share an image with something |
| Per-trust-boundary images (above) | Least privilege | Three image families, per-role Deployments, builder Job templates, one chart |

### 5.4 Gateway traffic

**Ingress:** external webhooks (authentication per 6.5a over raw bytes before any parse or guest call; replay window; delivery-id dedupe); bulk intake for high-rate producers (per-batch signature, 5B.6); API triggers (scope `workflows:execute`); Elder-originated runs (UI, schedule, entity event, approval) come from the `api`/`scheduler` roles, not the public listener. Ingest flowbundles receive authenticated bytes and parse; they hold no socket or credential. Long-lived connections are out of v1 (a "poll" source is scheduler-driven). Other products may only trigger existing workflows.

**Egress:** WASM `http`, runner traffic (git, package fetch), notifications, outbound webhooks, marketplace git fetch all take the pipeline in 6.5. Operator-configured infrastructure endpoints are not subject to the bundle guard (provenance, not address range; Waddles §8.5).

### 5.5 Flowbundle model — AUTHOR'S CALL L

| Concept | Definition |
|---|---|
| Function | Smallest WASM component with typed input/output and declared permissions; signed, immutable by digest. `uses: <namespace>/<name>@<version>` |
| Flowbundle | Signed, versioned unit for one service integration: manifest + graph(s) + function pins + stage roles (`ingest`, `process`, `action`) + permissions + limits |
| `uses:` | Authoring may name a tag/range; publish resolves to an immutable digest and writes a lockfile; runtime accepts digests only |
| Scopes | Platform (Elder-shipped), tenant-private, marketplace-shared (5D) |

Manifest fields (builder owns the schema): `name`, `version`, `kind`, `stages`, `language`, `sdk_version`, `abi`/WIT world version, `uses[]`, `permissions` (`egress[]` host+methods, `secrets[]` names, `elder[]` ops, `diagram[]`), `limits` (timeout, memory, fuel), `inputs`/`outputs`, `license_feature`.

- Lifecycle (int codes): `draft → built → scanned → published → approved → active → revoked`. Consent shows hosts + methods, secret names, Elder ops, limits, scan status, ABI; approval records a `permission_hash`; runtime authorization derives from the approval record (Waddles D26). Widening needs re-consent; narrowing auto-approves with audit. Revocation reaches the data plane in about a second.
- v1 host capabilities (the gate runs first on each): `context`, `http` (via gateway-egress, secrets by reference), `elder` (5A.3), `diagram`, `log`, `clock`, `flags`, `emit` (via the scheduler role). Deferred: `kv`, `db`, `relay`. No `wasi:sockets`, no filesystem beyond an empty scratch, no `wasi:cli/environment`; the executor natively implements the refusing interfaces.

### 5.6 Canvas and graph model — AUTHOR'S CALL D

One canonical graph document: the canvas edits it; it is compiled, signed, and run. Semantic layer (nodes, edges; executed) plus presentation layer (x/y/w/h, style, annotation shapes; not executed). Today separate: `stream_nodes`/`stream_edges` vs `dg_shapes`/`dg_connectors`; builder designs the merge (Phase 4). Cross-use: diagram → flow via registry link; flow → diagram via host `diagram`; run overlay over the existing WebSocket + Redis channel (`elder:dg:{diagram_id}`); conversion 5A.7. Editing concurrency (collab sessions vs `stream_editor_locks`): builder picks one. Loop semantics of `while_loop`/`for_each`: UNVERIFIED, read first.

### 5.7 Identity, transport, trust

| Concern | Design |
|---|---|
| Machine identity | EdDSA short-lived JWT (≤1 h), `sub` = the Deployment's SPIFFE ID; SPIFFE-ready (mTLS/X.509-SVID first-class). Elder-side issuer UNVERIFIED |
| Control-plane calls | gRPC `workflows.v1`, `api_version` per request; async and batched (5B.2) |
| **Run envelope producers — AUTHOR'S CALL X** | Producers sign envelopes with their own EdDSA keys: roles `api` (Elder API), `gateway-in`, `scheduler`. The engine holds only public keys by role, verifies tenant/run/trace binding at every hop, never mints. Each role may mint only its envelope kinds. Failure = DLQ, never retried, counted. Valkey ACL: only those roles may XADD the run stream; the engine is consume-only on its own group. Signature granularity is a scale tunable (5B.8) |
| **Run-as principal — AUTHOR'S CALL Y** | Each run executes as a service identity scoped to the workflow's owning team and tenant. Effective scopes = consented permissions in the approval record ∩ the owning team's grants (narrower layers restrict, never expand). The triggering user/event is audit-only and supplies no authority. Per-user actions (if ever) use the user's seat tier; team features use the team's allocated tier. Resolves the confused-deputy claim in 6.2 |
| Fail closed | Grants missing/cache-miss/loader-error = deny; flags/license last-known, unseen OFF; cached-grant staleness bounded (builder) |

### 5.8 Communication: streams vs direct calls — AUTHOR'S CALL AP

Streams (via `penguin-spine`) carry run dispatch, async actions, events, fan-out, and results, as in Waddles. Synchronous host calls from a running module go over direct mTLS gRPC/HTTP2 with deadlines, as Waddles' `bundle_executor` dials its stage and egress goes to `egress_proxy`. Reason: a stream hop per host call adds latency and reply-correlation machinery and roughly doubles stream load at 100k eps.

| Interaction | Transport | Failure semantics |
|---|---|---|
| Ingress / scheduler → run | Partitioned run stream (XADD) | Producer acked only after XADD succeeds; `429/503` + `Retry-After` on backpressure; idempotency key; dedupe |
| Engine consume | Consumer groups; lease + epoch fencing | At-least-once; reclaim after idle; DLQ after max deliveries; idempotent steps |
| Engine → next step / async action | Per-flow action streams; gateway-egress consumer group | At-least-once; backoff; DLQ; dedupe key |
| Engine → history | Bounded buffer → batched sink | Bounded, counted loss window |
| Module `http` host call | Engine → gateway-egress, mTLS gRPC, per-call deadline | Returns typed error to module; no auto-retry unless idempotent; circuit breaker; `resource_exhausted` on backpressure |
| Module `elder.*` host call | Engine → Elder internal API, gRPC, deadline | `elder_unavailable` fail closed; retry reads with jitter; writes carry an idempotency key |
| Engine ↔ executor | UDS in the pod | Executor death = trip; call fails retryable |
| Controller → Jobs; results | K8s API; results to a stream | Job TTL; failure recorded |
| Control plane ↔ cell | gRPC, pull + push-invalidate | Bounded staleness |

### 5.9 Scaling model — AUTHOR'S CALL AR

| Role | Bottleneck | Scale signal | Min/max (placeholder) | Vertical knobs |
|---|---|---|---|---|
| gateway-ingress | TLS, connections | RPS, p99 ack, CPU, 429 rate | 3 / unbounded | Cores, connection and body limits |
| gateway-egress | Outbound pool, TLS, vault cache | In-flight requests, pool saturation, action lag | 2 / unbounded | Pool sizes, cores |
| gateway-scheduler | Event matching, timers | Event stream lag, due-timer backlog | 2 / ≤ partitions | Cores, trigger-cache memory |
| engine | WASM CPU, instance memory | Run-stream lag per partition, executor saturation | 2 / ≤ partitions | Executor instances per pod, pooling-allocator memory, cores per pod, fuel budgets |
| gateway-controller | Job creation | Job backlog | 2 (HA) / 4 | Small |
| builder/runner Jobs | Concurrency | Queue depth, per-tenant quota | Quota-bound | Job CPU/memory/disk limits |
| Valkey cluster | Memory, ops/s | Memory, ops, replication lag | Shards | Node size |

- Horizontal: stateless roles, consumer-group sharding, partitions, autoscale on lag (HPA/KEDA: UNVERIFIED availability), PDBs, graceful drain (hot-swap: load → verify → swap → drain → unload; Waddles spec §7.6, VERIFIED), cells for blast radius (5B.4).
- Vertical: per-role requests/limits; engine instance pooling sizing. Scale up while a pod's pooled-instance memory and per-core throughput stay efficient; scale out otherwise. Parallelism is capped by partitions (replicas × cores ≤ partitions); the fix for a ceiling is a higher partition count, not per-key migration (5B.3). Waddles' scale design has no NUMA guidance, so none is assumed (grep).

---

## 5A. Melding with Elder data

### 5A.1 Boundary — AUTHOR'S CALL R

| Rule | Design |
|---|---|
| System of record | Elder API for assets, issues, schedule items, identities, diagrams, documents. No copy beyond run-scoped context (A9) |
| Only path | `elder.*` host functions are the ONLY way a module touches Elder data: module → gate (deny-by-default) → engine Elder adapter → Elder internal API (gRPC; SPIFFE-ready mTLS + short-lived machine JWT scoped to the run-as principal and tenant). Tenant scoping, RLS (once landed), PII tokenization, and OIDC scopes then apply automatically |
| No direct DB | No Postgres credentials or read path in the data plane (A10) |
| Failure | Elder API unreachable: step fails closed with `elder_unavailable`; no cached fallback, no stub result |
| Scopes | Each op maps to an existing OIDC scope (`issues:read/write`, `infrastructure:read/write` VERIFIED pattern); checked at the gate (approval ∩ team grants) and by Elder API |
| Audit | Per call: tenant, run, module digest, op, object address, outcome. No PII |

Elder has gRPC stubs for auth, graph, organization only (`apps/api/grpc/generated/`, VERIFIED); no resource-CRUD service. Builder: thin internal gRPC facade over the service layer vs REST with a machine JWT (REST only if gRPC is unavailable).

### 5A.2 Addressing — AUTHOR'S CALL S

- Registry today: `references` table, `GET /refs/resolve`, `/refs/backlinks`; types in 1.3. Diagrams, streams, flows are not registered (VERIFIED).
- **Finding:** no `public_id` UUID column exists in `apps/` or `shared/` (VERIFIED by grep). The stable cross-system address is `village_id` (`TTTTTTTT-OOOOOOOOOOOOOOOO`: tenant hex + per-tenant sequential Redis counter, `shared/utils/village_id.py`): opaque to users but sequential and tenant-prefixed. The global rule wants UUIDs across boundaries.
- Call S: flows address Elder objects only by registry address `(module, type, village_id)`; never names, slugs, or numeric PKs. Every `elder.*` read re-checks tenant and scope, so enumeration yields nothing cross-tenant. A UUID `public_id` is an Elder-wide migration outside this spec (Q12).
- A hostname may be a search INPUT; it resolves to an address immediately and definitions store addresses. Prerequisite: register `diagram`, flow, flowbundle, `run`, and (if built) `schedule_item`. `elder.ref.resolve` returns no title for `identity`.

### 5A.3 `elder.*` surface (v1, curated) — part of call R

| Resource | Ops | Scope | Notes |
|---|---|---|---|
| entity (hosts, cloud resources) | get, search (typed filter: type, tags, provider, region, status, hostname input), create, update (incl. tags), link | `infrastructure:*` | Has `organization_id`, `tags`, `entity_metadata`, no `hostname` column (VERIFIED); canonical hostname field UNVERIFIED |
| issue | get, search, create, update, comment | `issues:*` | Structured fields only by default |
| schedule item | create, update, list | TBD | 5A.5; absent from the WIT until built |
| identity | get (opaque ref, type, status, team ids), `entity.owner` | UNVERIFIED | Never username, email, name, phone. Owner = `organizations.owner_identity_id` via `entities.organization_id` (VERIFIED); entity-level owner UNVERIFIED |
| diagram | get graph, create/update shapes | `diagrams:*` (UNVERIFIED) | Per-diagram scope |
| document / page | get, search (read-only) | `documents:read` | |
| ref, notify | resolve, backlinks; `send` (5A.4) | tenant | |

Typed ops only (no raw SQL or paths); tenant from the envelope, never arguments; explicit DTO outputs; page-size caps and per-op rate limits; no delete in v1. Free-text bodies (issue description, comments, documents) may hold PII that cannot be tokenized: reads return structured fields only unless the manifest declares a dangerous-tier `text` permission. Every op appears in `permissions.elder[]` and on the consent screen.

### 5A.4 PII boundary and the email action — AUTHOR'S CALL U

- **Constraint:** raw PII (name, email, username, phone, address) lives only in the Elder API server. `identities.email`/`username` (`apps/api/models/identity.py:85-86`) and alert recipient JSON (`alert_config.py:73`) are VERIFIED. `svc-workflows`, WASM memory, run history, logs, traces, queues, and DLQs never hold them.
- **Email action:** `elder.notify.send(channel=email, recipient=<identity address> | owner_of(<entity address>), template=<id>, vars=<non-PII scalars and addresses>)`. The flow passes opaque IDs. Elder API resolves the recipient, renders the template with display names resolved there, applies suppression and rate limits, and sends via an Elder-owned transport. Only an opaque `message_ref` returns. The address exists only in Elder API memory and the transport.
- A raw address as recipient is refused (`pii_in_argument`); external recipients use registered notification contacts held in Elder. Run history stores IDs, op names, outcomes; no response bodies by default. Rejected: SMTP from gateway-egress with a detokenize call (plaintext in gateway memory).
- **Gap:** Elder has no working email transport; `_send_email_notification` returns success without sending (`apps/api/services/webhooks/service.py:1014-1025`, VERIFIED). A real transport is a prerequisite (Q19).
- External trigger payloads may hold PII the platform cannot tokenize: stored per a per-trigger `payload_retention` (default: not stored); sensitive keys redacted in logs/traces.

### 5A.5 Schedules — AUTHOR'S CALL V

| Existing | State |
|---|---|
| `stream_schedules` (cron, tz, `next_run_at`) | Model only; no executor (VERIFIED) |
| `sbom_scan_schedules` (`models/security.py:118`) | Scan scheduling; not examined |
| On-call tables (`modules/services_oncall/models.py`) | People scheduling |
| Issue `due_date` (`issue.py:323`); project/milestone dates (`project.py:36-37,58`) | Dates on work items |
| Worker `aiocron` (`apps/worker/main.py:683-697`) | Precedent for a time executor |
| Calendar / schedule-event model | **None found** (grep); UNVERIFIED that no module has one |

- "Add a schedule event as an action" has **no target today**. Options: (1) issue with `due_date`; (2) on-call override/shift; (3) a new minimal schedule item (title, start/end or due, recurrence, registry links, owner team, `village_id`); (4) a flow cron trigger (a trigger, not an Elder event).
- Call V: recommend (3). Until Q7, Phase 2 uses `elder.issue.*` with `due_date` and a `schedule.due` trigger; `elder.schedule.*` is absent from the WIT, never stubbed.
- Time triggers are part of melding: cron (tz-aware), due dates, schedule start/end, waits. The Streams cron gap (Q14) is in scope in the `scheduler` role: fire key = (trigger, scheduled instant); missed-fire policy is the builder's.

### 5A.6 Entity events as triggers — AUTHOR'S CALL T

- Today: `shared/events.py::emit_event` XADDs to `elder:events:{domain}`. **Zero callers and zero consumers** (VERIFIED by grep). It is fail-soft (a Redis outage drops the event), uses a sync client in an async function, carries int IDs. Entity writes have ≥3 paths: API routes, direct DB upserts in the discovery worker (`apps/worker/discovery/service.py:428-443`), and connectors via `ElderAPIClient` (`apps/worker/utils/elder_client.py`). A common write layer: UNVERIFIED. `AuditService.log_modification` (`services/audit/service.py:213`) is a candidate hook (called on entity writes: UNVERIFIED). Reusable: stream naming, JobBus idempotency/DLQ patterns.
- Design: events `entity.created|updated|deleted|tagged|linked`, `discovery.found`, `issue.*`, `schedule.due`; payload = village_id, changed field names, non-PII subset. **Transactional outbox** (row in the same DB transaction), via a Postgres trigger or ORM/DAL hook covering all write paths including the worker; relayed to the cell's event stream. No lost events.
- Trigger definitions live in the control plane (tenant-scoped, owning team): event types + typed predicate filter on a whitelisted attribute set (type, tags, provider/account/region, status, org), flow digest, rate limit, dedupe window, `payload_retention`. The `scheduler` role consumes events, matches against its seeded + lazy-loaded cache, applies dedupe/loop guard/limits, and enqueues signed envelopes. The engine never sees raw events. Idempotency key = (event_id, trigger); rapid updates coalesce within the window.
- **Loop prevention:** every envelope carries `origin` (root run, causation depth, chain hash). Writes a run makes through `elder.*` tag their outbox rows with the causing run and depth. The matcher blocks a trigger from firing on events caused by its own flow within one chain (default), caps depth, counts every block, and auto-disables a trigger after N hits with an alert. Cross-flow chains need an explicit per-trigger opt-in.
- **Rate and burst:** per-trigger and per-tenant limits; bounded queues with backpressure to the matcher (pause consumption); batch-trigger mode (a list of addresses per run); discovery bursts (e.g. 100k events) coalesce (5B.9).

### 5A.7 Diagram → flow conversion — AUTHOR'S CALL W

- **Diagram-side prerequisites (not built):** `dg_shapes.elder_entity_id` is a numeric soft ref with no FK (`diagrams.py:130-135`, VERIFIED); icon sets are an unchecked item (`docs/design/inventories/icecharts.md:9`, VERIFIED); diagrams are not in the registry (VERIFIED); recon puts diagrams Phase 4 at ~70%. Needed: (1) register `diagram`; (2) entity-bound shapes as `binding` references by `village_id`, migrated off the numeric ref; (3) a shape-kind taxonomy (`shape_type`, `dg_shape_metadata`, `dg_node_metadata` exist, unread); (4) connector direction semantics (UNVERIFIED).

| Diagram element | Becomes |
|---|---|
| Entity-bound shape | Entity-typed node, default `lookup`; reviewer assigns `trigger` (lifecycle event for that entity/type/tag), `lookup`, or `action` |
| Connector between mapped shapes | Edge (control flow default; dependency-like types map to data edges) |
| Unbound typed library shape (e.g. "Server") | `search` node on that type, flagged for binding |
| Decorative text/groups | Annotation nodes (presentation layer) or dropped, reviewer's choice |
| Ambiguous/unresolved (unbound labelled shape, deleted/inaccessible binding, undirected connector, cycle, several roots) | Listed in a guided review; **never guessed**; the draft cannot be enabled while any remain |

- Output: a **DRAFT** flow in the canonical graph, flagged `draft`, linked back to the diagram (`derived_from`). Enabling needs human review and approval (consent for derived permissions) and `workflows.diagram-convert`. The draft inherits no permissions; converting needs read on the diagram and referenced objects.
- **One-shot fork by default.** Later diagram changes raise a "changed since conversion" indicator with a diff and an offer to convert into a new draft; an enabled flow is never mutated by diagram edits. Live bidirectional link rejected for v1: edits would silently change production behavior and break signing (Q8).

### 5A.8 Acceptance scenarios (flag-on, real-path E2E; counts printed; all other products absent)

| # | Scenario | Assertions |
|---|---|---|
| a | Asset discovered → flow → issue + schedule item | Entity created (API or discovery); outbox → stream → scheduler; exactly one run; issue linked to the entity; schedule leg per Q7 (`due_date` until built); registry links; history IDs only; replay creates no second issue; another tenant's identical asset does not trigger |
| b | User-run flow emails a known user about a known hostname | Search hostname → address; `elder.notify.send` with identity ref; email arrives at a real test transport; scan of svc-workflows logs, spans, metrics, queues, run history, executor memory for address and username: zero matches over N>0 artifacts; a raw address argument is refused |
| c | Small topology diagram → draft → review → enable → run | 3 bound shapes + 1 decorative; draft links back; an ambiguous item blocks enable; approval required; run succeeds; overlay on canvas; later diagram edit shows "changed", enabled flow unchanged |
| d | A flow that creates an asset does not retrigger itself | Exactly one run; `loop_guard_blocked` ≥ 1; depth cap holds for F→G→F; trigger auto-disables after N hits |
| e | Elder API down mid-run | Step fails `elder_unavailable`; nothing fabricated; counted |

---

## 5B. Scale & capacity

### 5B.1 Target (D11) and placeholders

Target: >100,000 events/s sustained aggregate, reached by adding partitions, cores, and cells, not by redesign; day-one baseline is lower. eps = trigger events accepted per second; step rate = eps × steps per run. Q1: per tenant, per cell, or aggregate; SLOs; test environment.

| Placeholder SLO (not owner-set) | Target |
|---|---|
| Ingest ack p99 | < 50 ms |
| Event → run start p99 | < 1 s |
| Native step p99 / WASM step p99 (warm, excl. module work) | < 5 ms / < 20 ms |
| Accepted-event loss | 0 (at-least-once; duplicates counted) |

### 5B.2 Control plane out of the hot path; durability tiers — AUTHOR'S CALL AA

No synchronous per-step gRPC checkpoint into Quart/Postgres. Hot run state lives in the data plane; durable history goes to an append-only batched sink; Postgres holds definitions, summaries, indexes.

| State | Where | Durable? | On loss |
|---|---|---|---|
| Definitions, grants, triggers | Postgres; cell read-only cache | Yes | Reload (seed + lazy) |
| Accepted event | Partition stream (Valkey cluster, persistence + HA) | Yes (replicated before ack, tunable) | Producer retries; dedupe |
| In-flight step state | Engine memory + stream pending list | Reconstructible | Reclaim, redeliver; idempotent steps (key = run, step, attempt) |
| Short waits (≤ 30 s placeholder) | Delayed entry in the partition | Reconstructible | Redelivery |
| Long waits (delays, schedules, approvals) | Wait record in Postgres (low volume) | Yes | Scheduler re-enqueues when due |
| Run/step history | Append-only batches in the sink | Yes, eventually | Bounded buffer; counted loss window |
| Summaries, indexes | Postgres via batched upserts | Yes | Rebuild from sink |
| Logs, metrics, traces | OTel | No | Sampled; outside the hot path's failure domain |

- Sink options (builder decides; a new datastore needs owner approval, Q15; the merge design fixed Postgres + Neo4j + Redis): object-storage segments + Postgres index (cheap, durable, slow ad hoc query); columnar/time-series store (fast query, new datastore, rates UNVERIFIED); partitioned Postgres (not credible at this rate).
- History policy: full detail for failures; success steps sampled or opt-in; counters always. 500k steps/s × 200 B = 100 MB/s ≈ 8.6 TB/day if every step were stored (UNVERIFIED sizes).

### 5B.3 Partitioning and fencing — AUTHOR'S CALL AC (Waddles design, not code)

Source: `waddles:docs/superpowers/specs/2026-09-28-dataplane-scale-design.md` (VERIFIED). It is sized on cardinality (streams, connections, digests); it states no events/s figure. **Implementation not found:** no `fenced_commit`, `lease:epoch`, or `N_PARTITIONS` in `waddles:core/`; `penguin-spine` @ `586b8dad` has no partition/lease code. Reuse the design; build the code (in spine or here).

| Waddles element | Use here |
|---|---|
| Fixed partitions (2048) via rendezvous hash as the transport unit | Key = hash(tenant, flow); count from capacity math |
| Ownership by Valkey lease (`SET NX PX`, renewal, jitter, claim cap) | Same |
| Hard self-eviction on a monotonic deadline | Same (GC-pause hole) |
| Server-side Lua epoch fencing: XADD hand-off, **XACK last**, dedup key | Same; scripts do not roll back, so ordering + dedup are the safety |
| Voluntary shed above 150% of average; never migrate hot keys, raise the partition count | Same |
| Fairness: rate limit → weighted fair dispatch → executor sub-ceiling → aggregate trip | Same |
| Lazy load, LRU by digest, decayed-score pinning, byte-capped pin budget, single-flight | Same (5C.7) |
| HPA on partition lag and tenant aggregate lag | 5.9 |

Valkey cluster mode; hash tags colocate a partition's stream and dedupe keys. Fencing does not make side effects exactly-once; at-least-once + idempotency is the contract.

### 5B.4 Cells — AUTHOR'S CALL AB

A cell is an independent deployment of all roles plus its own Valkey cluster and history sink. A control-plane directory assigns tenants to cells; a router sends ingress to the owning cell. Scale by adding cells or partitions; blast radius is one cell; a single deployment is one cell. Definitions replicate by pull + push-invalidate (read-only cache); no hot-path call crosses cells. Multi-region and residency are future: a cell is region-pinned with no cross-cell hot-path dependency. If D11 means 100k eps for one tenant, that tenant spans cells by (tenant, flow) hash; one hot flow cannot split without losing order (Q1).

### 5B.5 Capacity math (worked example; every figure UNVERIFIED unless marked)

| Item | Estimate |
|---|---|
| Load | 100k eps × 5 steps = 500k steps/s; 49 eps/partition average at 2048 partitions, 250 at 5× skew |
| Valkey | ~3 commands/event = 300k/s; ~100k ops/s per shard without pipelining: ≥4 shards, plan 8-16 |
| WASM steps | 40% = 200k/s × 1 ms = ~200 cores (Waddles measured **2-4 ms per `db` round trip, Python component, VERIFIED**); Rust modules at ~0.1 ms ≈ 20 cores |
| Native steps | 300k/s × 20 µs ≈ 6 cores |
| Stream memory | 60 s backlog = 6M × ~1 KB ≈ 6 GB across shards |
| Gateway-in | ~2 KB/event = 200 MB/s ≈ 1.6 Gbps; TLS at 100k req/s; batch intake cuts per-request cost |
| Envelope verify | EdDSA at 100k/s costs a few cores: per-batch signing if it binds |

Phase 0/1 spikes (single-partition throughput, pooled call cost per language, fencing overhead, Elder bulk lookup) replace these numbers.

### 5B.6 Backpressure, quotas, fairness at ingress — AUTHOR'S CALL AD

| Layer (cheapest first) | Behavior |
|---|---|
| Connection and request limits | Reject early |
| Per-source and per-tenant token buckets | `429` + `Retry-After` |
| Target-partition depth/lag check | `429`/`503` before XADD; never unbounded growth |
| Bulk intake | HTTP/2 or gRPC flow control; per-batch accept/reject counts |
| Weighted fair dequeue in the engine | Weights by tier; no starvation |
| Load shedding | Low-priority/bulk first; counted, audited, sampled to DLQ; never silent |

An ingest rate limit is a requirement (Waddles RECON: intake endpoints unmounted, no limit). Idempotency keys on every envelope; per-flow rate limits; loop guard.

### 5B.7 Hot path — AUTHOR'S CALL AE (Waddles gaps are requirements here)

- wasmtime pooling allocator and pre-instantiation; fresh memory per call kept but cheap.
- `.cwasm` cache keyed `{digest}-{wasmtime_abi}-{collector}`; lazy load + LRU + pinning (Waddles design: ~100-150 hot digests at 50 replicas, 250-entry LRU, ≥95% hit target; UNVERIFIED here).
- CPU fuel with epoch deadline. Waddles never built fuel (`fuel_used: 0` hardcoded, `waddles:core/bundle_executor/src/wire.rs:546,910`, `invoke.rs:809`), has `.cwasm` caching as a TODO (`lib.rs:27-30`), and parses pool knobs without using them (all VERIFIED).
- Batch/vectorized invocation: WIT `process-batch(list<event>)` for ingest/process stages. Most steps need no host call; host calls are multiplexed and batched.
- Other Waddles lessons: mount the ingress rate limit from day one; no stub returns success (Waddles links unhandled calls to non-retryable `not_implemented`, VERIFIED); do not assume gVisor; change Helm values, not `kubectl` overrides (RECON); finish the cut-over (RECON: Waddles has `.py` remnants); WASI has no threads, so `asyncio.run` fails (VERIFIED).
- Targets (placeholders): native step < 50 µs, warm WASM call overhead < 500 µs excl. module work, host-call round trip < 200 µs.

### 5B.8 Gateway, observability, autoscaling at scale — AUTHOR'S CALLS AG

- Gateways are stateless, horizontally scalable tiers. Egress: HTTP/2 and pooling; DNS cache holding **validated, pinned** IPs (TTL capped at the record TTL, revalidated per new connection); per-tenant and per-destination limits and circuit breakers; retries with backoff + jitter budgets; vault material in a short-TTL in-memory cache only, fail closed on vault outage (6.6).
- Observability: bounded label cardinality (no tenant/run IDs as labels), head sampling at ingress + tail sampling in the collector, log sampling, OTLP batching with bounded queues that drop oldest; telemetry never in the hot path's failure domain.
- Autoscale on lag (HPA/KEDA): verify metrics-server, an external-metrics adapter, and KEDA on MicroK8s and DOKS (UNVERIFIED).
- Envelope verification: per envelope or per batch (tunable).

### 5B.9 Elder data access at scale — AUTHOR'S CALL AF

`elder.*` calls are cached and batched with epoch-based invalidation. Decision-gating caches (grants, triggers, run-as scope) are seeded and lazy-loaded, never invalidate-only. Data reads are read-through with a short TTL, invalidated from the entity event stream. Elder side needs bulk APIs (`GetMany`, cursor `SearchPage`), per-tenant/per-cell lookup limits and a circuit breaker so flows cannot overload the system of record; a read replica for lookups is a builder option. Event bursts (a scan emitting 100k events): coalesce by (trigger, resource) window, batch-trigger mode, per-flow limits, backpressure to the matcher.

### 5B.10 Licensing (open)

Scale is a paywall axis (scale/structure, not features). Which limits map to tiers (eps, concurrent runs, nodes/cells, retention, compile minutes, marketplace sources) is Q2; nothing is decided. Note the tier matrix gives Free/Professional one backend node per service type and Enterprise multiple (`docs/superpowers/specs/2026-08-11-module-reorg-license-gating-design.md`, VERIFIED), so multi-node scale-out is Enterprise by that model.

### 5B.11 Load-test gate — AUTHOR'S CALL AH

From Phase 1, every phase exit runs a load test (generator: Rust or k6-style). Report counts (sent, accepted, `429`-rejected, acked, delivered, duplicates), never a bare pass; a zero denominator fails.

| Test | Pass criterion |
|---|---|
| Ramp to target | Sent = accepted + rejected; accepted = acked; zero accepted loss; p99 within placeholders |
| Scaling curve | Near-linear in cores/partitions/cells until a named bottleneck |
| Soak of the new engine (a test, not production soak) | No leak or drift over the agreed hours |
| Noisy neighbor | A hot tenant does not move healthy tenants' p99 beyond SLO |
| Backpressure | Overload yields `429`, not loss |
| Chaos | Kill an engine pod mid-run and the Valkey primary: no accepted-event loss |

Environments: local single-node alpha runs functional and small-load tests only; sustaining the target needs a multi-node cluster whose capacity is UNVERIFIED (dal2 beta offline as of 2026-08-21 per `testing.md`; now unknown). Q1.

---

## 5C. Languages, toolchains, compile pipeline, cold start

### 5C.1 Languages and toolchains (D12)

Maturity is evidence-based; an unverified cell is not "supported".

| Language | Release | Componentization | SDK / bindings | Build image | Evidence | Cost |
|---|---|---|---|---|---|---|
| Rust | v4.0.x | `cargo-component` 0.21.1, Rust 1.97.1, `wasm32-wasip2` (+ `wasm32-wasip1` pre-installed) | `waddles:sdk/waddle-sdk-rs` | `rust:1.97-slim-bookworm` by digest (VERIFIED `waddles:bundles/Dockerfile.core-bundles`) | Hermetic build 0.40 s; reproducible-build script (VERIFIED) | Small artifact (size UNVERIFIED) |
| JS/TS | v4.0.x | `componentize-js … --disable all` | `waddles:sdk/waddle-sdk-js` | By digest (version UNVERIFIED here) | Hermetic build 5.13 s (VERIFIED) | Ships a JS runtime in the component: size/instantiate UNVERIFIED |
| Python | v4.0.x | `componentize-py` 0.25.1 `… --stub-wasi` | `waddles:sdk/waddle-sdk` | By digest | Build 3.4-5.5 s; **21.6 MB; 3.3-4.5 s uncached vs 4.5-5.4 ms from `.cwasm`**; `asyncio.run` fails (needs `PollLoop`); executes bundle code at build (VERIFIED, Waddles spec R1/R2) | Largest artifact; needs the `.cwasm` cache and pooling |
| C# | v4.1.x | `componentize-dotnet` 0.8.0-preview00011 + NativeAOT-LLVM 10.0.0-rc.1.26306.1, .NET SDK 10.0.401 | `waddles:sdk/waddle-sdk-cs` ("Tier-1(spike)"; per-bundle adapter: wit-bindgen C# types are nominal) | By digest (VERIFIED) | Two C# bundles exist in Waddles (`csping`, `superpenguin-roll`). README: toolchain "still experimental"; spec D35 still says spike-pending (stale vs code). Build uses network for `dotnet restore` (VERIFIED; needs a vendored NuGet cache here). Executor conformance: UNVERIFIED | Preview/RC toolchain; size UNVERIFIED |
| Go | v4.1.x | UNVERIFIED candidates: TinyGo wasip2 component support; Go wasip1 + component adaptation + `wit-bindgen-go` | To be generated | To be chosen | No Waddles evidence (no Go bundle, no spec mention; VERIFIED) | Runtime/GC overhead UNVERIFIED |
| Ruby | v4.1.x | UNVERIFIED candidate: ruby.wasm (CRuby on WASI) + component adaptation | To be generated | To be chosen | No Waddles evidence (VERIFIED) | Embeds an interpreter: large, slow instantiate (expected) |

- v4.1.x prerequisites (not dropped): a per-language feasibility spike, the conformance suite, and the security gate. Toolchain provenance versus the PRC-origin rule is UNVERIFIED for every row and every transitive build dependency.
- **Slow start is documented, not a blocker (D14):** authoring docs and the limits table state that interpreter-embedded languages (Python, JS; later Ruby) have larger artifacts and slower cold start, with the measured Python numbers above.

### 5C.2 Contracts and SDKs — AUTHOR'S CALL AM

WIT worlds for `ingest`, `process`, `action` stages and a `function` world, versioned (Q13); the manifest per 5.5. Per language: bindings generated from WIT plus a thin idiomatic layer for typed host functions (`elder.*`, `http`, `log`, `clock`, `flags`, `diagram`). A shared conformance suite of "golden flowbundles" every language must pass (identical outputs on golden events, imports inside the world, limits enforced); a local dev/test harness; an authoring CLI.

### 5C.3 The compile pipeline is a security boundary — AUTHOR'S CALL AK

Mirrors Waddles §4.6/D27 (VERIFIED): untrusted `build` init container + trusted `publisher`, shared `emptyDir`, `backoffLimit: 0`, `activeDeadlineSeconds: 900`, TTL after finish. The pipeline is language-pluggable (D12).

| Stage | Trust | Does |
|---|---|---|
| 1 Ingest source | Trusted fetch, no user code | Pinned commit SHA via gateway-egress (5D) or upload; size caps; archive hardening (traversal, symlinks, bombs) |
| 2 Inert checks | Trusted | Manifest validation, semgrep, secrets scan, dependency audit, license check, forbidden constructs, PRC deny-list. **All pass before any user code executes** (Waddles D34). Each scanner reports items examined; zero = failure |
| 3 Dependencies | Trusted verify | **Vendored dependencies required**, verified against the lockfile hashes. Matches Waddles' decided v1 ("vendored deps required for sandbox builds; no mirror in v1", marketplace design Q6, VERIFIED). A gated, checksummed mirror pre-fetch is a later option. Rejects unpinned/unhashed deps, VCS/URL deps, install scripts, native extensions without a WASM build |
| 4 Build | **Untrusted** (runs build scripts, proc macros, source generators, import-time code) | Ephemeral Job, rootless, caps dropped, read-only rootfs + scratch, seccomp, CPU/memory/disk/time limits, **no network**, no secrets, toolchain image by digest with all components pre-installed, deterministic environment, per-language recipe with required flags (`--stub-wasi`, `--disable all`) |
| 5 Validate + publish | Trusted publisher | `wasm-tools validate`; world matches the declared stage; **imports restricted to the allowed WIT world per language, anything else rejected**; size cap; limits recorded; digest computed here, never in the build sandbox; SBOM + vulnerability scan; provenance (source, lockfile, toolchain digests, log digest) |
| 6 Approve, sign, publish | Control plane | `permission_hash` consent; Ed25519 sign; artifact + sidecar to the bucket; executor verifies before load |

- Retained for audit: source, build log, provenance, SBOM (retention: Q15 sibling). Quotas: per-tenant build concurrency and minutes; weighted fair queue; backpressure with ETA; dedupe by (source digest, toolchain digest).
- **Isolation floor — AUTHOR'S CALL AN:** untrusted builds need enforced default-deny Cilium + enforced Tetragon + hardened pods; gVisor/Kata optional (Waddles R3: gVisor unavailable on MicroK8s and DOKS; Elder clusters UNVERIFIED). An environment without the floor refuses untrusted builds and builds only platform-origin source. Alpha: Tetragon is off (`values.yaml` comment, VERIFIED), so tenant builds are disabled there.
- **No prebuilt path, even first-party — AUTHOR'S CALL AL:** first-party flowbundles use the same pipeline with a system auto-approve gate (Waddles' marketplace design unifies first-party into one pipeline). One path, one gate.
- The compile pipeline lands with the first language set (Phase 1 builds first-party bundles through it). Tenant upload stays last (call J).

### 5C.4 Build threat model (adds to 6.2)

Malicious source and build-time code execution: stage-4 sandbox, scan before execution. Malicious or confused dependencies, install scripts: vendored + hashed lockfile, namespace pinning. Toolchain compromise: digest-pinned checksummed images, reproducibility check, controlled updates. Resource exhaustion (zip/compile bombs, macro loops): size/time/CPU/memory/disk limits, quotas. Build sandbox escape: the isolation floor, ephemeral, no credentials. Artifact tampering: publisher-computed digest, signature, verify before load. A component importing outside its world: validator rejects, executor refuses unknown imports. Log injection or secret exfil through output: sanitized logs, no secrets in the environment.

### 5C.5 Acceptance (per-language conformance)

| # | Scenario | Assertions |
|---|---|---|
| f | Same "hello flow" built from source in each shipped language | Passes the conformance suite; identical outputs on golden events; counts per language |
| g | Out-of-world import | Rejected at publish and by the executor |
| h | Over-limit module (time, memory, fuel) | Trapped, counted, three-strike disable |
| i | Build attempts network access | Fails; no packet leaves |
| j | Tampered artifact or bad signature | Refused at load; previous version keeps serving |

### 5C.6 Repo-policy note — AUTHOR'S CALL AO

The platform stays Rust. The Go Phase-Out and "no Go for security-sensitive" rules govern PenguinTech's own services; Go is only a supported USER language (v4.1.x), and the Go toolchain is build tooling inside a pinned builder image. Q20.

### 5C.7 Cold start and snapshots (D14) — AUTHOR'S CALL AJ

Speed-ups in order of preference; snapshots are NOT a baseline dependency:

1. wasmtime pooling allocator and instance pooling.
2. Precompiled `.cwasm` cache pinned to wasmtime version and GC collector (Waddles built neither; requirements here, 5B.7).
3. Lazy bundle load plus a warm-keep policy (pinned hot set).
4. Language-level tuning: trim stdlib/imports, componentize flags.
5. Pre-initialization snapshots ONLY as an opt-in, per-language, off-by-default optimization behind `workflows.snapshot-preinit`, enabled only after the safety gate below.

**What went wrong at Waddles.** Waddles' repo docs contain no `wizer`/pre-init text (grep of `waddles:docs`, `core`, `sdk`, `bundles`); the evidence is in Waddles project memory notes and `.TODO`. Only what is cited is known; Q10 asks the owner for the rest.

| Problem | Source |
|---|---|
| Python builds are non-reproducible: `componentize-py` snapshots CPython's initialized heap, so `pyping.wasm` differs on every build (ASLR, identity-ordered objects, GC timing); Rust is byte-reproducible. The #517 reproducibility gate covers Rust only. Disabling ASLR needs a privileged build container, which the rootless rule forbids; the owner pre-approved no-snapshot builds, "accepting slower cold starts" | `~/.claude/projects/-home-penguin-code-waddles/memory/python-bundle-snapshot-fallback.md:11,15`; `waddles:.TODO:60`; `.TODO:14` (#521) |
| Rebuilding the same source gives a new digest for the same version, so the seeder refuses (`digest_conflict`); on 2026-10-15 only 3 of 86 bundles seeded, and a skipped bundle silently lost activation and grants | `…/memory/rebuild-in-place-digest-conflict.md:13` |
| Wizening keeps only host-import submodules imported eagerly at module load; a capability referenced only inside functions was absent at runtime, the SDK guard degraded to default, and the bundle silently returned "no response" | `…/memory/componentize-py-eager-wizen.md:3,20` |
| Module-level `random.randint` may freeze into the snapshot, making outcomes predictable | `…/memory/bundle-batch-2026-10-21.md:42` |
| A precompiled artifact built under a different GC collector fails to load (related pinning hazard) | `waddles:docs/superpowers/specs/2026-09-14-rust-data-plane-design.md:1687` |

**Snapshot hazards to guard against**

| Hazard | Guard |
|---|---|
| Captured non-determinism (RNG seeds, time, env) | Ban init-time randomness/clock/env reads; entropy canary test: a bundle reading random at init must differ per instance |
| Secrets or tenant data baked in: a snapshot is shared by digest across tenants, so a leak is cross-tenant | Build sandbox has no secrets or tenant data; scan init code and output; secret-canary test |
| Staleness vs wasmtime/runtime version or collector | Cache key includes both; mismatch discards and recompiles |
| Per-instance state sharing (identity-hash seeds, object order) | Each instance gets its own copy-on-write memory; determinism lint |
| Build non-reproducibility (Waddles' failure) | Byte-identical digest across independent rootless builds; no privileged ASLR changes |
| Capability tree-shaking (eager-import trap) | Import all capabilities at SDK top level; a real-wasm test per capability; guards fail loud |
| Silent degradation | No default-on-missing fallbacks (A3) |

**Safety gate before any opt-in (all required, per language):** (i) N independent rootless builds produce byte-identical digests; (ii) conformance plus a real-wasm capability-presence test; (iii) the entropy and secret canaries pass; (iv) a measured benefit over the `.cwasm` cache above a stated threshold (placeholder); (v) a per-bundle opt-in recorded in the manifest and consent; (vi) flag default OFF with a kill-switch; (vii) security sign-off.

---

## 5D. Marketplace sources (D15) — AUTHOR'S CALL AS

A marketplace source is a git repo with a `bundles/` folder in a format we validate; one mechanism serves first-party and external bundles, as in Waddles' design (`waddles:docs/superpowers/specs/2026-10-27-unified-bundle-marketplace-sources-design.md`, VERIFIED, with its connector/permission specs). Consistent with call L: a bundle publishes as a function or flowbundle by digest and is referenced as `uses: <source-namespace>/<name>@<version>`, resolved to a digest lockfile.

**Layout** (adopts Waddles' `bundles/{language}/{app}/`, VERIFIED):

```
bundles/{language}/{name}/
  bundle.yaml    required: name, version (semver), stages (ingest|process|action|function), language,
                 WIT world version, permissions {egress hosts+methods, secret names, elder ops, diagram ops}, limits
  src/           source files
  vendor/ + lockfile   required: vendored dependencies with hashes
  README.md      required
  bundle.sig     optional: detached signature over the source-tree digest
```

A format validator rejects unknown layouts and fields, symlinks, path traversal, and oversize trees (reject, never repair).

**Source kinds and trust tiers (call AS; Q9):**

| Kind | Trust | Approver | Auth |
|---|---|---|---|
| PenguinTech-official: the built-in source (this repo's `bundles/` at the release commit) and, optionally, a separate official marketplace repo | Highest; system auto-approve for built-in, PenguinTech review for the shared repo | PenguinTech | Public https |
| Tenant-private git repo | Tenant's own; pinned SHA + admin ack | Tenant admin | Credentials in the vault, read-only deploy key, injected by gateway-egress |
| Community / third-party | Highest scrutiny; opt-in by tenant admin; signature required | Tenant admin, after PenguinTech listing review (Q9) | Public https |

Waddles contrast: global-admin-only sources, tenant-private deferred, no git credentials in v1 (VERIFIED). Elder's owner decision includes tenant-private, so credentials go through the vault.

**Source authenticity:** git-native signed commit/tag by a key in the source's registered trust anchors and/or `bundle.sig`; required for official and community, optional for tenant-private. The primitive is the builder's choice. It is an attestation only and never replaces compiling on our infra, approval, or platform signing; the executor trusts platform keys only (Waddles `signing.rs`, VERIFIED).

**Flow:** (1) add source (repo URL + path + ref) → (2) pin to an immutable commit SHA; never a branch or tag at runtime → (3) validate layout → (4) inert checks and vendored-dependency verification (5C.3) → (5) compile on our infra → (6) permission diff + consent (`permission_hash`) → (7) platform-sign → (8) publish to the artifact bucket → (9) available as `uses:` by digest.

**Updates:** a new commit SHA is recorded as pending; the approved version keeps running; re-validate, re-build, permission-diff, re-approve. A permission increase always needs fresh consent; no auto-upgrade. A force-push that orphans a pinned SHA sets the source to error with the pin intact and alerts (Waddles §4.5).

**Sync and limits:** poll with jitter (cadence builder's, placeholder 15 min) plus an optional provider push webhook through gateway-ingress; per-source minimum interval, per-tenant source and concurrent-scan caps, backoff. **Git egress** goes through gateway-egress (https only, allowlisted hosts, SSRF guard, DNS pin, CGNAT excluded); no direct git egress from the controller (Waddles §4.3, VERIFIED). The fetcher runs `git` with no shell, `--depth 1`, no submodules, LFS, or hooks, https-only protocol; limits on clone bytes, file count, single-file size, path depth, wall-clock. The platform assigns a source-scoped namespace so a source cannot shadow another's or first-party names.

**Supply chain and audit:** dependency manifests checked against a central PRC/sanctioned deny-list; vendored dependencies hashed; audit rows for add/remove/rescan, SHA, scan counts, approvals, signatures (no raw credentials, no PII). Tier gating of sources: Q2.

Scenarios (k) add a source and publish a bundle by digest; (l) update with a permission increase is held for re-consent while the old version runs; (m) a malicious repo (symlink, traversal, oversize, unknown field, shadowed namespace, bad signature) is rejected with counts.

---

## 6. Security & threat model for untrusted user-authored modules

### 6.1 Posture

Assume the WASM sandbox will eventually be escaped. An escaped executor reaches only its own memory, a read-only scratch, and the engine's UDS (every call re-authorized against the module's grants). It holds no secrets or SA token and has no network path of its own beyond the pod policy. **Trust premise:** producers sign envelopes; the engine verifies and never mints (call X).

| # | Layer | Stops |
|---|---|---|
| 1 | Role separation by image: the engine image has no credential-injection, ingress, or K8s Job code; the executor container holds no secrets | Credential theft; cross-role leverage |
| 2 | Pod security: non-root, no escalation, drop ALL caps, `RuntimeDefault` seccomp, read-only rootfs, PSA `restricted` | Escalation toward the node |
| 3 | Default-deny `CiliumNetworkPolicy` per role | Lateral movement, internet |
| 4 | UDS peer-credential check engine ↔ executor; mTLS between roles | Impersonation |
| 5 | WASM isolation: no sockets, fs, env | Guest reaching anything not handed to it |
| 6 | Capability gate on every host import | Ungranted capability |
| 7 | Epoch deadline + fuel + memory + instances | CPU/memory DoS |
| 8 | Three-strike disable per `(bundle, digest)` | Repeat offenders |
| 9 | Enforced Tetragon per role | Native escape running tooling |

gVisor is not assumed (Waddles R3/D32); Kata is the named escalation (Q17).

### 6.2 Threats

| Threat | Mitigation | Residual |
|---|---|---|
| Cross-tenant read | Tenant bound per run, verified per hop; host-built scope; fresh instance per call; RLS when landed | Scope-construction bug; compile-fail test |
| Egress exfiltration; credential theft | Declared allowlist, consent, 6.5, tenant denylist; credentials never in sandbox, injected at egress, host-bound (6.6) | Exfil to an approved host; compromised gateway-egress |
| WASM escape | Layers 1-9 | Host-kernel syscall surface |
| Forged ingress; compromised ingress minting envelopes | 6.5a; per-role keys and envelope kinds; engine verifies against role keys | Leaked secret + second factor; key compromise |
| **Confused deputy** | Run-as principal; scopes = approval ∩ team grants; initiator audit-only (call Y) | None |
| Supply chain; malicious marketplace source | Signed, digest-pinned bundles; platform keys only; fail closed; 5C.4; 5D validator, pinned SHA, compile + consent | DB + signing key compromise; build sandbox escape |
| CPU/memory/log DoS; run amplification; event storms | 6.3; depth cap; loop guard; backpressure | Slow-but-under-limit modules |
| DNS rebinding; CGNAT | 6.5 steps 6-7; Cilium excludes CGNAT | None |
| **PII reaching the data plane** | `elder.*` returns no PII; Elder-side send; raw addresses refused; payload retention off (5A.4) | PII in free text or external payloads |
| Data plane reading Elder data; Elder API load from flows | Gate + Elder API recheck scopes and tenant; no DB creds; audit; per-tenant/cell limits, breaker (5B.9) | Data within the run-as grants |

### 6.3 Resource limits

Starting values from Waddles (master spec §7.3, VERIFIED); tunables, not commitments.

| Limit | Start | Ceiling | On breach |
|---|---|---|---|
| Wall-clock per module call | 2000 ms | 10000 ms | Epoch interrupt; trip |
| Linear memory | 64 MB | 256 MB | Allocation refused; trip |
| CPU fuel per call | measured | per tier | Trap; trip (required, 5B.7) |
| Executor concurrent calls | 32 | — | Short queue, then retryable failure |
| Egress rate per module | 10 rps, burst 20 | per tier | `rate_limited` |
| Egress body / timeout | 1 MiB / 5 s | — | Truncate / `timeout` |
| Host-call frame | 1 MiB | — | Fatal protocol error |

Run-level caps (builder values): steps per run, wall-clock, output/log bytes, host calls per kind, fan-out and trigger depth, per-tenant concurrent runs and queue depth (none today, RECON). A manifest value above its ceiling fails at publish; never clamped. Interpreter languages carry a documented slow cold start (5C.7); their limits are the same.

### 6.4 Network policy and Tetragon

Per role, default-deny `CiliumNetworkPolicy`, own ServiceAccount and SPIFFE ID. Gateway-egress `toEntities: world` excludes RFC 1918, link-local, loopback, **CGNAT 100.64.0.0/10**, multicast, reserved, IPv6 ULA/link-local, and metadata addresses (the flows-invoker policy does not exclude CGNAT, RECON). Tetragon (call G): enforced `TracingPolicy` on gateway, engine, and executor; no blanket exemption like `values.yaml:629-632`; runner and builder Jobs get named profiles.

### 6.5 Egress pipeline (gateway-egress)

Adapted from Waddles §8.2 (VERIFIED); + = Elder additions. Each denial has a reason code and counter.

| # | Check | Reason |
|---|---|---|
| 1 | Scheme `https` (git over https; others by explicit allowlist, carrying over `gitops.py:62`) | `scheme_not_allowed` |
| 2 | Clean parse; no embedded credentials; normalize IP-literal forms (decimal, octal, hex, IPv4-mapped, NAT64, 6to4) first + | `malformed_url` |
| 3 | Host in the run's approved `egress[]` | `host_not_declared` |
| 4 | Method allowed | `method_not_declared` |
| 5 | Tier and tenant denylist permit it + | `tier_denied` / `host_denylisted` |
| 6 | Resolve once; EVERY address passes (loopback, private, link-local, unspecified, multicast, reserved, **CGNAT**, ULA, metadata); deny if any fails | `ssrf_blocked_address` |
| 7 | **Pin the connection to the validated IP**; SNI/`Host` stay the original name | `dns_rebind_blocked` |
| 8 | Credential-host binding + | `credential_host_mismatch` |
| 9 | Per-module bucket and per-tenant concurrency | `rate_limited` |
| 10 | TLS 1.2+ verified chain | `tls_verification_failed` |
| 11 | ≤3 redirects, each re-running 1-8 | `redirect_off_allowlist` |
| 12 | Body and time caps | truncate / `timeout` |
| 13 | Audit: tenant, run, digest, host, method, outcome (no secrets, no PII) + | — |

Private-range hosts need an explicit per-tenant opt-in for already-allowlisted hosts; loopback, link-local, multicast, metadata stay blocked (Q18). Today's guard admits the rebinding gap (`net_guard.py` docstring); step 7 fixes it.

**6.5a Ingress authentication — AUTHOR'S CALL AI.** Webhook URLs and schemes may change freely (D10); adopt the stricter design. Providers that sign the body (GitHub `X-Hub-Signature-256`, `flows/routes/hooks.py:29`): HMAC-SHA256 over raw bytes + timestamp window + delivery-id dedupe + a mandatory second factor (source CIDR allowlist or bearer/mTLS). Providers that cannot sign the body (GitLab token header, `hooks.py:51`): secret-token header compared in constant time + source allowlist + replay/idempotency from the provider's delivery id when present (header names UNVERIFIED) + per-source rate limit. Secrets are per source, never reused, stored in the vault; hook URLs use an unguessable per-source identifier.

### 6.6 Credentials (ANSWERED design; resolves #171, Phase 3)

- Credentials never enter the sandbox, executor, runner pod, log, span, metric label, or a response the guest sees. A module names a secret by reference; gateway-egress resolves and injects it just before sending and scrubs reflections. Unresolvable → `secret_unresolved`, non-retryable.
- **Envelope encryption:** per-tenant data keys (DEKs) wrapped by a platform-managed master key (KEK) held in the platform secret store (Vault / Sealed Secrets / External Secrets Operator per `security.md`); at-rest encryption mandatory; customer-managed/external KMS is the Enterprise upsell on top. **Rotation:** KEK rotation re-wraps DEKs; DEK rotation re-encrypts lazily on next access. Plaintext exists only at the gateway-egress injection step, in memory, never logged.
- The vault is a hot dependency of gateway-egress: short-TTL in-memory cache only; fail closed on vault outage. #171's gate is this work. Builder decides the KMS interface.
- **Credential-host binding (call M):** each credential carries the hosts it may be sent to; a token bound to one host is never sent to another even if a module allowlists it. Credentials are per-tenant; the connector manifests' platform-wide `env_var` credentials (VERIFIED) are dropped. Git credentials for tenant-private sources (5D) live in the same vault.
- Today `iceflows_credentials.access_token` is plaintext, writes return 501, and webhook secrets are plaintext columns (no encryption evidenced). With no production history (D10) nothing migrates.

### 6.7 Supply chain, signing

Ed25519 over a length-prefixed, domain-separated payload binding id, version, digest, approval id; the executor verifies the sidecar on every load before instantiation (missing/invalid = refuse). The control plane signs at approval; the data plane holds public keys only; unset = fail closed at startup; `kid` rotation with overlap. The publisher computes digests, never the build sandbox; the control plane re-hashes and audits disagreement. The engine states the expected digest, so the bucket never decides what runs. `uses:` pins by digest. No prebuilt `.wasm` (D13, call AL).

### 6.8 Runner jobs (heavy steps) — AUTHOR'S CALL C

Steps WASM cannot do (git clone/merge/push, test subprocesses, image builds) run as short-lived K8s Jobs the controller creates and the gateway gates. Precedent: dal2alpha runs ARC runner pods (RECON). The owner said "WASM for any logic": Q3.

- One Job per step; TTL; deleted after results are collected. Input: signed job descriptor (tenant, run, step, allowlisted image digest, argv).
- No credentials and no ServiceAccount token; git credentials injected by gateway-egress. Cilium: egress only to gateway-egress + DNS.
- Rootless, caps dropped, `RuntimeDefault` seccomp, read-only rootfs + workspace `emptyDir`, PSA `restricted`; allowlisted pinned images; dedicated Tetragon profile.
- Limits start from today's rlimits (CPU 900 s, AS 2 GiB, nproc 4096, fsize 512 MiB). The binary allowlist (`DEFAULT_ALLOWED_BINARIES` + env scrub) is a starting point, not the boundary: `bash`/`pip`/`npm` run arbitrary code. The boundary is pod isolation + no creds + gated network.

Runner jobs execute native code: the highest-risk surface (R1). Git over the gateway with credential injection is feasible in principle; UNVERIFIED for push; a token in the pod would conflict with "no creds" (Q3).

### 6.9 Multi-tenant isolation and fairness

Tenant is bound to every run and host call (host-built scope). Per-tenant fair share and concurrency caps (5B.6); a dedicated executor pool per tier is a builder option (Q2). Precompiled artifacts shared by digest are fine (code, not data); instance memory is never reused. Telemetry carries UUIDs in logs/spans, never as metric labels.

---

## 7. Migration plan — replace, not strangle

### 7.1 Strategy — AUTHOR'S CALL F (revised on D10)

- No production history, so: no shadow-run, no production soak, no recorded production parity corpus, no rotation notices, no run/credential data migration, no long-lived dual stack.
- Keep: (i) the existing Streams/Flows tests (~6.5k LOC, RECON) as a behavioral fixture suite for the node types and promotion semantics carried forward; (ii) a short flag-gated cutover on the v4.0.X line, with the old in-process engine (`apps/worker/streams`, `handle_streams`) and `apps/flows_invoker` **deleted before v4.0 GA** (no half-wired old+new at GA); (iii) schema changes without back-compat shims. Dev/demo/seed graphs are regenerated or re-seeded; webhook URLs and schemes may change.
- Flags develop new beside old until the fixture suite passes; then flip and delete within the release line. The role split exists from Phase 1; there is no monolith first. Untrusted upload stays last (call J). Phases 2, 3, 5 shrank accordingly.

### 7.2 Phases

| Phase | Scope | Exit gate |
|---|---|---|
| 0 Prereqs + spikes | RLS landed on `release/v4.0.X` (or app-layer scoping); `village_id` on child tables; register diagram/flow/run in the registry; `workflows.v1` + Elder internal API contracts; first Rust workspace + pinned build container + CI + the three image families building empty; penguin-libs consumption (8); event outbox design; Q7; infra assumptions verified (7.9). Spikes: single-partition throughput, fencing, pooled wasmtime cost per Tier-1 language, Elder bulk lookup | Owner sign-off on Q1-Q6; CI builds empty gated crates and images; spike numbers replace 5B.5 |
| 1 Role skeleton + scale spine | Gateway image (ingress, egress, scheduler, controller) and engine image (engine + executor) as separate Deployments; partitions + lease + fencing via spine; pool, `.cwasm` cache, lazy load, fuel; ingress rate limit + backpressure; batched history sink; read-only `elder.*` + gate + run-as; outbox → scheduler → run with loop guard; compile pipeline for Rust/JS/Python building first-party functions; built-in marketplace source; load harness | Flag-on E2E with counts; telemetry gate; baseline load test; anti-goal failing-test proofs; scenarios d, e, f-j |
| 2 Streams replacement | The 21 node types; triggers (manual, webhook, cron, entity events); `elder.*` writes; `elder.notify.send` with a real transport; connectors as optional bundles | Fixture suite; scenarios a, b; scaling-curve test |
| 3 Flows replacement | Runner jobs; promotions and approvals; credential vault + gateway injection (#171); Darwin-style gate as an optional step; Flows→Streams engine-native | Fixture suite; runner negative tests; no plaintext credential column |
| 4 Canvas, conversion, upload | Graph unification; registry links; run overlay; diagram→draft conversion with diagram prerequisites; marketplace sources beyond built-in (tenant-private, community); **tenant upload last** | Security review; consent; scenarios c, k-m; noisy-neighbor and chaos tests |
| 5 Cutover + deletion before GA | Flip flags; delete old engine, `flows_invoker` image/CI/Helm/NetworkPolicy/Tetragon exemption; drop `stream_custom_modules` | Deletion gate 7.8; production soak not applicable (D10) |
| v4.1.x (post-GA) | Go, C#, Ruby: per-language spike, conformance, security gate; additive to the pluggable pipeline. Optional snapshot opt-in after its safety gate | Per-language gate |

Anything with no consumer yet (e.g. tenant upload) may slip past GA without blocking the deletion gate.

### 7.3 Behavioral parity (fixture suite)

Count-verified; a zero denominator fails. Existing tests for each carried-forward node class pass against the new implementation (N examined, N passed). Triggers: manual, webhook (new scheme), signature-failure and replay. Promotions: pipeline → stages → tests/calls/reviews/approvers semantics, fail-closed review preserved. Security carry-over: SSRF, CGNAT, git scheme allowlist, rlimits, env scrub reproduced and tested. Ops: dashboards, alerts, OTel on all roles. Not carried forward: promised-but-never-built nodes (gRPC, MCP, cloud, code, ask_ai).

### 7.4 Streams' 21 node types

Proposal; the builder verifies each node by reading it (internals UNVERIFIED).

| Category | Target | Why |
|---|---|---|
| Control flow: `comparisons`, `logic_gates`, `if_then`, `switch`, `for_each`, `while_loop` | Native engine built-ins, typed, non-Turing-complete, with run caps (call N) | Graph primitives, not user logic |
| Data: `filter`, `json_transform`, `merge`, `split`, `delay` | Native built-ins (`delay` = scheduler wait) | Deterministic, bounded |
| Free-form `expression` | First-party WASM function, never in the engine process | User text; an interpreter bug must not be engine RCE |
| `http_request`, `webhook_out`, `log` | Host `http` via gateway-egress; host `log` | One egress path |

Call N tension: D6 says "WASM for any logic"; the author reads it as tenant-authored logic, with control-flow primitives native and trusted. Connectors (31): optional bundles; the 13 PenguinTech-product ones are never core (absent product = inert, no startup/health dependency, D7). DB connectors assume an HTTP `database-ops` backend whose existence in Elder is UNVERIFIED (R9).

### 7.5 Flag plan

Elder's existing flag is `elder.license-enforcement` (VERIFIED); the brief specifies `workflows.*`; builder reconciles the prefix with `{product}.{feature}` first. All default OFF.

| Flag | Gates |
|---|---|
| `workflows.engine`, `workflows.gateway-ingress` | Data plane receives dispatched runs; external ingress |
| `workflows.streams-on-engine`, `workflows.flows-on-engine` | Execution routed to `svc-workflows` |
| `workflows.elder-resources`, `workflows.entity-triggers` | `elder.*` host functions; entity-event triggers |
| `workflows.diagram-convert`, `workflows.canvas-graph` | Conversion; unified editor |
| `workflows.custom-modules`, `workflows.marketplace-sources` | Tenant upload of untrusted flowbundles; non-built-in sources |
| `workflows.snapshot-preinit` | Snapshot opt-in (5C.7), per language |
| `workflows.disable-engine` | Opt-out kill-switch until legacy deletion: unseen/OFF = `svc-workflows` is the engine, ON = legacy fallback; deleted in Phase 5 |

Flags fall back to last-known cached value; unseen = OFF; a flag outage never crashes a run.

### 7.6 Deployment touch points (VERIFIED paths)

| Touch point | Change |
|---|---|
| `.github/workflows/docker-build.yml` (~43-44, 116, 230) | Three image families in the matrix: `svc-workflows-gateway`, `svc-workflows-engine`, `svc-workflows-builder-{rust,js,python}` + publisher (one more builder image per 4.1.x language). Remove `flows-invoker` at Phase 5 |
| `scripts/deploy-beta.sh:128` `IMAGES` | Add the entries |
| Helm (`k8s/helm/elder`) | One chart: per-role Deployments (`gateway-ingress`, `gateway-egress`, `gateway-scheduler`, `gateway-controller`, `engine`), PDBs, HPA/KEDA objects (availability UNVERIFIED), builder/runner Job templates. Reference: Waddles' chart `waddles:k8s/helm/waddlebot` (name per its D22) |
| Per role | `CiliumNetworkPolicy`, ServiceAccount, SPIFFE ID (`spiffe://penguintech.io/<env>/svc-workflows-gateway-ingress`, `-gateway-egress`, `-gateway-scheduler`, `-gateway-controller`, `-engine`, `-builder`), Tetragon policy; graceful drain |
| `apps/flows_invoker/Dockerfile` | Pattern reference only (pinned digest, USER 1000); deleted in Phase 5 |
| Tetragon exemption `values.yaml:629-632` | Not extended; removed at Phase 5 |
| Environments | Namespace = product name only; CI publishes beta and above |

CI cost: three image-family matrix entries plus per-language builders; three role families of Deployments plus Job templates; one chart.

### 7.7 Data migration

Almost none (D10): dev/demo/seed definitions are regenerated or re-seeded; `stream_schedules` re-seeded; `stream_custom_modules` dropped; new tables via Alembic without shims; no credential or run-history migration, no rotation notices.

### 7.8 Deletion gate (before v4.0 GA)

Fixture suite and scenarios a-e green; no import of `apps.worker.streams`, `apps.flows_invoker`, or the old handlers; old images, CI entries, Helm components, NetworkPolicy, and Tetragon exemption removed. "Agreed soak period": not applicable (D10); revisit if production use appears before GA.

### 7.9 Assumed infrastructure — verify per environment — AUTHOR'S CALL Z

| Assumption | Needed for | Status | Task |
|---|---|---|---|
| S3-compatible object storage | Signed artifacts, history sink, build records | UNVERIFIED (merge design lists `minio` as optional) | Phase 0 |
| Cilium on the cluster running network-policy negative tests | G2, 6.1 | UNVERIFIED; flows-invoker uses plain NetworkPolicy | Phase 0 |
| Tetragon on the E2E cluster | 6.1 layer 9, build floor | Off on alpha (VERIFIED comment); default ON beta/gamma | Run off-alpha |
| Valkey cluster mode, AOF/RDB, HA | 5B | UNVERIFIED Elder config | Beta/DO required; alpha single-node OK |
| Valkey per-role ACL users | Call X | UNVERIFIED | Phase 1 |
| HPA/KEDA + metrics adapter | 5.9 | UNVERIFIED | Verify on MicroK8s and DOKS |
| Runner/build isolation floor | 6.8, 5C.3 | UNVERIFIED | Q17 |
| Machine-JWT issuer | 5.7 | UNVERIFIED | Phase 0 |
| Real email transport | 5A.4 | Missing (stub, VERIFIED) | Phase 2 |
| Multi-node cluster for load tests | 5B.11 | UNVERIFIED | Owner (Q1) |

---

## 8. Waddles reuse notes

Waddles is a reference and a source of lessons; `svc-workflows` never depends on the Waddles repo (D7). Reuse is through `penguin-libs`, a shared library repo, not a product (D8).

### 8.1 Already in penguin-libs (git dependencies)

| Crate | Path | Purpose | Waddles pin | Applies |
|---|---|---|---|---|
| `penguin-spine` | `packages/rust-spine` | Valkey Streams client (XADD/XREADGROUP/XACK), consumer groups, reclaim. Its two client-connection-separation rules (dedicated blocking XREADGROUP connection vs shared admin connection; Waddles spec §5.7) are the connection management | 0.1.0 @ `586b8dad` (VERIFIED); tip 0.2.0 @ `90060a9` (RECON) | Yes; no partition/lease/fencing code in 0.1.0 |
| `penguin-bundle-host` | `packages/rust-bundle-host` | Host-API frame codec + manifest validator; no network | 0.2.0 @ `b063b7ba` (VERIFIED); `bundle_compiler` pins 0.1.0 @ `69f77b1a` (RECON) | Codec yes; the validator targets Waddles `bundle.yaml` v2, generalization UNVERIFIED |
| `penguin-logging`, `penguin-licensing` | `packages/rust-logging`, `rust-licensing` | Logs + OTel; license/flag client | 0.1.0 @ `69e740ce`, `f642e069` (RECON) | Yes |
| `rust-rpc` | `packages/rust-rpc` | Name suggests gRPC helpers | Not in Waddles pins | UNVERIFIED |

"The connection library" the owner mentioned: no crate has that name; best match is `penguin-spine`. Owner to confirm (Q5). All are git-pinned, not on crates.io; `b063b7ba` and `586b8dad` exist in the local clone (VERIFIED).

### 8.2 Still in the Waddles repo: extract or reimplement

Extract (generic core; Elder-specific parts become config or its own implementation): `bundle_executor` (engine, `signing.rs`, `wire.rs`, `invoke.rs`; host capabilities are Elder's own), `bundle_capability_gate` (with a pluggable permission catalog), `egress_proxy` + `egress_assertion` + the `bundle_host_http` guard, `service_auth` (EdDSA machine JWT, `TrustBundle` trait; Elder needs its own issuer). Do not extract: `hub_client` (Waddles-specific; write an Elder control-plane gRPC client) and `bundle_active_set` (reads Waddles' schema; its header says same-repo path dependency; reimplement against Elder's control plane). Fixing the 5B.7 shortfalls during extraction benefits both products. Extracted crates contain no product code and never require another product.

### 8.3 Reuse vs reimplement — AUTHOR'S CALL E (revised on D8)

| Option | For | Against |
|---|---|---|
| 1 Depend on the Waddles repo | No extraction | Violates D7. Rejected |
| 2 Extract Waddles-resident crates to penguin-libs; consume all as shared crates (**recommended; matches D8**) | Fix once, both inherit; one copy of security code | Waddles must migrate; generalization cost; shared cadence; on Elder's critical path |
| 3 Reimplement in `svc-workflows` | Fast, tailored | Two copies of security code; contradicts "contribute to penguin-libs first" (`backend.md`). Fallback if extraction stalls |
| 4 Hybrid: consume what exists; extract incrementally; reimplement only product-specific | Unblocks Phase 1 | Interim copies must be deleted |

Plan: consume 8.1 from Phase 0; extract `service_auth`, gate, executor core, egress guard/proxy/assertion in that order; reimplement `hub_client` and `active_set` equivalents. Use the newest released commit per crate (read its changelog), pinned by full SHA, not Waddles' older pins.

### 8.4 Dependency handling and risk

**`penguin-spine`, `penguin-bundle-host`, `penguin-logging` are not on penguin-libs' default branch.** `origin/main` has only `rust-licensing` and `rust-rpc` among Rust crates (VERIFIED); the others live on release branches (`release/rust-bundle-host/v0.1.x`, `v0.2.x`, `release/rust-logging/v0.1.x` seen in the local clone; `release/rust-spine/v0.1.x` RECON); the local clone is on a stale feature branch. Consume by full commit SHA, vendored, never a branch. Risks: force-push or deletion orphans a SHA; cargo-deny/audit must cover git sources; skew. Owner options: promote to `main`, tag, or publish (Q5). First Rust workspace in Elder: layout is call P.

### 8.5 Job-bus compatibility

- Elder JobBus (`shared/jobbus/core.py`): `elder:jobs:<group>`, `...:results`, `...:dead`; one JSON `data` field with int `tenant_id`; idempotency SET NX 24 h; XAUTOCLAIM; DLQ at 5 deliveries. Waddles spine / scale design: partition streams + per-app work streams, envelope with binding MAC, pending list + XAUTOCLAIM, max deliveries to DLQ, MAXLEN.
- AUTHOR'S CALL K (revised): `svc-workflows` consumes a dedicated partitioned namespace (e.g. `elder:wf:*`), not `elder:jobs:*`; legacy groups run untouched until cutover; never two consumers on one stream.
- Producer: Elder's Python control plane enqueues through the penguin-dal Valkey stream backend (`python-dal/src/penguin_dal/stream/valkey_streams.py`, RECON; Elder pins `penguin-dal==0.3.0`, Waddles 0.4.0; introducing version UNVERIFIED) or a `JobBus` adapter. Whether spine is generic over envelope contents, and the deployed Redis/Valkey version, are UNVERIFIED (read in Phase 0). Waddles' Python `core/workflow_core_module` was not reviewed and is not reusable under D7.

---

## 9. Author's calls

Every row: AUTHOR'S CALL — owner to confirm, unless marked.

| ID | Call | Note |
|---|---|---|
| A | Control plane = Elder API (definitions, authz, identity, summaries); data plane = `svc-workflows`; fails closed. **Revised:** run/step history and hot state live in the data plane's sink (AA) | Owner confirmed original framing; D11 forces the revision |
| B | Modules get no direct net/fs; gateway authorizes and injects credentials; resolves #171 | Owner confirmed |
| C | Heavy CI steps run as sandboxed runner jobs; WASM for logic/glue | Q3 |
| D | Canvas = editor on one graph model | Q4 |
| E | **Revised (D8):** reuse penguin-libs crates now; extract Waddles-resident crates; reimplement only product-specific | Q5 |
| F | **Revised (D10):** replace, not strangle; delete old engine before GA; fixture suite; no shims | — |
| G | Rust (tokio/axum), wasmtime + WASI 0.2, exact pins; OTel + penguin logging; RLS dependency; SPIFFE-ready JWTs; rootless, stateless; Cilium default-deny; enforced Tetragon | — |
| H | Licensing for custom modules, concurrency, run/scale limits, marketplace sources left open | Q2 |
| **I** | **Replaced (D16):** one workspace, images split by trust boundary: gateway (`ingress`, `egress`, `scheduler`, `controller` as separate Deployments), engine (`engine` + `executor` containers), builder family (Jobs); runner Jobs | Q6 |
| J | Untrusted tenant upload ships last | — |
| K | **Revised:** dedicated partitioned streams via `penguin-spine`; Elder API sole writer of domain tables; no Postgres in the data plane | 8.5 |
| L | Function + flowbundle taxonomy; `uses:` resolves to a digest | — |
| M | Credential-host binding | Beyond Waddles |
| N | Structured control-flow/data nodes native; free-form expressions and tenant logic in WASM | Tension with D6 |
| O | Darwin review gate becomes an optional connector step; fail closed only when a tenant marks it required | Today blocks when required and absent (VERIFIED) |
| P | Cargo workspace `apps/svc_workflows/` in the Elder repo | Separate repo alternative |
| Q | **Revised:** external traffic via gateway; `elder.*` via the gated Elder adapter | Q11 |
| R | `elder.*` is the only path to Elder data; curated typed surface; `elder_unavailable` fails closed | 5A.1, 5A.3 |
| S | Address Elder objects by registry `(module, type, village_id)` | Q12 |
| T | Entity events via transactional outbox → stream → scheduler; loop guard, dedupe, limits, batch mode | 5A.6 |
| U | Email sent Elder-side; flows pass IDs only; raw addresses refused | Q19 |
| V | Minimal first-class schedule item recommended; interim via issue `due_date`; time triggers in `scheduler` | Q7, Q14 |
| W | Diagram → draft flow: one-shot fork, guided review, link back, human approval | Q8 |
| X | Producer-signed envelopes (roles `api`, `gateway-in`, `scheduler`); engine verifies, never mints; Valkey ACLs | 5.7 |
| Y | Run-as principal scoped to the owning team; scopes = approval ∩ team grants; initiator audit-only | 5.7 |
| Z | Assumed-infrastructure table | 7.9 |
| AA | Durability tiers; control plane out of the hot path; batched sink | Q15 |
| AB | Shared-nothing cells behind a tenant router | 5B.4 |
| AC | Partitions + lease + epoch fencing from Waddles' design; Valkey cluster | 5B.3 |
| AD | Ingress backpressure, bulk intake, per-tenant quotas, weighted fair queuing | 5B.6 |
| AE | Hot-path requirements: pooling, `.cwasm` cache, lazy load, fuel, batch invocation | 5B.7 |
| AF | `elder.*` cache + batch with seed/lazy-load and epoch invalidation | 5B.9 |
| AG | Autoscale on lag; bounded labels; sampling | 5B.8 |
| AH | Load-test gate; per-language conformance acceptance | 5B.11, 5C.5 |
| AI | Webhook auth: HMAC + second factor; token-header alternative | 6.5a |
| **AJ** | **Replaces the old language-sequencing call (now D12):** cold-start order pooling, `.cwasm`, lazy load, language tuning; snapshots only opt-in behind a safety gate | 5C.7; Q10 |
| AK | Compile pipeline: vendored deps, offline build, publisher validation, quotas | 5C.3 |
| AL | No prebuilt `.wasm`, even first-party | Resolves the old prebuilt question per D13 |
| AM | SDKs generated from WIT + idiomatic layer, conformance suite, harness, CLI | 5C.2 |
| AN | Untrusted builds require the enforced isolation floor; fail closed per environment | Q17 |
| AO | Go is a user language only; platform stays Rust | Q20 |
| **AP** | Streams for dispatch, async actions, events, results; direct mTLS gRPC for synchronous host calls | 5.8 |
| **AQ** | Executor as a separate container in the engine pod over UDS (refines "credential-less") | Q6 |
| **AR** | Per-role scaling model, horizontal and vertical | 5.9 |
| **AS** | Marketplace: `bundles/{language}/{name}/` layout, source kinds and trust tiers, signature attestation, pinned-SHA flow, tenant admin vs PenguinTech approval | 5D; Q9 |

---

## 10. Left to the builder

- WIT worlds, host-call wire, manifest/graph schemas (fail loud on unimplemented calls; gate before every import; ABI version mandatory); `workflows.v1` and Elder internal API protos (versioned, explicit DTOs, bulk and cursor ops).
- Partition count, lease timings, shed thresholds, scheduler fairness, pool sizing, fuel budgets, run caps: from spikes, not guesses. Idempotency window and dedupe structure (60M keys for a 10-min window at 100k eps is large, UNVERIFIED); envelope signing granularity.
- History sink, retention, summary batching (Q15); expression and loop semantics (fixture suite is the oracle); canvas editing model, conversion palette, schedule-item shape (Q7, Q8); outbox mechanism (must cover the discovery worker); machine-JWT issuer; cached-grant staleness bound.
- Per-language recipes, signature primitive, sync cadence (5C.3, 5D; check PRC-origin); runner log/artifact return and push via gateway (prove early, R4); Helm chart, Cilium/Tetragon content, KEDA; metric names and usage metering (no tenant/run IDs as labels).
- Tests: fixture suite, flag-on E2E, telemetry counts, negative sandbox tests, independence run, load gate, 90%+ coverage.
- Flag prefix reconciliation; Rust logging conformance: `testing.md` says Rust asserts `tracing`, recon says `penguin-logging` exists on a release branch: assert both, flag the stale rule.

---

## 11. Open questions for the owner (most important first)

| # | Question | Default if unanswered |
|---|---|---|
| Q1 | **Scale definition and SLOs:** is 100k eps per tenant, per deployment/cell, or aggregate across a multi-tenant deployment? Latency/loss SLOs? Which environment hosts load tests? | Aggregate; cells scale out; placeholder SLOs; owner provides a multi-node cluster |
| Q2 | **Licensing and tier mapping** for custom modules, concurrency, run limits, scale limits (eps, concurrent runs, nodes/cells), compile quotas, marketplace sources, history retention. Frame: scale/structure/premium capability | Metering + `@enforce_limit` hooks, observe-only; no numbers |
| Q3 | **Confirm the heavy-step runner split (call C)**; a short-lived scoped token in a runner pod if gateway injection for push is infeasible? tenant-supplied runner images? | As designed; no token; allowlisted images |
| Q4 | **Confirm canvas = editor on one graph model (call D)** | Unify in Phase 4 |
| Q5 | **Shared libs:** is "the connection library" `penguin-spine`? Extract Waddles-resident crates into penguin-libs (who owns PRs and Waddles' migration)? Promote spine/bundle-host/logging to `main`, tag, or publish? Which SHA? | Consume 8.1 by SHA; extract incrementally |
| Q6 | **Role split:** confirm split by trust boundary vs fewer images; ingress/egress as separate Deployments of one image or separate images; scheduler and controller as gateway modes or separate; executor as a container in the engine pod or a separate Deployment | Calls I and AQ |
| Q7 | **Schedule-event model:** build a first-class schedule item, or scope to existing structures (issue `due_date`, on-call)? | Interim `due_date`; recommend a minimal item |
| Q8 | **Diagram conversion:** one-shot vs live-linked; which node-type mapping/palette is v1? | One-shot fork; palette 5A.7 |
| Q9 | **Marketplace:** confirm source kinds and trust tiers; who approves (tenant admin for private, PenguinTech review for shared); is there an official repo separate from the built-in; tenant-private sources in v4.0 or later; community sources at all? | 5D table |
| Q10 | **Snapshot history:** describe the specific Waddles snapshot problems if more than 5C.7 records; confirm snapshots stay opt-in only | Opt-in only, gated |
| Q11 | **Gateway scope:** external only, or also control-plane and `elder.*` traffic through a gateway mode? | External only; gated Elder adapter |
| Q12 | **Object addressing:** `village_id`, or add a UUID `public_id` Elder-wide? | `village_id` |
| Q13 | **ABI versioning:** WIT semver, support window, who migrates user modules | `abi` in manifest; N and N-1 |
| Q14 | **Streams cron executor** in scope? Now part of melding | In scope, Phase 2, in `scheduler` |
| Q15 | **History sink and retention:** approve object storage and/or a columnar store (merge design fixed Postgres + Neo4j + Redis); retention for history, source, build logs | Object storage + Postgres index |
| Q16 | **Runs (serverless) module:** in or out? | Out; not precluded |
| Q17 | **Isolation floor:** is hardening + Cilium + Tetragon enough for runner jobs and builds, or require Kata/gVisor? | Hardening stack; untrusted builds refused where absent |
| Q18 | **Optional integrations and private-host egress:** which of the 13 product connectors ship first-party; Darwin per call O; per-tenant private-host egress opt-in? | Optional bundles; opt-in off |
| Q19 | **Email transport:** none exists (stub). Provider/SMTP and owner? | Build in Elder API, Phase 2 |
| Q20 | **Go as a user language:** confirm the Go Phase-Out rules do not apply to user flowbundles or the Go builder image | As 5C.6 |

---

## 12. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Runner jobs execute native code: highest-risk surface | 6.8; no creds; gated egress; Kata escalation (Q17) |
| R2 | Build executes user code; interpreter languages have large components and slow cold start (Python 21.6 MB, 3.3-4.5 s uncached, VERIFIED) | `.cwasm` cache, pooling, no-network build, publisher split; slow start documented (D14) |
| R3 | Node reimplementation drifts | Fixture suite from existing tests |
| R4 | Pushing to a remote through gateway credential injection may not work (forces a token into the pod) | Early spike (Q3) |
| R5 | Noisy neighbor on shared executors | Weighted fair dequeue, per-tenant caps, optional dedicated pool |
| R6 | Scope: roles + scale + canvas + languages + compile + marketplace + melding | Phases; gated exits; upload last; deletion gate may exclude it |
| R7 | Fail-closed couples runs to Elder API and the control plane | Bounded-staleness caches; HA; typed errors |
| R8 | wasmtime CVE/upgrade invalidates the artifact cache | Exact pin + patch runbook; cache key includes ABI |
| R9 | DB connectors assume an HTTP `database-ops` backend that may not exist; WASM cannot do native DB egress | Out of v1 (N7) |
| R10 | Independence erodes | CI run with all other products absent |
| R11 | **Capacity numbers are unverified** | Phase 0/1 spikes; load gate |
| R12 | Hot-path coupling to Quart/Postgres creeps back | A11; batching only; review each phase |
| R13 | Lease/fencing is unbuilt (design only in Waddles); Valkey cluster ops are new for Elder | Lua fencing + dedup; chaos tests; Phase 0 provisioning |
| R14 | History sink cost; possible new datastore | Sampling; object-storage first (Q15) |
| R15 | Elder API overloaded by `elder.*` lookups; entity-event loops/storms | Caches, bulk APIs, limits, breaker; loop guard, coalescing |
| R16 | PII leak via free text or external payloads | Structured-only reads; retention off; redaction |
| R17 | No email transport; diagram prerequisites unbuilt; no schedule-item model | Phase 0 decisions (Q7, Q19); diagram work before Phase 4 |
| R18 | C# preview/RC, Go and Ruby unproven; PRC-origin provenance unchecked | Per-language gates (D12); provenance review |
| R19 | `penguin-*` crates only on release branches; extraction needs another team; first Rust workspace | SHA pin + vendoring; hybrid option 4; empty gated crate in Phase 0 |
| R20 | Cells, router, and many roles add operational complexity | One-cell default; shared chart |
| R21 | Per-envelope signature verification binds at 100k eps | Per-batch signing |
| R22 | Engine pod's shared network policy weakens executor isolation vs Waddles' separate Deployments | No secrets in executor; Tetragon; separate Deployment option (Q6) |
| R23 | Snapshot opt-in reintroduces non-reproducible builds and cross-tenant leak risk | Off by default; 5C.7 gate |
| R24 | Marketplace sources widen the build attack surface | 5D validator, pinned SHA, compile + consent, quotas |
