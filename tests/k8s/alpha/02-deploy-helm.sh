#!/bin/bash

# ==========================================================================
# Deploy with Helm for Alpha Testing
# ==========================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
HELM_DIR="infrastructure/helm/$PROJECT_NAME"
NAMESPACE="${PROJECT_NAME}-alpha"

echo "Deploying $PROJECT_NAME to Kubernetes (alpha namespace)..."
cd "$PROJECT_DIR"

# Check if Helm is installed
if ! command -v helm &> /dev/null; then
    echo "Error: helm is not installed"
    exit 1
fi

# Check if helm directory exists
if [ ! -d "$HELM_DIR" ]; then
    echo "Error: Helm directory not found at $HELM_DIR"
    exit 1
fi

# Create namespace if it doesn't exist
echo "Creating namespace: $NAMESPACE"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - 2>/dev/null || true

# Deploy using helm
echo "Running helm install/upgrade..."
helm upgrade --install "$PROJECT_NAME" \
    "./$HELM_DIR" \
    --namespace "$NAMESPACE" \
    --create-namespace \
    --values "./$HELM_DIR/values-alpha.yaml" \
    --wait \
    --timeout 5m

echo "Helm deployment completed"
