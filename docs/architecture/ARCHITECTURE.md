# Elder Architecture

This document describes the architecture, design decisions, and technical implementation of Elder.

## System Overview

Elder is a multi-tier infrastructure tracking and dependency management platform built with Python, Flask, PostgreSQL, and Redis.

```
┌──────────────────────────────────────────────────────────────┐
│                        Client Layer                          │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐ │
│  │   Web UI    │  │  CLI Tools  │  │  External Systems    │ │
│  │  (React)    │  │  (Python)   │  │  (AWS/GCP/Workspace) │ │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬───────────┘ │
└─────────┼─────────────────┼────────────────────┼─────────────┘
          │                 │                    │
┌─────────┼─────────────────┼────────────────────┼─────────────┐
│         ▼                 ▼                    ▼             │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐ │
│  │   REST API   │  │   gRPC API   │  │  Worker Service    │ │
│  │   (Flask)    │  │ (Enterprise) │  │  (Multi-cloud sync)│ │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬──────────┘ │
│         │                 │                      │            │
│         └─────────────────┴──────────────────────┘            │
│                           │                                   │
│                      Application Layer                        │
└───────────────────────────┼───────────────────────────────────┘
                            │
┌───────────────────────────┼───────────────────────────────────┐
│                           ▼                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  PostgreSQL  │  │    Redis     │  │   Prometheus     │   │
│  │  (Primary)   │  │   (Cache)    │  │   (Metrics)      │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│                                                               │
│                      Data Layer                               │
└───────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Flask REST API (`apps/api/`)

**Technology Stack:**
- Flask 3.0+ (Python 3.13)
- Flask-RESTX for REST API
- SQLAlchemy for ORM
- Marshmallow for validation
- Flask-CORS for cross-origin requests
- Prometheus Flask Exporter for metrics

**Key Features:**
- RESTful API with OpenAPI documentation
- JWT authentication
- Role-based access control (RBAC)
- Comprehensive error handling
- Request validation with schemas
- Async support with asyncio

**Directory Structure:**
```
apps/api/
├── api/v1/              # API endpoints
│   ├── organizations.py
│   ├── entities.py
│   ├── dependencies.py
│   ├── issues.py
│   └── ...
├── models/              # SQLAlchemy models
│   ├── organization.py
│   ├── entity.py
│   ├── dependency.py
│   └── ...
├── schemas/             # Marshmallow schemas
├── auth/                # Authentication & authorization
├── grpc/                # gRPC server (Enterprise)
└── main.py              # Application factory
```

### 2. React Web UI (`web/`)

**Technology Stack:**
- React 18+
- TypeScript
- React Router v6
- vis.js for graph visualization
- Tailwind CSS for styling

**Key Features:**
- Interactive entity relationship graphs
- Organization hierarchy tree views
- Real-time updates via WebSocket
- Responsive design
- Dark mode support

### 3. Worker Service (`apps/worker/`)

**Purpose:** Synchronize external infrastructure and identity data into Elder.

**Supported Sources:**
- AWS (EC2, VPC, S3)
- GCP (Compute, VPC, Storage)
- Google Workspace (Users, Groups, OrgUnits)
- LDAP/LDAPS (Users, Groups, OUs)

**Architecture:**
```
Worker Service
├── Scheduler (aiocron)
├── AWS Connector ──┐
├── GCP Connector ──┼─→ Elder API Client ──→ Elder REST API
├── Workspace Conn ─┤
└── LDAP Connector ─┘
```

See [Worker Documentation](../worker/README.md) for details.

### 4. gRPC API (Enterprise)

**Technology Stack:**
- gRPC/Protobuf
- Python grpcio
- Envoy proxy for gRPC-Web

**Features:**
- High-performance machine-to-machine communication
- Streaming support for real-time updates
- Type-safe API with Protobuf definitions
- Enterprise-only feature

## Data Model

### Core Entities

#### Organization
```python
class Organization:
    id: int
    name: str
    description: str
    parent_id: int | None          # Hierarchical structure
    ldap_dn: str | None            # LDAP integration
    saml_group: str | None         # SAML integration
    owner_identity_id: int | None
    owner_group_id: int | None
    created_at: datetime
    updated_at: datetime
```

**Hierarchy Example:**
```
Company (id=1)
└── Engineering (id=2, parent_id=1)
    ├── Platform (id=3, parent_id=2)
    └── Security (id=4, parent_id=2)
```

#### Entity
```python
class Entity:
    id: int
    unique_id: int                 # 64-bit public identifier
    name: str
    description: str
    entity_type: EntityType        # datacenter, vpc, compute, etc.
    organization_id: int
    owner_identity_id: int | None
    entity_metadata: dict          # JSON metadata
    created_at: datetime
    updated_at: datetime
