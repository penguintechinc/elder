#!/bin/bash
#
# Elder Kubernetes ServiceAccount Setup for GitHub Actions CI/CD
#
# This script creates a Kubernetes ServiceAccount with appropriate RBAC permissions
# for GitHub Actions to deploy Elder to a k8s cluster.
#
# Supports: MicroK8s, kind, k3s, standard Kubernetes
#
# Usage:
#   ./setup-github-serviceaccount.sh [OPTIONS]
#
# Options:
#   --namespace NAME          Kubernetes namespace (default: elder)
#   --serviceaccount NAME     ServiceAccount name (default: github-ci)
#   --role-name NAME          Role name (default: github-ci-deployer)
#   --output FORMAT           Output format: github, json, yaml (default: github)
#   --context CONTEXT         Kubectl context to use
#   --help                    Show this help message
#

set -euo pipefail

# Default values
NAMESPACE="${NAMESPACE:-elder}"
SERVICEACCOUNT="${SERVICEACCOUNT:-github-ci}"
ROLE_NAME="${ROLE_NAME:-github-ci-deployer}"
OUTPUT_FORMAT="${OUTPUT_FORMAT:-github}"
KUBECTL_CMD="kubectl"
KUBECTL_CONTEXT=""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Functions
print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_header() { echo -e "${CYAN}=== $1 ===${NC}"; }

usage() {
    cat <<EOF
${CYAN}Elder Kubernetes ServiceAccount Setup for GitHub Actions${NC}

${BLUE}USAGE:${NC}
    $0 [OPTIONS]

${BLUE}DESCRIPTION:${NC}
    Creates a Kubernetes ServiceAccount with appropriate RBAC permissions
    for GitHub Actions to deploy Elder to a k8s cluster. Generates kubeconfig
    and outputs credentials in GitHub Secrets format.

${BLUE}OPTIONS:${NC}
    --namespace NAME          Kubernetes namespace (default: elder)
    --serviceaccount NAME     ServiceAccount name (default: github-ci)
    --role-name NAME          Role name (default: github-ci-deployer)
    --output FORMAT           Output format: github, json, yaml (default: github)
    --context CONTEXT         Kubectl context to use
    --help                    Show this help message

${BLUE}EXAMPLES:${NC}
    # Basic usage with defaults
    $0

    # Custom namespace
    $0 --namespace elder-prod

    # JSON output for programmatic use
    $0 --output json

    # Full customization
    $0 --namespace elder-staging --serviceaccount deploy-bot --output github

${BLUE}SUPPORTED PLATFORMS:${NC}
    - MicroK8s
    - kind (Kubernetes in Docker)
    - k3s (Lightweight Kubernetes)
    - Standard Kubernetes

${BLUE}OUTPUT:${NC}
    The script will output:
    - KUBE_CONFIG: Base64-encoded kubeconfig for GitHub secret
    - K8S_NAMESPACE: Target namespace name

${BLUE}REQUIREMENTS:${NC}
    - kubectl installed and configured
    - jq installed (for JSON processing)
    - Access to Kubernetes cluster
    - Permissions to create namespaces, serviceaccounts, roles, rolebindings

EOF
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --namespace) NAMESPACE="$2"; shift 2 ;;
        --serviceaccount) SERVICEACCOUNT="$2"; shift 2 ;;
        --role-name) ROLE_NAME="$2"; shift 2 ;;
        --output) OUTPUT_FORMAT="$2"; shift 2 ;;
        --context) KUBECTL_CONTEXT="$2"; shift 2 ;;
        --help) usage ;;
        *) print_error "Unknown option: $1"; usage ;;
    esac
done

# Detect Kubernetes distribution
detect_k8s_distribution() {
    print_header "Detecting Kubernetes Distribution"

    if command -v microk8s >/dev/null 2>&1; then
        if microk8s status >/dev/null 2>&1; then
            print_success "Detected MicroK8s"
            KUBECTL_CMD="microk8s kubectl"
            return 0
        else
            print_warning "MicroK8s found but not running"
        fi
    fi

    # Check if kubectl is available
    if ! command -v kubectl >/dev/null 2>&1; then
        print_error "kubectl not found. Please install kubectl."
        exit 1
    fi

    KUBECTL_CMD="kubectl"

    # Try to detect cluster type from cluster-info
    if $KUBECTL_CMD cluster-info 2>/dev/null | grep -qi "kind"; then
        print_success "Detected kind (Kubernetes in Docker)"
    elif $KUBECTL_CMD cluster-info 2>/dev/null | grep -qi "k3s"; then
        print_success "Detected k3s"
    else
        print_success "Using standard Kubernetes"
    fi

    # Check for helm (optional dependency; not otherwise used by this script)
    if ! command -v helm >/dev/null 2>&1; then
        print_warning "Helm not found (optional, but recommended)"
    fi
}

