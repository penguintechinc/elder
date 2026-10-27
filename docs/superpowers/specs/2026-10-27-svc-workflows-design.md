# svc-workflows — Design Spec

**Status:** Draft for owner review (2026-10-27). Design only. No code, scaffolding, or Dockerfiles until the derived plan is reviewed.

**Goal:** Move Elder's workflow and CI/CD engines (Streams, Flows) out of the Python monolith into one new service, `svc-workflows`: a Rust system layer that runs flows inside WASM sandboxes, behind one gated gateway, so tenants can author their own modules with lowered risk to the platform.

**Architecture:** Elder API (Python/Quart) stays the control plane. `svc-workflows` (Rust) is the data plane: engine, gateway, credential-less WASM executor, and a launcher for short-lived sandboxed runner jobs. The diagram canvas is the editor for the same graph that executes.

**Tech stack:** Rust (tokio/axum, wasmtime component model + WASI 0.2), gRPC to the control plane, Valkey/Redis Streams for run dispatch, shared `penguin-libs` Rust crates (git-pinned), React Flow canvas (`@xyflow/react` 12.3.5, already in `web/package.json`), Helm only.

---

## 0. Reading guide

| Marker | Meaning |
|---|---|
| **DECISION** | Made by the product owner. Treat as fixed. |
| **AUTHOR'S CALL — owner to confirm** | The spec author chose it so the doc is plannable. Builder may proceed on it but must not treat it as settled. |
| **VERIFIED** | Read in code or docs by the author. Elder = `origin/release/v4.0.X` @ `bbd5ba7`; Waddles = `ae935412`. |
| **RECON** | Taken from the owner/recon brief; not re-read by the author. |
| **UNVERIFIED** | Could not be verified. Builder must check before relying on it. |

Paths are relative to the Elder repo unless prefixed `waddles:` or `penguin-libs:`.

---

## 1. Intent & why

### 1.1 Intent

- One service presentation that combines three things users know:
  - Lucidchart (diagrams) — Elder's existing diagrams layer.
  - n8n / Zapier (event in, process, action out) — ingest / process / action flow bundles.
  - GitHub Actions (CI/CD workflows plus reusable `uses:` functions).
- All tenant-authored logic runs as WASM. Platform code never executes user code natively.
- All traffic into and out of the service passes one gateway that we gate.
- Elder and `svc-workflows` work fully with no other PenguinTech product present.

### 1.2 Why now

| Problem today | Evidence |
|---|---|
| Two engines, ~15k prod LOC, moderately entangled, no WASM, no Rust | RECON; `find . -name Cargo.toml` returns nothing (VERIFIED) |
| Streams runs tenant-authored graphs in-process inside the shared worker | `apps/worker/jobs/registry.py:206` `handle_streams`; `apps/worker/streams/executor/playbook_executor.py:163` (VERIFIED) |
| Flows runs git and arbitrary test commands in a process-level sandbox only; Tetragon explicitly exempted | `apps/flows_invoker/sandbox.py`; `k8s/helm/elder/values.yaml:629-632` (VERIFIED) |
| Users cannot safely extend: `stream_custom_modules.code_blob` exists as a table but nothing executes it | `apps/api/modules/streams/models/streams.py` (`StreamCustomModule`); grep finds no other reference (VERIFIED) |
| Credentials are plaintext; credential writes return 501 | `iceflows_credentials.access_token`; `apps/api/modules/flows/routes/credentials.py:100-106,152-161` cites gate #171 (VERIFIED) |
| Promised node types never built (gRPC, MCP, cloud, code, ask_ai) | `docs/design/2026-07-07-elder-platform-merge-design.md` (VERIFIED the doc promises them) |
| `stream_schedules` has a cron model and no executor | `streams.py:263` model; no consumer found (VERIFIED by grep, not exhaustive) |
| Egress controls are fragmented; one has a self-admitted gap | `apps/worker/streams/nodes/net_guard.py` docstring: DNS-rebinding between check and connect not pinned (VERIFIED) |
| No per-tenant concurrency limits; no alerts/dashboards for either engine; `apps/flows_invoker` and `apps/mcp` have no OTel | RECON |

### 1.3 What exists today (VERIFIED unless marked)

