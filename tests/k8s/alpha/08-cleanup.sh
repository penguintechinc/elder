#!/bin/bash

# ==========================================================================
# Cleanup Alpha Resources
# ==========================================================================

PROJECT_NAME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && basename "$(pwd)")"
NAMESPACE="${PROJECT_NAME}-alpha"

echo "Cleaning up alpha resources for $PROJECT_NAME..."

# Uninstall Helm release
if helm list -n "$NAMESPACE" 2>/dev/null | grep -q "$PROJECT_NAME"; then
    echo "Uninstalling Helm release..."
    helm uninstall "$PROJECT_NAME" -n "$NAMESPACE" 2>/dev/null || true
fi

# Delete namespace
echo "Deleting namespace: $NAMESPACE"
kubectl delete namespace "$NAMESPACE" 2>/dev/null || true

echo "Cleanup completed"
