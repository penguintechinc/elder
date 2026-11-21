# IceCharts — Feature Parity Checklist

Source: /home/penguin/code/icecharts. **CRITICAL: primary source = `origin/v1.1.x`** (contains full automation suite, never merged to main; "mostly stable, a few webui/api bugs"). Use main only where more complete. Files marked [v1.1.x] retrievable via `git show origin/v1.1.x:<path>`.

Target modules: `diagrams` (dg_), `playbooks` (playbook_*), `cicd_flows` (iceflows_*), `functions` (iceruns_*). ALL Free tier; AI-powered nodes (ask_ai, Darwin review) follow Pro AI gating; live collab Pro+.

## Module: diagrams (core IceCharts — from main + v1.1.x)
- [ ] Canvas editor: React-Flow wrapper, nodes/edges, connector routing (auto/straight/curved/orthogonal), zoom/pan/grid/snap, alignment, undo/redo, per-shape metadata
- [ ] Icon sets: AWS/Azure/IBM/Iconoir/internal with search
- [ ] Templates (blank/architecture/flowchart/org/ERD/network)
- [ ] Shape libraries (shape_libraries, library_shapes, shapes, shape_metadata) + Libraries page [v1.1.x]
- [ ] Drawings mgmt: CRUD, versions, shares; Drawings/DrawingDetail[v1.1.x]/SharedDrawing pages
- [ ] Collections: CRUD, items, shares; incl. DiagramCollections [v1.1.x]
- [ ] Real-time collab (Pro+): join/leave, cursors, drawing_change, shape lock/unlock (5-min auto-release), presence, attention_click, session cleanup → rewrite on Quart WS + Redis pub/sub
- [ ] Comments: threaded, shape-anchored, resolve/unresolve, @mentions, email notifications
- [ ] Export: PNG/SVG/PDF/JSON, DPI/size/quality, async via task queue → Streams job
- [ ] Sharing/public links: view/comment/edit tiers, share_analytics view tracking
- [ ] Storage providers: base interface + s3/gcs/minio/google_drive/onedrive; admin StorageConfiguration; StoragePickerDialog
- [ ] Elder integration: import entities as shapes w/ layout algorithms (hierarchical/force/grid/circular), dependency connectors, one-time vs live-sync → becomes internal module integration
- [ ] Dashboard + admin stats

## Module: playbooks (IceStreams — [v1.1.x])
- [ ] Node-graph editor (PlaybookEditor React-Flow), list/detail/templates/collections pages
- [ ] Tables: playbooks, nodes, edges, node_metadata, executions, node_executions, versions, templates, shares, schedules (cron), webhooks, forms + form_submissions, editor_locks, approval_gates, execution_approvals, custom_modules
- [ ] API: CRUD, duplicate, editor lock, execute, executions, node metadata, webhooks; approvals (my-approvals, approve/reject, gates); inbound hooks /<token> all verbs + test
- [ ] Executor: topological sort, inter-node data flow, conditional branching, error handling (icestreams-worker → Elder worker task group on Streams)
- [ ] Node catalog — actions: http_request, grpc_call, webhook_out, mcp_call, log, aws_lambda, gcp_cloudrun, openwhisk; conditionals: if_then, switch, for_each, while_loop, logic_gates, comparisons; transforms: code, expression, filter, json_transform, merge, split, delay, ask_ai (AI-gated)
- [ ] Connectors framework: registry + node_generator from YAML manifests; 31 manifests (13 PenguinTech products incl. elder, waddleai, darwin + 18 DB connectors)
- [ ] OAuth2/OIDC clients for authenticated connector calls
- [ ] Node config panels (AskAI, Conditional, MCP, Connector), AnimatedFlowEdge
- [ ] ApprovalCenter (unified with IceFlows — dedupe approval logic during port)

## Module: cicd_flows (IceFlows — [v1.1.x])
- [ ] Tables: iceflows, stages (ordered), stage_approvers, stage_tests, stage_calls, stage_reviews, credentials, promotions, approvals, executions + execution_steps, webhooks, darwin_config, notifications + notification_log
- [ ] API: flows CRUD + enable/disable/duplicate + stages CRUD/reorder + export/yaml; stage sub-resources (approvers/tests/calls/reviews); promotions (create, merge, approve/reject/override, my-approvals, status); credentials CRUD + test; inbound GitHub/GitLab webhooks
- [ ] Worker (iceflows-worker → Elder worker task group): PipelineExecutor (stages: tests → git ops → external calls), git clone/branch/merge, Darwin AI reviewer (ReviewIssue: error/warning/style/security/optimization; AI-gated), call_handler, test_runner
- [ ] Notification service
- [ ] UI: IceFlowsList/Editor/Detail/Promotions/MyApprovals

## Module: functions (IceRuns — [v1.1.x])
- [ ] Tables: iceruns, versions, executions, schedules (cron)
- [ ] API: function CRUD, package upload/get/delete, activate/pause/archive, webhook regenerate, versions, config, secrets; executions (execute, logs, output, status, retry, artifacts, stats); hooks /hook/<token> + config/test
- [ ] Invoker service (SEPARATE container like scanner — container pools need own deployment): invoker.py (Redis Streams queue), container_pool (warm reuse, TTL), action_runtime, metrics
- [ ] 7 runtimes: Python 3.13, Node 20, Go 1.23, Ruby 3.3, Bash 5.2, PowerShell 7.4, Rust 1.75 (runtime modules + Dockerfiles + action-server harnesses)
- [ ] K8s: iceruns invoker deployment/HPA/PDB/NetworkPolicy/ServiceMonitor (adapt from k8s/iceruns/)
- [ ] UI: List/Create/Edit/Detail/Executions/ExecutionDetail/Schedules/Test; CronBuilder, ExecutionStatus, LogViewer, PackageUpload, RuntimeSelector, WebhookConfig
- [ ] iceruns_nodes.py bridge (call functions from playbooks)

## Shared/platform (→ core or existing modules)
- [ ] Auth/SSO/OAuth: SAML+OIDC (idp_configurations), Google OAuth, email verification → Elder core SSO (Google OAuth → Pro tier feature)
- [ ] Groups/teams → Elder identities/groups
- [ ] Service accounts: long-lived scoped JWTs, rate limits → Elder api_keys/service accounts (merge concepts)
- [ ] Admin: settings (system_settings), stats, license settings [v1.1.x], activity_logs + audit_logs [v1.1.x] → Elder core admin/audit
- [ ] database_ops API [v1.1.x] (generic multi-DB query/insert/update/delete/procedure/schema) — ⚠ powerful surface, review auth scoping; needed by DB connectors
- [ ] Multi-tenancy (tenants) → Elder tenants
- [ ] Health/metrics → Elder patterns

## ⚠ Flags / fix-during-port
1. v1.1.x schema drift: iceflows_*/iceruns_* tables in pydal_models.py but MISSING from sqlalchemy_schema.py — author proper SQLAlchemy schema + Alembic in Elder.
2. Duplicate frontend trees: src/ (stale) vs src/client/ (authoritative) — port from src/client/.
3. Connectors config/test endpoints commented out (half-built) — finish during port.
4. Darwin reviewer + WaddleAI/MCP nodes depend on external services — graceful degradation + AI gating.
5. Unify ApprovalCenter approval logic across playbooks + cicd_flows.
6. "A few webui/api bugs" on v1.1.x per user — expect fixes during port; parity tests define correct behavior.
7. docs/WORKFLOWS.md = GitHub Actions CI docs, not a product feature.
