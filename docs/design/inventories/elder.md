# Elder — Feature Inventory → Module Mapping

Source: /home/penguin/code/elder. Groups map to modules per the approved spec.

## A. Graph & Discovery Core → `infrastructure` module
organizations (PyDAL impl is live), organization_tree, entities, entity_types (Network/Compute/Storage/Datacenter/Security sub-types), dependencies (bulk ops), graph (impact analysis, path-finding, topology map), search + saved searches, public lookup (/lookup, /id/{village_id}), Dashboard.
NOTE: search + lookup + dashboard stay CORE (shell); graph/entities/deps → infrastructure module.

## B. Identity & Access → core (+ access_reviews module)
auth (login/register/refresh/change-password), identities (users/service accounts/groups), users admin, profile, api_keys, IAM integration page, RBAC models.
ENT: resource_roles, group_membership, access_reviews (+ scheduler) → `access_reviews` module (Enterprise SKU).

## C. Secrets/Keys/Certs → `secrets` module
secrets (external providers), builtin_secrets (native encrypted store), keys (CryptoKey/KeyProvider), certificates.

## D. Software & Security Posture → `sbom` module
software, services tracking, service endpoints (+ scanner parsers Django/Express/FastAPI/Flask/Go), SBOM components/scans/schedules, vulnerabilities (CVE + component links). (Pro tier)

## E. Infrastructure & Networking → `infrastructure` + `ipam` + `discovery` + `datastores`
networking resources/topology, IPAM (prefix/address/VLAN), Compute page (K8s, LXD tabs), cloud discovery jobs, costs (⚠ no nav entry — add one), data stores + labels.

## F. Tracking → `issues` module
issues (ENT-gated per route today → Free in new tiers, un-gate), comments (⚠ blueprint NOT registered — wire it during port), labels, milestones, projects, typed metadata (ENT).

## G. On-Call → `services_oncall` module
on-call rotations (CRUD/history/participants), shifts/overrides/escalation, current-on-call, inbound alert webhooks.
⚠ AlertConfiguration model has no route/page — decide: wire or drop.

## H. Webhooks/Notifications → `webhooks_alerting` module
outbound webhooks + delivery log, NotificationRule (event→channel; NOT a workflow engine — Flows module will be the workflow story), on-call alert webhooks.

## I. Worker connectors → `discovery` module task groups
Wired: AWS, GCP, Google Workspace, LDAP, Okta (ENT), Authentik (ENT), LXD.
⚠ NOT wired (half-built): iboss, fleetdm, vcenter, k8s connectors — decide wire/defer per connector.
Issue-tracker two-way sync: GitHub, GitLab, Jira, OpenProject, Trello (webhook_handler + batch_scheduler + conflict_resolver).
Cloud discovery executor: AWS, Azure, GCP, Vultr, K8s.
Sync admin: sync API + SyncConfig/History/Conflict/Mapping models + SyncConfig.tsx.

## J. Scanner → stays separate container
masscan network scan, banner grab, Playwright HTTP screenshots, SBOM git-repo scanner, endpoint parsers.

## K. MCP → core + ai_search extension
Existing tools: search_entities/get/update, relationships CRUD, list_organizations, list_identities, search_services. New AI tools land per spec.

## L. gRPC → core internal comms
ElderService: auth, organizations, entities, dependencies (bulk), graph (GetDependencyGraph/AnalyzeGraph/FindPath/GetEntityImpact), health.

## M. Admin/Enterprise → core admin + `compliance` module
audit (core) + audit_enterprise/retention (ENT → compliance/SIEM SKU), logs viewer, admin settings, SSO/SAML/**SCIM already modeled** (SCIMConfiguration exists — build on it), portal auth, tenants mgmt, license policies, backups, Google Workspace provider config.

## Fix-during-port flags
1. Legacy organizations.py (SQLAlchemy) superseded by organizations_pydal.py — delete legacy.
2. comments.py blueprint unregistered — register in issues module.
3. Unwired connectors (iboss/fleetdm/vcenter/k8s) — decision per connector.
4. AlertConfiguration model-only — wire or drop.
5. Costs has no sidebar nav — add under Infrastructure.
6. DTO duplication (dataclasses/schemas/pydantic overlap) — consolidate during module moves.
7. License gating is per-route decorator — replace with module-manifest gating; audit every ENT route during move.
8. Issues/metadata currently ENT-gated → become Free under new tier matrix (un-gate deliberately).
