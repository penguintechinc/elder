# Elder Application Standards

**Application-specific standards and implementation details for Elder.**

This document extends the [company-wide standards](STANDARDS.md) with Elder-specific architecture, RBAC configuration, and implementation patterns.

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
./scripts/deploy-to-beta.sh all

# Deploy only API
./scripts/deploy-to-beta.sh api

# Rollout only (no rebuild)
./scripts/deploy-to-beta.sh -r all
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

**Last Updated**: 2026-02-10
**Version**: 3.1.0
