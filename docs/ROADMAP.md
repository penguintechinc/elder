# Deprecation & Breaking Changes

## v3.1.0 (Current) — Discovery Migration

**Cloud discovery execution has moved from API to Worker Service** to improve scalability and reliability.

### What Changed
- `POST /jobs/<id>/run` now queues jobs for the Worker Service and returns `202 Accepted` instead of executing synchronously
- Worker Service processes discovery jobs asynchronously with proper resource isolation
- API discovery provider classes (`aws_discovery.py`, `gcp_discovery.py`, `azure_discovery.py`, `k8s_discovery.py`) marked as deprecated

### Backward Compatibility
**For immediate emergency use only:** Add `?legacy=true` query parameter to `POST /jobs/<id>/run` to trigger synchronous execution in API

```bash
# v3.1.0 - Asynchronous (recommended)
curl -X POST https://api.example.com/jobs/123/run
# Returns: 202 Accepted

# v3.1.0 - Synchronous fallback (legacy, not recommended)
curl -X POST https://api.example.com/jobs/123/run?legacy=true
# Returns: 200 OK with results (blocks until complete)
```

### Migration Required
Update clients to use async pattern:
```python
# OLD (v2.x & early v3.x) - Synchronous
response = requests.post(f"{api_url}/jobs/{job_id}/run")
results = response.json()  # Blocks until discovery completes

# NEW (v3.1.0+) - Asynchronous
response = requests.post(f"{api_url}/jobs/{job_id}/run")
assert response.status_code == 202  # Accepted

# Poll for completion
while True:
    history = requests.get(f"{api_url}/jobs/{job_id}/history")
    if history.json()[-1]["status"] == "completed":
        break
    time.sleep(5)
```

---

## v4.0.0 (Next Major) — Breaking Changes

**v4.0.0 removes all synchronous discovery execution and legacy fallback mechanisms.** This is a breaking change that requires client updates.

### Removed
- **Synchronous discovery execution in API** - All `POST /jobs/<id>/run` implementations deleted
- **API discovery provider classes** deleted:
  - `apps/api/services/discovery/aws_discovery.py`
  - `apps/api/services/discovery/gcp_discovery.py`
  - `apps/api/services/discovery/azure_discovery.py`
  - `apps/api/services/discovery/k8s_discovery.py`
- **Legacy `?legacy=true` fallback** - No longer supported

### Breaking API Changes
```
POST /jobs/<id>/run
Before (v3.0.x): Returns 200 with results (synchronous)
After (v4.0.0): Returns 202 with queue location (asynchronous only)
```

### Required Client Migration
**All applications relying on synchronous `POST /jobs/<id>/run` must migrate to the async pattern:**

1. **Queue the job**
   ```bash
   POST /jobs/{id}/run
   # Returns: 202 Accepted
   ```

2. **Poll for completion**
   ```bash
   GET /jobs/{id}/history
   # Check if latest entry has status: "completed"
   ```

3. **Get results**
   ```bash
   GET /jobs/{id}/history
   # Parse entries with status: "success"/"failure"
   ```

---

## Migration Path: v3.1.0 → v4.0.0

### Step 1: Verify Worker Service Deployment
Ensure Worker Service has `DATABASE_URL` configured in v3.1.0:
```yaml
# docker-compose.yml or K8s deployment
worker:
  environment:
    DATABASE_URL: postgres://...
    WORKER_DISCOVERY_ENABLED: "true"
```

### Step 2: Deploy v3.1.0
Update client code **before** upgrading to v4.0.0:
- Test that discovery jobs queue successfully via `POST /jobs/<id>/run` (202 response)
- Implement polling logic using `GET /jobs/<id>/history`
- Verify discovery_history table receives completion events
- Monitor Worker logs for discovery job processing

### Step 3: Monitor Discovery Completion
```python
# Verify discovery jobs complete successfully
import requests
import time

def wait_for_discovery(job_id, max_wait=300):
    start = time.time()
    while time.time() - start < max_wait:
        resp = requests.get(f"{api_url}/jobs/{job_id}/history")
        history = resp.json()
        if history and history[-1]["status"] in ["completed", "failed"]:
            return history[-1]
        time.sleep(5)
    raise TimeoutError(f"Discovery job {job_id} did not complete")
```

