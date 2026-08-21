# Elder

[![Continuous Integration](https://github.com/penguintechinc/elder/actions/workflows/ci.yml/badge.svg)](https://github.com/penguintechinc/elder/actions/workflows/ci.yml)
[![Docker Build](https://github.com/penguintechinc/elder/actions/workflows/docker-build.yml/badge.svg)](https://github.com/penguintechinc/elder/actions/workflows/docker-build.yml)
[![Test Coverage](https://codecov.io/gh/penguintechinc/elder/branch/main/graph/badge.svg)](https://codecov.io/gh/penguintechinc/elder)
[![Version](https://img.shields.io/badge/version-4.0.0-green.svg)](https://github.com/penguintechinc/elder/releases)
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

As of **v4.0.0**, Elder is a **modular monolith**: one deployable platform made of 15 independently toggleable modules, grouped into the five WIRED pillars below. Several formerly separate PenguinTech products (IceCharts, IceStreams, IceFlows, Ruffled) now ship as modules inside it.

> ✅ **MariaDB Galera Cluster Compatible** - Full support for multi-master replication and high-availability deployments

🌐 **[Website](https://elder.penguintech.io)** | 📚 **[Documentation](https://elder-docs.penguintech.io)** | 💬 **[Discussions](https://github.com/penguintechinc/elder/discussions)**

---

## The WIRED Model

Elder is an **internal** platform — it manages your own organization's estate, not your customers'. Everything it tracks falls into one of five pillars, and those five pillars are also how the product is grouped: they name the sidebar sections, the module groups, and the `ELDER_GROUP_*` deployment toggles.

```
  W  Workstreams    things that run on a trigger      streams · flows · webhooks & alerting
  I  Issues         things that need doing            issues · projects · internal tickets
  R  Relationships  edges, discovered and attested    discovery · access reviews
  E  Entities       things you inventory              CMDB · IPAM · SBOM · services · secrets
  D  Documents      things you write down             documents · pages · diagrams
```

| Pillar | What it holds | Modules |
|--------|---------------|---------|
| **W** — Workstreams | Automation that fires on a schedule, an event, or a commit — workflow runs, CI/CD pipelines, outbound webhooks and alerts | `streams`, `flows`, `webhooks_alerting` |
| **I** — Issues | Every unit of work, in one model: operational issues, project tasks, and internal support tickets (`issue_type=support`) | `issues`, `helpdesk` |
| **R** — Relationships | How things connect — edges built automatically by cloud/K8s/IdP discovery, and identity→resource access attested by humans | `discovery`, `access_reviews` |
| **E** — Entities | The inventory itself: the CMDB, IP space, software bill of materials, service catalog, and secrets | `infrastructure`, `ipam`, `sbom`, `services_oncall`, `secrets` |
| **D** — Documents | Written knowledge attached to the estate — knowledge base articles, documentation pages, and diagrams | `documents`, `pages`, `diagrams` |

**Where the graph lives:** the dependency, topology, and relationship-graph views are served by the `infrastructure` module (Entities) — it owns both the objects and the edges between them. The **Relationships** group holds the modules whose *output* is edges: `discovery` creates them from scans, `access_reviews` attests the identity→resource ones.

**Not in Elder:** customer relationships, public intake forms, and community support live in **Waddles**. Elder's helpdesk is strictly internal — employees and contractors.

---

## Modules

All 15 modules are installed in every build and toggled at deploy time. Grouping is presentation and deployment only — **no group is license-tier-locked**.

> ⚠️ `flows` and `helpdesk` are **API-only today** — both expose REST surfaces and worker tasks, but neither has a page in the React app yet, so enabling them adds no sidebar entry.

| Module | Title | Group | License-gated |
|--------|-------|-------|---------------|
| `streams` | Streams (Workflows) | workstreams | — |
| `flows` | Flows (CI/CD) | workstreams | — |
| `webhooks_alerting` | Webhooks & Alerting | workstreams | — |
| `issues` | Issues & Project Tracking | issues | — |
| `helpdesk` | Helpdesk (Internal Ticketing) | issues | — |
| `discovery` | Discovery & Sync | relationships | — |
| `access_reviews` | Access Reviews | relationships | `access-reviews` |
| `infrastructure` | Infrastructure CMDB | entities | — |
| `ipam` | IP Address Management | entities | — |
| `sbom` | Software Bill of Materials | entities | — |
| `services_oncall` | Service Catalog & On-Call | entities | — |
| `secrets` | Secrets Management | entities | — |
| `documents` | Documents & Knowledge Base | documents | — |
| `pages` | Pages & Documentation | documents | — |
| `diagrams` | Diagrams & Drawing | documents | — |

**Toggling**, highest precedence first:

```bash
ELDER_MODULE_HELPDESK=false        # 1. per-module override
ELDER_GROUP_WORKSTREAMS=false      # 2. whole WIRED group
ELDER_MODULES_ENABLED=all          # 3. base set ("all" or a comma-separated list)
```

`GET /api/v1/modules` returns each module's `group`, `licensed`, and `effective` state; the web UI renders only the modules that resolve to `effective: true`, bucketed by pillar in acronym order.

---

## Overview

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
- **Issues**: Unified problem/task tracking attached to any resource or entity — also the native home for internal support tickets (`issue_type=support`), with a polymorphic assignee (identity or organizational unit)
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
- **Workflow Automation**: Streams (event/schedule-driven workflows) and Flows (CI/CD pipelines)
- **Diagramming**: React-Flow diagram editor with real-time collaboration
- **Knowledge Base**: Documents and documentation pages attached to the estate
- **Enterprise Features**: Audit logging, RBAC, MFA, SSO, multi-tenant, license management
- **Backups**: S3/cloud backup jobs with scheduling and point-in-time restore
- **Webhooks**: Event-driven notifications for entity and issue lifecycle events, including `issue.assigned` (filterable by `issue_type` and by assignee identity/org unit)
- **SBOM Dashboard**: Software Bill of Materials inventory with vulnerability tracking
- **Multi-Tenancy**: Tenant isolation and management for enterprise deployments
- **Global Search**: Full-text search across all resource types and entities
- **Audit Logging**: Comprehensive action logging with admin filtering
- **Network Topology Map**: Interactive geo-located visualization of infrastructure relationships
- **MCP Server**: Model Context Protocol server so AI agents can query Elder over the authenticated REST API

## Screenshots

> Captured against seeded mock data (`make seed-mock-data`). The full set lives in [`docs/screenshots/`](docs/screenshots/). Streams and the Relationship Graph do not have captures yet; Flows and the internal Helpdesk have no web UI at all (see Modules above).

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

### Entities

<table>
<tr>
<td width="50%">
<a href="docs/screenshots/entities.png" target="_blank">
  <img src="docs/screenshots/entities.png" alt="Entities" style="max-width: 100%;">
</a>
<p align="center"><em>Entities</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/organizations.png" target="_blank">
  <img src="docs/screenshots/organizations.png" alt="Organizations" style="max-width: 100%;">
</a>
<p align="center"><em>Organizations</em></p>
</td>
</tr>
<tr>
<td width="50%">
<a href="docs/screenshots/compute.png" target="_blank">
  <img src="docs/screenshots/compute.png" alt="Compute" style="max-width: 100%;">
</a>
<p align="center"><em>Compute</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/networking.png" target="_blank">
  <img src="docs/screenshots/networking.png" alt="Networking" style="max-width: 100%;">
</a>
<p align="center"><em>Networking</em></p>
</td>
</tr>
<tr>
<td width="50%">
<a href="docs/screenshots/data-stores.png" target="_blank">
  <img src="docs/screenshots/data-stores.png" alt="Data Stores" style="max-width: 100%;">
</a>
<p align="center"><em>Data Stores</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/ipam.png" target="_blank">
  <img src="docs/screenshots/ipam.png" alt="IPAM" style="max-width: 100%;">
</a>
<p align="center"><em>IPAM</em></p>
</td>
</tr>
<tr>
<td width="50%">
<a href="docs/screenshots/sbom.png" target="_blank">
  <img src="docs/screenshots/sbom.png" alt="SBOM Dashboard" style="max-width: 100%;">
</a>
<p align="center"><em>SBOM Dashboard</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/vulnerabilities.png" target="_blank">
  <img src="docs/screenshots/vulnerabilities.png" alt="Vulnerabilities" style="max-width: 100%;">
</a>
<p align="center"><em>Vulnerabilities</em></p>
</td>
</tr>
</table>

### Relationships

<table>
<tr>
<td width="50%">
<a href="docs/screenshots/dependencies.png" target="_blank">
  <img src="docs/screenshots/dependencies.png" alt="Dependencies" style="max-width: 100%;">
</a>
<p align="center"><em>Dependencies</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/map.png" target="_blank">
  <img src="docs/screenshots/map.png" alt="Topology Map" style="max-width: 100%;">
</a>
<p align="center"><em>Topology Map</em></p>
</td>
</tr>
<tr>
<td width="50%">
<a href="docs/screenshots/discovery.png" target="_blank">
  <img src="docs/screenshots/discovery.png" alt="Discovery" style="max-width: 100%;">
</a>
<p align="center"><em>Discovery &amp; Sync</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/diagram.png" target="_blank">
  <img src="docs/screenshots/diagram.png" alt="Infrastructure Diagram" style="max-width: 100%;">
</a>
<p align="center"><em>Infrastructure Diagram</em></p>
</td>
</tr>
</table>

### Issues

<table>
<tr>
<td width="50%">
<a href="docs/screenshots/issues.png" target="_blank">
  <img src="docs/screenshots/issues.png" alt="Issues" style="max-width: 100%;">
</a>
<p align="center"><em>Issues</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/projects.png" target="_blank">
  <img src="docs/screenshots/projects.png" alt="Projects" style="max-width: 100%;">
</a>
<p align="center"><em>Projects</em></p>
</td>
</tr>
<tr>
<td width="50%">
<a href="docs/screenshots/milestones.png" target="_blank">
  <img src="docs/screenshots/milestones.png" alt="Milestones" style="max-width: 100%;">
</a>
<p align="center"><em>Milestones</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/labels.png" target="_blank">
  <img src="docs/screenshots/labels.png" alt="Labels" style="max-width: 100%;">
</a>
<p align="center"><em>Labels</em></p>
</td>
</tr>
</table>

### Documents

<table>
<tr>
<td width="50%">
<a href="docs/screenshots/documents.png" target="_blank">
  <img src="docs/screenshots/documents.png" alt="Documents" style="max-width: 100%;">
</a>
<p align="center"><em>Documents &amp; Knowledge Base</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/pages.png" target="_blank">
  <img src="docs/screenshots/pages.png" alt="Pages" style="max-width: 100%;">
</a>
<p align="center"><em>Pages &amp; Documentation</em></p>
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

### Administration

<table>
<tr>
<td width="50%">
<a href="docs/screenshots/admin-settings.png" target="_blank">
  <img src="docs/screenshots/admin-settings.png" alt="Admin Settings" style="max-width: 100%;">
</a>
<p align="center"><em>Admin Settings</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/admin-tenants.png" target="_blank">
  <img src="docs/screenshots/admin-tenants.png" alt="Tenants" style="max-width: 100%;">
</a>
<p align="center"><em>Tenants</em></p>
</td>
</tr>
<tr>
<td width="50%">
<a href="docs/screenshots/admin-sso.png" target="_blank">
  <img src="docs/screenshots/admin-sso.png" alt="SSO Configuration" style="max-width: 100%;">
</a>
<p align="center"><em>SSO Configuration</em></p>
</td>
<td width="50%">
<a href="docs/screenshots/admin-audit-logs.png" target="_blank">
  <img src="docs/screenshots/admin-audit-logs.png" alt="Audit Logs" style="max-width: 100%;">
</a>
<p align="center"><em>Audit Logs</em></p>
</td>
</tr>
</table>

## Key Features

### Core Capabilities
- ✅ **WIRED Model**: Workstreams, Issues, Relationships, Entities, Documents — five pillars, five module groups, one platform
- ✅ **Modular Monolith**: 15 modules, each toggleable per-module or per-group at deploy time
- ✅ **Dual Data Model**: 6 Resource types (dedicated schemas) + 5 Entity categories (flexible schema)
- ✅ **Multi-Entity Support**: 5 entity categories with 40+ sub-types
- ✅ **Hierarchical Organizations**: Unlimited depth organizational structures
- ✅ **Dependency Graphs**: Visualize complex entity relationships
- ✅ **Full RBAC**: Role-based permissions with org-scoped access
- ✅ **Multi-Auth**: Local, SAML, OAuth2, OIDC, and LDAP authentication
- ✅ **RESTful & gRPC APIs**: Complete API coverage
- ✅ **Audit Logging**: Comprehensive audit trail for compliance
- ✅ **MariaDB Galera**: Full support for multi-master MySQL clustering
- ✅ **Internal Helpdesk & Ticketing**: Employee/contractor support — tickets with SLA policies, canned responses, and teams, plus `issue_type=support` Issues with a combined identity+org-unit assignee picker and `issue.assigned` assignment webhooks

### v4.0.0 Highlights (Unreleased)

- **Modular monolith restructure** (#164): feature code moved from flat `apps/api/models/*.py` + `apps/api/api/v1/*` into per-module packages `apps/api/modules/<name>/{models,routes}/`. **Breaking**: `apps.api.models.infrastructure` no longer exists.
- **WIRED module groups**: the old `core`/`crm`/`workflow`/`kb` buckets are replaced by the five pillars. **Breaking**: `ELDER_GROUP_CORE`/`CRM`/`WORKFLOW`/`KB` → `ELDER_GROUP_WORKSTREAMS`/`ISSUES`/`RELATIONSHIPS`/`ENTITIES`/`DOCUMENTS`.
- **IceCharts merge** (#167, #168): `diagrams` (React-Flow editor with real-time collaboration), `streams` (workflow automation), `flows` (CI/CD) now ship as Elder modules.
- **Ruffled merge** (#166, #173): `helpdesk`, `documents`, and `pages` merged in. Rookery was intentionally excluded.
- **Issues ↔ tickets unification**: a support request is just an Issue (`issue_type=support`) — no parallel ticket resource. Polymorphic assignee (`identity` or `org_unit`) plus `issue.assigned` HMAC-signed webhooks filterable by type and assignee.
- **Helpdesk is internal-only**: the customer-relations half (CRM records, public intake forms, customer email intake) moved to **Waddles**. Migration `040` drops the removed tables and columns.
- **License enforcement**: per-tenant and global counters for teams, tenants, admins, objects, and nodes, with a `402` `enforce_limit` guard (observe-only flag for staged rollout).
- **Cloud scan relationship linkage**: cloud discovery scans now auto-create dependency edges between discovered resources.

Full history: [docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md).

### License Tiers

Elder uses a fair-use licensing model with the Limited AGPL v3 license:

- **Personal & Internal Use**: Free for individual and internal organizational use
- **Commercial Use**: Requires a commercial license from Penguin Tech Inc
- **Modifications**: Must be shared under the same license terms (AGPL)
- **SaaS Deployment**: Requires commercial license if providing Elder as a service

For commercial licensing inquiries: sales@penguintech.io

## Quick Start

### Prerequisites

- **Kubernetes** (primary): MicroK8s, Docker Desktop K8s, or Podman Desktop K8s
- **kubectl** + **helm v3**: For K8s deployments
- **Docker**: For local image builds (alpha dev only)
- **Python 3.13+**: For local development without K8s
- **Node.js 18+**: For Web UI development (container and CI builds use Node 24)

> **Note**: Docker Compose is deprecated. All environments (alpha, beta, prod) deploy to Kubernetes.

### Local Development

```bash
make setup            # venv, Python deps, git hooks
make dev              # Start backing services (postgres + redis)
make dev-api          # Start the API
make seed-mock-data   # Seed 3-4 realistic items per feature
```

### Kubernetes Deployment (Recommended)

The Helm chart lives at [`k8s/helm/elder`](k8s/helm/elder) — `alpha.yml` for local clusters, `values-beta.yaml` for the beta cluster.

**Local alpha cluster** (builds images, pushes to `localhost:32000`, deploys via Helm):

```bash
make docker-build-alpha       # Build + push service images to the local registry
make deploy-alpha             # Helm upgrade --install into the local-alpha context
```

Or drive the script directly for finer control:

```bash
./scripts/deploy-alpha.sh --help          # options: --build/--skip-build, --tag, --service, --dry-run, --rollback
./scripts/deploy-alpha.sh --skip-build
```

**Manual Helm install:**

```bash
cd k8s/helm/elder
helm dependency update
helm install elder . -f alpha.yml \
  --set config.secretKey="$(openssl rand -base64 32)" \
  --set postgresql.auth.password="$(openssl rand -base64 32)" \
  --set redis.auth.password="$(openssl rand -base64 32)"

kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=elder --timeout=5m
kubectl port-forward svc/elder-web 3000:80
```

**Validate before deploying:**

```bash
make helm-lint          # helm lint the chart
make helm-template      # render templates with alpha values
```

**GitHub Actions CI/CD:**

Beta images are built by CI and pushed to `ghcr.io/penguintechinc/elder/{service}:beta-<epoch>` on merge to `main`. To wire up cluster deployment:

```bash
# 1. Run the setup script against your cluster
./scripts/k8s/setup-github-serviceaccount.sh

# 2. Add the output secrets to GitHub:
#    KUBE_CONFIG · K8S_NAMESPACE · SECRET_KEY · POSTGRES_PASSWORD · REDIS_PASSWORD
```

**Resources:**
- 📖 [Local Kubernetes Setup Guide](docs/deployment/local-kubernetes-setup.md)
- 🔧 [GitHub Actions Kubernetes Deployment](docs/deployment/github-actions-k8s.md)
- ⚙️ [Deployment Docs](docs/deployment/README.md)

## Configuration

Key environment variables:

```bash
# Database (penguin-dal/PyDAL supports PostgreSQL, MySQL/MariaDB, SQLite)
# PostgreSQL (recommended)
DATABASE_URL=postgresql://elder:password@localhost:5432/elder

# MariaDB Galera Cluster (high availability)
# DATABASE_URL=mysql://elder:password@galera-node1:3306/elder?wsrep_sync_wait=1

# Redis
REDIS_URL=redis://:password@localhost:6379/0

# Module enablement (precedence: module > group > base)
ELDER_MODULES_ENABLED=all
ELDER_GROUP_WORKSTREAMS=true
ELDER_GROUP_ISSUES=true
ELDER_GROUP_RELATIONSHIPS=true
ELDER_GROUP_ENTITIES=true
ELDER_GROUP_DOCUMENTS=true
ELDER_MODULE_HELPDESK=false

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

Full list: [`.env.example`](.env.example).

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Client Layer                         │
│  React UI │ REST Clients │ gRPC Clients │ MCP Agents    │
└─────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                   API Layer (modular monolith)          │
│  Quart REST (ASGI/uvicorn) │ gRPC Server │ WebSocket    │
│  JWT Auth │ RBAC │ Rate Limiting │ Module Registry      │
│                                                         │
│  W workstreams  I issues  R relationships               │
│  E entities     D documents                             │
└─────────────────────────────────────────────────────────┘
        │                  │                    │
┌───────────────┐ ┌─────────────────┐ ┌────────────────────┐
│ Worker        │ │ Scanner         │ │ Flows Invoker      │
│ Cloud disc.   │ │ Network/Banner  │ │ CI/CD pipeline     │
│ Connector sync│ │ SBOM scan       │ │ step execution     │
│ Cred refresh  │ │ HTTP screenshot │ │                    │
│ SLA breach    │ │ Endpoint parser │ │                    │
└───────────────┘ └─────────────────┘ └────────────────────┘
                            │
┌─────────────────────────────────────────────────────────┐
│                   Data Layer                            │
│  penguin-dal/PyDAL (PostgreSQL, MySQL/MariaDB, SQLite)  │
│  Redis/Valkey (Cache, Sessions, Job Bus)                │
└─────────────────────────────────────────────────────────┘
```

### Services

| Service | Path | Role |
|---------|------|------|
| **api** | `apps/api/` | Quart REST + gRPC, module registry, auth |
| **web** | `web/` | React SPA served by Express |
| **worker** | `apps/worker/` | Cloud discovery, connector sync, credential refresh, SLA breach |
| **scanner** | `apps/scanner/` | Network, banner, SBOM, HTTP screenshot, endpoint parsing |
| **flows-invoker** | `apps/flows_invoker/` | CI/CD pipeline step execution |
| **mcp** | `apps/mcp/` | MCP (Model Context Protocol) server for AI agents, stdio transport |

### Technology Stack

- **Backend**: Quart 0.20 (Python 3.13, ASGI) on uvicorn, penguin-dal/PyDAL
- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS, ReactFlow, MapLibre GL
- **Database**: PostgreSQL (recommended), MySQL/MariaDB Galera, SQLite
- **Cache / Job Bus**: Redis / Valkey (Redis Streams)
- **Migrations**: SQLAlchemy + Alembic (schema only; runtime queries go through penguin-dal)
- **APIs**: REST (OpenAPI 3.0), gRPC, MCP
- **Auth**: JWT, SAML, OIDC, OAuth2, LDAP, SCIM 2.0
- **Connectors**: AWS, GCP, Kubernetes, Okta, LDAP, vCenter, FleetDM, iBoss, Authentik, Google Workspace
- **Monitoring**: Prometheus, Grafana
- **Deployment**: Kubernetes via Helm (`k8s/helm/elder`), MicroK8s for local alpha

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
Bi-directional sync with identity providers and infrastructure platforms. Cloud scans automatically create dependency edges between the resources they discover.

| Connector | Capabilities |
|-----------|--------------|
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
| [Modules & the WIRED Model](docs/MODULES.md) | The 15 modules, their groups, and the `ELDER_*` toggles |
| [API Reference](docs/API.md) | REST & gRPC API documentation |
| [Database Schema](docs/DATABASE.md) | Database structure and penguin-dal/PyDAL usage |
| [Connectors](docs/CONNECTORS.md) | Connector configuration and sync behaviour |
| [Sync Documentation](docs/SYNC.md) | Project management sync setup |
| [Backup Configuration](docs/S3_BACKUP_CONFIGURATION.md) | S3 backup setup |
| [Usage Guide](docs/USAGE.md) | User guide and workflows |
| [Development](docs/DEVELOPMENT.md) | Local development setup |
| [Testing](docs/TESTING.md) | Test tiers and how to run them |
| [Roadmap](docs/ROADMAP.md) | Planned work |
| [Contributing](docs/CONTRIBUTING.md) | Contribution guidelines |
| [Release Notes](docs/RELEASE_NOTES.md) | Version history |

## Development

```bash
# Setup & development
make setup            # venv, Python deps, git hooks
make dev              # Start postgres and redis
make dev-api          # Start the API
make seed-mock-data   # Seed mock data

# Testing
make test             # lint + unit + integration + functional + security
make test-unit        # Unit tests
make test-integration # Integration tests
make smoke-test       # <2 min pre-commit smoke tests
make test-ui          # Playwright web UI tests
make test-coverage    # HTML coverage report

# Quality
make lint             # ruff, mypy, hadolint, shellcheck, eslint, prettier
make format           # Auto-format Python and web code
make pre-commit       # Full pre-commit sequence

# Database
make db-migrate       # Alembic upgrade head
make db-create-migration
make db-reset         # Destroys all data

# Build & deploy
make docker-build     # Build all service images
make deploy-alpha     # Deploy to local alpha cluster
make helm-lint        # Lint the Helm chart
```

Run `make help` for the full target list.

## Security

- ✅ Multi-factor authentication
- ✅ Fine-grained RBAC with org-scoped permissions
- ✅ TLS 1.3 enforcement
- ✅ Input validation with penguin-dal/PyDAL validators
- ✅ SQL injection prevention via parameterized queries
- ✅ Audit logging
- ✅ Container scanning with Trivy
- ✅ Secrets detection (gitleaks, trufflehog) and SAST (bandit, semgrep) in pre-commit hooks

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](docs/CONTRIBUTING.md) for guidelines.

## License

Elder is licensed under the Limited AGPL v3 with Fair Use Preamble. See [LICENSE.md](LICENSE.md) for details.

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

This exception rewards contributors by providing lasting fair use rights to their employers. See [LICENSE.md](LICENSE.md) for full terms.

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