| Piece | Where | Notes |
|---|---|---|
| Streams API | `apps/api/modules/streams/` (~3.4k LOC, RECON) | Tables `stream_playbooks/nodes/edges/versions/webhooks/executions/node_executions/schedules/shares/editor_locks/templates/forms/form_submissions/node_metadata/custom_modules/approval_gates` |
| Streams triggers | `routes/streams.py:1006` manual execute; `routes/hooks.py:23,48` HMAC webhook | Cron: model only |
| Streams executor | `apps/worker/streams/` (~5.7k LOC, RECON); 21 node classes in `nodes/{actions,conditionals,transforms}` | `expression.py:113` AST whitelist evaluator |
| Streams connectors | `apps/worker/streams/connectors/manifests/` — 31 YAML manifests | 13 PenguinTech-product connectors (`darwin`, `waddlebot`, `waddleai`, `nest`, `killkrill`, `marchproxy`, `squawk`, `tobogganing`, `skauswatch`, `articdbm`, `iceshelves`, `elder`, `waddleperf`) + 18 DB connectors. HTTP-manifest style; auth via `env_var` |
| Flows API | `apps/api/modules/flows/` (~3.4k LOC, RECON) | Tables `iceflows`, `_stages`, `_stage_approvers`, `_stage_tests`, `_stage_calls`, `_stage_reviews`, `_credentials`, `_promotions`, `_approvals`, `_webhooks`, `_executions` (reserved, per model docstring) |
| Flows triggers | `routes/promotions.py:465` manual; `routes/hooks.py:29,51,74` GitHub/GitLab | |
| Flows executor | `apps/flows_invoker/` (~1.8k LOC, RECON): `main.py:27` Redis group `flows`; `executor.py:389` `execute_promotion_pipeline`; `gitops.py:197,257`; `sandbox.py` | Darwin AI review gate (`review.py`) is fail-closed when a stage marks it required |
| Flows → Streams coupling | `calls.py:79-195` inserts into `stream_executions` directly | Hard to extract as-is |
| Job bus | `shared/jobbus/core.py` | `elder:jobs:<group>`, `elder:results:<group>`, `…:dead`; one JSON `data` field; constant group `workers`; idempotency key; XAUTOCLAIM; DLQ after 5 deliveries; OTel trace context in envelope |
| Streams UI | `web/src/pages/{StreamsList,PlaybookEditor,ExecutionsList,ExecutionDetail,ApprovalCenter}.tsx` (~920 LOC, RECON) | Flows has no web UI; Runs module absent |
| Diagrams | `apps/api/modules/diagrams/`: 18 `dg_` tables; `dg_shapes` (x/y/w/h, `style_json`, `elder_entity_id` soft numeric ref), `dg_connectors` (start/end shape) | Real-time collab: `routes/collab.py` — WebSocket + Redis pub/sub, 60 s ticket in Redis |
| Reference registry | `apps/api/models/references.py` (`Reference`); `apps/api/api/v1/lookup_village_id.py` | Diagrams/streams/flows not yet registered (RECON; issue #165 Phase 2 open) |
| Licensing | `apps/api/common/licensing/{limits,counters,enforce,tier_gate}.py`; `@enforce_limit`, flag `elder.license-enforcement`, observe-only by default | |
| Egress controls to carry over | `net_guard.py:21` (private + CGNAT blocked, redirects off); `gitops.py:62` git scheme allowlist; `sandbox.py` binary allowlist (25 entries incl. `bash`, `sh`, `pip`, `npm`), rlimits (CPU 900 s, AS 2 GiB, nproc 4096, fsize 512 MiB), env scrub; `templates/networkpolicy-flows-invoker.yaml` | NetworkPolicy is plain `networking.k8s.io/v1`, not Cilium. Whether it excludes CGNAT: RECON says no |

### 1.4 Relevant issues

- #168 (open): Phase 4b Streams, Flows, Runs, ~50% (RECON).
- #171 (open): Phase 6 compliance / KMS encryption; the credential gate in `credentials.py` cites it.
- #165 (open): Phase 2 gaps, incl. the reference registry lacking diagrams/streams/flows.
- No `svc-workflows` issue and no Streams/Flows spec exist under `docs/superpowers/specs/`.

---

## 2. Goals / non-goals / anti-goals

### 2.1 Goals

| # | Goal | Verified by |
|---|---|---|
| G1 | Tenant-authored logic runs only as WASM components with no ambient authority | Negative tests: guest cannot open a socket, read a file, or read env; assertions counted |
| G2 | One gateway is the only way into or out of the service; every crossing is authorized per tenant, license tier, and allowlist | Network policy test: no other pod/port reaches or leaves; denial metrics non-zero in the test run |
| G3 | Signed, versioned bundles for ingest / process / action; reusable functions referenced via `uses:` | Unsigned/tampered/unpinned bundle refused; counted |
| G4 | The canvas edits the same graph that runs; diagrams and flows cross-link | One graph document round-trips canvas → publish → run → canvas overlay |
| G5 | Parity with Streams and Flows, then delete the old code | Phase gates in section 7 |
| G6 | Credentials never enter the sandbox; encrypted at rest; closes #171 for workflow credentials | Plaintext columns empty; scan of guest memory/logs/spans finds no secret; count-verified migration |
| G7 | Independence: works with no other product; optional integrations degrade gracefully | CI job runs the full suite with every other product absent |
| G8 | OTel logs + metrics + traces; per-tenant usage metering | Smoke test asserts ≥1 log record, ≥1 metric point, ≥1 histogram, ≥1 span; counts printed |
| G9 | Fail closed when the control plane is unreachable | Chaos test: kill control plane; new runs denied, no stale-grant allow beyond the bound |

### 2.2 Non-goals

| # | Non-goal | Why |
|---|---|---|
| N1 | A shared platform service for other products | Owner: serves Elder only |
| N2 | Rewriting Elder's control plane | Definitions, authz, run history, tenant/identity stay in Quart |
| N3 | A general container scheduler | Runner jobs exist only for steps that cannot run in WASM |
| N4 | Per-tenant Postgres RLS | Separate Elder PR in progress (number UNVERIFIED — not in the open-PR list when checked); dependency, not scope |
| N5 | Changing the license tier model | Paywall gates scale/structure, not features |
| N6 | A public marketplace storefront | Default out of scope pending Q6 |
| N7 | Native DB-protocol egress (Postgres wire etc.) | v1 connectors are HTTP; see risk R9 |
| N8 | The Runs (serverless) module | Absent today; design does not preclude it; Q9 |
| N9 | C# or other SDK languages | v1 SDKs: Python, Rust, JS only |

### 2.3 Anti-goals (must never happen)

| # | Anti-goal |
|---|---|
| A1 | A user module reaching the network, filesystem, or credentials directly |
| A2 | A cross-product dependency: compile-time, runtime, deploy-time, or data |
| A3 | A silent fallback or stub path (an unimplemented host call returns a loud `not_implemented`, never fake success) |
| A4 | A gate that cannot fail (`\|\| true`, zero-denominator "pass", unmounted check) |
| A5 | A dual stack that never ends (old engines are deleted at cutover) |
| A6 | Secrets or PII in spans, metric labels, logs, bundles, or the sandbox |
| A7 | Guest-supplied tenant, run, or identity fields |
| A8 | Reproducing the Waddles shortfalls listed in section 8.6 |

---

## 3. Constraints

| Rule | Source | Implication here |
|---|---|---|
| In-line-of-traffic, security-sensitive, agents/CLI = Rust; control plane = Python 3.13 + Quart | `critical-rules.md` Data Plane; `general.md` Language Selection | `svc-workflows` is Rust; Elder API stays Quart |
| No Go | Go Phase-Out | None |
| No PRC-origin deps, images, models, Actions | `general.md` Supply Chain | Applies to every crate, base image, toolchain |
| All deps pinned to immutable refs, cryptographically verified | `critical-rules.md` Dependency Pinning | `Cargo.lock` + `cargo vendor`; git crates by full commit SHA; images by digest; `wasmtime` by exact `=` version |
| Rootless and stateless | Rootless Containers | `USER` non-root, read-only rootfs; no durable local state; `.cwasm` cache is a disposable `emptyDir` |
| OTel logs + metrics + traces, `OTEL_*` env only, dead exporter never breaks the app | Observability | Plus penguin logging (section 8.1) |
| PostHog flag per feature, default OFF; license gate; domain bypass only | Feature Flags | Flag plan in section 7.5 |
| No stubs; flag-on E2E | `devops.md` No-Stubs | `enforcing-no-stubs` gate in every phase |
| PII tokenization; UUID across boundaries; int enum codes; no string references | Entity Identity & Typing | Runs, bundles, functions carry `public_id` UUID; status/kind are int codes; Elder API is the PII boundary |
| Every Elder object has a unique `village_id` | Project rule (memory) | New tables carry it; child tables of streams/flows lack it today (RECON) |
| Tenant is a hard boundary; tenant middleware first; client cannot set tenant | `security.md` | Tenant bound to every run and verified at every hop |
| Service-to-service: gRPC, every call carries a short-lived signed JWT; SPIFFE-ready | `backend.md`; `security.md` | Section 5.7 |
| REST gets OpenAPI 3.x; docs endpoints authenticated except login; gRPC versioned by package + `api_version` | `backend.md` | Gateway ingress HTTP surface needs `utoipa` spec; control-plane proto `workflows.v1` |
| Explicit response schemas, never raw model objects | `security.md` Output Validation | Applies to every Elder API route that facades the service |
| 90%+ coverage, all languages, builds fail below | `critical-rules.md` Coverage | `cargo llvm-cov` gate (tool choice is the builder's) |
| Default-deny `CiliumNetworkPolicy`; Tetragon enforced; PSA `restricted` | `security.md` Kubernetes Network Security | Section 6.4. Note: existing flows-invoker uses plain NetworkPolicy |
| Helm only; kustomize is gone (#206) | `devops.md`; RECON | Touch points in section 7.6 |
| Builds run in a pinned container toolchain (house practice; the exact Rust rule text was not checked — UNVERIFIED) | `general.md` Build & Deployment | First Rust crate in this repo (VERIFIED no `Cargo.toml`) |
| Independence mandate | Owner (D7) | Section 4; enforced by test G7 |

---

## 4. Decisions (owner's)

| # | Decision |
|---|---|
| D1 | Move Elder's workflow and CI/CD functionality into its own image/container, `svc-workflows`: a Rust system layer that runs flows inside WASM environments |
| D2 | Product shape: "Lucidchart + n8n/Zapier + GitHub Actions in one service presentation." The existing diagrams layer (from IceCharts; Elder's Lucidchart alternative; 18 `dg_` tables, React Flow canvas, real-time collab over WebSocket + Redis) must be usable and cross-used within workflows. Owner correction: closer to GitHub Actions than to Ansible — CI/CD workflows and reusable "functions" |
| D3 | Flow bundles for different services covering ingest, processing, and actions — signed, versioned units. A reusable function is like a GitHub Action `uses:` |
| D4 | A unified gateway layer for ingress/egress out of the system, which we gate. It is the only way in or out |
| D5 | Take notes from the Waddles data plane (section 8) |
| D6 | WASM is for any "logic". Intent: let users create custom modules with lowered risk to the underlying system. Bundles are user/tenant-authored, untrusted code |
| D7 | Independence mandate: all PenguinTech products are independent with no cross-dependencies. Other products may trigger EXISTING workflows through webhook or API, and may offer simple optional integration for "better features", but nothing may depend on them. Elder and `svc-workflows` must work fully with no other product present and degrade gracefully if one is absent or down. `svc-workflows` serves Elder only |
| D8 | (Owner direction, 2026-10-27) Reuse the shared `penguin-libs` Rust crates the Waddles data plane uses; do not reimplement them and do not depend on the Waddles repo. Details and the parts still open: call E |

---

## 5. Architecture

### 5.1 Overview

```
  Browser (React Flow canvas)         Other products / customer systems (webhook, API)
        |  REST /api/v1                         |  HTTPS
        v                                       v
 +-------------------------+            +--------------------------+
 | Elder API (Quart)       |            | GATEWAY-IN               |   svc-workflows image,
 | CONTROL PLANE           |            | authN raw bytes, replay, |   run mode: gateway
 | definitions, versions,  |            | rate limit, 2nd factor   |
 | run history, authz,     |            +------------+-------------+
 | tenant/identity, vault, |                         | event
 | license, signing        |                         v
 +----+-------------+------+            +--------------------------+      +-------------------+
      | gRPC        | enqueue           | ENGINE                   |<---->| EXECUTOR          |
      | (machine    v                   | run scheduler, graph     | mTLS | wasmtime host,    |
      | JWT)   Valkey/Redis Stream ---->| walker, capability gate, | host | credential-less,  |
      |        (penguin-spine)          | quotas, checkpoints      | call | own Deployment    |
      |                                 +-----+--------------+-----+      +-------------------+
      |                                       |              | launch
      |                                       v              v
      |                          +----------------+   +--------------------+
      +------------------------->| GATEWAY-OUT    |<--| RUNNER JOBS        |
        grants, secrets-by-ref   | allowlist, SSRF|   | git / test / build |
        run events               | pin, cred      |   | ephemeral, no creds|
                                 | injection      |   +--------------------+
                                 +-------+--------+
                                         v
                                   Internet / customer APIs

  Object storage: signed bundle artifacts (executor reads, read-only)
```

- Control plane = Elder API. Data plane = `svc-workflows`. AUTHOR'S CALL A.
- One image, several run modes, separate Deployments so the executor holds none of the engine's or gateway's secrets. AUTHOR'S CALL I.
- Gateway scope is external traffic. Control-plane gRPC is a separate mutually authenticated channel. AUTHOR'S CALL Q.

### 5.2 Control plane (Elder API)

| Owns | Notes |
|---|---|
| Definitions: graphs, versions, functions, bundles, triggers, schedules | Authoring store; existing `stream_*` / `iceflows_*` tables until Phase 4 |
| Run history and approvals | Engine reports events over gRPC; Elder API is the only writer of domain tables |
| Authz: OIDC scopes, tenant, teams | Data plane never decides authz on its own; it enforces grants the control plane issued |
| Identity and PII | Users appear to the data plane as UUIDs only |
| Credential vault | Envelope-encrypted; values never leave except to the gateway-out by reference |
| License and flags | Tier resolved at tenant/team scope; limits are license-server-configurable |
| Signing | Signs approved bundle digests; private key never in the data plane |
| Facade | `/api/v1/streams/*` and `/api/v1/flows/*` keep their shapes during migration |

### 5.3 Data plane modes (one image)

| Mode | Holds | Network |
|---|---|---|
| `engine` | Machine identity; stream consumer credentials; no external creds | Control plane (gRPC), Valkey/Redis, executor port, launcher API |
| `gateway-in` | Raw-request authentication material (per-source secrets by ref) | Accepts external; talks to engine/stream only |
| `gateway-out` | Credential-injection material (decrypted only in memory, per call) | Internet allowlist; control plane for secret resolution |
| `executor` | Nothing of the stage's secrets; client cert; read-only bucket credential | Egress only to engine host-API port and bucket; no ingress |
| `launcher` | Kubernetes API permission to create/delete runner Jobs in one namespace | K8s API only |

- Run dispatch: Elder API enqueues a run onto a dedicated stream; the engine consumes it with `penguin-spine` (consumer group, ack, reclaim, DLQ). AUTHOR'S CALL K.
- Engine is stateless: in-flight run state lives in the stream's pending list plus checkpoints posted to the control plane. A crashed engine pod loses nothing the control plane has not recorded.
- Hot reload: the engine learns the active set (bundle/graph digests, grants) by pull + push-invalidate; the executor fetches and verifies artifacts by digest.

### 5.4 Gateway

**Ingress (gateway-in)** — every way into the service:

| Source | Mechanism |
|---|---|
| Webhook from outside (incl. other products) | Per-source HMAC over the raw bytes, verified before any parse or guest call, plus a second factor (IP allowlist, bearer, or basic) — Waddles D29. Replay window + delivery-id dedupe |
| API trigger | Authenticated call to an existing workflow's trigger endpoint; scope `workflows:execute` |
| Elder-originated (UI "run", schedule, approval decision) | Control plane enqueues; does not transit the external gateway (call Q) |
| Ingest bundles | Receive bytes the gateway already authenticated. They parse and normalize; they never hold a socket or a credential |

- Long-lived connections (websocket/IRC-style sources) are out of scope for v1. A "poll" source is gateway-scheduled and the bundle only parses the response. Builder confirms.
- Other products may only trigger workflows that already exist. They never create, edit, or read definitions through this path.

**Egress (gateway-out)** — every way out:

| Caller | Path |
|---|---|
| WASM host `http` capability | Gateway egress pipeline (section 6.5) |
| Runner jobs (git clone/push, package fetch) | Same pipeline; credentials injected by the gateway, not the pod |
| Notifications / outbound webhooks | Same pipeline |
| Callbacks to Elder API | Not egress — control-plane gRPC (call Q) |

- Operator-configured infrastructure endpoints (Valkey, bucket, control plane, OTLP) are NOT evaluated by the bundle egress guard. Provenance, not address range, is the test (Waddles §8.5).

### 5.5 Bundle model — AUTHOR'S CALL L

| Concept | Definition |
|---|---|
| **Function** | Smallest WASM component with typed input/output and a declared permission set. Versioned, signed, immutable by digest. Referenced from a node as `uses: <namespace>/<name>@<version>` |
| **Flow bundle** | Signed, versioned unit for one service integration: manifest + graph(s) + function pins + stage roles (`ingest`, `process`, `action`) + declared permissions + limits |
| **`uses:` resolution** | Authoring may name a tag/range. Publish resolves to an immutable digest and writes a lockfile into the bundle. Runtime accepts digests only — same rule as pinning Actions by SHA |
| **Ownership scopes** | Platform (Elder-shipped), tenant-private, and marketplace-shared (Q6) |

Manifest (conceptual — builder owns the schema):

| Field | Purpose |
|---|---|
| `id`, `version`, `kind` (function / flow bundle), `abi` | Identity; ABI world version (Q7) |
| `stages[]` | Roles and the graph entry points |
| `uses[]` | Pinned dependencies (digest) |
| `permissions` | `egress[]` (host + methods), `secrets[]` (reference names only), `elder[]` (typed Elder capabilities), `diagram[]`, `limits` |
| `inputs` / `outputs` | JSON Schemas |
| `license_feature` | Flag/entitlement key |

Lifecycle (int enum codes; builder fixes values):

```
draft -> built -> scanned -> published -> approved(consent) -> active -> deactivated/revoked
```

- Consent: the approver sees the full contract (hosts + methods, secret reference names, Elder capabilities, limits, scan status, ABI). Approval records a `permission_hash` over the canonical summary. Runtime authorization derives from the approval record, not the manifest (Waddles D26).
- Widening on upgrade needs re-consent against a diff; narrowing auto-approves with an audit entry. Auto-update is blocked on any permission delta.
- Revocation: pushing a revoked digest reaches the engine and executor in about a second; in-flight calls finish, further host calls in the same invocation are denied.

Proposed v1 host capabilities (WIT surface is the builder's; every import calls the gate first):

| Capability | Purpose | Notes |
|---|---|---|
| `context` | Run/tenant/trace UUIDs, inputs, config | Host-built; guest cannot set |
| `http` | Outbound request through gateway-out | Secrets by reference only |
| `log` | Structured log | Scrubbed; level capped |
| `clock` | Wall + monotonic | No other time source |
| `flags` | Read flag value | Cached last-known |
| `elder` | Typed Elder operations (create issue, add comment, read entity by UUID) | One permission per operation; calls the control plane as the run's tenant |
| `diagram` | Typed diagram operations (create/update shapes, read graph) | Per-diagram resource scope |
| `emit` | Produce output / follow-on triggers | Depth- and rate-capped |

- Deferred from v1: `kv`, `db`, `relay`. Add only with a real consumer and the gate in front.
- No `wasi:sockets`, no `wasi:filesystem` beyond an empty read-only scratch, no `wasi:cli/environment`. The executor natively implements the refusing interfaces (Waddles round-2 finding: hand-built stubs were impractical).

### 5.6 Canvas and graph model — AUTHOR'S CALL D

- One canonical graph document. The canvas is the editor; the same document is compiled, signed, and run.
- Two layers in one document:

| Layer | Content | Executed? |
|---|---|---|
| Semantic | Nodes (id, kind, config, ports), edges (port to port, condition) | Yes |
| Presentation | x/y/w/h, style, annotation shapes, swimlanes | No — engine ignores `annotation` nodes |

- Today these are separate: `stream_nodes`/`stream_edges` for execution and `dg_shapes`/`dg_connectors` for drawing. Builder designs the merge and its Alembic migration (Phase 4).
- Cross-use:

| Direction | Mechanism |
|---|---|
| Diagram → flow | Shape type `flow-ref` / step link via the Elder reference registry (needs diagrams/streams/flows registered — #165 gap) |
| Flow → diagram | Host `diagram` capability (read graph, update shapes) with per-diagram permission |
| Run overlay | Node status chips painted on the canvas; live over the existing WebSocket + Redis channel (`elder:dg:{diagram_id}`) |
| Entity links | `dg_shapes.elder_entity_id` is a numeric soft ref today; move to `village_id`/UUID through the registry |

- Editing concurrency: diagrams use collab sessions, Streams uses `stream_editor_locks`. Builder picks one model for the unified editor.
- Loop semantics (`while_loop`, `for_each`): how Streams executes them was not read. UNVERIFIED — builder reads before designing cycles in the graph.

### 5.7 Identity, transport, and fail-closed

| Concern | Design |
|---|---|
| Machine identity | EdDSA short-lived JWT (≤1 h), `sub` = SPIFFE ID `spiffe://penguintech.io/<env>/svc-workflows[-<mode>]`; SPIFFE-ready (mTLS/X.509-SVID accepted first-class). Issuer in Elder: UNVERIFIED (penguin-aaa vs new). Builder confirms |
| Sync control-plane calls | gRPC, proto package `workflows.v1`, `api_version` in every request, unknown version → `UNIMPLEMENTED` |
| Sensitive claims | Nested JWT (sign then encrypt) |
| Executor ↔ engine | mTLS, executor dials out; host calls flow back on the same connection (Waddles A3) |
| Tenant binding | Run envelope carries tenant + run + trace bound by a MAC under a key held only by engine/gateway modes; verified at every hop; failure = DLQ, never retried, counted (Waddles D30) |
| Fail closed | Grants: missing, cache-miss, or loader-error = deny. Flags and license: last-known cached value; never-seen flags OFF. Staleness bound for cached grants is a builder tunable (Q) |

---

## 6. Security & threat model for untrusted user-authored modules

### 6.1 Posture

- Assumption: the WASM sandbox will eventually be escaped. Design so an escape reaches almost nothing.
- An escaped executor can reach: its own memory, a read-only scratch, the engine's host-API port (every call re-authorized against the escaped module's own grants), and read-only bucket GETs. Not reachable: credentials, Valkey, Postgres, control plane, internet, other tenants' data.
- Sandbox layers (outermost first; each needs a negative test):

| # | Layer | Stops |
|---|---|---|
| 1 | Executor is its own Deployment; no engine/gateway Secrets mounted | Credential theft by any means |
| 2 | Pod security: non-root, `allowPrivilegeEscalation: false`, drop ALL caps, `RuntimeDefault` seccomp, read-only rootfs, PSA `restricted` | Escalation toward the node |
| 3 | Default-deny `CiliumNetworkPolicy`: executor egress only to engine host-API port + bucket; no ingress | Lateral movement, internet access |
| 4 | mTLS host API with pinned peer identity | Impersonating the executor |
| 5 | WASM component isolation: no sockets, no fs, no env | Guest reaching anything not handed to it |
| 6 | Capability gate on every host import | Calling a capability never granted |
| 7 | Per-call limits: epoch deadline, fuel, memory, instances | CPU/memory DoS |
| 8 | Three-strike disable per `(bundle, digest)` | A repeatedly tripping module degrading the service |
| 9 | Enforced Tetragon `TracingPolicy` on engine, gateway, executor | Native escape running tooling or odd syscalls |

- gVisor is not assumed. Waddles found it undeliverable on MicroK8s and DOKS (waddles spec R3, D32). Elder's clusters are the same set; availability on Elder clusters is UNVERIFIED. Kata is the named escalation (Q11).

### 6.2 Threats

| Threat | Mitigation | Residual |
|---|---|---|
| Cross-tenant read | Tenant bound per run and verified per hop; host calls scoped by host-built `InvokeScope`; fresh instance per call; Postgres RLS (separate PR) for control-plane tables | Bug in scope construction; covered by a compile-fail test (no struct literal outside the crate) |
| Data exfiltration via egress | Declared egress allowlist, consent, gateway pipeline, tenant denylist | Exfil to an approved host is accepted — approving a host approves that reach |
| Credential theft | Never in sandbox; injected at egress; credential bound to hosts (section 6.6) | Compromised gateway-out |
| WASM escape | Layers 1-9 | Host-kernel syscall surface in default posture |
| Hostile build input | Build container has no network/secrets; digest computed in a separate trusted publisher (6.7) | Container escape from the build pod |
| Forged ingress | HMAC on raw bytes + second factor + replay window + dedupe | Leaked secret and satisfied second factor |
| Confused deputy | Guest supplies no scope; scope host-built from the run envelope | — |
| Supply chain | Signed, digest-pinned bundles; platform keys only; fail-closed if keys unset | Compromise of DB and signing key |
| CPU/memory/log DoS | 6.3 | Slow-but-under-limit modules; visible in histograms |
| Run amplification (fan-out, loops, trigger cycles) | Run caps (6.3), `uses:`/trigger depth cap, per-tenant rate | — |
| DNS rebinding | Resolve once in gateway-out, validate every address, connect to the validated IP | — (section 6.5) |
| CGNAT / internal address reach | Blocked in the guard AND in the network policy | — (section 6.5) |

### 6.3 Resource limits

Starting values copied from Waddles (`waddles` master spec §7.3, VERIFIED). They are tunables to measure against Elder workloads, not commitments.

| Limit | Start | Ceiling | On breach |
|---|---|---|---|
| Wall-clock per module call | 2000 ms | 10000 ms | Epoch interrupt; step failed; trip |
| Linear memory per instance | 64 MB | 256 MB | Allocation refused; trap; trip |
| **CPU fuel per call** | builder sets by measurement | per tier | Trap; trip. **Required in v1** (6.9) |
| Concurrent calls (executor) | 32 | — | Queue to a short bound, then retryable failure |
| Egress rate per module | 10 rps, burst 20 | per tier | `rate-limited` returned to guest; not a trip |
| Egress response body | 1 MiB | — | Truncated with flag |
| Egress timeout | 5 s | — | `timeout` |
| Frame size (host API) | 1 MiB | — | Fatal protocol error; executor restarts |

Run-level caps (new for Elder; values are the builder's):

| Cap | Why |
|---|---|
| Steps per run | Stops loop bombs (`while_loop`, `for_each`) |
| Run wall-clock | Bounded cost per run |
| Output/log bytes per step and per run | Log flooding |
| Host calls per run, by kind | Abuse of `elder`/`http` |
| Fan-out and `uses:`/trigger depth | Amplification, cycles |
| Concurrent runs and queue depth per tenant | Noisy neighbor — Elder has none today (RECON) |

A manifest value above its ceiling fails validation at publish; it is never silently clamped.

### 6.4 Network policy and Tetragon

- Per mode, `CiliumNetworkPolicy` default-deny. Executor: egress to engine host-API port and bucket only. Engine: control plane, stream, executor, launcher. Gateway-in: external ingress on the serving port only. Gateway-out: internet minus the excluded ranges below, plus the control plane for secret resolution.
- Gateway-out `toEntities: world` must exclude RFC 1918, link-local, loopback, **CGNAT 100.64.0.0/10**, multicast, reserved, IPv6 ULA/link-local, and cloud-metadata addresses. The existing flows-invoker NetworkPolicy does not exclude CGNAT (RECON).
- Tetragon decision — AUTHOR'S CALL (inside G): enforced `TracingPolicy` (binary + syscall allowlist) on engine, gateway, and executor. No blanket exemption of the kind flows-invoker has today (`values.yaml:629-632`). Runner jobs get their own named profile (section 6.8).
- All inter-namespace traffic default-deny; same-namespace explicit.

### 6.5 Egress pipeline (gateway-out)

Adapted from Waddles §8.2 (VERIFIED), plus Elder additions marked +. Each denial returns a reason code and increments a counter.

| # | Check | Reason |
|---|---|---|
| 1 | Scheme is `https` (git over https; other schemes only by explicit allowlist, carrying over `gitops.py:62`) | `scheme_not_allowed` |
| 2 | URL parses cleanly; no embedded credentials; normalize IP-literal forms (decimal, octal, hex, IPv4-mapped, NAT64, 6to4) before any check + | `malformed_url` |
| 3 | Host matches the run's approved `egress[]` entry | `host_not_declared` |
| 4 | Method in that entry's methods | `method_not_declared` |
| 5 | Tenant license tier and tenant denylist permit it + | `tier_denied` / `host_denylisted` |
| 6 | Resolve once; EVERY returned address must pass: loopback, private, link-local, unspecified, multicast, reserved, **CGNAT**, ULA, metadata; deny if any address fails (not just the first) | `ssrf_blocked_address` |
| 7 | **Pin the connection to the validated IP**; TLS SNI and `Host` stay the original name; no second resolution between check and connect | `dns_rebind_blocked` |
| 8 | Credential-host binding: any injected credential is valid only for its bound hosts + | `credential_host_mismatch` |
| 9 | Per-module token bucket and per-tenant concurrency | `rate_limited` |
| 10 | TLS 1.2+ with a verified chain | `tls_verification_failed` |
| 11 | Redirects: ≤3; every hop re-runs 1-8 | `redirect_off_allowlist` |
| 12 | Response size and total timeout caps | truncation / `timeout` |
| 13 | Audit record: tenant UUID, run UUID, module digest, host, method, outcome (no secrets, no PII) + | — |

- Private-range hosts (self-hosted CI, on-prem) are reachable only via an explicit per-tenant opt-in that applies to hosts already in the approved allowlist. Loopback, link-local, unspecified, multicast, and metadata stay blocked always (Waddles `allowPrivateHosts` pattern). Whether Elder wants this: Q.
- DNS rebinding is acknowledged but unfixed in today's guard (`net_guard.py` docstring, VERIFIED). Step 7 is the fix.

### 6.6 Credentials — AUTHOR'S CALL B (+ M)

- Credentials never enter the sandbox, the executor, a runner pod, a log, a span, a metric label, or a response the guest sees.
- A module names a secret by reference (header name → reference name). The gateway-out resolves it from the vault and injects it immediately before sending, then scrubs it from any reflected response.
- Unresolvable reference → `denied(secret_unresolved)`, non-retryable.
- **Credential-host binding (AUTHOR'S CALL M):** each stored credential carries the hosts it may be sent to. Even if a module's allowlist contains an attacker-chosen host, a token bound to `github.com` is never sent there. Waddles accepts that residual; this closes it.
- Storage: envelope encryption with platform-managed keys as baseline; external KMS is the Enterprise upsell on top (`security.md`).
- This resolves the credential encryption gate (#171) for workflow credentials. Today `iceflows_credentials.access_token` is plaintext and writes return 501; `iceflows_webhooks.webhook_secret` and stream webhook secrets are plaintext columns (no encryption evidenced).

### 6.7 Supply chain, signing, build

| Item | Design |
|---|---|
| Signature | Ed25519 over a length-prefixed, domain-separated payload binding id, version, digest, approval id. Sidecar verified by the executor on every load, before instantiation. Missing or invalid = refuse, never warn |
| Key custody | Control plane signs at approval. Data plane holds public keys only. Unset keys = fail closed at startup. `kid` rotation with overlap |
| Digest authority | Computed in a trusted publisher step, never in the sandbox that executed module code. The control plane re-hashes and audit-logs any disagreement |
| Load by digest | Stage tells the executor the expected digest; the bucket alone never decides what runs |
| Build | Source builds execute module code (`componentize-py` runs top-level code at build time — Waddles R2, VERIFIED). Build container: no network, no secrets, rootless, caps dropped, read-only rootfs, ephemeral Job. Scan source (SAST, dependency audit, secrets) before any step that executes it |
| Prebuilt components | Accept or refuse: Q10. If accepted: permanent "not scanned" badge |
| Pinning | `uses:` by digest at runtime; toolchains pinned with checksums |
| Consent | 5.5 |

Build pipeline mirrors Waddles D27 (untrusted `build` / trusted `publisher`). AUTHOR'S CALL (inside I/J).

### 6.8 Runner jobs (heavy steps) — AUTHOR'S CALL C

- Steps that cannot run in WASM (git clone/merge/push, test subprocesses, image builds) run as short-lived Kubernetes Jobs that the `launcher` creates and the gateway gates. WASM handles logic and glue. Precedent: dal2alpha already runs ARC runner pods (RECON). The owner said "WASM for any logic" — owner to confirm this split (Q2).

| Property | Design |
|---|---|
| Lifecycle | One Job per step; TTL; deleted after result collected; never reused |
| Inputs | Signed job descriptor (tenant UUID, run UUID, step, image digest from an allowlist, argv from the graph) |
| Credentials | None baked in; no ServiceAccount token mounted; git credentials injected by gateway-out |
| Network | `CiliumNetworkPolicy`: egress only to gateway-out + DNS; everything else denied |
| Isolation | Rootless, caps dropped, `RuntimeDefault` seccomp, read-only rootfs + workspace `emptyDir`, PSA `restricted` |
| Limits | CPU/memory/ephemeral-storage requests and limits; wall-clock; carry over today's rlimits (CPU 900 s, AS 2 GiB, nproc 4096, fsize 512 MiB) as the starting profile |
| Binary allowlist | Start from `DEFAULT_ALLOWED_BINARIES` and the env-key scrub in `sandbox.py`. NOT the real boundary: `bash`/`sh`/`pip`/`npm` run arbitrary code. The boundary is pod isolation + no creds + gated network |
| Images | Allowlisted pinned digests only; tenant-supplied images are Q |
| Tetragon | Dedicated named profile; not exempt, not shared with the executor profile |
| Output | Exit code, bounded log, artifacts back through the engine |

- Runner jobs execute native code, not WASM. This is the highest-risk surface (R1).
- Git over the gateway with credential injection (smart-HTTP proxy) is feasible in principle; UNVERIFIED for push. Fallback needs an owner decision because a short-lived scoped token in the pod conflicts with "no creds baked in" (Q2).

### 6.9 Waddles lessons applied as requirements

| Requirement | Reason |
|---|---|
| Epoch interruption **and** fuel metering, both live in v1 | Waddles never built fuel (`fuel_used: 0` hardcoded at `waddles:core/bundle_executor/src/wire.rs:546,910`, `invoke.rs:809`, VERIFIED). Fuel is also the metering input |
| Instance pool actually implemented; pre-instantiate; fresh memory per call | Waddles parses pool knobs in `config.rs:49-60`; grep finds no implementation (VERIFIED). Fresh-per-call is the right isolation property — make it cheap, do not skip it |
| Precompiled artifact cache keyed `{digest}-{wasmtime_abi}-{collector}` | Waddles `lib.rs:27-30`: "Scaffolded with a TODO: precompiled `.cwasm` caching … every load JIT-compiles fresh" (VERIFIED) |
| Lazy load on first use; bounded resident set | RECON: Waddles eager-loading 85 bundles needed 16 Gi |
| Ingress rate limiting and body-size caps mounted from day one | RECON: Waddles generic intake endpoints were unmounted and had no intake rate limit |
| No stub that returns success | Waddles linked unhandled `overlay` calls to a non-retryable `not_implemented` (VERIFIED in capability model doc) — copy that behavior |
| Do not assume gVisor | Section 6.1 |
| Chart changes via values, not `kubectl` overrides | RECON: overrides were reverted by `helm upgrade` |

### 6.10 Multi-tenant isolation and fairness

- Tenant is bound to every run (5.7) and to every host call by host-built scope.
- Shared executors need per-tenant fair-share scheduling and concurrency caps. Whether Enterprise gets a dedicated executor pool: builder/Q1.
- Precompiled artifacts shared by digest across tenants are fine (code, not data). Instance memory is never reused across calls.
- Telemetry: tenant, run, and bundle identifiers appear as UUIDs in logs/spans, never as metric labels (cardinality). Usage goes to a metering stream and the control plane's table.

---

## 7. Migration plan

### 7.1 Strategy — AUTHOR'S CALL F

- Strangler at the API boundary. Existing Streams and Flows keep working until `svc-workflows` reaches parity, behind PostHog flags (`workflows.*`, default OFF). Then cut over and delete the old code. No dual stack forever.
- The Elder API route shapes (`/api/v1/streams/*`, `/api/v1/flows/*`) do not change for the UI. Only what executes behind them changes.
- Dual-run (shadow) only for side-effect-free graphs; never double-execute side effects.
- Untrusted custom-module upload ships last (AUTHOR'S CALL J).

### 7.2 Phases

| Phase | Scope | Flag(s) | Exit gate |
|---|---|---|---|
| 0 Prereqs | Resolve blocking owner questions (Q2-Q5). RLS PR merged. `village_id` on child tables. Register diagrams/streams/flows in the reference registry (#165). `workflows.v1` proto. First Rust workspace + pinned build container + CI. `penguin-libs` consumption decided (section 8) | — | Owner sign-off on Q2-Q5; CI builds an empty crate with cargo-deny/audit/coverage gates that can fail |
| 1 Skeleton | Image with all modes; gRPC; stream consumer via `penguin-spine`; executor; gate; gateway-out (HTTP); one first-party function end to end; fuel, pool, artifact cache, lazy load, ingress rate limit | `workflows.engine` | Flag-on E2E passes with printed counts; telemetry gate passes; every anti-goal has a failing-test proof |
| 2 Streams parity | Port the 21 node types; triggers; run history; connectors as bundles; Streams UI unchanged | `workflows.streams-on-engine` | Parity corpus green (7.3); shadow diff zero on side-effect-free graphs |
| 3 Flows parity | Runner jobs; promotions state machine; approvals; Darwin-style review gate as optional step; credential vault + gateway injection; Flows→Streams becomes engine-native | `workflows.flows-on-engine` | Parity corpus green; plaintext credentials migrated and cleared |
| 4 Canvas + `uses:` + custom modules | Graph model unification; reference-registry links; run overlay; function/bundle publish pipeline; SDKs; **user upload of custom modules** after security review | `workflows.canvas-graph`, `workflows.custom-modules` | Security review of gateway/gate/executor passes; consent flow works; build pipeline has no network |
| 5 Cutover + deletion | Flip the `on` flags to default ON by tier (kill-switch `workflows.disable-engine` stays available); then delete old code, infra, and the kill-switch | `workflows.disable-engine` | Deletion gate (7.8) |

### 7.3 Parity criteria

| Area | Criterion (count-verified; a zero denominator is a FAIL) |
|---|---|
| Streams node types | Each of the 21 classes mapped (7.4); each has a parity test comparing old vs new output on a recorded corpus; N examined and N passed printed |
| Triggers | Manual execute and HMAC webhook produce equivalent runs; signature-failure and replay cases tested |
| Run history | `stream_executions` / `stream_node_executions` equivalents populated; existing UI pages render without change |
| Approvals | Approval gate pauses and resumes; decision via Elder API |
| Flows | Pipeline → stages → tests/calls/reviews/approvers → promotions equivalent; clone/merge/test/push outcomes match; fail-closed review gate preserved |
| Security carry-over | SSRF, CGNAT, git scheme allowlist, rlimits, env scrub reproduced and tested |
| Regression | Every previously reported issue in either engine has a regression test |
| Ops | Dashboards and alerts exist (none today); OTel on all modes |

### 7.4 Streams' 21 node types

Mapping is a proposal; builder verifies each node's semantics by reading it (UNVERIFIED for node internals).

| Category (files) | Target | Why |
|---|---|---|
| Control flow: `comparisons`, `logic_gates`, `if_then`, `switch`, `for_each`, `while_loop` | Native engine built-ins, typed and non-Turing-complete, with run-level caps | Graph-control primitives, not user logic. AUTHOR'S CALL N |
| Data: `filter`, `json_transform`, `merge`, `split`, `delay` | Native engine built-ins (delay = scheduler wait, not a sleeping call) | Deterministic, bounded |
| Free-form `expression` | First-party WASM function, never evaluated in the engine process | User-controlled text; an interpreter bug must not be engine RCE. Parity corpus against the Python AST whitelist |
| Actions: `http_request`, `webhook_out` | Host `http` through gateway-out (guard moves from `net_guard.py` into the pipeline) | One egress path |
| Action: `log` | Host `log` | Scrubbed |
| Missing, promised (gRPC, MCP, cloud, code, ask_ai) | Not part of parity. New functions/bundles after Phase 2; `code` is user functions by definition | Never built |

- AUTHOR'S CALL N tension: the owner said "WASM is for any logic". The author reads this as tenant/user-authored logic, with engine control-flow primitives native and trusted. Owner to confirm.
- Connectors (31 manifests): become connector bundles. The 13 PenguinTech-product connectors are optional integrations, never core: absent product = connector inert, workflows unaffected, no startup or health dependency (D7). DB connectors: manifests assume an HTTP `database-ops` backend; whether one exists in Elder is UNVERIFIED (R9).

### 7.5 Flag plan

Elder's existing flag is `elder.license-enforcement` (VERIFIED). The brief specifies `workflows.*`; builder reconciles prefix convention with the rules' `{product}.{feature}` before first use. UNVERIFIED which is right.

| Flag | Default | Gates |
|---|---|---|
| `workflows.engine` | OFF | Data plane receives any dispatched run |
| `workflows.gateway-ingress` | OFF | Webhook/API ingress through gateway-in |
| `workflows.streams-on-engine` | OFF | Streams execution routed to `svc-workflows` |
| `workflows.flows-on-engine` | OFF | Flows execution + runner jobs routed to `svc-workflows` |
| `workflows.canvas-graph` | OFF | Unified graph editor |
| `workflows.custom-modules` | OFF | Tenant upload of untrusted modules |
| `workflows.disable-engine` | OFF | Opt-out kill-switch for the cutover state: unseen/OFF = `svc-workflows` is the engine, ON = legacy fallback. Exists only while legacy code exists; deleted in Phase 5 |

- Flags fall back to last-known cached value; unseen flags are OFF; flag server down never crashes a run.
- Rollback is a flag flip plus the legacy code still present until Phase 5.

### 7.6 Deployment touch points (VERIFIED paths)

| Touch point | Change |
|---|---|
| `apps/flows_invoker/Dockerfile` | Pattern reference only (pinned python digest, USER 1000). New Dockerfile is the builder's |
| `.github/workflows/docker-build.yml` (~lines 43-44, 116, 230) | Add `svc-workflows` to the build matrix and per-service lists |
| `scripts/deploy-beta.sh:128` `IMAGES` | Add entry |
| Helm values + `templates/deployment.yaml` component list | Add components for each mode |
| NetworkPolicy template | Replace plain NetworkPolicy pattern with `CiliumNetworkPolicy` per mode |
| Tetragon exemption `values.yaml:629-632` | Do not extend to `svc-workflows`; remove flows-invoker's at Phase 5 |
| Chart is Helm-only | No kustomize (#206) |
| Environments | Namespace is the product name only; env lives in context + values file; CI publishes beta and above only |

### 7.7 Data migration

| Source | Target | Method | Verification |
|---|---|---|---|
| `stream_playbooks/nodes/edges/versions`, `iceflows_*` definitions | Remain the authoring store through Phase 3 (no schema change needed for parity); compiled to signed graph snapshots at publish | None in Phases 1-3 | Snapshot digest recorded per version |
| Same | Unified graph tables (Phase 4) | Idempotent Alembic data migration; mint `village_id` for child rows; keep old tables read-only until deletion | Row counts per table match; round-trip compile equality on all versions |
| `iceflows_credentials.access_token` | Vault (envelope-encrypted) | Migration job: read plaintext, encrypt, decrypt-verify, then null the plaintext column | Count migrated = count read; zero plaintext rows remain; job fails loudly on any mismatch |
| Webhook secrets (`iceflows_webhooks`, `stream_webhooks`) | Vault | Same | Same |
| Credential rotation | — | Advise tenants to rotate migrated credentials: they sat plaintext at rest | Report count notified |
| Run history (`stream_executions`, `stream_node_executions`, `iceflows_promotions`) | Stays; new runs write the same tables via the control plane | No copy | Old UI still renders old runs |
| `stream_schedules` | Engine scheduler | Q8 | — |
| `stream_custom_modules` rows | Reviewed manually; `code_blob` is not auto-compiled | Owner call on whether any exist (UNVERIFIED) | — |

### 7.8 Deletion gate (Phase 5)

Delete only after all hold:

- Parity criteria green for both engines for the agreed soak period.
- Data migration verified; plaintext columns cleared.
- No import of `apps.worker.streams`, `apps.flows_invoker`, or the old handlers remains.
- Legacy images, CI entries, Helm components, NetworkPolicy, and the Tetragon exemption removed.
- Rollback path no longer needed (owner sign-off).

---

## 8. Waddles reuse notes

Waddles is a reference and a source of lessons. `svc-workflows` never depends on the Waddles repo (D7). Reuse happens through `penguin-libs`, a shared library repo, not a product (D8).

### 8.1 Already in penguin-libs (reuse as git dependencies)

| Crate | Path in penguin-libs | Purpose | Waddles pin | Applies here |
|---|---|---|---|---|
| `penguin-spine` | `packages/rust-spine` | Valkey Streams client (XADD / XREADGROUP / XACK), consumer groups, reclaim. Its two client-connection-separation rules (dedicated blocking XREADGROUP connection vs shared admin connection; Waddles spec §5.7) are the connection management | 0.1.0 @ `586b8dad` (VERIFIED in Waddles `svc_process/Cargo.toml`). Release tip 0.2.0 @ `90060a9` (RECON) | Yes — run dispatch consumer |
| `penguin-bundle-host` | `packages/rust-bundle-host` | WASM host-API frame codec + manifest validator; no network | 0.2.0 @ `b063b7ba` (VERIFIED). Waddles' `bundle_compiler` pins an older 0.1.0 @ `69f77b1a` (RECON) | Yes for the codec. The manifest validator targets Waddles' `bundle.yaml` v2; whether it generalizes is UNVERIFIED |
| `penguin-logging` | `packages/rust-logging` | Structured logs + OTel | 0.1.0 @ `69e740ce` (RECON) | Yes |
| `penguin-licensing` | `packages/rust-licensing` | License / flag client | 0.1.0 @ `f642e069` (RECON) | Yes — flags and license fallback |
| `rust-rpc` | `packages/rust-rpc` | Name suggests gRPC helpers | Not used by Waddles that the author saw | UNVERIFIED — builder evaluates for the control-plane channel |
| `penguin-connector-twitch/discord` | `packages/rust-connectors` | Platform-specific | — | Not relevant |

- Note on "the connection library": the owner said Waddles uses shared connection crates. No crate is literally named that; the best match is `penguin-spine`. Owner to confirm (Q4).
- All are git-pinned, not on crates.io.
- Commits `b063b7ba` and `586b8dad` exist in the local penguin-libs clone (VERIFIED). The other SHAs above are RECON.

### 8.2 Still in the Waddles repo — extract or reimplement

These are not penguin-libs crates today (RECON; the author confirmed the crate directories exist under `waddles:core/`). Elder must not depend on the Waddles repo.

| Waddles crate | Generic or product-specific | Disposition proposed |
|---|---|---|
| `bundle_executor` (wasmtime engine, `signing.rs`, `wire.rs`, `invoke.rs`) | Core is generic; host implementations and manifest are product-specific | Extract engine + signing + wire to penguin-libs; Elder writes its own host capabilities. Fixing the shortfalls in 6.9 during extraction benefits both products |
| `bundle_capability_gate` (`gate.rs`) | Gate pipeline generic; permission catalog is Waddles-specific (reputation, economy, overlay) | Extract with the catalog as a pluggable data/trait; Elder supplies its own catalog |
| `egress_proxy`, `egress_assertion`, `bundle_host_http` guard | Generic network policy logic | Extract; Elder-specific defaults (service allowlist, JWKS source) become config |
| `service_auth` (EdDSA machine JWT, `TrustBundle` trait) | Generic; today's issuer is Waddles hub-api | Extract; Elder needs its own issuer (5.7, UNVERIFIED) |
| `hub_client` | Waddles-specific | Do not extract. Write an Elder control-plane gRPC client |
| `bundle_active_set` | Reads Waddles' own schema; its header says it is deliberately a same-repo path dependency | Reimplement for Elder against the control-plane API |
| `bundle_host_kv`, `bundle_host_db` | Product-specific | Not needed in v1 (5.5) |

- Rule for extracted crates: no product code and no product assumptions inside; a library may never require another product to be present.

### 8.3 Reuse vs reimplement — AUTHOR'S CALL E (revised on owner direction)

Owner direction (D8): reuse the shared libs, do not reimplement, do not depend on the Waddles repo. Still open and marked for owner confirmation: which crate "connection library" means, whether the Waddles-resident crates are extracted, who does the work, and how it is pinned.

| Option | For | Against |
|---|---|---|
| 1 Depend on the Waddles repo | Zero extraction work | Violates D7. Rejected |
| 2 Extract Waddles-resident crates to penguin-libs, consume all as shared crates (**author's recommendation; matches owner direction**) | Fix once, both products inherit; security-critical code is not duplicated; shortfalls in 6.9 get fixed in one place; independence preserved because penguin-libs is a library | Waddles must migrate to the extracted crates (cross-repo coordination); generalization cost (catalog, scope shape, issuer); shared release cadence; risk of a lowest-common-denominator API; extraction is on Elder's critical path |
| 3 Reimplement in `svc-workflows` | Fast; Elder-tailored; no coordination | Two copies of security-critical code; drift; two places to patch CVEs. Contradicts "contribute to penguin-libs before writing custom equivalents" (`backend.md`). Fallback only if extraction stalls |
| 4 Hybrid: consume what is already in penguin-libs now; extract generic crates incrementally; reimplement product-specific ones | Unblocks Phase 1 | Interim copies must be deleted once extracted |

- Plan: use the already-published crates (8.1) from Phase 0. Extract in this order as Phase 1 needs them: `service_auth`, gate, executor core, egress guard/proxy/assertion. Reimplement only `hub_client` and `active_set` equivalents.
- Elder does not take Waddles' older pins blindly: pick the newest released commit after reading each crate's changelog, and pin the full SHA.

### 8.4 Dependency handling — risk and open question

- **`penguin-spine` and `penguin-bundle-host` are not on penguin-libs' default branch.** `origin/main` has `packages/rust-licensing` and `packages/rust-rpc` only among Rust crates (VERIFIED by tree listing); spine, bundle-host, and logging live on release branches: `release/rust-bundle-host/v0.1.x` and `v0.2.x` and `release/rust-logging/v0.1.x` (names seen by the author in the local clone), `release/rust-spine/v0.1.x` (RECON). Commit tips are RECON. The local clone is on a stale feature branch and is not a source of truth.
- Consume them as git dependencies pinned to an immutable full commit SHA (dependency-pinning rule), never a branch. Use `cargo vendor` so builds are hermetic.
- Risks: a release branch force-push or deletion orphans a SHA; cargo-deny/audit must cover git sources; version skew with Waddles. Options for the owner: promote to `main`, tag, or publish to the public registry (Q4).
- Elder is pure Python today (VERIFIED: no `Cargo.toml`); this is Elder's first Rust workspace. Layout: AUTHOR'S CALL P.

### 8.5 Waddles job-bus compatibility

| Aspect | Elder JobBus today (`shared/jobbus/core.py`) | Waddles spine |
|---|---|---|
| Streams | `elder:jobs:<group>`, `elder:results:<group>`, `…:dead` | Per-ingest-source streams, per-bundle consumer groups |
| Envelope | One JSON `data` field: `job_id`, `job_type`, `tenant_id`, `payload`, `enqueued_at`, `idempotency_key`, OTel `trace_context` | Waddles envelope incl. binding MAC (D30) |
| Reliability | Idempotency SET NX 24 h; XAUTOCLAIM sweeper; DLQ at 5 deliveries | Pending-entries list + XAUTOCLAIM; max deliveries → DLQ; MAXLEN trimming |
| Producer | Python `JobBus.enqueue` | Rust services |

- AUTHOR'S CALL K (revised): `svc-workflows` consumes a new dedicated stream namespace (e.g. `elder:wf:*`) via `penguin-spine`. It does not read `elder:jobs:*`. Legacy `streams` and `flows` groups keep running untouched until cutover. Never two consumers (Python and Rust) on the same stream.
- Producer: Elder's Python control plane enqueues through the penguin-dal Valkey stream backend (`python-dal/src/penguin_dal/stream/valkey_streams.py`, RECON; Elder pins `penguin-dal==0.3.0`, Waddles uses 0.4.0; which version introduced the backend is UNVERIFIED) or a thin adapter in `JobBus`. Builder decides after reading both.
- Whether `penguin-spine` is generic over envelope contents, or assumes Waddles' envelope: UNVERIFIED. Builder reads the crate before committing the wire format.
- Server naming: Elder says Redis, Waddles says Valkey. Streams commands needed (XADD, XREADGROUP, XAUTOCLAIM) are protocol-compatible; confirm the deployed server version supports them — UNVERIFIED.
- Waddles has its own Python `core/workflow_core_module`; not reviewed, and not reusable anyway under D7.

### 8.6 Waddles shortfalls to avoid

- Fuel, pool, `.cwasm` cache, lazy load, ingress rate limit, no-stub behavior, gVisor, `helm` overrides: requirements in 6.9 (each marked VERIFIED or RECON there).
- Rust cut-over incomplete (`.py` remnants) — RECON: Phase 5 deletion gate.
- Signing stub in the Waddles compiler — RECON: no-stubs gate plus a test that an unsigned bundle is refused (6.7).
- `componentize-py` executes bundle code at build — VERIFIED (spec R2): 6.7.
- WASI has no threads, so `asyncio.run` fails and needs a shim — VERIFIED (spec R1): Python SDK item in section 10.
- Precompiled artifacts are tied to the wasmtime version and GC collector — VERIFIED (spec §7.2): cache key in 6.9.

Waddles wasmtime pin for reference: `=49.0.2`, component-model + gc-drc + pooling allocator (VERIFIED in `waddles:core/bundle_executor/Cargo.toml`). Builder re-confirms the current patched release and pins exactly.

---

## 9. Author's calls

Every row: AUTHOR'S CALL — owner to confirm.

| ID | Call | Alternative | Status |
|---|---|---|---|
| A | Control plane = Elder API (definitions, run history, authz, tenant/identity); data plane = `svc-workflows`. Data plane fails closed if the control plane is unreachable: grants missing/cache-miss/loader-error = deny; flags and license fall back to last-known cached value; unseen flags OFF | Data plane owns run history | Owner confirmed this framing |
| B | WASM modules get no direct network or filesystem; they call only host functions that the gateway authorizes per tenant, license tier, and allowlist. Credentials never enter the sandbox; the gateway injects them at egress. This resolves #171 for workflow credentials | Per-module scoped tokens in the sandbox | Owner confirmed |
| C | Heavy CI steps run as short-lived sandboxed runner jobs that `svc-workflows` launches and the gateway gates; WASM does logic/glue | Everything in WASM (git/test not feasible) | Owner to confirm — Q2 |
| D | Canvas and flow share one graph model; canvas is the editor; diagrams link to flows/steps via the reference registry | Separate diagram and flow models | Owner has not answered — Q3 |
| E | Reuse shared `penguin-libs` crates (spine, bundle-host, logging, licensing now; extract executor/gate/egress/service_auth later); no Waddles repo dependency; reimplement only product-specific pieces | Reimplement everything | Owner direction: reuse (D8). Specifics and "connection library" meaning to confirm — Q4 |
| F | Strangler migration behind `workflows.*` flags, default OFF; delete old code at cutover; plaintext credentials re-encrypted | Big-bang | Owner to confirm |
| G | Rust (tokio/axum), wasmtime component model + WASI 0.2, exact pins, no PRC-origin deps; SDKs Python/Rust/JS; OTel + penguin logging; Postgres RLS via separate PR (dependency); SPIFFE-ready identity + short-lived machine JWTs; rootless + stateless; Cilium default-deny; enforced Tetragon on engine/gateway/executor (no blanket exemption); licensing gates scale/structure | — | Owner to confirm |
| H | Licensing: custom-module upload, concurrency, and run limits mapped to tiers is left as an open question; this spec decides nothing | — | Q1 |
| I | One image, several run modes, separate Deployments (engine, gateway-in, gateway-out, executor, launcher) | Separate images per role | Tradeoff: one artifact to sign/pin vs larger code surface per image |
| J | Untrusted custom-module upload ships last (Phase 4) after first-party bundles exercise the gateway/gate/executor and a security review passes | Ship with Phase 1 | — |
| K | Run dispatch over a dedicated stream consumed via `penguin-spine`; sync control calls over gRPC; Elder API is the only writer of domain tables; no Postgres access from the data plane | Reuse `elder:jobs:*` | Section 8.5 |
| L | Bundle taxonomy: function + flow bundle; `uses:` resolves to a digest at publish, with a lockfile | Single bundle type | — |
| M | Credential-host binding: a credential is sent only to hosts it is bound to | Allowlist only | Beyond Waddles |
| N | Structured control-flow/data nodes are native engine built-ins; free-form expressions and all tenant logic run in WASM | Everything in WASM | Tension with D6 stated in 7.4 |
| O | The Darwin review gate becomes an optional review-gate step provided by an optional connector bundle; fail-closed only when a tenant marks it required; never a platform dependency | Keep `DARWIN_API_URL` in the runner | Today a required Darwin review blocks a promotion if Darwin is absent (VERIFIED `review.py`) |
| P | Cargo workspace under `apps/svc_workflows/` in the Elder repo, built in a pinned container | Separate repo | Serves Elder only (D7) |
| Q | Gateway scope: external traffic only. Control-plane gRPC is a separate mutually authenticated channel with its own policy | Route everything incl. control-plane calls through the gateway | Q5 |

---

## 10. Left to the builder

| Area | Constraint on the builder |
|---|---|
| WIT worlds and host-call wire | Fail loud on unimplemented calls; gate before every import; canonical JSON text for open-ended values (Waddles A2) |
| Manifest/graph JSON Schemas | Must carry what 5.5/5.6 list; ABI version mandatory |
| Proto definitions (`workflows.v1`) | Version package + `api_version`; no raw model objects |
| Scheduler fairness algorithm, executor pool sizing | Per-tenant fairness required; measure, do not guess |
| Fuel budgets and run-level cap values | Measured on real workloads |
| Checkpoint granularity and resume semantics | Idempotent steps; at-least-once; document |
| Expression semantics | Parity corpus against `expression.py` |
| Loop/cycle semantics in the graph | Read how Streams does it first |
| Canvas editing model (locks vs collab) | One model |
| Cron/schedule executor | If Q8 says in scope |
| SDK layout and naming (Python via componentize-py, Rust via cargo-component, JS via componentize-js) | Build containers have no network; Python needs an async shim |
| Machine-JWT issuer in Elder | Confirm penguin-aaa vs new; EdDSA; ≤1 h |
| Staleness bound for cached grants | Choose and document; revocation window must be bounded |
| Runner log/artifact return path | No credentials; bounded |
| Git push via gateway credential injection | Prove feasibility early (R4) |
| Helm chart structure; Cilium and Tetragon policy content | Per-mode; negative tests per policy |
| Metric names, histograms, usage metering shape | No tenant/run IDs as labels |
| Test strategy | Parity corpus, flag-on E2E, telemetry counts, negative sandbox tests, independence run (G7), 90%+ coverage |
| Flag prefix reconciliation | 7.5 |
| Rust logging conformance | `testing.md` says Rust asserts `tracing` because no penguin Rust logging crate exists; recon says `penguin-logging` 0.1.0 exists on a release branch. If used, assert both; flag the stale rule text to the rules owner |
| Base image and toolchain pins | Digests; Western-origin |
| Run-history retention and storage | Q |

---

## 11. Open questions for the owner (most important first)

| # | Question | Why it matters / default if unanswered |
|---|---|---|
| Q1 | **Licensing and tier mapping** for custom-module upload, concurrency, and run limits. Candidate dimensions, none decided: custom modules (a premium capability vs a count per tier), concurrent runs per tenant, runs per month, executor memory/time ceilings, runner job size and minutes, run-history retention, bundle count, marketplace publish. Free is one team at full capability, so "feature gating" is off the table; scale/structure/premium-capability is the frame | Blocks enforcement design. Default: build metering and `@enforce_limit` hooks, observe-only (`elder.license-enforcement` pattern), set no numbers |
| Q2 | **Confirm the heavy-step runner split (call C).** You said "WASM for any logic"; git clone/merge, test subprocesses, and image builds cannot run in WASM. Also: may a short-lived scoped token live in the runner pod if gateway credential injection for git push is not feasible? Tenant-supplied runner images? | Defines the highest-risk surface. Default: runner jobs as designed in 6.8; no token in pod; allowlisted images |
| Q3 | **Confirm canvas = editor on one graph model (call D).** Merging `stream_nodes/edges` with `dg_shapes/connectors` is a data migration | Default: Phase 4 unification; Phases 1-3 keep both models |
| Q4 | **Shared libs.** (a) Is "the connection library" `penguin-spine`? (b) Extract the Waddles-resident crates (executor, gate, egress proxy/assertion, `service_auth`) into penguin-libs, and who owns the PRs and Waddles' migration? (c) `penguin-spine`, `penguin-bundle-host`, `penguin-logging` live only on release branches — promote to `main`, tag, or publish? (d) Which commit to pin? | Gates Phase 0/1 dependency setup. Default: consume 8.1 crates by SHA now; extract incrementally |
| Q5 | **Gateway scope.** Does "only way in or out" cover external traffic only, or also Elder API ↔ `svc-workflows` control traffic and UI → API? | Default (call Q): external only; control-plane gRPC is separately authenticated |
| Q6 | **Who reviews and approves** tenant-private vs marketplace-shared modules? Waddles' marketplace-sources design uses global-admin-only sources in v1 (no per-tenant private sources), prebuilt external source = Professional, sandbox build = Enterprise (VERIFIED in `waddles:docs/superpowers/specs/2026-10-27-unified-bundle-marketplace-sources-design.md`). Is there a marketplace at all? | Default: tenant-private modules approved by a tenant admin; no marketplace in v1 |
| Q7 | **Bundle ABI versioning.** WIT world semver; support window (N and N-1?); who migrates user modules on a breaking change; deprecation notice | Default: `abi` field in the manifest; host supports N and N-1; breaking change needs a new world |
| Q8 | **Is the Streams cron executor gap in scope?** `stream_schedules` has a model and no executor | Author's recommendation: yes, in Phase 2 — a `schedule:` trigger is cheap on the new engine and expensive to build on the old one. Not parity-critical. Default: out of parity scope |
| Q9 | **Runs (serverless) module.** Absent today; WASM functions are its natural runtime. In or out? | Default: out; design does not preclude |
| Q10 | **Accept prebuilt WASM components?** Waddles allows them with a permanent "not scanned" badge. Source-only is safer | Default: source-only in v1 |
| Q11 | **Runner isolation floor.** Is pod hardening + Cilium + Tetragon enough for runner jobs, or require Kata/gVisor (availability on Elder clusters UNVERIFIED; Waddles found gVisor undeliverable on MicroK8s/DOKS)? | Default: hardening stack; Kata evaluated if enforcement proves insufficient |
| Q12 | **Optional integrations.** Which of the 13 PenguinTech-product connectors ship first-party as optional bundles? Darwin gate per call O? Private-host egress opt-in (6.5)? | Default: ship connectors as optional bundles, none at launch except what Phase 2 parity needs |
| Q13 | **JobBus transition.** Producer path to the new stream (penguin-dal stream backend vs a JobBus adapter) | Builder proposes after reading both (8.5) |
| Q14 | **Run-history retention and storage cost** | Default: keep current behavior |

---

## 12. Risks

| # | Risk | Impact | Mitigation | Spike |
|---|---|---|---|---|
| R1 | Runner jobs execute native code (build/test commands) | Highest: escape reaches the node kernel | 6.8 stack; no creds; gated egress; ephemeral; Kata escalation (Q11) | Negative-test suite for the runner pod before Phase 3 exit |
| R2 | Python/JS modules compile to large components; build executes module code; cold compile is seconds (Waddles measured a 21.6 MB Python component, 3.3-4.5 s uncached vs ~5 ms precompiled — VERIFIED in spec §7.2) | Slow publish, build-time attack surface | Artifact cache, no-network build, trusted publisher; Rust/JS first | Build a hello-world per Tier 1 language |
| R3 | Parity: 21 Python node classes reimplemented; expression semantics drift | Silent behavior change for users | Recorded corpus; shadow diff; per-node tests with printed counts | Corpus capture before Phase 2 |
| R4 | Git clone/push with gateway credential injection may not work through a proxy | Forces a token into the pod | Early spike; fallback needs owner decision (Q2) | Smart-HTTP push through gateway-out |
| R5 | Shared-executor noisy neighbor; no per-tenant limits exist today | One tenant starves others | Per-tenant fairness + caps; optional dedicated pool | Load test with a hanging module beside healthy ones |
| R6 | Scope: engine + gateway + executor + canvas unification + SDKs + build pipeline + two migrations | Schedule slip | Phases; each exits on a count-verified gate; untrusted upload last | — |
| R7 | Fail-closed couples the data plane's availability to the control plane | Elder API outage halts runs | Bounded-staleness grant cache; push-invalidate; HA control plane; documented | Chaos test |
| R8 | wasmtime CVE or upgrade invalidates the artifact cache and needs fast patching | Thundering recompile; exposure window | Exact pin + patch runbook; rolling recompile; cache key includes ABI | Measure recompile cost |
| R9 | DB connectors assume an HTTP `database-ops` backend that may not exist in Elder; native DB protocols cannot egress from WASM | 18 connectors non-functional | Treat as out of v1; decide per connector (N7) | Check for a `database-ops` service |
| R10 | Dual-stack period doubles the test matrix | Drift, cost | Shadow only side-effect-free graphs; time-boxed; deletion gate | — |
| R11 | Independence erodes: optional connectors creep into core | Violates D7 | CI job with all other products absent; connectors only as optional bundles; no health/startup coupling | Independence E2E in Phase 1 |
| R12 | Interactive "test run" latency through gateway + executor | Poor canvas UX | Warm pool, artifact cache, native built-ins | Measure p95 on a 10-node graph |
| R13 | Plaintext credentials sat at rest | Possible prior exposure | Migrate, clear, advise rotation | — |
| R14 | `penguin-spine` / `penguin-bundle-host` / `penguin-logging` only on release branches, git-pinned | Orphaned SHA; supply-chain and version-skew risk | Immutable SHA + vendoring; promote/tag/publish (Q4); cargo-deny on git sources | — |
| R15 | Elder's first Rust workspace: toolchain, CI, container, coverage, audit gates are all new | Late CI surprises | Phase 0 builds an empty gated crate first | CI green on an empty crate |
| R16 | Extraction of Waddles-resident crates is on the critical path and needs another team's coordination | Phase 1 slip | Hybrid option 4; reimplement only if stalled | — |
| R17 | `penguin-spine` envelope may assume Waddles' format | Rework of dispatch | Read the crate in Phase 0 | Round-trip a job end to end |
| R18 | No alerts or dashboards exist for either engine today (RECON) | Blind operation during migration | Ship dashboards and alerts with Phase 1; histograms, bounded labels | — |
