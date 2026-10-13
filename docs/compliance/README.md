# Compliance & Privacy Documentation

This directory contains compliance documentation for Elder, including privacy policy, records of processing activities (RoPA), and data retention schedules.

**⚠️ TEMPLATES** — All documents in this directory are templates. They must be reviewed and approved by Legal and the Data Protection Officer (DPO) before publication.

## Documents

### 1. [Privacy Policy](./privacy-policy.md)

Describes how Elder collects, processes, and protects personal data in accordance with GDPR, CCPA, and other applicable regulations.

**Covers:**
- Data categories processed (identities, audit logs, cloud integrations, etc.)
- Lawful basis for each processing activity
- Retention periods and purge schedules
- Data subject rights (access, deletion, portability, objection)
- Security measures and sub-processors
- Contact information for privacy inquiries

### 2. [Records of Processing Activities (RoPA)](./records-of-processing.md)

GDPR Article 30 compliant record of all personal data processing conducted by Elder.

**Covers:**
- Seven processing activities (identity mgmt, audit logging, cloud integration, RBAC, tenant mgmt, session/API keys, telemetry)
- Personal data categories for each activity
- Purpose, legal basis, retention, recipients, and safeguards
- Data subject rights fulfillment table
- **Note:** Rows marked 🔶 (inferred) require developer/DPO confirmation

### 3. [Data Retention Policy](./data-retention-policy.md)

Detailed retention schedules and automatic purge rules enforced by Elder.

**Covers:**
- Retention window for each data category (audit logs, identities, credentials, sessions, etc.)
- Automatic purge job triggers and grace periods
- Legal hold procedures
- Configuration via Tenant API
- Compliance attestation (SOC 2, ISO 27001, GDPR, CCPA)

---

## Cross-References

- **Privacy Policy** references **Data Retention Policy** for detailed retention schedules (§ 5)
- **RoPA** references **Privacy Policy** § 3 for CCPA data categories; **Data Retention Policy** for specific retention windows per activity
- **Data Retention Policy** references code implementations: `audit/service.py`, `api/v1/tenants.py`, etc.

---

## Next Steps

1. **DPO Review**: Forward all three documents to your Data Protection Officer for legal review
2. **Legal Review**: Customize contact information, sub-processor list, and transfer mechanisms (SCC/adequacy) per your deployment
3. **Customer Communication**: Publish approved versions to your privacy portal or website
4. **Employee Training**: Ensure all staff handling personal data understand the retention and rights procedures

---

## Configuration Examples

### Set Tenant Audit Retention to 180 Days

```bash
curl -X POST /api/v1/tenants/{tenant_id} \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"data_retention_days": 180}'
```

### Request Data Export (DSAR)

Contact: `dpo@penguintech.io`

Provide:
- Proof of identity
- Data subject email (the account to export)
- Specific date range (optional)

**Expected timeline:** 30 days

---

## Gaps & To-Do

**Before publication, fill in or verify:**

- [ ] **Privacy Policy § 7**: Update sub-processor list with actual vendors (database, email, SIEM, etc.)
- [ ] **Privacy Policy § 8**: Confirm international transfer mechanism (SCC, adequacy decisions) for your deployment region(s)
- [ ] **RoPA Activities 3, 6, 7**: Confirm inferred rows (marked 🔶) by reviewing code/architecture with dev team
- [ ] **Data Retention Policy**: Verify purge job implementations (`audit/service.py`, etc.) match schedule
- [ ] **All documents**: Update dates, contact emails, approval lines

---

**Last Updated:** 2026-09-23
**Status:** TEMPLATE — Not approved for publication
