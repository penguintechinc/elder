# Elder Connectors Documentation

Elder Connectors are discovery integrations that automatically sync resources from external platforms into Elder. All connectors are read-only and run as part of the Worker Service.

## Available Connectors

| Connector | Platform | Resources Synced |
|-----------|----------|------------------|
| [AWS](#aws-connector) | Amazon Web Services | EC2, RDS, ElastiCache, SQS, S3, Lambda, EKS |
| [GCP](#gcp-connector) | Google Cloud Platform | Compute Engine, Cloud SQL, GKE, Cloud Functions |
| [Google Workspace](#google-workspace-connector) | Google Workspace | Users, Groups, OUs |
| [Kubernetes](#kubernetes-connector) | Kubernetes | Namespaces, Pods, Services, Secrets, PVCs, RBAC |
| [LDAP](#ldap-connector) | LDAP/Active Directory | Users, Groups, OUs |
| [iBoss](#iboss-connector) | iBoss Cloud Security | Users, Groups, Policies, Connectors, Applications |
| [vCenter](#vcenter-connector) | VMware vCenter | VMs, Hosts, Datastores, Clusters, Networks |
| [FleetDM](#fleetdm-connector) | FleetDM | Hosts, Vulnerabilities, Policies, Software |

## Common Configuration

All connectors share these common settings:

```bash
# Elder API Connection
ELDER_API_URL=http://api:4000
ELDER_API_KEY=<api-key>

# Organization Mapping
DEFAULT_ORGANIZATION_ID=1
CREATE_MISSING_ORGANIZATIONS=true

# Sync Settings
SYNC_ON_STARTUP=true
SYNC_BATCH_SIZE=100
SYNC_MAX_RETRIES=3
```

---

## AWS Connector

Syncs AWS resources across multiple regions.

### Configuration

```bash
AWS_ENABLED=true
AWS_ACCESS_KEY_ID=<access-key>
AWS_SECRET_ACCESS_KEY=<secret-key>
AWS_DEFAULT_REGION=us-east-1
AWS_REGIONS=us-east-1,us-west-2,eu-west-1
AWS_SYNC_INTERVAL=3600
```

### Resources Synced

- **Compute**: EC2 instances, Lambda functions
- **Storage**: S3 buckets, EBS volumes
- **Database**: RDS instances, ElastiCache clusters
- **Container**: EKS clusters, Fargate tasks
- **Messaging**: SQS queues, SNS topics
- **Network**: VPCs, Subnets, Security Groups

### Required IAM Permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:Describe*",
        "rds:Describe*",
        "elasticache:Describe*",
        "sqs:List*",
        "sqs:Get*",
        "s3:List*",
        "lambda:List*",
        "eks:Describe*",
        "eks:List*"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## GCP Connector

Syncs Google Cloud Platform resources.

### Configuration

```bash
GCP_ENABLED=true
GCP_PROJECT_ID=my-project-id
GCP_CREDENTIALS_PATH=/app/credentials/gcp-service-account.json
GCP_SYNC_INTERVAL=3600
```

### Resources Synced

- **Compute**: Compute Engine instances
- **Database**: Cloud SQL instances
- **Container**: GKE clusters
- **Functions**: Cloud Functions
- **Storage**: Cloud Storage buckets

### Required Service Account Permissions

- `compute.instances.list`
- `compute.zones.list`
- `cloudsql.instances.list`
- `container.clusters.list`
- `cloudfunctions.functions.list`
- `storage.buckets.list`

---

## Google Workspace Connector

Syncs users and groups from Google Workspace.

### Configuration

```bash
GOOGLE_WORKSPACE_ENABLED=true
GOOGLE_WORKSPACE_CREDENTIALS_PATH=/app/credentials/workspace-sa.json
GOOGLE_WORKSPACE_ADMIN_EMAIL=admin@example.com
GOOGLE_WORKSPACE_CUSTOMER_ID=my_customer
GOOGLE_WORKSPACE_SYNC_INTERVAL=3600
```

### Resources Synced

- **Identity**: Users, Groups
- **Organization**: Organizational Units

### Required Permissions

Enable domain-wide delegation for the service account with these scopes:
- `https://www.googleapis.com/auth/admin.directory.user.readonly`
- `https://www.googleapis.com/auth/admin.directory.group.readonly`
- `https://www.googleapis.com/auth/admin.directory.orgunit.readonly`

---

## Kubernetes Connector

Syncs resources from Kubernetes clusters.

### Configuration

```bash
K8S_ENABLED=true
K8S_KUBECONFIG_PATH=/app/credentials/kubeconfig
# OR use in-cluster config
K8S_IN_CLUSTER=true
K8S_SYNC_INTERVAL=3600
```

### Resources Synced

- **Workloads**: Pods, Deployments, StatefulSets, DaemonSets
- **Services**: Services, Ingresses
- **Config**: ConfigMaps, Secrets (metadata only)
- **Storage**: PersistentVolumes, PersistentVolumeClaims
- **RBAC**: Roles, RoleBindings, ServiceAccounts
- **Namespaces**: All namespaces

### Required RBAC Permissions

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: elder-worker
rules:
- apiGroups: [""]
  resources: ["namespaces", "pods", "services", "secrets", "configmaps", "persistentvolumes", "persistentvolumeclaims", "serviceaccounts"]
  verbs: ["get", "list"]
- apiGroups: ["apps"]
  resources: ["deployments", "statefulsets", "daemonsets", "replicasets"]
  verbs: ["get", "list"]
- apiGroups: ["rbac.authorization.k8s.io"]
  resources: ["roles", "rolebindings", "clusterroles", "clusterrolebindings"]
  verbs: ["get", "list"]
```

---

## LDAP Connector

Syncs users and groups from LDAP/Active Directory.

### Configuration

```bash
LDAP_ENABLED=true
LDAP_SERVER=ldap.example.com
LDAP_PORT=389
LDAP_USE_SSL=false
LDAP_VERIFY_CERT=true
LDAP_BIND_DN=cn=admin,dc=example,dc=com
LDAP_BIND_PASSWORD=<password>
LDAP_BASE_DN=dc=example,dc=com
LDAP_USER_FILTER=(objectClass=person)
LDAP_GROUP_FILTER=(objectClass=group)
LDAP_SYNC_INTERVAL=3600
```

### Resources Synced

- **Identity**: Users with attributes (cn, uid, mail, etc.)
- **Groups**: Groups with member counts
- **Organizations**: Organizational Units as Elder organizations

### LDAPS (SSL/TLS)

For secure LDAP connections:

```bash
LDAP_PORT=636
LDAP_USE_SSL=true
LDAP_VERIFY_CERT=true
```

---

## iBoss Connector

Syncs resources from iBoss cloud security platform.

### Configuration

```bash
IBOSS_ENABLED=true
IBOSS_API_URL=https://api.iboss.com
IBOSS_API_KEY=<api-key>
IBOSS_TENANT_ID=<tenant-id>
IBOSS_SYNC_INTERVAL=3600
```

### Resources Synced

- **Identity**: Users, Groups
- **Security**: Web filtering policies
- **Network**: Cloud connectors/gateways
- **Applications**: Application usage and visibility

### Entity Types

| Resource | Elder Entity Type | Tags |
|----------|-------------------|------|
| Users | identity | iboss, user |
| Groups | identity | iboss, group |
| Policies | security | iboss, policy, web-filtering |
| Connectors | network | iboss, connector, gateway |
| Applications | compute | iboss, application, software, {category} |

---

## vCenter Connector

Syncs resources from VMware vCenter infrastructure.

### Configuration

```bash
VCENTER_ENABLED=true
VCENTER_HOST=vcenter.example.com
VCENTER_PORT=443
VCENTER_USERNAME=administrator@vsphere.local
VCENTER_PASSWORD=<password>
VCENTER_VERIFY_SSL=true
VCENTER_SYNC_INTERVAL=3600
```

### Resources Synced

- **Compute**: ESXi hosts, Virtual machines
- **Storage**: Datastores
- **Network**: Networks, Distributed port groups
- **Organizations**: Datacenters, Clusters

### Organization Hierarchy

vCenter resources are organized in Elder as:
```
vCenter: vcenter.example.com
├── DC: Datacenter-1
│   ├── Cluster: Production
│   │   ├── esxi-host-01
│   │   ├── esxi-host-02
│   │   ├── vm-web-01
│   │   └── vm-db-01
│   └── Cluster: Development
└── DC: Datacenter-2
```

### Required Permissions

Read-only access to vCenter is sufficient:
- `System.View`
- `System.Read`

---

## FleetDM Connector

Syncs endpoints and security data from FleetDM.

### Configuration

```bash
FLEETDM_ENABLED=true
FLEETDM_URL=https://fleet.example.com
FLEETDM_API_TOKEN=<api-token>
FLEETDM_SYNC_INTERVAL=3600
```

### Resources Synced

- **Compute**: Managed endpoints/hosts
- **Security**: Vulnerabilities (CVEs), Compliance policies
- **Software**: Installed software inventory across fleet
- **Organizations**: Teams

### Entity Types

| Resource | Elder Entity Type | Tags |
|----------|-------------------|------|
| Hosts | compute | fleetdm, endpoint, {platform} |
| Vulnerabilities | security | fleetdm, vulnerability, {severity} |
| Policies | security | fleetdm, policy, compliance |
| Software | compute | fleetdm, software, application, {source} |

### Vulnerability Severity Mapping

FleetDM CVSS scores are mapped to severity levels:
- **Critical**: CVSS >= 9.0
- **High**: CVSS >= 7.0
- **Medium**: CVSS >= 4.0
- **Low**: CVSS < 4.0

---

## Running the Worker Service

### Docker Compose

```bash
docker-compose up -d worker
```

### Standalone

```bash
cd apps/worker
python3 -m main
```

### Test Connectivity

```bash
docker-compose exec worker \
  python3 /app/apps/worker/test_connectivity.py
```

### Health Check

```bash
curl http://localhost:8000/status
```

---

## Sync Behavior

### Initial Sync

On startup, connectors perform a full sync of all resources.

### Incremental Updates

Connectors run on configurable intervals (default: 1 hour) and update existing entities or create new ones.

### Entity Matching

Entities are matched using provider-specific IDs stored in attributes:
- AWS: `aws_instance_id`, `aws_rds_id`, etc.
- vCenter: `vcenter_vm_id`, `vcenter_host_id`, etc.
- FleetDM: `fleetdm_host_id`, `cve`, etc.

### Error Handling

- Failed syncs are logged but don't stop other connectors
- Individual entity failures are recorded in sync results
- Retry logic with configurable max retries

---

## Monitoring

### Prometheus Metrics

Connectors expose metrics at `/metrics`:
- `elder_connector_sync_total` - Total sync operations
- `elder_connector_sync_duration_seconds` - Sync duration
- `elder_connector_entities_synced` - Entities synced per connector
- `elder_connector_errors_total` - Sync errors

### Logging

Set log level via `LOG_LEVEL` environment variable:
- `DEBUG`: Detailed sync information
- `INFO`: Standard operation logs
- `WARNING`: Non-critical issues
- `ERROR`: Failures

---

## Troubleshooting

### Common Issues

**Connection refused**
- Verify network connectivity to external service
- Check firewall rules
- Validate credentials

**Authentication failed**
- Verify API keys/tokens
- Check credential permissions
- Ensure credentials haven't expired

**No entities synced**
- Check filters (e.g., LDAP filters)
- Verify permissions on external service
- Review sync logs for errors

### Debug Mode

Enable debug logging:
```bash
LOG_LEVEL=DEBUG
```

### Test Individual Connectors

```python
# In Python shell
from apps.worker.connectors.aws_connector import AWSConnector

async def test():
    connector = AWSConnector()
    await connector.connect()
    result = await connector.sync()
    print(result.to_dict())
    await connector.disconnect()

import asyncio
asyncio.run(test())
```
