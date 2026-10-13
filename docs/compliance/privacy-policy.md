# Privacy Policy — Elder

**⚠️ TEMPLATE — Review by legal counsel and Data Protection Officer (DPO) before publishing or deployment.**

## 1. Introduction

Elder is a multi-tenant infrastructure asset-management SaaS platform. This privacy policy describes how we collect, process, store, and protect personal data in accordance with GDPR, CCPA, and applicable data protection regulations.

## 2. Scope

This policy applies to:
- Tenant administrators and users accessing Elder
- Service accounts and API keys issued within Elder
- Cloud provider identities integrated with Elder (AWS, GCP, Azure account linking)
- Audit and compliance data retained within Elder

## 3. Personal Data Categories

Elder processes the following personal data:

| Category | Examples | Source | Purpose |
|----------|----------|--------|---------|
| **Identity & Authentication** | Email, username, full name, password hash, MFA secrets | User registration / SSO | Account access, MFA enforcement |
| **Authorization & Roles** | Tenant ID, team assignments, global/tenant-level roles, scopes | Role assignment | Access control, audit logging |
| **Audit & Compliance** | User identity, action performed, resource affected, timestamp, IP address | Automatic logging | Security, compliance, incident response |
| **Cloud Integration Data** | AWS ARN, GCP service account email, Azure object ID, cloud provider external IDs | User-initiated cloud linkage | Multi-cloud asset discovery and management |
| **Tenant Metadata** | Tenant name, slug, domain, license tier, subscription status, feature flags | Tenant configuration | License enforcement, feature control |
| **Contact Information** | Email address, phone (optional, via identity metadata) | User profile, CRM integration | Support, billing, notifications |
| **Technical Identifiers** | IP address, user agent, session tokens, API key fingerprints | HTTP logs, session management | Security, rate limiting, troubleshooting |

## 4. Lawful Basis for Processing

| Activity | Lawful Basis | Retention | Notes |
|----------|--------------|-----------|-------|
| **Account & Identity Management** | Contractual necessity | Account lifetime + 90 days after deletion | Fulfilling SaaS contract |
| **Authentication & MFA** | Contractual necessity; legitimate interest | Account lifetime | Security & compliance |
| **Audit Logging** | Legal obligation (compliance, incident investigation) | 1–36,500 days (tenant-configurable, default 90) | Reflects `data_retention_days` setting |
| **Cloud Integration** | Contractual necessity; user consent (explicit linking) | As long as integration remains active | User can revoke at any time |
| **License Enforcement** | Contractual necessity | Account lifetime | Usage metering & subscription validation |
| **Support & Troubleshooting** | Legitimate interest; contractual necessity | 90 days post-resolution | Incident resolution & improvement |
| **Marketing / Analytics** | Consent (opt-in where required by law) | Until consent revoked | PII excluded; only anonymized/pseudonymized event data |

## 5. Data Retention Policy

→ See `data-retention-policy.md` for detailed retention windows and automatic purge schedules.

**Default Retention:**
- **Audit logs**: 90 days (tenant-configurable 1–36,500 days)
- **User accounts**: Lifetime of account; 90 days post-deletion before permanent purge
- **Sessions/tokens**: 24 hours (short-lived JWT)
- **Cloud integration credentials**: Until explicitly revoked; no permanent storage of cloud API keys

## 6. Data Subject Rights

### 6.1 Access & Portability
- Data subjects may request a complete copy of their personal data in a structured, machine-readable format (e.g., JSON/CSV)
- Requests processed within 30 days; contact: `dpo@penguintech.io` or via tenant admin console
- Includes audit logs, identity records, role assignments

### 6.2 Rectification
- Incorrect personal data may be corrected via user profile or tenant admin console
- Cloud integration identifiers cannot be edited directly; revoke and re-link instead

### 6.3 Erasure ("Right to be Forgotten")
- **Timescale**: Up to 30 days after request and end of retention period
- **Exceptions**:
  - Audit logs (legal/compliance hold) — anonymized instead of deleted
  - Identity records (contractual necessity) — retained until account deleted and retention period expires
- Contact: `dpo@penguintech.io`

### 6.4 Restriction of Processing
- Data subjects may request processing to be limited (e.g., suspend account without deletion)
- Supported: temporarily disable account; audit logging continues for compliance

### 6.5 Objection
- Right to object to processing on grounds of legitimate interest
- Example: opt out of analytics/telemetry; still required to retain audit logs for compliance

### 6.6 CCPA-Specific Rights (US Residents)
- **Do Not Sell or Share Personal Information**: Data not sold; PII never shared with third parties for marketing
- **Opt-Out**: Via settings or `dpo@penguintech.io`
- **Consumer Rights**:
  - Access to collected personal information
  - Deletion of personal information (subject to legal holds)
  - Opt-out of analytics

## 7. Sub-Processors & Third Parties

Elder may engage the following sub-processors (subject to Data Processing Agreement):

| Service | Purpose | Region | Certification |
|---------|---------|--------|---|
| **[Database Provider]** | Data storage & redundancy | [Region] | [GDPR/SOC2] |
| **[Email Service]** | Transactional email (support, billing) | [Region] | [GDPR/SOC2] |
| **[Cloud IaaS]** | Hosting & backups | [Region] | [GDPR/SOC2] |
| **[Telemetry/Monitoring]** | Performance & error tracking (no PII) | [Region] | [Privacy-respecting] |

**Note:** Cloud provider integrations (AWS, GCP, Azure) are not sub-processors; users link their own accounts and retain full control over data flow.

## 8. International Data Transfers

Elder may process personal data in multiple geographic regions. For transfers outside the EU/EEA or UK:
- Standard Contractual Clauses (SCC) in place for US & other adequacy determinations
- Transfers comply with Schrems II implications
- Data localization available for Enterprise tier customers

## 9. Security Measures

- **Transport**: TLS 1.3 (HTTPS, encrypted APIs)
- **Storage**: AES-256 encryption at rest on all data stores
- **Access**: Role-based access control (RBAC), tenant isolation, IP whitelisting available
- **Audit**: All access logged; security scans (SAST, trivy, dependency audits) run before every release
- **Incident Response**: Breach notification within 72 hours of discovery (as required by GDPR Art. 33)

## 10. Contact & Requests

**Data Protection Officer (DPO):** `dpo@penguintech.io`
**Legal / Privacy Inquiries:** `legal@penguintech.io`
**Support:** `support@penguintech.io`

Data Subject Requests (DSR): Submit to DPO with proof of identity; processed within 30 days.

## 11. Policy Updates

This policy may be updated to reflect regulatory changes or product features. Material changes notified to customers ≥30 days in advance.

**Last Updated:** [Date]
**Effective Date:** [Date]

---

**Approval by Legal & DPO Required Before Publishing**
