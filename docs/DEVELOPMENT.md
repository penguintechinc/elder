# Elder Local Development Guide

Complete guide to setting up a local development environment for Elder, running the application locally, and following the development workflow including testing and pre-commit checks.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Initial Setup](#initial-setup)
3. [Starting Development Environment](#starting-development-environment)
4. [Development Workflow](#development-workflow)
5. [Common Tasks](#common-tasks)
6. [Village ID System](#village-id-system)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### System Requirements

- **macOS 12+**, **Linux (Ubuntu 20.04+)**, or **Windows 10+ with WSL2**
- **Docker Desktop** 4.0+ or **Docker Engine 20.10+** with **Docker Compose V2** (plugin, not legacy v1)
- **Git** 2.30+
- **Python** 3.13+ (for API development)
- **Node.js** 18+ (for Web UI development)
- **Go** 1.23+ (optional, for connector services)

### Optional Tools

- **Docker Buildx** (for multi-architecture builds)
- **Helm** (for Kubernetes deployments)
- **kubectl** (for Kubernetes clusters)
- **jq** (for JSON parsing in scripts)

### Installation

**macOS (Homebrew)**:
```bash
brew install docker docker-compose git python node go
brew install --cask docker
```

**Ubuntu/Debian**:
```bash
sudo apt-get update
sudo apt-get install -y docker.io git python3.13 nodejs golang-1.23
sudo usermod -aG docker $USER  # Allow docker without sudo
newgrp docker                   # Activate group change
```

**Docker Compose V2 Installation** (if not already included):
```bash
mkdir -p ~/.docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o ~/.docker/cli-plugins/docker-compose
chmod +x ~/.docker/cli-plugins/docker-compose
docker compose version  # Verify installation
```

**Verify Installation**:
```bash
docker --version          # Docker 20.10+
docker compose version    # Docker Compose V2
git --version
python3 --version         # Python 3.13+
node --version            # Node.js 18+
```

---

## Initial Setup

### Clone Repository

```bash
git clone https://github.com/penguintechinc/elder.git
cd elder
```

### Install Dependencies

```bash
# Install all project dependencies
make setup

# This runs:
# - Python virtual environment setup (venv, requirements)
# - Node.js dependency installation (npm install)
# - Go module setup (if applicable)
# - Pre-commit hooks installation
# - Database initialization
```

### Environment Configuration

Copy and customize environment files:

```bash
# Copy example environment file
cp .env.example .env

# Edit for your local setup
nano .env
```

**Key Environment Variables for Elder**:

```bash
# Database (PostgreSQL recommended for development)
DB_TYPE=postgres
DB_HOST=localhost
DB_PORT=5432
DB_NAME=elder
DB_USER=elder
DB_PASSWORD=elder

# OR use full connection URL
DATABASE_URL=postgres://elder:elder@localhost:5432/elder

# Redis (for caching and sessions)
REDIS_URL=redis://localhost:6379/0
REDIS_PASSWORD=

# Application Ports
API_PORT=5000              # Flask API
WEB_PORT=3005              # React Web UI
ADMINER_PORT=8080          # Database UI

# Authentication (development)
FLASK_ENV=development
FLASK_DEBUG=1
SECRET_KEY=your-secret-key-for-dev

# License (Development - all features available)
RELEASE_MODE=false
LICENSE_KEY=not-required-in-dev

# Admin User (auto-created on first startup)
ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@localhost
ADMIN_PASSWORD=admin123

# Guest Login (optional read-only access)
ENABLE_GUEST_LOGIN=false
```

### Database Initialization

```bash
# Start database and services
make dev

# Run migrations (auto-run on startup, but can be manual)
make db-init

# Seed with mock data (3-4 items per entity)
make seed-mock-data

# Seed native unified-model demo data (support issues, CRM, intake forms, webhooks)
make seed-demo-unified

# Verify database connection
make db-health
```

---

## Starting Development Environment

### Quick Start (All Services)

```bash
# Start all services in one command
make dev

# This runs:
# - PostgreSQL database (port 5432)
# - Redis cache (port 6379)
# - Flask API (port 5000)
# - React Web UI (port 3005)
# - Adminer (database UI on port 8080)

# Access the services:
# Web UI:      http://localhost:3005
# API:         http://localhost:5000
# API Docs:    http://localhost:5000/api/docs
# Database UI: http://localhost:8080 (server: postgres, user: elder, password: elder)
```

### Individual Service Management

**Start specific services**:
```bash
# Start only API
docker compose up -d postgres redis api

# Start only Web UI
docker compose up -d postgres redis web

# Start without detaching (see logs in real-time)
docker compose up postgres redis api
```

**View service logs**:
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api

# Last 100 lines, follow new entries
docker compose logs -f --tail=100 web
```

**Stop and manage services**:
```bash
# Stop all services (keep data)
docker compose down

# Stop and remove volumes (clean slate - DESTRUCTIVE)
docker compose down -v

# Restart services (WARNING: does NOT load new code - use rebuild below)
docker compose restart

# Rebuild and restart (apply code changes)
docker compose down && docker compose up -d --build
```

### Service Health Checks

```bash
# Check all services are running
docker compose ps

# Test API health
curl http://localhost:5000/healthz

# Expected output:
# {"service":"elder","status":"healthy","version":"3.0.0"}

# Test authentication (login)
curl -X POST http://localhost:5000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

---

## Development Workflow

### 1. Start Development Environment

```bash
make dev              # Start all services
make seed-mock-data   # Populate with test data (3-4 items per entity)
make seed-demo-unified # Populate unified-model demo data (support issues, CRM, intake forms, webhooks)
```

### 2. Make Code Changes

Edit files in your favorite editor. Services auto-reload:

- **Python (Flask)**: Reload on file save (FLASK_DEBUG=1)
- **Node.js (React)**: Hot reload (Vite dev server)
- **Go**: Requires container restart (`docker compose restart service-name`)

### 3. Verify Changes

```bash
# Quick smoke tests (fast, <2 min)
make smoke-test

# Run linters
make lint

# Run unit tests (specific service)
cd apps/api && python -m pytest tests/unit/

# Run all tests
make test

# Full pre-commit checklist
./scripts/pre-commit/pre-commit.sh
```

### 4. Testing with Mock Data

After implementing a feature, test it with realistic data:

```bash
# Populate database with mock data
make seed-mock-data

# This creates sample:
# - Organizations with hierarchy
# - Entities (servers, services, networks)
# - Resources (identities, software, data stores)
# - Dependencies and relationships
# - Village IDs for all created items

# For the unified Issues/support/CRM model specifically:
make seed-demo-unified

# This creates sample:
# - Native support issues (issue_type=support) with polymorphic assignees
# - customer_company / customer_contact CRM entities
# - Intake forms (public and private)
# - Assignment (issue.assigned) webhooks
```

### 5. Village ID System Testing

Elder uses Village IDs for universal item identification. Test the Village ID lookup:

```bash
# Get a Village ID from a created item
curl http://localhost:5000/api/v1/organizations \
  -H "Authorization: Bearer $TOKEN" | jq '.items[0].village_id'

# Lookup item by Village ID (no prefix needed)
VILLAGE_ID="a1b2-c3d4-e5f67890"
curl "http://localhost:5000/id/${VILLAGE_ID}" \
  -H "Authorization: Bearer $TOKEN"

# Expected response includes:
# - item_type (organization, entity, resource, etc.)
# - village_id, tenant_id, org_id
# - Resource details with full context
```

### 6. Run Pre-Commit Checklist

Before committing, run the comprehensive pre-commit script:

```bash
./scripts/pre-commit/pre-commit.sh
```

**Steps**:
1. ✅ Linters (flake8, black, eslint, golangci-lint, etc.)
2. ✅ Security scans (bandit, npm audit, gosec)
3. ✅ Secret detection (no API keys, passwords, tokens)
4. ✅ Build & Run (build all containers, verify runtime)
5. ✅ Smoke tests (build, health checks, UI loads)
6. ✅ Unit tests (isolated component testing)
7. ✅ Integration tests (component interactions)
8. ✅ Version update & Docker standards

**Troubleshooting Pre-Commit**: See [PRE_COMMIT.md](PRE_COMMIT.md) for detailed guidance.

### 7. Testing & Validation

Comprehensive testing guide available in [TESTING.md](TESTING.md):

**Quick Test Commands**:
```bash
# Smoke tests only (fast, <2 min)
make smoke-test

# Unit tests only
make test-unit

# Integration tests
make test-integration

# API tests (test Village ID lookup, auth, CRUD)
make test-api

# All tests
make test

# Specific test file
python -m pytest tests/unit/test_auth.py

# Cross-architecture testing (QEMU)
make test-multiarch
```

### 8. Create Pull Request

Once tests pass:

```bash
# Push branch
git push origin feature-branch-name

# Create PR via GitHub CLI
gh pr create --title "Brief feature description" \
  --body "Detailed description of changes"

# Or use web UI: https://github.com/penguintechinc/elder/compare
```

---

## Common Tasks

### Adding a New Python Dependency

```bash
# Add to apps/api/requirements.txt
echo "new-package==1.0.0" >> apps/api/requirements.txt

# Rebuild API container
docker compose up -d --build api

# Verify import works
docker compose exec api python -c "import new_package"
```

### Adding a New Node.js Dependency

```bash
# Add to web/package.json
npm install new-package

# Rebuild Web UI container
docker compose up -d --build web

# Verify in running container
docker compose exec web npm list new-package
```

### Adding a New Environment Variable

```bash
# Add to .env
echo "NEW_VAR=value" >> .env

# Restart services to pick up new variable
docker compose restart

# Verify it's set
docker compose exec api printenv | grep NEW_VAR
```

### Debugging a Service

**View logs in real-time**:
```bash
docker compose logs -f api
```

**Access container shell**:
```bash
# Python API service
docker compose exec api bash

# Node.js Web UI service
docker compose exec web bash
```

**Execute commands in container**:
```bash
# Run Python script
docker compose exec api python -c "print('hello')"

# Check API health
docker compose exec api curl http://localhost:5000/healthz

# Check database connection
docker compose exec api python -c "from app.models import db; print('Connected')"
```

### Database Operations

**Connect to database**:
```bash
# PostgreSQL via psql
docker compose exec postgres psql -U elder -d elder

# View tables
\dt

# View schema for a table
\d organizations
```

**Reset database**:
```bash
# Full reset (deletes all data)
docker compose down -v
make db-init
make seed-mock-data
make seed-demo-unified
```

**Run migrations**:
```bash
# Auto-migrate on startup
docker compose restart api

# Or manually run migration
docker compose exec api python -m alembic upgrade head
```

### Working with Git Branches

```bash
# Create feature branch
git checkout -b feature/new-feature-name

# Keep branch updated with main
git fetch origin
git rebase origin/main

# Clean commit history before PR
git rebase -i origin/main  # Interactive rebase

# Push branch
git push origin feature/new-feature-name
```

### Database Backups

```bash
# Backup PostgreSQL
docker compose exec postgres pg_dump -U elder elder > backup.sql

# Restore from backup
docker compose exec -T postgres psql -U elder elder < backup.sql

# Backup SQLite (if using)
docker cp elder-postgres:/data/app.db ./app.db.backup
```

---

## Village ID System

Elder uses **Village IDs** for universal item identification across all resources and entities.

### Village ID Structure

**Format**: `TTTT-OOOO-IIIIIIII` (18 chars with dashes)

- `TTTT`: 16-bit tenant segment (4 hex chars) - randomized, unique per tenant
- `OOOO`: 16-bit organization segment (4 hex chars) - randomized, unique per org
- `IIIIIIII`: 32-bit item segment (8 hex chars) - randomized

**Examples**:
- Tenant: `a1b2-0000-00000000` (org and item zeroed)
- Organization: `a1b2-c3d4-00000000` (item zeroed)
- Item: `a1b2-c3d4-e5f67890` (full hierarchy)

### Village ID Benefits

- **Universal Lookup**: Access any item via `/id/{village_id}` without knowing its type
- **Hierarchical Context**: Tenant and org ID embedded in Village ID itself
- **Type Resolution**: System automatically determines resource type on lookup
- **Simplified Sharing**: Share items by Village ID across organizations and contexts

### Working with Village IDs

**Get Village ID from API**:
```bash
# Create an organization
curl -X POST http://localhost:5000/api/v1/organizations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Engineering","description":"Engineering team"}' | jq '.village_id'

# Returns: "a1b2-c3d4-e5f67890"
```

**Lookup by Village ID**:
```bash
VILLAGE_ID="a1b2-c3d4-e5f67890"

curl "http://localhost:5000/id/${VILLAGE_ID}" \
  -H "Authorization: Bearer $TOKEN"

# Returns full resource details with type information
```

**Village ID Generation** (in code):
```python
from apps.api.utils.village_id import VillageIDGenerator

# Generate new Village ID
gen = VillageIDGenerator()
tenant_vid = gen.generate_tenant_id()
org_vid = gen.generate_org_id(tenant_vid)
item_vid = gen.generate_item_id(org_vid)

print(f"Tenant: {tenant_vid}")
print(f"Organization: {org_vid}")
print(f"Item: {item_vid}")
```

---

## Troubleshooting

### Services Won't Start

**Check if ports are already in use**:
```bash
# Find what's using port 5000
lsof -i :5000

# Kill the process
kill -9 <PID>

# Or use different ports in .env
API_PORT=5001
WEB_PORT=3006
```

**Docker daemon not running**:
```bash
# macOS
open /Applications/Docker.app

# Linux
sudo systemctl start docker

# Windows (Docker Desktop)
# Start Docker Desktop from Applications
```

### Docker Compose V2 Issues

**Legacy v1 Errors** (old `docker-compose` command):
```bash
# WRONG (legacy):
docker-compose up

# CORRECT (V2 plugin):
docker compose up

# Verify you're using V2:
docker compose version  # Should show "Docker Compose version 2.x.x"
```

### Database Connection Error

```bash
# Verify database container is running
docker compose ps postgres

# Check database credentials in .env
grep DB_ .env

# Connect to database directly
docker compose exec postgres psql -U elder -d elder

# View logs
docker compose logs postgres
```

### API Backend Won't Start

```bash
# Check logs
docker compose logs api

# Verify database migration
docker compose exec api python -m alembic current

# Reset and rebuild
docker compose down -v
docker compose up -d --build api
make db-init
```

### Smoke Tests Failing

**Check which test failed**:
```bash
# Run individually
./tests/smoke/api/test-api-health.sh
./tests/smoke/ui/test-pages-load.sh
./tests/smoke/build/test-docker-build.sh
```

**Common issues**:
- Service not healthy (logs: `docker compose logs <service>`)
- Port not exposed (check docker-compose.yml)
- API endpoint not implemented
- Missing environment variables
- Database not initialized

See [TESTING.md](TESTING.md#smoke-tests) for detailed troubleshooting.

### Git Merge Conflicts

```bash
# View conflicts
git status

# Edit conflicted files (marked with <<<<, ====, >>>>)
# Remove conflict markers and keep desired code

# Mark as resolved
git add <resolved-file>

# Complete merge
git commit -m "Resolve merge conflicts"
```

### Slow Docker Builds

```bash
# Check Docker disk usage
docker system df

# Clean up unused images/containers
docker system prune

# Rebuild without cache (slow, but fresh)
docker compose build --no-cache api
```

### QEMU Cross-Architecture Build Issues

**QEMU not available**:
```bash
# Install QEMU support
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes

# Verify buildx setup
docker buildx ls
```

**Slow arm64 build with QEMU**:
```bash
# Expected: 2-5x slower with QEMU emulation
# Use only for final validation, not every iteration

# Build native architecture (fast)
docker buildx build --load .

# Build alternate with QEMU (slow)
docker buildx build --platform linux/arm64 .
```

See [TESTING.md](TESTING.md#cross-architecture-testing) for complete details.

---

## Tips & Best Practices

### Hot Reload Development

For fastest iteration:
```bash
# Start services once
docker compose up -d

# Edit Python files → auto-reload (FLASK_DEBUG=1)
# Edit JavaScript/TypeScript files → hot reload (Vite)
# Edit Go files → restart service
```

### Environment-Specific Configuration

```bash
# Development settings (auto-loaded)
.env              # Default development config
.env.local        # Local machine overrides (gitignored)

# Production settings (via secret management)
Kubernetes secrets
AWS Secrets Manager
HashiCorp Vault
```

### Code Organization

Keep project clean:
```bash
# Remove old branches
git branch -D old-branch

# Clean local Docker images
docker image prune -a

# Clean unused containers
docker container prune
```

### Performance Tips

```bash
# Use specific services to reduce memory usage
docker compose up postgres redis api  # Skip web

# Use lightweight testing
make smoke-test  # Instead of full test suite while developing

# Cache Docker layers
# Build in order of frequency of change:
# base → dependencies → code → entrypoint
```

---

## Related Documentation

- **Testing**: [TESTING.md](TESTING.md)
  - Mock data scripts
  - Smoke tests
  - Unit/integration/E2E tests
  - Performance tests
  - Cross-architecture testing

- **Pre-Commit**: [PRE_COMMIT.md](PRE_COMMIT.md)
  - Linting requirements
  - Security scanning
  - Build verification
  - Test requirements

- **Deployment**: [deployment/](deployment/)
  - Docker Compose production
  - Kubernetes deployment
  - Health checks and monitoring

- **Architecture**: [architecture/](architecture/)
  - System design
  - Village ID system
  - Resource types and entities

- **API**: [API.md](API.md) and [api/](api/)
  - REST API documentation
  - Endpoint reference
  - Authentication and authorization

- **Database**: [DATABASE.md](DATABASE.md)
  - Database configuration
  - PyDAL ORM patterns
  - Migration strategies

---

**Last Updated**: 2026-01-06
**Maintained by**: Penguin Tech Inc
**Project**: Elder 3.0.0
