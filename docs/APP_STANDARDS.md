# Elder Application Standards

**Application-specific standards and implementation details for Elder.**

This document extends the [company-wide standards](STANDARDS.md) with Elder-specific architecture, RBAC configuration, and implementation patterns.

---

## Microservices Architecture

Elder implements a **stateless, multi-container microservices architecture** with specialized role separation for cloud discovery, local scanning, and REST API operations.

### Service Roles & Responsibilities

**API Service** (Flask backend, port 5000)
- REST CRUD endpoints for all domain resources (entities, organizations, services, etc.)
- Discovery job management (create, update, delete, list, test)
- Job queuing via `POST /api/v1/discovery/jobs/<id>/run` (sets `next_run_at = now()`, returns 202)
- Scanner job status queries (fetch pending jobs for scanner)
- Discovery history tracking and reporting
- Legacy discovery execution via `?legacy=true` query parameter (deprecated, sunset v4.0.0)

**Worker Service** (Python background service, no public ports)
- Direct PostgreSQL polling of `discovery_jobs` table every 5 minutes
- Executes cloud discovery jobs (AWS, GCP, Azure, Kubernetes) directly against cloud provider APIs
- Uses least-privilege DB account `elder_worker` with read-only access to config
- Writes discovery results and history directly to PostgreSQL
- No PVC — state persisted entirely to database
- Credentials sourced from K8s Secrets at `/var/run/secrets/elder/` (e.g., `AWS_ROLE_ARN`, `GCP_SERVICE_ACCOUNT`)

**Scanner Service** (Python background service, ephemeral storage)
- HTTP polling of `GET /api/v1/discovery/jobs/pending` every 5 minutes
- Executes local network scans:
  - `network` — network discovery (NMAP, ARP)
  - `banner` — service banner grabbing (TCP port enumeration)
  - `http_screenshot` — HTTP endpoint screenshots (Playwright, Firefox)
- Uploads screenshots to API or persists to ephemeral emptyDir/tmpfs
- Uses least-privilege DB account `elder_scanner`
- No PVC — screenshots stored in ephemeral memory or temporary storage

### Database Account Separation

Elder enforces least-privilege database access via separate PostgreSQL accounts:

| Account | Service | Permissions | Purpose |
|---------|---------|-------------|---------|
| `elder` | API | Full read/write | REST API operations, all tables |
| `elder_worker` | Worker | `discovery_jobs`, `discovery_history` read/write | Cloud discovery job polling & result storage |
| `elder_scanner` | Scanner | `discovery_jobs` read, `discovery_history` write | Local scan job polling & history recording |

Each account password is provisioned via K8s Secrets and passed through environment variables during container startup.

---

## Database Access Patterns

Elder optimizes database performance through **primary/replica read splitting** and specialized query patterns for each service.

### Primary/Replica Split

```
PostgreSQL Cluster
├── Primary (DATABASE_URL) — All writes, transactional reads
└── Read Replica (DATABASE_READ_URL) — Analytical & reporting reads
```

**Configuration**:
```bash
# Primary connection (writes, transactional)
DATABASE_URL=postgres://elder:password@db-primary:5432/elder

# Read replica (read-heavy queries)
DATABASE_READ_URL=postgres://elder:password@db-replica:5432/elder
```

### Read-Heavy Endpoints (Use Read Replica)

The following API endpoints use `DATABASE_READ_URL` for horizontal scaling:

- `GET /api/v1/entities` — Entity listing with filtering
- `GET /api/v1/entities/search` — Full-text entity search
- `GET /api/v1/entities/<id>/graph` — Entity dependency graph visualization
- `GET /api/v1/discovery/history` — Discovery execution history
- Analytics and reporting endpoints

### Write Operations (Always Primary)

- `POST /api/v1/entities` — Entity creation
- `POST /api/v1/discovery/jobs/<id>/run` — Job queuing
- `PATCH /api/v1/entities/<id>` — Entity updates
- `DELETE /api/v1/entities/<id>` — Entity deletion
- All discovery result storage

### Worker Discovery Execution Pattern

The **DiscoveryExecutor** in the worker service uses split connections:

```python
# Polling for pending jobs (uses read replica if available)
jobs = db_read(
    (db_read.discovery_jobs.enabled == True) &
    (db_read.discovery_jobs.provider.belongs(['aws', 'gcp', 'azure', 'kubernetes']))
).select()

# Writing results (always primary)
db_write.discovery_history.insert(
    job_id=job_id,
    status='completed',
    results_json=discovery_results
)
db_write.commit()
```

