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
| DB stack | **PostgreSQL (pgvector image) + Neo4j + Redis ONLY** — explicit user override of the usual multi-DB support matrix: no MySQL/MariaDB/SQLite support. Postgres-only Alembic (free use of JSONB/arrays/recursive CTEs/vector), Neo4j is a standard required dependency (networkx path kept only as runtime fail-soft, not a support tier). SQLAlchemy + Alembic = schema authority; **penguin-dal = runtime** (Ruffled's SQLAlchemy-runtime code gets ported) |
| AuthZ model | **penguin-aaa with OIDC scopes for EVERYTHING** — every module and feature/access level is a scope (`<module>:<action>`, e.g. `helpdesk:read`, `streams:execute`, `documents:admin`); module manifests declare their scopes; middleware checks scopes only, never role names. **Enterprise custom roles = tenant-defined scope bundles** (admin UI to compose roles from the scope catalog), expanded to scopes at token issuance |
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
| Code standards | **All code files under 1000 lines** — split larger files into focused modules (enforce via lint check in CI; applies to ported code too: oversized Ruffled/IceCharts files get split during their port) |
| Observability | **OpenTelemetry for ALL logging AND metrics** (and traces) — single OTel pipeline, no separate Prometheus client / ad-hoc logging. **Free tier / ungated** (observability is never license-gated). Export to self-hosted **SigNoz** (OTel-native; `penguin-signoz` repo) via OTLP. Backend: OTel SDK + auto-instrumentation for Quart/httpx/redis/psycopg + `structlog`→OTel logs bridge (structlog stays the dev-facing API, OTel is the emission/export layer); replace the existing Prometheus `/metrics` scrape with OTel metrics (OTLP push, or OTel Prometheus exporter only if a scrape endpoint is still needed). Frontend: optional OTel browser SDK. Worker + scanner + runs-invoker all emit via the same OTLP config (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME` per service). |

## Product Surfaces (user-facing naming)

Eight surfaces under one roof, all cross-referenceable:

| Surface | Module | Source |
|---|---|---|
| **Entities & Resources** | `infrastructure` (+ ipam, sbom, etc.) | Elder CMDB |
| **Documents** | `documents` (split out of helpdesk) | Ruffled KB + full roadmap |
| **Pages** | `pages` (NEW — WYSIWYG block editor) | Ruffled Plan 9 design |
| **Diagrams** | `diagrams` | IceCharts canvas |
| **Streams** (workflows) | `streams` (was "playbooks") | IceCharts IceStreams v1.1.x |
| **Flows** (CI/CD) | `flows` (was "cicd_flows") | IceCharts IceFlows v1.1.x |
| **Runs** (serverless) | `runs` (was "functions") | IceCharts IceRuns v1.1.x |
| **Helpdesk** | `helpdesk` | Ruffled tickets/SLA/email/CRM |

## Cross-Reference Layer (core service — key differentiator)

Everything can reference everything. **Full depth approved: refs + live embeds + automation actions.**

- **Stable IDs = village_id** (already built): Elder's hierarchical 64-bit hex ID (`TTTT-OOOO-IIIIIIII`, `shared/utils/village_id.py`, unique+indexed, same lib already used by IceCharts). Extend the `village_id` column to ALL referenceable module tables (documents, pages, drawings, playbooks/streams, flows, runs, tickets). Generalize the existing resolver `apps/api/api/v1/lookup_village_id.py` (`GET /id/<village_id>` + `RESOURCE_URL_MAP`) from table-scan to a registry-driven resolver; add `GET /api/v1/refs/resolve` returning `{type, id, village_id, title, url, preview, broken}` (permission-checked).
- **Reference registry (core table):** `references(id, tenant_id, source_module, source_type, source_id, target_module, target_type, target_id, ref_type, context JSONB, created_by, timestamps)` — generalizes Ruffled's designed `doc_links(source_id, target_id)` edge table and Elder's typed `IssueEntityLink(link_type)` pattern. Written on: wiki-link parse (rebuild-on-save, per Ruffled Plan 5), diagram shape↔entity bind, stream node binding, page embed block insert, issue↔entity links (migrate existing pattern in). Backlinks = indexed reverse query: `GET /api/v1/refs/backlinks?target=<module:type:id>`.
- **Wiki-link syntax generalized** (from Ruffled Plan 5's `[[slug]]`): `[[entity:web-01]]`, `[[doc:runbook-db]]`, `[[diagram:prod-arch]]`, `[[page:onboarding]]`, bare `[[slug]]` defaults to document. Regex parse on save → rebuild refs (delete+reinsert outbound, ON CONFLICT DO NOTHING); render via MarkdownRenderer preprocess (Ruffled pattern); autocomplete via cross-module search. Non-blocking on failure.
- **Live embeds:** Pages/Documents render entity cards, diagram previews (SVG snapshot), document excerpts, stream-run status chips via the resolver `preview` payload. Diagram shapes get proper registry-backed entity references (upgrading IceCharts' current import-only approach, where `elder_id` lives buried in node `data` JSON — a correction from exploration: today's integration is one-time import, NOT live-sync; the registry + optional refresh job makes it live).
- **Automation actions:** Streams get first-party "internal connector" node sets per module, auto-generated from YAML manifests using the SAME schema as the existing external connectors (v1.1.x `elder.yaml` proves the pattern: `triggers` (webhook/event nodes), `actions` (endpoint+method+config_schema with `{{var}}` templating), `transforms` (lookups)). Module event triggers consume `elder:events:*` Redis Streams. Runs functions get a scoped SDK/token to call module APIs.
- **Deletion semantics:** soft integrity — deleting a target leaves dangling refs flagged (`broken=true` on resolve), surfaced in backlink panels; never cascade-deletes source content.
- **ai_search synergy:** reference edges project into Neo4j alongside dependencies and feed RAG graph-expansion (retrieve a document → pull referenced entities' context). Note: Ruffled's umbrella spec already designed Documents/Pages on pgvector (768-dim) + Neo4j — dimensions align with EmbeddingGemma default.

### Documents module design (grounded in Ruffled Plans 2/5/6 — reuse directly)
- Collections: `doc_collections` self-ref `parent_id` tree, recursive CTE traversal, `document_collections` join (CASCADE), visibility gates, 409-on-nonempty-delete; CollectionsTree UI.
- Versioning: `doc_versions` immutable snapshots on publish (`version_number = MAX+1`), restore-creates-new-version, unified-diff UI (`diff` npm lib).
- Wiki-links: via core reference registry (above) instead of doc-only `doc_links`.
- AI writing assist (AI-gated, Plan 7 design): slash commands, draft generation, SSE streaming, related-docs context.

### Pages module design (grounded in Ruffled Plan 9 — reuse directly)
- Editor: **TipTap 2.x** (@tiptap/react + starter-kit + image/table/code-lowlight/task-list extensions), slash-command BlockMenu + BlockToolbar; `body_html` authoritative.
- Security: mandatory bleach ≥6 allow-list sanitization on every create/update.
- Schema: `pg_pages` (slug, title, body_html, status, visibility + roles/users, is_public, embedding vector(768), village_id) + `pg_page_collections` (shares the Collections system with Documents).
- NEW beyond Plan 9: **reference-embed blocks** (TipTap custom nodes) rendering live entity cards/diagram previews/doc excerpts/run status via the resolver.

## Module Taxonomy

**Always-on core:** identity/IAM, tenants/orgs, auth (JWT/OIDC/SAML via penguin-aaa), RBAC, admin console, licensing, audit, settings, webhooks framework, `/api/v1/modules` endpoint, **cross-reference registry + resolver**.

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
| `helpdesk` | Ruffled | Tickets, SLA, canned responses, email accounts, forms/CAPTCHA, companies/contacts (KB moves to `documents`) | `hd_` |
| `documents` | Ruffled (KB + full roadmap) | Documents/KB: markdown editor, publish workflow, visibility ACL, public docs, **Collections (hierarchical), [[wiki-links]] generalized to cross-module refs + backlinks, version history/diff/restore, AI writing assist (AI-gated)**. Complete the half-finished KB→Documents rename properly. Free | `doc_` |
| `pages` | NEW (Ruffled Plan 9 design) | WYSIWYG block-editor pages: text/heading/media blocks + **reference-embed blocks** (entity cards, diagram previews, document excerpts, stream-run status), templates, sharing. Free | `pg_` |
| `diagrams` | IceCharts (main+v1.1.x) | Canvas (React Flow), shapes/connectors, icon sets, shape libraries, drawings/versions, collections, sharing/public links + analytics, comments (@mentions), export PNG/SVG/PDF/JSON, storage providers (MinIO/S3/GCS/GDrive/OneDrive), templates, entity-bound shapes (generalized Elder import); real-time collab sub-feature (Pro+) | `dg_` |
| `streams` | IceCharts v1.1.x (IceStreams) | **Workflow engine**: node-graph editor, topological executor (conditionals/loops/transforms), action nodes (HTTP/gRPC/MCP/cloud), 31 connectors (13 PenguinTech + 18 DBs) + third-party connector catalog (Slack, Discord, Teams, Telegram, GitHub/GitLab, Jira, PagerDuty, Twilio, Google Sheets/Drive, S3, …) usable as both stream nodes and diagram nodes, **+ first-party internal connectors for all modules (query/create/update entities, docs, pages, diagrams, tickets; event triggers from elder:events:*)**, schedules/webhooks/forms triggers, approval gates. Free; `ask_ai` node AI-gated | `playbook_` (existing) |
| `flows` | IceCharts v1.1.x (IceFlows) | **CI/CD pipelines**: git-bound stage pipelines, approvals, stage tests/calls, Darwin AI review (AI-gated), promotions/merges, GitHub/GitLab webhooks, credentials. Free | `iceflows_` (existing) |
| `runs` | IceCharts v1.1.x (IceRuns) | **Serverless FaaS**: functions in 7 runtimes, warm container pools, webhook/cron/manual triggers, execution logs/artifacts, **scoped SDK/token for calling module APIs from functions**. Free. Invoker = separate container (like scanner) | `iceruns_` (existing) |
| `ai_search` | New | pgvector semantic search, Neo4j graph queries, embedding pipeline, RAG API, MCP tools, AI assistant, **AI auto-recommend entity/resource relationships** (suggests dependencies via embedding similarity + graph heuristics, human-approved) | `ai_` |
| `access_reviews` | Elder | Access reviews, resource roles (Enterprise per-feature SKU) | (existing) |
| `compliance` | New (Ent.) | **KMS field encryption** (AWS KMS/GCP KMS envelope encryption via `shared/crypto` + `EncryptedString` SQLAlchemy type, Postgres-only), GDPR tooling (DSAR export, right-to-erasure, retention policies), audit export/SIEM, SCIM provisioning | `cp_` |

**Feature-parity requirement:** exhaustive per-product inventories are committed at `elder/docs/design/inventories/{elder,ruffled,icecharts}.md`; every inventoried feature maps to a module + phase or is explicitly marked deferred with user sign-off (Phase 7 gate).

## License Tier Matrix (APPROVED)

| | **Free** | **Professional** (per-seat) | **Enterprise** (per-feature + per-seat) |
|---|---|---|---|
| **Target** | Early startups, non-profits, home labs | Established companies | SSO / HIPAA / GDPR-bound orgs |
| **Features** | Entities/Resources (CMDB + relationship mapping), IPAM, issues/projects, services/on-call, **Documents** (incl. collections, wiki-links, versioning), **Pages** (WYSIWYG + reference embeds), helpdesk (tickets/SLA/email), **Diagrams**, **Streams** (workflows), **Flows** (CI/CD), **Runs** (serverless), cross-referencing everywhere — AI-powered features (ask_ai, Darwin review, writing assist) follow Pro AI gating — local auth + MFA | + MCP server access, Google Auth SSO, AI doc search/generation, AI auto-recommend entity/resource relationships, live collaboration, SBOM/vuln scanning, basic discovery (K8s/network), advanced analytics/reporting, webhook integrations (Slack/PagerDuty) | + OAuth2/OIDC/SAML2 SSO with custom roles, AWS/GCP KMS field encryption (sensitive Postgres data), access reviews, audit export/SIEM + extended retention, SCIM provisioning, IdP discovery sync (Okta/AD/Google Workspace), GDPR tooling (DSAR export, right-to-erasure, retention policies), WaddleAI provider |

**Licensing enforcement:**
- Professional: **per-seat** — penguin-licensing seat-count entitlement, checked against active (non-disabled) identities per tenant; grace behavior on overage (warn, then block new seats — never lock out existing users).
- Enterprise: **per-feature + per-seat** — each Enterprise feature is its own license feature key/SKU (`sso-oidc-saml`, `kms-encryption`, `access-reviews`, `siem-export`, `scim`, `idp-sync`, `gdpr-tooling`, `waddleai`), mapping 1:1 to module manifest `license_feature` / PostHog flag keys.
- Free requires no license key; graceful degradation everywhere per house rules.

## Technical Design

### 1. Backend module framework
- Current seam: `apps/api/main.py:_register_blueprints()` hardcodes ~45 blueprint imports; `shared/database/__init__.py:init_sqlalchemy_tables()` hardcodes model imports.
- New `apps/api/modules/` package: explicit `MODULES` list of frozen `ModuleManifest` dataclasses (`name, title, license_feature, depends_on, blueprints (lazy zero-arg callable), models, table_prefix, worker_tasks, nav_id, optional_services, default_enabled`) + `registry.py` (env parsing, dep validation, topo-sort, blueprint mounting, `app.extensions["elder_modules"]` state + blueprint→module reverse map).
- **Phase 0 trick:** module `blueprints.py` initially re-exports existing `apps/api/api/v1/*` blueprints in place — zero file moves; physical moves happen per-module later.

### 1b. Scope-based authorization (penguin-aaa)
- Module manifests gain `scopes: tuple[str, ...]` — the scope catalog is generated from manifests (e.g. helpdesk → `helpdesk:read`, `helpdesk:write`, `helpdesk:admin`; feature-level scopes where finer: `streams:execute`, `flows:approve`, `runs:invoke`, `documents:publish`, `refs:write`).
- Every endpoint declares required scope(s) via penguin-aaa decorators; the module request guard checks module effective-enablement AND the caller's module scope. No role-name branching anywhere.
- Built-in roles (Admin/Maintainer/Viewer + team roles) = predefined scope bundles. **Enterprise custom roles**: `custom_roles(tenant_id, name, scopes JSONB)` core table + admin UI composing roles from the scope catalog; auth service expands role→scopes at token issuance (roles claim stays audit-only per house rules).
- Ported code (Ruffled `require_scope`, IceCharts custom auth) converges on penguin-aaa scope middleware during each module's port.

### 2. Toggle resolution (strict AND, cheapest-first)
- Layer 1 startup: `ELDER_MODULE_<NAME>` env (Helm ConfigMap) → unmounted = 404.
- Layer 2 license: penguin-licensing feature key per manifest; guarded 403 `MODULE_UNLICENSED`.
- Layer 3 tenant: new core table `tenant_modules(tenant_id, module_name, enabled, settings JSONB)`, Redis-cached (`elder:modtoggle:{tenant_id}`, TTL 60s, busted on write); admin API `GET/PUT /api/v1/tenants/{id}/modules`; 403 `MODULE_DISABLED`.
- Layer 4 PostHog: `posthog` python lib with **local evaluation** (no per-request network); helper `flag_enabled(key, default)` degrades to default when PostHog down. Client: `posthog-js` + `useFeatureFlagEnabled`. Module visibility does NOT use PostHog — single source of truth is `GET /api/v1/modules` (returns per-module `installed/licensed/tenant_enabled/effective/capabilities` + flag snapshot).
- One `before_request` hook resolves `request.blueprint` → module and enforces layers 2–3.

### 3. Database
- **PostgreSQL-only** (pgvector image): drop DB_TYPE multi-DB switching from config; Alembic migrations may freely use JSONB, ARRAY, recursive CTEs, and `vector` (pgvector extension created unconditionally in the baseline migration). Neo4j + Redis are standard stack components.
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
- Postgres image = `pgvector/pgvector:pg16-*@sha256` (the only supported DB); `vector` extension created unconditionally by baseline migration. Neo4j rendered when aiSearch enabled (default on in full deployments); Redis always. No alternative-DB deployment paths.
- Kustomize: `components/{minio,neo4j,posthog-env}` opt-in per overlay; base ConfigMap ships Elder-native modules on, absorbed modules off.

### 7. AI/Search module (`ai_search`)
- pgvector + Neo4j are guaranteed by the standard stack (no multi-DB support paths). Startup probe remains only as runtime fail-soft: if Neo4j is temporarily unreachable → `capabilities.graph=false`, graph endpoints 503, networkx fallback path serves basic graph reads; recovery is automatic. Per-feature PostHog flags (`ai-semantic-search`, `ai-graph-neo4j`, `ai-rag`).
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
| **0.5. Observability (OTel)** | Wire OpenTelemetry SDK + OTLP export across api/worker/scanner (and runs-invoker later); auto-instrument Quart/redis/psycopg/httpx; bridge `structlog`→OTel logs; migrate Prometheus metrics → OTel metrics; Helm `otel:` values (`OTEL_EXPORTER_OTLP_ENDPOINT`, service names) → SigNoz; ungated/Free. Frontend OTel browser SDK optional. | Low-Med | 1 wk |
| **1. Modularize Elder** | Move `api/v1/*`, `models/*`, `services/*` into `modules/<name>/` per group (one PR per module); registry-driven `init_sqlalchemy_tables` + `alembic/env.py`; manifest-driven sidebar/routes; delete Go files. | Med | 2–3 wk |
| **2. Toggle layers + cross-ref core** | `tenant_modules` + admin API/UI, request guard, license feature keys (**incl. per-seat enforcement + per-feature Enterprise SKUs**), PostHog server+client, Redis Streams job-bus core (streams, consumer groups, XAUTOCLAIM sweeper, DLQ, idempotency lib). **Cross-reference core: `references` registry table, generalized village_id resolver, `/api/v1/refs/{resolve,backlinks}`, wiki-link parser service, frontend embed-card components.** | Med | 2–3 wk |
| **3. Helpdesk module** | Re-author Ruffled models (`hd_`, int tenant FK, identities); port 18 blueprints; **SQLAlchemy-runtime→penguin-dal port** (SLA logic, dashboard aggregates = parity-test targets); email worker → task group on Streams; chatbot (reuse ai_configs) flag-gated; frontend module. Parity vs Ruffled inventory checklist. | **High** | 4–6 wk |
| **3b. Documents + Pages modules** | `documents`: port Ruffled KB (completing the KB→Documents rename correctly) + build the designed roadmap — Collections (tree + CTE), versioning (snapshots/diff/restore), wiki-links via reference registry, AI writing assist (AI-gated). `pages`: greenfield per Plan 9 — TipTap editor, bleach sanitization, `pg_pages` + shared Collections, **reference-embed blocks** (entity cards, diagram previews, doc excerpts, run status). | Med-High | 4–5 wk |
| **4. Diagrams module** | **Flask→Quart port** of IceCharts diagramming backend (source: v1.1.x primary, main where more complete); storage abstraction (MinIO primary + S3/GCS/GDrive/OneDrive); React Flow + Zustand frontend module (port from `src/client/` tree — authoritative); then **Socket.IO→Quart WS + Redis pub/sub rewrite** (presence, shape locking, reconnect resync, multi-replica fanout; collab license-gated Pro+). Ship canvas first, collab flag-gated second. Parity vs IceCharts inventory checklist. | **High** | 4–6 wk |
| **4b. Automation modules (Streams/Flows/Runs)** | Port from v1.1.x: **streams** (IceStreams engine + node catalog + 31 connectors + approval gates **+ first-party internal-connector manifests for every module** using the elder.yaml pattern + event triggers from elder:events:*), **flows** (IceFlows pipelines, git ops, promotions, webhooks), **runs** (IceRuns FaaS — invoker as separate container, 7 runtimes, adapt existing k8s/iceruns manifests, scoped module-API SDK for functions). Their workers already use Redis Streams — map onto the Elder job bus. Author missing SQLAlchemy schema (v1.1.x drift: iceflows_*/iceruns_* absent from sqlalchemy_schema.py). Unify ApprovalCenter logic across streams+flows. Fix known v1.1.x webui/api bugs against parity tests. | **High** | 6–9 wk |
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

## Current Status (as of this planning round)

**Already done (on `release/v4.0.X` in elder):**
- Branch created from main + `feat/wire-penguin-aaa` merged in (Quart completion + penguin-aaa) — commit d1340b2
- Design spec + 3 parity inventories committed to `docs/design/` — commit c5c4401 (**needs re-commit with this round's updates: naming, Documents/Pages, cross-ref layer, revised phases**)
- Version bumped to 4.0.0 — commit beb8e5b
- GitHub issues #163–#172 created on milestone v4.0.x (**#166 needs edit: KB moves to new documents module; new issues needed for Phase 3b Documents+Pages and cross-ref core addition to #165**)

**Phase 0 in flight (on `feature/module-framework` branch):**
- Backend agent: `apps/api/modules/registry.py` created; remaining — modules/__init__.py manifests, main.py refactor, /api/v1/modules endpoint, shared/events.py, tests
- K8s agent: Helm values.yaml `modules:` block + `_helpers.tpl` upperSnakeCase + `configmap-modules.yaml` written (absorbed-module keys need rename to streams/flows/runs/documents/pages); remaining — deployment envFrom (shared templated Deployment, scope to api/worker components), Kustomize base ConfigMap + patches, helm lint/kustomize validation
- Frontend agent: **COMPLETE** — `web/src/modules/{types,registry}.ts` + 9 module manifests (lazy routes + nav), `useModules` hook, App.tsx/Layout.tsx wired, `getModules()` API method; Vite build + ESLint pass, zero visual change with all modules enabled

**Next steps on resume:**
1. Update the committed spec in `docs/design/` with this round's additions; amend issues (#165, #166) + create Phase 3b issue.
2. Let the three Phase 0 agents finish; verify (pytest registry tests, app import, npm build, helm lint, kustomize build); run `make lint` + smoke checks.
3. Merge `feature/module-framework` → `release/v4.0.X` when green (ask before committing further work).
4. Proceed per phases.

## Verification

- **Per phase:** existing full suite (`make test`) stays green — it is the regression harness for module moves; new registry unit tests (dep resolution, topo-sort, prefix convention); integration fixture parametrizing `ELDER_MODULES_ENABLED` (all-on / all-off / each-alone) asserting 404 vs 200.
- **Toggles:** unit tests for 4-layer AND composition (license via `licensing_fallback`); tenant toggle CRUD + Redis cache-bust integration tests; Playwright sidebar show/hide per `/api/v1/modules` mock; PostHog stubbed in CI.
- **Helpdesk:** Ruffled's existing API tests ported as parity contract against the DAL implementation; SLA golden tests; email flow vs mailpit container.
- **Diagrams:** dual-websocket integration tests (op broadcast, presence, reconnect resync); two-instance Redis fanout test; Playwright two-context collab e2e; MinIO container tests.
- **AI/Search:** deterministic fake-provider pipeline tests; pgvector + Neo4j containers in CI compose; projection reconcile tests; degradation tests (no pgvector/Neo4j → capability false, 501, UI hidden); MCP in-process client tests.
- **Job bus:** kill-worker-mid-job test asserting XAUTOCLAIM reclaim + idempotent replay; DLQ after N failures.
- **Release gates:** `make smoke-test` pre-commit; 90%+ coverage; beta validation via internal LB before enabling any module in beta.