### Step 4: Verify All Discovery Jobs Complete
Before upgrading to v4.0.0:
- Run smoke tests to confirm discovery jobs complete successfully
- Monitor `discovery_history` table for new entries
- Verify no jobs stuck in "queued" or "running" state
- Check Worker service logs for errors

### Step 5: Upgrade to v4.0.0
Once all clients updated and discovery jobs verified:
```bash
# Safe to upgrade
docker pull ghcr.io/penguintechinc/elder-api:v4.0.0
docker pull ghcr.io/penguintechinc/elder-worker:v4.0.0
```

### Rollback Strategy (v4.0.0 → v3.1.0)
If critical issues discovered:
```bash
# Rollback to v3.1.0 with legacy fallback still available
docker pull ghcr.io/penguintechinc/elder-api:v3.1.0
docker pull ghcr.io/penguintechinc/elder-worker:v3.1.0
# Client code can temporarily use ?legacy=true while making permanent changes
```

---

# v2.1.0 ✅ COMPLETED
## Integrations
- iBoss API integration and discovery to pull networking, user, applications, and other metadata (Read only)
- VMWare VCenter API integration and discovery to pull compute, users, storage, networks from it (Read only)
- FleetDM Endpoint Manager API to pull hosts, software inventory, vulnerabilities, and policies
- LDAP connector verified for pulling identities / users
- Automatic relationship creation between entities (users→groups, vulns→hosts, software→hosts)

---

# v2.2.0 - Enterprise Edition

## Overview

Major release transforming Elder into a full Enterprise multi-tenant platform with 3-tier permissions, SSO/SAML/SCIM at multiple levels, comprehensive audit logging, and production monitoring. Fully backwards compatible with existing v2.x deployments through default System tenant.

**Target Use Cases:** Hosting companies, Managed Service Providers (MSPs), Companies with subsidiaries

---

## Core Architecture

### Multi-Tenancy with Default System Tenant

**Always-on multi-tenancy** - no feature flags, one code path:

- `tenant_id=1` = "System" tenant (auto-created on first run)
- All existing data automatically migrates to System tenant
- Single-tenant deployments simply never create additional tenants
- No breaking changes - fully backwards compatible

**Database Migration:**
```sql
-- System tenant created first
INSERT INTO tenants (id, name, slug) VALUES (1, 'System', 'system');

-- All existing tables get tenant_id with default
ALTER TABLE organizations ADD COLUMN tenant_id INT DEFAULT 1 REFERENCES tenants(id);
-- (same pattern for all tables)
```

### 3-Tier Permission Model

```
Global (Elder Platform)
├── Tenant (Company/Customer)
│   └── Organization (Department/Team)
│       └── Entities, Issues, etc.
```

**Roles at each tier:**

| Tier | Admin | Maintainer | Reader |
|------|-------|------------|--------|
| **Global** | Platform management, all tenants | - | Support access across tenants |
| **Tenant** | Tenant config, users, integrations, all orgs | CRUD all org resources | View all org resources |
| **Organization** | Org settings | CRUD org resources | View org resources |

**Permission Inheritance:**
- Global Admin → Full access to all tenants → all orgs
- Tenant Admin → Full access within tenant → all orgs in tenant
- Org Admin → Full access to assigned org(s) only

**Portal User Model:**
```python
portal_users:
  - id, email, password_hash
  - tenant_id (required - which company)
  - global_role (optional - platform-wide: admin, support)
  - tenant_role (optional - tenant-wide: admin, maintainer, reader)
  - org_assignments: [{org_id, role}] (optional - org-scoped)
```

### IdP Configuration at Multiple Levels