---

## Stateless Container Design

All Elder containers follow a **stateless, shared-nothing architecture** with state persisted exclusively to PostgreSQL.

### Storage Layout

| Container | Mount | Purpose | Persistence |
|-----------|-------|---------|-------------|
| API | N/A | REST endpoints only | DB-backed |
| Worker | K8s Secrets `/var/run/secrets/elder/` | Cloud credentials (mounted read-only) | None (ephemeral) |
| Scanner | emptyDir `/tmp/screenshots` | HTTP screenshot storage (ephemeral) | None (discarded on pod restart) |

### Credential Management

All services retrieve credentials via **K8s Secrets** mounted as read-only volumes:

```yaml
# kubernetes deployment volume mounts
- name: elder-secrets
  mountPath: /var/run/secrets/elder/
  readOnly: true
```

**Secrets provisioned**:
- `AWS_ROLE_ARN` — IAM role for AWS discovery
- `GCP_SERVICE_ACCOUNT_JSON` — Service account key for GCP
- `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
- `KUBECONFIG` — Kubeconfig for Kubernetes discovery

**No hardcoded credentials** — all sourced from K8s Secrets at runtime.

### Pod Restarts & Data Loss

Stateless design ensures **zero data loss on pod restart**:

- **API restart** → Session cache flushed, all data in DB, request resumption seamless
- **Worker restart** → Pending jobs remain in `discovery_jobs`, poll cycle resumes next 5-min interval
- **Scanner restart** → Pending jobs remain in `discovery_jobs`, screenshots regenerated on next scan cycle

---

## Discovery Execution

Elder's **job queuing and execution** model separates API job management from background worker execution.

### Execution Flow

```
1. API receives: POST /api/v1/discovery/jobs/<id>/run
   ↓
2. API queues job: UPDATE discovery_jobs SET next_run_at = NOW()
   ↓
3. API returns: 202 Accepted (job queued, not executed)
   ↓
4. Worker polls: SELECT * FROM discovery_jobs WHERE next_run_at <= NOW()
   ↓
5. Worker executes: DiscoveryService.run_discovery(job_id)
   ↓
6. Worker stores: INSERT INTO discovery_history, UPDATE discovery_jobs.last_run_at
```

### Asynchronous Job Execution

- **API call returns immediately** (202 Accepted) — no long-running discovery in request context
- **Worker polls every 5 minutes** — executes pending cloud jobs asynchronously
- **Client polls history** — checks `GET /api/v1/discovery/history?job_id=<id>` for results
- **Prevents timeout** — discovery operations (especially multi-cloud) can take minutes

### Scanner Job Polling

Scanner service polls the API for local scan jobs:

```python
# Scanner polls API
GET /api/v1/discovery/jobs/pending
→ Returns: [{id, provider: "network"}, {id, provider: "banner"}, ...]

# Scanner executes
NetworkScanner().scan(targets)
BannerScanner().scan(targets)
HTTPScreenshotScanner().scan(targets)

# Scanner reports results back to API
POST /api/v1/discovery/jobs/<id>/complete
  {success: true, results: {...}}
