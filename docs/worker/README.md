# Elder Worker Service

The Elder Worker Service is a stateless background job orchestrator that owns two critical responsibilities:

1. **Cloud Discovery** — Discovers and inventories cloud infrastructure resources (AWS, GCP, Azure, Kubernetes)
2. **Identity & Infrastructure Connectors** — Syncs users, groups, and directory data from multiple sources

The worker runs scheduled jobs that poll the database for pending tasks and executes them asynchronously, enabling Elder to maintain an up-to-date inventory of cloud resources and identity data across your infrastructure.

## Overview

The worker is **stateless and horizontally scalable**. It runs background jobs that:
- Poll the `discovery_jobs` table every 5 minutes for pending cloud discovery tasks
- Execute cloud discovery via AWS SDK, GCP SDK, Azure SDK, and Kubernetes client
- Sync identity/infrastructure data (users, groups, permissions) from 11+ connectors
- Store all results directly in the database
- Expose metrics via Prometheus for monitoring

**Note**: The Worker Service was formerly called the "Connector Service" for backward compatibility.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     Elder Worker Service                          │
│                   (Stateless, Horizontally Scalable)              │
├──────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ┌───────────────────────────┐  ┌──────────────────────────────┐ │
│  │  Cloud Discovery Executor  │  │  Connector Sync Orchestrator │ │
│  ├───────────────────────────┤  ├──────────────────────────────┤ │
│  │ - Poll discovery_jobs      │  │ - AWS (EC2, VPC, RDS...)    │ │
│  │ - Execute via SDK clients  │  │ - GCP (Compute, VPC, GCS)   │ │
│  │ - Store results to DB      │  │ - Azure (VMs, Vnets, etc)   │ │
│  │ - AWS/GCP/Azure/K8s        │  │ - Kubernetes                │ │
│  └───────────────────────────┘  │ - Google Workspace          │ │
│                                  │ - LDAP / LDAPS              │ │
│                                  │ - Okta (Enterprise)         │ │
│                                  │ - Authentik (Enterprise)    │ │
│                                  │ - Fleet DM, iBoss, vCenter  │ │
│                                  │ - LXD (Container discovery) │ │
│                                  └──────────────────────────────┘ │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │  Job Scheduler + Health/Metrics Server (Flask)               │ │
│  │  - Polls discovery_jobs every 5 minutes                       │ │
│  │  - Executes enabled connectors on configured intervals        │ │
│  │  - /healthz, /metrics, /status endpoints                      │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
         │                              │
         │ Read/Write                   │ Write
         ▼                              ▼
┌──────────────────────────┐   ┌────────────────────────────┐
│  External Cloud Providers │   │   Elder Database           │
│  - AWS                    │   │   - discovery_jobs         │
│  - GCP                    │   │   - entities               │
│  - Azure                  │   │   - identities             │
│  - Kubernetes             │   │   - identity_groups        │
│  - Okta, Authentik, etc   │   │   - organizations          │
└──────────────────────────┘   └────────────────────────────┘
```

## Cloud Discovery

Elder Worker Service executes cloud discovery jobs to automatically discover and inventory infrastructure resources across AWS, GCP, Azure, and Kubernetes clusters.

### How Discovery Works

1. **API Scheduling**: The Elder API creates discovery jobs in the `discovery_jobs` table via admin panel or API
2. **Worker Polling**: The worker polls `discovery_jobs` every 5 minutes for pending tasks
3. **Job Execution**: For each due job, the worker:
   - Loads credentials from Kubernetes Secrets (or environment)
   - Connects to the cloud provider via official SDKs
   - Discovers resources (EC2 instances, GCP VMs, AKS clusters, etc.)
   - Stores results directly in the `entities` and `networking_resources` tables
4. **Status Tracking**: Job completion status, errors, and last run timestamp are recorded in `discovery_jobs`

### Supported Cloud Providers

| Provider | Discovery Scope | Credentials |
|----------|-----------------|-------------|
| **AWS** | EC2, RDS, ElastiCache, VPCs, Subnets, S3, Lambda | Access Key ID + Secret Key |
| **GCP** | Compute instances, VPC networks, Cloud Storage, GKE | Service Account JSON |
| **Azure** | Virtual Machines, Virtual Networks, Storage accounts, AKS | Service Principal credentials |
| **Kubernetes** | Pods, Services, Deployments, Namespaces, PersistentVolumes | kubeconfig or in-cluster auth |

### Discovery Job Configuration

Discovery jobs are configured via the Elder API or admin dashboard. Key fields:

```json
{
  "provider": "aws",              // aws, gcp, azure, kubernetes
  "organization_id": 1,           // Elder organization to own discovered resources
  "credentials": { ... },         // Provider-specific credentials (encrypted)
  "schedule_interval": 3600,      // Seconds between discovery runs (default: 1 hour)
  "enabled": true,                // Enable/disable this discovery job
  "next_run_at": "2025-02-23...", // When to run next (auto-calculated)
  "last_run_at": "2025-02-23...", // Timestamp of last execution
  "last_error": null,             // Error message if last run failed
  "status": "pending"             // pending, running, completed, failed
}
```

### Credentials Management

Cloud credentials are stored securely in Kubernetes Secrets and referenced by discovery jobs:

```bash
# Create a secret for AWS discovery
kubectl create secret generic aws-discovery-creds \
  --from-literal=AWS_ACCESS_KEY_ID=AKIA... \
  --from-literal=AWS_SECRET_ACCESS_KEY=... \
  -n elder

