# Elder Platform Merge — Modular Product Spec & Implementation Plan

## Context

PenguinTech maintains three overlapping products: **Elder** (infrastructure/entity relationship tracking, CMDB), **Ruffled** (helpdesk: tickets, SLA, email, KB, AI chatbot), and **IceCharts** (collaborative diagramming). They duplicate identity, tenancy, auth, licensing, deployment, and frontend scaffolding three times.

This plan merges Ruffled and IceCharts **into Elder** as toggleable modules, modularizes Elder's own feature areas, and elevates Elder into an **AI data-source/RAG provider** (pgvector semantic search, Neo4j graph projection, MCP + REST RAG surface). Every feature/product segment can be turned on or off. **Rookery is excluded** (user decision). Fresh DB — no data migration.

## Locked Decisions

| Decision | Choice |
|---|---|
| Base | Elder repo + branding; work on `release/v4.0.X` branch (major version bump to 4.0.0) |
| Architecture | Modular monolith — one Quart API; modules loaded conditionally; Python 3.13 everywhere; drop Go (incl. Elder's vestigial `shared/database/*.go`) |
| Granularity | Everything modular — Elder's own areas are modules too; thin always-on core (identity, tenants, auth/penguin-aaa, RBAC, admin, licensing, audit, webhooks framework) |
| Toggle layers | 1) Deployment (Helm/env) ⊇ 2) License entitlement ⊇ 3) Per-tenant admin toggle ⊇ 4) PostHog feature flags per-feature (AI, links, all future features) |
| DB | SQLAlchemy + Alembic = schema authority; **penguin-dal = runtime** (Ruffled's SQLAlchemy-runtime code gets ported); PostgreSQL primary via **pgvector/pgvector image** |
| Queues/cache | **Redis + Redis Streams for all caching and queuing.** Full job lifecycle in Streams: workers consume jobs from `elder:jobs:<module>` consumer groups AND publish results to `elder:results:<module>`. Replaces Celery (IceCharts) and APScheduler queuing (Ruffled) |
| Worker reliability | **Deployment + XAUTOCLAIM** (not StatefulSet): pod-name consumer identity via downward API, periodic reclaim sweeper for dead consumers, idempotency keys (job UUID dedup), dead-letter stream after N delivery attempts |
| Internal comms | gRPC for direct synchronous service-to-service; REST versioned `/api/v1/...` client-facing only |
| Real-time | Native Quart WebSockets + Redis pub/sub (`elder:dg:{diagram_id}`); drop Flask-SocketIO/socket.io-client |
| Graph | **Neo4j as synced projection** — Postgres source of truth, worker syncs; graceful degradation to existing networkx path when absent |
| Semantic search | **pgvector** embeddings across module data (entities, tickets, KB, diagrams) |
| AI provider | Pluggable (port Ruffled `ai_config`): **Ollama default with latest Gemma models** (Gemma 3 generation, EmbeddingGemma embeddings); OpenAI-compatible + WaddleAI (enterprise-gated) alternatives |
| RAG surface | MCP (primary, extend `apps/mcp`) + REST `/api/v1/ai/search`, `/api/v1/ai/rag/retrieve` + in-product AI features (UI assistant, ticket auto-suggestions, NL entity search) |
| Frontend | Single React 18 + Vite SPA (Elder `web/`); lazy-loaded module route bundles; module-gated sidebar |
| Workers | One consolidated worker image loading task groups per enabled module; scanner stays separate (NET_RAW/toolchain) |
| Flags/analytics | Self-hosted PostHog (external shared instance, NOT a subchart) — feature flags + product analytics |

## Module Taxonomy

**Always-on core:** identity/IAM, tenants/orgs, auth (JWT/OIDC/SAML via penguin-aaa), RBAC, admin console, licensing, audit, settings, webhooks framework, `/api/v1/modules` endpoint.

| Module | Source | Contents | Prefix |
|---|---|---|---|
| `infrastructure` | Elder | Entities, compute/network/storage, dependencies, relationship map, organizations | (existing) |
| `ipam` | Elder | IPAM, networking | (existing) |
| `sbom` | Elder | Software, SBOM, vulnerabilities, scans, license policies | (existing) |
| `services_oncall` | Elder | Service catalog, health checks, on-call rotations | (existing) |
| `issues` | Elder | Issues, projects, milestones, labels, comments | (existing) |
| `discovery` | Elder | Discovery/sync connectors (Okta, AWS, GCP, K8s, LDAP, Google Workspace) | (existing) |
| `secrets` | Elder | Secrets, keys, certificates | (existing) |
| `datastores` | Elder | Data stores + PII/PHI/PCI metadata | (existing) |
| `webhooks_alerting` | Elder | Webhooks, alert config, costs | (existing) |
| `helpdesk` | Ruffled | Tickets, SLA, canned responses, email accounts, forms/CAPTCHA, companies/contacts, KB | `hd_` |
| `diagrams` | IceCharts (main+v1.1.x) | Canvas (React Flow), shapes/connectors, icon sets, shape libraries, drawings/versions, collections, sharing/public links + analytics, comments (@mentions), export PNG/SVG/PDF/JSON, storage providers (MinIO/S3/GCS/GDrive/OneDrive), templates, Elder-entity import; real-time collab sub-feature (Pro+) | `dg_` |
| `playbooks` | IceCharts v1.1.x | **IceStreams workflow engine**: node-graph editor, topological executor (conditionals/loops/transforms), action nodes (HTTP/gRPC/MCP/cloud), 31 connectors (13 PenguinTech + 18 DBs) **+ new third-party connector catalog (Slack, Discord, Teams, Telegram, GitHub/GitLab, Jira, PagerDuty, Twilio, Google Sheets/Drive, S3, …) usable as both playbook nodes and diagram nodes**, schedules/webhooks/forms triggers, approval gates. Free; `ask_ai` node AI-gated | `playbook_` (existing) |
| `cicd_flows` | IceCharts v1.1.x | **IceFlows CI/CD**: git-bound stage pipelines, approvals, stage tests/calls, Darwin AI review (AI-gated), promotions/merges, GitHub/GitLab webhooks, credentials. Free | `iceflows_` (existing) |
| `functions` | IceCharts v1.1.x | **IceRuns serverless FaaS**: functions in 7 runtimes, warm container pools, webhook/cron/manual triggers, execution logs/artifacts. Free. Invoker = separate container (like scanner) | `iceruns_` (existing) |
| `ai_search` | New | pgvector semantic search, Neo4j graph queries, embedding pipeline, RAG API, MCP tools, AI assistant, **AI auto-recommend entity/resource relationships** (suggests dependencies via embedding similarity + graph heuristics, human-approved) | `ai_` |
| `access_reviews` | Elder | Access reviews, resource roles (Enterprise per-feature SKU) | (existing) |
| `compliance` | New (Ent.) | **KMS field encryption** (AWS KMS/GCP KMS envelope encryption via `shared/crypto` + `EncryptedString` SQLAlchemy type, Postgres-only), GDPR tooling (DSAR export, right-to-erasure, retention policies), audit export/SIEM, SCIM provisioning | `cp_` |

**Feature-parity requirement:** exhaustive per-product feature inventories (Elder, Ruffled, IceCharts — including Flows) are being compiled and MUST be appended to this spec as porting checklists; every inventoried feature maps to a module + phase or is explicitly marked deferred with user sign-off.

## License Tier Matrix (APPROVED)

| | **Free** | **Professional** (per-seat) | **Enterprise** (per-feature + per-seat) |
|---|---|---|---|
| **Target** | Early startups, non-profits, home labs | Established companies | SSO / HIPAA / GDPR-bound orgs |
| **Features** | CMDB + relationship mapping, IPAM, issues/projects, services/on-call, documents/KB, helpdesk (tickets/SLA/email), diagrams/charts, **workflows (Playbooks), CI/CD pipelines (IceFlows), serverless functions (IceRuns)** — AI-powered nodes (ask_ai, Darwin review) follow Pro AI gating — local auth + MFA | + MCP server access, Google Auth SSO, AI doc search/generation, AI auto-recommend entity/resource relationships, live collaboration, SBOM/vuln scanning, basic discovery (K8s/network), advanced analytics/reporting, webhook integrations (Slack/PagerDuty) | + OAuth2/OIDC/SAML2 SSO with custom roles, AWS/GCP KMS field encryption (sensitive Postgres data), access reviews, audit export/SIEM + extended retention, SCIM provisioning, IdP discovery sync (Okta/AD/Google Workspace), GDPR tooling (DSAR export, right-to-erasure, retention policies), WaddleAI provider |

**Licensing enforcement:**
- Professional: **per-seat** — penguin-licensing seat-count entitlement, checked against active (non-disabled) identities per tenant; grace behavior on overage (warn, then block new seats — never lock out existing users).
- Enterprise: **per-feature + per-seat** — each Enterprise feature is its own license feature key/SKU (`sso-oidc-saml`, `kms-encryption`, `access-reviews`, `siem-export`, `scim`, `idp-sync`, `gdpr-tooling`, `waddleai`), mapping 1:1 to module manifest `license_feature` / PostHog flag keys.
- Free requires no license key; graceful degradation everywhere per house rules.

## Technical Design

### 1. Backend module framework
- Current seam: `apps/api/main.py:_register_blueprints()` hardcodes ~45 blueprint imports; `shared/database/__init__.py:init_sqlalchemy_tables()` hardcodes model imports.
- New `apps/api/modules/` package: explicit `MODULES` list of frozen `ModuleManifest` dataclasses (`name, title, license_feature, depends_on, blueprints (lazy zero-arg callable), models, table_prefix, worker_tasks, nav_id, optional_services, default_enabled`) + `registry.py` (env parsing, dep validation, topo-sort, blueprint mounting, `app.extensions["elder_modules"]` state + blueprint→module reverse map).
- **Phase 0 trick:** module `blueprints.py` initially re-exports existing `apps/api/api/v1/*` blueprints in place — zero file moves; physical moves happen per-module later.

### 2. Toggle resolution (strict AND, cheapest-first)
- Layer 1 startup: `ELDER_MODULE_<NAME>` env (Helm ConfigMap) → unmounted = 404.
- Layer 2 license: penguin-licensing feature key per manifest; guarded 403 `MODULE_UNLICENSED`.
- Layer 3 tenant: new core table `tenant_modules(tenant_id, module_name, enabled, settings JSONB)`, Redis-cached (`elder:modtoggle:{tenant_id}`, TTL 60s, busted on write); admin API `GET/PUT /api/v1/tenants/{id}/modules`; 403 `MODULE_DISABLED`.
- Layer 4 PostHog: `posthog` python lib with **local evaluation** (no per-request network); helper `flag_enabled(key, default)` degrades to default when PostHog down. Client: `posthog-js` + `useFeatureFlagEnabled`. Module visibility does NOT use PostHog — single source of truth is `GET /api/v1/modules` (returns per-module `installed/licensed/tenant_enabled/effective/capabilities` + flag snapshot).
- One `before_request` hook resolves `request.blueprint` → module and enforces layers 2–3.

### 3. Database
- Single Alembic history; **schema is toggle-independent** (all module models always imported → deterministic migrations, empty tables are free).
- Existing Elder tables keep names; absorbed modules prefixed (`hd_`, `dg_`, `ai_`) — enforced by unit test.
- penguin-dal runtime reflects the SQLAlchemy-defined tables (`DAL(url, migrate=False)`), same as Elder today; `app.db.hd_tickets` available once models registered.
- Identity unification (re-author, not migrate): Ruffled `users`→Elder `identities`, Ruffled `tenants`(uuid)→Elder `tenants`(int); requesters→`hd_contacts`/`hd_companies` with optional `identity_id`; IceCharts users→`identities`, `dg_diagrams.owner_identity_id`. Reuse Ruffled `ai_configs`/`ai_conversations` in `ai_search` (extend provider enum with `waddleai`).

### 4. Redis Streams job bus
- Job streams `elder:jobs:<module>`, results `elder:results:<module>`; consumer group per module task group.
- Workers = K8s **Deployment**; consumer name = pod name (downward API); `XREADGROUP`/`XACK`; periodic `XAUTOCLAIM` sweeper reclaims idle pending messages and deletes orphaned consumers; job UUID idempotency check before execution; dead-letter stream after N deliveries.
- Streams also drive the embedding pipeline (`elder:ai:index`) and Neo4j sync events.
- Worker main (`apps/worker/main.py:WorkerService`) gains a task-group registry keyed off module manifests' `worker_tasks`, loaded only when the module env flag is on. Task groups: discovery (existing), helpdesk (email poll/send, SLA breach checker), diagrams (export/thumbnail rendering), ai_search (embedder, graph-syncer, backfill). Scheduled/cron tasks keep the existing aiocron pattern but enqueue jobs to Streams rather than executing inline.

### 5. Frontend
- `web/src/modules/<name>/index.tsx` manifests `{id, nav: MenuCategory[], adminNav?, routes: RouteObject[]}`; all pages `React.lazy` → one Vite chunk per module.
- `useModules` hook (TanStack Query on `/api/v1/modules`); `App.tsx` renders `registry.routesFor(enabled)` in `<Suspense>`; `Layout.tsx` builds sidebar from core categories + `navFor(enabled)`; `<ModuleRoute>` guard for deep links.
- Diagrams canvas: Zustand (per-canvas store factory) owns ephemeral canvas state; TanStack Query owns server state; `@xyflow/react` + `zustand` land only in the diagrams chunk.
- Collab client: native `WebSocket` to `/api/v1/diagrams/{id}/ws` via short-lived WS ticket endpoint; JSON ops `{join|op|cursor|presence|sync}`; reconnect + snapshot resync.

### 6. K8s / Helm / Kustomize
- `values.yaml` gains `modules:` map (per-module `enabled`), `posthog:` (host + keys only — external shared instance), `minio:` (rendered only when diagrams enabled), `neo4j:` (only when aiSearch enabled).
- `templates/configmap-modules.yaml` renders `ELDER_MODULE_*` envs; api + worker use `envFrom`.
- Postgres default image → `pgvector/pgvector:pg16-*@sha256` (drop-in superset); extension created by ai_search migration guarded on Postgres dialect.
- Kustomize: `components/{minio,neo4j,posthog-env}` opt-in per overlay; base ConfigMap ships Elder-native modules on, absorbed modules off.

### 7. AI/Search module (`ai_search`)
- Capability probe at startup: pgvector present? Neo4j reachable? → `capabilities: {semantic, graph, rag}` surfaced via `/api/v1/modules`; missing capability → 501 `CAPABILITY_UNAVAILABLE`, UI hides features. Per-feature PostHog flags (`ai-semantic-search`, `ai-graph-neo4j`, `ai-rag`).
- Schema: polymorphic `ai_embeddings(tenant_id, source_module, source_table, source_id, chunk_index, content, content_hash, embedding vector(dim), meta)` + HNSW index; `AI_EMBEDDING_DIM` env (default 768 for EmbeddingGemma/Ollama).
- Pipeline: write paths call `enqueue_index()` → Redis Stream `elder:ai:index` → worker embeds (markdown-aware chunking ~800 tokens/100 overlap for KB/docs; rendered-card single chunk for entities/tickets/diagrams); periodic reconcile via `content_hash`; admin-triggered backfill.
- Providers: `EmbeddingProvider` protocol — `ollama` (default, Gemma models), `openai`-compatible, `waddleai` (enterprise); per-tenant `ai_configs`, deployment default via env.
- Neo4j projection: worker syncs entities/dependencies/services (+ ticket links) incrementally by `updated_at` watermark + periodic full reconcile with tombstones; `tenant_id` property on every node; server-side Cypher templates only (impact analysis, path-between, blast radius, N-hop) — never client-supplied Cypher. Neo4j-backed fast path replaces networkx in `apps/api/api/v1/graph.py` behind flag, networkx remains fallback.
- API: `/api/v1/ai/search`, `/api/v1/ai/graph/query`, `/api/v1/ai/rag/retrieve` (semantic + graph expansion + cited context blocks), `/api/v1/ai/config`, `/api/v1/ai/admin/reindex`.
- MCP: keep thin FastMCP-over-REST architecture in `apps/mcp`; add `tools/search.py` (semantic_search, rag_retrieve) + `tools/graph.py` (find_dependencies, impact_analysis, find_path); register AI tools only when module effective.

## Phases

| Phase | Scope | Risk | Est. |
|---|---|---|---|
| **0. Framework skeleton** | `modules/` package + registry, `_load_modules()`, `/api/v1/modules`, Helm `modules:` + ConfigMap, frontend registry + `useModules` + lazy routing. No file moves. | Low | 1–2 wk |
| **1. Modularize Elder** | Move `api/v1/*`, `models/*`, `services/*` into `modules/<name>/` per group (one PR per module); registry-driven `init_sqlalchemy_tables` + `alembic/env.py`; manifest-driven sidebar/routes; delete Go files. | Med | 2–3 wk |
| **2. Toggle layers** | `tenant_modules` + admin API/UI, request guard, license feature keys (**incl. per-seat enforcement + per-feature Enterprise SKUs**), PostHog server+client, Redis Streams job-bus core (streams, consumer groups, XAUTOCLAIM sweeper, DLQ, idempotency lib). | Low-Med | 1–2 wk |
| **3. Helpdesk module** | Re-author Ruffled models (`hd_`, int tenant FK, identities); port 18 blueprints; **SQLAlchemy-runtime→penguin-dal port** (SLA logic, dashboard aggregates = parity-test targets); email worker → task group on Streams; KB + chatbot (reuse ai_configs) flag-gated; frontend module. Parity vs Ruffled inventory checklist. | **High** | 4–6 wk |
| **4. Diagrams module** | **Flask→Quart port** of IceCharts diagramming backend (source: v1.1.x primary, main where more complete); storage abstraction (MinIO primary + S3/GCS/GDrive/OneDrive); React Flow + Zustand frontend module (port from `src/client/` tree — authoritative); then **Socket.IO→Quart WS + Redis pub/sub rewrite** (presence, shape locking, reconnect resync, multi-replica fanout; collab license-gated Pro+). Ship canvas first, collab flag-gated second. Parity vs IceCharts inventory checklist. | **High** | 4–6 wk |
| **4b. Automation modules** | Port from v1.1.x: **playbooks** (IceStreams engine + node catalog + 31 connectors + approval gates), **cicd_flows** (IceFlows pipelines, git ops, promotions, webhooks), **functions** (IceRuns FaaS — invoker as separate container, 7 runtimes, adapt existing k8s/iceruns manifests). Their workers already use Redis Streams — map onto the Elder job bus. Author missing SQLAlchemy schema (v1.1.x drift: iceflows_*/iceruns_* absent from sqlalchemy_schema.py). Unify ApprovalCenter logic across playbooks+cicd_flows. Fix known v1.1.x webui/api bugs against parity tests. | **High** | 6–9 wk |
| **4c. Third-party connector catalog** | Zapier/n8n-style connector expansion on the existing YAML-manifest + node_generator framework. Starter set: **Slack, Discord, MS Teams, Telegram, generic Email (SMTP), GitHub, GitLab, Jira, PagerDuty, Opsgenie, Twilio (SMS), Google Sheets/Drive, S3-compatible, generic Webhook/HTTP, OpenAI-compatible AI**. Each connector = YAML manifest (auth config, actions, triggers where feasible) auto-generating playbook nodes; matching diagram node icons/shapes so connectors are also usable as diagram elements. OAuth2/API-key credential storage via existing penguin-sal refs + the playbooks OAuth client subsystem. Finish the half-built connector config/test endpoints as part of this. | Med | 3–4 wk (starter set) |
| **5. AI/Search module** | 5a pgvector + pipeline + `/search`; 5b providers + tenant config; 5c Neo4j projection + graph endpoints; 5d RAG + MCP tools; 5e **AI relationship auto-recommend** (suggestion queue + approval UI). Each sub-step flag-gated. | Med-High | 4–6 wk |
| **6. Compliance module (Enterprise)** | `shared/crypto` KMS envelope encryption (AWS KMS/GCP KMS providers, `EncryptedString` type, key rotation strategy, Postgres-only); GDPR tooling (DSAR export, right-to-erasure, retention policies); audit export/SIEM; SCIM provisioning; Google Auth SSO (Pro) if not already in core SSO. Each a separate license SKU. | Med | 3–4 wk |
| **7. Hardening** | Module-combination test matrix, feature-parity signoff vs inventories, consolidated worker polish, Kustomize components for all overlays, docs, perf, version v4.0.0 release. | Low | 2 wk |

Riskiest items (all isolated in toggleable modules — ship dark, enable per-tenant): (1) Socket.IO→WS collab rewrite, (2) IceCharts Flask→Quart port, (3) Ruffled DAL port.

## Critical Files

- `elder/apps/api/main.py` — `_register_blueprints()` → registry-driven `_load_modules()`; before_request module guard
- `elder/shared/database/__init__.py` — `init_sqlalchemy_tables()` → manifest-driven model imports
- `elder/web/src/App.tsx`, `elder/web/src/components/Layout.tsx` — static routes/nav → module registry + `useModules`
- `elder/k8s/helm/elder/values.yaml` + new `templates/configmap-modules.yaml`
- `elder/apps/worker/main.py` — `WorkerService` → per-module task groups on Redis Streams
- `elder/apps/mcp/server.py` — AI tool registration
- Source material: `ruffledfeathers/services/api/` (models, blueprints, tests as parity contract), `icecharts/services/flask-backend/app/` (canvas domain, storage providers), `ruffledfeathers/services/api/models/ai_config.py`

## First Implementation Steps

1. Create `release/v4.0.X` branch in elder repo; bump `.version` to 4.0.0 (Elder 3.x is tagged).
2. Commit this spec to `elder/docs/design/2026-07-07-elder-platform-merge-design.md`.
3. Track phases as GitHub issues with milestone `v4.0.x` per devops standards.
4. Begin Phase 0.

## Verification

- **Per phase:** existing full suite (`make test`) stays green — it is the regression harness for module moves; new registry unit tests (dep resolution, topo-sort, prefix convention); integration fixture parametrizing `ELDER_MODULES_ENABLED` (all-on / all-off / each-alone) asserting 404 vs 200.
- **Toggles:** unit tests for 4-layer AND composition (license via `licensing_fallback`); tenant toggle CRUD + Redis cache-bust integration tests; Playwright sidebar show/hide per `/api/v1/modules` mock; PostHog stubbed in CI.
- **Helpdesk:** Ruffled's existing API tests ported as parity contract against the DAL implementation; SLA golden tests; email flow vs mailpit container.
- **Diagrams:** dual-websocket integration tests (op broadcast, presence, reconnect resync); two-instance Redis fanout test; Playwright two-context collab e2e; MinIO container tests.
- **AI/Search:** deterministic fake-provider pipeline tests; pgvector + Neo4j containers in CI compose; projection reconcile tests; degradation tests (no pgvector/Neo4j → capability false, 501, UI hidden); MCP in-process client tests.
- **Job bus:** kill-worker-mid-job test asserting XAUTOCLAIM reclaim + idempotent replay; DLQ after N failures.
- **Release gates:** `make smoke-test` pre-commit; 90%+ coverage; beta validation via internal LB before enabling any module in beta.