# Validate prerequisites
validate_prerequisites() {
    print_header "Validating Prerequisites"

    # Check kubectl
    if ! command -v ${KUBECTL_CMD%% *} >/dev/null 2>&1; then
        print_error "kubectl command not found: ${KUBECTL_CMD}"
        exit 1
    fi

    # Check jq
    if ! command -v jq >/dev/null 2>&1; then
        print_error "jq not found (required for JSON processing)"
        print_info "Install with: sudo apt-get install jq  # or  brew install jq"
        exit 1
    fi

    # Check cluster connectivity
    if ! $KUBECTL_CMD cluster-info >/dev/null 2>&1; then
        print_error "Cannot connect to Kubernetes cluster"
        print_info "Please ensure kubectl is configured correctly"
        exit 1
    fi

    # Apply context if specified
    if [ -n "$KUBECTL_CONTEXT" ]; then
        KUBECTL_CMD="$KUBECTL_CMD --context=$KUBECTL_CONTEXT"
        print_info "Using context: $KUBECTL_CONTEXT"
    fi

    # Display cluster info
    CURRENT_CONTEXT=$($KUBECTL_CMD config current-context 2>/dev/null || echo "unknown")
    CLUSTER_SERVER=$($KUBECTL_CMD cluster-info 2>/dev/null | head -1 || echo "unknown")
    print_success "Connected to cluster: $CURRENT_CONTEXT"
    print_info "Cluster: $CLUSTER_SERVER"

    print_success "All prerequisites validated"
}

# Create namespace
create_namespace() {
    print_header "Creating Namespace"

    if $KUBECTL_CMD get namespace "$NAMESPACE" >/dev/null 2>&1; then
        print_warning "Namespace '$NAMESPACE' already exists (skipping creation)"
    else
        cat <<EOF | $KUBECTL_CMD apply -f -
apiVersion: v1
kind: Namespace
metadata:
  name: $NAMESPACE
  labels:
    app.kubernetes.io/name: elder
    app.kubernetes.io/instance: elder
    app.kubernetes.io/managed-by: github-actions
    environment: production
EOF
        print_success "Namespace '$NAMESPACE' created"
    fi
}

# Create ServiceAccount
create_serviceaccount() {
    print_header "Creating ServiceAccount"

    cat <<EOF | $KUBECTL_CMD apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $SERVICEACCOUNT
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: elder
    app.kubernetes.io/component: ci-cd
    app.kubernetes.io/managed-by: github-actions
  annotations:
    description: "ServiceAccount for GitHub Actions CI/CD deployments"
automountServiceAccountToken: false
EOF

    print_success "ServiceAccount '$SERVICEACCOUNT' created in namespace '$NAMESPACE'"
}

