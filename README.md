# Elder

[![Continuous Integration](https://github.com/penguintechinc/elder/actions/workflows/ci.yml/badge.svg)](https://github.com/penguintechinc/elder/actions/workflows/ci.yml)
[![Docker Build](https://github.com/penguintechinc/elder/actions/workflows/docker-build.yml/badge.svg)](https://github.com/penguintechinc/elder/actions/workflows/docker-build.yml)
[![Test Coverage](https://codecov.io/gh/penguintechinc/elder/branch/main/graph/badge.svg)](https://codecov.io/gh/penguintechinc/elder)
[![Version](https://img.shields.io/badge/version-3.1.0-green.svg)](https://github.com/penguintechinc/elder/releases)
[![Python](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Node.js](https://img.shields.io/badge/node.js-18+-green.svg)](https://nodejs.org/)
[![License: Limited AGPL v3](https://img.shields.io/badge/License-Limited_AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)*
[![Docker](https://img.shields.io/badge/docker-latest-blue.svg)](https://hub.docker.com/r/penguintechinc/elder)
[![MariaDB Galera](https://img.shields.io/badge/MariaDB_Galera-supported-green.svg)](https://mariadb.com/kb/en/galera-cluster/)

_*Limited AGPL v3 with preamble for fair use - Personal and Internal Use Only_

```
███████╗██╗     ██████╗ ███████╗██████╗
██╔════╝██║     ██╔══██╗██╔════╝██╔══██╗
█████╗  ██║     ██║  ██║█████╗  ██████╔╝
██╔══╝  ██║     ██║  ██║██╔══╝  ██╔══██╗
███████╗███████╗██████╔╝███████╗██║  ██║
╚══════╝╚══════╝╚═════╝ ╚══════╝╚═╝  ╚═╝

Resource, Entity, Element & Relationship Tracking System
```

<p align="center">
  <img src="Elder-Logo.png" alt="Elder Logo" width="200">
</p>

> **Enterprise-grade infrastructure dependency tracking and visualization**

**Elder** is a comprehensive resource, entity, element, and relationship tracking system designed for modern infrastructure management. Track dependencies, visualize relationships, and maintain control across complex organizational structures.

> ✅ **MariaDB Galera Cluster Compatible** - Full support for multi-master replication and high-availability deployments

🌐 **[Website](https://elder.penguintech.io)** | 📚 **[Documentation](https://elder-docs.penguintech.io)** | 💬 **[Discussions](https://github.com/penguintechinc/elder/discussions)**

## Overview

Elder provides visibility into your infrastructure and organizational relationships through:

### Resource Types (Dedicated Models)
Resources have dedicated database models with specialized schemas for better data modeling:

- **Identity**: Users, service accounts, API keys with multi-provider sync (Okta, LDAP, AWS, GCP)
- **Software**: Track applications, libraries, and tools with SBOM integration
- **Services**: Microservices with endpoints, health checks, and on-call rotations
- **Network**: VPCs, subnets, firewalls, load balancers with topology mapping
- **IPAM**: IP address management with prefixes, addresses, and VLANs
- **Data Stores**: S3, GCS, Azure Blob, NAS, SAN, databases with compliance metadata (PII, PHI, PCI)

### Entity Types (Generic Tracking)
Entities use a flexible schema for infrastructure components:

| Category | Sub-types |
|----------|-----------|
| **Network** | Subnet, Firewall, Proxy, Router, Switch, Hub, Tunnel, Route Table, VRRF, VXLAN, VLAN, Namespace |
| **Compute** | Server, Serverless, Laptop, Mobile, Desktop, Kubernetes Node, VM, K8s Cluster, Function Run |
| **Storage** | Hard Disk, NVMe, SSD, Virtual Disk, External Drive, Database, Caching, Queue System |
| **Datacenter** | Public VPC, Private VPC, Physical, Closet |
| **Security** | Vulnerability, Architectural, Config, Compliance, Code, Regulatory |

### Elements (Supporting Items)
- **Issues**: Problem/task tracking attached to any resource or entity
- **Labels**: Categorization and tagging system
- **Metadata Fields**: Custom properties for extensibility
- **Dependencies**: Relationship mapping between items
- **Comments**: Collaboration and audit trail
- **Milestones**: Timeline tracking tied to projects and goals
- **On-Call Rotations**: Schedule duty rotations with automatic participant cycling
- **License Policies**: License key and feature entitlement management

### Core Capabilities
- **Dependency Mapping**: Visualize relationships between entities
- **Organizational Hierarchy**: Manage Company → Department → Team structures
- **Unified IAM**: Manage identities across AWS, Azure, GCP, Okta, LDAP with group management
- **SSO Integration**: SAML 2.0, OpenID Connect (OIDC), and SCIM 2.0 provisioning
- **Secrets Management**: Integrate with Vault, AWS Secrets Manager, GCP Secret Manager
- **Network Topology**: Track VPCs, subnets, peering, VPN connections
- **Project Sync**: Bi-directional sync with GitHub, GitLab, Jira, Trello, OpenProject
- **Enterprise Features**: Audit logging, RBAC, MFA, SSO, multi-tenant, license management
- **Backups**: S3/cloud backup jobs with scheduling and point-in-time restore
- **Webhooks**: Event-driven notifications for entity and issue lifecycle events
- **SBOM Dashboard**: Software Bill of Materials inventory with vulnerability tracking
- **Multi-Tenancy**: Tenant isolation and management for enterprise deployments
- **Global Search**: Full-text search across all resource types and entities
- **Audit Logging**: Comprehensive action logging with admin filtering
- **Network Topology Map**: Interactive visualization of infrastructure relationships

## Screenshots

### Login & Dashboard

<table>
<tr>
<td width="50%">
<a href="docs/screenshots/login.png" target="_blank">
  <img src="docs/screenshots/login.png" alt="Login" style="max-width: 100%;">
</a>
<p align="center"><em>Login</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/dashboard.png" target="_blank">
  <img src="docs/screenshots/dashboard.png" alt="Dashboard" style="max-width: 100%;">
</a>
<p align="center"><em>Dashboard</em></p>
</td>
</tr>
</table>

### Asset Management

<table>
<tr>
<td width="50%">
<a href="docs/screenshots/organizations.png" target="_blank">
  <img src="docs/screenshots/organizations.png" alt="Organizations" style="max-width: 100%;">
</a>
<p align="center"><em>Organizations</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/entities.png" target="_blank">
  <img src="docs/screenshots/entities.png" alt="Entities" style="max-width: 100%;">
</a>
<p align="center"><em>Entities</em></p>
</td>
</tr>
<tr>
<td width="50%">
<a href="docs/screenshots/software.png" target="_blank">
  <img src="docs/screenshots/software.png" alt="Software" style="max-width: 100%;">
</a>
<p align="center"><em>Software</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/services.png" target="_blank">
  <img src="docs/screenshots/services.png" alt="Services" style="max-width: 100%;">
</a>
<p align="center"><em>Services</em></p>
</td>
</tr>
<tr>
<td width="50%">
<a href="docs/screenshots/data-stores.png" target="_blank">
  <img src="docs/screenshots/data-stores.png" alt="Data Stores" style="max-width: 100%;">
</a>
<p align="center"><em>Data Stores (v3.0.0)</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/dependencies.png" target="_blank">
  <img src="docs/screenshots/dependencies.png" alt="Dependencies" style="max-width: 100%;">
</a>
<p align="center"><em>Dependencies</em></p>
</td>
</tr>
</table>

### Project Tracking

<table>
<tr>
<td width="50%">
<a href="docs/screenshots/projects.png" target="_blank">
  <img src="docs/screenshots/projects.png" alt="Projects" style="max-width: 100%;">
</a>
<p align="center"><em>Projects</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/issues.png" target="_blank">
  <img src="docs/screenshots/issues.png" alt="Issues" style="max-width: 100%;">
</a>
<p align="center"><em>Issues</em></p>
</td>
</tr>
</table>

### Security & Identity

<table>
<tr>
<td width="50%">
<a href="docs/screenshots/identities.png" target="_blank">
  <img src="docs/screenshots/identities.png" alt="Identity Center" style="max-width: 100%;">
</a>
<p align="center"><em>Identity Center</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/secrets.png" target="_blank">
  <img src="docs/screenshots/secrets.png" alt="Secrets Management" style="max-width: 100%;">
</a>
<p align="center"><em>Secrets Management</em></p>
</td>
</tr>
<tr>
<td width="50%">
<a href="docs/screenshots/keys.png" target="_blank">
  <img src="docs/screenshots/keys.png" alt="API Keys" style="max-width: 100%;">
</a>
<p align="center"><em>API Keys</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/certificates.png" target="_blank">
  <img src="docs/screenshots/certificates.png" alt="Certificates" style="max-width: 100%;">
</a>
<p align="center"><em>Certificates</em></p>
</td>
</tr>
</table>

### Discovery & Profile

<table>
<tr>
<td width="50%">
<a href="docs/screenshots/discovery.png" target="_blank">
  <img src="docs/screenshots/discovery.png" alt="Discovery" style="max-width: 100%;">
</a>
<p align="center"><em>Discovery</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/profile.png" target="_blank">
  <img src="docs/screenshots/profile.png" alt="Profile" style="max-width: 100%;">
</a>
<p align="center"><em>Profile</em></p>
</td>
</tr>
</table>

## Key Features

### Core Capabilities
- ✅ **Dual Data Model**: 6 Resource types (dedicated schemas) + 5 Entity categories (flexible schema)
- ✅ **Multi-Entity Support**: 5 entity categories with 40+ sub-types
- ✅ **Hierarchical Organizations**: Unlimited depth organizational structures
- ✅ **Dependency Graphs**: Visualize complex entity relationships
- ✅ **Full RBAC**: Role-based permissions with org-scoped access
- ✅ **Multi-Auth**: Local, SAML, OAuth2, OIDC, and LDAP authentication
- ✅ **RESTful & gRPC APIs**: Complete API coverage
- ✅ **Audit Logging**: Comprehensive audit trail for compliance
- ✅ **MariaDB Galera**: Full support for multi-master MySQL clustering

### v3.1.0 Highlights (Latest)
- **Periodic Access Review System**: Automated quarterly/annual access reviews for identity groups with Okta sync (Enterprise)
  - Background scheduler creates reviews hourly; owners review members with Keep/Remove/Extend decisions
  - Full audit logging for compliance; member removals auto-sync to Okta
  - IAM → Access Reviews tab with real-time progress tracking and overdue warnings
- **LoginPageBuilder Integration**: Migrated login page to `react-libs` LoginPageBuilder for consistent UX
- **LXD Compute Sub-types**: Added LXD Container and LXD VM as entity sub-types under Compute
- **Version Injection**: APP_VERSION, VITE_VERSION, and VITE_BUILD_TIME injected into containers at build time
- **Playwright Web UI Test Suite**: 18 browser automation tests covering all pages, navigation, forms, and modals
- **K8s Deployment Standardization**: Helm + Kustomize values for alpha (`.localhost.local`) and beta (`.penguintech.cloud`)

### v3.0.x Highlights
- **v3.0.9**: Connector entity client fixes (removed invalid update fields, added sub_type support); Express and dependency security updates
- **OpenID Connect (OIDC)**: Full OIDC support alongside SAML for SSO integration
- **Data Stores Tracking**: Track S3, GCS, Azure Blob, NAS, SAN, databases, and data lakes with compliance metadata (PII, PHI, PCI flags)
- **Group Membership Management**: Approval workflows, access requests, owner reviews, and multi-provider write-back (LDAP + Okta)
- **Okta Connector**: Full Okta identity provider with bidirectional sync and group management
- **SCIM 2.0 Provisioning**: Complete SCIM user provisioning with JIT provisioning support
- **Enhanced Key Management**: Improved crypto key schema with provider ARN, key types, and state tracking
- **On-Call Rotation Management**: Schedule and manage on-call duty rotations with history tracking
- **Milestones**: Project milestone tracking and progress management
- **License Policy Management**: Enterprise license key and feature entitlement management
- **Webhooks System**: Event-driven notifications with test and retry capabilities
- **Network Topology Visualization**: Interactive map of infrastructure relationships
- **Sub-task Support**: Hierarchical issue tracking with parent-child task relationships
- **Shared Component Library**: Unified react_libs for consistent UI across all forms and modals

### v2.x Highlights
- **Unified Identity Center**: Single page for all identity types (Users, Groups, Service Accounts, API Keys)
- **Multi-backend Secrets**: HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager, Infisical
- **Network Topology**: VPCs, Subnets, Firewalls, Load Balancers with connection mapping
- **Project Sync**: Bi-directional sync with GitHub, GitLab, Jira, Trello, OpenProject
- **Cloud Connectors**: AWS, GCP, Kubernetes, Google Workspace, LDAP, iBoss, vCenter, FleetDM
- **SSL/TLS Certificate Management**: Track certificates with expiration, renewal, and compliance
- **Village ID System**: Universal hierarchical identifiers for all resources

### License Tiers

Elder uses a fair-use licensing model with the Limited AGPL v3 license:

- **Personal & Internal Use**: Free for individual and internal organizational use
- **Commercial Use**: Requires a commercial license from Penguin Tech Inc
- **Modifications**: Must be shared under the same license terms (AGPL)
- **SaaS Deployment**: Requires commercial license if providing Elder as a service

For commercial licensing inquiries: sales@penguintech.io

## Quick Start

### Prerequisites

- **Docker & Docker Compose V2**: Required for all services
- **Python 3.12+**: Backend API (included in Docker)
- **Node.js 18+**: Web UI build (included in Docker)
- **PostgreSQL 17**: Database (included in Docker Compose)
- **Redis 7**: Cache and session storage (included in Docker Compose)

### Installation

```bash
# Clone the repository
git clone https://github.com/penguintechinc/elder.git
cd elder

# Run setup
make setup

# Edit configuration
nano .env

# Start development environment
make dev
```

Access the services:
- **Elder Web UI**: http://localhost:3005
- **Elder API**: http://localhost:4000
- **API Docs**: http://localhost:4000/api/docs

### Docker Deployment

```bash
# Start all services
docker compose up -d

# Check health
curl http://localhost:4000/healthz
```

### Kubernetes Deployment

Elder supports deployment to Kubernetes clusters (MicroK8s, kind, k3s, or standard Kubernetes) using Helm.

**Quick Local Deployment:**

```bash
# Install to local Kubernetes cluster
cd infrastructure/helm/elder
helm dependency update
helm install elder . \
  --set config.secretKey="$(openssl rand -base64 32)" \
  --set postgresql.auth.password="$(openssl rand -base64 32)" \
  --set redis.auth.password="$(openssl rand -base64 32)"

# Wait for deployment
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=elder --timeout=5m

# Access via port-forward
kubectl port-forward svc/elder-api 8080:80
kubectl port-forward svc/elder-web 3000:80
```

**GitHub Actions CI/CD:**

Elder includes automated Kubernetes deployment via GitHub Actions. To set up:

```bash
# 1. Run the setup script on your cluster
./scripts/k8s/setup-github-serviceaccount.sh

# 2. Add the output secrets to GitHub:
#    - KUBE_CONFIG
#    - K8S_NAMESPACE
#    - SECRET_KEY
#    - POSTGRES_PASSWORD
#    - REDIS_PASSWORD

# 3. Push to main branch - automatic deployment!
```

**Resources:**
- 📖 [Local Kubernetes Setup Guide](docs/deployment/local-kubernetes-setup.md)
- 🔧 [GitHub Actions Kubernetes Deployment](docs/deployment/github-actions-k8s.md)
- ⚙️ [Helm Chart Documentation](infrastructure/helm/elder/README.md)

## Configuration

Key environment variables:

```bash
# Database (PyDAL supports PostgreSQL, MySQL/MariaDB, SQLite, Oracle, MSSQL)
# PostgreSQL (recommended)
DATABASE_URL=postgresql://elder:password@localhost:5432/elder

# MariaDB Galera Cluster (high availability)
# DATABASE_URL=mysql://elder:password@galera-node1:3306/elder?wsrep_sync_wait=1

# Redis
REDIS_URL=redis://:password@localhost:6379/0

# Authentication
SAML_ENABLED=true
OIDC_ENABLED=true
OAUTH2_ENABLED=true
LDAP_ENABLED=true

# License (optional)
LICENSE_KEY=PENG-XXXX-XXXX-XXXX-XXXX-XXXX

# Admin User
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
ADMIN_EMAIL=admin@example.com
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Client Layer                         │
│  React UI │ REST Clients │ gRPC Clients                 │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                   API Layer                             │
│  Flask REST │ gRPC Server │ WebSocket                   │
│  JWT Auth │ RBAC │ Rate Limiting                        │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                   Data Layer                            │
│  PyDAL (PostgreSQL, MySQL/MariaDB Galera, SQLite, etc.)│
│  Redis/Valkey (Cache, Sessions)                         │
└─────────────────────────────────────────────────────────┘
```

### Technology Stack

- **Backend**: Flask (Python 3.13), PyDAL
- **Frontend**: React, TypeScript, Vite, Tailwind CSS, ReactFlow
- **Database**: PostgreSQL (recommended), MySQL/MariaDB Galera, SQLite, Oracle, MSSQL
- **Cache**: Redis / Valkey
- **APIs**: REST (OpenAPI 3.0), gRPC
- **Auth**: JWT, SAML, OIDC, OAuth2, LDAP, SCIM 2.0
- **Connectors**: AWS, GCP, Kubernetes, Okta, LDAP, vCenter, FleetDM, iBoss
- **Monitoring**: Prometheus, Grafana

## Scanners & Integrations

### Scanners
Elder includes built-in scanners for automated discovery and security analysis:

| Scanner | Description |
|---------|-------------|
| **Network Scanner** | Discover hosts, open ports, and network topology |
| **Banner Scanner** | Grab service banners for version identification |
| **HTTP Screenshot** | Capture screenshots of web services for visual inventory |
| **SBOM Scanner** | Software Bill of Materials generation and vulnerability detection |

### Connectors (Integrators)
Bi-directional sync with identity providers and infrastructure platforms:

| Connector | Capabilities |
|-----------|-------------|
| **AWS** | EC2, VPC, IAM, S3, RDS discovery and sync |
| **GCP** | Compute Engine, VPC, IAM, Cloud Storage sync |
| **Kubernetes** | Clusters, namespaces, deployments, services |
| **Okta** | Users, groups, applications with write-back |
| **LDAP/AD** | Directory users and groups with bidirectional sync |
| **Google Workspace** | Users, groups, organizational units |
| **vCenter** | VMware VMs, hosts, clusters, datastores |
| **FleetDM** | Endpoint management and osquery integration |
| **iBoss** | Cloud security gateway policy sync |
| **Authentik** | Open-source identity provider integration |

### SBOM Parsers
Parse dependency files from multiple ecosystems for vulnerability tracking:

| Parser | File Types |
|--------|------------|
| **Python** | requirements.txt, setup.py, pyproject.toml, Pipfile |
| **Node.js** | package.json, package-lock.json, yarn.lock, pnpm-lock.yaml |
| **Go** | go.mod, go.sum |
| **Rust** | Cargo.toml, Cargo.lock |
| **Java/Maven** | pom.xml |
| **Gradle** | build.gradle, build.gradle.kts |
| **.NET** | csproj, fsproj, packages.config |

### Endpoint Parsers
Discover API endpoints from source code for service mapping:

- **Flask** (Python)
- **FastAPI** (Python)
- **Django** (Python)
- **Express** (Node.js)
- **Go** (net/http, Gin, Echo)

## Documentation

| Document | Description |
|----------|-------------|
| [API Reference](docs/API.md) | REST & gRPC API documentation |
| [Database Schema](docs/DATABASE.md) | Database structure and PyDAL usage |
| [Sync Documentation](docs/SYNC.md) | Project management sync setup |
| [Backup Configuration](docs/S3_BACKUP_CONFIGURATION.md) | S3 backup setup |
| [Usage Guide](docs/USAGE.md) | User guide and workflows |
| [Contributing](docs/CONTRIBUTING.md) | Contribution guidelines |
| [Release Notes](docs/RELEASE_NOTES.md) | Version history |

## Development

```bash
# Development
make dev              # Start postgres and redis
make dev-api          # Start Flask API
make dev-all          # Start all services

# Testing
make test             # Run all tests
make lint             # Run linters
make format           # Format code

# Docker
make docker-build     # Build Docker image
make docker-scan      # Scan for vulnerabilities
```

## Security

- ✅ Multi-factor authentication
- ✅ Fine-grained RBAC with org-scoped permissions
- ✅ TLS 1.3 enforcement
- ✅ Input validation with PyDAL validators
- ✅ SQL injection prevention
- ✅ Audit logging
- ✅ Container scanning with Trivy

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](docs/CONTRIBUTING.md) for guidelines.

## License

Elder is licensed under the Limited AGPL v3 with Fair Use Preamble. See [LICENSE.md](docs/LICENSE.md) for details.

**License Highlights:**
- **Personal & Internal Use**: Free under AGPL-3.0
- **Commercial Use**: Requires commercial license
- **SaaS Deployment**: Requires commercial license if providing Elder as a service

### Contributor Employer Exception (GPL-2.0 Grant)

Companies employing official contributors receive GPL-2.0 access to community features:

- **Perpetual for Contributed Versions**: GPL-2.0 rights to versions where the employee contributed remain valid permanently, even after the employee leaves the company
- **Attribution Required**: Employee must be credited in CONTRIBUTORS, AUTHORS, commit history, or release notes
- **Future Versions**: New versions released after employment ends require standard licensing
- **Community Only**: Enterprise features still require a commercial license

This exception rewards contributors by providing lasting fair use rights to their employers. See [LICENSE.md](docs/LICENSE.md) for full terms.

## Support

- **Company Homepage**: [www.penguintech.io](https://www.penguintech.io)
- **Documentation**: [docs.penguintech.io/elder](https://docs.penguintech.io/elder)
- **Issues**: [GitHub Issues](https://github.com/penguintechinc/elder/issues)
- **Email**: support@penguintech.io

## Default Login Credentials

For local development and testing, Elder creates a default admin user:

| Field | Value |
|-------|-------|
| **URL** | http://localhost:3005 |
| **Email** | admin@localhost.local |
| **Password** | admin123 |
| **Tenant** | System (ID: 1) |

> **Warning**: Change the default password immediately in production environments by setting the `ADMIN_PASSWORD` environment variable before first startup.

---

**Elder** - Know Your Infrastructure, Understand Your Dependencies

© 2025-2026 Penguin Tech Inc. All rights reserved.
