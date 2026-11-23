# Elder API Documentation

Elder provides comprehensive REST and gRPC APIs for infrastructure management.

## REST API

Elder's REST API follows OpenAPI 3.0 specification. Full interactive documentation is available at `/api/docs` (Swagger UI) when the API server is running.

### Authentication

All API endpoints (except `/healthz` and `/api/v1/auth/*`) require authentication via JWT Bearer token:

```bash
curl -H "Authorization: Bearer <your-token>" https://elder-api/api/v1/entities
```

### Core Endpoints

#### Organizations
```
GET    /api/v1/organizations              # List organizations
POST   /api/v1/organizations              # Create organization
GET    /api/v1/organizations/{id}         # Get organization
PATCH  /api/v1/organizations/{id}         # Update organization
DELETE /api/v1/organizations/{id}         # Delete organization
GET    /api/v1/organizations/{id}/tree    # Get organization tree
```

#### Entities
```
GET    /api/v1/entities                   # List entities
POST   /api/v1/entities                   # Create entity
GET    /api/v1/entities/{id}              # Get entity
PATCH  /api/v1/entities/{id}              # Update entity
DELETE /api/v1/entities/{id}              # Delete entity
```

#### Dependencies
```
GET    /api/v1/dependencies               # List dependencies
POST   /api/v1/dependencies               # Create dependency
DELETE /api/v1/dependencies/{id}          # Delete dependency
```

#### Identities (IAM)
```
GET    /api/v1/identities                 # List identities
POST   /api/v1/identities                 # Create identity
GET    /api/v1/identities/{id}            # Get identity
PATCH  /api/v1/identities/{id}            # Update identity
DELETE /api/v1/identities/{id}            # Delete identity
```

#### Networking
```
GET    /api/v1/networking/resources       # List network resources
POST   /api/v1/networking/resources       # Create network resource
GET    /api/v1/networking/resources/{id}  # Get network resource
PATCH  /api/v1/networking/resources/{id}  # Update network resource
DELETE /api/v1/networking/resources/{id}  # Delete network resource
GET    /api/v1/networking/topology        # Get network topology
POST   /api/v1/networking/topology        # Create topology connection
```

#### Secrets & Keys
```
GET    /api/v1/secrets                    # List secrets
POST   /api/v1/secrets                    # Create secret
GET    /api/v1/secrets/{id}               # Get secret
DELETE /api/v1/secrets/{id}               # Delete secret
GET    /api/v1/keys                       # List keys
POST   /api/v1/keys                       # Create key
GET    /api/v1/keys/{id}                  # Get key
DELETE /api/v1/keys/{id}                  # Delete key
```

#### Projects & Issues
```
GET    /api/v1/projects                   # List projects
POST   /api/v1/projects                   # Create project
GET    /api/v1/projects/{id}              # Get project
PATCH  /api/v1/projects/{id}              # Update project
DELETE /api/v1/projects/{id}              # Delete project

GET    /api/v1/issues                     # List issues
POST   /api/v1/issues                     # Create issue
GET    /api/v1/issues/{id}                # Get issue
PATCH  /api/v1/issues/{id}                # Update issue
DELETE /api/v1/issues/{id}                # Delete issue

GET    /api/v1/milestones                 # List milestones
POST   /api/v1/milestones                 # Create milestone
GET    /api/v1/milestones/{id}            # Get milestone
PATCH  /api/v1/milestones/{id}            # Update milestone
DELETE /api/v1/milestones/{id}            # Delete milestone
```

Issues are unified — a customer support ticket is simply an Issue with
`issue_type=support` (other values: `operations`, `code`, `config`,
`security`, `architecture`, `process`, `approval`, `feature`, `bug`,
`other`). There is no separate ticket resource. Assignment is
polymorphic: `assignee_type` is `identity` or `org_unit`, paired with
`assignee_id` referencing the matching table.

#### Entity Types
```
GET    /api/v1/entity-types/              # List all entity types
GET    /api/v1/entity-types/{type}        # Get entity type details
GET    /api/v1/entity-types/{type}/subtypes         # Get sub-types
GET    /api/v1/entity-types/{type}/metadata         # Get default metadata
GET    /api/v1/entity-types/{type}/{sub_type}/metadata  # Get sub-type metadata
```

#### Webhooks
```
GET    /api/v1/webhooks                   # List webhooks
POST   /api/v1/webhooks                   # Create webhook
GET    /api/v1/webhooks/{id}              # Get webhook
PATCH  /api/v1/webhooks/{id}              # Update webhook
DELETE /api/v1/webhooks/{id}              # Delete webhook
GET    /api/v1/webhooks/{id}/deliveries   # Get delivery history
```