```

**Entity Types:**
- `datacenter` - Physical/virtual datacenters
- `vpc` - Virtual Private Clouds
- `subnet` - Network subnets
- `compute` - Servers, VMs, containers
- `network` - Load balancers, VPNs, firewalls
- `user` - Users and service accounts
- `security_issue` - Vulnerabilities, CVEs

#### Dependency
```python
class Dependency:
    id: int
    source_entity_id: int          # Source entity
    target_entity_id: int          # Target entity (source depends on target)
    dependency_type: str           # network, database, application, etc.
    metadata: dict                 # Additional dependency info
    created_at: datetime
```

**Example:**
```
web-server (source) ──depends on──> database (target)
```

### Relationship Model

```
Organizations (Hierarchical)
    │
    ├── has many ──> Entities
    │                   │
    │                   ├── has many ──> Dependencies (outgoing)
    │                   └── has many ──> Dependencies (incoming)
    │
    └── has many ──> Issues
                        │
                        └── linked to ──> Entities
```

## Authentication & Authorization

### Authentication Methods

1. **Local Authentication**
   - Username/password
   - Bcrypt password hashing
   - Session-based or JWT tokens

2. **SAML/OAuth2** (Professional+)
   - Enterprise SSO
   - Configurable identity providers
   - Automatic role mapping

3. **LDAP Integration** (Enterprise)
   - Active Directory / OpenLDAP
   - Group membership sync
   - Automatic user provisioning

4. **API Keys**
   - Bearer token authentication
   - Scoped permissions
   - Key rotation support

### Authorization (RBAC)

#### Global Roles

| Role | Permissions |
|------|------------|
| **Super Admin** | Full system access, user management |
| **Organization Admin** | Manage org and all children |
| **Editor** | Create/edit entities and dependencies |
| **Viewer** | Read-only access |

#### Resource Roles

Fine-grained permissions per organization or entity:

| Role | Permissions |
|------|------------|
| **Maintainer** | Full CRUD on resource |
| **Operator** | Create/manage issues, operational tasks |
| **Viewer** | Read-only access to resource |

**Permission Hierarchy:**
```
Global Role → Organization Role → Entity Role
```

## Database Schema

### Primary Database (PostgreSQL)

**Tables:**
- `organizations` - Organizational hierarchy
- `entities` - Infrastructure entities
- `dependencies` - Entity relationships
- `identities` - Users and service accounts
- `identity_groups` - User groups
- `issues` - Issue tracking
- `issue_comments` - Issue comments
- `issue_labels` - Issue labels
- `projects` - Project management
- `milestones` - Project milestones
- `resource_roles` - Fine-grained permissions
- `metadata_fields` - Type-validated metadata
- `alert_configurations` - Alerting rules

**Indexes:**
- Organizations: `name`, `parent_id`, `ldap_dn`
- Entities: `name`, `entity_type`, `organization_id`, `unique_id`
- Dependencies: `source_entity_id`, `target_entity_id`

**Migrations:**
- Alembic for schema migrations
- Versioned migration files in `alembic/versions/`

### Cache Layer (Redis/Valkey)

**Usage:**
- Session storage
- API response caching
- Real-time event pub/sub
- Rate limiting counters

**Key Patterns:**
```
session:{session_id}              # User sessions
cache:entity:{entity_id}          # Entity cache
cache:org:{org_id}:children       # Organization children cache
ratelimit:{user_id}:{endpoint}    # Rate limit tracking
```

## API Design Principles

### RESTful Conventions

1. **Resource-based URLs**
   ```
   GET    /api/v1/entities          # List
   POST   /api/v1/entities          # Create
   GET    /api/v1/entities/{id}     # Read
   PATCH  /api/v1/entities/{id}     # Update
   DELETE /api/v1/entities/{id}     # Delete
   ```

2. **HTTP Methods**
   - GET - Retrieve resources
   - POST - Create new resources
   - PATCH/PUT - Update resources
   - DELETE - Remove resources

3. **Status Codes**
   - 200 - Success
   - 201 - Created
   - 204 - No Content (delete)
   - 400 - Bad Request
   - 401 - Unauthorized
   - 403 - Forbidden
   - 404 - Not Found
   - 500 - Server Error

4. **Pagination**
   ```json
   {
     "items": [...],
     "total": 500,
     "page": 1,
     "per_page": 50,
     "pages": 10
   }
   ```

### API Versioning

- Version in URL: `/api/v1/`
- Backward compatibility maintained
- Deprecation notices for breaking changes
- Version sunset policy

## Performance Optimization

### Database Optimization

1. **Connection Pooling**
   - SQLAlchemy connection pool
   - Pool size: 20 connections
   - Pool recycle: 3600 seconds

2. **Query Optimization**
   - Eager loading for relationships
   - Index optimization
   - Query result caching

3. **Batch Operations**
   - Bulk inserts via SQLAlchemy
   - Batch updates for connector sync

### Caching Strategy

1. **Redis Caching**
   - Entity cache TTL: 300 seconds
   - Organization hierarchy cache
   - Graph data cache

2. **HTTP Caching**
   - ETag support
   - Cache-Control headers
   - Conditional requests

### Async/Concurrent Operations

1. **Worker Service**
   - Async HTTP with aiohttp
   - Concurrent sync operations
   - Connection pooling

2. **API Layer**
   - Async request handlers
   - Background task processing
   - WebSocket for real-time updates

## Security Architecture

### Defense in Depth

1. **Network Security**
   - TLS 1.2+ enforcement
   - Rate limiting per user/IP
   - CORS configuration

2. **Application Security**
   - Input validation (all endpoints)
   - SQL injection prevention (ORM)
   - XSS prevention (output encoding)
   - CSRF protection

3. **Authentication Security**
   - Bcrypt password hashing (12 rounds)
   - JWT with secure signing
   - MFA support
   - Session timeout enforcement

4. **Authorization Security**
   - Role-based access control
   - Resource-level permissions
   - Audit logging

5. **Data Security**
   - Encrypted credentials storage
   - Secrets management
   - PII handling compliance

## Monitoring & Observability

### Metrics (Prometheus)

**Application Metrics:**
```
# HTTP requests
http_requests_total{method, endpoint, status}
http_request_duration_seconds{method, endpoint}

