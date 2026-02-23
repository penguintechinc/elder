# Elder Usage Guide

Quick reference guide for using Elder infrastructure tracking platform.

## Table of Contents

- [Getting Started](#getting-started)
- [Core Features](#core-features)
- [v2.0 Features](#v20-features)
- [Docker Compose](#docker-compose)
- [Kubernetes](#kubernetes)
- [Environment Variables](#environment-variables)
- [Common Tasks](#common-tasks)

## Getting Started

### Installation

```bash
# Clone repository
git clone https://github.com/penguintechinc/elder.git
cd elder

# Configure environment
cp .env.example .env
nano .env

# Start services
docker-compose up -d
```

### First Steps

1. **Access Web UI**: http://localhost:3005
2. **Login**: Use admin credentials from `.env`
3. **Create Organization**: Click "Organizations" → "New"
4. **Add Entity**: Click "Entities" → "New"

## Core Features

### Organizations

**Create via Web UI:**
1. Navigate to "Organizations"
2. Click "New Organization"
3. Enter name, type, and description
4. Select parent (optional)
5. Click "Create"

**Create via API:**
```bash
curl -X POST http://localhost:4000/api/v1/organizations \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production",
    "description": "Production infrastructure",
    "organization_type": "department"
  }'
```

### Entities

**Create via Web UI:**
1. Navigate to "Entities"
2. Click "New Entity"
3. Select category and sub-type (e.g., Compute → Virtual Machine)
4. Choose organization
5. Fill in details and metadata
6. Click "Create"

**Create via API:**
```bash
curl -X POST http://localhost:4000/api/v1/entities \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "web-server-01",
    "entity_type": "compute",
    "entity_sub_type": "virtual_machine",
    "organization_id": 1,
    "attributes": {
      "ip": "10.0.1.10",
      "os": "Ubuntu 22.04",
      "cpu": 4,
      "memory_gb": 16
    }
  }'
```

### Dependencies

**Create via Web UI:**
1. Open an entity detail page
2. Click "Add Dependency"
3. Select target entity
4. Choose dependency type
5. Click "Save"

**Create via API:**
```bash
curl -X POST http://localhost:4000/api/v1/dependencies \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "source_entity_id": 10,
    "target_entity_id": 20,
    "dependency_type": "network"
  }'
```

### Graph Visualization

**View Graph:**
1. Navigate to "Graph"
2. Select organization or entity
3. Interact with visualization:
   - Zoom: Scroll wheel
   - Pan: Click and drag
   - Select: Click nodes
   - Details: Double-click nodes

**Get Graph via API:**
```bash
curl http://localhost:4000/api/v1/graph?organization_id=1 \
  -H "Authorization: Bearer <token>"
```

## v2.0 Features

### Unified Identity Management (IAM)

Elder v2.0 provides a unified Identity Center for managing all identity types.

**Supported Identity Types:**
- Users (employees, vendors)
- Groups
- Service Accounts
- API Keys
- Roles

**IAM Providers:**
- AWS IAM
- Azure Active Directory
- GCP IAM
- Okta
- LDAP/Active Directory
- Kubernetes RBAC

**Create Identity via API:**
```bash
curl -X POST http://localhost:4000/api/v1/identities \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "deploy-service",
    "identity_type": "serviceAccount",
    "organization_id": 1,
    "provider": "aws",
    "attributes": {
      "arn": "arn:aws:iam::123456789012:role/deploy-service"
    }
  }'
```

### Secrets Management

Elder integrates with multiple secrets backends.

**Supported Providers:**
- HashiCorp Vault
- AWS Secrets Manager
- GCP Secret Manager
- Infisical
- Built-in encrypted storage

**Create Secret via API:**
```bash
curl -X POST http://localhost:4000/api/v1/secrets \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "database-password",
    "secret_type": "password",
    "organization_id": 1,
    "provider": "builtin",
    "value": "my-secret-password"
  }'
```

### Network Topology

Track network resources and their connections.

**Network Resource Types:**
- VPC
- Subnet
- Firewall
- Load Balancer
- NAT Gateway
- Router
- VPN Gateway

**Connection Types:**
- Peering
- VPN
- Direct Connect
- Transit Gateway
- Route

**Create Network Resource via API:**
```bash
curl -X POST http://localhost:4000/api/v1/networking/resources \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "prod-vpc",
    "network_type": "vpc",
    "organization_id": 1,
    "region": "us-east-1",
    "attributes": {
      "cidr": "10.0.0.0/16"
    }
  }'
```

**Create Network Connection via API:**
```bash
curl -X POST http://localhost:4000/api/v1/networking/topology \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "source_network_id": 1,
    "target_network_id": 2,
    "connection_type": "peering",
    "bandwidth": "10 Gbps"
  }'
```

### Project Management Sync

Bi-directional sync with external project management tools.

**Supported Platforms:**
- GitHub Issues
- GitLab Issues
- Jira
- Trello
- OpenProject

**Configure Sync via API:**
```bash
curl -X POST http://localhost:4000/api/v1/sync/configs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHub Sync",
    "platform": "github",
    "organization_id": 1,
    "config": {
      "owner": "myorg",
      "repo": "myrepo",
      "token": "ghp_xxxx"
    },
    "sync_issues": true,
    "sync_milestones": true
  }'
```

## Docker Compose

### Starting Services

```bash
# Start all services
docker-compose up -d

# Start specific services
docker-compose up -d postgres redis api web

# View logs
docker-compose logs -f

# Follow specific service
docker-compose logs -f api
```

### Stopping Services

```bash
# Stop all services
docker-compose stop

# Stop and remove containers
docker-compose down

# Stop and remove volumes (WARNING: deletes data!)
docker-compose down -v
```

### Service Status

```bash
# Check running services
docker-compose ps

# View resource usage
docker stats
```

## Kubernetes

### Helm Installation

```bash
# Add repository
helm repo add elder https://charts.penguintech.io/elder

# Install
helm install elder elder/elder \
  --namespace elder \
  --create-namespace

# Check status
kubectl get pods -n elder
```

### Kubectl Management

```bash
# View pods
kubectl get pods -n elder

# View logs
kubectl logs -f deployment/elder-api -n elder

# Scale API
kubectl scale deployment elder-api --replicas=3 -n elder
```

## Environment Variables

### Database Configuration

```bash
DATABASE_URL=postgresql://elder:password@localhost:5432/elder
```

### Redis Configuration

```bash
REDIS_URL=redis://:password@localhost:6379/0
```

### Application Configuration

```bash
FLASK_ENV=production
SECRET_KEY=<random-key>
LOG_LEVEL=INFO
```

### Admin User

```bash
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<password>
ADMIN_EMAIL=admin@example.com
```

### Authentication

```bash
SAML_ENABLED=true
SAML_METADATA_URL=https://your-idp.com/metadata
OAUTH2_ENABLED=true
OAUTH2_CLIENT_ID=<client-id>
LDAP_ENABLED=true
LDAP_SERVER=ldap.example.com
```

### License Configuration

```bash
LICENSE_KEY=PENG-XXXX-XXXX-XXXX-XXXX-XXXX
PRODUCT_NAME=elder
LICENSE_SERVER_URL=https://license.penguintech.io
```

### Worker Service

```bash
# AWS
AWS_ENABLED=true
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_REGIONS=us-east-1,us-west-2

# GCP
GCP_ENABLED=true
GCP_PROJECT_ID=<project>
GCP_CREDENTIALS_PATH=/app/credentials/gcp.json

# Google Workspace
GOOGLE_WORKSPACE_ENABLED=true
GOOGLE_WORKSPACE_ADMIN_EMAIL=admin@example.com

# LDAP
LDAP_ENABLED=true
LDAP_SERVER=ldap.example.com
LDAP_BASE_DN=dc=example,dc=com
```

## Common Tasks

### Database Backups

**Create Backup:**
```bash
docker-compose exec postgres pg_dump -U elder elder > backup.sql
```

**Restore Backup:**
```bash
docker-compose exec -T postgres psql -U elder elder < backup.sql
```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f api

# Last N lines
docker-compose logs --tail=100 api
```

### Health Checks

```bash
# API Health
curl http://localhost:4000/healthz

# Database Health
docker-compose exec postgres pg_isready

# Redis Health
docker-compose exec redis redis-cli ping
```

### Monitoring

- **Prometheus**: http://localhost:9091
- **Grafana**: http://localhost:4001 (admin/admin)
- **API Metrics**: http://localhost:4000/metrics

### Worker Service

**Test Connectivity:**
```bash
docker-compose exec worker \
  python3 /app/apps/worker/test_connectivity.py
```

**View Sync Status:**
```bash
curl http://localhost:8000/status
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
docker-compose logs <service>

# Check if port is in use
lsof -i :<port>

# Restart service
docker-compose restart <service>
```

### Database Connection Error

```bash
# Check PostgreSQL is running
docker-compose ps postgres

# Check connection
docker-compose exec postgres psql -U elder -d elder
```

### API Errors

```bash
# View API logs
docker-compose logs api

# Check environment
docker-compose exec api env

# Restart API
docker-compose restart api
```

## Further Reading

- [API Reference](API.md) - REST & gRPC API documentation
- [Database Schema](DATABASE.md) - Database structure
- [Sync Documentation](SYNC.md) - Project management sync
- [Backup Configuration](S3_BACKUP_CONFIGURATION.md) - S3 backup setup
- [Release Notes](RELEASE_NOTES.md) - Version history
