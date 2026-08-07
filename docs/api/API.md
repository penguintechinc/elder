# Elder REST API Documentation v2.0.0

The Elder REST API provides comprehensive access to all Elder functionality through RESTful HTTP endpoints.

## Base URL

```
http://localhost:5000/api/v1
```

## Authentication

Elder supports multiple authentication methods:

- **Local Authentication**: Username/password (development)
- **API Keys**: Bearer token authentication (recommended for automation)
- **SAML/OAuth2**: Enterprise SSO (Professional/Enterprise tiers)
- **LDAP**: Directory service integration (Enterprise tier)

### Bearer Token Authentication

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
  http://localhost:5000/api/v1/entities
```

---

## Complete API Endpoint Tree

### Authentication & User Management
- `POST /api/v1/auth/login` - User login
- `POST /api/v1/auth/register` - User registration
- `POST /api/v1/auth/logout` - User logout
- `POST /api/v1/auth/refresh` - Refresh access token
- `GET /api/v1/profile` - Get current user profile
- `PUT /api/v1/profile` - Update current user profile

### Organizations (15 endpoints)
- `GET /api/v1/organizations` - List organizations
- `POST /api/v1/organizations` - Create organization
- `GET /api/v1/organizations/{id}` - Get organization details
- `PUT /api/v1/organizations/{id}` - Update organization (full)
- `PATCH /api/v1/organizations/{id}` - Update organization (partial)
- `DELETE /api/v1/organizations/{id}` - Delete organization
- `GET /api/v1/organizations/{id}/children` - Get child organizations
- `GET /api/v1/organizations/{id}/hierarchy` - Get organization hierarchy
- `GET /api/v1/organizations/{id}/entities` - Get organization entities
- `GET /api/v1/organizations/{id}/issues` - Get organization issues
- `GET /api/v1/organizations/{id}/projects` - Get organization projects
- `GET /api/v1/organizations/{id}/secrets` - Get organization secrets
- `GET /api/v1/organizations/{id}/users` - Get organization users
- `GET /api/v1/organizations/{id}/dependencies` - Get organization dependencies
- `GET /api/v1/organization-tree` - Get full organization tree

### Entities (12 endpoints)
- `GET /api/v1/entities` - List entities
- `POST /api/v1/entities` - Create entity
- `GET /api/v1/entities/{id}` - Get entity details
- `PUT /api/v1/entities/{id}` - Update entity (full)
- `PATCH /api/v1/entities/{id}` - Update entity (partial)
- `DELETE /api/v1/entities/{id}` - Delete entity
- `GET /api/v1/entities/{id}/dependencies` - Get entity dependencies
- `PATCH /api/v1/entities/{id}/attributes` - Update entity attributes
- `GET /api/v1/entities/{id}/issues` - Get entity issues
- `GET /api/v1/entities/{id}/metadata` - Get entity metadata
- `GET /api/v1/entity-types` - List available entity types
- `GET /api/v1/entity-types/{type}` - Get entity type details

### Dependencies (5 endpoints)
- `GET /api/v1/dependencies` - List all dependencies
- `POST /api/v1/dependencies` - Create dependency
- `GET /api/v1/dependencies/{id}` - Get dependency details
- `PUT /api/v1/dependencies/{id}` - Update dependency
- `DELETE /api/v1/dependencies/{id}` - Delete dependency

### Identities (6 endpoints)
- `GET /api/v1/identities` - List identities
- `POST /api/v1/identities` - Create identity
- `GET /api/v1/identities/{id}` - Get identity details
- `PUT /api/v1/identities/{id}` - Update identity
- `PATCH /api/v1/identities/{id}` - Update identity (partial)
- `DELETE /api/v1/identities/{id}` - Delete identity

### Issues (14 endpoints)
- `GET /api/v1/issues` - List issues
- `POST /api/v1/issues` - Create issue
- `GET /api/v1/issues/{id}` - Get issue details
- `PUT /api/v1/issues/{id}` - Update issue
- `PATCH /api/v1/issues/{id}` - Update issue (partial)
- `DELETE /api/v1/issues/{id}` - Delete issue
- `POST /api/v1/issues/{id}/comments` - Add comment
- `GET /api/v1/issues/{id}/comments` - Get comments
- `DELETE /api/v1/issues/{id}/comments/{comment_id}` - Delete comment
- `POST /api/v1/issues/{id}/labels` - Add label to issue
- `DELETE /api/v1/issues/{id}/labels/{label_id}` - Remove label from issue
- `POST /api/v1/issues/{id}/entities` - Link entity to issue
- `DELETE /api/v1/issues/{id}/entities/{entity_id}` - Unlink entity from issue
- `GET /api/v1/issues/{id}/timeline` - Get issue timeline

*Issues are unified: a support ticket is an Issue with `issue_type=support`
(alongside `operations`, `code`, `config`, `security`, `architecture`,
`process`, `approval`, `feature`, `bug`, `other`). Assignment is
polymorphic — `assignee_type` (`identity` or `org_unit`) plus
`assignee_id` — surfaced in the UI as one combined identity+org-unit
search picker.*

### Intake Forms (7 endpoints)
- `GET /api/v1/intake-forms` - List intake forms (admin)
- `POST /api/v1/intake-forms` - Create intake form (admin)
- `GET /api/v1/intake-forms/{id}` - Get intake form details (admin)
- `PATCH /api/v1/intake-forms/{id}` - Update intake form (admin)
- `DELETE /api/v1/intake-forms/{id}` - Delete intake form (admin)
- `GET /api/v1/intake/{slug}` - Get public form definition (unauthenticated)
- `POST /api/v1/intake/{slug}/submit` - Submit public form, protected by Altcha captcha (unauthenticated)

*A successful public submission upserts a `customer_contact` identity and
creates a native `issue_type=support` Issue.*

### Projects & Milestones (11 endpoints)
- `GET /api/v1/projects` - List projects
- `POST /api/v1/projects` - Create project
- `GET /api/v1/projects/{id}` - Get project details
- `PUT /api/v1/projects/{id}` - Update project
- `DELETE /api/v1/projects/{id}` - Delete project
- `GET /api/v1/projects/{id}/issues` - Get project issues
- `GET /api/v1/milestones` - List milestones
- `POST /api/v1/milestones` - Create milestone
- `GET /api/v1/milestones/{id}` - Get milestone details
- `PUT /api/v1/milestones/{id}` - Update milestone
- `DELETE /api/v1/milestones/{id}` - Delete milestone

### Labels (5 endpoints)
- `GET /api/v1/labels` - List labels
- `POST /api/v1/labels` - Create label
- `GET /api/v1/labels/{id}` - Get label details
- `PUT /api/v1/labels/{id}` - Update label
- `DELETE /api/v1/labels/{id}` - Delete label

### Secrets Management (8 endpoints)
- `GET /api/v1/secrets` - List secrets
- `POST /api/v1/secrets` - Create secret
- `GET /api/v1/secrets/{id}` - Get secret details
- `PUT /api/v1/secrets/{id}` - Update secret
- `DELETE /api/v1/secrets/{id}` - Delete secret
- `POST /api/v1/secrets/test-connection` - Test secrets provider connection
- `GET /api/v1/secrets/providers` - List available secrets providers
- `GET /api/v1/secrets/{id}/versions` - Get secret versions (versioned providers)

### Built-in Secrets - v2.0.0 (6 endpoints)
- `GET /api/v1/builtin-secrets` - List built-in secrets
- `POST /api/v1/builtin-secrets` - Create built-in secret
- `GET /api/v1/builtin-secrets/{path}` - Get built-in secret by path
- `PUT /api/v1/builtin-secrets/{path}` - Update built-in secret
- `DELETE /api/v1/builtin-secrets/{path}` - Delete built-in secret
- `POST /api/v1/builtin-secrets/test-connection` - Test built-in secrets connection

### API Keys (7 endpoints)
- `GET /api/v1/api-keys` - List API keys
- `POST /api/v1/api-keys` - Create API key
- `GET /api/v1/api-keys/{id}` - Get API key details
- `PUT /api/v1/api-keys/{id}` - Update API key
- `DELETE /api/v1/api-keys/{id}` - Delete API key
- `POST /api/v1/api-keys/{id}/rotate` - Rotate API key
- `POST /api/v1/api-keys/{id}/disable` - Disable API key

### Encryption Keys (6 endpoints)
- `GET /api/v1/keys` - List encryption keys
- `POST /api/v1/keys` - Create encryption key
- `GET /api/v1/keys/{id}` - Get key details
- `PUT /api/v1/keys/{id}` - Update key
- `DELETE /api/v1/keys/{id}` - Delete key
- `POST /api/v1/keys/{id}/rotate` - Rotate encryption key

### IAM Providers - v2.0.0 (33+ endpoints)

#### Provider Management (7 endpoints)
- `GET /api/v1/iam/providers` - List IAM providers
- `POST /api/v1/iam/providers` - Create IAM provider
- `GET /api/v1/iam/providers/{id}` - Get provider details
- `PUT /api/v1/iam/providers/{id}` - Update provider
- `DELETE /api/v1/iam/providers/{id}` - Delete provider
- `POST /api/v1/iam/providers/{id}/test` - Test provider connectivity
- `POST /api/v1/iam/providers/{id}/sync` - Sync provider resources

#### User Management (5 endpoints)
- `GET /api/v1/iam/providers/{id}/users` - List users from provider
- `POST /api/v1/iam/providers/{id}/users` - Create user
- `GET /api/v1/iam/providers/{id}/users/{user_id}` - Get user details
- `PUT /api/v1/iam/providers/{id}/users/{user_id}` - Update user
- `DELETE /api/v1/iam/providers/{id}/users/{user_id}` - Delete user

#### Role Management (5 endpoints)
- `GET /api/v1/iam/providers/{id}/roles` - List roles from provider
- `POST /api/v1/iam/providers/{id}/roles` - Create role
- `GET /api/v1/iam/providers/{id}/roles/{role_id}` - Get role details
- `PUT /api/v1/iam/providers/{id}/roles/{role_id}` - Update role
- `DELETE /api/v1/iam/providers/{id}/roles/{role_id}` - Delete role

#### Policy Management (4 endpoints)
- `GET /api/v1/iam/providers/{id}/policies` - List policies
- `POST /api/v1/iam/providers/{id}/policies` - Create policy
- `GET /api/v1/iam/providers/{id}/policies/{policy_id}` - Get policy details
- `DELETE /api/v1/iam/providers/{id}/policies/{policy_id}` - Delete policy

#### Policy Attachments (6 endpoints)
- `POST /api/v1/iam/providers/{id}/users/{user_id}/policies/{policy_id}` - Attach policy to user
- `DELETE /api/v1/iam/providers/{id}/users/{user_id}/policies/{policy_id}` - Detach policy from user
- `POST /api/v1/iam/providers/{id}/roles/{role_id}/policies/{policy_id}` - Attach policy to role
- `DELETE /api/v1/iam/providers/{id}/roles/{role_id}/policies/{policy_id}` - Detach policy from role
- `GET /api/v1/iam/providers/{id}/users/{user_id}/policies` - List user policies
- `GET /api/v1/iam/providers/{id}/roles/{role_id}/policies` - List role policies

#### Access Keys (3 endpoints)
- `POST /api/v1/iam/providers/{id}/users/{user_id}/access-keys` - Create access key
- `GET /api/v1/iam/providers/{id}/users/{user_id}/access-keys` - List access keys
- `DELETE /api/v1/iam/providers/{id}/users/{user_id}/access-keys/{key_id}` - Delete access key

#### Group Management (5 endpoints)
- `GET /api/v1/iam/providers/{id}/groups` - List groups
- `POST /api/v1/iam/providers/{id}/groups` - Create group
- `DELETE /api/v1/iam/providers/{id}/groups/{group_id}` - Delete group
- `POST /api/v1/iam/providers/{id}/groups/{group_id}/users/{user_id}` - Add user to group
- `DELETE /api/v1/iam/providers/{id}/groups/{group_id}/users/{user_id}` - Remove user from group

### Google Workspace Integration (6 endpoints)
- `GET /api/v1/google-workspace/users` - List Google Workspace users
- `GET /api/v1/google-workspace/users/{id}` - Get user details
- `GET /api/v1/google-workspace/groups` - List groups
- `GET /api/v1/google-workspace/groups/{id}` - Get group details
- `POST /api/v1/google-workspace/sync` - Sync Google Workspace
- `GET /api/v1/google-workspace/status` - Get sync status

### Networking Resources - v2.0.0 (13 endpoints)

#### Network Resources (5 endpoints)
- `GET /api/v1/networking/networks` - List networking resources
- `POST /api/v1/networking/networks` - Create network resource
- `GET /api/v1/networking/networks/{id}` - Get network details
- `PUT /api/v1/networking/networks/{id}` - Update network
- `DELETE /api/v1/networking/networks/{id}` - Delete network (soft or hard)

#### Network Topology (4 endpoints)
- `GET /api/v1/networking/topology/connections` - List topology connections
- `POST /api/v1/networking/topology/connections` - Create topology connection
- `GET /api/v1/networking/topology/connections/{id}` - Get connection details
- `DELETE /api/v1/networking/topology/connections/{id}` - Delete connection

#### Entity-Network Mappings (3 endpoints)
- `GET /api/v1/networking/mappings` - List entity-network mappings
- `POST /api/v1/networking/mappings` - Map entity to network
- `GET /api/v1/networking/mappings/{id}` - Get mapping details
- `DELETE /api/v1/networking/mappings/{id}` - Delete mapping

#### Topology Visualization (1 endpoint)
- `GET /api/v1/networking/topology/graph` - Get network topology graph

### Resource Roles (6 endpoints)
- `GET /api/v1/resource-roles` - List resource roles
- `POST /api/v1/resource-roles` - Assign resource role
- `GET /api/v1/resource-roles/{id}` - Get resource role details
- `PUT /api/v1/resource-roles/{id}` - Update resource role
- `DELETE /api/v1/resource-roles/{id}` - Remove resource role
- `GET /api/v1/resource-roles/check` - Check user's role for resource

### Metadata (8 endpoints)
- `GET /api/v1/metadata/entity/{entity_id}` - List entity metadata fields
- `POST /api/v1/metadata/entity/{entity_id}/fields` - Set entity metadata field
- `GET /api/v1/metadata/entity/{entity_id}/fields/{field_name}` - Get entity metadata field
- `DELETE /api/v1/metadata/entity/{entity_id}/fields/{field_name}` - Delete entity metadata field
- `GET /api/v1/metadata/organization/{org_id}` - List organization metadata fields
- `POST /api/v1/metadata/organization/{org_id}/fields` - Set organization metadata field
- `GET /api/v1/metadata/organization/{org_id}/fields/{field_name}` - Get organization metadata field
- `DELETE /api/v1/metadata/organization/{org_id}/fields/{field_name}` - Delete organization metadata field

### Graph Visualization (5 endpoints)
- `GET /api/v1/graph` - Get complete entity graph
- `GET /api/v1/graph/organization/{org_id}` - Get organization graph
- `GET /api/v1/graph/entity/{entity_id}` - Get entity-centric graph
- `GET /api/v1/graph/dependency-chain/{entity_id}` - Get dependency chain
- `GET /api/v1/graph/impact-analysis/{entity_id}` - Get impact analysis

### Lookup (4 endpoints)
- `GET /api/v1/lookup/entity/{unique_id}` - Lookup entity by unique ID
- `GET /api/v1/lookup/external/{provider}/{external_id}` - Lookup by external ID
- `GET /api/v1/lookup/organization/{unique_id}` - Lookup organization by unique ID
- `GET /api/v1/lookup/user/{username}` - Lookup user by username

### Search (3 endpoints)
- `GET /api/v1/search` - Global search
- `GET /api/v1/search/entities` - Search entities
- `GET /api/v1/search/organizations` - Search organizations

### Discovery Jobs (6 endpoints)
- `GET /api/v1/discovery/jobs` - List discovery jobs
- `POST /api/v1/discovery/jobs` - Create discovery job
- `GET /api/v1/discovery/jobs/{id}` - Get job details
- `PUT /api/v1/discovery/jobs/{id}` - Update job
- `DELETE /api/v1/discovery/jobs/{id}` - Delete job
- `POST /api/v1/discovery/jobs/{id}/run` - Run discovery job

### Webhooks (7 endpoints)
- `GET /api/v1/webhooks` - List webhooks
- `POST /api/v1/webhooks` - Create webhook
- `GET /api/v1/webhooks/{id}` - Get webhook details
- `PUT /api/v1/webhooks/{id}` - Update webhook
- `DELETE /api/v1/webhooks/{id}` - Delete webhook
- `POST /api/v1/webhooks/{id}/test` - Test webhook
- `GET /api/v1/webhooks/{id}/deliveries` - Get webhook deliveries

*Supports an `issue.assigned` event, fired on any assignee change
(create, update, or an intake-form's default-assign). Each webhook can
filter `issue.assigned` deliveries by `issue_type` and by assignee
(`assignee_type` + `assignee_id`); an unset filter matches every
assignment. Deliveries are HMAC-signed (`X-Elder-Signature`) and
non-blocking.*

### Backups (7 endpoints)
- `GET /api/v1/backups` - List backups
- `POST /api/v1/backups` - Create backup
- `GET /api/v1/backups/{id}` - Get backup details
- `DELETE /api/v1/backups/{id}` - Delete backup
- `POST /api/v1/backups/{id}/restore` - Restore from backup
- `POST /api/v1/backups/{id}/download` - Download backup
- `GET /api/v1/backups/schedule` - Get backup schedule

### Sync (3 endpoints)
- `POST /api/v1/sync/aws` - Sync AWS resources
- `POST /api/v1/sync/gcp` - Sync GCP resources
- `POST /api/v1/sync/kubernetes` - Sync Kubernetes resources

### Audit Logs (3 endpoints)
- `GET /api/v1/audit/logs` - List audit logs
- `GET /api/v1/audit/logs/{id}` - Get audit log details
- `GET /api/v1/audit/export` - Export audit logs

### Users (Admin) (5 endpoints)
- `GET /api/v1/users` - List users (admin)
- `POST /api/v1/users` - Create user (admin)
- `GET /api/v1/users/{id}` - Get user details (admin)
- `PUT /api/v1/users/{id}` - Update user (admin)
- `DELETE /api/v1/users/{id}` - Delete user (admin)

---

## Total API Endpoints: 211+ (adds the 7 Intake Forms endpoints above; the IAM Providers total is itself "33+", so this is a floor, not an exact count)

## Detailed Endpoint Documentation

### Organizations

Manage hierarchical organizational structures (Company → Department → Teams).

#### List Organizations
```http
GET /api/v1/organizations
```

**Query Parameters:**
- `page` (int): Page number (default: 1)
- `per_page` (int): Items per page (default: 50, max: 1000)
- `parent_id` (int): Filter by parent organization ID
- `name` (string): Filter by name (partial match)

**Response:**
```json
{
  "items": [
    {
      "id": 1,
      "name": "Engineering",
      "description": "Engineering department",
      "parent_id": null,
      "ldap_dn": "ou=Engineering,dc=example,dc=com",
      "saml_group": "eng@example.com",
      "owner_identity_id": 10,
      "owner_group_id": 5,
      "created_at": "2025-10-01T10:00:00Z",
      "updated_at": "2025-10-01T10:00:00Z"
    }
  ],
  "total": 100,
  "page": 1,
  "per_page": 50,
  "pages": 2
}
```

#### Create Organization
```http
POST /api/v1/organizations
```

**Request Body:**
```json
{
  "name": "Platform Team",
  "description": "Platform engineering team",
  "parent_id": 1,
  "ldap_dn": "ou=Platform,ou=Engineering,dc=example,dc=com",
  "owner_identity_id": 10
}
```

---

### Entities

Track infrastructure and organizational resources.

#### Entity Types

- `datacenter` - Physical or virtual datacenters
- `vpc` - Virtual Private Clouds
- `subnet` - Network subnets
- `compute` - Servers, VMs, containers
- `network` - Load balancers, VPNs, firewalls
- `user` - Users and service accounts
- `security_issue` - Vulnerabilities and CVEs

#### List Entities
```http
GET /api/v1/entities
```

**Query Parameters:**
- `page`, `per_page` - Pagination
- `entity_type` - Filter by type
- `organization_id` - Filter by organization
- `name` - Filter by name (partial match)
- `is_active` - Filter by active status

#### Create Entity
```http
POST /api/v1/entities
```

**Request Body:**
```json
{
  "name": "web-server-01",
  "entity_type": "compute",
  "organization_id": 1,
  "description": "Production web server",
  "attributes": {
    "hostname": "web01.example.com",
    "ip": "10.0.1.10",
    "os": "Ubuntu 22.04",
    "cpu": 4,
    "memory_gb": 16
  },
  "tags": ["production", "web", "us-east-1"],
  "is_active": true
}
```

---

### Networking Resources (v2.0.0)

Manage network resources and topology connections.

#### Network Types

- `datacenter` - Physical or virtual datacenters
- `cloud_region` - Cloud provider regions
- `vpc` - Virtual Private Clouds
- `subnet` - Network subnets
- `vpn` - VPN connections
- `direct_connect` - Direct connections (AWS Direct Connect, Azure ExpressRoute)
- `peering` - Network peering connections
- `transit_gateway` - Transit gateways
- `load_balancer` - Load balancers
- `firewall` - Firewalls and security groups

#### List Networks
```http
GET /api/v1/networking/networks
```

**Query Parameters:**
- `organization_id` (int): Filter by organization
- `network_type` (string): Filter by network type
- `parent_id` (int): Filter by parent network
- `region` (string): Filter by region
- `is_active` (boolean): Filter by active status (default: true)
- `limit` (int): Maximum results (default: 100)
- `offset` (int): Offset for pagination (default: 0)

**Response:**
```json
{
  "networks": [
    {
      "id": 1,
      "name": "Production VPC",
      "network_type": "vpc",
      "organization_id": 1,
      "description": "Production VPC in us-east-1",
      "parent_id": null,
      "region": "us-east-1",
      "location": "AWS us-east-1",
      "poc": "network-team@example.com",
      "organizational_unit": "Infrastructure",
      "attributes": {
        "cidr": "10.0.0.0/16",
        "ipv6_cidr": "2600:1f18::/56"
      },
      "status_metadata": {
        "state": "available"
      },
      "tags": ["production", "vpc"],
      "is_active": true,
      "created_at": "2025-10-01T10:00:00Z",
      "updated_at": "2025-10-01T10:00:00Z"
    }
  ],
  "total": 50,
  "limit": 100,
  "offset": 0
}
```

#### Create Network
```http
POST /api/v1/networking/networks
```

**Request Body:**
```json
{
  "name": "Production VPC",
  "network_type": "vpc",
  "organization_id": 1,
  "description": "Production VPC in us-east-1",
  "region": "us-east-1",
  "location": "AWS us-east-1",
  "poc": "network-team@example.com",
  "attributes": {
    "cidr": "10.0.0.0/16",
    "provider": "aws"
  },
  "tags": ["production", "vpc"]
}
```

#### Create Topology Connection
```http
POST /api/v1/networking/topology/connections
```

**Request Body:**
```json
{
  "source_network_id": 1,
  "target_network_id": 2,
  "connection_type": "vpn",
  "bandwidth": "100Mbps",
  "latency": "50ms",
  "metadata": {
    "encryption": "AES-256",
    "protocol": "IPSec"
  }
}
```

#### Get Topology Graph
```http
GET /api/v1/networking/topology/graph?organization_id=1&include_entities=true
```

**Response:**
```json
{
  "nodes": [
    {
      "id": "network_1",
      "label": "Production VPC",
      "type": "vpc",
      "group": "network",
      "region": "us-east-1"
    },
    {
      "id": "entity_10",
      "label": "web-server-01",
      "type": "compute",
      "group": "entity"
    }
  ],
  "edges": [
    {
      "from": "network_1",
      "to": "network_2",
      "label": "VPN",
      "type": "vpn",
      "bandwidth": "100Mbps"
    }
  ]
}
```

---

### Built-in Secrets (v2.0.0)

Elder's native secrets management with encryption at rest.

#### List Built-in Secrets
```http
GET /api/v1/builtin-secrets?organization_id=1&prefix=/app/
```

**Response:**
```json
{
  "secrets": [
    {
      "path": "/app/db/password",
      "description": "Database password",
      "secret_type": "password",
      "tags": ["production", "database"],
      "created_at": "2025-10-01T10:00:00Z",
      "updated_at": "2025-10-01T10:00:00Z",
      "expires_at": null,
      "version": 1,
      "value_masked": "***MASKED***"
    }
  ]
}
```

#### Create Built-in Secret
```http
POST /api/v1/builtin-secrets
```

**Request Body:**
```json
{
  "name": "/app/db/password",
  "value": "super-secret-password",
  "organization_id": 1,
  "description": "Database password",
  "secret_type": "password",
  "tags": ["production", "database"],
  "expires_at": "2026-01-01T00:00:00Z"
}
```

**Secret Types:**
- `password` - Passwords and credentials
- `api_key` - API keys and tokens
- `certificate` - SSL/TLS certificates
- `ssh_key` - SSH private keys
- `encryption_key` - Encryption keys
- `token` - Generic tokens
- `other` - Other secret types

---

### IAM Providers (v2.0.0)

Manage IAM users, roles, and policies across AWS, GCP, Azure, and Kubernetes.

#### Provider Types

- `aws_iam` - AWS IAM
- `gcp_iam` - GCP IAM
- `azure_ad` - Azure Active Directory
- `kubernetes` - Kubernetes RBAC

#### Create IAM Provider
```http
POST /api/v1/iam/providers
```

**Request Body (AWS IAM):**
```json
{
  "name": "AWS Production IAM",
  "provider_type": "aws_iam",
  "description": "Production AWS IAM integration",
  "config": {
    "region": "us-east-1",
    "access_key_id": "AKIA...",
    "secret_access_key": "..."
  }
}
```

**Request Body (GCP IAM):**
```json
{
  "name": "GCP Production IAM",
  "provider_type": "gcp_iam",
  "description": "Production GCP IAM integration",
  "config": {
    "project_id": "my-project",
    "credentials": {
      "type": "service_account",
      "project_id": "my-project",
      "private_key_id": "...",
      "private_key": "...",
      "client_email": "...",
      "client_id": "..."
    }
  }
}
```

#### List IAM Users
```http
GET /api/v1/iam/providers/1/users?limit=100
```

**Response:**
```json
{
  "users": [
    {
      "user_id": "john.doe",
      "username": "john.doe",
      "display_name": "John Doe",
      "email": "john.doe@example.com",
      "created_at": "2025-10-01T10:00:00Z",
      "tags": {"department": "engineering"}
    }
  ],
  "next_token": "..."
}
```

#### Create IAM User
```http
POST /api/v1/iam/providers/1/users
```

**Request Body:**
```json
{
  "username": "jane.smith",
  "display_name": "Jane Smith",
  "tags": {"department": "platform", "role": "engineer"}
}
```

#### Attach Policy to User
```http
POST /api/v1/iam/providers/1/users/jane.smith/policies/arn:aws:iam::aws:policy/ReadOnlyAccess
```

---

## Response Format

### Success Response

```json
{
  "data": { /* response data */ },
  "message": "Success"
}
```

### Error Response

```json
{
  "error": "Entity not found",
  "code": 404,
  "details": {}
}
```

## Pagination

All list endpoints support pagination:

**Query Parameters:**
- `page` - Page number (1-indexed)
- `per_page` - Items per page (max: 1000)

**Response includes:**
```json
{
  "items": [/* data */],
  "total": 500,
  "page": 1,
  "per_page": 50,
  "pages": 10
}
```

## Rate Limiting

- **Community**: 100 requests/15 minutes
- **Professional**: 1000 requests/15 minutes
- **Enterprise**: Unlimited

## Error Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 204 | No Content (successful delete) |
| 400 | Bad Request (validation error) |
| 401 | Unauthorized |
| 403 | Forbidden (insufficient permissions) |
| 404 | Not Found |
| 409 | Conflict |
| 429 | Rate Limit Exceeded |
| 500 | Internal Server Error |

## Examples

### Complete Workflow Example

```bash
# 1. Create an organization
ORG=$(curl -X POST http://localhost:5000/api/v1/organizations \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production Infrastructure",
    "description": "Production environment"
  }' | jq -r '.id')