# The discovery job references: "secret://aws-discovery-creds"
```

### Database Schema

Discovered resources are stored in standard Elder tables:

| Table | Purpose |
|-------|---------|
| `entities` | EC2 instances, VM instances, compute resources |
| `networking_resources` | VPCs, Subnets, Networks, Namespaces, Load Balancers |
| `data_stores` | S3, EBS, GCS, RDS, Persistent Volumes |
| `services` | Lambda, Cloud Functions, K8s Services |
| `network_entity_mappings` | Links networking resources to entities |

## Identity & Infrastructure Connectors

The worker manages 11+ connectors that sync identity and infrastructure data into Elder, enabling RBAC, compliance auditing, and access control.

### Supported Connectors

| Connector | Type | Resources Synced | Write-Back |
|-----------|------|------------------|-----------|
| **AWS** | Cloud | EC2, RDS, Lambda, S3 resources | No |
| **GCP** | Cloud | Compute, GCS, Cloud Run resources | No |
| **Google Workspace** | Identity | Users, Groups, Org Units | No |
| **LDAP / LDAPS** | Directory | Users, Groups, Org Units | No |
| **Okta** | Identity | Users, Groups | Yes (group membership) |
| **Authentik** | Identity | Users, Groups (nested) | Yes (group membership) |
| **Kubernetes** | Infrastructure | Service Accounts, RBAC bindings | No |
| **Fleet DM** | Endpoint | Enrolled devices, OS info | No |
| **iBoss** | Network | Network policies, categories | No |
| **vCenter** | Virtualization | VMs, ESXi hosts, datastores | No |
| **LXD** | Container | Container instances, images | No |

### Connector Sync Flow

Each connector:
1. Connects to external service using stored credentials
2. Fetches users, groups, and resource data
3. Creates/updates entities and identity_groups in Elder
4. Syncs group memberships (enabling RBAC policy evaluation)
5. Records sync status and error metrics

### Write-Back Connectors

Some connectors support bidirectional sync:

- **Okta**: Changes to Elder identity_group membership are synced back to Okta groups
- **Authentik**: Changes to Elder identity_group membership are synced back to Authentik groups

This allows Elder to be the source-of-truth for group membership while keeping external systems in sync.

## Deployment Architecture

### Database Access

The worker operates with **direct database access** (not via Elder API) to efficiently:
- Poll `discovery_jobs` and sync task tables
- Store results at scale
- Track job status and metrics

**Database Configuration**:
```bash
DB_URL=postgresql://elder_worker:password@db:5432/elder  # Write connection
DB_READ_URL=postgresql://elder_worker_ro:password@db-replica:5432/elder  # Read replica (optional)
```

**Least-Privilege Access**: The worker uses a dedicated `elder_worker` database account with:
- `SELECT` on all discovery/sync tables
- `INSERT, UPDATE` on `entities`, `identities`, `discovery_jobs`
- `SELECT` on configuration tables
- No direct access to user accounts, secrets, or billing tables

### Credentials Management

Worker credentials are stored securely in **Kubernetes Secrets** (production) or environment variables (development):

```bash
# Kubernetes Secret example
kubectl create secret generic elder-worker \
  --from-literal=DB_URL=postgresql://... \
  --from-literal=AWS_ACCESS_KEY_ID=... \
  --from-literal=OKTA_API_TOKEN=... \
  -n elder
```

**Security Best Practices**:
- No PVC (persistent volumes) — credentials are mounted as Secrets, not persistent files
- Credentials rotated regularly via Secret Manager
- API keys stored with expiration dates
- No credentials logged or exposed in metrics

### Metrics & Monitoring

The worker exposes Prometheus metrics for operational visibility:

```
worker_discovery_jobs_executed_total{provider="aws",status="success"}
worker_discovery_poll_duration_seconds
worker_sync_total{connector="okta",status="success"}
worker_sync_errors_total{connector="ldap"}
worker_entities_synced{connector="gcp",operation="created"}
```

Integrate with Prometheus and Grafana for:
- Discovery job success rates
- Sync performance and latency
- Error tracking and alerting
- Entity creation/update trends

## Quick Links

- **[QUICKSTART.md](QUICKSTART.md)** — Get the worker running in 5 minutes (environment setup, testing connectivity, troubleshooting)
- **[IMPLEMENTATION.md](IMPLEMENTATION.md)** — Deep dive into architecture, connector implementation details, and deployment patterns

## Configuration Quick Reference

Worker configuration is done via environment variables. The worker automatically discovers enabled connectors and schedules them for execution.

| Setting | Required | Default | Description |
|---------|----------|---------|-------------|
| `DB_URL` | Yes | — | PostgreSQL connection string (write access) |
| `DB_READ_URL` | No | `DB_URL` | PostgreSQL read replica (optional) |
| `ELDER_API_URL` | No | `http://api:5000` | Elder API base URL |
| `HEALTH_CHECK_PORT` | No | `8000` | Health check server port |
| `LOG_LEVEL` | No | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `METRICS_ENABLED` | No | `true` | Enable Prometheus metrics |

