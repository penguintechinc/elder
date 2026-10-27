# svc-workflows — Design Spec

**Status:** Draft for owner review (2026-10-27). Design only. No code, scaffolding, or Dockerfiles until the derived plan is reviewed.

**Goal:** Replace Elder's in-process workflow and CI/CD engines (Streams, Flows) with `svc-workflows`: a multi-container Rust service that runs tenant-authored flowbundles inside WASM sandboxes, behind gated gateways, melded with Elder's own data, scalable past 100k events/second.

**Differentiator:** not a standalone automation engine. The point is "melding" with Elder's assets, issues, schedules, identities, diagrams, and documents (D9, 5A).

**Architecture:** Elder API (Python/Quart) stays the control plane (definitions, authz, identity, summaries) and is OUT of the per-event hot path. The data plane is one Cargo workspace producing several images split along trust boundaries (`gateway-in`, `gateway-out`, `engine`, `executor`, `controller`, `pipeline`, `builder-<lang>`), scaled per role, dividing work over Valkey Streams. v4.0 ships one cell.

**Tech stack:** Rust (tokio/axum, wasmtime component model + WASI 0.2), gRPC, partitioned Valkey/Redis Streams, shared `penguin-libs` Rust crates (git-pinned), React Flow canvas (`@xyflow/react` 12.3.5, in `web/package.json`), Helm only.

---

## 0. Reading guide

| Marker | Meaning |
|---|---|
| **DECISION** (D-numbered) | Only what the owner said. Author glosses live in calls |
| **AUTHOR'S CALL — owner to confirm** | Chosen by the author so the doc is plannable. Builder may proceed, not treat as settled |
| **ANSWERED** | Resolved by the coordinator/owner after the first draft; stated as fact |
| **VERIFIED** | Read by the author. Elder = `origin/release/v4.0.X` @ `bbd5ba7`; Waddles = `ae935412` |
| **RECON** | From the recon brief or coordinator; not re-read by the author |
| **UNVERIFIED** | Not verified. Builder checks first. All capacity numbers and toolchain-maturity claims not marked VERIFIED are UNVERIFIED |

Paths are relative to the Elder repo unless prefixed `waddles:` or `penguin-libs:`. Sections 5A (melding), 5B (scale), 5C (languages, compile, cold start), 5D (marketplace) follow section 5. "Flowbundle" = Elder's term for Waddles' "app bundle".

---

## 1. Intent & why

### 1.1 Intent

- One service presentation: Lucidchart (diagrams) + n8n/Zapier (event in, process, action out) + GitHub Actions (CI/CD workflows, reusable `uses:` functions).
- Elder's data is the differentiator: look up and act on Elder resources; data events launch flows; a diagram converts to a flow.
- All tenant logic runs as WASM; platform code never executes user code natively. All traffic in or out passes a gateway we gate.
- Works fully with no other PenguinTech product present. Scales horizontally and vertically, per role, to a >100k events/s design ceiling.

### 1.2 Why now

| Problem today | Evidence |
|---|---|
| Two engines, ~15k prod LOC, no WASM, no Rust | RECON; no `Cargo.toml` (VERIFIED) |
| Users cannot extend safely: `stream_custom_modules.code_blob` is a table nothing executes | `streams.py` (VERIFIED by grep) |
| Credentials plaintext, writes return 501 (cites #171) | `routes/credentials.py:100-106,152-161` (VERIFIED) |
| No per-tenant concurrency limits, dashboards, or OTel in `flows_invoker`/`apps/mcp` | RECON |
| No production history: cheapest moment to replace | D10 and its evidence below |

### 1.3 What exists today (VERIFIED unless marked)

| Piece | Where / notes |
|---|---|
| Streams | `apps/api/modules/streams/` (~3.4k LOC RECON), `apps/worker/streams/` (~5.7k RECON). 16+ `stream_*` tables; triggers: manual `routes/streams.py:1006`, HMAC webhook `routes/hooks.py:23,48`; runs in the shared worker (`apps/worker/jobs/registry.py:206`); 21 node classes in `nodes/{actions,conditionals,transforms}`; `expression.py:113` AST whitelist; `stream_schedules` cron model with no executor (`streams.py:263`) |
| Connectors | `apps/worker/streams/connectors/manifests/`: 31 YAML. 13 PenguinTech-product (`darwin`, `waddlebot`, `waddleai`, `nest`, `killkrill`, `marchproxy`, `squawk`, `tobogganing`, `skauswatch`, `articdbm`, `iceshelves`, `elder`, `waddleperf`) + 18 DB. HTTP-manifest style; platform-wide `env_var` auth |
| Flows | `apps/api/modules/flows/` (~3.4k RECON), `apps/flows_invoker/` (~1.8k RECON). 11 `iceflows*` tables; triggers `routes/promotions.py:465`, `routes/hooks.py:29,51,74` (GitHub HMAC, GitLab token header); `executor.py:389`; process-level sandbox `sandbox.py`, Tetragon exempted (`values.yaml:629-632`); Darwin review gate `review.py` fail-closed when required; writes `stream_executions` directly (`calls.py:79-195`) |
| Job bus | `shared/jobbus/core.py`: `elder:jobs:<group>`, one JSON `data` field, idempotency key, XAUTOCLAIM, DLQ at 5 |
| UI | `web/src/pages/{StreamsList,PlaybookEditor,ExecutionsList,ExecutionDetail,ApprovalCenter}.tsx` (~920 LOC RECON); Flows has no UI; Runs absent |
| Diagrams | `apps/api/modules/diagrams/`: 18 `dg_` tables; `dg_shapes.elder_entity_id` numeric soft ref; `routes/collab.py` WebSocket + Redis pub/sub |
| Reference registry | `apps/api/models/references.py`; `apps/api/common/refs/registry.py`; `apps/api/api/v1/refs.py:95,191`. Registers tenant, organization, entity, identity, software, service, ipam_*, issue, project, milestone, document, page. NOT diagram, stream, flow |
| Licensing | `apps/api/common/licensing/*`; flag `elder.license-enforcement`; `@enforce_limit` observe-only |
| Egress carry-over | `apps/worker/streams/nodes/net_guard.py:21` (private + CGNAT blocked; docstring admits a DNS-rebinding gap); `gitops.py:62` scheme allowlist; `sandbox.py` (25 allowed binaries incl. `bash`, `pip`, `npm`; CPU 900 s, AS 2 GiB, nproc 4096, fsize 512 MiB; env scrub). `networkpolicy-flows-invoker.yaml` is plain NetworkPolicy; CGNAT exclusion RECON: no |

Issues (open): #168 Phase 4b (~50% RECON); #171 Phase 6 compliance/KMS (the credential gate cites it); #165 Phase 2 gaps (registry lacks diagrams/streams/flows). No `svc-workflows` issue or Streams/Flows spec exists.

---

## 2. Goals / non-goals / anti-goals

### 2.1 Goals

| # | Goal | Verified by |
|---|---|---|
| G1 | Tenant logic runs only as WASM components with no ambient authority | Negative tests: no socket, file, env |
| G2 | Gateways are the only way in or out; every crossing authorized | Network-policy test; denial metrics non-zero |
| G3 | Signed, versioned flowbundles and reusable `uses:` functions | Unsigned/tampered/unpinned refused |
| G4 | Canvas edits the graph that runs; diagrams and flows cross-link and convert | Round trip canvas → run → overlay |
| G5 | Carry forward Streams/Flows behavior; delete old code before v4.0 GA | Fixture suite; deletion gate 7.8 |
| G6 | Credentials never enter the sandbox; encrypted at rest; closes #171 | Secret scan of guest memory/logs/spans finds none |
| G7 | Independence: works with no other product | Independence run (suite with every other product absent): baseline in Phase 1, repeated at every phase exit |
| G8 | OTel logs + metrics + traces; usage metering | Telemetry gate; counts printed |
| G9 | Fail closed when the control plane is unreachable | Chaos test |
| G10 | **Melding with Elder data is the primary differentiator** | Scenarios 5A.8 a-e |
| G11 | **Scale design ceiling >100k events/s sustained** (D11) | 4.0: scale-out of partitions and replicas within one cell reaches the load-test ceiling (5B.11); multi-cell is 4.1+ (call AB) |
| G12 | Flowbundles in Rust, JS/TS, Python at v4.0.x; Go, C#, Ruby at v4.1.x; compiled by us | Conformance suite 5C.5 |
| G13 | Git `bundles/` marketplace sources | Scenarios k-m (5D) |

### 2.2 Non-goals

N1 A shared service for other products (D7). N2 Rewriting Elder's control plane. N3 A general container scheduler (runner jobs only for steps WASM cannot do). N4 Per-tenant Postgres RLS: ANSWERED, in progress on `feature/rls-tenant-isolation-v2` (no PR yet); a dependency, not scope (gate in Phase 0, 7.2); until it merges, app-layer scoping (`apps/api/utils/tenant_scoping.py`, VERIFIED). N5 Changing the license tier model. N6 A paid/public storefront (5D is git sources only). N7 Native DB-protocol egress (R9). N8 The Runs module (Q16). N9 User-supplied prebuilt `.wasm` for tenant bundles (D13). N10 A multi-cell router and directory in v4.0 (call AB).

### 2.3 Anti-goals

- A1 A user module reaching network, filesystem, or credentials directly.
- A2 A cross-product dependency (compile, runtime, deploy, data).
- A3 A silent fallback or stub (an unimplemented host call returns loud `not_implemented`).
- A4 A gate that cannot fail (`\|\| true`, zero-denominator pass, unmounted check).
- A5 A dual stack surviving to GA, or any legacy fallback or kill-switch after GA.
- A6 Secrets or PII in spans, labels, logs, bundles, or the sandbox.
- A7 Guest-supplied tenant, run, or identity fields.
- A8 Reproducing the Waddles shortfalls (5B.7, 5C.7).
- A9 `svc-workflows` as a second source of truth for Elder data.
- A10 Raw PII, or any direct database access, in the data plane.
- A11 The control plane (Quart/Postgres) in the per-event hot path.
- A12 Silent loss under overload (every shed is counted).
- A13 A build with network access while executing user code, or an unscanned/unsigned artifact loading.
- A14 Vault/egress credentials, an internet-facing listener, K8s Job RBAC, and untrusted-code execution sharing an image (D16). "Credentials" here means vault and egress credentials; the engine necessarily holds internal Valkey/Elder/object-storage credentials (5.3).

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
| Tenant hard boundary; service-to-service gRPC with short-lived signed JWTs, SPIFFE-ready | `security.md`; `backend.md` | 5.7 |
| 90% coverage; default-deny Cilium; enforced Tetragon; PSA `restricted`; decision-gating caches seeded + lazy-loaded | rules | 6.4 |
| Helm only; pinned build container | `devops.md` | First Rust crate in this repo (VERIFIED) |

---

## 4. Decisions (owner's)

Only what the owner said. Author reading, defaults, and consequences are in the calls (9) and the notes below the table.

| # | Decision |
|---|---|
| D1 | Move Elder's workflow and CI/CD functionality into its own image/container, `svc-workflows`: a Rust system layer that runs flows inside WASM environments |
| D2 | "Lucidchart + n8n/Zapier + GitHub Actions in one service presentation." The existing diagrams layer (IceCharts; 18 `dg_` tables, React Flow canvas, real-time collab over WebSocket + Redis) must be usable and cross-used within workflows. Closer to GitHub Actions than Ansible: CI/CD workflows and reusable "functions" |
| D3 | Flowbundles for different services covering ingest, processing, and actions — signed, versioned units. A reusable function is like a GitHub Action `uses:` |
| D4 | A unified gateway layer for ingress/egress out of the system, which we gate. It is the only way in or out |
| D5 | Take notes from the Waddles data plane (section 8) |
| D6 | WASM is for any "logic", so users can create custom modules with lowered risk to the system. Bundles are user/tenant-authored, untrusted code |
| D7 | Independence: all PenguinTech products are independent. Other products may trigger EXISTING workflows by webhook or API and may offer optional integration, but nothing depends on them. Works fully with none present; degrades gracefully if one is absent or down. Serves Elder only |
| D8 | (2026-10-27) Reuse the shared `penguin-libs` Rust crates the Waddles data plane uses; do not reimplement them; do not depend on the Waddles repo |
| D9 | (2026-10-27) "The reason workflows was merged into Elder was to allow the 'melding' of assets data, issues / schedules, diagrams, and workflows together — meaning I should be able to easily look up and use the other resources and assets of Elder (i.e. quickly add a 'schedule event' as an action, kick off a workflow which sends an email to a known hostname and user, and launch workflows when an asset is added... also quickly convert a diagram into a workflow if needed." |
| D10 | (2026-10-27) "We never made it to prod with IceCharts product before the merger here." There are no real tenants, playbooks, run history, credentials, or webhook URLs to preserve |
| D11 | (2026-10-27) "...we should be able to scale over 100k eps if needed to (a la would this work at a large enterprise like Uber?)." Scale target: >100k events/s sustained, a design ceiling |
| D12 | (2026-10-27) Flowbundles may be written in Python, C#, Node.js, Rust, Go, or Ruby, compiled to WASM components. v4.0.x ships Rust, JS/TS, Python; Go, C#, Ruby are a v4.1.x target |
| D13 | (2026-10-27) "We provide a means to do the compilation so we can secure them similar to Waddles": platform build-as-a-service (untrusted build in an ephemeral K8s Job, no network during build, results to the artifact bucket, approve, Ed25519 sign, verify before load). User-supplied prebuilt `.wasm` is not accepted for tenant bundles |
| D14 | (2026-10-27) Document the slow start of interpreter-embedded languages; speed it up where safe; snapshots caused headaches on Waddles |
| D15 | (2026-10-27) A "marketplace" is a `bundles/` folder in a git repo in a format we expect (manifest, folder structure, signature), as just specified for Waddles |
| D16 | (2026-10-27) svc-workflows is a multi-container service, not a single container. It scales horizontally and vertically. Roles are separated by run mode and/or image, and communicate and divide work over Valkey Streams/queues as Waddles does |

**Evidence and reading for D10 (author-verified, not owner words):** Elder v3.x never contained Streams/Flows: tag `v3.2.2` has none of `apps/api/modules/streams`, `apps/api/modules/flows`, `apps/flows_invoker`; first commits 2026-07-10 (`53ca62b`, `c09986a`); v4.0 has no tag yet (VERIFIED). UNVERIFIED: that no internal deployment holds data that matters. Reading: replace, do not strangle (call F).

---

## 5. Architecture

### 5.1 Overview (one cell)

```
 Browser (canvas)         Other products / customers (webhook, bulk API)
       | REST                       | HTTPS
       v                            v
 +-------------+          +----------------------+
 | Elder API   |          | gateway-in           | no vault, no egress credentials
 | CONTROL     |          | verify, limit, mint  |
 | PLANE       |          +-----------+----------+
 +--+-----+----+                      | XADD run-start (partitioned)
    |gRPC |outbox                     v
    |async v events    +-----------------------------+     scheduler (gateway-in image,
    |batch ----------->| Valkey Streams (cluster,    |<--- own Deployment: events, cron,
    |                  | AOF, HA); lease + fencing   |     timers, emit; mint key only)
    |                  +------+--------------+-------+
    |            run-start    |              | continuation streams
    |            consume      v              v
    |            +--------------------+   +---------------------+
    | elder.*    | engine pod         |   | gateway-out         |--> Internet
    +<-----------| engine <-UDS-> exec|-->| allowlist, pin,     |    customer APIs
                 | (creds)     (none) |   | vault injection     |
                 +---------+----------+   +---------------------+
              artifacts RO | history WO
                           v
          Object storage: signed-artifact prefix | QUARANTINE prefix | history prefix
 controller (K8s Job RBAC only) -> Jobs: fetch, scan, build, publish (5C); runner (git/test)
```

- Control plane = Elder API; data plane = `svc-workflows`. AUTHOR'S CALL A (revised: run/step history goes to the data plane's sink, AA).
- External traffic enters/leaves through gateway-in/gateway-out. `elder.*` calls take the engine's gated Elder adapter to Elder's internal API. AUTHOR'S CALL Q (Q11).