#### CodeRuns (Serverless Functions)
```
GET    /api/v1/runs                                       # List CodeRun functions
POST   /api/v1/runs                                       # Create CodeRun function (max timeout_seconds: 300)
GET    /api/v1/runs/{id}                                  # Get CodeRun function
PUT    /api/v1/runs/{id}                                  # Update CodeRun function (creates new version)
DELETE /api/v1/runs/{id}                                  # Delete CodeRun function
POST   /api/v1/runs/{id}/invoke                           # Queue a CodeRun execution
GET    /api/v1/runs/permissions/catalog                   # List valid granted_scopes for CodeRuns
GET    /api/v1/runs/executions                            # List CodeRun executions
GET    /api/v1/runs/executions/{execution_id}             # Get enriched execution detail & live stdout/stderr logs
GET    /api/v1/runs/internal/pending                      # Worker poll/claim (svc-runworker, mints ephemeral JWT)
POST   /api/v1/runs/internal/executions/{id}/logs         # Live in-progress stdout log flush (default every 30s)
POST   /api/v1/runs/internal/executions/{id}/result       # Worker completion report
```

CodeRuns support per-function scoped permissions (`granted_scopes`, `allowed_egress_domains`) enforced via short-lived ephemeral execution JWTs and the `svc-runworker` loopback Tool/Network Restriction Gateway, plus `worker_cluster` routing into the operator-managed `execution_namespace` (`elder-runs`).

#### Centralized Executions Audit Log
```
GET    /api/v1/streams/executions         # Unified audit log across Playbooks, CodeRuns, and Webhooks
                                          # Query params: ?source=all|playbook|coderun|webhook, ?status=, ?search=, ?stream_id=
```

#### CSV Bulk Imports
```
GET    /api/v1/import/templates                   # List downloadable CSV templates
GET    /api/v1/import/templates/{resource}/download  # Download CSV template for resource type
POST   /api/v1/import/{resource}                  # Upload CSV to S3 & enqueue job ID on Valkey (elder:csv_import:queue)
GET    /api/v1/import/jobs                        # List async CSV import jobs
GET    /api/v1/import/jobs/{job_id}               # Poll CSV import job status & row results
```

#### Remote Workers Fleet (Admin)
```
GET    /api/v1/admin/workers                      # List registered svc-runworker & svc-scanner fleet
POST   /api/v1/admin/workers                      # Onboard remote worker (returns one-time ewk_ bearer token)
GET    /api/v1/admin/workers/{id}                 # Get worker status, liveness & live config
PUT    /api/v1/admin/workers/{id}                 # Update live worker configuration
POST   /api/v1/admin/workers/{id}/rotate-token    # Rotate worker ewk_ token (returns new one-time token)
DELETE /api/v1/admin/workers/{id}                 # Offboard / revoke remote worker
POST   /api/v1/admin/workers/heartbeat            # Worker fleet heartbeat (reports online/stale/offline/revoked liveness)
```

#### Graph Visualization
```
GET    /api/v1/graph                      # Get full graph
GET    /api/v1/graph?organization_id={id} # Graph for organization
GET    /api/v1/graph?entity_id={id}&depth=2  # Graph centered on entity
```

#### Authentication
```
POST   /api/v1/auth/login                 # Login with credentials
POST   /api/v1/auth/logout                # Logout
POST   /api/v1/auth/refresh               # Refresh token
GET    /api/v1/auth/saml/login            # SAML SSO login
GET    /api/v1/auth/oauth2/authorize      # OAuth2 authorization
```

#### System
```
GET    /healthz                           # Health check
GET    /metrics                           # Prometheus metrics
GET    /api/v1/search                     # Global search
GET    /api/v1/audit                      # Audit logs
```

### Pagination

List endpoints support pagination:

```bash
GET /api/v1/entities?page=1&per_page=50
```

Response includes pagination metadata:
```json
{
  "items": [...],
  "total": 150,
  "page": 1,
  "per_page": 50,
  "pages": 3
}
```

### Filtering

Most list endpoints support filtering:

```bash
GET /api/v1/entities?organization_id=1&entity_type=compute
GET /api/v1/issues?status=open&priority=high
```

### Error Responses

Errors follow a consistent format:

```json
{
  "error": "Not Found",
  "message": "Entity with id 123 not found",
  "status_code": 404
}
```

## gRPC API

Elder also provides a gRPC API for high-performance integrations. The gRPC server runs on port `50051` by default.

### Service Definition