# Database
db_connections_active
db_query_duration_seconds

# Worker
connector_sync_total{connector, status}
connector_entities_synced{connector, operation}
```

### Logging

**Structured Logging:**
```json
{
  "timestamp": "2025-10-25T10:00:00Z",
  "level": "INFO",
  "logger": "api.organizations",
  "message": "Organization created",
  "org_id": 10,
  "user_id": 5
}
```

**Log Levels:**
- DEBUG - Development debugging
- INFO - Normal operations
- WARNING - Potential issues
- ERROR - Error conditions
- CRITICAL - System failures

### Health Checks

**Endpoints:**
- `/healthz` - Liveness probe
- `/readyz` - Readiness probe
- `/metrics` - Prometheus metrics

## Deployment Architecture

### Container-Based Deployment

```
┌─────────────────────────────────────────────────┐
│              Load Balancer (Nginx)              │
└─────────────────┬───────────────────────────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
┌──────────────┐    ┌──────────────┐
│   API Pod 1  │    │   API Pod 2  │
└──────┬───────┘    └──────┬───────┘
       │                   │
       └────────┬──────────┘
                ▼
┌───────────────────────────────────┐
│         PostgreSQL                │
│         (Primary + Replicas)      │
└───────────────────────────────────┘
```

**Services:**
- API (multiple replicas)
- Web UI (static hosting)
- Worker (single instance)
- gRPC (Enterprise, multiple replicas)
- PostgreSQL (primary + read replicas)
- Redis (cluster mode)
- Prometheus/Grafana (monitoring)

### High Availability

1. **API Layer**
   - Horizontal scaling (2+ replicas)
   - Load balancing
   - Health check-based routing

2. **Database Layer**
   - Primary-replica replication
   - Automatic failover
   - Point-in-time recovery

3. **Cache Layer**
   - Redis cluster mode
   - Sentinel for HA
   - Persistence for durability

## Scalability Considerations

### Horizontal Scaling

- **API**: Stateless, scales linearly
- **Worker**: Single instance (scheduled jobs)
- **Database**: Read replicas for queries
- **Cache**: Redis cluster

### Vertical Scaling Limits

- **Database**: CPU/memory for large graphs
- **API**: Memory for large result sets
- **Worker**: Network I/O for sync operations

### Performance Targets

- API response time: < 100ms (p95)
- Graph render time: < 2s (1000 nodes)
- Connector sync: < 5 min (10K entities)
- Database queries: < 50ms (p95)

## Technology Choices

### Why Python?

- Rapid development
- Rich ecosystem (Flask, SQLAlchemy)
- Excellent cloud SDK support
- Strong async capabilities

### Why Flask?

- Lightweight and flexible
- Extensive ecosystem
- Easy to test and deploy
- Good performance for APIs

### Why PostgreSQL?

- JSONB support for flexible metadata
- Excellent query optimizer
- Robust ACID compliance
- Strong ecosystem

### Why Redis?

- Fast in-memory caching
- Pub/sub for real-time features
- Session storage
- Rate limiting support

## Future Architecture

### Planned Enhancements

1. **Microservices Split**
   - Separate worker service
   - Dedicated graph service
   - Independent scaling

2. **Event-Driven Architecture**
   - Kafka/RabbitMQ for events
   - Event sourcing for audit
   - CQRS for read/write optimization

3. **Advanced Caching**
   - CDN for static assets
   - GraphQL with DataLoader
   - Materialized views

4. **Multi-Region**
   - Geographic distribution
   - Data replication
   - Regional failover

## References

- [API Documentation](../api/README.md)
- [Database Schema](../DATABASE.md)
- [Deployment Guide](../deployment/README.md)
- [Development Setup](../development/README.md)
