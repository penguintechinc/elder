# Elder Worker Service - Quick Start Guide

This guide will help you get the Elder Worker Service up and running in 5 minutes.

## Prerequisites

- Docker and Docker Compose installed
- Elder API running (`docker-compose up -d api`)
- Credentials for at least one external service (AWS, GCP, Google Workspace, LDAP, or cloud discovery)

## Quick Start

### Overview: Connectors vs Discovery

Before configuring the worker, understand the two main capabilities:

**Connectors** (Identity & Infrastructure Sync):
- Sync users, groups, and permissions from identity providers
- Examples: Google Workspace, Okta, Authentik, LDAP, AWS IAM
- Runs continuously on a schedule (default: every 1 hour)
- Updates Elder with identity and infrastructure metadata

**Cloud Discovery** (Resource Enumeration):
- Enumerate and catalog cloud resources and services
- Examples: AWS EC2, RDS, S3; GCP Compute Engine, GCS; Azure VMs, storage; Kubernetes resources
- Runs via discovery jobs created through the API
- Worker polls for pending jobs every 5 minutes
- Results stored in discovery history and entity database

Both run in the worker service but on different schedules. See [Discovery vs Connectors](#discovery-vs-connectors) below.

### 1. Configure Environment Variables

The worker service uses environment variables defined in your `.env` file (or the main Elder `.env.example`).

**Enable at least one connector:**

```bash
# Edit your .env file
nano .env

# Enable AWS connector (example)
AWS_ENABLED=true
AWS_ACCESS_KEY_ID=AKIAXXXXXXXXXXXXXXXX
AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AWS_REGIONS=us-east-1,us-west-2
```

### 2. Start the Worker Service

```bash
docker-compose up -d worker
```

### 3. Verify It's Running

```bash
# Check logs
docker-compose logs -f worker

# Check health
curl http://localhost:8000/healthz

# View status
curl http://localhost:8000/status
```

Expected output:
```json
{
  "status": "healthy",
  "service": "elder-worker",
  "running": true,
  "connectors": [...]
}
```

## Cloud Discovery Setup

Cloud discovery enumerates cloud resources (VMs, storage, networking, services) from AWS, GCP, Azure, and Kubernetes. The worker service picks up discovery jobs automatically.

### 4. Create a Discovery Job via API

Once the worker is running, create a discovery job. The worker will poll for it every 5 minutes.

**AWS Example:**

```bash
# Create an AWS discovery job
curl -X POST http://localhost:5000/api/v1/discovery/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "AWS Production Scan",
    "provider": "aws",
    "config": {
      "access_key_id": "AKIAXXXXXXXXXXXXXXXX",
      "secret_access_key": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "regions": ["us-east-1", "us-west-2"]
    },
    "organization_id": 1
  }'

# Response: Returns job ID
# {
#   "id": 42,
#   "name": "AWS Production Scan",
#   "provider": "aws",
#   "status": "pending",
#   "created_at": "2024-01-15T10:30:00Z"
# }
```

**GCP Example:**

```bash
curl -X POST http://localhost:5000/api/v1/discovery/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GCP Projects Scan",
    "provider": "gcp",
    "config": {
      "project_ids": ["my-gcp-project-123", "another-project-456"],
      "credentials_json": {
        "type": "service_account",
        "project_id": "my-gcp-project-123",
        "private_key_id": "...",
        "private_key": "...",
        "client_email": "service-account@project.iam.gserviceaccount.com"
      }
    },
    "organization_id": 1
  }'
```

**Azure Example:**

```bash
curl -X POST http://localhost:5000/api/v1/discovery/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Azure Subscriptions Scan",
    "provider": "azure",
    "config": {
      "subscription_ids": ["12345678-1234-1234-1234-123456789012"],
      "tenant_id": "87654321-4321-4321-4321-210987654321",
      "client_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      "client_secret": "client_secret_value_here"
    },
    "organization_id": 1
  }'
```

**Kubernetes Example:**

```bash
curl -X POST http://localhost:5000/api/v1/discovery/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "K8s Cluster Scan",
    "provider": "kubernetes",
    "config": {
      "kubeconfig_path": "/var/run/secrets/kubernetes.io/serviceaccount",
      "cluster_name": "production-eks"
    },
    "organization_id": 1
  }'
```

### 5. Monitor Discovery Job Progress

```bash
# Get job details
curl http://localhost:5000/api/v1/discovery/jobs/42 | jq

# Get discovery history (results)
curl http://localhost:5000/api/v1/discovery/jobs/42/history | jq

# View latest discovery results
curl http://localhost:5000/api/v1/discovery/jobs/42/history | jq '.results[-1]'

# Check for discovered resources
curl http://localhost:5000/api/v1/entities?organization_id=1 | jq
```

### 6. Trigger Immediate Discovery

Queue a job for immediate execution:

```bash
# Queue job for next worker poll (within 5 minutes)
curl -X POST http://localhost:5000/api/v1/discovery/jobs/42/queue | jq

# Response:
# {
#   "job_id": 42,
#   "success": true,
#   "message": "Job queued for worker execution",
#   "queued_at": "2024-01-15T10:35:00Z"
# }

# Monitor logs to see execution
docker-compose logs -f worker | grep "job_id: 42"
```

## Configuration Examples

### Discovery vs Connectors Reference

| Aspect | Connectors | Discovery |
|--------|-----------|-----------|
| **Purpose** | Sync identity/access data | Enumerate cloud resources |
| **Data Sources** | Google Workspace, Okta, LDAP, AWS IAM | AWS, GCP, Azure, Kubernetes |
| **Artifacts** | Users, groups, permissions, roles | VMs, storage, networking, databases, services |
| **Schedule** | Continuous (configurable intervals) | On-demand jobs (API-driven) |
| **Polling** | Not applicable | Worker polls every 5 minutes for pending jobs |
| **Database Required** | Optional (can sync without DB) | Required (worker needs DB to store results) |
| **Configuration** | Environment variables (connectors enabled at startup) | API calls (jobs created dynamically) |
| **Update Frequency** | Periodic (e.g., hourly sync) | Per-job (trigger via API) |
| **Example Config** | `GOOGLE_WORKSPACE_ENABLED=true` | `POST /api/v1/discovery/jobs` with AWS/GCP/Azure config |

**When to use what:**
- **Use Connectors** to keep user directories, groups, and identity information synchronized
- **Use Discovery** to inventory and catalog cloud infrastructure for asset management, compliance, or visualization

You can enable both simultaneously—they operate independently.

### AWS Example

```bash
AWS_ENABLED=true
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_DEFAULT_REGION=us-east-1
AWS_REGIONS=us-east-1,us-west-2,eu-west-1
AWS_SYNC_INTERVAL=3600  # Sync every hour
```

### GCP Example

```bash
GCP_ENABLED=true
GCP_PROJECT_ID=my-gcp-project-123456
GCP_CREDENTIALS_PATH=/app/credentials/gcp-credentials.json
GCP_SYNC_INTERVAL=3600
```

**Mount credentials:**
```bash
# Create volume and copy credentials
docker volume create worker_credentials
docker run --rm \
  -v worker_credentials:/credentials \
  -v $(pwd)/gcp-credentials.json:/src/gcp.json \
  alpine cp /src/gcp.json /credentials/gcp-credentials.json
```

### Google Workspace Example

```bash
GOOGLE_WORKSPACE_ENABLED=true
GOOGLE_WORKSPACE_CREDENTIALS_PATH=/app/credentials/workspace-credentials.json
GOOGLE_WORKSPACE_ADMIN_EMAIL=admin@example.com
GOOGLE_WORKSPACE_CUSTOMER_ID=my_customer
GOOGLE_WORKSPACE_SYNC_INTERVAL=3600
```

**Mount credentials:**
```bash
docker run --rm \
  -v worker_credentials:/credentials \
  -v $(pwd)/workspace-credentials.json:/src/workspace.json \
  alpine cp /src/workspace.json /credentials/workspace-credentials.json
```

### LDAP Example (Active Directory)

```bash
LDAP_ENABLED=true
LDAP_SERVER=ad.example.com
LDAP_PORT=389
LDAP_USE_SSL=false
LDAP_VERIFY_CERT=true
LDAP_BIND_DN=cn=admin,cn=Users,dc=example,dc=com
LDAP_BIND_PASSWORD=your_password_here
LDAP_BASE_DN=dc=example,dc=com
LDAP_USER_FILTER=(&(objectClass=user)(objectCategory=person))
LDAP_GROUP_FILTER=(objectClass=group)
LDAP_SYNC_INTERVAL=3600
```

### LDAPS Example (Secure LDAP)

```bash
LDAP_ENABLED=true
LDAP_SERVER=ldaps.example.com
LDAP_PORT=636
LDAP_USE_SSL=true
LDAP_VERIFY_CERT=true
LDAP_BIND_DN=cn=admin,dc=example,dc=com
LDAP_BIND_PASSWORD=your_password_here
LDAP_BASE_DN=dc=example,dc=com
LDAP_USER_FILTER=(objectClass=inetOrgPerson)
LDAP_GROUP_FILTER=(objectClass=groupOfNames)
LDAP_SYNC_INTERVAL=3600
```

## Worker Configuration for Discovery

The worker service requires database connectivity to execute discovery jobs. Additional cloud credentials can be passed via API at job creation time, but the following environment variables must be set:

### Required for Discovery Execution

```bash
# ============================================================================
# Database Configuration (Required for Discovery Execution)
# ============================================================================
# Primary database URL - worker writes discovery results here
DATABASE_URL=postgresql://user:password@postgres:5432/elder

# Optional: Read replica for pending job queries (improves performance)
DATABASE_READ_URL=postgresql://user:password@read-replica:5432/elder

# ============================================================================
# Cloud Credential Paths (Optional - for file-based credentials)
# ============================================================================
# Path to mounted cloud credentials (K8s Secrets)
# Credentials mounted in /var/run/secrets/elder/ are accessible here

# AWS credentials file path (if using file-based credentials)
AWS_CREDENTIALS_FILE=/var/run/secrets/elder/aws-credentials.json

# GCP service account JSON path
GCP_CREDENTIALS_FILE=/var/run/secrets/elder/gcp-credentials.json

# Azure service principal JSON path
AZURE_CREDENTIALS_FILE=/var/run/secrets/elder/azure-credentials.json

# Kubernetes kubeconfig path
KUBECONFIG=/var/run/secrets/elder/kubeconfig.yaml

# ============================================================================
# Cloud Provider Enablement
# ============================================================================
# Enable/disable specific cloud providers (true/false)
AWS_ENABLED=true
GCP_ENABLED=true
AZURE_ENABLED=true
KUBERNETES_ENABLED=true

# ============================================================================
# Discovery Polling Configuration
# ============================================================================
# How often worker polls for pending discovery jobs (seconds)
DISCOVERY_POLL_INTERVAL=300  # Default: 5 minutes

# Maximum discovery jobs to run concurrently
DISCOVERY_MAX_CONCURRENT=2

# Discovery job timeout (seconds)
DISCOVERY_JOB_TIMEOUT=3600  # Default: 1 hour
```

### Kubernetes Secrets Mount Example

For secure credential management, mount secrets as files:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: elder-discovery-creds
  namespace: elder
type: Opaque
data:
  aws-credentials.json: BASE64_ENCODED_AWS_JSON
  gcp-credentials.json: BASE64_ENCODED_GCP_JSON
  azure-credentials.json: BASE64_ENCODED_AZURE_JSON
  kubeconfig.yaml: BASE64_ENCODED_KUBECONFIG

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: elder-worker
  namespace: elder
spec:
  template:
    spec:
      containers:
      - name: worker
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: elder-db
              key: database-url
        - name: AWS_ENABLED
          value: "true"
        - name: GCP_ENABLED
          value: "true"
        volumeMounts:
        - name: discovery-creds
          mountPath: /var/run/secrets/elder
          readOnly: true
      volumes:
      - name: discovery-creds
        secret:
          secretName: elder-discovery-creds
```

## Testing Connectivity

Before running the full sync, test connectivity to your services:

```bash
docker-compose exec worker python3 /app/apps/worker/test_connectivity.py
```

Expected output:
```
============================================================
Elder Worker Service - Connectivity Test
============================================================
✓ Elder API connection successful
✓ AWS connection successful
✓ GCP connection successful
✗ Google Workspace: disabled
✗ LDAP: disabled
============================================================
All enabled tests passed! Worker is ready to run.
============================================================
```

## Monitoring

### View Logs

```bash
# Follow all logs
docker-compose logs -f worker

# View last 100 lines
docker-compose logs --tail=100 worker

# Search for errors
docker-compose logs worker | grep -i error

# Search for sync results
docker-compose logs worker | grep -E "sync.*completed"
```

### Check Metrics

```bash
# Prometheus metrics
curl http://localhost:8000/metrics

# Key metrics to watch
curl http://localhost:8000/metrics | grep connector_sync_total
curl http://localhost:8000/metrics | grep connector_entities_synced
curl http://localhost:8000/metrics | grep connector_sync_errors
```

### View Detailed Status

```bash
curl http://localhost:8000/status | jq
```

## Verify Data in Elder

### Check Organizations

```bash
# List all organizations
curl http://localhost:5000/api/v1/organizations | jq

# You should see organizations like:
# - "AWS"
# - "AWS us-east-1"
# - "AWS us-west-2"
# - "GCP"
# - "GCP Project: my-project"
# - "Google Workspace"
# - "LDAP: ldap.example.com"
```

### Check Entities

```bash
# List all entities
curl http://localhost:5000/api/v1/entities | jq

# Filter by type
curl "http://localhost:5000/api/v1/entities?entity_type=compute" | jq
curl "http://localhost:5000/api/v1/entities?entity_type=vpc" | jq
curl "http://localhost:5000/api/v1/entities?entity_type=user" | jq
```

## Common Issues & Solutions

### Issue: Worker Won't Start

**Check:**
```bash
docker-compose logs worker | grep -i error
```

**Common causes:**
- Elder API not running: `docker-compose up -d api`
- Invalid credentials: Double-check environment variables
- Missing credentials file: Verify volume mount

### Issue: Authentication Failures

**AWS:**
```bash
# Verify credentials
aws sts get-caller-identity --profile default

# Check environment variables
docker-compose exec worker env | grep AWS_
```

**GCP:**
```bash
# Verify credentials file exists
docker-compose exec worker ls -la /app/credentials/

# Check file contents (should be valid JSON)
docker-compose exec worker cat /app/credentials/gcp-credentials.json | jq
```

**LDAP:**
```bash
# Test LDAP connection from host
ldapsearch -H ldap://your-server -D "bind_dn" -W -b "base_dn"

# For LDAPS
ldapsearch -H ldaps://your-server -D "bind_dn" -W -b "base_dn"
```

### Issue: No Data Syncing

**Check sync interval:**
```bash
# View current settings
curl http://localhost:8000/status | jq '.settings'

# Force immediate sync by restarting
docker-compose restart worker
```

**Check Elder API connectivity:**
```bash
# From worker container
docker-compose exec worker curl http://api:5000/healthz
```

### Issue: SSL Certificate Errors (LDAPS)

**Disable certificate verification (testing only):**
```bash
LDAP_VERIFY_CERT=false
```

**For production, add CA certificate:**
```bash
# Mount CA certificate
volumes:
  - ./ca-cert.pem:/etc/ssl/certs/ca-cert.pem:ro
```

## Discovery Troubleshooting

### Issue: Discovery Jobs Stuck in "Pending" Status

**Symptom:** Created discovery job remains in "pending" status for more than 5 minutes.

**Diagnosis:**
```bash
# Check worker health
curl http://localhost:8000/healthz

# Check worker logs for discovery polling
docker-compose logs worker | grep -i discovery

# Verify DATABASE_URL is set
docker-compose exec worker env | grep DATABASE_URL
```

**Solutions:**
1. **Worker not running:** Start worker with `docker-compose up -d worker`
2. **No DATABASE_URL:** Add `DATABASE_URL` environment variable to worker
3. **Database connection failure:** Verify database is accessible from worker container
   ```bash
   docker-compose exec worker python3 -c "import psycopg2; psycopg2.connect('${DATABASE_URL}')"
   ```
4. **Worker restart:** Force job execution with `docker-compose restart worker`

### Issue: Discovery Results Not Appearing in Elder

**Symptom:** Discovery job completes but entities not visible in Elder UI or API.

**Diagnosis:**
```bash
# Check job execution status
curl http://localhost:5000/api/v1/discovery/jobs/42/history | jq '.results[-1].status'

# Verify organization_id in job config
curl http://localhost:5000/api/v1/discovery/jobs/42 | jq '.config._organization_id'

# Check entities filtered by organization
curl http://localhost:5000/api/v1/entities?organization_id=1 | jq '.entities | length'

# Check discovery_history table
docker-compose exec api python3 << 'EOF'
from apps.api.db.connection import get_db
db = get_db()
rows = db.executesql("SELECT id, job_id, status, entities_discovered FROM discovery_history ORDER BY id DESC LIMIT 5")
for row in rows:
    print(f"History ID: {row[0]}, Job: {row[1]}, Status: {row[2]}, Count: {row[3]}")
EOF
```

**Solutions:**
1. **Missing organization_id:** Recreate job with `"organization_id": 1` in request
2. **Wrong database:** Verify job results are in same database as Elder API
3. **Status = "error":** Check job error message in history
   ```bash
   curl http://localhost:5000/api/v1/discovery/jobs/42/history | jq '.results[-1].error'
   ```

### Issue: "Unsupported Provider" or "Invalid Credentials"

**Symptom:** Job creation fails with provider or credential error.

**Test job connectivity before creation:**
```bash
# Create test job with same credentials
curl -X POST http://localhost:5000/api/v1/discovery/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Job",
    "provider": "aws",
    "config": { "access_key_id": "YOUR_KEY", "secret_access_key": "YOUR_SECRET" }
  }'