```

### Job Scheduling

Discovery jobs support **interval-based scheduling**:

```json
{
  "id": 42,
  "name": "AWS Production Discovery",
  "provider": "aws",
  "schedule_interval": 3600,
  "last_run_at": "2026-02-23T10:00:00Z",
  "next_run_at": "2026-02-23T11:00:00Z"
}
```

- `schedule_interval = 0` → One-time job (execute once, no reschedule)
- `schedule_interval > 0` → Repeating job (execute every N seconds)
- Worker calculates `next_run_at = last_run_at + schedule_interval`

### Deprecation Timeline

**v3.1.0 (Current)**
- API discovery execution deprecated (marked with warning logs)
- Worker handles all cloud discovery jobs (AWS, GCP, Azure, K8s)
- Legacy synchronous execution available via `?legacy=true` query parameter
- API endpoints accept but discourage `/api/v1/discovery/jobs/<id>/run?legacy=true`

**v4.0.0 (Next Major)**
- API discovery execution removed entirely
- `DiscoveryService.run_discovery()` removed
- Only worker-based async execution supported
- All legacy query parameters ignored

---

## RBAC Implementation

Elder implements a **three-tier Role-Based Access Control (RBAC)** system providing granular permissions at global, tenant, and resource levels.

### Permission Model Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Global/Platform Level                     │
│  identities.portal_role: admin | editor | observer          │
│  portal_users.global_role: admin | support                  │
├─────────────────────────────────────────────────────────────┤
│                      Tenant Level                            │
│  portal_users.tenant_role: admin | maintainer | reader      │
├─────────────────────────────────────────────────────────────┤
│                     Resource Level                           │
│  resource_roles.role: maintainer | operator | viewer        │
│  (per entity or organization)                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Role Reference Tables

### Global/Platform Roles

**Table: `identities.portal_role`**

| Role | Description | Capabilities |
|------|-------------|--------------|
| `admin` | System administrator | Full system access, user management, all features |
| `editor` | Content editor | Read/write access to resources, no user management |
| `observer` | Read-only user | View-only access, default for new users |

**Table: `portal_users.global_role`** (Platform-wide, optional)

| Role | Description | Capabilities |
|------|-------------|--------------|
| `admin` | Platform administrator | Cross-tenant access, system configuration |
| `support` | Support staff | View user data, assist with issues across tenants |

### Tenant Roles

**Table: `portal_users.tenant_role`** (Scoped to tenant)

| Role | Description | Capabilities |
|------|-------------|--------------|
| `admin` | Tenant administrator | Full tenant access, user management within tenant |
| `maintainer` | Tenant maintainer | Read/write within tenant, no user management |
| `reader` | Tenant reader | Read-only access within tenant |

### Resource-Level Roles

**Table: `resource_roles`** (Per entity/organization)

| Role | Level | Description | Capabilities |
|------|-------|-------------|--------------|
| `maintainer` | 3 (highest) | Resource owner | Full CRUD, manage resource roles, delete |
| `operator` | 2 | Active user | Create/close issues, add comments/labels, read metadata |
| `viewer` | 1 (lowest) | Observer | View resource, create issues, add comments |

**Role Hierarchy**: `viewer` < `operator` < `maintainer`

A user with `maintainer` role automatically has all permissions of `operator` and `viewer`.

---

## Scope System

### OAuth2-Style Scopes

Elder uses OAuth2-style scopes for fine-grained API permissions:

```
{resource}:{action}
```

**Examples**: `users:read`, `entities:write`, `reports:admin`

### Scope Hierarchy

**Hierarchical Scopes** (higher includes lower):
```
entity:admin  →  entity:write  →  entity:read
     ↓              ↓               ↓
(delete, manage)  (create, update)  (view)
```

**Non-Hierarchical Scopes** (independent):
```
role:assign    (assign roles to users)
role:revoke    (revoke roles from users)
audit:read     (view audit logs)
```

### Role-to-Scope Mappings

| Role | Scopes |
|------|--------|
| `admin` | `*:*` (all scopes) |
| `editor` | `entities:read`, `entities:write`, `issues:*`, `comments:*` |
| `observer` | `entities:read`, `issues:read`, `comments:read` |
| `maintainer` (resource) | `{resource}:admin` |
| `operator` (resource) | `{resource}:write`, `{resource}:read` |
| `viewer` (resource) | `{resource}:read` |

---

## Database Schema

### RBAC Tables

**`identities`** (Global user records)
```sql
portal_role     VARCHAR(20)   -- 'admin', 'editor', 'observer'
is_superuser    BOOLEAN       -- Bypasses all permission checks
```

**`portal_users`** (Multi-tenant portal users)
```sql
tenant_id       INTEGER       -- FK to tenants
global_role     VARCHAR(50)   -- 'admin', 'support' (platform-wide, optional)
tenant_role     VARCHAR(50)   -- 'admin', 'maintainer', 'reader' (tenant-scoped)
```

**`resource_roles`** (Per-resource permissions)
```sql
identity_id     INTEGER       -- FK to identities (user)
group_id        INTEGER       -- FK to identity_groups (optional, for group permissions)
role            VARCHAR(50)   -- 'maintainer', 'operator', 'viewer'
resource_type   VARCHAR(50)   -- 'entity', 'organization'
resource_id     INTEGER       -- ID of the specific resource
```

---

## Authorization Decorators

Elder provides decorators in `apps/api/auth/decorators.py`:

### `@login_required`
Requires authenticated user (any role).

```python
@bp.route('/api/v1/profile')
@login_required
def get_profile():
    return jsonify(g.current_user.to_dict())
```

### `@role_required(roles)`
Requires specific portal role(s).

```python
@bp.route('/api/v1/users', methods=['POST'])
@login_required
@role_required('admin')
def create_user():
    # Only admins can create users
    pass

@bp.route('/api/v1/reports')
@login_required
@role_required(['admin', 'editor'])
def list_reports():
    # Admins and editors can list reports
    pass
