# svc-workflows Phase 0 (Prereqs + Spikes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land everything the spec's Phase 0 requires so Phase 1 can start: owner-gate record, executable graph-schema design, `workflows.v1` + Elder internal contracts, Elder's first Rust workspace with nine empty-but-gated images, Elder-side registry/village_id/outbox groundwork, penguin-libs consumption + extraction PRs started, dal2alpha provisioned, four spikes run, exit-gate check green.

**Architecture:** One Cargo workspace at `apps/svc_workflows/` (per-trust-boundary crates, call I/P) next to the unchanged Quart control plane; every gate is a script that proves it can fail (counts printed, zero denominators fail). Generic security crates are extracted in a SEPARATE repo (penguin-libs) and consumed only by pinned SHA. No Phase 1+ behavior: every role binary exits loudly with `not_implemented`.

**Tech Stack:** Rust (tokio/axum, tonic/prost, wasmtime component model + WASI 0.2), Python 3.13 + Quart + Alembic (Elder side), Helm, Valkey cluster, S3-compatible store, `cargo-deny`/`cargo-audit`/`cargo-llvm-cov`, `penguin-spine`/`penguin-bundle-host`/`penguin-logging`/`penguin-licensing` (git-pinned).

**Spec:** `docs/superpowers/specs/2026-10-27-svc-workflows-design.md` on branch `docs/svc-workflows-spec` @ `28f5ee0` (read with `git show origin/docs/svc-workflows-spec:docs/superpowers/specs/2026-10-27-svc-workflows-design.md`; this plan travels on the same branch). Section refs below: "§" = spec section, "call X" = spec §9 call, "Qn" = spec §11 question.

## Repos and execution notes