```protobuf
service ElderService {
  // Organizations
  rpc ListOrganizations(ListOrganizationsRequest) returns (ListOrganizationsResponse);
  rpc GetOrganization(GetOrganizationRequest) returns (Organization);
  rpc CreateOrganization(CreateOrganizationRequest) returns (Organization);
  rpc UpdateOrganization(UpdateOrganizationRequest) returns (Organization);
  rpc DeleteOrganization(DeleteOrganizationRequest) returns (Empty);

  // Entities
  rpc ListEntities(ListEntitiesRequest) returns (ListEntitiesResponse);
  rpc GetEntity(GetEntityRequest) returns (Entity);
  rpc CreateEntity(CreateEntityRequest) returns (Entity);
  rpc UpdateEntity(UpdateEntityRequest) returns (Entity);
  rpc DeleteEntity(DeleteEntityRequest) returns (Empty);

  // Dependencies
  rpc GetDependencyGraph(GetDependencyGraphRequest) returns (DependencyGraph);
  rpc CreateDependency(CreateDependencyRequest) returns (Dependency);
  rpc DeleteDependency(DeleteDependencyRequest) returns (Empty);

  // Identities
  rpc ListIdentities(ListIdentitiesRequest) returns (ListIdentitiesResponse);
  rpc GetIdentity(GetIdentityRequest) returns (Identity);
  rpc CreateIdentity(CreateIdentityRequest) returns (Identity);
  rpc UpdateIdentity(UpdateIdentityRequest) returns (Identity);
  rpc DeleteIdentity(DeleteIdentityRequest) returns (Empty);

  // Search
  rpc Search(SearchRequest) returns (SearchResponse);
}
```

### Proto Files

Proto definitions are located in `apps/api/grpc/proto/`. Generate client code using:

```bash
make generate-grpc
```

### Example Usage (Python)

```python
import grpc
from elder_pb2 import ListEntitiesRequest
from elder_pb2_grpc import ElderServiceStub

channel = grpc.insecure_channel('localhost:50051')
stub = ElderServiceStub(channel)

# List entities
request = ListEntitiesRequest(organization_id=1, page=1, per_page=50)
response = stub.ListEntities(request)

for entity in response.entities:
    print(f"{entity.name}: {entity.entity_type}")
```

### Example Usage (Go)

```go
package main

import (
    "context"
    "google.golang.org/grpc"
    pb "github.com/penguintechinc/elder/proto"
)

func main() {
    conn, _ := grpc.Dial("localhost:50051", grpc.WithInsecure())
    defer conn.Close()

    client := pb.NewElderServiceClient(conn)

    resp, _ := client.ListEntities(context.Background(), &pb.ListEntitiesRequest{
        OrganizationId: 1,
        Page: 1,
        PerPage: 50,
    })

    for _, entity := range resp.Entities {
        fmt.Printf("%s: %s\n", entity.Name, entity.EntityType)
    }
}
```

## Rate Limiting

API requests are rate limited to prevent abuse:

- **Anonymous**: 100 requests/minute
- **Authenticated**: 1000 requests/minute
- **Admin**: 5000 requests/minute

Rate limit headers are included in responses:
```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1635724800
```

## Webhooks

Elder can send webhook notifications for various events. See the Webhooks API endpoints above to configure webhooks.

Supported events:
- `entity.created`, `entity.updated`, `entity.deleted`
- `organization.created`, `organization.updated`, `organization.deleted`
- `issue.created`, `issue.updated`, `issue.closed`
- `issue.assigned` — fired on any assignee change (create or update).
  Filterable per-webhook by `issue_type`
  and by assignee (`assignee_type` + `assignee_id`, i.e. a specific
  identity or org unit); an empty filter matches every assignment.
  Deliveries are HMAC-signed (`X-Elder-Signature` header) and
  non-blocking.
- `dependency.created`, `dependency.deleted`

Webhook payloads include:
```json
{
  "event": "entity.created",
  "timestamp": "2025-11-19T12:00:00Z",
  "data": {
    "id": 123,
    "name": "web-server-01",
    "entity_type": "compute"
  }
}
```

## SDKs, CLI & MCP Server

Official clients and agent interfaces live in this repository and expose the full v4.0.x API surface, including CodeRuns and the Remote Workers Fleet (`/api/v1/admin/workers`):

- **Python SDK** (`sdk/python`, `elder_sdk`): Sync (`ElderClient`) and async (`AsyncElderClient`) clients, including `client.workers` (`list`, `get`, `onboard`, `update`, `rotate_token`, `offboard`, `heartbeat`) and `client.runs`.
- **Rust SDK** (`crates/elder-sdk`, pure-rustls): Typed async client, including `elder_sdk::workers` (`WorkersClient`) and `elder_sdk::runs`.
- **Rust CLI** (`cli/elder-cli`, pure-rustls): Ergonomic command-line client, including `elder workers` (`list`, `get`, `onboard`, `update`, `rotate-token`, `offboard`) and `elder runs`.
- **MCP Server (`hub-mcp`)** (`apps/mcp/`): Hosted caller-scoped Model Context Protocol server backed by `openapi/v4.yaml` / `apps/mcp/api_catalog.json`, forwarding the caller's own token to `hub-api`.
- **Built-in CodeRun Helper SDKs**: Zero-dependency `elder.py`, `elder.mjs`, and `elder.rb` helpers mounted automatically into every `svc-runworker` CodeRun workspace, communicating through the loopback Tool/Network Restriction Gateway.

## Additional Resources

- [Interactive API Docs](/api/docs) - Swagger UI (when running)
- [Database Schema](DATABASE.md) - Database structure
- [Sync Documentation](SYNC.md) - Project management sync
- [Release Notes](RELEASE_NOTES.md) - Version history
