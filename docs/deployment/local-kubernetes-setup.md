# Local Kubernetes Setup for Elder

This guide walks through setting up Elder on a local Kubernetes cluster for development and testing.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Kubernetes Distribution Options](#kubernetes-distribution-options)
  - [MicroK8s (Recommended)](#microk8s-recommended)
  - [kind (Kubernetes in Docker)](#kind-kubernetes-in-docker)
  - [k3s (Lightweight Kubernetes)](#k3s-lightweight-kubernetes)
- [Deploying Elder](#deploying-elder)
- [Accessing Services](#accessing-services)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)

## Prerequisites

- Linux, macOS, or Windows with WSL2
- At least 4GB RAM available for Kubernetes
- Docker installed (for kind)
- kubectl installed
- Helm 3 installed

## Quick Start

For the impatient:

```bash
# 1. Install MicroK8s (Linux)
sudo snap install microk8s --classic
microk8s enable dns storage helm3

# 2. Deploy Elder
cd k8s/helm/elder
microk8s helm3 dependency update
microk8s helm3 install elder . \
  --set config.secretKey="$(openssl rand -base64 32)" \
  --set postgresql.auth.password="$(openssl rand -base64 32)" \
  --set redis.auth.password="$(openssl rand -base64 32)"

# 3. Wait for deployment
microk8s kubectl wait --for=condition=ready pod \
  -l app.kubernetes.io/name=elder \
  --timeout=5m

# 4. Access Elder
microk8s kubectl port-forward svc/elder-api 8080:80
# Visit http://localhost:8080
```

## Kubernetes Distribution Options

### MicroK8s (Recommended)

MicroK8s is the easiest option for local development, especially on Linux.

**Installation (Linux):**

```bash
# Install MicroK8s
sudo snap install microk8s --classic

# Add your user to the microk8s group
sudo usermod -a -G microk8s $USER
sudo chown -R $USER ~/.kube
newgrp microk8s

# Enable required addons
microk8s enable dns storage helm3

# Optional: Enable additional addons
microk8s enable ingress  # For Ingress support
microk8s enable dashboard  # For Kubernetes dashboard
microk8s enable prometheus  # For monitoring
```

**Verify Installation:**

```bash
microk8s status --wait-ready
microk8s kubectl get nodes
```

**Using kubectl and helm:**

MicroK8s has its own kubectl and helm commands:
- `microk8s kubectl` instead of `kubectl`
- `microk8s helm3` instead of `helm`

**Optional: Create aliases:**

```bash
echo "alias kubectl='microk8s kubectl'" >> ~/.bashrc
echo "alias helm='microk8s helm3'" >> ~/.bashrc
source ~/.bashrc
```

### kind (Kubernetes in Docker)

kind is ideal if you already have Docker and want a throwaway cluster.

**Installation:**

```bash
# Install kind
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
chmod +x ./kind
sudo mv ./kind /usr/local/bin/kind

# Create a cluster
kind create cluster --name elder

# Verify
kubectl cluster-info --context kind-elder
kubectl get nodes
```

**Delete cluster when done:**

```bash
kind delete cluster --name elder
```

### k3s (Lightweight Kubernetes)

k3s is a production-ready, lightweight Kubernetes distribution.

**Installation (Linux):**

```bash
# Install k3s
curl -sfL https://get.k3s.io | sh -

# Wait for node to be ready
sudo k3s kubectl get nodes

# Optional: Use kubectl without sudo
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $USER ~/.kube/config
```

**Uninstall:**

```bash
/usr/local/bin/k3s-uninstall.sh
```

## Deploying Elder

### Step 1: Prepare Helm Chart

Navigate to the Helm chart directory:

```bash
cd k8s/helm/elder
```

Update dependencies (PostgreSQL and Redis from Bitnami):

```bash
helm dependency update
```

### Step 2: Configure Values

Create a custom values file for local deployment:

```bash
cat > values-local.yaml <<EOF
# Local development configuration
api:
  replicaCount: 1
  resources:
    requests:
      memory: "256Mi"
      cpu: "250m"

grpc:
  enabled: true
  replicaCount: 1

envoy:
  enabled: true
  replicaCount: 1

postgresql:
  enabled: true
  primary:
    persistence:
      size: 5Gi
  auth:
    password: "local-postgres-password"

redis:
  enabled: true
  master:
    persistence:
      size: 1Gi
  auth:
    password: "local-redis-password"

config:
  secretKey: "local-secret-key-change-in-production"
  flaskEnv: development
  logging:
    level: DEBUG
  metricsEnabled: true
EOF
```

### Step 3: Deploy with Helm

**For MicroK8s:**

```bash
microk8s helm3 install elder . -f values-local.yaml
```

**For kind or k3s:**

```bash
helm install elder . -f values-local.yaml
```

**Watch deployment progress:**

```bash
# MicroK8s
microk8s kubectl get pods -w

# Standard kubectl
kubectl get pods -w
```

### Step 4: Wait for Readiness

Wait for all pods to be ready:

```bash
kubectl wait --for=condition=ready pod \
  -l app.kubernetes.io/name=elder \
  --timeout=10m
```

## Accessing Services

### Port Forwarding

The simplest way to access services:

**Access API:**

```bash
kubectl port-forward svc/elder-api 8080:80
# Visit http://localhost:8080
# Health check: curl http://localhost:8080/healthz
```

**Access Web UI:**

```bash
kubectl port-forward svc/elder-web 3000:80
# Visit http://localhost:3000
```

**Access gRPC Server:**

```bash
kubectl port-forward svc/elder-grpc 50051:50051
```

**Access Grafana (if monitoring enabled):**

```bash
kubectl port-forward svc/elder-grafana 3001:80
# Visit http://localhost:3001
```

### Using Ingress (Optional)

If you enabled Ingress on MicroK8s:

```bash
# Enable Ingress
microk8s enable ingress

# Install Elder with Ingress enabled
helm upgrade --install elder . \
  -f values-local.yaml \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.host=elder.local

# Add to /etc/hosts
echo "127.0.0.1 elder.local" | sudo tee -a /etc/hosts

# Access
curl http://elder.local/healthz
```

## Configuration

### Environment Variables

Override configuration via Helm values:

```bash
helm upgrade elder . \
  --set config.flaskEnv=development \
  --set config.logging.level=DEBUG \
  --set config.auth.enableSAML=true \
  --set api.replicaCount=2
```

### Using External Databases

To use external PostgreSQL/Redis instead of in-cluster:

```bash
helm upgrade elder . \
  --set postgresql.enabled=false \
  --set redis.enabled=false \
  --set config.databaseUrl="postgresql://user:pass@external-host:5432/elder" \
  --set config.redisUrl="redis://:pass@external-host:6379/0"
```

### License Configuration

For enterprise features:

```bash
helm upgrade elder . \
  --set config.license.key="PENG-XXXX-XXXX-XXXX-XXXX-ABCD" \
  --set grpc.enabled=true \
  --set grpc.requireLicense=true
```

## Troubleshooting

### Pods Not Starting

**Check pod status:**

```bash
kubectl get pods
kubectl describe pod <pod-name>
```

**Check logs:**

```bash
kubectl logs <pod-name>
kubectl logs <pod-name> --previous  # Previous container logs
```

**Common issues:**

1. **ImagePullBackOff**: Images not available
   - Check image registry access
   - Verify image tags are correct
   - For GHCR: `docker login ghcr.io`

2. **CrashLoopBackOff**: Container crashing
   - Check logs: `kubectl logs <pod-name>`
   - Check configuration: `kubectl get configmap elder-config -o yaml`
   - Verify secrets: `kubectl get secret elder-secrets`

3. **Pending**: Insufficient resources
   - Check node resources: `kubectl describe node`
   - Reduce resource requests in values.yaml

### Database Connection Issues

**Check PostgreSQL pod:**

```bash
kubectl get pod -l app.kubernetes.io/name=postgresql
kubectl logs -l app.kubernetes.io/name=postgresql
```

**Connect to PostgreSQL directly:**

```bash
# Get password
POSTGRES_PASSWORD=$(kubectl get secret elder-postgresql -o jsonpath='{.data.password}' | base64 -d)

# Port forward
kubectl port-forward svc/elder-postgresql 5432:5432

# Connect
psql -h localhost -U elder -d elder
```

### Helm Deployment Failures

**Check Helm release status:**

```bash
helm list
helm status elder
helm get values elder
```

**Rollback to previous version:**

```bash
helm rollback elder
```

**Completely remove and reinstall:**

```bash
helm uninstall elder
kubectl delete namespace elder  # If using custom namespace
helm install elder . -f values-local.yaml
```

### Networking Issues

**Check services:**

```bash
kubectl get services
kubectl describe service elder-api
```

**Test internal connectivity:**

```bash
# Run a test pod
kubectl run test-pod --image=curlimages/curl --rm -it --restart=Never -- sh

# Inside the pod
curl http://elder-api/healthz
curl http://elder-postgresql:5432
```

### Resource Constraints

**Check resource usage:**

```bash
kubectl top nodes
kubectl top pods
```

**Adjust resource limits:**

```yaml
# values-local.yaml
api:
  resources:
    requests:
      memory: "128Mi"
      cpu: "100m"
    limits:
      memory: "512Mi"
      cpu: "500m"
```

### Debugging Tips

**Get all resources:**

```bash
kubectl get all -l app.kubernetes.io/name=elder
```

**Check events:**

```bash
kubectl get events --sort-by='.lastTimestamp'
```

**Exec into a running pod:**

```bash
kubectl exec -it <pod-name> -- /bin/bash
```

**Copy files from pod:**

```bash
kubectl cp <pod-name>:/path/to/file ./local-file
```

## Additional Resources

- [MicroK8s Documentation](https://microk8s.io/docs)
- [kind Documentation](https://kind.sigs.k8s.io/)
- [k3s Documentation](https://k3s.io/)
- [Helm Documentation](https://helm.sh/docs/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Elder Helm Chart](../../k8s/helm/elder/)
- [GitHub Actions Kubernetes Deployment](./github-actions-k8s.md)

## Next Steps

- [Setup GitHub Actions for CI/CD](./github-actions-k8s.md)
- [Configure monitoring and observability](../../infrastructure/monitoring/) — Prometheus, Alertmanager, and Grafana dashboard configs
- [GitHub Actions Kubernetes deployment](./github-actions-k8s.md)
