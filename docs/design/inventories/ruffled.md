# Ruffled (Helpdesk) — Feature Parity Checklist

> **SUPERSEDED**: this checklist tracked pre-unification feature parity
> against a standalone `helpdesk` ticket/CRM module. Tickets and CRM have
> since been unified into Elder's native Issue model — a support ticket is
> now an Issue with `issue_type=support` and a polymorphic
> `assignee_type`/`assignee_id` (identity or org unit); CRM companies and
> contacts are `customer_company`/`customer_contact` organization/identity
> types, not separate tables. Public ticket intake is now the
> `/api/v1/intake-forms` + `/api/v1/intake/<slug>` flow (Altcha captcha).
> Left in place as historical record of the original migration scope —
> see `docs/RELEASE_NOTES.md` ("Issues ↔ Tickets Unification") for the
> current model.

Source: /home/penguin/code/ruffledfeathers. Target module: `helpdesk` (+ KB → shared documents story, AI → `ai_search`).

## API surface (services/api/api/v1/)
- [ ] status — health (unauth)
- [ ] auth — login/logout/refresh (rotation), /me, change/forgot/reset password, JWT+OIDC claims
- [ ] tickets — CRUD, filter/paginate, assign, merge (tickets:admin); status/priority/channel/tags/category/SLA fields
- [ ] messages — ticket messages: reply/note/internal, email Message-ID threading
- [ ] users — CRUD + deactivate (soft delete) → maps to Elder identities
- [ ] teams — CRUD + members with roles
- [ ] email_accounts — CRUD + test connection; SMTP/IMAP/Gmail config
- [ ] sla_policies — CRUD per-priority
- [ ] canned_responses — CRUD, category, shared flag
- [ ] dashboard — ticket metrics by status/priority (analytics:read)
- [ ] ticket_forms — CRUD custom forms; PUBLIC form fetch + submit (unauth, CAPTCHA)
- [ ] kb_articles — CRUD, slug URLs, publish workflow, public endpoints, visibility ACL (public/authenticated/role/user), ILIKE search, category/tags
- [ ] ai — config CRUD, chat (KB-context + conversation history + sources), suggest reply for agents
- [ ] companies / contacts — CRM CRUD, contact timeline
- [ ] license — tier/features/limits endpoint

## Frontend pages
- [ ] Landing, Login, Dashboard
- [ ] Ticket list / new / detail (replies, notes, assignment, SLA indicator, badges)
- [ ] KB browse/search, article view, markdown editor (@uiw/react-md-editor), public KB pages
- [ ] AI chat page (ChatBot components) + AiSuggestButton
- [ ] Settings (tabs: Email Accounts, SLA, Teams, Forms, AI)
- [ ] User management, Profile (password change)
- [ ] Canned responses
- [ ] Company/Contact list + detail (timeline)
- [ ] FormDesigner + FormRenderer + CaptchaWidget (Turnstile/reCAPTCHA)

## Models → `hd_` tables (identity unification per spec)
users→identities; tenants→Elder tenants; tenant_users→RBAC; tickets, ticket_messages, ticket_attachments, teams, team_members, email_accounts, email_logs, documents (KB), sla_policies, canned_responses, ticket_forms, ai_configs→ai_ module, ai_conversations→ai_ module, audit_logs→core audit, companies, contacts

## Email worker → `cg:helpdesk:email` task group
- [ ] Per-account polling loops: IMAP4_SSL UNSEEN fetch + Gmail API poll
- [ ] Email parser (Message-ID/In-Reply-To/References, attachments)
- [ ] Email→ticket threading (find by In-Reply-To else create; auto-create customer)
- [ ] Redis dedup + distributed poll locks (maps directly onto Streams idempotency design)
- [ ] Outbound: send_ticket_notification, send_password_reset (penguin-email)

## Services logic to port with parity tests
- [ ] sla_service.calculate_breach_time (business-hours M–F 9–5 UTC)
- [ ] captcha_service (Turnstile + reCAPTCHA siteverify)
- [ ] ticket_service lifecycle (first_response_at/resolved_at/closed_at, merge)
- [ ] ai_service providers (OpenAI + Ollama) → ai_search provider abstraction

## ⚠ Known-broken / half-built (fix during port, do NOT copy bugs)
1. KB→Documents rename incomplete: `KbArticle` imports broken in kb_articles.py + ai_service.py; Document model unused. Port lands directly on the renamed `documents` design.
2. SLA apply_sla_policy / check_sla_breaches are TODO stubs — implement properly as Streams-scheduled breach-checker task.
3. License validation mocked (has_tier/has_feature unimplemented) — replaced by Elder penguin-licensing integration.
4. Advertised-but-unimplemented: SSO, WaddleAI, advanced analytics, webhooks — covered by Elder core / new tier plan.
5. MFA schema-only (mfa_secret, no endpoints) — Elder core MFA (pyotp) covers this.
6. AuditLog model unwired — Elder core audit covers this.

## Design-only roadmap (docs/superpowers/plans 2–9) — absorbed by merged-product plan
Collections (hierarchical), Neo4j search, pgvector semantic, wiki-links [[slug]] + backlinks, doc versioning/diff/restore, AI writing assist (slash commands, SSE), MCP server, Pages WYSIWYG. → Fold into `ai_search` + documents module scope; mark which phase each lands in or explicitly defer.
