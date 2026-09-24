# Data Retention Policy — Elder

**Retention Schedules & Automatic Purge Rules**

---

## Overview

Elder enforces configurable retention windows for audit logs and automatic purge schedules for other personal data. Retention is tenant-scoped (each tenant may configure their own audit log retention) where applicable.

**Configuration:** Retention settings managed via Tenant API (`data_retention_days` field) and enforced by automated background jobs.

---

## Retention Schedule by Data Category

### 1. Audit Logs

| Attribute | Value |
|-----------|-------|
| **Data Retained** | Action, actor (user ID), resource affected, timestamp, IP address, HTTP method/status |
| **Default Retention** | **90 days** |
| **Configurable Range** | 1–36,500 days (tenant admin may increase for compliance; decrease subject to legal holds) |
| **Purge Mechanism** | Automated daily job: soft-delete (logical deletion) + hard-delete after 30-day grace period |
| **Exceptions** | Active legal hold / ongoing investigation → purge date postponed; customer retention override available for specific incidents |
| **Configuration** | `POST /api/v1/tenants/{id}` → `data_retention_days` field |
| **Code Reference** | `apps/api/services/audit/service.py` (purge logic); `apps/api/api/v1/tenants.py` (config endpoint) |
| **Compliance Basis** | GDPR Art. 30 (audit trail); SOC 2 / ISO 27001 (audit log requirements); CCPA audit transparency |

---

### 2. User Identities & Accounts

| Attribute | Value |
|-----------|-------|
| **Data Retained** | Email, username, full name, password hash, MFA secret, authentication provider, creation/modification timestamps, IP address at account creation |
| **Retention Period** | **Account lifetime + 90 days post-deletion** |
| **Purge Mechanism** | Soft-delete at account deletion → hard-delete after 90-day grace period; prevents accidental admin errors |
| **Exceptions** | Legal hold / active investigation → grace period extended; audit log linking preserved (anonymized) |
| **Compliance Basis** | Contractual necessity (account lifetime); data minimization (deletion after contract end); GDPR Art. 17 (right to erasure after grace period) |
| **Notes** | 90-day grace allows customer to recover accidentally-deleted accounts; after grace, permanent deletion queued |

---

### 3. Cloud Integration Credentials & Identifiers

| Attribute | Value |
|-----------|-------|
| **Data Retained** | Cloud provider name (AWS/GCP/Azure), external IDs (ARN, service account email, object ID), last-synced timestamp, integration status |
| **Retention Period** | **Until integration revoked or deactivated** |
| **Purge Mechanism** | Immediate revocation of cloud credentials when integration deleted; metadata retained in audit logs per section 1 |
| **Credential Storage** | Never stored in clear text; encrypted at rest; destroyed immediately upon revocation (no grace period for credentials) |
| **Exceptions** | None — cloud credentials destroyed immediately |
| **Compliance Basis** | Security best practice (credential minimization); user control (integration is opt-in) |

---

### 4. Sessions & API Keys

| Attribute | Value |
|-----------|-------|
| **Data Retained** | Session token (JWT; client-side only), API key fingerprint (hash), token metadata (issued at, expires at, IP address) |
| **Session Retention** | **24 hours** (short-lived JWT; no server-side session store) |
| **API Key Retention** | **Until explicitly revoked by user**; optional expiration date set at creation |
| **Purge Mechanism** | Session: expires automatically (no storage); API key: deleted on revocation or expiration |
| **Exceptions** | None — stateless sessions leave no data to purge; API keys immutable once issued |
| **Compliance Basis** | Security (short-lived tokens reduce compromise window); user control (revocable on demand) |

---

### 5. Tenant Metadata & Subscription

| Attribute | Value |
|-----------|-------|
| **Data Retained** | Tenant name, slug, domain, subscription tier, license key, admin contact email, feature flags, storage quota, billing plan |
| **Retention Period** | **Account lifetime + 30 days post-cancellation** |
| **Purge Mechanism** | On account cancellation → 30-day grace period for billing reconciliation → hard-delete of tenant record and all child data (identities, audit logs per section 1, cloud integrations per section 3) |
| **Exceptions** | Legal hold / active investigation → grace period extended; billing disputes → extended until resolved |
| **Compliance Basis** | Contractual (billing); tax/legal (financial records may require longer hold per jurisdiction) |
| **Notes** | Tenant deletion is cascading: deletes all child identities, audit logs (per retention schedule), cloud integrations, RBAC records |

---

### 6. RBAC & Role Assignments

| Attribute | Value |
|-----------|-------|
| **Data Retained** | User ID, role name, scope assignments, team membership, assignment timestamp, assigned-by (audit trail) |
| **Retention Period** | **While identity is active; after identity deletion, retained in audit logs per section 1** |
| **Purge Mechanism** | Automatically deleted when user account deleted (cascade); audit trail of all role changes retained per section 1 retention schedule |
| **Exceptions** | None |
| **Compliance Basis** | Contractual (access control); audit (role change history in audit logs) |

---

### 7. Telemetry & Observability Data

| Attribute | Value |
|-----------|-------|
| **Data Retained** | Request paths, response status, trace IDs, span durations, error stacks, metric values, sanitized request/response bodies |
| **Retention Period** | **Varies by telemetry backend** (not controlled by Elder; customer-configurable via OTLP endpoint) |
| **Purge Mechanism** | Customer's OTLP backend (e.g., Grafana, Datadog, SigNoz) enforces its own retention policies |
| **Default (if self-hosted)** | 30–90 days (configurable in SigNoz / Grafana Loki) |
| **Exceptions** | None — telemetry retention is backend-dependent |
| **Compliance Basis** | Legitimate interest (monitoring); customer controls destination and retention via env var `OTEL_EXPORTER_OTLP_ENDPOINT` |
| **Note** | Secrets/tokens auto-masked during emission; no PII in metric labels |

