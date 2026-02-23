# Elder Worker Service - Quick Start Guide

This guide will help you get the Elder Worker Service up and running in 5 minutes.

## Prerequisites

- Docker and Docker Compose installed
- Elder API running (`docker-compose up -d api`)
- Credentials for at least one external service (AWS, GCP, Google Workspace, or LDAP)

## Quick Start

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

## Configuration Examples

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