```

### `@admin_required`
Shorthand for `@role_required('admin')`.

```python
@bp.route('/api/v1/system/config')
@login_required
@admin_required
def get_system_config():
    pass
```

### `@resource_role_required(role, resource_param)`
Requires role on specific resource.

```python
@bp.route('/api/v1/entities/<int:id>/metadata', methods=['POST'])
@login_required
@resource_role_required('maintainer', resource_param='id')
def create_metadata(id):
    # Only maintainers of this entity can create metadata
    pass

@bp.route('/api/v1/entities/<int:id>/issues', methods=['POST'])
@login_required
@resource_role_required('viewer', resource_param='id')
def create_issue(id):
    # Viewers can create issues (lowest role)
    pass
```

---

## Permission Check Flow

```
Request → @login_required → @role_required → @resource_role_required → Handler
             │                   │                    │
             ▼                   ▼                    ▼
         Is user             Does user            Does user have
         authenticated?      have portal          role on this
                            role?                 resource?
```

**Superuser Bypass**: Users with `is_superuser=True` bypass all permission checks after authentication.

---

## JWT Token Structure

```json
{
  "sub": "user_123",
  "email": "user@example.com",
  "portal_role": "editor",
  "tenant_id": 1,
  "tenant_role": "maintainer",
  "is_superuser": false,
  "scopes": ["entities:read", "entities:write", "issues:*"],
  "exp": 1704067200,
  "iat": 1704063600
}
```

---

## Custom Roles

Elder supports custom roles with selected scope combinations:

```python
# Create custom role via API
POST /api/v1/roles/custom
{
  "name": "report_viewer",
  "description": "Can only view reports",
  "scopes": ["reports:read", "analytics:read"]
}
```

Custom roles are stored in the `roles` table and assigned via `user_roles`.

---

## Common Permission Patterns

### Tenant Isolation
```python
def get_tenant_data():
    tenant_id = g.current_user.tenant_id
    return db(db.entities.tenant_id == tenant_id).select()
```

### Resource Ownership Check
```python
def can_modify_entity(entity_id):
    user = g.current_user
    if user.is_superuser:
        return True
    role = db(
        (db.resource_roles.identity_id == user.id) &
        (db.resource_roles.resource_type == 'entity') &
        (db.resource_roles.resource_id == entity_id)
    ).select().first()
    return role and role.role in ['maintainer', 'operator']
```

---

## Deployment & Testing

### Beta Cluster Access

The Elder beta cluster (`dal2-beta`) is deployed on the private PenguinTech data center at `dal2.penguintech.io`. Due to Cloudflare proxying, direct access requires using a bypass URL with Host header:

**Bypass URL with Host Header**:
```bash
curl -k -X GET "https://dal2.penguintech.io/api/v1/healthz" \
  -H "Host: elder.penguintech.io"
```

This approach:
- Uses the load balancer origin URL (`dal2.penguintech.io`)
- Sets the proper Host header for ingress routing (`elder.penguintech.io`)
- Bypasses Cloudflare proxying issues
- Works with kubectl port-forward and curl testing

**Deployment**:
```bash
# Deploy API and Web to beta cluster
./scripts/deploy-beta.sh all

# Deploy only API
./scripts/deploy-beta.sh api

# Rollout only (no rebuild)
./scripts/deploy-beta.sh -r all
```

**Smoke Tests**:
```bash
# Run smoke tests against beta cluster
./scripts/smoke-test.sh --beta

# Verbose output
./scripts/smoke-test.sh --beta -v
```

The smoke-test.sh script automatically uses the bypass URL and Host header for beta testing.

### Single-Tenant Deployment

Elder supports single-tenant deployments via automatic fallback to "default" tenant:

- During login, if no tenant is specified, the system falls back to a "default" tenant
- Default tenant is created automatically during initialization if it doesn't exist
- Portal authentication endpoint: `POST /api/v1/portal-auth/login`

**Login Request**:
```json
{
  "email": "admin@localhost.local",
  "password": "admin123"
}
```

The default admin user is created during initialization if `ADMIN_EMAIL` and `ADMIN_PASSWORD` environment variables are set.

---

## Related Documentation

- **[Security Standards](standards/SECURITY.md)** - RBAC concepts, scope patterns, implementation details
- **[Authentication Standards](standards/AUTHENTICATION.md)** - Flask-Security-Too integration, JWT handling
- **[Architecture Standards](standards/ARCHITECTURE.md)** - Container architecture, service roles

---

**Last Updated**: 2026-02-23
**Version**: 3.1.0