# 2. Create entities
WEB_SERVER=$(curl -X POST http://localhost:5000/api/v1/entities \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"web-server-01\",
    \"entity_type\": \"compute\",
    \"organization_id\": $ORG,
    \"attributes\": {
      \"ip\": \"10.0.1.10\",
      \"os\": \"Ubuntu 22.04\"
    }
  }" | jq -r '.id')

DB_SERVER=$(curl -X POST http://localhost:5000/api/v1/entities \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"db-server-01\",
    \"entity_type\": \"compute\",
    \"organization_id\": $ORG,
    \"attributes\": {
      \"ip\": \"10.0.1.20\",
      \"os\": \"Ubuntu 22.04\"
    }
  }" | jq -r '.id')

# 3. Create dependency
curl -X POST http://localhost:5000/api/v1/dependencies \
  -H "Content-Type: application/json" \
  -d "{
    \"source_entity_id\": $WEB_SERVER,
    \"target_entity_id\": $DB_SERVER,
    \"dependency_type\": \"database\",
    \"metadata\": {\"port\": 5432}
  }"

# 4. Create network (v2.0.0)
VPC=$(curl -X POST http://localhost:5000/api/v1/networking/networks \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"Production VPC\",
    \"network_type\": \"vpc\",
    \"organization_id\": $ORG,
    \"region\": \"us-east-1\",
    \"attributes\": {\"cidr\": \"10.0.0.0/16\"}
  }" | jq -r '.id')

