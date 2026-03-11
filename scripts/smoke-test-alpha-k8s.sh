#!/bin/bash
# Smoke Test Script for Elder - Kubernetes (MicroK8s) Alpha Environment
#
# Tests local K8s deployment:
# - Pod health and readiness
# - API endpoint availability
# - WebUI page loads
#
# Usage: ./scripts/smoke-test-alpha-k8s.sh [OPTIONS]
#
# Options:
#   --deploy     Deploy to K8s before testing (default: test existing deployment)
#   --verbose    Enable verbose output
#   --help       Show this help message

set -euo pipefail

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
CONTEXT="local-alpha"
NAMESPACE="elder"  # Per standards: namespace is just the product name
PRODUCT="elder"
DEPLOY=false
VERBOSE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --deploy) DEPLOY=true; shift ;;
        --verbose) VERBOSE=true; shift ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo "Options:"
            echo "  --deploy     Deploy to K8s before testing"
            echo "  --verbose    Enable verbose output"
            echo "  --help       Show this help"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Start tests
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Elder Smoke Test - Kubernetes Alpha${NC}"
echo -e "${BLUE}========================================${NC}"
log_info "Context: $CONTEXT"
log_info "Namespace: $NAMESPACE"
log_info "Product: $PRODUCT"
echo ""

# Check kubectl and context
log_info "Step 1: Checking Kubernetes setup..."
if ! command -v kubectl &> /dev/null; then
    log_error "kubectl not found. Install kubectl or enable K8s in Docker Desktop/Podman Desktop"
    exit 1
fi

if ! kubectl config get-contexts | grep -q "$CONTEXT"; then
    log_error "Context '$CONTEXT' not found. Available contexts:"
    kubectl config get-contexts
    exit 1
fi

log_success "kubectl found, context '$CONTEXT' available"

# Deploy if requested
if [ "$DEPLOY" = true ]; then
    log_info "Step 2: Deploying to K8s..."
    # Deploy manifests directly (Kustomize has path issues, to be fixed separately)
    kubectl apply --context "$CONTEXT" -f k8s/manifests/namespace.yaml
    kubectl apply --context "$CONTEXT" -f k8s/manifests/flask-backend/
    log_info "Waiting 60s for deployments to settle..."
    sleep 60
else
    log_info "Step 2: Skipping deployment (use --deploy to deploy)"
fi

# Check pods
log_info "Step 3: Checking pod health..."
POD_CHECK=$(kubectl --context "$CONTEXT" get pods -n "$NAMESPACE" -o json 2>/dev/null || echo "{}")

if [ "$POD_CHECK" = "{}" ]; then
    log_error "No pods found in namespace '$NAMESPACE'. Deploy with: $0 --deploy"
    exit 1
fi

# Count pods by status
TOTAL_PODS=$(echo "$POD_CHECK" | grep -o '"name"' | wc -l)
RUNNING_PODS=$(echo "$POD_CHECK" | jq -r '.items[] | select(.status.phase=="Running") | .metadata.name' 2>/dev/null | wc -l)
READY_PODS=$(echo "$POD_CHECK" | jq -r '.items[] | select(.status.conditions[]? | select(.type=="Ready" and .status=="True")) | .metadata.name' 2>/dev/null | wc -l)

log_info "Pods: $RUNNING_PODS/$TOTAL_PODS running, $READY_PODS/$TOTAL_PODS ready"

if [ "$RUNNING_PODS" -eq 0 ]; then
    log_error "No running pods found. Deploy with: $0 --deploy"
    kubectl --context "$CONTEXT" get pods -n "$NAMESPACE" -o wide
    exit 1
fi

log_success "Pods running"

# Get service endpoints
log_info "Step 4: Getting service endpoints..."
API_IP=$(kubectl --context "$CONTEXT" get svc -n "$NAMESPACE" -o jsonpath='{.items[?(@.metadata.name=="api")].status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
WEB_IP=$(kubectl --context "$CONTEXT" get svc -n "$NAMESPACE" -o jsonpath='{.items[?(@.metadata.name=="web")].status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")

# For local K8s, use port-forward if LoadBalancer IPs not available
if [ -z "$API_IP" ]; then
    log_info "No LoadBalancer IP for API. Using port-forward..."
    kubectl --context "$CONTEXT" port-forward -n "$NAMESPACE" svc/api 5000:5000 &>/dev/null &
    PF_API_PID=$!
    API_URL="http://localhost:5000"
    sleep 2
else
    API_URL="http://$API_IP:5000"
fi

if [ -z "$WEB_IP" ]; then
    log_info "No LoadBalancer IP for Web. Using port-forward..."
    kubectl --context "$CONTEXT" port-forward -n "$NAMESPACE" svc/web 3000:3000 &>/dev/null &
    PF_WEB_PID=$!
    WEB_URL="http://localhost:3000"
    sleep 2
else
    WEB_URL="http://$WEB_IP:3000"
fi

log_success "Service endpoints configured"

# Test API health
log_info "Step 5: Testing API health endpoint..."
API_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/healthz" 2>/dev/null || echo "000")

if [ "$API_HEALTH" = "200" ]; then
    log_success "API health check passed (HTTP 200)"
else
    log_error "API health check failed (HTTP $API_HEALTH)"
    [ ! -z "${PF_API_PID:-}" ] && kill $PF_API_PID 2>/dev/null || true
    [ ! -z "${PF_WEB_PID:-}" ] && kill $PF_WEB_PID 2>/dev/null || true
    exit 1
fi

# Test API status endpoint
log_info "Step 6: Testing API status endpoint..."
API_STATUS=$(curl -s "$API_URL/api/v1/status" 2>/dev/null | grep -o '"status"' || echo "")

if [ ! -z "$API_STATUS" ]; then
    log_success "API status endpoint working"
else
    log_error "API status endpoint failed"
    [ ! -z "${PF_API_PID:-}" ] && kill $PF_API_PID 2>/dev/null || true
    [ ! -z "${PF_WEB_PID:-}" ] && kill $PF_WEB_PID 2>/dev/null || true
    exit 1
fi

# Test web page load
log_info "Step 7: Testing WebUI page load..."
WEB_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$WEB_URL/" 2>/dev/null || echo "000")

if [ "$WEB_STATUS" = "200" ]; then
    log_success "WebUI loads successfully (HTTP 200)"
else
    log_error "WebUI failed to load (HTTP $WEB_STATUS)"
    [ ! -z "${PF_API_PID:-}" ] && kill $PF_API_PID 2>/dev/null || true
    [ ! -z "${PF_WEB_PID:-}" ] && kill $PF_WEB_PID 2>/dev/null || true
    exit 1
fi

# Cleanup port-forwards
[ ! -z "${PF_API_PID:-}" ] && kill $PF_API_PID 2>/dev/null || true
[ ! -z "${PF_WEB_PID:-}" ] && kill $PF_WEB_PID 2>/dev/null || true

# Summary
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✓ All smoke tests passed${NC}"
echo -e "${GREEN}========================================${NC}"
log_success "API: $API_URL/healthz"
log_success "WebUI: $WEB_URL/"
echo ""
