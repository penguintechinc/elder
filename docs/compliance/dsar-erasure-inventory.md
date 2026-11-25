# DSAR Erasure & Export Inventory

Source of truth: `apps/api/services/privacy/inventory.py` (this page mirrors it; `tests/unit/test_privacy_inventory.py` fails when a model gains a foreign key to `identities` or `portal_users` that is not inventoried). Advisory engineering record, not legal advice — DPO/counsel own the legal-basis wording.

## Availability

- Export, erasure, Do-Not-Sell/Share opt-out and consent withdrawal are **always on**: every license tier including Free, no feature flag, no dependency on a reachable flag or license server.
- Only the admin convenience layer (`/api/v1/privacy-admin/*`: bulk erase, request list) is Enterprise.
- Endpoints (authenticated subject only, tenant from the JWT): `GET /api/v1/privacy/me/export`, `POST /api/v1/privacy/me/erase`, `GET|POST /api/v1/privacy/me/consent`, `POST /api/v1/privacy/me/consent/withdraw`.
- Subjects: `identities` rows and `portal_users` rows. The two id spaces overlap; the subject type always comes from the authenticated principal.

## Erasure Safeguards

These pace or defer an irreversible action; none of them gates the right by tier, flag or server reachability.

| Safeguard | Behaviour | Why it is not an obstacle |
|---|---|---|
| Confirmation | `POST /me/erase` needs `{"confirm": "ERASE"}`; otherwise 403 naming the exact body to send | One extra field |
| Re-authentication | Current password in `password`, or an authenticator code in `mfa_code` when MFA is enrolled — one factor. Portal users use their own credential | Same effort as signing in |
| Subjects with no local credential (SSO/SCIM) | No password is demanded; the token's `auth_time` must be at most 5 minutes old (sign in again, then erase) | A password they do not have is never required |
| Last tenant administrator | 409 while the subject is the tenant's only active admin (identity `portal_role` admin or superuser, or portal user with an admin role) | Deferred, not denied: proceeds as soon as another active admin exists |
| Rate limit (per subject, per minute) | export 10, erase 5, consent 30, withdraw-all 10; 429 with `Retry-After`. The limiter fails open if its store is down | Far above human use; a cache outage cannot block a right |
| Token type | Only interactive user tokens; refresh and machine (worker, code-run, researcher) tokens get 403 | Machines are not data subjects |

`auth_time` is stamped only by an interactive login (password, MFA completion, SAML/OIDC callback, registration) and is copied unchanged by token refresh, so a refreshed token never looks recent. `iat` is not used. A token issued before this claim existed has no `auth_time` and is treated as not recent: the user signs in again.

## Erasure Sequence

1. Replay the stored receipt unchanged if a completed one exists. Otherwise refuse (HTTP 409, nothing changed) while the tenant has `tenants.legal_hold` set or the subject is its last active administrator.
2. Open an in-progress row in `privacy_erasure_receipts` (or reuse the one an interrupted run left).
3. Deactivate the subject. Authentication rejects an inactive subject on every request, so every issued access/refresh token stops working; API keys are deleted in step 5.
4. Redact the subject's identifiers from `audit_logs.details` — see "Free-Text Redaction Rules".
5. Walk the inventory below: delete, unlink, or retain with a legal basis.
6. Overwrite the subject's own row into an anonymised tombstone (the row and numeric id survive so retained records keep a valid foreign key; no column identifies a person).
7. Complete the receipt: tables, columns, row counts, action, legal basis, and the list under "Not Erased Synchronously". The receipt contains no personal data.

penguin-dal commits each statement, so the run is not one database transaction. It is ordered and repeatable instead: an interrupted run leaves the receipt in progress and is finished by calling erase again; a second call on an erased subject changes nothing and returns the stored receipt.

## Free-Text Redaction Rules