# 5. Map entities to network
curl -X POST http://localhost:5000/api/v1/networking/mappings \
  -H "Content-Type: application/json" \
  -d "{
    \"network_id\": $VPC,
    \"entity_id\": $WEB_SERVER,
    \"relationship_type\": \"resides_in\"
  }"

# 6. Get topology graph
curl "http://localhost:5000/api/v1/networking/topology/graph?organization_id=$ORG&include_entities=true" | jq

# 7. Create built-in secret (v2.0.0)
curl -X POST http://localhost:5000/api/v1/builtin-secrets \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"/app/db/password\",
    \"value\": \"super-secret\",
    \"organization_id\": $ORG,
    \"secret_type\": \"password\"
  }"
```

## SDK & Client Libraries

- **Python**: Official Python client (coming soon)
- **Go**: Official Go client (coming soon)
- **JavaScript/TypeScript**: Official JS client (coming soon)

## OpenAPI Specification

Full OpenAPI 3.0 specification available at:
```
http://localhost:5000/api/v1/openapi.json
```

## Further Reading

- [Quick Start Guide](../connector/QUICKSTART.md)
- [Architecture Documentation](../architecture/README.md)
- [Development Setup](../development/README.md)
- [v2.0.0 Release Notes](../RELEASE_NOTES.md)
