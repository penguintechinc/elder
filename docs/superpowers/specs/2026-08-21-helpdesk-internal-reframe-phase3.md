# Elder Phase 3 — Internal Helpdesk Reframe (customer relations → Waddles)

**Status:** Draft design (2026-08-21). Companion to the Waddles handoff spec (`~/code/waddlebot/docs/specs/2026-08-21-customer-relations-from-elder.md`). Supersedes the original Phase 3 ("absorb all helpdesk into unified issues"): the **customer/public/community half goes to Waddles**; Elder keeps and reframes only the **internal** ticketing for employees/contractors.

**Goal:** Elder's helpdesk becomes an internal (authenticated employee) Jira/Zendesk-style ticketing surface; remove the customer-relations pieces (they move to Waddles); add an internal form builder that routes submissions to a ticket and/or a stream, plus basic Slack integration.

---

## What Elder KEEPS (internal)
- **Ticketing / issues** — the unified `issues` model (Jira-like). `issue_type='support'` reframes to **internal** helpdesk (employee IT/ops tickets), not customer support.
- **Internal form builder** — a Jira Service Desk / Zendesk-style page so employees (esp. non-technical) can cut a ticket. Built on the shared `@penguintechinc/react-form-builder`. Submissions route to **(A)** a ticket (create an issue) **and/or (B)** a stream (kick off a workflow in the `streams` module) — see "New: form→stream".
- **SLA, canned responses, attachments, comments** — for internal tickets. (`hd_sla_policies`, `hd_canned_responses`, `hd_ticket_attachments`→`issue_attachments`, `hd_ticket_messages`→issue comments.) SLA math / canned rendering come from the shared penguin-libs engine (see below).
- **Internal support teams** — `hd_teams`/`hd_team_members` (pending final call; assume internal-team = keep).

## What Elder REMOVES (→ Waddles)
- CRM: `hd_companies`, `hd_contacts` (+ `hd_tickets.requester_contact_id` FK).
- Public/customer intake: `HdTicketForm`/`hd_ticket_forms`, the public path of `IntakeForm`, `altcha`, captcha config, the `/public/<slug>` + public `/api/v1/intake/<slug>` anonymous routes.
- Customer email intake: `hd_email_accounts`, `hd_email_logs`, worker `helpdesk_email_poll`/`helpdesk_email_send`.
- Any `hd_` route/service scoped to customer relations (companies/contacts/email/public-forms/dashboard-customer-widgets).

## New capabilities
- **form→stream** — an internal intake form can, on submit, invoke a stream (workflow) in the `streams` module (in addition to / instead of creating an issue). The form config gains a target: `{creates_issue: bool, stream_id: int|null}`. Reuses the streams execution API.
- **Slack integration (basic)** — internal helpdesk over Slack: receive a ticket (Slack form / slash command → create issue), answer questions, and push updates (assignment/status/SLA) back to the employee via Slack. Behind a PostHog flag; Slack tokens via penguin-sal.

## Shared into penguin-libs (maximize — fix once, both inherit)
Per the Waddles spec's shared-library rule. Extract the *engine*, keep the *wiring* per product:
- `python-forms` (new) ← Elder `helpdesk/services/form_validation.py` (+ altcha/captcha verification — server-side check of the solution; used by Waddles public forms, available to Elder if internal forms ever need it).
- **`@penguintechinc/react-form-builder` — add an `altcha` (and/or generic `captcha`) FIELD TYPE.** It has none today: `FieldType = text|email|password|number|textarea|select|checkbox|radio|date|time|datetime-local|tel|url`. Adding it makes a captcha a first-class, drop-in field both the Elder internal builder and Waddles public builder render the same way (widget + solution), verified by `python-forms`. Elder's internal forms are authenticated so won't usually need it, but it belongs in the shared field-type set, not per-product.
- ticket/SLA primitives (status/priority, SLA-breach math, canned rendering) ← Elder `helpdesk/services/sla.py`.
- email-intake engine → Waddles-owned but library-extracted (`python-email`) so if Elder ever needs internal email it reuses it.

## Sequencing (important — removal is last)
1. **Specs agreed** (this doc + Waddles doc). ← we are here
2. **Extract shared engines to penguin-libs** (form validation, SLA/ticket primitives).
3. **Waddles builds** the customer-relations capability from its spec (consuming penguin-libs).
4. **Elder internal reframe** — internal form builder, form→stream, Slack; reframe `issue_type` semantics.
5. **Data migration** — any live customer companies/contacts/tickets/email → Waddles (per-tenant job).
6. **Elder removal PR** — delete the customer `hd_` tables + routes + worker tasks ONLY AFTER Waddles is live and data migrated. Removing earlier drops a capability customers depend on.

## Open items
- `hd_teams` internal-vs-customer final call.
- `issue_type='support'` rename/retag for internal (e.g. `helpdesk`) vs leaving `support` meaning internal.
- Whether Elder needs any read-only link to Waddles (probably not — clean separation).

## Out of scope
The Waddles implementation (its own spec); Phase 4 (flip the license-enforcement flag); Phase 5 (delete `/helpdesk` module shell, after all the above).