- **Aliases** are the subject's stored e-mail and username, plus the display name only when no other subject in the tenant has the same one. An alias shorter than 4 characters or on the generic deny-list (`admin`, `root`, `user`, `test`, `system`, …) is never searched for.
- **Matching** is case-insensitive and whole-token: `bob` matches `login by bob.` but not `bobcat`, `bob-smith` or `bob@example.com`. Only the matched text is replaced with `[erased]`.
- **Scope:** events the subject performed, and events about the subject's identity performed by another identity of the same tenant. Events with no actor or an actor from another tenant are not modified. In events performed by someone else, fields naming that actor (`username`, `name`, `email` keys) are left alone; those keys are cleared only in the subject's own events.
- **Free-text columns** (`references.created_by`, `stream_executions.triggered_by`) are cleared only on an exact whole-value match inside the subject's tenant.
- **Receipt:** each redacted original value is recorded as a SHA-256 digest with a count, never in plaintext.
- Consequence: a subject whose only identifiers are short or generic leaves those words in other people's event text; the tenant administrator reviews such records manually.

## Identity Row (`identities`)

Overwritten: `username`, `email`, `full_name`, `password_hash`, `mfa_secret`, `mfa_enabled`, `auth_provider_id`, `external_id`, `metadata`, `last_login_at`, `is_active`; `anonymized_at` set. Portal users (`portal_users`): `email`, `full_name`, `password_hash`, `mfa_secret`, `mfa_backup_codes`, `last_login_at`, `is_active`.

## Identity Inventory (89 linked columns in 74 tables)

Actions: delete 15, unlink 54, retain 19, scrub match 2. Columns after the first are PII columns cleared on the same rows.