### Enable Connectors

Each connector can be enabled independently:

| Connector | Enable Setting | Interval Setting | Docs |
|-----------|----------------|------------------|------|
| **AWS** | `AWS_ENABLED` | `AWS_SYNC_INTERVAL` | QUICKSTART.md |
| **GCP** | `GCP_ENABLED` | `GCP_SYNC_INTERVAL` | QUICKSTART.md |
| **Azure** | `AZURE_ENABLED` | `AZURE_SYNC_INTERVAL` | QUICKSTART.md |
| **Kubernetes** | `K8S_ENABLED` | `K8S_SYNC_INTERVAL` | QUICKSTART.md |
| **Google Workspace** | `GOOGLE_WORKSPACE_ENABLED` | `GOOGLE_WORKSPACE_SYNC_INTERVAL` | QUICKSTART.md |
| **LDAP/LDAPS** | `LDAP_ENABLED` | `LDAP_SYNC_INTERVAL` | QUICKSTART.md |
| **Okta** | `OKTA_ENABLED` | `OKTA_SYNC_INTERVAL` | QUICKSTART.md |
| **Authentik** | `AUTHENTIK_ENABLED` | `AUTHENTIK_SYNC_INTERVAL` | QUICKSTART.md |
| **Fleet DM** | `FLEETDM_ENABLED` | `FLEETDM_SYNC_INTERVAL` | QUICKSTART.md |
| **iBoss** | `IBOSS_ENABLED` | `IBOSS_SYNC_INTERVAL` | QUICKSTART.md |
| **vCenter** | `VCENTER_ENABLED` | `VCENTER_SYNC_INTERVAL` | QUICKSTART.md |
| **LXD** | `LXD_ENABLED` | `LXD_SYNC_INTERVAL` | QUICKSTART.md |

See **QUICKSTART.md** for detailed configuration examples for each connector, including credential setup and common configurations (e.g., Active Directory, OpenLDAP, LDAPS).

## Getting Started

Start the worker service with configured connectors:

```bash
# 1. Configure .env file with enabled connectors
export AWS_ENABLED=true
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret

# 2. Start the worker
docker-compose up -d worker

# 3. Verify it's running
curl http://localhost:8000/healthz
docker-compose logs -f worker
```

For detailed setup instructions, see **[QUICKSTART.md](QUICKSTART.md)**.

## Data & Metrics

The worker stores discovered resources directly in the database:

- **`entities`** — Compute resources (EC2, VMs), network resources, data stores
- **`identities`** — Users and service accounts from directory services
- **`identity_groups`** — Groups and group memberships
- **`discovery_jobs`** — Status and history of cloud discovery tasks
- **`organizations`** — Auto-created organizational hierarchy

Prometheus metrics are exposed at `/metrics`:

```
worker_discovery_jobs_executed_total{provider="aws",status="success"}
worker_sync_total{connector="okta",status="success"}
worker_sync_duration_seconds{connector="gcp"}
worker_entities_synced{connector="ldap",operation="created"}
```

## Security

- **No PVCs** — Credentials stored in Kubernetes Secrets, not persistent volumes
- **Least Privilege** — Dedicated `elder_worker` DB account with minimal permissions
- **Encrypted** — All sensitive credentials encrypted at rest
- **Rotation** — API keys and passwords rotated regularly
- **No Logging** — Credentials excluded from logs and metrics

## Project Structure

```
apps/worker/
├── config/settings.py          # Configuration and validation
├── connectors/                 # 12+ connector implementations
│   ├── base.py                # Base connector interface
│   ├── aws_connector.py       # AWS, GCP, Azure, K8s
│   ├── okta_connector.py      # Okta, Authentik, LDAP
│   └── ...
├── discovery/                  # Cloud discovery service
│   ├── base.py                # Discovery provider interface
│   ├── executor.py            # Job scheduler and executor
│   └── aws_discovery.py       # AWS, GCP, Azure, K8s discovery
├── utils/elder_client.py      # Database and API client
├── main.py                    # Service orchestrator
└── Dockerfile
```

See **[IMPLEMENTATION.md](IMPLEMENTATION.md)** for architectural deep-dive and developer guide.

## License

Elder Worker Service is part of the Elder infrastructure management platform.

Copyright © 2025 Penguin Tech Inc. All rights reserved.