### 5.2 Control plane (Elder API)

Owns: definitions (graphs, flowbundles, triggers, schedules; schema changes need no shims, D10); authz (OIDC scopes, tenant, teams); identity and PII; credential vault (6.6); license and flags; approval, consent, and signing (private key never in the data plane); run summaries and indexes (batched, never per event). The UI is rewritten in Phase 4 against the new API (AUTHOR'S CALL F); if Phases 1-3 need a thin `/api/v1/streams/*`/`flows/*` facade, it is TEMPORARY and removed at the Phase 4 exit gate.

### 5.3 Roles, images, deployments — AUTHOR'S CALL I (replaces "one image, several modes"), AQ

One Cargo workspace, several images split along **trust boundaries** (Waddles runs ingest, process, action, bundle_executor, and egress_proxy as separate services). A run mode within an image exists only where roles share a trust level and holdings.

| Role (SPIFFE `spiffe://penguintech.io/<env>/svc-workflows-<role>`) | Image / mode | Holds | Network | Scales on |
|---|---|---|---|---|
| `gateway-in` | `gateway-in` / `ingress` | Inbound verification secrets (own key scope, distinct from egress credentials); `gateway-in` producer key. NO vault, NO egress credentials | Public listener; Valkey (XADD run-start); control plane | RPS, connections, 429 rate |
| `scheduler` | `gateway-in` / `scheduler`, own Deployment | `scheduler` producer key; trigger cache. No listener | Control plane; Valkey; no internet | Event/timer lag |
| `gateway-out` | `gateway-out` | Vault access; credentials in memory per call; chain-verification secrets; assertion key. No public listener | Internet allowlist; vault; Valkey (consume async-action streams); mTLS from engine | In-flight egress, pool saturation, action-stream lag |
| `engine` | `engine` (container 1 of the engine pod) | Valkey consume + continuation XADD; run-as Elder tokens; object storage **read-only** on the signed-artifact prefix and **write-only** on the history prefix | Valkey, gateway-out, Elder internal API, object storage | Run-stream lag, executor saturation |
| (executor) | `executor` (container 2, same pod) | Nothing: no secrets, no SA token | UDS to `engine` only | With the engine |
| `controller` | `controller` (small separate image) | K8s RBAC: create/watch Jobs in one namespace; nothing else | K8s API; control plane | Job backlog |
| `pipeline-fetch`, `pipeline-publish` | `pipeline` / `fetch`, `scan`, `publish` Job steps | fetch: none (git via gateway-out); scan: none; publish: write to the QUARANTINE prefix | Per step | Build queue |
| (builder) | `builder-<lang>` Job | Nothing | None | Per-tenant quota |
| (runner) | Allowlisted runner images | Nothing | gateway-out only | Per-tenant quota |
| (cell router/directory) | **Designed, not built in 4.0** (call AB) | — | — | — |

Roles needing identity get one SPIFFE ID each (7: `gateway-in`, `scheduler`, `gateway-out`, `engine`, `controller`, `pipeline-fetch`, `pipeline-publish`); executor, builder, runner hold none. Tetragon: one policy per image role.

Why split by trust boundary: (1) least privilege: vault/egress credentials live only in `gateway-out`, the internet-facing listener only in `gateway-in`, K8s Job RBAC only in `controller`; the engine and executor, which run untrusted code, have no ingress, no vault, no Job code, minimal binaries, and tight Tetragon and network policy; a code path absent from an image cannot be leveraged; (2) different resource profiles and scaling signals; (3) independent rollout and drain; (4) smaller attack surface.

- **Controller (recommendation):** a small separate image, not internet-facing, no vault. Alternative: run it inside an Elder-side worker; rejected as the default because it puts K8s Job RBAC in the shared control-plane worker and couples build orchestration to Elder's release (Q6).
- **Scheduler** shares the `gateway-in` image because it holds only an envelope-minting key and no listener; a separate image is the stricter option (Q6).
- **Executor (call AQ):** "credential-less" is precise: the engine holds internal Valkey, Elder, and object-storage credentials; a guest escape must not reach them. Wasmtime runs in the separate `executor` container: no secrets, no SA token, UDS peer-credential check. The engine fetches artifacts from the signed prefix, verifies signature and digest, and hands bytes to the executor through a shared `emptyDir` mounted read-only in the executor. Pod-level network policy is shared (weaker than Waddles' separate Deployments over mTLS); a separate executor Deployment is the option if network isolation is required, at a hop per host call (Q6).
- No role keeps durable local state. Scaling: 5.9. Helm, CI, Tetragon: 7.6.

| Alternative | For | Against |
|---|---|---|
| Single image, all modes | Simplest CI/ops | Vault credentials, internet listener, and the untrusted-code executor in one image. Rejected |
| Two images (gateway + engine) | Minimum split | Ingress and vault/egress share a binary; Job RBAC and builders share an image with something |
| Per-trust-boundary images (above) | Least privilege | Nine image builds at v4.0, per-role Deployments, Job templates, one chart |

### 5.4 Gateway traffic

**gateway-in:** external webhooks (authentication per 6.5a over raw bytes before any parse or guest call; replay window; delivery-id dedupe); bulk intake for high-rate producers (per-batch signature, 5B.6); API triggers (scope `workflows:execute`). Elder-originated runs (UI, schedule, entity event, approval) come from the `api` and `scheduler` roles, not the public listener. Ingest flowbundles receive authenticated bytes and parse; they hold no socket or credential. Long-lived connections are out of v1 (a "poll" source is scheduler-driven). Other products may only trigger existing workflows.

**gateway-out:** WASM `http`, runner traffic (git), pipeline `fetch` (git), notifications, outbound webhooks all take the pipeline in 6.5. Operator-configured infrastructure endpoints are not subject to the bundle guard (provenance, not address range; Waddles §8.5).

### 5.5 Flowbundle model — AUTHOR'S CALL L

| Concept | Definition |
|---|---|
| Function | Smallest WASM component with typed input/output and declared permissions; signed, immutable by digest. `uses: <namespace>/<name>@<version>` |
| Flowbundle | Signed, versioned unit for one service integration: manifest + graph(s) + function pins + stage roles (`ingest`, `process`, `action`) + permissions + limits |
| `uses:` | Authoring may name a tag/range; publish resolves to an immutable digest and writes a lockfile; runtime accepts digests only |
| Scopes | Platform (Elder-shipped), tenant-private, marketplace-shared (5D) |

Manifest fields (builder owns the schema): `name`, `version`, `kind`, `stages`, `language`, `sdk_version`, `abi`/WIT world version, `uses[]`, `permissions` (`egress[]` host+methods, `secrets[]` names, `elder[]` ops, `diagram[]`), `limits` (timeout, memory, fuel), `inputs`/`outputs`, `license_feature`.

- **Canonical lifecycle (call AX; one lifecycle for 5C.3, 5D, and D13):** `fetched → source-scanned → built → output-validated → pending-approval → approved → signed → published → active → revoked` (plus `rejected`). Order of operations: fetch → source scan + vendored-dependency verify → build → output validate/scan → approve/consent (`permission_hash`) → sign → publish to the signed-artifact prefix. Anything not yet signed and approved lives in a separate **QUARANTINE** prefix that the engine can never read. First-party bundles use the same lifecycle with a system auto-approve gate (call AL).
- Consent shows hosts + methods, secret names, Elder ops, limits, scan status, ABI; runtime authorization derives from the approval record (Waddles D26). Widening needs re-consent; narrowing auto-approves with audit. Revocation reaches the data plane in about a second.
- v1 host capabilities (the gate runs first on each): `context`, `http` (via gateway-out, secrets by reference), `elder` (5A.3), `diagram`, `log`, `clock`, `flags`, `emit` (an emit-request continuation consumed by the scheduler, 5.8). Deferred: `kv`, `db`, `relay`. No `wasi:sockets`, no filesystem beyond an empty scratch, no `wasi:cli/environment`; the executor natively implements the refusing interfaces.

### 5.6 Canvas and graph model — AUTHOR'S CALL D

One canonical graph document: the canvas edits it; it is compiled, signed, and run. Semantic layer (nodes with typed ports, edges, versioning; executed) plus presentation layer (x/y/w/h, style, annotation shapes; not executed). **Timing (call D):** the executable schema, including node/edge/typed ports, versioning, and the sign/run format, is designed in Phase 0 and executed by the engine in Phase 1; the Phase 2 parity fixtures target that schema; Phase 4 builds the canvas, editor, and conversion on it. Today separate: `stream_nodes`/`stream_edges` vs `dg_shapes`/`dg_connectors`. Cross-use: diagram → flow via registry link; flow → diagram via host `diagram`; run overlay over the existing WebSocket + Redis channel (`elder:dg:{diagram_id}`); conversion 5A.7. Editing concurrency (collab sessions vs `stream_editor_locks`): builder picks one. Loop semantics of `while_loop`/`for_each`: UNVERIFIED, read first.

### 5.7 Identity, transport, trust

| Concern | Design |
|---|---|
| Machine identity | EdDSA short-lived JWT (≤1 h), `sub` = the role's SPIFFE ID; SPIFFE-ready (mTLS/X.509-SVID first-class). Elder-side issuer UNVERIFIED |
| Control-plane calls | gRPC `workflows.v1`, `api_version` per request; async and batched (5B.2) |
| **Run-start envelopes — AUTHOR'S CALL X** | Signed (EdDSA) by their producer: roles `api` (Elder API), `gateway-in`, `scheduler`. Each role mints only its envelope kinds. The engine holds only public keys, verifies tenant/run/trace binding at every hop, never mints a run-start. Failure = DLQ, never retried, counted |
| **Continuation envelopes — AUTHOR'S CALL AT** | The engine never mints new runs. Hand-offs, next-step, async-action, and emit-request messages carry a CHILD envelope MAC-chained from the parent: a per-run key = HKDF(producer-role secret, tenant ‖ run id), delivered to the engine inside the run-start envelope (sealed to the engine role). The engine can therefore only EXTEND a run it was given, for the same tenant; it cannot start runs, switch tenants, or fabricate credentialed egress requests. `gateway-out` re-derives the run key from the role secret and verifies the chain at every hop; producer secrets are role-scoped, so a compromised `gateway-in` can at worst start runs, which it can already do. Chains carry causation depth |
| **Valkey ACLs (call X/AT)** | Producers (`api`, `gateway-in`, `scheduler`) may XADD run-start streams; the engine consumes run-start streams and may XADD only continuation streams (next-stage, async-action, emit-request); `gateway-out` consumes async-action streams; the scheduler consumes emit-request streams |
| **Run-as principal — AUTHOR'S CALL Y** | Each run executes as a service identity scoped to the workflow's owning team and tenant. Effective scopes = consented permissions in the approval record ∩ the owning team's grants (narrower layers restrict, never expand). The triggering user/event is audit-only and supplies no authority. Per-user actions (if ever) use the user's seat tier; team features use the team's allocated tier. Resolves the confused-deputy claim in 6.2 |
| Fail closed | Grants missing/cache-miss/loader-error = deny; flags/license last-known, unseen OFF; cached-grant staleness bounded (builder) |

### 5.8 Communication: streams vs direct calls — AUTHOR'S CALL AP

Streams (via `penguin-spine`) carry run dispatch, async actions, events, fan-out, and results, as in Waddles. Synchronous host calls from a running module go over direct mTLS gRPC/HTTP2 with deadlines, as Waddles' `bundle_executor` dials its stage and egress goes to `egress_proxy`. A stream hop per host call adds latency and reply-correlation machinery and roughly doubles stream load at 100k eps. Within a run, next-step hops stay in memory (5.10).

| Interaction | Transport | Failure semantics |
|---|---|---|
| Ingress / scheduler → run-start | Partitioned run-start streams (XADD) | Producer acked only after XADD succeeds; `429/503` + `Retry-After` on backpressure; idempotency key; dedupe |
| Engine consume | Consumer groups; lease + epoch fencing | At-least-once; reclaim after idle; DLQ after max deliveries; resume from checkpoint (5.10) |
| Async action; fan-out | Continuation streams (child envelope); gateway-out consumer group | At-least-once; backoff; DLQ; key = (run id, step id) |
| `emit` (follow-on run) | Emit-request continuation stream → scheduler, which verifies the chain, applies loop guard/depth/limits, and mints a run-start | Capped; counted |
| Engine → history | Bounded buffer → batched sink | Bounded, counted loss window |
| Module `http` host call | Engine → gateway-out, mTLS gRPC, per-call deadline | Typed error to the module; no auto-retry unless idempotent; circuit breaker; `resource_exhausted` on backpressure |
| Module `elder.*` host call | Engine → Elder internal API, gRPC, deadline | `elder_unavailable` fail closed; retry reads with jitter; writes carry an idempotency key |
| Engine ↔ executor | UDS in the pod | Executor death = trip; call fails retryable |
| Controller → Jobs; results | K8s API; results to a stream | Job TTL; failure recorded |
| Control plane ↔ cell | gRPC, pull + push-invalidate | Bounded staleness |

### 5.9 Scaling model — AUTHOR'S CALL AR

| Role | Bottleneck | Scale signal | Min/max (placeholder) | Vertical knobs |
|---|---|---|---|---|
| `gateway-in` | TLS, connections | RPS, p99 ack, CPU, 429 rate | 3 / unbounded | Cores, connection and body limits |
| `scheduler` | Event matching, timers | Event stream lag, due-timer backlog | 2 / ≤ partitions | Cores, trigger-cache memory |
| `gateway-out` | Outbound pool, TLS, vault cache | In-flight requests, pool saturation, action lag | 2 / unbounded | Pool sizes, cores |
| `engine` + executor | WASM CPU, instance memory | Run-stream lag per partition, executor saturation | 2 / ≤ partitions | Executor instances per pod, pooling-allocator memory, cores per pod, fuel budgets |
| `controller` | Job creation | Job backlog | 2 (HA) / 4 | Small |
| pipeline/builder/runner Jobs | Concurrency | Queue depth, per-tenant quota | Quota-bound | Job CPU/memory/disk limits |
| Valkey cluster | Memory, ops/s | Memory, ops, replication lag | Shards | Node size |

- Horizontal: stateless roles, consumer-group sharding, partitions, autoscale on lag (HPA/KEDA: UNVERIFIED availability), PDBs, graceful drain (load → verify → swap → drain → unload; Waddles spec §7.6, VERIFIED). v4.0 is one cell; cells are 4.1+ (5B.4).
- Vertical: per-role requests/limits; engine instance pooling. Scale up while a pod's pooled-instance memory and per-core throughput stay efficient; scale out otherwise. Parallelism is capped by partitions (replicas × cores ≤ partitions); the fix for a ceiling is a higher partition count, not per-key migration (5B.3). Waddles' scale design has no NUMA guidance, so none is assumed (grep).

### 5.10 Run execution and resume — AUTHOR'S CALL AU

- A run executes to completion **in memory** on one engine worker. Next-step hops stay in memory; streams are used only at fan-out, async-action, and wait boundaries.
- Checkpoints are written only at durable boundaries: waits/delays/approvals, and before and after externally visible side-effect actions. The checkpoint store is a partition-local Valkey hash (persistence + HA) with a TTL of the run's maximum duration; only long waits become control-plane records.
- After a crash, reclaim resumes from the last checkpoint (replay from the start if none; steps before the first side effect are pure).
- Idempotency key for side effects = (run id, step id), **not** attempt. An attempt counter exists only for retries of a failed step, which reuse the same key so receivers that honor it cannot see duplicates. External APIs that ignore idempotency keys can still see at-least-once duplicates.
- Delays and timers are owned by the control plane as durable timer records, created in batches; a delay shorter than `T_short` (placeholder 30 s) is held in the worker with its checkpoint. The scheduler re-enqueues when a long wait completes.

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

Elder has gRPC stubs for auth, graph, organization only (`apps/api/grpc/generated/`, VERIFIED); no resource-CRUD service. Builder: thin internal gRPC facade over the service layer vs REST with a machine JWT (REST only if gRPC is unavailable). Phase: read ops in Phase 1, writes in Phase 2 (call BB).

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
- A raw address as recipient is refused (`pii_in_argument`); external recipients use registered notification contacts held in Elder. Run history stores IDs, op names, outcomes; no response bodies by default. Rejected: SMTP from gateway-out with a detokenize call (plaintext in gateway memory).
- **Gap:** Elder has no working email transport; `_send_email_notification` returns success without sending (`apps/api/services/webhooks/service.py:1014-1025`, VERIFIED). A real transport is a prerequisite (Q19).
- External trigger payloads may hold PII the platform cannot tokenize: stored per a per-trigger `payload_retention` (default: not stored); sensitive keys redacted in logs/traces.

### 5A.5 Schedules — AUTHOR'S CALL V

- Existing (VERIFIED): `stream_schedules` (cron, tz, `next_run_at`; model only, no executor); `sbom_scan_schedules` (`models/security.py:118`; not examined); on-call tables (`modules/services_oncall/models.py`; people scheduling); issue `due_date` (`issue.py:323`) and project/milestone dates (`project.py:36-37,58`); worker `aiocron` (`apps/worker/main.py:683-697`, precedent for a time executor). A calendar or schedule-event model: **none found** (grep); UNVERIFIED that no module has one.
- "Add a schedule event as an action" has **no target today**. Options: (1) issue with `due_date`; (2) on-call override/shift; (3) a new minimal schedule item (title, start/end or due, recurrence, registry links, owner team, `village_id`); (4) a flow cron trigger (a trigger, not an Elder event).
- Call V: recommend (3). Until Q7, Phase 2 uses `elder.issue.*` with `due_date` and a `schedule.due` trigger; `elder.schedule.*` is absent from the WIT, never stubbed.
- Time triggers are part of melding: cron (tz-aware), due dates, schedule start/end, waits. The Streams cron gap (Q14) is in scope in the `scheduler` role: fire key = (trigger, scheduled instant); missed-fire policy is the builder's.

### 5A.6 Entity events as triggers — AUTHOR'S CALL T

- Today: `shared/events.py::emit_event` XADDs to `elder:events:{domain}`. **Zero callers and zero consumers** (VERIFIED by grep). It is fail-soft (a Redis outage drops the event), uses a sync client in an async function, carries int IDs. Entity writes have ≥3 paths: API routes, direct DB upserts in the discovery worker (`apps/worker/discovery/service.py:428-443`), and connectors via `ElderAPIClient` (`apps/worker/utils/elder_client.py`). A common write layer: UNVERIFIED. `AuditService.log_modification` (`services/audit/service.py:213`) is a candidate hook (called on entity writes: UNVERIFIED). Reusable: stream naming, JobBus idempotency/DLQ patterns.
- Design: events `entity.created|updated|deleted|tagged|linked`, `discovery.found`, `issue.*`, `schedule.due`; payload = village_id, changed field names, non-PII subset. **Transactional outbox** (row in the same DB transaction), via a Postgres trigger or ORM/DAL hook covering all write paths including the worker; an Elder-side relayer (Python, call BB) forwards to the cell's event stream. No lost events.
- Trigger definitions live in the control plane (tenant-scoped, owning team): event types + typed predicate filter on a whitelisted attribute set (type, tags, provider/account/region, status, org), flow digest, rate limit, dedupe window, `payload_retention`. The `scheduler` role consumes events, matches against its seeded + lazy-loaded cache, applies dedupe/loop guard/limits, and mints run-start envelopes. The engine never sees raw events. Idempotency key = (event_id, trigger); rapid updates coalesce within the window.
- **Loop prevention:** every envelope carries `origin` (root run, causation depth, chain hash). Writes a run makes through `elder.*` tag their outbox rows with the causing run and depth. The matcher blocks a trigger from firing on events caused by its own flow within one chain (default), caps depth, counts every block, and auto-disables a trigger after N hits with an alert. Cross-flow chains need an explicit per-trigger opt-in.
- **Rate and burst:** per-trigger and per-tenant limits; bounded queues with backpressure to the matcher (pause consumption); batch-trigger mode (a list of addresses per run); discovery bursts (e.g. 100k events) coalesce (5B.9).

### 5A.7 Diagram → flow conversion — AUTHOR'S CALL W

- **Diagram-side prerequisites (not built):** `dg_shapes.elder_entity_id` is a numeric soft ref with no FK (`diagrams.py:130-135`, VERIFIED); icon sets are an unchecked item (`docs/design/inventories/icecharts.md:9`, VERIFIED); diagrams are not in the registry (VERIFIED); recon puts diagrams Phase 4 at ~70%. Needed, in Phase 3 (before conversion in Phase 4): (1) register `diagram`; (2) entity-bound shapes as `binding` references by `village_id`, migrated off the numeric ref; (3) a shape-kind taxonomy (`shape_type`, `dg_shape_metadata`, `dg_node_metadata` exist, unread); (4) connector direction semantics (UNVERIFIED).

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

| # | Scenario | Phase | Assertions |
|---|---|---|---|
| a | Asset discovered → flow → issue + schedule item | 2 | Entity created (API or discovery); outbox → stream → scheduler; exactly one run; issue linked to the entity; schedule leg per Q7 (`due_date` until built); registry links; history IDs only; replay creates no second issue; another tenant's identical asset does not trigger |
| b | User-run flow emails a known user about a known hostname | 2 | Search hostname → address; `elder.notify.send` with identity ref; email arrives at a real test transport; scan of svc-workflows logs, spans, metrics, queues, run history, executor memory for address and username: zero matches over N>0 artifacts; a raw address argument is refused |
| c | Small topology diagram → draft → review → enable → run | 4 | 3 bound shapes + 1 decorative; draft links back; an ambiguous item blocks enable; approval required; run succeeds; overlay on canvas (needs a recorded run, 5B.2); later diagram edit shows "changed", enabled flow unchanged |
| d | A flow that creates an asset does not retrigger itself | 2 | Needs `elder.*` writes and the nodes: exactly one run; `loop_guard_blocked` ≥ 1; depth cap holds for F→G→F; trigger auto-disables after N hits |
| e | Elder API down mid-run | 1 | Step fails `elder_unavailable`; nothing fabricated; counted |

---

## 5B. Scale & capacity

### 5B.1 Target (D11), default scope, placeholders

Owner target: >100k events/s sustained, a design ceiling. **Author's default (call AB):** aggregate across a multi-tenant deployment; v4.0 ships one cell, so 4.0 demonstrates what scale-out of partitions and replicas within one cell sustains; the 100k aggregate is a 4.0 claim only if one cell reaches it (UNVERIFIED), otherwise multi-cell (4.1+) delivers it. Scope per tenant/cell/aggregate stays open (Q1). eps = trigger events accepted per second; step rate = eps × steps per run.

| Placeholder SLO (not owner-set) | Target |
|---|---|
| Ingest ack p99 | < 50 ms |
| Event → run start p99 | < 1 s |
| Native step p99 / WASM step p99 (warm, excl. module work) | < 5 ms / < 20 ms |
| Accepted-event loss | 0 (at-least-once; duplicates counted) |

### 5B.2 Control plane out of the hot path; durability tiers — AUTHOR'S CALL AA, AW

No synchronous per-step gRPC checkpoint into Quart/Postgres. Hot run state lives in the data plane; durable history goes to an append-only batched sink; Postgres holds definitions, summaries, indexes.

| State | Where | Durable? | On loss |
|---|---|---|---|
| Definitions, grants, triggers | Postgres; cell read-only cache | Yes | Reload (seed + lazy) |
| Accepted event | Partition stream (Valkey cluster, persistence + HA) | Yes (replicated before ack, tunable) | Producer retries; dedupe |
| In-flight run | Engine memory + stream pending list + checkpoint hash (5.10) | Reconstructible | Reclaim, resume from checkpoint |
| Long waits, timers, approvals | Control-plane records (low volume) | Yes | Scheduler re-enqueues when due |
| Run/step history | Append-only batches in the sink | Yes, eventually | Bounded buffer; counted loss window |
| Summaries, indexes | Postgres via batched upserts | Yes | Rebuild from the sink |
| Logs, metrics, traces | OTel | No | Sampled; outside the hot path's failure domain |

- **Run IDs (call AW):** UUIDv7 generated in the data plane, never an Elder counter. Run records are registered with the Elder registry **asynchronously and in batches**, only for runs in `recorded` mode.
- **Record modes** per flow or per run: `always`, `sampled`, `on-failure`, `on-demand-replay` (a recorded re-run of a past input, side-effect steps in dry-run; builder defines). **Default: `on-failure`** (full step history only for failed runs) plus summaries and counters for all runs; a canvas "test run" defaults to `always`. ExecutionDetail and the canvas overlay (scenario c) read per-step data from recorded runs.
- Sink options (builder decides; a new datastore needs owner approval, Q15; the merge design fixed Postgres + Neo4j + Redis): object-storage segments + Postgres index (cheap, durable, slow ad hoc query); columnar/time-series store (fast query, new datastore, rates UNVERIFIED); partitioned Postgres (not credible at this rate). 500k steps/s × 200 B = 100 MB/s ≈ 8.6 TB/day if every step were stored (UNVERIFIED sizes), hence the record modes.

### 5B.3 Partitioning, ordering, fencing — AUTHOR'S CALL AC (Waddles design, not code)

Source: `waddles:docs/superpowers/specs/2026-09-28-dataplane-scale-design.md` (VERIFIED). It is sized on cardinality (streams, connections, digests) and states no events/s figure. **Implementation not found:** no `fenced_commit`, `lease:epoch`, or `N_PARTITIONS` in `waddles:core/`; `penguin-spine` @ `586b8dad` has no partition/lease code. Reuse the design; build the code (in spine or here).

- **Partition key (default):** hash(tenant, flow, event key); **no ordering guarantee across events**. The 100k eps ceiling applies to unordered flows.
- **Ordered mode (opt-in per flow):** the partition is pinned by flow or by a declared ordering key; throughput is bounded by one consumer per partition (ceiling UNVERIFIED; a spike measures it). Fencing, dedupe, and fairness operate at partition granularity.
- Reused from Waddles: fixed partition count with rendezvous hash as the transport unit; Valkey lease ownership (`SET NX PX`, renewal, jitter, claim cap); hard self-eviction on a monotonic deadline; server-side Lua epoch fencing (XADD hand-off, **XACK last**, dedup key; scripts do not roll back, so ordering + dedup are the safety); voluntary shed above 150% of average and raise the partition count rather than migrate hot keys; fairness layering (rate limit → weighted fair dispatch → executor sub-ceiling → aggregate trip); lazy load, LRU by digest, decayed-score pinning, single-flight loads; HPA on partition lag (5.9).
- Valkey cluster mode; hash tags colocate a partition's stream, checkpoints, and dedupe keys. Fencing does not make side effects exactly-once; at-least-once + idempotency is the contract.

### 5B.4 Cells — AUTHOR'S CALL AB (revised)

- **v4.0 ships a SINGLE cell.** A cell identifier is carried in envelopes and stream/key names so a router can be added later. The cell router/directory (tenant → cell, edge/DNS routing, replicated definitions) is a **v4.1+ deliverable**: designed here, not built in 4.0.
- A cell is an independent deployment of all roles plus its own Valkey cluster and history sink; no hot-path call crosses cells. Multi-region and residency are future: a cell is region-pinned.
- Consequence for the claim: 4.0 scales out within one cell; multi-cell scale-out and blast-radius isolation are 4.1+. If D11 means 100k eps for one tenant, that tenant spans cells by (tenant, flow) hash; one hot flow cannot split without losing order (Q1).

### 5B.5 Capacity math (worked example; every figure UNVERIFIED unless marked)

| Item | Estimate |
|---|---|
| Load | 100k eps × 5 steps = 500k steps/s; 49 eps/partition average at 2048 partitions, 250 at 5× skew |
| Valkey commands (per-event vs per-boundary) | Run stays in memory: ~1.2 commands/event (XADD + batched XREADGROUP/XACK) = ~120k/s. Each boundary (fan-out, async action, wait) adds ~5 (XADD, XREADGROUP, XACK, 2 checkpoint writes): at 1 boundary/event ~620k/s. Next-step hops add none. ~100k ops/s per shard unpipelined: 2-7 shards, plan 8-16 for headroom |
| WASM steps | 40% = 200k/s × 1 ms = ~200 cores (Waddles measured **2-4 ms per `db` round trip, Python component, VERIFIED**); Rust modules at ~0.1 ms ≈ 20 cores |
| Native steps | 300k/s × 20 µs ≈ 6 cores |
| Stream memory | 60 s backlog = 6M × ~1 KB ≈ 6 GB across shards |
| `gateway-in` | ~2 KB/event = 200 MB/s ≈ 1.6 Gbps; TLS at 100k req/s; batch intake cuts per-request cost |
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

- wasmtime pooling allocator and pre-instantiation; fresh memory per call kept but cheap. `.cwasm` cache keyed `{digest}-{wasmtime_abi}-{collector}`; lazy load + LRU + pinning (Waddles design: ~100-150 hot digests at 50 replicas, 250-entry LRU, ≥95% hit target; UNVERIFIED here).
- CPU fuel with epoch deadline. Waddles never built fuel (`fuel_used: 0` hardcoded, `waddles:core/bundle_executor/src/wire.rs:546,910`, `invoke.rs:809`), has `.cwasm` caching as a TODO (`lib.rs:27-30`), and parses pool knobs without using them (all VERIFIED).
- Batch/vectorized invocation: WIT `process-batch(list<event>)` for ingest/process stages. Most steps need no host call; host calls are multiplexed and batched.
- Other Waddles lessons: mount the ingress rate limit from day one; no stub returns success (Waddles links unhandled calls to non-retryable `not_implemented`, VERIFIED); do not assume gVisor; change Helm values, not `kubectl` overrides (RECON); finish the cut-over (RECON: Waddles has `.py` remnants); WASI has no threads, so `asyncio.run` fails (VERIFIED).
- Targets (placeholders): native step < 50 µs, warm WASM call overhead < 500 µs excl. module work, host-call round trip < 200 µs.

### 5B.8 Gateways, observability, autoscaling — AUTHOR'S CALLS AG

- Gateways are stateless, horizontally scalable tiers. `gateway-out`: HTTP/2 and pooling; DNS cache holding **validated, pinned** IPs (TTL capped at the record TTL, revalidated per new connection); per-tenant and per-destination limits and circuit breakers; retries with backoff + jitter budgets; vault material in a short-TTL in-memory cache only, fail closed on vault outage (6.6).
- Observability: bounded label cardinality (no tenant/run IDs as labels), head sampling at ingress + tail sampling in the collector, log sampling, OTLP batching with bounded queues that drop oldest; telemetry never in the hot path's failure domain.
- Autoscale on lag (HPA/KEDA): verify metrics-server, an external-metrics adapter, and KEDA on the gating cluster (UNVERIFIED). Envelope verification per envelope or per batch (tunable).

### 5B.9 Elder data access at scale — AUTHOR'S CALL AF

`elder.*` calls are cached and batched with epoch-based invalidation. Decision-gating caches (grants, triggers, run-as scope) are seeded and lazy-loaded, never invalidate-only. Data reads are read-through with a short TTL, invalidated from the entity event stream. Elder side needs bulk APIs (`GetMany`, cursor `SearchPage`), per-tenant/per-cell lookup limits and a circuit breaker so flows cannot overload the system of record; a read replica for lookups is a builder option. Event bursts (a scan emitting 100k events): coalesce by (trigger, resource) window, batch-trigger mode, per-flow limits, backpressure to the matcher.

### 5B.10 Licensing (open)

Scale is a paywall axis (scale/structure, not features). Which limits map to tiers (eps, concurrent runs, nodes/cells, retention, compile minutes, marketplace sources) is Q2; nothing is decided. The tier matrix gives Free/Professional one backend node per service type and Enterprise multiple (`docs/superpowers/specs/2026-08-11-module-reorg-license-gating-design.md`, VERIFIED), so multi-node scale-out is Enterprise by that model.

### 5B.11 Load-test gate — AUTHOR'S CALL AH; gating environment — AUTHOR'S CALL BA

From Phase 1, phase exits run load tests (generator: Rust or k6-style). Report counts (sent, accepted, `429`-rejected, acked, delivered, duplicates), never a bare pass; a zero denominator fails.

| Test | Pass criterion | Phase |
|---|---|---|
| Ramp to target; backpressure (overload yields `429`, not loss) | Sent = accepted + rejected; accepted = acked; zero accepted loss; p99 within placeholders | 1 |
| Scaling curve | Near-linear in cores/partitions/replicas within one cell until a named bottleneck | 2 |
| Soak of the new engine (a test; duration placeholder 4 h; production soak is not applicable, D10) | No leak or drift | 2 |
| Noisy neighbor | A hot tenant does not move healthy tenants' p99 beyond SLO | 2 |
| Chaos: kill an engine pod mid-run and the Valkey primary | No accepted-event loss | 4 |

**Gating environment (call BA):** `dal2alpha`. Per coordinator recon (RECON, not verified by the author) it already runs Cilium 1.20.1 and Tetragon 1.7.1, plus ARC runner scale sets, with no Elder namespace and no Valkey yet. Local MicroK8s alpha lacks them (UNVERIFIED for local; the chart's Tetragon is off there, VERIFIED) and `dal2-beta` is offline (per `testing.md`, as of 2026-08-21; current state unknown). dal2alpha gates Phase 1 exits f-j, network-policy negative tests, and the load tests; provisioning tasks (Phase 0): Valkey cluster mode with persistence, an Elder namespace, an artifact bucket. Whether sustaining the target needs a larger cluster is UNVERIFIED (Q1, Q21).

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

- v4.1.x prerequisites (not dropped): a per-language feasibility spike, the conformance suite, and the security gate. The v4.0 pipeline is language-pluggable so 4.1.x languages are additive (call AK). Toolchain provenance versus the PRC-origin rule is UNVERIFIED for every row and every transitive build dependency.
- **Slow start is documented, not a blocker (D14):** authoring docs and the limits table state that interpreter-embedded languages (Python, JS; later Ruby) have larger artifacts and slower cold start, with the measured Python numbers above.

### 5C.2 Contracts and SDKs — AUTHOR'S CALL AM

WIT worlds for `ingest`, `process`, `action` stages and a `function` world, versioned (Q13); the manifest per 5.5. Per language: bindings generated from WIT plus a thin idiomatic layer for typed host functions (`elder.*`, `http`, `log`, `clock`, `flags`, `diagram`). A shared conformance suite of "golden flowbundles" every language must pass (identical outputs on golden events, imports inside the world, limits enforced); a local dev/test harness; an authoring CLI.

### 5C.3 The compile pipeline is a security boundary — AUTHOR'S CALL AK

Mirrors Waddles §4.6/D27 (VERIFIED): untrusted `build` plus trusted steps, `backoffLimit: 0`, `activeDeadlineSeconds: 900`, TTL after finish. Stages follow the canonical lifecycle in 5.5 (call AX); the controller runs each as a Job step.

| Stage | Step / image | Trust | Does |
|---|---|---|---|
| 1 Fetch | `pipeline` / `fetch` Job step | Trusted, no user code | Controlled egress only: git over https **through gateway-out's pipeline** (6.5; credential injection for private repos; no dedicated mirror in v4.0). A dedicated git/dependency mirror becomes the carrier only if a mirror-based dependency pre-fetch is added later. Pinned commit SHA; size caps; archive hardening (traversal, symlinks, bombs). Feasibility of authenticated clone through the proxy: UNVERIFIED (R4) |
| 2 Source scan + vendored-dependency verify | `pipeline` / `scan` | Trusted, **no network** | Manifest validation, semgrep, secrets scan, dependency audit, license check, forbidden constructs, PRC deny-list. **Vendored dependencies required**, verified against lockfile hashes (Waddles' decided v1: "vendored deps required for sandbox builds; no mirror in v1", marketplace design Q6, VERIFIED). Rejects unpinned/unhashed deps, VCS/URL deps, install scripts, native extensions without a WASM build. **All pass before any user code executes** (Waddles D34); each scanner reports items examined, zero = failure |
| 3 Build | `builder-<lang>` | **Untrusted** (build scripts, proc macros, source generators, import-time code) | Ephemeral Job, rootless, caps dropped, read-only rootfs + scratch, seccomp, CPU/memory/disk/time limits, **no network**, no secrets, toolchain image by digest with all components pre-installed, per-language recipe with required flags (`--stub-wasi`, `--disable all`) |
| 4 Output validate/scan | `pipeline` / `publish` | Trusted | `wasm-tools validate`; world matches the declared stage; **imports restricted to the allowed WIT world per language, anything else rejected**; size cap; limits recorded; digest computed here, never in the build sandbox; SBOM + vulnerability scan; provenance (source, lockfile, toolchain, recipe, log digests). Writes to the QUARANTINE prefix |
| 5 Approve/consent | Control plane | Trusted | `permission_hash` consent; system auto-approve for first-party |
| 6 Sign | Control plane | Trusted | Ed25519 over the artifact digest the platform produced |
| 7 Publish | Control plane | Trusted | Copy to the signed-artifact prefix; executor verifies before load |

- Retained for audit: source, build log, provenance, SBOM (retention: Q15 sibling). Quotas: per-tenant build concurrency and minutes; weighted fair queue; backpressure with ETA; dedupe by input digest.
- **Artifact identity (call AY):** identity = hash(source tree + lockfile + toolchain image digest + build recipe id), the *input digest*. The platform signs the *output digest it produced*; dedupe is on the input digest. Byte-reproducibility is **not** required of Python or JS (Waddles found `componentize-py` builds non-reproducible, 5C.7). A rebuild that yields a different output digest is **recorded, not an error**; the first-signed artifact is canonical unless re-approved. Same version with a changed input digest is refused (a new version is required; no `digest_conflict` skip as in Waddles' seeder). Byte-reproducibility is required only for Rust and for opt-in snapshots.
- **Isolation floor — AUTHOR'S CALL AN:** untrusted builds need enforced default-deny Cilium + enforced Tetragon + hardened pods; gVisor/Kata optional (Waddles R3: gVisor unavailable on MicroK8s and DOKS; Elder clusters UNVERIFIED). An environment without the floor refuses untrusted builds and builds only platform-origin source. Local alpha has Tetragon off (VERIFIED), so tenant builds are disabled there.
- **First-party bundles — AUTHOR'S CALL AL (separate from D13, which covers tenant bundles):** no prebuilt path, even for first-party. First-party flowbundles use the same pipeline with a system auto-approve gate (Waddles' marketplace design unifies first-party into one pipeline).
- The compile pipeline lands with the first language set (Phase 1 builds first-party bundles through it). Tenant upload stays last (call J).

### 5C.4 Build threat model (adds to 6.2)

Malicious source and build-time code execution: stage-3 sandbox, scan before execution. Malicious or confused dependencies, install scripts: vendored + hashed lockfile, namespace pinning. Toolchain compromise: digest-pinned checksummed images, controlled updates, byte-reproducibility check for Rust, recorded output digests for others. Resource exhaustion (zip/compile bombs, macro loops): size/time/CPU/memory/disk limits, quotas. Build sandbox escape: the isolation floor, ephemeral, no credentials. Artifact tampering: publisher-computed digest, quarantine prefix, signature, verify before load. A component importing outside its world: validator rejects, executor refuses unknown imports. Log injection or secret exfil through output: sanitized logs, no secrets in the environment.

### 5C.5 Acceptance (per-language conformance)

| # | Scenario | Phase | Assertions |
|---|---|---|---|
| f | Same "hello flow" built from source in each shipped language | 1 | Passes the conformance suite; identical outputs on golden events; counts per language |
| g | Out-of-world import | 1 | Rejected at validate and by the executor |
| h | Over-limit module (time, memory, fuel) | 1 | Trapped, counted, three-strike disable |
| i | Build attempts network access | 1 | Fails; no packet leaves |
| j | Tampered artifact, bad signature, or artifact in quarantine | 1 | Refused at load; the engine cannot read quarantine; previous version keeps serving |

### 5C.6 Repo-policy note — AUTHOR'S CALL AO

The platform stays Rust. The Go Phase-Out and "no Go for security-sensitive" rules govern PenguinTech's own services; Go is only a supported USER language (v4.1.x), and the Go toolchain is build tooling inside a pinned builder image. Q20.

### 5C.7 Cold start and snapshots (D14) — AUTHOR'S CALL AJ

Speed-ups in order of preference; snapshots are NOT a baseline dependency (author's reading of D14):

1. wasmtime pooling allocator and instance pooling.
2. Precompiled `.cwasm` cache pinned to wasmtime version and GC collector (Waddles built neither; requirements here, 5B.7).
3. Lazy bundle load plus a warm-keep policy (pinned hot set).
4. Language-level tuning: trim stdlib/imports, componentize flags.
5. Pre-initialization snapshots ONLY as an opt-in, per-language, off-by-default optimization behind `workflows.snapshot-preinit`, enabled only after the safety gate below.

**Phase 1 spike (call AY):** determine, for `componentize-py` and `componentize-js`, whether pre-initialization/heap snapshotting is inherent to the build recipe and whether a no-pre-init recipe exists (UNVERIFIED for JS). The outcome decides whether Python/JS builds are non-reproducible by construction (tolerated by artifact identity, 5C.3) or can avoid snapshots.

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
| Captured non-determinism (RNG seeds, time, env) | Ban init-time randomness/clock/env reads; entropy canary: a bundle reading random at init must differ per instance |
| Secrets or tenant data baked in: a snapshot is shared by digest across tenants, so a leak is cross-tenant | Build sandbox has no secrets or tenant data; scan init code and output; secret-canary test |
| Staleness vs wasmtime/runtime version or collector | Cache key includes both; mismatch discards and recompiles |
| Per-instance state sharing (identity-hash seeds, object order) | Each instance gets its own copy-on-write memory; determinism lint |
| Build non-reproducibility (Waddles' failure) | For an opt-in snapshot, N independent rootless builds must give byte-identical digests; no privileged ASLR changes |
| Capability tree-shaking (eager-import trap) | Import all capabilities at SDK top level; a real-wasm test per capability; guards fail loud |
| Silent degradation | No default-on-missing fallbacks (A3) |

**Safety gate before any opt-in (all required, per language):** (i) byte-identical digests across N independent rootless builds; (ii) conformance plus a real-wasm capability-presence test; (iii) the entropy and secret canaries pass; (iv) a measured benefit over the `.cwasm` cache above a stated threshold (placeholder); (v) a per-bundle opt-in recorded in the manifest and consent; (vi) flag default OFF with a kill-switch; (vii) security sign-off.

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
| Tenant-private git repo | Tenant's own; pinned SHA + admin ack | Tenant admin | Credentials in the vault, read-only deploy key, injected by gateway-out |
| Community / third-party | Highest scrutiny; opt-in by tenant admin; signature required | Tenant admin, after PenguinTech listing review (Q9) | Public https |

Waddles contrast: global-admin-only sources, tenant-private deferred, no git credentials in v1 (VERIFIED). Elder's owner decision (D15) names a git `bundles/` folder; tenant-private and credentials via the vault are the author's extension.

**Source authenticity:** git-native signed commit/tag by a key in the source's registered trust anchors and/or `bundle.sig`; required for official and community, optional for tenant-private. The primitive is the builder's choice. It is an attestation only and never replaces compiling on our infra, approval, or platform signing; the executor trusts platform keys only (Waddles `signing.rs`, VERIFIED).

**Flow (the canonical lifecycle of 5.5):** (1) add source (repo URL + path + ref) → (2) pin to an immutable commit SHA, never a branch or tag at runtime → (3) fetch → (4) validate layout, source scan, vendored-dependency verify → (5) build on our infra → (6) output validate/scan into quarantine → (7) permission diff + approval/consent (`permission_hash`) → (8) platform-sign → (9) publish to the signed-artifact prefix → (10) available as `uses:` by digest.

**Updates:** a new commit SHA is recorded as pending; the approved version keeps running; re-fetch, re-scan, re-build, permission-diff, re-approve. A permission increase always needs fresh consent; no auto-upgrade. A force-push that orphans a pinned SHA sets the source to error with the pin intact and alerts (Waddles §4.5).

**Sync and limits:** poll with jitter (cadence builder's, placeholder 15 min) plus an optional provider push webhook through gateway-in; per-source minimum interval, per-tenant source and concurrent-scan caps, backoff. **Git egress** goes through gateway-out (https only, allowlisted hosts, SSRF guard, DNS pin, CGNAT excluded); no direct git egress from the controller or pipeline (Waddles §4.3, VERIFIED). The fetcher runs `git` with no shell, `--depth 1`, no submodules, LFS, or hooks, https-only protocol; limits on clone bytes, file count, single-file size, path depth, wall-clock. The platform assigns a source-scoped namespace so a source cannot shadow another's or first-party names.

**Supply chain and audit:** dependency manifests checked against a central PRC/sanctioned deny-list; vendored dependencies hashed; audit rows for add/remove/rescan, SHA, scan counts, approvals, signatures (no raw credentials, no PII). Tier gating of sources: Q2.

Scenarios (k) add a source and publish a bundle by digest; (l) update with a permission increase is held for re-consent while the old version runs; (m) a malicious repo (symlink, traversal, oversize, unknown field, shadowed namespace, bad signature) is rejected with counts. All Phase 4.

---

## 6. Security & threat model for untrusted user-authored modules

### 6.1 Posture

Assume the WASM sandbox will eventually be escaped. An escaped executor reaches only its own memory, a read-only scratch, and the engine's UDS (every call re-authorized against the module's grants). It holds no secrets or SA token and has no network path of its own beyond the pod policy. **Trust premise:** producers sign run-start envelopes; the engine verifies, extends only via MAC-chained continuations, and never starts runs (calls X, AT).

| # | Layer | Stops |
|---|---|---|
| 1 | Role separation by image: the engine and executor images have no vault, ingress, or K8s Job code; the executor container holds no secrets; the engine can read only the signed-artifact prefix | Credential theft; cross-role leverage; loading unsigned code |
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
| Egress exfiltration; credential theft | Declared allowlist, consent, 6.5, tenant denylist; credentials never in sandbox, injected at egress, host-bound (6.6) | Exfil to an approved host; compromised gateway-out |
| WASM escape | Layers 1-9 | Host-kernel syscall surface |
| Forged ingress; compromised `gateway-in` or engine minting | 6.5a; per-role keys and envelope kinds; continuation chain (AT) | Key compromise; a compromised engine extends only runs it already holds |
| **Confused deputy** | Run-as principal; scopes = approval ∩ team grants; initiator audit-only (call Y) | None |
| Supply chain; malicious marketplace source | Signed, digest-pinned bundles; quarantine prefix; platform keys only; fail closed; 5C.4; 5D validator, pinned SHA, compile + consent | DB + signing key compromise; build sandbox escape |
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

Per role, default-deny `CiliumNetworkPolicy`, own ServiceAccount and (where it has one) SPIFFE ID. `gateway-out` `toEntities: world` excludes RFC 1918, link-local, loopback, **CGNAT 100.64.0.0/10**, multicast, reserved, IPv6 ULA/link-local, and metadata addresses (the flows-invoker policy does not exclude CGNAT, RECON). The engine's policy allows Valkey, `gateway-out`, Elder internal API, and object storage; the executor's allows nothing but the UDS. Tetragon (call G): enforced `TracingPolicy` per image role, no blanket exemption like `values.yaml:629-632`; runner and builder Jobs get named profiles.

### 6.5 Egress pipeline (`gateway-out`)

Adapted from Waddles §8.2 (VERIFIED); + = Elder additions. Each denial has a reason code and counter.

| # | Check | Reason |
|---|---|---|
| 1 | Scheme `https` (git over https; others by explicit allowlist, carrying over `gitops.py:62`) | `scheme_not_allowed` |
| 2 | Clean parse; no embedded credentials; normalize IP-literal forms (decimal, octal, hex, IPv4-mapped, NAT64, 6to4) first + | `malformed_url` |
| 3 | Host in the run's approved `egress[]`; chain verified (5.7) + | `host_not_declared` |
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

**6.5a Ingress authentication — AUTHOR'S CALL AI.** Webhook URLs and schemes may change freely (D10); adopt the stricter design. Providers that sign the body (GitHub `X-Hub-Signature-256`, `flows/routes/hooks.py:29`): HMAC-SHA256 over raw bytes + timestamp window + delivery-id dedupe + a mandatory second factor (source CIDR allowlist or bearer/mTLS). Providers that cannot sign the body (GitLab token header, `hooks.py:51`): secret-token header compared in constant time + source allowlist + replay/idempotency from the provider's delivery id when present (header names UNVERIFIED) + per-source rate limit. Inbound secrets are per source, never reused, stored by the control plane under a key scope distinct from egress credentials and delivered to `gateway-in` (seeded + lazy-loaded cache, no vault access); hook URLs use an unguessable per-source identifier.

### 6.6 Credentials

**ANSWERED design (resolves #171; Phase 1 minimal, Phase 3 hardened):**
- Credentials never enter the sandbox, executor, runner pod, log, span, metric label, or a response the guest sees. A module names a secret by reference; `gateway-out` resolves and injects it just before sending and scrubs reflections. Unresolvable → `secret_unresolved`, non-retryable.
- **Envelope encryption:** per-tenant data keys (DEKs) wrapped by a platform-managed master key (KEK) held in the platform secret store (Vault / Sealed Secrets / External Secrets Operator per `security.md`); at-rest encryption mandatory; customer-managed/external KMS is the Enterprise upsell on top. **Rotation:** KEK rotation re-wraps DEKs; DEK rotation re-encrypts lazily on next access. Plaintext exists only at the `gateway-out` injection step, in memory, never logged.
- The vault is a hot dependency of `gateway-out`: short-TTL in-memory cache only; fail closed on vault outage. #171's gate is this work. Builder decides the KMS interface.
- **Phasing (call BB):** a minimal vault (store/get per-tenant credentials + `gateway-out` injection) lands in Phase 1; rotation and KMS-interface hardening in Phase 3.

**AUTHOR'S CALL M — credential-host binding:** each credential carries the hosts it may be sent to; a token bound to one host is never sent to another even if a module allowlists it. Credentials are per-tenant; the connector manifests' platform-wide `env_var` credentials (VERIFIED) are dropped. Git credentials for tenant-private sources (5D) live in the same vault.

Today `iceflows_credentials.access_token` is plaintext, writes return 501, and webhook secrets are plaintext columns (no encryption evidenced). With no production history (D10) nothing migrates.

### 6.7 Supply chain, signing

Ed25519 over a length-prefixed, domain-separated payload binding id, version, output digest, approval id; the executor path verifies the sidecar on every load before instantiation (the engine verifies, then hands bytes over; missing/invalid = refuse). The control plane signs at approval; the data plane holds public keys only; unset = fail closed at startup; `kid` rotation with overlap. The publisher computes digests, never the build sandbox; the control plane re-hashes and audits disagreement. The engine states the expected digest and reads only the signed prefix, so the store never decides what runs. `uses:` pins by digest. No prebuilt `.wasm` (D13, call AL).

### 6.8 Runner jobs (heavy steps) — AUTHOR'S CALL C

Steps WASM cannot do (git clone/merge/push, test subprocesses, image builds) run as short-lived K8s Jobs the `controller` creates and the gateway gates. Precedent: dal2alpha runs ARC runner pods (RECON). The owner said "WASM for any logic": Q3.

- One Job per step; TTL; deleted after results are collected. Input: signed job descriptor (tenant, run, step, allowlisted image digest, argv).
- No credentials and no ServiceAccount token; git credentials injected by `gateway-out`. Cilium: egress only to `gateway-out` + DNS.
- Rootless, caps dropped, `RuntimeDefault` seccomp, read-only rootfs + workspace `emptyDir`, PSA `restricted`; allowlisted pinned images; dedicated Tetragon profile.
- Limits start from today's rlimits (CPU 900 s, AS 2 GiB, nproc 4096, fsize 512 MiB). The binary allowlist (`DEFAULT_ALLOWED_BINARIES` + env scrub) is a starting point, not the boundary: `bash`/`pip`/`npm` run arbitrary code. The boundary is pod isolation + no creds + gated network.

Runner jobs execute native code: the highest-risk surface (R1). Git over the gateway with credential injection is feasible in principle; UNVERIFIED for push; a token in the pod would conflict with "no creds" (Q3).

### 6.9 Multi-tenant isolation and fairness

Tenant is bound to every run and host call (host-built scope). Per-tenant fair share and concurrency caps (5B.6); a dedicated executor pool per tier is a builder option (Q2). Precompiled artifacts shared by digest are fine (code, not data); instance memory is never reused. Telemetry carries UUIDs in logs/spans, never as metric labels.

---

## 7. Migration plan — replace, not strangle

### 7.1 Strategy — AUTHOR'S CALL F (revised on D10)

- No production history, so: no shadow-run, no production soak, no recorded production parity corpus, no rotation notices, no run/credential data migration, no long-lived dual stack.
- Keep: (i) the existing Streams/Flows tests (~6.5k LOC, RECON) as a behavioral fixture suite for the node types and promotion semantics carried forward, targeting the canonical graph schema (5.6); (ii) a short cutover on the v4.0.X line, with the old in-process engine (`apps/worker/streams`, `handle_streams`) and `apps/flows_invoker` **deleted before v4.0 GA** (no half-wired old+new at GA); (iii) schema changes without back-compat shims. Dev/demo/seed graphs are regenerated or re-seeded; webhook URLs and schemes may change.
- The role split exists from Phase 1; there is no monolith first. Untrusted upload stays last (call J).

### 7.2 Phases

Phase 0 exits only when **RLS has landed on `release/v4.0.X`** (or the owner accepts app-layer scoping in writing) and the other gates below hold.

| Phase | Scope | Exit gate |
|---|---|---|
| 0 Prereqs + spikes | `village_id` on child tables; register diagram/flow/run in the registry; `workflows.v1` + Elder internal API contracts; **executable graph schema design (5.6)**; first Rust workspace + pinned build container + CI + the nine image builds empty; penguin-libs consumption and extraction PRs started (8); event outbox design; Q7; infra assumptions verified and **dal2alpha provisioned: Valkey cluster, Elder namespace, artifact bucket (7.9)**. Spikes: single-partition throughput, fencing, pooled wasmtime cost per Tier-1 language, Elder bulk lookup | RLS landed; owner has answered Q1-Q6 or accepted their defaults (below); CI builds empty gated crates and images; spike numbers replace 5B.5 |
| 1 Role skeleton + scale spine | `gateway-in` (+ `scheduler`), `gateway-out`, `engine` + `executor`, `controller`, `pipeline` as separate Deployments/Jobs; partitions + lease + fencing via spine; pool, `.cwasm` cache, lazy load, fuel; ingress rate limit + backpressure; batched history sink + record modes; the graph schema executing; read-only `elder.*` + gate + run-as; **minimal vault + `gateway-out` injection**; run-start/continuation envelopes; outbox + relayer → scheduler → run; compile pipeline for Rust/JS/Python building first-party functions through quarantine → sign → publish; built-in marketplace source; load harness. **Spike:** is pre-init/heap snapshotting inherent to `componentize-py`/`componentize-js` builds (5C.7). Elder-side Python: internal gRPC facade (reads) + bulk lookup, machine-JWT issuer, approval/consent/signing service with first-party auto-approve, summary-ingest endpoint, outbox relayer | On dal2alpha: flag-on E2E with counts; telemetry gate; independence run (G7); baseline ramp + backpressure test; anti-goal failing-test proofs; network-policy negative tests; scenarios e, f-j |
| 2 Streams replacement | The 21 node types on the graph schema; triggers (manual, webhook, cron, entity events); `elder.*` writes; `elder.notify.send` with a real transport; connectors as optional bundles | Fixture suite; scenarios a, b, d; scaling-curve, soak, and noisy-neighbor tests |
| 3 Flows replacement | Runner jobs; promotions and approvals; vault rotation and KMS hardening (#171); Darwin-style gate as an optional step; Flows→Streams engine-native; **diagram-side prerequisites** (registry, entity-bound shapes, shape taxonomy) | Fixture suite; runner negative tests; no plaintext credential column |
| 4 Canvas, conversion, UI | UI rewritten against the new API (temporary facade removed); canvas/editor/conversion on the Phase 1 schema; marketplace sources beyond built-in; tenant approval UI; **tenant upload last** | Security review; consent; scenarios c, k-m; chaos test |
| 5 Cutover + deletion before GA | Flip `workflows.engine` default ON; delete the old engine, `flows_invoker` image/CI/Helm/NetworkPolicy/Tetragon exemption, `stream_custom_modules`; remove `workflows.engine` | Deletion gate 7.8 |
| v4.1.x (post-GA) | Go, C#, Ruby (spike, conformance, security gate); multi-cell router and directory; optional snapshot opt-in after its gate | Per-item gates |

Anything with no consumer yet (e.g. tenant upload) may slip past GA without blocking the deletion gate.

**Provisional defaults (call BB):** if the owner has not answered Q1-Q6 by the Phase 0 exit, these proceed: Q1 aggregate scope, one cell, placeholder SLOs; Q2 metering observe-only, no tier numbers; Q3 runner split as designed; Q4 canvas unification in Phase 4 on the Phase 1 schema; Q5 consume existing crates, extraction as in call E; Q6 role split as recommended. Reversal after Phase 1 costs rework; the owner's written acceptance is preferred.

### 7.3 Behavioral parity (fixture suite)

Count-verified; a zero denominator fails. Existing tests for each carried-forward node class pass against the new implementation (N examined, N passed). Triggers: manual, webhook (new scheme), signature-failure and replay. Promotions: pipeline → stages → tests/calls/reviews/approvers semantics, fail-closed review preserved. Security carry-over: SSRF, CGNAT, git scheme allowlist, rlimits, env scrub reproduced and tested. Ops: dashboards, alerts, OTel on all roles. Not carried forward: promised-but-never-built nodes (gRPC, MCP, cloud, code, ask_ai).

### 7.4 Streams' 21 node types

Proposal; the builder verifies each node by reading it (internals UNVERIFIED).

| Category | Target | Why |
|---|---|---|
| Control flow: `comparisons`, `logic_gates`, `if_then`, `switch`, `for_each`, `while_loop` | Native engine built-ins, typed, non-Turing-complete, with run caps (call N) | Graph primitives, not user logic |
| Data: `filter`, `json_transform`, `merge`, `split`, `delay` | Native built-ins (`delay` = control-plane timer or in-worker wait, 5.10) | Deterministic, bounded |
| Free-form `expression` | First-party WASM function, never in the engine process | User text; an interpreter bug must not be engine RCE |
| `http_request`, `webhook_out`, `log` | Host `http` via `gateway-out`; host `log` | One egress path |

Call N tension: D6 says "WASM for any logic"; the author reads it as tenant-authored logic, with control-flow primitives native and trusted. Connectors (31): optional bundles; the 13 PenguinTech-product ones are never core (absent product = inert, no startup/health dependency, D7). DB connectors assume an HTTP `database-ops` backend whose existence in Elder is UNVERIFIED (R9).

### 7.5 Flag plan (call AZ)

Elder's existing flag is `elder.license-enforcement` (VERIFIED); the brief specifies `workflows.*`; builder reconciles the prefix with `{product}.{feature}` first. All default OFF.

- **`workflows.engine` is a DEVELOPMENT-TIME cutover flag.** OFF = the old in-process Streams/Flows engine runs (the default during Phases 1-4); ON = `svc-workflows` runs. Phase 5 flips the default and deletes the old code; the flag is removed at GA. There is no legacy fallback and no kill-switch afterwards. The "opt-out kill-switch for core platform mechanisms" rule does not apply: with no production history (D10) and the old engine deleted before GA, there is nothing to fall back to.
- Feature flags that remain after GA (per-feature, default OFF): `workflows.elder-resources`, `workflows.entity-triggers`, `workflows.diagram-convert`, `workflows.canvas-graph`, `workflows.custom-modules`, `workflows.marketplace-sources`, `workflows.snapshot-preinit` (per language).
- Flags fall back to last-known cached value; unseen = OFF; a flag outage never crashes a run.

### 7.6 Deployment touch points (VERIFIED paths)

| Touch point | Change |
|---|---|
| `.github/workflows/docker-build.yml` (~43-44, 116, 230) | Nine image builds at v4.0: `svc-workflows-{gateway-in,gateway-out,engine,executor,controller,pipeline}` + `builder-{rust,js,python}` (one more per 4.1.x language). Remove `flows-invoker` at Phase 5 |
| `scripts/deploy-beta.sh:128` `IMAGES` | Add the entries |
| Helm (`k8s/helm/elder`) | One chart: Deployments `gateway-in`, `scheduler`, `gateway-out`, `engine` (engine + executor containers), `controller`; PDBs; HPA/KEDA objects (availability UNVERIFIED); Job templates for `pipeline` steps, `builder`, and `runner`. Reference: Waddles' chart `waddles:k8s/helm/waddlebot` (name per its D22) |
| Per role | `CiliumNetworkPolicy`, ServiceAccount, SPIFFE ID (seven, 5.3), Tetragon policy per image role (gateway-in, gateway-out, engine, executor, controller, pipeline, builder, runner); graceful drain |
| `apps/flows_invoker/Dockerfile` | Pattern reference only (pinned digest, USER 1000); deleted in Phase 5 |
| Tetragon exemption `values.yaml:629-632` | Not extended; removed at Phase 5 |
| Environments | Namespace = product name only; CI publishes beta and above; gating environment dal2alpha (call BA) |

CI cost: nine image builds, five Deployments plus three Job templates, one chart.

### 7.7 Data migration

Almost none (D10): dev/demo/seed definitions are regenerated or re-seeded; `stream_schedules` re-seeded; `stream_custom_modules` dropped; new tables via Alembic without shims; no credential or run-history migration, no rotation notices.

### 7.8 Deletion gate (before v4.0 GA)

Fixture suite and scenarios a-e green; no import of `apps.worker.streams`, `apps.flows_invoker`, or the old handlers; old images, CI entries, Helm components, NetworkPolicy, and Tetragon exemption removed; `workflows.engine` removed. No production soak (D10).

### 7.9 Assumed infrastructure — verify per environment — AUTHOR'S CALL Z

| Assumption | Needed for | Status | Task |
|---|---|---|---|
| dal2alpha as the gating cluster | Phase 1 exits, network-policy and load tests | Per coordinator recon: Cilium 1.20.1, Tetragon 1.7.1, ARC runner scale sets; no Elder namespace, no Valkey (RECON) | Phase 0: provision Valkey cluster mode (AOF/RDB, HA), Elder namespace, artifact bucket |
| S3-compatible object storage with signed-artifact, quarantine, and history prefixes and per-prefix credentials | 5.3 | UNVERIFIED (merge design lists `minio` as optional) | Phase 0 |
| Cilium + Tetragon on every cluster that runs negative tests | G2, 6.1, build floor | Off on local alpha (VERIFIED comment); `dal2-beta` offline | Run on dal2alpha |
| Valkey per-role ACL users | Calls X, AT | UNVERIFIED | Phase 1 |
| HPA/KEDA + metrics adapter | 5.9 | UNVERIFIED | Verify on dal2alpha |
| Runner/build isolation floor | 6.8, 5C.3 | UNVERIFIED | Q17 |
| Machine-JWT issuer | 5.7 | UNVERIFIED | Phase 0 decision, Phase 1 build |
| Real email transport | 5A.4 | Missing (stub, VERIFIED) | Phase 2 |

---

## 8. Waddles reuse notes

Waddles is a reference and a source of lessons; `svc-workflows` never depends on the Waddles repo (D7). Reuse is through `penguin-libs`, a shared library repo, not a product (D8).

### 8.1 Already in penguin-libs (git dependencies pinned to immutable SHAs)

| Crate | Path | Purpose | Waddles pin | Applies |
|---|---|---|---|---|
| `penguin-spine` | `packages/rust-spine` | Valkey Streams client (XADD/XREADGROUP/XACK), consumer groups, reclaim. Its two client-connection-separation rules (dedicated blocking XREADGROUP connection vs shared admin connection; Waddles spec §5.7) are the connection management | 0.1.0 @ `586b8dad` (VERIFIED); tip 0.2.0 @ `90060a9` (RECON) | Yes; no partition/lease/fencing code in 0.1.0 |
| `penguin-bundle-host` | `packages/rust-bundle-host` | Host-API frame codec + manifest validator; no network | 0.2.0 @ `b063b7ba` (VERIFIED); `bundle_compiler` pins 0.1.0 @ `69f77b1a` (RECON) | Codec yes; the validator targets Waddles `bundle.yaml` v2, generalization UNVERIFIED |
| `penguin-logging`, `penguin-licensing` | `packages/rust-logging`, `rust-licensing` | Logs + OTel; license/flag client | 0.1.0 @ `69e740ce`, `f642e069` (RECON) | Yes |
| `rust-rpc` | `packages/rust-rpc` | Name suggests gRPC helpers | Not in Waddles pins | UNVERIFIED |

"The connection library" the owner mentioned: no crate has that name; best match is `penguin-spine`. Owner to confirm (Q5). All are git-pinned, not on crates.io; `b063b7ba` and `586b8dad` exist in the local clone (VERIFIED).

### 8.2 Still in the Waddles repo: extract, do not copy

Generic cores to extract into penguin-libs (Elder-specific parts become config or its own implementation): `bundle_executor` (engine, `signing.rs`, `wire.rs`, `invoke.rs`; host capabilities are Elder's own), `bundle_capability_gate` (with a pluggable permission catalog), `egress_proxy` + `egress_assertion` + the `bundle_host_http` guard, `service_auth` (EdDSA machine JWT, `TrustBundle` trait; Elder needs its own issuer). Not extracted: `hub_client` (Waddles-specific; write an Elder control-plane gRPC client) and `bundle_active_set` (reads Waddles' schema; its header says same-repo path dependency; reimplement against Elder's control plane). Fixing the 5B.7 shortfalls during extraction benefits both products. Extracted crates contain no product code and never require another product.

### 8.3 Reuse and extraction — AUTHOR'S CALL E (revised)

- **Plan:** (1) consume the existing crates (8.1) from Phase 0 as git dependencies pinned to full immutable SHAs, vendored, never a branch; (2) the Waddles-resident generic security crates (8.2: bundle executor host, capability gate, egress proxy/guard, `service_auth`, signing verify) are **extracted to penguin-libs as an early Phase 0/1 deliverable**, owned jointly by the Elder and Waddles teams (Q5); they are authored once, in penguin-libs, and consumed from there; (3) only `hub_client` and `active_set` equivalents are written in Elder.
- **Plainly:** for the first weeks of Phase 1 the extracted security crates do not yet exist in penguin-libs. Phase 1 work on them proceeds in penguin-libs feature branches consumed by pinned SHA, so no copy lives in the Elder repo.
- **One contingency (needs explicit owner approval to trigger):** if the extraction PRs have not merged by the Phase 1 exit, the owner may approve in-repo crates in the Elder workspace with a removal date at the Phase 2 exit. Without that approval the contingency does not apply.
- Depending on the Waddles repo violates D7 and is rejected. Use the newest released commit per crate (read its changelog), not Waddles' older pins.

### 8.4 Dependency handling and risk

**`penguin-spine`, `penguin-bundle-host`, `penguin-logging` are not on penguin-libs' default branch.** `origin/main` has only `rust-licensing` and `rust-rpc` among Rust crates (VERIFIED); the others live on release branches (`release/rust-bundle-host/v0.1.x`, `v0.2.x`, `release/rust-logging/v0.1.x` seen in the local clone; `release/rust-spine/v0.1.x` RECON); the local clone is on a stale feature branch. Risks: force-push or deletion orphans a SHA; cargo-deny/audit must cover git sources; skew. Owner options: promote to `main`, tag, or publish (Q5). First Rust workspace in Elder: layout is call P.

### 8.5 Job-bus compatibility

- Elder JobBus (`shared/jobbus/core.py`): `elder:jobs:<group>`, `…:results`, `…:dead`; one JSON `data` field with int `tenant_id`; idempotency SET NX 24 h; XAUTOCLAIM; DLQ at 5 deliveries. Waddles spine / scale design: partition streams + per-app work streams, envelope with binding MAC, pending list + XAUTOCLAIM, max deliveries → DLQ, MAXLEN.
- AUTHOR'S CALL K (revised): `svc-workflows` consumes dedicated partitioned namespaces (e.g. `elder:wf:*`), not `elder:jobs:*`; legacy groups run untouched until cutover; never two consumers on one stream.
- Producer: Elder's Python control plane enqueues through the penguin-dal Valkey stream backend (`python-dal/src/penguin_dal/stream/valkey_streams.py`, RECON; Elder pins `penguin-dal==0.3.0`, Waddles 0.4.0; introducing version UNVERIFIED) or a `JobBus` adapter. Whether spine is generic over envelope contents, and the deployed Redis/Valkey version, are UNVERIFIED (read in Phase 0). Waddles' Python `core/workflow_core_module` was not reviewed and is not reusable under D7.

---

## 9. Author's calls

Every row: AUTHOR'S CALL — owner to confirm, unless a note says the owner confirmed. "Rev" = changed in this revision; "New" = added in this revision.

| ID | Call | Note |
|---|---|---|
| A | Control plane = Elder API (definitions, authz, identity, summaries); data plane = `svc-workflows`; fails closed. Run/step history and hot state live in the data plane's sink (AA) | Owner confirmed the original framing in conversation (per brief); D11 forces the revision |
| B | Modules get no direct net/fs; the gateways authorize and `gateway-out` injects credentials; resolves #171 | Owner confirmed in conversation (per brief) |
| C | Heavy CI steps run as sandboxed runner jobs; WASM for logic/glue | Q3 |
| D | **Rev:** canvas = editor on one graph model; the executable schema is designed in Phase 0, executed in Phase 1, edited in Phase 4 | Q4 |
| E | **Rev:** consume existing penguin-libs crates now; extract the Waddles-resident security crates to penguin-libs as an early Phase 0/1 deliverable (joint ownership); one owner-approved contingency only | 8.3; Q5 |
| F | Replace, not strangle: short cutover on v4.0.X; delete old engine before GA; fixture suite; no shims | D10 |
| G | Rust (tokio/axum), wasmtime + WASI 0.2, exact pins; OTel + penguin logging; RLS dependency; SPIFFE-ready JWTs; rootless, stateless; Cilium default-deny; enforced Tetragon per image role | — |
| H | Licensing for custom modules, concurrency, run/scale limits, marketplace sources left open | Q2 |
| I | **Rev:** images split by trust boundary: `gateway-in` (+ `scheduler` Deployment), `gateway-out`, `engine`, `executor`, `controller` (small separate image), `pipeline`, `builder-<lang>`; runner Jobs; seven SPIFFE IDs | 5.3; Q6 |
| J | Untrusted tenant upload ships last | — |
| K | **Rev:** dedicated partitioned streams via `penguin-spine`; Elder API sole writer of domain tables; no Postgres in the data plane | 8.5 |
| L | Function + flowbundle taxonomy; `uses:` resolves to a digest | — |
| M | **Rev:** credential-host binding (moved out of the ANSWERED vault block) | Beyond Waddles |
| N | Structured control-flow/data nodes native; free-form expressions and tenant logic in WASM | Tension with D6 |
| O | Darwin review gate becomes an optional connector step; fail closed only when a tenant marks it required | Today blocks when required and absent (VERIFIED) |
| P | Cargo workspace `apps/svc_workflows/` in the Elder repo | Separate repo alternative |
| Q | External traffic via the gateways; `elder.*` via the gated Elder adapter | Q11 |
| R | `elder.*` is the only path to Elder data; curated typed surface; `elder_unavailable` fails closed | 5A.1, 5A.3 |
| S | Address Elder objects by registry `(module, type, village_id)` | Q12 |
| T | Entity events via transactional outbox → stream → scheduler; loop guard, dedupe, limits, batch mode | 5A.6 |
| U | Email sent Elder-side; flows pass IDs only; raw addresses refused | Q19 |
| V | Minimal first-class schedule item recommended; interim via issue `due_date`; time triggers in `scheduler` | Q7, Q14 |
| W | Diagram → draft flow: one-shot fork, guided review, link back, human approval; diagram prerequisites in Phase 3 | Q8 |
| X | **Rev:** producer-signed run-start envelopes (roles `api`, `gateway-in`, `scheduler`); engine verifies, never starts runs; Valkey ACLs per role | 5.7 |
| Y | Run-as principal scoped to the owning team; scopes = approval ∩ team grants; initiator audit-only | 5.7 |
| Z | **Rev:** assumed-infrastructure table incl. dal2alpha provisioning | 7.9 |
| AA | Durability tiers; control plane out of the hot path; batched sink | Q15 |
| AB | **Rev:** v4.0 = ONE cell with a cell id in envelopes/keys; router/directory is a 4.1+ deliverable; default scope aggregate | 5B.4; Q1 |
| AC | **Rev:** default partition key hash(tenant, flow, event key), no cross-event ordering; opt-in ordered mode with a per-partition ceiling; lease + epoch fencing from Waddles' design | 5B.3 |
| AD | Ingress backpressure, bulk intake, per-tenant quotas, weighted fair queuing | 5B.6 |
| AE | Hot-path requirements: pooling, `.cwasm` cache, lazy load, fuel, batch invocation | 5B.7 |
| AF | `elder.*` cache + batch with seed/lazy-load and epoch invalidation | 5B.9 |
| AG | Autoscale on lag; bounded labels; sampling | 5B.8 |
| AH | Load-test gate with tests assigned to phases; per-language conformance acceptance | 5B.11, 5C.5 |
| AI | Webhook auth: HMAC + second factor; token-header alternative; inbound secrets in their own key scope, not via `gateway-in` vault access | 6.5a |
| AJ | Cold-start order: pooling, `.cwasm`, lazy load, language tuning; snapshots opt-in behind a safety gate; Phase 1 spike | 5C.7; Q10 |
| AK | **Rev:** compile pipeline: `fetch` Job step through `gateway-out`, scan with vendored-dependency verify, offline build, publisher validate into quarantine, approve, sign, publish; language-pluggable | 5C.3 |
| AL | **Rev:** first-party bundles also use the pipeline (no prebuilt path); separate from D13 (tenant bundles) | 5C.3 |
| AM | SDKs generated from WIT + idiomatic layer, conformance suite, harness, CLI | 5C.2 |
| AN | Untrusted builds require the enforced isolation floor; fail closed per environment | Q17 |
| AO | Go is a user language only; platform stays Rust | Q20 |
| AP | Streams for dispatch, async actions, events, results; direct mTLS gRPC for synchronous host calls; next-step hops in memory | 5.8 |
| AQ | **Rev:** `executor` is its own image and container in the engine pod; the engine holds RO artifact and WO history credentials and hands artifacts over a read-only `emptyDir` | Q6 |
| AR | **Rev:** per-role scaling model over the new role set | 5.9 |
| AS | Marketplace: layout, source kinds/trust tiers, signature attestation, pinned-SHA flow, approvers | 5D; Q9 |
| AT | **New:** continuation envelopes MAC-chained from the parent (per-run key from a role secret, tenant ‖ run id); engine can only extend; `gateway-out` verifies the chain; emit via emit-request to the scheduler | 5.7, 5.8 |
| AU | **New:** run execution/resume model: in-memory runs, checkpoints at durable boundaries, idempotency key (run id, step id), control-plane timers | 5.10 |
| AV | **New:** artifact object-store access: engine RO on the signed prefix, WO on the history prefix; quarantine prefix unreadable by the engine; one canonical lifecycle | 5.3, 5.5 |
| AW | **New:** run IDs are data-plane UUIDv7; async batched registry registration for recorded runs; modes always/sampled/on-failure/on-demand-replay, default on-failure | 5B.2 |
| AX | **New:** one artifact lifecycle shared by 5C.3, 5D, and D13 | 5.5 |
| AY | **New:** artifact identity = input digest; platform signs the output digest; non-reproducible rebuilds are recorded, not errors; reproducibility only for Rust and snapshots; Phase 1 pre-init spike | 5C.3, 5C.7 |
| AZ | **New:** flags are development-time: `workflows.engine` OFF = old engine, ON = new; Phase 5 flips and deletes; removed at GA; no kill-switch (the opt-out rule does not apply) | 7.5 |
| BA | **New:** dal2alpha is the gating environment for Phase 1 exits, network-policy and load tests | 5B.11, 7.9; Q21 |
| BB | **New:** Elder-side Python deliverables by phase (internal gRPC facade + bulk lookups, machine-JWT issuer, approval/consent/signing service, summary-ingest endpoint, outbox relayer: Phase 1; writes: Phase 2; tenant approval UI: Phase 4); minimal vault in Phase 1; provisional defaults for unanswered Q1-Q6 | 7.2 |

---

## 10. Left to the builder

- WIT worlds, host-call wire, manifest/graph schemas (fail loud on unimplemented calls; gate before every import; ABI version mandatory); `workflows.v1` and Elder internal API protos (versioned, explicit DTOs, bulk and cursor ops).
- Partition count, lease timings, shed thresholds, scheduler fairness, pool sizing, fuel budgets, run caps, `T_short`: from spikes, not guesses. Idempotency window and dedupe structure (60M keys for a 10-min window at 100k eps is large, UNVERIFIED); envelope signing granularity.
- History sink, retention, summary batching (Q15); expression and loop semantics (fixture suite is the oracle); canvas editing model, conversion palette, schedule-item shape (Q7, Q8); outbox mechanism (must cover the discovery worker); machine-JWT issuer; cached-grant staleness bound.
- Per-language recipes, signature primitive, sync cadence (5C.3, 5D; check PRC-origin); runner log/artifact return and authenticated clone/push via `gateway-out` (prove early, R4); Helm chart, Cilium/Tetragon content, KEDA; metric names and usage metering (no tenant/run IDs as labels).
- Tests: fixture suite, flag-on E2E, telemetry counts, negative sandbox tests, independence run, load gate, 90%+ coverage.
- Flag prefix reconciliation; Rust logging conformance: `testing.md` says Rust asserts `tracing`, recon says `penguin-logging` exists on a release branch: assert both, flag the stale rule.

---

## 11. Open questions for the owner (most important first)

| # | Question | Default if unanswered |
|---|---|---|
| Q1 | **Scale definition and SLOs:** is 100k eps per tenant, per cell, or aggregate across a multi-tenant deployment? Does the 4.0 claim require one cell to sustain it, or is multi-cell (4.1+) acceptable? Latency/loss SLOs? | Aggregate; one cell in 4.0; placeholder SLOs |
| Q2 | **Licensing and tier mapping** for custom modules, concurrency, run limits, scale limits (eps, concurrent runs, nodes/cells), compile quotas, marketplace sources, history retention. Frame: scale/structure/premium capability | Metering + `@enforce_limit` hooks, observe-only; no numbers |
| Q3 | **Confirm the heavy-step runner split (call C)**; a short-lived scoped token in a runner pod if gateway injection for push is infeasible? tenant-supplied runner images? | As designed; no token; allowlisted images |
| Q4 | **Confirm canvas = editor on one graph model (call D)**, with the schema fixed in Phases 0-1 | Unify in Phase 4 on the Phase 1 schema |
| Q5 | **Shared libs:** is "the connection library" `penguin-spine`? Who owns the extraction of the Waddles-resident security crates into penguin-libs (jointly owned, early Phase 0/1) and Waddles' migration? Promote spine/bundle-host/logging to `main`, tag, or publish? Which SHA? | Consume 8.1 by SHA; extraction per call E |
| Q6 | **Role split:** confirm split by trust boundary; `controller` as a separate small image vs inside an Elder-side worker; scheduler in the `gateway-in` image vs its own; executor container in the engine pod vs a separate Deployment | Calls I and AQ |
| Q7 | **Schedule-event model:** build a first-class schedule item, or scope to existing structures (issue `due_date`, on-call)? | Interim `due_date`; recommend a minimal item |
| Q8 | **Diagram conversion:** one-shot vs live-linked; which node-type mapping/palette is v1? | One-shot fork; palette 5A.7 |
| Q9 | **Marketplace:** confirm source kinds and trust tiers; who approves (tenant admin for private, PenguinTech review for shared); an official repo separate from the built-in; tenant-private in v4.0 or later; community sources at all? | 5D table |
| Q10 | **Snapshot history:** describe the specific Waddles snapshot problems if more than 5C.7 records; confirm snapshots stay opt-in only | Opt-in only, gated |
| Q11 | **Gateway scope:** external only, or also control-plane and `elder.*` traffic through a gateway role? | External only; gated Elder adapter |
| Q12 | **Object addressing:** `village_id`, or add a UUID `public_id` Elder-wide? | `village_id` |
| Q13 | **ABI versioning:** WIT semver, support window, who migrates user modules | `abi` in manifest; N and N-1 |
| Q14 | **Streams cron executor** in scope? Now part of melding | In scope, Phase 2, in `scheduler` |
| Q15 | **History sink and retention:** approve object storage and/or a columnar store (merge design fixed Postgres + Neo4j + Redis); retention for history, source, build logs | Object storage + Postgres index |
| Q16 | **Runs (serverless) module:** in or out? | Out; not precluded |
| Q17 | **Isolation floor:** is hardening + Cilium + Tetragon enough for runner jobs and builds, or require Kata/gVisor? | Hardening stack; untrusted builds refused where absent |
| Q18 | **Optional integrations and private-host egress:** which of the 13 product connectors ship first-party; Darwin per call O; per-tenant private-host egress opt-in? | Optional bundles; opt-in off |
| Q19 | **Email transport:** none exists (stub). Provider/SMTP and owner? | Build in Elder API, Phase 2 |
| Q20 | **Go as a user language:** confirm the Go Phase-Out rules do not apply to user flowbundles or the Go builder image | As 5C.6 |
| Q21 | **Gating environment:** confirm dal2alpha for Phase 1 exits and negative/load tests, and that `dal2-beta` being offline is expected; is a larger cluster available for the load ceiling? | dal2alpha; larger cluster needed for the ceiling (UNVERIFIED) |

---

## 12. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Runner jobs execute native code: highest-risk surface | 6.8; no creds; gated egress; Kata escalation (Q17) |
| R2 | Build executes user code; interpreter languages have large components and slow cold start (Python 21.6 MB, 3.3-4.5 s uncached, VERIFIED) | `.cwasm` cache, pooling, no-network build, quarantine; slow start documented (D14) |
| R3 | Node reimplementation drifts | Fixture suite from existing tests |
| R4 | Authenticated clone/push through `gateway-out` may not work (forces a token into a pod) | Phase 1 spike (Q3) |
| R5 | Noisy neighbor on shared executors | Weighted fair dequeue, per-tenant caps, optional dedicated pool |
| R6 | Scope: roles + scale + canvas + languages + compile + marketplace + melding; nine images | Phases; gated exits; upload last; deletion gate may exclude it |
| R7 | Fail-closed couples runs to Elder API and the control plane | Bounded-staleness caches; HA; typed errors |
| R8 | wasmtime CVE/upgrade invalidates the artifact cache | Exact pin + patch runbook; cache key includes ABI |
| R9 | DB connectors assume an HTTP `database-ops` backend that may not exist; WASM cannot do native DB egress | Out of v1 (N7) |
| R10 | Independence erodes | Independence run each phase |
| R11 | **Capacity numbers are unverified**; one cell may not reach 100k eps | Phase 0/1 spikes; load gate; multi-cell 4.1+ |
| R12 | Hot-path coupling to Quart/Postgres creeps back | A11; batching only; review each phase |
| R13 | Lease/fencing is unbuilt (design only in Waddles); Valkey cluster ops are new for Elder | Lua fencing + dedup; chaos tests; Phase 0 provisioning |
| R14 | History sink cost; possible new datastore | Record modes; object-storage first (Q15) |
| R15 | Elder API overloaded by `elder.*` lookups; entity-event loops/storms | Caches, bulk APIs, limits, breaker; loop guard, coalescing |
| R16 | PII leak via free text or external payloads | Structured-only reads; retention off; redaction |
| R17 | No email transport; no schedule-item model; diagram prerequisites unbuilt | Phase 0/2 decisions (Q7, Q19); diagram prerequisites in Phase 3, before Phase 4 conversion |
| R18 | C# preview/RC, Go and Ruby unproven; PRC-origin provenance unchecked | Per-language gates (D12); provenance review |
| R19 | `penguin-*` crates only on release branches; security-crate extraction needs another team and is on the Phase 1 critical path; first Rust workspace | SHA pin + vendoring; feature-branch SHAs; one owner-approved contingency; empty gated crate in Phase 0 |
| R20 | Many roles and nine images add operational and CI complexity | One cell; shared chart; per-role scaling table |
| R21 | Per-envelope signature verification and per-run key derivation bind at 100k eps | Per-batch signing; derived keys cached per run |
| R22 | Engine pod's shared network policy weakens executor isolation vs Waddles' separate Deployments | No secrets in executor; Tetragon; separate Deployment option (Q6) |
| R23 | Snapshot opt-in reintroduces non-reproducible builds and cross-tenant leak risk; Python/JS builds non-reproducible by default | Off by default; 5C.7 gate; input-digest identity (AY) |
| R24 | Marketplace sources widen the build attack surface | 5D validator, pinned SHA, compile + consent, quotas |
| R25 | dal2alpha is not yet provisioned for Elder and may be too small for the load ceiling | Phase 0 provisioning; Q21 |
| R26 | Engine holds internal Valkey, Elder, and object-storage credentials | Executor split; per-prefix credentials; continuation chain limits what a compromise can forge |