# Get job ID from response, then test it
curl -X POST http://localhost:5000/api/v1/discovery/jobs/42/test | jq

# Response will show success/failure with identity details
```

**Common credential errors:**
- **AWS AccessDenied:** User lacks discovery permissions; see [AWS Discovery Setup](../development/aws-discovery-setup.md)
- **GCP authentication failed:** Service account JSON invalid or key expired
- **Azure authentication failed:** Client secret expired or wrong subscription ID
- **K8s connection timeout:** Kubeconfig path invalid or cluster unreachable

### Issue: Discovery Takes Too Long

**Symptom:** Discovery job running for more than 1 hour or timing out.

**Tune discovery configuration:**
```bash
# Increase job timeout (default 3600 seconds / 1 hour)
DISCOVERY_JOB_TIMEOUT=7200  # 2 hours

# Reduce concurrent jobs to avoid resource contention
DISCOVERY_MAX_CONCURRENT=1

# Filter cloud resources in job config (reduce scope)
# Example: AWS regions
{
  "config": {
    "regions": ["us-east-1"]  # Scan only this region instead of all
  }
}

# Check resource counts discovered
curl http://localhost:5000/api/v1/discovery/jobs/42/history | jq '.results[-1].entities_discovered'
```

### Issue: Worker Memory or CPU Spikes During Discovery

**Symptom:** Worker container using excessive resources during discovery job execution.

**Optimize discovery:**
```bash
# Reduce batch sizes in discovery jobs
# In job config, add:
{
  "config": {
    "batch_size": 100,  # Default, reduce for large environments
    "parallel_accounts": 2,  # For AWS org discovery, scan N accounts in parallel
    "page_size": 20  # API pagination size
  }
}

