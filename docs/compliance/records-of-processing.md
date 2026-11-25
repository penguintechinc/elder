# Records of Processing Activities (RoPA) — Elder

**GDPR Article 30 Records; CCPA Privacy Policy Compliance**

**⚠️ TEMPLATE — To be reviewed and finalized by Data Protection Officer and Legal. Marked rows indicate inferred or potential processing; verify against current feature set before publication.**

---

## Overview

Elder is a multi-tenant infrastructure asset-management SaaS platform. This RoPA documents all personal data processing activities conducted by PenguinTech (Processor & Controller) and our customers (Controllers for their own data).

**Document Authority:** GDPR Articles 30(1), 30(2); CCPA § 1798.100 et seq.

---

## Processing Activities

### Activity 1: User Identity & Account Management

| Field | Value |
|-------|-------|
| **Activity Name** | User Account Registration, Authentication, Identity Lifecycle |
| **Personal Data Categories** | Email address, username, full name, password hash, MFA secret, phone number (optional), profile metadata |
| **Purpose(s)** | Account creation, authentication, MFA enforcement, tenant isolation, identity federation (SAML/OAuth2) |
| **Legal Basis** | Contractual necessity (subscription agreement); legitimate interest (fraud prevention) |
| **Data Subjects** | Tenant administrators, end users, service accounts |
| **Data Source** | User registration form, SSO provider (Okta, Google, custom SAML), manual tenant admin provisioning |
| **Recipients** | Tenant-scoped database tables (`portal_users`, `identities`); no external disclosure |
| **Retention** | Account lifetime + 90 days post-deletion before permanent purge |
| **Transfers** | Stored within [deployment region]; international transfers via SCC (if applicable) |
| **Automated Decision-Making** | No individual profiling; RBAC-based access decisions are policy-driven, not ML-driven |
| **Storage Safeguards** | AES-256 at-rest encryption; TLS 1.3 in-transit; password hashing (bcrypt); MFA secret encryption |
| **Sub-processors** | (If using managed identity providers: Okta, Google Cloud Identity, etc. — list in DPA) |
| **Code Reference** | `apps/api/models/identity.py`; `apps/api/models/tenant.py` |
| **Notes** | ✅ **Grounded in code** — `identity.py` confirms storage of email, full_name, auth_provider, mfa_secret, password_hash. Phone/location stored in `identity_metadata` (optional). |

---

### Activity 2: Audit & Compliance Logging

| Field | Value |
|-------|-------|
| **Activity Name** | Audit Log Capture & Retention |
| **Personal Data Categories** | Actor identity (user ID/email), action (create/update/delete/login), resource affected, timestamp, IP address, HTTP method/status code, optional PII in resource data (masked during logging) |
| **Purpose(s)** | Legal compliance, incident investigation, security monitoring, anomaly detection |
| **Legal Basis** | Legal obligation (GDPR Art. 30, SOC 2 compliance, CCPA transparency); legitimate interest (fraud/intrusion detection) |
| **Data Subjects** | All users performing actions within Elder; service accounts |
| **Data Source** | Automatic capture at API request time; middleware instrumentation |
| **Recipients** | Tenant-scoped audit tables (`audit_logs`); optional: SIEM/SOC tools (via telemetry exporter) for threat intelligence |
| **Retention** | Tenant-configurable: 1–36,500 days (default 90 days); enforced via automated purge job |
| **Transfers** | Stored within [deployment region]; optional export to SIEM (customer-controlled destination via OTLP) |
| **Automated Decision-Making** | No; audit data is read-only and reviewed by humans for incident response |
| **Storage Safeguards** | AES-256 at-rest; no deletion of audit records without legal hold; append-only journal |
| **Sub-processors** | (If shipping to external SIEM: Datadog, Splunk, etc. — disclose in audit log telemetry config) |
| **Code Reference** | `apps/api/models/audit.py`; `apps/api/services/audit/service.py` |
| **Notes** | ✅ **Grounded in code** — `audit.py` confirms capture of identity_id, action_name, resource_type, resource_id, timestamp. Default retention_days=90; configured in `apps/api/api/v1/tenants.py` (field: `data_retention_days`). Purge logic in `audit/service.py`. |

---

### Activity 3: Cloud Provider Integration & Credential Linking