---

## Automatic Purge Jobs

### Audit Log Cleanup

**Trigger:** Daily, 02:00 UTC (configurable)
**Process:**
1. Query audit logs older than `tenant.data_retention_days`
2. Soft-delete (mark `deleted_at` timestamp)
3. After 30-day grace period, hard-delete (permanent removal)
4. Log purge event (how many records deleted, tenant ID)

**Code:** `apps/api/services/audit/service.py`

### Identity & Tenant Cleanup

**Trigger:** Daily, 03:00 UTC
**Process:**
1. Query deleted identities with `deleted_at` > 90 days
2. Query deleted tenants with `deleted_at` > 30 days
3. Hard-delete identities, cascade-delete related RBAC, cloud integrations
4. Hard-delete tenants, cascade-delete all child tables
5. Log deletion (count, tenant IDs, affected data volumes)

### API Key Expiration

**Trigger:** On request (lazy expiration) + daily batch (02:30 UTC)
**Process:**
1. Check API key expiration date against current timestamp
2. Invalidate expired keys (mark `revoked_at`)
3. Delete revoked keys (no grace period)
4. Log expiration (count, identity IDs)

---

## Legal Holds & Data Preservation

**Procedure for Legal Hold:**
1. DPO receives legal notice or investigation request
2. DPO issues hold order to system: `HOLD <tenant_id> <data_type> <until_date> <reason>`
3. Purge jobs skip data matching hold conditions (audit logs, identities, etc.)
4. On hold expiration date, purge resumes automatically
5. All hold events logged to immutable audit trail

**Example:**
```
HOLD tenant_123 audit_logs 2026-12-31 "Investigation #INV-2026-09-001"
```
After 2026-12-31, audit logs for tenant_123 resume normal purge schedule.

---

## Data Retention by Regulatory Framework

### GDPR (EU/EEA & UK)

| Data Type | GDPR Requirement | Elder Implementation |
|-----------|------------------|----------------------|
| Audit logs | "As long as necessary" | Tenant-configurable (default 90d); up to 36,500d for compliance |
| User data | Erasure after contract end | 90-day grace; then hard-delete |
| Consent records | Retain proof of consent | Logged in audit trail (signature/timestamp) |
| DPA amendments | Version history | Audit trail captures all policy changes |

### CCPA (California)

| Data Type | CCPA Requirement | Elder Implementation |
|-----------|------------------|----------------------|
| Consumer info | Disclose collection; allow deletion | Privacy policy + deletion flow |
| Opt-out records | Honor opt-out ("Do Not Sell") | Settings page + manual override via DPO |
| Deletion timeline | 45 days (with 45-day extension possible) | Immediate soft-delete; hard-delete within 90 days (stricter than CCPA) |
| Categories retained | List all categories | privacy-policy.md § 3 |

### SOC 2 / ISO 27001

| Data Type | Requirement | Elder Implementation |
|-----------|-------------|----------------------|
| Audit logs | Retain ≥12 months | Default 90d; customers may configure up to 10 years |
| Change logs | Immutable audit trail | SQLAlchemy + database constraints prevent modification |
| Access logs | IP, user, action, timestamp | Captured for every API request |

---

## Compliance Attestation

✅ **Audit logs retained for minimum statutory period** (default 90d; customer-configurable)
✅ **Automatic purge enforced** (no manual deletions without audit trail)
✅ **Legal hold mechanism available** (DPO can pause purge on court order)
✅ **Grace periods implemented** (prevent accidental data loss; allow account recovery)
✅ **Secrets destroyed immediately** (cloud credentials, API keys, tokens)
✅ **Telemetry retention customer-controlled** (no vendor lock-in; OTLP configurable)
✅ **Purge events logged immutably** (audit trail of what was deleted and why)

---

## Configuration & Monitoring

### Tenant Audit Log Retention (Admin API)

```bash
# Set retention to 180 days
curl -X POST /api/v1/tenants/{tenant_id} \
  -H "Authorization: Bearer {token}" \
  -d '{"data_retention_days": 180}'

# Query current setting
curl /api/v1/tenants/{tenant_id} | jq '.data_retention_days'
```

### Monitoring Purge Jobs

Purge job metrics emit to OpenTelemetry:
- `elder.audit.purge.records_soft_deleted` (count)
- `elder.audit.purge.records_hard_deleted` (count)
- `elder.identity.purge.identities_deleted` (count)
- `elder.auth.purge.api_keys_expired` (count)

**Alert Conditions:**
- Purge job fails to run → alert after 24 hours
- Purge job error rate > 5% → page oncall
- Legal hold count > expected (suspicious) → review manually

---

## Schedule Summary

| Data Type | Default Retention | Configurable? | Purge Delay |
|-----------|-------------------|---------------|-------------|
| **Audit logs** | 90 days | Yes (1–36,500 days) | Soft: 0d; Hard: +30d |
| **User identities** | Lifetime + 90d | No | Soft at deletion; Hard: +90d |
| **Cloud credentials** | Until revoked | No | Immediate (no grace) |
| **Sessions** | 24 hours | No | Automatic (stateless) |
| **API keys** | Until revoked | Optional (exp. date) | Lazy + batch cleanup |
| **Tenants** | Lifetime + 30d | No | Soft at cancellation; Hard: +30d |
| **Telemetry** | Backend-dependent | Yes (OTLP configurable) | Per backend |

---

**Last Updated:** [Date]
**Approved by:** [DPO/Legal]
**Next Review:** [Date + 12 months]

---

**⚠️ Retention schedules are effective immediately upon approval. Legacy data older than this policy's effective date may be retained longer per prior commitments; contact DPO for amnesty inquiries.**