| Repo | Tasks | Notes |
|---|---|---|
| `elder` (this repo) | T1-T18, T24 | One branch `feature/svc-wf-p0-<n>-<slug>` + own PR per task, off `release/v4.0.X` (never `main`); push after every commit; PR assigned to the human overseer; CODEOWNERS review (`@PenguinzTech @Chromeninja`) |
| `penguin-libs` (`/home/penguin/code/penguin-libs`) | T19-T23 | SEPARATE repo, separate PRs; its own rules/CI apply; run these tasks there, not in Elder |
| dal2alpha cluster | T13-T15 | Needs a human-supplied kube context (name UNVERIFIED; the rules' cluster table lists no `dal2alpha`); never `sudo` |

Python tests run in the container, one DB + redis index per concurrent agent (`$PYT` = `docker run --rm --network host -e DATABASE_URL=postgresql://elder_test:<pw from docker inspect elder-test-postgres>@localhost:55432/<own_db> -e REDIS_URL=redis://localhost:56379/<n> -v $PWD:/app -w /app --entrypoint python3 elder-test:3.13 -m pytest`). Rust runs only through `make svc-wf-*` (created in T3). Tasks that edit `Makefile`/`.github/workflows/svc-workflows.yml` (T3, T7, T8, T10, T11, T14, T15) serialize or use separate worktrees.

```
O-RLS (feature/rls-tenant-isolation-v2, not a task) --> T10, T11 rebase on it, then --> T24
T1 -> T2 -> T3 -> T4 -> T7 -> T8        T13 -> T14 -> T16
         |-> T5   |-> T6                 T13 -> T15        T7 -> T17        T18 (local alpha)
T9, T12 independent;  T19..T23 (penguin-libs) parallel -> PR URLs -> T24
```

## Owner gates (nothing here is decided by this plan)

| Gate | Blocks | Used default (spec) |
|---|---|---|
| O-RLS: RLS landed on `release/v4.0.X`, or owner accepts app-layer scoping in writing (N4, §7.2) | T10/T11 final merge, T24 | none: stays OPEN until it lands (RLS branch is 2 commits ahead of release, no PR; it owns Alembic 042/043) |
| Q1 aggregate scope, one cell, placeholder SLOs | T16 targets | PROVISIONAL (owner to confirm: Q1) |
| Q2 metering observe-only, no tier numbers | none in Phase 0 | PROVISIONAL (owner to confirm: Q2) |
| Q3 runner split as designed | none in Phase 0 | PROVISIONAL (owner to confirm: Q3) |
| Q4 canvas unification in Phase 4 on the Phase 1 schema | T6 | PROVISIONAL (owner to confirm: Q4) |
| Q5 consume §8.1 crates by SHA; extraction per call E; crate names/owners | T2, T19-T23 | PROVISIONAL (owner to confirm: Q5) |
| Q6 role split as recommended (separate `controller`, `scheduler` in `gateway-in` image, `executor` container in engine pod) | T3, T4, T7, T8 | PROVISIONAL (owner to confirm: Q6) |
| Q7 schedule-event model: interim issue `due_date`, no `elder.schedule.*` | T5 | PROVISIONAL (owner to confirm: Q7) |
| Q11 external-only gateways, gated Elder adapter | T5 | PROVISIONAL (owner to confirm: Q11) |
| Q12 address by `village_id` | T5, T11 | PROVISIONAL (owner to confirm: Q12) |
| Q15 history sink = object storage + Postgres index | T15 | PROVISIONAL (owner to confirm: Q15) |
| Q21 dal2alpha is the gating environment | T13-T15 | PROVISIONAL (owner to confirm: Q21) |
| O-ENV: human supplies dal2alpha kube context + S3 provider decision if none exists | T13-T15 | none: explicit human task |

## Global Constraints

Every task's requirements include this section.

- Rust latest stable, exact toolchain in `rust-toolchain.toml` (Waddles pins `1.97.1`; confirm latest at execution); tokio/axum; gRPC via tonic/prost. Platform is Rust; Go only ever as a USER language (call AO); Elder API stays Python 3.13 + Quart.
- Dependencies pinned to immutable references, verified: exact `=x.y.z`, committed `Cargo.lock`, git deps by full 40-char SHA + `version = "=..."` + `package`, never branch/tag; images by digest; no PRC-origin deps, images, models, toolchains or GitHub Actions.
- `wasmtime` exact pin, one version across workspace and extracted crates. The spec states NO number (Waddles' working tree shows `=49.0.4`; caller cited `=49.0.2`): resolve at T2 and record once in `[workspace.dependencies]`.
- TDD; 90%+ coverage (lines, branches, functions, statements). `cargo llvm-cov` stable has no branch metric: gate lines + functions + regions at 90 and say so in the PR.
- penguin-logging AND OpenTelemetry (logs + metrics + traces), OTLP destination from `OTEL_*` env only, a dead exporter never breaks the app; assert `tracing` AND penguin-logging (spec §10).
- Rootless + stateless containers (non-root, read-only rootfs, drop ALL caps, `RuntimeDefault` seccomp, PSA `restricted`); Valkey/object store are the only stateful pieces.
- PostHog flags `workflows.*`, default OFF, unseen = OFF. No stubs: unimplemented paths fail loudly (`not_implemented`, anti-goals A3/A4); a gate that cannot fail is a defect (`set -euo pipefail`, no `|| true`, counts printed, zero denominators fail).
- UUID across boundaries; Elder objects addressed only by registry `(module, type, village_id)` (call S, Q12); never names or numeric PKs. Tenant from token/envelope, never arguments (A7). No PII outside the Elder API server (A6, A10).
- SPIFFE-ready identity `spiffe://penguintech.io/<env>/svc-workflows-<role>`; seven IDs: `gateway-in`, `scheduler`, `gateway-out`, `engine`, `controller`, `pipeline-fetch`, `pipeline-publish`; executor, scan, builder, runner hold none.
- Helm only (no compose/kustomize); namespace is the product name `elder`, never env-suffixed. CI publishes beta+ only; Phase 0 images are build-only (`push: false`).
- Every function/class gets a 2-3 line doc comment (Rust `///`, Python docstring); no ASCII-art section dividers.
- Status/type/kind/role columns and proto fields are int enum codes, never strings or booleans; Alembic migrations are idempotent with no back-compat shims (D10).
- Never commit to `main`; PRs into `release/v4.0.X` with a description; never `--force`; no secrets in argv/logs.

## Review Focus

Failure modes the spec implies that no spec-listed test covers, each pinned to a task test:

1. Malformed graph documents (cycle, dangling edge, port-type mismatch, unknown field, future `schema_version`) must yield typed errors, all reported, no panic. T6: `validate_reports_all_errors_not_first`, `validate_never_panics_on_malformed`.
2. A presentation-only canvas edit (move/rename/annotate) must not change the semantic digest, or every drag would invalidate approval and signature. T6: `semantic_digest_ignores_presentation`.
3. Object-store prefix boundary and listing leakage: `signed-artifacts-extra/` must not match `signed-artifacts/`; the write-only history credential must not list or read. T15: `test_boundary_prefix_probe_included` + live access matrix.
4. Valkey cluster mode: a fencing script touching keys in different slots fails with CROSSSLOT; passing on a standalone node proves nothing. T16: `cross_slot_script_is_rejected_on_cluster`.
5. Gate scripts that pass vacuously (branch-ref dependency, PII-named proto field, zero-row decision record, zero images, zero-denominator coverage). T1, T2, T3, T5, T7, T10, T24 each carry a negative-fixture test.

---

## Task group A: Gates, pins, workspace (Elder)

### Task 1: Phase 0 decision record and parser (S)

**Files:**
- Create: `docs/design/svc-workflows/phase0-decisions.md`, `scripts/svc_workflows/decision_record.py`
- Test: `tests/unit/svc_workflows/test_decision_record.py`

**Interfaces:**
- Produces: `Decision(id: str, status: Literal["ANSWERED","ACCEPTED","PROVISIONAL","OPEN"], default_used: str, evidence: str)`; `parse_decisions(path: Path) -> list[Decision]` (table columns `id | status | default used | evidence`; raises `DecisionRecordError` on zero rows, unknown status, duplicate id, malformed row); `require(decisions: list[Decision], ids: Iterable[str]) -> list[Decision]` (raises naming every missing id). Consumed by T24.

- [ ] **Step 1: Write the failing tests:** `test_parse_returns_all_rows` (3-row fixture -> len 3), `test_zero_rows_raises`, `test_unknown_status_raises`, `test_duplicate_id_raises`, `test_require_names_every_missing_id` (missing Q2,Q3 -> message contains both), `test_open_status_returned_not_hidden`, `test_real_record_has_q1_to_q7_q11_q12_q15_q21_and_o_rls_o_env`.
- [ ] **Step 2: Run to verify failure:** `$PYT tests/unit/svc_workflows/test_decision_record.py -q`; expected FAIL with `ModuleNotFoundError` (module missing).
- [ ] **Step 3: Implement** `decision_record.py` (stdlib only). Then write the record: one row per gate in "Owner gates" above, status PROVISIONAL with the spec default verbatim in `default used`; `O-RLS` and `O-ENV` status OPEN.
- [ ] **Step 4: Run tests;** expected 7 PASS.
- [ ] **Step 5: Owner gate (human):** open the PR with the table in the description asking the owner to answer or accept each default; when they reply, edit the row to ANSWERED/ACCEPTED with the link. Commit `docs: svc-workflows phase0 decision record`.

### Task 2: Pin checker, penguin-libs SHA resolution, spine read-through (S)

**Files:**
- Create: `scripts/svc_workflows/check_pins.py`, `docs/design/svc-workflows/penguin-libs-consumption.md`
- Test: `tests/unit/svc_workflows/test_check_pins.py`

**Interfaces:**
- Produces: `check_manifest(cargo_toml: Path) -> PinReport(examined: int, violations: list[str])` (raises `PinCheckError` when examined == 0); `reachable_branches(rev: str, repo_url: str) -> list[str]`. Consumed by T3 (`make svc-wf-pins`).

- [ ] **Step 1: Write the failing tests:** `test_branch_ref_is_violation` (git dep with `branch = "main"` -> violation naming the dep), `test_tag_ref_is_violation`, `test_short_sha_is_violation`, `test_caret_range_is_violation`, `test_git_dep_missing_version_is_violation`, `test_exact_pin_ok` (examined == 1), `test_zero_deps_raises`, `test_reachable_branches_lists_release_branch` and `test_orphan_sha_has_no_branch` (tmp git repo fixtures).
- [ ] **Step 2: Run:** `$PYT tests/unit/svc_workflows/test_check_pins.py -q`; expected FAIL import error.
- [ ] **Step 3: Implement** with `tomllib`; rules: every entry in `[dependencies]`, `[dev-dependencies]`, `[build-dependencies]`, `[workspace.dependencies]` has `=` version; git deps carry 40-hex `rev`, `package`, `version`; `path` allowed only for workspace members. `reachable_branches` = blobless clone + `git branch -r --contains`.
- [ ] **Step 4: Run tests;** expected 9 PASS.
- [ ] **Step 5: Resolve and record** in `penguin-libs-consumption.md` (read-only clone, cite file:line, anything unreadable stays UNVERIFIED): newest released commit per consumed crate (`penguin-spine`, `penguin-bundle-host`, `penguin-logging`, `penguin-licensing`; call E says newest, not Waddles' older pins; Waddles' working tree, commit not checked, shows spine 0.2.0 @ `90060a93` (= §8.1's tip), bundle-host 0.3.0 @ `0b97183f`, logging @ `0a320ba2`, licensing @ `7110125d`, the last three newer than the §8.1 table) with the branch each lives on; wasmtime/tokio/axum/opentelemetry versions in those crates' lockfiles (to avoid duplicate majors); answers to §8.5 and §8.1 UNVERIFIEDs: is spine generic over envelope contents (it ships D30 envelope/binding-MAC types that overlap calls X/AT), required Valkey version, `rust-rpc` contents; and the decision "envelope formats are defined in the Phase 1 plan after these answers". Commit `feat(svc-workflows): pin checker + penguin-libs consumption record`.

### Task 3: Cargo workspace, CI toolchain image, gates, `wf-boot` identity (L)

**Files:**
- Create: `apps/svc_workflows/{Cargo.toml,rust-toolchain.toml,deny.toml,Dockerfile.ci,.dockerignore}`, `apps/svc_workflows/crates/wf-boot/{Cargo.toml,src/lib.rs,src/identity.rs}`, `scripts/svc_workflows/coverage_gate.py`, `.github/workflows/svc-workflows.yml`
- Modify: `Makefile` (targets `svc-wf-ci-image`, `svc-wf-fmt-check`, `svc-wf-lint`, `svc-wf-test` (forwards `CARGO_ARGS` to cargo), `svc-wf-coverage`, `svc-wf-deny`, `svc-wf-audit`, `svc-wf-pins`; add `svc-wf-lint` + `svc-wf-pins` to `lint`), `.pre-commit-config.yaml` (local hook `svc-wf-fmt` -> `make svc-wf-fmt-check`), `.gitignore` (`apps/svc_workflows/target/`)
- Test: `apps/svc_workflows/crates/wf-boot/src/identity.rs` (`#[cfg(test)]`), `tests/unit/svc_workflows/test_coverage_gate.py`

**Interfaces:**
- Consumes: `check_manifest` (T2).
- Produces (Rust, crate `wf-boot`): `pub enum Role { GatewayIn, Scheduler, GatewayOut, Engine, Executor, Controller, PipelineFetch, PipelineScan, PipelinePublish }` with `Role::ALL: [Role; 9]`, `fn as_str(self) -> &'static str` (`"gateway-in"`, `"scheduler"`, `"gateway-out"`, `"engine"`, `"executor"`, `"controller"`, `"pipeline-fetch"`, `"pipeline-scan"`, `"pipeline-publish"`), `fn has_spiffe_id(self) -> bool`; `pub fn spiffe_id(role: Role, env: &str) -> Result<String, IdentityError>`; `pub enum IdentityError { NoIdentityForRole(Role), InvalidEnv(String) }`; `pub const EXIT_NOT_IMPLEMENTED: i32 = 70;`. Python: `check_coverage(summary_json: dict, minimum: float = 90.0) -> CoverageReport(examined_lines: int, ok: bool, detail: str)`.

- [ ] **Step 1: Write the failing tests.** Rust: `spiffe_id_matches_spec_pattern` (`Engine`,`"beta"` -> `"spiffe://penguintech.io/beta/svc-workflows-engine"`), `exactly_seven_roles_hold_an_identity` (count over `Role::ALL` == 7), `executor_and_scan_have_no_identity` (-> `NoIdentityForRole`), `invalid_env_rejected` (rstest: `""`, `"Beta"`, `"be/ta"`, `"a b"`, `".."`, `"béta"`, 65 chars -> `InvalidEnv`), `valid_envs_accepted` (`prealpha`,`alpha`,`beta`,`gamma`,`prod`). Python: `test_zero_denominator_fails`, `test_below_90_fails`, `test_at_90_passes_and_reports_counts`.
- [ ] **Step 2: Run:** `make svc-wf-test CARGO_ARGS="-p wf-boot"` and `$PYT tests/unit/svc_workflows/test_coverage_gate.py -q`; expected FAIL (workspace missing / import error).
- [ ] **Step 3: Implement** workspace (`members = ["crates/*"]`, `[workspace.dependencies]` pins from T2, `resolver = "2"`), `Dockerfile.ci` (rust base by digest; `cargo-deny`, `cargo-audit`, `cargo-llvm-cov`, `protoc` pinned exact with SHA256, installed `--locked`), `identity.rs` (env charset `[a-z0-9-]{1,32}`), `coverage_gate.py` (parses `cargo llvm-cov --json --summary-only`; lines, functions, regions each >= 90 and `count > 0`; excludes `wf-spikes` and generated code). `svc-wf-pins` also runs `reachable_branches` for every git dep (CI has network) and fails if none of `main`/`release/rust-*` contains the SHA. `deny.toml`: `unknown-git = "deny"`, `allow-git = ["https://github.com/penguintechinc/penguin-libs"]`, `wildcards = "deny"`, advisories deny, license allowlist derived from `make license-check-deps` policy. Workflow job `rust-gates` runs fmt, clippy `-D warnings`, test, coverage, deny, audit, pins with actions pinned by SHA (mirror `docker-build.yml`).
- [ ] **Step 4: Run:** `make svc-wf-lint svc-wf-test svc-wf-coverage svc-wf-deny svc-wf-audit svc-wf-pins` and the pytest; expected all PASS, coverage output shows `examined > 0`. Then prove each gate fails once: add a branch-ref dep, a clippy warning, a `GPL` dep in scratch; each makes its target exit non-zero (record in PR).
- [ ] **Step 5: Commit** `feat(svc-workflows): cargo workspace, ci toolchain image and gates`.

### Task 4: `wf-boot` runtime and the six role binaries (M)

**Files:**
- Create: `apps/svc_workflows/crates/wf-boot/src/{boot.rs,testing.rs}`, `crates/{gateway-in,gateway-out,engine,executor,controller,pipeline}/{Cargo.toml,src/main.rs,tests/boot.rs}` (packages `wf-gateway-in` ... `wf-pipeline`, bins `gateway-in` ... `pipeline`)
- Modify: `apps/svc_workflows/crates/wf-boot/{Cargo.toml,src/lib.rs}` (penguin-logging by SHA from T2; feature `testing` for the in-process OTLP sink)

**Interfaces:**
- Consumes: `Role`, `EXIT_NOT_IMPLEMENTED` (T3); `penguin_logging::{ServiceConfig::from_env, init}` (UNVERIFIED against the pinned SHA: Waddles calls `ServiceConfig::from_env(name)` and `init(cfg) -> (TelemetryGuard, LevelHandle, Registry)`).
- Produces: `pub fn boot(role: Role) -> std::process::ExitCode` (init telemetry, emit one startup log + `wf_boot_started_total` counter + a boot span, write `not_implemented: <role>` to stderr, flush with a 2 s bound, return 70); mode parsing: `gateway-in {ingress|scheduler}`, `pipeline {fetch|scan|publish}`, unknown mode -> exit 64; `wf_boot::testing::{OtlpSink, assert_role_boot(bin: &str, args: &[&str], role: Role)}`.

- [ ] **Step 1: Write the failing tests** (each bin crate's `tests/boot.rs` calls `assert_role_boot`): `role_boot_emits_logs_metrics_traces` (sink logs >= 1, metric points >= 1, spans >= 1, counts printed; exit == 70; stderr contains `not_implemented: engine`), `dead_otlp_endpoint_still_exits_70_within_5s` (endpoint `http://127.0.0.1:1`), `malformed_otlp_endpoint_still_exits_70` (`"not a url"`), `unknown_mode_exits_64`; in `wf-boot`: `source_uses_tracing_and_penguin_logging_only` (scans >= 1 file; zero `println!`/`print!`; the one permitted stderr write lives in `boot.rs`).
- [ ] **Step 2: Run:** `make svc-wf-test`; expected FAIL (`boot` undefined).
- [ ] **Step 3: Implement** `boot` and the six `main.rs` (one line each calling `boot`). Sink pattern: tonic `LogsService`/`MetricsService`/`TraceService` servers from `opentelemetry-proto` (read `waddles:core/bundle_executor/tests` for the shape; do not depend on Waddles).
- [ ] **Step 4: Run:** `make svc-wf-test svc-wf-coverage`; expected PASS, coverage >= 90 with counts.
- [ ] **Step 5: Commit** `feat(svc-workflows): wf-boot runtime and six role binaries (fail loudly)`.

### Task 5: `workflows.v1` and Elder internal contracts, contract checker, issuer ADR (M)

**Files:**
- Create: `apps/svc_workflows/proto/workflows/v1/{common.proto,control_plane.proto}`, `apps/api/grpc/proto/elder_internal/v1/elder_data.proto`, `apps/svc_workflows/crates/wf-proto/{Cargo.toml,build.rs,src/lib.rs}`, `scripts/svc_workflows/check_proto_contracts.py`, `docs/design/svc-workflows/machine-jwt-issuer.md`
- Modify: `Makefile` (`generate-grpc-workflows`, `svc-wf-proto-check`), `apps/api/grpc/generated/` (new stubs)
- Test: `tests/unit/svc_workflows/test_proto_contracts.py`, `tests/unit/svc_workflows/test_proto_stubs.py`, `wf-proto` unit test

**Interfaces:**
- Produces: package `workflows.v1`: `ObjectAddress{module,type,village_id}`, service `ControlPlane { GetTriggers, GetGrants, GetApproval, GetPlatformPublicKeys, IngestRunSummaries, RegisterRuns, SubscribeInvalidations(stream) }` (batched/cursor shapes, §5.7/§5B.2); package `elder.internal.v1`: service `ElderData { ResolveRef, GetMany, SearchPage }` (read ops only; writes arrive Phase 2 per call BB; `notify.send` and `schedule.*` absent by design, Q7). Every request message starts `string api_version = 1;`. `check_descriptor(fds: FileDescriptorSet) -> ContractReport(examined_messages: int, violations: list[str])`. Envelope (run-start/continuation) messages are NOT defined here (T2 outcome decides, Phase 1).

- [ ] **Step 1: Write the failing tests:** `test_missing_api_version_flagged`, `test_pii_field_flagged` (`string email = 2;`; denylist `email|username|phone|first_name|last_name|full_name|display_name|street|postal`), `test_numeric_pk_flagged` (`int64 entity_id`), `test_string_status_kind_state_flagged` (allowlist `ObjectAddress.type`/`module`), `test_bulk_and_cursor_shapes_required` (`GetMany` has `repeated ObjectAddress`, `SearchPage` has `cursor` + `page_size`), `test_zero_messages_raises`, `test_real_protos_have_no_violations` (prints examined > 0); `test_stubs_import_and_package_names`; Rust `generated_messages_roundtrip`.
- [ ] **Step 2: Run:** `$PYT tests/unit/svc_workflows/test_proto_contracts.py -q`; expected FAIL import error.
- [ ] **Step 3: Implement** the checker over a `protoc --descriptor_set_out` descriptor set (grpcio-tools, not regex), the protos, `build.rs` (tonic-build, `protoc` from the T3 image), Python stubs via the new make target. `machine-jwt-issuer.md`: read how Elder issues/validates JWTs today (UNVERIFIED) and `penguin-libs` python-aaa; record Context / Options / Decision (builder's call, §10) / Consequences, including how the extracted `TrustBundle` (T19) obtains Elder's public keys (`GetPlatformPublicKeys` vs JWKS).
- [ ] **Step 4: Run:** `make svc-wf-proto-check svc-wf-test` and the pytest files; expected PASS.
- [ ] **Step 5: Commit** `feat(svc-workflows): workflows.v1 and elder-internal contracts + checker`.

### Task 6: `wf-graph` executable canonical graph schema (L)

**Files:**
- Create: `apps/svc_workflows/crates/wf-graph/{Cargo.toml,src/{lib.rs,model.rs,parse.rs,validate.rs,digest.rs,plan.rs}}`, `docs/design/svc-workflows/graph-schema.md`
- Test: `crates/wf-graph/tests/{parse.rs,validate.rs,digest.rs,plan.rs}`

**Interfaces:**
- Produces (`wf-graph`): `GraphDoc { schema_version: u32, graph_id: Uuid, version: u64, semantic: Semantic, presentation: Presentation }`; `Semantic { nodes: Vec<Node>, edges: Vec<Edge> }`; `Node { id: Uuid, kind: String, kind_version: u32, params: serde_json::Value, inputs: Vec<Port>, outputs: Vec<Port> }`; `Port { name: String, ty: PortType, card: Card }`; `PortType { Control, Data(DataType) }`; `DataType { Null, Bool, Int, Float, String, Bytes, Json, Address, List(Box<DataType>) }` (`Address` = registry `(module,type,village_id)`); `Card { One, Many }`; `Edge { id: Uuid, from: Endpoint, to: Endpoint }`; `Endpoint { node: Uuid, port: String }`; `trait NodeCatalog { fn spec(&self, kind: &str, kind_version: u32) -> Option<NodeSpec> }`; `NodeSpec { inputs, outputs, class: NodeClass, boundary: Boundary }`; `NodeClass { Native, Wasm, HostCall }`; `Boundary { None, Wait, FanOut, AsyncAction, SideEffect }`; `const SUPPORTED_SCHEMA_VERSIONS: &[u32] = &[1]`; `parse(&[u8]) -> Result<GraphDoc, GraphError>`; `validate(&GraphDoc, &dyn NodeCatalog) -> Result<ValidatedGraph, Vec<GraphError>>`; `GraphDoc::semantic_bytes(&self) -> Vec<u8>`, `semantic_digest(&self) -> [u8; 32]` (SHA-256 over sorted-key canonical JSON of `semantic` only); `signing_payload(graph_id: Uuid, version: u64, digest: &[u8; 32], approval_id: Uuid) -> Vec<u8>` (domain `svc-workflows/graph/v1`, length-prefixed, per §6.7; verification is the T20 crate, wired in Phase 1); `compile(&ValidatedGraph) -> RunPlan`, `RunPlan { steps: Vec<Step> }`, `Step { step_id: Uuid /* UUIDv5(digest, node id) */, node: Uuid, boundary: Boundary }`. Run ids are UUIDv7 minted in the data plane (call AW), not here.

- [ ] **Step 1: Read first (UNVERIFIED in spec):** `apps/worker/streams/nodes/conditionals/{for_each.py,while_loop.py}` and `nodes/base.py` port declarations; record in `graph-schema.md` whether loops are iterator nodes (`Many` output + `done`), which keeps v1 a DAG. If reading disproves it, change the model before any test below. Also record: §7.4 says 21 node types, `origin/release/v4.0.X` has 15 node modules (3 actions, 6 conditionals, 6 transforms) and the §7.4 table lists 15.
- [ ] **Step 2: Write the failing tests:** `parse_rejects_unknown_field` (root and inside a node -> `GraphError::UnknownField{path}`), `parse_rejects_future_schema_version` (2 -> `UnsupportedSchemaVersion{found: 2, supported: [1]}`), `validate_reports_all_errors_not_first` (dangling edge + port-type mismatch + duplicate node id + control cycle -> exactly 4 errors), `validate_never_panics_on_malformed` (12-row table -> all `Err`), `port_type_mismatch_and_cardinality_rejected` (Data into Control; `Many` into `One`), `semantic_digest_ignores_presentation` (x/y move, rename, add annotation -> equal digest), `semantic_digest_changes_on_semantic_edit` (param, edge, `kind_version` each differ), `canonical_bytes_independent_of_key_order_and_whitespace`, `signing_payload_golden` (fixed inputs -> hex vector; each field change changes bytes), `compile_is_deterministic_and_topological` (twice equal; every edge source index < target), `step_ids_stable_under_presentation_edits_and_node_reorder`, `compile_marks_boundaries` (Wait/SideEffect nodes appear as checkpoints before/after side effects, §5.10).
- [ ] **Step 3: Run:** `make svc-wf-test CARGO_ARGS="-p wf-graph"`; expected FAIL (types undefined).
- [ ] **Step 4: Implement** the signatures above (`#[serde(deny_unknown_fields)]` everywhere; `validate` collects, never short-circuits; a test-only `TestCatalog` in `tests/common` covers the control-flow / data / host-call categories of §7.4). Write `graph-schema.md`: semantic vs presentation split, versioning (`schema_version` format vs flow `version`), sign/run format, loop semantics, legacy-node-to-category table.
- [ ] **Step 5: Run:** `make svc-wf-test svc-wf-coverage`; expected PASS, >= 90. Commit `feat(svc-workflows): wf-graph canonical graph schema`.

### Task 7: Nine images and image CI (M)

**Files:**
- Create: `apps/svc_workflows/Dockerfile` (stage `builder`; targets `gateway-in`, `gateway-out`, `engine`, `executor`, `controller`, `pipeline`), `apps/svc_workflows/builders/Dockerfile.{rust,js,python}`, `scripts/svc_workflows/check_images.sh`
- Modify: `.github/workflows/svc-workflows.yml` (job `images`, 9-entry matrix, amd64 + arm64, `push: false`, trivy step), `Makefile` (`svc-wf-images`, `svc-wf-image-smoke`)
- Test: `tests/unit/svc_workflows/test_check_images.py` (fake `docker` shim on `PATH`)

**Interfaces:**
- Consumes: the six binaries (T4), exit code 70 (T3).
- Produces: local tags `elder-svc-workflows-{gateway-in,gateway-out,engine,executor,controller,pipeline}:phase0` and `elder-svc-workflows-builder-{rust,js,python}:phase0` (image names per §7.6; ghcr publish path left to Phase 1: Elder's `elder-<svc>` convention differs from the rules' `{org}/{repo}/{service}`); `check_images.sh <tag>...` (bash 3.2, `set -euo pipefail`) prints `examined=N passed=M`.

- [ ] **Step 1: Write the failing tests** (shim `docker`): `test_zero_images_fails`, `test_wrong_exit_code_fails` (shim returns 0 for a service image), `test_missing_not_implemented_text_fails`, `test_root_user_fails` (`.Config.User` empty or `0`), `test_builder_toolchain_version_mismatch_fails`, `test_all_nine_pass_prints_counts`.
- [ ] **Step 2: Run:** `$PYT tests/unit/svc_workflows/test_check_images.py -q`; expected FAIL (script missing).
- [ ] **Step 3: Implement** the script: service images run `docker run --rm --read-only --network none --cap-drop ALL --security-opt no-new-privileges:true <img> [mode]` and assert exit 70 + `not_implemented`; builders run the toolchain version command under `--network none` and assert the pinned version (`cargo component --version` contains `0.21.1`, `componentize-py --version` contains `0.25.1`, `componentize-js` version recorded UNVERIFIED). Dockerfiles: build context is the repo root (`wf-proto` reads `apps/api/grpc/proto/elder_internal`), multi-stage, base images by digest (resolve with `docker buildx imagetools inspect`; service runtime `gcr.io/distroless/cc-debian12:nonroot` is the builder's choice for "minimal binaries", record it), numeric non-root `USER`, `cargo build --release --locked`; builders `rust:1.97-slim-bookworm@<digest>`, Node 26.x (24.x while migrating) and `python:3.13-slim-bookworm` by digest.
- [ ] **Step 4: Run:** `make svc-wf-images svc-wf-image-smoke` (expect `examined=9 passed=9`), hadolint via pre-commit, then push and confirm the CI `images` job is green for all nine. Existing `docker-build.yml` publishes on EVERY branch push, which conflicts with the CI Image Build Policy; leave it untouched and do not add these images to it or to `scripts/deploy-beta.sh` (Phase 1).
- [ ] **Step 5: Commit** `feat(svc-workflows): nine empty gated images + image ci`.

### Task 8: Helm scaffolding for the role set (M)

**Files:**
- Modify: `k8s/helm/elder/values.yaml` (block `svcWorkflows`, `enabled: false`)
- Create: `k8s/helm/elder/templates/svc-workflows.yaml` (one `range` over roles)
- Test: `tests/unit/svc_workflows/test_helm_svc_workflows.py` (`helm template` + PyYAML)

**Interfaces:**
- Produces values `svcWorkflows.roles.<gateway-in|scheduler|gateway-out|engine|controller>.{enabled,replicas,image{repository,digest},resources}`; per enabled role: Deployment, ServiceAccount, PodDisruptionBudget, `CiliumNetworkPolicy` default-deny (mirror `templates/networkpolicy-default-deny.yaml`). `scheduler` uses the `gateway-in` image with arg `scheduler`; `engine` pod has containers `engine` + `executor` sharing `emptyDir` volumes `artifacts` (read-only in executor) and `uds`.

- [ ] **Step 1: Write the failing tests:** `test_disabled_by_default_renders_no_svc_workflows_resources`, `test_enabled_renders_five_deployments` (count == 5, printed), `test_engine_pod_has_exactly_engine_and_executor_containers`, `test_every_container_restricted_security_context` (runAsNonRoot, readOnlyRootFilesystem, allowPrivilegeEscalation false, drop ALL, seccomp RuntimeDefault; examined > 0), `test_executor_has_no_env_no_secret_mounts_and_pod_has_no_sa_token` (`automountServiceAccountToken: false` on every role except `controller`), `test_namespace_is_elder_only`, `test_default_deny_policy_per_role` (5), `test_no_nodeport_hostnetwork_or_hostport`, `test_helm_lint_passes`.
- [ ] **Step 2: Run:** `$PYT tests/unit/svc_workflows/test_helm_svc_workflows.py -q`; expected FAIL (no such resources).
- [ ] **Step 3: Implement** values + template (images by digest from T7; no env-suffixed names; PDB only when replicas >= 2). Deferred to Phase 1: role allow-rules, Tetragon policies, HPA/KEDA, Job templates, dal2alpha values overlay.
- [ ] **Step 4: Run** the pytest and `make helm-lint`; expected PASS.
- [ ] **Step 5: Commit** `feat(svc-workflows): helm scaffolding (disabled by default)`.

## Task group B: Elder (Python) groundwork

### Task 9: Workflow flag registry (S)

**Files:**
- Create: `apps/api/common/flags/workflows.py`
- Test: `tests/unit/test_workflow_flags.py`

**Interfaces:**
- Consumes: `flag_enabled(key, distinct_id, default=False)` (`apps/api/common/flags/posthog_client.py`).
- Produces: `FLAG_PREFIX = "workflows."`; `WORKFLOW_FLAGS: tuple[str, ...]` = the eight keys of §7.5 (`workflows.engine`, `.elder-resources`, `.entity-triggers`, `.diagram-convert`, `.canvas-graph`, `.custom-modules`, `.marketplace-sources`, `.snapshot-preinit`); `workflow_flag_enabled(key: str, distinct_id: str) -> bool`; `UnknownWorkflowFlag(KeyError)`.

- [ ] **Step 1: Write the failing tests:** `test_flag_names_match_spec_list` (exact tuple), `test_every_flag_uses_prefix`, `test_all_default_off_when_posthog_unreachable` (client raises -> False, never raises), `test_unknown_flag_raises`.
- [ ] **Step 2: Run:** `$PYT tests/unit/test_workflow_flags.py -q`; expected FAIL import error.
- [ ] **Step 3: Implement** (single prefix constant so a rename is one line: the spec says builder reconciles `workflows.*` with the rules' `{product}.{feature}` convention, i.e. `elder.workflows-*`; record the conflict in the PR for the owner). Per-language suffix for `snapshot-preinit` is a v4.1 decision.
- [ ] **Step 4: Run;** expected 4 PASS.
- [ ] **Step 5: Commit** `feat(workflows): flag registry (all default off)`.

### Task 10: village_id audit gate and diagram-child columns (M)

**Files:**
- Create: `scripts/svc_workflows/audit_village_id.py`, `.village-id-baseline`, Alembic revision `<next after alembic heads>_workflows_child_village_id.py` (do NOT hardcode: RLS reserves 042/043 on its branch, release head is 041)
- Modify: `apps/api/modules/diagrams/models/diagrams.py` (the `dg_shapes` and `dg_connectors` models gain `VillageIDMixin`), the shape/connector create paths (find with `git grep -n "DgShape(\|dg_shapes\|DgConnector("`; mint via `shared.utils.village_id`)
- Test: `tests/unit/svc_workflows/test_audit_village_id.py`, `tests/unit/test_diagram_child_village_id.py`

**Interfaces:**
- Produces: `audit(models_root: Path) -> AuditReport(tables: int, missing: list[str])` (raises on tables == 0); baseline ratchet (count may fall, never rise).

- [ ] **Step 1: Scope note (spec gap):** §7.2 says "`village_id` on child tables" without naming them; on `release/v4.0.X` about 75 of 121 modeled tables lack it (heuristic scan of the model files; top-level `dg_diagrams`, `stream_playbooks`, `iceflows` already have it). Scope here: non-legacy children melding addresses (`dg_shapes`, `dg_connectors`; Streams/Flows tables are deleted in Phase 5). Record the other 73 in `.village-id-baseline` for owner triage.
- [ ] **Step 2: Write the failing tests:** `test_audit_lists_tables_without_village_id` (fixture model file), `test_audit_zero_tables_raises`, `test_ratchet_fails_when_count_rises`, `test_ratchet_passes_when_count_falls`, `test_migration_is_idempotent` (run twice), `test_new_shape_gets_valid_village_id` (through the real create path; `is_valid_village_id`), `test_new_connector_gets_valid_village_id`.
- [ ] **Step 3: Run:** `$PYT tests/unit/svc_workflows/test_audit_village_id.py tests/unit/test_diagram_child_village_id.py -q`; expected FAIL.
- [ ] **Step 4: Implement** audit, migration (mirror `038_issues_village_id.py`: nullable unique column + index, existing rows stay NULL), model mixin, create-path mints. After RLS lands, rebase and re-run.
- [ ] **Step 5: Run** both files + `make lint` (tenant-scoping gate); expected PASS. Commit `feat(workflows): village_id on diagram children + audit gate`.

### Task 11: Registry extension: `diagram`, `flow`, `run` (M)

**Files:**
- Create: `apps/api/models/workflows.py`, Alembic revision `<next>_workflow_anchor_tables.py`
- Modify: `apps/api/common/refs/registry.py` (`_init_registry`), `shared/database/rls_tables.py` + RLS revision once the RLS branch has landed (new tenant tables must be covered; its `test_rls_all_tables` fails otherwise)
- Test: extend `tests/unit/test_refs_registry.py`; Create `tests/integration/test_refs_workflows.py`

**Interfaces:**
- Produces: `WfFlow(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin)` table `wf_flows` (`title String(500)`, `status SmallInteger server_default`, enum `FlowStatus{DRAFT=0, ENABLED=1, DISABLED=2}`); `WfRun` table `wf_runs` (`run_uuid UUID unique` from the data plane, `flow_id` FK, `record_mode SmallInteger` enum `RecordMode{ALWAYS=0, SAMPLED=1, ON_FAILURE=2, ON_DEMAND_REPLAY=3}` per §5B.2). Registered types: `diagram` -> `dg_diagrams` (title `title`, `/diagrams/{id}`), `flow` -> `wf_flows`, `run` -> `wf_runs` (title `run_uuid`). Spec gap: §7.2 says register diagram/flow/run but names no flow/run table (the new definition and run tables do not exist, legacy `stream_*`/`iceflows_*` die in Phase 5); these minimal anchors are a builder decision (§10), extended additively by Phase 1/2; column set and enum codes PROVISIONAL, reviewer confirms.

- [ ] **Step 1: Write the failing tests:** `test_registry_contains_diagram_flow_run`, `test_diagram_type_points_at_dg_diagrams`, `test_duplicate_registration_still_raises`; integration: `test_resolve_flow_by_village_id_same_tenant`, `test_resolve_flow_cross_tenant_returns_none`, `test_resolve_without_tenant_returns_none` (fail-closed, regression gh-237 shape), `test_run_uuid_unique`, `test_wf_tables_in_rls_registry` (after RLS lands).
- [ ] **Step 2: Run:** `$PYT tests/unit/test_refs_registry.py tests/integration/test_refs_workflows.py -q`; expected FAIL.
- [ ] **Step 3: Implement** models (use `server_default`, not ORM `default=`: penguin-dal ignores it on insert), migration (idempotent, mirror 038), the three registrations.
- [ ] **Step 4: Run** the files + `make lint`; expected PASS.
- [ ] **Step 5: Commit** `feat(workflows): register diagram/flow/run in the reference registry`.

### Task 12: Entity-event outbox design and write-path census (M)

**Files:**
- Create: `scripts/svc_workflows/census_entity_writes.py`, `docs/design/svc-workflows/event-outbox.md`
- Test: `tests/unit/svc_workflows/test_census_entity_writes.py`

**Interfaces:**
- Produces: `census(root: Path) -> Census(sites: list[Site], by_class: dict[str, int])`, `Site(file: str, line: int, cls: Literal["api_route","worker_direct_db","elder_api_client","unclassified"])` (raises when 0 sites examined; unclassified sites are reported, never dropped).

- [ ] **Step 1: Write the failing tests:** `test_classifies_api_route_write`, `test_classifies_worker_direct_db_write`, `test_classifies_elder_api_client_write` (tiny fixture sources), `test_zero_sites_examined_exits_2`, `test_unclassified_site_reported_not_dropped`.
- [ ] **Step 2: Run:** `$PYT tests/unit/svc_workflows/test_census_entity_writes.py -q`; expected FAIL.
- [ ] **Step 3: Implement** with `ast`. Run it against the repo (counts per class printed). Check the spec's UNVERIFIED claims by reading: callers of `AuditService.log_modification` (`apps/api/services/audit/service.py:213`) on entity writes; discovery direct upserts (`apps/worker/discovery/service.py:428-443`); `ElderAPIClient`.
- [ ] **Step 4: Write `event-outbox.md`:** why `shared/events.py::emit_event` cannot be the outbox (zero callers/consumers, fail-soft, sync redis client in an async function, int IDs); mechanism decision (Postgres trigger vs ORM/DAL hook) judged by census coverage of EVERY class found (including the worker); outbox row shape (tenant village_id, int-enum event type, non-PII payload, `origin{root_run, causation_depth, chain_hash}`, `relayed_at`); relayer sketch; Phase 1 test plan. No table/migration in Phase 0 (no relayer yet).
- [ ] **Step 5: Run** the pytest; commit `docs(workflows): entity-event outbox design + write-path census`.

## Task group C: Gating environment (dal2alpha) and spikes

### Task 13: Infrastructure assumption probe (M)

**Files:**
- Create: `scripts/svc_workflows/infra_probe.py`, `tests/unit/svc_workflows/fixtures/infra/*.json`, `docs/design/svc-workflows/infra-verification.md`
- Test: `tests/unit/svc_workflows/test_infra_probe.py`

**Interfaces:**
- Produces: `ProbeRow(assumption: str, status: Literal["Verified","Absent","Unknown"], evidence: str)`; `probe(kubectl_json: Callable[[list[str]], dict]) -> list[ProbeRow]` over the §7.9 rows plus the §5.9 items: Cilium version, Tetragon version, ARC runner scale sets, metrics-server, KEDA CRDs, default StorageClass, existing Valkey/Redis, `elder` namespace, S3-compatible endpoint, PSA labels.

- [ ] **Step 1: Write the failing tests:** `test_cilium_version_parsed_from_daemonset_image`, `test_absent_crd_reported_absent`, `test_kubectl_error_is_unknown_never_verified`, `test_zero_rows_raises`, `test_row_count_equals_assumption_table`.
- [ ] **Step 2: Run:** `$PYT tests/unit/svc_workflows/test_infra_probe.py -q`; expected FAIL.
- [ ] **Step 3: Implement** read-only (`kubectl get -o json` only).
- [ ] **Step 4: Run tests;** then (O-ENV human supplies the context) run live and write results into `infra-verification.md`. Expected per recon: Cilium 1.20.1, Tetragon 1.7.1, ARC present, no Elder namespace, no Valkey; any mismatch is a finding and each Unknown gets a named follow-up. Record the S3 provider decision here.
- [ ] **Step 5: Commit** `feat(svc-workflows): dal2alpha infra probe + verification record`.

### Task 14: Valkey cluster on dal2alpha (M)

**Files:**
- Create: `infrastructure/k8s/svc-workflows/valkey/values-dal2alpha.yml`, `scripts/svc_workflows/verify_valkey_cluster.py`
- Test: `tests/unit/svc_workflows/test_verify_valkey_cluster.py`

**Interfaces:**
- Produces: `verify(cluster_info: str, cluster_nodes: str, config_appendonly: str, version: str) -> ClusterReport(masters: int, replicas: int, slots_covered: int, aof: bool, version: str)` (ok only when slots_covered == 16384, masters >= 3, replicas >= 1, aof true).

- [ ] **Step 1: Write the failing tests:** `test_requires_all_16384_slots`, `test_aof_off_fails`, `test_zero_masters_raises`, `test_report_prints_version`.
- [ ] **Step 2: Run:** `$PYT tests/unit/svc_workflows/test_verify_valkey_cluster.py -q`; expected FAIL.
- [ ] **Step 3: Implement** verifier; install a pinned upstream Valkey cluster Helm chart (chart and version resolved in T13's record: candidate must support cluster mode + AOF, be non-PRC and license-compatible; UNVERIFIED) with persistence, non-root, default-deny policy plus same-namespace allow, and `# STATEFUL EXCEPTION (approved)` annotation on the StatefulSet (spec §3 names Valkey as stateful infra, but the overseer must approve the exception in the PR; how Elder deploys it today is UNVERIFIED). Per-role ACL users are Phase 1.
- [ ] **Step 4: Live gate (human-assisted):** `helm install` into `elder` on the dal2alpha context; run verifier (`masters >= 3`, `slots_covered == 16384`); chaos-lite: write `{p0}:probe`, delete the owning primary pod, poll until the key reads back, print `written=1 recovered=1`; failure to recover fails the task.
- [ ] **Step 5: Commit** `feat(svc-workflows): valkey cluster provisioning + verifier`.

### Task 15: Elder namespace bootstrap and object store with per-prefix credentials (M)

**Files:**
- Create: `infrastructure/k8s/svc-workflows/bootstrap/{Chart.yaml,templates/namespace.yaml,templates/default-deny.yaml}` (namespace `elder`, PSA `restricted` labels), `infrastructure/k8s/svc-workflows/object-store/policies/{engine,pipeline-publish,control-plane}.json`, `scripts/svc_workflows/provision_object_store.sh`, `scripts/svc_workflows/verify_object_store.py`
- Test: `tests/unit/svc_workflows/test_verify_object_store.py` (fake S3 client)

**Interfaces:**
- Produces: one bucket, prefixes `signed-artifacts/`, `quarantine/`, `history/` (§5.3/§7.9; literal names are the builder's, PROVISIONAL); credentials: `engine` (GET `signed-artifacts/*`; PUT-only `history/*`; nothing on `quarantine/*`), `pipeline-publish` (PUT `quarantine/*`), `control-plane` (GET `quarantine/*` and `history/*`, PUT `signed-artifacts/*`). `matrix(client_for: Callable[[str], S3Like]) -> MatrixReport(probes: int, mismatches: list[str])` (GET/PUT/LIST/DELETE per credential per prefix plus boundary keys; fails when probes == 0).

- [ ] **Step 1: Write the failing tests:** `test_matrix_fails_when_engine_can_read_quarantine`, `test_matrix_fails_when_engine_can_list_or_read_history`, `test_boundary_prefix_probe_included` (`signed-artifacts-extra/x` and `../quarantine/x` keys are probed, RF3), `test_matrix_fails_on_zero_probes`, `test_matrix_passes_for_correct_policy_set`.
- [ ] **Step 2: Run:** `$PYT tests/unit/svc_workflows/test_verify_object_store.py -q`; expected FAIL.
- [ ] **Step 3: Implement** matrix + policies + provisioning script (`set -euo pipefail`; secrets via env/stdin only, never argv). Provider per T13: reuse an S3-compatible store if the cluster has one; otherwise a pinned Helm chart whose licence and origin T13 vetted (Q15 default: object storage + Postgres index).
- [ ] **Step 4: Live gate (human-assisted):** `helm install` the bootstrap chart; provision the store; run `verify_object_store.py --live`; expected `mismatches=[]` with `probes` printed (>= 3 creds x 3 prefixes x 4 ops + boundary probes).
- [ ] **Step 5: Commit** `feat(svc-workflows): elder namespace bootstrap + object store matrix`.

### Task 16: Spikes S1 (single-partition throughput) and S2 (fencing) (M)

**Files:**
- Create: `apps/svc_workflows/crates/wf-spikes/{Cargo.toml,src/{lib.rs,summary.rs,partition_throughput.rs,fencing.rs},src/bin/spike_valkey.rs}` (not in any image; excluded from coverage and `deny` exceptions listed in the PR), appended section in `docs/design/svc-workflows/phase0-spike-results.md`
- Modify: `Makefile` (`svc-wf-spike-live`)
- Test: unit tests in `summary.rs`; live tests behind feature `live`, run only by `make svc-wf-spike-live` (env `WF_SPIKE_VALKEY_URL` required: unset = hard failure, never skip)

**Interfaces:**
- Consumes: `penguin-spine` (T2 SHA; the spike falls back to raw commands only where spine lacks an API, and says which).
- Produces: `Summary::from_samples(&[Duration]) -> Summary{p50,p99,max,count}`; `Counts{sent,acked,rejected,duplicates}` with `fn assert_balanced(&self) -> Result<(), String>` (`sent == acked + rejected`, `sent > 0`); `const FENCED_HANDOFF_LUA: &str` (hash-tagged keys; XADD hand-off, XACK last, dedup key); `lease_acquire/lease_renew` (`SET NX PX`).

- [ ] **Step 1: Write the failing tests:** `summary_percentiles_exact_on_known_samples`, `counts_balance_or_error`, `zero_sent_is_error`; live: `single_partition_reports_balanced_counts`, `stale_epoch_handoff_is_rejected` (fenced_rejects >= 1, duplicates == 0), `cross_slot_script_is_rejected_on_cluster` (a deliberately cross-slot call returns CROSSSLOT, proving cluster mode; RF4), `fenced_overhead_reported_vs_unfenced`.
- [ ] **Step 2: Run:** `make svc-wf-test CARGO_ARGS="-p wf-spikes"`; expected FAIL (crate missing).
- [ ] **Step 3: Implement** the signatures; the harness prints counts and writes a JSON block.
- [ ] **Step 4: Run live on dal2alpha:** `make svc-wf-spike-live`; paste into `phase0-spike-results.md` the rows: S1 single-partition events/s and p99 ack (the ordered-mode ceiling, §5B.3), S2 fenced vs unfenced ops/s and added latency, with Valkey version and node count.
- [ ] **Step 5: Commit** `spike(svc-workflows): single-partition throughput + fencing`.

### Task 17: Spike S3: pooled wasmtime cost per Tier-1 language (M)

**Files:**
- Create: `apps/svc_workflows/crates/wf-spikes/src/pooled_wasmtime.rs`, `apps/svc_workflows/spikes/components/{rust,js,python}/` (trivial `wf:spike/hello` world, `run: func(input: string) -> string`), `scripts/svc_workflows/build_spike_components.sh`
- Modify: `Makefile` (`svc-wf-spike-wasmtime`)
- Test: unit tests in `pooled_wasmtime.rs`; component fixtures built in the T7 builder images (spike builds may fetch dependencies; the pipeline's no-network rule is Phase 1)

**Interfaces:**
- Consumes: pinned `wasmtime` (T2), builder images (T7), `Summary`, `Counts` (T16).
- Produces: `measure(component: &[u8], calls: usize) -> LangReport{artifact_bytes, precompile_ms, cold_instantiate_ms, warm_p50, warm_p99, rss_per_instance}`; recipes per §5C.1 (`cargo-component` 0.21.1; `componentize-py` 0.25.1 `--stub-wasi`; `componentize-js ... --disable all`, version UNVERIFIED).

- [ ] **Step 1: Write the failing tests:** `report_requires_nonzero_calls`, `each_language_must_succeed_at_least_once` (all three present or the run errors), `precompiled_cwasm_load_faster_than_uncached` (assert on recorded numbers, Python expected 4.5-5.4 ms vs 3.3-4.5 s per §5C.1; failure prints both).
- [ ] **Step 2: Run:** `make svc-wf-test CARGO_ARGS="-p wf-spikes pooled"`; expected FAIL.
- [ ] **Step 3: Implement** with the pooling allocator and pre-instantiation (§5B.7); `.cwasm` key `{digest}-{wasmtime_abi}-{collector}` used for the cache measurement only.
- [ ] **Step 4: Run** `make svc-wf-spike-wasmtime` with N >= 1000 calls per language; add per-language rows to `phase0-spike-results.md` (and note any language whose build recipe failed, as a finding).
- [ ] **Step 5: Commit** `spike(svc-workflows): pooled wasmtime cost per language`.

### Task 18: Spike S4: Elder bulk lookup (S)

**Files:**
- Create: `scripts/svc_workflows/spike_elder_bulk_lookup.py`
- Test: `tests/unit/svc_workflows/test_spike_elder_bulk_lookup.py`

**Interfaces:**
- Consumes: `resolve_by_village_id` (`apps/api/common/refs/registry.py`).
- Produces: `resolve_many(db, village_ids: list[str], tenant_id: int) -> dict[str, dict]` (spike prototype, one `IN` query per registry table, tenant-scoped, fail-closed without tenant; NOT mounted in the app); `run_spike(n: int) -> SpikeCounts(expected, found, singles_ops_s, bulk_ops_s)`.

- [ ] **Step 1: Write the failing tests:** `test_bulk_matches_single_results_for_seeded_ids` (found == expected > 0), `test_bulk_without_tenant_returns_empty`, `test_zero_ids_raises`, `test_cross_tenant_ids_not_returned`.
- [ ] **Step 2: Run:** `$PYT tests/unit/svc_workflows/test_spike_elder_bulk_lookup.py -q`; expected FAIL.
- [ ] **Step 3: Implement**; seed N = 10,000 entities on local MicroK8s alpha via the existing seed scripts.
- [ ] **Step 4: Run** the spike; add `singles_ops_s` vs `bulk_ops_s` per Quart worker and the Postgres latency to `phase0-spike-results.md`; note the `GetMany`/cursor shapes this informs for T5.
- [ ] **Step 5: Commit** `spike(workflows): elder bulk lookup`.

## Task group D: penguin-libs extraction (SEPARATE REPO: `/home/penguin/code/penguin-libs`)

Common to T19-T23 (call E, §8.2/8.3; all PROVISIONAL, owner to confirm: Q5): work in that repo on `feature/<crate>-extract` off the crate's release branch `release/rust-<crate>/v0.1.x` (create from `main` if absent; penguin-libs branching practice UNVERIFIED, confirm with its owners), packages at `packages/rust-<crate>/`; copy-then-genericize from Waddles at a recorded SHA (read-only; note it in the crate `CHANGELOG.md`; no dependency on the Waddles repo, D7); product-specific parts become traits, Waddles' own migration to these crates is out of scope (Q5 owner); exact pins + `deny.toml` + 90% coverage per that repo's Rust standards; tests run through the crate's own `make test` (its containerized toolchain, UNVERIFIED for the new crates), written below as `cargo test` for brevity but never on the host; open a DRAFT PR and record its URL and head SHA in T2's `penguin-libs-consumption.md`. "Started" (Phase 0) = draft PR open, generic tests green in penguin-libs CI, seams documented in the PR description; merge is Phase 1 (Elder consumes by pinned SHA, never a copy).

### Task 19: `penguin-service-auth` from `waddles:core/service_auth` (M)

**Files:** Create `packages/rust-service-auth/{Cargo.toml,CHANGELOG.md,src/{lib.rs,jwt_hardening.rs,test_support.rs}}`.

**Interfaces:**
- Produces: `trait TrustBundle: Send + Sync` (keys by `kid`), `ServiceClaims`, `verify(...)`, `JwksTrustBundle`, `MachineJwtClient`, `BoundMachineJwtClient` (names as in Waddles at the recorded SHA), plus new `trait SubjectPolicy { fn accept(&self, sub: &str) -> bool }` so Elder can require `sub` = the role's SPIFFE ID (§5.7) without product strings in the crate.

- [ ] **Step 1:** Copy Waddles' tests first; run `cargo test`; expected FAIL (does not compile).
- [ ] **Step 2: Add the failing seam tests:** `verify_rejects_alg_none`, `verify_rejects_wrong_audience`, `verify_rejects_expired_and_not_yet_valid`, `verify_accepts_eddsa_with_kid_rotation_overlap` (old + new key), `ttl_over_one_hour_rejected` (§5.7), `subject_policy_rejection_denies`.
- [ ] **Step 3: Implement** the genericization (strip Waddles claim/env names behind config) and `SubjectPolicy`; run `cargo test`; expected PASS.
- [ ] **Step 4: Commit and open the draft PR;** record URL/SHA.

### Task 20: `penguin-bundle-signing` from `waddles:core/bundle_executor/src/signing.rs` (S)

**Files:** Create `packages/rust-bundle-signing/{Cargo.toml,CHANGELOG.md,src/lib.rs}`.

**Interfaces:**
- Produces: `SigningDomain(&'static str)`; `signing_payload(domain: SigningDomain, id: &str, version: &str, output_digest: &[u8; 32], approval_id: &str) -> Vec<u8>` (Ed25519 input: length-prefixed, domain-separated, §6.7); `SignedSidecar`; `PlatformPublicKeys` with `verify(&self, sidecar: &SignedSidecar, payload: &[u8]) -> Result<(), SignError>` and `require_nonempty(&self) -> Result<(), SignError>` (unset = fail closed at startup); `kid` rotation with overlap.

- [ ] **Step 1: Write the failing tests:** `waddles_golden_vector_still_verifies` (byte-compat: vector taken from Waddles' own test, Waddles' domain tag), `other_domain_does_not_verify` (an Elder-domain payload fails against a Waddles-domain signature), `kid_rotation_overlap_accepts_old_and_new`, `empty_key_set_fails_closed`, `tampered_digest_rejected`.
- [ ] **Step 2: Run** `cargo test`; expected FAIL.
- [ ] **Step 3: Implement** by genericizing `signing.rs` (domain becomes a parameter; Waddles' tag stays available as a constant only in tests); run; expected PASS.
- [ ] **Step 4: Commit and open the draft PR;** record URL/SHA. `wf-graph::signing_payload` (T6) and this crate must agree on framing: add a cross-check vector in the Phase 1 plan.

### Task 21: `penguin-capability-gate` from `waddles:core/bundle_capability_gate` (L)

**Files:** Create `packages/rust-capability-gate/{Cargo.toml,CHANGELOG.md,src/{lib.rs,gate.rs,denied.rs,grant.rs,scope.rs,quota.rs,audit.rs}}`. Waddles' static `permission.rs` catalog and `gate_economy_tests.rs` stay in Waddles.

**Interfaces:**
- Produces: `trait PermissionCatalog: Send + Sync { fn entry(&self, key: &str) -> Option<CatalogEntry> }` (replaces Waddles' closed `PermissionId` enum); `CapabilityGate::new(catalog: Arc<dyn PermissionCatalog>, grants: Arc<dyn GrantSource>)`; `Denied { reason: &'static str, key: String }` with audit record; deny-by-default, gate runs first on every import (§5.5).

- [ ] **Step 1: Write the failing tests** with a toy 3-entry catalog: `unknown_permission_denied_by_default`, `gate_runs_before_every_import_call` (counting import never invoked on deny), `ungranted_capability_denied_without_ambient_authority`, `denied_carries_reason_code_and_audit_record`, `quota_ledger_trait_is_consulted`.
- [ ] **Step 2: Run** `cargo test`; expected FAIL.
- [ ] **Step 3: Implement** the seam (read Waddles `gate.rs`/`permission.rs` to find every product-specific type); run; expected PASS.
- [ ] **Step 4: Commit and open the draft PR;** record URL/SHA.

### Task 22: `penguin-egress` from `waddles:core/{bundle_host_http,egress_assertion,egress_proxy}` (L)

**Files:** Create `packages/rust-egress/{Cargo.toml,CHANGELOG.md,src/{lib.rs,guard.rs,ip_policy.rs,dns.rs,assertion.rs,limits.rs}}` (library only; `gateway-out` in Elder composes it, Phase 1).

**Interfaces:**
- Produces: `EgressGuard` running the §6.5 pipeline in order with exact reason codes (`scheme_not_allowed`, `malformed_url`, `host_not_declared`, `method_not_declared`, `tier_denied`, `host_denylisted`, `ssrf_blocked_address`, `dns_rebind_blocked`, `credential_host_mismatch`, `rate_limited`, `tls_verification_failed`, `redirect_off_allowlist`); seams `trait EgressRuleSource`, `trait CredentialBroker`, `trait HttpTransport`, `trait ProxyAssertionSigner`, `trait Resolver`, and new `trait PipelineStep { fn check(&self, req: &EgressRequest) -> Result<(), Denial> }` for Elder's `+` steps 3, 5, 8, 13 (chain verification, tier/tenant denylist, credential-host binding, audit); `ValidatedTarget` carries the pinned `SocketAddr` (SNI/`Host` stay the original name); `is_forbidden_address(ip, allow_private) -> Option<&'static str>`.

- [ ] **Step 1: Write the failing tests:** `cgnat_100_64_0_0_10_blocked` (100.64.0.1, 100.127.255.254 -> `ssrf_blocked_address`; UNVERIFIED whether Waddles blocks it today, the Elder requirement benefits both), `ip_literal_forms_normalized_before_check` (`http://2130706433/`, `0177.0.0.1`, `0x7f.1`, `[::ffff:127.0.0.1]`, `64:ff9b::7f00:1`, `2002:7f00:1::` all blocked), `any_bad_resolved_address_denies` (`[public, 10.0.0.1]`), `connection_pinned_to_validated_ip` (resolver returns a different address on a second lookup; transport's connected address == the validated one), `redirect_rechecks_steps_1_to_8` (redirect to a private host denied, max 3), `pipeline_steps_run_in_spec_order` (custom steps observe positions 3, 5, 8, 13), `reason_codes_are_exact_strings`.
- [ ] **Step 2: Run** `cargo test`; expected FAIL.
- [ ] **Step 3: Implement** (extract `bundle_host_http/src/egress.rs` guard, `egress_assertion`, `egress_proxy` `ip_policy.rs`/`dns.rs`/`limits.rs`; add pinning and CGNAT where missing); run; expected PASS with coverage >= 90.
- [ ] **Step 4: Commit and open the draft PR;** record URL/SHA.

### Task 23: `penguin-bundle-executor` host core from `waddles:core/bundle_executor` (L)

**Files:** Create `packages/rust-bundle-executor/{Cargo.toml,CHANGELOG.md,src/{lib.rs,engine.rs,limits.rs,refuse.rs},tests/fixtures/*.wat}`. Excluded (Waddles-specific): `connector.rs`, `bucket.rs`, `heartbeat.rs`, `manifest.rs`. Fuel metering and the `.cwasm` cache are Phase 1 requirements (§5B.7): the crate must not fake them (no hardcoded `fuel_used: 0`).

**Interfaces:**
- Produces: `build_engine(cfg: &EngineConfig) -> Result<Engine, ExecutorError>` (component model, WASI 0.2, epoch interruption, pooling allocator, pinned collector); `trait HostImports: Send + Sync` (the embedding crate supplies `context/http/elder/diagram/log/clock/flags/emit`); `trait ArtifactSource { fn bytes(&self, digest: &Digest) -> Result<Vec<u8>, ExecutorError> }` (the executor never fetches); `Limits { wall_ms: u64, memory_bytes: usize, instances: u32 }` with start values 2000 ms / 64 MB / 32 concurrent (§6.3); `ExecutorError::NotImplemented` -> non-retryable.

- [ ] **Step 1: Write the failing tests** (WAT component fixtures): `component_importing_wasi_sockets_is_refused`, `guest_sees_no_env_and_no_args`, `no_filesystem_beyond_empty_scratch`, `epoch_deadline_traps_busy_loop_within_2x_limit`, `memory_limit_trap_is_counted`, `unimplemented_host_call_returns_not_implemented_nonretryable` (A3), `dependency_graph_has_no_network_or_db_crates` (`cargo tree`: no reqwest/redis/sqlx; prints packages examined > 0, as Waddles' `dependency_policy.rs`).
- [ ] **Step 2: Run** `cargo test`; expected FAIL.
- [ ] **Step 3: Implement** by extracting `engine.rs` (build_engine/linker/epoch ticker) and the refusing interfaces; wasmtime pinned to the T2 version; run; expected PASS.
- [ ] **Step 4: Commit and open the draft PR;** record URL/SHA.

## Task group E: Exit gate (Elder)

### Task 24: Phase 0 exit-gate checker (M)

**Files:**
- Create: `scripts/svc_workflows/phase0_gate.py`
- Modify: `Makefile` (`svc-wf-phase0-gate`), `docs/design/svc-workflows/phase0-spike-results.md` (5B.5 replacement table)
- Test: `tests/unit/svc_workflows/test_phase0_gate.py`

**Interfaces:**
- Consumes: `parse_decisions`, `require` (T1); results docs (T13-T18); `gh` CLI.
- Produces: `CheckResult(name: str, examined: int, passed: bool, detail: str)`; `run_gate(repo: Path, head: str) -> list[CheckResult]`; process exit non-zero if any `passed` is false OR any `examined == 0`. Checks: `rls_landed` (`shared/database/rls_tables.py` exists with non-empty `RLS_TABLES` and an Alembic revision containing `CREATE POLICY`, OR decision `O-RLS` is ACCEPTED), `decisions_q1_q6` (each ANSWERED/ACCEPTED passes; PROVISIONAL passes with a printed warning count per call BB; OPEN or missing fails), `ci_green` (`gh run view --json jobs` on `svc-workflows.yml` for `head`: rust-gates + all nine image jobs succeeded), `spike_numbers` (S1-S4 sections each hold numeric values and counts > 0), `infra_recorded` (T13-T15 evidence present, `mismatches=[]`), `libs_prs` (five extraction PR URLs recorded and resolvable).

- [ ] **Step 1: Write the failing tests** (fixture repo tree + fake `gh`): `test_gate_fails_when_rls_missing_and_not_accepted`, `test_gate_fails_on_open_decision`, `test_gate_warns_but_passes_on_provisional`, `test_gate_fails_on_zero_ci_jobs`, `test_gate_fails_when_a_spike_section_missing`, `test_gate_fails_on_zero_examined_in_any_check`, `test_gate_passes_all_green_with_counts_printed`.
- [ ] **Step 2: Run:** `$PYT tests/unit/svc_workflows/test_phase0_gate.py -q`; expected FAIL.
- [ ] **Step 3: Implement** the checker (reuse `decision_record.py`; no `|| true`).
- [ ] **Step 4: Run live:** `make svc-wf-phase0-gate`; expected all checks `passed` with `examined > 0`, warnings listing every PROVISIONAL row. Update §5B.5 numbers: add the measured table to `phase0-spike-results.md` and push the matching edit to the spec file on `docs/svc-workflows-spec` (spec and plan travel together).
- [ ] **Step 5: Commit** `feat(svc-workflows): phase 0 exit-gate checker`; open the PR summary for the Phase 0 exit (PROVISIONAL list included, owner's written acceptance preferred).

## Spec gaps found while planning (for the reviewer)

- §7.2 names no wasmtime version (Waddles: `=49.0.4` in its working tree, caller cited `=49.0.2`); T2 resolves it.
- RLS is unlanded and owns Alembic 042/043; revision numbers must not be hardcoded and new tenant tables need RLS registration (T10, T11).
- §7.2 "village_id on child tables" and "register flow/run" name no tables (T10, T11 scope them; flagged PROVISIONAL).
- §7.4 says 21 node types; `release/v4.0.X` has 15 node modules (T6 records it).
- §7.6 adds images to `docker-build.yml`, which publishes on every branch push (conflicts with the CI Image Build Policy); T7 uses a separate build-only workflow.
- §7.5 `workflows.*` vs the rules' `{product}.{feature}` flag convention (T9 isolates the prefix).
- §8.1 SHAs are older than Waddles' current pins; spine ships D30 envelope/MAC types overlapping calls X/AT (T2 reads it before any envelope format is fixed).
- `dal2alpha` is absent from the rules' cluster/values-file tables; "Cargo.lock + vendoring" does not say whether vendor output is committed (plan: not committed, `--locked` builds).
- Stable `llvm-cov` has no branch coverage (T3 gates lines, functions, regions).

## Next plans (separate, not part of this plan)

| Plan | Phase | Starts after |
|---|---|---|
| Phase 1: role skeleton + scale spine (engine/executor/gateways real logic, partitions + lease + fencing, envelopes, history sink, minimal vault, compile pipeline, read-only `elder.*`, outbox + relayer, load harness, pre-init spike); also carries Phase 0 deferrals: envelope proto, publish path + `deploy-beta.sh` entries, dal2alpha values overlay, Tetragon, Valkey ACLs, HPA/KEDA, Job templates | 1 | T24 green + extraction PRs merged or the owner-approved contingency (§8.3) |
| Phase 2: Streams replacement (node types, triggers, `elder.*` writes, `elder.notify.send`, cron) | 2 | Phase 1 exit |
| Phase 3: Flows replacement (runner jobs, promotions, vault hardening #171, diagram prerequisites) | 3 | Phase 2 exit |
| Phase 4: canvas, conversion, UI, marketplace sources, tenant upload | 4 | Phase 3 exit |
| Phase 5: cutover and deletion before GA | 5 | Phase 4 exit |
| v4.1.x: Go/C#/Ruby, multi-cell router, snapshot opt-in | post-GA | GA |