| Field | Value |
|-------|-------|
| **Activity Name** | Multi-Cloud Account Linkage & Asset Discovery |
| **Personal Data Categories** | Cloud provider external IDs (AWS ARN, GCP service account email, Azure object ID), cloud provider name/slug, optional: IAM role names, cloud tags/labels (may contain PII if customer-tagged with personal names) |
| **Purpose(s)** | Asset discovery, cost tracking, compliance mapping, cloud security posture management |
| **Legal Basis** | Contractual necessity (core SaaS feature); user consent (explicit linking at time of integration) |
| **Data Subjects** | Cloud account owners / AWS/GCP/Azure account admins |
| **Data Source** | User-initiated OAuth2/service account creation; Elder calls cloud provider APIs to enumerate resources |
| **Recipients** | Cloud provider (credentials sent to AWS/GCP/Azure per user request); no third-party disclosure |
| **Retention** | As long as integration remains active; revocable by customer at any time (credential destruction within 30 days) |
| **Transfers** | Credentials stored within [deployment region]; API calls to AWS/GCP/Azure (customer's cloud regions); no data re-export |
| **Automated Decision-Making** | No; asset tagging/classification is rule-based, not ML-driven |
| **Storage Safeguards** | Cloud credentials: encrypted at rest, never logged, never shared; stored in secure vault (Sealed Secrets / External Secrets Operator) |
| **Sub-processors** | AWS, GCP, Azure (customers' own infrastructure; not sub-processors) |
| **Code Reference** | `apps/api/models/dataclasses.py` (cloud_provider field); `apps/api/services/costs/base.py`, `providers/gcp_billing.py` |
| **Notes** | 🔶 **Partially grounded; inferred.** Code confirms storage of `cloud_provider` identifiers and integration with GCP BigQuery for cost data. AWS cost integration assumed (not confirmed in grepped results). Credential storage mechanism (vault type) **not confirmed** in read — verify implementation before publication. |

---

### Activity 4: Authorization & Role Management

| Field | Value |
|-------|-------|
| **Activity Name** | Role-Based Access Control (RBAC) & Permission Assignment |
| **Personal Data Categories** | User/identity ID, role name (e.g., "admin", "maintainer", "viewer"), scope assignments, team membership |
| **Purpose(s)** | Access control enforcement, least-privilege implementation, audit trail of permission changes |
| **Legal Basis** | Contractual necessity (SaaS access control); legitimate interest (security) |
| **Data Subjects** | All users assigned to roles within a tenant |
| **Data Source** | Tenant admin role assignment; auto-provisioning via SSO/SAML attribute mapping |
| **Recipients** | Tenant-scoped RBAC tables; included in audit logs for permission audit trail |
| **Retention** | While account active; audit trail retained per Activity 2 (audit log retention) |
| **Transfers** | Stored in tenant database; no external sharing |
| **Automated Decision-Making** | No; access decisions are policy-driven based on role/scope membership |
| **Storage Safeguards** | Same as identity (Activity 1): AES-256 at-rest, RBAC-protected read/write |
| **Sub-processors** | (If using external IdP: Okta, Azure AD — disclose in DPA) |
| **Code Reference** | `apps/api/models/rbac.py` |
| **Notes** | ✅ **Grounded in code** — `rbac.py` confirms role/scope modeling for authorization decisions. Middleware enforces scope checks per `critical-rules.md` (security.md in admin rules). |

---

### Activity 5: Tenant & Subscription Management

| Field | Value |
|-------|-------|
| **Activity Name** | Tenant Provisioning, Billing, License Tracking |
| **Personal Data Categories** | Tenant name, slug, admin email, subscription tier, license key/ID, feature flag state, billing contact email, domain, storage quota |
| **Purpose(s)** | Subscription fulfillment, license enforcement, feature control, billing/metering, multi-tenancy isolation |
| **Legal Basis** | Contractual necessity (billing/subscription); legitimate interest (license compliance) |
| **Data Subjects** | Tenant owners / primary admin contact |
| **Data Source** | Tenant creation (signup or manual provisioning), license server validation |
| **Recipients** | Billing system (internal); no third-party disclosure without consent |
| **Retention** | Account lifetime; 30 days post-deletion (for billing reconciliation); deleted after audit retention expires |
| **Transfers** | Stored within [deployment region]; optional query to external license server (`license.penguintech.io`) for validation |
| **Automated Decision-Making** | Yes (limited): license tier gates feature access via PostHog flag evaluation; no profiling |
| **Storage Safeguards** | AES-256 at-rest; license keys encrypted; admin email not included in API responses (output validation enforced) |
| **Sub-processors** | License server (if external) |
| **Code Reference** | `apps/api/models/tenant.py`; `apps/api/api/v1/tenants.py` |
| **Notes** | ✅ **Grounded in code** — `tenant.py` confirms storage of name, slug, domain, license_key, subscription_tier, feature_flags, data_retention_days. Admin email implied via PortalUser linkage. |

---

### Activity 6: Session & API Key Management

| Field | Value |
|-------|-------|
| **Activity Name** | Session Lifecycle & API Key Provisioning |
| **Personal Data Categories** | Session token (JWT; contains sub/iss/aud/iat/exp/scope/tenant), API key fingerprint (hash only; full key never logged), token expiration, IP address at token issuance |
| **Purpose(s)** | Authentication state management, API access control, rate limiting, token revocation |
| **Legal Basis** | Contractual necessity (API access) |
| **Data Subjects** | API consumers (users or service accounts) |
| **Data Source** | /login endpoint, /auth/token endpoint; API key creation |
| **Recipients** | Client (in HTTP response); JWT claims flow to all downstream services for authorization check |
| **Retention** | Session: 24 hours (short-lived JWT); API keys: until revoked by user |
| **Transfers** | JWT in Authorization header (encrypted in-transit via TLS); stored in client-side session only |
| **Automated Decision-Making** | No; token validation is deterministic (exp check, scope match) |
| **Storage Safeguards** | Tokens never stored server-side (stateless JWT); API key fingerprints hashed; no full keys retained |
| **Sub-processors** | None (purely internal) |
| **Code Reference** | `apps/api/services/auth/`, JWT handling (JWT BCP per `security.md` in admin rules) |
| **Notes** | 🔶 **Inferred** — JWT structure assumed from OIDC standards and `critical-rules.md` JWT Claims guidance; specific implementation code for token issuance not grepped. Verify token lifecycle before publication. |

---

### Activity 7: Telemetry & Observability (OTel)

| Field | Value |
|-------|-------|
| **Activity Name** | OpenTelemetry Logging, Metrics, Traces |
| **Personal Data Categories** | Request path/URL, HTTP headers (user-agent, x-forwarded-for), sanitized request/response bodies (no tokens/secrets logged), service name, span trace IDs |
| **Purpose(s)** | Performance monitoring, error tracking, incident debugging, SLA validation |
| **Legal Basis** | Legitimate interest (system monitoring and improvement) |
| **Data Subjects** | Service operators (minimal PII in telemetry; auto-sanitized per penguin-logging library) |
| **Data Source** | Service instrumentation; middleware emission; OTel SDKs |
| **Recipients** | OTLP endpoint (customer-configurable destination: Grafana, Datadog, SigNoz, etc. via env var `OTEL_EXPORTER_OTLP_ENDPOINT`) |
| **Retention** | Per customer's telemetry backend (not controlled by Elder) |
| **Transfers** | Sent to customer-specified OTLP destination (no external vendor lock-in) |
| **Automated Decision-Making** | No; metrics are descriptive only |
| **Storage Safeguards** | Secrets/tokens auto-masked during emission; no PII in metric labels; DEBUG-level logs disabled in production |
| **Sub-processors** | (Customer's choice of OTLP backend — list in customer's DPA) |
| **Code Reference** | `critical-rules.md` Observability (OTel); penguin-logging library usage confirmed in test requirements |
| **Notes** | 🔶 **Inferred** — OTel implementation specifics not fully audited in grepped code. Verify that secrets are masked and PII excluded before publication; confirm telemetry sink in deployment config. |

---

## Summary Table: Data Categories & Retention

| Processing Activity | Data Category | Lawful Basis | Retention | Grounding |
|---|---|---|---|---|
| User Identity & Auth | Email, username, MFA | Contractual | Lifetime + 90d | ✅ Code |
| Audit & Compliance | Action, actor, resource, timestamp | Legal obligation | 1–36,500d (default 90d) | ✅ Code |
| Cloud Integration | Cloud provider IDs, ARNs | Contractual + consent | While active | 🔶 Inferred |
| RBAC | Role, scope, team membership | Contractual | While active | ✅ Code |
| Tenant & Billing | Name, tier, license, email | Contractual | Lifetime + 30d | ✅ Code |
| Sessions & API Keys | Token, IP, fingerprint | Contractual | 24h (session) / revocable (API key) | 🔶 Inferred |
| OTel Telemetry | Request/response, spans | Legitimate interest | Varies (backend-dependent) | 🔶 Inferred |

---

## Data Subject Rights Fulfillment

**Access (GDPR Art. 15 / CCPA § 1798.100):**
- ✅ Data subject portal for profile & settings access
- ✅ Admin API for export of tenant data (JSON/CSV format)
- ⏳ DSAR (Data Subject Access Request) flow via DPO: `dpo@penguintech.io` → manual export within 30 days

**Rectification (GDPR Art. 16):**
- ✅ Users may edit their own profile (email, name, phone)
- ⏳ Incorrect audit log entries: contact DPO (cannot directly edit; may be annotated)

**Erasure (GDPR Art. 17 / CCPA § 1798.105):**
- ✅ Account deletion deletes identity & tenant associations after 90-day grace period
- ❌ Audit logs: retained for compliance hold; anonymized instead of deleted
- ✅ Cloud integration credentials: immediately revoked upon deletion request

**Restriction (GDPR Art. 18):**
- ✅ Account suspension available; audit logging continues

**Data Portability (GDPR Art. 20):**
- ✅ DSAR export in structured format (JSON/CSV)

**Objection (GDPR Art. 21):**
- ✅ Opt-out of analytics/non-essential telemetry via settings
- ❌ Audit logging cannot be opted out of (legal obligation)

**CCPA-Specific:**
- ✅ Do Not Sell/Share: No sale of PII; no sharing with marketers
- ✅ Opt-Out: Supported via settings & DPO request
- ✅ Consumer Rights: Implemented as above (Access, Delete, Opt-Out)

---

## Amendments & Maintenance

This RoPA is living documentation. Updates required when:
- New processing activity introduced (e.g., new feature or integration)
- Legal basis or retention policy changes
- Sub-processor added/removed
- Data subject rights procedure modified

**Last Updated:** [Date]
**DPO Review Date:** [Date]
**Next Review:** [Date + 12 months]

---

**⚠️ Rows marked 🔶 (Inferred) require confirmation by Developer / DPO before final publication.**