# Monitor resource usage
docker stats elder-worker

# Add resource limits to Docker Compose
services:
  worker:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 1G
```

### Fallback: Emergency Job Execution via API

If worker is offline or unreachable, run discovery directly via API (deprecated but available):

```bash
# Warning: This is deprecated and uses API resources
# Use only for emergency/testing
curl -X POST "http://localhost:5000/api/v1/discovery/jobs/42/run?legacy=true" \
  -H "Content-Type: application/json" | jq
```

This is slower and may timeout with large resource sets. Always prefer worker service.

## Advanced Configuration

### Custom Sync Intervals

```bash
# Sync every 30 minutes (1800 seconds)
AWS_SYNC_INTERVAL=1800

# Sync every 6 hours (21600 seconds)
GCP_SYNC_INTERVAL=21600

# Different intervals per connector
AWS_SYNC_INTERVAL=3600
GCP_SYNC_INTERVAL=7200
LDAP_SYNC_INTERVAL=1800
```

### Organization Mapping

```bash
# Use a specific Elder organization as default
DEFAULT_ORGANIZATION_ID=1

# Disable auto-creation (requires DEFAULT_ORGANIZATION_ID)
CREATE_MISSING_ORGANIZATIONS=false
```

### Sync Behavior

```bash
# Disable initial sync on startup
SYNC_ON_STARTUP=false

# Increase batch size for better performance
SYNC_BATCH_SIZE=500

# Increase retries for unreliable networks
SYNC_MAX_RETRIES=5
```

### Logging

```bash
# Enable debug logging
LOG_LEVEL=DEBUG

# Use text format instead of JSON
LOG_FORMAT=text
```

## Next Steps

- **Set up monitoring**: Add Prometheus scraping and Grafana dashboards
- **Configure alerts**: Set up alerts for sync failures or errors
- **Review data**: Check Elder UI for synced organizations and entities
- **Fine-tune sync intervals**: Adjust based on your data change frequency
- **Add more connectors**: Enable additional data sources as needed

## Resources

- Full Documentation: `apps/worker/README.md`
- Configuration Reference: `apps/worker/.env.example`
- Test Script: `apps/worker/test_connectivity.py`
- Implementation Details: `WORKER_SUMMARY.md`

## Support

For issues or questions:
1. Check logs: `docker-compose logs worker`
2. Verify configuration: `curl http://localhost:8000/status`
3. Test connectivity: Run `test_connectivity.py`
4. Review documentation in `apps/worker/README.md`
