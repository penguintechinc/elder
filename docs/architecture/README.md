# Elder Architecture Documentation

Overview of Elder's architecture, design decisions, and technical implementation.

## Architecture Overview

Elder is a multi-tier infrastructure tracking platform built with Python, Flask, PostgreSQL, and Redis.

```
┌────────────────────────────────────────────────┐
│              Client Layer                      │
│  Web UI | CLI Tools | External Integrations   │
└────────────┬───────────────────────────────────┘
             │
┌────────────┴───────────────────────────────────┐
│         Application Layer                      │
│  REST API | gRPC API | Connector Service      │
└────────────┬───────────────────────────────────┘
             │
┌────────────┴───────────────────────────────────┐
│            Data Layer                          │
│  PostgreSQL | Redis | Prometheus              │
└────────────────────────────────────────────────┘
```

## Core Components

### Application Services

1. **[Flask REST API](ARCHITECTURE.md#1-flask-rest-api)** - Primary API interface
2. **[React Web UI](ARCHITECTURE.md#2-react-web-ui)** - Interactive web interface
3. **[Connector Service](../../connector/IMPLEMENTATION.md)** - Multi-cloud data sync
4. **[gRPC API](../grpc/README.md)** - High-performance API (Enterprise)

### Data Stores

1. **PostgreSQL** - Primary data store
2. **Redis/Valkey** - Caching and sessions
3. **Prometheus** - Metrics and monitoring

## Data Model

### Core Entities

- **Organizations** - Hierarchical structures (Company → Dept → Team)
- **Entities** - Infrastructure resources (compute, network, users)
- **Dependencies** - Relationships between entities
- **Issues** - GitHub-style issue tracking, unified with support: a support ticket is an Issue with `issue_type=support`, assigned to an identity or an org unit (polymorphic `assignee_type`/`assignee_id`); public intake forms create these from unauthenticated submitters, optionally gated by an Altcha captcha (off by default)
- **Identities** - Users and service accounts

📖 **[Complete Data Model](ARCHITECTURE.md#data-model)**

## Technology Stack

### Backend
- **Python 3.13+** - Primary language
- **Flask 3.0+** - Web framework
- **SQLAlchemy** - ORM
- **PostgreSQL 15+** - Database
- **Redis 7+** - Cache

### Frontend
- **React 18+** - UI framework
- **TypeScript** - Type safety
- **vis.js** - Graph visualization
- **Tailwind CSS** - Styling

### Infrastructure
- **Docker** - Containerization
- **Docker Compose** - Local development
- **Prometheus/Grafana** - Monitoring
- **Envoy** - gRPC-Web proxy

## Key Design Decisions

### 1. Why Python?
- Rich cloud SDK ecosystem (AWS, GCP, Azure)
- Rapid development with Flask
- Excellent async support
- Strong data processing libraries

### 2. Why Flask over Django?
- Lightweight and flexible
- Microservices-friendly
- Easy testing
- Fine-grained control

### 3. Why PostgreSQL?
- **JSONB** support for flexible metadata
- Excellent query optimizer
- Robust ACID compliance
- JSON operations and indexing

### 4. Why SQLAlchemy?
- Type-safe ORM
- Supports multiple databases
- Migration management with Alembic
- Good performance

## Architecture Principles

### 1. RESTful Design
- Resource-based URLs
- Standard HTTP methods
- Stateless API
- Versioned endpoints

### 2. Separation of Concerns
- API layer (Flask)
- Business logic (models)
- Data access (SQLAlchemy)
- External sync (Connector)

### 3. Scalability
- Horizontal scaling (stateless API)
- Database read replicas
- Redis caching
- Async operations

### 4. Security
- Defense in depth
- RBAC + resource-level permissions
- Input validation everywhere
- Audit logging

## Performance Optimization

### Database
- Connection pooling (20 connections)
- Query optimization with indexes
- Batch operations for bulk data
- Read replicas for scaling

### Caching
- Redis for entity/org data
- HTTP caching with ETags
- Connection pooling

### Async Operations
- Connector service uses asyncio
- Background task processing
- Non-blocking I/O

## Security Architecture

### Authentication
- Local (username/password)
- API Keys (Bearer tokens)
- SAML/OAuth2 (Enterprise)
- LDAP (Enterprise)

### Authorization
- **Global Roles**: Super Admin, Org Admin, Editor, Viewer
- **Resource Roles**: Maintainer, Operator, Viewer (per org/entity)
- Hierarchical permissions

### Data Security
- Bcrypt password hashing
- Encrypted secrets storage
- TLS 1.2+ enforcement
- SQL injection prevention (ORM)
- XSS prevention
- CSRF protection

## Monitoring & Observability

### Metrics (Prometheus)
- HTTP request metrics
- Database connection/query metrics
- Connector sync metrics
- Custom business metrics

### Logging
- Structured JSON logging
- Configurable log levels
- Correlation IDs
- Audit trail

### Health Checks
- `/healthz` - Liveness
- `/readyz` - Readiness
- `/metrics` - Prometheus metrics

## Deployment

### Container-Based
```
Load Balancer
    ↓
API Pods (2+) ──→ PostgreSQL (primary + replicas)
    ↓
Redis Cluster
```

### High Availability
- Multiple API replicas
- Database replication
- Redis Sentinel
- Load balancing

## Documentation

### Detailed Documentation

- **[Complete Architecture Guide](ARCHITECTURE.md)** - In-depth technical details
- **[Data Model](../DATABASE.md)** - Database schema
- **[API Design](../api/API.md)** - API conventions
- **[Deployment](../deployment/README.md)** - Production deployment

### Component Documentation

- **[Connector Service](../connector/IMPLEMENTATION.md)** - Multi-cloud sync architecture
- **[gRPC API](../grpc/README.md)** - gRPC implementation
- **[Monitoring](../logging/README.md)** - Logging and metrics

## Scalability

### Current Limits
- Entities: Tested to 100K entities
- API: 1000+ req/sec per instance
- Graph rendering: 1000 nodes @ < 2sec

### Scaling Strategies
- Horizontal: Add more API pods
- Vertical: Increase database resources
- Caching: Aggressive cache strategy
- Sharding: Future consideration

## Future Architecture

### Planned Enhancements
1. **Event-Driven** - Kafka for real-time events
2. **Microservices** - Split connector, graph services
3. **Multi-Region** - Geographic distribution
4. **GraphQL** - Alternative API interface
5. **CQRS** - Separate read/write models

## Further Reading

- [API Documentation](../api/README.md)
- [Database Schema](../DATABASE.md)
- [Development Setup](../development/README.md)
- [Deployment Guide](../deployment/README.md)
- [Connector Architecture](../connector/IMPLEMENTATION.md)