# Create RBAC
create_rbac() {
    print_header "Creating RBAC (Role + RoleBinding)"

    cat <<EOF | $KUBECTL_CMD apply -f -
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: $ROLE_NAME
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: elder
    app.kubernetes.io/component: ci-cd
    app.kubernetes.io/managed-by: github-actions
rules:
# Deployments, ReplicaSets, StatefulSets
- apiGroups: ["apps"]
  resources: ["deployments", "replicasets", "statefulsets"]
  verbs: ["get", "list", "create", "update", "patch", "delete", "watch"]

# Services, ConfigMaps, Secrets, Pods, PVCs
- apiGroups: [""]
  resources: ["services", "configmaps", "secrets", "pods", "persistentvolumeclaims"]
  verbs: ["get", "list", "create", "update", "patch", "delete", "watch"]

# Pod logs (for debugging)
- apiGroups: [""]
  resources: ["pods/log"]
  verbs: ["get", "list"]

# Ingress resources
- apiGroups: ["networking.k8s.io"]
  resources: ["ingresses"]
  verbs: ["get", "list", "create", "update", "patch"]

# HorizontalPodAutoscaler
- apiGroups: ["autoscaling"]
  resources: ["horizontalpodautoscalers"]
  verbs: ["get", "list", "create", "update", "patch"]

# Batch resources (Jobs, CronJobs)
- apiGroups: ["batch"]
  resources: ["jobs", "cronjobs"]
  verbs: ["get", "list", "create", "update", "patch", "delete"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: $ROLE_NAME
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: elder
    app.kubernetes.io/component: ci-cd
    app.kubernetes.io/managed-by: github-actions
subjects:
- kind: ServiceAccount
  name: $SERVICEACCOUNT
  namespace: $NAMESPACE
roleRef:
  kind: Role
  name: $ROLE_NAME
  apiGroup: rbac.authorization.k8s.io
EOF

    print_success "Role '$ROLE_NAME' created with namespace-scoped permissions"
    print_success "RoleBinding created for ServiceAccount '$SERVICEACCOUNT'"

    print_info "Permissions granted:"
    echo "  - Deployments, ReplicaSets, StatefulSets: full access"
    echo "  - Services, ConfigMaps, Secrets, Pods, PVCs: full access"
    echo "  - Pod logs: read-only"
    echo "  - Ingress: create/update"
    echo "  - HorizontalPodAutoscaler: create/update"
    echo "  - Jobs, CronJobs: full access"
}

# Generate kubeconfig
generate_kubeconfig() {
    print_header "Generating Kubeconfig"

    # Get ServiceAccount token (handles both legacy and new token API)
    print_info "Retrieving ServiceAccount token..."

    # Try legacy method first (token in secret)
    SA_SECRET=$($KUBECTL_CMD get serviceaccount "$SERVICEACCOUNT" -n "$NAMESPACE" \
        -o jsonpath='{.secrets[0].name}' 2>/dev/null || echo "")

    if [ -n "$SA_SECRET" ] && [ "$SA_SECRET" != "null" ]; then
        print_info "Using legacy token from secret: $SA_SECRET"
        SA_TOKEN=$($KUBECTL_CMD get secret "$SA_SECRET" -n "$NAMESPACE" \
            -o jsonpath='{.data.token}' | base64 -d)
    else
        # New method: create token (Kubernetes 1.24+)
        print_info "Using new token API (Kubernetes 1.24+)"
        SA_TOKEN=$($KUBECTL_CMD create token "$SERVICEACCOUNT" -n "$NAMESPACE" --duration=87600h 2>/dev/null || \
                  $KUBECTL_CMD create token "$SERVICEACCOUNT" -n "$NAMESPACE" 2>/dev/null)

        if [ -z "$SA_TOKEN" ]; then
            print_error "Failed to create token for ServiceAccount"
            exit 1
        fi
    fi

    print_success "Token retrieved successfully"

    # Get cluster info
    print_info "Retrieving cluster information..."
    CLUSTER_SERVER=$($KUBECTL_CMD config view --minify -o jsonpath='{.clusters[0].cluster.server}')
    CLUSTER_CA=$($KUBECTL_CMD config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')

    # If CA data is empty, try to get it from the cluster
    if [ -z "$CLUSTER_CA" ] || [ "$CLUSTER_CA" = "null" ]; then
        print_info "Certificate authority data not in config, extracting from cluster..."
        CA_CERT_FILE=$($KUBECTL_CMD config view --minify -o jsonpath='{.clusters[0].cluster.certificate-authority}')
        if [ -n "$CA_CERT_FILE" ] && [ -f "$CA_CERT_FILE" ]; then
            CLUSTER_CA=$(base64 -w 0 < "$CA_CERT_FILE")
        else
            print_warning "No CA certificate found, using insecure-skip-tls-verify"
            CLUSTER_CA=""
        fi
    fi

    print_success "Cluster information retrieved"

    # Build kubeconfig
    print_info "Building kubeconfig..."

    if [ -n "$CLUSTER_CA" ]; then
        KUBECONFIG_CONTENT=$(cat <<EOF
apiVersion: v1
kind: Config
clusters:
- name: elder-cluster
  cluster:
    certificate-authority-data: $CLUSTER_CA
    server: $CLUSTER_SERVER
contexts:
- name: elder-context
  context:
    cluster: elder-cluster
    namespace: $NAMESPACE
    user: $SERVICEACCOUNT
current-context: elder-context
users:
- name: $SERVICEACCOUNT
  user:
    token: $SA_TOKEN
EOF
        )
    else
        # Fallback for clusters without CA (like some local dev clusters)
        KUBECONFIG_CONTENT=$(cat <<EOF
apiVersion: v1
kind: Config
clusters:
- name: elder-cluster
  cluster:
    insecure-skip-tls-verify: true
    server: $CLUSTER_SERVER
contexts:
- name: elder-context
  context:
    cluster: elder-cluster
    namespace: $NAMESPACE
    user: $SERVICEACCOUNT
current-context: elder-context
users:
- name: $SERVICEACCOUNT
  user:
    token: $SA_TOKEN
EOF
        )
    fi

    # Base64 encode for GitHub secret
    KUBECONFIG_BASE64=$(echo "$KUBECONFIG_CONTENT" | base64 -w 0)

    print_success "Kubeconfig generated and encoded"
}

# Output results
output_results() {
    print_header "Configuration Complete"

    case "$OUTPUT_FORMAT" in
        github)
            echo ""
            echo "============================================"
            echo "GitHub Secrets Configuration"
            echo "============================================"
            echo ""
            echo "Add these secrets to your GitHub repository:"
            echo "(Settings → Secrets and variables → Actions → New repository secret)"
            echo ""
            echo "${GREEN}Secret Name:${NC} KUBE_CONFIG"
            echo "${YELLOW}Secret Value:${NC}"
            echo "$KUBECONFIG_BASE64"
            echo ""
            echo "${GREEN}Secret Name:${NC} K8S_NAMESPACE"
            echo "${YELLOW}Secret Value:${NC} $NAMESPACE"
            echo ""
            echo "============================================"
            echo "Additional Recommended Secrets"
            echo "============================================"
            echo ""
            echo "Generate and add these secrets for full functionality:"
            echo ""
            echo "${GREEN}SECRET_KEY${NC} (Flask secret key)"
            echo "  Generate with: openssl rand -base64 32"
            echo ""
            echo "${GREEN}POSTGRES_PASSWORD${NC} (PostgreSQL password)"
            echo "  Generate with: openssl rand -base64 32"
            echo ""
            echo "${GREEN}REDIS_PASSWORD${NC} (Redis password)"
            echo "  Generate with: openssl rand -base64 32"
            echo ""
            echo "${GREEN}LICENSE_KEY${NC} (Elder license key - optional)"
            echo "  Format: PENG-XXXX-XXXX-XXXX-XXXX-ABCD"
            echo ""
            echo "============================================"
            echo "Next Steps"
            echo "============================================"
            echo ""
            echo "1. Copy KUBE_CONFIG value to GitHub secret"
            echo "2. Copy K8S_NAMESPACE value to GitHub secret"
            echo "3. Add other required secrets (SECRET_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD)"
            echo "4. Push to main branch to trigger deployment"
            echo "5. Monitor deployment: gh workflow view docker-build"
            echo ""
            echo "${GREEN}Setup completed successfully!${NC}"
            echo ""
            ;;
        json)
            cat <<EOF
{
  "secrets": {
    "KUBE_CONFIG": "$KUBECONFIG_BASE64",
    "K8S_NAMESPACE": "$NAMESPACE"
  },
  "serviceAccount": "$SERVICEACCOUNT",
  "namespace": "$NAMESPACE",
  "roleName": "$ROLE_NAME",
  "clusterServer": "$CLUSTER_SERVER"
}
EOF
            ;;
        yaml)
            cat <<EOF
secrets:
  KUBE_CONFIG: $KUBECONFIG_BASE64
  K8S_NAMESPACE: $NAMESPACE
serviceAccount: $SERVICEACCOUNT
namespace: $NAMESPACE
roleName: $ROLE_NAME
clusterServer: $CLUSTER_SERVER
EOF
            ;;
        *)
            print_error "Unknown output format: $OUTPUT_FORMAT"
            exit 1
            ;;
    esac
}

# Main execution
main() {
    echo ""
    print_header "Elder Kubernetes ServiceAccount Setup"
    echo ""
    print_info "This script will create Kubernetes resources for GitHub Actions CI/CD"
    print_info "Namespace: $NAMESPACE"
    print_info "ServiceAccount: $SERVICEACCOUNT"
    print_info "Role: $ROLE_NAME"
    print_info "Output format: $OUTPUT_FORMAT"
    echo ""

    detect_k8s_distribution
    validate_prerequisites
    create_namespace
    create_serviceaccount
    create_rbac
    generate_kubeconfig
    output_results

    echo ""
    print_success "All operations completed successfully!"
    echo ""
}

# Run main function
main