| Table | Columns | Action | Basis |
|---|---|---|---|
| `api_keys` | `identity_id` | delete | Art. 17(1) |
| `saved_searches` | `identity_id` | delete | Art. 17(1) |
| `on_call_rotation_participants` | `identity_id` | delete | Art. 17(1) |
| `on_call_notifications` | `identity_id` | delete | Art. 17(1) |
| `access_review_items` | `identity_id` | retain | completed access-review evidence (SOC 2) on tombstone |
| `identity_group_memberships` | `identity_id`, `provider_member_id` | delete — except a membership a retained access-review item references, which is kept with `provider_member_id` cleared | Art. 17(1); kept rows: access-review evidence |
| `user_roles` | `identity_id` | delete | Art. 17(1) |
| `resource_roles` | `identity_id` | delete | Art. 17(1) |
| `hd_team_members` | `identity_id` | delete | Art. 17(1) |
| `iceflows_stage_approvers` | `identity_id` | delete | Art. 17(1) |
| `dg_shares` | `shared_with_identity_id` | delete | Art. 17(1) |
| `dg_collection_shares` | `shared_with_identity_id` | delete | Art. 17(1) |
| `stream_shares` | `shared_with_identity_id` | delete | Art. 17(1) |
| `stream_editor_locks` | `locked_by_identity_id` | delete | Art. 17(1) |
| `dg_collaboration_sessions` | `identity_id` | delete | Art. 17(1) |
| `audit_logs` | `identity_id`, `ip_address`, `user_agent` | unlink (NULL) | security audit trail, pseudonymised |
| `key_access_log` | `identity_id` | unlink (NULL) | security audit trail, pseudonymised |
| `secret_access_log` | `identity_id` | retain | security audit trail, pseudonymised |
| `dg_share_analytics` | `accessed_by_identity_id`, `access_ip`, `user_agent` | unlink (NULL) | Art. 17(1) |
| `stream_form_submissions` | `submitted_by_identity_id`, `ip_address`, `user_agent` | unlink (NULL) | Art. 17(1) |
| `references` | `created_by` | scrub match | Art. 17(1) |
| `stream_executions` | `triggered_by` | scrub match | Art. 17(1) |
| `dg_comments` | `author_identity_id` | unlink (NULL) | Art. 17(1) |
| `dg_comment_replies` | `author_identity_id` | unlink (NULL) | Art. 17(1) |
| `issue_comments` | `author_id` | unlink (NULL) | Art. 17(1) |
| `hd_ticket_messages` | `sender_identity_id` | retain | tenant business record on tombstone |
| `csv_import_jobs` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `dg_diagrams` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `dg_diagrams` | `updated_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `dg_storage_providers` | `owner_identity_id` | unlink (NULL) | Art. 17(1) |
| `dg_templates` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `dg_collection_items` | `added_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `dg_collection_shares` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `dg_diagram_versions` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `dg_node_metadata` | `updated_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `dg_shares` | `shared_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `doc_versions` | `author_identity_id` | unlink (NULL) | Art. 17(1) |
| `hd_canned_responses` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `hd_tickets` | `requester_identity_id` | unlink (NULL) | Art. 17(1) |
| `hd_tickets` | `assignee_identity_id` | unlink (NULL) | Art. 17(1) |
| `metadata_fields` | `created_by_id` | unlink (NULL) | Art. 17(1) |
| `organizations` | `owner_identity_id` | unlink (NULL) | Art. 17(1) |
| `identity_groups` | `owner_identity_id` | unlink (NULL) | Art. 17(1) |
| `remote_workers` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `resource_roles` | `granted_by_id` | unlink (NULL) | Art. 17(1) |
| `run_functions` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `run_executions` | `triggered_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `run_function_versions` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `stream_custom_modules` | `uploaded_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `stream_playbooks` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `stream_playbooks` | `updated_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `stream_templates` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `stream_executions` | `triggered_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `stream_node_metadata` | `updated_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `stream_shares` | `shared_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `stream_versions` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `stream_execution_approvals` | `approver_identity_id` | unlink (NULL) | Art. 17(1) |
| `sync_conflicts` | `resolved_by_id` | unlink (NULL) | Art. 17(1) |
| `issues` | `reporter_id` | unlink (NULL) | Art. 17(1) |
| `issues` | `closed_by_id` | unlink (NULL) | Art. 17(1) |
| `resource_costs` | `created_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `services` | `poc_identity_id` | unlink (NULL) | Art. 17(1) |
| `ai_resources` | `owner_id` | unlink (NULL) | Art. 17(1) |
| `certificates` | `created_by_id` | unlink (NULL) | Art. 17(1) |
| `certificates` | `updated_by_id` | unlink (NULL) | Art. 17(1) |
| `component_vulnerabilities` | `remediated_by_id` | unlink (NULL) | Art. 17(1) |
| `software` | `purchasing_poc_id` | unlink (NULL) | Art. 17(1) |
| `data_stores` | `poc_identity_id` | unlink (NULL) | Art. 17(1) |
| `iceflows_promotions` | `requested_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `iceflows_promotions` | `merged_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `iceflows_executions` | `started_by_identity_id` | unlink (NULL) | Art. 17(1) |
| `on_call_escalation_policies` | `identity_id` | unlink (NULL) | Art. 17(1) |
| `on_call_overrides` | `created_by_id` | unlink (NULL) | Art. 17(1) |
| `access_review_assignments` | `reviewer_identity_id` | retain | tenant business record on tombstone |
| `group_access_requests` | `requester_id` | retain | tenant business record on tombstone |
| `group_access_approvals` | `approver_id` | retain | tenant business record on tombstone |
| `dg_collections` | `owner_identity_id` | retain | tenant business record on tombstone |
| `dg_diagrams` | `owner_identity_id` | retain | tenant business record on tombstone |
| `dg_shape_libraries` | `owner_identity_id` | retain | tenant business record on tombstone |
| `doc_documents` | `author_identity_id` | retain | tenant business record on tombstone |
| `pg_pages` | `author_identity_id` | retain | tenant business record on tombstone |
| `stream_playbooks` | `owner_identity_id` | retain | tenant business record on tombstone |
| `iceflows` | `created_by_identity_id` | retain | tenant business record on tombstone |
| `iceflows_credentials` | `created_by_identity_id` | retain | tenant business record on tombstone |
| `iceflows_approvals` | `approver_identity_id` | retain | tenant business record on tombstone |
| `on_call_shifts` | `identity_id` | retain | tenant business record on tombstone |
| `on_call_overrides` | `original_identity_id` | retain | tenant business record on tombstone |
| `on_call_overrides` | `override_identity_id` | retain | tenant business record on tombstone |
| `dsar_requests` | `identity_id` | retain | accountability record (ids only) |

## Portal-User Inventory

| Table | Columns | Action | Basis |
|---|---|---|---|
| `portal_user_org_assignments` | `portal_user_id` | delete | Art. 17(1) |
| `portal_mfa_challenges` | `portal_user_id` | delete | Art. 17(1) |
| `data_stores` | `created_by` | unlink (NULL) | Art. 17(1) |
| `dsar_requests` | `portal_user_id` | retain | accountability record (ids only) |

## Retention Exceptions

- **Security audit trail** (`audit_logs`, `key_access_log`, `secret_access_log`): the event is kept; the identity link, IP address and user agent are removed where the column is nullable. `secret_access_log.identity_id` is NOT NULL and stays attached to the tombstone.
- **Access-review evidence** (`access_review_items`, and the group membership each item references): kept as evidence that the review took place; the subject link resolves only to the tombstone and the membership's external member id is cleared. All other memberships are deleted.
- **Tenant business records** with a NOT NULL author/owner column stay attached to the tombstone. Making those columns nullable would allow a full unlink; that is a schema change outside this inventory.
- **Soft integer references** without a foreign key (for example `issues.assignee_id`, `group_access_requests.decided_by_id`, JSON id lists such as `doc_documents.visibility_users`) are not rewritten; they resolve only to the tombstone.
- **Accountability records** (`dsar_requests`, `privacy_consent_records`, `privacy_erasure_receipts`) are kept; they hold ids and codes only.

## Not Erased Synchronously

| Store | Why |
|---|---|
| `database_backups` | Backups are immutable snapshots; the subject's data ages out when the backup retention window expires and must be re-erased if a backup taken before this receipt is ever restored. |
| `free_text_and_attachments` | Comment bodies, ticket messages, documents, form submissions and uploaded files are tenant records whose free text is not parsed; personal data typed into them needs manual review by the tenant administrator. |
| `issued_access_tokens` | JWTs are stateless and are not individually revoked; they are rejected on every request because the subject row is deactivated before anything else is erased. |
| `external_systems` | Identity providers (SSO/SCIM/LDAP), already-delivered webhooks and exported telemetry are outside this database; the tenant must erase the subject there separately. |
| `search_and_cache` | Search runs directly on the database (no separate index) and there is no server-side session store; the only subject-bound cache entries are 60-second diagram-collaboration tickets, which expire on their own rather than being purged. |

**Backup retention window:** this repository does not define one — it is set by the operator of each deployment. The operator must record the window here for their deployment and re-run erasure for receipts issued after any backup that is restored.

## Export Bundle

`GET /me/export` returns JSON: `subject`, `identity` (profile; never password hash, MFA secret or key hashes), `consent` (current state per purpose plus full ledger with lawful basis), and `categories` — one list per inventoried table/column holding only the explicitly allowed columns of rows linked to the requesting subject in the caller's tenant. Lists are capped at 5000 rows per table; capped tables are named in `truncated`.

## Consent Ledger

`privacy_consent_records` is append-only: subject, purpose (1 sale/sharing, 2 analytics, 3 marketing), state (1 granted, 2 withdrawn), lawful basis (1 consent, 2 legal obligation, 3 legitimate interest), timestamp. `identities.do_not_sell_share` / `consent_withdrawn_at` are kept in step for existing readers.
