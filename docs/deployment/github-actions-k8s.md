# GitHub Actions Kubernetes Deployment Guide

This guide explains how to configure GitHub Actions to automatically deploy Elder to your Kubernetes cluster on every push to the main branch.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Setup Process](#setup-process)
  - [Step 1: Prepare Kubernetes Cluster](#step-1-prepare-kubernetes-cluster)
  - [Step 2: Run Setup Script](#step-2-run-setup-script)
  - [Step 3: Configure GitHub Secrets](#step-3-configure-github-secrets)
  - [Step 4: Test Deployment](#step-4-test-deployment)
- [How It Works](#how-it-works)
- [Configuration Options](#configuration-options)
- [Monitoring Deployments](#monitoring-deployments)
- [Troubleshooting](#troubleshooting)
- [Security Considerations](#security-considerations)

## Overview

The Elder project includes automated Kubernetes deployment via GitHub Actions. When you push to the main branch:

1. **Build**: Multi-arch Docker images are built (amd64/arm64)
2. **Push**: Images are pushed to GHCR (GitHub Container Registry)
3. **Test**: Integration tests run against built images
4. **Deploy**: Images are automatically deployed to your Kubernetes cluster
5. **Verify**: Health checks ensure successful deployment

**Key Features:**
- ✅ Automatic deployment on main branch pushes
- ✅ Graceful skip if Kubernetes is not configured
- ✅ 60-second delay for GHCR container propagation
- ✅ Helm-based deployment with automatic rollback
- ✅ Health checks and pod readiness verification
- ✅ Detailed logging and failure diagnostics

## Prerequisites

Before setting up GitHub Actions deployment, ensure you have:

1. **Kubernetes Cluster**: Running and accessible (MicroK8s, kind, k3s, or standard Kubernetes)
2. **kubectl**: Installed and configured to access your cluster
3. **helm**: Helm 3 installed
4. **jq**: JSON processor (for setup script)
5. **Cluster Access**: Admin or sufficient permissions to create namespaces, serviceaccounts, and roles
6. **GitHub Repository**: Admin access to configure secrets

## Setup Process

### Step 1: Prepare Kubernetes Cluster

Ensure your Kubernetes cluster is running and accessible:

```bash
# Verify cluster access
kubectl cluster-info
kubectl get nodes

# Verify Helm is working
helm version
```

**For MicroK8s:**

```bash
microk8s status --wait-ready
microk8s enable dns storage helm3
```

**For kind:**

```bash
kind create cluster --name elder
kubectl cluster-info --context kind-elder
```

### Step 2: Run Setup Script

The setup script automates ServiceAccount creation and kubeconfig generation.

**Basic usage:**

```bash
cd /path/to/Elder
./scripts/k8s/setup-github-serviceaccount.sh
```

**With custom options:**

```bash
# Custom namespace
./scripts/k8s/setup-github-serviceaccount.sh --namespace elder-prod

# Custom ServiceAccount name
./scripts/k8s/setup-github-serviceaccount.sh \
  --namespace elder \
  --serviceaccount deploy-bot \
  --role-name deploy-bot-role

# JSON output (for automation)
./scripts/k8s/setup-github-serviceaccount.sh --output json
```

**Script output:**

The script will display:
1. ✅ Kubernetes distribution detected
2. ✅ Prerequisites validated
3. ✅ Namespace created
4. ✅ ServiceAccount created
5. ✅ RBAC permissions configured
6. ✅ Kubeconfig generated
7. 📋 GitHub secrets in copy-paste format

**Example output:**

```
============================================
GitHub Secrets Configuration
============================================

Add these secrets to your GitHub repository:
(Settings → Secrets and variables → Actions → New repository secret)

Secret Name: KUBE_CONFIG
Secret Value:
YXBpVmVyc2lvbjogdjEKa2luZDogQ29uZmlnCmNsdXN0ZXJzOgotIG5hbWU6IGVs...

Secret Name: K8S_NAMESPACE
Secret Value: elder

============================================
```

### Step 3: Configure GitHub Secrets

Add the secrets output by the setup script to your GitHub repository.

**Navigate to GitHub:**

1. Go to your repository on GitHub
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**

**Required Secrets:**

| Secret Name | Description | Source |
|-------------|-------------|--------|
| `KUBE_CONFIG` | Base64-encoded kubeconfig | Setup script output |
| `K8S_NAMESPACE` | Target namespace | Setup script output (default: `elder`) |

**Additional Recommended Secrets:**

| Secret Name | Description | How to Generate |
|-------------|-------------|-----------------|
| `SECRET_KEY` | Application secret key | `openssl rand -base64 32` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `openssl rand -base64 32` |
| `REDIS_PASSWORD` | Redis password | `openssl rand -base64 32` |
| `LICENSE_KEY` | Elder license key (optional) | Your license key |

**Adding a secret:**

1. Click **New repository secret**
2. **Name**: `KUBE_CONFIG` (use exact name)
3. **Secret**: Paste the base64 value from script output
4. Click **Add secret**
5. Repeat for other secrets

### Step 4: Test Deployment

Trigger a deployment by pushing to main branch:

```bash
git checkout main
git add .
git commit -m "test: Trigger Kubernetes deployment"
git push origin main
```

**Monitor the workflow:**

```bash
# Using GitHub CLI
gh workflow view docker-build

# Or view in browser
# https://github.com/YOUR_ORG/Elder/actions
```

## How It Works

### Workflow Trigger

The deployment job (`deploy-k8s`) runs when:
- ✅ Push is to `main` branch
- ✅ Event is not a pull request
- ✅ `KUBE_CONFIG` secret exists

### Deployment Steps

1. **Checkout code**: Get latest code from repository
2. **Wait for GHCR**: 60-second delay for multi-arch manifest propagation
3. **Setup kubectl**: Install kubectl v1.28.0
4. **Setup Helm**: Install Helm v3.13.0
5. **Configure kubeconfig**: Decode and apply kubeconfig from secret
6. **Update dependencies**: Download PostgreSQL and Redis Helm charts
7. **Deploy with Helm**: Run `helm upgrade --install` with:
   - Image tags: `beta` (from main branch)
   - Secrets from GitHub secrets
   - `--atomic` flag for automatic rollback
   - 10-minute timeout
8. **Verify deployment**: Check rollout status and pod health
9. **Health check**: Port-forward and test `/healthz` endpoint
10. **Summary**: Display access instructions

### Image Tags

Images are tagged based on branch:

| Branch | Tag | Example |
|--------|-----|---------|
| `main` | `beta` | `ghcr.io/penguintechinc/elder-api:beta` |
| `develop` | `prototype-develop` | `ghcr.io/penguintechinc/elder-api:prototype-develop` |
| Release tags | Semver + `latest` | `ghcr.io/penguintechinc/elder-api:v1.2.3` |

### GHCR Propagation Delay

Multi-architecture images require time to propagate across GHCR infrastructure. The workflow includes a configurable 60-second delay before deployment.

**Why is this needed?**
- Multi-arch builds create manifest lists
- Manifest lists take time to become available
- Without delay, Kubernetes may fail to pull images

**Configuring the delay:**

Edit `.github/workflows/docker-build.yml`:

```yaml
env:
  GHCR_PROPAGATION_DELAY: 90  # Increase to 90 seconds
```

## Configuration Options

### Namespace Configuration

**Using different namespace:**

1. Update `K8S_NAMESPACE` secret in GitHub
2. Re-run setup script with `--namespace` flag

```bash
./scripts/k8s/setup-github-serviceaccount.sh --namespace elder-staging
```

### Custom Helm Values

**Override Helm values in workflow:**

Edit `.github/workflows/docker-build.yml`:

```yaml
helm upgrade --install elder ./k8s/helm/elder \
  --namespace ${{ env.K8S_NAMESPACE }} \
  --set api.replicaCount=3 \
  --set config.logging.level=INFO \
  --set monitoring.enabled=true
```

### Disabling Kubernetes Deployment

**Temporarily disable:**

Remove the `KUBE_CONFIG` secret from GitHub. The workflow will skip deployment.

**Permanently disable:**

Comment out or remove the `deploy-k8s` job from `.github/workflows/docker-build.yml`.

## Monitoring Deployments

### GitHub Actions UI

**View workflow runs:**
1. Go to repository **Actions** tab
2. Click on **Build and Push Docker Images** workflow
3. Click on latest run
4. Click on **deploy-k8s** job

### GitHub CLI

**List workflow runs:**

```bash
gh workflow list
gh workflow view docker-build
gh run list --workflow=docker-build
```

**View specific run:**

```bash
gh run view <run-id>
gh run view <run-id> --log
```

**Watch live logs:**

```bash
gh run watch
```

### Kubernetes Cluster

**Check deployment status:**

```bash
kubectl get deployments -n elder
kubectl get pods -n elder
kubectl rollout status deployment/elder-api -n elder
```

**View logs:**

```bash
kubectl logs -n elder -l app.kubernetes.io/component=api --tail=100
kubectl logs -n elder -l app.kubernetes.io/component=api -f  # Follow
```

**Check events:**

```bash
kubectl get events -n elder --sort-by='.lastTimestamp'
```

## Troubleshooting

### Deployment Fails: "KUBE_CONFIG not found"

**Problem**: The `KUBE_CONFIG` secret is not configured or incorrectly named.

**Solution**:
1. Verify secret exists: GitHub Settings → Secrets → Actions
2. Check exact spelling: `KUBE_CONFIG` (case-sensitive)
3. Re-run setup script and add secret

### Deployment Fails: "Unable to connect to cluster"

**Problem**: kubeconfig is invalid or cluster is unreachable.

**Solution**:
1. Verify cluster is running: `kubectl cluster-info`
2. Re-run setup script to generate new kubeconfig
3. Check if cluster IP changed (common with kind/MicroK8s)
4. Ensure firewall allows GitHub Actions IPs (if using cloud cluster)

### Deployment Fails: "Image pull error"

**Problem**: Images not available in GHCR or propagation delay too short.

**Solution**:
1. Verify images exist: `docker pull ghcr.io/penguintechinc/elder-api:beta`
2. Increase propagation delay:
   ```yaml
   env:
     GHCR_PROPAGATION_DELAY: 90
   ```
3. Check GHCR permissions: Images must be public or cluster must authenticate

### Deployment Fails: "Insufficient permissions"

**Problem**: ServiceAccount lacks necessary RBAC permissions.

**Solution**:
1. Check Role permissions:
   ```bash
   kubectl describe role github-ci-deployer -n elder
   ```
2. Re-run setup script to recreate RBAC
3. Verify RoleBinding exists:
   ```bash
   kubectl get rolebinding -n elder
   ```

### Health Check Fails

**Problem**: Deployment succeeded but health check fails.

**Solution**:
1. Check pod logs:
   ```bash
   kubectl logs -n elder -l app.kubernetes.io/component=api
   ```
2. Verify healthz endpoint:
   ```bash
   kubectl port-forward svc/elder-api 8080:80
   curl http://localhost:8080/healthz
   ```
3. Check database connectivity
4. Verify all required secrets are configured

### Helm Deployment Fails

**Problem**: Helm upgrade/install fails.

**View Helm release:**

```bash
# On your cluster
helm list -n elder
helm status elder -n elder
helm get values elder -n elder
```

**Rollback manually:**

```bash
helm rollback elder -n elder
```

**Debug Helm:**

The workflow uses `--debug` flag. Check GitHub Actions logs for:
- Helm chart syntax errors
- Missing dependencies
- Invalid values

## Security Considerations

### ServiceAccount Permissions

The GitHub CI ServiceAccount has **namespace-scoped** permissions only:

**What it CAN do:**
- ✅ Create/update Deployments, Services, ConfigMaps, Secrets
- ✅ Read Pod logs for debugging
- ✅ Create Ingress and HPA resources
- ✅ Manage Jobs and CronJobs

**What it CANNOT do:**
- ❌ Access other namespaces
- ❌ View or modify cluster-wide resources
- ❌ Create or modify Nodes
- ❌ Modify RBAC beyond assigned namespace
- ❌ Access cluster admin functions

### Secret Management

**Best Practices:**

1. **Rotate secrets regularly**: Update `KUBE_CONFIG` every 90 days
2. **Use strong passwords**: Generate with `openssl rand -base64 32`
3. **Limit secret access**: Only repository admins should configure secrets
4. **Audit secret usage**: Review GitHub Actions logs regularly
5. **Never commit secrets**: Secrets must only be in GitHub Secrets

### Token Expiration

ServiceAccount tokens generated by the setup script have a **10-year expiration** (87600 hours).

**To use shorter expiration:**

Edit setup script:

```bash
SA_TOKEN=$(kubectl create token "$SERVICEACCOUNT" -n "$NAMESPACE" --duration=2160h) # 90 days
```

### Network Security

**For production clusters:**

1. **Firewall**: Restrict GitHub Actions IPs (if possible)
2. **VPN**: Consider VPN tunnel for external clusters
3. **Private clusters**: Use self-hosted GitHub Actions runners

## Additional Resources

- [Setup Script Source](../../scripts/k8s/setup-github-serviceaccount.sh)
- [Kubernetes Manifests](../../infrastructure/k8s/github-ci/)
- [Workflow File](../../.github/workflows/docker-build.yml)
- [Local Kubernetes Setup](./local-kubernetes-setup.md)
- [Helm Chart](../../k8s/helm/elder/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)

## Support

For issues or questions:
- Check GitHub Actions logs: `gh run view --log`
- Review pod logs: `kubectl logs -n elder -l app.kubernetes.io/name=elder`
- Verify cluster status: `kubectl cluster-info`
- File an issue: [GitHub Issues](https://github.com/penguintechinc/Elder/issues)