**Global IdP:** Platform operator's identity provider (e.g., MSP staff)
**Tenant IdP:** Per-customer identity provider (e.g., customer's Okta/Azure AD)

**Example MSP Scenario:**
```
Global IdP: Okta (MSP internal staff)
├── Tenant "Customer A": Azure AD
├── Tenant "Customer B": Google Workspace
└── Tenant "Customer C": No IdP (local auth)
```

**Login Flow:**
1. User enters email
2. System checks: Global IdP match? → Tenant IdP match? → Local auth
3. Redirects to appropriate IdP or shows password form

**Admin UI Visibility:**
- Global Admin: Sees global IdP + all tenant IdPs
- Tenant Admin: Sees only their tenant's IdP config

---

## Phase 1: Database Schema & Tenant Foundation

### New Tables

**tenants:**
- id (PK), name, slug, domain
- subscription_tier, license_key
- settings (JSONB), feature_flags (JSONB)
- data_retention_days, storage_quota_gb
- created_at, updated_at, is_active

**portal_users:**
- id (PK), tenant_id (FK)
- email, password_hash, mfa_secret
- global_role, tenant_role
- is_active, email_verified
- last_login, failed_attempts
- created_at, updated_at

**portal_user_org_assignments:**
- portal_user_id (FK), organization_id (FK)
- role (admin/maintainer/reader)

**idp_configurations:**
- id (PK), tenant_id (FK, nullable for global)
- idp_type (saml/oidc)
- name, entity_id, metadata_url
- sso_url, slo_url, certificate
- attribute_mappings (JSONB)
- jit_provisioning_enabled
- default_role, is_active

**scim_configurations:**
- id (PK), tenant_id (FK)
- endpoint_url, bearer_token
- sync_groups, is_active

### Schema Updates

All existing tables add: `tenant_id INT DEFAULT 1 REFERENCES tenants(id)`

Tables affected:
- organizations, entities, dependencies
- identities, identity_groups
- issues, issue_labels, issue_comments
- projects, milestones
- webhooks, api_keys
- discovery_jobs, sync_logs
- secret_providers, key_providers, builtin_secrets
- iam_providers, google_workspace_providers
- networking_resources, network_topology
- backup_jobs, metadata_fields
- resource_roles, audit_logs

### Row-Level Security (RLS)

PostgreSQL RLS policies for tenant isolation:
```sql
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON organizations
  USING (tenant_id = current_setting('app.current_tenant_id')::INT);
```

---

## Phase 2: Authentication & Portal Users

### Portal User Management

- Registration with email verification
- Password requirements (complexity, history)
- Account lockout after failed attempts
- Password reset flow
- Impersonation for support (audit logged)

### Session Management

- JWT tokens with tenant_id claim
- Configurable session timeout per tenant
- Concurrent session limits
- Device/browser tracking
- Session revocation (individual/bulk)
- Remember device functionality

### MFA Support

- TOTP (Google Authenticator, Authy)
- WebAuthn/FIDO2 (security keys)
- Backup codes
- Per-tenant MFA enforcement
- Per-role MFA requirements
- Conditional MFA (risk-based)

---

## Phase 3: SSO/SAML/SCIM Implementation

### SAML 2.0

- Global and tenant-level IdP configuration
- Multiple IdPs per tenant (future)
- SP-initiated and IdP-initiated SSO
- SAML attribute mapping UI
- Signed assertions, encrypted responses
- Single Logout (SLO)
- IdP metadata auto-refresh

### SCIM 2.0 Provisioning

- Full SCIM 2.0 server implementation
- User provisioning/deprovisioning
- Group provisioning with membership sync
- Bulk operations
- SCIM event webhooks
- Conflict resolution policies

### Just-in-Time (JIT) Provisioning

- Auto-create users on first SSO login
- Configurable default role
- Attribute-based role assignment rules
- Organization assignment from SAML groups
- Welcome email flow

### Directory Sync

- Scheduled sync from LDAP/AD
- Delta sync for efficiency
- Conflict resolution
- Sync status dashboard
- Manual sync trigger

---

## Phase 4: Audit Logging & Compliance

### Comprehensive Audit Logging

**Events captured:**
- Authentication (login, logout, failed, MFA)
- Authorization (permission checks, access denied)
- Data access (reads on sensitive data)
- Data modifications (create, update, delete)
- Configuration changes (settings, integrations)
- Admin actions (user management, role changes)
- API access (all calls with request/response)

**Audit log schema:**
```python
audit_logs:
  - event_id (UUID), tenant_id
  - timestamp, correlation_id
  - actor_user_id, actor_ip, actor_user_agent
  - action, resource_type, resource_id
  - changes (JSONB - before/after)
  - outcome (success/failure), reason
```

**Storage:**
- Immutable append-only
- Configurable retention per tenant
- Archival to S3/GCS
- Encryption at rest

### Compliance Reporting

**Pre-built reports:**
- User access report
- Permission changes report
- Data access report
- Failed authentication report
- Admin actions report
- Integration activity report

**Compliance frameworks:**
- SOC 2 Type II evidence
- ISO 27001 controls mapping
- HIPAA audit requirements
- GDPR data access logging

**Features:**
- Report scheduling and delivery
- Export: PDF, CSV, JSON
- Saved report templates

### Data Governance

- Classification labels (public, internal, confidential, restricted)
- Retention policies with automated enforcement
- Data deletion workflows
- Right to erasure (GDPR)
- Legal hold capability

---

## Phase 5: Admin Consoles

### Super Admin Console (Global)

**Tenant Management:**
- Create/edit/delete tenants
- Tenant settings and feature flags
- License assignment and validation
- Usage dashboards per tenant
- Tenant impersonation for support

**Platform Configuration:**
- Global settings
- Global IdP configuration
- Default tenant settings template
- Feature flag management
- Maintenance mode
- System health monitoring

**User Management (cross-tenant):**
- Global user directory
- User search across tenants
- Bulk operations
- Activity monitoring

### Tenant Admin Console

**Settings:**
- General (name, logo, timezone)
- Security (session timeout, MFA policy)
- Notifications
- Data retention

**User Management:**
- User list with search/filter
- Invite users (email flow)
- Role assignment (tenant + org)
- User deactivation/reactivation
- Bulk operations

**Integration Management:**
- Connector configuration
- Sync schedules
- API key management
- Webhook configuration

**SSO Configuration:**
- IdP setup wizard
- SAML metadata upload/URL
- Attribute mapping UI
- Test SSO connection
- SCIM endpoint configuration
- JIT provisioning rules

**Audit Log Viewer:**
- Filterable log view
- Export capabilities
- Saved searches
- Alert configuration

### Support Portal Feature

Enable/disable per tenant (admin setting):

- Users with `reader` role can:
  - View all resources (read-only)
  - Create issues against any resource/entity
  - Comment on existing issues
  - Cannot modify resources
- Simplified UI for support users
- Issue templates
- SLA tracking

---

## Phase 6: Kubernetes Integration

### Kubernetes Connector Enhancement

- Multi-cluster support with registration
- Automatic namespace discovery
- Pod and container inventory
- Service and ingress mapping
- Secret metadata (no values)
- RBAC policy discovery
- Resource quota monitoring
- Network policy discovery

### Kubernetes Page

- Cluster overview tiles with health
- Drill-down to cluster view
- Searchable/filterable resource list
- Resource type sections
- Real-time status
- Relationship visualization
- Quick actions (YAML, events, logs link)

### Kubernetes Entities

- Map K8S resources to Elder entity types
- Auto-create relationships (Pod→Service, Deployment→Pod)
- Sync status per cluster
- Cluster health monitoring

---

## Phase 7: API Enhancements

### API Improvements

- **v2 API** with tenant context support
- v1 API maintained (uses System tenant)
- Cursor-based pagination
- Bulk operations (batch create/update/delete)
- Sparse fieldsets
- Complex filters (AND/OR logic)
- GraphQL API (future)

### Rate Limiting

- Per-tenant limits by subscription tier
- Per-user limits
- Per-endpoint limits
- Burst allowance
- Rate limit headers
- Webhook on limit events

### Event Streaming

**Event types:**
- Entity lifecycle (created, updated, deleted)
- Organization changes
- User events
- Sync events
- Security events

**Delivery methods:**
- Webhooks (enhanced)
- Kafka integration
- WebSocket for real-time UI
- AWS SNS/SQS
- GCP Pub/Sub

**Features:**
- CloudEvents spec compliance
- At-least-once delivery
- Event replay from timestamp

---

## Phase 8: Monitoring & Observability

### Health Endpoints

All services expose `/healthz`:
```json
{
  "status": "healthy",
  "version": "2.2.0",
  "checks": {
    "database": "ok",
    "redis": "ok",
    "disk": "ok"
  }
}
```

### Prometheus Metrics

All services expose `/metrics`:

**Standard metrics:**
- `http_requests_total{method, endpoint, status}`
- `http_request_duration_seconds{method, endpoint}`
- `http_requests_in_progress`

**Custom metrics:**
- `elder_entities_total{tenant_id, entity_type}`
- `elder_api_calls_total{tenant_id, endpoint}`
- `elder_sync_duration_seconds{connector}`
- `elder_sync_entities_total{connector, status}`
- `elder_active_sessions{tenant_id}`
- `elder_audit_events_total{tenant_id, action}`

### Prometheus Service

Docker Compose addition:
```yaml
prometheus:
  image: prom/prometheus:latest
  container_name: elder-prometheus
  ports:
    - "9090:9090"
  volumes:
    - ./infrastructure/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
    - prometheus_data:/prometheus
  command:
    - '--config.file=/etc/prometheus/prometheus.yml'
    - '--storage.tsdb.retention.time=15d'
```

**Scrape targets:**
- API: localhost:5000/metrics
- Web: localhost:3005/metrics
- Worker: localhost:8000/metrics
- gRPC: localhost:50051/metrics

### Grafana Dashboards

Pre-built dashboards:
- Platform Overview
- Per-Tenant Usage
- API Performance
- Worker Sync Status
- Security Events

---

## Phase 9: High Availability & Performance

### High Availability

- **Database:** PostgreSQL streaming replication
- **Cache:** Redis Sentinel or Cluster
- **Application:** Horizontal scaling, load balancer
- **Sessions:** Distributed store (Redis)
- **Files:** S3/GCS multi-region

### Disaster Recovery

- Automated backups (database, files, config)
- Point-in-time recovery
- Cross-region backup replication
- Backup encryption
- Recovery testing automation
- RTO/RPO documentation

### Performance Optimization

**Database:**
- Query optimization, indexing
- Connection pooling (PgBouncer)
- Read replicas for reporting
- Partitioning (audit_logs)

**Caching:**
- Multi-level (application, Redis)
- Tenant-aware caching
- Cache invalidation strategies

**Async Processing:**
- Background job queue (Celery/RQ)
- Scheduled jobs management
- Job monitoring and retry

---

## Phase 10: License Server Integration

### License Validation

- Startup validation
- Periodic keepalive
- Grace period for lapses
- Feature degradation on expiry

### License Model - Tenant-Based Pricing

**Key Principle:** License fees based on number of tenants (max_tenants from license server)

**Enterprise is All-or-Nothing:**
- Pay for all tenant slots, even if unused
- Enterprise license key required for SSO/SAML/SCIM
- No à la carte feature purchasing

### License Tiers

**Community (Free):**
- Single tenant (System only, max_tenants=1)
- Local authentication only
- Basic RBAC (3 roles)
- 100 entity limit
- 30-day audit retention
- No SSO/SAML/SCIM

**Professional ($99/mo):**
- Single tenant (max_tenants=1)
- Local authentication only
- Enhanced RBAC (custom roles)
- Unlimited entities
- 1-year audit retention
- 1000 req/min API limit
- No SSO/SAML/SCIM

**Enterprise (Custom pricing based on tenant count):**
- Multi-tenant (max_tenants from license)
- SSO/SAML/SCIM enabled
- 3-tier permissions
- Compliance reporting
- Unlimited audit retention
- 10000 req/min API limit
- Priority support

### License Server Response

```json
{
  "valid": true,
  "tier": "enterprise",
  "features": [
    {"name": "sso_saml", "entitled": true},
    {"name": "scim", "entitled": true},
    {"name": "multi_tenant", "entitled": true}
  ],
  "limits": {
    "max_tenants": 50,
    "max_users": -1
  }
}
```

### Usage Reporting

- Tenant count (active vs licensed)
- Entity count per tenant
- API call volume
- Storage usage
- User count
- Feature usage analytics

---

## Migration & Compatibility

### Migration Path

- Automatic System tenant creation
- Existing data assigned to System tenant
- Role mapping (existing → new RBAC)
- Zero-downtime migration support
- Rollback procedures

### API Compatibility

- v1 API: Unchanged, uses System tenant
- v2 API: Adds tenant context
- Migration guide for v1→v2
- SDK updates

### Breaking Changes

**None for existing deployments:**
- v1 API continues working
- Existing data in System tenant
- Current auth still functional

---

## Implementation Phases

### Phase 1-2: Foundation
- Database schema changes
- Tenant management
- Portal users
- Session management

### Phase 3-4: Authentication & Compliance
- SSO/SAML/SCIM
- MFA policies
- Audit logging
- Compliance reporting

### Phase 5-6: Features
- Admin consoles
- Support Portal
- Kubernetes integration
- K8S page

### Phase 7-8: API & Monitoring
- v2 API
- Rate limiting
- Event streaming
- Prometheus metrics
- Health endpoints

### Phase 9-10: Production & Licensing
- HA/DR
- Performance optimization
- License integration
- Migration tools

---

## Success Criteria

- [ ] Default System tenant works for existing deployments
- [ ] 3-tier permissions functioning correctly
- [ ] Global and tenant IdP configuration working
- [ ] SSO with major IdPs (Okta, Azure AD, Google)
- [ ] SCIM provisioning tested
- [ ] Audit logs meet SOC 2 requirements
- [ ] Prometheus metrics on all services
- [ ] Sub-200ms API response time (p95)
- [ ] Zero cross-tenant data leakage
- [ ] Migration from v2.1.x without data loss

---

## Dependencies

- PostgreSQL 15+ (RLS policies)
- Redis 7+ (distributed sessions)
- Prometheus (metrics collection)
- License Server (PenguinTech)
- S3-compatible storage (audit archives)

---
# Future Versions

## v2.3.0
### Integrations
- Dedicated scanner container with:
  - HTTP Screenshot scan (headless browser captures of web services, similar to httpscreenshot)
  - masscan for high-speed port scanning
  - banner grabber similar to what netcat does for banner grabbing on known ports (ssh, etc.)
- Cloud networking auto-discovery (AWS/GCP VPCs, subnets, etc.)

### Linking
Update the entity linking page to include all resources, not just the entity types... that is the main point of this after all.
For example let me link any security risks to any Identity, VPC, network, compute, etc.

### Models
- Network IPAM section with collapsible tree CIDR superset views
- Full IPAM features (inspired by NIPAP https://spritelink.github.io/NIPAP/)
#### Software 
- add a software page / model under the tracking category
- have it track things like Purchasing POC (who bought it), License or Contract Link, version , business purpose, type, seats, and notes fields
- make the Purchasing POC field a search field for identities within Elder, auto link the two once set
- Make the default url for License link to the GNU AGPL3 url but let the user with write permissions be able to change it 
- Make the type a drop down of: SaaS, PaaS, IaaS, Productivity, Software, Administrative, and other types you would commonly expect here (opening this up for your awarenes to add more here Claude)
- Put validation to ensure seats is an integer

#### Services
- add a Services page / model under the tracking category
- have it track things like  domain association, path, POC, language of the service, deployment method, deployment type, if it is public or not, notes, SLAs , and any other common micro-service or service tracking ((opening this up for your awarenes to add more here Claude))
- The domain association is something like app.example.com or somedomain.com or an IP4/6 address, we should have a plus sign to add more and store these as an array on the backend
- The path is the part after the domain, so something like /some/api/endpoint, we should have plus sign to add more then one and store these as an array on the backend
- The POC should be an identity or OU and be a search field, auto link them when set
- The language should be all of the common programming languages, for example: Rust, GoLang, Python2/3, NodeJS, ReactJS, C, C++, PHP, etc. (opening this up for your awarenes to add more here Claude)
- The deployment method should be one of the following drop down selections: Serverless, Kubernetes, Docker/Docker Compose, OS Local, Function (ie: Cloud Run / Lambda), Other
- The public should be essentially boolean for now


# v3.0.0

## Models

### ✅ Group Membership Management (COMPLETED)
- Full group management feature with approvals and group management through Elder
- Configurable approval workflows (any, all, or N-of-M approvals required)
- Access request workflow with justification and optional expiration dates
- Owner review dashboard for approving/denying pending requests
- Bulk approve/deny operations
- Background tasks for expired membership cleanup
- Enterprise license-gated feature
- Audit logging for all membership changes

**Database Tables Added:**
- Extended `identity_groups`: owner_id, owning_group_id, approval_workflow, min_approvals, sync_enabled, sync_provider, external_group_id
- Extended `identity_group_memberships`: requested_at, approved_at, approved_by, expires_at, membership_status
- New `group_access_requests`: status, justification, requested_expires_at, decided_at, decided_by, decision_notes
- New `group_access_approvals`: for multi-approval workflows

**API Endpoints (13):**
- GET/POST /group-membership/groups - List/configure managed groups
- GET/PUT /group-membership/groups/<id> - Get/update group settings
- POST /group-membership/groups/<id>/requests - Submit access request
- GET /group-membership/groups/<id>/requests - List requests for group
- GET /group-membership/requests/pending - Owner's pending requests dashboard
- POST /group-membership/requests/<id>/approve - Approve request
- POST /group-membership/requests/<id>/deny - Deny request
- DELETE /group-membership/requests/<id> - Cancel own request
- POST /group-membership/requests/bulk-approve - Bulk approve requests
- GET /group-membership/groups/<id>/members - List group members
- POST /group-membership/groups/<id>/members - Add member directly (owners)
- DELETE /group-membership/groups/<id>/members/<identity_id> - Remove member

**Frontend Components:**
- GroupMembershipManager: Full management UI in IAM → Groups & Roles tab
- Groups list with search and filtering
- Pending requests dashboard with bulk actions
- Group details modal with member management
- Access request modal with justification and expiration
- Group settings modal for owners

---

### ✅ Data Store Tracking (COMPLETED - Community Feature)
Track sensitive data stores across your infrastructure for compliance and governance.

**Database Schema: `data_stores` table**
```
- id, village_id, tenant_id, organization_id
- name (required), description
- storage_type: enum('s3', 'gcs', 'azure_blob', 'disk', 'nas', 'san', 'database', 'data_lake', 'hdfs', 'other')
- storage_provider: string (e.g., "AWS", "GCP", "Azure", "On-Premise", "Wasabi", "Backblaze")
- location_region: string (e.g., "us-west-1", "us-east-2", "eu-west-1")
- location_physical: string (e.g., "Dallas, TX", "Frankfurt, DE")
- data_classification: enum('public', 'internal', 'confidential', 'restricted')
- encryption_at_rest: boolean
- encryption_in_transit: boolean
- encryption_key_id: FK to crypto_keys (optional)
- retention_days: integer (nullable, for compliance)
- backup_enabled: boolean
- backup_frequency: string (e.g., "daily", "hourly")
- access_control_type: enum('iam', 'acl', 'rbac', 'public', 'private')
- poc_identity_id: FK to identities (point of contact / data steward)
- compliance_frameworks: JSONB array (e.g., ["SOC2", "HIPAA", "GDPR", "PCI-DSS"])
- size_bytes: bigint (estimated or actual)
- contains_pii: boolean
- contains_phi: boolean (protected health info)
- contains_pci: boolean (payment card data)
- last_access_audit: timestamp
- metadata: JSONB
- created_at, updated_at, created_by, is_active
```

**Data Store Labels: `data_store_labels` table**
```
- id, data_store_id (FK), label_id (FK to labels table)
- Enables user-defined labeling (e.g., type=PII, department=HR, sensitivity=high)
```

**API Endpoints (8):**
- GET /data-stores - List all data stores (with filtering by classification, type, region)
- POST /data-stores - Create new data store
- GET /data-stores/<id> - Get data store details
- PUT /data-stores/<id> - Update data store
- DELETE /data-stores/<id> - Delete data store
- GET /data-stores/<id>/labels - Get labels for data store
- POST /data-stores/<id>/labels - Add labels to data store
- DELETE /data-stores/<id>/labels/<label_id> - Remove label from data store

**Frontend: Data Stores Page**
- Card-based display with classification badges (color-coded: green=public, yellow=internal, orange=confidential, red=restricted)
- Filtering by classification, storage type, region, compliance status
- Detail modal showing full data store information
- Create/edit modal using ModalFormBuilder
- PII/PHI/PCI indicator badges
- Compliance framework tags
- Link to data steward (identity)

---

## Integrations

### ✅ LDAP Bidirectional Group Sync (COMPLETED)
- GroupOperationsMixin interface in ldap_connector.py
- Methods: add_member_to_group, remove_member_from_group, sync_group_membership
- Uses ldap3 library with MODIFY_ADD and MODIFY_DELETE operations
- Supports memberOf overlay and groupOfNames/groupOfUniqueNames schemas

### ✅ Okta Bidirectional Group Sync (COMPLETED)
- Full OktaConnector with sync + write-back
- Sync: Pull users and groups from Okta organization
- Write-back: PUT /groups/{groupId}/users/{userId} (add), DELETE (remove)
- SSWS token authentication
- Implements GroupOperationsMixin interface
- Configurable via settings: okta_enabled, okta_domain, okta_api_token, okta_write_back_enabled

### ✅ Authentik Bidirectional Group Sync (COMPLETED)
Authentik is an open-source identity provider that supports OIDC, SAML, LDAP, and SCIM.

**AuthentikConnector Implementation:**
- File: `apps/worker/connectors/authentik_connector.py` (474 lines)
- Extends BaseConnector and GroupOperationsMixin
- Bearer token authentication via API v3

**Configuration Settings:** Added to `apps/worker/config/settings.py`:
- `authentik_enabled`, `authentik_domain`, `authentik_api_token`
- `authentik_sync_interval`, `authentik_sync_users`, `authentik_sync_groups`
- `authentik_write_back_enabled`, `authentik_verify_ssl`

**Sync Capabilities:**
- Users: GET /api/v3/core/users/ with pagination
- Groups: GET /api/v3/core/groups/ with pagination
- Group Members: Included in group response (users_obj field)

**Write-Back Capabilities:**
- Add member: POST /api/v3/core/groups/{group_pk}/add_user/ with {"pk": user_pk}
- Remove member: POST /api/v3/core/groups/{group_pk}/remove_user/ with {"pk": user_pk}

**Entity Mappings:**
- Users → identity (identity_type: employee/serviceAccount based on attributes)
- Groups → identity_group

---

### ✅ OIDC SSO Support (COMPLETED)
OpenID Connect support alongside existing SAML for SSO authentication.

**OIDCService Implementation:**
- File: `apps/api/services/sso/oidc_service.py` (496 lines)
- OIDC Discovery (.well-known/openid-configuration)
- Authorization Code Flow with JWT validation
- Just-in-Time (JIT) user provisioning
- Token refresh capability
- RP-Initiated Logout support

**Database Schema:** Added to `idp_configurations` table:
- `oidc_client_id`, `oidc_client_secret`, `oidc_issuer_url`
- `oidc_scopes`, `oidc_response_type`, `oidc_token_endpoint_auth_method`

**API Endpoints (5):**
- GET /sso/oidc/authorize/<idp_id> - Initiate OIDC login
- GET /sso/oidc/callback - Handle callback with code exchange
- POST /sso/oidc/logout/<idp_id> - OIDC logout
- GET /sso/oidc/userinfo/<idp_id> - Get user info from IdP
- POST /sso/oidc/refresh/<idp_id> - Refresh access token

**Supported Providers:**
- Google Workspace, Microsoft Azure AD/Entra ID, Okta, Auth0
- Keycloak, GitLab, GitHub, and any OIDC-compliant provider

---

# v3.1.0 (Future)

## Enhanced Data Governance
- Data lineage tracking (source → transformations → destinations)
- Data flow visualization on Map page
- Automated PII/PHI detection suggestions based on naming patterns
- Data retention policy enforcement with alerts
- Cross-border data transfer compliance warnings

## Additional Identity Provider Connectors
- **Azure AD/Entra ID**: Groups bidirectional sync via Microsoft Graph API
- **JumpCloud**: Directory sync and group management
- **OneLogin**: SCIM-based user/group provisioning

## Group Nesting Support
- Nested group hierarchies (groups containing groups)
- Transitive membership resolution
- Circular dependency detection
- Nested group visualization in IAM page

## Underlying Promethesus and Alert Manager... managmenet
- Expose the dashboards and statuses via Elder webui of the packaged Promethesus for Global Admins
- Expose the features, config, etc. of Promethesus Alert Manager for global admins in Admin section for Elder WebUI too.
